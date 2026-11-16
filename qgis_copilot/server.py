"""Local TCP endpoint inside QGIS. The stdio MCP bridge connects here.

Protocol: newline-delimited JSON. Request {"token","id","method","params"}.
Runs on the Qt main thread so every tool may touch QGIS objects safely.
"""
import json
import os
import secrets
import traceback

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QObject, pyqtSignal
from qgis.PyQt.QtNetwork import QTcpServer, QHostAddress

from . import tools
from . import paths

ENDPOINT_DIR = paths.get_app_data_dir()
ENDPOINT_FILE = paths.get_endpoint_file()
MAX_BUFFER_BYTES = 32 * 1024 * 1024


def _is_deleted(obj):
    if obj is None:
        return True
    try:
        return bool(sip.isdeleted(obj))
    except Exception:
        return False


class ToolServer(QObject):
    activity = pyqtSignal(str)
    tool_called = pyqtSignal(str, dict)
    tool_progress = pyqtSignal(str, int, str)
    tool_completed = pyqtSignal(str, object)
    tool_failed = pyqtSignal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_conn)
        self.token = secrets.token_hex(16)
        self.buffers = {}
        self._reading_sockets = set()
        self._busy = False
        self._active_sock = None
        self._disconnect_cancelled = False
        tools._PROGRESS_CALLBACK = lambda name, pct, msg="": self.tool_progress.emit(str(name), int(pct), str(msg or ""))

    def _write_endpoint_file(self):
        os.makedirs(ENDPOINT_DIR, exist_ok=True)
        tmp_path = f"{ENDPOINT_FILE}.{os.getpid()}.tmp"
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"port": self.server.serverPort(), "token": self.token, "pid": os.getpid()}, f)
        os.replace(tmp_path, ENDPOINT_FILE)

    def ensure_endpoint(self):
        """Ensure the TCP server is listening and endpoint.json points to this live QGIS instance."""
        if not self.server.isListening():
            return self.start()
        cur_port = self.server.serverPort()
        needs_write = True
        try:
            with open(ENDPOINT_FILE, encoding="utf-8") as f:
                ep = json.load(f)
            if ep.get("pid") == os.getpid() and ep.get("port") == cur_port and ep.get("token") == self.token:
                needs_write = False
        except Exception:
            needs_write = True
        if needs_write:
            self._write_endpoint_file()
        return cur_port

    def start(self):
        if not self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost), 0):
            raise RuntimeError(self.server.errorString())
        self._write_endpoint_file()
        return self.server.serverPort()

    def stop(self):
        cur_port = self.server.serverPort()
        for sock in list(self.buffers.keys()):
            self.buffers.pop(sock, None)
            if not _is_deleted(sock):
                try:
                    sock.disconnectFromHost()
                    sock.close()
                    sock.deleteLater()
                except Exception:  # nosec B110
                    pass
        self._reading_sockets.clear()
        self.server.close()
        try:
            with open(ENDPOINT_FILE, encoding="utf-8") as f:
                ep = json.load(f)
                if ep.get("pid") == os.getpid() and ep.get("port") == cur_port:
                    os.remove(ENDPOINT_FILE)
        except Exception:  # nosec B110
            pass

    def _on_conn(self):
        while self.server.hasPendingConnections():
            s = self.server.nextPendingConnection()
            self.buffers[s] = b""
            s.readyRead.connect(lambda s=s: self._on_read(s))
            s.disconnected.connect(lambda s=s: self._on_disconnect(s))

    def _on_disconnect(self, sock):
        self.buffers.pop(sock, None)
        self._reading_sockets.discard(sock)
        if self._busy and sock is self._active_sock and not tools.CANCEL_REQUESTED:
            tools.CANCEL_REQUESTED = True
            self._disconnect_cancelled = True
        if hasattr(sock, "deleteLater") and not _is_deleted(sock):
            try:
                sock.deleteLater()
            except Exception:  # nosec B110
                pass

    def _on_read(self, sock):
        if _is_deleted(sock) or sock not in self.buffers or sock in self._reading_sockets:
            return
        self._reading_sockets.add(sock)
        try:
            chunk = bytes(sock.readAll())
            cur = self.buffers.get(sock, b"")
            if len(cur) + len(chunk) > MAX_BUFFER_BYTES:
                self.buffers.pop(sock, None)
                if not _is_deleted(sock):
                    try:
                        sock.write((json.dumps({"id": None, "error": "request exceeds 32MB buffer limit"}) + "\n").encode("utf-8"))
                        sock.flush()
                        sock.close()
                    except Exception:  # nosec B110
                        pass
                return
            self.buffers[sock] = cur + chunk
        finally:
            self._reading_sockets.discard(sock)

        if not self._busy:
            self._drain_buffers()

    def _drain_buffers(self):
        if self._busy:
            return
        self._busy = True
        try:
            while True:
                handled_any = False
                for sock in list(self.buffers.keys()):
                    buf = self.buffers.get(sock)
                    if not buf or b"\n" not in buf:
                        continue
                    line, rest = buf.split(b"\n", 1)
                    self.buffers[sock] = rest
                    if not line.strip():
                        handled_any = True
                        continue
                    handled_any = True
                    self._active_sock = sock
                    self._disconnect_cancelled = False
                    try:
                        resp_bytes = (json.dumps(self._handle(line)) + "\n").encode("utf-8")
                    finally:
                        self._active_sock = None
                        if self._disconnect_cancelled:
                            tools.CANCEL_REQUESTED = False
                            self._disconnect_cancelled = False
                    if sock in self.buffers and not _is_deleted(sock) and sock.isValid():
                        sock.write(resp_bytes)
                        sock.flush()
                if not handled_any:
                    break
        finally:
            self._busy = False

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
