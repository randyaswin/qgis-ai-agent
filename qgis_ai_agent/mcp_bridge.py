#!/usr/bin/env python3
"""Stdio MCP server that forwards tool calls to the running QGIS AI Agent plugin.

Register once in any AI CLI, e.g.:
    claude mcp add qgis python3 /path/to/mcp_bridge.py
    agy    mcp add qgis python3 /path/to/mcp_bridge.py
Pure stdlib, no QGIS imports. Reads ~/.qgis_ai_agent/endpoint.json on each call,
so it keeps working across QGIS restarts.
"""
import json
import os
import socket
import sys

ENDPOINT_FILE = os.path.expanduser("~/.qgis_ai_agent/endpoint.json")
_counter = [0]


def qgis_rpc(method, params=None):
    try:
        with open(ENDPOINT_FILE) as f:
            ep = json.load(f)
    except FileNotFoundError:
        raise RuntimeError("QGIS AI Agent plugin is not running. Open QGIS and enable the plugin.")
    _counter[0] += 1
    req = {"token": ep["token"], "id": _counter[0], "method": method, "params": params or {}}
    with socket.create_connection(("127.0.0.1", ep["port"]), timeout=600) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
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


def main():
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
                       "serverInfo": {"name": "qgis-ai-agent", "version": "0.1.0"}}
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
