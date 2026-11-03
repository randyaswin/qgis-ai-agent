"""Adapters that spawn AI CLIs non-interactively and register the MCP bridge."""
import os
import shutil
import signal
import subprocess
import sys

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, pyqtSignal

GUARD = os.path.join(os.path.dirname(__file__), "guard.py")
BRIDGE = os.path.join(os.path.dirname(__file__), "mcp_bridge.py")
PY = shutil.which("python3") or shutil.which("python") or sys.executable


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
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["agy"]
            + ([f"--conversation={session_id}"] if (cont and session_id) else (["-c"] if cont else []))
            + (["--dangerously-skip-permissions"] if auto else [])
            + (["--model", model] if model else [])
            + (["--effort", effort.lower()] if effort and effort.lower() != "default" else [])
            + ["-p", p]
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
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["claude"]
            + (["--resume", session_id] if (cont and session_id) else (["-c"] if cont else []))
            + (["--dangerously-skip-permissions"] if auto else ["--allowedTools", "mcp__qgis"])
            + (["--model", model] if model else [])
            + (["--effort", effort.lower()] if effort and effort.lower() != "default" else [])
            + ["-p", p]
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
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["gemini"]
            + (["--resume", session_id] if (cont and session_id) else (["--resume", "latest"] if cont else (["--session-id", session_id] if session_id else [])))
            + (["--yolo"] if auto else ["--allowed-mcp-server-names", "qgis"])
            + (["-m", model] if model else [])
            + ["-p", p]
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
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            (["codex", "exec", "resume", session_id] if (cont and session_id) else (["codex", "exec", "resume", "--last"] if cont else ["codex", "exec"]))
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

        # Cross-platform binary discovery (Linux, macOS Homebrew, Windows npm/AppData)
        extra_paths = [
            os.path.expanduser("~/.local/bin"),
            os.path.expanduser("~/.npm-global/bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            os.path.join(os.environ.get("APPDATA", ""), "npm"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm"),
        ]
        valid_paths = [p for p in extra_paths if p and os.path.isdir(p)]
        if valid_paths:
            env.insert("PATH", os.pathsep.join(valid_paths + [env.value("PATH")]))

        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(
            lambda: self.output.emit(bytes(self.proc.readAllStandardOutput()).decode(errors="replace")))
        self.proc.finished.connect(lambda code, _s: self.finished.emit(code))
        self.proc.errorOccurred.connect(lambda e: self.output.emit(f"\n[process error: {e}]\n"))

        bin_exec = shutil.which(argv[0]) or argv[0]
        # Wrap CLI invocation with guard.py to guarantee termination if QGIS closes or crashes
        qgis_pid = str(os.getpid())
        guard_argv = [PY, GUARD, qgis_pid, cwd, bin_exec] + argv[1:]
        self.proc.start(guard_argv[0], guard_argv[1:])

    def stop(self):
        if self.running():
            pid = self.proc.processId()
            # 1. Graceful termination
            self.proc.terminate()
            if pid > 0 and hasattr(os, "killpg"):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except Exception:
                    pass
            elif pid > 0 and sys.platform == "win32":
                try:
                    subprocess.call(["taskkill", "/F", "/T", "/PID", str(pid)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass

            if not self.proc.waitForFinished(1500):
                # 2. Force kill process tree if still active
                if pid > 0 and hasattr(os, "killpg"):
                    try:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    except Exception:
                        pass
                self.proc.kill()
                self.proc.waitForFinished(1000)
