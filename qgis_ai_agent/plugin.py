import os

from qgis.PyQt.QtCore import Qt
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
        self.server = ToolServer()
        port = self.server.start()
        from .dock import AgentDock
        self.dock = AgentDock(self.iface, self.server)
        self.dock.setObjectName("QgisAIAgentDock")
        self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
        self.action = QAction(QIcon(os.path.join(os.path.dirname(__file__), "icon.svg")), "AI Map Agent", self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.setChecked(True)
        self.action.toggled.connect(self.dock.setVisible)
        self.dock.visibilityChanged.connect(self.action.setChecked)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("AI Map Agent", self.action)
        self.iface.messageBar().pushInfo("AI Map Agent", f"MCP endpoint listening on 127.0.0.1:{port}")

    def unload(self):
        if self.dock:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
        if self.action:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("AI Map Agent", self.action)
        if self.server:
            self.server.stop()
