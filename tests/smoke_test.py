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

# 6. Dynamic CLI model auto-detection & disk cache + effort flag forwarding
agy_models = runner.get_cli_models("agy (Antigravity CLI)", force_refresh=True)
agy_ids = [m_id for _, m_id in agy_models]
assert agy_models and agy_models[0][1] == "", f"Expected Default entry first, got {agy_models[:2]}"
assert any(m.startswith("gemini-4-") for m in agy_ids), f"Expected gemini-4-* in agy models: {agy_ids}"
if runner.is_bin_available("agy"):
    assert os.path.exists(paths.get_models_cache_file()), "Expected persisted models cache file"
runner._CLI_MODELS_CACHE.pop("agy (Antigravity CLI)", None)
cached_agy = runner.get_cli_models("agy (Antigravity CLI)", force_refresh=False, allow_subprocess=False)
cached_ids = [m_id for _, m_id in cached_agy]
assert any(m.startswith("gemini-4-") for m in cached_ids), f"Expected cached gemini-4-* without subprocess: {cached_ids}"
agy_argv = runner.ADAPTERS["agy (Antigravity CLI)"]["run"]("test prompt", False, True, model="gemini-4-argon-high", effort="xhigh")
assert "--model" in agy_argv and "gemini-4-argon-high" in agy_argv and "--effort" in agy_argv and "xhigh" in agy_argv, agy_argv

# 7. Dead-QGIS PID checks, fast /proc tree discovery, stale endpoint cleanup, and Overture normalization
import time as _t
from qgis_ai_agent import guard, mcp_bridge

assert mcp_bridge._is_pid_alive(os.getpid()) is True
assert mcp_bridge._is_pid_alive(99999999) is False
assert guard._is_pid_alive(os.getpid()) is True
assert guard._is_pid_alive(99999999) is False

_t0 = _t.monotonic()
_tree = guard._get_process_tree(os.getpid(), {os.getpid()})
_elapsed = _t.monotonic() - _t0
assert isinstance(_tree, list) and _elapsed < 0.25, f"_get_process_tree took {_elapsed:.3f}s"

_orig_ep_file = mcp_bridge.ENDPOINT_FILE
_tmp_ep = os.path.join(out, "stale_endpoint.json")
try:
    with open(_tmp_ep, "w", encoding="utf-8") as _ef:
        json.dump({"port": 12345, "token": "abc", "pid": 99999999}, _ef)
    mcp_bridge.ENDPOINT_FILE = _tmp_ep
    try:
        mcp_bridge.qgis_rpc("list_tools")
        raise AssertionError("Expected RuntimeError on stale endpoint PID")
    except RuntimeError as _exc:
        assert "stale endpoint" in str(_exc).lower(), _exc
    assert not os.path.exists(_tmp_ep), "Expected stale endpoint file to be removed"
finally:
    mcp_bridge.ENDPOINT_FILE = _orig_ep_file

# Verify query_overture_maps aliases & schema normalization + disconnect cancel handling
_captured_sql = []
_orig_duck_exec = tools._execute_duckdb_nonblocking
try:
    import duckdb as _duck_check  # noqa: F401
    def _fake_duck_exec(setup_sql, copy_sql, fetch_scalar=False):
        if fetch_scalar:
            return 5000
        _captured_sql.append(copy_sql)
        raise RuntimeError("Execution stopped by user.")
    tools._execute_duckdb_nonblocking = _fake_duck_exec
    try:
        T("query_overture_maps", {
            "theme": "water",
            "bbox": [110.335, -7.835, 110.405, -7.765],
            "layer_name": "yogyakarta_water",
            "source": "parquet",
        })
    except RuntimeError:
        pass
    try:
        T("query_overture_maps", {
            "theme": "places",
            "bbox": [110.335, -7.835, 110.405, -7.765],
            "filter_sql": "categories.primary = 'museum'",
            "layer_name": "yogyakarta_landmarks",
            "source": "parquet",
        })
    except RuntimeError:
        pass
    assert len(_captured_sql) == 2, _captured_sql
    assert "theme=base/type=water/" in _captured_sql[0], _captured_sql[0]
    assert "taxonomy.primary = 'museum'" in _captured_sql[1], _captured_sql[1]
    assert "categories.primary" not in _captured_sql[1], _captured_sql[1]
except ImportError:
    pass
finally:
    tools._execute_duckdb_nonblocking = _orig_duck_exec

# Verify dock 4-coord bbox formatting & Overture scope badge
_fmt_bbox = dock.format_in_args({"bbox": [110.355, -7.808, 110.376, -7.780], "theme": "transportation"})
assert "110.355" in _fmt_bbox and "-7.78" in _fmt_bbox, f"Truncated bbox display: {_fmt_bbox}"
assert dock.categorize_tool("query_overture_maps", {"theme": "transportation", "bbox": [110.355, -7.808, 110.376, -7.780]}) == ("Overture", "transportation (bbox)")
assert dock.categorize_tool("query_overture_maps", {"theme": "places", "filter_sql": None}) == ("Overture", "places (canvas)")

# Verify live fast PMTiles / STAC-indexed Overture queries for Malioboro (< 10s each)
T("new_project", {"crs": "EPSG:4326"})
_t_roads = _t.monotonic()
_roads_res = T("query_overture_maps", {
    "theme": "transportation",
    "bbox": [110.355, -7.808, 110.376, -7.780],
    "layer_name": "malioboro_roads",
    "limit": 500,
})
_dt_roads = _t.monotonic() - _t_roads
print(f"malioboro_roads fetched {_roads_res['features']} features in {_dt_roads:.2f}s")
assert _roads_res["features"] >= 2000 and _dt_roads < 10.0, (_roads_res, _dt_roads)

_t_bld = _t.monotonic()
_bld_res = T("query_overture_maps", {
    "theme": "buildings",
    "bbox": [110.355, -7.808, 110.376, -7.780],
    "layer_name": "malioboro_buildings",
    "limit": 500,
})
_dt_bld = _t.monotonic() - _t_bld
print(f"malioboro_buildings fetched {_bld_res['features']} features in {_dt_bld:.2f}s")
assert _bld_res["features"] >= 20000 and _dt_bld < 10.0, (_bld_res, _dt_bld)

_t_plc = _t.monotonic()
_plc_res = T("query_overture_maps", {
    "theme": "places",
    "bbox": [110.355, -7.808, 110.376, -7.780],
    "layer_name": "malioboro_places",
})
_dt_plc = _t.monotonic() - _t_plc
print(f"malioboro_places fetched {_plc_res['features']} features in {_dt_plc:.2f}s")
assert _plc_res["features"] >= 1000 and _dt_plc < 10.0, (_plc_res, _dt_plc)

_plc_cap = T("query_overture_maps", {
    "theme": "places",
    "bbox": [110.355, -7.808, 110.376, -7.780],
    "layer_name": "malioboro_places_capped",
    "limit": 150,
    "fetch_all": False,
})
assert _plc_cap["features"] == 150, _plc_cap
T("remove_layer", {"layer": "malioboro_places_capped"})

_t_flt = _t.monotonic()
_flt_res = T("query_overture_maps", {
    "theme": "buildings",
    "bbox": [110.355, -7.808, 110.376, -7.780],
    "filter_sql": "height IS NOT NULL",
    "layer_name": "malioboro_tall",
    "limit": 200,
})
_dt_flt = _t.monotonic() - _t_flt
print(f"malioboro_tall (filter_sql) fetched {_flt_res['features']} features in {_dt_flt:.2f}s")
assert _flt_res["features"] >= 2000 and _dt_flt < 10.0, (_flt_res, _dt_flt)

# Verify DuckDB spatial pagination strips small trailing LIMIT when fetch_all=True & paginates all rows
try:
    import duckdb as _duck_pag  # noqa: F401
    _src_pt_gpkg = os.path.join(out, "pag_src.gpkg")
    from osgeo import ogr as _ogr_pag, osr as _osr_pag
    _p_drv = _ogr_pag.GetDriverByName("GPKG")
    _p_ds = _p_drv.CreateDataSource(_src_pt_gpkg)
    _p_srs = _osr_pag.SpatialReference(); _p_srs.ImportFromEPSG(4326)
    _p_lyr = _p_ds.CreateLayer("pts", _p_srs, _ogr_pag.wkbPoint)
    _p_lyr.CreateField(_ogr_pag.FieldDefn("pid", _ogr_pag.OFTInteger))
    for _pi in range(8):
        _pf = _ogr_pag.Feature(_p_lyr.GetLayerDefn())
        _pf.SetField("pid", _pi)
        _pg = _ogr_pag.Geometry(_ogr_pag.wkbPoint)
        _pg.AddPoint(110.36 + _pi * 0.001, -7.79)
        _pf.SetGeometry(_pg)
        _p_lyr.CreateFeature(_pf)
    _p_ds = None
    _src_sql = _src_pt_gpkg.replace("\\", "/").replace("'", "''")
    _duck_pag_res = T("query_duckdb_spatial", {
        "query": f"SELECT pid, geom AS geometry FROM st_read('{_src_sql}') ORDER BY pid LIMIT 3",
        "layer_name": "duckdb_paginated_pts",
        "page_size": 3,
        "fetch_all": True,
    })
    assert _duck_pag_res["features"] == 8, f"Expected all 8 paginated DuckDB features, got {_duck_pag_res}"
    T("remove_layer", {"layer": "duckdb_paginated_pts"})
except ImportError:
    pass

# Verify add_vector_layer pagination across ArcGIS REST & OGC WFS remote endpoints
_orig_fetch_bytes = tools._fetch_http_bytes
try:
    _seen_urls = []
    def _mock_remote_vector_bytes(url, headers=None, timeout=12):
        _seen_urls.append(url)
        if "FeatureServer" in url:
            if "resultOffset=0" in url:
                return json.dumps({
                    "type": "FeatureCollection",
                    "properties": {"exceededTransferLimit": True},
                    "features": [
                        {"type": "Feature", "properties": {"id": i}, "geometry": {"type": "Point", "coordinates": [110.36 + i * 0.001, -7.79]}}
                        for i in range(50)
                    ],
                }).encode("utf-8")
            return json.dumps({
                "type": "FeatureCollection",
                "properties": {"exceededTransferLimit": False},
                "features": [
                    {"type": "Feature", "properties": {"id": 50 + i}, "geometry": {"type": "Point", "coordinates": [110.36 + i * 0.001, -7.80]}}
                    for i in range(15)
                ],
            }).encode("utf-8")
        raise ValueError(f"Unexpected test URL: {url}")

    tools._fetch_http_bytes = _mock_remote_vector_bytes
    _arc_res = T("add_vector_layer", {
        "path": "https://example.org/arcgis/rest/services/POIs/FeatureServer/0/query",
        "name": "mock_arcgis_paginated",
        "bbox": [110.35, -7.81, 110.38, -7.78],
        "paginate": True,
        "page_size": 100,
    })
    assert _arc_res["features"] == 65 and any("resultOffset=50" in u for u in _seen_urls), (_arc_res, _seen_urls)
    T("remove_layer", {"layer": "mock_arcgis_paginated"})
finally:
    tools._fetch_http_bytes = _orig_fetch_bytes

# Cartographic hierarchy, line-merged/thinned labeling, and live KPI layout checks
_cat_res = T("style_categorized", {"layer": "malioboro_roads", "field": "class"})
assert _cat_res.get("hierarchy") == "cased_roads", _cat_res
assert tools._layer("malioboro_roads").renderer().usingSymbolLevels() is True

T("set_labels", {"layer": "malioboro_roads", "field": "name", "font_size": 7.5})
T("set_labels", {"layer": "malioboro_places", "field": "name", "font_size": 7.0})
_r_lbl_cfg = tools._layer("malioboro_roads").labeling().settings()
_p_lbl_cfg = tools._layer("malioboro_places").labeling().settings()
assert getattr(_r_lbl_cfg, "mergeLines", False) or (
    hasattr(_r_lbl_cfg, "placementSettings") and _r_lbl_cfg.placementSettings().mergeConnectedLines()
)
assert _p_lbl_cfg.thinningSettings().limitNumberOfLabelsEnabled() is True

T("create_styled_layout", {
    "name": "Malioboro_Bento_Test",
    "style_theme": "bento_dashboard",
    "title": "Yogyakarta · Malioboro Corridor",
    "subtitle": "Urban Fabric & Street Network",
    "credits": "Overture Maps",
})
from qgis.core import QgsLayoutItemLabel, QgsLayoutItemLegend
_lay_test = QgsProject.instance().layoutManager().layoutByName("Malioboro_Bento_Test")
assert _lay_test is not None
_lbl_texts = [it.text() for it in _lay_test.items() if isinstance(it, QgsLayoutItemLabel)]
assert all(("OPTIMAL" not in t and "100%" not in t) for t in _lbl_texts), _lbl_texts
assert any(("BUILDINGS MAPPED" in t or f"{_bld_res['features']:,}" in t) for t in _lbl_texts), _lbl_texts
_leg_items = [it for it in _lay_test.items() if isinstance(it, QgsLayoutItemLegend)]
assert _leg_items
_leg_titles = [ch.customProperty("legend/title-label") for ch in _leg_items[0].model().rootGroup().findLayers()]
assert _leg_titles and all(t and "_" not in str(t) for t in _leg_titles), _leg_titles

# Verify antique_historical & poster_minimalist layout framing (cover=True edge-to-edge fit) & complete elements
from qgis.core import QgsLayoutItemMap, QgsLayoutItemPicture, QgsLayoutItemScaleBar, QgsLayoutItemShape
_ant_res = T("create_styled_layout", {
    "name": "Malioboro_Antique_Test",
    "style_theme": "antique_historical",
    "orientation": "portrait",
    "title": "PLAN OF YOGYAKARTA",
    "subtitle": "Historic Axis: Tugu — Malioboro — Kraton",
    "credits": "Overture Maps",
    "legend": True,
    "north_arrow": True,
    "scalebar": True,
})
_ant_lay = QgsProject.instance().layoutManager().layoutByName("Malioboro_Antique_Test")
assert _ant_lay is not None
_ant_maps = [it for it in _ant_lay.items() if isinstance(it, QgsLayoutItemMap)]
_ant_shapes = [it for it in _ant_lay.items() if isinstance(it, QgsLayoutItemShape)]
_ant_legends = [it for it in _ant_lay.items() if isinstance(it, QgsLayoutItemLegend)]
_ant_pics = [it for it in _ant_lay.items() if isinstance(it, QgsLayoutItemPicture)]
_ant_sbs = [it for it in _ant_lay.items() if isinstance(it, QgsLayoutItemScaleBar)]
_ant_lbls = [it.text() for it in _ant_lay.items() if isinstance(it, QgsLayoutItemLabel)]
assert _ant_maps and _ant_legends and _ant_pics and _ant_sbs, (len(_ant_maps), len(_ant_legends), len(_ant_pics), len(_ant_sbs))
assert any("PLAN OF YOGYAKARTA" in t and "Overture Maps" in t for t in _ant_lbls), _ant_lbls
# Verify parchment background shape (#f4ecd8) exists
assert any(it.symbol() and it.symbol().color().name().lower() == "#f4ecd8" for it in _ant_shapes), "Missing #f4ecd8 parchment background"
# Verify edge-to-edge cover-fit aspect ratio matches map frame rect
_mp = _ant_maps[0]
_ext_ar = _mp.extent().width() / max(_mp.extent().height(), 1e-9)
_frm_ar = _mp.rect().width() / max(_mp.rect().height(), 1e-9)
assert abs(_ext_ar - _frm_ar) / _frm_ar < 0.05, f"Map extent aspect {_ext_ar:.3f} != frame aspect {_frm_ar:.3f}"

_post_res = T("create_styled_layout", {
    "name": "Malioboro_Poster_Test",
    "style_theme": "poster_minimalist",
    "orientation": "portrait",
    "title": "Y O G Y A K A R T A",
    "subtitle": "MALIOBORO CORRIDOR",
    "credits": "Overture Maps",
})
_post_lay = QgsProject.instance().layoutManager().layoutByName("Malioboro_Poster_Test")
assert _post_lay is not None
_post_mp = [it for it in _post_lay.items() if isinstance(it, QgsLayoutItemMap)][0]
_p_ext_ar = _post_mp.extent().width() / max(_post_mp.extent().height(), 1e-9)
_p_frm_ar = _post_mp.rect().width() / max(_post_mp.rect().height(), 1e-9)
assert abs(_p_ext_ar - _p_frm_ar) / _p_frm_ar < 0.05, f"Poster extent aspect {_p_ext_ar:.3f} != frame aspect {_p_frm_ar:.3f}"

# Verify snapshot_layout returns universal QA diagnostics across themes
for _lay_nm in ("Malioboro_Bento_Test", "Malioboro_Antique_Test", "Malioboro_Poster_Test"):
    _snap_qa = T("snapshot_layout", {"name": _lay_nm, "dpi": 72})
    assert "qa" in _snap_qa and _snap_qa["qa"]["map_area_pct"] >= 45.0, (_lay_nm, _snap_qa.get("qa"))
    assert not _snap_qa["qa"]["out_of_bounds_items"], (_lay_nm, _snap_qa["qa"])
    assert not _snap_qa["qa"]["overlapping_items"], (_lay_nm, _snap_qa["qa"])

# Verify update_layer(name=...) alias, style_categorized(column=...) alias, and outlier-extent exclusion
_tmp_out = T("create_vector_layer", {"layer_name": "giant_outlier_river", "geometry_type": "Polygon", "fields": [{"name": "tag", "type": "string"}]})
T("digitize_features", {"layer": "giant_outlier_river", "features": [{"geometry": [[110.20, -7.95], [110.50, -7.95], [110.50, -7.65], [110.20, -7.65], [110.20, -7.95]], "attributes": {"tag": "river"}}]})
_upd_nm = T("update_layer", {"layer": "giant_outlier_river", "name": "Giant_River_Renamed"})
assert _upd_nm["name"] == "Giant_River_Renamed", _upd_nm
_col_cat = T("style_categorized", {"layer": "Giant_River_Renamed", "column": "tag"})
assert _col_cat["categories"] >= 1, _col_cat
T("create_styled_layout", {"name": "Outlier_Framing_Test", "style_theme": "antique_historical", "orientation": "portrait"})
_out_mp = [it for it in QgsProject.instance().layoutManager().layoutByName("Outlier_Framing_Test").items() if isinstance(it, QgsLayoutItemMap)][0]
assert _out_mp.extent().width() < 0.08, f"Giant outlier layer zoomed out map extent: {_out_mp.extent()}"
# Verify explicit bbox locks layout directly to the requested bbox even with giant outlier layer present
T("create_styled_layout", {
    "name": "Explicit_BBox_Lock_Test",
    "style_theme": "poster_minimalist",
    "orientation": "portrait",
    "bbox": [110.358, -7.806, 110.373, -7.783],
})
_bbox_mp = [it for it in QgsProject.instance().layoutManager().layoutByName("Explicit_BBox_Lock_Test").items() if isinstance(it, QgsLayoutItemMap)][0]
assert abs(_bbox_mp.extent().width() - 0.015) < 0.004, f"Expected explicit bbox lock width ~0.015, got {_bbox_mp.extent().width()}"
T("remove_layer", {"layer": "Giant_River_Renamed"})

from qgis_ai_agent import runner as _runner
assert any("Agent Orchestration" in lbl and val == "gemini-pro-orchestrator-g1-pro"
           for lbl, val in _runner.DEFAULT_ADAPTER_MODELS["agy (Antigravity CLI)"]), "Missing Agent Orchestration in DEFAULT_ADAPTER_MODELS"

# Protocol 0 vague prompt gating
from qgis_ai_agent import prompts as _prompts
assert skills.REGISTRY.is_vague_request("make city map yogyakarta around malioboro") is True
assert skills.REGISTRY.is_vague_request("make city map of center yogyakarta around malioboro and tugu") is True
assert "DO NOT EXECUTE MAP TOOLS YET" in _prompts.build("", "", "", "make city map yogyakarta around malioboro", False, is_vague=True)
assert "DO NOT EXECUTE MAP TOOLS YET" not in _prompts.build("", "", "", "Tema: Street Network; Gaya Layout: Bento Dashboard", False, is_vague=False)

_leaked_vsi = [x for x in (_gdal.ReadDir("/vsimem/") or []) if x.startswith("ov_pmt_")]
assert not _leaked_vsi, f"Leaked /vsimem/ entries: {_leaked_vsi}"

srv3 = ToolServer()
srv3._busy = True
srv3._active_sock = "fake_sock"
srv3.buffers["fake_sock"] = b"partial"
tools.CANCEL_REQUESTED = False
srv3._on_disconnect("fake_sock")
assert tools.CANCEL_REQUESTED is True and srv3._disconnect_cancelled is True
tools.CANCEL_REQUESTED = False
srv3._busy = False
srv3._active_sock = None

print("tools count check:", len(tools.TOOLS))
assert len(tools.TOOLS) == 70, f"Expected 70 tools, got {len(tools.TOOLS)}"

# Clean up test temp dir cleanly
QgsProject.instance().clear()
skills._rmtree_safe(out, ignore_errors=True)

print("All smoke test assertions PASSED!")
