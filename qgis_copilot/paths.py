"""Cross-platform directory and path resolution for Windows, macOS, and Linux.

Provides unified discovery of:
1. Documents directory (QStandardPaths DocumentsLocation)
2. App data directory (.qgis_ai_agent or OS-specific AppData)
3. AI CLI transcript, project, and session caches across all operating systems.
"""
import glob
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path


def get_home_dir():
    """Returns the user home directory cross-platform."""
    user_prof = os.environ.get("USERPROFILE")
    if user_prof and os.path.isdir(user_prof):
        return os.path.normpath(user_prof)
    try:
        return str(Path.home())
    except Exception:
        return os.path.expanduser("~")


def get_documents_dir():
    """Returns user Documents directory cross-platform (Linux: ~/Documents, macOS: ~/Documents, Windows: %USERPROFILE%\\Documents)."""
    try:
        from qgis.PyQt.QtCore import QStandardPaths
        loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        if loc and os.path.isdir(loc):
            return os.path.normpath(loc)
    except Exception:  # nosec B110
        pass

    # Windows fallback
    if sys.platform == "win32":
        user_prof = os.environ.get("USERPROFILE")
        if user_prof:
            cand = os.path.join(user_prof, "Documents")
            if os.path.isdir(cand):
                return os.path.normpath(cand)
        one_drive = os.environ.get("OneDrive")
        if one_drive:
            cand = os.path.join(one_drive, "Documents")
            if os.path.isdir(cand):
                return os.path.normpath(cand)
        cand = os.path.expanduser("~/Documents")
        if os.path.isdir(cand):
            return os.path.normpath(cand)

    # macOS fallback
    if sys.platform == "darwin":
        cand = os.path.expanduser("~/Documents")
        if os.path.isdir(cand):
            return os.path.normpath(cand)

    # Unix fallback
    cand = os.path.expanduser("~/Documents")
    if os.path.isdir(cand):
        return os.path.normpath(cand)
    return get_home_dir()


_ACTIVE_PROJECT_DIR = None


def set_active_project_dir(path):
    """Record the active QGIS project directory for scratch, AI sessions, and autosave."""
    global _ACTIVE_PROJECT_DIR
    if path:
        _ACTIVE_PROJECT_DIR = os.path.normpath(os.path.abspath(os.path.expanduser(str(path))))
        try:
            os.makedirs(_ACTIVE_PROJECT_DIR, exist_ok=True)
        except Exception:  # nosec B110
            pass
    else:
        _ACTIVE_PROJECT_DIR = None
    return _ACTIVE_PROJECT_DIR


def get_active_project_dir():
    """Return the active QGIS project directory if set, else None."""
    return _ACTIVE_PROJECT_DIR


def get_configured_output_dir():
    """Return the user-configured output directory (env override -> QSettings -> default)."""
    env_dir = os.environ.get("QGIS_AI_OUTPUT_DIR", "").strip()
    if env_dir:
        target = os.path.normpath(os.path.abspath(os.path.expanduser(env_dir)))
        try:
            os.makedirs(target, exist_ok=True)
        except Exception:  # nosec B110
            pass
        return target
    try:
        from qgis.PyQt.QtCore import QSettings
        saved = str(QSettings().value("qgis_ai_agent/outdir", "") or "").strip()
        if saved:
            target = os.path.normpath(os.path.abspath(os.path.expanduser(saved)))
            os.makedirs(target, exist_ok=True)
            return target
    except Exception:  # nosec B110
        pass
    return get_default_output_dir()


def resolve_project_dir(project_file=None):
    """Resolve the active project root directory from a .qgz/.qgs path or _ACTIVE_PROJECT_DIR."""
    if project_file and os.path.isabs(str(project_file)):
        return os.path.normpath(os.path.dirname(os.path.abspath(str(project_file))))
    if _ACTIVE_PROJECT_DIR:
        return _ACTIVE_PROJECT_DIR
    return None


def get_scratch_dir(project_file=None):
    """Return the persistent scratch directory inside the active project dir (no /tmp or memory layers)."""
    pdir = resolve_project_dir(project_file)
    sdir = os.path.join(pdir, "scratch") if pdir else os.path.join(get_configured_output_dir(), "_scratch")
    try:
        os.makedirs(sdir, exist_ok=True)
    except Exception:  # nosec B110
        pass
    return os.path.normpath(sdir)


def get_ai_project_dir(project_file=None, project_id=None):
    """Return the AI session/workspace directory inside the active project dir when available."""
    pdir = resolve_project_dir(project_file)
    if pdir:
        adir = os.path.join(pdir, "ai")
    else:
        adir = os.path.join(get_projects_dir(), project_id or "default")
    try:
        os.makedirs(adir, exist_ok=True)
    except Exception:  # nosec B110
        pass
    return os.path.normpath(adir)


def get_default_output_dir():
    """Returns the standardized cross-platform default directory for generated maps, layouts, and exports:
    - Linux: ~/Documents/qgis-ai-maps
    - macOS: ~/Documents/qgis-ai-maps
    - Windows: %USERPROFILE%\\Documents\\qgis-ai-maps
    """
    doc_dir = get_documents_dir()
    target_dir = os.path.join(doc_dir, "qgis-ai-maps")
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception:  # nosec B110
        pass
    return os.path.normpath(target_dir)


def get_app_data_dir():
    """Returns the base data directory for QGIS AI Agent."""
    home = get_home_dir()
    legacy_dir = os.path.join(home, ".qgis_ai_agent")
    if os.path.exists(legacy_dir):
        return legacy_dir

    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata and os.path.isdir(appdata):
            return os.path.join(appdata, "qgis_ai_agent")

    return legacy_dir


def get_projects_dir():
    """Returns projects session directory."""
    return os.path.join(get_app_data_dir(), "projects")


def get_skills_dir():
    """Returns user custom skills directory."""
    return os.path.join(get_app_data_dir(), "skills")


def get_endpoint_file():
    """Returns endpoint.json path for MCP bridge server."""
    return os.path.join(get_app_data_dir(), "endpoint.json")


def get_models_cache_file():
    """Returns persistent cache file path for dynamically discovered CLI models."""
    return os.path.join(get_app_data_dir(), "models_cache.json")


def get_workspace_project_slugs(workspace_dir):
    """Generates cross-platform project directory slugs as encoded by Claude Code / CLIs.
    Example:
      /home/randy/qgis-ai-agent -> ['-home-randy-qgis-ai-agent']
      C:\\Users\\randy\\qgis-ai-agent -> ['C--Users-randy-qgis-ai-agent', 'c-users-randy-qgis-ai-agent', ...]
    """
    if not workspace_dir:
        return []
    raw = os.path.abspath(workspace_dir)
    real = os.path.realpath(raw)
    slugs = set()

    for p in (raw, real):
        clean = p.replace(":", "").replace("\\", "-").replace("/", "-")
        if not clean.startswith("-"):
            clean = "-" + clean
        slugs.add(clean)

        colon_clean = p.replace(":", "-").replace("\\", "-").replace("/", "-")
        slugs.add(colon_clean)
        slugs.add(colon_clean.lower())

        import re
        slugs.add(re.sub(r"-+", "-", clean))
        slugs.add(re.sub(r"-+", "-", colon_clean))

    return list(slugs)


def get_candidate_cli_dirs(cli_key=""):
    """Returns existing candidate directories for transcripts, configs, and session caches across OSes."""
    home = get_home_dir()
    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    user_prof = os.environ.get("USERPROFILE", "")

    dirs = []

    def _add(*parts):
        full = os.path.join(*[p for p in parts if p])
        if full and os.path.isdir(full) and full not in dirs:
            dirs.append(full)

    key = (cli_key or "").lower()

    # Antigravity CLI / Gemini
    if not key or "agy" in key or "antigravity" in key or "gemini" in key:
        _add(home, ".gemini", "antigravity-cli")
        _add(home, ".gemini", "antigravity")
        _add(home, ".gemini")
        if appdata:
            _add(appdata, "gemini", "antigravity-cli")
            _add(appdata, "gemini", "antigravity")
            _add(appdata, "gemini")
        if local_appdata:
            _add(local_appdata, "gemini", "antigravity-cli")
            _add(local_appdata, "gemini", "antigravity")
            _add(local_appdata, "gemini")
        if user_prof:
            _add(user_prof, ".gemini", "antigravity-cli")
            _add(user_prof, ".gemini", "antigravity")

    # Claude Code
    if not key or "claude" in key:
        _add(home, ".claude")
        if appdata:
            _add(appdata, "Claude")
            _add(appdata, "claude")
        if local_appdata:
            _add(local_appdata, "Claude")
        if user_prof:
            _add(user_prof, ".claude")

    # Codex CLI
    if not key or "codex" in key:
        _add(home, ".codex")
        if appdata:
            _add(appdata, "codex")
        if user_prof:
            _add(user_prof, ".codex")

    # OpenCode
    if not key or "opencode" in key:
        _add(home, ".opencode")
        _add(home, ".config", "opencode")
        if appdata:
            _add(appdata, "opencode")
        if user_prof:
            _add(user_prof, ".opencode")

    # Copilot
    if not key or "copilot" in key:
        _add(home, ".config", "github-copilot")
        if local_appdata:
            _add(local_appdata, "github-copilot")
        if appdata:
            _add(appdata, "github-copilot")

    # Cursor
    if not key or "cursor" in key:
        _add(home, ".cursor")
        if appdata:
            _add(appdata, "cursor")
        if user_prof:
            _add(user_prof, ".cursor")

    # DeepSeek / Hermes
    if not key or "deepseek" in key or "hermes" in key or "dsh" in key:
        _add(home, ".hermes")
        _add(home, ".deepseek")
        _add(home, ".dsh")
        if appdata:
            _add(appdata, "hermes")
            _add(appdata, "deepseek")
        if user_prof:
            _add(user_prof, ".hermes")

    return dirs


def get_subprocess_kwargs():
    """Returns extra kwargs for subprocess calls to prevent console window flashes on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def _is_windows_store_stub(path):
    """Returns True if path points to the broken WindowsApps Python store alias."""
    if sys.platform != "win32" or not path:
        return False
    return "windowsapps" in os.path.normcase(os.path.abspath(path))


def get_python_executable():
    """Resolves a real Python interpreter across Windows, macOS, and Linux.
    Avoids returning qgis-bin.exe, macOS QGIS binary, or WindowsApps store stubs.
    """
    exe = sys.executable or ""
    base = os.path.basename(exe).lower()
    if base.startswith("python") and not _is_windows_store_stub(exe) and os.path.isfile(exe):
        return exe

    candidates = []
    search_roots = []
    for root in (
        os.environ.get("OSGEO4W_ROOT"),
        sys.prefix,
        sys.exec_prefix,
        os.path.dirname(exe) if exe else None,
    ):
        if root and root not in search_roots:
            search_roots.append(root)

    if sys.platform == "win32":
        for root in search_roots:
            candidates.extend([
                os.path.join(root, "bin", "python3.exe"),
                os.path.join(root, "bin", "python.exe"),
                os.path.join(root, "python3.exe"),
                os.path.join(root, "python.exe"),
            ])
            candidates.extend(sorted(glob.glob(os.path.join(root, "apps", "Python*", "python3.exe")), reverse=True))
            candidates.extend(sorted(glob.glob(os.path.join(root, "apps", "Python*", "python.exe")), reverse=True))
    elif sys.platform == "darwin":
        for root in search_roots:
            candidates.extend([
                os.path.join(root, "bin", "python3"),
                os.path.join(root, "bin", "python"),
                os.path.normpath(os.path.join(root, "..", "Resources", "python", "bin", "python3")),
                os.path.normpath(os.path.join(root, "..", "Resources", "python", "bin", "python")),
            ])
    else:
        for root in search_roots:
            candidates.extend([
                os.path.join(root, "bin", "python3"),
                os.path.join(root, "bin", "python"),
            ])

    for cand in candidates:
        if cand and os.path.isfile(cand) and not _is_windows_store_stub(cand):
            return cand

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and not _is_windows_store_stub(found):
            return found

    return exe or "python3"


def get_extra_bin_paths():
    """Returns extra binary directories for AI CLIs across Linux, macOS, and Windows."""
    home = get_home_dir()
    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", "")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", "")

    extra_paths = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".opencode", "bin"),
        os.path.join(home, ".npm-global", "bin"),
        os.path.join(home, ".cursor", "bin"),
        os.path.join(home, ".cargo", "bin"),
        os.path.join(home, ".volta", "bin"),
        os.path.join(home, ".bun", "bin"),
        os.path.join(home, ".asdf", "shims"),
        os.path.join(home, ".local", "share", "mise", "shims"),
        os.path.join(home, ".local", "share", "pnpm"),
        os.path.join(home, "Library", "pnpm"),
    ]

    # Dynamic Node version managers (newest versions prioritized)
    for pattern in (
        os.path.join(home, ".nvm", "versions", "node", "*", "bin"),
        os.path.join(home, ".local", "share", "fnm", "node-versions", "*", "installation", "bin"),
        os.path.join(home, "Library", "Application Support", "fnm", "node-versions", "*", "installation", "bin"),
    ):
        extra_paths.extend(sorted(glob.glob(pattern), reverse=True))

    if sys.platform != "win32":
        extra_paths.extend([
            "/opt/homebrew/bin",
            "/opt/homebrew/sbin",
            "/usr/local/bin",
            "/opt/local/bin",
            "/snap/bin",
        ])

    if sys.platform == "win32" or appdata or local_appdata:
        if appdata:
            extra_paths.append(os.path.join(appdata, "npm"))
            extra_paths.extend(sorted(glob.glob(os.path.join(appdata, "nvm", "*")), reverse=True))
        if local_appdata:
            extra_paths.extend([
                os.path.join(local_appdata, "npm"),
                os.path.join(local_appdata, "pnpm"),
                os.path.join(local_appdata, "Volta", "bin"),
                os.path.join(local_appdata, "Programs"),
                os.path.join(local_appdata, "Programs", "cursor", "resources", "app", "bin"),
                os.path.join(local_appdata, "Programs", "Antigravity", "bin"),
                os.path.join(local_appdata, "AnthropicClaude", "bin"),
            ])
            extra_paths.extend(sorted(glob.glob(os.path.join(local_appdata, "fnm_multishells", "*")), reverse=True))
        if program_files:
            extra_paths.append(os.path.join(program_files, "nodejs"))
        if program_files_x86:
            extra_paths.append(os.path.join(program_files_x86, "nodejs"))

    seen = set()
    valid = []
    for p in extra_paths:
        if not p:
            continue
        norm = os.path.normpath(p)
        if norm not in seen and os.path.isdir(norm):
            seen.add(norm)
            valid.append(norm)
    return valid


def get_augmented_path_env(base_env=None):
    """Returns an environment dict with augmented PATH containing CLI bin locations."""
    env = dict(base_env if base_env is not None else os.environ)
    valid = get_extra_bin_paths()
    cur_path = env.get("PATH", "")
    if valid:
        existing_parts = [p for p in cur_path.split(os.pathsep) if p] if cur_path else []
        merged = []
        seen = set()
        for p in valid + existing_parts:
            if p not in seen:
                seen.add(p)
                merged.append(p)
        env["PATH"] = os.pathsep.join(merged)
    return env
