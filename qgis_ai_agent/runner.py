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
import threading
import time
import uuid

from qgis.PyQt.QtCore import QObject, QProcess, QProcessEnvironment, QThread, pyqtSignal

from . import paths

GUARD = os.path.join(os.path.dirname(__file__), "guard.py")
BRIDGE = os.path.join(os.path.dirname(__file__), "mcp_bridge.py")
PY = paths.get_python_executable()


def find_cli_bin(bin_name):
    """Finds CLI binary across standard and user npm/local/cargo paths cross-platform."""
    if not bin_name:
        return bin_name
    aug_path = paths.get_augmented_path_env().get("PATH", "")
    p = shutil.which(bin_name, path=aug_path) or shutil.which(bin_name)
    if p:
        if sys.platform == "win32" and not os.path.splitext(p)[1]:
            for ext in (".exe", ".cmd", ".bat"):
                if os.path.isfile(p + ext):
                    return p + ext
        return p
    for ep in paths.get_extra_bin_paths():
        if sys.platform == "win32":
            for ext in (".exe", ".cmd", ".bat"):
                candidate_cmd = os.path.join(ep, bin_name + ext)
                if os.path.isfile(candidate_cmd):
                    return candidate_cmd
        candidate = os.path.join(ep, bin_name)
        if os.path.isfile(candidate) and (os.access(candidate, os.X_OK) or sys.platform == "win32"):
            return candidate
    return bin_name


# In-memory cache for CLI models: cli_key -> [(label, id), ...]
_CLI_MODELS_CACHE = {}
_MODELS_CACHE_LOCK = threading.Lock()

# Curated offline fallback catalog (only used when no live CLI catalog or disk cache is available)
DEFAULT_ADAPTER_MODELS = {
    "agy (Antigravity CLI)": [
        ("Gemini 4 Argon (High)", "gemini-4-argon-high"),
        ("Gemini 4 Argon (Medium)", "gemini-4-argon-medium"),
        ("Gemini 4 Argon (Low)", "gemini-4-argon-low"),
        ("Gemini 4 Hydrogen (XHigh)", "gemini-4-hydrogen-xhigh"),
        ("Gemini 4 Hydrogen (High)", "gemini-4-hydrogen-high"),
        ("Gemini 4 Hydrogen (Medium)", "gemini-4-hydrogen-medium"),
        ("Gemini 4 Hydrogen (Low)", "gemini-4-hydrogen-low"),
        ("Agent Orchestration (Argon)", "gemini-pro-orchestrator-g1-pro"),
        ("Gemini 3.8 Flash (High)", "gemini-3.8-flash-high"),
        ("Gemini 3.8 Flash (Medium)", "gemini-3.8-flash-medium"),
        ("Gemini 3.8 Flash (Low)", "gemini-3.8-flash-low"),
        ("Claude Opus 5.5 (High)", "claude-opus-5-5-high"),
        ("Claude Opus 5.5 (Medium)", "claude-opus-5-5-medium"),
        ("Claude Opus 5.5 (Low)", "claude-opus-5-5-low"),
        ("Claude Sonnet 5.5 (High)", "claude-sonnet-5-5-high"),
        ("Claude Sonnet 5.5 (Medium)", "claude-sonnet-5-5-medium"),
        ("Claude Sonnet 5.5 (Low)", "claude-sonnet-5-5-low"),
        ("GPT-OSS 120B (Medium)", "gpt-oss-120b-medium"),
    ],
    "Claude Code": [
        ("Claude Opus 5.5 (claude-opus-5-5)", "claude-opus-5-5"),
        ("Claude Fable 5.2 (claude-fable-5-2)", "claude-fable-5-2"),
        ("Claude Sonnet 5.5 (claude-sonnet-5-5)", "claude-sonnet-5-5"),
        ("Claude Haiku 5.5 (claude-haiku-5-5)", "claude-haiku-5-5"),
        ("Claude Fable 5.1 (claude-fable-5-1)", "claude-fable-5-1"),
        ("Claude Sonnet 4.6 (claude-sonnet-4-6)", "claude-sonnet-4-6"),
        ("Claude Opus 4.6 (claude-opus-4-6)", "claude-opus-4-6"),
        ("Claude Haiku 4.5 (claude-haiku-4-5)", "claude-haiku-4-5"),
        ("Claude Opus (Alias: opus)", "opus"),
        ("Claude Sonnet (Alias: sonnet)", "sonnet"),
        ("Claude Haiku (Alias: haiku)", "haiku"),
    ],
    "Gemini CLI": [
        ("Gemini 3.8 Flash", "gemini-3.8-flash"),
        ("Gemini 3.5 Flash", "gemini-3.5-flash"),
        ("Gemini 3.1 Pro Preview", "gemini-3.1-pro-preview"),
        ("Gemini 3 Pro Preview", "gemini-3-pro-preview"),
        ("Gemini 3 Flash Preview", "gemini-3-flash-preview"),
        ("Gemini 2.5 Pro", "gemini-2.5-pro"),
        ("Gemini 2.5 Flash", "gemini-2.5-flash"),
        ("Gemma 4 31B IT", "gemma-4-31b-it"),
    ],
    "Codex CLI": [
        ("gpt-5.3-codex", "gpt-5.3-codex"),
        ("gpt-5.2-codex", "gpt-5.2-codex"),
        ("gpt-5.2", "gpt-5.2"),
        ("gpt-5.1-codex-max", "gpt-5.1-codex-max"),
        ("gpt-5.1-codex-mini", "gpt-5.1-codex-mini"),
        ("GPT-5.4", "gpt-5.4"),
        ("GPT-5.4 Mini", "gpt-5.4-mini"),
        ("o3-mini", "o3-mini"),
    ],
    "GitHub Copilot CLI": [
        ("Claude 3.7 Sonnet", "claude-3.7-sonnet"),
        ("Claude 3.5 Sonnet", "claude-3.5-sonnet"),
        ("GPT-4o", "gpt-4o"),
        ("o3-mini", "o3-mini"),
    ],
    "OpenCode": [
        ("DeepSeek V4.1 Flash (Free)", "opencode/deepseek-v4.1-flash-free"),
        ("MIMO V2.6 Flash (Free)", "opencode/mimo-v2.6-flash-free"),
        ("Nemotron 3.5 Lightning (Free)", "opencode/nemotron-3.5-lightning-free"),
        ("Beam (Free)", "opencode/beam-free"),
        ("Flint 40B (Free)", "opencode/flint-40b-free"),
        ("GLM 5.3 Flash (Free)", "opencode/glm-5.3-flash-free"),
    ],
    "DeepSeek / Hermes CLI": [
        ("DeepSeek V3 (Chat)", "deepseek-chat"),
        ("DeepSeek R1 (Reasoner)", "deepseek-reasoner"),
        ("Hermes 3 (Llama 3.1)", "hermes-3"),
    ],
    "Cursor Agent": [
        ("Claude 3.7 Sonnet", "claude-3.7-sonnet"),
        ("Claude 3.5 Sonnet", "claude-3.5-sonnet"),
        ("GPT-4o", "gpt-4o"),
        ("Cursor Small", "cursor-small"),
    ],
    "Qwen Code": [
        ("Qwen Max", "qwen-max"),
        ("Qwen Plus", "qwen-plus"),
        ("Qwen Turbo", "qwen-turbo"),
        ("Qwen 2.5 Coder 32B", "qwen-2.5-coder-32b"),
    ],
    "Trae CLI": [
        ("Claude 3.7 Sonnet", "claude-3.7-sonnet"),
        ("GPT-4o", "gpt-4o"),
    ],
    "Kimi Code": [
        ("Kimi K1.5", "kimi-k1.5"),
        ("Moonshot v1 128k", "moonshot-v1-128k"),
    ],
    "Aider": [
        ("Claude 3.7 Sonnet", "sonnet"),
        ("GPT-4o", "gpt-4o"),
        ("DeepSeek V3", "deepseek/deepseek-chat"),
        ("Gemini 2.5 Pro", "gemini/gemini-2.5-pro"),
    ],
}


def _load_disk_models_cache():
    """Load persisted CLI model discovery cache from disk."""
    cache_path = paths.get_models_cache_file()
    if not os.path.exists(cache_path):
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_disk_models_cache(cli_key, models, default_model=""):
    """Persist discovered models for a CLI to disk atomically."""
    if not cli_key or not models:
        return
    cache_path = paths.get_models_cache_file()
    cache_dir = os.path.dirname(cache_path)
    with _MODELS_CACHE_LOCK:
        try:
            os.makedirs(cache_dir, exist_ok=True)
            data = _load_disk_models_cache()
            data[cli_key] = {
                "default_model": default_model or "",
                "models": [[str(lbl), str(val)] for lbl, val in models if val],
                "updated_at": time.time(),
            }
            fd, tmp_path = tempfile.mkstemp(prefix=".models_cache_", suffix=".tmp", dir=cache_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    try:
                        os.fsync(f.fileno())
                    except Exception:
                        pass
                os.replace(tmp_path, cache_path)
            except Exception:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
        except Exception:
            pass


def _strip_ansi_line(raw_line):
    clean = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", raw_line)
    if "\r" in clean:
        clean = clean.rsplit("\r", 1)[-1]
    return clean.strip()


def _discover_agy_models(bin_path, allow_subprocess=True):
    """Discover configured default and live model catalog from Antigravity CLI (`agy models`)."""
    configured_default = ""
    agy_dirs = [d for d in paths.get_candidate_cli_dirs("agy") if "antigravity" in d.lower()]
    for adir in agy_dirs:
        cfg_path = os.path.join(adir, "settings.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                m = sdata.get("model")
                if isinstance(m, str) and m.strip():
                    configured_default = m.strip()
                    break
            except Exception:
                pass

    discovered = []
    catalog_discovered = False
    if allow_subprocess and bin_path:
        try:
            env = paths.get_augmented_path_env()
            env["NO_COLOR"] = "1"
            res = subprocess.run(
                [bin_path, "models"],
                capture_output=True,
                text=True,
                timeout=15.0,
                env=env,
                **paths.get_subprocess_kwargs(),
            )
            if res.returncode == 0 and res.stdout:
                for raw_line in res.stdout.replace("\r\n", "\n").splitlines():
                    line = _strip_ansi_line(raw_line)
                    if not line or "Fetching" in line or "..." in line or line.startswith(("Usage:", "Flags:", "Available")):
                        continue
                    parts = line.split(None, 1)
                    if not parts:
                        continue
                    m_id = parts[0].strip()
                    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$", m_id):
                        continue
                    m_label = parts[1].strip() if len(parts) > 1 else m_id
                    if "orchestrator" in m_id.lower() and "agent orchestration" not in m_label.lower():
                        m_label = f"Agent Orchestration — {m_label}"
                    if not any(m_id == x[1] for x in discovered):
                        discovered.append((m_label, m_id))
                if discovered:
                    catalog_discovered = True
        except Exception:
            pass

    return discovered, configured_default, catalog_discovered


def _discover_opencode_models(bin_path, allow_subprocess=True):
    """Discover configured default and live models from OpenCode (`opencode models`)."""
    configured_default = ""
    for odir in paths.get_candidate_cli_dirs("opencode"):
        cfg_path = os.path.join(odir, "opencode.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    odata = json.load(f)
                m = odata.get("model")
                if isinstance(m, str) and m.strip():
                    configured_default = m.strip()
                    break
            except Exception:
                pass

    discovered = []
    catalog_discovered = False
    if allow_subprocess:
        home = paths.get_home_dir()
        oc_exe = "opencode.exe" if sys.platform == "win32" else "opencode"
        cand_bins = []
        for cand in (bin_path, os.path.join(home, ".opencode", "bin", oc_exe), os.path.join(home, ".opencode", "bin", "opencode")):
            if cand and cand not in cand_bins and (os.path.isfile(cand) or cand == bin_path):
                cand_bins.append(cand)

        env = paths.get_augmented_path_env()
        env["NO_COLOR"] = "1"
        for cand in cand_bins:
            try:
                res = subprocess.run(
                    [cand, "models"],
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    env=env,
                    **paths.get_subprocess_kwargs(),
                )
                if res.returncode == 0 and res.stdout:
                    parsed = []
                    for raw_line in res.stdout.splitlines():
                        line = _strip_ansi_line(raw_line)
                        if not line or line.startswith("#") or " " in line or line.startswith(("Error", "Usage:")):
                            continue
                        parts = line.split("/")
                        short_lbl = parts[-1] if len(parts) > 1 else line
                        if not any(line == x[1] for x in parsed):
                            parsed.append((f"{short_lbl} ({line})", line))
                    if parsed:
                        discovered = parsed
                        catalog_discovered = True
                        break
            except Exception:
                pass

    return discovered, configured_default, catalog_discovered


def _discover_claude_models():
    """Discover Claude Code catalog and user-configured models from local cache & settings."""
    configured_default = ""
    discovered = []
    catalog_discovered = False
    claude_dirs = paths.get_candidate_cli_dirs("claude")

    # 1. Inspect settings.json and cache/model-catalog/*.json
    extra_setting_ids = []
    catalog_files = []
    for cdir in claude_dirs:
        settings_path = os.path.join(cdir, "settings.json")
        if os.path.isfile(settings_path):
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                if not configured_default and isinstance(sdata.get("model"), str) and sdata["model"].strip():
                    configured_default = sdata["model"].strip()
                msettings = sdata.get("modelSettings")
                if isinstance(msettings, dict):
                    for k in msettings.keys():
                        if isinstance(k, str) and k.strip():
                            extra_setting_ids.append(k.strip())
            except Exception:
                pass
        catalog_files.extend(glob.glob(os.path.join(cdir, "cache", "model-catalog", "*.json")))

    # Newest model-catalog first
    catalog_files = sorted(set(catalog_files), key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0, reverse=True)
    for cat_path in catalog_files:
        try:
            with open(cat_path, "r", encoding="utf-8") as f:
                cdata = json.load(f)
            models_list = cdata.get("catalog", {}).get("config", {}).get("models", [])
            if not isinstance(models_list, list):
                continue
            main_entries = []
            other_entries = []
            for m in models_list:
                if not isinstance(m, dict):
                    continue
                m_id = str(m.get("id") or "").strip()
                if not m_id:
                    continue
                name = str(m.get("name") or m.get("short_name") or m_id).strip()
                lbl = name if name.lower().startswith("claude") else f"Claude {name}"
                entry = (f"{lbl} ({m_id})", m_id)
                if m.get("section") == "main":
                    main_entries.append(entry)
                else:
                    other_entries.append(entry)
            for entry in main_entries + other_entries:
                if not any(entry[1] == x[1] for x in discovered):
                    discovered.append(entry)
            if discovered:
                catalog_discovered = True
                break
        except Exception:
            pass

    # 2. Check claude.json / ~/.claude.json for org defaults & additionalModelOptionsCache
    candidate_configs = [os.path.join(d, "claude.json") for d in claude_dirs] + [os.path.join(paths.get_home_dir(), ".claude.json")]
    for cfg_path in candidate_configs:
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                cfg_m = cdata.get("model") or cdata.get("orgModelDefaultCache")
                if not configured_default and isinstance(cfg_m, str) and cfg_m.strip():
                    configured_default = cfg_m.strip()
                add_models = cdata.get("additionalModelOptionsCache") or []
                for am in add_models:
                    if isinstance(am, dict) and am.get("value"):
                        val = str(am["value"]).strip()
                        lbl = str(am.get("label") or val).strip()
                        if val and not any(val == m[1] for m in discovered):
                            discovered.append((f"{lbl} ({val})", val))
            except Exception:
                pass

    for m_id in extra_setting_ids:
        if not any(m_id == m[1] for m in discovered):
            pretty = m_id.replace("-", " ").title()
            discovered.append((f"{pretty} ({m_id})", m_id))

    if catalog_discovered or discovered:
        for lbl, alias_id in (
            ("Claude Opus (Alias: opus)", "opus"),
            ("Claude Sonnet (Alias: sonnet)", "sonnet"),
            ("Claude Haiku (Alias: haiku)", "haiku"),
        ):
            if not any(alias_id == m[1] for m in discovered):
                discovered.append((lbl, alias_id))

    return discovered, configured_default, catalog_discovered


def _format_gemini_label(model_id):
    tokens = model_id.split("-")
    out = []
    for tok in tokens:
        low = tok.lower()
        if low in ("it", "b"):
            out.append(low.upper())
        elif re.match(r"^\d+[bB]$", tok):
            out.append(tok.upper())
        elif re.match(r"^[a-z]\d+[a-z]$", low):
            out.append(tok.upper())
        else:
            out.append(tok.capitalize())
    return " ".join(out)


def _gemini_sort_key(model_id):
    low = model_id.lower()
    fam_rank = 2 if low.startswith("gemini") else 1
    ver_match = re.search(r"^(?:gemini|gemma)-(\d+(?:\.\d+)*)", low)
    ver_tuple = tuple(int(x) for x in ver_match.group(1).split(".")) if ver_match else (0,)
    if "pro" in low:
        tier_rank = 3
    elif "flash-lite" in low:
        tier_rank = 1
    elif "flash" in low:
        tier_rank = 2
    else:
        tier_rank = 0
    stable_rank = 0 if ("preview" in low or "customtools" in low) else 1
    return (fam_rank, ver_tuple, tier_rank, stable_rank, model_id)


def _discover_gemini_models(bin_path, allow_heavy_scan=True):
    """Discover Gemini CLI configured model and valid models from its installed JS bundle."""
    configured_default = ""
    gemini_dirs = [d for d in paths.get_candidate_cli_dirs("gemini") if "antigravity" not in d.lower()]
    for gdir in gemini_dirs:
        cfg_path = os.path.join(gdir, "settings.json")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    sdata = json.load(f)
                custom_m = sdata.get("model")
                if isinstance(custom_m, dict):
                    custom_m = custom_m.get("name") or custom_m.get("default")
                if isinstance(custom_m, str) and custom_m.strip():
                    configured_default = custom_m.strip()
                    break
            except Exception:
                pass

    discovered = []
    catalog_discovered = False
    if allow_heavy_scan and bin_path:
        bundle_dirs = []
        roots_to_check = []
        for p in (bin_path, os.path.realpath(bin_path) if os.path.exists(bin_path) else None):
            if p and p not in roots_to_check:
                roots_to_check.append(p)
        for p in roots_to_check:
            pdir = os.path.dirname(p)
            for cand in (
                pdir if p.endswith((".js", ".mjs", ".cjs")) else None,
                os.path.join(pdir, "node_modules", "@google", "gemini-cli", "bundle"),
                os.path.join(os.path.dirname(pdir), "lib", "node_modules", "@google", "gemini-cli", "bundle"),
            ):
                if cand and os.path.isdir(cand) and cand not in bundle_dirs:
                    bundle_dirs.append(cand)

        model_ids = []
        for bdir in bundle_dirs:
            files = sorted(glob.glob(os.path.join(bdir, "chunk-*.js"))) + [os.path.join(bdir, "gemini.js")]
            for fn in files:
                if not os.path.isfile(fn):
                    continue
                try:
                    with open(fn, "r", encoding="utf-8", errors="ignore") as f:
                        txt = f.read()
                    idx = txt.find("VALID_GEMINI_MODELS")
                    if idx < 0:
                        continue
                    snippet = txt[max(0, idx - 2500):idx + 1500]
                    for m in re.finditer(r'var\s+[A-Z0-9_]+_MODEL\s*=\s*["\']([^"\']+)["\']', snippet):
                        m_id = m.group(1).strip()
                        if m_id and m_id != "none" and m_id not in model_ids:
                            model_ids.append(m_id)
                    set_match = re.search(r"VALID_GEMINI_MODELS\s*=\s*(?:/\*[^*]*\*/\s*)?new Set\(\[([^\]]+)\]\)", snippet)
                    if set_match:
                        for qm in re.finditer(r'["\']((?:gemini|gemma)-[a-z0-9._-]+)["\']', set_match.group(1)):
                            m_id = qm.group(1).strip()
                            if m_id and m_id not in model_ids:
                                model_ids.append(m_id)
                    if model_ids:
                        break
                except Exception:
                    pass
            if model_ids:
                break

        if model_ids:
            model_ids.sort(key=_gemini_sort_key, reverse=True)
            for m_id in model_ids:
                discovered.append((f"{_format_gemini_label(m_id)} ({m_id})", m_id))
            catalog_discovered = True

    return discovered, configured_default, catalog_discovered


def _discover_codex_models(bin_path, allow_heavy_scan=True):
    """Discover Codex CLI configured model and embedded preset catalog from installed binary."""
    configured_default = ""
    for cdir in paths.get_candidate_cli_dirs("codex"):
        cfg_path = os.path.join(cdir, "config.toml")
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    content = f.read()
                match = re.search(r'^\s*model\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
                if match:
                    configured_default = match.group(1).strip()
                    break
            except Exception:
                pass

    discovered = []
    catalog_discovered = False
    if allow_heavy_scan and bin_path:
        cand_bins = []
        roots_to_check = []
        for p in (bin_path, os.path.realpath(bin_path) if os.path.exists(bin_path) else None):
            if p and p not in roots_to_check:
                roots_to_check.append(p)
        for p in roots_to_check:
            if os.path.isfile(p) and not p.endswith((".js", ".mjs", ".cjs", ".cmd", ".bat", ".ps1")) and p not in cand_bins:
                cand_bins.append(p)
            pdir = os.path.dirname(p)
            pkg_root = os.path.dirname(pdir)
            for pat in (
                os.path.join(pkg_root, "node_modules", "@openai", "codex-*", "vendor", "*", "codex", "codex*"),
                os.path.join(pdir, "node_modules", "@openai", "codex", "node_modules", "@openai", "codex-*", "vendor", "*", "codex", "codex*"),
                os.path.join(pkg_root, "lib", "node_modules", "@openai", "codex", "node_modules", "@openai", "codex-*", "vendor", "*", "codex", "codex*"),
            ):
                for b in glob.glob(pat):
                    if b not in cand_bins:
                        cand_bins.append(b)

        for b in cand_bins:
            if not os.path.isfile(b) or b.endswith((".js", ".cmd", ".bat", ".ps1", ".pdb")):
                continue
            try:
                if os.path.getsize(b) <= 100000:
                    continue
                with open(b, "rb") as f:
                    data = f.read()
                list_models = []
                other_models = []
                seen_slugs = set()
                for m in re.finditer(
                    rb'"slug":\s*"([^"]+)",\s*"display_name":\s*"([^"]+)",\s*"description":\s*"([^"]*)"[\s\S]{0,1200}?"visibility":\s*"([^"]+)"',
                    data,
                ):
                    slug = m.group(1).decode("utf-8", errors="ignore").strip()
                    disp = m.group(2).decode("utf-8", errors="ignore").strip() or slug
                    vis = m.group(4).decode("utf-8", errors="ignore").strip()
                    if not slug or slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)
                    entry = (disp, slug)
                    if vis == "list":
                        list_models.append(entry)
                    else:
                        other_models.append(entry)
                if list_models or other_models:
                    discovered = list_models + other_models
                    catalog_discovered = True
                    break
            except Exception:
                pass

    return discovered, configured_default, catalog_discovered


def get_cli_models(cli_key, force_refresh=False, allow_subprocess=True):
    """Dynamically discover models available for a given AI CLI.
    Returns list of (display_label, model_id) tuples.
    Default option is ALWAYS first with empty model_id ('') so no model is forced.
    """
    if not force_refresh:
        with _MODELS_CACHE_LOCK:
            if cli_key in _CLI_MODELS_CACHE:
                return _CLI_MODELS_CACHE[cli_key]

    adapter = ADAPTERS.get(cli_key, {})
    bin_name = adapter.get("bin", "")
    bin_path = find_cli_bin(bin_name)

    discovered = []
    configured_default = ""
    catalog_discovered = False
    live_discovered = False

    # 1. Antigravity CLI: live `agy models` + settings.json
    if "agy" in bin_name.lower():
        discovered, configured_default, live_discovered = _discover_agy_models(bin_path, allow_subprocess=allow_subprocess)
        catalog_discovered = live_discovered

    # 2. OpenCode CLI: live `opencode models` + opencode.json
    elif "opencode" in bin_name.lower():
        discovered, configured_default, live_discovered = _discover_opencode_models(bin_path, allow_subprocess=allow_subprocess)
        catalog_discovered = live_discovered

    # 3. Claude Code: model-catalog cache + settings.json + .claude.json
    elif "claude" in bin_name.lower():
        discovered, configured_default, live_discovered = _discover_claude_models()
        catalog_discovered = live_discovered

    # 4. DeepSeek / Hermes CLI: inspect ~/.hermes/config.yaml
    elif "deepseek" in bin_name.lower() or "hermes" in bin_name.lower():
        hermes_cfg = os.path.join(paths.get_home_dir(), ".hermes", "config.yaml")
        if os.path.isfile(hermes_cfg):
            try:
                with open(hermes_cfg, "r", encoding="utf-8") as f:
                    content = f.read()
                m_match = re.search(r"^\s*default\s*:\s*([^\s#]+)", content, re.MULTILINE)
                if m_match:
                    configured_default = m_match.group(1).strip()
            except Exception:
                pass

    # 5. Gemini CLI: non-antigravity settings.json + bundle JS models.ts catalog
    elif "gemini" in bin_name.lower():
        discovered, configured_default, live_discovered = _discover_gemini_models(bin_path, allow_heavy_scan=allow_subprocess)
        catalog_discovered = live_discovered

    # 6. Codex CLI: config.toml + embedded binary preset catalog
    elif "codex" in bin_name.lower():
        discovered, configured_default, live_discovered = _discover_codex_models(bin_path, allow_heavy_scan=allow_subprocess)
        catalog_discovered = live_discovered

    # Fallback to persisted disk cache when live discovery was skipped or offline
    if not catalog_discovered:
        disk_entry = _load_disk_models_cache().get(cli_key)
        if isinstance(disk_entry, dict):
            cached_models = [
                (str(item[0]), str(item[1]))
                for item in (disk_entry.get("models") or [])
                if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]
            ]
            if cached_models:
                for lbl, val in cached_models:
                    if not any(val == d[1] for d in discovered):
                        discovered.append((lbl, val))
                if not configured_default and disk_entry.get("default_model"):
                    configured_default = str(disk_entry["default_model"]).strip()
                catalog_discovered = True

    default_entry = (
        (f"Default (CLI: {configured_default})", "")
        if configured_default
        else ("Default (CLI / Account Default)", "")
    )
    result = [default_entry]
    for lbl, val in discovered:
        if val and not any(val == r[1] for r in result):
            result.append((lbl, val))

    if not catalog_discovered:
        for lbl, val in DEFAULT_ADAPTER_MODELS.get(cli_key, []):
            if val and not any(val == r[1] for r in result):
                result.append((lbl, val))

    if live_discovered and discovered:
        _save_disk_models_cache(cli_key, discovered, configured_default)

    if allow_subprocess or catalog_discovered:
        with _MODELS_CACHE_LOCK:
            _CLI_MODELS_CACHE[cli_key] = result

    return result


class ModelDiscoveryThread(QThread):
    models_ready = pyqtSignal(str, list)

    def __init__(self, cli_name, parent=None):
        super().__init__(parent)
        self.cli_name = cli_name
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        if self._cancelled:
            return
        models = get_cli_models(self.cli_name, force_refresh=True)
        if not self._cancelled:
            self.models_ready.emit(self.cli_name, models)


# name -> run(prompt, continue, auto_approve, model, effort) -> argv, plus MCP registration command.
ADAPTERS = {
    "agy (Antigravity CLI)": {
        "bin": "agy",
        "supports_effort": True,
        "run": lambda p, cont, auto, model=None, effort=None, session_id=None: (
            ["agy"]
            + ([f"--conversation={session_id}"] if (cont and session_id) else (["-c"] if cont else []))
            + (["--dangerously-skip-permissions"] if auto else [])
            + (["--model", model] if (model and model.strip()) else [])
            + (["--effort", effort.lower()] if effort and effort.lower() != "default" else [])
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
        "supports_effort": True,
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
            ([find_cli_bin("copilot")] if is_bin_available("copilot") else [find_cli_bin("gh"), "copilot"])
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
    found = find_cli_bin(bin_name)
    return bool(found and os.path.isabs(found) and os.path.exists(found))


def available():
    res = {}
    for k, v in ADAPTERS.items():
        bin_name = v.get("bin", "")
        if bin_name == "copilot":
            is_avail = is_bin_available("copilot") or is_bin_available("gh")
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
    from . import guard
    return guard._get_process_tree(root_pid, protected_pids)


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
                        stderr=subprocess.DEVNULL,
                        **paths.get_subprocess_kwargs(),
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
                if pg not in protected_pids and pg > 1:
                    all_pgids.add(pg)
            except Exception:
                pass
            # If setsid was used by child, pid itself is a process group leader
            if pid not in protected_pids and pid > 1:
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
        self._stopping = False

    def running(self):
        return self.proc is not None and self.proc.state() != QProcess.ProcessState.NotRunning

    def _cleanup_pid_file(self):
        if self._current_pid_file and os.path.exists(self._current_pid_file):
            try:
                os.remove(self._current_pid_file)
            except Exception:
                pass
        self._current_pid_file = None

    def _dispose_proc(self):
        proc = self.proc
        self.proc = None
        if not proc:
            return
        for sig_name in ("readyReadStandardOutput", "finished", "errorOccurred"):
            try:
                getattr(proc, sig_name).disconnect()
            except Exception:
                pass
        try:
            proc.deleteLater()
        except Exception:
            pass

    def _on_finished(self, code, _status=None):
        self._cleanup_pid_file()
        if self._stopping:
            return
        self.finished.emit(code)

    def start(self, argv, cwd):
        if self.running():
            raise RuntimeError("An agent is already running")
        self._dispose_proc()
        self._stopping = False
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("NO_COLOR", "1")

        # Create unique PID tracking file for this invocation
        self._current_pid_file = os.path.join(tempfile.gettempdir(), f"qgis_ai_{uuid.uuid4().hex[:12]}.pid")
        env.insert("QGIS_AI_PID_FILE", self._current_pid_file)
        env.insert("QGIS_AI_PARENT_PID", str(os.getpid()))

        # Cross-platform binary discovery (Linux, macOS Homebrew, Windows npm/AppData/Cargo)
        valid_paths = paths.get_extra_bin_paths()
        if valid_paths:
            env.insert("PATH", os.pathsep.join(valid_paths + [env.value("PATH")]))

        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(cwd)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(
            lambda: self.proc and self.output.emit(bytes(self.proc.readAllStandardOutput()).decode(errors="replace")))
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(lambda e: self.output.emit(f"\n[process error: {e}]\n"))

        bin_exec = find_cli_bin(argv[0])
        # Wrap CLI invocation with guard.py to guarantee termination if QGIS closes or crashes
        qgis_pid = str(os.getpid())
        guard_argv = [PY, GUARD, qgis_pid, cwd, bin_exec] + argv[1:]
        # Keep stdin pipe open so guard.py's stdin_eof_monitor detects stop() or unexpected QGIS exit
        self.proc.start(guard_argv[0], guard_argv[1:])

    def stop(self):
        self._stopping = True

        # Signal guard.py via stdin EOF first before escalating to tree kill
        if self.proc:
            try:
                self.proc.closeWriteChannel()
            except Exception:
                pass

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

        self._dispose_proc()
        self._cleanup_pid_file()
