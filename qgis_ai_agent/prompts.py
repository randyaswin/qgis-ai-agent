"""Prompt assembly: system prompt + live project context + relevant skills + user request."""
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def system_prompt(qgis_version, output_dir, mode):
    with open(os.path.join(HERE, "AGENT.md"), encoding="utf-8") as f:
        text = f.read()
    return text.replace("{qgis_version}", str(qgis_version)).replace("{output_dir}", output_dir).replace("{skill_mode}", mode)


def skills_block(sel, catalog_names):
    """sel: result of Registry.select(); catalog_names: names of other skills the agent can load."""
    parts = []
    full = sel.get("pinned", []) + sel.get("full", [])
    if full:
        banner = "# Loaded skills (follow these; they were selected for this request)"
        if sel.get("is_e2e"):
            banner += "\n> [!IMPORTANT] MANDATORY E2E MAP PIPELINE:\n> This request requires end-to-end map creation. You MUST strictly apply both the loaded Cartography skill (symbology, CVD-safe color harmony, visual hierarchy, label halos) and Layout skill (print layout composition, clean gutters, scalebar, north arrow, default A4 paper size). Zero Data Fabrication: Never invent mock data — you are fully free to search the internet to find real data from official agencies and open repositories, record provenance, and mandatorily embed real sources into the layout credits (`credits='Data Sources: ...'`). Finish by saving the project (`save_project`). Do NOT export (`export_layout`) unless the user explicitly requested an export file/image."
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


def build(system, context, skills_text, user, continuing, session_history=None):
    segs = []
    if not continuing and system:
        segs.append(system)
    if context:
        segs.append("# Current QGIS state (live, injected automatically)\n" + context)
    if session_history and not continuing:
        segs.append(session_history)
    if skills_text:
        segs.append(skills_text)
    req_header = "# User request"
    if not continuing and not session_history:
        req_header += " (If requirements, spatial methodology, or data sources are vague or underspecified, follow Protocol 0 to clarify with flexible, non-template questions and interactive options [🔘 Label](prompt:...) before executing tools)"
    else:
        req_header += " (Follow-up turn: if crucial methodological forks or data source choices still need confirmation, you are fully permitted to ask follow-up clarification under Protocol 0 before executing tools)"
    segs.append(f"{req_header}\n{user}")
    return "\n\n".join(segs)
