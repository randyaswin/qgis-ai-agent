"""Adapters that spawn AI CLIs non-interactively and register the MCP bridge."""
import json
import os
import re
import shutil
import signal
import subprocess
import sys

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, QThread, pyqtSignal

GUARD = os.path.join(os.path.dirname(__file__), "guard.py")
BRIDGE = os.path.join(os.path.dirname(__file__), "mcp_bridge.py")
PY = shutil.which("python3") or shutil.which("python") or sys.executable


def find_cli_bin(bin_name):
    """Finds CLI binary across standard and user npm/local paths."""
    p = shutil.which(bin_name)
    if p:
        return p
    extra_paths = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.npm-global/bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.path.join(os.environ.get("APPDATA", ""), "npm"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm"),
    ]
    for ep in extra_paths:
        candidate = os.path.join(ep, bin_name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        if sys.platform == "win32":
            candidate_cmd = os.path.join(ep, bin_name + ".cmd")
            if os.path.isfile(candidate_cmd):
                return candidate_cmd
    return bin_name


# In-memory cache for live CLI discovered models: cli_key -> [(label, id), ...]
_CLI_MODELS_CACHE = {}


def get_cli_models(cli_key, force_refresh=False):
    """Dynamically discover models available for a given AI CLI.
    Returns list of (display_label, model_id) tuples.
    """
    if not force_refresh and cli_key in _CLI_MODELS_CACHE:
        return _CLI_MODELS_CACHE[cli_key]

    adapter = ADAPTERS.get(cli_key, {})
    bin_name = adapter.get("bin", "")
    bin_path = find_cli_bin(bin_name)

    discovered = []

    # 1. Antigravity CLI: dynamic live models via `agy models`
    if "agy" in bin_name.lower():
        try:
            res = subprocess.run([bin_path, "models"], capture_output=True, text=True, timeout=5)
            if res.returncode == 0 and res.stdout:
                clean = re.sub(r'\x1b\[[0-9;?]*[A-Za-z]|\r', '', res.stdout)
                for line in clean.splitlines():
                    line = line.strip()
                    if not line or "Fetching" in line or "..." in line:
                        continue
                    parts = line.split(None, 1)
                    if parts:
                        m_id = parts[0].strip()
                        m_label = parts[1].strip() if len(parts) > 1 else m_id
                        discovered.append((m_label, m_id))
        except Exception:
            pass

    # 2. Claude Code: inspect ~/.claude.json for accessible/additional models
    elif "claude" in bin_name.lower():
        claude_models = [
            ("Claude 3.7 Sonnet (Recommended)", "sonnet"),
            ("Claude 3.7 Sonnet (Extended Thinking)", "claude-3-7-sonnet-20250219"),
            ("Claude 3.5 Haiku (Fast)", "haiku"),
            ("Claude 3.5 Sonnet", "claude-3-5-sonnet"),
            ("Claude 3 Opus", "opus"),
        ]
        try:
            cfg_path = os.path.expanduser("~/.claude.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                add_models = cdata.get("additionalModelOptionsCache") or []
                for am in add_models:
                    if isinstance(am, dict) and "value" in am:
                        val = am["value"]
                        lbl = am.get("label") or am.get("description") or val
                        if not any(val == m[1] for m in claude_models):
                            claude_models.append((f"{lbl} ({val})", val))
        except Exception:
            pass
        discovered = claude_models

    # 3. Gemini CLI: inspect ~/.gemini/settings.json or active Gemini family
    elif "gemini" in bin_name.lower():
        gemini_models = [
            ("Gemini 2.5 Pro (High Reasoning)", "gemini-2.5-pro"),
            ("Gemini 2.5 Flash (Fast & Capable)", "gemini-2.5-flash"),
            ("Gemini 2.0 Flash (Multimodal)", "gemini-2.0-flash"),
            ("Gemini 1.5 Pro", "gemini-1.5-pro"),
            ("Gemini 1.5 Flash", "gemini-1.5-flash"),
        ]
        try:
            cfg_path = os.path.expanduser("~/.gemini/settings.json")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                custom_m = sdata.get("model")
                if custom_m and not any(custom_m == m[1] for m in gemini_models):
                    gemini_models.insert(0, (f"Custom ({custom_m})", custom_m))
        except Exception:
            pass
        discovered = gemini_models

    # 4. Codex CLI: inspect ~/.codex/config.toml
    elif "codex" in bin_name.lower():
        codex_models = [
            ("o3-mini (Reasoning)", "o3-mini"),
            ("o1 (Deep Reasoning)", "o1"),
            ("gpt-4o (Omni)", "gpt-4o"),
            ("gpt-4.5-preview", "gpt-4.5-preview"),
        ]
        try:
            cfg_path = os.path.expanduser("~/.codex/config.toml")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()
                match = re.search(r'^\s*model\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                if match:
                    cm = match.group(1).strip()
                    if not any(cm == m[1] for m in codex_models):
                        codex_models.insert(0, (f"Configured ({cm})", cm))
        except Exception:
            pass
        discovered = codex_models

    if not discovered:
        discovered = adapter.get("default_models", [])

    if discovered:
        _CLI_MODELS_CACHE[cli_key] = discovered

    return discovered


class ModelDiscoveryThread(QThread):
    models_ready = pyqtSignal(str, list)

    def __init__(self, cli_name, parent=None):
        super().__init__(parent)
        self.cli_name = cli_name

    def run(self):
        models = get_cli_models(self.cli_name, force_refresh=True)
        self.models_ready.emit(self.cli_name, models)


# name -> run(prompt, continue, auto_approve, model, effort) -> argv, plus MCP registration command.
ADAPTERS = {
    "agy (Antigravity CLI)": {
        "bin": "agy",
        "default_models": [
            ("Gemini 3.8 Flash (High)", "gemini-3.8-flash-high"),
            ("Gemini 3.8 Flash (Medium)", "gemini-3.8-flash-medium"),
            ("Gemini 3.8 Flash (Low)", "gemini-3.8-flash-low"),
            ("Claude Sonnet 4.6 (Thinking)", "claude-sonnet-4-6"),
            ("Claude Opus 4.6 (Thinking)", "claude-opus-4-6-thinking"),
            ("GPT-OSS 120B (Medium)", "gpt-oss-120b-medium"),
        ],
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["agy"]
            + ([f"--conversation={session_id}"] if (cont and session_id) else (["-c"] if cont else []))
            + (["--dangerously-skip-permissions"] if auto else [])
            + (["--model", model] if model else [])
            + ["-p", p]
        ),
        "register": ["agy", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Claude Code": {
        "bin": "claude",
        "default_models": [
            ("Claude 3.7 Sonnet (Recommended)", "sonnet"),
            ("Claude 3.7 Sonnet (Extended Thinking)", "claude-3-7-sonnet-20250219"),
            ("Claude 3.5 Haiku (Fast)", "haiku"),
            ("Claude 3.5 Sonnet", "claude-3-5-sonnet"),
            ("Claude 3 Opus", "opus"),
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
        "default_models": [
            ("Gemini 2.5 Pro (High Reasoning)", "gemini-2.5-pro"),
            ("Gemini 2.5 Flash (Fast & Capable)", "gemini-2.5-flash"),
            ("Gemini 2.0 Flash (Multimodal)", "gemini-2.0-flash"),
            ("Gemini 1.5 Pro", "gemini-1.5-pro"),
            ("Gemini 1.5 Flash", "gemini-1.5-flash"),
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
        "default_models": [
            ("o3-mini (Reasoning)", "o3-mini"),
            ("o1 (Deep Reasoning)", "o1"),
            ("gpt-4o (Omni)", "gpt-4o"),
            ("gpt-4.5-preview", "gpt-4.5-preview"),
        ],
        "supports_effort": lambda m: bool(m and (m.startswith("o1") or m.startswith("o3") or "reasoning" in m.lower())),
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
    return {k: bool(shutil.which(v["bin"]) or find_cli_bin(v["bin"]) != v["bin"]) for k, v in ADAPTERS.items()}


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
