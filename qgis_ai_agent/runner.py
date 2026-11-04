"""Adapters that spawn AI CLIs non-interactively and register the MCP bridge."""
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, QThread, pyqtSignal

from . import paths

GUARD = os.path.join(os.path.dirname(__file__), "guard.py")
BRIDGE = os.path.join(os.path.dirname(__file__), "mcp_bridge.py")
PY = shutil.which("python3") or shutil.which("python") or sys.executable


def find_cli_bin(bin_name):
    """Finds CLI binary across standard and user npm/local/cargo paths cross-platform."""
    p = shutil.which(bin_name)
    if p:
        return p
    for ep in paths.get_extra_bin_paths():
        candidate = os.path.join(ep, bin_name)
        if os.path.isfile(candidate) and (os.access(candidate, os.X_OK) or sys.platform == "win32"):
            return candidate
        if sys.platform == "win32":
            for ext in [".cmd", ".bat", ".exe"]:
                candidate_cmd = os.path.join(ep, bin_name + ext)
                if os.path.isfile(candidate_cmd):
                    return candidate_cmd
    return bin_name


# In-memory cache for live CLI discovered models: cli_key -> [(label, id), ...]
_CLI_MODELS_CACHE = {}


def get_cli_models(cli_key, force_refresh=False):
    """Dynamically discover models available for a given AI CLI.
    Returns list of (display_label, model_id) tuples.
    Default option is ALWAYS ('Default (CLI / Account Default)', '') so no model is forced.
    """
    default_entry = ("Default (CLI / Account Default)", "")

    if not force_refresh and cli_key in _CLI_MODELS_CACHE:
        return _CLI_MODELS_CACHE[cli_key]

    adapter = ADAPTERS.get(cli_key, {})
    bin_name = adapter.get("bin", "")
    bin_path = find_cli_bin(bin_name)

    discovered = []

    # 1. Antigravity CLI: dynamic live models via `agy models`
    if "agy" in bin_name.lower():
        try:
            res = subprocess.run([bin_path, "models"], capture_output=True, text=True, timeout=4)
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

    # 2. Claude Code: inspect ~/.claude.json / %APPDATA%\Claude for accessible models
    elif "claude" in bin_name.lower():
        claude_dirs = paths.get_candidate_cli_dirs("claude")
        candidate_configs = [os.path.join(d, "claude.json") for d in claude_dirs] + [os.path.join(paths.get_home_dir(), ".claude.json")]
        for cfg_path in candidate_configs:
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cdata = json.load(f)
                    cfg_m = cdata.get("model") or cdata.get("orgModelDefaultCache")
                    if cfg_m and isinstance(cfg_m, str):
                        discovered.append((f"Account Default ({cfg_m})", cfg_m))
                    add_models = cdata.get("additionalModelOptionsCache") or []
                    for am in add_models:
                        if isinstance(am, dict) and "value" in am:
                            val = am["value"]
                            lbl = am.get("label") or am.get("description") or val
                            if not any(val == m[1] for m in discovered):
                                discovered.append((f"{lbl} ({val})", val))
                    if discovered:
                        break
                except Exception:
                    pass

    # 3. Gemini CLI: inspect settings.json across candidate paths
    elif "gemini" in bin_name.lower():
        gemini_dirs = paths.get_candidate_cli_dirs("gemini")
        for gdir in gemini_dirs:
            cfg_path = os.path.join(gdir, "settings.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        sdata = json.load(f)
                    custom_m = sdata.get("model")
                    if custom_m:
                        discovered.append((f"Configured ({custom_m})", custom_m))
                        break
                except Exception:
                    pass

    # 4. Codex CLI: inspect config.toml
    elif "codex" in bin_name.lower():
        codex_dirs = paths.get_candidate_cli_dirs("codex")
        for cdir in codex_dirs:
            cfg_path = os.path.join(cdir, "config.toml")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    match = re.search(r'^\s*model\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                    if match:
                        cm = match.group(1).strip()
                        discovered.append((f"Configured ({cm})", cm))
                        break
                except Exception:
                    pass

    # 5. OpenCode CLI: inspect config.json
    elif "opencode" in bin_name.lower():
        opencode_dirs = paths.get_candidate_cli_dirs("opencode")
        for odir in opencode_dirs:
            cfg_path = os.path.join(odir, "config.json")
            if os.path.exists(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        odata = json.load(f)
                    m = odata.get("model")
                    if m:
                        discovered.append((f"Configured ({m})", m))
                        break
                except Exception:
                    pass

    # Build final list: Always place CLI Account Default at the top!
    result = [default_entry]
    for lbl, val in discovered:
        if val and not any(val == r[1] for r in result):
            result.append((lbl, val))

    _CLI_MODELS_CACHE[cli_key] = result
    return result


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
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["agy"]
            + ([f"--conversation={session_id}"] if (cont and session_id) else (["-c"] if cont else []))
            + (["--dangerously-skip-permissions"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["--output-format", "stream-json"]
            + ["-p", p]
        ),
        "register": ["agy", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Claude Code": {
        "bin": "claude",
        "supports_effort": True,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["claude"]
            + (["--resume", session_id] if (cont and session_id) else (["-c"] if cont else []))
            + (["--dangerously-skip-permissions"] if auto else ["--allowedTools", "mcp__qgis"])
            + (["--model", model] if (model and model.strip()) else [])
            + (["--effort", effort.lower()] if effort and effort.lower() != "default" else [])
            + ["--output-format", "stream-json", "--verbose", "--include-partial-messages"]
            + ["-p", p]
        ),
        "register": ["claude", "mcp", "add", "--scope", "user", "qgis", PY, BRIDGE],
    },
    "Gemini CLI": {
        "bin": "gemini",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["gemini"]
            + (["--resume", session_id] if (cont and session_id) else (["--resume", "latest"] if cont else (["--session-id", session_id] if session_id else [])))
            + (["--yolo"] if auto else ["--allowed-mcp-server-names", "qgis"])
            + (["-m", model] if (model and model.strip()) else [])
            + ["--output-format", "stream-json"]
            + ["-p", p]
        ),
        "register": ["gemini", "mcp", "add", "--scope", "user", "qgis", PY, BRIDGE],
    },
    "Codex CLI": {
        "bin": "codex",
        "supports_effort": lambda m: bool(m and (m.startswith("o1") or m.startswith("o3") or "reasoning" in m.lower())),
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            (["codex", "exec", "resume", session_id] if (cont and session_id) else (["codex", "exec", "resume", "--last"] if cont else ["codex", "exec"]))
            + (["--full-auto", "--skip-git-repo-check"] if auto else ["--skip-git-repo-check"])
            + (["-m", model] if (model and model.strip()) else [])
            + (["-c", f'model_reasoning_effort="{effort.lower()}"'] if effort and effort.lower() != "default" else [])
            + ["--json"]
            + [p]
        ),
        "register": ["codex", "mcp", "add", "qgis", "--", PY, BRIDGE],
    },
    "GitHub Copilot CLI": {
        "bin": "copilot",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            (["copilot"] if shutil.which("copilot") else ["gh", "copilot"])
            + (["--resume", session_id] if (cont and session_id) else (["--continue"] if cont else []))
            + (["--yolo"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["-p", p]
        ),
        "register": ["copilot", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "OpenCode": {
        "bin": "opencode",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["opencode"]
            + (["--session", session_id] if (cont and session_id) else (["-c"] if cont else []))
            + (["--auto"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["--prompt", p]
        ),
        "register": ["opencode", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "DeepSeek / Hermes CLI": {
        "bin": "deepseek",
        "supports_effort": True,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ([find_cli_bin("deepseek") or find_cli_bin("hermes") or "deepseek"])
            + (["--resume", session_id] if (cont and session_id) else (["--continue"] if cont else []))
            + (["--yolo"] if auto else [])
            + (["-m", model] if (model and model.strip()) else [])
            + (["--reasoning", effort.lower()] if effort and effort.lower() != "default" else [])
            + ["-z", p]
        ),
        "register": ["deepseek", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Cursor Agent": {
        "bin": "cursor-agent",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ([find_cli_bin("cursor-agent") or find_cli_bin("cursor") or "cursor-agent"])
            + (["--resume", session_id] if (cont and session_id) else (["-c"] if cont else []))
            + (["--auto"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["-p", p]
        ),
        "register": ["cursor-agent", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Qwen Code": {
        "bin": "qwen",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ([find_cli_bin("qwen") or find_cli_bin("qwen-code") or "qwen"])
            + (["-c"] if cont else [])
            + (["--yolo"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["-p", p]
        ),
        "register": ["qwen", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Trae CLI": {
        "bin": "trae",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ([find_cli_bin("trae") or find_cli_bin("traecli") or "trae"])
            + (["-c"] if cont else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["-p", p]
        ),
        "register": ["trae", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Kimi Code": {
        "bin": "kimi",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ([find_cli_bin("kimi") or find_cli_bin("kimi-cli") or "kimi"])
            + (["-c"] if cont else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["-p", p]
        ),
        "register": ["kimi", "mcp", "add", "qgis", PY, BRIDGE],
    },
    "Aider": {
        "bin": "aider",
        "supports_effort": False,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["aider", "--no-git", "--no-auto-commits"]
            + (["--yes"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + ["--message", p]
        ),
        "register": ["aider"],
    },
}


def is_bin_available(bin_name):
    if shutil.which(bin_name):
        return True
    found = find_cli_bin(bin_name)
    return bool(found and (os.path.isabs(found) and os.path.exists(found)))


def available():
    res = {}
    for k, v in ADAPTERS.items():
        bin_name = v.get("bin", "")
        if bin_name == "copilot":
            is_avail = is_bin_available("copilot") or bool(shutil.which("gh"))
        elif bin_name == "deepseek":
            is_avail = is_bin_available("deepseek") or is_bin_available("hermes") or is_bin_available("dsh")
        elif bin_name == "cursor-agent":
            is_avail = is_bin_available("cursor-agent") or is_bin_available("cursor")
        elif bin_name == "qwen":
            is_avail = is_bin_available("qwen") or is_bin_available("qwen-code")
        elif bin_name == "trae":
            is_avail = is_bin_available("trae") or is_bin_available("traecli")
        elif bin_name == "kimi":
            is_avail = is_bin_available("kimi") or is_bin_available("kimi-cli")
        else:
            is_avail = is_bin_available(bin_name)
        res[k] = is_avail
    return res


def get_process_descendants(root_pid, protected_pids=None):
    """Recursively collect all child/descendant PIDs of root_pid."""
    if protected_pids is None:
        protected_pids = set()
    descendants = set()
    to_visit = [root_pid]
    while to_visit:
        curr = to_visit.pop(0)
        found = set()
        for child_file in glob.glob(f"/proc/{curr}/task/*/children"):
            try:
                with open(child_file, "r") as f:
                    for c in f.read().split():
                        if c.isdigit():
                            found.add(int(c))
            except Exception:
                pass
        try:
            out = subprocess.check_output(["pgrep", "-P", str(curr)], stderr=subprocess.DEVNULL, text=True)
            for c in out.split():
                if c.isdigit():
                    found.add(int(c))
        except Exception:
            pass
        for c in found:
            if c not in descendants and c not in protected_pids:
                descendants.add(c)
                to_visit.append(c)
    return list(descendants)


def kill_entire_tree(root_pids, protected_pids=None):
    """Guarantees complete termination of processes and all their children."""
    if protected_pids is None:
        protected_pids = set()
    protected_pids.add(os.getpid())
    try:
        protected_pids.add(os.getppid())
    except Exception:
        pass

    all_targets = set()
    for rpid in root_pids:
        if rpid and rpid > 0 and rpid not in protected_pids:
            all_targets.add(rpid)
            if sys.platform.startswith("linux") or sys.platform == "darwin":
                try:
                    for d in get_process_descendants(rpid, protected_pids):
                        all_targets.add(d)
                except Exception:
                    pass

    # Windows native process tree termination
    if sys.platform == "win32":
        for rpid in root_pids:
            if rpid and rpid > 0 and rpid not in protected_pids:
                try:
                    subprocess.call(
                        ["taskkill", "/F", "/T", "/PID", str(rpid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                except Exception:
                    pass
        return

    # Pre-collect process groups while PIDs are still alive
    all_pgids = set()
    if hasattr(os, "getpgid") and hasattr(os, "killpg"):
        for pid in all_targets:
            try:
                pg = os.getpgid(pid)
                if pg not in protected_pids and pg > 0:
                    all_pgids.add(pg)
            except Exception:
                pass
            # If setsid was used by child, pid itself is a process group leader
            if pid not in protected_pids and pid > 0:
                all_pgids.add(pid)

    # First wave: SIGTERM to process groups first, then individual targets
    for pg in all_pgids:
        try:
            os.killpg(pg, signal.SIGTERM)
        except Exception:
            pass

    for pid in all_targets:
        if pid not in protected_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass

    time.sleep(0.06)

    # Second wave: SIGKILL to process groups first, then individual targets
    for pg in all_pgids:
        try:
            os.killpg(pg, signal.SIGKILL)
        except Exception:
            pass

    for pid in all_targets:
        if pid not in protected_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


class CliRunner(QObject):
    output = pyqtSignal(str)
    finished = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.proc = None
        self._current_pid_file = None

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def _cleanup_pid_file(self):
        if self._current_pid_file and os.path.exists(self._current_pid_file):
            try:
                os.remove(self._current_pid_file)
            except Exception:
                pass
        self._current_pid_file = None

    def _on_finished(self, code, _status=None):
        self._cleanup_pid_file()
        self.finished.emit(code)

    def start(self, argv, cwd):
        if self.running():
            raise RuntimeError("An agent is already running")
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")

        # Create unique PID tracking file for this invocation
        self._current_pid_file = os.path.join(tempfile.gettempdir(), f"qgis_ai_{uuid.uuid4().hex[:12]}.pid")
        env.insert("QGIS_AI_PID_FILE", self._current_pid_file)

        # Cross-platform binary discovery (Linux, macOS Homebrew, Windows npm/AppData/Cargo)
        valid_paths = paths.get_extra_bin_paths()
        if valid_paths:
            env.insert("PATH", os.pathsep.join(valid_paths + [env.value("PATH")]))

        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(
            lambda: self.output.emit(bytes(self.proc.readAllStandardOutput()).decode(errors="replace")))
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(lambda e: self.output.emit(f"\n[process error: {e}]\n"))

        bin_exec = shutil.which(argv[0]) or argv[0]
        # Wrap CLI invocation with guard.py to guarantee termination if QGIS closes or crashes
        qgis_pid = str(os.getpid())
        guard_argv = [PY, GUARD, qgis_pid, cwd, bin_exec] + argv[1:]
        self.proc.start(guard_argv[0], guard_argv[1:])

    def stop(self):
        # 1. Read child CLI PID directly from the tracking file if available
        child_pid = None
        if self._current_pid_file and os.path.exists(self._current_pid_file):
            try:
                with open(self._current_pid_file, "r") as f:
                    content = f.read().strip()
                    if content.isdigit():
                        child_pid = int(content)
            except Exception:
                pass

        # 2. Get guard supervisor PID
        guard_pid = None
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            try:
                p = self.proc.processId()
                if p and p > 0:
                    guard_pid = p
            except Exception:
                pass

        # 3. Kill all targets and their entire descendant process trees directly
        targets = [p for p in (guard_pid, child_pid) if p and p > 0]
        if targets:
            kill_entire_tree(targets, protected_pids={os.getpid(), os.getppid()})

        # 4. Finish Qt QProcess object cleanly
        if self.proc and self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.terminate()
            if not self.proc.waitForFinished(400):
                self.proc.kill()
                self.proc.waitForFinished(200)

        self._cleanup_pid_file()
