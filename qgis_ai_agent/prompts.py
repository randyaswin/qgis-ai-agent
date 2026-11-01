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
    full = sel["pinned"] + sel["full"]
    if full:
        parts.append("# Loaded skills (follow these; they were selected for this request)")
        for s in full:
            tag = "pinned by user" if s in sel["pinned"] else f"auto-selected, score {sel['scores'].get(s.name, 0):.1f}"
            refs = f"\nReferences (load with load_skill reference=...): {', '.join(s.references)}" if s.references else ""
            parts.append(f"## Skill: {s.name} ({tag})\n{s.body()}{refs}")
    if sel["hint"]:
        parts.append("# Also relevant (too large to inline; call `load_skill` if needed)\n" +
                     "\n".join(f"- {s.name}: {s.description[:200]}" for s in sel["hint"]))
    rest = [n for n in catalog_names if n not in {s.name for s in full + sel["hint"]}]
    if rest:
        parts.append("# Other skills you can load on demand with `load_skill(name)` (use `list_skills(query)` to search)\n" +
                     ", ".join(rest))
    return "\n\n".join(parts)


def build(system, context, skills_text, user, continuing):
    segs = []
    if not continuing and system:
        segs.append(system)
    if context:
        segs.append("# Current QGIS state (live, injected automatically)\n" + context)
    if skills_text:
        segs.append(skills_text)
    segs.append("# User request\n" + user)
    return "\n\n".join(segs)
