"""Qt Dialog for managing, pinning, inspecting, and installing AI GIS skills."""
import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem,
    QTextEdit, QPushButton, QLabel, QSplitter, QCheckBox, QFileDialog, QInputDialog,
    QMessageBox, QGroupBox,
)

from .skills import REGISTRY, USER_DIR


class SkillsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI GIS Skills Manager")
        self.resize(780, 520)

        main_layout = QVBoxLayout(self)

        top_bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search skills (e.g. 'flood', 'choropleth', 'lidar', 'utm', 'layout', 'overture')...")
        self.search_box.textChanged.connect(self._refresh_list)
        top_bar.addWidget(QLabel("Search:"))
        top_bar.addWidget(self.search_box, 1)

        btn_install_folder = QPushButton("📁 Install from Folder")
        btn_install_folder.clicked.connect(self._install_folder)
        btn_install_git = QPushButton("🌐 Install from Git")
        btn_install_git.clicked.connect(self._install_git)
        top_bar.addWidget(btn_install_folder)
        top_bar.addWidget(btn_install_git)
        main_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_widget = QGroupBox("Skills Catalog")
        left_layout = QVBoxLayout(left_widget)
        self.skill_list = QListWidget()
        self.skill_list.currentItemChanged.connect(self._on_select_skill)
        left_layout.addWidget(self.skill_list)

        btn_row = QHBoxLayout()
        self.btn_pin = QPushButton("📌 Pre-load / Pin Skill")
        self.btn_pin.clicked.connect(self._toggle_pin)
        self.btn_disable = QPushButton("🚫 Disable/Enable")
        self.btn_disable.clicked.connect(self._toggle_disable)
        self.btn_delete = QPushButton("🗑️ Delete User Skill")
        self.btn_delete.clicked.connect(self._delete_user_skill)
        btn_row.addWidget(self.btn_pin)
        btn_row.addWidget(self.btn_disable)
        btn_row.addWidget(self.btn_delete)
        left_layout.addLayout(btn_row)
        splitter.addWidget(left_widget)

        right_widget = QGroupBox("Skill Details & Guide (SKILL.md)")
        right_layout = QVBoxLayout(right_widget)
        self.detail_text = QTextEdit()
        self.detail_text.setReadOnly(True)
        right_layout.addWidget(self.detail_text)
        splitter.addWidget(right_widget)

        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)
        main_layout.addWidget(splitter, 1)

        bottom_bar = QHBoxLayout()
        self.lbl_stats = QLabel()
        bottom_bar.addWidget(self.lbl_stats)
        bottom_bar.addStretch()
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        bottom_bar.addWidget(btn_close)
        main_layout.addLayout(bottom_bar)

        self._refresh_list()

    def _refresh_list(self):
        query = self.search_box.text().strip()
        self.skill_list.clear()
        results = REGISTRY.search(query, limit=100) if query else [
            {**REGISTRY.skills[n].brief(), "score": 0} for n in sorted(REGISTRY.skills.keys())
        ]

        total = len(REGISTRY.skills)
        pinned_count = len(REGISTRY.pinned)
        disabled_count = len(REGISTRY.disabled)
        self.lbl_stats.setText(f"Total: {total} skills | Pinned: {pinned_count} | Disabled: {disabled_count}")

        for s in results:
            name = s["name"]
            is_pinned = name in REGISTRY.pinned
            is_disabled = name in REGISTRY.disabled
            tag = []
            if is_pinned:
                tag.append("📌 PRE-LOADED")
            if is_disabled:
                tag.append("🚫 DISABLED")
            tag_str = f" [{', '.join(tag)}]" if tag else ""
            item_text = f"{name}{tag_str} ({s['source']})"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            if is_disabled:
                item.setForeground(Qt.GlobalColor.gray)
            elif is_pinned:
                item.setForeground(Qt.GlobalColor.darkBlue)
            self.skill_list.addItem(item)

        if self.skill_list.count() > 0:
            self.skill_list.setCurrentRow(0)

    def _selected_skill_name(self):
        cur = self.skill_list.currentItem()
        return cur.data(Qt.ItemDataRole.UserRole) if cur else None

    def _on_select_skill(self, current, _previous):
        if not current:
            self.detail_text.clear()
            return
        name = current.data(Qt.ItemDataRole.UserRole)
        s = REGISTRY.skills.get(name)
        if not s:
            return
        refs = f"\n\n**Additional Reference Files:**\n" + "\n".join(f"- {r}" for r in s.references) if s.references else ""
        header = f"# {s.name}\n**Source:** {s.source} | **Path:** {s.path}\n**Description:** {s.description}\n{refs}\n\n---\n\n"
        self.detail_text.setMarkdown(header + s.body())

    def _toggle_pin(self):
        name = self._selected_skill_name()
        if not name:
            return
        if name in REGISTRY.pinned:
            REGISTRY.pinned.remove(name)
        else:
            REGISTRY.pinned.add(name)
        REGISTRY.save_settings()
        self._refresh_list()

    def _toggle_disable(self):
        name = self._selected_skill_name()
        if not name:
            return
        if name in REGISTRY.disabled:
            REGISTRY.disabled.remove(name)
        else:
            REGISTRY.disabled.add(name)
            if name in REGISTRY.pinned:
                REGISTRY.pinned.remove(name)
        self._refresh_list()

    def _delete_user_skill(self):
        name = self._selected_skill_name()
        if not name:
            return
        s = REGISTRY.skills.get(name)
        if not s or s.source != "user":
            QMessageBox.information(self, "Delete Skill", "Only user-installed skills in ~/.qgis_ai_agent/skills/ can be deleted.")
            return
        if QMessageBox.question(self, "Confirm Delete", f"Delete skill '{name}'?") == QMessageBox.StandardButton.Yes:
            REGISTRY.remove_user_skill(name)
            self._refresh_list()

    def _install_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Skill Folder (must contain SKILL.md)")
        if folder:
            try:
                installed = REGISTRY.install_path(folder)
                QMessageBox.information(self, "Success", f"Successfully installed skills: {', '.join(installed)}")
                self._refresh_list()
            except Exception as e:
                QMessageBox.warning(self, "Installation Failed", str(e))

    def _install_git(self):
        url, ok = QInputDialog.getText(self, "Install from Git", "Enter Git repository URL (https://...):")
        if ok and url.strip():
            try:
                installed = REGISTRY.install_git(url.strip())
                QMessageBox.information(self, "Success", f"Successfully installed skills from Git: {', '.join(installed)}")
                self._refresh_list()
            except Exception as e:
                QMessageBox.warning(self, "Installation Failed", str(e))
