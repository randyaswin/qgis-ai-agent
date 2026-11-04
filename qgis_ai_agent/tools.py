"""QGIS operations exposed to the AI agent as MCP tools.

Every tool is a function taking keyword args and returning a JSON-serialisable
value. All tools run on the QGIS main thread (called from the TCP server slot).
Coverage follows the REA interaction map: project/CRS, layers, symbology,
labels, processing, expressions, print layout, export, canvas snapshot.
"""
import base64
import contextlib
import html
import io
import os
import tempfile
import time
import traceback
from . import paths

from qgis.PyQt.QtCore import QSize, QBuffer, QIODevice
from qgis.PyQt.QtGui import QColor, QFont
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
)
from qgis.PyQt.QtCore import QMetaType

# QGIS API compatibility monkey-patches for renderer symbol setters
if hasattr(QgsCategorizedSymbolRenderer, "setSourceSymbol") and not hasattr(QgsCategorizedSymbolRenderer, "setSymbol"):
    QgsCategorizedSymbolRenderer.setSymbol = lambda self, sym: self.setSourceSymbol(sym)

if hasattr(QgsGraduatedSymbolRenderer, "setSourceSymbol") and not hasattr(QgsGraduatedSymbolRenderer, "setSymbol"):
    QgsGraduatedSymbolRenderer.setSymbol = lambda self, sym: self.setSourceSymbol(sym)

TOOLS = {}
ALLOW_PYTHON = {"value": True}
CANCEL_REQUESTED = False


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
    p = QgsProject.instance()
    lyr = p.mapLayer(ref)
    if lyr:
        return lyr
    found = p.mapLayersByName(ref)
    if not found:
        raise ValueError(f"Layer not found: {ref!r}. Call get_project_state for names.")
    return found[0]


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


def _canvas():
    iface = _iface()
    if iface and hasattr(iface, "mapCanvas"):
        return iface.mapCanvas()
    try:
        from qgis.gui import QgsMapCanvas
        return QgsMapCanvas()
    except Exception:
        return None


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
    except Exception:
        pass
    try:
        if not dst_crs.isGeographic():
            b = dst_crs.bounds()
            if _is_valid_extent(b):
                tr_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"), dst_crs, p)
                clamped_b = tr_wgs.transformBoundingBox(b)
                if _is_valid_extent(clamped_b):
                    return clamped_b
    except Exception:
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


# ---------------------------------------------------------------- project
@tool("Snapshot of the whole session: project CRS, layers (fields, geometry, renderer), canvas extent, layouts. Call this first and after every change.")
def get_project_state():
    p = QgsProject.instance()
    order = [n.layerId() for n in p.layerTreeRoot().findLayers()]
    c = _canvas()
    ext = c.extent() if c else None
    state = {
        "project_file": p.fileName(),
        "crs": p.crs().authid(),
        "layers_top_to_bottom": [_layer_info(p.mapLayer(i)) for i in order if p.mapLayer(i)],
        "canvas_extent": [round(ext.xMinimum(), 4), round(ext.yMinimum(), 4), round(ext.xMaximum(), 4), round(ext.yMaximum(), 4)] if _is_valid_extent(ext) else None,
        "canvas_scale": c.scale() if c else 1.0,
        "layouts": [l.name() for l in p.layoutManager().layouts()],
        "qgis_version": Qgis.version(),
    }
    return state


@tool("Clear the project and start fresh.", {"crs": S("string", "e.g. EPSG:4326 or EPSG:32748")})
def new_project(crs="EPSG:4326"):
    p = QgsProject.instance()
    p.clear()
    c = QgsCoordinateReferenceSystem(crs)
    if c.isValid():
        p.setCrs(c)
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


@tool("Save project to .qgz/.qgs. If path is omitted, saves to the current file or auto-saves to output directory.",
      {"path": S("string", "optional absolute path; defaults to current project file or ~/qgis-ai-maps/<title>.qgz")})
def save_project(path=None):
    p = QgsProject.instance()
    if not path:
        path = p.fileName()
    if not path:
        out_dir = paths.get_default_output_dir()
        os.makedirs(out_dir, exist_ok=True)
        title = p.title() or "QGIS_Project"
        clean_title = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in title).strip("_") or "QGIS_Project"
        path = os.path.join(out_dir, f"{clean_title}.qgz")
    ok = p.write(path)
    try:
        from .sessions import record_autosave_info
        record_autosave_info(path)
    except Exception:
        pass
    return {"saved": ok, "path": path}


@tool("Open an existing project file (.qgz/.qgs). STRICT RULE: Only call this tool if the user explicitly requested to open this specific project file. Never open unrelated projects autonomously.", {"path": S("string", "absolute path to project file")})
def open_project(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Project file not found: {path}")
    QgsProject.instance().read(path)
    return get_project_state()


# ---------------------------------------------------------------- layers
@tool("Add a vector layer (shp, gpkg, geojson, csv-with-uri, PostGIS uri...).",
      {"path": S("string", "file path or provider uri"), "name": S("string", "layer name"),
       "provider": S("string", "ogr (default), delimitedtext, postgres, memory, wfs...")})
def add_vector_layer(path, name=None, provider="ogr"):
    lyr = QgsVectorLayer(path, name or os.path.splitext(os.path.basename(path))[0], provider)
    if not lyr.isValid():
        raise ValueError(f"Invalid vector source: {path}")
    if not _is_valid_extent(lyr.extent()):
        try:
            lyr.updateExtents()
        except Exception:
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
@tool("Georeference an unreferenced raster image (scanned map, drone aerial photo, PNG/JPG/TIF) using Ground Control Points (GCPs). Warps the image to the specified CRS using GDAL and optionally adds it to the QGIS canvas.",
      {"image_path": S("string", "absolute path to unreferenced raster image"),
       "gcps": S("array", "list of GCPs as [[pixel_x, pixel_y, map_x, map_y], ...] or [{'pixel_x':..., 'pixel_y':..., 'map_x':..., 'map_y':...}]"),
       "output_path": S("string", "output GeoTIFF path (defaults to same folder with _georef.tif)"),
       "crs": S("string", "target CRS, default 'EPSG:4326'"),
       "transform": S("string", "'polynomial1' (Affine, 3+ GCPs, default) | 'polynomial2' (6+ GCPs) | 'polynomial3' (10+ GCPs) | 'tps' (Thin Plate Spline, 4+ GCPs)"),
       "resample": S("string", "'bilinear' (default) | 'nearest' | 'cubic' | 'cubicspline'"),
       "load_to_canvas": S("boolean", "automatically add resulting layer to QGIS project (default true)")})
def georeference_raster(image_path, gcps, output_path=None, crs="EPSG:4326", transform="polynomial1", resample="bilinear", load_to_canvas=True):
    from osgeo import gdal, osr
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Input image not found: {image_path}")

    min_gcps = {"polynomial1": 3, "affine": 3, "polynomial2": 6, "polynomial3": 10, "tps": 4, "helmert": 2}
    req = min_gcps.get(transform.lower(), 3)
    if len(gcps) < req:
        raise ValueError(f"Transformation '{transform}' requires at least {req} GCPs, but {len(gcps)} provided.")

    gdal_gcps = []
    for item in gcps:
        if isinstance(item, dict):
            px, py = float(item["pixel_x"]), float(item["pixel_y"])
            mx, my = float(item["map_x"]), float(item["map_y"])
        else:
            px, py, mx, my = float(item[0]), float(item[1]), float(item[2]), float(item[3])
        gdal_gcps.append(gdal.GCP(mx, my, 0, px, py))

    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)

    vrt_path = f"/vsimem/gcp_warp_{os.getpid()}_{len(gcps)}.vrt"
    temp_ds = gdal.Translate(vrt_path, image_path, GCPs=gdal_gcps, outputSRS=srs)
    if not temp_ds:
        raise RuntimeError("Failed to attach GCPs to image.")

    resample_map = {
        "nearest": gdal.GRA_NearestNeighbour,
        "bilinear": gdal.GRA_Bilinear,
        "cubic": gdal.GRA_Cubic,
        "cubicspline": gdal.GRA_CubicSpline,
    }
    alg = resample_map.get(resample.lower(), gdal.GRA_Bilinear)

    poly_order = None
    use_tps = False
    t_low = transform.lower()
    if t_low in ("polynomial1", "affine"):
        poly_order = 1
    elif t_low == "polynomial2":
        poly_order = 2
    elif t_low == "polynomial3":
        poly_order = 3
    elif t_low == "tps":
        use_tps = True

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

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_ds = gdal.Warp(output_path, temp_ds, options=warp_options)
    temp_ds = None
    gdal.Unlink(vrt_path)

    if not out_ds:
        raise RuntimeError(f"GDAL warping failed for {output_path}")

    gt = out_ds.GetGeoTransform()
    width, height = out_ds.RasterXSize, out_ds.RasterYSize
    out_ds = None

    layer_info = None
    if load_to_canvas:
        layer_info = add_raster_layer(output_path, name=os.path.splitext(os.path.basename(output_path))[0])

    return {
        "output_path": output_path,
        "crs": crs,
        "transform": transform,
        "extent": [gt[0], gt[3] + gt[5] * height, gt[0] + gt[1] * width, gt[3]],
        "resolution": [abs(gt[1]), abs(gt[5])],
        "gcp_count": len(gcps),
        "layer": layer_info
    }


@tool("Create a new empty vector layer (GeoPackage, Shapefile, or memory scratch) with specified geometry type, CRS, and attribute fields.",
      {"layer_name": S("string", "name for the new layer"),
       "geometry_type": S("string", "'Point' | 'LineString' | 'Polygon' | 'MultiPoint' | 'MultiLineString' | 'MultiPolygon' (default 'Polygon')"),
       "crs": S("string", "layer CRS, default 'EPSG:4326'"),
       "fields": S("array", "field definitions e.g. [{'name': 'id', 'type': 'integer'}, {'name': 'name', 'type': 'string'}, {'name': 'area_m2', 'type': 'double'}]"),
       "output_path": S("string", "optional path to save as .gpkg or .shp (creates memory scratch layer if omitted)"),
       "load_to_canvas": S("boolean", "add layer to active QGIS project (default true)")})
def create_vector_layer(layer_name, geometry_type="Polygon", crs="EPSG:4326", fields=None, output_path=None, load_to_canvas=True):
    from qgis.PyQt.QtCore import QVariant
    geom_str = geometry_type.strip()
    c_auth = crs or "EPSG:4326"

    if output_path:
        ext = os.path.splitext(output_path)[1].lower()
        driver = "GPKG" if ext in (".gpkg", ".geopackage") else "ESRI Shapefile"
        from osgeo import ogr, osr
        srs = osr.SpatialReference()
        srs.SetFromUserInput(c_auth)
        ogr_drv = ogr.GetDriverByName(driver)
        if os.path.exists(output_path) and driver == "GPKG":
            ds = ogr_drv.Open(output_path, 1)
        else:
            ds = ogr_drv.CreateDataSource(output_path)

        wkb_map = {
            "point": ogr.wkbPoint,
            "linestring": ogr.wkbLineString,
            "polygon": ogr.wkbPolygon,
            "multipoint": ogr.wkbMultiPoint,
            "multilinestring": ogr.wkbMultiLineString,
            "multipolygon": ogr.wkbMultiPolygon
        }
        wkb = wkb_map.get(geom_str.lower(), ogr.wkbPolygon)
        ogr_layer = ds.CreateLayer(layer_name, srs, wkb)
        if fields:
            for f in fields:
                f_name = f.get("name", "field")
                f_type = f.get("type", "string").lower()
                ogr_type = ogr.OFTInteger if f_type in ("int", "integer") else (ogr.OFTReal if f_type in ("double", "float", "real") else ogr.OFTString)
                ogr_layer.CreateField(ogr.FieldDefn(f_name, ogr_type))
        ds.FlushCache()
        ds = None

        lyr = QgsVectorLayer(output_path, layer_name, "ogr")
    else:
        uri = f"{geom_str}?crs={c_auth}"
        lyr = QgsVectorLayer(uri, layer_name, "memory")
        if fields:
            type_map = {
                "int": QVariant.Int, "integer": QVariant.Int,
                "double": QVariant.Double, "float": QVariant.Double, "real": QVariant.Double,
                "string": QVariant.String, "text": QVariant.String
            }
            qfields = [QgsField(f.get("name", "f"), type_map.get(f.get("type", "string").lower(), QVariant.String)) for f in fields]
            lyr.dataProvider().addAttributes(qfields)
            lyr.updateFields()

    if not lyr.isValid():
        raise RuntimeError(f"Failed to create valid vector layer '{layer_name}'")

    if load_to_canvas:
        QgsProject.instance().addMapLayer(lyr)

    return _layer_info(lyr)


@tool("Add digitized vector features (points, lines, or polygons) to an existing vector layer with automatic geometry validation and attribute assignment.",
      {"layer": S("string", "target layer name or id"),
       "features": S("array", "list of feature objects: [{'geometry': coordinates or WKT or GeoJSON geometry, 'attributes': {'field': val, ...}}, ...]"),
       "auto_fix_geometry": S("boolean", "automatically repair self-intersections or unclosed rings using makeValid (default true)")})
def digitize_features(layer, features, auto_fix_geometry=True):
    import json as _json
    lyr = _layer(layer)
    if not isinstance(lyr, QgsVectorLayer):
        raise ValueError(f"Layer '{layer}' is not a vector layer.")

    if not lyr.isEditable():
        lyr.startEditing()

    fields = lyr.fields()
    added_count = 0
    errors = []

    for idx, f_item in enumerate(features):
        geom_raw = f_item.get("geometry")
        attrs = f_item.get("attributes", {})
        geom = None

        if isinstance(geom_raw, str):
            g_str = geom_raw.strip()
            if g_str.startswith("{"):
                geom = QgsGeometry.fromGeoJson(g_str)
            else:
                geom = QgsGeometry.fromWkt(g_str)
        elif isinstance(geom_raw, dict):
            geom = QgsGeometry.fromGeoJson(_json.dumps(geom_raw))
        elif isinstance(geom_raw, list):
            wkb = lyr.wkbType()
            base_type = QgsWkbTypes.geometryType(wkb)
            if base_type == Qgis.GeometryType.Point:
                geom = QgsGeometry.fromPointXY(QgsPointXY(float(geom_raw[0]), float(geom_raw[1])))
            elif base_type == Qgis.GeometryType.Line:
                pts = [QgsPointXY(float(pt[0]), float(pt[1])) for pt in geom_raw]
                geom = QgsGeometry.fromPolylineXY(pts)
            elif base_type == Qgis.GeometryType.Polygon:
                if len(geom_raw) > 0 and isinstance(geom_raw[0], list) and len(geom_raw[0]) > 0 and isinstance(geom_raw[0][0], list):
                    rings = [[QgsPointXY(float(p[0]), float(p[1])) for p in ring] for ring in geom_raw]
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

        feat = QgsFeature(fields)
        feat.setGeometry(geom)
        for k, v in attrs.items():
            if fields.indexFromName(k) != -1:
                feat.setAttribute(k, v)

        if lyr.addFeature(feat):
            added_count += 1
        else:
            errors.append(f"Feature #{idx}: failed to add to layer")

    lyr.commitChanges()
    _refresh()

    return {
        "layer": lyr.name(),
        "features_added": added_count,
        "total_features": lyr.featureCount(),
        "errors": errors if errors else None
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
      {"layer": S("string", "name or id"), "new_name": S("string", ""), "visible": S("boolean", ""),
       "opacity": S("number", "0..1"), "position": S("integer", "0 = top of stack"),
       "min_scale": S("number", "denominator"), "max_scale": S("number", "denominator")})
def update_layer(layer, new_name=None, visible=None, opacity=None, position=None, min_scale=None, max_scale=None):
    lyr = _layer(layer)
    root = QgsProject.instance().layerTreeRoot()
    node = root.findLayer(lyr.id())
    if new_name:
        lyr.setName(new_name)
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


def _vector_detail(lyr, sample, max_unique):
    fields = lyr.fields()
    total = lyr.featureCount()
    stats = []
    nulls = {f.name(): 0 for f in fields}
    numeric = {f.name(): [] for f in fields if f.isNumeric()}
    geom_invalid = geom_empty = 0
    scan_limit = 50000
    for i, f in enumerate(lyr.getFeatures()):
        if i >= scan_limit:
            break
        for name, v in zip(fields.names(), f.attributes()):
            if v is None or str(v) == "NULL":
                nulls[name] += 1
            elif name in numeric:
                try:
                    numeric[name].append(float(v))
                except (TypeError, ValueError):
                    pass
        g = f.geometry()
        if g is None or g.isEmpty():
            geom_empty += 1
        elif i < 5000 and not g.isGeosValid():
            geom_invalid += 1
    for idx, fd in enumerate(fields):
        s = {"name": fd.name(), "alias": fd.alias() or None, "type": fd.typeName(), "length": fd.length(),
             "nulls": nulls[fd.name()], "comment": fd.comment() or None,
             "origin": {0: "unknown", 1: "provider", 2: "join", 3: "edit", 4: "expression"}.get(int(fields.fieldOrigin(idx)))
             if hasattr(fields, "fieldOrigin") else None}
        uniq = lyr.uniqueValues(idx, max_unique + 1)
        s["unique_count"] = len(uniq) if len(uniq) <= max_unique else f">{max_unique}"
        vals = numeric.get(fd.name())
        if vals:
            vals.sort()
            n = len(vals)
            mean = sum(vals) / n
            s.update(min=vals[0], max=vals[-1], mean=mean, median=vals[n // 2],
                     stdev=(sum((x - mean) ** 2 for x in vals) / n) ** 0.5, sum=sum(vals))
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
        "feature_count": total, "scanned": min(total, scan_limit),
        "empty_geometries": geom_empty, "invalid_geometries_in_first_5000": geom_invalid,
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
        except Exception:
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
    except Exception:
        pass
    return out


def _vectortile_detail(lyr):
    out = {"source_type": lyr.sourceType(), "zoom": [lyr.sourceMinZoom(), lyr.sourceMaxZoom()]}
    try:
        out["styles"] = [s.styleName() + " / layer=" + s.layerName() for s in lyr.renderer().styles()][:80]
    except Exception:
        pass
    return out


@tool("Deep inspection of ANY existing layer (vector, raster, mesh, point cloud, vector tile, annotation...): "
      "metadata, CRS & extents, schema with per-field null counts / unique values / numeric stats, geometry validity, "
      "selection, joins, raster band stats+histogram+nodata, mesh datasets, point-cloud attributes, current style. "
      "Use before analysing or restyling user data.",
      {"layer": S("string", "name or id"), "sample": S("integer", "sample rows / raster sample pixels, default 5"),
       "max_unique": S("integer", "list distinct values if <= this, default 30")})
def describe_layer(layer=None, sample=5, max_unique=30, layer_name=None, layer_id=None):
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
        base.update(_vector_detail(lyr, sample, max_unique))
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
            out.append(describe_layer(node.layerId(), sample=0, max_unique=max_unique))
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
        vals = [f[field] for f in lyr.getFeatures() if f[field] not in (None,) and str(f[field]) != "NULL"]
        if vals:
            res.update(min=min(vals), max=max(vals), mean=sum(vals) / len(vals), count=len(vals))
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
    exp = QgsExpression(expression)
    if exp.hasParserError():
        raise ValueError(exp.parserErrorString())
    ctx = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(lyr))
    exp.prepare(ctx)
    changes = {}
    for f in lyr.getFeatures():
        ctx.setFeature(f)
        changes[f.id()] = {idx: exp.evaluate(ctx)}
    lyr.dataProvider().changeAttributeValues(changes)
    lyr.triggerRepaint()
    return {"updated": len(changes), "eval_error": exp.evalErrorString() or None}


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


@tool("Run a Processing algorithm. Layer params accept layer names/ids. OUTPUT defaults to TEMPORARY_OUTPUT. Results are added to the map when load=true.",
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
        params.setdefault("OUTPUT", "TEMPORARY_OUTPUT")
    before = set(QgsProject.instance().mapLayers())
    res = processing.runAndLoadResults(algorithm, params) if load else processing.run(algorithm, params)
    _sanitize_canvas_extent()
    new = [QgsProject.instance().mapLayer(i) for i in set(QgsProject.instance().mapLayers()) - before]
    if output_name and new:
        new[0].setName(output_name)
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


@tool("Categorized (unique values) style.",
      {"layer": S("string", ""), "field": S("string", "field or expression"), "ramp": S("string", "e.g. Spectral, Set1, Viridis"),
       "outline_color": S("string", "")})
def style_categorized(layer, field, ramp="Spectral", outline_color=None):
    lyr = _layer(layer)
    idx = lyr.fields().indexOf(field)
    raw_values = sorted(lyr.uniqueValues(idx), key=str) if idx >= 0 else []
    MAX_CATS = 250
    values = raw_values[:MAX_CATS]
    r = _ramp(ramp)
    cats = []
    n = max(len(values) - 1, 1)
    for i, v in enumerate(values):
        sym = _symbol(lyr, outline_color=outline_color)
        sym.setColor(r.color(i / n))
        cats.append(QgsRendererCategory(v, sym, str(v)))
    lyr.setRenderer(QgsCategorizedSymbolRenderer(field, cats))
    lyr.triggerRepaint()
    _iface().layerTreeView().refreshLayerSymbology(lyr.id())
    return {"categories": len(cats), "total_unique": len(raw_values)}


@tool("Graduated (choropleth / proportional) style.",
      {"layer": S("string", ""), "field": S("string", "numeric field or expression"), "classes": S("integer", "default 5"),
       "method": S("string", "jenks|quantile|equal|pretty|stddev"), "ramp": S("string", "e.g. YlOrRd, Reds, Viridis"),
       "invert_ramp": S("boolean", ""), "outline_color": S("string", "")})
def style_graduated(layer, field, classes=5, method="jenks", ramp="YlOrRd", invert_ramp=False, outline_color=None):
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
       "placement": S("string", "around_point|over_point|horizontal|free|curved|parallel"), "enabled": S("boolean", "")})
def set_labels(layer, field=None, expression=None, size=9, color="#000000", font=None, bold=False,
               buffer_size=1.0, buffer_color="#ffffff", placement=None, enabled=True):
    lyr = _layer(layer)
    if not enabled:
        lyr.setLabelsEnabled(False)
        lyr.triggerRepaint()
        return {"labels": False}
    s = QgsPalLayerSettings()
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
    if placement in pm:
        s.placement = pm[placement]
    lyr.setLabeling(QgsVectorLayerSimpleLabeling(s))
    lyr.setLabelsEnabled(True)
    lyr.triggerRepaint()
    return {"labels": True}


# ---------------------------------------------------------------- canvas snapshot (verification loop)
def _render(extent, width, height, layers=None):
    ms = QgsMapSettings()
    p = QgsProject.instance()
    ms.setLayers(layers or _canvas().layers())
    ms.setDestinationCrs(p.crs())
    ms.setExtent(extent)
    ms.setOutputSize(QSize(int(width), int(height)))
    ms.setBackgroundColor(QColor("white"))
    job = QgsMapRendererParallelJob(ms)
    job.start()
    job.waitForFinished()
    return job.renderedImage()


@tool("Render the current map canvas to PNG and return it as an image so you can visually verify the map.",
      {"width": S("integer", "default 1000"), "height": S("integer", "default 700")})
def snapshot_canvas(width=1000, height=700):
    img = _render(_canvas().extent(), width, height)
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return {"__image__": base64.b64encode(bytes(buf.data())).decode(), "mime": "image/png"}


@tool("Render a visual preview image of a print layout and return it as an image. ALWAYS call this after creating or modifying a layout to visually verify layout balance, check for overflowing legend items, inspect text proportions, and verify that the map fills the page before finalizing.",
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

    ex = QgsLayoutExporter(lay)
    st = QgsLayoutExporter.ImageExportSettings()
    st.dpi = int(dpi or 120)
    tmp = os.path.join(tempfile.gettempdir(), f"qgis_ai_layout_{lay.name()}.png")
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except Exception:
            pass
    res = ex.exportToImage(tmp, st)
    if res != QgsLayoutExporter.ExportResult.Success or not os.path.exists(tmp):
        raise RuntimeError(f"Failed to render layout preview (exporter code {res})")
    with open(tmp, "rb") as fh:
        raw_b64 = base64.b64encode(fh.read()).decode()
    return {
        "__image__": raw_b64,
        "mime": "image/png",
        "layout": lay.name(),
        "page_mm": [lay.pageCollection().page(0).pageSize().width(), lay.pageCollection().page(0).pageSize().height()]
    }


# ---------------------------------------------------------------- print layout
@tool("Build a complete print layout: map frame, title, subtitle, legend, scale bar, north arrow, credits. Replaces an existing layout with the same name.",
      {"name": S("string", "layout name"), "title": S("string", ""), "subtitle": S("string", ""),
       "credits": S("string", "source / author text, bottom"), "page_size": S("string", "A4|A3|A2|Letter"),
       "orientation": S("string", "landscape|portrait"), "legend": S("boolean", "default true"),
       "scalebar": S("boolean", "default true"), "north_arrow": S("boolean", "default true"),
       "legend_layers": S("array", "only these layers in legend", items={"type": "string"}),
       "extent_layer": S("string", "fit map to this layer (default: canvas extent)"),
       "grid": S("boolean", "draw coordinate grid")})
def create_layout(name="Map", title="", subtitle="", credits="", page_size="A4", orientation="landscape",
                  legend=True, scalebar=True, north_arrow=True, legend_layers=None, extent_layer=None, grid=False):
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
    lay.addLayoutItem(mp)
    mp.setCrs(p.crs())
    mp.attemptMove(QgsLayoutPoint(m, top, mm))
    mp.attemptResize(QgsLayoutSize(W - 2 * m - side, H - top - bottom, mm))
    if extent_layer:
        try:
            lyr = _layer(extent_layer)
            ext = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(lyr.extent())
            ext.scale(1.05)
        except Exception:
            ext = _canvas().extent()
    else:
        ext = _canvas().extent()
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
        if legend_layers:
            lg.setAutoUpdateModel(False)
            root = lg.model().rootGroup()
            keep = {_layer(x).id() for x in legend_layers}
            for n in list(root.findLayers()):
                if n.layerId() not in keep:
                    n.parent().removeChildNode(n)
        lay.addLayoutItem(lg)
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
        from qgis.PyQt.QtCore import QEventLoop
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
        from qgis.PyQt.QtCore import QEventLoop
        from qgis.PyQt.QtWidgets import QApplication
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        return designer
    except Exception as e:
        LOGGER.warning(f"Failed to open layout designer window: {e}")
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


@tool("Create a publication-grade themed print layout matching specific cartographic genres: 'poster_minimalist' (modern city art poster like Tokyo), 'antique_historical' (sepia engraving with grid like Plan of Rome), 'landscape_masterplan' (architectural watercolor site plan), 'scientific_dark' (dark-mode thematic atlas with continuous colorbar like Cambridge climate risk), 'editorial_infographic' (multi-frame magazine atlas with hero+inset+KPI stats like African soils), 'infographic_disaster' (situational alert dashboard), 'bento_dashboard' (modern modular bento grid cards with KPI chips and auto-legend), 'planning_official' (formal Kop Peta with right-side metadata block, zoning legend, inset, and neatline), 'dual_comparison' (side-by-side synchronized temporal or scenario comparison), 'corridor_trail' (linear route/river corridor with profile/waypoint strip), 'cyberpunk_hud' (dark sci-fi neon HUD with monospace telemetry), 'nautical_bathymetric' (marine hydrographic chart with nautical ticks and compass rose), 'natgeo' (classic National Geographic parchment, double neatline, serif typography), 'statistical_thematic' (clean choropleth with distribution cards), or 'relief_topographic'. Defaults to A4.",
      {"name": S("string", "layout name"),
       "style_theme": S("string", "poster_minimalist | antique_historical | landscape_masterplan | scientific_dark | editorial_infographic | infographic_disaster | bento_dashboard | planning_official | dual_comparison | corridor_trail | cyberpunk_hud | nautical_bathymetric | natgeo | statistical_thematic | relief_topographic | modern_clean"),
       "title": S("string", "main map title"),
       "subtitle": S("string", "subtitle or administrative region"),
       "credits": S("string", "mandatory real data source attribution"),
       "extent_layer": S("string", "main study area layer name"),
       "key_extent_layer": S("string", "optional regional overview / second scenario extent layer"),
       "page_size": S("string", "A4|A3|A2, default A4"),
       "orientation": S("string", "landscape|portrait, default landscape"),
       "grid": S("boolean", "include coordinate grid (default false)"),
       "agency": S("string", "government agency or municipal department name")})
def create_styled_layout(name="Map", style_theme="planning_official", title="", subtitle="", credits="",
                         extent_layer=None, key_extent_layer=None, page_size="A4", orientation="landscape",
                         grid=False, agency="", **kwargs):
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

    th = (style_theme or "planning_official").lower()
    is_landscape_masterplan = th in ("landscape_masterplan", "landscape_plan", "site_plan", "master_plan_landscape", "watercolor_plan", "eco_park", "landscape")
    is_planning = not is_landscape_masterplan and th in ("planning_official", "tata_ruang", "master_plan", "urban_planning", "zoning", "planning", "official")
    is_infographic = th in ("infographic_bencana", "infographic_disaster", "disaster", "hazard", "bencana")
    is_natgeo = th in ("natgeo", "national_geographic")
    is_statistical = th in ("statistical_thematic", "choropleth", "demographic", "socio_economic", "thematic")
    is_relief = th in ("relief_topographic", "relief", "swiss", "topographic")
    is_poster = th in ("poster", "poster_minimalist", "city_poster", "minimalist_poster", "art_poster", "minimalist")
    is_scientific_dark = th in ("scientific_dark", "climate_risk", "dark_atlas", "scientific", "dark_mode", "dark")
    is_editorial = th in ("editorial", "magazine", "editorial_infographic", "infographic_magazine", "story")
    is_antique = th in ("antique", "antique_historical", "historical", "vintage", "old_map", "engraving")
    is_bento = th in ("bento", "bento_dashboard", "dashboard_bento", "modular_grid", "bento_grid", "apple_dashboard")
    is_dual = th in ("dual", "dual_comparison", "split_comparison", "before_after", "comparison", "side_by_side")
    is_corridor = th in ("corridor", "corridor_trail", "strip_map", "route_profile", "trail", "linear")
    is_cyberpunk = th in ("cyberpunk", "cyberpunk_hud", "synthwave", "neon_dark", "hud", "sci_fi", "neon")
    is_nautical = th in ("nautical", "nautical_bathymetric", "marine", "hydrographic", "admiralty")

    def _setup_extent(mp_item, lyr_name):
        if lyr_name:
            try:
                lyr = _layer(lyr_name)
                ext = QgsCoordinateTransform(lyr.crs(), p.crs(), p).transformBoundingBox(lyr.extent())
                ext.scale(1.05)
                mp_item.zoomToExtent(ext)
                return ext
            except Exception:
                pass
        canv = _canvas()
        if canv and hasattr(canv, "extent"):
            ext = canv.extent()
            mp_item.zoomToExtent(ext)
            return ext
        from qgis.core import QgsRectangle
        ext = QgsRectangle(-180, -90, 180, 90)
        mp_item.zoomToExtent(ext)
        return ext

    def _apply_grid(mp_item, ext):
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
        g.setAnnotationPosition(QgsLayoutItemMapGrid.InsideMapFrame, QgsLayoutItemMapGrid.BorderSide.Left)
        g.setAnnotationPosition(QgsLayoutItemMapGrid.InsideMapFrame, QgsLayoutItemMapGrid.BorderSide.Bottom)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.HideAll, QgsLayoutItemMapGrid.BorderSide.Top)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.HideAll, QgsLayoutItemMapGrid.BorderSide.Right)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.ShowAll, QgsLayoutItemMapGrid.BorderSide.Bottom)
        g.setAnnotationDisplay(QgsLayoutItemMapGrid.DisplayMode.ShowAll, QgsLayoutItemMapGrid.BorderSide.Left)
        g.setAnnotationDirection(QgsLayoutItemMapGrid.Vertical, QgsLayoutItemMapGrid.BorderSide.Left)
        g.setLineSymbol(QgsLineSymbol.createSimple({"color": "#94a3b8", "width": "0.15", "opacity": "0.6"}))
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
        sub_html = f"<div style='font-size:7.5px; color:#475569; margin-top:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_title.setText(
            f"<div style='text-align:center; font-family:Arial, sans-serif; padding:4px 3px;'>"
            f"<div style='font-size:10px; font-weight:bold; color:#0f172a; letter-spacing:0.3px;'>{_html.escape(title or 'PETA RENCANA TATA RUANG')}</div>"
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
        leg.setAutoUpdateModel(True)
        lay.addLayoutItem(leg)
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
            header_card = QgsLayoutItemLabel(lay)
            header_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            sub_txt = f"<div style='font-size:9.5px; font-style:italic; color:#374151; margin-top:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
            header_card.setText(
                f"<div style='font-family:Georgia, serif; text-align:center; padding:4px; background:#fbf9f4; border:1px solid #263238;'>"
                f"<div style='font-size:14px; font-weight:bold; color:#1a1a1a; letter-spacing:1px; text-transform:uppercase;'>{_html.escape(title)}</div>"
                f"{sub_txt}"
                f"</div>"
            )
            lay.addLayoutItem(header_card)
            header_card.attemptMove(QgsLayoutPoint(m + 20.0, m, mm))
            header_card.attemptResize(QgsLayoutSize(W - 2 * m - 40.0, header_h, mm))

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
        leg.setTitle("LEGEND")
        leg.setBackgroundColor(QColor("#fcfbf7"))
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor("#263238"))
        leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))
        lay.addLayoutItem(leg)
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
            sub_txt = f"<span style='font-size:9.5px; color:#64748b; font-weight:normal; margin-left:8px;'>{_html.escape(subtitle)}</span>" if subtitle else ""
            header_card.setText(
                f"<div style='font-family:Arial, sans-serif; padding:2px 4px; border-bottom:1.5px solid #0f172a;'>"
                f"<span style='font-size:13px; font-weight:bold; color:#0f172a;'>{_html.escape(title)}</span>{sub_txt}"
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
        stat_card = QgsLayoutItemLabel(lay)
        stat_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        stat_card.setText(
            f"<div style='font-family:Arial, sans-serif; background:#f8fafc; border:1px solid #e2e8f0; border-radius:4px; padding:6px;'>"
            f"<div style='font-size:8.5px; font-weight:bold; color:#1e293b; text-transform:uppercase;'>STATISTICAL DISTRIBUTION</div>"
            f"<div style='font-size:7px; color:#64748b; margin-top:2px;'>Method: Natural Breaks (Jenks) · 5 Classes</div>"
            f"</div>"
        )
        lay.addLayoutItem(stat_card)
        stat_card.attemptMove(QgsLayoutPoint(right_x, map_top, mm))
        stat_card.attemptResize(QgsLayoutSize(right_w, 20.0, mm))

        # Thematic Legend
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        leg.setTitle("CLASSIFICATION:")
        leg.setAutoUpdateModel(True)
        lay.addLayoutItem(leg)
        leg.attemptMove(QgsLayoutPoint(right_x, map_top + 23.0, mm))
        leg.attemptResize(QgsLayoutSize(right_w, map_h - 25.0, mm))

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
        sub_html = f"<span style='font-size:7.5px; color:#94a3b8; margin-left:8px; font-weight:normal;'>{_html.escape(subtitle)}</span>" if subtitle else ""
        lbl_head.setText(
            f"<div style='font-family:Arial, sans-serif; padding:2px 6px; line-height:1.2;'>"
            f"<span style='background:#ef4444; color:#ffffff; font-size:6.5px; font-weight:bold; padding:1px 4px; border-radius:2px;'>SITUATIONAL ALERT & CRISIS ASSESSMENT</span><br>"
            f"<span style='font-size:10.5px; font-weight:bold; color:#ffffff;'>{_html.escape(title or 'DISASTER & HAZARD SITUATION MAP')}</span>{sub_html}"
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

        # Right Column: KPI Cards Panel
        card_h = 18.0
        kpis = [
            ("HAZARD & EXPOSURE AREA", "CRITICAL IMPACT ZONE", "#dc2626"),
            ("POPULATION AT RISK", "HUMAN VULNERABILITY COUNT", "#ea580c"),
            ("EMERGENCY SITUATION", "RESPONSE HQ & ACCESS STATUS", "#0284c7")
        ]
        curr_ry = map_top
        for i, (kpi_title, kpi_sub, kpi_color) in enumerate(kpis):
            cy = curr_ry + i * (card_h + 2.0)
            c_shape = QgsLayoutItemShape(lay)
            c_shape.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
            c_shape.setSymbol(QgsFillSymbol.createSimple({"color": "#ffffff", "outline_color": "#cbd5e1", "outline_width": "0.3"}))
            lay.addLayoutItem(c_shape)
            c_shape.attemptMove(QgsLayoutPoint(right_x, cy, mm))
            c_shape.attemptResize(QgsLayoutSize(right_w, card_h, mm))

            c_lbl = QgsLayoutItemLabel(lay)
            c_lbl.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
            c_lbl.setText(
                f"<div style='font-family:Arial, sans-serif; padding:2px 4px; border-left:3px solid {kpi_color}; line-height:1.2;'>"
                f"<div style='font-size:6.5px; font-weight:bold; color:#475569; text-transform:uppercase;'>{kpi_title}</div>"
                f"<div style='font-size:9.5px; font-weight:bold; color:#0f172a; margin-top:1px;'>MONITORED / ACTIVE</div>"
                f"<div style='font-size:6px; color:#64748b;'>{kpi_sub}</div>"
                f"</div>"
            )
            lay.addLayoutItem(c_lbl)
            c_lbl.attemptMove(QgsLayoutPoint(right_x, cy, mm))
            c_lbl.attemptResize(QgsLayoutSize(right_w, card_h, mm))

        curr_ry += len(kpis) * (card_h + 2.0)

        # Right Column: Thematic Legend Box (vertical space ~100mm, plenty of room without overflowing!)
        leg_h = map_top + map_h - curr_ry
        if leg_h > 30.0:
            leg_bg = QgsLayoutItemShape(lay)
            leg_bg.setShapeType(QgsLayoutItemShape.Shape.Rectangle)
            leg_bg.setSymbol(QgsFillSymbol.createSimple({"color": "#ffffff", "outline_color": "#cbd5e1", "outline_width": "0.3"}))
            lay.addLayoutItem(leg_bg)
            leg_bg.attemptMove(QgsLayoutPoint(right_x, curr_ry, mm))
            leg_bg.attemptResize(QgsLayoutSize(right_w, leg_h, mm))

            leg = QgsLayoutItemLegend(lay)
            leg.setLinkedMap(mp)
            leg.setTitle("MAP LEGEND")
            leg.setBackgroundColor(QColor(255, 255, 255, 0))
            leg.setAutoUpdateModel(False)
            lay.addLayoutItem(leg)

            # Auto-prune background layers (basemaps, hillshades, raw rasters)
            skip_terms = ("esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas", "satellite", "terrain")
            root = leg.model().rootGroup()
            for ch in list(root.children()):
                low = ch.name().lower() if hasattr(ch, "name") else ""
                if any(st in low for st in skip_terms):
                    root.removeChildNode(ch)
            if len(root.children()) == 0:
                leg.setAutoUpdateModel(True)
            leg.attemptMove(QgsLayoutPoint(right_x + 1.5, curr_ry + 1.0, mm))
            leg.attemptResize(QgsLayoutSize(right_w - 3.0, leg_h - 2.0, mm))

        # Bottom Credit & Technical Metadata Footer (Compact, 1-2 lines, clean)
        foot_y = map_top + map_h + 1.5
        lbl_cred = QgsLayoutItemLabel(lay)
        lbl_cred.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        crs_str = p.crs().authid() if p.crs().isValid() else "WGS 84"
        lbl_cred.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6.5px; color:#64748b; line-height:1.2;'>"
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
        stamp_h = 24.0
        lbl_stamp = QgsLayoutItemLabel(lay)
        lbl_stamp.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        plan_title = title or "SITE MASTER PLAN"
        plan_sub = subtitle or "Landscape & Environmental Architecture"
        lbl_stamp.setText(
            f"<div style='font-family:Century Gothic, Helvetica, Arial, sans-serif; background:rgba(255,255,255,0.92); border:1px solid #94a3b8; border-radius:3px; padding:5px 8px;'>"
            f"<div style='font-size:6.5px; font-weight:bold; color:#059669; letter-spacing:1.2px; text-transform:uppercase;'>MASTER PLAN & DESIGN PROPOSAL</div>"
            f"<div style='font-size:11.5px; font-weight:800; color:#1e293b; text-transform:uppercase; margin-top:1px;'>{_html.escape(plan_title)}</div>"
            f"<div style='font-size:7px; color:#64748b; margin-top:2px;'>{_html.escape(plan_sub)}</div>"
            f"<div style='font-size:6px; color:#94a3b8; margin-top:2px;'>CRS: {p.crs().authid()} &bull; {_html.escape(credits or 'Survey & Planning Bureau')}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_stamp)
        lbl_stamp.attemptMove(QgsLayoutPoint(W - m - stamp_w - 5.0, m + 5.0, mm))
        lbl_stamp.attemptResize(QgsLayoutSize(stamp_w, stamp_h, mm))

        # Floating Clean Legend Badge (bottom-right)
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        leg.setTitle("LAND USE & FEATURES")
        leg.setAutoUpdateModel(False)
        lay.addLayoutItem(leg)
        skip_terms = ("esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas", "satellite", "terrain")
        root = leg.model().rootGroup()
        for ch in list(root.children()):
            low = ch.name().lower() if hasattr(ch, "name") else ""
            if any(st in low for st in skip_terms):
                root.removeChildNode(ch)
        if len(root.children()) == 0:
            leg.setAutoUpdateModel(True)
        leg.setBackgroundColor(QColor(255, 255, 255, 230))
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
    # THEME 5: MINIMALIST CITY ART POSTER (Full-Bleed, Modern Floating Typography)
    # =========================================================================
    if is_poster:
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
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#0f172a"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))

        # Floating Art Poster Typography Block
        lbl_art = QgsLayoutItemLabel(lay)
        lbl_art.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        city_title = title or "CITY MAP"
        region_sub = subtitle or "URBAN NETWORK"
        crs_code = p.crs().authid() if p.crs().isValid() else ""
        coord_txt = f"{crs_code} &bull; {_html.escape(credits)}" if credits else crs_code
        lbl_art.setText(
            f"<div style='text-align:center; font-family:Century Gothic, Helvetica Neue, Arial, sans-serif; padding-top:2px;'>"
            f"<div style='font-size:24px; font-weight:800; color:#0f172a; letter-spacing:4px; text-transform:uppercase;'>{_html.escape(city_title)}</div>"
            f"<div style='font-size:9.5px; font-weight:600; color:#64748b; letter-spacing:2px; text-transform:uppercase; margin-top:2px;'>{_html.escape(region_sub)}</div>"
            f"<div style='font-size:6.5px; color:#94a3b8; letter-spacing:1px; margin-top:3px;'>{coord_txt}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_art)
        lbl_art.attemptMove(QgsLayoutPoint(m, m + map_h + 3.0, mm))
        lbl_art.attemptResize(QgsLayoutSize(map_w, title_block_h, mm))

        _register_layout(mgr, lay)
        return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale()}

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
        lbl_sci = QgsLayoutItemLabel(lay)
        lbl_sci.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_lead = f"<div style='font-size:7.5px; color:#94a3b8; margin-bottom:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_sci.setText(
            f"<div style='font-family:Arial, sans-serif; background:rgba(15,23,42,0.85); border:1px solid #334155; border-radius:4px; padding:6px 8px;'>"
            f"{sub_lead}"
            f"<div style='font-size:12px; font-weight:bold; color:#f8fafc; line-height:1.25;'>{_html.escape(title or 'SCIENTIFIC THEMATIC ANALYSIS')}</div>"
            f"<div style='font-size:6.5px; color:#cbd5e1; margin-top:3px; line-height:1.2;'>Observed records & continuous spatial projection</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_sci)
        lbl_sci.attemptMove(QgsLayoutPoint(m + 6.0, H - m - 45.0, mm))
        lbl_sci.attemptResize(QgsLayoutSize(90.0, 22.0, mm))

        # Floating Continuous Gradient Ramp Legend
        ramp_lbl = QgsLayoutItemLabel(lay)
        ramp_lbl.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        ramp_lbl.setText(
            f"<div style='font-family:Arial, sans-serif; background:rgba(15,23,42,0.85); border:1px solid #334155; border-radius:4px; padding:4px 6px;'>"
            f"<div style='font-size:6.5px; color:#94a3b8; font-weight:bold; margin-bottom:2px;'>RELATIVE VALUE DISTRIBUTION</div>"
            f"<div style='height:7px; width:100%; background:linear-gradient(to right, #440154, #3b528b, #21918c, #5ec962, #fde725); border-radius:2px;'></div>"
            f"<table width='100%' style='margin-top:2px; font-size:6.5px; color:#cbd5e1; font-family:monospace;'>"
            f"<tr><td align='left'>Low / Min</td><td align='center'>Median</td><td align='right'>High / Extreme</td></tr>"
            f"</table>"
            f"</div>"
        )
        lay.addLayoutItem(ramp_lbl)
        ramp_lbl.attemptMove(QgsLayoutPoint(m + 6.0, H - m - 21.0, mm))
        ramp_lbl.attemptResize(QgsLayoutSize(90.0, 16.0, mm))

        # Institutional Metadata / Branding Block in lower right negative space
        lbl_inst = QgsLayoutItemLabel(lay)
        lbl_inst.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_inst.setText(
            f"<div style='font-family:Arial, sans-serif; text-align:right; color:#94a3b8; font-size:6.5px; line-height:1.3;'>"
            f"<span style='color:#f8fafc; font-weight:bold; font-size:8px;'>RESEARCH INITIATIVE &bull; GIS LAB</span><br>"
            f"Sources: {_html.escape(credits or 'Global Open Data, Earth Observation STAC')}<br>"
            f"CRS: {p.crs().authid()} &bull; Map Cartography: QGIS Copilot"
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
        lead_txt = f"<div style='font-size:7.5px; color:#475569; font-style:italic; margin-top:2px; line-height:1.3;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_head.setText(
            f"<div style='font-family:Georgia, serif; padding:2px 4px; border-bottom:1.5px solid #0f172a;'>"
            f"<div style='font-size:18px; font-weight:bold; color:#0f172a; letter-spacing:0.5px;'>{_html.escape(title or 'EDITORIAL ATLAS STORY')}</div>"
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
        lbl_call = QgsLayoutItemLabel(lay)
        lbl_call.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_call.setText(
            f"<div style='font-family:Arial, sans-serif; background:rgba(255,255,255,0.92); border:1px solid #cbd5e1; border-radius:4px; padding:4px 6px;'>"
            f"<div style='font-size:14px; font-weight:bold; color:#0284c7;'>KEY REGION</div>"
            f"<div style='font-size:6.5px; color:#475569; line-height:1.2;'>High-priority environmental & socio-economic impact corridor</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_call)
        lbl_call.attemptMove(QgsLayoutPoint(m + 4.0, map_top + 4.0, mm))
        lbl_call.attemptResize(QgsLayoutSize(55.0, 18.0, mm))

        # Right Column: Multi-Column Legend
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        leg.setTitle("CLASSIFICATION")
        leg.setAutoUpdateModel(False)
        lay.addLayoutItem(leg)
        skip_terms = ("esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas", "satellite", "terrain")
        root = leg.model().rootGroup()
        for ch in list(root.children()):
            low = ch.name().lower() if hasattr(ch, "name") else ""
            if any(st in low for st in skip_terms):
                root.removeChildNode(ch)
        if len(root.children()) == 0:
            leg.setAutoUpdateModel(True)
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
        page.setBackgroundColor(QColor("#f4ecd8"))
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
        mp.attemptMove(QgsLayoutPoint(m, m, mm))
        mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))
        main_ext = _setup_extent(mp, extent_layer)
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor("#2e2115"))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.4, mm))
        _apply_grid(mp, main_ext)

        # Classical Corner Cartouche Title in Negative Space (bottom-right)
        cart_w = 65.0
        cart_h = 24.0
        lbl_cart = QgsLayoutItemLabel(lay)
        lbl_cart.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        plan_title = title or "PLAN OF THE CITY"
        lbl_cart.setText(
            f"<div style='font-family:Georgia, serif; text-align:center; background:#f4ecd8; border:1px solid #2e2115; padding:4px;'>"
            f"<div style='font-size:12px; font-weight:bold; letter-spacing:1px; color:#2e2115; text-transform:uppercase;'>{_html.escape(plan_title)}</div>"
            f"<div style='font-size:7.5px; font-style:italic; color:#4a3728; margin-top:1px;'>{_html.escape(subtitle or 'Accurately delineated from authentic surveys')}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_cart)
        lbl_cart.attemptMove(QgsLayoutPoint(W - m - cart_w - 4.0, H - m - cart_h - 4.0, mm))
        lbl_cart.attemptResize(QgsLayoutSize(cart_w, cart_h, mm))

        # Antique Scale Bar (classical single box with authentic sepia ink styling)
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
        lbl_b1 = QgsLayoutItemLabel(lay)
        lbl_b1.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_txt = f"<div style='font-size:7.5px; color:#64748b; margin-top:2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        lbl_b1.setText(
            f"<div style='font-family:Arial, sans-serif; background:#ffffff; border:1px solid #cbd5e1; border-radius:4px; padding:6px 8px;'>"
            f"<div style='font-size:6.5px; font-weight:bold; color:#0284c7; text-transform:uppercase; letter-spacing:0.8px;'>BENTO GEODASHBOARD</div>"
            f"<div style='font-size:11px; font-weight:bold; color:#0f172a; margin-top:1px; line-height:1.2;'>{_html.escape(title or 'SPATIAL ANALYTICS')}</div>"
            f"{sub_txt}"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b1)
        lbl_b1.attemptMove(QgsLayoutPoint(right_x, curr_y, mm))
        lbl_b1.attemptResize(QgsLayoutSize(right_w, card1_h, mm))
        curr_y += card1_h + gap

        # Card 2: Dual KPI Stat Metrics
        card2_h = 24.0
        lbl_b2 = QgsLayoutItemLabel(lay)
        lbl_b2.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_b2.setText(
            f"<table width='100%' cellpadding='0' cellspacing='4' style='font-family:Arial, sans-serif;'>"
            f"<tr>"
            f"<td width='50%' style='background:#ffffff; border:1px solid #cbd5e1; border-radius:4px; padding:4px 6px; text-align:center;'>"
            f"<div style='font-size:6px; font-weight:bold; color:#64748b; text-transform:uppercase;'>EXTENT METRIC</div>"
            f"<div style='font-size:13px; font-weight:800; color:#0f172a; margin-top:1px;'>100%</div>"
            f"<div style='font-size:6px; color:#10b981;'>Covered & Verified</div>"
            f"</td>"
            f"<td width='50%' style='background:#ffffff; border:1px solid #cbd5e1; border-radius:4px; padding:4px 6px; text-align:center;'>"
            f"<div style='font-size:6px; font-weight:bold; color:#64748b; text-transform:uppercase;'>STATUS TIER</div>"
            f"<div style='font-size:13px; font-weight:800; color:#0284c7; margin-top:1px;'>OPTIMAL</div>"
            f"<div style='font-size:6px; color:#64748b;'>Multi-Criteria Grade</div>"
            f"</td>"
            f"</tr>"
            f"</table>"
        )
        lay.addLayoutItem(lbl_b2)
        lbl_b2.attemptMove(QgsLayoutPoint(right_x, curr_y, mm))
        lbl_b2.attemptResize(QgsLayoutSize(right_w, card2_h, mm))
        curr_y += card2_h + gap

        # Card 3: Categorized Legend Card (auto-pruned)
        card3_h = avail_h - curr_y + m - 20.0
        leg = QgsLayoutItemLegend(lay)
        leg.setLinkedMap(mp)
        leg.setTitle("CLASSIFICATION")
        leg.setAutoUpdateModel(False)
        lay.addLayoutItem(leg)
        skip_terms = ("esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas", "satellite", "terrain")
        root = leg.model().rootGroup()
        for ch in list(root.children()):
            low = ch.name().lower() if hasattr(ch, "name") else ""
            if any(st in low for st in skip_terms):
                root.removeChildNode(ch)
        if len(root.children()) == 0:
            leg.setAutoUpdateModel(True)
        leg.setBackgroundColor(QColor("#ffffff"))
        leg.setFrameEnabled(True)
        leg.setFrameStrokeColor(QColor("#cbd5e1"))
        leg.setFrameStrokeWidth(QgsLayoutMeasurement(0.25, mm))
        leg.attemptMove(QgsLayoutPoint(right_x, curr_y, mm))
        leg.attemptResize(QgsLayoutSize(right_w, card3_h, mm))
        curr_y += card3_h + gap

        # Card 4: Metadata & Provenance Card
        card4_h = avail_h - curr_y + m
        lbl_b4 = QgsLayoutItemLabel(lay)
        lbl_b4.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        crs_code = p.crs().authid() if p.crs().isValid() else "WGS 84"
        lbl_b4.setText(
            f"<div style='font-family:Arial, sans-serif; background:#ffffff; border:1px solid #cbd5e1; border-radius:4px; padding:4px 6px; font-size:6.5px; color:#64748b; line-height:1.25;'>"
            f"<b>Sources:</b> {_html.escape(credits or 'Official Spatial Repositories')} &nbsp;&bull;&nbsp; "
            f"<b>CRS:</b> {crs_code}<br>"
            f"<b>Platform:</b> QGIS Copilot &nbsp;&bull;&nbsp; Publication Edition"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b4)
        lbl_b4.attemptMove(QgsLayoutPoint(right_x, curr_y, mm))
        lbl_b4.attemptResize(QgsLayoutSize(right_w, card4_h, mm))

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
            f"<div style='font-family:Arial, sans-serif; background:rgba(15,23,42,0.85); color:#ffffff; font-size:7.5px; font-weight:bold; padding:2px 6px; border-radius:3px;'>"
            f"PHASE A &bull; BASELINE STATE"
            f"</div>"
        )
        lay.addLayoutItem(lbl_a)
        lbl_a.attemptMove(QgsLayoutPoint(m + 3.0, map_top + 3.0, mm))
        lbl_a.attemptResize(QgsLayoutSize(45.0, 7.0, mm))

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
            f"<div style='font-family:Arial, sans-serif; background:rgba(2,132,199,0.85); color:#ffffff; font-size:7.5px; font-weight:bold; padding:2px 6px; border-radius:3px;'>"
            f"PHASE B &bull; SCENARIO / IMPACT"
            f"</div>"
        )
        lay.addLayoutItem(lbl_b)
        lbl_b.attemptMove(QgsLayoutPoint(m + half_w + gap + 3.0, map_top + 3.0, mm))
        lbl_b.attemptResize(QgsLayoutSize(45.0, 7.0, mm))

        # Bottom Shared Dock: Scale bar & Legend & Credits
        foot_y = map_top + map_h + gap
        _create_clean_scalebar(mp1, style="Stepped Line", custom_x=m + 2.0, custom_y=foot_y,
                               max_width=45.0, color="#0f172a", background="#ffffff", frame_color=None)

        lbl_meta = QgsLayoutItemLabel(lay)
        lbl_meta.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_meta.setText(
            f"<div style='font-family:Arial, sans-serif; font-size:6.5px; color:#64748b; line-height:1.2; text-align:right;'>"
            f"Synchronized Dual Extent &bull; Sources: {_html.escape(credits or 'Multi-temporal Datasets')} &bull; QGIS Copilot"
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
            f"<div style='font-size:12px; font-weight:bold; color:#0f172a; text-transform:uppercase;'>{_html.escape(title or 'CORRIDOR ROUTE & TRAIL GUIDE')}</div>"
            f"<div style='font-size:7.5px; color:#64748b;'>{_html.escape(subtitle or 'Linear Infrastructure / Elevation & Waypoints Profile')}</div>"
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

        # Bottom Corridor Profile & Waypoint Strip
        strip_y = map_top + map_h + gap
        lbl_strip = QgsLayoutItemLabel(lay)
        lbl_strip.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_strip.setText(
            f"<div style='font-family:Arial, sans-serif; background:#f8fafc; border:1px solid #cbd5e1; border-radius:4px; padding:4px 8px;'>"
            f"<div style='font-size:7px; font-weight:bold; color:#0284c7; text-transform:uppercase;'>ROUTE MILESTONES & PROFILE</div>"
            f"<table width='100%' style='margin-top:2px; font-size:7px; color:#334155;'>"
            f"<tr>"
            f"<td width='25%'><b>[KM 0.0]</b> Start Point / Terminal</td>"
            f"<td width='25%'><b>[KM 25.0]</b> Mid-Valley Waypoint</td>"
            f"<td width='25%'><b>[KM 50.0]</b> Summit / High Elevation</td>"
            f"<td width='25%' align='right'><b>[KM 75.0]</b> Finish Station</td>"
            f"</tr>"
            f"</table>"
            f"<div style='font-size:6px; color:#64748b; margin-top:3px;'>Data Sources: {_html.escape(credits or 'Open Route Data')} &bull; Projected CRS: {p.crs().authid()}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_strip)
        lbl_strip.attemptMove(QgsLayoutPoint(m, strip_y, mm))
        lbl_strip.attemptResize(QgsLayoutSize(map_w, strip_h, mm))

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
        lbl_hud = QgsLayoutItemLabel(lay)
        lbl_hud.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_hud.setText(
            f"<div style='font-family:Courier New, monospace; background:rgba(9,10,15,0.90); border:1px solid #00f0ff; border-radius:3px; padding:4px 6px;'>"
            f"<div style='font-size:7px; color:#00f0ff; letter-spacing:1px; font-weight:bold;'>// HUD GEOSPATIAL FEED [ONLINE]</div>"
            f"<div style='font-size:11px; font-weight:bold; color:#ff007f; text-transform:uppercase;'>{_html.escape(title or 'CYBERPUNK NEON MATRIX')}</div>"
            f"<div style='font-size:6.5px; color:#10b981;'>CRS: {p.crs().authid()} &bull; LAT/LON RADAR SCAN ACTIVE</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_hud)
        lbl_hud.attemptMove(QgsLayoutPoint(m + 4.0, m + 4.0, mm))
        lbl_hud.attemptResize(QgsLayoutSize(75.0, 18.0, mm))

        # Bottom Right Attribution HUD
        lbl_tele = QgsLayoutItemLabel(lay)
        lbl_tele.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_tele.setText(
            f"<div style='font-family:Courier New, monospace; background:rgba(9,10,15,0.90); border:1px solid #ff007f; padding:3px 5px; font-size:6px; color:#94a3b8; text-align:right;'>"
            f"<span style='color:#ff007f; font-weight:bold;'>PROVENANCE:</span> {_html.escape(credits or 'Synthetic Earth Observation')}<br>"
            f"OPERATIONAL TELEMETRY &bull; QGIS AGY CORE"
            f"</div>"
        )
        lay.addLayoutItem(lbl_tele)
        lbl_tele.attemptMove(QgsLayoutPoint(W - m - 65.0, H - m - 12.0, mm))
        lbl_tele.attemptResize(QgsLayoutSize(60.0, 9.0, mm))

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
        cart_w = 75.0
        cart_h = 28.0
        lbl_naut = QgsLayoutItemLabel(lay)
        lbl_naut.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        lbl_naut.setText(
            f"<div style='font-family:Times New Roman, serif; text-align:center; background:#fbf5dc; border:1px solid #1e293b; padding:4px 6px;'>"
            f"<div style='font-size:6.5px; letter-spacing:1px; color:#475569; text-transform:uppercase;'>HYDROGRAPHIC SURVEY & NAVIGATION CHART</div>"
            f"<div style='font-size:11px; font-weight:bold; color:#0f172a; text-transform:uppercase; margin-top:1px;'>{_html.escape(title or 'COASTAL WATERS & CHANNELS')}</div>"
            f"<div style='font-size:6.5px; font-style:italic; color:#334155; margin-top:2px;'>Depths and Soundings in Meters &bull; Chart Datum: WGS 84</div>"
            f"<div style='font-size:5.5px; color:#64748b; margin-top:1px;'>Data: {_html.escape(credits or 'Hydrographic Offices & Open Data')}</div>"
            f"</div>"
        )
        lay.addLayoutItem(lbl_naut)
        lbl_naut.attemptMove(QgsLayoutPoint(W - m - cart_w - 4.0, H - m - cart_h - 4.0, mm))
        lbl_naut.attemptResize(QgsLayoutSize(cart_w, cart_h, mm))

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
        header_card = QgsLayoutItemLabel(lay)
        header_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_txt = f"<div style='font-size: 10px; color: #546e7a; margin-top: 2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        header_card.setText(
            f"<div style='font-family: Arial, sans-serif; text-align: center; padding: 4px; background: #ffffff; border: 1px solid #cfd8dc; border-radius: 4px;'>"
            f"<div style='font-size: 14px; font-weight: bold; color: #0f172a;'>{_html.escape(title)}</div>"
            f"{sub_txt}"
            f"</div>"
        )
        lay.addLayoutItem(header_card)
        header_card.attemptMove(QgsLayoutPoint(m, m, mm))
        header_card.attemptResize(QgsLayoutSize(W - 2 * m, header_h, mm))

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
        tmp = os.path.join(tempfile.gettempdir(), "qgis_ai_layout_preview.png")
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        st = QgsLayoutExporter.ImageExportSettings(); st.dpi = 80
        ex.exportToImage(tmp, st)
        with open(tmp, "rb") as fh:
            out = {"__image__": base64.b64encode(fh.read()).decode(), "mime": "image/png", "meta": out}
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
@tool("List curated 100% free, keyless public geospatial data sources: Overture Maps (buildings, places, roads), STAC APIs (Earth Search Sentinel-2/Landsat), Copernicus DEM GLO-30 COGs, ESA WorldCover, and WorldPop.")
def list_public_geodata_sources():
    return {
        "overture_maps": {
            "description": "Global open map features from Overture Maps Foundation (Meta, Microsoft, Amazon, TomTom) queried directly over S3 parquet with zero API keys.",
            "themes": ["places", "buildings", "transportation", "base", "divisions"],
            "tool": "query_overture_maps"
        },
        "stac_apis": [
            {
                "name": "Earth Search by Element84",
                "url": "https://earth-search.aws.element84.com/v1",
                "auth": "None (100% Free Public)",
                "collections": ["sentinel-2-l2a", "cop-dem-glo-30", "cop-dem-glo-90", "landsat-c2-l2", "naip"]
            },
            {
                "name": "Microsoft Planetary Computer",
                "url": "https://planetarycomputer.microsoft.com/api/stac/v1",
                "auth": "Public open search (no key required for metadata & open assets)"
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
            }
        }
    }


@tool("Search public STAC catalogs (e.g. Earth Search Sentinel-2, Landsat, Copernicus DEM) for Cloud-Optimized GeoTIFFs (COGs) matching an area of interest.",
      {"collections": S("array", "collection IDs e.g. ['sentinel-2-l2a'] or ['cop-dem-glo-30']", items={"type": "string"}),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] in WGS84 (default: current map canvas extent)"),
       "datetime": S("string", "optional date/range e.g. '2026-09-01/2026-10-31' or '2026-10-15'"),
       "limit": S("integer", "max items to return, default 3"),
       "api_url": S("string", "STAC endpoint URL, default 'https://earth-search.aws.element84.com/v1'")})
def search_stac_items(collections, bbox=None, datetime=None, limit=3, api_url=None):
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

    params = {"collections": collections, "bbox": bbox, "max_items": int(limit)}
    if datetime:
        params["datetime"] = datetime

    search = client.search(**params)
    results = []
    for item in search.items():
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


@tool("Query Overture Maps Foundation open data (buildings, places/POI, transportation/roads, water) directly from S3 parquet using DuckDB and load it into QGIS.",
      {"theme": S("string", "'places' | 'buildings' | 'transportation' | 'base' | 'divisions' (default 'places')"),
       "type": S("string", "optional subtype: 'place', 'building', 'segment', 'water' (auto-selected if omitted)"),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] in WGS84 (default: current map canvas extent)"),
       "filter_sql": S("string", "optional SQL condition, e.g. \"taxonomy.primary = 'restaurant'\" or \"names.primary ILIKE '%hotel%'\""),
       "limit": S("integer", "max features to fetch, default 500"),
       "output_name": S("string", "layer name in QGIS, default e.g. 'Overture_Places'")})
def query_overture_maps(theme="places", type=None, bbox=None, filter_sql=None, limit=500, output_name=None):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    import duckdb
    import time as _time
    default_types = {
        "places": "place", "buildings": "building", "transportation": "segment",
        "base": "water", "divisions": "division"
    }
    t_name = theme.lower()
    t_type = (type or default_types.get(t_name, t_name)).lower()

    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        c = _canvas()
        cur_ext = c.extent() if c else None
        p_crs = QgsProject.instance().crs()
        wgs_ext = _safe_transform_extent(cur_ext, p_crs, QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        if wgs_ext and _is_valid_extent(wgs_ext):
            bbox = [round(wgs_ext.xMinimum(), 5), round(wgs_ext.yMinimum(), 5),
                    round(wgs_ext.xMaximum(), 5), round(wgs_ext.yMaximum(), 5)]
        else:
            bbox = [137.35, 34.73, 137.45, 34.80]

    raw_xmin, raw_ymin, raw_xmax, raw_ymax = bbox
    xmin = max(-180.0, min(180.0, min(raw_xmin, raw_xmax)))
    xmax = max(-180.0, min(180.0, max(raw_xmin, raw_xmax)))
    ymin = max(-85.0, min(85.0, min(raw_ymin, raw_ymax)))
    ymax = max(-85.0, min(85.0, max(raw_ymin, raw_ymax)))

    # Select key attributes based on theme
    if t_name == "places":
        select_clause = "id, names.primary as name, taxonomy.primary as category, geometry"
    elif t_name == "buildings":
        select_clause = "id, names.primary as name, height, num_floors, geometry"
    elif t_name == "transportation":
        select_clause = "id, names.primary as name, class, subtype, subclass, geometry"
    elif t_name == "base":
        select_clause = "id, names.primary as name, subtype, class, geometry"
    else:
        select_clause = "id, subtype, geometry"

    where_clauses = [
        f"bbox.xmin >= {xmin} AND bbox.xmax <= {xmax}",
        f"bbox.ymin >= {ymin} AND bbox.ymax <= {ymax}"
    ]
    if filter_sql:
        where_clauses.append(f"({filter_sql})")

    out_file = os.path.join(tempfile.gettempdir(), f"overture_{t_name}_{int(_time.time())}.gpkg")
    if os.path.exists(out_file):
        try:
            os.remove(out_file)
        except Exception:
            pass
    s3_path = f"s3://overturemaps-us-west-2/release/2026-10-21.0/theme={t_name}/type={t_type}/*"

    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_region='us-west-2';")
        con.execute("SET geometry_always_xy=true;")
        con.execute("SET memory_limit='1GB';")
        con.execute("SET threads=2;")
        con.execute("SET preserve_insertion_order=false;")

        copy_query = f"""
        COPY (
            SELECT {select_clause}
            FROM read_parquet('{s3_path}', hive_partitioning=1)
            WHERE {' AND '.join(where_clauses)}
            LIMIT {int(limit)}
        ) TO '{out_file}' WITH (FORMAT GDAL, DRIVER 'GPKG');
        """
        con.execute(copy_query)
    except Exception as e:
        raise RuntimeError(
            f"Overture Maps query failed for theme='{t_name}', type='{t_type}'. Error: {e}. "
            f"Tip: If a column or filter fails, adjust parameters or use `query_duckdb_spatial` for custom SQL against '{s3_path}'."
        ) from e
    finally:
        try:
            con.close()
        except Exception:
            pass

    layer_label = output_name or f"Overture_{t_name.capitalize()}"
    lyr = QgsVectorLayer(out_file, layer_label, "ogr")
    if not lyr.isValid():
        raise ValueError(f"Failed to load Overture data exported to {out_file}")
    p = QgsProject.instance()
    is_first = len([l for l in p.mapLayers().values() if l.providerType() != "wms"]) == 0
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


@tool("Execute arbitrary spatial DuckDB queries on remote Parquet, S3, GeoParquet, or local tabular files, and load the result as a QGIS vector layer.",
      {"query": S("string", "DuckDB SQL query returning geometry and attributes"),
       "layer_name": S("string", "name of resulting layer in QGIS")})
def query_duckdb_spatial(query, layer_name="DuckDB_Layer"):
    import duckdb
    out_file = os.path.join(tempfile.gettempdir(), f"duckdb_result_{int(time.time())}.gpkg")
    if os.path.exists(out_file):
        try:
            os.remove(out_file)
        except Exception:
            pass
    con = duckdb.connect()
    try:
        con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
        con.execute("SET s3_region='us-west-2';")
        con.execute("SET geometry_always_xy=true;")
        con.execute("SET memory_limit='1GB';")
        con.execute("SET threads=2;")
        con.execute("SET preserve_insertion_order=false;")

        copy_query = f"COPY ({query}) TO '{out_file}' WITH (FORMAT GDAL, DRIVER 'GPKG');"
        con.execute(copy_query)
    except Exception as e:
        raise RuntimeError(f"DuckDB spatial query failed. Query: {query}. Error: {e}") from e
    finally:
        try:
            con.close()
        except Exception:
            pass

    lyr = QgsVectorLayer(out_file, layer_name, "ogr")
    if not lyr.isValid():
        raise ValueError(f"Failed to load DuckDB spatial query output from {out_file}")
    p = QgsProject.instance()
    is_first = len([l for l in p.mapLayers().values() if l.providerType() != "wms"]) == 0
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


# ---------------------------------------------------------------- escape hatch
@tool("Execute arbitrary PyQGIS code (iface, QgsProject, processing in scope). Assign to `result` to return a value. Only works if the user enabled it in the plugin panel.",
      {"code": S("string", "python source")})
def exec_pyqgis(code):
    if CANCEL_REQUESTED:
        raise RuntimeError("Execution stopped by user.")
    if not ALLOW_PYTHON["value"]:
        raise PermissionError("exec_pyqgis is disabled by the user. Use the dedicated tools.")
    import processing
    from qgis.PyQt.QtCore import QEventLoop
    from qgis.PyQt.QtWidgets import QApplication
    QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
    ns = {"iface": _iface(), "QgsProject": QgsProject, "processing": processing, "result": None}
    exec("from qgis.core import *\nfrom qgis.gui import *", ns)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, ns)
    QApplication.processEvents()
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
    elif "name" in params and "name" not in clean_args:
        for alias in ("layout", "layout_name", "layer", "layer_name"):
            if alias in clean_args:
                clean_args["name"] = clean_args.pop(alias)
                break

    # If function does not accept **kwargs, drop extra unexpected keys to avoid TypeError
    if not has_var_keyword:
        filtered_args = {k: v for k, v in clean_args.items() if k in params}
        return fn(**filtered_args)

    return fn(**clean_args)
