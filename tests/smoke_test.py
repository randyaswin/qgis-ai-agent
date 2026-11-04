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
with open(gj, "w") as f:
    json.dump({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": f"K{i}", "pop": i * 1000, "cls": "ab"[i % 2]},
         "geometry": {"type": "Point", "coordinates": [106.8 + i * 0.05, -6.2 + (i % 3) * 0.05]}} for i in range(8)]}, f)

T = tools.call
p_state = T("new_project", {"crs": "EPSG:4326"})
assert p_state["crs"] == "EPSG:4326", f"Expected EPSG:4326, got {p_state['crs']}"

v_info = T("add_vector_layer", {"path": gj, "name": "titik"})
assert v_info["features"] == 8, f"Expected 8 features, got {v_info['features']}"

r = T("run_processing", {"algorithm": "native:buffer", "params": {"INPUT": "titik", "DISTANCE": 0.02}, "output_name": "buffer"})
new_names = [l["name"] for l in r["new_layers"]]
print("buffer new layers:", new_names)
assert "buffer" in new_names, f"Expected 'buffer' layer, got {new_names}"

calc_res = T("calculate_field", {"layer": "buffer", "field": "dens", "expression": "\"pop\" / ($area*1e4)"})
assert calc_res["updated"] == 8, f"Expected 8 updated features, got {calc_res['updated']}"

f_stats = T("field_stats", {"layer": "buffer", "field": "dens"})
assert f_stats["count"] == 8, f"Expected 8 counted items, got {f_stats}"
assert f_stats["min"] >= 0, f"Expected non-negative dens, got {f_stats}"

desc = T("describe_layer", {"layer": "titik"})
assert desc["valid"] is True, f"Layer 'titik' not valid: {desc}"
assert desc["feature_count"] == 8, f"Expected 8 features in describe_layer, got {desc['feature_count']}"

# Test digitize_features
c_layer = T("create_vector_layer", {"layer_name": "poly_test", "geometry_type": "Polygon", "fields": [{"name": "tag", "type": "string"}]})
assert c_layer["name"] == "poly_test", f"Failed to create vector layer: {c_layer}"
d_res = T("digitize_features", {"layer": "poly_test", "features": [{"geometry": [[106.8, -6.2], [106.9, -6.2], [106.9, -6.1], [106.8, -6.2]], "attributes": {"tag": "zone_a"}}]})
assert d_res["features_added"] == 1, f"Expected 1 feature added, got {d_res}"

# Test sampling grid generation & labeling
grid_res = T("create_sampling_grid", {"layer_name": "grid_test", "extent_layer": "buffer", "cell_size": 0.05, "grid_type": "square"})
assert grid_res["cell_count"] > 0, f"Expected grid cells, got {grid_res}"
print("sampling grid created, cell count:", grid_res["cell_count"])

lbl_res = T("label_grid_cells", {
    "layer": "grid_test",
    "labels": [
        {"grid_id": 1, "class": "Vegetation", "confidence": 0.95, "notes": "Tree canopy"},
        {"grid_id": 2, "class": "Water Body", "confidence": 0.98, "notes": "Reservoir"}
    ],
    "auto_style": True
})
assert lbl_res["cells_updated"] >= 1, f"Expected updated cells, got {lbl_res}"
print("labeled grid cells:", lbl_res["cells_updated"])

grad_res = T("style_graduated", {"layer": "buffer", "field": "dens", "classes": 4})
assert len(grad_res["ranges"]) >= 2, f"Expected >= 2 ranges, got {grad_res['ranges']}"
print(grad_res["ranges"][:2])

T("style_categorized", {"layer": "titik", "field": "cls"})
T("set_labels", {"layer": "titik", "field": "name", "placement": "around_point"})
z_res = T("zoom_to", {"layer": "buffer"})
assert len(z_res["extent"]) == 4, f"Invalid zoom extent: {z_res}"

snap = T("snapshot_canvas", {})
assert len(snap["__image__"]) > 500, f"Snapshot too small: {len(snap['__image__'])}"
print("snapshot bytes:", len(snap["__image__"]))

lay_res = T("create_layout", {"name": "Peta", "title": "Uji Peta", "subtitle": "sub", "credits": "Sumber: test", "grid": True})
assert lay_res["layout"] == "Peta", f"Layout creation failed: {lay_res}"
print(lay_res)

exp_pdf = T("export_layout", {"name": "Peta", "path": out + "/peta.pdf", "dpi": 100})
assert exp_pdf.get("success") or exp_pdf.get("code") == 0, f"PDF export failed: {exp_pdf}"
print(exp_pdf)

exp_png = T("export_layout", {"name": "Peta", "path": out + "/peta.png", "dpi": 100, "preview": True})
assert "__image__" in exp_png, f"Missing preview image in export_layout: {exp_png}"
print("png preview:", "__image__" in exp_png)

# Test error handling
try:
    T("describe_layer", {"layer": "layer_that_does_not_exist"})
    raise AssertionError("Should have raised ValueError for non-existent layer")
except ValueError:
    pass

try:
    T("set_project_crs", {"crs": "NOT_A_VALID_CRS"})
    raise AssertionError("Should have raised ValueError for invalid CRS")
except ValueError:
    pass

print("tools:", len(tools.TOOLS))
assert len(tools.TOOLS) >= 50, f"Expected at least 50 tools, got {len(tools.TOOLS)}"

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
received_ids = []
for line in res["out"].splitlines():
    m = json.loads(line)
    r = m.get("result", m)
    received_ids.append(m.get("id"))
    print("bridge", m["id"], (str(len(r["tools"])) + " tools") if "tools" in r else json.dumps(r)[:160])
assert 1 in received_ids, "Missing init response from bridge"
assert 2 in received_ids, "Missing tools/list response from bridge"
assert 3 in received_ids, "Missing tools/call response from bridge"
assert 4 in received_ids, "Missing exec_pyqgis response from bridge"
print(res["err"][-500:])
srv.stop()
print("All smoke test assertions PASSED!")
