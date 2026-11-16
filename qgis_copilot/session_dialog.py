"""Dialog for managing project chat sessions, switching branches, and exporting history."""
import os
from . import paths
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem,
    QPushButton, QLabel, QInputDialog, QMessageBox, QFileDialog, QHeaderView, QGroupBox,
)
from qgis.core import QgsProject


class SessionManagerDialog(QDialog):
    def __init__(self, session_manager, parent=None):
        super().__init__(parent)
        self.sm = session_manager
        self.setWindowTitle("Project Session Manager")
        self.resize(760, 460)

        main_layout = QVBoxLayout(self)

        # Top Project Info Banner
        p = QgsProject.instance()
        proj_title = p.title() or os.path.basename(p.fileName()) if p.fileName() else "Untitled Project"
        proj_path = p.fileName() or "Unsaved project in memory"
        info_box = QGroupBox("Project Information")
        info_lay = QVBoxLayout(info_box)
        info_lay.addWidget(QLabel(f"<b>Project:</b> {proj_title} (<code>{self.sm.get_project_id()}</code>)"))
        info_lay.addWidget(QLabel(f"<b>File:</b> {proj_path}"))
        main_layout.addWidget(info_box)

        # Table of sessions
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Status", "Session Name", "Turns", "Model / Effort", "Last Updated"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(self._activate_selected)
        main_layout.addWidget(self.table, 1)

        # Action Buttons Row
        btn_row = QHBoxLayout()
        btn_activate = QPushButton("▶ Switch To")
        btn_activate.setToolTip("Switch active conversation session")
        btn_activate.clicked.connect(self._activate_selected)

        btn_new = QPushButton("➕ New Session")
        btn_new.clicked.connect(self._new_session)

        btn_rename = QPushButton("✏️ Rename")
        btn_rename.clicked.connect(self._rename_session)

        btn_dup = QPushButton("📋 Duplicate")
        btn_dup.setToolTip("Branch this session into a new conversation copy")
        btn_dup.clicked.connect(self._duplicate_session)

        btn_export = QPushButton("💾 Export...")
        btn_export.clicked.connect(self._export_session)

        btn_delete = QPushButton("🗑️ Delete")
        btn_delete.clicked.connect(self._delete_session)

        btn_row.addWidget(btn_activate)
        btn_row.addWidget(btn_new)
        btn_row.addWidget(btn_rename)
        btn_row.addWidget(btn_dup)
        btn_row.addWidget(btn_export)
        btn_row.addWidget(btn_delete)
        main_layout.addLayout(btn_row)

        # Bottom Bar
        bottom_bar = QHBoxLayout()
        self.lbl_stats = QLabel()
        bottom_bar.addWidget(self.lbl_stats)
        bottom_bar.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(btn_close)
        main_layout.addLayout(bottom_bar)

        self._refresh_table()

    def _selected_session_id(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 1)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _refresh_table(self):
        self.table.setRowCount(0)
        sessions = sorted(self.sm.sessions.values(), key=lambda s: s.updated_at, reverse=True)
        self.table.setRowCount(len(sessions))

        for row, s in enumerate(sessions):
            is_active = (s.id == self.sm.active_session_id)
            status_str = "🟢 Active" if is_active else "⚪"
            status_item = QTableWidgetItem(status_str)
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            name_item = QTableWidgetItem(s.name)
            name_item.setData(Qt.ItemDataRole.UserRole, s.id)
            if is_active:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)

            turns_item = QTableWidgetItem(f"{s.turns_count} turns")
            turns_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            model_str = s.model or s.cli or "-"
            if s.effort and s.effort != "default":
                model_str += f" ({s.effort})"
            model_item = QTableWidgetItem(model_str)

            time_item = QTableWidgetItem(s.updated_at)

            self.table.setItem(row, 0, status_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, turns_item)
            self.table.setItem(row, 3, model_item)
            self.table.setItem(row, 4, time_item)

            if is_active:
                self.table.selectRow(row)

        self.lbl_stats.setText(f"Total sessions: {len(sessions)}")

    def _activate_selected(self):
        sid = self._selected_session_id()
        if sid:
            self.sm.switch_session(sid)
            self._refresh_table()
            self.accept()

    def _new_session(self):
        name, ok = QInputDialog.getText(self, "New Session", "Enter session name:")
        if ok and name.strip():
            self.sm.create_session(name=name.strip())
            self._refresh_table()

    def _rename_session(self):
        sid = self._selected_session_id()
        if not sid:
            return
        curr_name = self.sm.sessions[sid].name
        name, ok = QInputDialog.getText(self, "Rename Session", "Enter new name:", text=curr_name)
        if ok and name.strip():
            self.sm.rename_session(sid, name.strip())
            self._refresh_table()

    def _duplicate_session(self):
        sid = self._selected_session_id()
        if sid:
            self.sm.duplicate_session(sid)
            self._refresh_table()

    def _delete_session(self):
        sid = self._selected_session_id()
        if not sid:
            return
        if len(self.sm.sessions) <= 1:
            QMessageBox.warning(self, "Cannot Delete", "The project must have at least one active session.")
            return
        name = self.sm.sessions[sid].name
        if QMessageBox.question(self, "Confirm Delete", f"Delete session '{name}' and its chat history?") == QMessageBox.StandardButton.Yes:
            self.sm.delete_session(sid)
            self._refresh_table()

    def _export_session(self):
        sid = self._selected_session_id()
        if not sid:
            return
        s = self.sm.sessions[sid]
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Export Chat History",
            os.path.join(paths.get_documents_dir(), f"chat_{s.name.replace(' ', '_').lower()}.md"),
            "Markdown (*.md);;JSON (*.json)"
        )
        if save_path:
            try:
                if save_path.endswith(".json"):
                    import json
                    with open(save_path, "w", encoding="utf-8") as f:
                        json.dump(s.to_dict(), f, indent=2, ensure_ascii=False)
                else:
                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(s.export_markdown())
                QMessageBox.information(self, "Export Successful", f"Saved to {save_path}")
            except Exception as e:
                QMessageBox.warning(self, "Export Failed", str(e))
