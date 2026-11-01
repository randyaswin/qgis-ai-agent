"""Adapters that spawn AI CLIs non-interactively and register the MCP bridge."""
import os
import shutil
import sys

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

BRIDGE = os.path.join(os.path.dirname(__file__), "mcp_bridge.py")
PY = shutil.which("python3") or sys.executable


# name -> run(prompt, continue, auto_approve) -> argv, plus MCP registration command.
ADAPTERS = {
    "agy (Antigravity CLI)": {
        "bin": "agy",
        "run": lambda p, cont, auto: ["agy", "-p", p] + (["-c"] if cont else []) + (["--dangerously-skip-permissions"] if auto else []),
        "register": ["agy", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Claude Code": {
        "bin": "claude",
        "run": lambda p, cont, auto: ["claude", "-p", p] + (["-c"] if cont else [])
        + (["--dangerously-skip-permissions"] if auto else ["--allowedTools", "mcp__qgis"]),
        "register": ["claude", "mcp", "add", "--scope", "user", "qgis", PY, BRIDGE],
    },
    "Gemini CLI": {
        "bin": "gemini",
        "run": lambda p, cont, auto: ["gemini", "-p", p] + (["--resume", "latest"] if cont else [])
        + (["--yolo"] if auto else ["--allowed-mcp-server-names", "qgis"]),
        "register": ["gemini", "mcp", "add", "--scope", "user", "qgis", PY, BRIDGE],
    },
    "Codex CLI": {
        "bin": "codex",
        "run": lambda p, cont, auto: (["codex", "exec", "resume", "--last"] if cont else ["codex", "exec"])
        + (["--full-auto", "--skip-git-repo-check"] if auto else ["--skip-git-repo-check"]) + [p],
        "register": ["codex", "mcp", "add", "qgis", "--", PY, BRIDGE],
    },
}


def available():
    return {k: bool(shutil.which(v["bin"])) for k, v in ADAPTERS.items()}


class CliRunner(QObject):
    output = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def start(self, argv, cwd):
        if self.running():
            raise RuntimeError("An agent is already running")
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")
        env.insert("PATH", os.pathsep.join([os.path.expanduser("~/.local/bin"),
                                            os.path.expanduser("~/.npm-global/bin"), env.value("PATH")]))
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(
            lambda: self.output.emit(bytes(self.proc.readAllStandardOutput()).decode(errors="replace")))
        self.proc.finished.connect(lambda code, _s: self.finished.emit(code))
        self.proc.errorOccurred.connect(lambda e: self.output.emit(f"\n[process error: {e}]\n"))
        self.proc.start(argv[0], argv[1:])

    def stop(self):
        if self.running():
            self.proc.terminate()
            if not self.proc.waitForFinished(3000):
                self.proc.kill()
