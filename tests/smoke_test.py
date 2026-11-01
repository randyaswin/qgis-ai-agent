"""Headless smoke test: tools + TCP server + stdio MCP bridge round trip."""
import json, os, subprocess, sys, threading
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qgis.core import QgsApplication, QgsProject
from qgis.gui import QgsMapCanvas, QgsLayerTreeView
from qgis.PyQt.QtCore import QEventLoop, QTimer

app = QgsApplication([], True); app.initQgis()
sys.path.append("/usr/share/qgis/python/plugins")
import processing
from processing.core.Processing import Processing; Processing.initialize()

class Iface:
    def __init__(s):
        s.c = QgsMapCanvas(); s.v = QgsLayerTreeView()
        from qgis.core import QgsLayerTreeModel
        s.v.setModel(QgsLayerTreeModel(QgsProject.instance().layerTreeRoot()))
        from qgis.gui import QgsLayerTreeMapCanvasBridge
        s.b = QgsLayerTreeMapCanvasBridge(QgsProject.instance().layerTreeRoot(), s.c)
    def mapCanvas(s): return s.c
    def layerTreeView(s): return s.v
import qgis.utils; qgis.utils.iface = Iface()

from qgis_ai_agent import tools
from qgis_ai_agent.server import ToolServer
out = "/tmp/qgis_ai_test"; os.makedirs(out, exist_ok=True)
gj = os.path.join(out, "pts.geojson")
json.dump({"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": f"K{i}", "pop": i * 1000, "cls": "ab"[i % 2]},
     "geometry": {"type": "Point", "coordinates": [106.8 + i * 0.05, -6.2 + (i % 3) * 0.05]}} for i in range(8)]}, open(gj, "w"))

T = tools.call
T("new_project", {"crs": "EPSG:4326"})
T("add_vector_layer", {"path": gj, "name": "titik"})
r = T("run_processing", {"algorithm": "native:buffer", "params": {"INPUT": "titik", "DISTANCE": 0.02}, "output_name": "buffer"})
print("buffer new layers:", [l["name"] for l in r["new_layers"]])
T("calculate_field", {"layer": "buffer", "field": "dens", "expression": "\"pop\" / ($area*1e4)"})
print(T("style_graduated", {"layer": "buffer", "field": "dens", "classes": 4})["ranges"][:2])
T("style_categorized", {"layer": "titik", "field": "cls"})
T("set_labels", {"layer": "titik", "field": "name", "placement": "around_point"})
T("zoom_to", {"layer": "buffer"})
print("snapshot bytes:", len(T("snapshot_canvas", {})["__image__"]))
print(T("create_layout", {"name": "Peta", "title": "Uji Peta", "subtitle": "sub", "credits": "Sumber: test", "grid": True}))
print(T("export_layout", {"name": "Peta", "path": out + "/peta.pdf", "dpi": 100}))
print("png preview:", "__image__" in T("export_layout", {"name": "Peta", "path": out + "/peta.png", "dpi": 100, "preview": True}))
print("tools:", len(tools.TOOLS))

# server + bridge round trip
srv = ToolServer(); srv.start()
bridge = os.path.join(os.path.dirname(tools.__file__), "mcp_bridge.py")
msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_project_state", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "exec_pyqgis", "arguments": {"code": "1"}}}]
res = {}
def run():
    p = subprocess.run([sys.executable, bridge], input="\n".join(map(json.dumps, msgs)) + "\n", capture_output=True, text=True)
    res["out"] = p.stdout; res["err"] = p.stderr
th = threading.Thread(target=run); th.start()
loop = QEventLoop()
t = QTimer(); t.timeout.connect(lambda: (not th.is_alive()) and loop.quit()); t.start(50)
loop.exec()
for line in res["out"].splitlines():
    m = json.loads(line); r = m.get("result", m)
    print("bridge", m["id"], (str(len(r["tools"])) + " tools") if "tools" in r else json.dumps(r)[:160])
print(res["err"][-500:])
srv.stop()
