import html
import json
import os
import random
import re
import subprocess
import time

from qgis.PyQt.QtCore import Qt, QSettings, QTimer
from qgis.PyQt.QtGui import QTextCursor
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QPlainTextEdit,
    QTextEdit, QCheckBox, QLabel, QLineEdit, QFileDialog, QMessageBox, QDialog,
    QInputDialog, QFrame, QToolButton, QMenu, QAction,
)
from qgis.core import Qgis, QgsProject

from . import tools
from .runner import ADAPTERS, CliRunner, available
from .skills import REGISTRY
from .skills_dialog import SkillsDialog
from .sessions import SessionManager
from .session_dialog import SessionManagerDialog
from . import prompts

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
SKEY = "qgis_ai_agent/"


def summarize_tool_result(name, result):
    """Generate concise, human-readable summary of tool execution."""
    if not isinstance(result, dict):
        return html.escape(str(result)[:250])
    if name == "snapshot_canvas":
        kb = len(result.get("__image__", "")) * 3 // 4 // 1024
        return f"Canvas screenshot captured ({kb} KB)"
    if name == "export_layout":
        meta = result.get("meta", result)
        path = meta.get("path", "")
        succ = "Success" if meta.get("success") else "Failed"
        return f"Layout exported to <code>{html.escape(path)}</code> ({succ})"
    if name == "create_layout":
        return f"Print layout '<b>{html.escape(result.get('layout', ''))}</b>' created (Page: {result.get('page_mm', [])} mm, Scale 1:{result.get('map_scale', 0):,.0f})"
    if name == "create_styled_layout":
        return f"Styled layout '<b>{html.escape(result.get('theme', ''))}</b>' (<b>{html.escape(result.get('layout', ''))}</b>) successfully created"
    if name == "add_layout_inset_map":
        return "🖼️ Inset detail map added to layout (linked overview rectangle)"
    if name == "add_layout_infographic_chart":
        return f"📊 Infographic bar chart ({result.get('items', 0)} items) added to layout"
    if name == "add_layout_gradient_legend":
        return f"🌈 Horizontal gradient legend bar '<b>{html.escape(result.get('title', ''))}</b>' added to layout"
    if name == "add_layout_callout_box":
        return f"🏷️ Cluster callout card <b>{html.escape(result.get('badge', ''))}</b> ('{html.escape(result.get('title', ''))}') added"
    if name == "list_public_geodata_sources":
        return "🌐 Public open geospatial data catalog (Overture, STAC, COG) loaded"
    if name == "search_stac_items":
        return f"🛰️ STAC search found {result.get('found_count', 0)} COG imagery/elevation items"
    if name == "load_public_cog":
        return f"🛰️ COG raster '<b>{html.escape(result.get('name', ''))}</b>' streamed via HTTP/S3"
    if name == "query_overture_maps":
        return f"🏢 Overture Maps '<b>{html.escape(result.get('name', ''))}</b>' loaded ({result.get('features', 0)} features)"
    if name == "query_duckdb_spatial":
        return f"🦆 DuckDB spatial query '<b>{html.escape(result.get('name', ''))}</b>' executed ({result.get('features', 0)} features)"
    if name == "add_vector_layer":
        return f"Vector layer '<b>{html.escape(result.get('name', ''))}</b>' loaded ({result.get('geometry')}, {result.get('features')} features, CRS: {result.get('crs')})"
    if name == "add_raster_layer":
        return f"Raster layer '<b>{html.escape(result.get('name', ''))}</b>' loaded ({result.get('width')}x{result.get('height')} px, {result.get('bands')} bands, CRS: {result.get('crs')})"
    if name == "add_basemap":
        return f"Basemap '<b>{html.escape(result.get('name', ''))}</b>' loaded"
    if name == "run_processing":
        new_lyrs = [l.get("name") for l in result.get("new_layers", []) if isinstance(l, dict)]
        nl_str = f"New layers: <b>{', '.join(html.escape(n) for n in new_lyrs)}</b>" if new_lyrs else "Operation finished"
        return f"Geoprocessing algorithm completed ({nl_str})"
    if name == "open_processing_toolbox":
        return "🛠️ Processing Toolbox panel opened in QGIS"
    if name == "open_processing_algorithm_dialog":
        return f"🛠️ Algorithm dialog '<b>{html.escape(result.get('display_name', ''))}</b>' opened in QGIS"
    if name == "open_model_designer":
        return "🛠️ Graphical Model Designer opened in QGIS"
    if name == "list_processing_providers":
        return f"🛠️ Found {len(result)} active geoprocessing providers"
    if name == "calculate_field":
        return f"Field calculated successfully ({result.get('updated')} features updated)"
    if name == "set_labels":
        st = "enabled" if result.get("labels") else "disabled"
        return f"Layer labeling {st}"
    if name.startswith("style_"):
        return f"Symbology style ({html.escape(name)}) applied"
    if name == "describe_layer":
        return f"Inspect '<b>{html.escape(result.get('name', ''))}</b>': Type={result.get('layer_type')}, CRS={result.get('crs')}, Features={result.get('feature_count', '-')}"
    if name == "describe_all_layers":
        return f"Project audit: {len(result.get('layers', []))} layers detected"
    if name == "get_project_state":
        return f"Active project: CRS={result.get('crs')}, {len(result.get('layers_top_to_bottom', []))} layers, {len(result.get('layouts', []))} layouts"
    if name == "load_skill":
        return f"Skill guide '<b>{html.escape(result.get('skill', ''))}</b>' loaded"
    if name == "list_skills":
        return f"Found {len(result)} relevant skills"
    if name == "zoom_to":
        return "Canvas view centered"
    # Fallback to compact JSON
    s = json.dumps(result, ensure_ascii=False, default=str)
    return html.escape(s[:250] + ("..." if len(s) > 250 else ""))


def categorize_tool(name, args):
    """Categorize tool and extract primary target to match Claude-style header (e.g. Second-brain [read_note] index.md)."""
    args = args or {}
    primary = ""
    category = "QGIS"

    if name in ("read_note", "write_note", "append_note", "search_notes", "delete_note", "list_notes"):
        category = "Second-brain"
        primary = args.get("path") or args.get("query") or ""
    elif name == "query_overture_maps":
        category = "Overture"
        primary = f"{args.get('theme', 'places')} ({args.get('filter_sql', '')[:25] or 'canvas'})"
    elif name in ("search_stac_items", "load_public_cog", "list_public_geodata_sources"):
        category = "STAC / COG"
        if name == "search_stac_items":
            primary = ", ".join(args.get("collections", []))
        elif name == "load_public_cog":
            primary = args.get("name") or os.path.basename(args.get("url", ""))
    elif name == "query_duckdb_spatial":
        category = "DuckDB"
        primary = args.get("layer_name") or args.get("query", "")[:30]
    elif name in ("run_processing", "list_algorithms", "describe_algorithm", "list_processing_providers",
                  "open_processing_toolbox", "open_processing_algorithm_dialog", "open_model_designer"):
        category = "Processing"
        primary = args.get("algorithm") or args.get("query") or ""
    elif name in ("create_layout", "create_styled_layout", "edit_layout", "export_layout",
                  "add_layout_inset_map", "add_layout_infographic_chart", "add_layout_gradient_legend", "add_layout_callout_box"):
        category = "Layout"
        primary = args.get("name") or args.get("style_theme") or args.get("title") or ""
    elif name in ("style_single", "style_categorized", "style_graduated", "style_raster", "load_style", "set_labels"):
        category = "Symbology"
        primary = args.get("layer") or ""
    elif name in ("describe_layer", "describe_all_layers", "get_features", "get_selected_features", "field_stats", "sample_raster"):
        category = "Inspect"
        primary = args.get("layer") or ""
    elif name in ("add_vector_layer", "add_raster_layer", "add_basemap", "remove_layer", "update_layer"):
        category = "Layer"
        primary = args.get("name") or args.get("preset") or os.path.basename(args.get("path", ""))
    elif name == "exec_pyqgis":
        category = "PyQGIS"
        primary = (args.get("code") or "").strip().split("\n")[0][:40]

    return category, primary


def format_in_args(args):
    """Format input arguments as compact key-value pairs for the IN row."""
    if not args:
        return "()"
    parts = []
    for k, v in args.items():
        if k in ("code",) and isinstance(v, str):
            first_line = v.strip().split("\n")[0][:50]
            parts.append(f"{k}={first_line!r}...")
        elif isinstance(v, str):
            v_str = v if len(v) <= 45 else v[:42] + "..."
            parts.append(f"{k}={v_str!r}")
        elif isinstance(v, (int, float, bool)):
            parts.append(f"{k}={v}")
        elif isinstance(v, list):
            parts.append(f"{k}={v[:3]!r}")
        else:
            parts.append(f"{k}={str(v)[:30]!r}")
    return "  ".join(parts)


def format_out_result(name, result):
    """Format tool execution output as clean text for the OUT block."""
    summary = summarize_tool_result(name, result)
    return re.sub(r"<[^>]+>", "", summary)


def markdown_to_clean_html(md_text):
    """Convert standard markdown into clean, elegant HTML supported by Qt rich text."""
    if not md_text:
        return ""
    text = md_text.replace("\r\n", "\n").replace("\r", "\n")

    # 1. Protect & convert fenced code blocks
    code_blocks = []
    def _save_code(m):
        lang = m.group(1).strip()
        code = html.escape(m.group(2).strip("\n"))
        lang_badge = f"<div style='color: #64748b; font-size: 10px; font-weight: bold; margin-bottom: 2px;'>{html.escape(lang.upper())}</div>" if lang else ""
        card = (
            f"<div style='margin: 6px 0; background-color: #0f1117; border: 1px solid #232736; "
            f"border-radius: 5px; padding: 8px 12px; font-family: monospace; font-size: 11px; "
            f"color: #a5b4fc; white-space: pre-wrap;'>{lang_badge}{code}</div>"
        )
        idx = len(code_blocks)
        code_blocks.append(card)
        return f"__CODE_BLOCK_{idx}__"

    text = re.sub(r"```([a-zA-Z0-9_-]*)\n(.*?)```", _save_code, text, flags=re.S)

    # 2. Markdown tables
    def _table_conv(m):
        table_str = m.group(0).strip()
        lines = [ln.strip() for ln in table_str.split("\n") if ln.strip()]
        if len(lines) < 2:
            return table_str
        rows = []
        for i, line in enumerate(lines):
            if re.match(r"^\|?[\s:\-|]+\|?$", line):
                continue
            cols = [c.strip() for c in line.strip("|").split("|")]
            cell_tag = "th" if i == 0 else "td"
            cell_style = "padding: 4px 8px; border: 1px solid #283040; "
            if i == 0:
                cell_style += "background-color: #1e293b; color: #38bdf8; font-weight: bold; font-size: 11px;"
            else:
                bg = "#141923" if i % 2 == 1 else "#0f131a"
                cell_style += f"background-color: {bg}; color: #cbd5e1; font-size: 11px;"
            row_html = "".join(f"<{cell_tag} style='{cell_style}'>{c}</{cell_tag}>" for c in cols)
            rows.append(f"<tr>{row_html}</tr>")
        body = "".join(rows)
        return f"<table style='margin: 8px 0; border-collapse: collapse; width: 100%; border: 1px solid #283040;'>{body}</table>"

    text = re.sub(r"(?:^\|.+\|\n?){2,}", _table_conv, text, flags=re.M)

    # 3. Inline code spans
    text = re.sub(r"`([^`\n]+)`", lambda m: f"<code style='background-color: #1e2230; color: #93c5fd; padding: 1px 5px; border-radius: 3px; font-family: monospace; font-size: 11px;'>{html.escape(m.group(1))}</code>", text)

    # 4. Headings
    text = re.sub(r"^###\s+(.+)$", r"<div style='color: #f1f5f9; font-size: 13px; font-weight: bold; margin: 10px 0 4px 0;'>\1</div>", text, flags=re.M)
    text = re.sub(r"^##\s+(.+)$", r"<div style='color: #60a5fa; font-size: 14px; font-weight: bold; margin: 12px 0 4px 0;'>\1</div>", text, flags=re.M)
    text = re.sub(r"^#\s+(.+)$", r"<div style='color: #38bdf8; font-size: 15px; font-weight: bold; margin: 14px 0 6px 0;'>\1</div>", text, flags=re.M)

    # 5. Bold & Italic
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*([^*\n]+)\*", r"<i>\1</i>", text)

    # 6. Horizontal rule
    text = re.sub(r"^---+$", r"<hr style='border: none; border-top: 1px solid #282f3d; margin: 8px 0;'>", text, flags=re.M)

    # 7. Blockquotes
    text = re.sub(r"^>\s*(.+)$", r"<div style='margin: 6px 0; border-left: 3px solid #6366f1; background-color: #151824; padding: 4px 10px; color: #94a3b8; font-style: italic;'>\1</div>", text, flags=re.M)

    # 8. Block flusher (grouping paragraphs vs list items vs preformatted elements)
    lines = text.strip().split("\n")
    blocks = []
    current_p = []

    def flush_p():
        if current_p:
            p_text = "<br>".join(current_p)
            blocks.append(f"<div style='margin: 4px 0; line-height: 1.5; color: #e2e8f0;'>{p_text}</div>")
            current_p.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_p()
            continue
        if stripped.startswith("__CODE_BLOCK_") or stripped.startswith("<table") or stripped.startswith("<hr") or stripped.startswith("<div"):
            flush_p()
            blocks.append(stripped)
            continue
        m_b = re.match(r"^\s*[-*]\s+(.+)$", line)
        m_n = re.match(r"^\s*(\d+)\.\s+(.+)$", line)
        if m_b:
            flush_p()
            blocks.append(f"<div style='margin: 2px 0 2px 8px; color: #e2e8f0; line-height: 1.45;'><span style='color: #38bdf8; margin-right: 6px;'>•</span>{m_b.group(1)}</div>")
        elif m_n:
            flush_p()
            blocks.append(f"<div style='margin: 2px 0 2px 8px; color: #e2e8f0; line-height: 1.45;'><span style='color: #38bdf8; font-weight: bold; margin-right: 6px;'>{m_n.group(1)}.</span>{m_n.group(2)}</div>")
        else:
            current_p.append(stripped)
    flush_p()

    result = "\n".join(blocks)

    # 9. Restore code blocks
    for idx, card in enumerate(code_blocks):
        result = result.replace(f"__CODE_BLOCK_{idx}__", card)

    return result


def render_assistant_message_html(text, model_info="", timestamp=""):
    """Render an AI Assistant message as an elegant, neatly formatted card."""
    formatted_body = markdown_to_clean_html(text)
    time_str = f"<span style='color: #64748b; font-size: 10px; font-weight: normal; margin-left: 6px;'>{html.escape(timestamp)}</span>" if timestamp else ""
    meta_str = f"<span style='color: #94a3b8; font-size: 10px; font-weight: normal; margin-left: 6px;'>({html.escape(model_info)})</span>" if model_info else ""

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 8px 0 6px 0;'>"
        f"<tr><td>"
        f"<div style='background-color: #171822; border: 1px solid #28283a; "
        f"border-radius: 6px; padding: 10px 12px;'>"
        f"<div style='margin-bottom: 6px; line-height: 16px;'>"
        f"<span style='background-color: #059669; color: #ecfdf5; font-size: 10px; font-weight: bold; "
        f"padding: 1px 6px; border-radius: 3px;'>AGENT</span>&nbsp;&nbsp;"
        f"{meta_str}{time_str}"
        f"</div>"
        f"<div style='color: #e2e8f0; font-size: 12px; line-height: 1.5;'>"
        f"{formatted_body}"
        f"</div>"
        f"</div>"
        f"</td></tr>"
        f"</table>"
    )


def render_user_message_html(text, attachments=None, timestamp=""):
    """Render a User message as a clean, styled card."""
    time_str = f"<span style='color: #64748b; font-size: 10px; font-weight: normal; margin-left: 6px;'>{html.escape(timestamp)}</span>" if timestamp else ""

    att_html = ""
    if attachments:
        pills = []
        for a in attachments:
            fname = os.path.basename(a)
            pills.append(f"<span style='background-color: #0f172a; border: 1px solid #1e293b; color: #38bdf8; padding: 2px 7px; border-radius: 10px; font-size: 10px; margin-right: 4px;'>📎 {html.escape(fname)}</span>")
        att_html = f"<div style='margin-top: 6px;'>{''.join(pills)}</div>"

    clean_text = html.escape(text)

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 8px 0 4px 0;'>"
        f"<tr><td>"
        f"<div style='background-color: #172033; border: 1px solid #233352; "
        f"border-left: 3px solid #3b82f6; border-radius: 6px; padding: 8px 12px;'>"
        f"<div style='margin-bottom: 4px; line-height: 16px;'>"
        f"<span style='background-color: #2563eb; color: #eff6ff; font-size: 10px; font-weight: bold; "
        f"padding: 1px 6px; border-radius: 3px;'>YOU</span>&nbsp;&nbsp;"
        f"{time_str}"
        f"</div>"
        f"<div style='color: #f8fafc; font-size: 12px; line-height: 1.45; white-space: pre-wrap;'>"
        f"{clean_text}"
        f"</div>"
        f"{att_html}"
        f"</div>"
        f"</td></tr>"
        f"</table>"
    )


def render_tool_node_html(category, name, primary_arg, in_str, out_str, is_success=True, is_running=False, img_base64=None, duration=None):
    """Render a sleek vertical timeline tool node matching reference design."""
    is_python = (category == "PyQGIS" or name == "exec_pyqgis" or "python" in name.lower())

    if is_running:
        dot_color = "#c084fc" if is_python else "#38bdf8"
    elif is_success:
        dot_color = "#10b981"
    else:
        dot_color = "#ef4444"

    dur_str = f" &bull; <span style='color: #64748b; font-size: 10px;'>{duration}s</span>" if (duration and not is_running) else ""

    if is_python:
        header_desc = f"{name} &bull; Run Python Script" if (not primary_arg or primary_arg == name) else f"{name} &bull; {primary_arg}"
    else:
        header_desc = f"{name} &bull; {primary_arg}" if primary_arg else name

    in_val = html.escape(in_str or "()")
    if is_running:
        if is_python:
            out_cell = (
                "<div style='background-color: #1e112a; border: 1px solid #6b21a8; "
                "border-radius: 4px; padding: 4px 8px; color: #c084fc; "
                "font-family: monospace; font-size: 10px; line-height: 1.45; font-style: italic;'>"
                "⏳ Running Python Script in QGIS..."
                "</div>"
            )
        else:
            out_cell = (
                "<div style='background-color: #0c2340; border: 1px solid #1e4976; "
                "border-radius: 4px; padding: 4px 8px; color: #38bdf8; "
                "font-family: monospace; font-size: 10px; line-height: 1.45; font-style: italic;'>"
                "⏳ Executing MCP tool in QGIS..."
                "</div>"
            )
    else:
        out_bg = "#0d1117" if is_success else "#1c1418"
        out_border = "#21262d" if is_success else "#7f1d1d"
        out_color = "#c9d1d9" if is_success else "#fca5a5"
        escaped_out = html.escape(out_str or "").replace("\n", "<br>")
        out_cell = (
            f"<div style='background-color: {out_bg}; border: 1px solid {out_border}; "
            f"border-radius: 4px; padding: 5px 8px; color: {out_color}; "
            f"font-family: monospace; font-size: 10px; line-height: 1.45; word-break: break-all;'>"
            f"{escaped_out}"
            f"</div>"
        )

    img_html = ""
    if img_base64:
        cap_prefix = "📄 Print Layout Preview" if ("layout" in name.lower()) else "🗺️ Canvas Preview"
        img_html = (
            f"<div style='margin-top: 6px; text-align: left;'>"
            f"<img src='data:image/png;base64,{img_base64}' style='max-width: 95%; border: 1px solid #334155; border-radius: 4px; box-shadow: 0 2px 8px rgba(0,0,0,0.5);'><br>"
            f"<span style='font-size: 10px; color: #64748b;'>{cap_prefix} ({html.escape(name)})</span>"
            f"</div>"
        )

    sub_table = (
        f"<table cellpadding='0' cellspacing='0' style='margin-top: 4px; margin-left: 2px; width: 100%;'>"
        f"<tr>"
        f"<td width='34' valign='top' style='color: #64748b; font-family: monospace; font-size: 10px; font-weight: bold; padding-bottom: 3px;'>IN</td>"
        f"<td valign='top' style='color: #94a3b8; font-family: monospace; font-size: 10px; padding-bottom: 3px; word-break: break-all;'>{in_val}</td>"
        f"</tr>"
        f"<tr>"
        f"<td width='34' valign='top' style='color: #64748b; font-family: monospace; font-size: 10px; font-weight: bold;'>OUT</td>"
        f"<td valign='top'>{out_cell}</td>"
        f"</tr>"
        f"</table>"
    )

    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 0;'>"
        f"<tr>"
        f"<td width='20' valign='top' align='center' style='border-right: 2px solid #334155; padding-right: 8px;'>"
        f"<span style='color: {dot_color}; font-size: 13px; line-height: 16px;'>●</span>"
        f"</td>"
        f"<td valign='top' style='padding-left: 10px; padding-bottom: 10px;'>"
        f"<div style='line-height: 16px;'>"
        f"<b style='color: #f8fafc; font-size: 11px;'>{html.escape(category)}</b>&nbsp;&nbsp;"
        f"<span style='color: #cbd5e1; font-size: 11px;'>{header_desc}</span>{dur_str}"
        f"</div>"
        f"{sub_table}"
        f"{img_html}"
        f"</td>"
        f"</tr>"
        f"</table>"
    )


def render_thinking_node_html(text="Thought for 1s"):
    """Render a short, sleek Thinking timeline node with connected vertical line."""
    if not text:
        text = "Thought for 1s"
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 0;'>"
        f"<tr>"
        f"<td width='20' valign='top' align='center' style='border-right: 2px solid #334155; padding-right: 8px;'>"
        f"<span style='color: #64748b; font-size: 11px; line-height: 16px;'>●</span>"
        f"</td>"
        f"<td valign='top' style='padding-left: 10px; padding-bottom: 6px;'>"
        f"<span style='color: #94a3b8; font-size: 11px;'>{html.escape(text)}</span>"
        f"</td>"
        f"</tr>"
        f"</table>"
    )


def render_streaming_thinking_html(text="", elapsed=0):
    """Render live streaming thinking node while agent is actively reasoning."""
    clean_text = html.escape(text.strip())
    if clean_text:
        clean_text = clean_text.replace("\n", "<br>")
        content = (
            f"<div style='margin-top: 4px; padding: 5px 8px; background-color: #0d1527; "
            f"border-left: 2px solid #818cf8; border-radius: 3px; color: #94a3b8; "
            f"font-family: sans-serif; font-size: 10.5px; font-style: italic; line-height: 1.45; word-break: break-all;'>"
            f"{clean_text}"
            f"</div>"
        )
    else:
        content = ""
    return (
        f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 0;'>"
        f"<tr>"
        f"<td width='20' valign='top' align='center' style='border-right: 2px solid #334155; padding-right: 8px;'>"
        f"<span style='color: #818cf8; font-size: 11px; line-height: 16px;'>💭</span>"
        f"</td>"
        f"<td valign='top' style='padding-left: 10px; padding-bottom: 6px;'>"
        f"<span style='color: #818cf8; font-size: 11px; font-weight: bold;'>Thinking ({elapsed}s)...</span>"
        f"{content}"
        f"</td>"
        f"</tr>"
        f"</table>"
    )


class AgentDock(QDockWidget):
    def __init__(self, iface, server):
        super().__init__("AI Map Agent")
        self.iface, self.server = iface, server
        self.runner = CliRunner(self)
        self.first_turn = True
        self.start_time = 0
        self.is_agent_streaming = False
        self._current_assistant_text = ""
        self._current_model_header = ""
        self._last_tool_call = {}
        self._active_tool_pos = None
        self._active_running_tool = None
        self._thinking_active = False
        self._thinking_start_time = 0
        self._thinking_text = ""
        self._thinking_pos = None
        self._in_explicit_tag = False
        self._last_event_time = 0
        self.message_queue = []
        self.attachments = []
        s = QSettings()

        # Session Manager bound to QGIS project
        self.session_manager = SessionManager(on_change_callback=self._on_sessions_changed)

        # Connect runner and server signals
        self.runner.output.connect(self._on_cli_output)
        self.runner.finished.connect(self._done)
        self.server.tool_called.connect(self._on_tool_called)
        self.server.tool_completed.connect(self._on_tool_completed)
        self.server.tool_failed.connect(self._on_tool_failed)

        # 1-second pulse timer for live thinking stream elapsed timer & queue UI
        self.indicator_timer = QTimer(self)
        self.indicator_timer.setInterval(1000)
        self.indicator_timer.timeout.connect(self._pulse_indicator)

        # Internal configuration storage
        self.outdir = QLineEdit(s.value(SKEY + "outdir", os.path.expanduser("~/qgis-ai-maps")))
        self.cli = QComboBox()
        av = available()
        for name in ADAPTERS:
            self.cli.addItem(name + ("" if av[name] else "  (not found)"), name)
        saved_cli = s.value(SKEY + "cli", "agy (Antigravity CLI)")
        idx = self.cli.findData(saved_cli)
        self.cli.setCurrentIndex(max(0, idx))
        self.cli.currentIndexChanged.connect(self._on_cli_changed)

        self.skill_mode = QComboBox()
        self.skill_mode.addItem("Skills: Auto (Smart Select)", "auto")
        self.skill_mode.addItem("Skills: Manual (Pinned Only)", "manual")
        self.skill_mode.addItem("Skills: Off (Disabled)", "off")
        saved_mode = s.value(SKEY + "skill_mode", "auto")
        self.skill_mode.setCurrentIndex(max(0, self.skill_mode.findData(saved_mode)))
        self.skill_mode.currentIndexChanged.connect(self._on_skill_mode_changed)

        # Safety & Context Actions (housed in Settings menu)
        self.auto = QAction("Auto-approve Tool Calls", self)
        self.auto.setCheckable(True)
        self.auto.setChecked(s.value(SKEY + "auto", True, type=bool))
        self.auto.toggled.connect(lambda v: s.setValue(SKEY + "auto", v))

        self.pyexec = QAction("Allow exec_pyqgis (Arbitrary Code)", self)
        self.pyexec.setCheckable(True)
        self.pyexec.setChecked(s.value(SKEY + "pyexec", False, type=bool))
        self.pyexec.toggled.connect(lambda v: (tools.ALLOW_PYTHON.__setitem__("value", v), s.setValue(SKEY + "pyexec", v)))
        tools.ALLOW_PYTHON["value"] = self.pyexec.isChecked()

        self.cont = QAction("Continue Previous Session Context", self)
        self.cont.setCheckable(True)
        self.cont.setChecked(s.value(SKEY + "cont", True, type=bool))
        self.cont.toggled.connect(lambda v: s.setValue(SKEY + "cont", v))

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setSpacing(6)
        lay.setContentsMargins(6, 6, 6, 6)

        # Row 0: Session Selector & Settings Menu (Replaces bulky button rows)
        row0 = QHBoxLayout()
        row0.setSpacing(6)

        lbl_sess = QLabel("Session:")
        lbl_sess.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")
        self.session_combo = QComboBox()
        self.session_combo.setToolTip("Active conversation session bound to this QGIS project")
        self.session_combo.currentIndexChanged.connect(self._on_session_combo_changed)

        btn_new_sess = QPushButton("➕")
        btn_new_sess.setToolTip("Start a new conversation session for this project")
        btn_new_sess.setFixedWidth(28)
        btn_new_sess.setStyleSheet("""
            QPushButton {
                background-color: #232533;
                color: #38bdf8;
                border: 1px solid #36394d;
                border-radius: 4px;
                font-weight: bold;
                font-size: 12px;
                padding: 3px 0;
            }
            QPushButton:hover {
                background-color: #2e3245;
                color: #7dd3fc;
            }
        """)
        btn_new_sess.clicked.connect(self._create_new_session)

        # Revert Checkpoints Button & Menu (Cycle Rollback)
        self.revert_btn = QToolButton()
        self.revert_btn.setText("↺ Revert")
        self.revert_btn.setToolTip("Revert QGIS project and chat history to a previous cycle checkpoint")
        self.revert_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.revert_btn.setStyleSheet("""
            QToolButton {
                background-color: #232533;
                color: #cbd5e1;
                border: 1px solid #36394d;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QToolButton:hover {
                background-color: #2e3245;
                color: #ffffff;
            }
            QToolButton::menu-indicator {
                image: none;
                width: 0px;
            }
        """)
        self.revert_menu = QMenu(self.revert_btn)
        self.revert_btn.setMenu(self.revert_menu)

        # Settings & Tools Dropdown Menu (Consolidates 7 buttons & 3 checkboxes into 1 neat popup!)
        self.settings_btn = QToolButton()
        self.settings_btn.setText("⚙ Settings")
        self.settings_btn.setToolTip("Tools, skills, engine configuration, and safety settings")
        self.settings_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.settings_btn.setStyleSheet("""
            QToolButton {
                background-color: #232533;
                color: #cbd5e1;
                border: 1px solid #36394d;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 11px;
                font-weight: 500;
            }
            QToolButton:hover {
                background-color: #2e3245;
                color: #ffffff;
            }
            QToolButton::menu-indicator {
                image: none;
                width: 0px;
            }
        """)
        self.settings_menu = QMenu(self.settings_btn)
        self.settings_btn.setMenu(self.settings_menu)

        row0.addWidget(lbl_sess)
        row0.addWidget(self.session_combo, 1)
        row0.addWidget(btn_new_sess)
        row0.addWidget(self.revert_btn)
        row0.addWidget(self.settings_btn)
        lay.addLayout(row0)

        # Row 1: Model & Reasoning Effort
        row1 = QHBoxLayout()
        row1.setSpacing(6)

        lbl_model = QLabel("Model:")
        lbl_model.setStyleSheet("color: #94a3b8; font-size: 11px; font-weight: 600;")
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setToolTip("Select model or enter custom model ID")

        self.effort_combo = QComboBox()
        self.effort_combo.addItem("Effort: Default", "default")
        self.effort_combo.addItem("Effort: Low", "low")
        self.effort_combo.addItem("Effort: Medium", "medium")
        self.effort_combo.addItem("Effort: High", "high")
        self.effort_combo.addItem("Effort: Max", "max")
        saved_effort = s.value(SKEY + "effort", "default")
        self.effort_combo.setCurrentIndex(max(0, self.effort_combo.findData(saved_effort)))
        self.effort_combo.currentIndexChanged.connect(self._save_model_settings)
        self.model_combo.currentIndexChanged.connect(self._save_model_settings)

        row1.addWidget(lbl_model)
        row1.addWidget(self.model_combo, 1)
        row1.addWidget(self.effort_combo)
        lay.addLayout(row1)

        # Chat log display (Claude Dark IDE Theme)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log.setOpenLinks(False)
        self.log.anchorClicked.connect(self._on_log_anchor_clicked)
        self.log.setStyleSheet("""
            QTextEdit {
                background-color: #12131a;
                color: #e2e8f0;
                border: 1px solid #232533;
                border-radius: 6px;
                padding: 10px;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                font-size: 12px;
                selection-background-color: #2563eb;
            }
        """)
        lay.addWidget(self.log, 1)

        # Queue & Interrupt Notification Bar (Only shown when user queues a new message while process is running)
        self.indicator_frame = QFrame()
        self.indicator_frame.setStyleSheet("""
            QFrame {
                background-color: #172554;
                border: 1px solid #1e40af;
                border-radius: 4px;
                padding: 3px 8px;
            }
        """)
        ind_lay = QHBoxLayout(self.indicator_frame)
        ind_lay.setContentsMargins(6, 2, 6, 2)
        self.queue_label = QLabel("")
        self.queue_label.setStyleSheet("color: #7dd3fc; font-weight: bold; font-size: 11px;")
        ind_lay.addWidget(self.queue_label)
        ind_lay.addStretch()
        self.interrupt_btn = QPushButton("⚡ Interrupt")
        self.interrupt_btn.setToolTip("Interrupt active execution immediately to send queued message")
        self.interrupt_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: #ffffff;
                font-size: 11px;
                font-weight: bold;
                padding: 3px 10px;
                border: none;
                border-radius: 3px;
            }
            QPushButton:hover {
                background-color: #ef4444;
            }
        """)
        self.interrupt_btn.clicked.connect(self._interrupt_now)
        self.interrupt_btn.hide()
        ind_lay.addWidget(self.interrupt_btn)
        self.indicator_frame.hide()
        lay.addWidget(self.indicator_frame)

        # Attachment pills bar
        self.attach_bar = QWidget()
        self.attach_bar_lay = QHBoxLayout(self.attach_bar)
        self.attach_bar_lay.setContentsMargins(2, 0, 2, 0)
        self.attach_bar_lay.addStretch()
        self.attach_bar.hide()
        lay.addWidget(self.attach_bar)

        # User prompt input
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Type map request (e.g. 'Build a disaster infographic layout with inset map and bar chart' or 'Buffer roads 500m') [Ctrl+Enter]")
        self.prompt.setMaximumHeight(95)
        self.prompt.setStyleSheet("""
            QPlainTextEdit {
                background-color: #1c1c26;
                color: #f8fafc;
                border: 1px solid #333344;
                border-radius: 5px;
                padding: 6px;
                font-size: 12px;
            }
        """)
        lay.addWidget(self.prompt)

        # Action buttons (Clean, minimal action bar)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(6)

        self.attach_btn = QPushButton("📎 Attach")
        self.attach_btn.setToolTip("Attach reference images, geodata, or style files")
        self.attach_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e1e2a;
                color: #94a3b8;
                border: 1px solid #333348;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #272738;
                color: #e2e8f0;
            }
        """)
        self.attach_btn.clicked.connect(self._add_attachment)

        lbl_hint = QLabel("Ctrl+Enter to send")
        lbl_hint.setStyleSheet("color: #475569; font-size: 10px; margin-left: 6px;")

        self.action_btn = QPushButton("▶ Send")
        self.action_btn.setToolTip("Execute map request (Ctrl+Enter)")
        self.action_btn.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 6px 18px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #10b981;
            }
        """)
        self.action_btn.clicked.connect(self._on_action_btn_clicked)

        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setToolTip("Stop active AI agent execution immediately")
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #ef4444;
            }
        """)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.stop_btn.hide()

        # Backwards compatibility handle
        self.run_btn = self.action_btn

        action_row.addWidget(self.attach_btn)
        action_row.addWidget(lbl_hint)
        action_row.addStretch()
        action_row.addWidget(self.action_btn)
        action_row.addWidget(self.stop_btn)
        lay.addLayout(action_row)

        self.setWidget(w)
        self.prompt.installEventFilter(self)
        self.custom_instructions = s.value(SKEY + "custom_instructions", "")
        self._build_settings_menu()
        self._on_cli_changed()
        self._on_skill_mode_changed()
        self._on_sessions_changed()

    def eventFilter(self, obj, ev):
        if obj is self.prompt and ev.type() == ev.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._run()
            return True
        return super().eventFilter(obj, ev)

    def _on_cli_changed(self):
        cli_name = self.cli.currentData()
        adapter = ADAPTERS.get(cli_name, {})
        self.model_combo.clear()
        models = adapter.get("models", [])
        for label, val in models:
            self.model_combo.addItem(label, val)

        s = QSettings()
        saved_m = s.value(f"{SKEY}model_{cli_name}", "")
        if saved_m:
            idx = self.model_combo.findData(saved_m)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            else:
                self.model_combo.setEditText(saved_m)
        elif self.model_combo.count() > 0:
            self.model_combo.setCurrentIndex(0)

        # Effort support
        sup_effort = adapter.get("supports_effort", False)
        self.effort_combo.setEnabled(sup_effort)
        if not sup_effort:
            self.effort_combo.setToolTip("This CLI controls reasoning effort via model selection directly")
        else:
            self.effort_combo.setToolTip("Set reasoning / thinking effort for the selected model")

        s.setValue(SKEY + "cli", cli_name)

    def _save_model_settings(self):
        cli_name = self.cli.currentData()
        m_val = self.model_combo.currentData() or self.model_combo.currentText().strip()
        e_val = self.effort_combo.currentData()
        s = QSettings()
        s.setValue(f"{SKEY}model_{cli_name}", m_val)
        s.setValue(SKEY + "effort", e_val)

    def _on_sessions_changed(self):
        try:
            from qgis.PyQt import sip
            if sip.isdeleted(self) or not hasattr(self, "session_combo") or sip.isdeleted(self.session_combo):
                return
        except Exception:
            pass
        self._populate_sessions_dropdown()
        self._render_active_session_log()
        self._update_revert_ui()

    def _populate_sessions_dropdown(self):
        try:
            from qgis.PyQt import sip
            if sip.isdeleted(self) or not hasattr(self, "session_combo") or sip.isdeleted(self.session_combo):
                return
        except Exception:
            pass
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        active_id = self.session_manager.active_session_id
        sessions = sorted(self.session_manager.sessions.values(), key=lambda s: s.updated_at, reverse=True)
        active_idx = 0
        for idx, s in enumerate(sessions):
            label = f"{s.name} ({s.turns_count} turns)"
            self.session_combo.addItem(label, s.id)
            if s.id == active_id:
                active_idx = idx
        if self.session_combo.count() > 0:
            self.session_combo.setCurrentIndex(active_idx)
        self.session_combo.blockSignals(False)

    def _on_session_combo_changed(self, idx):
        sid = self.session_combo.currentData()
        if sid and sid != self.session_manager.active_session_id:
            self.session_manager.switch_session(sid)
            self._render_active_session_log()
            self._update_revert_ui()

    def _create_new_session(self):
        name, ok = QInputDialog.getText(self, "New Session", "Enter session name:")
        if ok and name.strip():
            selected_model = self.model_combo.currentData() or self.model_combo.currentText().strip() or ""
            selected_effort = self.effort_combo.currentData() or "default"
            self.session_manager.create_session(
                name=name.strip(),
                cli=self.cli.currentData(),
                model=selected_model,
                effort=selected_effort
            )

    def _open_session_manager(self):
        dlg = SessionManagerDialog(self.session_manager, self)
        dlg.exec()

    def _build_settings_menu(self):
        self.settings_menu.clear()

        # Tools & Managers
        act_manage_sess = self.settings_menu.addAction("🗂️ Manage Sessions...")
        act_manage_sess.triggered.connect(self._open_session_manager)

        act_skills = self.settings_menu.addAction("🧩 Skills Manager...")
        act_skills.triggered.connect(self._open_skills_dialog)

        act_toolbox = self.settings_menu.addAction("🛠️ QGIS Processing Toolbox")
        act_toolbox.triggered.connect(self._open_toolbox)

        act_prompt = self.settings_menu.addAction("📝 System Prompt & Custom Instructions...")
        act_prompt.triggered.connect(self._inspect_prompt)

        self.settings_menu.addSeparator()

        # Workspace & Engine Submenus
        m_cli = self.settings_menu.addMenu("⚙️ CLI Engine")
        av = available()
        curr_cli = self.cli.currentData()
        for name in ADAPTERS:
            label = name + ("" if av[name] else "  (not found)")
            act = m_cli.addAction(label)
            act.setCheckable(True)
            act.setChecked(name == curr_cli)
            act.setEnabled(av[name])
            act.triggered.connect(lambda _, n=name: self._set_cli(n))

        m_skills_mode = self.settings_menu.addMenu("🧠 Skills Selection Mode")
        modes = [
            ("Auto (Smart Select based on prompt)", "auto"),
            ("Manual (Pinned skills only)", "manual"),
            ("Off (Skills disabled)", "off"),
        ]
        curr_mode = self.skill_mode.currentData()
        for label, mode_id in modes:
            act = m_skills_mode.addAction(label)
            act.setCheckable(True)
            act.setChecked(mode_id == curr_mode)
            act.triggered.connect(lambda _, m=mode_id: self._set_skill_mode(m))

        # Output Folder action
        out_path = self.outdir.text().strip() or os.path.expanduser("~/qgis-ai-maps")
        short_out = out_path if len(out_path) <= 30 else "…" + out_path[-27:]
        act_outdir = self.settings_menu.addAction(f"📁 Output Folder ({short_out})")
        act_outdir.setToolTip(f"Change output folder (Current: {out_path})")
        act_outdir.triggered.connect(self._choose_outdir)

        act_reg = self.settings_menu.addAction("⚡ Register MCP with CLI")
        act_reg.triggered.connect(self._register)

        self.settings_menu.addSeparator()

        # Checkable Execution Preferences
        self.settings_menu.addAction(self.auto)
        self.settings_menu.addAction(self.pyexec)
        self.settings_menu.addAction(self.cont)

        self.settings_menu.addSeparator()

        act_clr = self.settings_menu.addAction("🗑️ Clear Chat History")
        act_clr.triggered.connect(self._clear_chat_log)

    def _clear_chat_log(self):
        self._active_tool_pos = None
        self._active_running_tool = None
        self._thinking_active = False
        self._thinking_pos = None
        self._thinking_text = ""
        self.log.clear()

    def _set_cli(self, name):
        idx = self.cli.findData(name)
        if idx >= 0:
            self.cli.setCurrentIndex(idx)
        self._build_settings_menu()

    def _set_skill_mode(self, mode_id):
        idx = self.skill_mode.findData(mode_id)
        if idx >= 0:
            self.skill_mode.setCurrentIndex(idx)
        self._build_settings_menu()

    def _choose_outdir(self):
        cur = self.outdir.text().strip() or os.path.expanduser("~/qgis-ai-maps")
        d = QFileDialog.getExistingDirectory(self, "Select Output Folder", cur)
        if d:
            self.outdir.setText(d)
            QSettings().setValue(SKEY + "outdir", d)
            self._build_settings_menu()

    def _on_action_btn_clicked(self):
        self._run()

    def _on_stop_clicked(self):
        if self.runner.running():
            self.runner.stop()
            self._append_html(
                f"<div style='margin: 6px 0; padding: 4px 8px; background-color: #450a0a; "
                f"border-left: 3px solid #ef4444; border-radius: 3px; font-size: 11px; color: #fca5a5;'>"
                f"⏹ <b>Execution stopped by user.</b>"
                f"</div>"
            )
            self._done(-9)

    def _on_log_anchor_clicked(self, url):
        u_str = url.toString() if hasattr(url, "toString") else str(url)
        if u_str.startswith("revert:"):
            try:
                cycle_no = int(u_str.split(":", 1)[1])
                self._confirm_and_revert(cycle_no)
            except Exception as e:
                tools.LOGGER.warning(f"Error handling revert anchor: {e}")

    def _save_checkpoint(self, cycle_no, label=""):
        try:
            active_sess = self.session_manager.get_active_session()
            if not active_sess:
                return
            chk_dir = os.path.join(self.session_manager.get_workspace_dir(active_sess.id), "checkpoints")
            os.makedirs(chk_dir, exist_ok=True)
            chk_filename = f"checkpoint_cycle_{cycle_no}.qgz"
            chk_path = os.path.join(chk_dir, chk_filename)

            p = QgsProject.instance()
            orig_filename = p.fileName()
            res = p.write(chk_path)
            if orig_filename:
                p.setFileName(orig_filename)

            if res:
                active_sess.add_checkpoint(
                    cycle=cycle_no,
                    filename=chk_filename,
                    prompt=label,
                    msg_count=len(active_sess.messages)
                )
                self.session_manager.save_project_sessions()
                self._update_revert_ui()
        except Exception as e:
            tools.LOGGER.warning(f"Failed to save cycle checkpoint: {e}")

    def _update_revert_ui(self):
        try:
            from qgis.PyQt import sip
            if sip.isdeleted(self) or not hasattr(self, "revert_menu") or sip.isdeleted(self.revert_menu):
                return
        except Exception:
            pass

        self.revert_menu.clear()
        active_sess = self.session_manager.get_active_session() if hasattr(self, "session_manager") else None
        chks = active_sess.get_checkpoints() if active_sess else []
        if not chks:
            act_none = self.revert_menu.addAction("(No checkpoints yet)")
            act_none.setEnabled(False)
            self.revert_btn.setEnabled(False)
            return

        self.revert_btn.setEnabled(True)
        for c in reversed(chks):
            cyc = c.get("cycle", 0)
            p_txt = c.get("prompt", "")
            if len(p_txt) > 40:
                p_txt = p_txt[:37] + "..."
            t_str = c.get("timestamp", "")
            label = f"↺ Revert to Cycle {cyc}: {p_txt} ({t_str})" if cyc > 0 else f"↺ Revert to Cycle 0: Initial State ({t_str})"
            act = self.revert_menu.addAction(label)
            act.triggered.connect(lambda checked=False, target_cycle=cyc: self._confirm_and_revert(target_cycle))

    def _confirm_and_revert(self, cycle_no):
        if self.runner.running():
            QMessageBox.warning(self, "Agent Running", "Please stop active AI execution before reverting.")
            return

        active_sess = self.session_manager.get_active_session()
        if not active_sess:
            return
        chk = active_sess.get_checkpoint(cycle_no)
        if not chk:
            QMessageBox.warning(self, "Checkpoint Not Found", f"Checkpoint for Cycle {cycle_no} was not found.")
            return

        label_str = chk.get("prompt") or f"Cycle {cycle_no}"
        reply = QMessageBox.question(
            self,
            "Confirm Revert to Checkpoint",
            f"Are you sure you want to revert to Cycle {cycle_no}?\n\n"
            f"Checkpoint: \"{label_str}\" ({chk.get('timestamp', '')})\n\n"
            "This will restore the QGIS project state (layers, styling, layouts) "
            "and roll back all subsequent chat history in this session.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        chk_dir = os.path.join(self.session_manager.get_workspace_dir(active_sess.id), "checkpoints")
        chk_path = os.path.join(chk_dir, chk.get("filename", f"checkpoint_cycle_{cycle_no}.qgz"))
        if not os.path.exists(chk_path):
            QMessageBox.critical(self, "File Missing", f"Checkpoint file not found on disk:\n{chk_path}")
            return

        orig_filename = QgsProject.instance().fileName()
        res = QgsProject.instance().read(chk_path)
        if orig_filename:
            QgsProject.instance().setFileName(orig_filename)

        active_sess.rollback_to_cycle(cycle_no)
        self.session_manager.save_project_sessions()
        self.first_turn = (active_sess.turns_count == 0)
        self.cont.setChecked(active_sess.turns_count > 0)
        self.iface.mapCanvas().refreshAllLayers()
        self._render_active_session_log()

        rev_html = (
            f"<div style='margin: 8px 0; padding: 6px 10px; background-color: #1e1b4b; "
            f"border-left: 3px solid #818cf8; border-radius: 4px; font-size: 11px; color: #c7d2fe;'>"
            f"⏪ <b>Reverted to Cycle {cycle_no} Checkpoint:</b> {html.escape(label_str)} ({chk.get('timestamp', '')})<br>"
            f"<span style='color: #94a3b8; font-size: 10px;'>QGIS project layers, symbology, layouts, and conversation history successfully restored.</span>"
            f"</div>"
        )
        self._append_html(rev_html)
        self._update_revert_ui()

    def _render_active_session_log(self):
        try:
            from qgis.PyQt import sip
            if sip.isdeleted(self) or not hasattr(self, "log") or sip.isdeleted(self.log):
                return
        except Exception:
            pass
        self._active_tool_pos = None
        self._active_running_tool = None
        self._thinking_active = False
        self._thinking_pos = None
        self._thinking_text = ""
        self.log.clear()
        sess = self.session_manager.get_active_session()
        for m in sess.messages:
            if m.role == "tool" and m.tool_calls:
                tc = m.tool_calls[0]
                cat, prim = categorize_tool(tc.get("name", "QGIS"), tc.get("args", {}))
                in_str = format_in_args(tc.get("args", {}))
                card_html = render_tool_node_html(
                    category=cat,
                    name=tc.get("name", "Tool"),
                    primary_arg=prim,
                    in_str=in_str,
                    out_str=tc.get("summary", ""),
                    is_success=tc.get("success", True)
                )
                self._append_html(card_html)
            elif m.html:
                self._append_html(m.html)
            elif m.text:
                if m.role == "user":
                    self._append_html(render_user_message_html(m.text, timestamp=m.timestamp))
                elif m.role == "assistant":
                    self._append_html(render_assistant_message_html(m.text, model_info=m.model_info, timestamp=m.timestamp))
                elif m.role == "tool":
                    self._append_html(render_tool_node_html("QGIS", m.text, "", "", m.text))
        self.first_turn = (sess.turns_count == 0)
        self.cont.setChecked(sess.turns_count > 0)

    def _on_skill_mode_changed(self):
        REGISTRY.mode = self.skill_mode.currentData()
        s = QSettings()
        s.setValue(SKEY + "skill_mode", REGISTRY.mode)

    def _open_skills_dialog(self):
        dlg = SkillsDialog(self)
        dlg.exec()

    def _open_toolbox(self):
        res = tools.open_processing_toolbox()
        self._append_html(
            f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 4px 0;'>"
            f"<tr><td>"
            f"<div style='padding: 4px 8px; background-color: #1e293b; border-radius: 3px; font-size: 11px; color: #38bdf8;'>"
            f"🛠️ <b>Processing Toolbox:</b> Panel opened in QGIS.</div>"
            f"</td></tr>"
            f"</table>"
        )

    def _inspect_prompt(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("System Prompt & Custom Instructions")
        dlg.resize(650, 480)
        d_lay = QVBoxLayout(dlg)
        d_lay.addWidget(QLabel("User Custom Instructions (injected into every new prompt):"))
        txt = QTextEdit()
        txt.setPlainText(self.custom_instructions)
        d_lay.addWidget(txt, 1)
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(lambda: (self._save_instructions(txt.toPlainText()), dlg.accept()))
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(dlg.reject)
        btn_box.addStretch()
        btn_box.addWidget(btn_save)
        btn_box.addWidget(btn_cancel)
        d_lay.addLayout(btn_box)
        dlg.exec()

    def _save_instructions(self, text):
        self.custom_instructions = text
        QSettings().setValue(SKEY + "custom_instructions", text)

    def _adapter(self):
        return ADAPTERS[self.cli.currentData()]

    def _append_html(self, html_content):
        c = self.log.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        if c.positionInBlock() > 0:
            c.insertBlock()
        pos = c.position()
        c.insertHtml(html_content)
        c.insertBlock()
        self.log.setTextCursor(c)
        self.log.ensureCursorVisible()
        return pos

    def _clean_ansi(self, text):
        return ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")

    def _start_thinking_node(self):
        """Starts a live streaming thinking node in the chat log."""
        if self._thinking_active:
            return
        self._thinking_active = True
        self._thinking_start_time = time.time()
        self._thinking_text = ""
        self._in_explicit_tag = False
        node_html = render_streaming_thinking_html(text="", elapsed=0)

        c = self.log.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        if c.positionInBlock() > 0:
            c.insertBlock()
        start_pos = c.position()
        c.insertHtml(node_html)
        c.insertBlock()
        end_pos = c.position()
        self.log.setTextCursor(c)
        self.log.ensureCursorVisible()
        self._thinking_pos = (start_pos, end_pos)

    def _update_thinking_stream(self, append_text=""):
        """Updates the streaming thinking text or elapsed timer in the active thinking node."""
        if not self._thinking_active or not self._thinking_pos:
            return
        if append_text:
            self._thinking_text += append_text
        elapsed = max(0, round(time.time() - self._thinking_start_time))
        preview_text = self._thinking_text[-1200:] if len(self._thinking_text) > 1200 else self._thinking_text
        node_html = render_streaming_thinking_html(text=preview_text, elapsed=elapsed)

        start_pos, end_pos = self._thinking_pos
        doc_len = self.log.document().characterCount()
        if start_pos <= doc_len and end_pos <= doc_len:
            rep_c = self.log.textCursor()
            rep_c.setPosition(start_pos)
            rep_c.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
            rep_c.insertHtml(node_html)
            rep_c.insertBlock()
            self._thinking_pos = (start_pos, rep_c.position())
            self.log.setTextCursor(rep_c)
            self.log.ensureCursorVisible()

    def _finalize_thinking_node(self, discard_if_empty=True):
        """Replaces the live streaming thinking node with 'Thought for Xs', or removes if empty."""
        if not self._thinking_active:
            return

        has_thought_content = bool(self._thinking_text and self._thinking_text.strip())
        if discard_if_empty and not has_thought_content:
            # No genuine thinking text was generated; remove placeholder so it is never mistaken as thinking
            if self._thinking_pos:
                start_pos, end_pos = self._thinking_pos
                doc_len = self.log.document().characterCount()
                if start_pos <= doc_len and end_pos <= doc_len:
                    rep_c = self.log.textCursor()
                    rep_c.setPosition(start_pos)
                    rep_c.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                    rep_c.removeSelectedText()
                    self.log.setTextCursor(rep_c)
            self._thinking_active = False
            self._thinking_pos = None
            self._thinking_text = ""
            self._in_explicit_tag = False
            return

        elapsed = max(1, round(time.time() - self._thinking_start_time))
        final_html = render_thinking_node_html(f"Thought for {elapsed}s")

        if self._thinking_pos:
            start_pos, end_pos = self._thinking_pos
            doc_len = self.log.document().characterCount()
            if start_pos <= doc_len and end_pos <= doc_len:
                rep_c = self.log.textCursor()
                rep_c.setPosition(start_pos)
                rep_c.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                rep_c.insertHtml(final_html)
                rep_c.insertBlock()
                self.log.setTextCursor(rep_c)
                self.log.ensureCursorVisible()

        self._thinking_active = False
        self._thinking_pos = None
        self._thinking_text = ""
        self._in_explicit_tag = False
        self._last_event_time = time.time()

    def _stream_agent_text(self, text):
        if not text:
            return
        self._current_assistant_text += text
        if not self.is_agent_streaming:
            cli_label = self.cli.currentData()
            model_info = self.model_combo.currentData() or self.model_combo.currentText().strip()
            effort_info = self.effort_combo.currentData()
            meta = [cli_label]
            if model_info:
                meta.append(model_info)
            if effort_info and effort_info != "default":
                meta.append(f"effort={effort_info}")
            header_str = " | ".join(meta)
            self._current_model_header = header_str
            self._append_html(
                f"<table width='100%' cellpadding='0' cellspacing='0' style='margin-top: 8px; margin-bottom: 2px;'>"
                f"<tr><td>"
                f"<span style='background-color: #059669; color: #ecfdf5; font-size: 10px; font-weight: bold; padding: 1px 6px; border-radius: 3px;'>AGENT</span>"
                f"<span style='color: #64748b; font-size: 10px; margin-left: 6px;'>({html.escape(header_str)})</span>"
                f"</td></tr>"
                f"</table>"
            )
            self.is_agent_streaming = True

        c = self.log.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(c)
        c.insertText(text)
        self.log.ensureCursorVisible()

    def _on_cli_output(self, text):
        clean = self._clean_ansi(text)
        if not clean:
            return

        rem = clean
        while rem:
            if not self._in_explicit_tag:
                m_open = re.search(r"<(?:thinking|thought|think)>", rem, re.IGNORECASE)
                if m_open:
                    pre = rem[:m_open.start()]
                    if pre.strip():
                        self._finalize_thinking_node(discard_if_empty=True)
                        self._stream_agent_text(pre)
                    rem = rem[m_open.end():]
                    self._in_explicit_tag = True
                    if not self._thinking_active:
                        self._start_thinking_node()
                else:
                    self._finalize_thinking_node(discard_if_empty=True)
                    self._stream_agent_text(rem)
                    rem = ""
            else:
                m_close = re.search(r"<\/(?:thinking|thought|think)>", rem, re.IGNORECASE)
                if m_close:
                    thought_chunk = rem[:m_close.start()]
                    self._update_thinking_stream(thought_chunk)
                    self._finalize_thinking_node(discard_if_empty=False)
                    self._in_explicit_tag = False
                    rem = rem[m_close.end():]
                else:
                    self._update_thinking_stream(rem)
                    rem = ""

    def _on_tool_called(self, name, args):
        self.is_agent_streaming = False
        # Tool call / script execution is NOT thinking: discard empty thinking placeholder immediately
        self._finalize_thinking_node(discard_if_empty=True)
        cat, prim = categorize_tool(name, args)
        in_str = format_in_args(args)
        now = time.time()
        self._last_tool_call = {
            "name": name,
            "args": args or {},
            "category": cat,
            "primary": prim,
            "in_str": in_str,
            "start_time": now,
        }
        self._active_running_tool = name
        self._last_event_time = now

        # Immediately render the tool node with IN visible and OUT in "Running..." state!
        running_node_html = render_tool_node_html(
            cat, name, prim, in_str, "", is_success=True, is_running=True
        )

        c = self.log.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        if c.positionInBlock() > 0:
            c.insertBlock()
        start_pos = c.position()
        c.insertHtml(running_node_html)
        c.insertBlock()
        end_pos = c.position()
        self.log.setTextCursor(c)
        self.log.ensureCursorVisible()

        self._active_tool_pos = (start_pos, end_pos)

        # Force Qt GUI repaint so user sees IN arguments and running state immediately!
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.processEvents()

    def _on_tool_completed(self, name, result):
        clean_summary = format_out_result(name, result)
        cat = self._last_tool_call.get("category", "QGIS")
        prim = self._last_tool_call.get("primary", "")
        in_str = self._last_tool_call.get("in_str", "")

        tool_start = self._last_tool_call.get("start_time", time.time())
        duration = max(0.1, round(time.time() - tool_start, 1))

        # Check for canvas snapshot or layout preview image
        img_b64 = None
        if isinstance(result, dict):
            img_b64 = result.get("__image__") or result.get("image_base64")

        full_node_html = render_tool_node_html(
            cat, name, prim, in_str, clean_summary, is_success=True, is_running=False, img_base64=img_b64, duration=duration
        )

        # Replace the running placeholder block with the completed block
        replaced = False
        if self._active_tool_pos:
            start_pos, end_pos = self._active_tool_pos
            doc_len = self.log.document().characterCount()
            if start_pos <= doc_len and end_pos <= doc_len:
                rep_c = self.log.textCursor()
                rep_c.setPosition(start_pos)
                rep_c.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                rep_c.insertHtml(full_node_html)
                rep_c.insertBlock()
                self.log.setTextCursor(rep_c)
                self.log.ensureCursorVisible()
                replaced = True
            self._active_tool_pos = None

        if not replaced:
            self._append_html(full_node_html)

        self._active_running_tool = None
        self.iface.mapCanvas().refresh()
        self._last_event_time = time.time()

        # Persist into active project session and autosave across iterations
        active_sess = self.session_manager.get_active_session()
        active_sess.add_tool_record(
            name=name,
            args=self._last_tool_call.get("args", {}),
            summary=clean_summary,
            success=True,
            html_card=full_node_html
        )
        self.session_manager.save_project_sessions()

        # Force Qt GUI update
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.processEvents()

    def _on_tool_failed(self, name, err_msg):
        cat = self._last_tool_call.get("category", "QGIS")
        prim = self._last_tool_call.get("primary", "")
        in_str = self._last_tool_call.get("in_str", "")

        tool_start = self._last_tool_call.get("start_time", time.time())
        duration = max(0.1, round(time.time() - tool_start, 1))

        full_node_html = render_tool_node_html(
            cat, name, prim, in_str, err_msg, is_success=False, is_running=False, duration=duration
        )

        replaced = False
        if self._active_tool_pos:
            start_pos, end_pos = self._active_tool_pos
            doc_len = self.log.document().characterCount()
            if start_pos <= doc_len and end_pos <= doc_len:
                rep_c = self.log.textCursor()
                rep_c.setPosition(start_pos)
                rep_c.setPosition(end_pos, QTextCursor.MoveMode.KeepAnchor)
                rep_c.insertHtml(full_node_html)
                rep_c.insertBlock()
                self.log.setTextCursor(rep_c)
                self.log.ensureCursorVisible()
                replaced = True
            self._active_tool_pos = None

        if not replaced:
            self._append_html(full_node_html)

        self._active_running_tool = None
        self._last_event_time = time.time()

        active_sess = self.session_manager.get_active_session()
        active_sess.add_tool_record(
            name=name,
            args=self._last_tool_call.get("args", {}),
            summary=err_msg,
            success=False,
            html_card=full_node_html
        )
        self.session_manager.save_project_sessions()

        # Force Qt GUI update
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.processEvents()

    def _register(self):
        argv = self._adapter()["register"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                               env={**os.environ, "PATH": os.path.expanduser("~/.local/bin") + os.pathsep
                                    + os.path.expanduser("~/.npm-global/bin") + os.pathsep + os.environ.get("PATH", "")})
            msg = (r.stdout + r.stderr).strip() or "OK"
            self._append_html(
                f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 4px 0;'>"
                f"<tr><td>"
                f"<div style='padding: 6px 10px; background-color: #1e293b; border: 1px solid #334155; border-radius: 4px; font-size: 11px; color: #cbd5e1;'>"
                f"<b>Register MCP:</b> <code>{' '.join(argv)}</code><br><pre style='color: #94a3b8; margin: 4px 0 0 0;'>{html.escape(msg)}</pre></div>"
                f"</td></tr>"
                f"</table>"
            )
        except Exception as e:
            QMessageBox.warning(self, "Register MCP", str(e))

    def _get_live_context(self):
        p = QgsProject.instance()
        layers = p.mapLayers().values()
        if not layers:
            return "Empty project (no layers currently loaded)."
        summary = [f"Project CRS: {p.crs().authid()} | File: {p.fileName() or '(unsaved)'}"]
        for lyr in layers:
            info = f"- '{lyr.name()}' ({lyr.type().name.lower() if hasattr(lyr.type(), 'name') else str(lyr.type())}, CRS={lyr.crs().authid()})"
            if hasattr(lyr, "featureCount"):
                info += f" features={lyr.featureCount()}"
            summary.append(info)
        return "\n".join(summary)

    def _add_attachment(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Attach Reference Material", "",
            "All Supported (*.png *.jpg *.jpeg *.tif *.tiff *.pdf *.geojson *.shp *.gpkg *.qml *.txt);;Images (*.png *.jpg *.jpeg *.tif);;Vector Data (*.geojson *.shp *.gpkg);;Styles (*.qml);;All Files (*)"
        )
        if paths:
            for p in paths:
                if p not in self.attachments:
                    self.attachments.append(p)
            self._update_attachment_bar()

    def _update_attachment_bar(self):
        # Clear existing items
        while self.attach_bar_lay.count():
            item = self.attach_bar_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self.attachments:
            self.attach_bar.hide()
            self.attach_btn.setText("📎 Attach")
            self.attach_btn.setStyleSheet("""
                QPushButton {
                    background-color: #1e1e2a;
                    color: #94a3b8;
                    border: 1px solid #333348;
                    border-radius: 4px;
                    padding: 6px 12px;
                    font-size: 11px;
                }
                QPushButton:hover {
                    background-color: #272738;
                    color: #e2e8f0;
                }
            """)
            return

        self.attach_bar.show()
        self.attach_btn.setText(f"📎 Attach ({len(self.attachments)})")
        self.attach_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #38bdf8;
                border: 1px solid #0284c7;
                border-radius: 4px;
                padding: 6px 12px;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        for idx, path in enumerate(self.attachments):
            base = os.path.basename(path)
            pill = QFrame()
            pill.setStyleSheet("""
                QFrame {
                    background-color: #272738;
                    border: 1px solid #4a4a68;
                    border-radius: 3px;
                    padding: 1px 4px;
                }
            """)
            p_lay = QHBoxLayout(pill)
            p_lay.setContentsMargins(4, 1, 4, 1)
            lbl = QLabel(f"📎 {base}")
            lbl.setStyleSheet("color: #e2e8f0; font-size: 11px;")
            p_lay.addWidget(lbl)

            btn_del = QPushButton("×")
            btn_del.setStyleSheet("color: #f87171; font-weight: bold; font-size: 11px; background: transparent; border: none; padding: 0 2px;")
            btn_del.clicked.connect(lambda _, i=idx: self._remove_attachment(i))
            p_lay.addWidget(btn_del)
            self.attach_bar_lay.addWidget(pill)

        self.attach_bar_lay.addStretch()

    def _remove_attachment(self, idx):
        if 0 <= idx < len(self.attachments):
            self.attachments.pop(idx)
            self._update_attachment_bar()

    def _pulse_indicator(self):
        if not self.runner.running():
            self._update_queue_ui()
            self.indicator_timer.stop()
            return
        if self._thinking_active and not self._active_running_tool:
            self._update_thinking_stream()
        self._update_queue_ui()

    def _update_queue_ui(self):
        q_len = len(self.message_queue)
        if q_len > 0 and self.runner.running():
            self.queue_label.setText(f"📥 {q_len} queued message(s) waiting")
            self.interrupt_btn.show()
            self.indicator_frame.show()
        else:
            self.interrupt_btn.hide()
            self.queue_label.setText("")
            self.indicator_frame.hide()

    def _interrupt_now(self):
        if not self.runner.running():
            return
        new_text = self.prompt.toPlainText().strip()
        if new_text:
            self.message_queue.append({
                "text": new_text,
                "attachments": list(self.attachments)
            })
            self.prompt.clear()
            self.attachments = []
            self._update_attachment_bar()

        if not self.message_queue:
            return

        self._append_html(
            f"<div style='margin: 4px 0; padding: 4px 8px; background-color: #451a1a; border-left: 3px solid #dc2626; border-radius: 3px; font-size: 11px; color: #fca5a5;'>"
            f"⚡ <b>Interrupted</b> by user with immediate priority.</div>"
        )
        self.runner.stop()

    def _run(self):
        user = self.prompt.toPlainText().strip()
        if self.runner.running():
            if user:
                # Process is already running -> Queue the user's message
                self.message_queue.append({
                    "text": user,
                    "attachments": list(self.attachments)
                })
                self.prompt.clear()
                self.attachments = []
                self._update_attachment_bar()
                self._append_html(
                    f"<div style='margin: 4px 0; padding: 4px 8px; background-color: #172554; border-left: 3px solid #38bdf8; border-radius: 3px; font-size: 11px; color: #bae6fd;'>"
                    f"📥 <b>Message queued</b> (#{len(self.message_queue)}): <i>{html.escape(user[:120])}...</i></div>"
                )
                self._update_queue_ui()
            return

        # Dequeue message if prompt is empty but queue exists
        attach_list = list(self.attachments)
        if not user and self.message_queue:
            queued = self.message_queue.pop(0)
            user = queued["text"]
            attach_list = queued.get("attachments", [])

        if not user:
            return

        # Build prompt with attached reference materials if present
        if attach_list:
            att_block = ["\n\n# User Attached References / Files:"]
            for a_path in attach_list:
                att_block.append(f"- File: `{a_path}`")
            user += "\n".join(att_block)
            self.attachments = []
            self._update_attachment_bar()

        outdir = self.outdir.text().strip() or os.path.expanduser("~/qgis-ai-maps")
        os.makedirs(outdir, exist_ok=True)
        active_sess = self.session_manager.get_active_session()
        cont = self.cont.isChecked() and (not self.first_turn or active_sess.turns_count > 0)
        self.start_time = time.time()
        self.is_agent_streaming = False
        self._current_assistant_text = ""

        REGISTRY.mode = self.skill_mode.currentData()
        catalog = list(REGISTRY.available().keys())

        inherited_skills = []
        if cont and active_sess.active_skills:
            # Continuing turn: inherit previously selected skills
            inherited_skills = list(active_sess.active_skills)
            new_sel = REGISTRY.select(user) if REGISTRY.mode != "off" else {"pinned": [], "full": [], "hint": [], "scores": {}}
            new_names = [s.name for s in new_sel.get("pinned", []) + new_sel.get("full", [])]
            added_names = [n for n in new_names if n not in inherited_skills]

            merged_skill_names = inherited_skills + added_names
            active_sess.active_skills = merged_skill_names
            if new_sel.get("is_e2e"):
                active_sess.is_e2e = True

            loaded_skills = merged_skill_names
            # If new skills were added this turn, build their full bodies; otherwise a lightweight reminder
            avail_skills = REGISTRY.available() or REGISTRY.skills
            if added_names:
                added_objs = [avail_skills[n] for n in added_names if n in avail_skills]
                skills_txt = (
                    f"# Active skills inherited: {', '.join(inherited_skills)}\n\n"
                    + prompts.skills_block({"pinned": [], "full": added_objs, "hint": [], "scores": new_sel.get("scores", {}), "is_e2e": False}, catalog)
                )
            else:
                skills_txt = f"# Active session skills: {', '.join(merged_skill_names)} (already in context)"
            sel = {"pinned": [], "full": [avail_skills[n] for n in merged_skill_names if n in avail_skills], "scores": {}}
        else:
            # First turn or fresh session
            sel = REGISTRY.select(user) if REGISTRY.mode != "off" else {"pinned": [], "full": [], "hint": [], "scores": {}}
            loaded_skills = [s.name for s in sel.get("pinned", []) + sel.get("full", [])]
            active_sess.active_skills = list(loaded_skills)
            active_sess.is_e2e = sel.get("is_e2e", False)
            skills_txt = prompts.skills_block(sel, catalog) if REGISTRY.mode != "off" else ""

        # Model and Effort selection
        selected_model = self.model_combo.currentData() or self.model_combo.currentText().strip() or None
        selected_effort = self.effort_combo.currentData()

        # Update active session metadata
        active_sess.cli = self.cli.currentData()
        active_sess.model = selected_model or ""
        active_sess.effort = selected_effort or "default"

        # Assemble prompt: live QGIS context is injected; session history is omitted when continuing native CLI session
        sys_text = prompts.system_prompt(Qgis.version(), outdir, REGISTRY.mode)
        if self.custom_instructions.strip():
            sys_text += f"\n\n# User custom instructions\n{self.custom_instructions.strip()}"
        live_ctx = self._get_live_context()
        sess_hist = active_sess.format_history_for_prompt() if (active_sess.turns_count > 0 and not cont) else None
        full = prompts.build(sys_text, live_ctx, skills_txt, user, continuing=cont, session_history=sess_hist)

        # Explicit CLI Session ID tracking: never rely on global "last"
        cli_sess_id = active_sess.cli_session_id
        if not cli_sess_id and self.cli.currentData() == "Gemini CLI":
            cli_sess_id = str(uuid.uuid4())
            active_sess.cli_session_id = cli_sess_id

        argv = self._adapter()["run"](
            full,
            cont,
            self.auto.isChecked(),
            model=selected_model,
            effort=selected_effort,
            session_id=cli_sess_id or None
        )

        s = QSettings()
        s.setValue(SKEY + "cli", self.cli.currentData())
        s.setValue(SKEY + "outdir", outdir)
        s.setValue(SKEY + "auto", self.auto.isChecked())
        s.setValue(SKEY + "pyexec", self.pyexec.isChecked())
        s.setValue(f"{SKEY}model_{self.cli.currentData()}", selected_model or "")
        s.setValue(SKEY + "effort", selected_effort)

        # Render User Message Card (Clean Modern Dark Theme)
        now_str = time.strftime("%H:%M:%S")
        user_card_html = render_user_message_html(user, attachments=attach_list, timestamp=now_str)

        # Render Skills & Model Info Badge
        meta_badges = []
        if cont:
            turn_no = active_sess.turns_count + 1
            sess_label = f"🔄 Continuing Turn {turn_no}"
            if active_sess.cli_session_id:
                sess_label += f" <span style='color: #94a3b8; font-size: 10px;'>({active_sess.cli_session_id[:8]}...)</span>"
            meta_badges.append(f"<span style='color: #34d399; font-weight: bold;'>{sess_label}</span>")
        if selected_model:
            meta_badges.append(f"<span style='color: #a5b4fc;'>Model:</span> <code>{html.escape(selected_model)}</code>")
        if selected_effort and selected_effort != "default":
            meta_badges.append(f"<span style='color: #c084fc;'>Effort:</span> <code>{html.escape(selected_effort)}</code>")
        if loaded_skills:
            inh_count = len(inherited_skills) if cont and inherited_skills else 0
            if cont and inh_count > 0:
                skill_tag = f"Skills ({len(loaded_skills)} active, inherited {inh_count}):"
            else:
                skill_tag = f"Skills ({len(loaded_skills)}):"
            meta_badges.append(f"<span style='color: #67e8f9;'>{skill_tag}</span> <b>{', '.join(html.escape(s) for s in loaded_skills)}</b>")

        badge_html = ""
        if meta_badges:
            badge_html = (
                f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 2px 0 6px 0;'>"
                f"<tr><td>"
                f"<div style='padding: 3px 8px; background-color: #1a1a26; "
                f"border-left: 3px solid #8b5cf6; border-radius: 3px; font-size: 11px; color: #cbd5e1;'>"
                f"{' &nbsp;|&nbsp; '.join(meta_badges)}"
                f"</div>"
                f"</td></tr>"
                f"</table>"
            )

        full_user_html = user_card_html + badge_html
        self._append_html(full_user_html)
        active_sess.add_user_message(user, full_user_html)
        self.session_manager.save_project_sessions()

        self._active_tool_pos = None
        self._active_running_tool = None
        self._last_event_time = time.time()
        self._start_thinking_node()

        # Checkpoint Cycle 0 (initial project state) if not yet recorded
        if not active_sess.get_checkpoint(0):
            self._save_checkpoint(0, "Initial Project State")

        self.prompt.clear()
        self.stop_btn.show()
        self.stop_btn.setEnabled(True)
        self.action_btn.setText("📥 Queue")
        self.action_btn.setToolTip("Queue this message to execute next (Ctrl+Enter)")
        self.action_btn.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self._update_queue_ui()
        self.indicator_timer.start()
        try:
            # Use isolated session workspace for CLI subprocess
            proj_workspace = self.session_manager.get_workspace_dir(active_sess.id)
            self.runner.start(argv, proj_workspace)
        except Exception as e:
            self._on_tool_failed("Subprocess Start", str(e))
            self._done(-1)

    def _done(self, code):
        self._finalize_thinking_node(discard_if_empty=True)
        self.indicator_timer.stop()
        self._active_tool_pos = None
        self._active_running_tool = None
        self._update_queue_ui()
        elapsed = time.time() - self.start_time if self.start_time else 0
        status_color = "#10b981" if code == 0 else "#ef4444"
        now_str = time.strftime("%H:%M:%S")

        # Record assistant response and persist sessions
        active_sess = self.session_manager.get_active_session()

        # Detect and store the exact CLI conversation ID
        try:
            proj_workspace = self.session_manager.get_workspace_dir(active_sess.id)
            active_sess.detect_and_store_cli_session_id(proj_workspace, active_sess.cli)
        except Exception:
            pass

        raw_asst_text = self._current_assistant_text.strip()
        if raw_asst_text:
            card_html = render_assistant_message_html(
                raw_asst_text,
                model_info=self._current_model_header,
                timestamp=now_str
            )
            active_sess.add_assistant_message(
                text=raw_asst_text,
                html_card=card_html,
                model_info=self._current_model_header
            )

        self.session_manager.save_project_sessions()
        self._populate_sessions_dropdown()

        # Re-render active session log with clean markdown and sleek cards
        self._render_active_session_log()

        # Checkpoint this completed cycle
        cycle_no = active_sess.turns_count
        if cycle_no > 0 and code == 0:
            last_prompt = ""
            for m in reversed(active_sess.messages):
                if m.role == "user":
                    last_prompt = m.text
                    break
            self._save_checkpoint(cycle_no, last_prompt)

        revert_link = f"<span style='float: left;'><a href='revert:{cycle_no}' style='color: #38bdf8; text-decoration: none; font-size: 11px;'>↺ Revert to Cycle {cycle_no}</a></span>" if cycle_no > 0 else ""
        done_html = (
            f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 8px 0;'>"
            f"<tr><td>"
            f"<div style='padding: 5px 10px; background-color: #171c24; border: 1px solid #282f3d; "
            f"border-radius: 4px; font-size: 11px; color: {status_color}; text-align: right;'>"
            f"{revert_link}"
            f"🏁 <b>Finished Cycle {cycle_no}</b> in {elapsed:.1f}s (Exit Code: {code})"
            f"</div>"
            f"</td></tr>"
            f"</table>"
        )
        self._append_html(done_html)

        self.is_agent_streaming = False
        self.first_turn = False
        self.cont.setChecked(True)
        self.stop_btn.hide()
        self.action_btn.setText("▶ Send")
        self.action_btn.setToolTip("Execute map request (Ctrl+Enter)")
        self.action_btn.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: #ffffff;
                font-weight: bold;
                border: none;
                border-radius: 4px;
                padding: 6px 18px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #10b981;
            }
        """)
        self._update_revert_ui()
        self.iface.mapCanvas().refreshAllLayers()

        # Check if there are queued messages to execute next
        if self.message_queue:
            QTimer.singleShot(500, self._run)

    def shutdown(self):
        self.runner.stop()
        if hasattr(self, "session_manager") and self.session_manager:
            self.session_manager.disconnect_signals()

    def closeEvent(self, event):
        if hasattr(self, "session_manager") and self.session_manager:
            self.session_manager.disconnect_signals()
        super().closeEvent(event)
