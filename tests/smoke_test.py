"""Headless smoke test: tools + TCP server + stdio MCP bridge round trip."""
import glob, json, os, stat, subprocess, sys, tempfile, threading
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from qgis.core import QgsApplication, QgsProject
from qgis.gui import QgsMapCanvas, QgsLayerTreeView
from qgis.PyQt.QtCore import QEventLoop, QTimer

app = QgsApplication([], True); app.initQgis()

def _find_qgis_plugins_path():
    cands = []
    pkg_data = QgsApplication.pkgDataPath()
    if pkg_data:
        cands.append(os.path.join(pkg_data, "python", "plugins"))
    osgeo_root = os.environ.get("OSGEO4W_ROOT")
    if osgeo_root:
        cands.extend(glob.glob(os.path.join(osgeo_root, "apps", "qgis*", "python", "plugins")))
    cands.extend([
        "/usr/share/qgis/python/plugins",
        "/usr/local/share/qgis/python/plugins",
        "/Applications/QGIS.app/Contents/Resources/python/plugins",
    ])
    for c in cands:
        if c and os.path.isdir(os.path.join(c, "processing")) and c not in sys.path:
            return c
    return None

_proc_plug = _find_qgis_plugins_path()
if _proc_plug:
    sys.path.append(_proc_plug)
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
out = os.path.join(tempfile.gettempdir(), f"qgis_ai_test_{os.getpid()}")
os.makedirs(out, exist_ok=True)
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

# Expanded audit checks: LOGGER, layout_tools QgsLineSymbol, schemas, avoid_overlap, helmert RMSE, skills & dock summaries
tools.LOGGER.info("smoke test logger info")
tools.LOGGER.warning("smoke test logger warning")

from qgis_ai_agent import layout_tools, skills, dock
assert hasattr(layout_tools, "QgsLineSymbol"), "QgsLineSymbol missing in layout_tools"

# Verify updated tool schemas
assert "avoid_overlap" in tools.TOOLS["digitize_features"]["inputSchema"]["properties"]
assert "snap_tolerance" in tools.TOOLS["digitize_features"]["inputSchema"]["properties"]
assert "show_labels" in tools.TOOLS["create_sampling_grid"]["inputSchema"]["properties"]
assert "opacity" in tools.TOOLS["style_categorized"]["inputSchema"]["properties"]
assert "color_map" in tools.TOOLS["style_categorized"]["inputSchema"]["properties"]

# Verify avoid_overlap & snap_tolerance in digitize_features
ov_res = T("digitize_features", {
    "layer": "poly_test",
    "features": [{"geometry": [[106.85, -6.2], [106.95, -6.2], [106.95, -6.1], [106.85, -6.2]], "attributes": {"tag": "zone_b"}}],
    "avoid_overlap": True,
    "snap_tolerance": 0.005,
})
assert ov_res["features_added"] == 1 and ov_res["total_features"] == 2, f"Unexpected overlap digitize result: {ov_res}"

# Verify helmert transform + RMSE diagnostics on a synthetic raster
from osgeo import gdal as _gdal
synth_img = os.path.join(out, "synth_scan.tif")
drv = _gdal.GetDriverByName("GTiff")
sds = drv.Create(synth_img, 100, 100, 1, _gdal.GDT_Byte)
sds.GetRasterBand(1).Fill(128)
sds = None
georef_out = os.path.join(out, "synth_georef.tif")
gres = T("georeference_raster", {
    "image_path": synth_img,
    "output_path": georef_out,
    "gcps": [[0, 0, 106.8, -6.1], [100, 100, 106.9, -6.2]],
    "transform": "helmert",
    "crs": "EPSG:4326",
    "load_to_canvas": True,
})
assert gres["gcp_count"] == 2 and "rmse_pixels" in gres and len(gres["gcp_residuals"]) == 2, f"Helmert RMSE check failed: {gres}"
assert gres["rmse_pixels"] < 0.01, f"Expected near-zero Helmert 2-GCP residual, got {gres['rmse_pixels']}"

# Verify skills registry, keywords_id, vague request, and disabled persistence
reg = skills.REGISTRY
assert "raster-interpretation-and-grid-labeling" in reg.skills, "Missing raster-interpretation-and-grid-labeling skill"
assert "raster-interpretation-and-grid-labeling" in reg.extra.get("keywords", {}), "Missing keywords_id entry for raster-interpretation-and-grid-labeling"
assert not reg.is_vague_request("klasifikasi grid tutupan lahan dari citra ini", layer_names=["synth_georef"]), "Prompt with loaded layers should not be vague"
assert not reg.is_vague_request("digitasi batas zonasi dari file scan_peta.png"), "Prompt with image attachment should not be vague"
sel_grid = reg.select("buat sampling fishnet grid labeling untuk interpretasi citra", layer_names=["synth_georef"])
grid_names = [s.name for s in sel_grid["full"] + sel_grid["hint"]]
assert "raster-interpretation-and-grid-labeling" in grid_names, f"Expected grid labeling skill, got {grid_names}"

# Verify summarize_tool_result formatting
assert "RMSE=" in dock.summarize_tool_result("georeference_raster", gres)
assert "Digitized +1 features" in dock.summarize_tool_result("digitize_features", ov_res)
assert "cells generated" in dock.summarize_tool_result("create_sampling_grid", grid_res) or "Sampling grid" in dock.summarize_tool_result("create_sampling_grid", grid_res)

# --- Phase 2: performance, memory bounds, new skills, & auto skill selection ---
from qgis_ai_agent import sessions, prompts

# 1. Streaming Welford vector stats + NoGeometry/chunked calculate_field
d_titik = T("describe_layer", {"layer": "titik"})
assert d_titik["scanned"] == 8 and d_titik["invalid_geometries_sampled"] == 0 and d_titik["invalid_geometries_checked"] == 8, d_titik
pop_stat = next(fs for fs in d_titik["fields"] if fs["name"] == "pop")
assert pop_stat["min"] == 0.0 and pop_stat["max"] == 7000.0 and pop_stat["mean"] == 3500.0 and pop_stat["sum"] == 28000.0, pop_stat
calc_attr = T("calculate_field", {"layer": "titik", "field": "pop2", "expression": '"pop" * 2', "field_type": "double"})
assert calc_attr["updated"] == 8 and calc_attr["eval_error"] is None, calc_attr
calc_geom = T("calculate_field", {"layer": "poly_test", "field": "poly_area", "expression": "$area", "field_type": "double"})
assert calc_geom["updated"] == 2 and calc_geom["eval_error"] is None, calc_geom

# 2. Compact tool args, checkpoint eviction cap, manifest trimming, tail reader, prompt cache
comp = sessions._compact_tool_args({"big": "x" * 1000, "arr": list(range(30)), "__image__": "abc"})
assert "...(+" in comp["big"] and len(comp["arr"]) == 13 and comp["__image__"] == "<base64:3 chars>", comp

chk_sess = sessions.Session(name="Test")
chk_sess.add_user_message("hello", "<div>raw_card</div>")
evicted_all = []
for cyc in range(1, 14):
    evicted_all.extend(chk_sess.add_checkpoint(cyc, f"checkpoint_cycle_{cyc}.qgz", prompt=f"Cycle {cyc}"))
assert len(chk_sess.checkpoints) == 10, f"Expected 10 checkpoints kept, got {len(chk_sess.checkpoints)}"
assert [c["cycle"] for c in chk_sess.checkpoints] == list(range(4, 14)), chk_sess.checkpoints
assert evicted_all == ["checkpoint_cycle_1.qgz", "checkpoint_cycle_2.qgz", "checkpoint_cycle_3.qgz"], evicted_all
assert chk_sess.to_dict(include_html=False)["messages"][0]["html"] == "", "Compact manifest must omit HTML"

tail_path = os.path.join(out, "large_transcript.jsonl")
with open(tail_path, "w", encoding="utf-8") as tf:
    for i in range(250):
        tf.write(json.dumps({"idx": i, "thinking": "pad " * 60 if i < 249 else "final_thought"}) + "\n")
tail_lines = dock._tail_lines(tail_path, max_bytes=4096, max_lines=5)
assert len(tail_lines) == 5 and json.loads(tail_lines[-1])["thinking"] == "final_thought", tail_lines

sp1 = prompts.system_prompt("3.40", out, "auto")
sp2 = prompts.system_prompt("3.40", out, "auto")
assert sp1 == sp2 and prompts._AGENT_MD_CACHE["text"] is not None

# 3. Four new custom skills registered with cached _body
new_custom_skills = (
    "urban-planning-and-zoning-rtrw",
    "multi-hazard-and-environmental-modeling",
    "hydrology-watershed-and-coastal-analysis",
    "automated-data-cleaning-and-topology-qa",
)
for sk_name in new_custom_skills:
    assert sk_name in reg.skills, f"Missing new custom skill: {sk_name}"
    assert sk_name in reg.extra.get("keywords", {}), f"Missing keywords_id entry: {sk_name}"
    assert reg.skills[sk_name]._body is not None and len(reg.skills[sk_name].body()) > 200, f"Expected cached body for {sk_name}"

# 4. Improved auto skill selection: zero pinned leakage on non-E2E tasks, deduplicated vendor skills, <= 45k budget
orig_mode = reg.mode
try:
    reg.mode = "auto"
    sel_clean = reg.select("bersihkan topologi sliver polygon dan perbaiki geometri invalid pada layer persil")
    clean_full = [s.name for s in sel_clean["full"]]
    assert not sel_clean["is_e2e"], "Topology clean utility ask should not be E2E map creation"
    assert sel_clean["pinned"] == [], f"Auto mode should not pre-load pinned skills without always=true, got {sel_clean['pinned']}"
    assert "automated-data-cleaning-and-topology-qa" in clean_full, f"Expected data-cleaning skill, got {clean_full}"
    assert "cartographic-symbology-and-styling" not in clean_full and "advanced-map-layouting" not in clean_full, clean_full
    assert "geo-data-engineering" not in clean_full, f"Expected vendor geo-data-engineering demoted to hint, got {clean_full}"

    sel_rtrw = reg.select("buat peta zonasi rtrw pola ruang dan kesesuaian sempadan sungai jakarta")
    rtrw_full = [s.name for s in sel_rtrw["full"]]
    assert sel_rtrw["is_e2e"] and len(rtrw_full) <= 4, rtrw_full
    assert "urban-planning-and-zoning-rtrw" in rtrw_full, f"Expected urban-planning skill, got {rtrw_full}"
    assert "advanced-map-layouting" in rtrw_full, f"Expected mandatory layout skill in E2E RTRW, got {rtrw_full}"
    assert sum(s.size for s in sel_rtrw["full"]) <= reg.budget_chars

    sel_haz = reg.select("buat peta kerentanan tanah longsor dan multi hazard inarisk bandung")
    haz_full = [s.name for s in sel_haz["full"]]
    assert "multi-hazard-and-environmental-modeling" in haz_full, f"Expected multi-hazard skill, got {haz_full}"

    sel_hyd = reg.select("buat peta daerah aliran sungai das dan analisis slr pesisir semarang")
    hyd_full = [s.name for s in sel_hyd["full"]]
    assert "hydrology-watershed-and-coastal-analysis" in hyd_full, f"Expected hydrology skill, got {hyd_full}"
    assert "terrain-hydrology" not in hyd_full, f"Expected vendor terrain-hydrology demoted to hint, got {hyd_full}"
finally:
    reg.mode = orig_mode

# --- Phase 3: thread/memory safety & cross-platform portability ---
from qgis_ai_agent import paths, runner, server

# 1. Cross-platform Python & CLI path discovery
py_exec = paths.get_python_executable()
assert py_exec and os.path.isfile(py_exec) and "windowsapps" not in os.path.normcase(py_exec), f"Bad PY: {py_exec}"
assert runner.PY == py_exec, f"Expected runner.PY == {py_exec}, got {runner.PY}"
assert isinstance(paths.get_extra_bin_paths(), list)
assert isinstance(paths.get_subprocess_kwargs(), dict)
found_py = runner.find_cli_bin("python3") or runner.find_cli_bin("python")
assert found_py, "find_cli_bin failed to locate python"

# 2. Windows-safe read-only tree removal
ro_dir = os.path.join(out, "ro_tree")
os.makedirs(ro_dir, exist_ok=True)
ro_file = os.path.join(ro_dir, "locked.pack")
with open(ro_file, "w") as rf:
    rf.write("readonly")
os.chmod(ro_file, stat.S_IREAD)
skills._rmtree_safe(ro_dir)
assert not os.path.exists(ro_dir), "_rmtree_safe failed to delete read-only directory"

# 3. Safe ModelDiscoveryThread cancellation & cleanup
mt = runner.ModelDiscoveryThread("Codex CLI")
mt.cancel()
mt.start()
assert mt.wait(3000), "Cancelled ModelDiscoveryThread did not finish cleanly"

# 4. Atomic ToolServer endpoint & re-entrancy guard
srv2 = ToolServer()
srv2.start()
assert os.path.exists(server.ENDPOINT_FILE), "Missing atomic ENDPOINT_FILE"
srv2._reading_sockets.add("dummy_sock")
srv2.buffers["dummy_sock"] = b""
srv2._on_read("dummy_sock")  # Must immediately return without error or recursion
srv2._reading_sockets.discard("dummy_sock")
srv2.buffers.pop("dummy_sock", None)
srv2.stop()
assert not os.path.exists(server.ENDPOINT_FILE), "ToolServer.stop() did not remove ENDPOINT_FILE"

# 5. New empty project with canvas freeze guard & no hardcoded bbox in Overture
T("new_project", {"crs": "EPSG:4326"})
assert len(QgsProject.instance().mapLayers()) == 0
try:
    tools.query_overture_maps(theme="places")
    raise AssertionError("Expected ValueError when Overture called on empty project without bbox")
except (ValueError, RuntimeError) as exc:
    assert "bounding box" in str(exc).lower() or "duckdb" in str(exc).lower(), f"Unexpected Overture error: {exc}"

print("tools count check:", len(tools.TOOLS))
assert len(tools.TOOLS) == 70, f"Expected 70 tools, got {len(tools.TOOLS)}"

# Clean up test temp dir cleanly
QgsProject.instance().clear()
skills._rmtree_safe(out, ignore_errors=True)

print("All smoke test assertions PASSED!")
