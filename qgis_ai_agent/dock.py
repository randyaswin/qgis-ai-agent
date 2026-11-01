import json
import os
import re
import subprocess

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

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
SKEY = "qgis_ai_agent/"


class AgentDock(QDockWidget):
    def __init__(self, iface, server):
        super().__init__("AI Map Agent")
        self.iface, self.server = iface, server
        self.runner = CliRunner(self)
        self.runner.output.connect(self._append)
        self.runner.finished.connect(self._done)
        self.server.activity.connect(lambda m: self._append(f"\n{m}\n", tool=True))
        self.first_turn = True
        s = QSettings()

        w = QWidget()
        lay = QVBoxLayout(w)

        # Row 1: CLI & MCP
        row1 = QHBoxLayout()
        self.cli = QComboBox()
        av = available()
        for name in ADAPTERS:
            self.cli.addItem(name + ("" if av[name] else "  (not found)"), name)
        self.cli.setCurrentIndex(max(0, self.cli.findData(s.value(SKEY + "cli", "agy (Antigravity CLI)"))))
        reg = QPushButton("Register MCP")
        reg.setToolTip("Run '<cli> mcp add qgis …' once so the CLI can see the QGIS tools")
        reg.clicked.connect(self._register)
        row1.addWidget(QLabel("CLI:"))
        row1.addWidget(self.cli, 1)
        row1.addWidget(reg)
        lay.addLayout(row1)

        # Row 2: Skills & Prompts control
        row2 = QHBoxLayout()
        self.skill_mode = QComboBox()
        self.skill_mode.addItem("Skills: Auto (Pilih Otomatis)", "auto")
        self.skill_mode.addItem("Skills: Manual (Hanya Pinned)", "manual")
        self.skill_mode.addItem("Skills: Off (Tanpa Skills)", "off")
        saved_mode = s.value(SKEY + "skill_mode", "auto")
        self.skill_mode.setCurrentIndex(max(0, self.skill_mode.findData(saved_mode)))
        self.skill_mode.currentIndexChanged.connect(self._on_skill_mode_changed)

        btn_skills = QPushButton("Skills Manager...")
        btn_skills.clicked.connect(self._open_skills_dialog)

        btn_prompt_inspect = QPushButton("Prompt...")
        btn_prompt_inspect.setToolTip("Lihat atau sesuaikan System Prompt & Aturan Tambahan")
        btn_prompt_inspect.clicked.connect(self._inspect_prompt)

        row2.addWidget(self.skill_mode, 1)
        row2.addWidget(btn_skills)
        row2.addWidget(btn_prompt_inspect)
        lay.addLayout(row2)

        # Row 3: Output dir
        row3 = QHBoxLayout()
        self.outdir = QLineEdit(s.value(SKEY + "outdir", os.path.expanduser("~/qgis-ai-maps")))
        b = QPushButton("…")
        b.clicked.connect(lambda: self.outdir.setText(QFileDialog.getExistingDirectory(self, "Output folder", self.outdir.text()) or self.outdir.text()))
        row3.addWidget(QLabel("Output:"))
        row3.addWidget(self.outdir, 1)
        row3.addWidget(b)
        lay.addLayout(row3)

        # Row 4: Toggles
        row4 = QHBoxLayout()
        self.auto = QCheckBox("Auto-approve")
        self.auto.setToolTip("Pass the CLI's skip-permission flag. Needed for fully unattended runs.")
        self.auto.setChecked(s.value(SKEY + "auto", True, type=bool))
        self.pyexec = QCheckBox("Allow exec_pyqgis")
        self.pyexec.setChecked(s.value(SKEY + "pyexec", False, type=bool))
        self.pyexec.toggled.connect(lambda v: tools.ALLOW_PYTHON.__setitem__("value", v))
        tools.ALLOW_PYTHON["value"] = self.pyexec.isChecked()
        self.cont = QCheckBox("Continue session")
        self.cont.setToolTip("Resume the previous CLI conversation instead of starting a new one")
        for c in (self.auto, self.pyexec, self.cont):
            row4.addWidget(c)
        lay.addLayout(row4)

        # Chat log display
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        lay.addWidget(self.log, 1)

        # User prompt input
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText("Contoh: Buat peta kepadatan penduduk per kecamatan Jawa Barat, choropleth 5 kelas natural breaks, label nama, layout A3 landscape, ekspor PDF. (Ctrl+Enter)")
        self.prompt.setMaximumHeight(110)
        lay.addWidget(self.prompt)

        # Action buttons
        row5 = QHBoxLayout()
        self.run_btn = QPushButton("▶ Run")
        self.run_btn.clicked.connect(self._run)
        self.stop_btn = QPushButton("■ Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.runner.stop)
        clr = QPushButton("Clear")
        clr.clicked.connect(self.log.clear)
        row5.addWidget(self.run_btn, 1)
        row5.addWidget(self.stop_btn)
        row5.addWidget(clr)
        lay.addLayout(row5)

        self.setWidget(w)
        self.prompt.installEventFilter(self)
        self.custom_instructions = s.value(SKEY + "custom_instructions", "")
        self._on_skill_mode_changed()

    def eventFilter(self, obj, ev):
        if obj is self.prompt and ev.type() == ev.Type.KeyPress and ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) \
                and ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._run()
            return True
        return super().eventFilter(obj, ev)

    def _on_skill_mode_changed(self):
        REGISTRY.mode = self.skill_mode.currentData()
        s = QSettings()
        s.setValue(SKEY + "skill_mode", REGISTRY.mode)

    def _open_skills_dialog(self):
        dlg = SkillsDialog(self)
        dlg.exec()

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

    def _append(self, text, tool=False):
        text = ANSI.sub("", text)
        c = self.log.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log.setTextCursor(c)
        if tool:
            self.log.setTextColor(Qt.GlobalColor.darkGreen)
        self.log.insertPlainText(text)
        self.log.setTextColor(Qt.GlobalColor.black)
        self.log.ensureCursorVisible()

    def _register(self):
        argv = self._adapter()["register"]
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60,
                               env={**os.environ, "PATH": os.path.expanduser("~/.local/bin") + os.pathsep
                                    + os.path.expanduser("~/.npm-global/bin") + os.pathsep + os.environ.get("PATH", "")})
            msg = (r.stdout + r.stderr).strip() or "OK"
            self._append(f"\n$ {' '.join(argv)}\n{msg}\n", tool=True)
        except Exception as e:
            QMessageBox.warning(self, "Register MCP", str(e))

    def _get_live_context(self):
        """Compact summary of live QGIS project layers and canvas."""
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

        REGISTRY.mode = self.skill_mode.currentData()
        sel = REGISTRY.select(user)

        # Assemble prompt
        if cont:
            # Continuing turn: send only user text, or with dynamic skill/context if new ones were picked
            full = user
        else:
            sys_text = prompts.system_prompt(Qgis.version(), outdir, REGISTRY.mode)
            if self.custom_instructions.strip():
                sys_text += f"\n\n# User custom instructions\n{self.custom_instructions.strip()}"
            live_ctx = self._get_live_context()
            catalog = list(REGISTRY.available().keys())
            skills_txt = prompts.skills_block(sel, catalog) if REGISTRY.mode != "off" else ""
            full = prompts.build(sys_text, live_ctx, skills_txt, user, continuing=False)

        argv = self._adapter()["run"](full, cont, self.auto.isChecked())

        s = QSettings()
        s.setValue(SKEY + "cli", self.cli.currentData())
        s.setValue(SKEY + "outdir", outdir)
        s.setValue(SKEY + "auto", self.auto.isChecked())
        s.setValue(SKEY + "pyexec", self.pyexec.isChecked())

        # Log UI banner
        self._append(f"\n━━ 🧑 User Request: {user}\n", tool=False)
        loaded_skills = [s.name for s in sel["pinned"] + sel["full"]]
        if loaded_skills:
            self._append(f"━━ 🧠 Skills Injected ({REGISTRY.mode}): {', '.join(loaded_skills)}\n", tool=True)
        if sel["hint"]:
            self._append(f"━━ 💡 Skills on standby (agent can call load_skill): {', '.join(s.name for s in sel['hint'])}\n", tool=True)
        self._append(f"━━ ▶ Executing {argv[0]} ({'continue session' if cont else 'new session'})...\n", tool=True)

        self.prompt.clear()
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        try:
            self.runner.start(argv, outdir)
        except Exception as e:
            self._append(f"\n[error] {e}\n")
            self._done(-1)

    def _done(self, code):
        self._append(f"\n━━ finished (exit {code})\n", tool=True)
        self.first_turn = False
        self.cont.setChecked(True)
        self.run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.iface.mapCanvas().refreshAllLayers()

    def shutdown(self):
        self.runner.stop()
