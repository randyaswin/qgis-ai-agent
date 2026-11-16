#!/usr/bin/env python3
"""Stdio MCP server that forwards tool calls to the running QGIS Copilot plugin.

Register once in any AI CLI, e.g.:
    claude mcp add qgis python3 /path/to/mcp_bridge.py
    agy    mcp add qgis python3 /path/to/mcp_bridge.py
Pure stdlib, no QGIS imports. Reads ~/.qgis_ai_agent/endpoint.json on each call,
so it keeps working across QGIS restarts.
"""
import ctypes
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

try:
    from . import paths
    ENDPOINT_FILE = paths.get_endpoint_file()
except Exception:
    try:
        import paths
        ENDPOINT_FILE = paths.get_endpoint_file()
    except Exception:
        _u = os.environ.get("USERPROFILE")
        _home = _u if (_u and os.path.isdir(_u)) else os.path.expanduser("~")
        _appdata = os.environ.get("APPDATA")
        if sys.platform == "win32" and _appdata and os.path.isdir(_appdata):
            _cand = os.path.join(_appdata, "qgis_ai_agent", "endpoint.json")
            ENDPOINT_FILE = _cand if os.path.exists(_cand) else os.path.join(_home, ".qgis_ai_agent", "endpoint.json")
        else:
            ENDPOINT_FILE = os.path.join(_home, ".qgis_ai_agent", "endpoint.json")
_counter = [0]
_ACTIVE_QGIS_PID = [None]
_ABORT_LOCK = threading.Lock()


def _is_pid_alive(pid):
    """Cross-platform check whether process PID is alive and not a zombie."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if handle:
                try:
                    exit_code = ctypes.c_uint32()
                    if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                        return exit_code.value == STILL_ACTIVE
                finally:
                    kernel32.CloseHandle(handle)
            else:
                if kernel32.GetLastError() == 87:  # ERROR_INVALID_PARAMETER
                    return False
        except Exception:
            pass
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    if os.path.isdir("/proc"):
        stat_path = f"/proc/{pid}/stat"
        try:
            with open(stat_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            rparen = data.rfind(")")
            if rparen != -1 and len(data) > rparen + 2:
                state = data[rparen + 2]
                return state != "Z"
            return True
        except FileNotFoundError:
            return False
        except PermissionError:
            pass
        except OSError:
            pass

    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _cleanup_stale_endpoint(dead_pid=None):
    """Remove ENDPOINT_FILE if it points to a dead QGIS process (or any when dead_pid is None)."""
    try:
        if not os.path.exists(ENDPOINT_FILE):
            return
        if dead_pid is not None:
            with open(ENDPOINT_FILE, encoding="utf-8") as f:
                ep = json.load(f)
            if ep.get("pid") != dead_pid:
                return
        os.remove(ENDPOINT_FILE)
    except Exception:
        pass


def _abort_on_dead_qgis(dead_pid=None):
    """Terminate CLI/MCP transport cleanly without replying when QGIS dies unexpectedly."""
    with _ABORT_LOCK:
        _cleanup_stale_endpoint(dead_pid)

        has_qgis_session = bool(os.environ.get("QGIS_AI_PID_FILE") or os.environ.get("QGIS_AI_PARENT_PID"))
        if has_qgis_session:
            sig_kill = getattr(signal, "SIGKILL", signal.SIGTERM)
            self_pid = os.getpid()
            protected = {self_pid}
            if dead_pid:
                try:
                    protected.add(int(dead_pid))
                except (TypeError, ValueError):
                    pass

            target_pids = []
            pid_file = os.environ.get("QGIS_AI_PID_FILE")
            if pid_file and os.path.isfile(pid_file):
                try:
                    with open(pid_file, "r", encoding="utf-8") as f:
                        raw = f.read().strip()
                    if raw.isdigit():
                        cli_pid = int(raw)
                        if cli_pid > 1 and cli_pid not in protected:
                            target_pids.append(cli_pid)
                except Exception:
                    pass

            try:
                ppid = os.getppid()
                if ppid > 1 and ppid not in protected and ppid not in target_pids:
                    target_pids.append(ppid)
            except Exception:
                pass

            if sys.platform == "win32":
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                for tp in target_pids:
                    try:
                        subprocess.call(
                            ["taskkill", "/F", "/T", "/PID", str(tp)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=creationflags,
                        )
                    except Exception:
                        pass
            else:
                for tp in target_pids:
                    try:
                        if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                            pg = os.getpgid(tp)
                            if pg > 1 and pg not in protected:
                                os.killpg(pg, sig_kill)
                    except Exception:
                        pass
                    try:
                        os.kill(tp, sig_kill)
                    except Exception:
                        pass

        os._exit(1)


def _read_endpoint_with_retry(method):
    """Read endpoint.json with brief retry to survive QGIS startup or plugin reload races."""
    max_attempts = 12 if method == "list_tools" else 6
    last_exc = None
    for attempt in range(max_attempts):
        try:
            with open(ENDPOINT_FILE, encoding="utf-8") as f:
                ep = json.load(f)
            if isinstance(ep, dict) and ep.get("port") and ep.get("token"):
                return ep
        except FileNotFoundError as exc:
            last_exc = exc
        except (json.JSONDecodeError, OSError) as exc:
            last_exc = exc
        raw_parent = os.environ.get("QGIS_AI_PARENT_PID", "").strip()
        if raw_parent.isdigit() and not _is_pid_alive(int(raw_parent)):
            break
        if attempt < max_attempts - 1:
            time.sleep(0.25)
    raise RuntimeError("QGIS Copilot plugin is not running. Open QGIS and enable the plugin.") from last_exc


def qgis_rpc(method, params=None):
    ep = _read_endpoint_with_retry(method)

    ep_pid = ep.get("pid")
    if ep_pid and not _is_pid_alive(ep_pid):
        _cleanup_stale_endpoint(ep_pid)
        if _ACTIVE_QGIS_PID[0] == ep_pid or os.environ.get("QGIS_AI_PARENT_PID"):
            _abort_on_dead_qgis(ep_pid)
        raise RuntimeError("QGIS Copilot plugin is not running (cleaned up stale endpoint from previous session). Open QGIS and enable the plugin.")

    if ep_pid:
        _ACTIVE_QGIS_PID[0] = ep_pid

    _counter[0] += 1
    req = {"token": ep["token"], "id": _counter[0], "method": method, "params": params or {}}
    timeout_sec = float(os.environ.get("QGIS_AI_TIMEOUT", "300"))
    buf = b""
    try:
        with socket.create_connection(("127.0.0.1", ep["port"]), timeout=timeout_sec) as s:
            s.sendall((json.dumps(req) + "\n").encode())
            while b"\n" not in buf:
                chunk = s.recv(1 << 20)
                if not chunk:
                    break
                buf += chunk
    except socket.timeout:
        if ep_pid and not _is_pid_alive(ep_pid):
            _abort_on_dead_qgis(ep_pid)
        raise RuntimeError(f"QGIS operation timed out after {int(timeout_sec)}s.")
    except (ConnectionRefusedError, ConnectionResetError, BrokenPipeError, OSError):
        if ep_pid and not _is_pid_alive(ep_pid):
            _abort_on_dead_qgis(ep_pid)
        # Re-read endpoint once in case QGIS reloaded on a new port
        try:
            ep2 = _read_endpoint_with_retry(method)
            if ep2.get("port") != ep.get("port"):
                with socket.create_connection(("127.0.0.1", ep2["port"]), timeout=timeout_sec) as s2:
                    req2 = {"token": ep2["token"], "id": _counter[0], "method": method, "params": params or {}}
                    s2.sendall((json.dumps(req2) + "\n").encode())
                    buf2 = b""
                    while b"\n" not in buf2:
                        chunk = s2.recv(1 << 20)
                        if not chunk:
                            break
                        buf2 += chunk
                    if buf2:
                        resp2 = json.loads(buf2.split(b"\n", 1)[0], parse_constant=lambda c: None)
                        if "error" in resp2:
                            raise RuntimeError(resp2["error"] + ("\n" + resp2["trace"] if resp2.get("trace") else ""))
                        return resp2["result"]
        except RuntimeError:
            raise
        except Exception:
            pass
        raise RuntimeError("QGIS Copilot plugin port is not responding. Ensure QGIS is running and the plugin is active.")
    if not buf:
        if ep_pid and not _is_pid_alive(ep_pid):
            _abort_on_dead_qgis(ep_pid)
        raise RuntimeError("QGIS Copilot plugin closed connection without response.")
    resp = json.loads(buf.split(b"\n", 1)[0], parse_constant=lambda c: None)
    if "error" in resp:
        raise RuntimeError(resp["error"] + ("\n" + resp["trace"] if resp.get("trace") else ""))
    return resp["result"]


def to_content(result):
    if isinstance(result, dict) and "__image__" in result:
        c = [{"type": "image", "data": result["__image__"], "mimeType": result.get("mime", "image/png")}]
        if result.get("meta"):
            c.append({"type": "text", "text": json.dumps(result["meta"], ensure_ascii=False)})
        return c
    return [{"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str, indent=1)}]


def send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _qgis_watchdog():
    env_pid = None
    raw_parent = os.environ.get("QGIS_AI_PARENT_PID")
    if raw_parent and raw_parent.strip().isdigit():
        p = int(raw_parent.strip())
        if p > 0:
            env_pid = p
    while True:
        qpid = _ACTIVE_QGIS_PID[0] or env_pid
        if qpid and not _is_pid_alive(qpid):
            _abort_on_dead_qgis(qpid)
        time.sleep(0.2)


def main():
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    threading.Thread(target=_qgis_watchdog, daemon=True).start()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:  # notification
            continue
        try:
            if method == "initialize":
                res = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                       "capabilities": {"tools": {}},
                       "serverInfo": {"name": "qgis-ai-agent", "version": "1.1.3"}}
            elif method == "ping":
                res = {}
            elif method == "tools/list":
                try:
                    tl = qgis_rpc("list_tools")
                except Exception as e:
                    tl = [{"name": "get_project_state", "description": f"(QGIS offline: {e})",
                           "inputSchema": {"type": "object", "properties": {}}}]
                res = {"tools": tl}
            elif method == "tools/call":
                p = msg.get("params", {})
                try:
                    res = {"content": to_content(qgis_rpc("call_tool", {"name": p.get("name"), "arguments": p.get("arguments") or {}}))}
                except Exception as e:
                    res = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            elif method in ("resources/list", "prompts/list"):
                res = {method.split("/")[0]: []}
            else:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": res})
        except Exception as e:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(e)}})


if __name__ == "__main__":
    main()
