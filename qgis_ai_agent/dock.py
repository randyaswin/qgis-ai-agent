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
from . import prompts

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
SKEY = "qgis_ai_agent/"


def summarize_tool_result(name, result):
    """Generate concise, human-readable summary of tool execution."""
    if not isinstance(result, dict):
        return html.escape(str(result)[:250])
    if name == "snapshot_canvas":
        kb = len(result.get("__image__", "")) * 3 // 4 // 1024
        return f"Tangkapan layar kanvas berhasil diambil ({kb} KB)"
    if name == "export_layout":
        meta = result.get("meta", result)
        path = meta.get("path", "")
        succ = "Berhasil" if meta.get("success") else "Gagal"
        return f"Layout diekspor ke <code>{html.escape(path)}</code> ({succ})"
    if name == "create_layout":
        return f"Layout cetak '<b>{html.escape(result.get('layout', ''))}</b>' dibuat (Ukuran: {result.get('page_mm', [])} mm, Skala 1:{result.get('map_scale', 0):,.0f})"
    if name == "add_vector_layer":
        return f"Layer vektor '<b>{html.escape(result.get('name', ''))}</b>' dimuat ({result.get('geometry')}, {result.get('features')} fitur, CRS: {result.get('crs')})"
    if name == "add_raster_layer":
        return f"Layer raster '<b>{html.escape(result.get('name', ''))}</b>' dimuat ({result.get('width')}x{result.get('height')} px, {result.get('bands')} band, CRS: {result.get('crs')})"
    if name == "add_basemap":
        return f"Basemap '<b>{html.escape(result.get('name', ''))}</b>' berhasil dimuat"
    if name == "run_processing":
        new_lyrs = [l.get("name") for l in result.get("new_layers", []) if isinstance(l, dict)]
        nl_str = f"Layer baru: <b>{', '.join(html.escape(n) for n in new_lyrs)}</b>" if new_lyrs else "Operasi selesai"
        return f"Algoritma geoprocessing selesai ({nl_str})"
    if name == "open_processing_toolbox":
        return "🛠️ Panel Processing Toolbox dibuka di QGIS"
    if name == "open_processing_algorithm_dialog":
        return f"🛠️ Dialog algoritma '<b>{html.escape(result.get('display_name', ''))}</b>' dibuka di QGIS"
    if name == "open_model_designer":
        return "🛠️ Graphical Model Designer dibuka di QGIS"
    if name == "list_processing_providers":
        return f"🛠️ Ditemukan {len(result)} provider geoprocessing aktif"
    if name == "calculate_field":
        return f"Kolom berhasil dihitung ({result.get('updated')} fitur diperbarui)"
    if name == "set_labels":
        st = "diaktifkan" if result.get("labels") else "dinonaktifkan"
        return f"Pengaturan label {st}"
    if name.startswith("style_"):
        return f"Gaya visualisasi ({html.escape(name)}) berhasil diterapkan"
    if name == "describe_layer":
        return f"Inspeksi '<b>{html.escape(result.get('name', ''))}</b>': Tipe={result.get('layer_type')}, CRS={result.get('crs')}, Fitur={result.get('feature_count', '-')}"
    if name == "describe_all_layers":
        return f"Inspeksi proyek: {len(result.get('layers', []))} layer terdeteksi"
    if name == "get_project_state":
        return f"Proyek aktif: CRS={result.get('crs')}, {len(result.get('layers_top_to_bottom', []))} layer, {len(result.get('layouts', []))} layout"
    if name == "load_skill":
        return f"Panduan skill '<b>{html.escape(result.get('skill', ''))}</b>' berhasil dimuat"
    if name == "list_skills":
        return f"Ditemukan {len(result)} skill relevan"
    if name == "zoom_to":
        return "Tampilan kanvas dipusatkan"
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
        s = QSettings()

        # Connect runner and server signals
        self.runner.output.connect(self._on_cli_output)
        self.runner.finished.connect(self._done)
        self.server.tool_called.connect(self._on_tool_called)
        self.server.tool_completed.connect(self._on_tool_completed)
        self.server.tool_failed.connect(self._on_tool_failed)

        w = QWidget()
        lay = QVBoxLayout(w)

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
        reg.setToolTip("Daftarkan server QGIS ke konfigurasi MCP CLI ini (cukup 1x)")
        reg.clicked.connect(self._register)

        row1.addWidget(QLabel("CLI:"))
        row1.addWidget(self.cli, 1)
        row1.addWidget(reg)
        lay.addLayout(row1)

        # Row 2: Model & Reasoning Effort (Bobot)
        row2 = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setToolTip("Pilih model atau ketik custom model ID")

        self.effort_combo = QComboBox()
        self.effort_combo.addItem("Bobot: Default", "default")
        self.effort_combo.addItem("Bobot: Low", "low")
        self.effort_combo.addItem("Bobot: Medium", "medium")
        self.effort_combo.addItem("Bobot: High", "high")
        self.effort_combo.addItem("Bobot: Max", "max")
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
        self.skill_mode.addItem("Skills: Auto (Pilih Otomatis)", "auto")
        self.skill_mode.addItem("Skills: Manual (Hanya Pinned)", "manual")
        self.skill_mode.addItem("Skills: Off (Tanpa Skills)", "off")
        saved_mode = s.value(SKEY + "skill_mode", "auto")
        self.skill_mode.setCurrentIndex(max(0, self.skill_mode.findData(saved_mode)))
        self.skill_mode.currentIndexChanged.connect(self._on_skill_mode_changed)

        btn_skills = QPushButton("Skills...")
        btn_skills.setToolTip("Buka Skills Manager untuk melihat, pin, atau pasang skill baru")
        btn_skills.clicked.connect(self._open_skills_dialog)

        btn_toolbox = QPushButton("Toolbox...")
        btn_toolbox.setToolTip("Buka Processing Toolbox QGIS")
        btn_toolbox.clicked.connect(self._open_toolbox)

        btn_prompt_inspect = QPushButton("Prompt...")
        btn_prompt_inspect.setToolTip("Lihat atau sesuaikan System Prompt & Aturan Tambahan")
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
        self.auto.setToolTip("Lewati konfirmasi izin alat secara otomatis pada CLI.")
        self.auto.setChecked(s.value(SKEY + "auto", True, type=bool))
        self.pyexec = QCheckBox("Allow exec_pyqgis")
        self.pyexec.setChecked(s.value(SKEY + "pyexec", False, type=bool))
        self.pyexec.toggled.connect(lambda v: tools.ALLOW_PYTHON.__setitem__("value", v))
        tools.ALLOW_PYTHON["value"] = self.pyexec.isChecked()
        self.cont = QCheckBox("Continue session")
        self.cont.setToolTip("Lanjutkan sesi percakapan CLI sebelumnya")
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
        self.prompt.setPlaceholderText("Ketik perintah peta (misal: 'Buka processing toolbox dan jalankan buffer 500m pada layer jalan' atau 'Buat peta kepadatan...') [Ctrl+Enter]")
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
            self.effort_combo.setToolTip("CLI ini mengatur bobot melalui pemilihan model langsung")
        else:
            self.effort_combo.setToolTip("Atur bobot reasoning / thinking effort untuk model terpilih")

        s.setValue(SKEY + "cli", cli_name)

    def _save_model_settings(self):
        cli_name = self.cli.currentData()
        m_val = self.model_combo.currentData() or self.model_combo.currentText().strip()
        e_val = self.effort_combo.currentData()
        s = QSettings()
        s.setValue(f"{SKEY}model_{cli_name}", m_val)
        s.setValue(SKEY + "effort", e_val)

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
            f"🛠️ <b>Processing Toolbox:</b> Panel berhasil dibuka di jendela QGIS.</div>"
        )

    def _inspect_prompt(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("System Prompt & Custom Instructions")
        dlg.resize(650, 480)
        d_lay = QVBoxLayout(dlg)
        d_lay.addWidget(QLabel("Aturan Tambahan Pengguna (diinjeksi ke setiap prompt baru):"))
        txt = QTextEdit()
        txt.setPlainText(self.custom_instructions)
        d_lay.addWidget(txt, 1)
        btn_box = QHBoxLayout()
        btn_save = QPushButton("Simpan")
        btn_save.clicked.connect(lambda: (self._save_instructions(txt.toPlainText()), dlg.accept()))
        btn_cancel = QPushButton("Batal")
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
        args_str = html.escape(json.dumps(args or {}, ensure_ascii=False))
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        card = (
            f"<div style='margin: 4px 0 2px 8px; padding: 5px 8px; background-color: #fffde7; "
            f"border-left: 3px solid #fbc02d; border-radius: 3px; font-size: 11px; color: #4e342e;'>"
            f"<b>⚙️ Aksi QGIS:</b> <code>{html.escape(name)}</code> <span style='color: #6d4c41;'>({args_str})</span>"
            f"</div>"
        )
        self._append_html(card)

    def _on_tool_completed(self, name, result):
        summary = summarize_tool_result(name, result)
        img_html = ""
        # Check for inline map rendering (canvas snapshot or layout preview)
        if isinstance(result, dict) and "__image__" in result:
            b64 = result["__image__"]
            mime = result.get("mime", "image/png")
            img_html = (
                f"<div style='margin: 6px 0 6px 8px; text-align: center;'>"
                f"<img src='data:{mime};base64,{b64}' style='max-width: 96%; border: 1px solid #b0bec5; border-radius: 4px; box-shadow: 0 1px 4px rgba(0,0,0,0.15);'><br>"
                f"<span style='font-size: 10px; color: #546e7a;'>🖼️ Pratinjau Visual Peta ({html.escape(name)})</span>"
                f"</div>"
            )
        card = (
            f"<div style='margin: 2px 0 6px 8px; padding: 5px 8px; background-color: #e8f5e9; "
            f"border-left: 3px solid #43a047; border-radius: 3px; font-size: 11px; color: #1b5e20;'>"
            f"<b>✅ Hasil:</b> {summary}"
            f"</div>{img_html}"
        )
        self._append_html(card)
        self.iface.mapCanvas().refresh()

    def _on_tool_failed(self, name, err_msg):
        card = (
            f"<div style='margin: 4px 0 4px 8px; padding: 5px 8px; background-color: #ffebee; "
            f"border-left: 3px solid #e53935; border-radius: 3px; font-size: 11px; color: #b71c1c;'>"
            f"<b>❌ Gagal mengeksekusi {html.escape(name)}:</b> {html.escape(err_msg)}"
            f"</div>"
        )
        self._append_html(card)

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
            return "Project kosong (tidak ada layer yang sedang dimuat)."
        summary = [f"Project CRS: {p.crs().authid()} | File: {p.fileName() or '(belum disimpan)'}"]
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

        REGISTRY.mode = self.skill_mode.currentData()
        sel = REGISTRY.select(user)

        # Assemble prompt
        if cont:
            full = user
        else:
            sys_text = prompts.system_prompt(Qgis.version(), outdir, REGISTRY.mode)
            if self.custom_instructions.strip():
                sys_text += f"\n\n# User custom instructions\n{self.custom_instructions.strip()}"
            live_ctx = self._get_live_context()
            catalog = list(REGISTRY.available().keys())
            skills_txt = prompts.skills_block(sel, catalog) if REGISTRY.mode != "off" else ""
            full = prompts.build(sys_text, live_ctx, skills_txt, user, continuing=False)

        # Model and Effort selection
        selected_model = self.model_combo.currentData() or self.model_combo.currentText().strip() or None
        selected_effort = self.effort_combo.currentData()

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
            f"🧑 ANDA ({now_str})</div>"
            f"<div style='font-size: 13px; color: #0d47a1; white-space: pre-wrap;'>{html.escape(user)}</div>"
            f"</div>"
        )
        self._append_html(user_html)

        # Render Skills & Model Info Badge
        loaded_skills = [s.name for s in sel["pinned"] + sel["full"]]
        meta_badges = []
        if selected_model:
            meta_badges.append(f"Model: <code>{html.escape(selected_model)}</code>")
        if selected_effort and selected_effort != "default":
            meta_badges.append(f"Bobot: <code>{html.escape(selected_effort)}</code>")
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
            self.runner.start(argv, outdir)
        except Exception as e:
            self._on_tool_failed("Subprocess Start", str(e))
            self._done(-1)

    def _done(self, code):
        elapsed = time.time() - self.start_time if self.start_time else 0
        status_color = "#2e7d32" if code == 0 else "#c62828"
        done_html = (
            f"<div style='margin: 8px 0; padding: 4px 10px; background-color: #f5f5f5; border-radius: 4px; "
            f"font-size: 11px; color: {status_color}; text-align: right;'>"
            f"🏁 <b>Selesai</b> dalam {elapsed:.1f} detik (Exit Code: {code})"
            f"</div>"
        )
        self._append_html(done_html)
        self.is_agent_streaming = False
        self.first_turn = False
        self.cont.setChecked(True)
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.iface.mapCanvas().refreshAllLayers()

    def shutdown(self):
        self.runner.stop()
