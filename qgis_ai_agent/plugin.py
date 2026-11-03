import os

from qgis.PyQt.QtCore import Qt, QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .server import ToolServer


class AIAgentPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.server = None
        self.dock = None
        self.action = None

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

        # Ensure complete shutdown if QGIS is closed
        app = QCoreApplication.instance()
        if app:
            app.aboutToQuit.connect(self.unload)

    def unload(self):
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
