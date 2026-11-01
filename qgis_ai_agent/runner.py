"""Adapters that spawn AI CLIs non-interactively and register the MCP bridge."""
import os
import shutil
import sys

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

BRIDGE = os.path.join(os.path.dirname(__file__), "mcp_bridge.py")
PY = shutil.which("python3") or sys.executable


# name -> run(prompt, continue, auto_approve, model, effort) -> argv, plus MCP registration command.
ADAPTERS = {
    "agy (Antigravity CLI)": {
        "bin": "agy",
        "models": [
            ("gemini-3.8-flash-high (Fast & Recommended)", "gemini-3.8-flash-high"),
            ("gemini-3.8-flash-medium", "gemini-3.8-flash-medium"),
            ("gemini-4-argon-high (High Precision)", "gemini-4-argon-high"),
            ("gemini-4-argon-medium", "gemini-4-argon-medium"),
            ("claude-opus-5-5-high", "claude-opus-5-5-high"),
            ("claude-sonnet-5-5-high", "claude-sonnet-5-5-high"),
            ("claude-sonnet-5-5-medium", "claude-sonnet-5-5-medium"),
            ("gpt-oss-120b-medium", "gpt-oss-120b-medium"),
        ],
        "supports_effort": True,
        "run": lambda p, cont, auto, model=None, effort=None: (
            ["agy", "-p", p]
            + (["-c"] if cont else [])
            + (["--dangerously-skip-permissions"] if auto else [])
            + (["--model", model] if model else [])
            + (["--effort", effort.lower()] if effort and effort.lower() != "default" else [])
        ),
        "register": ["agy", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Claude Code": {
        "bin": "claude",
        "models": [
            ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5-20250929"),
            ("claude-opus-4-5-20251101", "claude-opus-4-5-20251101"),
            ("claude-3-7-sonnet", "claude-3-7-sonnet"),
            ("claude-3-5-sonnet", "claude-3-5-sonnet"),
            ("claude-3-5-haiku", "claude-3-5-haiku"),
        ],
        "supports_effort": True,
        "run": lambda p, cont, auto, model=None, effort=None: (
            ["claude", "-p", p]
            + (["-c"] if cont else [])
            + (["--dangerously-skip-permissions"] if auto else ["--allowedTools", "mcp__qgis"])
            + (["--model", model] if model else [])
            + (["--effort", effort.lower()] if effort and effort.lower() != "default" else [])
        ),
        "register": ["claude", "mcp", "add", "--scope", "user", "qgis", PY, BRIDGE],
    },
    "Gemini CLI": {
        "bin": "gemini",
        "models": [
            ("gemini-2.5-pro", "gemini-2.5-pro"),
            ("gemini-2.5-flash", "gemini-2.5-flash"),
            ("gemini-2.0-flash", "gemini-2.0-flash"),
            ("gemini-1.5-pro", "gemini-1.5-pro"),
            ("gemini-1.5-flash", "gemini-1.5-flash"),
        ],
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None: (
            ["gemini", "-p", p]
            + (["--resume", "latest"] if cont else [])
            + (["--yolo"] if auto else ["--allowed-mcp-server-names", "qgis"])
            + (["-m", model] if model else [])
        ),
        "register": ["gemini", "mcp", "add", "--scope", "user", "qgis", PY, BRIDGE],
    },
    "Codex CLI": {
        "bin": "codex",
        "models": [
            ("o3-mini", "o3-mini"),
            ("o1", "o1"),
            ("gpt-4o", "gpt-4o"),
            ("gpt-4.5-preview", "gpt-4.5-preview"),
        ],
        "supports_effort": True,
        "run": lambda p, cont, auto, model=None, effort=None: (
            (["codex", "exec", "resume", "--last"] if cont else ["codex", "exec"])
            + (["--full-auto", "--skip-git-repo-check"] if auto else ["--skip-git-repo-check"])
            + (["-m", model] if model else [])
            + (["-c", f'model_reasoning_effort="{effort.lower()}"'] if effort and effort.lower() != "default" else [])
            + [p]
        ),
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
