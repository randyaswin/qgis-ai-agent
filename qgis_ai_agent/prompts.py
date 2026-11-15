"""Prompt assembly: system prompt + live project context + relevant skills + user request."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
_AGENT_MD_CACHE = {"path": None, "mtime": None, "text": None}


def system_prompt(qgis_version, output_dir, mode):
    path = os.path.join(HERE, "AGENT.md")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = None
    if (
        _AGENT_MD_CACHE["text"] is None
        or _AGENT_MD_CACHE["path"] != path
        or _AGENT_MD_CACHE["mtime"] != mtime
    ):
        with open(path, encoding="utf-8") as f:
            _AGENT_MD_CACHE["text"] = f.read()
        _AGENT_MD_CACHE["path"] = path
        _AGENT_MD_CACHE["mtime"] = mtime
    text = _AGENT_MD_CACHE["text"]
    return text.replace("{qgis_version}", str(qgis_version)).replace("{output_dir}", output_dir).replace("{skill_mode}", mode)


def skills_block(sel, catalog_names):
    """sel: result of Registry.select(); catalog_names: names of other skills the agent can load."""
    parts = []
    full = sel.get("pinned", []) + sel.get("full", [])
    if full:
        banner = "# Loaded skills (follow these; they were selected for this request)"
        if sel.get("is_e2e"):
            banner += "\n> [!IMPORTANT] MANDATORY E2E MAP PIPELINE:\n> This request requires end-to-end map creation. Reason deeply across domain, geographic aspect ratio, figure-ground hierarchy, and layout composition—do NOT default blindly to a single fixed theme. Strictly apply both Cartography and Layout best practices (default A4, clean gutters, scalebar, north arrow, real source credits). After `snapshot_layout`, inspect BOTH the image and `qa` diagnostics and iterate (`update_layout_item`, `style_*`, `set_labels`) until `qa_warnings` is clean and the result is publication-grade. Zero Data Fabrication: Never invent mock data. Finish with `save_project`; do NOT call `export_layout` unless explicitly asked."
        parts.append(banner)
        for s in full:
            if isinstance(s, dict):
                s_name = s.get("skill") or s.get("name") or "unknown"
                s_body = s.get("content") or s.get("body") or ""
                s_refs = s.get("references", [])
            else:
                s_name = getattr(s, "name", str(s))
                s_body = s.body() if callable(getattr(s, "body", None)) else getattr(s, "body", str(s))
                s_refs = getattr(s, "references", [])

            tag = "pinned by user" if s in sel.get("pinned", []) else f"auto-selected, score {sel.get('scores', {}).get(s_name, 0):.1f}"
            refs = f"\nReferences (load with load_skill reference=...): {', '.join(s_refs)}" if s_refs else ""
            parts.append(f"## Skill: {s_name} ({tag})\n{s_body}{refs}")
    if sel.get("hint"):
        parts.append("# Also relevant (too large to inline; call `load_skill` if needed)\n" +
                     "\n".join(f"- {getattr(s, 'name', str(s))}: {getattr(s, 'description', '')[:200]}" for s in sel["hint"]))
    rest = [n for n in catalog_names if n not in {getattr(s, 'name', str(s)) for s in full + sel.get("hint", [])}]
    if rest:
        parts.append("# Other skills you can load on demand with `load_skill(name)` (use `list_skills(query)` to search)\n" +
                     ", ".join(rest))
    return "\n\n".join(parts)


def build(system, context, skills_text, user, continuing, session_history=None, is_vague=False):
    segs = []
    if not continuing and system:
        segs.append(system)
    if context:
        segs.append("# Current QGIS state (live, injected automatically)\n" + context)
    if session_history and not continuing:
        segs.append(session_history)
    if skills_text:
        segs.append(skills_text)
    if is_vague and not continuing:
        segs.append(
            "> [!IMPORTANT] MANDATORY CLARIFICATION TURN (PROTOCOL 0 — DO NOT EXECUTE MAP TOOLS YET):\n"
            "> The user's request is brief or underspecified (missing thematic focus/layers, preferred data sources, analytical method, and/or cartographic layout style).\n"
            "> - You MUST NOT call mutating or data-fetching tools (`add_basemap`, `query_overture_maps`, `load_public_cog`, `create_layout`, `create_styled_layout`, `run_processing`, `save_project`, etc.) on this turn.\n"
            "> - You may call read-only inspection (`get_project_state`, `describe_all_layers`) ONLY if the active QGIS project already contains layers.\n"
            "> - Reply directly to the user with 2 to 3 concise, tailored clarification questions in the user's language, including clickable interactive option chips (`[🔘 Label](prompt:Kategori: Nilai)`, `[✏️ Custom...](fill:Kategori: )`, and optionally `[⚡ Recommended Default](send:...)`)."
        )
    req_header = "# User request"
    if is_vague or (not continuing and not session_history):
        req_header += " (If requirements, spatial methodology, or data sources are vague or underspecified, follow Protocol 0 to clarify with flexible, non-template questions and interactive options [🔘 Label](prompt:...) before executing tools)"
    else:
        req_header += " (Follow-up turn: if crucial methodological forks or data source choices still need confirmation, you are fully permitted to ask follow-up clarification under Protocol 0 before executing tools)"
    segs.append(f"{req_header}\n{user}")
    return "\n\n".join(segs)
