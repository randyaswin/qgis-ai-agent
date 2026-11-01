import html
import json
import os
import re
import subprocess
import time

from qgis.PyQt.QtCore import Qt, QSettings
from qgis.PyQt.QtGui import QTextCursor
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QPlainTextEdit,
    QTextEdit, QCheckBox, QLabel, QLineEdit, QFileDialog, QMessageBox, QDialog,
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


def render_tool_node_html(category, name, primary_arg, in_str, out_str, is_success=True, img_base64=None):
    """Render a Claude-style timeline tool execution node with IN and OUT blocks."""
    dot_color = "#22c55e" if is_success else "#ef4444"
    status_border = "#2e3440" if is_success else "#7f1d1d"

    img_html = ""
    if img_base64:
        img_html = (
            f"<div style='margin-top: 6px; text-align: center;'>"
            f"<img src='data:image/png;base64,{img_base64}' style='max-width: 96%; border: 1px solid #3b4252; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.4);'><br>"
            f"<span style='font-size: 10px; color: #94a3b8;'>🖼️ Map Visual Preview ({html.escape(name)})</span>"
            f"</div>"
        )

    in_block = ""
    if in_str:
        in_block = (
            f"<table style='margin-top: 3px; font-family: monospace; font-size: 11px; width: 100%; border-collapse: collapse;'>"
            f"<tr>"
            f"<td style='width: 32px; vertical-align: top; color: #64748b; font-weight: bold; font-size: 10px; padding-top: 2px;'>IN</td>"
            f"<td style='color: #cbd5e1; background-color: #22222e; padding: 2px 8px; border-radius: 3px;'>{html.escape(in_str)}</td>"
            f"</tr>"
            f"</table>"
        )

    out_block = ""
    if out_str:
        out_bg = "#121218" if is_success else "#1c1418"
        out_color = "#e2e8f0" if is_success else "#fca5a5"
        out_block = (
            f"<table style='margin-top: 3px; font-family: monospace; font-size: 11px; width: 100%; border-collapse: collapse;'>"
            f"<tr>"
            f"<td style='width: 32px; vertical-align: top; color: #64748b; font-weight: bold; font-size: 10px; padding-top: 3px;'>OUT</td>"
            f"<td>"
            f"<div style='color: {out_color}; background-color: {out_bg}; border: 1px solid {status_border}; padding: 4px 8px; border-radius: 4px; word-break: break-all;'>"
            f"{html.escape(out_str)}"
            f"</div>"
            f"</td>"
            f"</tr>"
            f"</table>"
        )

    arg_display = f"<span style='color: #94a3b8; font-size: 11px; margin-left: 6px;'>{html.escape(primary_arg)}</span>" if primary_arg else ""

    return (
        f"<div style='margin: 6px 0 6px 12px; border-left: 2px solid #374151; padding: 0 0 4px 14px;'>"
        f"<div style='margin-left: -21px; margin-bottom: 3px; line-height: 16px;'>"
        f"<span style='color: {dot_color}; font-size: 13px; margin-right: 6px;'>●</span>"
        f"<b style='color: #f1f5f9; font-size: 12px;'>{html.escape(category)} [{html.escape(name)}]</b>"
        f"{arg_display}"
        f"</div>"
        f"{in_block}"
        f"{out_block}"
        f"{img_html}"
        f"</div>"
    )


def render_thinking_node_html(text=""):
    """Render a Claude-style Thinking timeline node."""
    preview_html = ""
    if text.strip():
        preview_html = f"<div style='color: #71717a; font-style: italic; font-size: 11px; margin-top: 2px; padding: 2px 4px;'>{html.escape(text.strip())}</div>"
    return (
        f"<div style='margin: 6px 0 6px 12px; border-left: 2px solid #374151; padding: 0 0 4px 14px;'>"
        f"<div style='margin-left: -21px; line-height: 16px;'>"
        f"<span style='color: #94a3b8; font-size: 13px; margin-right: 6px;'>●</span>"
        f"<span style='color: #94a3b8; font-size: 12px; font-weight: 500;'>Thinking</span>"
        f"</div>"
        f"{preview_html}"
        f"</div>"
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
        s = QSettings()

        # Session Manager bound to QGIS project
        self.session_manager = SessionManager(on_change_callback=self._on_sessions_changed)

        # Connect runner and server signals
        self.runner.output.connect(self._on_cli_output)
        self.runner.finished.connect(self._done)
        self.server.tool_called.connect(self._on_tool_called)
        self.server.tool_completed.connect(self._on_tool_completed)
        self.server.tool_failed.connect(self._on_tool_failed)

        w = QWidget()
        lay = QVBoxLayout(w)

        # Row 0: Session Management & Project Chat History
        row0 = QHBoxLayout()
        self.session_combo = QComboBox()
        self.session_combo.setToolTip("Active conversation session bound to this QGIS project")
        self.session_combo.currentIndexChanged.connect(self._on_session_combo_changed)

        btn_new_sess = QPushButton("➕ New")
        btn_new_sess.setToolTip("Start a new conversation session for this project")
        btn_new_sess.clicked.connect(self._create_new_session)

        btn_manage_sess = QPushButton("Sessions...")
        btn_manage_sess.setToolTip("Open Session Manager (rename, branch, export, delete)")
        btn_manage_sess.clicked.connect(self._open_session_manager)

        row0.addWidget(QLabel("Session:"))
        row0.addWidget(self.session_combo, 1)
        row0.addWidget(btn_new_sess)
        row0.addWidget(btn_manage_sess)
        lay.addLayout(row0)

        # Row 1: CLI Selection & MCP Registration
        row1 = QHBoxLayout()
        self.cli = QComboBox()
        av = available()
        for name in ADAPTERS:
            self.cli.addItem(name + ("" if av[name] else "  (not found)"), name)
        saved_cli = s.value(SKEY + "cli", "agy (Antigravity CLI)")
        idx = self.cli.findData(saved_cli)
        self.cli.setCurrentIndex(max(0, idx))
        self.cli.currentIndexChanged.connect(self._on_cli_changed)

        reg = QPushButton("Register MCP")
        reg.setToolTip("Register QGIS server with this CLI MCP configuration (run once)")
        reg.clicked.connect(self._register)

        row1.addWidget(QLabel("CLI:"))
        row1.addWidget(self.cli, 1)
        row1.addWidget(reg)
        lay.addLayout(row1)

        # Row 2: Model & Reasoning Effort
        row2 = QHBoxLayout()
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

        row2.addWidget(QLabel("Model:"))
        row2.addWidget(self.model_combo, 1)
        row2.addWidget(self.effort_combo)
        lay.addLayout(row2)

        # Row 3: Skills & Quick Tools Access
        row3 = QHBoxLayout()
        self.skill_mode = QComboBox()
        self.skill_mode.addItem("Skills: Auto (Smart Select)", "auto")
        self.skill_mode.addItem("Skills: Manual (Pinned Only)", "manual")
        self.skill_mode.addItem("Skills: Off (Disabled)", "off")
        saved_mode = s.value(SKEY + "skill_mode", "auto")
        self.skill_mode.setCurrentIndex(max(0, self.skill_mode.findData(saved_mode)))
        self.skill_mode.currentIndexChanged.connect(self._on_skill_mode_changed)

        btn_skills = QPushButton("Skills...")
        btn_skills.setToolTip("Open Skills Manager to inspect, pin, or install new skills")
        btn_skills.clicked.connect(self._open_skills_dialog)

        btn_toolbox = QPushButton("Toolbox...")
        btn_toolbox.setToolTip("Open QGIS Processing Toolbox panel")
        btn_toolbox.clicked.connect(self._open_toolbox)

        btn_prompt_inspect = QPushButton("Prompt...")
        btn_prompt_inspect.setToolTip("View or edit System Prompt & Custom Instructions")
        btn_prompt_inspect.clicked.connect(self._inspect_prompt)

        row3.addWidget(self.skill_mode, 1)
        row3.addWidget(btn_skills)
        row3.addWidget(btn_toolbox)
        row3.addWidget(btn_prompt_inspect)
        lay.addLayout(row3)

        # Row 4: Output Folder
        row4 = QHBoxLayout()
        self.outdir = QLineEdit(s.value(SKEY + "outdir", os.path.expanduser("~/qgis-ai-maps")))
        b = QPushButton("…")
        b.clicked.connect(lambda: self.outdir.setText(QFileDialog.getExistingDirectory(self, "Output folder", self.outdir.text()) or self.outdir.text()))
        row4.addWidget(QLabel("Output:"))
        row4.addWidget(self.outdir, 1)
        row4.addWidget(b)
        lay.addLayout(row4)

        # Row 5: Checkboxes Toggles
        row5 = QHBoxLayout()
        self.auto = QCheckBox("Auto-approve")
        self.auto.setToolTip("Automatically approve tool calls without CLI confirmation prompts.")
        self.auto.setChecked(s.value(SKEY + "auto", True, type=bool))
        self.pyexec = QCheckBox("Allow exec_pyqgis")
        self.pyexec.setChecked(s.value(SKEY + "pyexec", False, type=bool))
        self.pyexec.toggled.connect(lambda v: tools.ALLOW_PYTHON.__setitem__("value", v))
        tools.ALLOW_PYTHON["value"] = self.pyexec.isChecked()
        self.cont = QCheckBox("Continue session")
        self.cont.setToolTip("Continue previous AI CLI session context")
        for c in (self.auto, self.pyexec, self.cont):
            row5.addWidget(c)
        lay.addLayout(row5)

        # Chat log display (Claude Dark IDE Theme)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.log.setStyleSheet("""
            QTextEdit {
                background-color: #17171e;
                color: #e2e8f0;
                border: 1px solid #282836;
                border-radius: 6px;
                padding: 8px;
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                font-size: 12px;
                selection-background-color: #2563eb;
            }
        """)
        lay.addWidget(self.log, 1)

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

        # Action buttons
        row6 = QHBoxLayout()
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.setStyleSheet("font-weight: bold; background-color: #2e7d32; color: white; padding: 6px;")
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.runner.stop)
        clr = QPushButton("Clear")
        clr.clicked.connect(self.log.clear)
        row6.addWidget(self.run_btn, 1)
        row6.addWidget(self.stop_btn)
        row6.addWidget(clr)
        lay.addLayout(row6)

        self.setWidget(w)
        self.prompt.installEventFilter(self)
        self.custom_instructions = s.value(SKEY + "custom_instructions", "")
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
        self._populate_sessions_dropdown()
        self._render_active_session_log()

    def _populate_sessions_dropdown(self):
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

    def _render_active_session_log(self):
        self.log.clear()
        sess = self.session_manager.get_active_session()
        for m in sess.messages:
            if m.html:
                self._append_html(m.html)
            elif m.text:
                if m.role == "user":
                    now_str = m.timestamp
                    user_html = (
                        f"<div style='margin: 8px 0 6px 0; padding: 6px 12px; background-color: #1e2433; "
                        f"border-left: 3px solid #3b82f6; border-radius: 4px;'>"
                        f"<div style='font-size: 11px; font-weight: bold; color: #60a5fa; margin-bottom: 3px;'>"
                        f"🧑 YOU <span style='font-weight: normal; color: #94a3b8;'>({now_str})</span></div>"
                        f"<div style='font-size: 12px; color: #f1f5f9; white-space: pre-wrap;'>{html.escape(m.text)}</div>"
                        f"</div>"
                    )
                    self._append_html(user_html)
                elif m.role == "assistant":
                    self._append_html(
                        f"<div style='margin-top: 10px; margin-bottom: 2px; font-weight: bold; color: #10b981; font-size: 11px;'>"
                        f"🤖 AI AGENT:</div>"
                    )
                    c = self.log.textCursor()
                    c.movePosition(QTextCursor.MoveOperation.End)
                    self.log.setTextCursor(c)
                    c.insertText(m.text)
                    self.log.ensureCursorVisible()
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
            f"<div style='margin: 4px 0; padding: 4px 8px; background-color: #e8f5e9; border-radius: 3px; font-size: 11px; color: #1b5e20;'>"
            f"🛠️ <b>Processing Toolbox:</b> Panel opened in QGIS.</div>"
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
        self.log.setTextCursor(c)
        c.insertHtml(html_content)
        self.log.ensureCursorVisible()

    def _clean_ansi(self, text):
        return ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")

    def _on_cli_output(self, text):
        clean = self._clean_ansi(text)
        if not clean:
            return
        self._current_assistant_text += clean

        # Detect Thinking blocks from reasoning models
        lower_clean = clean.lower()
        if ("<thinking>" in lower_clean or "<thought>" in lower_clean or
            clean.strip().startswith(("* Thinking", "Thinking:", "Thought:"))):
            self._append_html(render_thinking_node_html())
            clean = re.sub(r"<\/?(thinking|thought)>|\*+\s*Thinking[^*]*\*+|Thinking:", "", clean, flags=re.I).strip()
            if not clean:
                return

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
                f"<div style='margin-top: 10px; margin-bottom: 2px; font-weight: bold; color: #10b981; font-size: 11px;'>"
                f"🤖 AI AGENT ({html.escape(header_str)}):</div>"
            )
            self.is_agent_streaming = True

        c = self.log.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(c)
        c.insertText(clean)
        self.log.ensureCursorVisible()

    def _on_tool_called(self, name, args):
        self.is_agent_streaming = False
        cat, prim = categorize_tool(name, args)
        in_str = format_in_args(args)
        self._last_tool_call = {
            "name": name,
            "args": args or {},
            "category": cat,
            "primary": prim,
            "in_str": in_str,
        }
        arg_disp = f"<span style='color: #94a3b8; font-size: 11px; margin-left: 6px;'>{html.escape(prim)}</span>" if prim else ""
        card = (
            f"<div style='margin: 6px 0 2px 12px; border-left: 2px solid #374151; padding: 0 0 2px 14px;'>"
            f"<div style='margin-left: -21px; margin-bottom: 3px; line-height: 16px;'>"
            f"<span style='color: #22c55e; font-size: 13px; margin-right: 6px;'>●</span>"
            f"<b style='color: #f1f5f9; font-size: 12px;'>{html.escape(cat)} [{html.escape(name)}]</b>"
            f"{arg_disp}"
            f"</div>"
            f"<table style='margin-top: 3px; font-family: monospace; font-size: 11px; width: 100%; border-collapse: collapse;'>"
            f"<tr>"
            f"<td style='width: 32px; vertical-align: top; color: #64748b; font-weight: bold; font-size: 10px; padding-top: 2px;'>IN</td>"
            f"<td style='color: #cbd5e1; background-color: #22222e; padding: 2px 8px; border-radius: 3px;'>{html.escape(in_str)}</td>"
            f"</tr>"
            f"</table>"
            f"</div>"
        )
        self._append_html(card)

    def _on_tool_completed(self, name, result):
        clean_summary = format_out_result(name, result)
        img_html = ""
        img_b64 = None
        if isinstance(result, dict) and "__image__" in result:
            img_b64 = result["__image__"]
            mime = result.get("mime", "image/png")
            img_html = (
                f"<div style='margin-top: 6px; text-align: center;'>"
                f"<img src='data:{mime};base64,{img_b64}' style='max-width: 96%; border: 1px solid #3b4252; border-radius: 4px; box-shadow: 0 2px 6px rgba(0,0,0,0.4);'><br>"
                f"<span style='font-size: 10px; color: #94a3b8;'>🖼️ Map Visual Preview ({html.escape(name)})</span>"
                f"</div>"
            )

        card = (
            f"<div style='margin: 0 0 6px 12px; border-left: 2px solid #374151; padding: 0 0 4px 14px;'>"
            f"<table style='margin-top: 2px; font-family: monospace; font-size: 11px; width: 100%; border-collapse: collapse;'>"
            f"<tr>"
            f"<td style='width: 32px; vertical-align: top; color: #64748b; font-weight: bold; font-size: 10px; padding-top: 3px;'>OUT</td>"
            f"<td>"
            f"<div style='color: #e2e8f0; background-color: #121218; border: 1px solid #282836; padding: 4px 8px; border-radius: 4px; word-break: break-all;'>"
            f"{html.escape(clean_summary)}"
            f"</div>"
            f"</td>"
            f"</tr>"
            f"</table>"
            f"{img_html}"
            f"</div>"
        )
        self._append_html(card)
        self.iface.mapCanvas().refresh()

        # Persist unified Claude timeline node into active project session
        cat = self._last_tool_call.get("category", "QGIS")
        prim = self._last_tool_call.get("primary", "")
        in_str = self._last_tool_call.get("in_str", "")
        full_node_html = render_tool_node_html(cat, name, prim, in_str, clean_summary, is_success=True, img_base64=img_b64)

        active_sess = self.session_manager.get_active_session()
        active_sess.add_tool_record(
            name=name,
            args=self._last_tool_call.get("args", {}),
            summary=clean_summary,
            success=True,
            html_card=full_node_html,
            image=img_b64
        )

    def _on_tool_failed(self, name, err_msg):
        card = (
            f"<div style='margin: 0 0 6px 12px; border-left: 2px solid #374151; padding: 0 0 4px 14px;'>"
            f"<table style='margin-top: 2px; font-family: monospace; font-size: 11px; width: 100%; border-collapse: collapse;'>"
            f"<tr>"
            f"<td style='width: 32px; vertical-align: top; color: #ef4444; font-weight: bold; font-size: 10px; padding-top: 3px;'>OUT</td>"
            f"<td>"
            f"<div style='color: #fca5a5; background-color: #1c1418; border: 1px solid #7f1d1d; padding: 4px 8px; border-radius: 4px; word-break: break-all;'>"
            f"{html.escape(err_msg)}"
            f"</div>"
            f"</td>"
            f"</tr>"
            f"</table>"
            f"</div>"
        )
        self._append_html(card)

        cat = self._last_tool_call.get("category", "QGIS")
        prim = self._last_tool_call.get("primary", "")
        in_str = self._last_tool_call.get("in_str", "")
        full_node_html = render_tool_node_html(cat, name, prim, in_str, err_msg, is_success=False)

        active_sess = self.session_manager.get_active_session()
        active_sess.add_tool_record(
            name=name,
            args=self._last_tool_call.get("args", {}),
            summary=err_msg,
            success=False,
            html_card=full_node_html
        )

    def _register(self):
        argv = self._adapter()["register"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                               env={**os.environ, "PATH": os.path.expanduser("~/.local/bin") + os.pathsep
                                    + os.path.expanduser("~/.npm-global/bin") + os.pathsep + os.environ.get("PATH", "")})
            msg = (r.stdout + r.stderr).strip() or "OK"
            self._append_html(
                f"<div style='margin: 4px 0; padding: 4px 8px; background-color: #eceff1; border-radius: 3px; font-size: 11px;'>"
                f"<b>Register MCP:</b> <code>{' '.join(argv)}</code><br><pre>{html.escape(msg)}</pre></div>"
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

    def _run(self):
        if self.runner.running():
            return
        user = self.prompt.toPlainText().strip()
        if not user:
            return
        outdir = self.outdir.text().strip() or os.path.expanduser("~/qgis-ai-maps")
        os.makedirs(outdir, exist_ok=True)
        cont = self.cont.isChecked() and not self.first_turn
        self.start_time = time.time()
        self.is_agent_streaming = False
        self._current_assistant_text = ""

        REGISTRY.mode = self.skill_mode.currentData()
        sel = REGISTRY.select(user)

        active_sess = self.session_manager.get_active_session()

        # Model and Effort selection
        selected_model = self.model_combo.currentData() or self.model_combo.currentText().strip() or None
        selected_effort = self.effort_combo.currentData()

        # Update active session metadata
        active_sess.cli = self.cli.currentData()
        active_sess.model = selected_model or ""
        active_sess.effort = selected_effort or "default"

        # Assemble prompt with session history context if restarting/resuming
        if cont:
            full = user
        else:
            sys_text = prompts.system_prompt(Qgis.version(), outdir, REGISTRY.mode)
            if self.custom_instructions.strip():
                sys_text += f"\n\n# User custom instructions\n{self.custom_instructions.strip()}"
            live_ctx = self._get_live_context()
            catalog = list(REGISTRY.available().keys())
            skills_txt = prompts.skills_block(sel, catalog) if REGISTRY.mode != "off" else ""
            sess_hist = active_sess.format_history_for_prompt() if active_sess.turns_count > 0 else None
            full = prompts.build(sys_text, live_ctx, skills_txt, user, continuing=False, session_history=sess_hist)

        argv = self._adapter()["run"](full, cont, self.auto.isChecked(), model=selected_model, effort=selected_effort)

        s = QSettings()
        s.setValue(SKEY + "cli", self.cli.currentData())
        s.setValue(SKEY + "outdir", outdir)
        s.setValue(SKEY + "auto", self.auto.isChecked())
        s.setValue(SKEY + "pyexec", self.pyexec.isChecked())
        s.setValue(f"{SKEY}model_{self.cli.currentData()}", selected_model or "")
        s.setValue(SKEY + "effort", selected_effort)

        # Render User Message Card (Claude Dark Theme)
        now_str = time.strftime("%H:%M:%S")
        user_html = (
            f"<div style='margin: 8px 0 6px 0; padding: 6px 12px; background-color: #1e2433; "
            f"border-left: 3px solid #3b82f6; border-radius: 4px;'>"
            f"<div style='font-size: 11px; font-weight: bold; color: #60a5fa; margin-bottom: 3px;'>"
            f"🧑 YOU <span style='font-weight: normal; color: #94a3b8;'>({now_str})</span></div>"
            f"<div style='font-size: 12px; color: #f1f5f9; white-space: pre-wrap;'>{html.escape(user)}</div>"
            f"</div>"
        )
        self._append_html(user_html)
        active_sess.add_user_message(user, user_html)

        # Render Skills & Model Info Badge
        loaded_skills = [s.name for s in sel["pinned"] + sel["full"]]
        meta_badges = []
        if selected_model:
            meta_badges.append(f"Model: <code>{html.escape(selected_model)}</code>")
        if selected_effort and selected_effort != "default":
            meta_badges.append(f"Effort: <code>{html.escape(selected_effort)}</code>")
        if loaded_skills:
            meta_badges.append(f"Skills: <b>{', '.join(html.escape(s) for s in loaded_skills)}</b>")

        if meta_badges:
            badge_html = (
                f"<div style='margin: 2px 0 6px 0; padding: 3px 8px; background-color: #261f36; "
                f"border-left: 3px solid #a855f7; border-radius: 3px; font-size: 11px; color: #d8b4fe;'>"
                f"{' | '.join(meta_badges)}"
                f"</div>"
            )
            self._append_html(badge_html)

        # If thinking / reasoning effort is active, emit initial Thinking timeline node
        if selected_effort and selected_effort != "default":
            self._append_html(render_thinking_node_html())

        self.prompt.clear()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        try:
            # Use isolated project workspace for CLI subprocess
            proj_workspace = self.session_manager.get_workspace_dir()
            self.runner.start(argv, proj_workspace)
        except Exception as e:
            self._on_tool_failed("Subprocess Start", str(e))
            self._done(-1)

    def _done(self, code):
        elapsed = time.time() - self.start_time if self.start_time else 0
        status_color = "#10b981" if code == 0 else "#ef4444"
        done_html = (
            f"<div style='margin: 8px 0; padding: 4px 10px; background-color: #171c24; border: 1px solid #282f3d; "
            f"border-radius: 4px; font-size: 11px; color: {status_color}; text-align: right;'>"
            f"🏁 <b>Finished</b> in {elapsed:.1f}s (Exit Code: {code})"
            f"</div>"
        )
        self._append_html(done_html)

        # Record assistant response and persist sessions
        active_sess = self.session_manager.get_active_session()
        if self._current_assistant_text.strip():
            active_sess.add_assistant_message(
                text=self._current_assistant_text.strip(),
                html_card="",
                model_info=self._current_model_header
            )
        self.session_manager.save_project_sessions()
        self._populate_sessions_dropdown()

        self.is_agent_streaming = False
        self.first_turn = False
        self.cont.setChecked(True)
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.iface.mapCanvas().refreshAllLayers()

    def shutdown(self):
        self.runner.stop()
