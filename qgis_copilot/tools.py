"""QGIS operations exposed to the AI agent as MCP tools.

Every tool is a function taking keyword args and returning a JSON-serialisable
value. All tools run on the QGIS main thread (called from the TCP server slot).
Coverage follows the REA interaction map: project/CRS, layers, symbology,
labels, processing, expressions, print layout, export, canvas snapshot.
"""
import base64
import bisect
from concurrent.futures import ThreadPoolExecutor
import contextlib
import gc
import glob
import gzip
import html
import io
import json
import logging
import math
import os
import uuid
import random
import re
import struct
import tempfile
import threading
import time
import traceback
import urllib.parse
import urllib.request
from . import paths

from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QSize, QBuffer, QIODevice, Qt
from qgis.PyQt.QtGui import QColor, QFont, QImage
from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsRasterLayer, QgsCoordinateReferenceSystem,
    QgsRectangle, QgsCoordinateTransform, QgsMapSettings, QgsMapRendererParallelJob,
    QgsSymbol, QgsSingleSymbolRenderer, QgsCategorizedSymbolRenderer, QgsRendererCategory,
    QgsGraduatedSymbolRenderer, QgsClassificationQuantile, QgsClassificationJenks,
    QgsClassificationEqualInterval, QgsClassificationPrettyBreaks, QgsClassificationStandardDeviation,
    QgsStyle, QgsPalLayerSettings, QgsTextFormat, QgsTextBufferSettings,
    QgsVectorLayerSimpleLabeling, QgsExpression, QgsExpressionContext,
    QgsExpressionContextUtils, QgsFeatureRequest, QgsField, QgsApplication,
    QgsPrintLayout, QgsLayoutItemMap, QgsLayoutItemLabel, QgsLayoutItemLegend,
    QgsLayoutItemScaleBar, QgsLayoutItemPicture, QgsLayoutPoint, QgsLayoutSize,
    QgsLayoutMeasurement, QgsUnitTypes, QgsLayoutItemPage, QgsLayoutExporter,
    QgsSingleBandPseudoColorRenderer, QgsColorRampShader, QgsRasterShader, QgsRasterBandStats, QgsWkbTypes,
    QgsGeometry, QgsPointXY, QgsFeature, QgsMessageLog, QgsProcessingFeedback,
)
from qgis.PyQt.QtCore import QMetaType

# QGIS API compatibility monkey-patches for renderer symbol setters
if hasattr(QgsCategorizedSymbolRenderer, "setSourceSymbol") and not hasattr(QgsCategorizedSymbolRenderer, "setSymbol"):
    QgsCategorizedSymbolRenderer.setSymbol = lambda self, sym: self.setSourceSymbol(sym)

if hasattr(QgsGraduatedSymbolRenderer, "setSourceSymbol") and not hasattr(QgsGraduatedSymbolRenderer, "setSymbol"):
    QgsGraduatedSymbolRenderer.setSymbol = lambda self, sym: self.setSourceSymbol(sym)

_PY_LOGGER = logging.getLogger("qgis_ai_agent")


class _PluginLogger:
    """Thread-safe logger writing to both Python logging and QGIS Message Log."""

    def _log(self, msg, level_py, level_qgis):
        text = str(msg)
        _PY_LOGGER.log(level_py, text)
        try:
            QgsMessageLog.logMessage(text, "AI Agent", level_qgis)
        except Exception:  # nosec B110
            pass

    def info(self, msg):
        self._log(msg, logging.INFO, Qgis.MessageLevel.Info if hasattr(Qgis, "MessageLevel") else 0)

    def warning(self, msg):
        self._log(msg, logging.WARNING, Qgis.MessageLevel.Warning if hasattr(Qgis, "MessageLevel") else 1)

    def error(self, msg):
        self._log(msg, logging.ERROR, Qgis.MessageLevel.Critical if hasattr(Qgis, "MessageLevel") else 2)


LOGGER = _PluginLogger()
TOOLS = {}
ALLOW_PYTHON = {"value": True}
CANCEL_REQUESTED = False
_PROGRESS_CALLBACK = None


def _report_progress(tool_name, pct, msg=""):
    cb = _PROGRESS_CALLBACK
    if cb is not None:
        try:
            clamped = max(0, min(100, int(pct)))
            cb(tool_name, clamped, msg)
        except Exception:  # nosec B110
            pass


def tool(description, schema=None):
    def deco(fn):
        TOOLS[fn.__name__] = {
            "fn": fn,
            "description": description,
            "inputSchema": {"type": "object", "properties": schema or {}, "additionalProperties": True},
        }
        return fn
    return deco


def S(t, d, **kw):
    return dict(type=t, description=d, **kw)


# ---------------------------------------------------------------- helpers
def _iface():
    from qgis.utils import iface
    return iface


def _layer(ref):
    if not ref or not str(ref).strip():
        raise ValueError("Layer reference is empty. Call get_project_state for available layers.")
    key = str(ref).strip()
    p = QgsProject.instance()
    lyr = p.mapLayer(key)
    if lyr:
        return lyr
    found = p.mapLayersByName(key)
    if found:
        return found[0]
    key_low = key.lower()
    all_lyrs = list(p.mapLayers().values())
    ci_matches = [l for l in all_lyrs if l.name().strip().lower() == key_low]
    if ci_matches:
        return ci_matches[0]
    sub_matches = [l for l in all_lyrs if key_low in l.name().strip().lower()]
    if len(sub_matches) == 1:
        return sub_matches[0]
    avail = [l.name() for l in all_lyrs]
    raise ValueError(f"Layer not found: {ref!r}. Available layers: {avail}")


def _ramp(name):
    ramp = QgsStyle.defaultStyle().colorRamp(name or "Viridis")
    if ramp is None:
        raise ValueError(f"Unknown color ramp {name!r}. Use list_color_ramps.")
    return ramp


def _layer_info(lyr):
    d = {"id": lyr.id(), "name": lyr.name(), "crs": lyr.crs().authid(),
         "visible": QgsProject.instance().layerTreeRoot().findLayer(lyr.id()).isVisible()
         if QgsProject.instance().layerTreeRoot().findLayer(lyr.id()) else None,
         "source": lyr.publicSource()}
    if isinstance(lyr, QgsVectorLayer):
        d.update(type="vector", geometry=QgsWkbTypes.displayString(lyr.wkbType()),
                 features=lyr.featureCount(),
                 fields=[{"name": f.name(), "type": f.typeName()} for f in lyr.fields()],
                 renderer=lyr.renderer().type() if lyr.renderer() else None,
                 labels=lyr.labelsEnabled())
    elif isinstance(lyr, QgsRasterLayer):
        d.update(type="raster", bands=lyr.bandCount(), width=lyr.width(), height=lyr.height())
    else:
        d["type"] = "other"
    return d


_HEADLESS_CANVAS = None


def _canvas():
    iface = _iface()
    if iface and hasattr(iface, "mapCanvas"):
        return iface.mapCanvas()
    global _HEADLESS_CANVAS
    if _HEADLESS_CANVAS is None:
        try:
            from qgis.gui import QgsMapCanvas
            _HEADLESS_CANVAS = QgsMapCanvas()
        except Exception:  # nosec B110
            pass
    return _HEADLESS_CANVAS


def _is_valid_extent(e):
    if e is None:
        return False
    try:
        if e.isNull() or e.isEmpty():
            return False
        import math
        vals = [e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()]
        for v in vals:
            if math.isnan(v) or math.isinf(v) or abs(v) > 1e16:
                return False
        if e.xMinimum() >= e.xMaximum() or e.yMinimum() >= e.yMaximum():
            return False
        return True
    except Exception:
        return False


def _safe_transform_extent(ext, src_crs, dst_crs, project=None):
    if not _is_valid_extent(ext):
        return None
    if not src_crs or not dst_crs or not src_crs.isValid() or not dst_crs.isValid() or src_crs == dst_crs:
        return QgsRectangle(ext)
    p = project or QgsProject.instance()
    try:
        tr = QgsCoordinateTransform(src_crs, dst_crs, p)
        res = tr.transformBoundingBox(ext)
        if _is_valid_extent(res):
            return res
    except Exception:  # nosec B110
        pass
    try:
        if not dst_crs.isGeographic():
            b = dst_crs.bounds()
            if _is_valid_extent(b):
                tr_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), dst_crs, p)
                clamped_b = tr_wgs.transformBoundingBox(b)
                if _is_valid_extent(clamped_b):
                    return clamped_b
    except Exception:  # nosec B110
        pass
    return None


def _sanitize_canvas_extent(target_crs=None, fallback_from_crs=None):
    c = _canvas()
    if not c or not hasattr(c, "extent") or not hasattr(c, "setExtent"):
        return
    p = QgsProject.instance()
    dst_crs = target_crs or p.crs()
    if not dst_crs or not dst_crs.isValid():
        return

    cur_ext = c.extent()
    if _is_valid_extent(cur_ext):
        if fallback_from_crs and fallback_from_crs.isValid() and fallback_from_crs != dst_crs:
            safe_trans = _safe_transform_extent(cur_ext, fallback_from_crs, dst_crs, p)
            if safe_trans:
                c.setExtent(safe_trans)
                return
        else:
            return

    # If canvas extent is corrupt, empty, null, or out-of-bounds in dst_crs:
    valid_extent = None
    for lyr in p.mapLayers().values():
        if not lyr.isValid() or lyr.providerType() == "wms":
            continue
        lyr_ext = lyr.extent()
        if _is_valid_extent(lyr_ext):
            trans_ext = _safe_transform_extent(lyr_ext, lyr.crs(), dst_crs, p)
            if trans_ext:
                if valid_extent is None:
                    valid_extent = QgsRectangle(trans_ext)
                else:
                    valid_extent.combineExtentWith(trans_ext)

    if valid_extent and _is_valid_extent(valid_extent):
        valid_extent.scale(1.05)
        c.setExtent(valid_extent)
        return

    if not dst_crs.isGeographic():
        b = dst_crs.bounds()
        if _is_valid_extent(b):
            safe_b = _safe_transform_extent(b, QgsCoordinateReferenceSystem("EPSG:4326"), dst_crs, p)
            if safe_b:
                c.setExtent(safe_b)
                return

    if dst_crs.authid() in ("EPSG:4326", "OGC:CRS84"):
        c.setExtent(QgsRectangle(-180, -85, 180, 85))
    elif dst_crs.authid() == "EPSG:3857":
        c.setExtent(QgsRectangle(-20037508, -20037508, 20037508, 20037508))
    else:
        c.setExtent(QgsRectangle(-50000, -50000, 50000, 50000))


def _refresh():
    _sanitize_canvas_extent()
    c = _canvas()
    if c:
        c.refreshAllLayers()


def _project_scratch_file(prefix="scratch", ext=".gpkg"):
    """Return a unique persistent file path inside the active project's scratch/ directory."""
    pfile = QgsProject.instance().fileName() or None
    sdir = paths.get_scratch_dir(pfile)
    os.makedirs(sdir, exist_ok=True)
    clean = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(prefix or "scratch")).strip("_") or "scratch"
    return os.path.join(sdir, f"{clean}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}{ext}")


# ---------------------------------------------------------------- project
@tool("Snapshot of the whole session: project CRS, layers (fields, geometry, renderer), canvas extent, layouts. Call this first and after every change.")
def get_project_state():
    p = QgsProject.instance()
    order = [n.layerId() for n in p.layerTreeRoot().findLayers()]
    c = _canvas()
    ext = c.extent() if c else None
    pfile = p.fileName() or ""
    pdir = paths.resolve_project_dir(pfile or None)
    state = {
        "project_file": pfile,
        "project_dir": pdir,
        "scratch_dir": paths.get_scratch_dir(pfile or None),
        "ai_dir": paths.get_ai_project_dir(pfile or None),
        "crs": p.crs().authid(),
        "layers_top_to_bottom": [_layer_info(p.mapLayer(i)) for i in order if p.mapLayer(i)],
        "canvas_extent": [round(ext.xMinimum(), 4), round(ext.yMinimum(), 4), round(ext.xMaximum(), 4), round(ext.yMaximum(), 4)] if _is_valid_extent(ext) else None,
        "canvas_scale": c.scale() if c else 1.0,
        "layouts": [l.name() for l in p.layoutManager().layouts()],
        "qgis_version": Qgis.version(),
    }
    return state


def _is_deleted(obj):
    if obj is None:
        return True
    try:
        return bool(sip.isdeleted(obj))
    except Exception:
        return False


@contextlib.contextmanager
def _canvas_freeze_guard():
    """Stop active parallel rendering and freeze the canvas across destructive project reloads."""
    c = _canvas()
    if c and not _is_deleted(c):
        try:
            c.stopRendering()
            c.freeze(True)
        except Exception:  # nosec B110
            pass
    try:
        yield
    finally:
        if c and not _is_deleted(c):
            try:
                c.freeze(False)
                c.refreshAllLayers()
            except Exception:  # nosec B110
                pass
        gc.collect()


_ACTIVE_STUDY_BBOX = None


def _record_study_bbox(bbox, crs_authid="EPSG:4326"):
    """Remember the most recent explicit study-area bbox [xmin, ymin, xmax, ymax] for full-bbox layout framing."""
    global _ACTIVE_STUDY_BBOX
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        try:
            vals = [float(v) for v in bbox]
            if vals[2] > vals[0] and vals[3] > vals[1]:
                _ACTIVE_STUDY_BBOX = (vals, str(crs_authid or "EPSG:4326"))
        except Exception:  # nosec B110
            pass


def _study_bbox_rect(p, explicit_bbox=None):
    """Return QgsRectangle in project CRS for an explicit bbox or the active session's recorded study bbox."""
    src = None
    if isinstance(explicit_bbox, (list, tuple)) and len(explicit_bbox) == 4:
        try:
            vals = [float(v) for v in explicit_bbox]
            if vals[2] > vals[0] and vals[3] > vals[1]:
                src = (vals, "EPSG:4326")
        except Exception:
            src = None
    elif _ACTIVE_STUDY_BBOX is not None:
        src = _ACTIVE_STUDY_BBOX
    if not src:
        return None
    vals, src_authid = src
    rect = QgsRectangle(*vals)
    src_crs = QgsCoordinateReferenceSystem(src_authid)
    if src_crs.isValid() and p.crs().isValid() and src_crs != p.crs():
        tr = _safe_transform_extent(rect, src_crs, p.crs(), p)
        if tr and _is_valid_extent(tr):
            return tr
    return rect if _is_valid_extent(rect) else None


@tool("Clear the project and start fresh inside a new dedicated project directory under the configured output directory (with scratch/, ai/, and autosave .qgz).",
      {"crs": S("string", "e.g. EPSG:4326 or EPSG:32748"),
       "name": S("string", "optional project folder/file name"),
       "output_dir": S("string", "optional parent output directory; defaults to configured Output Folder")})
def new_project(crs="EPSG:4326", name=None, output_dir=None):
    global _ACTIVE_STUDY_BBOX
    _ACTIVE_STUDY_BBOX = None

    base_out = os.path.normpath(os.path.abspath(os.path.expanduser(output_dir))) if output_dir else paths.get_configured_output_dir()
    os.makedirs(base_out, exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    if name:
        clean_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in str(name)).strip("_") or "project"
        folder_name = f"{clean_name}_{ts}" if os.path.exists(os.path.join(base_out, clean_name)) else clean_name
    else:
        clean_name = f"project_{ts}"
        folder_name = f"{clean_name}_{uuid.uuid4().hex[:4]}"

    proj_dir = os.path.join(base_out, folder_name)
    os.makedirs(os.path.join(proj_dir, "scratch"), exist_ok=True)
    os.makedirs(os.path.join(proj_dir, "ai"), exist_ok=True)
    paths.set_active_project_dir(proj_dir)

    p = QgsProject.instance()
    with _canvas_freeze_guard():
        p.clear()
        c = QgsCoordinateReferenceSystem(crs)
        if c.isValid():
            p.setCrs(c)
        if name:
            p.setTitle(clean_name)
        p.setPresetHomePath(proj_dir)
        qgz_path = os.path.join(proj_dir, f"{folder_name}.qgz")
        p.write(qgz_path)
        _sanitize_canvas_extent(target_crs=c)
    return get_project_state()


@tool("Set project CRS.", {"crs": S("string", "authid")})
def set_project_crs(crs):
    c = QgsCoordinateReferenceSystem(crs)
    if not c.isValid():
        raise ValueError(f"Invalid CRS {crs}")
    p = QgsProject.instance()
    old_crs = p.crs()
    p.setCrs(c)
    _sanitize_canvas_extent(target_crs=c, fallback_from_crs=old_crs)
    _refresh()
    return {"crs": c.authid()}


@tool("Save project to .qgz/.qgs. If path is omitted, saves to the current file or auto-saves inside the active project directory.",
      {"path": S("string", "optional path; defaults to current project file or active project directory")})
def save_project(path=None):
    p = QgsProject.instance()
    if not path:
        path = p.fileName()
    if not path:
        out_dir = paths.get_active_project_dir() or paths.get_configured_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        title = p.title() or "QGIS_Project"
        clean_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in title).strip("_") or "QGIS_Project"
        path = os.path.join(out_dir, f"{clean_title}.qgz")
    elif not os.path.isabs(path):
        base_dir = paths.resolve_project_dir(p.fileName() or None) or paths.get_configured_output_dir()
        path = os.path.join(base_dir, path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    ok = p.write(path)
    paths.set_active_project_dir(os.path.dirname(os.path.abspath(path)))
    try:
        from .sessions import record_autosave_info
        record_autosave_info(path)
    except Exception:  # nosec B110
        pass
    return {"saved": ok, "path": path}


@tool("Open an existing project file (.qgz/.qgs). STRICT RULE: Only call this tool if the user explicitly requested to open this specific project file. Never open unrelated projects autonomously.", {"path": S("string", "absolute path to project file")})
def open_project(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Project file not found: {path}")
    with _canvas_freeze_guard():
        QgsProject.instance().read(path)
    paths.set_active_project_dir(os.path.dirname(os.path.abspath(path)))
    return get_project_state()


# ---------------------------------------------------------------- layers
def _gpkg_first_layer_name(gpkg_path):
    """Return the first OGR layer name inside a GeoPackage, or None."""
    from osgeo import ogr

    ds = ogr.Open(gpkg_path)
    if ds is None:
        return None
    try:
        lyr = ds.GetLayer(0)
        return lyr.GetName() if lyr is not None else None
    finally:
        ds = None


def _is_remote_vector_candidate(path, provider="ogr"):
    """Return True if path/provider represents an HTTP/REST/WFS vector endpoint eligible for iterative fetching."""
    if not isinstance(path, str) or not path.strip():
        return False
    low_path = path.strip().lower()
    low_prov = (provider or "ogr").strip().lower()
    if low_prov == "wfs":
        return True
    if not low_path.startswith(("http://", "https://")):
        return False
    # ArcGIS FeatureServer / MapServer REST query URLs
    if re.search(r"/(?:featureserver|mapserver)/\d+(?:/query)?/?(?:\?|$)", low_path):
        return True
    # OGC WFS HTTP URLs
    if "service=wfs" in low_path or "request=getfeature" in low_path or re.search(r"/wfs(?:/|\?|$)", low_path):
        return True
    # BBox-templated REST endpoints
    if any(tok in low_path for tok in ("{xmin}", "{ymin}", "{xmax}", "{ymax}", "{bbox}")):
        return True
    # Explicit offset/page API endpoints (CKAN/Socrata/GBIF/GeoJSON APIs)
    if any(tok in low_path for tok in ("resultoffset=", "startindex=", "offset=", "page=")):
        return True
    return False


def _append_remote_bytes_to_gpkg(raw_bytes, tag, page_idx, out_file, target_layer_name="features"):
    """Stage downloaded vector payload in a temp file and write/append into out_file GeoPackage. Returns (written_count, target_layer_name)."""
    from osgeo import gdal, ogr

    ext = ".geojson" if raw_bytes.lstrip()[:1] in (b"{", b"[") else ".xml"
    tmp_src = os.path.join(tempfile.gettempdir(), f"rem_vec_{tag}_{threading.get_ident()}_{page_idx}{ext}")
    ds = None
    try:
        with open(tmp_src, "wb") as fh:
            fh.write(raw_bytes)
        ds = ogr.Open(tmp_src)
        if ds is None or ds.GetLayerCount() == 0:
            return 0, target_layer_name
        lyr = ds.GetLayer(0)
        cnt = int(lyr.GetFeatureCount()) if lyr is not None else 0
        ds = None
        if cnt <= 0:
            return 0, target_layer_name
        exists = os.path.exists(out_file)
        t_name = _gpkg_first_layer_name(out_file) if exists else target_layer_name
        t_name = t_name or target_layer_name
        opts_kw = {
            "format": "GPKG",
            "layerName": t_name,
            "dstSRS": "EPSG:4326",
            "reproject": True,
        }
        if exists:
            opts_kw["accessMode"] = "append"
        res = gdal.VectorTranslate(out_file, tmp_src, options=gdal.VectorTranslateOptions(**opts_kw))
        if res is not None:
            res = None
        return cnt, t_name
    finally:
        ds = None
        try:
            if os.path.exists(tmp_src):
                os.remove(tmp_src)
        except Exception:  # nosec B110
            pass


def _parse_qgis_wfs_uri(path):
    """Extract base URL and TYPENAME from either a QGIS WFS URI string or an HTTP WFS URL."""
    s = path.strip()
    if s.lower().startswith(("http://", "https://")):
        return s, None
    url_m = re.search(r"url=['\"]([^'\"]+)['\"]", s, flags=re.IGNORECASE)
    tn_m = re.search(r"typename=['\"]([^'\"]+)['\"]", s, flags=re.IGNORECASE)
    return (url_m.group(1) if url_m else None), (tn_m.group(1) if tn_m else None)


def _fetch_paginated_remote_vector_to_gpkg(path, provider="ogr", bbox=None, page_size=1000):
    """Iteratively paginate or spatially tile a remote ArcGIS REST, WFS, or GeoJSON API into an indexed GPKG."""
    if not _is_remote_vector_candidate(path, provider):
        return None

    batch_sz = max(50, min(int(page_size or 1000), 10000))
    max_total = 250000
    _cleanup_old_temp_files()
    out_file = _project_scratch_file("remote_vec", ".gpkg")
    valid_bbox = isinstance(bbox, (list, tuple)) and len(bbox) == 4
    total_cnt = 0
    layer_tbl = "features"
    tag = f"{int(time.time() * 1000)}"

    try:
        raw_s = path.strip()
        low_s = raw_s.lower()
        low_prov = (provider or "ogr").strip().lower()

        # 1. ArcGIS REST FeatureServer / MapServer pagination via resultOffset & resultRecordCount
        if re.search(r"/(?:featureserver|mapserver)/\d+(?:/query)?/?(?:\?|$)", low_s):
            parts = urllib.parse.urlsplit(raw_s)
            q_path = parts.path.rstrip("/")
            if not q_path.lower().endswith("/query"):
                q_path = q_path + "/query"
            base_params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
            lower_keys = {k.lower(): k for k in base_params}
            if "where" not in lower_keys:
                base_params["where"] = "1=1"
            if "outfields" not in lower_keys:
                base_params["outFields"] = "*"
            base_params[lower_keys.get("returngeometry", "returnGeometry")] = "true"
            base_params[lower_keys.get("outsr", "outSR")] = "4326"
            base_params[lower_keys.get("f", "f")] = "geojson"
            if valid_bbox:
                xmin, ymin, xmax, ymax = [float(v) for v in bbox]
                base_params["geometry"] = f"{xmin},{ymin},{xmax},{ymax}"
                base_params["geometryType"] = "esriGeometryEnvelope"
                base_params["inSR"] = "4326"
                base_params["spatialRel"] = "esriSpatialRelIntersects"

            offset = 0
            page_idx = 0
            while total_cnt < max_total:
                if CANCEL_REQUESTED:
                    raise RuntimeError("Execution stopped by user.")
                page_idx += 1
                params = dict(base_params)
                params["resultOffset"] = str(offset)
                params["resultRecordCount"] = str(batch_sz)
                page_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, q_path, urllib.parse.urlencode(params), ""))
                raw_bytes = _fetch_http_bytes(page_url, timeout=25)
                payload = json.loads(raw_bytes.decode("utf-8"))
                if isinstance(payload, dict) and payload.get("error"):
                    raise RuntimeError(f"ArcGIS REST error: {payload['error']}")
                feats = payload.get("features") if isinstance(payload, dict) else []
                if not feats:
                    break
                added, layer_tbl = _append_remote_bytes_to_gpkg(raw_bytes, tag, page_idx, out_file, layer_tbl)
                if added <= 0:
                    break
                total_cnt += added
                offset += added
                _report_progress("add_vector_layer", min(94, 15 + page_idx * 10), f"ArcGIS REST page {page_idx} ({total_cnt:,} features)...")
                exceeded = payload.get("exceededTransferLimit")
                if exceeded is None and isinstance(payload.get("properties"), dict):
                    exceeded = payload["properties"].get("exceededTransferLimit")
                if exceeded is False or (exceeded is None and added < batch_sz):
                    break
            return out_file if total_cnt > 0 and os.path.exists(out_file) else None

        # 2. OGC WFS pagination via STARTINDEX & COUNT
        wfs_url, wfs_typename = _parse_qgis_wfs_uri(raw_s)
        if wfs_url and (low_prov == "wfs" or "service=wfs" in low_s or "request=getfeature" in low_s or re.search(r"/wfs(?:/|\?|$)", low_s)):
            parts = urllib.parse.urlsplit(wfs_url)
            base_params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
            lower_keys = {k.lower(): k for k in base_params}
            base_params[lower_keys.get("service", "service")] = "WFS"
            base_params[lower_keys.get("request", "request")] = "GetFeature"
            ver = base_params.get(lower_keys.get("version", "version"), "2.0.0")
            base_params[lower_keys.get("version", "version")] = ver
            if wfs_typename and "typenames" not in lower_keys and "typename" not in lower_keys:
                base_params["typeNames" if ver.startswith("2") else "typeName"] = wfs_typename
            if "srsname" not in lower_keys:
                base_params["srsName"] = "EPSG:4326"
            if "outputformat" not in lower_keys:
                base_params["outputFormat"] = "application/json"
            if valid_bbox and "bbox" not in lower_keys:
                xmin, ymin, xmax, ymax = [float(v) for v in bbox]
                base_params["bbox"] = f"{xmin},{ymin},{xmax},{ymax},EPSG:4326"

            offset = 0
            page_idx = 0
            while total_cnt < max_total:
                if CANCEL_REQUESTED:
                    raise RuntimeError("Execution stopped by user.")
                page_idx += 1
                params = dict(base_params)
                params["startIndex" if ver.startswith("2") else "startIndex"] = str(offset)
                params["count" if ver.startswith("2") else "maxFeatures"] = str(batch_sz)
                page_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(params), ""))
                raw_bytes = _fetch_http_bytes(page_url, timeout=25)
                added, layer_tbl = _append_remote_bytes_to_gpkg(raw_bytes, tag, page_idx, out_file, layer_tbl)
                if added <= 0:
                    break
                total_cnt += added
                offset += added
                _report_progress("add_vector_layer", min(94, 15 + page_idx * 10), f"WFS page {page_idx} ({total_cnt:,} features)...")
                if added < batch_sz:
                    break
            return out_file if total_cnt > 0 and os.path.exists(out_file) else None

        # 3. BBox-templated REST URL (spatial tile grid iteration)
        if any(tok in low_s for tok in ("{xmin}", "{ymin}", "{xmax}", "{ymax}", "{bbox}")):
            if not valid_bbox:
                raise ValueError("URL requires a 'bbox': [xmin, ymin, xmax, ymax] parameter for tiled fetching.")
            xmin, ymin, xmax, ymax = [float(v) for v in bbox]
            nx = max(1, min(6, math.ceil(abs(xmax - xmin) / 0.1)))
            ny = max(1, min(6, math.ceil(abs(ymax - ymin) / 0.1)))
            dx = (xmax - xmin) / nx
            dy = (ymax - ymin) / ny
            tiles = [
                (xmin + ix * dx, ymin + iy * dy, xmin + (ix + 1) * dx, ymin + (iy + 1) * dy)
                for ix in range(nx) for iy in range(ny)
            ]
            for t_idx, (tx0, ty0, tx1, ty1) in enumerate(tiles, start=1):
                if CANCEL_REQUESTED:
                    raise RuntimeError("Execution stopped by user.")
                t_url = (
                    raw_s.replace("{xmin}", str(tx0)).replace("{ymin}", str(ty0))
                    .replace("{xmax}", str(tx1)).replace("{ymax}", str(ty1))
                    .replace("{bbox}", f"{tx0},{ty0},{tx1},{ty1}")
                )
                raw_bytes = _fetch_http_bytes(t_url, timeout=25)
                added, layer_tbl = _append_remote_bytes_to_gpkg(raw_bytes, tag, t_idx, out_file, layer_tbl)
                total_cnt += added
                _report_progress("add_vector_layer", min(94, int(15 + 75 * t_idx / len(tiles))), f"Tiled fetch {t_idx}/{len(tiles)} ({total_cnt:,} features)...")
            return out_file if total_cnt > 0 and os.path.exists(out_file) else None

        # 4. Generic offset/page GeoJSON REST endpoints
        parts = urllib.parse.urlsplit(raw_s)
        base_params = dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
        lower_keys = {k.lower(): k for k in base_params}
        use_page_num = "page" in lower_keys and "offset" not in lower_keys
        page_key = lower_keys.get("page", "page") if use_page_num else lower_keys.get("offset", "offset")
        limit_key = lower_keys.get("limit") or lower_keys.get("per_page") or lower_keys.get("pagesize")
        page_idx = 0
        offset = 1 if use_page_num else 0
        while total_cnt < max_total:
            if CANCEL_REQUESTED:
                raise RuntimeError("Execution stopped by user.")
            page_idx += 1
            params = dict(base_params)
            params[page_key] = str(offset)
            if limit_key:
                params[limit_key] = str(batch_sz)
            page_url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(params), ""))
            raw_bytes = _fetch_http_bytes(page_url, timeout=25)
            added, layer_tbl = _append_remote_bytes_to_gpkg(raw_bytes, tag, page_idx, out_file, layer_tbl)
            if added <= 0:
                break
            total_cnt += added
            offset += 1 if use_page_num else added
            _report_progress("add_vector_layer", min(94, 15 + page_idx * 10), f"API page {page_idx} ({total_cnt:,} features)...")
            if added < batch_sz and not limit_key:
                # If API used its own smaller page limit when no limit_key was present, check added == 0 on next or stop if small
                if page_idx == 1 and added >= 50:
                    continue
                break
            elif limit_key and added < batch_sz:
                break
        return out_file if total_cnt > 0 and os.path.exists(out_file) else None
    except Exception as exc:
        if "Execution stopped by user" in str(exc):
            raise
        LOGGER.info(f"Paginated remote vector helper fell back to direct provider for {path!r}: {exc}")
        if os.path.exists(out_file):
            try:
                os.remove(out_file)
            except Exception:  # nosec B110
                pass
        return None


@tool("Add a vector layer (shp, gpkg, geojson, csv-with-uri, PostGIS uri, or paginated WFS / ArcGIS FeatureServer REST endpoints).",
      {"path": S("string", "file path or provider uri"),
       "name": S("string", "layer name"),
       "provider": S("string", "ogr (default), delimitedtext, postgres, memory, wfs..."),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] in WGS84 for tiled/paginated remote fetching"),
       "paginate": S("boolean", "iterate pages/tiles for remote WFS/FeatureServer/REST APIs until complete (default true)"),
       "page_size": S("integer", "features per page/tile iteration (default 1000)")})
def add_vector_layer(path, name=None, provider="ogr", bbox=None, paginate=True, page_size=1000):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        _record_study_bbox(bbox, "EPSG:4326")
    resolved_name = name or os.path.splitext(os.path.basename(str(path).split("?")[0]))[0] or "Vector_Layer"
    load_path = path
    load_provider = provider
    if paginate and _is_remote_vector_candidate(path, provider):
        gpkg_path = _fetch_paginated_remote_vector_to_gpkg(path, provider=provider, bbox=bbox, page_size=page_size)
        if gpkg_path and _count_gpkg_features(gpkg_path) > 0:
            load_path = gpkg_path
            load_provider = "ogr"

    lyr = QgsVectorLayer(load_path, resolved_name, load_provider)
    if not lyr.isValid():
        raise ValueError(f"Invalid vector source: {path}")
    if not _is_valid_extent(lyr.extent()):
        try:
            lyr.updateExtents()
        except Exception:  # nosec B110
            pass
    p = QgsProject.instance()
    non_wms = [l for l in p.mapLayers().values() if l.providerType() != "wms"]
    is_first = len(non_wms) == 0
    p.addMapLayer(lyr)
    if is_first and _is_valid_extent(lyr.extent()):
        safe_ext = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
        if safe_ext:
            c = _canvas()
            if c:
                safe_ext.scale(1.05)
                c.setExtent(safe_ext)
                c.refresh()
    else:
        _sanitize_canvas_extent()
    return _layer_info(lyr)


@tool("Add a raster layer (GeoTIFF, DEM, WMS uri...).",
      {"path": S("string", "file path or uri"), "name": S("string", ""), "provider": S("string", "gdal (default) or wms")})
def add_raster_layer(path, name=None, provider="gdal"):
    lyr = QgsRasterLayer(path, name or os.path.splitext(os.path.basename(path))[0], provider)
    if not lyr.isValid():
        raise ValueError(f"Invalid raster source: {path}")
    p = QgsProject.instance()
    non_wms = [l for l in p.mapLayers().values() if l.providerType() != "wms"]
    is_first = len(non_wms) == 0
    p.addMapLayer(lyr)
    if is_first and _is_valid_extent(lyr.extent()):
        safe_ext = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
        if safe_ext:
            c = _canvas()
            if c:
                safe_ext.scale(1.05)
                c.setExtent(safe_ext)
                c.refresh()
    else:
        _sanitize_canvas_extent()
    return _layer_info(lyr)


# ---------------------------------------------------------------- digitizing & georeferencing
def _solve_linear_3x3(A, b):
    """Solve 3x3 linear system Ax = b via Gaussian elimination with partial pivoting."""
    M = [[float(A[r][c]) for c in range(3)] + [float(b[r])] for r in range(3)]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-15:
            return None
        M[col], M[piv] = M[piv], M[col]
        div = M[col][col]
        for c in range(col, 4):
            M[col][c] /= div
        for r in range(3):
            if r != col and M[r][col] != 0.0:
                fac = M[r][col]
                for c in range(col, 4):
                    M[r][c] -= fac * M[col][c]
    return [M[0][3], M[1][3], M[2][3]]


def _fit_helmert(pts):
    """Fit 4-DOF Helmert similarity transform: X = tx + a*x - b*y, Y = ty + b*x + a*y."""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    mX = sum(p[2] for p in pts) / n
    mY = sum(p[3] for p in pts) / n
    denom = sum((p[0] - mx) ** 2 + (p[1] - my) ** 2 for p in pts)
    if denom < 1e-12:
        raise ValueError("GCP pixel coordinates are collinear or identical; spread GCPs across the image.")
    num_a = sum((p[0] - mx) * (p[2] - mX) + (p[1] - my) * (p[3] - mY) for p in pts)
    num_b = sum((p[0] - mx) * (p[3] - mY) - (p[1] - my) * (p[2] - mX) for p in pts)
    a = num_a / denom
    b = num_b / denom
    tx = mX - a * mx + b * my
    ty = mY - b * mx - a * my
    scale = math.hypot(a, b)
    if scale < 1e-15:
        raise ValueError("GCP map coordinates collapse to zero scale; verify target coordinates.")
    return a, b, tx, ty, scale


def _fit_affine_residuals(pts):
    """Fit 6-DOF affine transform in centered coordinates for RMSE diagnostics."""
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    mX = sum(p[2] for p in pts) / n
    mY = sum(p[3] for p in pts) / n
    A = [[0.0] * 3 for _ in range(3)]
    bx = [0.0] * 3
    by = [0.0] * 3
    for px, py, X, Y in pts:
        row = (1.0, px - mx, py - my)
        for r in range(3):
            for c in range(3):
                A[r][c] += row[r] * row[c]
            bx[r] += row[r] * (X - mX)
            by[r] += row[r] * (Y - mY)
    cx = _solve_linear_3x3(A, bx)
    cy = _solve_linear_3x3(A, by)
    if not cx or not cy:
        return None
    preds = []
    for px, py, _, _ in pts:
        u, v = px - mx, py - my
        preds.append((mX + cx[0] + cx[1] * u + cx[2] * v, mY + cy[0] + cy[1] * u + cy[2] * v))
    px_scale = max(math.hypot(cx[1], cx[2]), math.hypot(cy[1], cy[2]), 1e-15)
    return preds, px_scale


@tool("Georeference an unreferenced raster image (scanned map, drone aerial photo, PNG/JPG/TIF) using Ground Control Points (GCPs). Supports Helmert similarity (2+ GCPs), Polynomial 1-3, and TPS with per-GCP residual and RMSE diagnostics.",
      {"image_path": S("string", "absolute path to unreferenced raster image"),
       "gcps": S("array", "list of GCPs as [[pixel_x, pixel_y, map_x, map_y], ...] or [{'pixel_x':..., 'pixel_y':..., 'map_x':..., 'map_y':...}]"),
       "output_path": S("string", "output GeoTIFF path (defaults to same folder with _georef.tif)"),
       "crs": S("string", "target CRS, default 'EPSG:4326'"),
       "transform": S("string", "'helmert' (4-DOF similarity, 2+ GCPs) | 'polynomial1' (Affine, 3+ GCPs, default) | 'polynomial2' (6+ GCPs) | 'polynomial3' (10+ GCPs) | 'tps' (Thin Plate Spline, 4+ GCPs)"),
       "resample": S("string", "'bilinear' (default) | 'nearest' | 'cubic' | 'cubicspline'"),
       "load_to_canvas": S("boolean", "automatically add resulting layer to QGIS project (default true)")})
def georeference_raster(image_path, gcps, output_path=None, crs="EPSG:4326", transform="polynomial1", resample="bilinear", load_to_canvas=True):
    from osgeo import gdal, osr
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Input image not found: {image_path}")

    t_low = (transform or "polynomial1").lower().strip()
    min_gcps = {"helmert": 2, "polynomial1": 3, "affine": 3, "projective": 4, "polynomial2": 6, "polynomial3": 10, "tps": 4}
    req = min_gcps.get(t_low, 3)
    if len(gcps) < req:
        raise ValueError(f"Transformation '{transform}' requires at least {req} GCPs, but {len(gcps)} provided.")

    parsed_gcps = []
    for idx, item in enumerate(gcps):
        try:
            if isinstance(item, dict):
                px, py = float(item["pixel_x"]), float(item["pixel_y"])
                mx, my = float(item["map_x"]), float(item["map_y"])
            else:
                px, py, mx, my = float(item[0]), float(item[1]), float(item[2]), float(item[3])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid GCP at index {idx}: {item!r} ({exc})")
        parsed_gcps.append((px, py, mx, my))

    # Compute Helmert baseline (provides true similarity transform AND pixel scale for RMSE diagnostics)
    a, b, tx, ty, helmert_scale = _fit_helmert(parsed_gcps)
    helmert_preds = [(tx + a * px - b * py, ty + b * px + a * py) for px, py, _, _ in parsed_gcps]

    preds = helmert_preds
    px_scale = helmert_scale
    transform_note = None
    if t_low in ("polynomial1", "affine", "projective") and len(parsed_gcps) >= 3:
        aff = _fit_affine_residuals(parsed_gcps)
        if aff:
            preds, px_scale = aff
    elif t_low in ("polynomial2", "polynomial3", "tps"):
        transform_note = f"Residuals/RMSE computed against linear baseline for '{transform}'."

    residuals = []
    sq_map = 0.0
    sq_px = 0.0
    for idx, ((px, py, mx, my), (pred_x, pred_y)) in enumerate(zip(parsed_gcps, preds)):
        dx = pred_x - mx
        dy = pred_y - my
        err_map = math.hypot(dx, dy)
        err_px = err_map / px_scale
        sq_map += err_map ** 2
        sq_px += err_px ** 2
        residuals.append({
            "gcp_index": idx,
            "dx_map": round(dx, 6),
            "dy_map": round(dy, 6),
            "error_map": round(err_map, 6),
            "error_pixels": round(err_px, 3),
        })
    rmse_map = math.sqrt(sq_map / len(parsed_gcps))
    rmse_px = math.sqrt(sq_px / len(parsed_gcps))

    # Prepare GDAL GCPs (for true 4-DOF Helmert, synthesize exact corner GCPs so GDAL applies zero shear/skew)
    if t_low == "helmert":
        src_ds = gdal.Open(image_path)
        try:
            src_w = src_ds.RasterXSize if src_ds else 1000
            src_h = src_ds.RasterYSize if src_ds else 1000
        finally:
            src_ds = None
        corners = [(0.0, 0.0), (float(src_w), 0.0), (0.0, float(src_h)), (float(src_w), float(src_h))]
        gdal_gcps = [
            gdal.GCP(tx + a * cx - b * cy, ty + b * cx + a * cy, 0.0, cx, cy)
            for cx, cy in corners
        ]
        poly_order = 1
        use_tps = False
    else:
        gdal_gcps = [gdal.GCP(mx, my, 0.0, px, py) for px, py, mx, my in parsed_gcps]
        poly_order = None
        use_tps = False
        if t_low in ("polynomial1", "affine"):
            poly_order = 1
        elif t_low == "projective":
            poly_order = 1
            transform_note = "GDAL Warp applied degree-1 affine approximation for 'projective'."
        elif t_low == "polynomial2":
            poly_order = 2
        elif t_low == "polynomial3":
            poly_order = 3
        elif t_low == "tps":
            use_tps = True
        else:
            poly_order = 1

    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)

    resample_map = {
        "nearest": gdal.GRA_NearestNeighbour,
        "bilinear": gdal.GRA_Bilinear,
        "cubic": gdal.GRA_Cubic,
        "cubicspline": gdal.GRA_CubicSpline,
    }
    alg = resample_map.get((resample or "bilinear").lower(), gdal.GRA_Bilinear)

    warp_options = gdal.WarpOptions(
        format="GTiff",
        dstSRS=crs,
        resampleAlg=alg,
        polynomialOrder=poly_order,
        tps=use_tps
    )

    if not output_path:
        base, _ = os.path.splitext(image_path)
        output_path = f"{base}_georef.tif"

    vrt_path = f"/vsimem/gcp_warp_{os.getpid()}_{len(parsed_gcps)}.vrt"
    temp_ds = None
    out_ds = None
    try:
        temp_ds = gdal.Translate(vrt_path, image_path, GCPs=gdal_gcps, outputSRS=srs)
        if not temp_ds:
            raise RuntimeError("Failed to attach GCPs to image.")

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        out_ds = gdal.Warp(output_path, temp_ds, options=warp_options)
        temp_ds = None

        if not out_ds:
            raise RuntimeError(f"GDAL warping failed for {output_path}")

        gt = out_ds.GetGeoTransform()
        width, height = out_ds.RasterXSize, out_ds.RasterYSize
    finally:
        temp_ds = None
        out_ds = None
        try:
            gdal.Unlink(vrt_path)
        except Exception:  # nosec B110
            pass

    layer_info = None
    if load_to_canvas:
        layer_info = add_raster_layer(output_path, name=os.path.splitext(os.path.basename(output_path))[0])

    out = {
        "output_path": output_path,
        "crs": crs,
        "transform": transform,
        "extent": [gt[0], gt[3] + gt[5] * height, gt[0] + gt[1] * width, gt[3]],
        "resolution": [abs(gt[1]), abs(gt[5])],
        "gcp_count": len(parsed_gcps),
        "rmse_map_units": round(rmse_map, 6),
        "rmse_pixels": round(rmse_px, 3),
        "gcp_residuals": residuals,
        "layer": layer_info
    }
    if transform_note:
        out["transform_note"] = transform_note
    if rmse_px > 2.0 and t_low != "tps":
        out["quality_warning"] = f"RMSE ({rmse_px:.2f} px) exceeds 2.0 px. Inspect worst gcp_residuals or switch transform."
    return out


@tool("Create a new empty vector layer (GeoPackage, Shapefile, or memory scratch) with specified geometry type, CRS, and attribute fields.",
      {"layer_name": S("string", "name for the new layer"),
       "geometry_type": S("string", "'Point' | 'LineString' | 'Polygon' | 'MultiPoint' | 'MultiLineString' | 'MultiPolygon' (default 'Polygon')"),
       "crs": S("string", "layer CRS, default 'EPSG:4326'"),
       "fields": S("array", "field definitions e.g. [{'name': 'id', 'type': 'integer'}, {'name': 'name', 'type': 'string'}, {'name': 'area_m2', 'type': 'double'}, {'name': 'active', 'type': 'boolean'}]"),
       "output_path": S("string", "optional path to save as .gpkg, .shp, or .geojson (creates memory scratch layer if omitted)"),
       "load_to_canvas": S("boolean", "add layer to active QGIS project (default true)")})
def create_vector_layer(layer_name, geometry_type="Polygon", crs="EPSG:4326", fields=None, output_path=None, load_to_canvas=True):
    geom_str = geometry_type.strip()
    c_auth = crs or "EPSG:4326"

    if output_path:
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        ext = os.path.splitext(output_path)[1].lower()
        if ext in (".gpkg", ".geopackage"):
            driver = "GPKG"
        elif ext in (".geojson", ".json"):
            driver = "GeoJSON"
        else:
            driver = "ESRI Shapefile"

        from osgeo import ogr, osr
        srs = osr.SpatialReference()
        srs.SetFromUserInput(c_auth)
        ogr_drv = ogr.GetDriverByName(driver)
        if driver == "GPKG" and os.path.exists(output_path):
            ds = ogr.Open(output_path, 1)
            if ds is None:
                ds = ogr_drv.CreateDataSource(output_path)
            create_opts = ["OVERWRITE=YES"]
        else:
            if os.path.exists(output_path):
                try:
                    ogr_drv.DeleteDataSource(output_path)
                except Exception:
                    os.remove(output_path)
            ds = ogr_drv.CreateDataSource(output_path)
            create_opts = []

        if ds is None:
            raise RuntimeError(f"Could not open/create OGR datasource at {output_path}")

        try:
            wkb_map = {
                "point": ogr.wkbPoint,
                "linestring": ogr.wkbLineString,
                "polygon": ogr.wkbPolygon,
                "multipoint": ogr.wkbMultiPoint,
                "multilinestring": ogr.wkbMultiLineString,
                "multipolygon": ogr.wkbMultiPolygon
            }
            wkb = wkb_map.get(geom_str.lower(), ogr.wkbPolygon)
            ogr_layer = ds.CreateLayer(layer_name, srs, wkb, options=create_opts) if create_opts else ds.CreateLayer(layer_name, srs, wkb)
            if ogr_layer is None:
                raise RuntimeError(f"Failed to create OGR layer '{layer_name}' in {output_path}")

            ogr_type_map = {
                "int": ogr.OFTInteger, "integer": ogr.OFTInteger,
                "int64": ogr.OFTInteger64, "long": ogr.OFTInteger64,
                "double": ogr.OFTReal, "float": ogr.OFTReal, "real": ogr.OFTReal,
                "string": ogr.OFTString, "str": ogr.OFTString, "text": ogr.OFTString,
                "bool": getattr(ogr, "OFTBoolean", ogr.OFTInteger), "boolean": getattr(ogr, "OFTBoolean", ogr.OFTInteger),
                "date": ogr.OFTDate, "datetime": ogr.OFTDateTime,
            }
            if fields:
                for f in fields:
                    f_name = f.get("name", "field")
                    f_type = str(f.get("type", "string")).lower()
                    ogr_layer.CreateField(ogr.FieldDefn(f_name, ogr_type_map.get(f_type, ogr.OFTString)))
            ds.FlushCache()
        finally:
            ds = None

        uri = f"{output_path}|layername={layer_name}" if driver == "GPKG" else output_path
        lyr = QgsVectorLayer(uri, layer_name, "ogr")
        if not lyr.isValid() and driver == "GPKG":
            lyr = QgsVectorLayer(output_path, layer_name, "ogr")
    else:
        uri = f"{geom_str}?crs={c_auth}"
        lyr = QgsVectorLayer(uri, layer_name, "memory")
        if fields:
            type_map = {
                "int": QMetaType.Type.Int, "integer": QMetaType.Type.Int,
                "int64": QMetaType.Type.LongLong, "long": QMetaType.Type.LongLong,
                "double": QMetaType.Type.Double, "float": QMetaType.Type.Double, "real": QMetaType.Type.Double,
                "string": QMetaType.Type.QString, "str": QMetaType.Type.QString, "text": QMetaType.Type.QString,
                "bool": QMetaType.Type.Bool, "boolean": QMetaType.Type.Bool,
                "date": QMetaType.Type.QDate, "datetime": QMetaType.Type.QDateTime,
            }
            qfields = [QgsField(f.get("name", "f"), type_map.get(str(f.get("type", "string")).lower(), QMetaType.Type.QString)) for f in fields]
            lyr.dataProvider().addAttributes(qfields)
            lyr.updateFields()

    if not lyr.isValid():
        raise RuntimeError(f"Failed to create valid vector layer '{layer_name}'")

    if load_to_canvas:
        QgsProject.instance().addMapLayer(lyr)

    return _layer_info(lyr)


def _snap_geometry_to_candidates(geom, candidates, tol):
    """Snap each vertex of geom to nearest vertex/segment of candidate geometries within tol."""
    if not candidates or tol <= 0.0:
        return geom
    out = QgsGeometry(geom)
    idx = 0
    while True:
        v = out.vertexAt(idx)
        if v.isEmpty():
            break
        pt = QgsPointXY(v.x(), v.y())
        pt_geom = QgsGeometry.fromPointXY(pt)
        best_pt = None
        best_dist = float(tol)
        for cand in candidates:
            if not cand or cand.isEmpty():
                continue
            near = cand.nearestPoint(pt_geom)
            if near and not near.isEmpty():
                np_pt = near.asPoint()
                d = pt.distance(np_pt)
                if d <= best_dist:
                    best_dist = d
                    best_pt = np_pt
        if best_pt is not None and best_dist > 0.0:
            out.moveVertex(best_pt.x(), best_pt.y(), idx)
        idx += 1
    return out


@tool("Add digitized vector features (points, lines, or polygons) to an existing vector layer with automatic geometry validation, snapping, topological overlap avoidance, and attribute assignment.",
      {"layer": S("string", "target layer name or id"),
       "features": S("array", "list of feature objects: [{'geometry': coordinates or WKT or GeoJSON geometry, 'attributes': {'field': val, ...}}, ...]"),
       "auto_fix_geometry": S("boolean", "automatically repair self-intersections or unclosed rings using makeValid (default true)"),
       "avoid_overlap": S("boolean", "clip new polygons against existing polygons in the layer so adjacent zones share clean borders with zero overlap (default false)"),
       "snap_tolerance": S("number", "snap incoming vertices to existing layer features within this tolerance in layer CRS units (default 0, disabled)")})
def digitize_features(layer, features, auto_fix_geometry=True, avoid_overlap=False, snap_tolerance=0.0):
    import json as _json
    lyr = _layer(layer)
    if not isinstance(lyr, QgsVectorLayer):
        raise ValueError(f"Layer '{layer}' is not a vector layer.")

    was_editable = lyr.isEditable()
    if not was_editable:
        lyr.startEditing()

    fields = lyr.fields()
    added_count = 0
    added_feats = []
    batch_geoms = []
    errors = []
    snap_tol = float(snap_tolerance or 0.0)
    need_multi = QgsWkbTypes.isMultiType(lyr.wkbType())
    layer_geom_type = QgsWkbTypes.geometryType(lyr.wkbType())

    for idx, f_item in enumerate(features):
        geom_raw = f_item.get("geometry")
        attrs = f_item.get("attributes", {})
        geom = None

        if isinstance(geom_raw, str):
            g_str = geom_raw.strip()
            geom = QgsGeometry.fromGeoJson(g_str) if g_str.startswith("{") else QgsGeometry.fromWkt(g_str)
        elif isinstance(geom_raw, dict):
            geom = QgsGeometry.fromGeoJson(_json.dumps(geom_raw))
        elif isinstance(geom_raw, list):
            if layer_geom_type == Qgis.GeometryType.Point:
                geom = QgsGeometry.fromPointXY(QgsPointXY(float(geom_raw[0]), float(geom_raw[1])))
            elif layer_geom_type == Qgis.GeometryType.Line:
                pts = [QgsPointXY(float(pt[0]), float(pt[1])) for pt in geom_raw]
                geom = QgsGeometry.fromPolylineXY(pts)
            elif layer_geom_type == Qgis.GeometryType.Polygon:
                if len(geom_raw) > 0 and isinstance(geom_raw[0], list) and len(geom_raw[0]) > 0 and isinstance(geom_raw[0][0], list):
                    rings = []
                    for r_in in geom_raw:
                        ring = [QgsPointXY(float(p[0]), float(p[1])) for p in r_in]
                        if ring and (ring[0].x() != ring[-1].x() or ring[0].y() != ring[-1].y()):
                            ring.append(ring[0])
                        rings.append(ring)
                    geom = QgsGeometry.fromPolygonXY(rings)
                else:
                    ring = [QgsPointXY(float(p[0]), float(p[1])) for p in geom_raw]
                    if ring and (ring[0].x() != ring[-1].x() or ring[0].y() != ring[-1].y()):
                        ring.append(ring[0])
                    geom = QgsGeometry.fromPolygonXY([ring])

        if not geom or geom.isEmpty():
            errors.append(f"Feature #{idx}: invalid or empty geometry")
            continue

        if auto_fix_geometry and not geom.isGeosValid():
            fixed = geom.makeValid()
            if fixed and not fixed.isEmpty():
                geom = fixed

        if snap_tol > 0.0:
            search_rect = geom.boundingBox()
            search_rect.grow(snap_tol)
            cands = [ef.geometry() for ef in lyr.getFeatures(QgsFeatureRequest().setFilterRect(search_rect)) if ef.hasGeometry()]
            cands.extend(g for g in batch_geoms if g.boundingBox().intersects(search_rect))
            geom = _snap_geometry_to_candidates(geom, cands, snap_tol)

        if avoid_overlap and layer_geom_type == Qgis.GeometryType.Polygon:
            search_rect = geom.boundingBox()
            existing = [ef.geometry() for ef in lyr.getFeatures(QgsFeatureRequest().setFilterRect(search_rect)) if ef.hasGeometry()]
            existing.extend(g for g in batch_geoms if g.boundingBox().intersects(search_rect))
            for eg in existing:
                if geom.intersects(eg):
                    diff = geom.difference(eg)
                    if diff is not None:
                        geom = diff
                    if geom.isEmpty():
                        break
            if geom.isEmpty():
                errors.append(f"Feature #{idx}: completely covered by existing polygon(s) with avoid_overlap=True")
                continue

        if need_multi and not QgsWkbTypes.isMultiType(geom.wkbType()):
            geom.convertToMultiType()

        feat = QgsFeature(fields)
        feat.setGeometry(geom)
        for k, v in attrs.items():
            if fields.indexFromName(k) != -1:
                feat.setAttribute(k, v)

        if lyr.addFeature(feat):
            added_count += 1
            added_feats.append(feat)
            batch_geoms.append(geom)
        else:
            errors.append(f"Feature #{idx}: failed to add to layer")

    committed = lyr.commitChanges()
    if not committed and added_feats:
        commit_errs = lyr.commitErrors()
        lyr.rollBack()
        ok, _ = lyr.dataProvider().addFeatures(added_feats)
        if not ok:
            errors.extend(commit_errs or ["commitChanges and dataProvider fallback failed"])
            added_count = 0
        else:
            lyr.updateExtents()
    _refresh()

    return {
        "layer": lyr.name(),
        "features_added": added_count,
        "total_features": lyr.featureCount(),
        "errors": errors if errors else None
    }


@tool("Create a regular square (fishnet) or hexagonal polygon grid over an extent or layer for raster sampling, visual interpretation, or spatial labeling.",
      {"layer_name": S("string", "name for the new grid layer (default 'Sampling_Grid')"),
       "extent_layer": S("string", "optional layer name or id to derive grid extent from"),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] extent; defaults to canvas or extent_layer"),
       "cell_size": S("number", "spacing/size of grid cells (in layer CRS units; meters >1 on EPSG:4326 are auto-converted to degrees)"),
       "grid_type": S("string", "'square' (Rectangle, default) | 'hexagon'"),
       "crs": S("string", "CRS for the grid (defaults to target extent/layer CRS or project CRS)"),
       "output_path": S("string", "optional .gpkg or .shp path; creates memory layer if omitted"),
       "show_labels": S("boolean", "display centered grid_id labels for visual inspection (default true)")})
def create_sampling_grid(layer_name="Sampling_Grid", extent_layer=None, bbox=None, cell_size=100.0,
                         grid_type="square", crs=None, output_path=None, show_labels=True):
    import processing
    p = QgsProject.instance()

    # Determine CRS and Extent
    target_crs = None
    target_extent = None
    if extent_layer:
        lyr = _layer(extent_layer)
        target_crs = lyr.crs()
        target_extent = lyr.extent()
    elif bbox and len(bbox) == 4:
        target_extent = QgsRectangle(*bbox)
        target_crs = QgsCoordinateReferenceSystem(crs or p.crs().authid())
    else:
        c = _canvas()
        if c and _is_valid_extent(c.extent()):
            target_extent = c.extent()
            target_crs = p.crs()
        else:
            for l in p.mapLayers().values():
                if l.isValid() and l.providerType() != "wms" and _is_valid_extent(l.extent()):
                    target_extent = l.extent()
                    target_crs = l.crs()
                    break

    if not target_extent or not _is_valid_extent(target_extent):
        raise ValueError("Could not determine valid extent for sampling grid. Specify 'extent_layer' or 'bbox'.")

    final_crs = QgsCoordinateReferenceSystem(crs) if crs else (target_crs or p.crs())
    if not final_crs.isValid():
        final_crs = p.crs()

    if target_crs and target_crs != final_crs and target_crs.isValid():
        tr = QgsCoordinateTransform(target_crs, final_crs, p)
        target_extent = tr.transformBoundingBox(target_extent)

    spacing = float(cell_size)
    unit_note = None
    if spacing <= 0:
        spacing = 100.0 if not final_crs.isGeographic() else 0.001
    elif final_crs.isGeographic() and spacing > 1.0:
        # Guard against passing meter-scale cell_size (e.g. 100) into a geographic degree CRS
        orig_m = spacing
        spacing = round(orig_m / 111320.0, 6)
        unit_note = f"Converted {orig_m} m -> {spacing} deg for geographic CRS {final_crs.authid()}."

    width = max(target_extent.width(), 1e-9)
    height = max(target_extent.height(), 1e-9)
    est_cells = int(math.ceil(width / spacing) * math.ceil(height / spacing))
    MAX_CELLS = 50000
    if est_cells > MAX_CELLS:
        min_recommended = round(math.sqrt((width * height) / 10000.0), 6)
        raise ValueError(
            f"Grid would generate ~{est_cells:,} cells (limit {MAX_CELLS:,}), risking memory exhaustion. "
            f"Increase cell_size to at least {min_recommended}."
        )

    type_code = 4 if str(grid_type).lower() in ("hexagon", "hex") else 2

    grid_out = output_path or _project_scratch_file(layer_name or "grid", ".gpkg")
    params = {
        "TYPE": type_code,
        "EXTENT": target_extent,
        "HSPACING": spacing,
        "VSPACING": spacing,
        "HOVERLAY": 0,
        "VOVERLAY": 0,
        "CRS": final_crs,
        "OUTPUT": grid_out
    }

    res = processing.run("native:creategrid", params)
    out_lyr = res.get("OUTPUT")
    if isinstance(out_lyr, str):
        grid_lyr = QgsVectorLayer(out_lyr, layer_name, "ogr")
    else:
        grid_lyr = out_lyr
        grid_lyr.setName(layer_name)

    if not grid_lyr or not grid_lyr.isValid():
        raise RuntimeError("Failed to generate sampling grid.")

    f_defs = [
        QgsField("grid_id", QMetaType.Type.Int),
        QgsField("class", QMetaType.Type.QString),
        QgsField("confidence", QMetaType.Type.Double),
        QgsField("notes", QMetaType.Type.QString),
    ]
    existing_names = [f.name() for f in grid_lyr.fields()]
    to_add = [f for f in f_defs if f.name() not in existing_names]
    if to_add:
        grid_lyr.dataProvider().addAttributes(to_add)
        grid_lyr.updateFields()

    idx_id = grid_lyr.fields().indexOf("grid_id")
    if idx_id >= 0:
        changes = {}
        for seq, feat in enumerate(grid_lyr.getFeatures()):
            changes[feat.id()] = {idx_id: seq + 1}
        grid_lyr.dataProvider().changeAttributeValues(changes)

    # Transparent fill with high-visibility boundary outline so imagery underneath stays clear
    sym = QgsSymbol.defaultSymbol(grid_lyr.geometryType())
    sym.setColor(QColor(0, 0, 0, 0))
    sl = sym.symbolLayer(0)
    if hasattr(sl, "setStrokeColor"):
        sl.setStrokeColor(QColor("#0284c7"))
    if hasattr(sl, "setStrokeWidth"):
        sl.setStrokeWidth(0.35)
    grid_lyr.setRenderer(QgsSingleSymbolRenderer(sym))

    p.addMapLayer(grid_lyr)
    if show_labels:
        try:
            set_labels(layer=grid_lyr.id(), field="grid_id", size=9, color="#0f172a", buffer_color="#ffffff", placement="over_centroid")
        except Exception as exc:
            LOGGER.warning(f"Failed to enable grid_id labels: {exc}")
    _refresh()

    out = {
        "layer_name": grid_lyr.name(),
        "layer_id": grid_lyr.id(),
        "crs": final_crs.authid(),
        "cell_count": grid_lyr.featureCount(),
        "cell_size": spacing,
        "extent": [round(target_extent.xMinimum(), 4), round(target_extent.yMinimum(), 4),
                   round(target_extent.xMaximum(), 4), round(target_extent.yMaximum(), 4)]
    }
    if unit_note:
        out["unit_note"] = unit_note
    return out


SEMANTIC_GRID_COLORS = {
    "water": "#0284c7", "water body": "#0284c7", "air": "#0284c7", "perairan": "#0284c7", "sungai": "#0284c7", "flooded": "#0284c7", "banjir": "#0284c7",
    "dense forest": "#1b5e20", "forest": "#1b5e20", "hutan": "#1b5e20", "hutan lebat": "#1b5e20",
    "open vegetation": "#4caf50", "vegetation": "#4caf50", "vegetasi": "#4caf50", "semak": "#4caf50", "intact": "#22c55e", "aman": "#22c55e",
    "agriculture": "#cddc39", "cropland": "#cddc39", "pertanian": "#cddc39", "sawah": "#cddc39", "perkebunan": "#84cc16",
    "urban": "#dc2626", "built-up": "#dc2626", "built up": "#dc2626", "terbangun": "#dc2626", "destroyed": "#dc2626", "hancur": "#dc2626",
    "residential": "#f87171", "permukiman": "#f87171", "damaged": "#f97316", "rusak": "#f97316",
    "commercial": "#9333ea", "industrial": "#9333ea", "industri": "#9333ea", "komersial": "#9333ea",
    "bare soil": "#d97706", "bare land": "#d97706", "tanah terbuka": "#d97706", "lahan terbuka": "#d97706",
    "cloud": "#94a3b8", "shadow": "#94a3b8", "awan": "#94a3b8", "bayangan": "#94a3b8", "unclassified": "#cbd5e1",
}


def _semantic_color_for_class(val):
    s = str(val or "").strip().lower()
    if s in SEMANTIC_GRID_COLORS:
        return SEMANTIC_GRID_COLORS[s]
    for key, hex_col in SEMANTIC_GRID_COLORS.items():
        if key in s:
            return hex_col
    return None


@tool("Batch update classification labels, confidence scores, and notes on a sampling grid layer.",
      {"layer": S("string", "grid layer name or id"),
       "labels": S("array", "list of dicts: [{'grid_id': 1, 'class': 'Vegetation', 'confidence': 0.95, 'notes': 'Dense canopy'}, ...] or index-based [{'id': ..., 'class': ...}]"),
       "auto_style": S("boolean", "automatically apply semi-transparent semantic categorized symbology color-coded by class (default true)")})
def label_grid_cells(layer, labels, auto_style=True):
    lyr = _layer(layer)
    if not isinstance(lyr, QgsVectorLayer):
        raise ValueError(f"Layer '{layer}' is not a vector layer.")

    fields = lyr.fields()
    idx_id = fields.indexOf("grid_id")
    idx_class = fields.indexOf("class")
    idx_conf = fields.indexOf("confidence")
    idx_notes = fields.indexOf("notes")

    to_add = []
    if idx_class < 0:
        to_add.append(QgsField("class", QMetaType.Type.QString))
    if idx_conf < 0:
        to_add.append(QgsField("confidence", QMetaType.Type.Double))
    if idx_notes < 0:
        to_add.append(QgsField("notes", QMetaType.Type.QString))
    if to_add:
        lyr.dataProvider().addAttributes(to_add)
        lyr.updateFields()
        fields = lyr.fields()
        idx_class = fields.indexOf("class")
        idx_conf = fields.indexOf("confidence")
        idx_notes = fields.indexOf("notes")

    grid_to_fid = {}
    if idx_id >= 0:
        for f in lyr.getFeatures():
            val = f[idx_id]
            if val is not None:
                grid_to_fid[val] = f.id()

    changes = {}
    updated = 0
    all_fids = set(lyr.allFeatureIds())
    for item in labels:
        if not isinstance(item, dict):
            continue
        fid = None
        if "grid_id" in item and item["grid_id"] in grid_to_fid:
            fid = grid_to_fid[item["grid_id"]]
        elif "id" in item:
            candidate_id = item["id"]
            fid = grid_to_fid[candidate_id] if candidate_id in grid_to_fid else candidate_id

        if fid is None or fid not in all_fids:
            continue

        feat_changes = {}
        if "class" in item and idx_class >= 0:
            feat_changes[idx_class] = str(item["class"])
        if "confidence" in item and idx_conf >= 0:
            try:
                feat_changes[idx_conf] = float(item["confidence"])
            except (ValueError, TypeError):
                pass
        if "notes" in item and idx_notes >= 0:
            feat_changes[idx_notes] = str(item["notes"])

        if feat_changes:
            changes[fid] = feat_changes
            updated += 1

    if changes:
        lyr.dataProvider().changeAttributeValues(changes)
        lyr.triggerRepaint()

    if auto_style and idx_class >= 0:
        try:
            unique_vals = lyr.uniqueValues(idx_class)
            color_map = {}
            for uv in unique_vals:
                col = _semantic_color_for_class(uv)
                if col:
                    color_map[str(uv)] = col
            style_categorized(layer=lyr.id(), field="class", ramp="Spectral", outline_color="#1e293b", opacity=0.68, width=0.25, color_map=color_map or None)
        except Exception as exc:
            LOGGER.warning(f"Failed to auto-style grid layer '{lyr.name()}': {exc}")

    return {
        "layer": lyr.name(),
        "cells_updated": updated,
        "total_cells": lyr.featureCount()
    }


BASEMAP_PRESETS = {
    "esri_light": ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", "Esri Light Gray Canvas (No Key)", 19),
    "carto_light": ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}", "Light Gray Canvas (No Key)", 19),
    "osm": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "OpenStreetMap Standard (No Key)", 19),
    "esri_satellite": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", "Esri World Imagery (No Key)", 19),
    "esri_dark": ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", "Esri Dark Gray Canvas (No Key)", 19),
    "carto_dark": ("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", "Dark Gray Canvas (No Key)", 19),
    "carto_voyager": ("https://tile.openstreetmap.org/{z}/{x}/{y}.png", "OpenStreetMap (No Key)", 19),
    "esri_topo": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}", "Esri World Topo (No Key)", 19),
    "esri_streets": ("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}", "Esri World Street Map (No Key)", 19),
    "opentopomap": ("https://tile.opentopomap.org/{z}/{x}/{y}.png", "OpenTopoMap (No Key)", 17),
}


@tool("Add a 100% free XYZ tile basemap at the bottom of the layer stack without requiring ANY API key. "
      "Choose from proven 100% keyless presets: 'esri_light' (default, pristine muted grey for thematic & choropleth maps), "
      "'osm' (OpenStreetMap standard roads), 'esri_satellite' (high-res public aerial imagery), "
      "'esri_dark' (dark grey mode), 'esri_topo' (topographic), or 'esri_streets'. "
      "Never use basemaps that require API keys or credentials.",
      {"preset": S("string", "'esri_light' | 'osm' | 'esri_satellite' | 'esri_dark' | 'esri_topo' | 'esri_streets' (default 'esri_light')"),
       "url": S("string", "custom keyless XYZ tile URL template"),
       "name": S("string", "optional custom layer name")})
def add_basemap(preset="esri_light", url=None, name=None, **kwargs):
    preset_key = (preset or "esri_light").lower()
    if preset_key in BASEMAP_PRESETS:
        p_url, p_name, zmax = BASEMAP_PRESETS[preset_key]
        target_url = url or p_url
        layer_name = name or p_name
    else:
        target_url = url or BASEMAP_PRESETS["esri_light"][0]
        layer_name = name or "Basemap"
        zmax = 19

    uri = f"type=xyz&url={target_url}&zmax={zmax}&zmin=0&http-header:User-Agent=Mozilla/5.0%20(Windows%20NT%2010.0;%20Win64;%20x64)%20QGIS/3.44"
    lyr = QgsRasterLayer(uri, layer_name, "wms")
    if not lyr.isValid():
        raise ValueError(f"Basemap failed to load from {target_url}")
    p = QgsProject.instance()
    _sanitize_canvas_extent(target_crs=p.crs())
    p.addMapLayer(lyr, False)
    p.layerTreeRoot().insertLayer(-1, lyr)
    return _layer_info(lyr)


@tool("Remove a layer.", {"layer": S("string", "layer name or id (also accepted as layer_id)")})
def remove_layer(layer=None, layer_id=None):
    ref = layer or layer_id
    if not ref:
        raise ValueError("Provide layer name/id via the 'layer' argument.")
    QgsProject.instance().removeMapLayer(_layer(ref).id())
    return {"removed": ref}


@tool("Rename, show/hide, set opacity, set scale-visibility, or move a layer.",
      {"layer": S("string", "name or id"), "new_name": S("string", ""), "name": S("string", "alias for new_name"),
       "visible": S("boolean", ""), "opacity": S("number", "0..1"), "position": S("integer", "0 = top of stack"),
       "min_scale": S("number", "denominator"), "max_scale": S("number", "denominator")})
def update_layer(layer, new_name=None, visible=None, opacity=None, position=None, min_scale=None, max_scale=None, name=None):
    lyr = _layer(layer)
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(lyr.id())
    resolved_name = new_name or name
    if resolved_name:
        lyr.setName(resolved_name)
    if visible is not None and node:
        node.setItemVisibilityChecked(bool(visible))
    if opacity is not None:
        lyr.setOpacity(float(opacity))
    if min_scale is not None or max_scale is not None:
        lyr.setScaleBasedVisibility(True)
        if min_scale is not None:
            lyr.setMinimumScale(float(min_scale))
        if max_scale is not None:
            lyr.setMaximumScale(float(max_scale))
    if position is not None and node:
        clone = node.clone()
        parent = node.parent()
        parent.insertChildNode(int(position), clone)
        parent.removeChildNode(node)
    lyr.triggerRepaint()
    return _layer_info(lyr)


@tool("Zoom canvas to a layer, or to an explicit bbox in project CRS.",
      {"layer": S("string", "name or id"), "bbox": S("array", "[xmin,ymin,xmax,ymax]", items={"type": "number"}),
       "margin": S("number", "fractional padding, default 0.05")})
def zoom_to(layer=None, bbox=None, margin=0.05):
    c = _canvas()
    p = QgsProject.instance()
    if bbox:
        _record_study_bbox(bbox, p.crs().authid() if p.crs().isValid() else "EPSG:4326")
        r = QgsRectangle(*bbox)
    else:
        lyr = _layer(layer)
        r = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
        if not r:
            raise ValueError(f"Cannot calculate a valid bounding box to zoom for layer '{lyr.name()}'. Extent is empty or unprojectable.")
    if not _is_valid_extent(r):
        raise ValueError("Invalid target bounding box for zoom.")
    r.scale(1 + float(margin))
    c.setExtent(r)
    c.refresh()
    return {"extent": [r.xMinimum(), r.yMinimum(), r.xMaximum(), r.yMaximum()]}


# ---------------------------------------------------------------- deep layer inspection
def _jsonable(v):
    if isinstance(v, (int, float, str, bool, type(None))):
        return v
    return str(v)


def _extent_info(lyr):
    p = QgsProject.instance()
    e = lyr.extent()
    if not _is_valid_extent(e):
        return {"extent_layer_crs": None, "extent_wgs84": None}
    d = {"extent_layer_crs": [round(e.xMinimum(), 6), round(e.yMinimum(), 6), round(e.xMaximum(), 6), round(e.yMaximum(), 6)]}
    w = _safe_transform_extent(e, lyr.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), p)
    if w:
        d["extent_wgs84"] = [round(w.xMinimum(), 6), round(w.yMinimum(), 6), round(w.xMaximum(), 6), round(w.yMaximum(), 6)]
    else:
        d["extent_wgs84"] = None
    return d


def _metadata(lyr):
    md = lyr.metadata()
    out = {"title": md.title(), "abstract": md.abstract()[:1500], "keywords": {k: v for k, v in md.keywords().items()},
           "rights": md.rights(), "provider": lyr.providerType(),
           "crs_desc": lyr.crs().description(), "crs_units": QgsUnitTypes.toString(lyr.crs().mapUnits()),
           "crs_geographic": lyr.crs().isGeographic()}
    return {k: v for k, v in out.items() if v not in ("", [], {}, None)}


def _renderer_detail(lyr):
    r = lyr.renderer()
    if r is None:
        return None
    d = {"type": r.type()}
    if isinstance(r, QgsCategorizedSymbolRenderer):
        d["field"] = r.classAttribute()
        d["categories"] = [{"value": _jsonable(c.value()), "label": c.label(), "color": c.symbol().color().name()}
                           for c in r.categories()][:100]
    elif isinstance(r, QgsGraduatedSymbolRenderer):
        d["field"] = r.classAttribute()
        d["method"] = r.classificationMethod().id() if r.classificationMethod() else None
        d["ranges"] = [{"lower": x.lowerValue(), "upper": x.upperValue(), "label": x.label(),
                        "color": x.symbol().color().name()} for x in r.ranges()]
    elif isinstance(r, QgsSingleSymbolRenderer):
        d["color"] = r.symbol().color().name()
    elif r.type() == "RuleRenderer":
        d["rules"] = [{"label": x.label(), "filter": x.filterExpression()} for x in r.rootRule().descendants()][:100]
    return d


def _vector_detail(lyr, sample, max_unique, scan_limit=10000, valid_check_limit=500):
    fields = lyr.fields()
    n_fields = len(fields)
    total = lyr.featureCount()
    scan_limit = max(1, int(scan_limit or 10000))
    valid_check_limit = max(0, int(valid_check_limit if valid_check_limit is not None else 500))

    nulls = [0] * n_fields
    is_num = [fd.isNumeric() for fd in fields]
    num_n = [0] * n_fields
    num_mean = [0.0] * n_fields
    num_m2 = [0.0] * n_fields
    num_min = [None] * n_fields
    num_max = [None] * n_fields
    num_sum = [0.0] * n_fields
    med_cap = 1024
    num_samples = [[] for _ in range(n_fields)]
    rng = random.Random(0)  # nosec B311

    geom_invalid = geom_empty = scanned = 0
    req = QgsFeatureRequest().setLimit(scan_limit)
    for i, f in enumerate(lyr.getFeatures(req)):
        scanned += 1
        attrs = f.attributes()
        for c in range(min(n_fields, len(attrs))):
            v = attrs[c]
            if v is None or str(v) == "NULL":
                nulls[c] += 1
            elif is_num[c]:
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(fv):
                    continue
                cnt = num_n[c] + 1
                num_n[c] = cnt
                num_sum[c] += fv
                if num_min[c] is None or fv < num_min[c]:
                    num_min[c] = fv
                if num_max[c] is None or fv > num_max[c]:
                    num_max[c] = fv
                delta = fv - num_mean[c]
                num_mean[c] += delta / cnt
                num_m2[c] += delta * (fv - num_mean[c])
                buf = num_samples[c]
                if cnt <= med_cap:
                    buf.append(fv)
                else:
                    j = rng.randrange(cnt)
                    if j < med_cap:
                        buf[j] = fv
        g = f.geometry()
        if g is None or g.isEmpty():
            geom_empty += 1
        elif i < valid_check_limit and not g.isGeosValid():
            geom_invalid += 1

    stats = []
    for idx, fd in enumerate(fields):
        s = {"name": fd.name(), "alias": fd.alias() or None, "type": fd.typeName(), "length": fd.length(),
             "nulls": nulls[idx], "comment": fd.comment() or None,
             "origin": {0: "unknown", 1: "provider", 2: "join", 3: "edit", 4: "expression"}.get(int(fields.fieldOrigin(idx)))
             if hasattr(fields, "fieldOrigin") else None}
        uniq = lyr.uniqueValues(idx, max_unique + 1)
        s["unique_count"] = len(uniq) if len(uniq) <= max_unique else f">{max_unique}"
        cnt = num_n[idx]
        if cnt:
            med_buf = sorted(num_samples[idx])
            s.update(
                min=num_min[idx],
                max=num_max[idx],
                mean=num_mean[idx],
                median=med_buf[len(med_buf) // 2],
                stdev=(num_m2[idx] / cnt) ** 0.5,
                sum=num_sum[idx],
            )
        elif len(uniq) <= max_unique:
            s["values"] = sorted((_jsonable(u) for u in uniq), key=str)
        else:
            s["examples"] = [_jsonable(u) for u in list(uniq)[:10]]
        stats.append({k: v for k, v in s.items() if v is not None})
    rows = get_features(lyr.id(), limit=sample) if sample else []
    return {
        "geometry": QgsWkbTypes.displayString(lyr.wkbType()),
        "geometry_type": QgsWkbTypes.geometryDisplayString(lyr.geometryType()),
        "has_z": QgsWkbTypes.hasZ(lyr.wkbType()), "has_m": QgsWkbTypes.hasM(lyr.wkbType()),
        "feature_count": total, "scanned": scanned,
        "empty_geometries": geom_empty,
        "invalid_geometries_sampled": geom_invalid,
        "invalid_geometries_checked": min(scanned, valid_check_limit),
        "selected_count": lyr.selectedFeatureCount(), "subset_filter": lyr.subsetString() or None,
        "editable": lyr.isEditable(), "storage": lyr.storageType(), "encoding": lyr.dataProvider().encoding(),
        "fields": stats, "sample_rows": rows,
        "joins": [{"layer": j.joinLayerId(), "join_field": j.joinFieldName(), "target_field": j.targetFieldName()}
                  for j in lyr.vectorJoins()],
        "relations": [r.name() for r in QgsProject.instance().relationManager().referencedRelations(lyr)],
        "renderer": _renderer_detail(lyr),
        "labels": {"enabled": lyr.labelsEnabled(),
                   "field": lyr.labeling().settings().fieldName if lyr.labelsEnabled() and hasattr(lyr.labeling(), "settings") else None},
        "temporal": lyr.temporalProperties().isActive(),
    }


def _raster_detail(lyr, sample):
    dp = lyr.dataProvider()
    bands = []
    for b in range(1, lyr.bandCount() + 1):
        st = dp.bandStatistics(b, QgsRasterBandStats.Stats.All, lyr.extent(), sample or 250000)
        info = {"band": b, "name": lyr.bandName(b), "description": dp.generateBandName(b),
                "data_type": int(dp.dataType(b)), "nodata": dp.sourceNoDataValue(b) if dp.sourceHasNoDataValue(b) else None,
                "min": st.minimumValue, "max": st.maximumValue, "mean": st.mean, "stdev": st.stdDev}
        try:
            from qgis.core import QgsRasterHistogram  # noqa
            h = dp.histogram(b, 10, st.minimumValue, st.maximumValue, lyr.extent(), sample or 250000)
            info["histogram_10"] = list(h.histogramVector)
        except Exception:  # nosec B110
            pass
        if dp.colorTable(b):
            info["color_table"] = [{"value": c.value, "label": c.label, "color": c.color.name()} for c in dp.colorTable(b)][:64]
        bands.append(info)
    r = lyr.renderer()
    rd = {"type": r.type() if r else None}
    if isinstance(r, QgsSingleBandPseudoColorRenderer) and r.shader():
        fn = r.shader().rasterShaderFunction()
        rd["classes"] = [{"value": i.value, "label": i.label, "color": i.color.name()} for i in fn.colorRampItemList()]
    return {
        "width": lyr.width(), "height": lyr.height(), "band_count": lyr.bandCount(),
        "pixel_size": [lyr.rasterUnitsPerPixelX(), lyr.rasterUnitsPerPixelY()],
        "data_provider": dp.name(), "file_format": lyr.publicSource().rsplit(".", 1)[-1] if dp.name() == "gdal" else None,
        "bands": bands, "renderer": rd, "is_tiled_service": dp.name() == "wms",
    }


def _mesh_detail(lyr):
    dp = lyr.dataProvider()
    groups = []
    for g in range(dp.datasetGroupCount()):
        m = dp.datasetGroupMetadata(g)
        groups.append({"index": g, "name": m.name(), "scalar": m.isScalar(), "min": m.minimum(), "max": m.maximum(),
                       "datasets": dp.datasetCount(g), "temporal": m.isTemporal()})
    return {"vertices": dp.vertexCount(), "faces": dp.faceCount(), "edges": dp.edgeCount(), "dataset_groups": groups}


def _pointcloud_detail(lyr):
    dp = lyr.dataProvider()
    attrs = [{"name": a.name(), "type": a.displayType()} for a in dp.attributes().attributes()]
    out = {"point_count": dp.pointCount(), "attributes": attrs}
    try:
        st = dp.metadataStatistics()
        out["stats"] = {a["name"]: {"min": _jsonable(st.minimum(a["name"])), "max": _jsonable(st.maximum(a["name"])),
                                    "mean": _jsonable(st.mean(a["name"]))} for a in attrs}
        if "Classification" in [a["name"] for a in attrs]:
            out["classes"] = {str(k): v for k, v in st.statisticsOf("Classification").classCount.items()}
    except Exception:  # nosec B110
        pass
    return out


def _vectortile_detail(lyr):
    out = {"source_type": lyr.sourceType(), "zoom": [lyr.sourceMinZoom(), lyr.sourceMaxZoom()]}
    try:
        out["styles"] = [s.styleName() + " / layer=" + s.layerName() for s in lyr.renderer().styles()][:80]
    except Exception:  # nosec B110
        pass
    return out


@tool("Deep inspection of ANY existing layer (vector, raster, mesh, point cloud, vector tile, annotation...): "
      "metadata, CRS & extents, schema with per-field null counts / unique values / numeric stats, geometry validity, "
      "selection, joins, raster band stats+histogram+nodata, mesh datasets, point-cloud attributes, current style. "
      "Use before analysing or restyling user data.",
      {"layer": S("string", "name or id"), "sample": S("integer", "sample rows / raster sample pixels, default 5"),
       "max_unique": S("integer", "list distinct values if <= this, default 30")})
def describe_layer(layer=None, sample=5, max_unique=30, layer_name=None, layer_id=None,
                   scan_limit=10000, valid_check_limit=500):
    target = layer if layer is not None else (layer_name if layer_name is not None else layer_id)
    if target is None:
        raise ValueError("layer name or id is required for describe_layer")
    lyr = _layer(target)
    base = {"id": lyr.id(), "name": lyr.name(), "source": lyr.publicSource(), "crs": lyr.crs().authid(),
            "valid": lyr.isValid(), "opacity": lyr.opacity() if hasattr(lyr, "opacity") else None,
            "scale_visibility": [lyr.minimumScale(), lyr.maximumScale()] if lyr.hasScaleBasedVisibility() else None}
    base.update(_extent_info(lyr))
    base["metadata"] = _metadata(lyr)
    if not lyr.isValid():
        base["error"] = "Layer is invalid (broken source path or unsupported data); fix the source before using it."
        return base
    t = lyr.type()
    LT = Qgis.LayerType
    if t == LT.Vector:
        base["layer_type"] = "vector"
        base.update(_vector_detail(lyr, sample, max_unique, scan_limit=scan_limit, valid_check_limit=valid_check_limit))
    elif t == LT.Raster:
        base["layer_type"] = "raster"
        base.update(_raster_detail(lyr, 0))
    elif t == LT.Mesh:
        base["layer_type"] = "mesh"
        base.update(_mesh_detail(lyr))
    elif t == LT.PointCloud:
        base["layer_type"] = "pointcloud"
        base.update(_pointcloud_detail(lyr))
    elif t == LT.VectorTile:
        base["layer_type"] = "vectortile"
        base.update(_vectortile_detail(lyr))
    else:
        base["layer_type"] = str(t).split(".")[-1].lower()
    return base


@tool("Describe every layer in the project at once (compact: no sample rows). Use to understand a project the user already prepared.",
      {"max_unique": S("integer", "default 15")})
def describe_all_layers(max_unique=15):
    out = []
    for node in QgsProject.instance().layerTreeRoot().findLayers():
        try:
            out.append(describe_layer(
                node.layerId(), sample=0, max_unique=max_unique, scan_limit=2500, valid_check_limit=200
            ))
        except Exception as e:
            out.append({"id": node.layerId(), "error": str(e)})
    groups = [g.name() for g in QgsProject.instance().layerTreeRoot().findGroups(True)]
    return {"groups": groups, "layers": out}


@tool("Features currently selected by the user on the canvas.",
      {"layer": S("string", "name or id; default = active layer"), "limit": S("integer", "default 50"),
       "with_geometry": S("boolean", "")})
def get_selected_features(layer=None, limit=50, with_geometry=False):
    lyr = _layer(layer) if layer else _iface().activeLayer()
    if not isinstance(lyr, QgsVectorLayer):
        raise ValueError("No vector layer selected/active")
    out = []
    for f in lyr.getSelectedFeatures():
        if len(out) >= int(limit):
            break
        row = {k: _jsonable(v) for k, v in zip(lyr.fields().names(), f.attributes())}
        if with_geometry:
            row["_wkt"] = f.geometry().asWkt(4)[:2000]
        out.append(row)
    return {"layer": lyr.name(), "selected_count": lyr.selectedFeatureCount(), "features": out}


@tool("Sample raster values at points (x,y in project CRS) for one or all bands.",
      {"layer": S("string", ""), "points": S("array", "[[x,y],...]", items={"type": "array"})})
def sample_raster(layer, points):
    from qgis.core import QgsPointXY
    lyr = _layer(layer)
    tr = QgsCoordinateTransform(QgsProject.instance().crs(), lyr.crs(), QgsProject.instance())
    out = []
    for x, y in points:
        pt = tr.transform(QgsPointXY(float(x), float(y)))
        vals = [lyr.dataProvider().sample(pt, b)[0] for b in range(1, lyr.bandCount() + 1)]
        out.append({"x": x, "y": y, "values": vals})
    return out


# ---------------------------------------------------------------- data / expressions
@tool("Read features (attributes, optional WKT) with an optional QGIS expression filter.",
      {"layer": S("string", ""), "expression": S("string", "filter, e.g. \"pop\" > 1000"),
       "limit": S("integer", "default 20"), "with_geometry": S("boolean", "")})
def get_features(layer, expression=None, limit=20, with_geometry=False):
    lyr = _layer(layer)
    req = QgsFeatureRequest()
    if expression:
        req.setFilterExpression(expression)
    req.setLimit(int(limit))
    out = []
    for f in lyr.getFeatures(req):
        row = {k: (v if isinstance(v, (int, float, str, bool, type(None))) else str(v))
               for k, v in zip(lyr.fields().names(), f.attributes())}
        if with_geometry:
            row["_wkt"] = f.geometry().asWkt(4)[:2000]
        out.append(row)
    return out


@tool("Field statistics (min/max/mean/unique values) to choose classification.",
      {"layer": S("string", ""), "field": S("string", "")})
def field_stats(layer, field):
    lyr = _layer(layer)
    idx = lyr.fields().indexOf(field)
    if idx < 0:
        raise ValueError(f"No field {field}")
    uniq = list(lyr.uniqueValues(idx, 51))
    res = {"unique_count_sample": len(uniq), "unique_sample": [str(u) for u in uniq[:50]]}
    if lyr.fields()[idx].isNumeric():
        f_name = lyr.fields()[idx].name()
        req = QgsFeatureRequest().setSubsetOfAttributes([f_name], lyr.fields())
        req.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        count = 0
        total = 0.0
        min_v = float("inf")
        max_v = float("-inf")
        scan_limit = 100000
        for i, f in enumerate(lyr.getFeatures(req)):
            if i >= scan_limit:
                res["scanned_limit"] = scan_limit
                break
            v = f[idx]
            if v is not None and str(v) != "NULL":
                try:
                    num = float(v)
                    count += 1
                    total += num
                    if num < min_v:
                        min_v = num
                    if num > max_v:
                        max_v = num
                except (ValueError, TypeError):
                    pass
        if count > 0:
            res.update(min=min_v, max=max_v, mean=total / count, count=count)
    return res


@tool("Field calculator: create or update a field from a QGIS expression (e.g. $area/1e6).",
      {"layer": S("string", ""), "field": S("string", ""), "expression": S("string", ""),
       "field_type": S("string", "double|int|string, used if field is new")})
def calculate_field(layer, field, expression, field_type="double"):
    lyr = _layer(layer)
    types = {"double": QMetaType.Type.Double, "int": QMetaType.Type.Int, "string": QMetaType.Type.QString}
    if lyr.fields().indexOf(field) < 0:
        lyr.dataProvider().addAttributes([QgsField(field, types.get(field_type, QMetaType.Type.Double))])
        lyr.updateFields()
    idx = lyr.fields().indexOf(field)
    if idx < 0:
        raise ValueError(f"Failed to create or locate field '{field}' on layer '{lyr.name()}'")
    exp = QgsExpression(expression)
    if exp.hasParserError():
        raise ValueError(exp.parserErrorString())
    ctx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(lyr))
    exp.prepare(ctx)

    req = QgsFeatureRequest()
    if not exp.needsGeometry():
        req.setFlags(QgsFeatureRequest.Flag.NoGeometry)
    all_attrs_token = getattr(QgsFeatureRequest, "ALL_ATTRIBUTES", "#!allattributes!#")
    ref_cols = exp.referencedColumns()
    if all_attrs_token not in ref_cols:
        valid_cols = [c for c in ref_cols if lyr.fields().indexOf(c) >= 0]
        if len(valid_cols) == len(ref_cols):
            req.setSubsetOfAttributes(valid_cols, lyr.fields())

    dp = lyr.dataProvider()
    chunk = {}
    updated = 0
    chunk_size = 5000
    for f in lyr.getFeatures(req):
        ctx.setFeature(f)
        chunk[f.id()] = {idx: exp.evaluate(ctx)}
        updated += 1
        if len(chunk) >= chunk_size:
            dp.changeAttributeValues(chunk)
            chunk.clear()
    if chunk:
        dp.changeAttributeValues(chunk)
        chunk.clear()
    lyr.triggerRepaint()
    return {"updated": updated, "eval_error": exp.evalErrorString() or None}


@tool("Set a provider subset filter (SQL WHERE) on a layer; empty string clears it.",
      {"layer": S("string", ""), "where": S("string", "")})
def set_layer_filter(layer, where=""):
    lyr = _layer(layer)
    ok = lyr.setSubsetString(where)
    return {"ok": ok, "features": lyr.featureCount()}


# ---------------------------------------------------------------- processing & toolbox
@tool("List all active Processing providers (e.g. native, gdal, pdal, qgis, grass, model, script) and their algorithm counts.")
def list_processing_providers():
    reg = QgsApplication.processingRegistry()
    out = []
    for p in reg.providers():
        algs = [a.id() for a in reg.algorithms() if a.provider().id() == p.id()]
        out.append({"id": p.id(), "name": p.name(), "algorithm_count": len(algs)})
    return out


@tool("Search Processing algorithms by name/id and optionally filter by provider (e.g. 'native', 'gdal', 'qgis').",
      {"query": S("string", "substring, e.g. buffer, clip, contour, slope, raster"),
       "provider": S("string", "optional provider id: native, gdal, qgis, pdal, grass, model")})
def list_algorithms(query="", provider=None):
    q = query.lower()
    reg = QgsApplication.processingRegistry()
    out = []
    for a in reg.algorithms():
        if provider and a.provider().id().lower() != provider.lower():
            continue
        if not q or (q in a.id().lower() or q in a.displayName().lower()):
            out.append({"id": a.id(), "name": a.displayName(), "provider": a.provider().id()})
            if len(out) >= 100:
                break
    return out


@tool("Open the visual Processing Toolbox dock panel in the QGIS user interface.")
def open_processing_toolbox():
    from qgis.PyQt.QtWidgets import QDockWidget, QAction
    iface = _iface()
    if hasattr(iface, "mainWindow") and iface.mainWindow():
        dock = iface.mainWindow().findChild(QDockWidget, "ProcessingToolbox")
        if dock:
            dock.setVisible(True)
            dock.raise_()
            return {"status": "opened", "dock": "ProcessingToolbox"}
        for act in iface.mainWindow().findChildren(QAction):
            if "Processing Toolbox" in act.text() or act.objectName() == "mActionShowProcessingToolbox":
                act.trigger()
                return {"status": "opened", "action": act.text()}
    return {"status": "action_triggered"}


@tool("Open the interactive QGIS Processing Algorithm dialog for a specific algorithm with optional prefilled parameters.",
      {"algorithm": S("string", "e.g. 'native:buffer', 'gdal:contour', 'qgis:heatmapkerneldensityestimation'"),
       "params": S("object", "optional prefilled parameters dict")})
def open_processing_algorithm_dialog(algorithm, params=None):
    alg = QgsApplication.processingRegistry().algorithmById(algorithm)
    if not alg:
        raise ValueError(f"Unknown algorithm {algorithm}")
    from processing.gui.AlgorithmDialog import AlgorithmDialog
    params = dict(params or {})
    for k, v in list(params.items()):
        if isinstance(v, str) and QgsProject.instance().mapLayersByName(v):
            params[k] = QgsProject.instance().mapLayersByName(v)[0]
    parent = _iface().mainWindow() if hasattr(_iface(), "mainWindow") else None
    dlg = AlgorithmDialog(alg.create(), params=params, parent=parent)
    dlg.show()
    return {"opened_dialog": algorithm, "display_name": alg.displayName()}


@tool("Open the QGIS Graphical Model Designer dialog for visual workflow creation.")
def open_model_designer():
    from processing.modeler.ModelerDialog import ModelerDialog
    parent = _iface().mainWindow() if hasattr(_iface(), "mainWindow") else None
    dlg = ModelerDialog(parent=parent)
    dlg.show()
    return {"status": "opened", "window": "ModelerDialog"}


@tool("Parameter definitions of a Processing algorithm.", {"algorithm": S("string", "e.g. native:buffer")})
def describe_algorithm(algorithm):
    a = QgsApplication.processingRegistry().algorithmById(algorithm)
    if not a:
        raise ValueError(f"Unknown algorithm {algorithm}")
    return {"id": a.id(), "params": [
        {"name": p.name(), "type": p.type(), "description": p.description(),
         "optional": bool(p.flags() & p.Flag.FlagOptional) if hasattr(p, "Flag") else None,
         "default": str(p.defaultValue())} for p in a.parameterDefinitions()],
        "outputs": [o.name() for o in a.outputDefinitions()]}


class _CancelableFeedback(QgsProcessingFeedback):
    """Processing feedback that honours user stop requests promptly."""

    def __init__(self, tool_name="run_processing", label=""):
        super().__init__()
        self._tool_name = tool_name
        self._label = label
        self._last_pct = -1

    def setProgress(self, progress):
        if CANCEL_REQUESTED:
            self.cancel()
        pct = int(progress or 0)
        if pct != self._last_pct and (pct == 0 or pct >= 100 or abs(pct - self._last_pct) >= 5):
            self._last_pct = pct
            _report_progress(self._tool_name, pct, self._label)
        super().setProgress(progress)


def _infer_processing_output_path(alg, output_name=None):
    """Build a persistent file path in the active project's scratch/ directory (no memory layers)."""
    out_def = alg.outputDefinition("OUTPUT")
    out_type = (out_def.type() if out_def else "").lower()
    prefix = output_name or (alg.id().replace(":", "_") if alg else "proc")
    if "raster" in out_type:
        return _project_scratch_file(prefix, ".tif")
    if "html" in out_type:
        return _project_scratch_file(prefix, ".html")
    if "folder" in out_type:
        d = _project_scratch_file(prefix, "")
        os.makedirs(d, exist_ok=True)
        return d
    if "file" in out_type:
        return _project_scratch_file(prefix, ".csv")
    return _project_scratch_file(prefix, ".gpkg")


@tool("Run a Processing algorithm. Layer params accept layer names/ids. OUTPUT defaults to a persistent file in the active project scratch/ directory. Results are added to the map when load=true.",
      {"algorithm": S("string", ""), "params": S("object", "parameter dict"),
       "load": S("boolean", "default true"), "output_name": S("string", "rename the loaded result")})
def run_processing(algorithm, params, load=True, output_name=None):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    import processing
    params = dict(params or {})
    for k, v in list(params.items()):
        if isinstance(v, str) and (QgsProject.instance().mapLayersByName(v)):
            params[k] = QgsProject.instance().mapLayersByName(v)[0].id()
    a = QgsApplication.processingRegistry().algorithmById(algorithm)
    if a and "OUTPUT" in [o.name() for o in a.outputDefinitions()]:
        cur_out = params.get("OUTPUT")
        if not cur_out or str(cur_out).strip().upper() == "TEMPORARY_OUTPUT" or str(cur_out).strip().lower().startswith("memory:"):
            params["OUTPUT"] = _infer_processing_output_path(a, output_name=output_name)
    feedback = _CancelableFeedback(tool_name="run_processing", label=f"Processing {algorithm}...")
    _report_progress("run_processing", 0, f"Starting {algorithm}...")
    before = set(QgsProject.instance().mapLayers())
    res = processing.runAndLoadResults(algorithm, params, feedback=feedback) if load else processing.run(algorithm, params, feedback=feedback)
    _sanitize_canvas_extent()
    new = [QgsProject.instance().mapLayer(i) for i in set(QgsProject.instance().mapLayers()) - before]
    if output_name and new:
        # Prefer the layer matching the primary OUTPUT result key to avoid renaming the wrong layer
        primary_id = str(res.get("OUTPUT", ""))
        target = next((l for l in new if l.id() == primary_id), new[0])
        target.setName(output_name)
    return {"results": {k: str(v) for k, v in res.items()}, "new_layers": [_layer_info(l) for l in new]}


# ---------------------------------------------------------------- symbology
def _symbol(lyr, color=None, outline_color=None, width=None, size=None, opacity=None):
    sym = QgsSymbol.defaultSymbol(lyr.geometryType())
    if color:
        sym.setColor(QColor(color))
    sl = sym.symbolLayer(0)
    if outline_color and hasattr(sl, "setStrokeColor"):
        sl.setStrokeColor(QColor(outline_color))
    if width is not None:
        if lyr.geometryType() == Qgis.GeometryType.Line:
            sym.setWidth(float(width))
        elif hasattr(sl, "setStrokeWidth"):
            sl.setStrokeWidth(float(width))
    if size is not None and lyr.geometryType() == Qgis.GeometryType.Point:
        sym.setSize(float(size))
    if opacity is not None:
        sym.setOpacity(float(opacity))
    return sym


@tool("Single-symbol style for a vector layer.",
      {"layer": S("string", ""), "color": S("string", "#rrggbb or name"), "outline_color": S("string", ""),
       "width": S("number", "line/stroke width mm"), "size": S("number", "point size mm"), "opacity": S("number", "0..1")})
def style_single(layer, color="#3388ff", outline_color=None, width=None, size=None, opacity=None):
    lyr = _layer(layer)
    lyr.setRenderer(QgsSingleSymbolRenderer(_symbol(lyr, color, outline_color, width, size, opacity)))
    lyr.triggerRepaint()
    _iface().layerTreeView().refreshLayerSymbology(lyr.id())
    return {"ok": True}


_ROAD_CLASS_VALUES = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link",
    "residential", "living_street", "unclassified", "service", "track",
    "pedestrian", "footway", "path", "steps", "cycleway", "bridleway",
    "standard_gauge", "rail", "light_rail", "narrow_gauge", "subway", "tram",
}

_ROAD_CLASS_TIERS = (
    ("Primary / Arterial Road", ("motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"), "#b45309", "#f59e0b", 1.15, 0.72, 5),
    ("Secondary Road", ("secondary", "secondary_link"), "#64748b", "#fbbf24", 0.90, 0.56, 4),
    ("Tertiary Road", ("tertiary", "tertiary_link"), "#64748b", "#fde68a", 0.72, 0.44, 3),
    ("Residential & Local Street", ("residential", "living_street", "unclassified"), "#94a3b8", "#f8fafc", 0.52, 0.30, 2),
    ("Service & Access Road", ("service", "track"), "#cbd5e1", "#e2e8f0", 0.24, 0.14, 0),
    ("Railway", ("standard_gauge", "rail", "light_rail", "narrow_gauge", "subway", "tram"), "#475569", None, 0.42, 0.0, 1),
    ("Pedestrian & Footpath", ("pedestrian", "footway", "path", "steps", "cycleway", "bridleway"), "#cbd5e1", None, 0.14, 0.0, 0),
)


def _layer_stack_rank(lyr):
    if lyr is None or not lyr.isValid():
        return 5
    if lyr.type() == Qgis.LayerType.Vector:
        gt = lyr.geometryType()
        if gt == Qgis.GeometryType.Point:
            return 0
        if gt == Qgis.GeometryType.Line:
            return 1
        if gt == Qgis.GeometryType.Polygon:
            return 2
        return 3
    if lyr.providerType() == "wms":
        return 5
    return 4


def _normalize_layer_order(p=None):
    """Ensure Points -> Lines -> Polygons -> Rasters -> Basemaps stacking order."""
    p = p or QgsProject.instance()
    root = p.layerTreeRoot()
    all_children = list(root.children())
    layer_indices = [
        (i, ch) for i, ch in enumerate(all_children)
        if hasattr(ch, "layer") and callable(ch.layer) and ch.layer() is not None
    ]
    if len(layer_indices) <= 1:
        return
    first_pos = layer_indices[0][0]
    layer_nodes = [ch for _, ch in layer_indices]
    desired = sorted(
        [(idx, ch) for idx, ch in enumerate(layer_nodes)],
        key=lambda item: (_layer_stack_rank(item[1].layer()), item[0]),
    )
    if [ch for _, ch in desired] == layer_nodes:
        return
    for offset, (_, node) in enumerate(desired):
        vis = node.isVisible() if hasattr(node, "isVisible") else True
        clone = node.clone()
        clone.setItemVisibilityChecked(bool(vis))
        root.insertChildNode(first_pos + offset, clone)
        root.removeChildNode(node)


def _humanize_category_label(val):
    s = str(val) if val is not None else ""
    if "_" in s and s.replace("_", "").isalnum() and s == s.lower():
        return s.replace("_", " ").title()
    return s


def _make_cased_line_symbol(casing_color, fill_color, casing_width, fill_width, fill_pass, opacity=1.0):
    from qgis.core import QgsLineSymbol, QgsSimpleLineSymbolLayer
    sym = QgsLineSymbol()
    sym.deleteSymbolLayer(0)
    if fill_color and fill_width > 0:
        casing = QgsSimpleLineSymbolLayer()
        casing.setColor(QColor(casing_color))
        casing.setWidth(float(casing_width))
        casing.setPenCapStyle(Qt.PenCapStyle.RoundCap)
        casing.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
        casing.setRenderingPass(1 if fill_pass >= 2 else 0)
        sym.appendSymbolLayer(casing)

        inner = QgsSimpleLineSymbolLayer()
        inner.setColor(QColor(fill_color))
        inner.setWidth(float(fill_width))
        inner.setPenCapStyle(Qt.PenCapStyle.RoundCap)
        inner.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
        inner.setRenderingPass(int(fill_pass))
        sym.appendSymbolLayer(inner)
    else:
        stroke = QgsSimpleLineSymbolLayer()
        stroke.setColor(QColor(casing_color))
        stroke.setWidth(float(casing_width))
        stroke.setPenCapStyle(Qt.PenCapStyle.RoundCap)
        stroke.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
        stroke.setRenderingPass(int(fill_pass))
        sym.appendSymbolLayer(stroke)
    if opacity is not None:
        sym.setOpacity(float(opacity))
    return sym


def _style_road_hierarchy(lyr, field, raw_values, width_scale=1.0, opacity=1.0):
    val_by_low = {}
    for v in raw_values:
        if v is None:
            continue
        k = str(v).strip().lower()
        if k not in val_by_low:
            val_by_low[k] = v

    cats = []
    matched_keys = set()
    w_mult = max(0.5, float(width_scale) / 0.4) if width_scale is not None else 1.0
    w_mult = min(w_mult, 1.8)

    for label, members, casing_col, fill_col, cw, fw, r_pass in _ROAD_CLASS_TIERS:
        present = [val_by_low[m] for m in members if m in val_by_low]
        if not present:
            continue
        matched_keys.update(str(x).strip().lower() for x in present)
        eff_opacity = min(float(opacity), 0.55) if r_pass == 0 else float(opacity)
        sym = _make_cased_line_symbol(casing_col, fill_col, cw * w_mult, fw * w_mult, r_pass, opacity=eff_opacity)
        cat_val = present[0] if len(present) == 1 else present
        cats.append(QgsRendererCategory(cat_val, sym, label))

    leftovers = [v for v in raw_values if v is not None and str(v).strip().lower() not in matched_keys]
    if leftovers:
        sym_other = _make_cased_line_symbol("#94a3b8", None, 0.25 * w_mult, 0.0, 0, opacity=opacity)
        cat_val = leftovers[0] if len(leftovers) == 1 else leftovers
        cats.append(QgsRendererCategory(cat_val, sym_other, "Other Ways"))

    ren = QgsCategorizedSymbolRenderer(field, cats)
    ren.setUsingSymbolLevels(True)
    return ren


@tool("Categorized (unique values) style.",
      {"layer": S("string", ""), "field": S("string", "field or expression"), "ramp": S("string", "e.g. Spectral, Set1, Viridis"),
       "outline_color": S("string", ""), "opacity": S("number", "0..1 fill opacity, default 1.0"),
       "width": S("number", "outline/line width mm"),
       "color_map": S("object", "optional explicit {value: '#rrggbb'} overrides")})
def style_categorized(layer, field=None, ramp="Spectral", outline_color=None, opacity=1.0, width=None, color_map=None, column=None):
    field = field or column
    if not field:
        raise ValueError("style_categorized requires 'field' (attribute column name)")
    lyr = _layer(layer)
    idx = lyr.fields().indexOf(field)
    raw_values = sorted(lyr.uniqueValues(idx), key=str) if idx >= 0 else []
    MAX_CATS = 250
    values = raw_values[:MAX_CATS]

    is_road_layer = (
        lyr.type() == Qgis.LayerType.Vector
        and lyr.geometryType() == Qgis.GeometryType.Line
        and not color_map
        and (
            str(field).strip().lower() in ("class", "highway", "subtype", "fclass", "road_class")
            or len({str(v).strip().lower() for v in values} & _ROAD_CLASS_VALUES) >= 2
        )
    )
    if is_road_layer and values:
        ren = _style_road_hierarchy(lyr, field, values, width_scale=width, opacity=opacity)
        lyr.setRenderer(ren)
        lyr.triggerRepaint()
        _iface().layerTreeView().refreshLayerSymbology(lyr.id())
        return {"categories": len(ren.categories()), "total_unique": len(raw_values), "hierarchy": "cased_roads"}

    r = _ramp(ramp)
    cmap = {str(k).strip().lower(): v for k, v in color_map.items()} if isinstance(color_map, dict) else {}
    cats = []
    n = max(len(values) - 1, 1)
    for i, v in enumerate(values):
        s_val = str(v)
        col = cmap.get(s_val.strip().lower()) or r.color(i / n).name()
        sym = _symbol(lyr, color=col, outline_color=outline_color, width=width, opacity=opacity)
        cats.append(QgsRendererCategory(v, sym, _humanize_category_label(v)))
    lyr.setRenderer(QgsCategorizedSymbolRenderer(field, cats))
    lyr.triggerRepaint()
    _iface().layerTreeView().refreshLayerSymbology(lyr.id())
    return {"categories": len(cats), "total_unique": len(raw_values)}


@tool("Graduated (choropleth / proportional) style.",
      {"layer": S("string", ""), "field": S("string", "numeric field or expression"), "classes": S("integer", "default 5"),
       "method": S("string", "jenks|quantile|equal|pretty|stddev"), "ramp": S("string", "e.g. YlOrRd, Reds, Viridis"),
       "invert_ramp": S("boolean", ""), "outline_color": S("string", "")})
def style_graduated(layer, field=None, classes=5, method="jenks", ramp="YlOrRd", invert_ramp=False, outline_color=None, column=None):
    field = field or column
    if not field:
        raise ValueError("style_graduated requires 'field' (numeric column name)")
    lyr = _layer(layer)
    meth = {"jenks": QgsClassificationJenks, "quantile": QgsClassificationQuantile,
            "equal": QgsClassificationEqualInterval, "pretty": QgsClassificationPrettyBreaks,
            "stddev": QgsClassificationStandardDeviation}[method]()
    r = _ramp(ramp)
    if invert_ramp:
        r.invert()
    rend = QgsGraduatedSymbolRenderer(field)
    rend.setSourceSymbol(_symbol(lyr, outline_color=outline_color))
    rend.setClassificationMethod(meth)
    rend.updateClasses(lyr, int(classes))
    rend.updateColorRamp(r)
    lyr.setRenderer(rend)
    lyr.triggerRepaint()
    _iface().layerTreeView().refreshLayerSymbology(lyr.id())
    return {"ranges": [[x.lowerValue(), x.upperValue(), x.label()] for x in rend.ranges()]}


@tool("Singleband pseudocolor style for a raster (DEM, rainfall...).",
      {"layer": S("string", ""), "band": S("integer", "default 1"), "ramp": S("string", "e.g. Spectral, Viridis"),
       "invert_ramp": S("boolean", ""), "classes": S("integer", "default 7")})
def style_raster(layer, band=1, ramp="Spectral", invert_ramp=False, classes=7):
    lyr = _layer(layer)
    st = lyr.dataProvider().bandStatistics(int(band), QgsRasterBandStats.Stats.Min | QgsRasterBandStats.Stats.Max)
    r = _ramp(ramp)
    if invert_ramp:
        r.invert()
    min_v = st.minimumValue
    max_v = st.maximumValue
    import math
    if math.isnan(min_v) or math.isinf(min_v):
        min_v = 0.0
    if math.isnan(max_v) or math.isinf(max_v):
        max_v = 100.0
    if min_v >= max_v:
        max_v = min_v + 1.0

    classes_count = max(2, min(int(classes), 50))
    fcn = QgsColorRampShader(min_v, max_v, r)
    fcn.classifyColorRamp(classes_count, int(band), lyr.extent(), lyr.dataProvider())
    sh = QgsRasterShader()
    sh.setRasterShaderFunction(fcn)
    lyr.setRenderer(QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), int(band), sh))
    lyr.triggerRepaint()
    _iface().layerTreeView().refreshLayerSymbology(lyr.id())
    return {"min": min_v, "max": max_v}


@tool("Apply a QML style file.", {"layer": S("string", ""), "qml_path": S("string", "")})
def load_style(layer, qml_path):
    msg, ok = _layer(layer).loadNamedStyle(qml_path)
    _layer(layer).triggerRepaint()
    return {"ok": ok, "message": msg}


@tool("Names of available color ramps.")
def list_color_ramps():
    return QgsStyle.defaultStyle().colorRampNames()


# ---------------------------------------------------------------- labels
@tool("Enable/disable labels on a vector layer.",
      {"layer": S("string", ""), "field": S("string", "field name"), "expression": S("string", "overrides field"),
       "size": S("number", "pt, default 9"), "color": S("string", ""), "font": S("string", "family"),
       "bold": S("boolean", ""), "buffer_size": S("number", "halo mm, default 1"), "buffer_color": S("string", ""),
       "placement": S("string", "around_point|over_point|horizontal|free|curved|parallel"),
       "max_labels": S("integer", "optional max rendered labels cap to prevent clutter"),
       "enabled": S("boolean", "")})
def set_labels(layer, field=None, expression=None, size=9, color="#000000", font=None, bold=False,
               buffer_size=1.0, buffer_color="#ffffff", placement=None, max_labels=None, enabled=True):
    lyr = _layer(layer)
    if not enabled:
        lyr.setLabelsEnabled(False)
        lyr.triggerRepaint()
        return {"labels": False}
    s = QgsPalLayerSettings()
    gtype = lyr.geometryType()
    if (
        not expression
        and gtype == Qgis.GeometryType.Line
        and field
        and str(field).strip().lower() == "name"
        and lyr.fields().indexOf("class") >= 0
    ):
        s.fieldName = (
            "CASE WHEN COALESCE(\"class\", '') NOT IN "
            "('footway', 'path', 'steps', 'service', 'track', 'bridleway', 'cycleway') "
            "THEN \"name\" END"
        )
        s.isExpression = True
    else:
        s.fieldName = expression or field
        s.isExpression = bool(expression)

    fmt = QgsTextFormat()
    f = QFont(font) if font else fmt.font()
    f.setBold(bool(bold))
    fmt.setFont(f)
    fmt.setSize(float(size))
    fmt.setColor(QColor(color))
    if buffer_size:
        b = QgsTextBufferSettings()
        b.setEnabled(True)
        b.setSize(float(buffer_size))
        b.setColor(QColor(buffer_color))
        fmt.setBuffer(b)
    s.setFormat(fmt)

    P = Qgis.LabelPlacement
    pm = {"around_point": P.AroundPoint, "over_point": P.OverPoint, "horizontal": P.Horizontal,
          "free": P.Free, "curved": P.Curved, "parallel": P.Line}
    if placement in pm and not (gtype == Qgis.GeometryType.Line and placement == "around_point"):
        s.placement = pm[placement]
    elif gtype == Qgis.GeometryType.Line:
        s.placement = P.Curved if hasattr(P, "Curved") else P.Line

    try:
        s.obstacleSettings().setIsObstacle(True)
    except Exception:  # nosec B110
        pass

    ts = s.thinningSettings()
    if gtype == Qgis.GeometryType.Line:
        s.mergeLines = True
        s.repeatDistance = 85.0
        ts.setAllowDuplicateRemoval(True)
        ts.setMinimumDistanceToDuplicate(55.0)
        ts.setLabelMarginDistance(3.0)
        ts.setMinimumFeatureSize(4.0)
        cap = int(max_labels) if max_labels is not None else 35
        if cap > 0:
            ts.setLimitNumberLabelsEnabled(True)
            ts.setMaximumNumberLabels(cap)
    elif gtype == Qgis.GeometryType.Point:
        feat_cnt = lyr.featureCount()
        if max_labels is not None or feat_cnt > 30:
            cap = int(max_labels) if max_labels is not None else 25
            ts.setAllowDuplicateRemoval(True)
            ts.setLimitNumberLabelsEnabled(True)
            ts.setMaximumNumberLabels(cap)
            ts.setLabelMarginDistance(4.5)
            ts.setMinimumDistanceToDuplicate(30.0)
            s.priority = 3
    elif gtype == Qgis.GeometryType.Polygon:
        feat_cnt = lyr.featureCount()
        if max_labels is not None or feat_cnt > 40:
            cap = int(max_labels) if max_labels is not None else 25
            ts.setLimitNumberLabelsEnabled(True)
            ts.setMaximumNumberLabels(cap)
            ts.setLabelMarginDistance(3.0)
            ts.setMinimumFeatureSize(4.0)
    s.setThinningSettings(ts)

    lyr.setLabeling(QgsVectorLayerSimpleLabeling(s))
    lyr.setLabelsEnabled(True)
    lyr.triggerRepaint()
    return {"labels": True}


_LAST_TEMP_CLEANUP = 0.0


def _cleanup_old_temp_files(
    patterns=("qgis_ai_layout_*.png", "overture_*.gpkg", "duckdb_result_*.gpkg"),
    max_age_hours=24,
):
    """Best-effort sweep of stale temporary exports, throttled to run at most every 30 min."""
    global _LAST_TEMP_CLEANUP
    now = time.time()
    if now - _LAST_TEMP_CLEANUP < 1800:
        return
    _LAST_TEMP_CLEANUP = now
    tmp_dir = tempfile.gettempdir()
    cutoff = now - max(1, float(max_age_hours)) * 3600.0
    try:
        active_sources = {
            os.path.abspath(lyr.publicSource().split("|")[0])
            for lyr in QgsProject.instance().mapLayers().values()
            if lyr.publicSource()
        }
    except Exception:
        active_sources = set()
    for pat in patterns:
        for fpath in glob.glob(os.path.join(tmp_dir, pat)):
            try:
                if os.path.abspath(fpath) in active_sources:
                    continue
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
            except Exception:  # nosec B110
                pass


def _export_layout_preview_b64(lay, dpi=120, max_dim=1600):
    """Export layout preview image, downscale large outputs, encode as PNG base64, and unlink temp file."""
    _cleanup_old_temp_files()
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = max(50, min(int(dpi or 120), 150))
    tmp = os.path.join(tempfile.gettempdir(), f"qgis_ai_layout_{os.getpid()}_{int(time.time() * 1000)}.png")
    try:
        res = QgsLayoutExporter(lay).exportToImage(tmp, st)
        if res != QgsLayoutExporter.ExportResult.Success or not os.path.exists(tmp):
            raise RuntimeError(f"Failed to render layout preview (exporter code {res})")
        img = QImage(tmp)
        if not img.isNull() and (img.width() > max_dim or img.height() > max_dim):
            img = img.scaled(
                max_dim,
                max_dim,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            return base64.b64encode(bytes(buf.data())).decode()
        with open(tmp, "rb") as fh:
            return base64.b64encode(fh.read()).decode()
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:  # nosec B110
                pass


# ---------------------------------------------------------------- canvas snapshot (verification loop)
def _render(extent, width, height, layers=None):
    ms = QgsMapSettings()
    p = QgsProject.instance()
    c = _canvas()
    if layers is not None:
        target_layers = layers
    elif c and not _is_deleted(c) and hasattr(c, "layers") and c.layers():
        target_layers = c.layers()
    else:
        root = p.layerTreeRoot()
        target_layers = [l for l in p.mapLayers().values() if (not root.findLayer(l.id())) or root.findLayer(l.id()).isVisible()]
    target_layers = [l for l in target_layers if not _is_deleted(l) and l.isValid()]
    ms.setLayers(target_layers)
    ms.setDestinationCrs(p.crs())
    ms.setExtent(extent)
    ms.setOutputSize(QSize(int(width), int(height)))
    ms.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(ms)
    try:
        job.start()
        job.waitForFinished()
        return QImage(job.renderedImage())
    finally:
        try:
            job.deleteLater()
        except Exception:  # nosec B110
            pass


@tool("Render the current map canvas to PNG and return it as an image so you can visually verify the map.",
      {"width": S("integer", "default 1000"), "height": S("integer", "default 700")})
def snapshot_canvas(width=1000, height=700):
    width = max(64, min(int(width or 1000), 1600))
    height = max(64, min(int(height or 700), 1200))
    c = _canvas()
    ext = c.extent() if c else None
    if not _is_valid_extent(ext):
        _sanitize_canvas_extent()
        ext = c.extent() if c else None
    if not _is_valid_extent(ext):
        p = QgsProject.instance()
        ext = None
        for lyr in p.mapLayers().values():
            if lyr.isValid() and lyr.providerType() != "wms" and _is_valid_extent(lyr.extent()):
                trans = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
                if trans:
                    if ext is None:
                        ext = QgsRectangle(trans)
                    else:
                        ext.combineExtentWith(trans)
        if ext and _is_valid_extent(ext):
            ext.scale(1.05)
        else:
            ext = QgsRectangle(-180, -85, 180, 85)
    img = _render(ext, width, height)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return {"__image__": base64.b64encode(bytes(buf.data())).decode(), "mime": "image/png"}


def _audit_layout_quality(lay, preview_b64=None):
    """Compute objective geometry & composition diagnostics for a print layout so the agent can iterate."""
    from qgis.core import (
        QgsLayoutItemMap, QgsLayoutItemLegend, QgsLayoutItemLabel,
        QgsLayoutItemScaleBar, QgsLayoutItemPicture, QgsLayoutItemShape,
    )
    page = lay.pageCollection().page(0)
    W = float(page.pageSize().width())
    H = float(page.pageSize().height())
    page_area = max(W * H, 1.0)

    maps = []
    overlays = []
    legends = []
    labels = []
    scalebars = []
    pictures = []
    warnings = []

    for it in lay.items():
        if not hasattr(it, "isVisible") or not it.isVisible():
            continue
        if isinstance(it, QgsLayoutItemMap):
            pos = it.pos()
            rect = it.rect()
            x, y, w, h = float(pos.x()), float(pos.y()), float(rect.width()), float(rect.height())
            if w > 0.1 and h > 0.1:
                maps.append((it, x, y, w, h))
            continue
        if not isinstance(it, (QgsLayoutItemLabel, QgsLayoutItemLegend, QgsLayoutItemScaleBar, QgsLayoutItemPicture)):
            continue
        if isinstance(it, QgsLayoutItemPicture) and "qgis_vignette_" in str(it.picturePath() or ""):
            continue
        pos = it.pos()
        rect = it.rect()
        x, y, w, h = float(pos.x()), float(pos.y()), float(rect.width()), float(rect.height())
        if w <= 0.1 or h <= 0.1:
            continue
        rot = float(it.itemRotation()) if hasattr(it, "itemRotation") else 0.0
        if abs(rot) > 0.1 and hasattr(it, "sceneBoundingRect"):
            sb = it.sceneBoundingRect()
            bx1, by1, bx2, by2 = float(sb.left()), float(sb.top()), float(sb.right()), float(sb.bottom())
        else:
            bx1, by1, bx2, by2 = x, y, x + w, y + h

        kind = type(it).__name__.replace("QgsLayoutItem", "")
        entry = (kind, bx1, by1, bx2, by2, it)
        if isinstance(it, QgsLayoutItemLegend):
            legends.append(it)
        elif isinstance(it, QgsLayoutItemLabel):
            labels.append(it)
        elif isinstance(it, QgsLayoutItemScaleBar):
            scalebars.append(it)
        elif isinstance(it, QgsLayoutItemPicture):
            pictures.append(it)
        overlays.append(entry)

    map_area_pct = round(sum(w * h for _, _, _, w, h in maps) / page_area * 100.0, 1)
    is_booklet_lay = "booklet" in str(lay.name() or "").lower()
    if maps and map_area_pct < 40.0 and not is_booklet_lay and len(overlays) < 12:
        warnings.append(f"Hero map covers only {map_area_pct}% of page; consider expanding map frame.")

    for mp_it, _, _, mw, mh in maps:
        ext = mp_it.extent()
        ew, eh = float(ext.width()), float(ext.height())
        if ew <= 0 or eh <= 0:
            warnings.append("Map item has empty extent.")
        elif abs(ew - 360.0) < 1e-3 and abs(eh - 180.0) < 1e-3:
            warnings.append("Map item is still at world fallback extent (-180..180); zoom to study area.")

    oob = []
    for kind, x1, y1, x2, y2, _ in overlays:
        if x1 < -1.5 or y1 < -1.5 or x2 > W + 1.5 or y2 > H + 1.5:
            oob.append(f"{kind}@({x1:.1f},{y1:.1f}..{x2:.1f},{y2:.1f})")
    if oob:
        warnings.append(f"Out-of-bounds items ({len(oob)}): {', '.join(oob[:4])}")

    overlaps = []
    for i in range(len(overlays)):
        k1, ax1, ay1, ax2, ay2, it1 = overlays[i]
        a_area = max(0.0, (ax2 - ax1) * (ay2 - ay1))
        for j in range(i + 1, len(overlays)):
            k2, bx1, by1, bx2, by2, it2 = overlays[j]
            b_area = max(0.0, (bx2 - bx1) * (by2 - by1))
            if (k1 == "Picture" and a_area > page_area * 0.12) or (k2 == "Picture" and b_area > page_area * 0.12):
                continue
            ix = min(ax2, bx2) - max(ax1, bx1)
            iy = min(ay2, by2) - max(ay1, by1)
            if ix > 2.0 and iy > 2.0:
                overlaps.append(f"{k1} vs {k2} ({ix:.1f}x{iy:.1f}mm)")
    if overlaps:
        warnings.append(f"Overlapping layout items: {', '.join(overlaps[:4])}")

    leg_count = 0
    for lg in legends:
        nodes = lg.model().rootGroup().findLayers()
        leg_count += len(nodes)
        for ch in nodes:
            nm = str(ch.customProperty("legend/title-label") or ch.name() or "").lower()
            if any(b in nm for b in ("esri", "basemap", "osm standard", "carto", "band 1")):
                warnings.append(f"Unpruned background/raw layer in legend: {nm}")
    if leg_count > 10:
        warnings.append(f"Legend has {leg_count} layers; consider pruning or multi-column layout.")

    # Pixel-level blank/empty map detection on the rendered layout image
    map_blank_pct = None
    if preview_b64 and maps:
        try:
            qimg = QImage.fromData(base64.b64decode(preview_b64), "PNG")
            if not qimg.isNull() and W > 0 and H > 0:
                sx = qimg.width() / W
                sy = qimg.height() / H
                worst_blank = 0.0
                for _, mx, my, mw, mh in maps:
                    x1 = max(1, int((mx + mw * 0.05) * sx))
                    x2 = min(qimg.width() - 2, int((mx + mw * 0.95) * sx))
                    y1 = max(1, int((my + mh * 0.05) * sy))
                    y2 = min(qimg.height() - 2, int((my + mh * 0.95) * sy))
                    if x2 <= x1 or y2 <= y1:
                        continue
                    c0 = qimg.pixelColor(x1, y1)
                    r0, g0, b0 = c0.red(), c0.green(), c0.blue()
                    total = 0
                    same = 0
                    step_x = max(1, (x2 - x1) // 20)
                    step_y = max(1, (y2 - y1) // 20)
                    for px in range(x1, x2 + 1, step_x):
                        for py in range(y1, y2 + 1, step_y):
                            total += 1
                            c = qimg.pixelColor(px, py)
                            if abs(c.red() - r0) <= 6 and abs(c.green() - g0) <= 6 and abs(c.blue() - b0) <= 6:
                                same += 1
                    if total > 0:
                        worst_blank = max(worst_blank, round(same / total * 100.0, 1))
                map_blank_pct = worst_blank
                if map_blank_pct >= 98.5:
                    warnings.append(
                        f"CRITICAL: Map frame rendered BLANK/EMPTY ({map_blank_pct}% uniform background)! "
                        "Check map extent/CRS, layer visibility, or overlapping shapes and iterate before finishing."
                    )
        except Exception:  # nosec B110
            pass

    return {
        "page_mm": [round(W, 1), round(H, 1)],
        "map_area_pct": map_area_pct,
        "map_blank_pct": map_blank_pct,
        "item_counts": {
            "maps": len(maps),
            "legends": len(legends),
            "scalebars": len(scalebars),
            "north_arrows": len(pictures),
            "labels": len(labels),
        },
        "out_of_bounds_items": oob,
        "overlapping_items": overlaps,
        "legend_layers_count": leg_count,
        "qa_warnings": warnings,
    }


@tool("Render a visual preview image of a print layout plus objective layout QA diagnostics (map_area_pct, out_of_bounds_items, overlapping_items, legend_layers_count, qa_warnings). ALWAYS call this after creating or modifying a layout and iterate until the visual composition and qa_warnings are clean.",
      {"name": S("string", "layout name (default: first or active layout)"), "dpi": S("integer", "render DPI (default: 120)")})
def snapshot_layout(name=None, dpi=120, layout=None, layout_name=None, **kwargs):
    p = QgsProject.instance()
    mgr = p.layoutManager()
    lay = None
    target_name = name or layout or layout_name
    if target_name:
        lay = mgr.layoutByName(str(target_name))
    if not lay:
        layouts = mgr.printLayouts()
        if layouts:
            lay = layouts[0]
    if not lay:
        raise ValueError(f"No print layout found. Available layouts: {[l.name() for l in mgr.printLayouts()]}")

    raw_b64 = _export_layout_preview_b64(lay, dpi=dpi)
    qa = _audit_layout_quality(lay, preview_b64=raw_b64)
    return {
        "__image__": raw_b64,
        "mime": "image/png",
        "layout": lay.name(),
        "page_mm": [lay.pageCollection().page(0).pageSize().width(), lay.pageCollection().page(0).pageSize().height()],
        "qa": qa,
    }


def _humanize_layer_title(name: str) -> str:
    """Turn raw layer names like Overture_Transportation into clean legend titles."""
    known_upper = {
        "rtrw", "rdtr", "poi", "aoi", "dem", "demnas", "osm", "ndvi", "ndwi", "lulc",
        "kdb", "klb", "wgs", "utm", "bnpb", "inarisk", "bmkg", "bps", "big", "das",
        "klhk", "atr", "bpn", "crs", "gdp", "pm25", "nox", "co2", "rgb", "sar",
    }
    aliases = {
        "overture_transportation": "Road Network",
        "overture_buildings": "Building Footprints",
        "overture_places": "Points of Interest",
        "overture_divisions": "Administrative Boundaries",
        "overture_base": "Land & Water Base",
    }
    raw = str(name or "").strip()
    if not raw:
        return "Layer"
    low_key = re.sub(r"[\s\-]+", "_", raw.lower()).strip("_")
    if low_key in aliases:
        return aliases[low_key]
    for prefix in ("overture_", "osm_", "stac_", "qgis_"):
        if low_key.startswith(prefix) and len(low_key) > len(prefix):
            raw = raw[len(prefix):]
            break
    tokens = [t for t in re.split(r"[_\-\s]+", raw) if t]
    out = []
    for tok in tokens:
        tl = tok.lower()
        if tl in known_upper or (tok.isupper() and len(tok) <= 4):
            out.append(tl.upper())
        else:
            out.append(tok.capitalize())
    return " ".join(out) or raw


def _compute_project_kpis(p) -> dict:
    """Compute real live layer/feature/extent metrics from the active QGIS project."""
    from qgis.core import (
        QgsVectorLayer, QgsWkbTypes, QgsDistanceArea,
        QgsCoordinateTransform, QgsGeometry, QgsPointXY, Qgis,
    )
    skip_terms = (
        "esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas",
        "satellite", "terrain", "google", "bing", "stamen", "opentopomap",
        "positron", "dark_matter", "voyager",
    )
    layers = [l for l in p.mapLayers().values() if not any(t in l.name().lower() for t in skip_terms)]
    vec_layers = [l for l in layers if isinstance(l, QgsVectorLayer)]
    total_features = point_features = line_features = polygon_features = 0
    roads_count = buildings_count = places_count = 0
    extent = None
    for lyr in vec_layers:
        fc = int(lyr.featureCount() or 0)
        if fc < 0:
            fc = 0
        total_features += fc
        gt = lyr.geometryType()
        ln = lyr.name().lower()
        if gt == QgsWkbTypes.GeometryType.PointGeometry:
            point_features += fc
            if any(k in ln for k in ("place", "poi", "landmark", "facility", "school", "hospital")):
                places_count += fc
        elif gt == QgsWkbTypes.GeometryType.LineGeometry:
            line_features += fc
            if any(k in ln for k in ("road", "street", "transport", "highway", "route", "corridor", "jalan")):
                roads_count += fc
        elif gt == QgsWkbTypes.GeometryType.PolygonGeometry:
            polygon_features += fc
            if any(k in ln for k in ("building", "footprint", "bangunan", "bldg")):
                buildings_count += fc
        ext = lyr.extent()
        if ext and not ext.isEmpty() and lyr.crs().isValid():
            try:
                if lyr.crs() != p.crs():
                    ext = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(ext)
                extent = ext if extent is None else extent.united(ext)
            except Exception:  # nosec B110
                pass
    area_km2 = width_km = height_km = 0.0
    if extent is not None and not extent.isEmpty():
        try:
            da = QgsDistanceArea()
            da.setSourceCrs(p.crs(), p.transformContext())
            da.setEllipsoid(p.crs().ellipsoidAcronym() or "WGS84")
            raw_area = abs(da.measureArea(QgsGeometry.fromRect(extent)))
            raw_w = da.measureLine(QgsPointXY(extent.xMinimum(), extent.center().y()),
                                   QgsPointXY(extent.xMaximum(), extent.center().y()))
            raw_h = da.measureLine(QgsPointXY(extent.center().x(), extent.yMinimum()),
                                   QgsPointXY(extent.center().x(), extent.yMaximum()))
            area_km2 = da.convertAreaMeasurement(raw_area, Qgis.AreaUnit.SquareKilometers)
            width_km = da.convertLengthMeasurement(raw_w, Qgis.DistanceUnit.Kilometers)
            height_km = da.convertLengthMeasurement(raw_h, Qgis.DistanceUnit.Kilometers)
        except Exception:
            area_km2 = 0.0
    crs_authid = p.crs().authid() if p.crs().isValid() else "EPSG:4326"
    if buildings_count > 0 and roads_count > 0:
        kpi1_label = "BUILDINGS MAPPED"
        kpi1_value = f"{buildings_count:,}"
        kpi1_sub = f"{roads_count:,} roads &middot; {places_count or point_features:,} POIs"
    elif roads_count > 0:
        kpi1_label = "ROAD SEGMENTS"
        kpi1_value = f"{roads_count:,}"
        kpi1_sub = f"{total_features:,} total features"
    elif total_features > 0:
        kpi1_label = "FEATURES MAPPED"
        kpi1_value = f"{total_features:,}"
        kpi1_sub = f"{len(vec_layers)} vector layer{'s' if len(vec_layers) != 1 else ''}"
    else:
        kpi1_label = "ACTIVE LAYERS"
        kpi1_value = f"{len(layers)}"
        kpi1_sub = crs_authid

    if area_km2 >= 0.05:
        kpi2_label = "STUDY AREA"
        kpi2_value = f"{area_km2:.1f} km²" if area_km2 < 100 else f"{area_km2:,.0f} km²"
        kpi2_sub = f"{width_km:.1f} &times; {height_km:.1f} km &middot; {crs_authid}"
    else:
        kpi2_label = "DATA LAYERS"
        kpi2_value = f"{len(layers)}"
        kpi2_sub = crs_authid

    return {
        "layer_count": len(layers),
        "vector_layer_count": len(vec_layers),
        "total_features": total_features,
        "point_features": point_features,
        "line_features": line_features,
        "polygon_features": polygon_features,
        "roads_count": roads_count,
        "buildings_count": buildings_count,
        "places_count": places_count,
        "area_km2": round(area_km2, 2),
        "width_km": round(width_km, 2),
        "height_km": round(height_km, 2),
        "crs_authid": crs_authid,
        "kpi1_label": kpi1_label,
        "kpi1_value": kpi1_value,
        "kpi1_sub": kpi1_sub,
        "kpi2_label": kpi2_label,
        "kpi2_value": kpi2_value,
        "kpi2_sub": kpi2_sub,
    }


def _configure_clean_legend(
    leg,
    mp,
    lay,
    title="Legend",
    dark=False,
    font_family="Arial",
    title_pt=10.0,
    group_pt=8.5,
    item_pt=8.0,
    max_items_per_layer=12,
):
    """Prune basemaps, humanize layer titles, cap noisy symbol lists, and style typography with QgsTextFormat."""
    from qgis.core import QgsLegendStyle, QgsTextFormat, QgsRasterLayer, QgsMapLayerLegendUtils
    from qgis.PyQt.QtGui import QColor, QFont

    leg.setLinkedMap(mp)
    leg.setTitle(title or "")
    leg.setAutoUpdateModel(False)
    skip_terms = (
        "esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas",
        "satellite", "terrain", "google", "bing", "stamen", "opentopomap",
        "positron", "dark_matter", "voyager",
    )
    root = leg.model().rootGroup()
    orig_children = list(root.findLayers())
    for ch in orig_children:
        lyr = ch.layer() if hasattr(ch, "layer") else None
        low = (ch.name() or "").lower()
        is_basemap_prov = bool(lyr is not None and isinstance(lyr, QgsRasterLayer) and lyr.providerType() == "wms")
        if any(st in low for st in skip_terms) or (
            is_basemap_prov and any(st in low for st in ("map", "gray", "light", "dark", "street", "tile"))
        ):
            parent = ch.parent()
            if parent is not None:
                parent.removeChildNode(ch)
    if len(root.findLayers()) == 0 and orig_children:
        leg.setAutoUpdateModel(True)
    else:
        for ch in list(root.findLayers()):
            lyr = ch.layer() if hasattr(ch, "layer") else None
            cname = lyr.name() if lyr is not None else ch.name()
            ch.setCustomProperty("legend/title-label", _humanize_layer_title(cname))
            try:
                nodes = leg.model().layerLegendNodes(ch)
                if max_items_per_layer and len(nodes) > max_items_per_layer:
                    QgsMapLayerLegendUtils.setLegendNodeOrder(ch, list(range(int(max_items_per_layer))))
                    leg.model().refreshLayerLegend(ch)
            except Exception:  # nosec B110
                pass

    title_color = QColor("#f8fafc" if dark else "#0f172a")
    sub_color = QColor("#e2e8f0" if dark else "#1e293b")
    lbl_color = QColor("#cbd5e1" if dark else "#334155")

    def _mk_fmt(pt_size, color, bold=False):
        fmt = QgsTextFormat()
        fnt = QFont(font_family, int(round(pt_size)))
        fnt.setBold(bold)
        fmt.setFont(fnt)
        fmt.setSize(float(pt_size))
        fmt.setColor(color)
        return fmt

    leg.rstyle(QgsLegendStyle.Style.Title).setTextFormat(_mk_fmt(title_pt, title_color, bold=True))
    leg.rstyle(QgsLegendStyle.Style.Group).setTextFormat(_mk_fmt(group_pt, sub_color, bold=True))
    leg.rstyle(QgsLegendStyle.Style.Subgroup).setTextFormat(_mk_fmt(group_pt, sub_color, bold=True))
    leg.rstyle(QgsLegendStyle.Style.SymbolLabel).setTextFormat(_mk_fmt(item_pt, lbl_color, bold=False))
    try:
        leg.rstyle(QgsLegendStyle.Style.Title).setMargin(QgsLegendStyle.Side.Bottom, 2.0)
        leg.rstyle(QgsLegendStyle.Style.Subgroup).setMargin(QgsLegendStyle.Side.Top, 1.8)
        leg.rstyle(QgsLegendStyle.Style.Subgroup).setMargin(QgsLegendStyle.Side.Bottom, 0.8)
        leg.rstyle(QgsLegendStyle.Style.Symbol).setMargin(QgsLegendStyle.Side.Bottom, 0.6)
    except Exception:  # nosec B110
        pass
    try:
        leg.setSymbolWidth(6.0)
        leg.setSymbolHeight(3.2)
        leg.setBoxSpace(2.5)
        leg.updateLegend()
    except Exception:  # nosec B110
        pass
    return leg


def _add_layout_card_shape(lay, x, y, w, h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.5):
    """Draw a real vector rounded-rectangle card so Qt ModeHtml borders aren't dropped."""
    from qgis.core import (
        QgsLayoutItemShape, QgsFillSymbol, QgsLayoutPoint, QgsLayoutSize,
        QgsLayoutMeasurement, QgsUnitTypes, Qgis,
    )
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters
    shp = QgsLayoutItemShape(lay)
    shp.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
    if radius_mm and radius_mm > 0:
        shp.setCornerRadius(QgsLayoutMeasurement(float(radius_mm), mm))
    props = {
        "color": fill,
        "outline_color": stroke or "transparent",
        "outline_width": str(stroke_w),
        "outline_width_unit": "MM",
    }
    if not stroke:
        props["outline_style"] = "no"
    shp.setSymbol(QgsFillSymbol.createSimple(props))
    lay.addLayoutItem(shp)
    shp.attemptMove(QgsLayoutPoint(float(x), float(y), mm))
    shp.attemptResize(QgsLayoutSize(float(w), float(h), mm))
    return shp


# ---------------------------------------------------------------- print layout
@tool("Build a complete print layout: map frame, title, subtitle, legend, scale bar, north arrow, credits. Replaces an existing layout with the same name.",
      {"name": S("string", "layout name"), "title": S("string", ""), "subtitle": S("string", ""),
       "credits": S("string", "source / author text, bottom"), "page_size": S("string", "A4|A3|A2|Letter"),
       "orientation": S("string", "landscape|portrait"), "legend": S("boolean", "default true"),
       "scalebar": S("boolean", "default true"), "north_arrow": S("boolean", "default true"),
       "legend_layers": S("array", "only these layers in legend", items={"type": "string"}),
       "extent_layer": S("string", "fit map to this layer (default: study bbox or core data extent)"),
       "bbox": S("array", "optional explicit [xmin,ymin,xmax,ymax] in WGS84 to lock full map face", items={"type": "number"}),
       "grid": S("boolean", "draw coordinate grid")})
def create_layout(name="Map", title="", subtitle="", credits="", page_size="A4", orientation="landscape",
                  legend=True, scalebar=True, north_arrow=True, legend_layers=None, extent_layer=None, grid=False, bbox=None):
    p = QgsProject.instance()
    mgr = p.layoutManager()
    old = mgr.layoutByName(name)
    if old:
        mgr.removeLayout(old)
    lay = QgsPrintLayout(p)
    lay.initializeDefaults()
    lay.setName(name)
    ori = QgsLayoutItemPage.Orientation.Landscape if orientation == "landscape" else QgsLayoutItemPage.Orientation.Portrait
    page = lay.pageCollection().page(0)
    page.setPageSize(page_size, ori)
    W, H = page.pageSize().width(), page.pageSize().height()
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters
    m = 10
    top = m + (18 if title else 0) + (8 if subtitle else 0)
    side = 70 if legend and orientation == "landscape" else 0
    bottom = m + (10 if credits else 0) + (0 if orientation == "landscape" or not legend else 55)

    def label(text, x, y, w, h, size, bold=False, align_center=False):
        it = QgsLayoutItemLabel(lay)
        it.setText(text)
        fmt = QgsTextFormat()
        f = QFont()
        f.setBold(bold)
        fmt.setFont(f)
        fmt.setSize(size)
        it.setTextFormat(fmt)
        if align_center:
            from qgis.PyQt.QtCore import Qt
            it.setHAlign(Qt.AlignmentFlag.AlignHCenter)
        lay.addLayoutItem(it)
        it.attemptMove(QgsLayoutPoint(x, y, mm))
        it.attemptResize(QgsLayoutSize(w, h, mm))
        return it

    if title:
        label(title, m, m, W - 2 * m, 16, 20, True, True)
    if subtitle:
        label(subtitle, m, m + 16, W - 2 * m, 8, 11, False, True)

    mp = QgsLayoutItemMap(lay)
    mp.setRect(0, 0, 10, 10)
    _normalize_layer_order(p)
    lay.addLayoutItem(mp)
    mp.setCrs(p.crs())
    mp.attemptMove(QgsLayoutPoint(m, top, mm))
    mp.attemptResize(QgsLayoutSize(W - 2 * m - side, H - top - bottom, mm))
    has_basemap = any(l.isValid() and l.providerType() == "wms" for l in p.mapLayers().values())
    ext = _study_bbox_rect(p, bbox) if bbox else None
    if ext is None and extent_layer:
        try:
            lyr = _layer(extent_layer)
            ext = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(lyr.extent())
        except Exception:
            ext = None
    if ext is None:
        ext = _study_bbox_rect(p, None)
    if ext is None:
        from qgis.core import QgsRectangle
        cands = []
        for lyr in p.mapLayers().values():
            if not lyr.isValid() or lyr.providerType() == "wms":
                continue
            try:
                le = lyr.extent()
                if le.isEmpty() or not math.isfinite(le.xMinimum()) or not math.isfinite(le.xMaximum()):
                    continue
                te = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(le)
                cands.append((max(float(te.width()) * float(te.height()), 0.0), te))
            except Exception:  # nosec B110
                pass
        if len(cands) >= 2:
            areas = sorted(a for a, _ in cands)
            med = areas[len(areas) // 2]
            if med > 0:
                filt = [te for a, te in cands if a <= med * 2.2]
                if filt:
                    cands = [(0.0, te) for te in filt]
        for _, te in cands:
            ext = QgsRectangle(te) if ext is None else (ext.combineExtentWith(te) or ext)
    if ext is None:
        ext = _canvas().extent()
    if has_basemap:
        ext.scale(1.05)
        mp.zoomToExtent(ext)
    else:
        ew, eh = float(ext.width()), float(ext.height())
        mw_f, mh_f = float(mp.rect().width()), float(mp.rect().height())
        if ew > 0 and eh > 0 and mw_f > 0 and mh_f > 0:
            from qgis.core import QgsRectangle
            f_ar = mw_f / mh_f
            e_ar = ew / eh
            cx, cy = ext.center().x(), ext.center().y()
            if e_ar > f_ar:
                nw, nh = eh * f_ar, eh
            else:
                nw, nh = ew, ew / f_ar
            ext = QgsRectangle(cx - nw / 2.0, cy - nh / 2.0, cx + nw / 2.0, cy + nh / 2.0)
            mp.setExtent(ext)
        else:
            mp.zoomToExtent(ext)
    mp.setFrameEnabled(True)
    if grid:
        from qgis.core import QgsLayoutItemMapGrid
        g = QgsLayoutItemMapGrid("grid", mp)
        g.setCrs(p.crs())
        iv = max(ext.width(), ext.height()) / 5
        g.setIntervalX(iv)
        g.setIntervalY(iv)
        g.setAnnotationEnabled(True)
        g.setStyle(QgsLayoutItemMapGrid.GridStyle.Cross if hasattr(QgsLayoutItemMapGrid, "GridStyle") else 1)
        mp.grids().addGrid(g)

    if legend:
        lg = QgsLayoutItemLegend(lay)
        lg.setTitle("Legend")
        lg.setLinkedMap(mp)
        lay.addLayoutItem(lg)
        if legend_layers:
            lg.setAutoUpdateModel(False)
            root = lg.model().rootGroup()
            keep = {_layer(x).id() for x in legend_layers}
            for n in list(root.findLayers()):
                if n.layerId() not in keep:
                    n.parent().removeChildNode(n)
            for n in list(root.findLayers()):
                if n.layer():
                    n.setCustomProperty("legend/title-label", _humanize_layer_title(n.layer().name()))
        else:
            _configure_clean_legend(lg, mp, lay, title="Legend", max_items_per_layer=8)
        if orientation == "landscape":
            lg.attemptMove(QgsLayoutPoint(W - m - side + 5, top, mm))
        else:
            lg.attemptMove(QgsLayoutPoint(m, H - bottom + 5, mm))
        lg.setFrameEnabled(True)

    if scalebar:
        sb = QgsLayoutItemScaleBar(lay)
        sb.setStyle("Stepped Line")
        sb.setLinkedMap(mp)
        du = Qgis.DistanceUnit if hasattr(Qgis, "DistanceUnit") else None
        units_obj = du.Meters if (du and mp.scale() < 15000) else (du.Kilometers if du else None)
        if units_obj is not None:
            sb.applyDefaultSize(units_obj)
        else:
            sb.applyDefaultSize()
        sb.setUnitLabel("m" if mp.scale() < 15000 else "km")
        sb.setNumberOfSegments(2)
        sb.setNumberOfSegmentsLeft(0)
        sb.setHeight(2.0)
        sb.setBoxContentSpace(1.2)
        sb.setLabelBarSpace(1.0)
        if hasattr(sb, "setSegmentSizeMode") and hasattr(Qgis, "ScaleBarSegmentSizeMode"):
            sb.setSegmentSizeMode(Qgis.ScaleBarSegmentSizeMode.FitWidth)
            if hasattr(sb, "setMaximumBarWidth"):
                sb.setMaximumBarWidth(42.0)
        fmt_s = QgsTextFormat()
        fmt_s.setFont(QFont("Arial", 6))
        fmt_s.setSize(6.5)
        fmt_s.setColor(QColor("#1e293b"))
        sb.setTextFormat(fmt_s)
        sb.setBackgroundEnabled(True)
        sb.setBackgroundColor(QColor("#ffffff"))
        sb.setFrameEnabled(False)
        lay.addLayoutItem(sb)
        sb.update()
        sb_sz = sb.sizeWithUnits()
        sb_w, sb_h = sb_sz.width(), sb_sz.height()
        target_x = m + 5.0
        target_y = max(top + 2.0, (top + (H - top - bottom)) - sb_h - 4.0)
        sb.attemptMove(QgsLayoutPoint(target_x, target_y, mm))
        sb.update()

    if north_arrow:
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(12, 18, mm))
        na.attemptMove(QgsLayoutPoint(W - m - side - 17, top + 5, mm))

    if credits:
        label(credits, m, H - m - 8, W - 2 * m, 8, 8)

    _register_layout(mgr, lay)
    return {"layout": name, "page_mm": [W, H], "map_scale": mp.scale()}


def _open_layout_window(lay):
    """Open the layout designer window in normal (non-fullscreen) mode so the user can see how the layout is made."""
    if not lay:
        return None
    iface = _iface()
    if not iface or not hasattr(iface, "openLayoutDesigner"):
        return None
    try:
        from qgis.PyQt.QtWidgets import QApplication
        designer = iface.openLayoutDesigner(lay)
        if designer and hasattr(designer, "window"):
            win = designer.window()
            if win:
                if hasattr(win, "isFullScreen") and win.isFullScreen():
                    win.showNormal()
                if hasattr(win, "isMaximized") and win.isMaximized():
                    win.showNormal()
                screen = QApplication.primaryScreen()
                if screen:
                    avail = screen.availableGeometry()
                    w = min(1200, max(800, int(avail.width() * 0.65)))
                    h = min(850, max(600, int(avail.height() * 0.72)))
                    x = max(avail.x(), avail.x() + (avail.width() - w) // 2)
                    y = max(avail.y(), avail.y() + (avail.height() - h) // 2)
                    win.setGeometry(x, y, w, h)
                win.show()
                win.raise_()
        return designer
    except Exception:  # nosec B110
        pass
    return None


def _register_layout(mgr, lay):
    """Add layout to manager and open layout designer window in normal (non-fullscreen) mode."""
    mgr.addLayout(lay)
    _open_layout_window(lay)


def _get_layout(layout_name, open_window=True):
    mgr = QgsProject.instance().layoutManager()
    lay = None
    if not layout_name:
        layouts = mgr.printLayouts()
        if layouts:
            lay = layouts[0]
        else:
            raise ValueError("No layout specified and active project has no layouts.")
    else:
        lay = mgr.layoutByName(str(layout_name))
        if not lay:
            for l in mgr.printLayouts():
                if l.name().lower() == str(layout_name).lower():
                    lay = l
                    break
        if not lay:
            avail = [l.name() for l in mgr.printLayouts()]
            raise ValueError(f"No layout {layout_name!r}. Available: {avail}")
    if open_window and lay:
        _open_layout_window(lay)
    return lay


# ---------------------------------------------------------------- modern & composite layout tools
@tool("Add an inset detail map to an existing print layout, with optional overview rectangle on the main map frame.",
      {"layout": S("string", "name of existing layout"), "x": S("number", "x position on page in mm"),
       "y": S("number", "y position on page in mm"), "width": S("number", "inset width in mm"),
       "height": S("number", "inset height in mm"), "extent_layer": S("string", "layer name to zoom inset map to"),
       "bbox": S("array", "[xmin,ymin,xmax,ymax] in project CRS"), "scale": S("number", "optional map scale denominator"),
       "title": S("string", "optional header text, e.g. 'DETAIL VIEW / INSET AREA'"),
       "show_overview_on_main": S("boolean", "draw an overview rectangle on the main map (default true)"),
       "grid": S("boolean", "include coordinate grid on the inset map")})
def add_layout_inset_map(layout=None, x=0, y=0, width=50, height=50, extent_layer=None, bbox=None, scale=None,
                         title="", show_overview_on_main=True, grid=False, **kwargs):
    target = layout or kwargs.get("layout_name") or kwargs.get("name")
    lay = _get_layout(target)
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters
    p = QgsProject.instance()

    # Find main map
    main_maps = [it for it in lay.items() if isinstance(it, QgsLayoutItemMap)]
    main_map = main_maps[0] if main_maps else None

    # Title label if provided
    top_offset = 0
    if title:
        top_offset = 8
        lbl = QgsLayoutItemLabel(lay)
        lbl.setText(title)
        fmt = QgsTextFormat()
        f = QFont()
        f.setBold(True)
        fmt.setFont(f)
        fmt.setSize(9)
        lbl.setTextFormat(fmt)
        lay.addLayoutItem(lbl)
        lbl.attemptMove(QgsLayoutPoint(float(x), float(y), mm))
        lbl.attemptResize(QgsLayoutSize(float(width), 7, mm))

    # Inset Map item
    inset = QgsLayoutItemMap(lay)
    inset.setRect(0, 0, 10, 10)
    lay.addLayoutItem(inset)
    inset.attemptMove(QgsLayoutPoint(float(x), float(y) + top_offset, mm))
    inset.attemptResize(QgsLayoutSize(float(width), float(height) - top_offset, mm))

    if bbox:
        ext = QgsRectangle(*bbox)
    elif extent_layer:
        lyr = _layer(extent_layer)
        ext = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(lyr.extent())
        ext.scale(1.05)
    else:
        ext = _canvas().extent()
    inset.zoomToExtent(ext)
    if scale:
        inset.setScale(float(scale))
    inset.setFrameEnabled(True)

    # Overview rectangle on the main map
    if show_overview_on_main and main_map:
        from qgis.core import QgsLayoutItemMapOverview, QgsFillSymbol
        ov = QgsLayoutItemMapOverview("overview", main_map)
        ov.setLinkedMap(inset)
        sym = QgsFillSymbol.createSimple({"color": "50,100,200,50", "outline_color": "0,80,180,255", "outline_width": "0.5"})
        ov.setFrameSymbol(sym)
        main_map.overviews().addOverview(ov)

    # Grid on inset
    if grid:
        from qgis.core import QgsLayoutItemMapGrid
        g = QgsLayoutItemMapGrid("inset_grid", inset)
        iv = max(ext.width(), ext.height()) / 4
        g.setIntervalX(iv)
        g.setIntervalY(iv)
        g.setAnnotationEnabled(True)
        inset.grids().addGrid(g)

    lay.refresh()
    return {"inset_created": True, "map_scale": inset.scale(), "x": x, "y": y, "width": width, "height": height}


@tool("Add an infographic horizontal bar chart card into the print layout using HTML/CSS (e.g. disaster counts per regency).",
      {"layout": S("string", "name of existing layout"), "x": S("number", "x pos in mm"), "y": S("number", "y pos in mm"),
       "width": S("number", "width in mm"), "height": S("number", "height in mm"),
       "title": S("string", "chart title, e.g. 'INCIDENTS BY DISTRICT'"),
       "subtitle": S("string", "subtitle, e.g. 'October 2026 · total 143 incidents'"),
       "data": S("array", "list of dicts [{'label':'District A','value':36}, ...] or key-value dict"),
       "bar_color": S("string", "hex color, default '#2b5c8f'"),
       "max_value": S("number", "optional axis maximum for percentage scaling")})
def add_layout_infographic_chart(layout=None, x=0, y=0, width=80, height=50, title="", subtitle="", data=None, bar_color="#2b5c8f", max_value=None, **kwargs):
    import html as _html
    target = layout or kwargs.get("layout_name") or kwargs.get("name")
    lay = _get_layout(target)
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters

    # Normalize data
    items = []
    if isinstance(data, dict):
        items = [{"label": k, "value": v} for k, v in data.items()]
    elif isinstance(data, list):
        items = data or []

    # Sort descending by value
    items.sort(key=lambda d: float(d.get("value", 0)), reverse=True)
    top_val = float(max_value or (items[0]["value"] if items else 1))
    top_val = max(top_val, 1)

    rows = []
    for d in items[:15]:
        lbl = _html.escape(str(d.get("label", "")))
        val = float(d.get("value", 0))
        pct = min(100, max(2, round((val / top_val) * 100)))
        val_str = str(int(val) if val.is_integer() else val)
        bar_table = (
            f"<table width='100%' cellpadding='0' cellspacing='0' style='border-collapse: collapse;'>"
            f"<tr>"
            f"<td width='{pct}%' bgcolor='{bar_color}' height='7' style='border-radius: 2px;'></td>"
            f"<td width='{100 - pct}%'></td>"
            f"</tr>"
            f"</table>"
        )
        rows.append(
            f"<tr>"
            f"<td style='padding: 2px 4px; font-size: 9px; color: #263238; white-space: nowrap;'>{lbl}</td>"
            f"<td style='padding: 2px 4px; width: 60%;'>{bar_table}</td>"
            f"<td align='right' style='padding: 2px 4px; font-size: 9px; font-weight: bold; color: #1a237e;'>{val_str}</td>"
            f"</tr>"
        )

    sub_html = f"<div style='font-size: 9px; color: #546e7a; margin-bottom: 6px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
    full_html = (
        f"<div style='font-family: Arial, Helvetica, sans-serif; background: #ffffff; border: 1px solid #cfd8dc; "
        f"border-radius: 4px; padding: 8px;'>"
        f"<div style='font-size: 11px; font-weight: bold; color: #1a237e; letter-spacing: 0.5px;'>{_html.escape(title)}</div>"
        f"{sub_html}"
        f"<table width='100%' style='border-collapse: collapse; margin-top: 4px;'>{''.join(rows)}</table>"
        f"</div>"
    )

    card = QgsLayoutItemLabel(lay)
    card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
    card.setText(full_html)
    lay.addLayoutItem(card)
    card.attemptMove(QgsLayoutPoint(float(x), float(y), mm))
    card.attemptResize(QgsLayoutSize(float(width), float(height), mm))
    lay.refresh()
    return {"chart_added": True, "items": len(items)}


@tool("Add a horizontal continuous gradient color bar legend into the layout (for elevation, flood hazard, or heatmaps).",
      {"layout": S("string", "name of existing layout"), "x": S("number", "x pos in mm"), "y": S("number", "y pos in mm"),
       "width": S("number", "width in mm, e.g. 55"), "height": S("number", "height in mm, e.g. 18"),
       "title": S("string", "title e.g. 'Elevation (m)' or 'Incident density'"),
       "ramp": S("string", "color ramp name e.g. 'Spectral', 'YlOrRd', 'Blues', 'Viridis'"),
       "min_val": S("string", "minimum label e.g. '-3' or '0 Rendah'"),
       "max_val": S("string", "maximum label e.g. '2.945' or '1 Tinggi'"),
       "invert_ramp": S("boolean", "invert color direction")})
def add_layout_gradient_legend(layout=None, x=0, y=0, width=55, height=18, title="", ramp="Spectral", min_val="0", max_val="100", invert_ramp=False, **kwargs):
    import html as _html
    target = layout or kwargs.get("layout_name") or kwargs.get("name")
    lay = _get_layout(target)
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters

    r = _ramp(ramp)
    steps = 9
    colors = [r.color((1.0 - (i / (steps - 1))) if invert_ramp else (i / (steps - 1))).name() for i in range(steps)]
    cells = "".join(f"<td width='{100 // steps}%' bgcolor='{c}' height='8'></td>" for c in colors)
    grad_bar = f"<table width='100%' cellpadding='0' cellspacing='0' style='margin: 3px 0 2px 0; border: 0.5px solid #b0bec5; border-collapse: collapse;'><tr>{cells}</tr></table>"

    full_html = (
        f"<div style='font-family: Arial, Helvetica, sans-serif; background: #ffffff; border: 1px solid #cfd8dc; "
        f"border-radius: 4px; padding: 5px 7px;'>"
        f"<div style='font-size: 9.5px; font-weight: bold; color: #263238;'>{_html.escape(title)}</div>"
        f"{grad_bar}"
        f"<table width='100%' style='border-collapse: collapse; font-size: 8px; color: #455a64;'>"
        f"<tr><td align='left'>{_html.escape(str(min_val))}</td><td align='right'>{_html.escape(str(max_val))}</td></tr>"
        f"</table>"
        f"</div>"
    )

    card = QgsLayoutItemLabel(lay)
    card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
    card.setText(full_html)
    lay.addLayoutItem(card)
    card.attemptMove(QgsLayoutPoint(float(x), float(y), mm))
    card.attemptResize(QgsLayoutSize(float(width), float(height), mm))
    lay.refresh()
    return {"gradient_legend_added": True, "ramp": ramp, "title": title}


@tool("Add a cluster callout card into the layout with optional connector pointer line to map coordinates.",
      {"layout": S("string", "name of existing layout"), "x": S("number", "card x in mm"), "y": S("number", "card y in mm"),
       "width": S("number", "card width in mm, e.g. 50"), "height": S("number", "card height in mm, e.g. 15"),
       "title": S("string", "cluster/area name, e.g. 'NORTH CLUSTER'"),
       "subtitle": S("string", "summary line, e.g. '18 incidents · 10 km radius'"),
       "badge": S("string", "e.g. 'A', 'B', 'C'"),
       "pointer_x": S("number", "optional pointer target x in layout mm"),
       "pointer_y": S("number", "optional pointer target y in layout mm")})
def add_layout_callout_box(layout=None, x=0, y=0, width=50, height=15, title="", subtitle="", badge="", pointer_x=None, pointer_y=None, **kwargs):
    import html as _html
    target = layout or kwargs.get("layout_name") or kwargs.get("name")
    lay = _get_layout(target)
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters

    badge_html = (
        f"<span style='display: inline-block; background: #1a237e; color: #ffffff; width: 13px; height: 13px; "
        f"text-align: center; border-radius: 2px; margin-right: 4px; font-size: 9px; line-height: 13px;'>{_html.escape(badge)}</span>"
        if badge else ""
    )
    sub_html = f"<div style='font-size: 8px; color: #455a64; margin-top: 1px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
    full_html = (
        f"<div style='font-family: Arial, Helvetica, sans-serif; background: rgba(255, 255, 255, 0.94); border: 1.2px solid #2b5c8f; "
        f"border-radius: 4px; padding: 4px 6px; box-shadow: 0 1px 3px rgba(0,0,0,0.15);'>"
        f"<div style='font-size: 9px; font-weight: bold; color: #0d47a1;'>{badge_html}{_html.escape(title)}</div>"
        f"{sub_html}"
        f"</div>"
    )

    card = QgsLayoutItemLabel(lay)
    card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
    card.setText(full_html)
    lay.addLayoutItem(card)
    card.attemptMove(QgsLayoutPoint(float(x), float(y), mm))
    card.attemptResize(QgsLayoutSize(float(width), float(height), mm))

    # Connector line if pointer target provided
    if pointer_x is not None and pointer_y is not None:
        from qgis.core import QgsLayoutItemPolyline
        from qgis.PyQt.QtCore import QPointF
        from qgis.PyQt.QtGui import QPolygonF
        start_x = float(x) + (float(width) if float(pointer_x) > float(x) else 0)
        start_y = float(y) + float(height) / 2
        line = QgsLayoutItemPolyline(QPolygonF([QPointF(start_x, start_y), QPointF(float(pointer_x), float(pointer_y))]), lay)
        lay.addLayoutItem(line)

    lay.refresh()
    return {"callout_added": True, "badge": badge, "title": title}


def _build_vignette_svg(w_mm, h_mm, bg_hex="#f5efe0"):
    """Create a smooth 4-edge + radial gradient vignette SVG mask that fades map edges into bg_hex."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w_mm:.2f} {h_mm:.2f}" '
        f'width="{w_mm:.2f}mm" height="{h_mm:.2f}mm" preserveAspectRatio="none">'
        f'<defs>'
        f'<linearGradient id="vTop" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{bg_hex}" stop-opacity="0.96"/>'
        f'<stop offset="14%" stop-color="{bg_hex}" stop-opacity="0"/>'
        f'</linearGradient>'
        f'<linearGradient id="vBot" x1="0" y1="1" x2="0" y2="0">'
        f'<stop offset="0%" stop-color="{bg_hex}" stop-opacity="0.96"/>'
        f'<stop offset="14%" stop-color="{bg_hex}" stop-opacity="0"/>'
        f'</linearGradient>'
        f'<linearGradient id="vLeft" x1="0" y1="0" x2="1" y2="0">'
        f'<stop offset="0%" stop-color="{bg_hex}" stop-opacity="0.96"/>'
        f'<stop offset="14%" stop-color="{bg_hex}" stop-opacity="0"/>'
        f'</linearGradient>'
        f'<linearGradient id="vRight" x1="1" y1="0" x2="0" y2="0">'
        f'<stop offset="0%" stop-color="{bg_hex}" stop-opacity="0.96"/>'
        f'<stop offset="14%" stop-color="{bg_hex}" stop-opacity="0"/>'
        f'</linearGradient>'
        f'<radialGradient id="vRad" cx="50%" cy="50%" r="68%">'
        f'<stop offset="52%" stop-color="{bg_hex}" stop-opacity="0"/>'
        f'<stop offset="100%" stop-color="{bg_hex}" stop-opacity="0.62"/>'
        f'</radialGradient>'
        f'</defs>'
        f'<rect width="100%" height="100%" fill="url(#vRad)"/>'
        f'<rect width="100%" height="100%" fill="url(#vTop)"/>'
        f'<rect width="100%" height="100%" fill="url(#vBot)"/>'
        f'<rect width="100%" height="100%" fill="url(#vLeft)"/>'
        f'<rect width="100%" height="100%" fill="url(#vRight)"/>'
        f'</svg>'
    )
    svg_path = os.path.join(tempfile.gettempdir(), f"qgis_vignette_{os.getpid()}_{int(time.time() * 1000)}.svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return svg_path


def _format_dms_center(ext, src_crs, p):
    """Format map center coordinates in DMS (e.g., 7° 47' 38\" S · 110° 21' 56\" E) in WGS84."""
    if ext is None or not _is_valid_extent(ext):
        return ""
    try:
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        tr = _safe_transform_extent(ext, src_crs, wgs84, p) if (src_crs.isValid() and src_crs != wgs84) else ext
        if tr is None or not _is_valid_extent(tr):
            return ""
        lon = tr.center().x()
        lat = tr.center().y()
        def _dms(val, pos, neg):
            hemi = pos if val >= 0 else neg
            av = abs(val)
            d = int(av)
            m_f = (av - d) * 60.0
            m = int(m_f)
            s = int(round((m_f - m) * 60.0))
            if s == 60:
                s = 0; m += 1
            if m == 60:
                m = 0; d += 1
            return f"{d}&deg; {m:02d}&prime; {s:02d}&Prime; {hemi}"
        return f"{_dms(lat, 'N', 'S')} &middot; {_dms(lon, 'E', 'W')}"
    except Exception:
        return ""


@tool("Create a publication-grade themed print layout matching specific cartographic genres: 'tourist_map_a3', 'stepped_booklet_a3', 'retro_poster', 'poster_minimalist', 'figure_ground', 'blueprint_technical', 'small_multiples', 'transit_schematic', 'editorial_vertical', 'antique_historical', 'landscape_masterplan', 'scientific_dark', 'editorial_infographic', 'infographic_disaster', 'bento_dashboard', 'planning_official', 'dual_comparison', 'corridor_trail', 'cyberpunk_hud', 'nautical_bathymetric', 'natgeo', 'statistical_thematic', or 'relief_topographic'. Defaults to A4 (A3 for tourist_map_a3/stepped_booklet_a3).",
      {"name": S("string", "layout name"),
       "style_theme": S("string", "tourist_map_a3 | stepped_booklet_a3 | retro_poster | poster_minimalist | figure_ground | blueprint_technical | small_multiples | transit_schematic | editorial_vertical | antique_historical | landscape_masterplan | scientific_dark | editorial_infographic | infographic_disaster | bento_dashboard | planning_official | dual_comparison | corridor_trail | cyberpunk_hud | nautical_bathymetric | natgeo | statistical_thematic | relief_topographic | modern_clean"),
       "title": S("string", "main map title"),
       "subtitle": S("string", "subtitle or administrative region"),
       "credits": S("string", "mandatory real data source attribution"),
       "extent_layer": S("string", "main study area layer name"),
       "bbox": S("array", "optional explicit [xmin,ymin,xmax,ymax] in WGS84 to lock full map face", items={"type": "number"}),
       "key_extent_layer": S("string", "optional regional overview / second scenario extent layer"),
       "page_size": S("string", "A4|A3|A2, default A4"),
       "orientation": S("string", "landscape|portrait, default landscape"),
       "grid": S("boolean", "include coordinate grid (default false)"),
       "agency": S("string", "government agency or municipal department name")})
def create_styled_layout(name="Map", style_theme="planning_official", title="", subtitle="", credits="",
                         extent_layer=None, key_extent_layer=None, page_size=None, orientation="landscape",
                         grid=False, agency="", bbox=None, **kwargs):
    import html as _html
    import math
    from qgis.core import (
        QgsLayoutItemShape, QgsFillSymbol, QgsLayoutItemMap, QgsLayoutItemLabel,
        QgsLayoutItemLegend, QgsLayoutItemScaleBar, QgsLayoutItemPicture,
        QgsLayoutPoint, QgsLayoutSize, QgsLayoutMeasurement, QgsLayoutItemMapGrid,
        QgsLineSymbol, QgsLayoutItemMapOverview, QgsTextFormat
    )
    from qgis.PyQt.QtGui import QColor, QFont

    name = name or kwargs.get("layout_name") or kwargs.get("layout") or "Map"
    p = QgsProject.instance()
    _normalize_layer_order(p)
    kpis = _compute_project_kpis(p)
    mgr = p.layoutManager()
    old = mgr.layoutByName(name)
    if old:
        mgr.removeLayout(old)

    th = (style_theme or "planning_official").lower()
    is_tourist_map = th in ("tourist_map_a3", "tourist_map", "city_tourist_map", "travel_map", "guide_map")
    is_stepped_booklet = th in ("stepped_booklet_a3", "stepped_booklet", "booklet_a3", "folded_booklet", "stepped_tab")
    eff_page_size = page_size or ("A3" if (is_tourist_map or is_stepped_booklet) else "A4")

    lay = QgsPrintLayout(p)
    lay.initializeDefaults()
    lay.setName(name)
    ori = QgsLayoutItemPage.Orientation.Landscape if orientation == "landscape" else QgsLayoutItemPage.Orientation.Portrait
    page = lay.pageCollection().page(0)
    page.setPageSize(eff_page_size, ori)
    W, H = page.pageSize().width(), page.pageSize().height()
    mm = QgsUnitTypes.LayoutUnit.LayoutMillimeters if hasattr(QgsUnitTypes, "LayoutUnit") else Qgis.LayoutUnit.Millimeters

    is_landscape_masterplan = th in ("landscape_masterplan", "landscape_plan", "site_plan", "master_plan_landscape", "watercolor_plan", "eco_park", "landscape")
    is_planning = not is_landscape_masterplan and th in ("planning_official", "tata_ruang", "master_plan", "urban_planning", "zoning", "planning", "official")
    is_infographic = th in ("infographic_bencana", "infographic_disaster", "disaster", "hazard", "bencana")
    is_natgeo = th in ("natgeo", "national_geographic")
    is_statistical = th in ("statistical_thematic", "choropleth", "demographic", "socio_economic", "thematic")
    is_relief = th in ("relief_topographic", "relief", "swiss", "topographic")
    is_poster = th in ("poster", "poster_minimalist", "city_poster", "minimalist_poster", "art_poster", "minimalist")
    is_retro = th in ("retro", "retro_poster", "retro_minimalist", "vintage_poster", "vintage_minimalist")
    is_figure_ground = th in ("figure_ground", "nolli", "nolli_plan", "schwarzplan", "urban_morphology")
    is_blueprint = th in ("blueprint", "blueprint_technical", "cad_blueprint", "engineering_blueprint", "technical_blueprint")
    is_small_multiples = th in ("small_multiples", "multi_panel", "grid_comparison", "time_series_grid", "quad_comparison")
    is_transit = th in ("transit", "transit_schematic", "mobility", "isochrone", "accessibility", "public_transport")
    is_editorial_vertical = th in ("editorial_vertical", "vertical_report", "news_brief", "mobile_editorial", "ft_editorial", "nyt_editorial")
    is_scientific_dark = th in ("scientific_dark", "climate_risk", "dark_atlas", "scientific", "dark_mode", "dark")
    is_editorial = th in ("editorial", "magazine", "editorial_infographic", "infographic_magazine", "story")
    is_antique = th in ("antique", "antique_historical", "historical", "vintage", "old_map", "engraving")
    is_bento = th in ("bento", "bento_dashboard", "dashboard_bento", "modular_grid", "bento_grid", "apple_dashboard")
    is_dual = th in ("dual", "dual_comparison", "split_comparison", "before_after", "comparison", "side_by_side")
    is_corridor = th in ("corridor", "corridor_trail", "strip_map", "route_profile", "trail", "linear")
    is_cyberpunk = th in ("cyberpunk", "cyberpunk_hud", "synthwave", "neon_dark", "hud", "sci_fi", "neon")
    is_nautical = th in ("nautical", "nautical_bathymetric", "marine", "hydrographic", "admiralty")

    def _combined_data_extent():
        candidates = []
        for lyr in p.mapLayers().values():
            if lyr.isValid() and lyr.providerType() != "wms" and _is_valid_extent(lyr.extent()):
                tr = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
                if tr and _is_valid_extent(tr):
                    area = max(float(tr.width()) * float(tr.height()), 0.0)
                    candidates.append((area, tr))
        if not candidates:
            return None
        if len(candidates) >= 2:
            areas = sorted(a for a, _ in candidates)
            med_area = areas[len(areas) // 2]
            if med_area > 0:
                filtered = [tr for a, tr in candidates if a <= med_area * 2.2]
                if filtered:
                    candidates = [(0.0, tr) for tr in filtered]
        comb = None
        for _, tr in candidates:
            comb = QgsRectangle(tr) if comb is None else (comb.combineExtentWith(tr) or comb)
        return comb

    def _fit_extent_to_frame(mp_item, ext, cover=False):
        if ext is None or not _is_valid_extent(ext):
            return ext
        fw = mp_item.rect().width()
        fh = mp_item.rect().height()
        if cover and fw > 0 and fh > 0 and ext.width() > 0 and ext.height() > 0:
            frame_ratio = fw / fh
            data_ratio = ext.width() / ext.height()
            cx = ext.center().x()
            cy = ext.center().y()
            if data_ratio < frame_ratio:
                w = ext.width()
                h = w / frame_ratio
            else:
                h = ext.height()
                w = h * frame_ratio
            cov_ext = QgsRectangle(cx - w * 0.5, cy - h * 0.5, cx + w * 0.5, cy + h * 0.5)
            mp_item.setExtent(cov_ext)
            return cov_ext
        mp_item.zoomToExtent(ext)
        return ext

    def _setup_extent(mp_item, lyr_name, cover=None):
        has_basemap = any(l.isValid() and l.providerType() == "wms" for l in p.mapLayers().values())
        use_cover = (not has_basemap) if cover is None else bool(cover)
        if bbox:
            explicit_rect = _study_bbox_rect(p, bbox)
            if explicit_rect is not None:
                return _fit_extent_to_frame(mp_item, explicit_rect, cover=use_cover)
        if lyr_name:
            try:
                lyr = _layer(lyr_name)
                ext = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(lyr.extent())
                if not use_cover:
                    ext.scale(1.05)
                return _fit_extent_to_frame(mp_item, ext, cover=use_cover)
            except Exception:  # nosec B110
                pass
        study_rect = _study_bbox_rect(p, None)
        if study_rect is not None:
            return _fit_extent_to_frame(mp_item, study_rect, cover=use_cover)
        data_ext = _combined_data_extent()
        if data_ext is not None:
            if not use_cover:
                data_ext.scale(1.04)
            return _fit_extent_to_frame(mp_item, data_ext, cover=use_cover)
        canv = _canvas()
        if canv and hasattr(canv, "extent"):
            ext = canv.extent()
            return _fit_extent_to_frame(mp_item, ext, cover=use_cover)
        from qgis.core import QgsRectangle
        ext = QgsRectangle(-180, -90, 180, 90)
        mp_item.zoomToExtent(ext)
        return ext

    def _apply_grid(mp_item, ext, line_color="#94a3b8", text_color="#334155", font_family="Arial"):
        g = QgsLayoutItemMapGrid("grid", mp_item)
        g.setStyle(QgsLayoutItemMapGrid.GridStyle.Cross if (is_planning or is_relief) else QgsLayoutItemMapGrid.GridStyle.Solid)
        g.setCrossLength(1.5)
        map_crs = p.crs() if (p.crs() and p.crs().isValid()) else QgsCoordinateReferenceSystem("EPSG:4326")
        g.setCrs(map_crs)
        w_span = max(abs(ext.width()), 1.0)
        h_span = max(abs(ext.height()), 1.0)
        span = max(w_span, h_span)
        if map_crs.isGeographic():
            iv = max(round(span / 4, 3), 0.001)
            g.setIntervalX(iv)
            g.setIntervalY(iv)
            g.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.DegreeMinute)
            g.setAnnotationPrecision(1)
        else:
            raw_iv = max(span / 5, 10.0)
            mag = 10.0 ** math.floor(math.log10(max(raw_iv, 10.0)))
            clean_iv = max(round(raw_iv / mag) * mag, mag)
            g.setIntervalX(float(clean_iv))
            g.setIntervalY(float(clean_iv))
            g.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.Decimal)
            g.setAnnotationPrecision(0)
        g.setAnnotationEnabled(True)
        g.setAnnotationPosition(QgsLayoutItemMapGrid.AnnotationPosition.InsideMapFrame, QgsLayoutItemMapGrid.BorderSide.Left)
        g.setAnnotationPosition(QgsLayoutItemMapGrid.AnnotationPosition.InsideMapFrame, QgsLayoutItemMapGrid.BorderSide.Bottom)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.HideAll, QgsLayoutItemMapGrid.BorderSide.Top)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.HideAll, QgsLayoutItemMapGrid.BorderSide.Right)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.ShowAll, QgsLayoutItemMapGrid.BorderSide.Bottom)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.ShowAll, QgsLayoutItemMapGrid.BorderSide.Left)
        g.setAnnotationDirection(QgsLayoutItemMapGrid.AnnotationDirection.Vertical, QgsLayoutItemMapGrid.BorderSide.Left)
        if hasattr(g, "setAnnotationFrameDistance"):
            g.setAnnotationFrameDistance(1.5)
        g_fmt = QgsTextFormat()
        g_fmt.setFont(QFont(font_family, 6))
        g_fmt.setSize(6.0)
        g_fmt.setColor(QColor(text_color))
        if hasattr(g, "setAnnotationTextFormat"):
            g.setAnnotationTextFormat(g_fmt)
        g.setLineSymbol(QgsLineSymbol.createSimple({"color": line_color, "width": "0.15", "opacity": "0.55"}))
        mp_item.grids().addGrid(g)

    def _add_divider(rx, cy, rw):
        div = QgsLayoutItemShape(lay)
        div.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        div.setSymbol(QgsFillSymbol.createSimple({"color": "#cbd5e1", "outline_color": "transparent"}))
        lay.addLayoutItem(div)
        div.attemptMove(QgsLayoutPoint(rx, cy, mm))
        div.attemptResize(QgsLayoutSize(rw, 0.4, mm))

    def _create_clean_scalebar(target_map, style=None, position="bottom-left", pad=5.0,
                               custom_x=None, custom_y=None, max_width=42.0, font_family="Arial",
                               font_size=6.5, color="#1e293b", secondary_color="#ffffff",
                               background="#ffffff", frame_color=None, transparent=False):
        sb = QgsLayoutItemScaleBar(lay)
        sb.setStyle(style or "Stepped Line")
        sb.setLinkedMap(target_map)

        scale = target_map.scale()
        du = Qgis.DistanceUnit if hasattr(Qgis, "DistanceUnit") else None
        units_obj = du.Meters if (du and scale < 15000) else (du.Kilometers if du else None)
        label_str = "m" if scale < 15000 else "km"
        if units_obj is not None:
            sb.applyDefaultSize(units_obj)
        else:
            sb.applyDefaultSize()
        sb.setUnitLabel(label_str)
        sb.setNumberOfSegments(2)
        sb.setNumberOfSegmentsLeft(0)
        sb.setHeight(2.0)
        sb.setBoxContentSpace(1.2)
        sb.setLabelBarSpace(1.0)

        if hasattr(sb, "setSegmentSizeMode") and hasattr(Qgis, "ScaleBarSegmentSizeMode"):
            sb.setSegmentSizeMode(Qgis.ScaleBarSegmentSizeMode.FitWidth)
            if hasattr(sb, "setMaximumBarWidth"):
                sb.setMaximumBarWidth(float(max_width))
            if hasattr(sb, "setMinimumBarWidth"):
                sb.setMinimumBarWidth(max(18.0, float(max_width) * 0.5))

        fmt_s = QgsTextFormat()
        f_sb = QFont(font_family, int(font_size))
        fmt_s.setFont(f_sb)
        fmt_s.setSize(font_size)
        fmt_s.setColor(QColor(color))
        sb.setTextFormat(fmt_s)

        if hasattr(sb, "fillSymbol") and sb.fillSymbol():
            fs = sb.fillSymbol().clone()
            fs.setColor(QColor(color))
            sb.setFillSymbol(fs)
        if hasattr(sb, "alternateFillSymbol") and sb.alternateFillSymbol():
            afs = sb.alternateFillSymbol().clone()
            afs.setColor(QColor(secondary_color))
            sb.setAlternateFillSymbol(afs)
        if hasattr(sb, "lineSymbol") and sb.lineSymbol():
            ls = sb.lineSymbol().clone()
            ls.setColor(QColor(color))
            ls.setWidth(0.25)
            sb.setLineSymbol(ls)

        if transparent or background is None or str(background).lower() == "none":
            sb.setBackgroundEnabled(False)
            sb.setFrameEnabled(False)
        else:
            sb.setBackgroundEnabled(True)
            sb.setBackgroundColor(QColor(background))
            if frame_color and str(frame_color).lower() != "none":
                sb.setFrameEnabled(True)
                sb.setFrameStrokeColor(QColor(frame_color))
                sb.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, mm))
            else:
                sb.setFrameEnabled(False)

        lay.addLayoutItem(sb)
        sb.update()

        sb_sz = sb.sizeWithUnits()
        sb_w = sb_sz.width()
        sb_h = sb_sz.height()

        mpos = target_map.pos()
        msz = target_map.rect()
        mx, my = mpos.x(), mpos.y()
        mw, mh = msz.width(), msz.height()

        if custom_x is not None and custom_y is not None:
            tx, ty = float(custom_x), float(custom_y)
        elif position == "bottom-right":
            tx = mx + mw - sb_w - pad
            ty = my + mh - sb_h - pad
        elif position == "top-left":
            tx = mx + pad
            ty = my + pad
        elif position == "top-right":
            tx = mx + mw - sb_w - pad
            ty = my + pad
        else:  # bottom-left
            tx = mx + pad
            ty = my + mh - sb_h - pad

        # Safety clamp: never protrude or cross outside the map neatline
        if mx <= tx <= mx + mw and ty + sb_h > my + mh:
            ty = max(my + pad, my + mh - sb_h - pad)
        if mx <= tx <= mx + mw and tx + sb_w > mx + mw:
            tx = max(mx + pad, mx + mw - sb_w - pad)

        sb.attemptMove(QgsLayoutPoint(tx, ty, mm))
        sb.update()
        return sb

    # =========================================================================
    # THEME 1: PLANNING OFFICIAL (Formal Kop Peta Layout)
    # =========================================================================
    if is_planning:
        m = 7.0
        neat_m = 3.5
        neat = QgsLayoutItemShape(lay)
        neat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        neat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#1e293b", "outline_width": "0.6"}))
        lay.addLayoutItem(neat)
        neat.attemptMove(QgsLayoutPoint(neat_m, neat_m, mm))
        neat.attemptResize(QgsLayoutSize(W - 2 * neat_m, H - 2 * neat_m, mm))

        right_w = round(W * 0.26)
        map_w = W - 2 * m - right_w - 2.0
        map_h = H - 2 * m
        right_x = m + map_w + 2.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#1e293b"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))
        _apply_grid(mp, main_ext)

        # Kop Peta Container
        kop = QgsLayoutItemShape(lay)
        kop.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        kop.setSymbol(QgsFillSymbol.createSimple({"color": "#ffffff", "outline_color": "#1e293b", "outline_width": "0.4"}))
        lay.addLayoutItem(kop)
        kop.attemptMove(QgsLayoutPoint(right_x, m, mm))
        kop.attemptResize(QgsLayoutSize(right_w, map_h, mm))

        curr_y = m
        agency_name = agency or "PEMERINTAH KOTA / KABUPATEN"
        lbl_agency = QgsLayoutItemLabel(lay)
        lbl_agency.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_agency.setText(
            f"<div style='text-align:center; font-family:Arial, sans-serif; padding:4px 2px;'>"
            f"<div style='font-size:8px; font-weight:bold; color:#0f172a; text-transform:uppercase;'>{_html.escape(agency_name)}</div>"
            f"<div style='font-size:6.5px; color:#64748b; margin-top:1px;'>BIDANG PENATAAN RUANG & TATA WILAYAH</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_agency)
        agency_h = 13.0
        lbl_agency.attemptMove(QgsLayoutPoint(right_x, curr_y, mm))
        lbl_agency.attemptResize(QgsLayoutSize(right_w, agency_h, mm))
        curr_y += agency_h

        _add_divider(right_x, curr_y, right_w)
        curr_y += 0.5

        lbl_title = QgsLayoutItemLabel(lay)
        lbl_title.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_html = f"<div style='font-size:8pt; color:#475569; margin-top:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_title.setText(
            f"<div style='text-align:center; font-family:Arial, sans-serif; padding:4px 3px;'>"
            f"<div style='font-size:10.5pt; font-weight:bold; color:#0f172a; letter-spacing:0.3px;'>{_html.escape(title or 'PETA RENCANA TATA RUANG')}</div>"
            f"{sub_html}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_title)
        title_h = 16.0 if subtitle else 12.0
        lbl_title.attemptMove(QgsLayoutPoint(right_x, curr_y, mm))
        lbl_title.attemptResize(QgsLayoutSize(right_w, title_h, mm))
        curr_y += title_h

        _add_divider(right_x, curr_y, right_w)
        curr_y += 0.5

        # North Arrow & Scale Bar Side-by-side
        arrow_w = 11.0
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptMove(QgsLayoutPoint(right_x + 3.0, curr_y + 1.0, mm))
        na.attemptResize(QgsLayoutSize(arrow_w, 14.0, mm))

        # Single Box scale bar bounded strictly within Kop Peta width (never overflows)
        sb_max_w = max(25.0, right_w - arrow_w - 7.0)
        _create_clean_scalebar(mp, style="Single Box", custom_x=right_x + arrow_w + 3.0,
                               custom_y=curr_y + 1.5, max_width=sb_max_w, color="#0f172a",
                               background="#ffffff", frame_color=None)
        curr_y += 16.0

        _add_divider(right_x, curr_y, right_w)
        curr_y += 0.5

        # Key Inset Map (if key_extent_layer provided and room exists)
        if key_extent_layer and curr_y < H - m - 75:
            key_h = 32.0
            lbl_key = QgsLayoutItemLabel(lay)
            lbl_key.setText("PETA SITUASI / INSET:")
            f_key = QFont("Arial", 6)
            f_key.setBold(True)
            fmt_k = QgsTextFormat()
            fmt_k.setFont(f_key)
            lbl_key.setTextFormat(fmt_k)
            lay.addLayoutItem(lbl_key)
            lbl_key.attemptMove(QgsLayoutPoint(right_x + 2.0, curr_y + 1.0, mm))
            lbl_key.attemptResize(QgsLayoutSize(right_w - 4.0, 4.0, mm))
            curr_y += 5.0

            kmp = QgsLayoutItemMap(lay)
            lay.addLayoutItem(kmp)
            kmp.setCrs(p.crs())
            kmp.attemptMove(QgsLayoutPoint(right_x + 2.0, curr_y, mm))
            kmp.attemptResize(QgsLayoutSize(right_w - 4.0, key_h - 6.0, mm))
            _setup_extent(kmp, key_extent_layer)
            kmp.setFrameEnabled(True)
            kmp.setFrameStrokeColor(QColor("#94a3b8"))
            kmp.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, mm))

            ov = QgsLayoutItemMapOverview("MainMapExt", kmp)
            ov.setLinkedMap(mp)
            ov.setFrameFillColor(QColor(220, 38, 38, 40))
            ov.setFrameOutlineColor(QColor(220, 38, 38, 220))
            kmp.overviews().addOverview(ov)

            curr_y += key_h - 1.0
            _add_divider(right_x, curr_y, right_w)
            curr_y += 0.5

        # Legend Box
        lbl_leg = QgsLayoutItemLabel(lay)
        lbl_leg.setText("LEGENDA / SIMBOLOGI:")
        f_leg = QFont("Arial", 7)
        f_leg.setBold(True)
        fmt_l = QgsTextFormat()
        fmt_l.setFont(f_leg)
        lbl_leg.setTextFormat(fmt_l)
        lay.addLayoutItem(lbl_leg)
        lbl_leg.attemptMove(QgsLayoutPoint(right_x + 2.0, curr_y + 1.0, mm))
        lbl_leg.attemptResize(QgsLayoutSize(right_w - 4.0, 4.0, mm))
        curr_y += 5.0

        leg_avail_h = max(25.0, (H - m - 26.0) - curr_y)
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        leg.setTitle("")
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=10)
        leg.attemptMove(QgsLayoutPoint(right_x + 1.0, curr_y, mm))
        leg.attemptResize(QgsLayoutSize(right_w - 2.0, leg_avail_h, mm))
        leg.setBackgroundColor(QColor(255, 255, 255, 0))

        # Technical Metadata block
        meta_y = H - m - 24.0
        _add_divider(right_x, meta_y, right_w)
        lbl_meta = QgsLayoutItemLabel(lay)
        lbl_meta.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        crs_auth = p.crs().authid()
        lbl_meta.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6.5px; color:#475569; padding:2px 3px; line-height:1.2;'>"
            f"<b>SISTEM KOORDINAT & PROYEKSI:</b><br>"
            f"Sistem Grid: Universal Transverse Mercator (UTM)<br>"
            f"Datum: WGS 84 · CRS: {crs_auth}<br>"
            f"<b>SUMBER DATA:</b> {_html.escape(credits or 'Portal Resmi Pemerintah, OSM, Open Data')}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_meta)
        lbl_meta.attemptMove(QgsLayoutPoint(right_x, meta_y + 0.5, mm))
        lbl_meta.attemptResize(QgsLayoutSize(right_w, 14.0, mm))

        # Approval Block
        appr_y = H - m - 9.5
        _add_divider(right_x, appr_y, right_w)
        lbl_appr = QgsLayoutItemLabel(lay)
        lbl_appr.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_appr.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6px; color:#64748b; text-align:center; padding-top:2px;'>"
            f"DIPERIKSA & DISETUJUI OLEH:<br>"
            f"<span style='font-size:6.5px; font-weight:bold; color:#1e293b;'>PEJABAT PEMBINA TATA RUANG DAERAH</span>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_appr)
        lbl_appr.attemptMove(QgsLayoutPoint(right_x, appr_y + 0.5, mm))
        lbl_appr.attemptResize(QgsLayoutSize(right_w, 9.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
                "kop_peta_rect": [right_x, m, right_w, map_h]}

    # =========================================================================
    # THEME 2: NATIONAL GEOGRAPHIC CLASSIC (Parchment, Serif, Double Neatline)
    # =========================================================================
    if is_natgeo:
        m = 10.0
        page.setBackgroundColor(QColor("#fcfbf7"))

        # Double Neatline
        out_neat = QgsLayoutItemShape(lay)
        out_neat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        out_neat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#263238", "outline_width": "0.8"}))
        lay.addLayoutItem(out_neat)
        out_neat.attemptMove(QgsLayoutPoint(m - 3.0, m - 3.0, mm))
        out_neat.attemptResize(QgsLayoutSize(W - 2 * m + 6.0, H - 2 * m + 6.0, mm))

        in_neat = QgsLayoutItemShape(lay)
        in_neat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        in_neat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#263238", "outline_width": "0.25"}))
        lay.addLayoutItem(in_neat)
        in_neat.attemptMove(QgsLayoutPoint(m - 1.5, m - 1.5, mm))
        in_neat.attemptResize(QgsLayoutSize(W - 2 * m + 3.0, H - 2 * m + 3.0, mm))

        # Centered Classic Header Card
        header_h = 20.0 if title else 0
        if title:
            _add_layout_card_shape(lay, m + 20.0, m, W - 2 * m - 40.0, header_h, fill="#fbf9f4", stroke="#263238", stroke_w=0.3, radius_mm=0.0)
            header_card = QgsLayoutItemLabel(lay)
            header_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            sub_txt = f"<div style='font-size:9.5pt; font-style:italic; color:#374151; margin-top:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
            header_card.setText(
                f"<div style='font-family:Georgia, serif; text-align:center;'>"
                f"<div style='font-size:14pt; font-weight:bold; color:#1a1a1a; letter-spacing:1px; text-transform:uppercase;'>{_html.escape(title)}</div>"
                f"{sub_txt}"
                f"</div>"
            )
            lay.addLayoutItem(header_card)
            header_card.attemptMove(QgsLayoutPoint(m + 22.0, m + 2.0, mm))
            header_card.attemptResize(QgsLayoutSize(W - 2 * m - 44.0, header_h - 4.0, mm))

        map_top = m + (header_h + 3.0 if title else 0)
        map_w = W - 2 * m
        map_h = H - map_top - m - 6.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#263238"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))
        if grid:
            _apply_grid(mp, main_ext)

        # Floating framed legend inside map
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="LEGEND", font_family="Georgia", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=8)
        leg.setBackgroundColor(QColor("#fcfbf7"))
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor("#263238"))
        leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))
        leg.attemptMove(QgsLayoutPoint(m + map_w - 65.0, map_top + map_h - 60.0, mm))
        leg.attemptResize(QgsLayoutSize(60.0, 55.0, mm))

        # Scale bar with classic cartographic Double Box & parchment fill
        _create_clean_scalebar(mp, style="Double Box", position="bottom-left", pad=6.0,
                               font_family="Georgia", font_size=6.5, color="#263238", secondary_color="#fcfbf7",
                               background="#fcfbf7", frame_color="#263238")

        # North Arrow
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(12.0, 16.0, mm))
        na.attemptMove(QgsLayoutPoint(m + 6.0, map_top + 4.0, mm))

        # Credits
        if credits:
            lbl_cred = QgsLayoutItemLabel(lay)
            lbl_cred.setText(f"National Geographic Cartography Style · Sources: {credits}")
            fmt = QgsTextFormat()
            fmt.setFont(QFont("Georgia", 7))
            fmt.setColor(QColor("#374151"))
            lbl_cred.setTextFormat(fmt)
            lay.addLayoutItem(lbl_cred)
            lbl_cred.attemptMove(QgsLayoutPoint(m, H - m - 4.0, mm))
            lbl_cred.attemptResize(QgsLayoutSize(W - 2 * m, 5.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 3: STATISTICAL THEMATIC (Choropleth & Socio-Economic Atlas)
    # =========================================================================
    if is_statistical:
        m = 8.0
        header_h = 16.0 if title else 0
        if title:
            header_card = QgsLayoutItemLabel(lay)
            header_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            sub_txt = f"<span style='font-size:9pt; color:#64748b; font-weight:normal; margin-left:8px;'>{_html.escape(subtitle)}</span>" if subtitle else ""
            header_card.setText(
                f"<div style='font-family:Arial, sans-serif; padding:2px 4px; border-bottom:1.5px solid #0f172a;'>"
                f"<span style='font-size:13pt; font-weight:bold; color:#0f172a;'>{_html.escape(title)}</span>{sub_txt}"
                f"</div>"
            )
            lay.addLayoutItem(header_card)
            header_card.attemptMove(QgsLayoutPoint(m, m, mm))
            header_card.attemptResize(QgsLayoutSize(W - 2 * m, header_h, mm))

        right_w = round(W * 0.24)
        map_w = W - 2 * m - right_w - 3.0
        map_top = m + (header_h + 3.0 if title else 0)
        map_h = H - map_top - m - 6.0
        right_x = m + map_w + 3.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#cbd5e1"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        # Right Statistical Sidecard
        _add_layout_card_shape(lay, right_x, map_top, right_w, 22.0, fill="#f8fafc", stroke="#e2e8f0", stroke_w=0.25, radius_mm=1.5)
        stat_card = QgsLayoutItemLabel(lay)
        stat_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        stat_card.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"<div style='font-size:7.5pt; font-weight:bold; color:#1e293b; text-transform:uppercase;'>{_html.escape(kpis['kpi1_label'])}</div>"
            f"<div style='font-size:12pt; font-weight:bold; color:#0284c7; margin-top:1px;'>{_html.escape(kpis['kpi1_value'])}</div>"
            f"<div style='font-size:6.5pt; color:#64748b; margin-top:1px;'>{kpis['kpi2_label']}: {_html.escape(kpis['kpi2_value'])}</div>"
            f"</div>"
        )
        lay.addLayoutItem(stat_card)
        stat_card.attemptMove(QgsLayoutPoint(right_x + 2.5, map_top + 2.0, mm))
        stat_card.attemptResize(QgsLayoutSize(right_w - 5.0, 18.0, mm))

        # Thematic Legend
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="LEGEND", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=10)
        leg.attemptMove(QgsLayoutPoint(right_x, map_top + 25.0, mm))
        leg.attemptResize(QgsLayoutSize(right_w, map_h - 27.0, mm))

        # Scale bar inside map with clean pill badge
        _create_clean_scalebar(mp, style="Stepped Line", position="bottom-left", pad=5.0,
                               color="#0f172a", background="#ffffff", frame_color=None)

        # Credits
        if credits:
            lbl_cred = QgsLayoutItemLabel(lay)
            lbl_cred.setText(f"Data Sources: {credits} | Cartography: QGIS Copilot")
            fmt = QgsTextFormat()
            fmt.setSize(7.0)
            fmt.setColor(QColor("#64748b"))
            lbl_cred.setTextFormat(fmt)
            lay.addLayoutItem(lbl_cred)
            lbl_cred.attemptMove(QgsLayoutPoint(m, H - m - 4.0, mm))
            lbl_cred.attemptResize(QgsLayoutSize(W - 2 * m, 5.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
                "right_panel_rect": [right_x, map_top, right_w, map_h]}

    # =========================================================================
    # THEME 4: DISASTER INFOGRAPHIC DASHBOARD (Situational Crisis / Alert)
    # =========================================================================
    if is_infographic:
        m = 7.0
        # Sleek Alert Header Band (13mm height, clean and responsive)
        header_h = 13.0
        head_shape = QgsLayoutItemShape(lay)
        head_shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        head_shape.setSymbol(QgsFillSymbol.createSimple({"color": "#0f172a", "outline_color": "transparent"}))
        lay.addLayoutItem(head_shape)
        head_shape.attemptMove(QgsLayoutPoint(m, m, mm))
        head_shape.attemptResize(QgsLayoutSize(W - 2 * m, header_h, mm))

        lbl_head = QgsLayoutItemLabel(lay)
        lbl_head.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_html = f"<span style='font-size:8pt; color:#94a3b8; margin-left:8px; font-weight:normal;'>{_html.escape(subtitle)}</span>" if subtitle else ""
        lbl_head.setText(
            f"<div style='font-family:Arial, sans-serif; padding:2px 6px; line-height:1.2;'>"
            f"<span style='background:#ef4444; color:#ffffff; font-size:6.5pt; font-weight:bold; padding:1px 4px;'>SPATIAL SITUATION BRIEFING</span><br>"
            f"<span style='font-size:11pt; font-weight:bold; color:#ffffff;'>{_html.escape(title or 'DISASTER & HAZARD SITUATION MAP')}</span>{sub_html}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_head)
        lbl_head.attemptMove(QgsLayoutPoint(m, m, mm))
        lbl_head.attemptResize(QgsLayoutSize(W - 2 * m, header_h, mm))

        right_w = 68.0
        credit_h = 5.0
        map_top = m + header_h + 2.0
        map_w = W - 2 * m - right_w - 2.5
        map_h = H - map_top - m - credit_h - 2.0
        right_x = m + map_w + 2.5

        # Hero Map Frame (fills 76% of page width, spacious and clean)
        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#334155"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.35, mm))

        # Right Column: Live KPI Cards Panel
        card_h = 18.0
        info_cards = [
            (kpis["kpi1_label"], kpis["kpi1_value"], kpis["kpi1_sub"], "#dc2626"),
            (kpis["kpi2_label"], kpis["kpi2_value"], kpis["kpi2_sub"], "#ea580c"),
            ("LAYER INVENTORY", f"{kpis['layer_count']} Active", f"Points: {kpis['point_features']:,} &bull; Lines: {kpis['line_features']:,}", "#0284c7"),
        ]
        curr_ry = map_top
        for i, (kpi_title, kpi_val, kpi_sub, kpi_color) in enumerate(info_cards):
            cy = curr_ry + i * (card_h + 2.0)
            _add_layout_card_shape(lay, right_x, cy, right_w, card_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.2)
            _add_layout_card_shape(lay, right_x, cy, 1.3, card_h, fill=kpi_color, stroke=None, radius_mm=0.0)

            c_lbl = QgsLayoutItemLabel(lay)
            c_lbl.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            c_lbl.setText(
                f"<div style='font-family:Arial, sans-serif; line-height:1.2;'>"
                f"<div style='font-size:6.5pt; font-weight:bold; color:#475569; text-transform:uppercase;'>{_html.escape(kpi_title)}</div>"
                f"<div style='font-size:11pt; font-weight:bold; color:#0f172a; margin-top:1px;'>{_html.escape(kpi_val)}</div>"
                f"<div style='font-size:6pt; color:#64748b;'>{kpi_sub}</div>"
                f"</div>"
            )
            lay.addLayoutItem(c_lbl)
            c_lbl.attemptMove(QgsLayoutPoint(right_x + 3.0, cy + 1.8, mm))
            c_lbl.attemptResize(QgsLayoutSize(right_w - 5.0, card_h - 3.6, mm))

        curr_ry += len(info_cards) * (card_h + 2.0)

        # Right Column: Thematic Legend Box
        leg_h = map_top + map_h - curr_ry
        if leg_h > 30.0:
            _add_layout_card_shape(lay, right_x, curr_ry, right_w, leg_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.2)

            leg = QgsLayoutItemLegend(lay)
            leg.setLinkedMap(mp)
            lay.addLayoutItem(leg)
            _configure_clean_legend(leg, mp, lay, title="MAP LEGEND", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=10)
            leg.setBackgroundColor(QColor(255, 255, 255, 0))
            leg.setFrameEnabled(False)
            leg.attemptMove(QgsLayoutPoint(right_x + 2.0, curr_ry + 1.5, mm))
            leg.attemptResize(QgsLayoutSize(right_w - 4.0, leg_h - 3.0, mm))

        # Bottom Credit & Technical Metadata Footer
        foot_y = map_top + map_h + 1.5
        lbl_cred = QgsLayoutItemLabel(lay)
        lbl_cred.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        crs_str = kpis["crs_authid"]
        lbl_cred.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6.8pt; color:#64748b; line-height:1.2;'>"
            f"<b>Data Sources:</b> {_html.escape(credits or 'Official Government Agencies, OpenStreetMap, Satellite Imagery')} &nbsp;&bull;&nbsp; "
            f"<b>CRS:</b> {crs_str} &nbsp;&bull;&nbsp; <b>Cartography:</b> QGIS Copilot"
            f"</div>"
        )
        lay.addLayoutItem(lbl_cred)
        lbl_cred.attemptMove(QgsLayoutPoint(m, foot_y, mm))
        lbl_cred.attemptResize(QgsLayoutSize(W - 2 * m, credit_h, mm))

        # Scale bar & North arrow inside map with high-visibility Stepped Line
        _create_clean_scalebar(mp, style="Stepped Line", position="bottom-left", pad=6.0,
                               color="#0f172a", background="#ffffff", frame_color=None)

        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(10.0, 14.0, mm))
        na.attemptMove(QgsLayoutPoint(m + map_w - 14.0, map_top + 3.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
                "right_panel_rect": [right_x, map_top, right_w, map_h]}

    # =========================================================================
    # THEME 4B: LANDSCAPE ARCHITECTURE & WATERCOLOR MASTER PLAN
    # =========================================================================
    if is_landscape_masterplan:
        page.setBackgroundColor(QColor("#fcfbf7"))
        m = 8.0
        map_w = W - 2 * m
        map_h = H - 2 * m

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#334155"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))

        # Floating Architectural Title Stamp in negative space (top-right)
        stamp_w = 80.0
        stamp_h = 25.0
        _add_layout_card_shape(lay, W - m - stamp_w - 5.0, m + 5.0, stamp_w, stamp_h, fill="#ffffff", stroke="#94a3b8", stroke_w=0.25, radius_mm=1.2)
        lbl_stamp = QgsLayoutItemLabel(lay)
        lbl_stamp.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        plan_title = title or "SITE MASTER PLAN"
        plan_sub = subtitle or "Landscape & Environmental Architecture"
        lbl_stamp.setText(
            f"<div style='font-family:Century Gothic, Helvetica, Arial, sans-serif;'>"
            f"<div style='font-size:6.5pt; font-weight:bold; color:#059669; letter-spacing:1.2px; text-transform:uppercase;'>MASTER PLAN & DESIGN PROPOSAL</div>"
            f"<div style='font-size:11.5pt; font-weight:bold; color:#1e293b; text-transform:uppercase; margin-top:2px;'>{_html.escape(plan_title)}</div>"
            f"<div style='font-size:7.5pt; color:#64748b; margin-top:2px;'>{_html.escape(plan_sub)}</div>"
            f"<div style='font-size:6pt; color:#94a3b8; margin-top:2px;'>CRS: {kpis['crs_authid']} &bull; {_html.escape(credits or 'Survey & Planning Bureau')}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_stamp)
        lbl_stamp.attemptMove(QgsLayoutPoint(W - m - stamp_w - 2.5, m + 7.0, mm))
        lbl_stamp.attemptResize(QgsLayoutSize(stamp_w - 5.0, stamp_h - 4.0, mm))

        # Floating Clean Legend Badge (bottom-right)
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="LEGEND", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=10)
        leg.setBackgroundColor(QColor(255, 255, 255, 235))
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor("#cbd5e1"))
        leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, mm))
        leg.attemptMove(QgsLayoutPoint(W - m - 65.0, H - m - 58.0, mm))
        leg.attemptResize(QgsLayoutSize(60.0, 52.0, mm))

        # Delicate Architectural Line Ticks Up scale bar (bottom-left)
        _create_clean_scalebar(mp, style="Line Ticks Up", position="bottom-left", pad=6.0,
                               color="#1e3a2f", background="rgba(255,255,255,0.85)", frame_color=None)

        # Minimalist North Arrow
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(10.0, 14.0, mm))
        na.attemptMove(QgsLayoutPoint(m + 6.0, m + 6.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 5A: RETRO / VINTAGE MINIMALIST CITY ART POSTER (Parchment + Gradient Vignette)
    # =========================================================================
    if is_retro:
        bg_hex = "#f5efe0"
        ink_hex = "#2c241b"
        _add_layout_card_shape(lay, 0, 0, W, H, fill=bg_hex, stroke="transparent", stroke_w=0, radius_mm=0)
        m = 7.5
        # Thin double neatline around outer mat
        outer_mat = QgsLayoutItemShape(lay)
        outer_mat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        outer_mat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": ink_hex, "outline_width": "0.45"}))
        lay.addLayoutItem(outer_mat)
        outer_mat.attemptMove(QgsLayoutPoint(m - 2.2, m - 2.2, mm))
        outer_mat.attemptResize(QgsLayoutSize(W - 2 * m + 4.4, H - 2 * m + 4.4, mm))

        title_block_h = 26.0
        map_w = W - 2 * m
        map_h = H - 2 * m - title_block_h - 1.5

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor(bg_hex))
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer, cover=True)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor(ink_hex))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))

        # Smooth 4-edge + radial gradient vignette mask over map frame
        if kwargs.get("vignette", True):
            vig_svg = _build_vignette_svg(map_w, map_h, bg_hex=bg_hex)
            vig = QgsLayoutItemPicture(lay)
            vig.setPicturePath(vig_svg)
            vig.setResizeMode(QgsLayoutItemPicture.ResizeMode.Stretch)
            lay.addLayoutItem(vig)
            vig.attemptMove(QgsLayoutPoint(m, m, mm))
            vig.attemptResize(QgsLayoutSize(map_w, map_h, mm))

        # Clean Retro Footer Typography Block (tracked uppercase + DMS coords + 1-line credits)
        lbl_ret = QgsLayoutItemLabel(lay)
        lbl_ret.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        city_title = title or "CITY MAP"
        region_sub = subtitle or "HISTORIC URBAN FABRIC"
        dms_txt = _format_dms_center(main_ext, p.crs(), p)
        crs_code = p.crs().authid() if p.crs().isValid() else ""
        cred_parts = [pt for pt in [credits, crs_code] if pt]
        cred_line = " &middot; ".join(_html.escape(pt) for pt in cred_parts)
        lbl_ret.setText(
            f"<div style='text-align:center; font-family:Georgia, \"Times New Roman\", serif; padding-top:2px;'>"
            f"<div style='font-size:17pt; font-weight:bold; color:{ink_hex}; letter-spacing:4px; text-transform:uppercase;'>{_html.escape(city_title)}</div>"
            f"<div style='font-size:8pt; font-weight:bold; color:#5c4d3c; letter-spacing:2.5px; text-transform:uppercase; margin-top:2px;'>{_html.escape(region_sub)}</div>"
            + (f"<div style='font-size:6.8pt; color:#6b5c4b; letter-spacing:1.2px; margin-top:2px;'>{dms_txt}</div>" if dms_txt else "")
            + (f"<div style='font-size:5.8pt; color:#8a7b68; margin-top:2px;'>{cred_line}</div>" if cred_line else "")
            + f"</div>"
        )
        lay.addLayoutItem(lbl_ret)
        lbl_ret.attemptMove(QgsLayoutPoint(m, m + map_h + 2.0, mm))
        lbl_ret.attemptResize(QgsLayoutSize(map_w, title_block_h, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(), "vignette": bool(kwargs.get("vignette", True))}

    # =========================================================================
    # THEME 5: MINIMALIST CITY ART POSTER (Full-Bleed, Modern Floating Typography)
    # =========================================================================
    if is_poster:
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#fafaf9", stroke="transparent", stroke_w=0, radius_mm=0)
        m = 10.0
        # Clean modern outer mat border
        mat = QgsLayoutItemShape(lay)
        mat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        mat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#0f172a", "outline_width": "0.6"}))
        lay.addLayoutItem(mat)
        mat.attemptMove(QgsLayoutPoint(m - 3.0, m - 3.0, mm))
        mat.attemptResize(QgsLayoutSize(W - 2 * m + 6.0, H - 2 * m + 6.0, mm))

        title_block_h = 28.0
        map_w = W - 2 * m
        map_h = H - 2 * m - title_block_h - 2.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor("#f8fafc"))
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer, cover=True)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#0f172a"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))

        if kwargs.get("vignette"):
            vig_svg = _build_vignette_svg(map_w, map_h, bg_hex="#fafaf9")
            vig = QgsLayoutItemPicture(lay)
            vig.setPicturePath(vig_svg)
            vig.setResizeMode(QgsLayoutItemPicture.ResizeMode.Stretch)
            lay.addLayoutItem(vig)
            vig.attemptMove(QgsLayoutPoint(m, m, mm))
            vig.attemptResize(QgsLayoutSize(map_w, map_h, mm))

        if kwargs.get("scalebar"):
            _create_clean_scalebar(mp, style="Line Ticks Down", position="bottom-left", pad=5.0,
                                   color="#0f172a", background="rgba(250,250,249,0.8)", frame_color=None)
        if kwargs.get("north_arrow"):
            na = QgsLayoutItemPicture(lay)
            na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
            na.setLinkedMap(mp)
            lay.addLayoutItem(na)
            na.attemptResize(QgsLayoutSize(8.0, 11.0, mm))
            na.attemptMove(QgsLayoutPoint(m + map_w - 12.0, m + 4.0, mm))

        # Floating Art Poster Typography Block
        lbl_art = QgsLayoutItemLabel(lay)
        lbl_art.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        city_title = title or "CITY MAP"
        region_sub = subtitle or "URBAN NETWORK"
        crs_code = p.crs().authid() if p.crs().isValid() else ""
        coord_txt = f"{crs_code} &bull; {_html.escape(credits)}" if credits else crs_code
        lbl_art.setText(
            f"<div style='text-align:center; font-family:Century Gothic, Helvetica Neue, Arial, sans-serif; padding-top:2px;'>"
            f"<div style='font-size:22pt; font-weight:bold; color:#0f172a; letter-spacing:4px; text-transform:uppercase;'>{_html.escape(city_title)}</div>"
            f"<div style='font-size:9.5pt; font-weight:bold; color:#64748b; letter-spacing:2px; text-transform:uppercase; margin-top:2px;'>{_html.escape(region_sub)}</div>"
            f"<div style='font-size:6.8pt; color:#94a3b8; letter-spacing:1px; margin-top:3px;'>{coord_txt}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_art)
        lbl_art.attemptMove(QgsLayoutPoint(m, m + map_h + 3.0, mm))
        lbl_art.attemptResize(QgsLayoutSize(map_w, title_block_h, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 5B: FIGURE-GROUND / NOLLI PLAN (Architectural Urban Morphology)
    # =========================================================================
    if is_figure_ground:
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#ffffff", stroke="transparent", stroke_w=0, radius_mm=0)
        m = 10.0
        footer_h = 20.0
        map_w = W - 2 * m
        map_h = H - 2 * m - footer_h - 2.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor("#ffffff"))
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer, cover=True)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#111111"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.35, mm))

        if kwargs.get("scalebar", True):
            _create_clean_scalebar(mp, style="Line Ticks Down", position="bottom-left", pad=5.0,
                                   font_size=6.5, color="#111111", background="rgba(255,255,255,0.88)", frame_color=None)
        if kwargs.get("north_arrow", True):
            na = QgsLayoutItemPicture(lay)
            na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
            na.setLinkedMap(mp)
            lay.addLayoutItem(na)
            na.attemptResize(QgsLayoutSize(7.5, 10.5, mm))
            na.attemptMove(QgsLayoutPoint(m + map_w - 11.5, m + 4.0, mm))

        lbl_fg_l = QgsLayoutItemLabel(lay)
        lbl_fg_l.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        fg_title = title or "FIGURE-GROUND PLAN"
        fg_sub = subtitle or "Urban Solid & Void Morphology"
        lbl_fg_l.setText(
            f"<div style='font-family:Helvetica Neue, Arial, sans-serif;'>"
            f"<div style='font-size:13.5pt; font-weight:bold; letter-spacing:2.5px; color:#111111; text-transform:uppercase;'>{_html.escape(fg_title)}</div>"
            f"<div style='font-size:7.5pt; color:#52525b; margin-top:2px;'>{_html.escape(fg_sub)}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_fg_l)
        lbl_fg_l.attemptMove(QgsLayoutPoint(m, m + map_h + 3.0, mm))
        lbl_fg_l.attemptResize(QgsLayoutSize(map_w * 0.60, footer_h, mm))

        lbl_fg_r = QgsLayoutItemLabel(lay)
        lbl_fg_r.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        scale_num = max(1, int(round(mp.scale())))
        cred_fg = _html.escape(credits) if credits else "Vector Building Footprints"
        lbl_fg_r.setText(
            f"<div style='text-align:right; font-family:Helvetica Neue, Arial, sans-serif;'>"
            f"<div style='font-size:9pt; font-weight:bold; color:#111111;'>SCALE 1 : {scale_num:,}</div>"
            f"<div style='font-size:6.2pt; color:#71717a; margin-top:2px;'>{kpis['crs_authid']} &bull; {cred_fg}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_fg_r)
        lbl_fg_r.attemptMove(QgsLayoutPoint(m + map_w * 0.62, m + map_h + 3.5, mm))
        lbl_fg_r.attemptResize(QgsLayoutSize(map_w * 0.38, footer_h, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 5C: ARCHITECTURAL & ENGINEERING BLUEPRINT (Prussian Blue CAD Sheet)
    # =========================================================================
    if is_blueprint:
        bp_bg = "#0b2545"
        bp_map_bg = "#0d2c54"
        bp_line = "#e0f2fe"
        bp_cyan = "#38bdf8"
        _add_layout_card_shape(lay, 0, 0, W, H, fill=bp_bg, stroke="transparent", stroke_w=0, radius_mm=0)
        m = 8.0
        outer_bp = QgsLayoutItemShape(lay)
        outer_bp.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        outer_bp.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": bp_line, "outline_width": "0.5"}))
        lay.addLayoutItem(outer_bp)
        outer_bp.attemptMove(QgsLayoutPoint(m - 2.5, m - 2.5, mm))
        outer_bp.attemptResize(QgsLayoutSize(W - 2 * m + 5.0, H - 2 * m + 5.0, mm))

        map_w = W - 2 * m
        map_h = H - 2 * m
        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor(bp_map_bg))
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer, cover=True)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor(bp_cyan))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))
        _apply_grid(mp, main_ext, line_color="#1d4ed8", text_color="#bae6fd", font_family="Consolas")

        if kwargs.get("legend", False):
            leg = QgsLayoutItemLegend(lay)
            leg.setLinkedMap(mp)
            lay.addLayoutItem(leg)
            _configure_clean_legend(leg, mp, lay, title="LAYERS / CAD", font_family="Consolas", title_pt=8.0, group_pt=7.0, item_pt=6.5, max_items_per_layer=6)
            leg.setFontColor(QColor(bp_line))
            leg.setBackgroundColor(QColor(11, 37, 69, 230))
            leg.setFrameEnabled(True)
            leg.setFrameStrokeColor(QColor(bp_cyan))
            leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))
            leg.attemptMove(QgsLayoutPoint(m + 4.5, m + 4.5, mm))
            leg.attemptResize(QgsLayoutSize(min(46.0, map_w * 0.28), min(52.0, map_h * 0.28), mm))

        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(9.0, 12.0, mm))
        na.attemptMove(QgsLayoutPoint(m + map_w - 13.5, m + 4.5, mm))

        _create_clean_scalebar(mp, style="Line Ticks Down", position="bottom-left", pad=5.5,
                               font_family="Consolas", font_size=6.5, color=bp_line, background=bp_bg, frame_color=bp_cyan)

        tb_w = min(78.0, map_w * 0.42)
        tb_h = 26.0
        _add_layout_card_shape(lay, W - m - tb_w - 4.0, H - m - tb_h - 4.0, tb_w, tb_h, fill=bp_bg, stroke=bp_cyan, stroke_w=0.35, radius_mm=0.0)
        lbl_tb = QgsLayoutItemLabel(lay)
        lbl_tb.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        bp_title = title or "TECHNICAL SITE PLAN"
        bp_sub = subtitle or "CAD / Infrastructure Blueprint"
        bp_cred = _html.escape(credits) if credits else "Engineering Survey"
        lbl_tb.setText(
            f"<div style='font-family:Consolas, \"Courier New\", monospace; color:{bp_line};'>"
            f"<div style='font-size:5.8pt; color:{bp_cyan}; letter-spacing:1px;'>DWG NO: CAD-01 &bull; {kpis['crs_authid']}</div>"
            f"<div style='font-size:10.5pt; font-weight:bold; text-transform:uppercase; margin-top:2px;'>{_html.escape(bp_title)}</div>"
            f"<div style='font-size:7pt; color:#bae6fd; margin-top:1px;'>{_html.escape(bp_sub)}</div>"
            f"<div style='font-size:5.5pt; color:#7dd3fc; margin-top:2px;'>SCALE 1:{int(round(mp.scale())):,} &bull; {bp_cred}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_tb)
        lbl_tb.attemptMove(QgsLayoutPoint(W - m - tb_w - 2.0, H - m - tb_h - 2.0, mm))
        lbl_tb.attemptResize(QgsLayoutSize(tb_w - 4.0, tb_h - 4.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 5D: SMALL MULTIPLES (2x2 Synchronized Grid for Time-Series / Scenarios)
    # =========================================================================
    if is_small_multiples:
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#f8fafc", stroke="transparent", stroke_w=0, radius_mm=0)
        m = 8.0
        hdr_h = 16.0
        ftr_h = 26.0
        gap = 4.0
        grid_w = W - 2 * m
        grid_h = H - 2 * m - hdr_h - ftr_h - 4.0
        pw = (grid_w - gap) / 2.0
        ph = (grid_h - gap) / 2.0

        lbl_sm_hdr = QgsLayoutItemLabel(lay)
        lbl_sm_hdr.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sm_title = title or "MULTI-SCENARIO SPATIAL COMPARISON"
        sm_sub = subtitle or "Synchronized Extent & Scale Small Multiples"
        lbl_sm_hdr.setText(
            f"<div style='font-family:Helvetica Neue, Arial, sans-serif;'>"
            f"<div style='font-size:14pt; font-weight:bold; color:#0f172a;'>{_html.escape(sm_title)}</div>"
            f"<div style='font-size:8pt; color:#475569; margin-top:1px;'>{_html.escape(sm_sub)}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_sm_hdr)
        lbl_sm_hdr.attemptMove(QgsLayoutPoint(m, m, mm))
        lbl_sm_hdr.attemptResize(QgsLayoutSize(grid_w, hdr_h, mm))

        panel_titles = kwargs.get("panel_titles") or ["Panel A", "Panel B", "Panel C", "Panel D"]
        panel_layers = kwargs.get("panel_layers")
        panels = []
        main_ext = None
        top_y = m + hdr_h + 2.0
        for idx in range(4):
            col_i = idx % 2
            row_i = idx // 2
            px = m + col_i * (pw + gap)
            py = top_y + row_i * (ph + gap)
            pmp = QgsLayoutItemMap(lay)
            pmp.setRect(0, 0, 10, 10)
            lay.addLayoutItem(pmp)
            pmp.setCrs(p.crs())
            pmp.setBackgroundEnabled(True)
            pmp.setBackgroundColor(QColor("#ffffff"))
            pmp.attemptMove(QgsLayoutPoint(px, py, mm))
            pmp.attemptResize(QgsLayoutSize(pw, ph, mm))
            if main_ext is None:
                main_ext = _setup_extent(pmp, extent_layer, cover=True)
            else:
                pmp.setExtent(main_ext)
            if isinstance(panel_layers, (list, tuple)) and idx < len(panel_layers) and panel_layers[idx]:
                try:
                    pmp.setLayers([_layer(ln) for ln in panel_layers[idx]])
                    pmp.setKeepLayerSet(True)
                except Exception:  # nosec B110
                    pass
            pmp.setFrameEnabled(True)
            pmp.setFrameStrokeColor(QColor("#94a3b8"))
            pmp.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))
            panels.append(pmp)

            ptxt = str(panel_titles[idx]) if idx < len(panel_titles) else f"Panel {idx + 1}"
            plbl = QgsLayoutItemLabel(lay)
            plbl.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            plbl.setText(
                f"<div style='font-family:Arial, sans-serif; background:#0f172a; color:#ffffff; "
                f"font-size:7pt; font-weight:bold; padding:2px 6px;'>{_html.escape(ptxt)}</div>"
            )
            lay.addLayoutItem(plbl)
            plbl.attemptMove(QgsLayoutPoint(px + 2.0, py + 2.0, mm))
            plbl.attemptResize(QgsLayoutSize(min(38.0, pw * 0.48), 6.5, mm))

        ftr_y = top_y + grid_h + 2.5
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(panels[0])
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, panels[0], lay, title="", title_pt=7.0, group_pt=6.8, item_pt=6.2, max_items_per_layer=3)
        leg.setColumnCount(4)
        leg.setResizeToContents(False)
        leg.setBackgroundEnabled(False)
        leg.setFrameEnabled(False)
        leg.attemptMove(QgsLayoutPoint(m, ftr_y, mm))
        leg.attemptResize(QgsLayoutSize(grid_w * 0.62, ftr_h - 2.0, mm))

        _create_clean_scalebar(panels[3], style="Line Ticks Down", position="bottom-right", pad=3.5,
                               font_size=6.0, color="#0f172a", background="rgba(255,255,255,0.88)", frame_color=None)

        lbl_sm_cred = QgsLayoutItemLabel(lay)
        lbl_sm_cred.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sm_cred = _html.escape(credits) if credits else "Multi-Temporal Spatial Dataset"
        lbl_sm_cred.setText(
            f"<div style='text-align:right; font-family:Arial, sans-serif; font-size:6.2pt; color:#475569; line-height:1.3;'>"
            f"<div><b>CRS:</b> {kpis['crs_authid']} &bull; <b>Scale:</b> 1:{int(round(panels[0].scale())):,}</div>"
            f"<div>{sm_cred}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_sm_cred)
        lbl_sm_cred.attemptMove(QgsLayoutPoint(m + grid_w * 0.64, ftr_y + 2.0, mm))
        lbl_sm_cred.attemptResize(QgsLayoutSize(grid_w * 0.36, ftr_h - 2.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": panels[0].scale(), "panels": 4}

    # =========================================================================
    # THEME 5E: TRANSIT SCHEMATIC & ACCESSIBILITY / ISOCHRONE MAP
    # =========================================================================
    if is_transit:
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#f8fafc", stroke="transparent", stroke_w=0, radius_mm=0)
        m = 7.5
        hdr_h = 15.0
        _add_layout_card_shape(lay, m, m, W - 2 * m, hdr_h, fill="#0f172a", stroke="transparent", stroke_w=0, radius_mm=1.0)
        _add_layout_card_shape(lay, m, m + hdr_h - 1.5, W - 2 * m, 1.5, fill="#0284c7", stroke="transparent", stroke_w=0, radius_mm=0.0)
        lbl_tr_h = QgsLayoutItemLabel(lay)
        lbl_tr_h.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        tr_title = title or "TRANSIT & ACCESSIBILITY NETWORK"
        tr_sub = subtitle or "Corridors, Stations & Service Coverage"
        lbl_tr_h.setText(
            f"<div style='font-family:Helvetica Neue, Arial, sans-serif; color:#ffffff;'>"
            f"<div style='font-size:13pt; font-weight:bold; letter-spacing:0.5px;'>{_html.escape(tr_title)}</div>"
            f"<div style='font-size:7.5pt; color:#bae6fd;'>{_html.escape(tr_sub)}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_tr_h)
        lbl_tr_h.attemptMove(QgsLayoutPoint(m + 4.0, m + 1.5, mm))
        lbl_tr_h.attemptResize(QgsLayoutSize(W - 2 * m - 8.0, hdr_h - 3.0, mm))

        body_y = m + hdr_h + 3.0
        body_h = H - body_y - m
        gap = 3.5
        is_port = H > W
        if is_port:
            map_x, map_y, map_w, map_h = m, body_y, W - 2 * m, body_h * 0.64
            pan_x, pan_y, pan_w, pan_h = m, body_y + map_h + gap, W - 2 * m, body_h - map_h - gap
        else:
            map_w = (W - 2 * m - gap) * 0.66
            map_h = body_h
            map_x, map_y = m, body_y
            pan_x, pan_y, pan_w, pan_h = m + map_w + gap, body_y, (W - 2 * m - gap) - map_w, body_h

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor("#ffffff"))
        mp.attemptMove(QgsLayoutPoint(map_x, map_y, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#cbd5e1"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        _create_clean_scalebar(mp, style="Stepped Line", position="bottom-left", pad=4.5,
                               color="#0f172a", background="#ffffff", frame_color="#cbd5e1")
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(8.0, 11.0, mm))
        na.attemptMove(QgsLayoutPoint(map_x + map_w - 11.5, map_y + 3.5, mm))

        kpi_h = min(22.0, pan_h * 0.30)
        _add_layout_card_shape(lay, pan_x, pan_y, pan_w, kpi_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.0)
        lbl_tkpi = QgsLayoutItemLabel(lay)
        lbl_tkpi.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_tkpi.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"<div style='font-size:6.2pt; font-weight:bold; color:#0284c7; text-transform:uppercase;'>{_html.escape(kpis['kpi1_label'])} &bull; {_html.escape(kpis['kpi2_label'])}</div>"
            f"<div style='font-size:11pt; font-weight:bold; color:#0f172a; margin-top:1px;'>{_html.escape(kpis['kpi1_value'])} &middot; {_html.escape(kpis['kpi2_value'])}</div>"
            f"<div style='font-size:6pt; color:#64748b;'>{kpis['kpi1_sub']}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_tkpi)
        lbl_tkpi.attemptMove(QgsLayoutPoint(pan_x + 3.0, pan_y + 2.0, mm))
        lbl_tkpi.attemptResize(QgsLayoutSize(pan_w - 6.0, kpi_h - 4.0, mm))

        cred_h = 12.0
        leg_y = pan_y + kpi_h + 2.5
        leg_h = max(25.0, pan_h - kpi_h - cred_h - 5.0)
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="NETWORK & CATCHMENT", title_pt=8.5, group_pt=7.5, item_pt=7.0, max_items_per_layer=8)
        leg.setBackgroundColor(QColor("#ffffff"))
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor("#cbd5e1"))
        leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))
        leg.attemptMove(QgsLayoutPoint(pan_x, leg_y, mm))
        leg.attemptResize(QgsLayoutSize(pan_w, leg_h, mm))

        lbl_tcred = QgsLayoutItemLabel(lay)
        lbl_tcred.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        tr_cred = _html.escape(credits) if credits else "OpenStreetMap / Overture Transit Network"
        lbl_tcred.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6pt; color:#64748b; line-height:1.25;'>"
            f"<b>Sources:</b> {tr_cred} &bull; {kpis['crs_authid']}</div>"
        )
        lay.addLayoutItem(lbl_tcred)
        lbl_tcred.attemptMove(QgsLayoutPoint(pan_x, pan_y + pan_h - cred_h, mm))
        lbl_tcred.attemptResize(QgsLayoutSize(pan_w, cred_h, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 5F: EDITORIAL VERTICAL (FT / NYT Modern Vertical Data Journalism)
    # =========================================================================
    if is_editorial_vertical:
        bg_ed = "#fffaf5"
        _add_layout_card_shape(lay, 0, 0, W, H, fill=bg_ed, stroke="transparent", stroke_w=0, radius_mm=0)
        m = 9.0
        _add_layout_card_shape(lay, m, m, W - 2 * m, 1.2, fill="#991b1b", stroke="transparent", stroke_w=0, radius_mm=0)
        hdr_h = 24.0
        lbl_ev = QgsLayoutItemLabel(lay)
        lbl_ev.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        ev_title = title or "SPATIAL BRIEFING"
        ev_sub = subtitle or "Focused cartographic analysis of key geographic patterns"
        lbl_ev.setText(
            f"<div style='font-family:Georgia, \"Times New Roman\", serif;'>"
            f"<div style='font-size:16pt; font-weight:bold; color:#111827; line-height:1.15;'>{_html.escape(ev_title)}</div>"
            f"<div style='font-family:Helvetica Neue, Arial, sans-serif; font-size:8.5pt; color:#4b5563; margin-top:3px;'>{_html.escape(ev_sub)}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_ev)
        lbl_ev.attemptMove(QgsLayoutPoint(m, m + 3.0, mm))
        lbl_ev.attemptResize(QgsLayoutSize(W - 2 * m, hdr_h, mm))

        ftr_h = 8.0
        map_y = m + hdr_h + 4.0
        map_w = W - 2 * m
        map_h = H - map_y - m - ftr_h - 2.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor("#f8f4ee"))
        mp.attemptMove(QgsLayoutPoint(m, map_y, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer, cover=True)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#d6d3d1"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))

        if kwargs.get("legend", True):
            leg = QgsLayoutItemLegend(lay)
            leg.setLinkedMap(mp)
            lay.addLayoutItem(leg)
            _configure_clean_legend(leg, mp, lay, title="KEY", font_family="Arial", title_pt=8.0, group_pt=7.0, item_pt=6.8, max_items_per_layer=6)
            leg.setBackgroundColor(QColor(255, 250, 245, 232))
            leg.setFrameEnabled(True)
            leg.setFrameStrokeColor(QColor("#d6d3d1"))
            leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, mm))
            leg.attemptMove(QgsLayoutPoint(m + map_w - min(48.0, map_w * 0.30) - 3.5, map_y + 3.5, mm))
            leg.attemptResize(QgsLayoutSize(min(48.0, map_w * 0.30), min(52.0, map_h * 0.28), mm))

        _create_clean_scalebar(mp, style="Line Ticks Down", position="bottom-left", pad=4.5,
                               font_size=6.5, color="#111827", background="rgba(255,250,245,0.88)", frame_color=None)

        lbl_ev_f = QgsLayoutItemLabel(lay)
        lbl_ev_f.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        ev_cred = _html.escape(credits) if credits else "Verified Geospatial Sources"
        lbl_ev_f.setText(
            f"<div style='font-family:Helvetica Neue, Arial, sans-serif; font-size:6.5pt; color:#6b7280;'>"
            f"Sources: {ev_cred} &bull; {kpis['crs_authid']}</div>"
        )
        lay.addLayoutItem(lbl_ev_f)
        lbl_ev_f.attemptMove(QgsLayoutPoint(m, H - m - ftr_h, mm))
        lbl_ev_f.attemptResize(QgsLayoutSize(map_w, ftr_h, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 5G: TOURIST MAP A3 (Full Side-1 Map + Grid + Highlight Cards + Directory)
    # =========================================================================
    if is_tourist_map:
        from . import layout_tools as _lt
        u = max(1.0, W / 420.0)
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#faf7f0", stroke="transparent", stroke_w=0, radius_mm=0)

        strip = 4.5 * u
        mx, my = 6.0 * u + strip, 6.0 * u + strip
        dir_h = max(44.0, H * 0.175)
        right_w = max(86.0, W * 0.245)
        mw = W - mx - right_w - 9.0 * u
        mh = H - my - dir_h - 8.5 * u

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setId("main_map")
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor("#eef3e2"))
        mp.attemptMove(QgsLayoutPoint(mx, my, mm))
        mp.attemptResize(QgsLayoutSize(mw, mh, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#94a3b8"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        _register_layout(mgr, lay)
        _lt.add_map_reference_grid(layout=name, map_id="main_map", cols=8, rows=6, strip_size=strip)

        # Inset downtown/detail map on bottom-left of main map
        inset_w, inset_h = mw * 0.28, mh * 0.34
        inset = QgsLayoutItemMap(lay)
        inset.setRect(0, 0, 10, 10)
        lay.addLayoutItem(inset)
        inset.setId("inset_map")
        inset.setCrs(p.crs())
        inset.setBackgroundEnabled(True)
        inset.setBackgroundColor(QColor("#ffffff"))
        inset.attemptMove(QgsLayoutPoint(mx + 3.0 * u, my + mh - inset_h - 3.0 * u, mm))
        inset.attemptResize(QgsLayoutSize(inset_w, inset_h, mm))
        _setup_extent(inset, key_extent_layer or extent_layer)
        if not key_extent_layer and main_ext and _is_valid_extent(main_ext):
            cx, cy = main_ext.center().x(), main_ext.center().y()
            zw, zh = main_ext.width() * 0.16, main_ext.height() * 0.16
            inset.zoomToExtent(QgsRectangle(cx - zw, cy - zh, cx + zw, cy + zh))
        inset.setFrameEnabled(True)
        inset.setFrameStrokeColor(QColor("#475569"))
        inset.setFrameStrokeWidth(QgsLayoutMeasurement(0.45, mm))
        ov = QgsLayoutItemMapOverview("inset_ov", mp)
        ov.setLinkedMap(inset)
        mp.overviews().addOverview(ov)

        # Curved ribbon title banner & QR code on top of main map header
        rib_title = title or "CITY TOURIST MAP"
        rib_w = min(88.0 * u, mw * 0.34)
        _lt.generate_layout_graphic(
            kind="ribbon_banner", text=rib_title, subtitle=subtitle or "Sightseeing · Food · Stay",
            color="#d97373", accent_color="#ffffff", layout=name,
            x=mx + 3.0 * u, y=my + 2.5 * u, width=rib_w, height=23.0 * u, item_id="hero_ribbon",
        )
        _lt.generate_layout_graphic(
            kind="qr_code", text=credits or "https://qgis.org", color="#1e293b", accent_color="#ffffff",
            layout=name, x=mx + 3.0 * u + rib_w + 2.5 * u, y=my + 3.5 * u, width=15.0 * u, height=15.0 * u, item_id="hero_qr",
        )

        _create_clean_scalebar(mp, style="Single Box", position="bottom-right", pad=3.5, font_size=6.5 * u,
                               color="#1e293b", background="#ffffff", frame_color="#cbd5e1")
        na = QgsLayoutItemPicture(lay)
        na.setId("north_arrow")
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(9.0 * u, 11.5 * u, mm))
        na.attemptMove(QgsLayoutPoint(mx + mw - 12.5 * u, my + 3.0 * u, mm))

        # Right Editorial Feature Column (3 themed cards with icon + image/slot + text)
        rx = mx + mw + 3.5 * u
        ry = my - strip
        card_specs = [
            ("card_1", "SCENIC HIGHLIGHTS", "camera", "#0284c7", "Scenic viewpoints, parks & iconic photo spots."),
            ("card_2", "WALK & HERITAGE", "hiking", "#65a30d", "Historic alleys, trails & museum corridors."),
            ("card_3", "RELAX & WELLNESS", "onsen", "#d97706", "Cafes, hot springs & local evening hubs."),
        ]
        card_gap = 3.0 * u
        total_rh = (my + mh) - ry
        ch = (total_rh - 2 * card_gap) / 3.0
        for idx, (cid, c_title, c_icon, c_col, c_desc) in enumerate(card_specs):
            cy = ry + idx * (ch + card_gap)
            _add_layout_card_shape(lay, rx, cy, right_w, ch, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.3, radius_mm=1.2)
            _add_layout_card_shape(lay, rx, cy, right_w, 7.5 * u, fill=c_col, stroke="transparent", stroke_w=0, radius_mm=1.0)
            _lt.generate_layout_graphic(kind="tourist_icon", subtitle=c_icon, color=c_col, accent_color="#ffffff",
                                        layout=name, x=rx + 1.5 * u, y=cy + 1.0 * u, width=5.5 * u, height=5.5 * u, item_id=f"{cid}_icon")
            _lt.add_layout_text(name, rx + 8.5 * u, cy + 0.8 * u, right_w - 10.0 * u, 6.0 * u, c_title, item_id=f"{cid}_title",
                                size=8.0 * u, color="#ffffff", bold=True, padding=0.2)
            _lt.add_layout_text(name, rx + 3.0 * u, cy + 9.0 * u, right_w - 6.0 * u, ch - 11.0 * u, c_desc, item_id=f"{cid}_body",
                                size=7.0 * u, color="#334155", valign="top", padding=0.5)

        # Bottom Multi-Column Categorized Directory Strip (Stay + Dining)
        dir_y = my + mh + 3.5 * u
        dir_x = 6.0 * u
        dir_total_w = W - 12.0 * u
        stay_w = dir_total_w * 0.44
        eat_w = dir_total_w - stay_w - 3.5 * u
        _lt.generate_layout_graphic(
            kind="directory_card", text="ACCOMMODATION & STAY", color="#65a30d", accent_color="#ffffff", columns=2,
            items=[{"num": i + 1, "name": f"Stay Highlight #{i + 1}", "grid": f"{chr(65 + i)}-2", "meta": "Populate from POI layer"} for i in range(4)],
            layout=name, x=dir_x, y=dir_y, width=stay_w, height=dir_h, item_id="dir_stay",
        )
        _lt.generate_layout_graphic(
            kind="directory_card", text="DINING, CAFES & LANDMARKS", color="#d97373", accent_color="#ffffff", columns=3,
            items=[{"num": i + 1, "name": f"Highlight Spot #{i + 1}", "grid": f"{chr(66 + i)}-4", "meta": "Populate from POI layer"} for i in range(6)],
            layout=name, x=dir_x + stay_w + 3.5 * u, y=dir_y, width=eat_w, height=dir_h, item_id="dir_eat",
        )

        lay.refresh()
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
                "items": ["main_map", "inset_map", "hero_ribbon", "hero_qr", "card_1_body", "card_2_body", "card_3_body", "dir_stay", "dir_eat"]}

    # =========================================================================
    # THEME 5H: STEPPED BOOKLET A3 (6-Panel 2x3 Fold with Stepped Vertical Side Tabs)
    # =========================================================================
    if is_stepped_booklet:
        from . import layout_tools as _lt
        u = max(1.0, W / 420.0)
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#faf8f5", stroke="transparent", stroke_w=0, radius_mm=0)

        col_w = W / 3.0
        row_h = H / 2.0
        tab_w = 7.5 * u
        # Fold guides (subtle dashed rectangles)
        for ci in range(1, 3):
            _add_layout_card_shape(lay, ci * col_w - 0.15, 0, 0.3, H, fill="#cbd5e1", stroke="transparent", stroke_w=0, radius_mm=0)
        _add_layout_card_shape(lay, 0, row_h - 0.15, W, 0.3, fill="#cbd5e1", stroke="transparent", stroke_w=0, radius_mm=0)

        panels = [
            # id, col, row, rot, tab_side, tab_color, tab_title, hdr_title, body_hint
            ("panel_4", 0, 0, 180, "left", "#0284c7", "NATURE & PLAY", "Nature & Outdoor", "Trails, viewpoints, seasonal highlights & activity spots."),
            ("panel_5", 1, 0, 180, "left", "#16a34a", "FOUR SEASONS", "Seasonal Story", "Spring blossoms, summer highlands, autumn foliage & winter charm."),
            ("panel_6", 2, 0, 180, "right", "#7c3aed", "ACCESS & INFO", "Access & Transit", "Rail stations, highway exits, transit hubs & tourist office."),
            ("panel_1", 0, 1, 0, "right", "#0f766e", "WELCOME COVER", title or "CITY GUIDE", subtitle or "Official Folded Tourist Map & Directory"),
            ("panel_2", 1, 1, 0, "right", "#d97706", "LOCAL FLAVOR", "Stay & Gastronomy", "Signature dishes, cafés, markets & boutique stays."),
            ("panel_3", 2, 1, 0, "right", "#d97373", "TOP SPOTS", "Featured Spots", "Must-visit landmarks, cultural heritage & photo stops."),
        ]

        _register_layout(mgr, lay)
        for idx, (pid, ci, ri, rot, tab_side, tcol, ttitle, htitle, btext) in enumerate(panels):
            px0 = ci * col_w + 3.0 * u
            py0 = ri * row_h + 3.0 * u
            pw = col_w - 6.0 * u
            ph = row_h - 6.0 * u
            # Stepped horizontal inset per column so folded tabs cascade neatly
            step_off = (idx % 2) * (tab_w * 0.9)
            if tab_side == "right":
                tab_x = ci * col_w + col_w - tab_w - step_off * 0.5
                content_x = px0
                content_w = max(40.0, tab_x - content_x - 2.5 * u)
            else:
                tab_x = ci * col_w + step_off * 0.5
                content_x = tab_x + tab_w + 2.5 * u
                content_w = max(40.0, (ci * col_w + col_w - 3.0 * u) - content_x)

            # Side Tab Strip (SVG text rotated cleanly)
            _lt.generate_layout_graphic(
                kind="stepped_tab", text=ttitle, color=tcol, accent_color="#ffffff",
                layout=name, x=tab_x, y=ri * row_h + 2.0 * u, width=tab_w, height=row_h - 4.0 * u,
                item_id=f"{pid}_tab", rotation=rot,
            )
            # Panel Card Header + Body
            _add_layout_card_shape(lay, content_x, py0, content_w, ph, fill="#ffffff", stroke="#e2e8f0", stroke_w=0.25, radius_mm=1.2)
            hdr_y = (py0 + ph - 11.0 * u) if rot == 180 else py0
            body_y = py0 + 2.0 * u if rot == 180 else (py0 + 13.0 * u)
            _lt.add_layout_text(name, content_x + 2.0 * u, hdr_y, content_w - 4.0 * u, 9.5 * u, htitle,
                                item_id=f"{pid}_header", size=10.5 * u, color="#ffffff", bold=True, align="center",
                                background=tcol, padding=0.8, rotation=rot)

            if pid == "panel_1":
                _lt.generate_layout_graphic(
                    kind="ribbon_banner", text=htitle, subtitle=btext, color=tcol, accent_color="#ffffff",
                    layout=name, x=content_x + 4.0 * u, y=py0 + 13.0 * u, width=content_w - 8.0 * u, height=22.0 * u,
                    item_id="cover_ribbon",
                )
                # Locator map inset on cover bottom
                loc_h = min(46.0 * u, ph * 0.34)
                loc = QgsLayoutItemMap(lay)
                loc.setRect(0, 0, 10, 10)
                lay.addLayoutItem(loc)
                loc.setId("locator_map")
                loc.setCrs(p.crs())
                loc.setBackgroundEnabled(True)
                loc.setBackgroundColor(QColor("#e8f0fe"))
                loc.attemptMove(QgsLayoutPoint(content_x + 4.0 * u, py0 + ph - loc_h - 4.0 * u, mm))
                loc.attemptResize(QgsLayoutSize(content_w * 0.48, loc_h, mm))
                _setup_extent(loc, key_extent_layer or extent_layer)
                loc.setFrameEnabled(True)
                loc.setFrameStrokeColor(QColor("#64748b"))
                loc.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))
                _lt.add_layout_text(name, content_x + content_w * 0.54, py0 + ph - loc_h - 2.0 * u, content_w * 0.42, loc_h - 4.0 * u,
                                    credits or "Replace with hero photo & regional summary.", item_id="panel_1_body",
                                    size=7.5 * u, color="#334155", valign="top", padding=1.0)
            elif pid == "panel_6":
                acc_h = min(56.0 * u, ph * 0.44)
                acc = QgsLayoutItemMap(lay)
                acc.setRect(0, 0, 10, 10)
                lay.addLayoutItem(acc)
                acc.setId("access_map")
                acc.setCrs(p.crs())
                acc.setBackgroundEnabled(True)
                acc.setBackgroundColor(QColor("#f1f5f9"))
                acc.attemptMove(QgsLayoutPoint(content_x + 4.0 * u, py0 + 6.0 * u, mm))
                acc.attemptResize(QgsLayoutSize(content_w - 8.0 * u, acc_h, mm))
                _setup_extent(acc, extent_layer)
                acc.setFrameEnabled(True)
                acc.setFrameStrokeColor(QColor("#64748b"))
                acc.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))
                acc.setItemRotation(180.0, True)
                _lt.generate_layout_graphic(
                    kind="qr_code", text=credits or "https://qgis.org", color="#1e293b", accent_color="#ffffff",
                    layout=name, x=content_x + 5.0 * u, y=py0 + acc_h + 9.0 * u, width=16.0 * u, height=16.0 * u,
                    item_id="access_qr", rotation=180,
                )
                _lt.add_layout_text(name, content_x + 23.0 * u, py0 + acc_h + 9.0 * u, content_w - 27.0 * u, ph - acc_h - 22.0 * u,
                                    btext, item_id="panel_6_body", size=7.5 * u, color="#334155", valign="top", padding=1.0, rotation=180)
            else:
                _lt.add_layout_text(name, content_x + 4.0 * u, body_y, content_w - 8.0 * u, ph - 17.0 * u,
                                    btext, item_id=f"{pid}_body", size=8.0 * u, color="#334155", valign="top", padding=1.5, rotation=rot)

        lay.refresh()
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "panels": 6,
                "items": ["panel_1_tab", "panel_2_tab", "panel_3_tab", "panel_4_tab", "panel_5_tab", "panel_6_tab",
                          "cover_ribbon", "locator_map", "access_map", "access_qr"]}

    # =========================================================================
    # THEME 6: SCIENTIFIC DARK MODE THEMATIC ATLAS (Continuous Gradient & Ocean Space)
    # =========================================================================
    if is_scientific_dark:
        page.setBackgroundColor(QColor("#181f2a"))
        m = 7.0
        map_w = W - 2 * m
        map_h = H - 2 * m

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#334155"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        # Floating Research Context & Title Block (in negative space)
        _add_layout_card_shape(lay, m + 6.0, H - m - 46.0, 90.0, 22.0, fill="#0f172a", stroke="#334155", stroke_w=0.3, radius_mm=1.5)
        lbl_sci = QgsLayoutItemLabel(lay)
        lbl_sci.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_lead = f"<div style='font-size:7.5pt; color:#94a3b8; margin-bottom:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_sci.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"{sub_lead}"
            f"<div style='font-size:12pt; font-weight:bold; color:#f8fafc; line-height:1.25;'>{_html.escape(title or 'SCIENTIFIC THEMATIC ANALYSIS')}</div>"
            f"<div style='font-size:6.8pt; color:#cbd5e1; margin-top:3px; line-height:1.2;'>{kpis['total_features']:,} features across {kpis['layer_count']} layers &bull; {kpis['crs_authid']}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_sci)
        lbl_sci.attemptMove(QgsLayoutPoint(m + 8.5, H - m - 44.0, mm))
        lbl_sci.attemptResize(QgsLayoutSize(85.0, 18.0, mm))

        # Floating Continuous Gradient Ramp Legend
        _add_layout_card_shape(lay, m + 6.0, H - m - 22.0, 90.0, 16.0, fill="#0f172a", stroke="#334155", stroke_w=0.3, radius_mm=1.5)
        ramp_lbl = QgsLayoutItemLabel(lay)
        ramp_lbl.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        ramp_lbl.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"<div style='font-size:6.5pt; color:#94a3b8; font-weight:bold; margin-bottom:2px;'>RELATIVE VALUE DISTRIBUTION</div>"
            f"<div style='height:7px; width:100%; background:linear-gradient(to right, #440154, #3b528b, #21918c, #5ec962, #fde725);'></div>"
            f"<table width='100%' style='margin-top:2px; font-size:6.5pt; color:#cbd5e1; font-family:monospace;'>"
            f"<tr><td align='left'>Low / Min</td><td align='center'>Median</td><td align='right'>High / Extreme</td></tr>"
            f"</table>"
            f"</div>"
        )
        lay.addLayoutItem(ramp_lbl)
        ramp_lbl.attemptMove(QgsLayoutPoint(m + 8.5, H - m - 20.0, mm))
        ramp_lbl.attemptResize(QgsLayoutSize(85.0, 12.0, mm))

        # Institutional Metadata / Branding Block in lower right negative space
        lbl_inst = QgsLayoutItemLabel(lay)
        lbl_inst.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_inst.setText(
            f"<div style='font-family:Arial, sans-serif; text-align:right; color:#94a3b8; font-size:6.8pt; line-height:1.3;'>"
            f"<span style='color:#f8fafc; font-weight:bold; font-size:8pt;'>{_html.escape(kpis['kpi2_label'])}: {_html.escape(kpis['kpi2_value'])}</span><br>"
            f"Sources: {_html.escape(credits or 'Global Open Data, Earth Observation STAC')}<br>"
            f"CRS: {kpis['crs_authid']} &bull; QGIS Copilot"
            f"</div>"
        )
        lay.addLayoutItem(lbl_inst)
        lbl_inst.attemptMove(QgsLayoutPoint(W - m - 95.0, H - m - 14.0, mm))
        lbl_inst.attemptResize(QgsLayoutSize(90.0, 12.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 7: EDITORIAL MAGAZINE & MULTI-FRAME INFOGRAPHIC (Rich Journalistic Atlas)
    # =========================================================================
    if is_editorial:
        m = 8.0
        # Editorial Headline & Lead Paragraph Banner
        header_h = 22.0
        lbl_head = QgsLayoutItemLabel(lay)
        lbl_head.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lead_txt = f"<div style='font-size:8.5pt; color:#475569; font-style:italic; margin-top:2px; line-height:1.3;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_head.setText(
            f"<div style='font-family:Georgia, serif; padding:2px 4px; border-bottom:1.5px solid #0f172a;'>"
            f"<div style='font-size:17pt; font-weight:bold; color:#0f172a; letter-spacing:0.5px;'>{_html.escape(title or 'EDITORIAL ATLAS STORY')}</div>"
            f"{lead_txt}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_head)
        lbl_head.attemptMove(QgsLayoutPoint(m, m, mm))
        lbl_head.attemptResize(QgsLayoutSize(W - 2 * m, header_h, mm))

        map_top = m + header_h + 3.0
        right_w = 70.0
        map_w = W - 2 * m - right_w - 3.0
        map_h = H - map_top - m - 4.0
        right_x = m + map_w + 3.0

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#cbd5e1"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        # Floating KPI Callout Box inside Map Negative Space
        _add_layout_card_shape(lay, m + 4.0, map_top + 4.0, 58.0, 20.0, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.2)
        lbl_call = QgsLayoutItemLabel(lay)
        lbl_call.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_call.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"<div style='font-size:6.5pt; font-weight:bold; color:#64748b; text-transform:uppercase;'>{_html.escape(kpis['kpi1_label'])}</div>"
            f"<div style='font-size:12.5pt; font-weight:bold; color:#0284c7;'>{_html.escape(kpis['kpi1_value'])}</div>"
            f"<div style='font-size:6pt; color:#475569; line-height:1.2;'>{kpis['kpi2_label']}: {_html.escape(kpis['kpi2_value'])}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_call)
        lbl_call.attemptMove(QgsLayoutPoint(m + 6.5, map_top + 5.8, mm))
        lbl_call.attemptResize(QgsLayoutSize(53.0, 16.5, mm))

        # Right Column: Multi-Column Legend
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="LEGEND", font_family="Georgia", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=10)
        leg.setBackgroundColor(QColor("#f8fafc"))
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor("#cbd5e1"))
        leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, mm))
        leg.attemptMove(QgsLayoutPoint(right_x, map_top, mm))
        leg.attemptResize(QgsLayoutSize(right_w, 65.0, mm))

        # Secondary Focus Inset Map or Overview Map in Right Column
        if key_extent_layer:
            kmp = QgsLayoutItemMap(lay)
            lay.addLayoutItem(kmp)
            kmp.setCrs(p.crs())
            kmp.attemptMove(QgsLayoutPoint(right_x, map_top + 68.0, mm))
            kmp.attemptResize(QgsLayoutSize(right_w, 45.0, mm))
            _setup_extent(kmp, key_extent_layer)
            kmp.setFrameEnabled(True)
            kmp.setFrameStrokeColor(QColor("#94a3b8"))
            kmp.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))
            ov = QgsLayoutItemMapOverview("MainMapExt", kmp)
            ov.setLinkedMap(mp)
            ov.setFrameFillColor(QColor(220, 38, 38, 40))
            ov.setFrameOutlineColor(QColor(220, 38, 38, 220))
            kmp.overviews().addOverview(ov)

        # Scale bar inside editorial hero map
        _create_clean_scalebar(mp, style="Stepped Line", position="bottom-left", pad=6.0,
                               font_family="Georgia", color="#0f172a", background="#ffffff", frame_color=None)

        # Bottom Editorial Metadata
        lbl_cred = QgsLayoutItemLabel(lay)
        lbl_cred.setText(f"Editorial Cartography · Sources: {credits or 'Public Repositories'} · Projection: {p.crs().authid()}")
        fmt = QgsTextFormat()
        fmt.setSize(6.5)
        fmt.setColor(QColor("#64748b"))
        lbl_cred.setTextFormat(fmt)
        lay.addLayoutItem(lbl_cred)
        lbl_cred.attemptMove(QgsLayoutPoint(m, H - m - 3.5, mm))
        lbl_cred.attemptResize(QgsLayoutSize(W - 2 * m, 4.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
                "right_panel_rect": [right_x, map_top, right_w, map_h]}

    # =========================================================================
    # THEME 8: ANTIQUE / HISTORICAL ENGRAVED PLAN (Parchment & Ornate Cartouche)
    # =========================================================================
    if is_antique:
        _add_layout_card_shape(lay, 0, 0, W, H, fill="#f4ecd8", stroke="transparent", stroke_w=0, radius_mm=0)
        m = 9.0
        # Ornate Double Neatline Border
        out_neat = QgsLayoutItemShape(lay)
        out_neat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        out_neat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#2e2115", "outline_width": "0.8"}))
        lay.addLayoutItem(out_neat)
        out_neat.attemptMove(QgsLayoutPoint(m - 3.0, m - 3.0, mm))
        out_neat.attemptResize(QgsLayoutSize(W - 2 * m + 6.0, H - 2 * m + 6.0, mm))

        in_neat = QgsLayoutItemShape(lay)
        in_neat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        in_neat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#2e2115", "outline_width": "0.25"}))
        lay.addLayoutItem(in_neat)
        in_neat.attemptMove(QgsLayoutPoint(m - 1.5, m - 1.5, mm))
        in_neat.attemptResize(QgsLayoutSize(W - 2 * m + 3.0, H - 2 * m + 3.0, mm))

        map_w = W - 2 * m
        map_h = H - 2 * m

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.setBackgroundEnabled(True)
        mp.setBackgroundColor(QColor("#f4ecd8"))
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer, cover=True)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#2e2115"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))
        _apply_grid(mp, main_ext, line_color="#8c7355", text_color="#4a3728", font_family="Georgia")

        # Antique North Arrow / Compass Rose (top-right inside map frame)
        if kwargs.get("north_arrow", True):
            na = QgsLayoutItemPicture(lay)
            na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
            na.setLinkedMap(mp)
            lay.addLayoutItem(na)
            na.attemptResize(QgsLayoutSize(10.0, 14.0, mm))
            na.attemptMove(QgsLayoutPoint(m + map_w - 15.0, m + 5.0, mm))

        # Compact Framed Parchment Legend (top-left inside map frame)
        if kwargs.get("legend", True):
            leg = QgsLayoutItemLegend(lay)
            leg.setLinkedMap(mp)
            lay.addLayoutItem(leg)
            _configure_clean_legend(leg, mp, lay, title="LEGEND", font_family="Georgia", title_pt=8.5, group_pt=7.5, item_pt=7.0, max_items_per_layer=8)
            leg.setBackgroundColor(QColor(244, 236, 216, 235))
            leg.setFrameEnabled(True)
            leg.setFrameStrokeColor(QColor("#2e2115"))
            leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))
            leg.attemptMove(QgsLayoutPoint(m + 4.5, m + 4.5, mm))
            leg.attemptResize(QgsLayoutSize(min(50.0, map_w * 0.30), min(58.0, map_h * 0.30), mm))

        # Classical Corner Cartouche Title + Credits in Negative Space (bottom-right)
        cart_w = min(76.0, map_w * 0.44)
        cart_h = 30.0 if credits else 25.0
        _add_layout_card_shape(lay, W - m - cart_w - 4.0, H - m - cart_h - 4.0, cart_w, cart_h, fill="#f4ecd8", stroke="#2e2115", stroke_w=0.35, radius_mm=0.0)
        lbl_cart = QgsLayoutItemLabel(lay)
        lbl_cart.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        plan_title = title or "PLAN OF THE CITY"
        cred_line = f"<div style='font-size:5.8pt; color:#5c4738; margin-top:3px; line-height:1.2;'>{_html.escape(credits)} &bull; {kpis['crs_authid']}</div>" if credits else f"<div style='font-size:5.8pt; color:#5c4738; margin-top:3px;'>CRS: {kpis['crs_authid']}</div>"
        lbl_cart.setText(
            f"<div style='font-family:Georgia, serif; text-align:center;'>"
            f"<div style='font-size:11.5pt; font-weight:bold; letter-spacing:1px; color:#2e2115; text-transform:uppercase;'>{_html.escape(plan_title)}</div>"
            f"<div style='font-size:7.5pt; font-style:italic; color:#4a3728; margin-top:2px;'>{_html.escape(subtitle or 'Surveyed Topographical Plan')}</div>"
            f"{cred_line}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_cart)
        lbl_cart.attemptMove(QgsLayoutPoint(W - m - cart_w - 2.0, H - m - cart_h - 2.0, mm))
        lbl_cart.attemptResize(QgsLayoutSize(cart_w - 4.0, cart_h - 4.0, mm))

        # Antique Scale Bar (classical single box with authentic sepia ink styling)
        if kwargs.get("scalebar", True):
            _create_clean_scalebar(mp, style="Single Box", position="bottom-left", pad=6.0,
                                   font_family="Georgia", font_size=6.5, color="#2e2115", secondary_color="#f4ecd8",
                                   background="#f4ecd8", frame_color="#2e2115")

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 9: BENTO GRID MODULAR DASHBOARD (Modern Apple / Linear Card UI)
    # =========================================================================
    if is_bento:
        page.setBackgroundColor(QColor("#f1f5f9"))
        m = 7.0
        gap = 3.5
        avail_w = W - 2 * m
        avail_h = H - 2 * m

        # Left Column: Hero Map Card (62% width)
        left_w = avail_w * 0.62
        right_w = avail_w - left_w - gap
        right_x = m + left_w + gap

        # Map Card Frame
        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(left_w, avail_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#cbd5e1"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        # Scale bar & North Arrow cleanly floating inside Map Card
        _create_clean_scalebar(mp, style="Stepped Line", position="bottom-left", pad=5.0,
                               color="#0f172a", background="#ffffff", frame_color=None)

        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(9.0, 12.0, mm))
        na.attemptMove(QgsLayoutPoint(m + left_w - 13.0, m + 4.0, mm))

        # Right Column Bento Cards
        curr_y = m

        # Card 1: Title & Executive Summary Card
        card1_h = 28.0
        _add_layout_card_shape(lay, right_x, curr_y, right_w, card1_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.8)
        lbl_b1 = QgsLayoutItemLabel(lay)
        lbl_b1.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_txt = f"<div style='font-size:8pt; color:#64748b; margin-top:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_b1.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"<div style='font-size:6.8pt; font-weight:bold; color:#0284c7; text-transform:uppercase; letter-spacing:0.8px;'>CARTOGRAPHIC OVERVIEW</div>"
            f"<div style='font-size:12pt; font-weight:bold; color:#0f172a; margin-top:2px; line-height:1.15;'>{_html.escape(title or 'SPATIAL ANALYTICS')}</div>"
            f"{sub_txt}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b1)
        lbl_b1.attemptMove(QgsLayoutPoint(right_x + 2.5, curr_y + 2.0, mm))
        lbl_b1.attemptResize(QgsLayoutSize(right_w - 5.0, card1_h - 4.0, mm))
        curr_y += card1_h + gap

        # Card 2: Dual Live KPI Stat Cards (backed by real vector corner shapes)
        card2_h = 24.0
        k_gap = 2.5
        kw = (right_w - k_gap) / 2.0
        _add_layout_card_shape(lay, right_x, curr_y, kw, card2_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.8)
        _add_layout_card_shape(lay, right_x + kw + k_gap, curr_y, kw, card2_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.8)

        lbl_b2_l = QgsLayoutItemLabel(lay)
        lbl_b2_l.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_b2_l.setText(
            f"<div style='font-family:Arial, sans-serif; text-align:center;'>"
            f"<div style='font-size:6.5pt; font-weight:bold; color:#64748b; text-transform:uppercase;'>{_html.escape(kpis['kpi1_label'])}</div>"
            f"<div style='font-size:12.5pt; font-weight:bold; color:#0f172a; margin-top:2px;'>{_html.escape(kpis['kpi1_value'])}</div>"
            f"<div style='font-size:6pt; color:#059669; margin-top:1px;'>{kpis['kpi1_sub']}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b2_l)
        lbl_b2_l.attemptMove(QgsLayoutPoint(right_x + 1.5, curr_y + 2.0, mm))
        lbl_b2_l.attemptResize(QgsLayoutSize(kw - 3.0, card2_h - 4.0, mm))

        lbl_b2_r = QgsLayoutItemLabel(lay)
        lbl_b2_r.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_b2_r.setText(
            f"<div style='font-family:Arial, sans-serif; text-align:center;'>"
            f"<div style='font-size:6.5pt; font-weight:bold; color:#64748b; text-transform:uppercase;'>{_html.escape(kpis['kpi2_label'])}</div>"
            f"<div style='font-size:12.5pt; font-weight:bold; color:#0284c7; margin-top:2px;'>{_html.escape(kpis['kpi2_value'])}</div>"
            f"<div style='font-size:6pt; color:#64748b; margin-top:1px;'>{kpis['kpi2_sub']}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b2_r)
        lbl_b2_r.attemptMove(QgsLayoutPoint(right_x + kw + k_gap + 1.5, curr_y + 2.0, mm))
        lbl_b2_r.attemptResize(QgsLayoutSize(kw - 3.0, card2_h - 4.0, mm))
        curr_y += card2_h + gap

        # Card 3: Categorized Legend Card (auto-pruned & humanized)
        card3_h = max(32.0, avail_h - curr_y + m - 22.0)
        _add_layout_card_shape(lay, right_x, curr_y, right_w, card3_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.8)
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        lay.addLayoutItem(leg)
        _configure_clean_legend(leg, mp, lay, title="LEGEND", title_pt=9.5, group_pt=8.0, item_pt=7.5, max_items_per_layer=10)
        leg.setBackgroundColor(QColor(255, 255, 255, 0))
        leg.setFrameEnabled(False)
        leg.attemptMove(QgsLayoutPoint(right_x + 2.0, curr_y + 2.0, mm))
        leg.attemptResize(QgsLayoutSize(right_w - 4.0, card3_h - 4.0, mm))
        curr_y += card3_h + gap

        # Card 4: Metadata & Provenance Card
        card4_h = max(14.0, avail_h - curr_y + m)
        _add_layout_card_shape(lay, right_x, curr_y, right_w, card4_h, fill="#ffffff", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.8)
        lbl_b4 = QgsLayoutItemLabel(lay)
        lbl_b4.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        crs_code = kpis["crs_authid"]
        scale_val = int(round(mp.scale())) if mp.scale() else 0
        scale_txt = f"1:{scale_val:,}" if scale_val > 0 else "Auto"
        lbl_b4.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6.8pt; color:#475569; line-height:1.3;'>"
            f"<b>Sources:</b> {_html.escape(credits or 'Official Spatial Repositories')}<br>"
            f"<b>CRS:</b> {crs_code} &bull; <b>Scale:</b> {scale_txt}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b4)
        lbl_b4.attemptMove(QgsLayoutPoint(right_x + 2.5, curr_y + 2.0, mm))
        lbl_b4.attemptResize(QgsLayoutSize(right_w - 5.0, card4_h - 4.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
                "bento_cards": 4}

    # =========================================================================
    # THEME 10: DUAL SYNCHRONIZED COMPARISON (Side-by-Side Temporal / Scenario)
    # =========================================================================
    if is_dual:
        m = 8.0
        gap = 4.0
        head_h = 16.0
        # Top Comparison Header
        lbl_head = QgsLayoutItemLabel(lay)
        lbl_head.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_str = f"<div style='font-size:7.5px; color:#475569;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_head.setText(
            f"<div style='font-family:Arial, sans-serif; text-align:center; padding:2px; border-bottom:1px solid #cbd5e1;'>"
            f"<div style='font-size:12px; font-weight:800; color:#0f172a; text-transform:uppercase;'>{_html.escape(title or 'TEMPORAL / SCENARIO COMPARATIVE ATLAS')}</div>"
            f"{sub_str}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_head)
        lbl_head.attemptMove(QgsLayoutPoint(m, m, mm))
        lbl_head.attemptResize(QgsLayoutSize(W - 2 * m, head_h, mm))

        footer_h = 14.0
        map_top = m + head_h + gap
        map_h = H - map_top - footer_h - m - gap
        half_w = (W - 2 * m - gap) / 2.0

        # Left Map (Baseline / Scenario A)
        mp1 = QgsLayoutItemMap(lay)
        mp1.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp1)
        mp1.setCrs(p.crs())
        mp1.attemptMove(QgsLayoutPoint(m, map_top, mm))
        mp1.attemptResize(QgsLayoutSize(half_w, map_h, mm))
        ext1 = _setup_extent(mp1, extent_layer)
        mp1.setFrameEnabled(True)
        mp1.setFrameStrokeColor(QColor("#cbd5e1"))
        mp1.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        lbl_a = QgsLayoutItemLabel(lay)
        lbl_a.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_a.setText(
            f"<div style='font-family:Arial, sans-serif; background:#0f172a; color:#ffffff; font-size:7.5pt; font-weight:bold; padding:2px 6px;'>"
            f"PHASE A &bull; BASELINE STATE"
            f"</div>"
        )
        lay.addLayoutItem(lbl_a)
        lbl_a.attemptMove(QgsLayoutPoint(m + 3.0, map_top + 3.0, mm))
        lbl_a.attemptResize(QgsLayoutSize(48.0, 7.5, mm))

        # Right Map (Scenario B / Post-Event)
        mp2 = QgsLayoutItemMap(lay)
        mp2.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp2)
        mp2.setCrs(p.crs())
        mp2.attemptMove(QgsLayoutPoint(m + half_w + gap, map_top, mm))
        mp2.attemptResize(QgsLayoutSize(half_w, map_h, mm))
        if key_extent_layer:
            _setup_extent(mp2, key_extent_layer)
        else:
            mp2.zoomToExtent(ext1)
        mp2.setFrameEnabled(True)
        mp2.setFrameStrokeColor(QColor("#cbd5e1"))
        mp2.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        lbl_b = QgsLayoutItemLabel(lay)
        lbl_b.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_b.setText(
            f"<div style='font-family:Arial, sans-serif; background:#0284c7; color:#ffffff; font-size:7.5pt; font-weight:bold; padding:2px 6px;'>"
            f"PHASE B &bull; SCENARIO / IMPACT"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b)
        lbl_b.attemptMove(QgsLayoutPoint(m + half_w + gap + 3.0, map_top + 3.0, mm))
        lbl_b.attemptResize(QgsLayoutSize(50.0, 7.5, mm))

        # Bottom Shared Dock: Scale bar & Legend & Credits
        foot_y = map_top + map_h + gap
        _create_clean_scalebar(mp1, style="Stepped Line", custom_x=m + 2.0, custom_y=foot_y,
                               max_width=45.0, color="#0f172a", background="#ffffff", frame_color=None)

        lbl_meta = QgsLayoutItemLabel(lay)
        lbl_meta.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_meta.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:7pt; color:#64748b; line-height:1.25; text-align:right;'>"
            f"Synchronized Dual Extent &bull; Sources: {_html.escape(credits or 'Multi-temporal Datasets')} &bull; CRS: {kpis['crs_authid']}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_meta)
        lbl_meta.attemptMove(QgsLayoutPoint(W / 2.0, foot_y + 2.0, mm))
        lbl_meta.attemptResize(QgsLayoutSize(W / 2.0 - m, 10.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "maps": ["Map 1 (Baseline)", "Map 2 (Scenario)"]}

    # =========================================================================
    # THEME 11: CORRIDOR / STRIP MAP & ROUTE PROFILE (Panoramic Linear Feature)
    # =========================================================================
    if is_corridor:
        m = 7.0
        gap = 3.5
        head_h = 16.0

        lbl_head = QgsLayoutItemLabel(lay)
        lbl_head.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_head.setText(
            f"<div style='font-family:Arial, sans-serif; padding:2px;'>"
            f"<div style='font-size:13pt; font-weight:bold; color:#0f172a; text-transform:uppercase;'>{_html.escape(title or 'CORRIDOR ROUTE & TRAIL GUIDE')}</div>"
            f"<div style='font-size:8.5pt; color:#64748b;'>{_html.escape(subtitle or 'Linear Infrastructure & Spatial Profile')}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_head)
        lbl_head.attemptMove(QgsLayoutPoint(m, m, mm))
        lbl_head.attemptResize(QgsLayoutSize(W - 2 * m, head_h, mm))

        map_top = m + head_h + gap
        strip_h = 32.0
        map_h = H - map_top - strip_h - m - gap
        map_w = W - 2 * m

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#cbd5e1"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

        # Bottom Corridor Summary Strip (live KPIs, vector card border)
        strip_y = map_top + map_h + gap
        _add_layout_card_shape(lay, m, strip_y, map_w, strip_h, fill="#f8fafc", stroke="#cbd5e1", stroke_w=0.25, radius_mm=1.5)
        lbl_strip = QgsLayoutItemLabel(lay)
        lbl_strip.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        scale_val = int(round(mp.scale())) if mp.scale() else 0
        scale_txt = f"1:{scale_val:,}" if scale_val > 0 else "Auto"
        lbl_strip.setText(
            f"<div style='font-family:Arial, sans-serif;'>"
            f"<div style='font-size:7.5pt; font-weight:bold; color:#0284c7; text-transform:uppercase;'>CORRIDOR SUMMARY METRICS</div>"
            f"<table width='100%' style='margin-top:3px; font-size:7.5pt; color:#334155;'>"
            f"<tr>"
            f"<td width='28%'><b>{_html.escape(kpis['kpi1_label'])}:</b> {_html.escape(kpis['kpi1_value'])}</td>"
            f"<td width='28%'><b>{_html.escape(kpis['kpi2_label'])}:</b> {_html.escape(kpis['kpi2_value'])}</td>"
            f"<td width='22%'><b>Layers:</b> {kpis['layer_count']} active</td>"
            f"<td width='22%' align='right'><b>Scale:</b> {scale_txt}</td>"
            f"</tr>"
            f"</table>"
            f"<div style='font-size:6.8pt; color:#64748b; margin-top:4px;'>Data Sources: {_html.escape(credits or 'Open Route Data')} &bull; Projected CRS: {kpis['crs_authid']}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_strip)
        lbl_strip.attemptMove(QgsLayoutPoint(m + 3.0, strip_y + 2.5, mm))
        lbl_strip.attemptResize(QgsLayoutSize(map_w - 6.0, strip_h - 5.0, mm))

        # Scale bar cleanly floating inside corridor map
        _create_clean_scalebar(mp, style="Stepped Line", position="bottom-left", pad=5.0,
                               color="#0f172a", background="#ffffff", frame_color=None)

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 12: CYBERPUNK / SYNTHWAVE NEON HUD (Dark Sci-Fi Geospatial HUD)
    # =========================================================================
    if is_cyberpunk:
        page.setBackgroundColor(QColor("#090a0f"))
        m = 8.0
        avail_w = W - 2 * m
        avail_h = H - 2 * m

        # Outer Neon Cyan Accent Bracket
        hud_neat = QgsLayoutItemShape(lay)
        hud_neat.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        hud_neat.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#00f0ff", "outline_width": "0.5"}))
        lay.addLayoutItem(hud_neat)
        hud_neat.attemptMove(QgsLayoutPoint(m - 2.0, m - 2.0, mm))
        hud_neat.attemptResize(QgsLayoutSize(avail_w + 4.0, avail_h + 4.0, mm))

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(avail_w, avail_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#00f0ff"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))
        _apply_grid(mp, main_ext)

        # Floating HUD Telemetry Terminal Box (top-left)
        _add_layout_card_shape(lay, m + 4.0, m + 4.0, 80.0, 19.0, fill="#090a0f", stroke="#00f0ff", stroke_w=0.35, radius_mm=1.0)
        lbl_hud = QgsLayoutItemLabel(lay)
        lbl_hud.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_hud.setText(
            f"<div style='font-family:Courier New, monospace;'>"
            f"<div style='font-size:7pt; color:#00f0ff; letter-spacing:1px; font-weight:bold;'>// GEOSPATIAL MATRIX &bull; {kpis['total_features']:,} FEATURES</div>"
            f"<div style='font-size:11pt; font-weight:bold; color:#ff007f; text-transform:uppercase;'>{_html.escape(title or 'CYBERPUNK NEON MATRIX')}</div>"
            f"<div style='font-size:6.5pt; color:#10b981;'>CRS: {kpis['crs_authid']} &bull; {kpis['layer_count']} ACTIVE LAYERS</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_hud)
        lbl_hud.attemptMove(QgsLayoutPoint(m + 6.0, m + 5.5, mm))
        lbl_hud.attemptResize(QgsLayoutSize(76.0, 16.0, mm))

        # Bottom Right Attribution HUD
        _add_layout_card_shape(lay, W - m - 68.0, H - m - 13.0, 64.0, 10.0, fill="#090a0f", stroke="#ff007f", stroke_w=0.35, radius_mm=1.0)
        lbl_tele = QgsLayoutItemLabel(lay)
        lbl_tele.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_tele.setText(
            f"<div style='font-family:Courier New, monospace; font-size:6.2pt; color:#94a3b8; text-align:right;'>"
            f"<span style='color:#ff007f; font-weight:bold;'>PROVENANCE:</span> {_html.escape(credits or 'Spatial Observation')}<br>"
            f"{_html.escape(kpis['kpi2_label'])}: {_html.escape(kpis['kpi2_value'])}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_tele)
        lbl_tele.attemptMove(QgsLayoutPoint(W - m - 66.5, H - m - 11.8, mm))
        lbl_tele.attemptResize(QgsLayoutSize(61.0, 8.0, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 13: NAUTICAL BATHYMETRIC & MARITIME CHART (Admiralty / Hydrographic)
    # =========================================================================
    if is_nautical:
        page.setBackgroundColor(QColor("#fbf5dc"))
        m = 9.0

        # Admiralty Double Border Neatline
        out_n = QgsLayoutItemShape(lay)
        out_n.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
        out_n.setSymbol(QgsFillSymbol.createSimple({"color": "transparent", "outline_color": "#1e293b", "outline_width": "0.7"}))
        lay.addLayoutItem(out_n)
        out_n.attemptMove(QgsLayoutPoint(m - 3.0, m - 3.0, mm))
        out_n.attemptResize(QgsLayoutSize(W - 2 * m + 6.0, H - 2 * m + 6.0, mm))

        map_w = W - 2 * m
        map_h = H - 2 * m

        mp = QgsLayoutItemMap(lay)
        mp.setRect(0, 0, 10, 10)
        lay.addLayoutItem(mp)
        mp.setCrs(p.crs())
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#1e293b"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.35, mm))
        _apply_grid(mp, main_ext)

        # Formal Nautical Cartouche in Ocean Space (bottom-right)
        cart_w = 78.0
        cart_h = 28.0
        _add_layout_card_shape(lay, W - m - cart_w - 4.0, H - m - cart_h - 4.0, cart_w, cart_h, fill="#fbf5dc", stroke="#1e293b", stroke_w=0.35, radius_mm=0.0)
        lbl_naut = QgsLayoutItemLabel(lay)
        lbl_naut.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_naut.setText(
            f"<div style='font-family:Times New Roman, serif; text-align:center;'>"
            f"<div style='font-size:6.5pt; letter-spacing:1px; color:#475569; text-transform:uppercase;'>HYDROGRAPHIC SURVEY & NAVIGATION CHART</div>"
            f"<div style='font-size:11pt; font-weight:bold; color:#0f172a; text-transform:uppercase; margin-top:2px;'>{_html.escape(title or 'COASTAL WATERS & CHANNELS')}</div>"
            f"<div style='font-size:6.8pt; font-style:italic; color:#334155; margin-top:2px;'>Chart Datum: {kpis['crs_authid']} &bull; {_html.escape(kpis['kpi2_value'])}</div>"
            f"<div style='font-size:6pt; color:#64748b; margin-top:2px;'>Data: {_html.escape(credits or 'Hydrographic Offices & Open Data')}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_naut)
        lbl_naut.attemptMove(QgsLayoutPoint(W - m - cart_w - 2.0, H - m - cart_h - 2.0, mm))
        lbl_naut.attemptResize(QgsLayoutSize(cart_w - 4.0, cart_h - 4.0, mm))

        # Compass Rose / North Arrow
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(14.0, 18.0, mm))
        na.attemptMove(QgsLayoutPoint(m + 4.0, m + 4.0, mm))

        # Nautical Scale bar
        _create_clean_scalebar(mp, style="Double Box", position="bottom-left", pad=6.0,
                               font_family="Times New Roman", font_size=6.5, color="#1e293b", secondary_color="#fbf5dc",
                               background="#fbf5dc", frame_color="#1e293b")

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

    # =========================================================================
    # THEME 14: DEFAULT / MODERN CLEAN / RELIEF TOPOGRAPHIC
    # =========================================================================
    m = 10.0
    header_h = 18.0 if title else 0
    if title:
        _add_layout_card_shape(lay, m, m, W - 2 * m, header_h, fill="#ffffff", stroke="#cfd8dc", stroke_w=0.25, radius_mm=1.5)
        header_card = QgsLayoutItemLabel(lay)
        header_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_txt = f"<div style='font-size: 9.5pt; color: #546e7a; margin-top: 2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        header_card.setText(
            f"<div style='font-family: Arial, sans-serif; text-align: center;'>"
            f"<div style='font-size: 14pt; font-weight: bold; color: #0f172a;'>{_html.escape(title)}</div>"
            f"{sub_txt}"
            f"</div>"
        )
        lay.addLayoutItem(header_card)
        header_card.attemptMove(QgsLayoutPoint(m + 2.5, m + 2.0, mm))
        header_card.attemptResize(QgsLayoutSize(W - 2 * m - 5.0, header_h - 4.0, mm))

    map_top = m + (header_h + 3.0 if title else 0)
    map_w = W - 2 * m
    map_h = H - map_top - m - 8.0

    mp = QgsLayoutItemMap(lay)
    mp.setRect(0, 0, 10, 10)
    lay.addLayoutItem(mp)
    mp.setCrs(p.crs())
    mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
    mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
    main_ext = _setup_extent(mp, extent_layer)
    mp.setFrameEnabled(True)
    mp.setFrameStrokeColor(QColor("#334155"))
    mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))
    if grid or is_relief:
        _apply_grid(mp, main_ext)

    _create_clean_scalebar(mp, style="Stepped Line" if not is_relief else "Double Box",
                           position="bottom-left", pad=6.0, color="#1e293b", background="#ffffff", frame_color=None)

    na = QgsLayoutItemPicture(lay)
    na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    na.setLinkedMap(mp)
    lay.addLayoutItem(na)
    na.attemptResize(QgsLayoutSize(11.0, 15.0, mm))
    na.attemptMove(QgsLayoutPoint(m + map_w - 16.0, map_top + 4.0, mm))

    if credits:
        lbl_cred = QgsLayoutItemLabel(lay)
        lbl_cred.setText(f"Sources: {credits} | Created with QGIS {Qgis.version()}")
        fmt = QgsTextFormat()
        fmt.setSize(7.5)
        lbl_cred.setTextFormat(fmt)
        lay.addLayoutItem(lbl_cred)
        lbl_cred.attemptMove(QgsLayoutPoint(m, H - m - 4.0, mm))
        lbl_cred.attemptResize(QgsLayoutSize(W - 2 * m, 5.0, mm))

    _register_layout(mgr, lay)
    return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}


@tool("Change any layout item text or the map scale/extent after creation.",
      {"name": S("string", "layout"), "map_scale": S("number", ""), "replace_text": S("object", "{old: new}")})
def edit_layout(name=None, map_scale=None, replace_text=None, **kwargs):
    target = name or kwargs.get("layout") or kwargs.get("layout_name")
    lay = _get_layout(target)
    for it in lay.items():
        if isinstance(it, QgsLayoutItemMap) and map_scale:
            it.setScale(float(map_scale))
        if isinstance(it, QgsLayoutItemLabel) and replace_text and it.text() in replace_text:
            it.setText(replace_text[it.text()])
    lay.refresh()
    return {"ok": True}


@tool("Export a layout to PDF, PNG, JPG or SVG (by file extension). Set preview=true to also get the PNG back as an image for verification.",
      {"name": S("string", "layout"), "path": S("string", "absolute output path"),
       "dpi": S("integer", "default 300"), "preview": S("boolean", "")})
def export_layout(name=None, path=None, dpi=300, preview=False, **kwargs):
    target_name = name or kwargs.get("layout") or kwargs.get("layout_name")
    target_path = path or kwargs.get("output_path") or kwargs.get("file_path") or kwargs.get("output")
    mgr = QgsProject.instance().layoutManager()
    if not target_name:
        layouts = mgr.printLayouts()
        if layouts:
            target_name = layouts[0].name()
        else:
            raise ValueError("No layout name provided and active project has no layouts.")
    lay = mgr.layoutByName(str(target_name))
    if not lay:
        for l in mgr.printLayouts():
            if l.name().lower() == str(target_name).lower():
                lay = l
                break
    if not lay:
        avail = [l.name() for l in mgr.printLayouts()]
        raise ValueError(f"No layout {target_name!r}. Available: {avail}")
    if not target_path:
        out_dir = paths.get_default_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        target_path = os.path.join(out_dir, f"{target_name}.png")
    path = target_path
    ex = QgsLayoutExporter(lay)
    ext = os.path.splitext(path)[1].lower()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if ext == ".pdf":
        st = QgsLayoutExporter.PdfExportSettings(); st.dpi = dpi
        r = ex.exportToPdf(path, st)
    elif ext == ".svg":
        st = QgsLayoutExporter.SvgExportSettings(); st.dpi = dpi
        r = ex.exportToSvg(path, st)
    else:
        st = QgsLayoutExporter.ImageExportSettings(); st.dpi = dpi
        r = ex.exportToImage(path, st)
    out = {"path": path, "success": r == QgsLayoutExporter.ExportResult.Success, "code": int(r)}
    if preview:
        raw_b64 = _export_layout_preview_b64(lay, dpi=80)
        out = {"__image__": raw_b64, "mime": "image/png", "meta": out}
    return out


@tool("Open the layout designer window for the user in normal (non-fullscreen) mode.", {"name": S("string", "")})
def open_layout_designer(name=None, **kwargs):
    target = name or kwargs.get("layout") or kwargs.get("layout_name")
    lay = _get_layout(target, open_window=True)
    _open_layout_window(lay)
    return {"opened": getattr(lay, "name", lambda: str(target))()}


# ---------------------------------------------------------------- skills
@tool("List or search specialized GIS skills and guidebooks available in this agent (e.g. choropleth, terrain, hydrology, remote sensing, CRS Indonesia, print layout).",
      {"query": S("string", "keyword or topic, e.g. 'flood', 'banjir', 'utm', 'kepadatan', 'layout'"), "limit": S("integer", "default 10")})
def list_skills(query="", limit=10):
    from .skills import REGISTRY
    return REGISTRY.search(query, limit=limit)


@tool("Load the full contents and instructions of a specialized skill by name, or a specific reference document from that skill.",
      {"name": S("string", "skill name, e.g. 'choropleth-density-map', 'coordinate-systems-and-crs', 'terrain-hydrology'"),
       "reference": S("string", "optional filename inside the skill references/ directory")})
def load_skill(name, reference=None):
    from .skills import REGISTRY
    return REGISTRY.load(name, reference=reference)


# ---------------------------------------------------------------- public geodata (COG, STAC, Overture, DuckDB)
@tool("List curated 100% free, keyless public geospatial data sources: Overture Maps, STAC APIs (Earth Search, Planetary Computer, USGS, NASA), COGs (Copernicus DEM, Sentinel-2, ESA WorldCover, WorldPop), OSM Overpass/Nominatim, geoBoundaries, Natural Earth, USGS Earthquakes, NASA EONET, and XYZ basemaps.")
def list_public_geodata_sources():
    return {
        "overture_maps": {
            "description": "Global open map features from Overture Maps Foundation (Meta, Microsoft, Amazon, TomTom) fetched via fast PMTiles vector tiles with automatic STAC-indexed GeoParquet fallback (zero API keys).",
            "themes": {
                "places": ["place"],
                "buildings": ["building"],
                "transportation": ["segment", "connector"],
                "base": ["water", "land", "land_use", "land_cover", "infrastructure", "bathymetry"],
                "divisions": ["division", "division_area", "division_boundary"],
            },
            "tool": "query_overture_maps"
        },
        "stac_apis": [
            {
                "name": "Earth Search by Element84",
                "url": "https://earth-search.aws.element84.com/v1",
                "auth": "None (100% Free Public)",
                "collections": ["sentinel-2-l2a", "sentinel-2-c1-l2a", "sentinel-1-grd", "cop-dem-glo-30", "cop-dem-glo-90", "landsat-c2-l2", "naip"]
            },
            {
                "name": "Microsoft Planetary Computer",
                "url": "https://planetarycomputer.microsoft.com/api/stac/v1",
                "auth": "Public open search (no key required for metadata & open assets)",
                "collections": ["sentinel-2-l2a", "landsat-c2-l2", "cop-dem-glo-30", "esa-worldcover", "nasadem", "alos-dem", "io-lulc-annual-v02", "modis-13Q1-061"]
            },
            {
                "name": "USGS LandsatLook STAC",
                "url": "https://landsatlook.usgs.gov/stac-server",
                "auth": "None (Public USGS catalog)",
                "collections": ["landsat-c2l2-sr", "landsat-c2l2-st"]
            },
            {
                "name": "NASA CMR-STAC (LPCLOUD)",
                "url": "https://cmr.earthdata.nasa.gov/stac/LPCLOUD",
                "auth": "Public search (Harmonized Landsat Sentinel-2)",
                "collections": ["HLSL30.v2.0", "HLSS30.v2.0"]
            }
        ],
        "public_cogs": {
            "copernicus_dem_glo_30": {
                "name": "Copernicus Global DEM 30m",
                "description": "High-precision global digital surface model at 30-meter resolution.",
                "stac_collection": "cop-dem-glo-30"
            },
            "sentinel_2_l2a": {
                "name": "Sentinel-2 Level-2A (Surface Reflectance)",
                "description": "10-meter optical imagery updated every 5 days globally with cloudless COGs.",
                "stac_collection": "sentinel-2-l2a"
            },
            "esa_worldcover": {
                "name": "ESA WorldCover 10m",
                "description": "Global 10-meter land cover map based on Sentinel-1 and Sentinel-2.",
                "s3_path": "s3://esa-worldcover/v200/2021/map/"
            },
            "nasadem": {
                "name": "NASA DEM 30m",
                "description": "Reprocessed SRTM 30m global elevation model.",
                "stac_collection": "nasadem"
            },
            "io_lulc_annual": {
                "name": "Impact Observatory / Esri 10m Annual LULC",
                "description": "Global 10m annual land use / land cover time series.",
                "stac_collection": "io-lulc-annual-v02"
            },
            "worldpop_1km": {
                "name": "WorldPop Global Population 1km",
                "description": "UN-adjusted global population density raster (1km COG/GeoTIFF).",
                "url": "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km_UNadj/2020/0_Mosaicked/ppp_2020_1km_Aggregated.tif"
            }
        },
        "vector_and_boundaries": {
            "osm_overpass": {
                "name": "OpenStreetMap Overpass API",
                "url": "https://overpass-api.de/api/interpreter",
                "description": "Live OSM vector features (roads, POIs, buildings, waterways, admin boundaries) via Overpass QL."
            },
            "osm_nominatim": {
                "name": "OpenStreetMap Nominatim Geocoding",
                "url": "https://nominatim.openstreetmap.org/search?format=json&q={query}",
                "description": "Free geocoding and bounding boxes for cities, districts, and landmarks."
            },
            "geoboundaries": {
                "name": "geoBoundaries Open Administrative Boundaries",
                "url": "https://www.geoboundaries.org/api/current/gbOpen/{ISO3}/{ADM0|ADM1|ADM2|ADM3}/",
                "description": "Global open administrative boundaries (GeoJSON) from country to district/sub-district."
            },
            "natural_earth": {
                "name": "Natural Earth Vector (GeoJSON)",
                "url": "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/",
                "description": "Global coastlines, countries, rivers, lakes, and populated places at 10m/50m/110m scales."
            }
        },
        "hazards_and_environment": {
            "usgs_earthquakes": {
                "name": "USGS Earthquake Catalog (GeoJSON)",
                "url": "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson",
                "description": "Real-time and historical global earthquake events."
            },
            "nasa_eonet": {
                "name": "NASA EONET Natural Events (GeoJSON)",
                "url": "https://eonet.gsfc.nasa.gov/api/v3/events/geojson",
                "description": "Real-time natural hazards (wildfires, volcanoes, severe storms, floods)."
            },
            "open_meteo_elevation": {
                "name": "Open-Meteo Elevation & Weather API",
                "url": "https://api.open-meteo.com/v1/elevation",
                "description": "Free keyless point elevation and weather/climate forecasts."
            }
        },
        "hydrology_and_water": {
            "jrc_global_surface_water": {
                "name": "JRC Global Surface Water 30m",
                "url": "https://global-surface-water.appspot.com/",
                "stac_collection": "jrc-gsw",
                "description": "Global 30m surface water occurrence, seasonality, and change."
            },
            "hydrosheds": {
                "name": "HydroSHEDS / HydroBASINS / HydroRIVERS",
                "url": "https://www.hydrosheds.org/products",
                "description": "Global watershed boundaries, river networks, and flow accumulation."
            }
        },
        "soil_climate_and_terrain": {
            "isric_soilgrids": {
                "name": "ISRIC SoilGrids 2.0 (250m COG)",
                "url": "https://files.isric.org/soilgrids/latest/data/",
                "description": "Global 250m soil organic carbon, pH, clay/sand/silt, nitrogen, and bulk density."
            },
            "openlandmap": {
                "name": "OpenLandMap Global Environmental COGs",
                "url": "https://s3.openlandmap.org/",
                "description": "Global 250m–1km soil, terrain, and biome Cloud-Optimized GeoTIFFs."
            },
            "chirps_rainfall": {
                "name": "CHIRPS v2.0 Global Precipitation",
                "url": "https://data.chc.ucsb.edu/products/CHIRPS-2.0/",
                "description": "Quasi-global daily, monthly, and annual rainfall GeoTIFF/COG datasets."
            },
            "worldclim": {
                "name": "WorldClim v2.1 Bioclimatic Variables",
                "url": "https://www.worldclim.org/data/worldclim21.html",
                "description": "Global historical and future bioclimatic rasters (19 vars, temp, precip)."
            },
            "gebco_bathymetry": {
                "name": "GEBCO Global Bathymetry & Elevation",
                "url": "https://www.gebco.net/data_and_products/gridded_bathymetry_data/",
                "description": "Global ocean bathymetry and land topography grid."
            }
        },
        "forests_and_biodiversity": {
            "hansen_gfc": {
                "name": "Hansen Global Forest Change 30m",
                "url": "https://storage.googleapis.com/earthenginepartners-hansen/GFC-2023-v1.11/",
                "description": "Global 30m tree canopy cover, annual forest loss, and gain."
            },
            "gbif_occurrences": {
                "name": "GBIF Global Biodiversity Occurrence API",
                "url": "https://api.gbif.org/v1/occurrence/search",
                "description": "Global species occurrence records (keyless REST API)."
            },
            "protected_planet_wdpa": {
                "name": "Protected Planet (WDPA)",
                "url": "https://www.protectedplanet.net/",
                "description": "World Database on Protected Areas."
            }
        },
        "urban_and_nighttime": {
            "ghsl": {
                "name": "Copernicus Global Human Settlement Layer (GHSL)",
                "url": "https://human-settlement.emergency.copernicus.eu/download.php",
                "description": "Global built-up surface, population grid, and urban centre boundaries."
            },
            "ms_global_buildings": {
                "name": "Microsoft Global ML Building Footprints",
                "url": "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv",
                "description": "Global AI-derived building footprints (also queried via Overture buildings)."
            },
            "viirs_nighttime_lights": {
                "name": "VIIRS Black Marble / Nighttime Lights",
                "url": "https://eogdata.mines.edu/products/vnl/",
                "description": "Global annual and monthly nighttime radiance rasters."
            }
        },
        "basemaps_xyz": {
            "tool": "add_basemap",
            "presets": ["osm", "carto_light", "carto_dark", "esri_imagery", "esri_topo", "opentopomap"]
        }
    }


@tool("Search public STAC catalogs (e.g. Earth Search Sentinel-2, Landsat, Copernicus DEM) for Cloud-Optimized GeoTIFFs (COGs) matching an area of interest across paginated results.",
      {"collections": S("array", "collection IDs e.g. ['sentinel-2-l2a'] or ['cop-dem-glo-30']", items={"type": "string"}),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] in WGS84 (default: current map canvas extent)"),
       "datetime": S("string", "optional date/range e.g. '2026-09-01/2026-10-31' or '2026-10-15'"),
       "limit": S("integer", "max items to return across pages, default 20"),
       "page_size": S("integer", "items requested per STAC page iteration, default 10"),
       "fetch_all": S("boolean", "iterate across STAC pages up to ceiling so full bbox coverage is returned (default true)"),
       "api_url": S("string", "STAC endpoint URL, default 'https://earth-search.aws.element84.com/v1'")})
def search_stac_items(collections, bbox=None, datetime=None, limit=20, page_size=10, fetch_all=True, api_url=None):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    from pystac_client import Client
    endpoint = api_url or "https://earth-search.aws.element84.com/v1"
    client = Client.open(endpoint)

    if not bbox:
        # Use current canvas extent in EPSG:4326 safely
        c = _canvas()
        cur_ext = c.extent() if c else None
        p_crs = QgsProject.instance().crs()
        wgs_ext = _safe_transform_extent(cur_ext, p_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        if wgs_ext and _is_valid_extent(wgs_ext):
            bbox = [round(wgs_ext.xMinimum(), 4), round(wgs_ext.yMinimum(), 4),
                    round(wgs_ext.xMaximum(), 4), round(wgs_ext.yMaximum(), 4)]
        else:
            bbox = [-180, -85, 180, 85]

    req_limit = max(1, int(limit or 20))
    max_items = max(req_limit, 20) if fetch_all else req_limit
    per_page = min(max_items, max(1, min(int(page_size or 10), 100)))
    params = {"collections": collections, "bbox": bbox, "max_items": max_items, "limit": per_page}
    if datetime:
        params["datetime"] = datetime

    search = client.search(**params)
    results = []
    for page_idx, page in enumerate(search.pages(), start=1):
        if CANCEL_REQUESTED:
            raise RuntimeError("Execution stopped by user.")
        for item in page:
            assets = {}
            for a_key, a_val in item.assets.items():
                if a_val.href and (a_val.href.endswith((".tif", ".tiff")) or a_val.media_type in ("image/tiff", "application/x-geotiff")):
                    assets[a_key] = {"href": a_val.href, "title": a_val.title or a_key}
            results.append({
                "id": item.id,
                "datetime": str(item.datetime or item.properties.get("datetime")),
                "cloud_cover": item.properties.get("eo:cloud_cover"),
                "bbox": item.bbox,
                "cog_assets": assets
            })
            if len(results) >= max_items:
                break
        _report_progress("search_stac_items", min(95, page_idx * 15), f"Fetched STAC page {page_idx} ({len(results)} items)...")
        if len(results) >= max_items:
            break
    return {"endpoint": endpoint, "searched_bbox": bbox, "found_count": len(results), "items": results}


@tool("Stream and load any public Cloud-Optimized GeoTIFF (COG) directly into QGIS over HTTP/S3 via GDAL /vsicurl/ without downloading the file to disk.",
      {"url": S("string", "public COG URL, e.g. 'https://sentinel-cogs.s3.us-west-2.amazonaws.com/.../B04.tif' or 's3://copernicus-dem-30m/...'"),
       "name": S("string", "optional layer name, e.g. 'Sentinel2_B04'")})
def load_public_cog(url, name=None):
    clean_url = url.strip()
    if clean_url.startswith("s3://"):
        parts = clean_url[5:].split("/", 1)
        bucket = parts[0]
        key = parts[1] if len(parts) > 1 else ""
        clean_url = f"https://{bucket}.s3.amazonaws.com/{key}"
    vsi_url = clean_url if clean_url.startswith("/vsi") else f"/vsicurl/{clean_url}"
    layer_name = name or os.path.splitext(os.path.basename(clean_url))[0]
    lyr = QgsRasterLayer(vsi_url, layer_name, "gdal")
    if not lyr.isValid():
        raise ValueError(f"Failed to load COG raster from {clean_url}. Check if the URL is accessible.")
    p = QgsProject.instance()
    is_first = len([l for l in p.mapLayers().values() if l.providerType() != "wms"]) == 0
    p.addMapLayer(lyr)
    _normalize_layer_order(p)
    if is_first and _is_valid_extent(lyr.extent()):
        safe_ext = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
        if safe_ext:
            c = _canvas()
            if c:
                safe_ext.scale(1.05)
                c.setExtent(safe_ext)
                c.refresh()
    else:
        _sanitize_canvas_extent()
    return _layer_info(lyr)


_DUCKDB_SESSION_SQL = (
    "SET s3_region='us-west-2';",
    "SET geometry_always_xy=true;",
    "SET memory_limit='1GB';",
    "SET threads=4;",
    "SET preserve_insertion_order=false;",
)


def _execute_duckdb_nonblocking(setup_sql_list, copy_query, fetch_scalar=False, timeout_sec=180):
    """Execute DuckDB SQL on a worker thread while keeping Qt events responsive, bounded, and cancellable."""
    try:
        import duckdb
    except ImportError:
        raise RuntimeError("duckdb is required for DuckDB spatial queries. Install it in your Python environment with: pip install duckdb")

    err_holder = [None]
    res_holder = [None]
    con_holder = [None]
    con_lock = threading.Lock()
    interrupted = [False]
    timed_out = [False]

    def _worker():
        con = None
        try:
            con = duckdb.connect()
            with con_lock:
                con_holder[0] = con
            if CANCEL_REQUESTED:
                return
            home_dir_sql = paths.get_home_dir().replace("\\", "/").replace("'", "''")
            tmp_dir_sql = tempfile.gettempdir().replace("\\", "/").replace("'", "''")
            con.execute(f"SET home_directory='{home_dir_sql}';")
            try:
                con.execute(f"SET temp_directory='{tmp_dir_sql}';")
            except Exception:  # nosec B110
                pass
            try:
                con.execute("LOAD spatial; LOAD httpfs;")
            except Exception:
                con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
            for sql in (setup_sql_list or ()):
                if CANCEL_REQUESTED:
                    return
                con.execute(sql)
            if CANCEL_REQUESTED:
                return
            if fetch_scalar:
                row = con.execute(copy_query).fetchone()
                res_holder[0] = row[0] if row else 0
            else:
                con.execute(copy_query)
        except Exception as exc:
            err_holder[0] = exc
        finally:
            with con_lock:
                active = con_holder[0]
                con_holder[0] = None
            if active is not None:
                try:
                    active.close()
                except Exception:  # nosec B110
                    pass

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    start_t = time.monotonic()
    while worker.is_alive():
        if CANCEL_REQUESTED and not interrupted[0]:
            interrupted[0] = True
            with con_lock:
                active_con = con_holder[0]
                if active_con is not None and hasattr(active_con, "interrupt"):
                    try:
                        active_con.interrupt()
                    except Exception:  # nosec B110
                        pass
        elif timeout_sec and (time.monotonic() - start_t) > timeout_sec and not timed_out[0]:
            timed_out[0] = True
            with con_lock:
                active_con = con_holder[0]
                if active_con is not None and hasattr(active_con, "interrupt"):
                    try:
                        active_con.interrupt()
                    except Exception:  # nosec B110
                        pass
        try:
            QCoreApplication.processEvents()
        except Exception:  # nosec B110
            pass
        worker.join(timeout=0.1)

    if CANCEL_REQUESTED or interrupted[0]:
        raise RuntimeError("Execution stopped by user.")
    if timed_out[0]:
        raise RuntimeError(f"DuckDB spatial query timed out after {int(timeout_sec)}s. Narrow your bbox or use PMTiles via query_overture_maps.")
    if err_holder[0] is not None:
        raise err_holder[0]
    return res_holder[0]


def _load_exported_gpkg_layer(out_file, layer_label, err_prefix="Failed to load exported data"):
    """Load an exported GeoPackage vector layer into QGIS and update canvas extent safely."""
    lyr = QgsVectorLayer(out_file, layer_label, "ogr")
    if not lyr.isValid():
        raise ValueError(f"{err_prefix} from {out_file}")
    p = QgsProject.instance()
    is_first = len([l for l in p.mapLayers().values() if l.providerType() != "wms"]) == 0
    p.addMapLayer(lyr)
    _normalize_layer_order(p)
    if is_first and _is_valid_extent(lyr.extent()):
        safe_ext = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
        if safe_ext:
            c = _canvas()
            if c:
                safe_ext.scale(1.05)
                c.setExtent(safe_ext)
                c.refresh()
    else:
        _sanitize_canvas_extent()
    return _layer_info(lyr)


from collections import OrderedDict

_OVERTURE_DEFAULT_RELEASE = "2026-10-21.0"
_OVERTURE_RELEASE_LOCK = threading.Lock()
_OVERTURE_RELEASE_CACHE = None
_PMTILES_HEADER_CACHE = {}
_PMTILES_LEAF_CACHE = {}
_PMTILES_TILE_CACHE = OrderedDict()
_PMTILES_TILE_CACHE_MAX = 32
_PMTILES_TILE_LOCK = threading.Lock()
_HTTP_SESSION = None
_HTTP_SESSION_LOCK = threading.Lock()
_OVERTURE_STAC_COL_CACHE = {}
_OVERTURE_STAC_ITEM_CACHE = {}

_OVERTURE_THEME_SCHEMA = {
    "places": [("id", "str"), ("name", "str"), ("category", "str"), ("confidence", "float")],
    "buildings": [("id", "str"), ("name", "str"), ("height", "float"), ("num_floors", "int")],
    "transportation": [("id", "str"), ("name", "str"), ("class", "str"), ("subtype", "str"), ("subclass", "str")],
    "base": [("id", "str"), ("name", "str"), ("subtype", "str"), ("class", "str")],
    "divisions": [
        ("id", "str"), ("name", "str"), ("subtype", "str"), ("class", "str"),
        ("admin_level", "int"), ("country", "str"), ("region", "str"),
    ],
}


def _get_http_session():
    """Return a shared connection-pooled requests.Session when available, else None."""
    global _HTTP_SESSION
    with _HTTP_SESSION_LOCK:
        if _HTTP_SESSION is not None:
            return _HTTP_SESSION or None
        try:
            import requests
            from requests.adapters import HTTPAdapter
            sess = requests.Session()
            sess.headers.update({"User-Agent": "QGIS-AI-Agent/1.1"})
            adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8)
            sess.mount("https://", adapter)
            sess.mount("http://", adapter)
            _HTTP_SESSION = sess
        except Exception:
            _HTTP_SESSION = False
        return _HTTP_SESSION or None


def _fetch_http_bytes(url, headers=None, timeout=12):
    """Download raw bytes over HTTP(S) with connection pooling and cancellation checks."""
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    sess = _get_http_session()
    if sess is not None:
        resp = sess.get(url, headers=headers or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.content
    hdrs = {"User-Agent": "QGIS-AI-Agent/1.1"}
    if headers:
        hdrs.update(headers)
    parsed_scheme = urllib.parse.urlparse(url).scheme.lower()
    if parsed_scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme '{parsed_scheme}'; only http/https are allowed.")
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
        return resp.read()


def _fetch_http_range(url, start, length, timeout=12):
    """Fetch a closed byte slice via HTTP Range header."""
    if length <= 0:
        return b""
    return _fetch_http_bytes(url, headers={"Range": f"bytes={start}-{start + length - 1}"}, timeout=timeout)


def _fetch_pmtiles_tile_cached(url, z, x, y, start, length):
    """Fetch a PMTiles tile with a bounded in-memory LRU cache to speed up repeat/filtered queries."""
    key = (url, z, x, y)
    with _PMTILES_TILE_LOCK:
        cached = _PMTILES_TILE_CACHE.get(key)
        if cached is not None:
            _PMTILES_TILE_CACHE.move_to_end(key)
            return cached
    raw = _fetch_http_range(url, start, length, timeout=12)
    with _PMTILES_TILE_LOCK:
        _PMTILES_TILE_CACHE[key] = raw
        _PMTILES_TILE_CACHE.move_to_end(key)
        while len(_PMTILES_TILE_CACHE) > _PMTILES_TILE_CACHE_MAX:
            _PMTILES_TILE_CACHE.popitem(last=False)
    return raw


def _get_overture_release(default=_OVERTURE_DEFAULT_RELEASE):
    """Discover and cache the latest Overture Maps release tag from stac.overturemaps.org."""
    global _OVERTURE_RELEASE_CACHE
    with _OVERTURE_RELEASE_LOCK:
        if _OVERTURE_RELEASE_CACHE:
            return _OVERTURE_RELEASE_CACHE
    try:
        raw = _fetch_http_bytes("https://stac.overturemaps.org/catalog.json", timeout=5)
        cat = json.loads(raw.decode("utf-8"))
        for link in cat.get("links", []):
            if link.get("latest") and link.get("href"):
                m = re.search(r"/(\d{4}-\d{2}-\d{2}\.\d+)/catalog\.json", link["href"])
                if m:
                    with _OVERTURE_RELEASE_LOCK:
                        _OVERTURE_RELEASE_CACHE = m.group(1)
                    return m.group(1)
    except Exception as exc:
        LOGGER.warning(f"Overture STAC release discovery failed ({exc}); using {default}")
    with _OVERTURE_RELEASE_LOCK:
        _OVERTURE_RELEASE_CACHE = default
    return default


def _pmtiles_rotate(n, x, y, rx, ry):
    if ry == 0:
        if rx == 1:
            x = n - 1 - x
            y = n - 1 - y
        x, y = y, x
    return x, y


def _zxy_to_tile_id(z, x, y):
    """Encode slippy-map tile (z, x, y) into a PMTiles v3 Hilbert tile ID."""
    if z == 0:
        return 0
    acc = ((1 << (z * 2)) - 1) // 3
    n = 1 << z
    d = 0
    s = n >> 1
    tx, ty = x, y
    while s > 0:
        rx = 1 if (tx & s) > 0 else 0
        ry = 1 if (ty & s) > 0 else 0
        d += s * s * ((3 * rx) ^ ry)
        tx, ty = _pmtiles_rotate(s, tx, ty, rx, ry)
        s >>= 1
    return acc + d


def _read_varint(buf, pos):
    shift = 0
    result = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, pos
        shift += 7


def _deserialize_pmtiles_dir(raw_bytes):
    """Decompress and decode a PMTiles v3 directory into parallel column tuples."""
    data = gzip.decompress(raw_bytes)
    n, pos = _read_varint(data, 0)
    tile_ids, run_lengths, lengths, offsets = [], [], [], []
    last_id = 0
    for _ in range(n):
        v, pos = _read_varint(data, pos)
        last_id += v
        tile_ids.append(last_id)
    for _ in range(n):
        v, pos = _read_varint(data, pos)
        run_lengths.append(v)
    for _ in range(n):
        v, pos = _read_varint(data, pos)
        lengths.append(v)
    for i in range(n):
        v, pos = _read_varint(data, pos)
        offsets.append(offsets[i - 1] + lengths[i - 1] if (v == 0 and i > 0) else v - 1)
    return tile_ids, offsets, lengths, run_lengths


def _find_pmtiles_entry(dir_tuple, target_id):
    tile_ids, offsets, lengths, run_lengths = dir_tuple
    idx = bisect.bisect_right(tile_ids, target_id) - 1
    if idx < 0:
        return None
    return tile_ids[idx], offsets[idx], lengths[idx], run_lengths[idx]


def _lonlat_to_tile_xy(lon, lat, z):
    n = 1 << z
    x = int((lon + 180.0) / 360.0 * n)
    lat_c = max(-85.05112878, min(85.05112878, lat))
    y = int((1.0 - math.asinh(math.tan(math.radians(lat_c))) / math.pi) / 2.0 * n)
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def _lonlat_to_3857(lon, lat):
    x = lon * 20037508.342789244 / 180.0
    lat_c = max(-85.05112878, min(85.05112878, lat))
    y = math.log(math.tan((90.0 + lat_c) * math.pi / 360.0)) * 20037508.342789244 / math.pi
    return x, y


def _pmtiles_header_and_root(url):
    """Fetch and cache the PMTiles v3 header + decoded root directory."""
    if url in _PMTILES_HEADER_CACHE:
        return _PMTILES_HEADER_CACHE[url]
    head = _fetch_http_range(url, 0, 16384, timeout=10)
    if len(head) < 102 or head[:7] != b"PMTiles" or head[7] != 3:
        raise ValueError(f"Invalid PMTiles v3 header at {url}")
    root_off, root_bytes, _, _, leaf_off, _, tile_off, _ = struct.unpack("<QQQQQQQQ", head[8:72])
    internal_comp, _tile_comp, _tile_type, min_zoom, max_zoom = struct.unpack("<BBBBB", head[97:102])
    if internal_comp != 2:
        raise ValueError(f"Unsupported PMTiles internal directory compression ({internal_comp}) at {url}")
    if root_off + root_bytes <= len(head):
        root_raw = head[root_off:root_off + root_bytes]
    else:
        root_raw = _fetch_http_range(url, root_off, root_bytes, timeout=10)
    hdr = {
        "root_dir": _deserialize_pmtiles_dir(root_raw),
        "leaf_dirs_offset": leaf_off,
        "tile_data_offset": tile_off,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
    }
    _PMTILES_HEADER_CACHE[url] = hdr
    return hdr


def _resolve_pmtiles_tile_range(url, hdr, z, x, y):
    """Locate the byte offset and length for tile (z, x, y), caching traversed leaf directories."""
    tid = _zxy_to_tile_id(z, x, y)
    cur_dir = hdr["root_dir"]
    for _ in range(4):
        ent = _find_pmtiles_entry(cur_dir, tid)
        if not ent:
            return None
        etid, off, length, rl = ent
        if rl == 0:
            cache_key = (url, off, length)
            if cache_key not in _PMTILES_LEAF_CACHE:
                raw_leaf = _fetch_http_range(url, hdr["leaf_dirs_offset"] + off, length, timeout=10)
                _PMTILES_LEAF_CACHE[cache_key] = _deserialize_pmtiles_dir(raw_leaf)
            cur_dir = _PMTILES_LEAF_CACHE[cache_key]
        else:
            if tid < etid + rl:
                return z, x, y, hdr["tile_data_offset"] + off, length
            return None
    return None


def _choose_pmtiles_tiles(xmin, ymin, xmax, ymax, min_zoom, max_zoom, t_name):
    """Select an appropriate PMTiles zoom level and tile list (up to 64 tiles for iterative batch fetching)."""
    def _tiles_for_z(z):
        tx0, ty0 = _lonlat_to_tile_xy(xmin, ymax, z)
        tx1, ty1 = _lonlat_to_tile_xy(xmax, ymin, z)
        return [(z, x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]

    max_allowed = 256
    tiles = _tiles_for_z(max_zoom)
    if len(tiles) <= max_allowed:
        return max_zoom, tiles

    if t_name in ("transportation", "base", "divisions"):
        floor_z = max(min_zoom, max_zoom - 3)
        for z in range(max_zoom - 1, floor_z - 1, -1):
            cand = _tiles_for_z(z)
            if len(cand) <= max_allowed:
                return z, cand

    return None, []


def _parse_json_primary(raw_val):
    if not raw_val:
        return None
    if isinstance(raw_val, dict):
        return raw_val.get("primary")
    try:
        parsed = json.loads(raw_val)
        if isinstance(parsed, dict):
            return parsed.get("primary")
    except Exception:  # nosec B110
        pass
    return None


def _build_lyr_field_index(lyr):
    defn = lyr.GetLayerDefn()
    return {defn.GetFieldDefn(i).GetName(): i for i in range(defn.GetFieldCount())}


def _extract_overture_pmtiles_attrs(feat, t_name, fallback_idx, field_idx=None):
    """Extract normalized Overture attributes from a decoded MVT feature using pre-resolved field indices."""
    if field_idx is None:
        defn = feat.GetDefnRef()
        field_idx = {defn.GetFieldDefn(i).GetName(): i for i in range(defn.GetFieldCount())}

    def _get(fname):
        idx = field_idx.get(fname)
        if idx is None or not feat.IsFieldSetAndNotNull(idx):
            return None
        return feat.GetField(idx)

    raw_id = _get("id")
    feat_id = str(raw_id) if raw_id not in (None, "") else f"pmt_{fallback_idx}"
    name = _get("@name") or _parse_json_primary(_get("names"))
    attrs = {"id": feat_id, "name": str(name) if name not in (None, "") else None}

    if t_name == "places":
        cat = _parse_json_primary(_get("taxonomy")) or _get("basic_category")
        attrs["category"] = str(cat) if cat not in (None, "") else None
        conf = _get("confidence")
        try:
            attrs["confidence"] = float(conf) if conf is not None else None
        except (TypeError, ValueError):
            attrs["confidence"] = None
    elif t_name == "buildings":
        h = _get("height")
        nf = _get("num_floors")
        attrs["height"] = float(h) if h is not None else None
        attrs["num_floors"] = int(nf) if nf is not None else None
    elif t_name == "transportation":
        for k in ("class", "subtype", "subclass"):
            v = _get(k)
            attrs[k] = str(v) if v not in (None, "") else None
    elif t_name == "base":
        for k in ("subtype", "class"):
            v = _get(k)
            attrs[k] = str(v) if v not in (None, "") else None
    elif t_name == "divisions":
        for k in ("subtype", "class", "country", "region"):
            v = _get(k)
            attrs[k] = str(v) if v not in (None, "") else None
        al = _get("admin_level")
        attrs["admin_level"] = int(al) if al is not None else None
    else:
        for k in ("subtype", "class"):
            v = _get(k)
            attrs[k] = str(v) if v not in (None, "") else None
    return attrs


def _balance_records_4x4(records, xmin, ymin, xmax, ymax, limit):
    """Select up to `limit` features evenly across a 4x4 grid covering [xmin, xmax] x [ymin, ymax], prioritizing higher-confidence named features."""
    if len(records) <= limit:
        return records
    n_grid = 4
    dx = max(1e-9, (xmax - xmin) / n_grid)
    dy = max(1e-9, (ymax - ymin) / n_grid)
    buckets = [[] for _ in range(n_grid * n_grid)]
    for rec in records:
        cx, cy = rec[2], rec[3]
        gx = max(0, min(n_grid - 1, int((cx - xmin) / dx)))
        gy = max(0, min(n_grid - 1, int((cy - ymin) / dy)))
        buckets[gy * n_grid + gx].append(rec)

    def _rec_score(rec):
        if len(rec) > 4:
            return rec[4]
        attrs = rec[0] if isinstance(rec[0], dict) else {}
        try:
            conf = float(attrs.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0
        return (1 if attrs.get("name") else 0, conf)

    for b in buckets:
        if len(b) > 1:
            b.sort(key=_rec_score, reverse=True)

    out = []
    pos = 0
    while len(out) < limit:
        added = False
        for b in buckets:
            if pos < len(b):
                out.append(b[pos])
                added = True
                if len(out) >= limit:
                    break
        if not added:
            break
        pos += 1
    return out


def _determine_ogr_wkb_type(records):
    """Choose a specific OGR geometry type when homogeneous, else wkbUnknown."""
    from osgeo import ogr

    families = set()
    for _, geom, _, _ in records:
        flat = ogr.GT_Flatten(geom.GetGeometryType())
        if flat in (ogr.wkbPoint, ogr.wkbMultiPoint):
            families.add("point")
        elif flat in (ogr.wkbLineString, ogr.wkbMultiLineString):
            families.add("line")
        elif flat in (ogr.wkbPolygon, ogr.wkbMultiPolygon):
            families.add("polygon")
        else:
            families.add("other")
    if families == {"point"}:
        return ogr.wkbPoint
    if families == {"line"}:
        return ogr.wkbMultiLineString
    if families == {"polygon"}:
        return ogr.wkbMultiPolygon
    return ogr.wkbUnknown


def _write_pmtiles_records_to_gpkg(records, fields_spec, gpkg_path):
    """Write normalized `(attrs, geom, cx, cy)` records to an EPSG:4326 GeoPackage."""
    from osgeo import ogr, osr

    if os.path.exists(gpkg_path):
        try:
            os.remove(gpkg_path)
        except Exception:  # nosec B110
            pass
    target_wkb = _determine_ogr_wkb_type(records)
    drv = ogr.GetDriverByName("GPKG")
    ds = drv.CreateDataSource(gpkg_path)
    if ds is None:
        raise RuntimeError(f"Failed to create GeoPackage at {gpkg_path}")
    try:
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(4326)
        srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        lyr = ds.CreateLayer("features", srs, target_wkb)
        type_map = {"str": ogr.OFTString, "float": ogr.OFTReal, "int": ogr.OFTInteger}
        for fname, fkind in fields_spec:
            lyr.CreateField(ogr.FieldDefn(fname, type_map.get(fkind, ogr.OFTString)))
        defn = lyr.GetLayerDefn()
        lyr.StartTransaction()
        for attrs, geom, _, _ in records:
            feat = ogr.Feature(defn)
            for fname, _ in fields_spec:
                val = attrs.get(fname)
                if val is not None:
                    feat.SetField(fname, val)
            out_geom = geom
            flat = ogr.GT_Flatten(geom.GetGeometryType())
            if target_wkb == ogr.wkbMultiPolygon and flat == ogr.wkbPolygon:
                out_geom = ogr.ForceToMultiPolygon(geom.Clone())
            elif target_wkb == ogr.wkbMultiLineString and flat == ogr.wkbLineString:
                out_geom = ogr.ForceToMultiLineString(geom.Clone())
            elif target_wkb == ogr.wkbPoint and flat == ogr.wkbMultiPoint and geom.GetGeometryCount() > 0:
                out_geom = geom.GetGeometryRef(0).Clone()
            feat.SetGeometry(out_geom)
            lyr.CreateFeature(feat)
        lyr.CommitTransaction()
    finally:
        ds = None


def _normalize_pmtiles_filter_sql(filter_sql):
    """Map nested Overture Parquet field paths in filter_sql to flat GPKG column names."""
    sql = filter_sql
    sql = re.sub(r"\bnames\.primary\b", "name", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\btaxonomy\.primary\b", "category", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bcategories\.primary\b", "category", sql, flags=re.IGNORECASE)
    return sql


def _count_gpkg_features(gpkg_path):
    from osgeo import ogr

    if not os.path.exists(gpkg_path):
        return 0
    ds = ogr.Open(gpkg_path)
    if ds is None:
        return 0
    try:
        lyr = ds.GetLayer(0)
        return int(lyr.GetFeatureCount()) if lyr is not None else 0
    finally:
        ds = None


def _deduplicate_gpkg_points(gpkg_path):
    """Remove cross-tile duplicate point features sharing identical Overture `id` values in-place."""
    from osgeo import ogr

    if not os.path.exists(gpkg_path):
        return 0
    ds = ogr.Open(gpkg_path, 1)
    if ds is None:
        return _count_gpkg_features(gpkg_path)
    try:
        lyr = ds.GetLayer(0)
        if lyr is None:
            return 0
        defn = lyr.GetLayerDefn()
        id_idx = defn.GetFieldIndex("id")
        if id_idx < 0:
            return int(lyr.GetFeatureCount())
        seen = set()
        dup_fids = []
        for feat in lyr:
            if not feat.IsFieldSetAndNotNull(id_idx):
                continue
            fid_val = str(feat.GetField(id_idx) or "")
            if not fid_val:
                continue
            if fid_val in seen:
                dup_fids.append(feat.GetFID())
            else:
                seen.add(fid_val)
        if dup_fids:
            lyr.StartTransaction()
            for dfid in dup_fids:
                lyr.DeleteFeature(dfid)
            lyr.CommitTransaction()
        return int(lyr.GetFeatureCount())
    finally:
        ds = None


def _apply_local_duckdb_filter(raw_gpkg, out_file, fields_spec, norm_filter, xmin, ymin, xmax, ymax, safe_limit, fetch_all=True, t_name="buildings"):
    """Run arbitrary SQL filter locally over a temporary PMTiles GeoPackage (full deduplicated extraction when fetch_all=True)."""
    raw_sql = raw_gpkg.replace("\\", "/").replace("'", "''")
    out_sql = out_file.replace("\\", "/").replace("'", "''")
    if fetch_all:
        agg_cols = []
        for fname, _ in fields_spec:
            if fname == "id":
                agg_cols.append("id")
            else:
                agg_cols.append(f"ANY_VALUE({fname}) AS {fname}")
        geom_expr = "ANY_VALUE(geom) AS geometry" if t_name == "places" else "ST_Union_Agg(geom) AS geometry"
        copy_sql = f"""
        COPY (
            SELECT {', '.join(agg_cols)}, {geom_expr}
            FROM st_read('{raw_sql}')
            WHERE ({norm_filter})
            GROUP BY id
            LIMIT {safe_limit}
        ) TO '{out_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');
        """  # nosec B608
    else:
        select_cols = ", ".join(fname for fname, _ in fields_spec)
        dx = max(1e-9, (xmax - xmin) / 4.0)
        dy = max(1e-9, (ymax - ymin) / 4.0)
        per_tile_limit = max(50, math.ceil(safe_limit / 16) * 2)
        copy_sql = f"""
        COPY (
            WITH _raw AS (
                SELECT {select_cols}, geom AS geometry,
                       FLOOR(((ST_XMin(geom) + ST_XMax(geom)) * 0.5 - {xmin}) / {dx}) AS _gx,
                       FLOOR(((ST_YMin(geom) + ST_YMax(geom)) * 0.5 - {ymin}) / {dy}) AS _gy
                FROM st_read('{raw_sql}')
                WHERE ({norm_filter})
            ),
            _ranked AS (
                SELECT * EXCLUDE (_gx, _gy),
                       ROW_NUMBER() OVER (PARTITION BY _gx, _gy) AS _tile_rn
                FROM _raw
            )
            SELECT * EXCLUDE (_tile_rn)
            FROM _ranked
            WHERE _tile_rn <= {per_tile_limit}
            ORDER BY _tile_rn
            LIMIT {safe_limit}
        ) TO '{out_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');
        """  # nosec B608
    local_setup = ("SET geometry_always_xy=true;", "SET preserve_insertion_order=false;")
    _execute_duckdb_nonblocking(local_setup, copy_sql)
    return _count_gpkg_features(out_file)


def _pmtiles_vectortranslate_to_gpkg(src_mvt, actual_lyr_name, field_idx, fields_spec, x3857_0, y3857_0, x3857_1, y3857_1, out_file, t_name="buildings", access_mode=None):
    """Fast C++ GDAL VectorTranslate extraction from decoded /vsimem/ MVT tiles directly to EPSG:4326 GPKG."""
    from osgeo import gdal

    cols = []
    for fname, ftype in fields_spec:
        if fname == "name":
            if "@name" in field_idx:
                cols.append('"@name" AS name')
            elif "name" in field_idx:
                cols.append('"name"')
            else:
                cols.append("CAST(NULL AS CHARACTER(254)) AS name")
        elif fname == "category":
            if "@category" in field_idx:
                cols.append('"@category" AS category')
            elif "basic_category" in field_idx:
                cols.append('"basic_category" AS category')
            elif "category" in field_idx:
                cols.append('"category"')
            elif "taxonomy" in field_idx:
                cols.append('"taxonomy" AS category')
            else:
                cols.append("CAST(NULL AS CHARACTER(64)) AS category")
        elif ftype == "float":
            cols.append(f'CAST("{fname}" AS FLOAT) AS {fname}' if fname in field_idx else f"CAST(NULL AS FLOAT) AS {fname}")
        elif ftype == "int":
            cols.append(f'CAST("{fname}" AS INTEGER) AS {fname}' if fname in field_idx else f"CAST(NULL AS INTEGER) AS {fname}")
        else:
            width = 128 if fname == "id" else 64
            cols.append(f'"{fname}"' if fname in field_idx else f"CAST(NULL AS CHARACTER({width})) AS {fname}")

    sql_stmt = f'SELECT {", ".join(cols)} FROM "{actual_lyr_name}"'  # nosec B608
    opts_kw = {
        "format": "GPKG",
        "spatFilter": [float(x3857_0), float(y3857_0), float(x3857_1), float(y3857_1)],
        "dstSRS": "EPSG:4326",
        "reproject": True,
        "geometryType": ("POINT" if t_name == "places" else "PROMOTE_TO_MULTI"),
        "layerName": "features",
        "SQLStatement": sql_stmt,
        "SQLDialect": "OGRSQL",
    }
    if access_mode and os.path.exists(out_file):
        opts_kw["accessMode"] = access_mode
    res = gdal.VectorTranslate(out_file, src_mvt, options=gdal.VectorTranslateOptions(**opts_kw))
    if res is not None:
        res = None
    return _count_gpkg_features(out_file)


def _fetch_overture_pmtiles_to_gpkg(t_name, t_type, xmin, ymin, xmax, ymax, release, filter_sql, safe_limit, out_file, fetch_all=True, page_size=1000):
    """Fetch Overture vector tiles iteratively in batches via PMTiles byte-ranges and write/append into a GeoPackage."""
    from osgeo import gdal, ogr, osr

    url = f"https://tiles.overturemaps.org/{release}/{t_name}.pmtiles"
    _report_progress("query_overture_maps", 10, f"Reading Overture {t_name} PMTiles index...")
    hdr = _pmtiles_header_and_root(url)
    target_z, tiles = _choose_pmtiles_tiles(xmin, ymin, xmax, ymax, hdr["min_zoom"], hdr["max_zoom"], t_name)
    if target_z is None or not tiles:
        raise RuntimeError(f"Bounding box spans too many z={hdr['max_zoom']} PMTiles; falling back to STAC Parquet.")

    tile_ranges = []
    for z, x, y in tiles:
        if CANCEL_REQUESTED:
            raise RuntimeError("Execution stopped by user.")
        tr = _resolve_pmtiles_tile_range(url, hdr, z, x, y)
        if tr is not None:
            tile_ranges.append(tr)
    if not tile_ranges:
        raise ValueError(f"No Overture PMTiles data at z={target_z} for theme='{t_name}' in bbox {[xmin, ymin, xmax, ymax]}.")

    fields_spec = _OVERTURE_THEME_SCHEMA.get(t_name, [("id", "str"), ("name", "str"), ("subtype", "str"), ("class", "str")])
    x3857_0, y3857_0 = _lonlat_to_3857(xmin, ymin)
    x3857_1, y3857_1 = _lonlat_to_3857(xmax, ymax)

    # C++ GDAL VectorTranslate iterative tile-batch path for all themes (places, buildings, transportation, base, divisions)
    if fetch_all or filter_sql or safe_limit >= 2500:
        tile_batch_size = 4
        batches = [tile_ranges[i:i + tile_batch_size] for i in range(0, len(tile_ranges), tile_batch_size)]
        vt_target = (out_file[:-5] + "_raw.gpkg") if filter_sql else out_file
        if os.path.exists(vt_target):
            try:
                os.remove(vt_target)
            except Exception:  # nosec B110
                pass
        vt_failed = False
        try:
            total_so_far = 0
            for b_idx, batch in enumerate(batches, start=1):
                if CANCEL_REQUESTED:
                    raise RuntimeError("Execution stopped by user.")
                pct = min(88, int(15 + 68 * (b_idx - 1) / max(1, len(batches))))
                _report_progress(
                    "query_overture_maps",
                    pct,
                    f"Fetching {t_name} tile batch {b_idx}/{len(batches)} ({len(batch)} tiles at z={target_z}, {total_so_far:,} features)...",
                )
                with ThreadPoolExecutor(max_workers=min(6, len(batch))) as pool:
                    fetched_batch = list(pool.map(lambda tr: (tr[0], tr[1], tr[2], _fetch_pmtiles_tile_cached(url, *tr)), batch))

                vsi_batch_base = f"/vsimem/ov_pmt_{t_name}_{int(time.time() * 1000)}_{threading.get_ident()}_b{b_idx}"
                written_batch_paths = []
                b_ds = None
                try:
                    for z, x, y, raw_bytes in fetched_batch:
                        p = f"{vsi_batch_base}/{z}/{x}/{y}.pbf"
                        gdal.FileFromMemBuffer(p, raw_bytes)
                        written_batch_paths.append(p)
                    src_mvt = f"MVT:{vsi_batch_base}/{target_z}"
                    b_ds = ogr.Open(src_mvt)
                    if b_ds is None:
                        continue
                    b_lyr = b_ds.GetLayerByName(t_type)
                    if b_lyr is None:
                        avail = [b_ds.GetLayer(i).GetName() for i in range(b_ds.GetLayerCount())]
                        if t_type == "water" and "water" in avail:
                            b_lyr = b_ds.GetLayerByName("water")
                        elif not avail:
                            continue
                        elif len(batches) == 1:
                            raise ValueError(f"PMTiles layer '{t_type}' not present in {t_name}.pmtiles (available: {avail})")
                        else:
                            continue
                    actual_lyr_name = b_lyr.GetName()
                    field_idx = _build_lyr_field_index(b_lyr)
                    b_ds = None
                    b_lyr = None
                    access_mode = "append" if os.path.exists(vt_target) else None
                    total_so_far = _pmtiles_vectortranslate_to_gpkg(
                        src_mvt, actual_lyr_name, field_idx, fields_spec,
                        x3857_0, y3857_0, x3857_1, y3857_1, vt_target,
                        t_name=t_name, access_mode=access_mode,
                    )
                finally:
                    b_ds = None
                    for p in written_batch_paths:
                        try:
                            gdal.Unlink(p)
                        except Exception:  # nosec B110
                            pass
                    if hasattr(gdal, "RmdirRecursive"):
                        try:
                            gdal.RmdirRecursive(vsi_batch_base)
                        except Exception:  # nosec B110
                            pass

            cnt = _count_gpkg_features(vt_target)
            if cnt == 0:
                if os.path.exists(vt_target):
                    try:
                        os.remove(vt_target)
                    except Exception:  # nosec B110
                        pass
                raise ValueError(f"No Overture features found for theme='{t_name}', type='{t_type}' in bbox {[xmin, ymin, xmax, ymax]}.")

            if not filter_sql:
                if t_name == "places":
                    cnt = _deduplicate_gpkg_points(vt_target)
                if fetch_all or cnt <= safe_limit:
                    _report_progress("query_overture_maps", 95, f"Loaded {cnt:,} {t_name} features")
                    return cnt
                if os.path.exists(vt_target):
                    os.remove(vt_target)
            else:
                try:
                    norm_filter = _normalize_pmtiles_filter_sql(filter_sql)
                    filtered_cnt = _apply_local_duckdb_filter(
                        vt_target, out_file, fields_spec, norm_filter,
                        xmin, ymin, xmax, ymax, safe_limit, fetch_all=fetch_all, t_name=t_name,
                    )
                    if filtered_cnt == 0:
                        raise ValueError(f"No Overture features matched filter_sql={filter_sql!r} in PMTiles.")
                    _report_progress("query_overture_maps", 95, f"Loaded {filtered_cnt:,} filtered {t_name} features")
                    return filtered_cnt
                finally:
                    if os.path.exists(vt_target):
                        try:
                            os.remove(vt_target)
                        except Exception:  # nosec B110
                            pass
        except ValueError:
            raise
        except Exception as vt_exc:
            vt_failed = True
            LOGGER.debug(f"GDAL VectorTranslate batched path skipped for {t_name}/{t_type} ({vt_exc}); falling back to OGR loop")
            if os.path.exists(vt_target):
                try:
                    os.remove(vt_target)
                except Exception:  # nosec B110
                    pass
        if vt_failed and os.path.exists(vt_target):
            try:
                os.remove(vt_target)
            except Exception:  # nosec B110
                pass

    # Fallback OGR path (used for explicit small 4x4 balanced samples when fetch_all=False or if C++ fast path failed)
    _report_progress("query_overture_maps", 25, f"Downloading {len(tile_ranges)} {t_name} PMTiles (z={target_z})...")
    with ThreadPoolExecutor(max_workers=min(6, len(tile_ranges))) as pool:
        fetched = list(pool.map(lambda tr: (tr[0], tr[1], tr[2], _fetch_pmtiles_tile_cached(url, *tr)), tile_ranges))

    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    _report_progress("query_overture_maps", 55, f"Decoding {t_name}/{t_type} vector tiles...")

    vsi_base = f"/vsimem/ov_pmt_{t_name}_{int(time.time() * 1000)}_{threading.get_ident()}"
    written_vsi_paths = []
    ds = None
    try:
        for z, x, y, raw_bytes in fetched:
            p = f"{vsi_base}/{z}/{x}/{y}.pbf"
            gdal.FileFromMemBuffer(p, raw_bytes)
            written_vsi_paths.append(p)

        src_mvt = f"MVT:{vsi_base}/{target_z}"
        ds = ogr.Open(src_mvt)
        if ds is None:
            raise RuntimeError(f"GDAL MVT driver failed to open {t_name} tiles")

        lyr = ds.GetLayerByName(t_type)
        if lyr is None:
            avail = [ds.GetLayer(i).GetName() for i in range(ds.GetLayerCount())]
            if t_type == "water" and "water" in avail:
                lyr = ds.GetLayerByName("water")
            elif avail:
                raise ValueError(f"PMTiles layer '{t_type}' not present in {t_name}.pmtiles (available: {avail})")
            else:
                raise RuntimeError(f"No layers found in decoded {t_name} PMTiles")

        actual_lyr_name = lyr.GetName()
        field_idx = _build_lyr_field_index(lyr)
        lyr.SetSpatialFilterRect(x3857_0, y3857_0, x3857_1, y3857_1)
        id_col_idx = field_idx.get("id")
        name_col_idx = field_idx.get("@name")
        conf_col_idx = field_idx.get("confidence")

        s_srs = osr.SpatialReference()
        s_srs.ImportFromEPSG(3857)
        t_srs = osr.SpatialReference()
        t_srs.ImportFromEPSG(4326)
        t_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        ct = osr.CoordinateTransformation(s_srs, t_srs)

        selected_ids = None
        if not fetch_all and not filter_sql and id_col_idx is not None:
            # 4x4 grid balancing only when caller explicitly requests fetch_all=False
            id_centers = {}
            for feat in lyr:
                if CANCEL_REQUESTED:
                    raise RuntimeError("Execution stopped by user.")
                geom = feat.GetGeometryRef()
                if geom is None or geom.IsEmpty():
                    continue
                raw_id = feat.GetField(id_col_idx) if feat.IsFieldSetAndNotNull(id_col_idx) else None
                fid = str(raw_id) if raw_id not in (None, "") else f"pmt_{feat.GetFID()}"
                if fid not in id_centers:
                    ex0, ex1, ey0, ey1 = geom.GetEnvelope()
                    if ex1 >= x3857_0 and ex0 <= x3857_1 and ey1 >= y3857_0 and ey0 <= y3857_1:
                        has_name = 1 if (name_col_idx is not None and feat.IsFieldSetAndNotNull(name_col_idx) and str(feat.GetField(name_col_idx) or "").strip()) else 0
                        try:
                            conf_val = float(feat.GetField(conf_col_idx)) if (conf_col_idx is not None and feat.IsFieldSetAndNotNull(conf_col_idx)) else 0.0
                        except (TypeError, ValueError):
                            conf_val = 0.0
                        id_centers[fid] = (fid, None, (ex0 + ex1) * 0.5, (ey0 + ey1) * 0.5, (has_name, conf_val))
            balanced = _balance_records_4x4(list(id_centers.values()), x3857_0, y3857_0, x3857_1, y3857_1, safe_limit)
            selected_ids = {rec[0] for rec in balanced}
            lyr.ResetReading()

        by_id = {}
        for feat in lyr:
            if CANCEL_REQUESTED:
                raise RuntimeError("Execution stopped by user.")
            if selected_ids is not None:
                raw_id = feat.GetField(id_col_idx) if feat.IsFieldSetAndNotNull(id_col_idx) else None
                fid = str(raw_id) if raw_id not in (None, "") else f"pmt_{feat.GetFID()}"
                if fid not in selected_ids:
                    continue
            else:
                fid = None

            geom = feat.GetGeometryRef()
            if geom is None or geom.IsEmpty():
                continue
            attrs = _extract_overture_pmtiles_attrs(feat, t_name, feat.GetFID(), field_idx)
            if fid is None:
                fid = attrs["id"]
            if fid not in by_id:
                by_id[fid] = [attrs, [geom.Clone()]]
            else:
                by_id[fid][1].append(geom.Clone())

        records = []
        for attrs, parts in by_id.values():
            if CANCEL_REQUESTED:
                raise RuntimeError("Execution stopped by user.")
            geom = parts[0]
            for extra in parts[1:]:
                try:
                    merged = geom.Union(extra)
                    if merged is not None and not merged.IsEmpty():
                        geom = merged
                except Exception:  # nosec B110
                    pass
            if geom.Transform(ct) != 0:
                continue
            ex0, ex1, ey0, ey1 = geom.GetEnvelope()
            if ex1 < xmin or ex0 > xmax or ey1 < ymin or ey0 > ymax:
                continue
            records.append((attrs, geom, (ex0 + ex1) * 0.5, (ey0 + ey1) * 0.5))
    finally:
        ds = None
        for p in written_vsi_paths:
            try:
                gdal.Unlink(p)
            except Exception:  # nosec B110
                pass
        if hasattr(gdal, "RmdirRecursive"):
            try:
                gdal.RmdirRecursive(vsi_base)
            except Exception:  # nosec B110
                pass

    if not records:
        raise ValueError(f"No Overture features found for theme='{t_name}', type='{t_type}' in bbox {[xmin, ymin, xmax, ymax]}.")

    if not filter_sql:
        if (not fetch_all) or len(records) > safe_limit:
            records = _balance_records_4x4(records, xmin, ymin, xmax, ymax, safe_limit)
        _report_progress("query_overture_maps", 85, f"Writing {len(records)} {t_name} features to GeoPackage...")
        _write_pmtiles_records_to_gpkg(records, fields_spec, out_file)
        _report_progress("query_overture_maps", 95, f"Loading {t_name} layer...")
        return len(records)

    raw_tmp = out_file[:-5] + "_raw.gpkg"
    try:
        _report_progress("query_overture_maps", 75, f"Filtering {len(records)} {t_name} features...")
        _write_pmtiles_records_to_gpkg(records, fields_spec, raw_tmp)
        norm_filter = _normalize_pmtiles_filter_sql(filter_sql)
        filtered_cnt = _apply_local_duckdb_filter(
            raw_tmp, out_file, fields_spec, norm_filter,
            xmin, ymin, xmax, ymax, safe_limit, fetch_all=fetch_all, t_name=t_name,
        )
        if filtered_cnt == 0:
            raise ValueError(f"No Overture features matched filter_sql={filter_sql!r} in PMTiles.")
        _report_progress("query_overture_maps", 95, f"Loading {t_name} layer...")
        return filtered_cnt
    finally:
        if os.path.exists(raw_tmp):
            try:
                os.remove(raw_tmp)
            except Exception:  # nosec B110
                pass


def _resolve_overture_stac_parquet_urls(theme, t_type, xmin, ymin, xmax, ymax, release):
    """Resolve exact HTTPS GeoParquet partition URLs intersecting a WGS84 bbox via Overture STAC."""
    col_key = (release, theme, t_type)
    if col_key in _OVERTURE_STAC_COL_CACHE:
        item_bboxes, item_hrefs = _OVERTURE_STAC_COL_CACHE[col_key]
    else:
        col_url = f"https://stac.overturemaps.org/{release}/{theme}/{t_type}/collection.json"
        col = json.loads(_fetch_http_bytes(col_url, timeout=8).decode("utf-8"))
        raw_bboxes = col.get("extent", {}).get("spatial", {}).get("bbox", [])
        item_hrefs = [lnk["href"] for lnk in col.get("links", []) if lnk.get("rel") == "item" and lnk.get("href")]
        item_bboxes = raw_bboxes[1:] if len(raw_bboxes) == len(item_hrefs) + 1 else raw_bboxes
        _OVERTURE_STAC_COL_CACHE[col_key] = (item_bboxes, item_hrefs)

    matching_hrefs = [
        item_hrefs[i]
        for i, b in enumerate(item_bboxes)
        if i < len(item_hrefs) and len(b) >= 4 and b[2] >= xmin and b[0] <= xmax and b[3] >= ymin and b[1] <= ymax
    ]
    missing = [h for h in matching_hrefs if h not in _OVERTURE_STAC_ITEM_CACHE]
    if missing:
        def _fetch_item(href):
            it = json.loads(_fetch_http_bytes(href, timeout=8).decode("utf-8"))
            aws_href = it.get("assets", {}).get("aws", {}).get("href")
            return href, aws_href

        with ThreadPoolExecutor(max_workers=min(6, len(missing))) as pool:
            for href, aws_href in pool.map(_fetch_item, missing):
                if aws_href:
                    _OVERTURE_STAC_ITEM_CACHE[href] = aws_href

    urls = []
    seen = set()
    for href in matching_hrefs:
        u = _OVERTURE_STAC_ITEM_CACHE.get(href)
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _overture_select_clause(t_name):
    if t_name == "places":
        return "id, names.primary as name, taxonomy.primary as category, confidence, geometry"
    if t_name == "buildings":
        return "id, names.primary as name, height, num_floors, geometry"
    if t_name == "transportation":
        return "id, names.primary as name, class, subtype, subclass, geometry"
    if t_name == "base":
        return "id, names.primary as name, subtype, class, geometry"
    if t_name == "divisions":
        return "id, names.primary as name, subtype, class, admin_level, country, region, geometry"
    return "id, names.primary as name, subtype, class, geometry"


def _iter_spatial_bbox_chunks(xmin, ymin, xmax, ymax, t_name):
    """Split a bbox into lightweight spatial tile chunks when it exceeds theme thresholds."""
    max_total_deg = 1.5 if t_name == "buildings" else (6.0 if t_name == "divisions" else 3.0)
    span_x = max(1e-9, xmax - xmin)
    span_y = max(1e-9, ymax - ymin)
    if span_x > max_total_deg or span_y > max_total_deg:
        raise ValueError(
            f"Bounding box span ({span_x:.2f}° × {span_y:.2f}°) exceeds safe limit ({max_total_deg}° × {max_total_deg}°) "
            f"for Overture '{t_name}'. Narrow the bbox to a city or district."
        )

    chunk_deg = 0.04 if t_name == "buildings" else (0.5 if t_name == "divisions" else 0.12)
    nx = max(1, math.ceil(span_x / chunk_deg))
    ny = max(1, math.ceil(span_y / chunk_deg))
    if nx * ny > 25:
        raise ValueError(
            f"Bounding box requires {nx * ny} spatial chunks for Overture '{t_name}' (max 25). "
            f"Narrow the bbox to prevent multi-GB download."
        )

    dx = span_x / nx
    dy = span_y / ny
    chunks = []
    for ix in range(nx):
        cx0 = xmin + ix * dx
        cx1 = xmax if ix == nx - 1 else xmin + (ix + 1) * dx
        for iy in range(ny):
            cy0 = ymin + iy * dy
            cy1 = ymax if iy == ny - 1 else ymin + (iy + 1) * dy
            chunks.append((cx0, cy0, cx1, cy1, ix == nx - 1, iy == ny - 1))
    return chunks


def _query_overture_stac_parquet_to_gpkg(t_name, t_type, xmin, ymin, xmax, ymax, release, filter_sql, safe_limit, out_file, fetch_all=True, page_size=2000):
    """Lightweight STAC-indexed GeoParquet fetch with bbox threshold and automatic spatial tile chunking."""
    from osgeo import gdal

    try:
        import duckdb  # noqa: F401
    except ImportError:
        raise RuntimeError("duckdb is required for GeoParquet Overture queries. Install it in your Python environment with: pip install duckdb")

    chunks = _iter_spatial_bbox_chunks(xmin, ymin, xmax, ymax, t_name) if fetch_all else [(xmin, ymin, xmax, ymax, True, True)]
    select_clause = _overture_select_clause(t_name)
    max_partitions = 25 if t_name == "buildings" else 40

    if len(chunks) == 1:
        _report_progress("query_overture_maps", 15, f"Resolving STAC Parquet partitions for {t_name}/{t_type}...")
        try:
            parquet_urls = _resolve_overture_stac_parquet_urls(t_name, t_type, xmin, ymin, xmax, ymax, release)
        except Exception as exc:
            raise RuntimeError(
                f"Overture STAC partition resolution failed for {t_name}/{t_type} ({exc}). "
                f"Skipping full-bucket S3 scan to prevent QGIS freeze; try PMTiles (source='pmtiles') or a smaller bbox."
            ) from exc

        if not parquet_urls:
            raise ValueError(f"No Overture STAC Parquet partitions found for theme='{t_name}', type='{t_type}' in bbox {[xmin, ymin, xmax, ymax]}.")
        if len(parquet_urls) > max_partitions:
            raise ValueError(
                f"Bounding box {[xmin, ymin, xmax, ymax]} spans {len(parquet_urls)} {t_name} Parquet partitions "
                f"(limit {max_partitions}). Narrow the bbox to prevent multi-GB download and memory exhaustion."
            )

        quoted = ", ".join("'" + u.replace("'", "''") + "'" for u in parquet_urls)
        parquet_source = f"read_parquet([{quoted}])"
        _report_progress("query_overture_maps", 35, f"Streaming {len(parquet_urls)} STAC-matched {t_name} Parquet file(s)...")

        where_clauses = [
            f"bbox.xmax >= {xmin} AND bbox.xmin <= {xmax}",
            f"bbox.ymax >= {ymin} AND bbox.ymin <= {ymax}",
        ]
        if filter_sql:
            where_clauses.append(f"({filter_sql})")

        out_file_sql = out_file.replace("\\", "/").replace("'", "''")
        if not fetch_all:
            dx = max(1e-9, (xmax - xmin) / 4.0)
            dy = max(1e-9, (ymax - ymin) / 4.0)
            per_tile_limit = max(50, math.ceil(safe_limit / 16) * 2)
            copy_query = f"""
            COPY (
                WITH _raw AS (
                    SELECT {select_clause},
                           FLOOR(((bbox.xmin + bbox.xmax) * 0.5 - {xmin}) / {dx}) AS _gx,
                           FLOOR(((bbox.ymin + bbox.ymax) * 0.5 - {ymin}) / {dy}) AS _gy
                    FROM {parquet_source}
                    WHERE {' AND '.join(where_clauses)}
                ),
                _ranked AS (
                    SELECT * EXCLUDE (_gx, _gy),
                           ROW_NUMBER() OVER (PARTITION BY _gx, _gy) AS _tile_rn
                    FROM _raw
                )
                SELECT * EXCLUDE (_tile_rn)
                FROM _ranked
                WHERE _tile_rn <= {per_tile_limit}
                ORDER BY _tile_rn
                LIMIT {safe_limit}
            ) TO '{out_file_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');
            """  # nosec B608
        else:
            copy_query = f"""
            COPY (
                SELECT {select_clause}
                FROM {parquet_source}
                WHERE {' AND '.join(where_clauses)}
                LIMIT {safe_limit}
            ) TO '{out_file_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');
            """  # nosec B608

        _execute_duckdb_nonblocking(_DUCKDB_SESSION_SQL, copy_query)
        cnt = _count_gpkg_features(out_file) if os.path.exists(out_file) else 0
    else:
        total_cnt = 0
        target_lyr_name = None
        written_first = False
        for c_idx, (cx0, cy0, cx1, cy1, is_last_x, is_last_y) in enumerate(chunks, start=1):
            if CANCEL_REQUESTED:
                raise RuntimeError("Execution stopped by user.")
            if total_cnt >= safe_limit:
                break

            pct = min(92, int(15 + 75 * (c_idx - 1) / len(chunks)))
            _report_progress(
                "query_overture_maps",
                pct,
                f"Fetching {t_name} spatial chunk {c_idx}/{len(chunks)} ({total_cnt:,} features)...",
            )
            try:
                chunk_urls = _resolve_overture_stac_parquet_urls(t_name, t_type, cx0, cy0, cx1, cy1, release)
            except Exception as exc:
                raise RuntimeError(
                    f"Overture STAC partition resolution failed on chunk {c_idx}/{len(chunks)} ({exc})."
                ) from exc
            if not chunk_urls:
                continue

            quoted = ", ".join("'" + u.replace("'", "''") + "'" for u in chunk_urls)
            parquet_source = f"read_parquet([{quoted}])"
            op_x = "<=" if is_last_x else "<"
            op_y = "<=" if is_last_y else "<"
            where_clauses = [
                f"bbox.xmax >= {cx0} AND bbox.xmin <= {cx1}",
                f"bbox.ymax >= {cy0} AND bbox.ymin <= {cy1}",
                f"((bbox.xmin + bbox.xmax) * 0.5) >= {cx0} AND ((bbox.xmin + bbox.xmax) * 0.5) {op_x} {cx1}",
                f"((bbox.ymin + bbox.ymax) * 0.5) >= {cy0} AND ((bbox.ymin + bbox.ymax) * 0.5) {op_y} {cy1}",
            ]
            if filter_sql:
                where_clauses.append(f"({filter_sql})")

            rem_limit = safe_limit - total_cnt
            chunk_path = out_file if not written_first else f"{out_file[:-5]}_c{c_idx}.gpkg"
            chunk_sql = chunk_path.replace("\\", "/").replace("'", "''")
            copy_query = f"""
            COPY (
                SELECT {select_clause}
                FROM {parquet_source}
                WHERE {' AND '.join(where_clauses)}
                LIMIT {rem_limit}
            ) TO '{chunk_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');
            """  # nosec B608
            try:
                _execute_duckdb_nonblocking(_DUCKDB_SESSION_SQL, copy_query)
                c_cnt = _count_gpkg_features(chunk_path) if os.path.exists(chunk_path) else 0
                if not written_first:
                    if c_cnt == 0:
                        if os.path.exists(chunk_path):
                            os.remove(chunk_path)
                        continue
                    written_first = True
                    total_cnt = c_cnt
                    target_lyr_name = _gpkg_first_layer_name(out_file) or "features"
                else:
                    if c_cnt > 0:
                        vt_opts = gdal.VectorTranslateOptions(
                            format="GPKG",
                            accessMode="append",
                            layerName=target_lyr_name or "features",
                        )
                        res = gdal.VectorTranslate(out_file, chunk_path, options=vt_opts)
                        if res is not None:
                            res = None
                        total_cnt += c_cnt
            finally:
                if written_first and chunk_path != out_file and os.path.exists(chunk_path):
                    try:
                        os.remove(chunk_path)
                    except Exception:  # nosec B110
                        pass
        cnt = _count_gpkg_features(out_file) if os.path.exists(out_file) else 0

    if cnt == 0:
        if os.path.exists(out_file):
            try:
                os.remove(out_file)
            except Exception:  # nosec B110
                pass
        raise ValueError(f"No Overture features found for theme='{t_name}', type='{t_type}' in bbox {[xmin, ymin, xmax, ymax]}.")
    _report_progress("query_overture_maps", 95, f"Loading {cnt} {t_name} features...")
    return cnt


def _rewrite_overture_s3_glob_in_sql(query):
    """Replace slow full-S3 Overture read_parquet globs with STAC-pruned HTTPS partition URLs when bbox bounds exist."""
    pattern = re.compile(
        r"read_parquet\(\s*'s3://overturemaps-us-west-2/release/([^/]+)/theme=([^/]+)/type=([^/*']+)/(?:\*(?:\.parquet)?)?'"
        r"(?:\s*,\s*hive_partitioning\s*=\s*[^)]+)?\s*\)",
        flags=re.IGNORECASE,
    )
    if not pattern.search(query):
        return query

    def _bound(col, ops):
        flip = {">=": "<=", ">": "<", "<=": ">=", "<": ">"}
        vals = []
        for op in ops:
            for m in re.finditer(rf"\b{col}\s*{op}\s*(-?\d+(?:\.\d+)?)", query, flags=re.IGNORECASE):
                vals.append(float(m.group(1)))
            rev_op = flip.get(op)
            if rev_op:
                for m in re.finditer(rf"(-?\d+(?:\.\d+)?)\s*{rev_op}\s*{col}\b", query, flags=re.IGNORECASE):
                    vals.append(float(m.group(1)))
        return vals

    xmins = _bound(r"bbox\.xmax", [">=", ">"]) + _bound(r"bbox\.xmin", [">=", ">"])
    xmaxs = _bound(r"bbox\.xmin", ["<=", "<"]) + _bound(r"bbox\.xmax", ["<=", "<"])
    ymins = _bound(r"bbox\.ymax", [">=", ">"]) + _bound(r"bbox\.ymin", [">=", ">"])
    ymaxs = _bound(r"bbox\.ymin", ["<=", "<"]) + _bound(r"bbox\.ymax", ["<=", "<"])
    if not (xmins and xmaxs and ymins and ymaxs):
        return query
    xmin, xmax = min(xmins), max(xmaxs)
    ymin, ymax = min(ymins), max(ymaxs)

    def _repl(match):
        release, theme, t_type = match.group(1), match.group(2), match.group(3)
        urls = _resolve_overture_stac_parquet_urls(theme, t_type, xmin, ymin, xmax, ymax, release)
        if not urls:
            raise ValueError(
                f"No Overture STAC Parquet partitions intersect bbox {[xmin, ymin, xmax, ymax]} for {theme}/{t_type}."
            )
        max_partitions = 25 if theme.lower() == "buildings" else 40
        if len(urls) > max_partitions:
            raise ValueError(
                f"Bbox {[xmin, ymin, xmax, ymax]} spans {len(urls)} {theme} Parquet partitions "
                f"(limit {max_partitions}). Narrow the bbox to prevent scanning gigabytes of S3 Parquet data."
            )
        quoted = ", ".join("'" + u.replace("'", "''") + "'" for u in urls)
        return f"read_parquet([{quoted}])"

    return pattern.sub(_repl, query)


@tool("Query Overture Maps Foundation open data (buildings, places/POI, transportation/roads, water) via iterative tiled PMTiles and paginated STAC GeoParquet until complete, and load into QGIS.",
      {"theme": S("string", "'places' | 'buildings' | 'transportation' | 'base' | 'divisions' (default 'places')"),
       "type": S("string", "optional subtype: 'place', 'building', 'segment', 'water' (auto-selected if omitted)"),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] in WGS84 (default: current map canvas extent)"),
       "filter_sql": S("string", "optional SQL condition, e.g. \"taxonomy.primary = 'restaurant'\" or \"names.primary ILIKE '%hotel%'\""),
       "limit": S("integer", "per-batch page size when fetch_all=true (iterates until full), or hard cap when fetch_all=false"),
       "fetch_all": S("boolean", "iterate across all tiles/pages until 100% of features in bbox are fetched (default true)"),
       "output_name": S("string", "layer name in QGIS, default e.g. 'Overture_Places'"),
       "tile_threshold": S("integer", "tile-balancing threshold (default 2000)"),
       "source": S("string", "optional backend: 'auto' (PMTiles -> STAC Parquet), 'pmtiles', or 'parquet' (default 'auto')")})
def query_overture_maps(theme="places", type=None, bbox=None, filter_sql=None, limit=None, output_name=None, layer_name=None, tile_threshold=2000, source="auto", fetch_all=True):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        _record_study_bbox(bbox, "EPSG:4326")

    output_name = output_name or layer_name
    default_types = {
        "places": "place", "buildings": "building", "transportation": "segment",
        "base": "water", "divisions": "division"
    }
    t_name = (theme or "places").strip().lower()
    req_type = type.strip().lower() if isinstance(type, str) and type.strip() else None
    if t_name in ("water", "land", "land_use", "land_cover", "infrastructure", "bathymetry"):
        t_type = req_type or t_name
        t_name = "base"
    elif t_name in ("roads", "road", "segment", "connector", "segments", "connectors"):
        t_type = req_type or ("connector" if "connect" in t_name else "segment")
        t_name = "transportation"
    elif t_name in ("poi", "pois", "place"):
        t_type = req_type or "place"
        t_name = "places"
    elif t_name in ("building",):
        t_type = req_type or "building"
        t_name = "buildings"
    else:
        t_type = req_type or default_types.get(t_name, t_name)

    if filter_sql and t_name == "places":
        filter_sql = re.sub(r"\bcategories\.primary\b", "taxonomy.primary", filter_sql, flags=re.IGNORECASE)
        filter_sql = re.sub(r"\bcategories\.alternate\b", "taxonomy.alternates", filter_sql, flags=re.IGNORECASE)
        filter_sql = re.sub(r"\bcategories\.", "taxonomy.", filter_sql, flags=re.IGNORECASE)

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        p = QgsProject.instance()
        wgs_ext = None
        if p.mapLayers():
            c = _canvas()
            p_crs = p.crs()
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            cur_ext = c.extent() if c and not _is_deleted(c) else None
            wgs_ext = _safe_transform_extent(cur_ext, p_crs, wgs84, p)
            if not (wgs_ext and _is_valid_extent(wgs_ext)):
                comb = None
                for lyr in p.mapLayers().values():
                    if lyr.isValid() and lyr.providerType() != "wms" and _is_valid_extent(lyr.extent()):
                        tr = _safe_transform_extent(lyr.extent(), lyr.crs(), wgs84, p)
                        if tr:
                            comb = QgsRectangle(tr) if comb is None else (comb.combineExtentWith(tr) or comb)
                wgs_ext = comb
        if wgs_ext and _is_valid_extent(wgs_ext):
            bbox = [round(wgs_ext.xMinimum(), 5), round(wgs_ext.yMinimum(), 5),
                    round(wgs_ext.xMaximum(), 5), round(wgs_ext.yMaximum(), 5)]
        else:
            raise ValueError("Cannot determine bounding box from empty project/canvas; please pass explicit 'bbox': [xmin, ymin, xmax, ymax] in WGS84.")

    raw_xmin, raw_ymin, raw_xmax, raw_ymax = bbox
    xmin = max(-180.0, min(180.0, min(raw_xmin, raw_xmax)))
    xmax = max(-180.0, min(180.0, max(raw_xmin, raw_xmax)))
    ymin = max(-85.0, min(85.0, min(raw_ymin, raw_ymax)))
    ymax = max(-85.0, min(85.0, max(raw_ymin, raw_ymax)))

    _cleanup_old_temp_files()
    fetch_all_flag = bool(fetch_all)
    if fetch_all_flag:
        page_size = max(100, min(int(limit), 10000)) if limit is not None else 2000
        safe_limit = 250000
    else:
        safe_limit = min(max(1, int(limit or 500)), 250000)
        page_size = safe_limit

    out_file = _project_scratch_file(f"overture_{t_name}_{t_type}", ".gpkg")
    if os.path.exists(out_file):
        try:
            os.remove(out_file)
        except Exception:  # nosec B110
            pass

    source_mode = (source or "auto").strip().lower()
    release = _get_overture_release()

    try:
        if source_mode == "parquet":
            _query_overture_stac_parquet_to_gpkg(t_name, t_type, xmin, ymin, xmax, ymax, release, filter_sql, safe_limit, out_file, fetch_all=fetch_all_flag, page_size=page_size)
        elif source_mode == "pmtiles":
            _fetch_overture_pmtiles_to_gpkg(t_name, t_type, xmin, ymin, xmax, ymax, release, filter_sql, safe_limit, out_file, fetch_all=fetch_all_flag, page_size=page_size)
        else:
            try:
                _fetch_overture_pmtiles_to_gpkg(t_name, t_type, xmin, ymin, xmax, ymax, release, filter_sql, safe_limit, out_file, fetch_all=fetch_all_flag, page_size=page_size)
            except Exception as pmt_exc:
                if "Execution stopped by user" in str(pmt_exc):
                    raise
                LOGGER.info(f"Overture PMTiles fallback to STAC Parquet ({t_name}/{t_type}): {pmt_exc}")
                if os.path.exists(out_file):
                    try:
                        os.remove(out_file)
                    except Exception:  # nosec B110
                        pass
                _query_overture_stac_parquet_to_gpkg(t_name, t_type, xmin, ymin, xmax, ymax, release, filter_sql, safe_limit, out_file, fetch_all=fetch_all_flag, page_size=page_size)
    except ValueError:
        raise
    except Exception as e:
        if os.path.exists(out_file):
            try:
                os.remove(out_file)
            except Exception:  # nosec B110
                pass
        if "Execution stopped by user" in str(e):
            raise
        s3_hint = f"s3://overturemaps-us-west-2/release/{release}/theme={t_name}/type={t_type}/*"
        raise RuntimeError(
            f"Overture Maps query failed for theme='{t_name}', type='{t_type}'. Error: {e}. "
            f"Tip: If a column or filter fails, adjust parameters or use `query_duckdb_spatial` against '{s3_hint}'."
        ) from e

    layer_label = output_name or f"Overture_{t_name.capitalize()}"
    return _load_exported_gpkg_layer(out_file, layer_label, err_prefix="Failed to load Overture data exported")


@tool("Execute arbitrary spatial DuckDB queries on remote Parquet, S3, GeoParquet, or local tabular files with automatic pagination until complete, and load the result as a QGIS vector layer.",
      {"query": S("string", "DuckDB SQL query returning geometry and attributes"),
       "layer_name": S("string", "name of resulting layer in QGIS"),
       "page_size": S("integer", "batch size per paginated iteration (default 2000)"),
       "fetch_all": S("boolean", "iterate pages until all rows are fetched, treating small trailing LIMIT as page hint (default true)")})
def query_duckdb_spatial(query, layer_name="DuckDB_Layer", page_size=2000, fetch_all=True):
    from osgeo import gdal

    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    try:
        import duckdb  # noqa: F401
    except ImportError:
        raise RuntimeError("duckdb is required for DuckDB spatial queries. Install it in your Python environment with: pip install duckdb")
    _cleanup_old_temp_files()
    out_file = _project_scratch_file(layer_name or "duckdb_result", ".gpkg")
    if os.path.exists(out_file):
        try:
            os.remove(out_file)
        except Exception:  # nosec B110
            pass
    rewritten_query = _rewrite_overture_s3_glob_in_sql(query)
    if "s3://overturemaps-us-west-2/" in rewritten_query.lower():
        raise ValueError(
            "Unscoped Overture S3 glob detected without bbox.xmin/bbox.xmax/bbox.ymin/bbox.ymax bounds. "
            "Use query_overture_maps(theme=..., bbox=[...]) or add explicit bbox.xmin/xmax/ymin/ymax WHERE filters "
            "so STAC partition pruning can run instead of scanning the entire global S3 bucket."
        )
    base_query = re.sub(r";\s*$", "", rewritten_query.strip())
    out_file_sql = out_file.replace("\\", "/").replace("'", "''")

    try:
        if not fetch_all:
            copy_query = f"COPY ({base_query}) TO '{out_file_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');"
            _execute_duckdb_nonblocking(_DUCKDB_SESSION_SQL, copy_query)
        else:
            lim_m = re.search(r"\bLIMIT\s+(\d+)\s*$", base_query, flags=re.IGNORECASE)
            has_offset = bool(re.search(r"\bOFFSET\s+\d+(?:\s+LIMIT\s+\d+)?\s*$", base_query, flags=re.IGNORECASE))
            if lim_m and not has_offset:
                base_query = base_query[:lim_m.start()].rstrip()

            max_total = 250000
            _report_progress("query_duckdb_spatial", 30, "Streaming DuckDB spatial query to GeoPackage...")
            copy_query = f"COPY (SELECT * FROM ({base_query}) AS _q LIMIT {max_total}) TO '{out_file_sql}' WITH (FORMAT GDAL, DRIVER 'GPKG');"  # nosec B608
            _execute_duckdb_nonblocking(_DUCKDB_SESSION_SQL, copy_query)
    except Exception as e:
        if os.path.exists(out_file):
            try:
                os.remove(out_file)
            except Exception:  # nosec B110
                pass
        if "Execution stopped by user" in str(e):
            raise
        raise RuntimeError(f"DuckDB spatial query failed. Query: {rewritten_query}. Error: {e}") from e

    return _load_exported_gpkg_layer(out_file, layer_name, err_prefix="Failed to load DuckDB spatial query output")


# ---------------------------------------------------------------- escape hatch
@tool("Execute arbitrary PyQGIS code (iface, QgsProject, processing in scope). Assign to `result` to return a value. Only works if the user enabled it in the plugin panel.",
      {"code": S("string", "python source")})
def exec_pyqgis(code):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    if not ALLOW_PYTHON["value"]:
        raise PermissionError("exec_pyqgis is disabled by the user. Use the dedicated tools.")
    import processing
    ns = {"iface": _iface(), "QgsProject": QgsProject, "processing": processing, "result": None}
    exec("from qgis.core import *\nfrom qgis.gui import *", ns)  # nosec B102
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, ns)  # nosec B102
    c = _canvas()
    if c and not _is_deleted(c):
        try:
            c.refresh()
        except Exception:  # nosec B110
            pass
    return {"stdout": out.getvalue()[-8000:], "result": repr(ns.get("result"))[:8000]}


# ---------------------------------------------------------------- dispatch
from . import layout_tools  # noqa: F401 - register layout composability tools


def list_tools():
    return [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOLS.items()]


def call(name, args):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    if name not in TOOLS:
        raise ValueError(f"Unknown tool {name}")
    fn = TOOLS[name]["fn"]
    clean_args = dict(args or {})

    # Automatic argument alias normalization
    import inspect
    sig = inspect.signature(fn)
    params = sig.parameters
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

    # Common aliases: layer / layer_name / layer_id / name
    if "layer" in params and "layer" not in clean_args:
        for alias in ("layer_name", "layer_id", "name", "target_layer"):
            if alias in clean_args:
                clean_args["layer"] = clean_args.pop(alias)
                break
    elif "layer_name" in params and "layer_name" not in clean_args:
        for alias in ("layer", "layer_id", "name"):
            if alias in clean_args:
                clean_args["layer_name"] = clean_args.pop(alias)
                break

    if "output_name" in params and "output_name" not in clean_args and "layer" not in params:
        for alias in ("layer_name", "layer", "name"):
            if alias in clean_args:
                clean_args["output_name"] = clean_args.pop(alias)
                break

    # Common aliases: layout / layout_name / name
    if "layout" in params and "layout" not in clean_args:
        for alias in ("layout_name", "name", "target_layout"):
            if alias in clean_args:
                clean_args["layout"] = clean_args.pop(alias)
                break
    elif "layout_name" in params and "layout_name" not in clean_args:
        for alias in ("layout", "name"):
            if alias in clean_args:
                clean_args["layout_name"] = clean_args.pop(alias)
                break
    elif "name" in params and "name" not in clean_args and "layer" not in params and "layer_name" not in params:
        for alias in ("layout", "layout_name"):
            if alias in clean_args:
                clean_args["name"] = clean_args.pop(alias)
                break

    # Common styling & labeling aliases
    if "field" in params and "field" not in clean_args and "column" in clean_args:
        clean_args["field"] = clean_args.pop("column")
    if "outline_color" in params and "outline_color" not in clean_args and "stroke_color" in clean_args:
        clean_args["outline_color"] = clean_args.pop("stroke_color")
    if "width" in params and "width" not in clean_args and "stroke_width" in clean_args:
        clean_args["width"] = clean_args.pop("stroke_width")
    if "font" in params and "font" not in clean_args and "font_family" in clean_args:
        clean_args["font"] = clean_args.pop("font_family")
    if "size" in params and "size" not in clean_args and "font_size" in clean_args:
        clean_args["size"] = clean_args.pop("font_size")
    if "palette" in clean_args:
        pal = clean_args.pop("palette")
        if isinstance(pal, dict) and "color_map" in params and "color_map" not in clean_args:
            clean_args["color_map"] = pal
        elif isinstance(pal, str) and "ramp" in params and "ramp" not in clean_args:
            clean_args["ramp"] = pal

    # If function does not accept **kwargs, drop extra unexpected keys to avoid TypeError
    if not has_var_keyword:
        filtered_args = {k: v for k, v in clean_args.items() if k in params}
        return fn(**filtered_args)

    return fn(**clean_args)
