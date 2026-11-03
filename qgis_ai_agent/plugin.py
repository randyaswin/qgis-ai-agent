import os

from qgis.PyQt.QtCore import Qt, QCoreApplication, QTimer
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QApplication, QMessageBox

from .server import ToolServer


class AIAgentPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.server = None
        self.dock = None
        self.action = None
        self.guard_timer = None

    def initGui(self):
        import sys
        if "qgis_ai_agent.dock" in sys.modules:
            del sys.modules["qgis_ai_agent.dock"]

        self.server = ToolServer()
        port = self.server.start()
        from .dock import AgentDock
        self.dock = AgentDock(self.iface, self.server)
        self.dock.setObjectName("QgisAIAgentDock")
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.action = QAction(QIcon(os.path.join(os.path.dirname(__file__), "icon.svg")), "QGIS Copilot", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.toggled.connect(self.dock.setVisible)
        self.dock.visibilityChanged.connect(self.action.setChecked)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("QGIS Copilot", self.action)
        self.iface.messageBar().pushInfo("QGIS Copilot", f"MCP endpoint listening on 127.0.0.1:{port}")

        # Guard timer to auto-dismiss and repair repetitive modal projection errors
        self.guard_timer = QTimer()
        self.guard_timer.setInterval(400)
        self.guard_timer.timeout.connect(self._check_modal_exceptions)
        self.guard_timer.start()

        # Ensure complete shutdown if QGIS is closed
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.unload)

    def _check_modal_exceptions(self):
        try:
            widget = QApplication.activeModalWidget()
            if isinstance(widget, QMessageBox):
                txt = f"{widget.text() or ''} {widget.informativeText() or ''} {widget.detailedText() or ''}"
                if "cannot create std::vector" in txt or "std::length_error" in txt:
                    widget.accept()
                    from .tools import _sanitize_canvas_extent
                    _sanitize_canvas_extent()
                    if hasattr(self.iface, "messageBar"):
                        self.iface.messageBar().pushWarning(
                            "QGIS Copilot",
                            "Auto-repaired canvas extent to resolve projection coordinate overflow."
                        )
        except Exception:
            pass

    def unload(self):
        if self.guard_timer:
            self.guard_timer.stop()
            self.guard_timer = None
        if self.dock:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("QGIS Copilot", self.action)
            self.action = None
        if self.server:
            self.server.stop()
            self.server = None
