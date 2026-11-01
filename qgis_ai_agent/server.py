"""Local TCP endpoint inside QGIS. The stdio MCP bridge connects here.

Protocol: newline-delimited JSON. Request {"token","id","method","params"}.
Runs on the Qt main thread so every tool may touch QGIS objects safely.
"""
import json
import os
import secrets
import traceback

from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QTcpServer, QHostAddress

from . import tools

ENDPOINT_DIR = os.path.expanduser("~/.qgis_ai_agent")
ENDPOINT_FILE = os.path.join(ENDPOINT_DIR, "endpoint.json")


class ToolServer(QObject):
    activity = pyqtSignal(str)
    tool_called = pyqtSignal(str, dict)
    tool_completed = pyqtSignal(str, object)
    tool_failed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_conn)
        self.token = secrets.token_hex(16)
        self.buffers = {}

    def start(self):
        if not self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0):
            raise RuntimeError(self.server.errorString())
        os.makedirs(ENDPOINT_DIR, exist_ok=True)
        fd = os.open(ENDPOINT_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"port": self.server.serverPort(), "token": self.token, "pid": os.getpid()}, f)
        return self.server.serverPort()

    def stop(self):
        self.server.close()
        try:
            with open(ENDPOINT_FILE) as f:
                if json.load(f).get("pid") == os.getpid():
                    os.remove(ENDPOINT_FILE)
        except Exception:
            pass

    def _on_conn(self):
        while self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            self.buffers[s] = b""
            s.readyRead.connect(lambda s=s: self._on_read(s))
            s.disconnected.connect(lambda s=s: (self.buffers.pop(s, None), s.deleteLater()))

    def _on_read(self, sock):
        self.buffers[sock] = self.buffers.get(sock, b"") + bytes(sock.readAll())
        while b"\n" in self.buffers[sock]:
            line, self.buffers[sock] = self.buffers[sock].split(b"\n", 1)
            if line.strip():
                sock.write((json.dumps(self._handle(line)) + "\n").encode())
                sock.flush()

    def _handle(self, line):
        try:
            req = json.loads(line)
        except ValueError:
            return {"id": None, "error": "bad json"}
        rid = req.get("id")
        if req.get("token") != self.token:
            return {"id": rid, "error": "unauthorized"}
        method, params = req.get("method"), req.get("params") or {}
        try:
            if method == "list_tools":
                return {"id": rid, "result": tools.list_tools()}
            if method == "call_tool":
                name = params.get("name")
                args = params.get("arguments") or {}
                self.activity.emit(f"🔧 {name} {json.dumps(args, ensure_ascii=False)[:300]}")
                self.tool_called.emit(name, args)
                try:
                    res = tools.call(name, args)
                    self.tool_completed.emit(name, res)
                    return {"id": rid, "result": res}
                except Exception as e:
                    err_msg = f"{type(e).__name__}: {e}"
                    self.activity.emit(f"⚠️ {err_msg}")
                    self.tool_failed.emit(name, err_msg)
                    return {"id": rid, "error": err_msg, "trace": traceback.format_exc()[-1500:]}
            return {"id": rid, "error": f"unknown method {method}"}
        except Exception as e:
            self.activity.emit(f"⚠️ {type(e).__name__}: {e}")
            return {"id": rid, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]}
