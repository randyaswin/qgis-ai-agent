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
    return _iface().mapCanvas()


def _refresh():
    _canvas().refreshAllLayers()


# ---------------------------------------------------------------- project
@tool("Snapshot of the whole session: project CRS, layers (fields, geometry, renderer), canvas extent, layouts. Call this first and after every change.")
def get_project_state():
    p = QgsProject.instance()
    order = [n.layerId() for n in p.layerTreeRoot().findLayers()]
    ext = _canvas().extent()
    return {
        "project_file": p.fileName(),
        "crs": p.crs().authid(),
        "layers_top_to_bottom": [_layer_info(p.mapLayer(i)) for i in order if p.mapLayer(i)],
        "canvas_extent": [ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()],
        "canvas_scale": _canvas().scale(),
        "layouts": [l.name() for l in p.layoutManager().layouts()],
        "qgis_version": Qgis.version(),
    }


@tool("Clear the project and start fresh.", {"crs": S("string", "e.g. EPSG:4326 or EPSG:32748")})
def new_project(crs="EPSG:4326"):
    QgsProject.instance().clear()
    QgsProject.instance().setCrs(QgsCoordinateReferenceSystem(crs))
    return get_project_state()


@tool("Set project CRS.", {"crs": S("string", "authid")})
def set_project_crs(crs):
    c = QgsCoordinateReferenceSystem(crs)
    if not c.isValid():
        raise ValueError(f"Invalid CRS {crs}")
    QgsProject.instance().setCrs(c)
    _refresh()
    return {"crs": c.authid()}


@tool("Save project to .qgz/.qgs.", {"path": S("string", "absolute path")})
def save_project(path):
    ok = QgsProject.instance().write(path)
    return {"saved": ok, "path": path}


@tool("Open an existing project file.", {"path": S("string", "absolute path")})
def open_project(path):
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
    QgsProject.instance().addMapLayer(lyr)
    return _layer_info(lyr)


@tool("Add a raster layer (GeoTIFF, DEM, WMS uri...).",
      {"path": S("string", "file path or uri"), "name": S("string", ""), "provider": S("string", "gdal (default) or wms")})
def add_raster_layer(path, name=None, provider="gdal"):
    lyr = QgsRasterLayer(path, name or os.path.splitext(os.path.basename(path))[0], provider)
    if not lyr.isValid():
        raise ValueError(f"Invalid raster source: {path}")
    QgsProject.instance().addMapLayer(lyr)
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
    if bbox:
        r = QgsRectangle(*bbox)
    else:
        lyr = _layer(layer)
        r = QgsCoordinateTransform(lyr.crs(), QgsProject.instance().crs(), QgsProject.instance()).transformBoundingBox(lyr.extent())
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
    d = {"extent_layer_crs": [e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()]}
    try:
        w = QgsCoordinateTransform(lyr.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), p).transformBoundingBox(e)
        d["extent_wgs84"] = [round(w.xMinimum(), 6), round(w.yMinimum(), 6), round(w.xMaximum(), 6), round(w.yMaximum(), 6)]
    except Exception:
        pass
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
def describe_layer(layer, sample=5, max_unique=30):
    lyr = _layer(layer)
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
    values = sorted(lyr.uniqueValues(idx), key=str) if idx >= 0 else []
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
    return {"categories": len(cats)}


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
    fcn = QgsColorRampShader(st.minimumValue, st.maximumValue, r)
    fcn.classifyColorRamp(int(classes), int(band), lyr.extent(), lyr.dataProvider())
    sh = QgsRasterShader()
    sh.setRasterShaderFunction(fcn)
    lyr.setRenderer(QgsSingleBandPseudoColorRenderer(lyr.dataProvider(), int(band), sh))
    lyr.triggerRepaint()
    _iface().layerTreeView().refreshLayerSymbology(lyr.id())
    return {"min": st.minimumValue, "max": st.maximumValue}


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
        sb.setStyle("Single Box")
        sb.setLinkedMap(mp)
        sb.applyDefaultSize()
        lay.addLayoutItem(sb)
        sb.attemptMove(QgsLayoutPoint(m + 5, H - bottom - 15, mm))

    if north_arrow:
        na = QgsLayoutItemPicture(lay)
        na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
        na.setLinkedMap(mp)
        lay.addLayoutItem(na)
        na.attemptResize(QgsLayoutSize(12, 18, mm))
        na.attemptMove(QgsLayoutPoint(W - m - side - 17, top + 5, mm))

    if credits:
        label(credits, m, H - m - 8, W - 2 * m, 8, 8)

    mgr.addLayout(lay)
    return {"layout": name, "page_mm": [W, H], "map_scale": mp.scale()}


def _get_layout(layout_name):
    mgr = QgsProject.instance().layoutManager()
    if not layout_name:
        layouts = mgr.printLayouts()
        if layouts:
            return layouts[0]
        raise ValueError("No layout specified and active project has no layouts.")
    lay = mgr.layoutByName(str(layout_name))
    if not lay:
        for l in mgr.printLayouts():
            if l.name().lower() == str(layout_name).lower():
                return l
        avail = [l.name() for l in mgr.printLayouts()]
        raise ValueError(f"No layout {layout_name!r}. Available: {avail}")
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


@tool("Create a publication-quality styled print layout with professional themes: 'infographic_disaster' (disaster dashboard with header banner, side metric panels, inset, chart, and multi-bar legends), 'natgeo' (National Geographic: classic neatline borders, serif typography, warm tones), 'relief_topographic' (Swiss/Imhof alpine topo), or 'modern_clean' (executive corporate).",
      {"name": S("string", "layout name, default 'Map'"),
       "style_theme": S("string", "'infographic_disaster' | 'infographic_bencana' | 'natgeo' | 'relief_topographic' | 'modern_clean'"),
       "title": S("string", "main map title"), "subtitle": S("string", "subtitle"),
       "credits": S("string", "credits and data source"),
       "extent_layer": S("string", "main study area layer name"),
       "page_size": S("string", "A4|A3|A2, default A3"),
       "orientation": S("string", "landscape|portrait, default landscape"),
       "grid": S("boolean", "include coordinate grid (default false)")})
def create_styled_layout(name="Map", style_theme="infographic_disaster", title="", subtitle="", credits="",
                         extent_layer=None, page_size="A3", orientation="landscape", grid=False, **kwargs):
    import html as _html
    import math
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
    m = 10

    # Monkey-patch QgsLayoutItemShape.setFillColor for backward/script compatibility
    try:
        from qgis.core import QgsLayoutItemShape, QgsFillSymbol
        from qgis.PyQt.QtGui import QColor
        if hasattr(QgsLayoutItemShape, "setSymbol") and not hasattr(QgsLayoutItemShape, "setFillColor"):
            def _shape_set_fill_color(self, color):
                c = QColor(color) if isinstance(color, str) else color
                sym = self.symbol()
                if sym:
                    sym.setColor(c)
                else:
                    self.setSymbol(QgsFillSymbol.createSimple({"color": c.name(), "outline_color": "transparent"}))
            QgsLayoutItemShape.setFillColor = _shape_set_fill_color
    except Exception:
        pass
    is_natgeo = (style_theme == "natgeo")
    is_infographic = (style_theme in ("infographic_bencana", "infographic_disaster"))
    is_relief = (style_theme == "relief_topographic")

    font_family = "Georgia" if is_natgeo else "Arial"

    # NatGeo page background tint
    if is_natgeo:
        page.setBackgroundColor(QColor("#fcfbf7"))

    # Top Header
    header_h = 18 if title else 0
    if title:
        header_card = QgsLayoutItemLabel(lay)
        header_card.setMode(QgsLayoutItemLabel.Mode.ModeHtml)
        sub_txt = f"<div style='font-size: 10.5px; color: #546e7a; margin-top: 2px;'>{_html.escape(subtitle)}</div>" if subtitle else ""
        border_css = "border: 1.5px solid #263238;" if is_natgeo else "border: 1px solid #cfd8dc; border-radius: 5px;"
        bg_css = "background: #fbf9f4;" if is_natgeo else "background: #ffffff;"
        header_card.setText(
            f"<div style='font-family: {font_family}, serif; text-align: center; padding: 5px; {bg_css} {border_css}'>"
            f"<div style='font-size: 15px; font-weight: bold; color: #0d47a1; letter-spacing: 0.8px;'>{_html.escape(title)}</div>"
            f"{sub_txt}"
            f"</div>"
        )
        lay.addLayoutItem(header_card)
        header_card.attemptMove(QgsLayoutPoint(m, m, mm))
        header_card.attemptResize(QgsLayoutSize(W - 2 * m, header_h, mm))

    # Main Map frame placement with strict non-overlapping gutters
    map_top = m + (header_h + 3 if title else 0)
    bottom_h = 58 if is_infographic else 18
    right_w = 82 if is_infographic else 0
    map_w = W - 2 * m - right_w
    map_h = H - map_top - bottom_h

    mp = QgsLayoutItemMap(lay)
    mp.setRect(0, 0, 10, 10)
    lay.addLayoutItem(mp)
    mp.setCrs(p.crs())
    mp.attemptMove(QgsLayoutPoint(m, map_top, mm))
    mp.attemptResize(QgsLayoutSize(map_w, map_h, mm))

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
    mp.setFrameStrokeColor(QColor("#334155"))
    mp.setFrameStrokeWidth(QgsLayoutMeasurement(0.3, mm))

    # Coordinate Grid (default False; when enabled, coordinates stay INSIDE frame so they never overlap outer panels)
    if grid:
        from qgis.core import QgsLayoutItemMapGrid, QgsLineSymbol
        g = QgsLayoutItemMapGrid("grid", mp)
        g.setStyle(QgsLayoutItemMapGrid.GridStyle.Cross)
        g.setCrossLength(1.5)
        map_crs = p.crs()
        g.setCrs(map_crs)
        if map_crs.isGeographic():
            iv = max(round(max(ext.width(), ext.height()) / 4, 3), 0.001)
            g.setIntervalX(iv)
            g.setIntervalY(iv)
            g.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.DegreeMinute)
            g.setAnnotationPrecision(1)
        else:
            raw_iv = max(ext.width(), ext.height()) / 5
            mag = 10 ** math.floor(math.log10(max(raw_iv, 100)))
            clean_iv = max(round(raw_iv / mag) * mag, mag)
            g.setIntervalX(clean_iv)
            g.setIntervalY(clean_iv)
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
        mp.grids().addGrid(g)

    # Scale bar (placed inside map at bottom left)
    sb = QgsLayoutItemScaleBar(lay)
    sb.setStyle("Single Box" if is_natgeo else "Line Ticks Up")
    sb.setLinkedMap(mp)
    sb.applyDefaultSize()
    lay.addLayoutItem(sb)
    sb.attemptMove(QgsLayoutPoint(m + 5, map_top + map_h - 12, mm))

    # North arrow (placed inside map at top right, clear of right panel)
    na = QgsLayoutItemPicture(lay)
    na.setPicturePath(":/images/north_arrows/layout_default_north_arrow.svg")
    na.setLinkedMap(mp)
    lay.addLayoutItem(na)
    na.attemptResize(QgsLayoutSize(12, 18, mm))
    na.attemptMove(QgsLayoutPoint(m + map_w - 18, map_top + 4, mm))

    # Credits footer (placed at bottom margin)
    if credits:
        lbl_cred = QgsLayoutItemLabel(lay)
        lbl_cred.setText(f"Sources: {credits} | Created with QGIS {Qgis.version()}")
        fmt = QgsTextFormat()
        fmt.setSize(7.5)
        lbl_cred.setTextFormat(fmt)
        lay.addLayoutItem(lbl_cred)
        lbl_cred.attemptMove(QgsLayoutPoint(m, H - m - 4, mm))
        lbl_cred.attemptResize(QgsLayoutSize(W - 2 * m, 5, mm))

    mgr.addLayout(lay)
    return {"layout": name, "theme": style_theme, "page_mm": [W, H], "main_map_scale": mp.scale(),
            "right_panel_rect": [m + map_w + 3, map_top, right_w - 3, map_h] if is_infographic else None,
            "bottom_panel_rect": [m, map_top + map_h + 3, W - 2 * m, bottom_h - 3 - 6] if is_infographic else None}


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
        out_dir = os.path.expanduser("~/qgis-ai-maps")
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
        st = QgsLayoutExporter.ImageExportSettings(); st.dpi = 80
        ex.exportToImage(tmp, st)
        with open(tmp, "rb") as fh:
            out = {"__image__": base64.b64encode(fh.read()).decode(), "mime": "image/png", "meta": out}
    return out


@tool("Open the layout designer window for the user.", {"name": S("string", "")})
def open_layout_designer(name=None, **kwargs):
    target = name or kwargs.get("layout") or kwargs.get("layout_name")
    lay = _get_layout(target)
    _iface().openLayoutDesigner(lay)
    return {"opened": target}


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
        # Use current canvas extent in EPSG:4326
        c = _canvas()
        tr = QgsCoordinateTransform(QgsProject.instance().crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        wgs_ext = tr.transformBoundingBox(c.extent())
        bbox = [round(wgs_ext.xMinimum(), 4), round(wgs_ext.yMinimum(), 4),
                round(wgs_ext.xMaximum(), 4), round(wgs_ext.yMaximum(), 4)]

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
    QgsProject.instance().addMapLayer(lyr)
    return _layer_info(lyr)


@tool("Query Overture Maps Foundation open data (buildings, places/POI, transportation/roads, water) directly from S3 parquet using DuckDB and load it into QGIS.",
      {"theme": S("string", "'places' | 'buildings' | 'transportation' | 'base' | 'divisions' (default 'places')"),
       "type": S("string", "optional subtype: 'place', 'building', 'segment', 'water' (auto-selected if omitted)"),
       "bbox": S("array", "optional [xmin, ymin, xmax, ymax] in WGS84 (default: current map canvas extent)"),
       "filter_sql": S("string", "optional SQL condition, e.g. \"taxonomy.primary = 'restaurant'\" or \"names.primary ILIKE '%hotel%'\""),
       "limit": S("integer", "max features to fetch, default 500"),
       "output_name": S("string", "layer name in QGIS, default e.g. 'Overture_Places'")})
def query_overture_maps(theme="places", type=None, bbox=None, filter_sql=None, limit=500, output_name=None):
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
        tr = QgsCoordinateTransform(QgsProject.instance().crs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
        wgs_ext = tr.transformBoundingBox(c.extent())
        bbox = [round(wgs_ext.xMinimum(), 5), round(wgs_ext.yMinimum(), 5),
                round(wgs_ext.xMaximum(), 5), round(wgs_ext.yMaximum(), 5)]

    raw_xmin, raw_ymin, raw_xmax, raw_ymax = bbox
    xmin = min(raw_xmin, raw_xmax)
    xmax = max(raw_xmin, raw_xmax)
    ymin = min(raw_ymin, raw_ymax)
    ymax = max(raw_ymin, raw_ymax)

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

    out_file = os.path.join(tempfile.gettempdir(), f"overture_{t_name}_{int(_time.time())}.geojson")
    s3_path = f"s3://overturemaps-us-west-2/release/2026-10-21.0/theme={t_name}/type={t_type}/*"

    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET geometry_always_xy=true;")

    copy_query = f"""
    COPY (
        SELECT {select_clause}
        FROM read_parquet('{s3_path}', hive_partitioning=1)
        WHERE {' AND '.join(where_clauses)}
        LIMIT {int(limit)}
    ) TO '{out_file}' WITH (FORMAT GDAL, DRIVER 'GeoJSON');
    """
    try:
        con.execute(copy_query)
    except Exception as e:
        raise RuntimeError(
            f"Overture Maps query failed for theme='{t_name}', type='{t_type}'. Error: {e}. "
            f"Tip: If a column or filter fails, adjust parameters or use `query_duckdb_spatial` for custom SQL against '{s3_path}'."
        ) from e

    layer_label = output_name or f"Overture_{t_name.capitalize()}"
    lyr = QgsVectorLayer(out_file, layer_label, "ogr")
    if not lyr.isValid():
        raise ValueError(f"Failed to load Overture data exported to {out_file}")
    QgsProject.instance().addMapLayer(lyr)
    return _layer_info(lyr)


@tool("Execute arbitrary spatial DuckDB queries on remote Parquet, S3, GeoParquet, or local tabular files, and load the result as a QGIS vector layer.",
      {"query": S("string", "DuckDB SQL query returning geometry and attributes"),
       "layer_name": S("string", "name of resulting layer in QGIS")})
def query_duckdb_spatial(query, layer_name="DuckDB_Layer"):
    import duckdb
    out_file = os.path.join(tempfile.gettempdir(), f"duckdb_result_{int(time.time())}.geojson")
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    con.execute("SET geometry_always_xy=true;")

    copy_query = f"COPY ({query}) TO '{out_file}' WITH (FORMAT GDAL, DRIVER 'GeoJSON');"
    try:
        con.execute(copy_query)
    except Exception as e:
        raise RuntimeError(f"DuckDB spatial query failed. Query: {query}. Error: {e}") from e

    lyr = QgsVectorLayer(out_file, layer_name, "ogr")
    if not lyr.isValid():
        raise ValueError(f"Failed to load DuckDB spatial query output from {out_file}")
    QgsProject.instance().addMapLayer(lyr)
    return _layer_info(lyr)


# ---------------------------------------------------------------- escape hatch
@tool("Execute arbitrary PyQGIS code (iface, QgsProject, processing in scope). Assign to `result` to return a value. Only works if the user enabled it in the plugin panel.",
      {"code": S("string", "python source")})
def exec_pyqgis(code):
    if not ALLOW_PYTHON["value"]:
        raise PermissionError("exec_pyqgis is disabled by the user. Use the dedicated tools.")
    import processing
    ns = {"iface": _iface(), "QgsProject": QgsProject, "processing": processing, "result": None}
    exec("from qgis.core import *\nfrom qgis.gui import *", ns)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        exec(code, ns)
    return {"stdout": out.getvalue()[-8000:], "result": repr(ns.get("result"))[:8000]}


# ---------------------------------------------------------------- dispatch
from . import layout_tools  # noqa: F401 - register layout composability tools


def list_tools():
    return [{"name": k, "description": v["description"], "inputSchema": v["inputSchema"]} for k, v in TOOLS.items()]


def call(name, args):
    if name not in TOOLS:
        raise ValueError(f"Unknown tool {name}")
    return TOOLS[name]["fn"](**(args or {}))
