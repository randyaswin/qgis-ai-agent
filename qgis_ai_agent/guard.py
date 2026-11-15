#!/usr/bin/env python3
"""Process guardian that guarantees child AI CLI process is terminated if QGIS closes or crashes.

This script runs as a lightweight supervisory wrapper around the AI CLI.
It monitors the parent QGIS process via:
1. Linux kernel hook: PR_SET_PDEATHSIG (sends SIGKILL the instant parent QGIS dies).
2. Cross-platform watchdog thread: monitors parent PID liveness every 300ms.
3. Signal forwarding: SIGTERM, SIGINT, SIGHUP -> kills child process tree.
4. PID tracking: writes child CLI PID to QGIS_AI_PID_FILE for instant direct targeting.
"""
import ctypes
import glob
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

_SUBPROC_KWARGS = {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)} if sys.platform == "win32" else {}

try:
    from . import paths as _paths
    _ENDPOINT_FILE = _paths.get_endpoint_file()
except Exception:
    try:
        import paths as _paths
        _ENDPOINT_FILE = _paths.get_endpoint_file()
    except Exception:
        _ENDPOINT_FILE = os.path.join(os.path.expanduser("~"), ".qgis_ai_agent", "endpoint.json")


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
                if kernel32.GetLastError() == 87:
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
        try:
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            rparen = data.rfind(")")
            if rparen != -1 and len(data) > rparen + 2:
                return data[rparen + 2] != "Z"
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


def _cleanup_stale_endpoint(parent_pid):
    """Remove stale endpoint.json when it still references the terminated parent QGIS PID."""
    import json
    try:
        if not os.path.exists(_ENDPOINT_FILE):
            return
        with open(_ENDPOINT_FILE, encoding="utf-8") as f:
            ep = json.load(f)
        if ep.get("pid") == parent_pid:
            os.remove(_ENDPOINT_FILE)
    except Exception:
        pass


def _get_process_tree(root_pids, protected_pids=None):
    """Recursively discover all descendant PIDs of root_pids."""
    if protected_pids is None:
        protected_pids = set()
    if isinstance(root_pids, int):
        roots = [root_pids]
    else:
        roots = [int(p) for p in root_pids if p]

    ppid_map = {}
    has_proc = os.path.isdir("/proc")
    if has_proc:
        # Single /proc scan avoids spawning pgrep subprocesses per PID on Linux
        try:
            with os.scandir("/proc") as entries:
                for entry in entries:
                    name = entry.name
                    if not name.isdigit():
                        continue
                    try:
                        with open(f"/proc/{name}/stat", "r", encoding="utf-8", errors="replace") as f:
                            data = f.read()
                        rparen = data.rfind(")")
                        if rparen != -1:
                            parts = data[rparen + 2:].split()
                            if len(parts) >= 2 and parts[1].isdigit():
                                ppid = int(parts[1])
                                ppid_map.setdefault(ppid, set()).add(int(name))
                    except Exception:
                        pass
        except Exception:
            pass

    descendants = set()
    visited = set(roots)
    to_visit = list(roots)
    while to_visit:
        curr = to_visit.pop(0)
        found = set()
        if has_proc:
            found.update(ppid_map.get(curr, ()))
            for child_file in glob.glob(f"/proc/{curr}/task/*/children"):
                try:
                    with open(child_file, "r", encoding="utf-8", errors="replace") as f:
                        for c in f.read().split():
                            if c.isdigit():
                                found.add(int(c))
                except Exception:
                    pass
        else:
            # pgrep -P fallback (cross-unix / macOS without /proc)
            try:
                out = subprocess.check_output(["pgrep", "-P", str(curr)], stderr=subprocess.DEVNULL, text=True, **_SUBPROC_KWARGS)
                for c in out.split():
                    if c.isdigit():
                        found.add(int(c))
            except Exception:
                pass
        for c in found:
            if c not in visited and c not in protected_pids:
                visited.add(c)
                descendants.add(c)
                to_visit.append(c)
    return list(descendants)


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("Usage: guard.py <parent_pid> <cwd> <cmd> [args...]\n")
        sys.exit(1)

    try:
        parent_pid = int(sys.argv[1])
    except ValueError:
        parent_pid = os.getppid()

    cwd = sys.argv[2]
    child_cmd = sys.argv[3:]
    pid_file = os.environ.get("QGIS_AI_PID_FILE")

    # Ensure guard process runs in its own process group, completely separated from QGIS
    if hasattr(os, "setpgrp"):
        try:
            os.setpgrp()
        except Exception:
            pass

    # 1. Linux PR_SET_PDEATHSIG: kernel sends SIGTERM if parent dies so on_signal/kill_child_tree runs
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL(None)
            PR_SET_PDEATHSIG = 1
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
        except Exception:
            pass

    proc = None
    protected_base = {parent_pid, os.getpid()}
    try:
        protected_base.add(os.getppid())
    except Exception:
        pass

    tracked_pids = set()
    tracked_pgids = set()
    track_lock = threading.Lock()
    _kill_lock = threading.Lock()
    _killed = [False]

    def _snapshot_descendants():
        if not (sys.platform.startswith("linux") or sys.platform == "darwin"):
            return set(), set()
        with track_lock:
            if not proc or not proc.pid:
                return set(tracked_pids), set(tracked_pgids)
            seeds = {proc.pid} | tracked_pids
        try:
            discovered = _get_process_tree(seeds, protected_base)
        except Exception:
            discovered = []
        candidate_pids = seeds | set(discovered)
        new_pgids = set()
        if hasattr(os, "getpgid") and hasattr(os, "killpg"):
            for p in candidate_pids:
                try:
                    pg = os.getpgid(p)
                    if pg not in protected_base and pg > 1:
                        new_pgids.add(pg)
                except Exception:
                    pass
                if p not in protected_base and p > 1:
                    new_pgids.add(p)
        with track_lock:
            tracked_pids.update(discovered)
            tracked_pgids.update(new_pgids)
            return set(tracked_pids), set(tracked_pgids)

    def kill_child_tree(parent_dead=False):
        nonlocal proc
        with _kill_lock:
            if _killed[0]:
                return
            _killed[0] = True

            if parent_dead:
                _cleanup_stale_endpoint(parent_pid)

            if not proc:
                return
            c_pid = proc.pid
            if not c_pid:
                return

            snap_pids, snap_pgids = _snapshot_descendants()
            all_pids = {c_pid} | snap_pids
            all_pgids = set(snap_pgids)
            if hasattr(os, "getpgid") and hasattr(os, "killpg"):
                for p in all_pids:
                    try:
                        pg = os.getpgid(p)
                        if pg not in protected_base and pg > 1:
                            all_pgids.add(pg)
                    except Exception:
                        pass
                    if p not in protected_base and p > 1:
                        all_pgids.add(p)

            # Windows tree termination
            if sys.platform == "win32":
                try:
                    subprocess.call(
                        ["taskkill", "/F", "/T", "/PID", str(c_pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        **_SUBPROC_KWARGS,
                    )
                except Exception:
                    pass
                return

            if not parent_dead:
                # First wave: SIGTERM to process groups first, then individual processes
                for pg in all_pgids:
                    try:
                        os.killpg(pg, signal.SIGTERM)
                    except Exception:
                        pass

                for p in all_pids:
                    if p not in protected_base:
                        try:
                            os.kill(p, signal.SIGTERM)
                        except Exception:
                            pass

                time.sleep(0.015)

            # Immediate/Second wave: SIGKILL to process groups first, then individual processes
            for pg in all_pgids:
                try:
                    os.killpg(pg, signal.SIGKILL)
                except Exception:
                    pass

            for p in all_pids:
                if p not in protected_base:
                    try:
                        os.kill(p, signal.SIGKILL)
                    except Exception:
                        pass

    def on_signal(signum, frame):
        kill_child_tree(parent_dead=not _is_pid_alive(parent_pid))
        os._exit(128 + signum)

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, on_signal)

    # 2. Watchdog thread: checks if QGIS is still alive cross-platform (Linux, macOS, Windows)
    def parent_watchdog():
        # Windows: Use native WaitForSingleObject for instant event notification if available
        if sys.platform == "win32":
            try:
                kernel32 = ctypes.windll.kernel32
                SYNCHRONIZE = 0x00100000
                h_parent = kernel32.OpenProcess(SYNCHRONIZE, False, parent_pid)
                if h_parent:
                    # Blocks until parent process terminates
                    kernel32.WaitForSingleObject(h_parent, 0xFFFFFFFF)
                    kernel32.CloseHandle(h_parent)
                    kill_child_tree(parent_dead=True)
                    os._exit(0)
            except Exception:
                pass

        # Polling fallback (Linux / macOS / generic) + continuous descendant snapshotting
        while True:
            is_dead = not _is_pid_alive(parent_pid)

            if not is_dead and hasattr(os, "getppid"):
                try:
                    if os.getppid() != parent_pid:
                        is_dead = True
                except Exception:
                    pass

            if is_dead:
                kill_child_tree(parent_dead=True)
                os._exit(0)

            _snapshot_descendants()
            time.sleep(0.15)

    watchdog = threading.Thread(target=parent_watchdog, daemon=True)
    watchdog.start()

    # 2b. Stdin pipe EOF monitor (instant notification when QGIS closes its QProcess stdin handle)
    def stdin_eof_monitor():
        try:
            while True:
                chunk = sys.stdin.read(1024)
                if not chunk:
                    break
        except Exception:
            pass
        kill_child_tree(parent_dead=not _is_pid_alive(parent_pid))
        os._exit(0)

    eof_thread = threading.Thread(target=stdin_eof_monitor, daemon=True)
    eof_thread.start()

    # 3. Windows Job Object (Kernel-level child process tree containment)
    h_job = None
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            h_job = kernel32.CreateJobObjectW(None, None)
            if h_job:
                class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ('PerProcessUserTimeLimit', ctypes.c_int64),
                        ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', ctypes.c_uint32),
                        ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t),
                        ('ActiveProcessLimit', ctypes.c_uint32),
                        ('Affinity', ctypes.c_size_t),
                        ('PriorityClass', ctypes.c_uint32),
                        ('SchedulingClass', ctypes.c_uint32),
                    ]
                class IO_COUNTERS(ctypes.Structure):
                    _fields_ = [
                        ('ReadOperationCount', ctypes.c_uint64),
                        ('WriteOperationCount', ctypes.c_uint64),
                        ('OtherOperationCount', ctypes.c_uint64),
                        ('ReadTransferCount', ctypes.c_uint64),
                        ('WriteTransferCount', ctypes.c_uint64),
                        ('OtherTransferCount', ctypes.c_uint64),
                    ]
                class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ('IoInfo', IO_COUNTERS),
                        ('ProcessMemoryLimit', ctypes.c_size_t),
                        ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryLimit', ctypes.c_size_t),
                        ('PeakJobMemoryLimit', ctypes.c_size_t),
                    ]
                JobObjectExtendedLimitInformation = 9
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
                info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                kernel32.SetInformationJobObject(
                    h_job,
                    JobObjectExtendedLimitInformation,
                    ctypes.byref(info),
                    ctypes.sizeof(info)
                )
        except Exception:
            h_job = None

    # 4. Spawn child in its own process group with kernel death signal attached to guard
    def _child_preexec():
        if hasattr(os, "setsid"):
            try:
                os.setsid()
            except Exception:
                pass
        if sys.platform.startswith("linux"):
            try:
                libc = ctypes.CDLL(None)
                PR_SET_PDEATHSIG = 1
                libc.prctl(PR_SET_PDEATHSIG, signal.SIGKILL)
            except Exception:
                pass

    creationflags = 0
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
        resolved_bin = shutil.which(child_cmd[0]) or child_cmd[0]
        if resolved_bin.lower().endswith((".cmd", ".bat")):
            child_cmd = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", resolved_bin] + child_cmd[1:]
        else:
            child_cmd = [resolved_bin] + child_cmd[1:]

    try:
        proc = subprocess.Popen(
            child_cmd,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            preexec_fn=_child_preexec if sys.platform != "win32" else None,
            creationflags=creationflags
        )

        # On Windows, assign child to Job Object so Windows kernel enforces termination on close
        if h_job and hasattr(proc, "_handle"):
            try:
                ctypes.windll.kernel32.AssignProcessToJobObject(h_job, proc._handle)
            except Exception:
                pass

        # Write child PID to PID tracking file so runner can immediately target it directly
        if pid_file:
            try:
                with open(pid_file, "w") as f:
                    f.write(f"{proc.pid}\n")
            except Exception:
                pass

        _snapshot_descendants()
        ret = proc.wait()
        sys.exit(ret)
    except Exception as e:
        sys.stderr.write(f"Guard process error: {e}\n")
        sys.exit(1)
    finally:
        kill_child_tree(parent_dead=not _is_pid_alive(parent_pid))


if __name__ == "__main__":
    main()
