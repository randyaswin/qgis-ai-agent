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

        # Chat log display
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        lay.addWidget(self.log, 1)

        # User prompt input
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Type map request (e.g. 'Build a disaster infographic layout with inset map and bar chart' or 'Buffer roads 500m') [Ctrl+Enter]")
        self.prompt.setMaximumHeight(95)
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
                        f"<div style='margin: 8px 0 4px 0; padding: 8px 12px; background-color: #e3f2fd; "
                        f"border-left: 4px solid #1976d2; border-radius: 4px;'>"
                        f"<div style='font-size: 11px; font-weight: bold; color: #1565c0; margin-bottom: 3px;'>"
                        f"🧑 YOU ({now_str})</div>"
                        f"<div style='font-size: 13px; color: #0d47a1; white-space: pre-wrap;'>{html.escape(m.text)}</div>"
                        f"</div>"
                    )
                    self._append_html(user_html)
                elif m.role == "assistant":
                    self._append_html(
                        f"<div style='margin-top: 8px; margin-bottom: 2px; font-weight: bold; color: #1b5e20; font-size: 11px;'>"
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
                f"<div style='margin-top: 8px; margin-bottom: 2px; font-weight: bold; color: #1b5e20; font-size: 11px;'>"
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
        self._last_tool_call = {"name": name, "args": args or {}}
        args_str = html.escape(json.dumps(args or {}, ensure_ascii=False))
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        card = (
            f"<div style='margin: 4px 0 2px 8px; padding: 5px 8px; background-color: #fffde7; "
            f"border-left: 3px solid #fbc02d; border-radius: 3px; font-size: 11px; color: #4e342e;'>"
            f"<b>⚙️ QGIS Action:</b> <code>{html.escape(name)}</code> <span style='color: #6d4c41;'>({args_str})</span>"
            f"</div>"
        )
        self._append_html(card)

    def _on_tool_completed(self, name, result):
        summary = summarize_tool_result(name, result)
        img_html = ""
        # Check for inline map rendering (canvas snapshot or layout preview)
        has_image = False
        if isinstance(result, dict) and "__image__" in result:
            b64 = result["__image__"]
            mime = result.get("mime", "image/png")
            has_image = True
            img_html = (
                f"<div style='margin: 6px 0 6px 8px; text-align: center;'>"
                f"<img src='data:{mime};base64,{b64}' style='max-width: 96%; border: 1px solid #b0bec5; border-radius: 4px; box-shadow: 0 1px 4px rgba(0,0,0,0.15);'><br>"
                f"<span style='font-size: 10px; color: #546e7a;'>🖼️ Map Visual Preview ({html.escape(name)})</span>"
                f"</div>"
            )
        card = (
            f"<div style='margin: 2px 0 6px 8px; padding: 5px 8px; background-color: #e8f5e9; "
            f"border-left: 3px solid #43a047; border-radius: 3px; font-size: 11px; color: #1b5e20;'>"
            f"<b>✅ Result:</b> {summary}"
            f"</div>{img_html}"
        )
        self._append_html(card)
        self.iface.mapCanvas().refresh()

        # Persist tool execution into active project session
        active_sess = self.session_manager.get_active_session()
        active_sess.add_tool_record(
            name=name,
            args=self._last_tool_call.get("args", {}),
            summary=summary,
            success=True,
            html_card=card,
            image=result.get("__image__") if has_image else None
        )

    def _on_tool_failed(self, name, err_msg):
        card = (
            f"<div style='margin: 4px 0 4px 8px; padding: 5px 8px; background-color: #ffebee; "
            f"border-left: 3px solid #e53935; border-radius: 3px; font-size: 11px; color: #b71c1c;'>"
            f"<b>❌ Failed to execute {html.escape(name)}:</b> {html.escape(err_msg)}"
            f"</div>"
        )
        self._append_html(card)

        # Persist tool failure into active project session
        active_sess = self.session_manager.get_active_session()
        active_sess.add_tool_record(
            name=name,
            args=self._last_tool_call.get("args", {}),
            summary=err_msg,
            success=False,
            html_card=card
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

        # Render User Message Card
        now_str = time.strftime("%H:%M:%S")
        user_html = (
            f"<div style='margin: 8px 0 4px 0; padding: 8px 12px; background-color: #e3f2fd; "
            f"border-left: 4px solid #1976d2; border-radius: 4px;'>"
            f"<div style='font-size: 11px; font-weight: bold; color: #1565c0; margin-bottom: 3px;'>"
            f"🧑 YOU ({now_str})</div>"
            f"<div style='font-size: 13px; color: #0d47a1; white-space: pre-wrap;'>{html.escape(user)}</div>"
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
                f"<div style='margin: 2px 0 4px 0; padding: 4px 8px; background-color: #f3e5f5; "
                f"border-left: 3px solid #8e24aa; border-radius: 3px; font-size: 11px; color: #4a148c;'>"
                f"{' | '.join(meta_badges)}"
                f"</div>"
            )
            self._append_html(badge_html)

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
        status_color = "#2e7d32" if code == 0 else "#c62828"
        done_html = (
            f"<div style='margin: 8px 0; padding: 4px 10px; background-color: #f5f5f5; border-radius: 4px; "
            f"font-size: 11px; color: {status_color}; text-align: right;'>"
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
