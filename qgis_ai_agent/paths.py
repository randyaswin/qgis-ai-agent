"""Cross-platform directory and path resolution for Windows, macOS, and Linux.

Provides unified discovery of:
1. Documents directory (QStandardPaths DocumentsLocation)
2. App data directory (.qgis_ai_agent or OS-specific AppData)
3. AI CLI transcript, project, and session caches across all operating systems.
"""
import os
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
    """Returns user Documents directory (e.g. C:\\Users\\Name\\Documents or ~/Documents)."""
    try:
        from qgis.PyQt.QtCore import QStandardPaths
        loc = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        if loc and os.path.isdir(loc):
            return os.path.normpath(loc)
    except Exception:
        pass

    # Windows fallback
    if sys.platform == "win32":
        user_prof = os.environ.get("USERPROFILE")
        if user_prof:
            cand = os.path.join(user_prof, "Documents")
            if os.path.isdir(cand):
                return cand
        cand = os.path.expanduser("~/Documents")
        if os.path.isdir(cand):
            return cand

    # Unix fallback
    cand = os.path.expanduser("~/Documents")
    if os.path.isdir(cand):
        return cand
    return get_home_dir()


def get_default_output_dir():
    """Returns the default directory for generated maps, layouts, and exports."""
    doc_dir = get_documents_dir()
    return os.path.join(doc_dir, "qgis-ai-maps")


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


def get_extra_bin_paths():
    """Returns extra binary directories for AI CLIs across Linux, macOS, and Windows."""
    home = get_home_dir()
    extra_paths = [
        os.path.join(home, ".local", "bin"),
        os.path.join(home, ".npm-global", "bin"),
        os.path.join(home, ".opencode", "bin"),
        os.path.join(home, ".cursor", "bin"),
        os.path.join(home, ".cargo", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        os.path.join(os.environ.get("APPDATA", ""), "npm"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "npm"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
    ]
    return [p for p in extra_paths if p and os.path.isdir(p)]


def get_augmented_path_env(base_env=None):
    """Returns an environment dict with augmented PATH containing CLI bin locations."""
    env = dict(base_env if base_env is not None else os.environ)
    valid = get_extra_bin_paths()
    cur_path = env.get("PATH", "")
    if valid:
        env["PATH"] = os.pathsep.join(valid + ([cur_path] if cur_path else []))
    return env
