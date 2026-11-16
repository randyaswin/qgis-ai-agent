"""Composable print-layout primitives and templates.

Each tool places one element (map frame, shape, text, picture, legend, scale bar,
north arrow) at explicit page coordinates in millimetres and gives it an item id,
so the agent can build any composition (posters, wayfinding signs, atlases,
dashboards) and adjust it afterwards with `list_layout_items` / `update_layout_item`.
Registered into tools.TOOLS on import.
"""
import glob
import html as _html
import math
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont
from qgis.core import (
    Qgis, QgsProject, QgsPrintLayout, QgsLayoutItemPage, QgsLayoutItemMap, QgsLayoutItemLabel,
    QgsLayoutItemLegend, QgsLayoutItemScaleBar, QgsLayoutItemPicture, QgsLayoutItemShape,
    QgsLayoutPoint, QgsLayoutSize, QgsLayoutMeasurement, QgsLayoutItemMapOverview, QgsLayoutItemMapGrid,
    QgsTextFormat, QgsTextBufferSettings, QgsTextBackgroundSettings, QgsFillSymbol, QgsLineSymbol, QgsMarkerSymbol,
    QgsSimpleMarkerSymbolLayer, QgsSvgMarkerSymbolLayer, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsPointXY, QgsField, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsRectangle,
    QgsSingleSymbolRenderer, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsLegendStyle,
    QgsApplication, QgsUnitTypes,
)
from qgis.PyQt.QtCore import QMetaType

from .tools import tool, S, LOGGER, _layer, _canvas, _safe_transform_extent, _is_valid_extent, _open_layout_window, _register_layout, _project_scratch_file

MM = Qgis.LayoutUnit.Millimeters
ALIGN_H = {"left": Qt.AlignmentFlag.AlignLeft, "center": Qt.AlignmentFlag.AlignHCenter,
           "right": Qt.AlignmentFlag.AlignRight, "justify": Qt.AlignmentFlag.AlignJustify}
ALIGN_V = {"top": Qt.AlignmentFlag.AlignTop, "middle": Qt.AlignmentFlag.AlignVCenter,
           "bottom": Qt.AlignmentFlag.AlignBottom}

THEMES = {
    # name: (frame/background, header text, panel fill, panel text, accent, section label)
    "dark": ("#1b1d21", "#ffffff", "#f4f1e8", "#1b1d21", "#c62828", "#e0e0e0"),
    "earth": ("#4a3527", "#ffffff", "#fbf7ee", "#2b1d14", "#c62828", "#f3e9dc"),
    "light": ("#eceff1", "#263238", "#ffffff", "#263238", "#c62828", "#455a64"),
    "navy": ("#14263f", "#ffffff", "#f5f7fa", "#14263f", "#d84315", "#cfd8dc"),
}


# ---------------------------------------------------------------- helpers
def _lay(name):
    mgr = QgsProject.instance().layoutManager()
    if not name:
        layouts = mgr.printLayouts()
        if layouts:
            return layouts[0]
        raise ValueError("No layout name provided and active project has no layouts. Create one with create_blank_layout / create_layout first.")
    lay = mgr.layoutByName(str(name))
    if not lay:
        for l in mgr.printLayouts():
            if l.name().lower() == str(name).lower():
                return l
        avail = [l.name() for l in mgr.printLayouts()]
        raise ValueError(f"No layout {name!r}. Available layouts: {avail}. Create it with create_blank_layout / create_layout first.")
    _open_layout_window(lay)
    return lay


def _place(lay, item, x, y, w=None, h=None, item_id=None, rotation=0):
    lay.addLayoutItem(item)
    item.attemptMove(QgsLayoutPoint(float(x), float(y), MM))
    if w is not None and h is not None:
        item.attemptResize(QgsLayoutSize(float(w), float(h), MM))
    if rotation:
        item.setItemRotation(float(rotation), True)
    if item_id:
        item.setId(str(item_id))
    return item


def _find(lay, item_id):
    it = lay.itemById(str(item_id))
    if not it:
        raise ValueError(f"No item with id {item_id!r} in layout. Use list_layout_items.")
    return it


def _map(lay, map_id=None):
    if map_id:
        it = _find(lay, map_id)
        if not isinstance(it, QgsLayoutItemMap):
            raise ValueError(f"Item {map_id!r} is not a map frame")
        return it
    maps = [i for i in lay.items() if isinstance(i, QgsLayoutItemMap)]
    if not maps:
        raise ValueError("Layout has no map frame yet. Call add_layout_map first.")
    return maps[0]


def _fmt(size=10, color="#000000", bold=False, font=None, italic=False, buffer_color=None, buffer_size=0.0, html=False):
    fmt = QgsTextFormat()
    f = QFont(font) if font else QFont()
    f.setBold(bool(bold))
    f.setItalic(bool(italic))
    fmt.setFont(f)
    fmt.setSize(float(size))
    fmt.setColor(QColor(color))
    if buffer_color and buffer_size:
        b = QgsTextBufferSettings()
        b.setEnabled(True)
        b.setSize(float(buffer_size))
        b.setColor(QColor(buffer_color))
        fmt.setBuffer(b)
    if html and hasattr(fmt, "setAllowHtmlFormatting"):
        fmt.setAllowHtmlFormatting(True)
    return fmt


def _extent(extent_layer=None, bbox=None, bbox_crs=None, margin=0.05):
    p = QgsProject.instance()
    ext = None
    if bbox:
        b = QgsRectangle(*[float(v) for v in bbox])
        if bbox_crs:
            ext = _safe_transform_extent(b, QgsCoordinateReferenceSystem(bbox_crs), p.crs(), p)
        elif p.crs().isValid() and not p.crs().isGeographic() and abs(b.xMinimum()) <= 180.0 and abs(b.xMaximum()) <= 180.0 and abs(b.yMinimum()) <= 90.0 and abs(b.yMaximum()) <= 90.0:
            # Auto-transform WGS84 lon/lat bbox into projected project CRS so map never renders blank at (110m, -7m)
            ext = _safe_transform_extent(b, QgsCoordinateReferenceSystem("EPSG:4326"), p.crs(), p)
        else:
            ext = b if _is_valid_extent(b) else None
    elif extent_layer:
        try:
            lyr = _layer(extent_layer)
            ext = _safe_transform_extent(lyr.extent(), lyr.crs(), p.crs(), p)
        except Exception:
            ext = None

    if not _is_valid_extent(ext):
        c = _canvas()
        canv_ext = c.extent() if c else None
        if _is_valid_extent(canv_ext):
            ext = QgsRectangle(canv_ext)
        else:
            ext = QgsRectangle(-180, -85, 180, 85)

    if margin and _is_valid_extent(ext):
        ext.scale(1 + float(margin))
    return ext


def _svg_path(name):
    """Resolve an SVG by absolute path, or by name/relative path inside QGIS SVG search paths."""
    if not name:
        return None
    if os.path.isfile(name) or name.startswith(":/"):
        return name
    for root in QgsApplication.svgPaths():
        for cand in (os.path.join(root, name), os.path.join(root, name + ".svg")):
            if os.path.isfile(cand):
                return cand
        hits = glob.glob(os.path.join(root, "**", os.path.basename(name).removesuffix(".svg") + ".svg"), recursive=True)
        if hits:
            return hits[0]
    raise ValueError(f"SVG icon {name!r} not found. Use list_svg_icons(query) to search.")


def marker_symbol(color="#c62828", size=4.0, shape="circle", svg=None, outline_color="#ffffff",
                  outline_width=0.4, icon_background=None, icon_color="#ffffff"):
    """Marker symbol: simple shape, SVG icon, or SVG glyph on a coloured badge (wayfinding style)."""
    layers = []
    if svg and icon_background:
        bg = QgsSimpleMarkerSymbolLayer.create({"name": shape if shape in ("square", "circle") else "square",
                                                "color": QColor(icon_background).name(), "outline_color": outline_color,
                                                "outline_width": str(outline_width), "size": str(size)})
        layers.append(bg)
        ic = QgsSvgMarkerSymbolLayer(_svg_path(svg), float(size) * 0.7)
        ic.setFillColor(QColor(icon_color))
        ic.setStrokeColor(QColor(icon_color))
        layers.append(ic)
    elif svg:
        ic = QgsSvgMarkerSymbolLayer(_svg_path(svg), float(size))
        ic.setFillColor(QColor(color))
        ic.setStrokeColor(QColor(outline_color))
        layers.append(ic)
    else:
        layers.append(QgsSimpleMarkerSymbolLayer.create({"name": shape, "color": QColor(color).name(),
                                                         "outline_color": outline_color,
                                                         "outline_width": str(outline_width), "size": str(size)}))
    return QgsMarkerSymbol(layers)


# ---------------------------------------------------------------- page
@tool("Create an empty print layout (page only) to compose freely with add_layout_* tools. Replaces a layout with the same name. "
      "Use for posters, wayfinding signs, infographics, or any custom composition.",
      {"name": S("string", "layout name"), "page_size": S("string", "A4|A3|A2|A1|A0|Letter|Tabloid, or 'custom'"),
       "orientation": S("string", "landscape|portrait"),
       "width": S("number", "page width mm (only with page_size='custom')"),
       "height": S("number", "page height mm (only with page_size='custom')"),
       "background": S("string", "page colour, default white")})
def create_blank_layout(name="Map", page_size="A4", orientation="landscape", width=None, height=None, background="#ffffff"):
    p = QgsProject.instance()
    mgr = p.layoutManager()
    old = mgr.layoutByName(name)
    if old:
        mgr.removeLayout(old)
    lay = QgsPrintLayout(p)
    lay.initializeDefaults()
    lay.setName(name)
    page = lay.pageCollection().page(0)
    if str(page_size).lower() == "custom" and width and height:
        page.setPageSize(QgsLayoutSize(float(width), float(height), MM))
    else:
        ori = QgsLayoutItemPage.Orientation.Landscape if orientation == "landscape" else QgsLayoutItemPage.Orientation.Portrait
        page.setPageSize(page_size, ori)
    sym = QgsFillSymbol.createSimple({"color": QColor(background).name(), "outline_style": "no"})
    page.setPageStyleSymbol(sym)
    _register_layout(mgr, lay)
    W, H = page.pageSize().width(), page.pageSize().height()
    return {"layout": name, "page_mm": [W, H], "hint": "Origin is the top-left corner; x grows right, y grows down (mm)."}


# ---------------------------------------------------------------- elements
@tool("Add a map frame to a layout at an explicit position. Supports fixed layer sets (e.g. a key map with only basemap + boundaries), "
      "a scale or extent, an overview rectangle showing another map's extent, a coordinate grid and frame styling.",
      {"layout": S("string", ""), "x": S("number", "mm"), "y": S("number", "mm"), "width": S("number", "mm"), "height": S("number", "mm"),
       "item_id": S("string", "unique id, e.g. 'main_map', 'key_map'"),
       "extent_layer": S("string", "fit to this layer"), "bbox": S("array", "[xmin,ymin,xmax,ymax]", items={"type": "number"}),
       "bbox_crs": S("string", "CRS of bbox, default project CRS, e.g. 'EPSG:4326'"),
       "scale": S("number", "fixed scale denominator (applied after extent)"),
       "margin": S("number", "fractional padding around extent, default 0.05"),
       "layers": S("array", "lock this map to these layers (top to bottom); default = all visible layers", items={"type": "string"}),
       "overview_of": S("string", "item id of another map whose extent is drawn as a rectangle on this map"),
       "overview_color": S("string", "overview outline colour, default '#c62828'"),
       "frame_color": S("string", "frame stroke colour, default '#333333'"), "frame_width": S("number", "mm, default 0.3; 0 = no frame"),
       "background": S("string", "map background colour"),
       "grid": S("boolean", "draw a coordinate grid"), "grid_interval": S("number", "grid interval in map units"),
       "rotation": S("number", "map rotation degrees")})
def add_layout_map(layout=None, x=0, y=0, width=100, height=100, item_id=None, extent_layer=None, bbox=None, bbox_crs=None, scale=None,
                   margin=0.05, layers=None, overview_of=None, overview_color="#c62828", frame_color="#333333",
                   frame_width=0.3, background=None, grid=False, grid_interval=None, rotation=None, cover=False, **kwargs):
    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    mp = QgsLayoutItemMap(lay)
    mp.setRect(0, 0, 10, 10)
    mp.setCrs(QgsProject.instance().crs())
    _place(lay, mp, x, y, width, height, item_id)
    ext = _extent(extent_layer, bbox, bbox_crs, 0.0 if cover else margin)
    if cover and _is_valid_extent(ext) and float(width) > 0 and float(height) > 0:
        f_ar = float(width) / float(height)
        e_ar = ext.width() / max(ext.height(), 1e-12)
        cx, cy = ext.center().x(), ext.center().y()
        if e_ar < f_ar:
            nw, nh = ext.width(), ext.width() / f_ar
        else:
            nh, nw = ext.height(), ext.height() * f_ar
        ext = QgsRectangle(cx - nw * 0.5, cy - nh * 0.5, cx + nw * 0.5, cy + nh * 0.5)
        mp.setExtent(ext)
    else:
        mp.zoomToExtent(ext)
    if scale:
        mp.setScale(float(scale))
    if layers:
        mp.setLayers([_layer(l) for l in layers])
        mp.setKeepLayerSet(True)
    if rotation:
        mp.setMapRotation(float(rotation))
    if frame_width and float(frame_width) > 0:
        mp.setFrameEnabled(True)
        mp.setFrameStrokeColor(QColor(frame_color))
        mp.setFrameStrokeWidth(QgsLayoutMeasurement(float(frame_width), MM))
    if background:
        mp.setBackgroundColor(QColor(background))
    if overview_of:
        src = _map(lay, overview_of)
        ov = QgsLayoutItemMapOverview("overview", mp)
        ov.setLinkedMap(src)
        ov.setFrameSymbol(QgsFillSymbol.createSimple({"color": "0,0,0,0", "outline_color": QColor(overview_color).name(),
                                                      "outline_width": "0.8"}))
        mp.overviews().addOverview(ov)
    if grid:
        import math
        g = QgsLayoutItemMapGrid("grid", mp)
        g.setStyle(QgsLayoutItemMapGrid.GridStyle.Cross)
        g.setCrossLength(1.5)
        map_crs = mp.crs() if hasattr(mp, "crs") and mp.crs().isValid() else QgsProject.instance().crs()
        if map_crs.isGeographic():
            iv = float(grid_interval) if grid_interval else max(round(max(ext.width(), ext.height()) / 4, 3), 0.001)
            g.setIntervalX(iv)
            g.setIntervalY(iv)
            g.setAnnotationFormat(QgsLayoutItemMapGrid.AnnotationFormat.DegreeMinute)
            g.setAnnotationPrecision(1)
        else:
            raw_iv = float(grid_interval) if grid_interval else max(ext.width(), ext.height()) / 5
            mag = 10 ** math.floor(math.log10(max(raw_iv, 1)))
            iv = max(round(raw_iv / mag) * mag, mag)
            g.setIntervalX(iv)
            g.setIntervalY(iv)
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
        g.setLineSymbol(QgsLineSymbol.createSimple({"color": "#94a3b8", "width": "0.15", "opacity": "0.6"}))
        mp.grids().addGrid(g)
    lay.refresh()
    return {"item_id": mp.id() or mp.uuid(), "scale": round(mp.scale()), "extent": [ext.xMinimum(), ext.yMinimum(), ext.xMaximum(), ext.yMaximum()]}


@tool("Add a rectangle, rounded rectangle, ellipse or triangle shape (header bands, panel backgrounds, badges, dividers).",
      {"layout": S("string", ""), "x": S("number", "mm"), "y": S("number", "mm"), "width": S("number", "mm"), "height": S("number", "mm"),
       "item_id": S("string", ""), "shape": S("string", "rectangle|ellipse|triangle, default rectangle"),
       "fill": S("string", "fill colour, 'none' for transparent"), "stroke": S("string", "outline colour, 'none' for no outline"),
       "stroke_width": S("number", "mm, default 0.3"), "corner_radius": S("number", "mm, rounded corners for rectangles"),
       "opacity": S("number", "0..1"), "rotation": S("number", "clockwise rotation degrees (0, 90, 180, 270)")})
def add_layout_shape(layout=None, x=0, y=0, width=10, height=10, item_id=None, shape="rectangle", fill="#ffffff", stroke="none",
                     stroke_width=0.3, corner_radius=0, opacity=None, rotation=0, **kwargs):
    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    it = QgsLayoutItemShape(lay)
    kinds = {"rectangle": QgsLayoutItemShape.Shape.Rectangle, "ellipse": QgsLayoutItemShape.Shape.Ellipse,
             "triangle": QgsLayoutItemShape.Shape.Triangle}
    it.setShapeType(kinds.get(shape, QgsLayoutItemShape.Shape.Rectangle))
    props = {"color": "0,0,0,0" if fill in (None, "none") else QColor(fill).name(),
             "outline_style": "no" if stroke in (None, "none") else "solid",
             "outline_color": "#000000" if stroke in (None, "none") else QColor(stroke).name(),
             "outline_width": str(stroke_width), "outline_width_unit": "MM"}
    it.setSymbol(QgsFillSymbol.createSimple(props))
    if corner_radius:
        it.setCornerRadius(QgsLayoutMeasurement(float(corner_radius), MM))
    if opacity is not None:
        it.setItemOpacity(float(opacity))
    _place(lay, it, x, y, width, height, item_id, rotation=rotation)
    return {"item_id": it.id() or it.uuid()}


@tool("Add a text box. Supports font, size, colour, bold/italic, alignment, rotation, a background fill, padding and simple inline HTML "
      "(<b>, <i>, <br>, <span style='font-size:..;color:..'>) for mixed styles such as multilingual titles.",
      {"layout": S("string", ""), "x": S("number", "mm"), "y": S("number", "mm"), "width": S("number", "mm"), "height": S("number", "mm"),
       "text": S("string", "text; may contain QGIS expressions in [% %]"), "item_id": S("string", ""),
       "size": S("number", "pt, default 10"), "color": S("string", "default '#000000'"), "font": S("string", "font family"),
       "bold": S("boolean", ""), "italic": S("boolean", ""),
       "align": S("string", "left|center|right|justify"), "valign": S("string", "top|middle|bottom"),
       "background": S("string", "fill colour behind text"), "frame_color": S("string", "outline colour"),
       "padding": S("number", "mm, default 1"), "html": S("boolean", "allow inline HTML formatting"),
       "halo_color": S("string", "text halo colour"), "halo_size": S("number", "halo mm"),
       "rotation": S("number", "clockwise rotation degrees (0, 90, 180, 270)")})
def add_layout_text(layout=None, x=0, y=0, width=50, height=10, text="", item_id=None, size=10, color="#000000", font=None, bold=False,
                    italic=False, align="left", valign="middle", background=None, frame_color=None, padding=1.0,
                    html=False, halo_color=None, halo_size=0, rotation=0, **kwargs):
    if "margin_mm" in kwargs:
        padding = kwargs["margin_mm"]
    elif "margin" in kwargs:
        padding = kwargs["margin"]
    elif "padding_mm" in kwargs:
        padding = kwargs["padding_mm"]

    margin_x = float(kwargs.get("margin_x", padding))
    margin_y = float(kwargs.get("margin_y", padding))

    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    it = QgsLayoutItemLabel(lay)
    it.setText(text)
    it.setTextFormat(_fmt(size, color, bold, font, italic, halo_color, halo_size, html))
    it.setHAlign(ALIGN_H.get(align, Qt.AlignmentFlag.AlignLeft))
    it.setVAlign(ALIGN_V.get(valign, Qt.AlignmentFlag.AlignVCenter))
    it.setMarginX(margin_x)
    it.setMarginY(margin_y)
    if background:
        it.setBackgroundEnabled(True)
        it.setBackgroundColor(QColor(background))
    if frame_color:
        it.setFrameEnabled(True)
        it.setFrameStrokeColor(QColor(frame_color))
    _place(lay, it, x, y, width, height, item_id, rotation=rotation)
    return {"item_id": it.id() or it.uuid()}


@tool("Add a picture: photo (PNG/JPG), logo, or SVG icon, from a local path or http(s) URL. Pictures keep aspect ratio inside the box.",
      {"layout": S("string", ""), "x": S("number", "mm"), "y": S("number", "mm"), "width": S("number", "mm"), "height": S("number", "mm"),
       "path": S("string", "file path, URL, or QGIS SVG name (see list_svg_icons)"), "item_id": S("string", ""),
       "fit": S("string", "zoom (keep aspect, default) | stretch | clip | zoom_resize"),
       "frame_color": S("string", "optional border colour"), "svg_fill": S("string", "fill colour for parametrised SVGs"),
       "rotation": S("number", "clockwise rotation degrees (0, 90, 180, 270)")})
def add_layout_picture(layout=None, x=0, y=0, width=10, height=10, path="", item_id=None, fit="zoom", frame_color=None, svg_fill=None, rotation=0, **kwargs):
    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    it = QgsLayoutItemPicture(lay)
    src = path
    if not path.startswith(("http://", "https://", ":/")) and not os.path.isfile(path):
        src = _svg_path(path)
    it.setPicturePath(src)
    modes = {"zoom": QgsLayoutItemPicture.ResizeMode.Zoom, "stretch": QgsLayoutItemPicture.ResizeMode.Stretch,
             "clip": QgsLayoutItemPicture.ResizeMode.Clip, "zoom_resize": QgsLayoutItemPicture.ResizeMode.ZoomResizeFrame}
    it.setResizeMode(modes.get(fit, QgsLayoutItemPicture.ResizeMode.Zoom))
    if svg_fill:
        it.setSvgFillColor(QColor(svg_fill))
    if frame_color:
        it.setFrameEnabled(True)
        it.setFrameStrokeColor(QColor(frame_color))
    _place(lay, it, x, y, width, height, item_id, rotation=rotation)
    return {"item_id": it.id() or it.uuid(), "source": src}


@tool("Add a fully configurable legend: linked map, layer subset and order, title, columns, font sizes, symbol size, background and frame. "
      "Hide layer group headers for clean POI legends.",
      {"layout": S("string", ""), "x": S("number", "mm"), "y": S("number", "mm"),
       "width": S("number", "mm (optional; legend auto-sizes if omitted)"), "height": S("number", "mm (optional)"),
       "item_id": S("string", ""), "map_id": S("string", "linked map item id, default first map"),
       "title": S("string", "legend title, default 'Legend'; '' for none"),
       "layers": S("array", "only these layers, in this order", items={"type": "string"}),
       "columns": S("integer", "default 1"), "title_size": S("number", "pt, default 12"), "item_size": S("number", "pt, default 9"),
       "layer_title_size": S("number", "pt for layer headings, default 10"),
       "hide_layer_titles": S("boolean", "hide layer names, show only categories"),
       "symbol_width": S("number", "mm, default 6"), "symbol_height": S("number", "mm, default 4"),
       "text_color": S("string", ""), "background": S("string", "fill colour, 'none' for transparent"),
       "frame_color": S("string", "outline colour, 'none' for no frame"),
       "filter_by_map": S("boolean", "only show symbols visible in the map extent")})
def add_layout_legend(layout=None, x=0, y=0, width=None, height=None, item_id=None, map_id=None, title="Legend", layers=None,
                      columns=1, title_size=12, item_size=9, layer_title_size=10, hide_layer_titles=False,
                      symbol_width=6, symbol_height=4, text_color="#222222", background="#ffffff", frame_color="#999999",
                      filter_by_map=False, **kwargs):
    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    lg = QgsLayoutItemLegend(lay)
    lg.setTitle(title or "")
    lay.addLayoutItem(lg)
    lg.setLinkedMap(_map(lay, map_id))
    if layers:
        lg.setAutoUpdateModel(False)
        root = lg.model().rootGroup()
        root.clear()
        for n in layers:
            try:
                lyr = _layer(n)
                if lyr:
                    root.addLayer(lyr)
            except Exception:  # nosec B110
                pass
    else:
        # Auto-exclude background basemaps, hillshades, and raw grayscale tiles
        skip_terms = ("esri", "osm", "carto", "basemap", "hillshade", "xyz", "canvas", "satellite", "terrain")
        lg.setAutoUpdateModel(False)
        root = lg.model().rootGroup()
        for ch in list(root.children()):
            low = ch.name().lower() if hasattr(ch, "name") else ""
            if any(st in low for st in skip_terms):
                root.removeChildNode(ch)
        if len(root.children()) == 0:
            lg.setAutoUpdateModel(True)
            root = lg.model().rootGroup()
    if hide_layer_titles:
        from qgis.core import QgsLegendRenderer
        lg.setAutoUpdateModel(False)
        for node in root.findLayers():
            QgsLegendRenderer.setNodeLegendStyle(node, QgsLegendStyle.Style.Hidden)
        lg.updateLegend()
    lg.setColumnCount(int(columns))
    lg.setSplitLayer(int(columns) > 1)
    for style, sz, bold in ((QgsLegendStyle.Style.Title, title_size, True),
                            (QgsLegendStyle.Style.Subgroup, layer_title_size, True),
                            (QgsLegendStyle.Style.Group, layer_title_size, True),
                            (QgsLegendStyle.Style.SymbolLabel, item_size, False)):
        lg.rstyle(style).setTextFormat(_fmt(sz, text_color, bold))
    lg.setSymbolWidth(float(symbol_width))
    lg.setSymbolHeight(float(symbol_height))
    lg.setLegendFilterByMapEnabled(bool(filter_by_map))
    if background and background != "none":
        lg.setBackgroundEnabled(True)
        lg.setBackgroundColor(QColor(background))
    else:
        lg.setBackgroundEnabled(False)
    if frame_color and frame_color != "none":
        lg.setFrameEnabled(True)
        lg.setFrameStrokeColor(QColor(frame_color))
    lg.attemptMove(QgsLayoutPoint(float(x), float(y), MM))
    if width and height:
        lg.setResizeToContents(False)
        lg.attemptResize(QgsLayoutSize(float(width), float(height), MM))
    if item_id:
        lg.setId(str(item_id))
    lg.adjustBoxSize() if not (width and height) else None
    lay.refresh()
    return {"item_id": lg.id() or lg.uuid()}


@tool("Add a scale bar linked to a map frame. Choose style, units and segments, e.g. 'Stepped Line', 'Single Box', 'Double Box', 'Line Ticks Down', 'Line Ticks Up', 'Line Ticks Middle', 'Numeric'. Supports safe auto-positioning ('bottom-left', 'bottom-right', 'top-left') so the bar never spills outside the map frame.",
      {"layout": S("string", ""), "x": S("number", "mm (optional if position is given)"), "y": S("number", "mm (optional if position is given)"), "item_id": S("string", ""),
       "map_id": S("string", "linked map id, default first map"),
       "style": S("string", "'Stepped Line' | 'Single Box' | 'Double Box' | 'Line Ticks Up' | 'Line Ticks Middle' | 'Line Ticks Down' | 'Hollow' | 'Numeric'"),
       "position": S("string", "'bottom-left' | 'bottom-right' | 'top-left' | 'top-right' (auto-positions safely inside the map)"),
       "units": S("string", "m|km|mi|ft, default km (or m for large scales)"),
       "segments": S("integer", "right segments, default 2 (clean and compact)"), "units_per_segment": S("number", "e.g. 100 for 100 m steps"),
       "max_width": S("number", "maximum bar width mm, default 42"),
       "height": S("number", "bar height mm, default 2.0"), "font_size": S("number", "pt, default 6.5"),
       "color": S("string", "primary bar and text colour, default '#1e293b'"),
       "secondary_color": S("string", "alternating box fill colour, default '#ffffff'"),
       "background": S("string", "optional fill behind bar (default '#ffffff' card, or 'none')"),
       "frame_color": S("string", "optional border stroke for the background card (default 'none')")})
def add_layout_scalebar(layout=None, x=None, y=None, item_id=None, map_id=None, style="Stepped Line", position=None,
                        units=None, segments=2, units_per_segment=None, max_width=42.0, height=2.0, font_size=6.5, color="#1e293b",
                        secondary_color="#ffffff", background="#ffffff", frame_color=None, **kwargs):
    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    mp = _map(lay, map_id)
    sb = QgsLayoutItemScaleBar(lay)
    sb.setStyle(style or "Stepped Line")
    sb.setLinkedMap(mp)
    du = Qgis.DistanceUnit
    if not units:
        units = "m" if mp.scale() < 15000 else "km"
    umap = {"m": (du.Meters, "m"), "km": (du.Kilometers, "km"), "mi": (du.Miles, "mi"), "ft": (du.Feet, "ft")}
    unit, label = umap.get(units, (du.Kilometers, "km"))
    sb.applyDefaultSize(unit)
    sb.setUnitLabel(label)
    sb.setNumberOfSegmentsLeft(0)
    sb.setNumberOfSegments(int(segments or 2))
    if units_per_segment:
        sb.setSegmentSizeMode(Qgis.ScaleBarSegmentSizeMode.Fixed)
        sb.setUnitsPerSegment(float(units_per_segment))
    elif hasattr(sb, "setSegmentSizeMode") and hasattr(Qgis, "ScaleBarSegmentSizeMode"):
        sb.setSegmentSizeMode(Qgis.ScaleBarSegmentSizeMode.FitWidth)
        if hasattr(sb, "setMaximumBarWidth"):
            sb.setMaximumBarWidth(float(max_width or 42.0))
        if hasattr(sb, "setMinimumBarWidth"):
            sb.setMinimumBarWidth(max(18.0, float(max_width or 42.0) * 0.5))

    sb.setHeight(float(height or 2.0))
    sb.setTextFormat(_fmt(font_size or 6.5, color))
    if hasattr(sb, "setBoxContentSpace"):
        sb.setBoxContentSpace(1.2)
    if hasattr(sb, "setLabelBarSpace"):
        sb.setLabelBarSpace(1.0)

    # Sophisticated fill & line styling (distinct cartographic aesthetics)
    if hasattr(sb, "fillSymbol") and sb.fillSymbol():
        fs = sb.fillSymbol().clone()
        fs.setColor(QColor(color))
        sb.setFillSymbol(fs)
    if hasattr(sb, "alternateFillSymbol") and sb.alternateFillSymbol():
        afs = sb.alternateFillSymbol().clone()
        afs.setColor(QColor(secondary_color or "#ffffff"))
        sb.setAlternateFillSymbol(afs)
    if hasattr(sb, "lineSymbol") and sb.lineSymbol():
        ls = sb.lineSymbol().clone()
        ls.setColor(QColor(color))
        ls.setWidth(0.25)
        sb.setLineSymbol(ls)

    # Sleek card container so the scalebar never blends into background features
    if background and str(background).lower() != "none":
        sb.setBackgroundEnabled(True)
        sb.setBackgroundColor(QColor(background))
    else:
        sb.setBackgroundEnabled(False)
    if frame_color and str(frame_color).lower() != "none":
        sb.setFrameEnabled(True)
        sb.setFrameStrokeColor(QColor(frame_color))
        sb.setFrameStrokeWidth(QgsLayoutMeasurement(0.2, MM))
    else:
        sb.setFrameEnabled(False)

    lay.addLayoutItem(sb)
    sb.update()
    sb_w = sb.sizeWithUnits().width()
    sb_h = sb.sizeWithUnits().height()

    # Smart positioning & safe bounds calculation relative to linked map
    map_pos = mp.positionWithUnits()
    map_sz = mp.sizeWithUnits()
    map_x = map_pos.x()
    map_y = map_pos.y()
    map_bottom = map_y + map_sz.height()
    map_right = map_x + map_sz.width()

    pad = 5.0
    pos_choice = position or kwargs.get("anchor")
    if pos_choice == "bottom-left" or (x is None and y is None and pos_choice != "bottom-right"):
        target_x = map_x + pad
        target_y = map_bottom - sb_h - pad
    elif pos_choice == "bottom-right":
        target_x = map_right - sb_w - pad
        target_y = map_bottom - sb_h - pad
    elif pos_choice == "top-left":
        target_x = map_x + pad
        target_y = map_y + pad
    elif pos_choice == "top-right":
        target_x = map_right - sb_w - pad
        target_y = map_y + pad
    else:
        target_x = float(x) if x is not None else (map_x + pad)
        target_y = float(y) if y is not None else (map_bottom - sb_h - pad)

    # CRITICAL: Automatically clamp target_y so the scale bar NEVER crosses outside the map frame!
    if map_x <= target_x <= map_right and target_y + sb_h > map_bottom:
        target_y = max(map_y + 2.0, map_bottom - sb_h - pad)
    if map_x <= target_x <= map_right and target_x + sb_w > map_right:
        target_x = max(map_x + pad, map_right - sb_w - pad)

    sb.attemptMove(QgsLayoutPoint(target_x, target_y, MM))
    if item_id:
        sb.setId(str(item_id))
    sb.update()
    return {"item_id": sb.id() or sb.uuid(), "units": label, "style": sb.style(), "x": round(target_x, 1), "y": round(target_y, 1)}


@tool("Add a north arrow linked to a map frame (rotates with the map).",
      {"layout": S("string", ""), "x": S("number", "mm"), "y": S("number", "mm"), "size": S("number", "mm, default 12"),
       "item_id": S("string", ""), "map_id": S("string", "default first map"),
       "style": S("string", "'default' | 'circle' | 'simple' | path/name of an SVG arrow"),
       "color": S("string", "fill colour for parametrised arrows, default '#222222'")})
def add_layout_north_arrow(layout=None, x=0, y=0, size=12, item_id=None, map_id=None, style="default", color="#222222", **kwargs):
    lay = _lay(layout or kwargs.get("layout_name") or kwargs.get("name"))
    presets = {"default": ":/images/north_arrows/layout_default_north_arrow.svg",
               "circle": "arrows/NorthArrow_04.svg", "simple": "arrows/NorthArrow_02.svg"}
    path = presets.get(style, style)
    na = QgsLayoutItemPicture(lay)
    try:
        na.setPicturePath(path if path.startswith(":/") else _svg_path(path))
    except ValueError:
        na.setPicturePath(presets["default"])
    na.setSvgFillColor(QColor(color))
    na.setLinkedMap(_map(lay, map_id))
    _place(lay, na, x, y, size, size * 1.3, item_id)
    return {"item_id": na.id() or na.uuid()}


# ---------------------------------------------------------------- inspect & edit
@tool("List all items in a layout with id, type, position and size (mm) and text excerpt, to plan edits or check overlaps.",
      {"layout": S("string", "")})
def list_layout_items(layout=None, **kwargs):
    target = layout or kwargs.get("layout_name") or kwargs.get("name")
    lay = _lay(target)
    page = lay.pageCollection().page(0).pageSize()
    out = []
    for it in lay.items():
        if not hasattr(it, "positionWithUnits") or not hasattr(it, "uuid"):
            continue
        if isinstance(it, QgsLayoutItemPage):
            continue
        pos, sz = it.positionWithUnits(), it.sizeWithUnits()
        d = {"id": it.id() or "", "uuid": it.uuid(), "type": type(it).__name__.replace("QgsLayoutItem", ""),
             "x": round(pos.x(), 1), "y": round(pos.y(), 1), "w": round(sz.width(), 1), "h": round(sz.height(), 1),
             "z": it.zValue(), "visible": it.isVisible()}
        if isinstance(it, QgsLayoutItemLabel):
            d["text"] = it.text()[:80]
        if isinstance(it, QgsLayoutItemMap):
            d["scale"] = round(it.scale())
        rot = round(float(it.itemRotation()), 2)
        if abs(rot) > 1e-3:
            d["rotation"] = rot
        out.append(d)
    return {"page_mm": [page.width(), page.height()], "items": sorted(out, key=lambda d: (d["y"], d["x"]))}


@tool("Move, resize, retext, restyle, rotate, reorder, hide or delete a layout item by id (or uuid). For maps also change scale/extent.",
      {"layout": S("string", ""), "item_id": S("string", "id or uuid"),
       "x": S("number", "mm"), "y": S("number", "mm"), "width": S("number", "mm"), "height": S("number", "mm"),
       "text": S("string", "new label text"), "size": S("number", "label font pt"), "color": S("string", "label text colour"),
       "background": S("string", "background colour"), "visible": S("boolean", ""), "delete": S("boolean", ""),
       "z": S("number", "stacking order (higher = on top)"), "rotation": S("number", "clockwise rotation degrees"),
       "scale": S("number", "map scale denominator"),
       "extent_layer": S("string", "map: fit to layer"), "bbox": S("array", "map: [xmin,ymin,xmax,ymax] project CRS", items={"type": "number"})})
def update_layout_item(layout=None, item_id=None, x=None, y=None, width=None, height=None, text=None, size=None, color=None,
                       background=None, visible=None, delete=False, z=None, rotation=None, scale=None, extent_layer=None, bbox=None, **kwargs):
    target = layout or kwargs.get("layout_name") or kwargs.get("name")
    lay = _lay(target)
    target_item_id = item_id or kwargs.get("id") or kwargs.get("item")
    if not target_item_id:
        raise ValueError("Missing item_id parameter.")
    it = lay.itemById(str(target_item_id)) or lay.itemByUuid(str(target_item_id))
    if not it:
        raise ValueError(f"No item {target_item_id!r}. Use list_layout_items.")
    if delete:
        lay.removeLayoutItem(it)
        return {"deleted": item_id}
    pos, sz = it.positionWithUnits(), it.sizeWithUnits()
    if x is not None or y is not None:
        it.attemptMove(QgsLayoutPoint(float(x if x is not None else pos.x()), float(y if y is not None else pos.y()), MM))
    if width is not None or height is not None:
        it.attemptResize(QgsLayoutSize(float(width if width is not None else sz.width()),
                                       float(height if height is not None else sz.height()), MM))
    if isinstance(it, QgsLayoutItemLabel):
        if text is not None:
            it.setText(text)
        if size is not None or color is not None:
            fmt = it.textFormat()
            if size is not None:
                fmt.setSize(float(size))
            if color is not None:
                fmt.setColor(QColor(color))
            it.setTextFormat(fmt)
    if isinstance(it, QgsLayoutItemMap):
        if extent_layer or bbox:
            it.zoomToExtent(_extent(extent_layer, bbox))
        if scale:
            it.setScale(float(scale))
    if background is not None:
        it.setBackgroundEnabled(background != "none")
        if background != "none":
            it.setBackgroundColor(QColor(background))
    if visible is not None:
        it.setVisibility(bool(visible))
    if z is not None:
        it.setZValue(float(z))
    if rotation is not None:
        it.setItemRotation(float(rotation), True)
    it.refresh()
    lay.refresh()
    return {"updated": item_id}


@tool("Search the SVG icon library shipped with QGIS (amenity, transport, tourist, health, religion, shopping, food...) for POI symbols "
      "and layout pictograms. Returns names usable in style_categorized categories, add_map_markers and add_layout_picture.",
      {"query": S("string", "keyword, e.g. 'bus', 'shrine', 'toilet', 'parking', 'hospital', 'info'"), "limit": S("integer", "default 30")})
def list_svg_icons(query="", limit=30):
    q = (query or "").lower()
    out = []
    for root in QgsApplication.svgPaths():
        for path in glob.glob(os.path.join(root, "**", "*.svg"), recursive=True):
            rel = os.path.relpath(path, root)
            if q in rel.lower():
                out.append(rel)
            if len(out) >= int(limit):
                return out
    return out


# ---------------------------------------------------------------- map annotations
@tool("Create a point layer of map markers from coordinates, e.g. 'You are here', numbered sightseeing spots, or POIs. "
      "Each marker can carry a label; symbol can be a shape, an SVG icon, or an icon on a coloured badge; labels can have a filled box.",
      {"name": S("string", "layer name, e.g. 'You are here'"),
       "points": S("array", "[{'x':lon,'y':lat,'label':'You are here','category':'...'}]", items={"type": "object"}),
       "crs": S("string", "CRS of the coordinates, default 'EPSG:4326'"),
       "shape": S("string", "circle|square|triangle|star|diamond|pentagon|hexagon|cross, default circle"),
       "svg": S("string", "SVG icon name/path (see list_svg_icons)"), "icon_background": S("string", "badge colour behind the SVG"),
       "color": S("string", "marker colour, default '#c62828'"), "size": S("number", "mm, default 5"),
       "label_size": S("number", "pt, default 9"), "label_color": S("string", "default '#ffffff' with box, else '#222222'"),
       "label_box": S("string", "fill colour of a box behind the label, e.g. '#c62828'"),
       "label_position": S("string", "right|left|above|below|over, default right")})
def add_map_markers(name, points, crs="EPSG:4326", shape="circle", svg=None, icon_background=None, color="#c62828",
                    size=5, label_size=9, label_color=None, label_box=None, label_position="right"):
    p = QgsProject.instance()
    vl = QgsVectorLayer(f"Point?crs={crs}", name, "memory")
    pr = vl.dataProvider()
    pr.addAttributes([QgsField("label", QMetaType.Type.QString), QgsField("category", QMetaType.Type.QString)])
    vl.updateFields()
    feats = []
    for pt in points or []:
        f = QgsFeature(vl.fields())
        f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(float(pt["x"]), float(pt["y"]))))
        f["label"] = str(pt.get("label", ""))
        f["category"] = str(pt.get("category", ""))
        feats.append(f)
    pr.addFeatures(feats)
    vl.updateExtents()
    vl.setRenderer(QgsSingleSymbolRenderer(marker_symbol(color, size, shape, svg, icon_background=icon_background)))
    if any(f["label"] for f in feats):
        s = QgsPalLayerSettings()
        s.fieldName = "label"
        fmt = _fmt(label_size, label_color or ("#ffffff" if label_box else "#222222"), True,
                   buffer_color=None if label_box else "#ffffff", buffer_size=0 if label_box else 0.8)
        if label_box:
            bg = QgsTextBackgroundSettings()
            bg.setEnabled(True)
            bg.setType(QgsTextBackgroundSettings.ShapeType.ShapeRectangle)
            bg.setSizeType(QgsTextBackgroundSettings.SizeType.SizeBuffer)
            bg.setSize(QgsPointXY(1.2, 0.8).toQPointF().toPoint() if False else bg.size())
            from qgis.PyQt.QtCore import QSizeF
            bg.setSize(QSizeF(1.2, 0.8))
            bg.setFillColor(QColor(label_box))
            bg.setStrokeColor(QColor("#ffffff"))
            bg.setStrokeWidth(0.3)
            fmt.setBackground(bg)
        s.setFormat(fmt)
        quad = {"right": Qgis.LabelQuadrantPosition.Right, "left": Qgis.LabelQuadrantPosition.Left,
                "above": Qgis.LabelQuadrantPosition.Above, "below": Qgis.LabelQuadrantPosition.Below,
                "over": Qgis.LabelQuadrantPosition.Over}
        s.placement = Qgis.LabelPlacement.OverPoint
        s.quadOffset = quad.get(label_position, Qgis.LabelQuadrantPosition.Right)
        s.dist = 1.5
        s.xOffset = {"right": size * 0.6, "left": -size * 0.6}.get(label_position, 0)
        s.yOffset = {"above": -size * 0.6, "below": size * 0.6}.get(label_position, 0)
        s.priority = 10
        s.displayAll = True
        vl.setLabeling(QgsVectorLayerSimpleLabeling(s))
        vl.setLabelsEnabled(True)
    p.addMapLayer(vl)
    return {"layer": vl.name(), "id": vl.id(), "features": len(feats)}


# ---------------------------------------------------------------- template: wayfinding / information sign
@tool("One-call template for a wayfinding / tourist information map (station or street sign style): coloured header band with an "
      "'i' badge, large title plus subtitle lines (e.g. translations), left legend panel, main detail map with scale bar and north "
      "arrow, optional 'You are here' marker, right column with a key (wide-area) map showing the detail extent and optional photo "
      "panels with captions, and a footer line. Every element gets an id so it can be refined with update_layout_item.",
      {"name": S("string", "layout name"), "title": S("string", "main title, e.g. 'Welcome to <City>'"),
       "subtitles": S("array", "extra title lines, e.g. translations", items={"type": "string"}),
       "main_extent_layer": S("string", "layer defining the detail map extent"),
       "main_bbox": S("array", "detail extent [xmin,ymin,xmax,ymax]", items={"type": "number"}),
       "key_extent_layer": S("string", "layer defining the key (wide area) map extent"),
       "key_bbox": S("array", "key map extent [xmin,ymin,xmax,ymax]", items={"type": "number"}),
       "bbox_crs": S("string", "CRS of the bboxes, default 'EPSG:4326'"),
       "main_layers": S("array", "lock detail map to these layers", items={"type": "string"}),
       "key_layers": S("array", "lock key map to these layers (e.g. basemap + boundaries)", items={"type": "string"}),
       "legend_layers": S("array", "layers in the legend panel (POIs first)", items={"type": "string"}),
       "you_are_here": S("array", "[x, y] in bbox_crs; adds a red 'You are here' marker", items={"type": "number"}),
       "you_are_here_label": S("string", "default 'You are here'"),
       "photos": S("array", "[{'path':'...jpg','caption':'1 Castle park'}] up to 4", items={"type": "object"}),
       "section_labels": S("object", "override headings: {'map':'Information Map','legend':'Legend','photos':'Sightseeing Spots','key':'Wide Area Map'}"),
       "footer": S("string", "bottom line, e.g. 'You are at 1-2 Main Street'"),
       "credits": S("string", "data sources line"), "theme": S("string", "dark|earth|light|navy, default dark"),
       "page_size": S("string", "default A2"), "orientation": S("string", "landscape|portrait, default landscape")})
def create_wayfinding_layout(name="Wayfinding_Map", title="Information Map", subtitles=None, main_extent_layer=None,
                             main_bbox=None, key_extent_layer=None, key_bbox=None, bbox_crs="EPSG:4326", main_layers=None,
                             key_layers=None, legend_layers=None, you_are_here=None, you_are_here_label="You are here",
                             photos=None, section_labels=None, footer="", credits="", theme="dark", page_size="A2",
                             orientation="landscape"):
    frame, head_txt, panel, panel_txt, accent, sect = THEMES.get(theme, THEMES["dark"])
    labels = {"map": "Information Map", "legend": "Legend", "photos": "Sightseeing Spots", "key": "Wide Area Map"}
    labels.update(section_labels or {})
    created = create_blank_layout(name, page_size, orientation, background=frame)
    W, H = created["page_mm"]
    u = W / 594.0                      # scale every measure to the page (designed on A2 landscape)
    m = 12 * u
    head_h = 70 * u if subtitles else 56 * u
    foot_h = 16 * u
    right_w = (W - 2 * m) * 0.36
    gap = 8 * u
    left_w = W - 2 * m - right_w - gap
    body_y = m + head_h + gap
    body_h = H - body_y - foot_h - m

    # Header: badge + title + subtitles
    badge = head_h * 0.72
    add_layout_shape(name, m, m + (head_h - badge) / 2, badge, badge, "badge", "ellipse", "#1e4e8c", "#ffffff", 1.2 * u)
    add_layout_text(name, m, m + (head_h - badge) / 2, badge, badge, "i", "badge_i", size=badge * 1.6, color="#ffffff",
                    bold=True, font="Georgia", align="center", valign="middle", padding=0)
    tx = m + badge + 10 * u
    title_w = (W - tx - m) * (0.55 if subtitles else 1.0)
    add_layout_text(name, tx, m, title_w, head_h, title, "title", size=44 * u, color=head_txt, bold=True, valign="middle")
    if subtitles:
        sx = tx + title_w + 6 * u
        add_layout_text(name, sx, m, W - sx - m, head_h, "<br>".join(subtitles), "subtitles", size=17 * u,
                        color=head_txt, html=True, valign="middle")

    # Left: section label, legend panel, main map
    lab_h = 9 * u
    add_layout_text(name, m, body_y, left_w, lab_h, labels["map"], "label_map", size=12 * u, color=sect, valign="bottom", padding=0)
    map_y = body_y + lab_h + 2 * u
    map_h = body_h - lab_h - 2 * u
    leg_w = left_w * 0.2
    add_layout_shape(name, m, map_y, leg_w, map_h, "legend_panel", fill=panel, stroke="none")
    main_x = m + leg_w
    main_w = left_w - leg_w
    add_layout_map(name, main_x, map_y, main_w, map_h, "main_map", extent_layer=main_extent_layer, bbox=main_bbox,
                   bbox_crs=bbox_crs if main_bbox else None, margin=0.02, layers=main_layers, frame_color=panel,
                   frame_width=0.6 * u, background="#f2efe6")

    # Right: photos (optional) then key map
    rx = m + left_w + gap
    ph = [p for p in (photos or []) if p.get("path")][:4]
    key_top = body_y
    if ph:
        add_layout_text(name, rx, body_y, right_w, lab_h, labels["photos"], "label_photos", size=12 * u, color=sect,
                        valign="bottom", padding=0)
        cols = 2 if len(ph) > 1 else 1
        rows = (len(ph) + cols - 1) // cols
        cell_w = (right_w - (cols - 1) * 5 * u) / cols
        cap_h = 9 * u
        cell_h = min(cell_w * 0.62, (body_h * 0.55 - lab_h) / rows - cap_h - 4 * u)
        for i, item in enumerate(ph):
            cx = rx + (i % cols) * (cell_w + 5 * u)
            cy = body_y + lab_h + 2 * u + (i // cols) * (cell_h + cap_h + 4 * u)
            add_layout_shape(name, cx, cy, cell_w, cell_h + cap_h, f"photo_bg_{i + 1}", fill=panel, stroke="none")
            add_layout_picture(name, cx + 1.5 * u, cy + 1.5 * u, cell_w - 3 * u, cell_h - 1.5 * u, item["path"],
                               f"photo_{i + 1}", fit="zoom")
            add_layout_text(name, cx + 1.5 * u, cy + cell_h, cell_w - 3 * u, cap_h, item.get("caption", ""),
                            f"photo_caption_{i + 1}", size=8.5 * u, color=panel_txt, bold=True, padding=0.5)
        key_top = body_y + lab_h + 2 * u + rows * (cell_h + cap_h + 4 * u) + 2 * u
    add_layout_text(name, rx, key_top, right_w, lab_h, labels["key"], "label_key", size=12 * u, color=sect,
                    valign="bottom", padding=0)
    ky = key_top + lab_h + 2 * u
    kh = body_y + body_h - ky
    add_layout_map(name, rx, ky, right_w, kh, "key_map", extent_layer=key_extent_layer, bbox=key_bbox,
                   bbox_crs=bbox_crs if key_bbox else None, margin=0.02, layers=key_layers, overview_of="main_map",
                   overview_color=accent, frame_color=panel, frame_width=0.6 * u, background="#f2efe6")

    # Marker after maps so it is part of both frames unless layer sets are locked
    if you_are_here:
        res = add_map_markers(you_are_here_label, [{"x": you_are_here[0], "y": you_are_here[1], "label": you_are_here_label}],
                              crs=bbox_crs, shape="triangle", color=accent, size=6 * u, label_size=11 * u,
                              label_box=accent, label_position="right")
        for mid in ("main_map", "key_map"):
            mp = _map(_lay(name), mid)
            if mp.keepLayerSet():
                mp.setLayers([QgsProject.instance().mapLayer(res["id"])] + mp.layers())

    # Legend inside the left panel, scale bar + north arrow on the main map
    add_layout_text(name, m + 3 * u, map_y + 3 * u, leg_w - 6 * u, 10 * u, labels["legend"], "legend_title",
                    size=13 * u, color=panel_txt, bold=True, valign="middle", padding=0)
    add_layout_shape(name, m + 3 * u, map_y + 13 * u, leg_w - 6 * u, 0.4 * u, "legend_rule", fill=panel_txt)
    add_layout_legend(name, m + 2 * u, map_y + 15 * u, leg_w - 4 * u, map_h - 18 * u, "legend", "main_map", title="",
                      layers=legend_layers, item_size=9 * u, layer_title_size=9.5 * u, hide_layer_titles=False,
                      symbol_width=7 * u, symbol_height=5 * u, text_color=panel_txt, background="none", frame_color="none")
    add_layout_north_arrow(name, main_x + 5 * u, map_y + 5 * u, 11 * u, "north_arrow", "main_map", "default", "#1e293b")
    add_layout_scalebar(name, main_x + 21 * u, map_y + 6 * u, "scalebar", "main_map", "Double Box", segments=4,
                        height=2.0 * u, font_size=9 * u, color="#1e293b", background="#ffffff", frame_color="#cbd5e1")

    # Footer
    if footer:
        add_layout_text(name, m, H - m - foot_h, W * 0.6, foot_h, footer, "footer", size=12 * u, color=sect, valign="middle", padding=0)
    if credits:
        add_layout_text(name, W * 0.6, H - m - foot_h, W * 0.4 - m, foot_h, credits, "credits", size=7.5 * u, color=sect,
                        align="right", valign="middle", padding=0)
    _lay(name).refresh()
    return {"layout": name, "page_mm": [W, H], "theme": theme,
            "items": [d["id"] for d in list_layout_items(name)["items"] if d["id"]],
            "next": "export_layout(preview=true), then refine with update_layout_item / add_layout_* as needed."}


# ---------------------------------------------------------------- tourist booklet / graphic generators & reference grid
_QR_CAPACITIES_L = (17, 32, 53, 78, 106, 134, 154)
_QR_TOTAL_CODEWORDS = (26, 44, 70, 100, 134, 172, 196)
_QR_EC_PER_BLOCK = (
    (7, 1),    # V1: 1 block, 7 ec
    (10, 1),   # V2: 1 block, 10 ec
    (15, 1),   # V3: 1 block, 15 ec
    (20, 1),   # V4: 1 block, 20 ec
    (26, 1),   # V5: 1 block, 26 ec
    (18, 2),   # V6: 2 blocks, 18 ec each
    (20, 2),   # V7: 2 blocks, 20 ec each
)
_QR_ALIGN_POS = ((), (), (6, 18), (6, 22), (6, 26), (6, 30), (6, 34), (6, 22, 38))


def _gf_mul(x, y):
    r = 0
    while y:
        if y & 1:
            r ^= x
        y >>= 1
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    return r


def _rs_encode(data, nsym):
    gen = [1]
    exp = 1
    for _ in range(nsym):
        nxt = [0] * (len(gen) + 1)
        for j, g in enumerate(gen):
            nxt[j] ^= g
            nxt[j + 1] ^= _gf_mul(g, exp)
        gen = nxt
        exp = _gf_mul(exp, 2)
    rem = list(data) + [0] * nsym
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _gf_mul(gen[j], coef)
    return rem[len(data):]


def _qr_matrix(payload):
    """Pure-Python ISO/IEC 18004 Byte-mode EC-L QR encoder (Versions 1..7, up to ~150 UTF-8 bytes)."""
    raw = str(payload or "https://qgis.org").encode("utf-8")[:150]
    ver = next((v + 1 for v, cap in enumerate(_QR_CAPACITIES_L) if len(raw) <= cap), 7)
    size = 17 + 4 * ver
    total_cw = _QR_TOTAL_CODEWORDS[ver - 1]
    ec_len, num_blocks = _QR_EC_PER_BLOCK[ver - 1]
    data_cw_total = total_cw - ec_len * num_blocks

    # Bit stream: mode 0100 (Byte), 8-bit char count, payload bytes, terminator, byte pad, 0xEC/0x11 pad
    bits = [0, 1, 0, 0]
    for b in range(7, -1, -1):
        bits.append((len(raw) >> b) & 1)
    for byte in raw:
        for b in range(7, -1, -1):
            bits.append((byte >> b) & 1)
    max_bits = data_cw_total * 8
    for _ in range(min(4, max_bits - len(bits))):
        bits.append(0)
    while len(bits) % 8:
        bits.append(0)
    pad_bytes = (0xEC, 0x11)
    p_idx = 0
    while len(bits) < max_bits:
        val = pad_bytes[p_idx % 2]
        p_idx += 1
        for b in range(7, -1, -1):
            bits.append((val >> b) & 1)
    data_cw = [sum(bits[i + k] << (7 - k) for k in range(8)) for i in range(0, len(bits), 8)]

    # Split blocks & compute RS EC + interleave
    short_len = data_cw_total // num_blocks
    long_blocks = data_cw_total % num_blocks
    data_blocks, ec_blocks = [], []
    off = 0
    for blk in range(num_blocks):
        blen = short_len + (1 if blk >= num_blocks - long_blocks else 0)
        d_blk = data_cw[off:off + blen]
        off += blen
        data_blocks.append(d_blk)
        ec_blocks.append(_rs_encode(d_blk, ec_len))
    interleaved = []
    for i in range(max(len(b) for b in data_blocks)):
        for b in data_blocks:
            if i < len(b):
                interleaved.append(b[i])
    for i in range(ec_len):
        for b in ec_blocks:
            interleaved.append(b[i])
    code_bits = [(cw >> b) & 1 for cw in interleaved for b in range(7, -1, -1)]

    # Matrix setup (-1 = unset, 0 = light, 1 = dark)
    grid = [[-1] * size for _ in range(size)]
    func = [[False] * size for _ in range(size)]

    def _set_func(r, c, v):
        if 0 <= r < size and 0 <= c < size:
            grid[r][c] = 1 if v else 0
            func[r][c] = True

    def _finder(r0, c0):
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                if dr in (-1, 7) or dc in (-1, 7):
                    _set_func(r, c, 0)
                elif dr in (0, 6) or dc in (0, 6) or (2 <= dr <= 4 and 2 <= dc <= 4):
                    _set_func(r, c, 1)
                else:
                    _set_func(r, c, 0)

    _finder(0, 0)
    _finder(0, size - 7)
    _finder(size - 7, 0)

    ap = _QR_ALIGN_POS[ver]
    for ar in ap:
        for ac in ap:
            if func[ar][ac]:
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    _set_func(ar + dr, ac + dc, 1 if (abs(dr) == 2 or abs(dc) == 2 or (dr == 0 and dc == 0)) else 0)

    for i in range(8, size - 8):
        if not func[6][i]:
            _set_func(6, i, 1 if i % 2 == 0 else 0)
        if not func[i][6]:
            _set_func(i, 6, 1 if i % 2 == 0 else 0)
    _set_func(size - 8, 8, 1)

    # Reserve format info cells
    fmt_coords_1 = [(8, i) for i in range(9) if i != 6] + [(i, 8) for i in range(7, -1, -1) if i != 6]
    fmt_coords_2 = [(size - 1 - i, 8) for i in range(7)] + [(8, size - 8 + i) for i in range(8)]
    for r, c in fmt_coords_1 + fmt_coords_2:
        func[r][c] = True
        if grid[r][c] < 0:
            grid[r][c] = 0

    # Place data bits in zigzag
    bit_i = 0
    col = size - 1
    up = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for r in rows:
            for dc in (0, -1):
                c = col + dc
                if not func[r][c]:
                    b = code_bits[bit_i] if bit_i < len(code_bits) else 0
                    bit_i += 1
                    grid[r][c] = b
        col -= 2
        up = not up

    # Mask 0: (r + c) % 2 == 0, Format info for EC-L (01) + Mask 0 (000) -> 111011111000100
    fmt_bits = "111011111000100"
    for r in range(size):
        for c in range(size):
            if not func[r][c] and (r + c) % 2 == 0:
                grid[r][c] ^= 1
    for idx, (r, c) in enumerate(fmt_coords_1):
        grid[r][c] = int(fmt_bits[idx])
    for idx, (r, c) in enumerate(fmt_coords_2):
        grid[r][c] = int(fmt_bits[idx])
    return grid


def _build_layout_graphic_svg(kind, text="", subtitle="", color="#d97373", accent_color="#ffffff", items=None, columns=2):
    """Build crisp vector SVG content for QR codes, ribbon banners, tourist icons/pins, tabs, and directory cards."""
    k = str(kind or "tourist_icon").strip().lower()
    fg = color or "#1e293b"
    bg = accent_color or "#ffffff"

    if k == "qr_code":
        mat = _qr_matrix(text or "https://qgis.org")
        n = len(mat)
        pad = 2
        vb = n + pad * 2
        rects = [
            f'<rect x="{c + pad}" y="{r + pad}" width="1.02" height="1.02" fill="{fg}"/>'
            for r in range(n) for c in range(n) if mat[r][c]
        ]
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vb} {vb}">'
            f'<rect width="{vb}" height="{vb}" fill="{bg}" rx="1"/>'
            + "".join(rects)
            + "</svg>"
        )

    if k == "ribbon_banner":
        t_esc = _html.escape(str(text or "GUIDE MAP"))
        sub_esc = _html.escape(str(subtitle or ""))
        sub_svg = f'<text x="150" y="69" text-anchor="middle" font-family="sans-serif" font-size="11" font-weight="bold" fill="{bg}">{sub_esc}</text>' if sub_esc else ""
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 84">'
            f'<path d="M12,30 Q150,8 288,30 L276,46 L292,62 Q150,40 8,62 L24,46 Z" fill="{fg}" opacity="0.88"/>'
            f'<path d="M36,24 Q150,4 264,24 L264,54 Q150,34 36,54 Z" fill="{fg}" stroke="{bg}" stroke-width="1.5"/>'
            f'<text x="150" y="38" text-anchor="middle" font-family="Georgia, serif" font-size="21" font-weight="bold" fill="{bg}">{t_esc}</text>'
            + sub_svg
            + "</svg>"
        )

    if k == "stepped_tab":
        t_esc = _html.escape(str(text or "SECTION"))
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 240">'
            f'<rect width="40" height="240" fill="{fg}"/>'
            f'<path d="M0,0 L40,20 L40,36 L0,16 Z M0,48 L40,68 L40,84 L0,64 Z M0,96 L40,116 L40,132 L0,112 Z '
            f'M0,144 L40,164 L40,180 L0,160 Z M0,192 L40,212 L40,228 L0,208 Z" fill="{bg}" opacity="0.14"/>'
            f'<text x="20" y="120" text-anchor="middle" dominant-baseline="central" transform="rotate(90 20 120)" '
            f'font-family="sans-serif" font-size="15" font-weight="bold" letter-spacing="1.5" fill="{bg}">{t_esc}</text>'
            f'</svg>'
        )

    if k == "directory_card":
        rows_data = list(items or ())
        cols = max(1, min(4, int(columns or 2)))
        w = cols * 185 + 16
        per_col = max(1, math.ceil(max(1, len(rows_data)) / cols))
        row_h = 34
        h = 34 + per_col * row_h + 10
        t_esc = _html.escape(str(text or "DIRECTORY"))
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}">',
            f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="6" fill="#fcfaf5" stroke="{fg}" stroke-width="1.8"/>',
            f'<rect x="1" y="1" width="{w - 2}" height="26" rx="6" fill="{fg}"/>',
            f'<text x="12" y="18" font-family="sans-serif" font-size="13" font-weight="bold" fill="{bg}">{t_esc}</text>',
        ]
        for idx, entry in enumerate(rows_data):
            if isinstance(entry, str):
                entry = {"num": idx + 1, "name": entry}
            ci = idx // per_col
            ri = idx % per_col
            cx = 10 + ci * 185
            cy = 34 + ri * row_h
            num_s = _html.escape(str(entry.get("num", idx + 1)))
            nm_s = _html.escape(str(entry.get("name", ""))[:24])
            gr_s = _html.escape(str(entry.get("grid", "")))
            mt_s = _html.escape(str(entry.get("meta") or entry.get("phone") or "")[:32])
            parts.append(f'<rect x="{cx}" y="{cy}" width="177" height="{row_h - 4}" rx="3" fill="#ffffff" stroke="#e2e8f0" stroke-width="0.8"/>')
            parts.append(f'<circle cx="{cx + 12}" cy="{cy + 11}" r="7.5" fill="{fg}"/>')
            parts.append(f'<text x="{cx + 12}" y="{cy + 14.5}" text-anchor="middle" font-family="sans-serif" font-size="8.5" font-weight="bold" fill="{bg}">{num_s}</text>')
            parts.append(f'<text x="{cx + 24}" y="{cy + 13}" font-family="sans-serif" font-size="8.8" font-weight="bold" fill="#1e293b">{nm_s}</text>')
            if gr_s:
                parts.append(f'<rect x="{cx + 146}" y="{cy + 4}" width="26" height="11" rx="2.5" fill="#f1f5f9" stroke="{fg}" stroke-width="0.6"/>')
                parts.append(f'<text x="{cx + 159}" y="{cy + 12}" text-anchor="middle" font-family="sans-serif" font-size="7" font-weight="bold" fill="{fg}">{gr_s}</text>')
            if mt_s:
                parts.append(f'<text x="{cx + 24}" y="{cy + 24.5}" font-family="sans-serif" font-size="7.2" fill="#64748b">{mt_s}</text>')
        parts.append("</svg>")
        return "".join(parts)

    # Default: tourist_icon / numbered pin
    icon_name = str(subtitle or "map_pin").strip().lower()
    lbl = _html.escape(str(text or ""))
    glyphs = {
        "onsen": '<path d="M14,22 Q12,16 16,12 Q18,9 15,6 M22,22 Q20,15 24,11 Q26,8 23,5 M30,22 Q28,16 32,12 Q34,9 31,6" fill="none" stroke="WHITE" stroke-width="2.4" stroke-linecap="round"/><ellipse cx="22" cy="30" rx="12" ry="5" fill="none" stroke="WHITE" stroke-width="2.6"/>',
        "hiking": '<path d="M8,34 L19,13 L26,25 L31,18 L38,34 Z" fill="WHITE" opacity="0.92"/><circle cx="33" cy="11" r="3" fill="WHITE"/>',
        "camera": '<rect x="9" y="15" width="26" height="18" rx="3" fill="none" stroke="WHITE" stroke-width="2.6"/><path d="M16,15 L19,11 L25,11 L28,15" fill="WHITE"/><circle cx="22" cy="24" r="5" fill="none" stroke="WHITE" stroke-width="2.4"/>',
        "dining": '<path d="M15,9 L15,35 M12,9 L12,18 Q15,21 18,18 L18,9 M28,9 C32,9 32,21 28,21 L28,35" fill="none" stroke="WHITE" stroke-width="2.4" stroke-linecap="round"/>',
        "hotel": '<path d="M8,33 L8,14 M8,27 L36,27 L36,33 M11,22 L21,22 L21,27 L11,27 Z M22,20 L35,20 Q36,20 36,27" fill="none" stroke="WHITE" stroke-width="2.4" stroke-linecap="round"/>',
        "train": '<rect x="13" y="9" width="18" height="22" rx="4" fill="none" stroke="WHITE" stroke-width="2.4"/><rect x="16" y="13" width="12" height="7" rx="1" fill="WHITE"/><circle cx="17" cy="26" r="1.6" fill="WHITE"/><circle cx="27" cy="26" r="1.6" fill="WHITE"/><path d="M16,31 L12,37 M28,31 L32,37" stroke="WHITE" stroke-width="2.4" stroke-linecap="round"/>',
        "flower": '<circle cx="22" cy="13" r="5" fill="WHITE"/><circle cx="30" cy="20" r="5" fill="WHITE"/><circle cx="26" cy="29" r="5" fill="WHITE"/><circle cx="18" cy="29" r="5" fill="WHITE"/><circle cx="14" cy="20" r="5" fill="WHITE"/><circle cx="22" cy="22" r="4" fill="{fg}"/>',
        "shrine": '<path d="M10,13 Q22,10 34,13 M12,18 L32,18 M16,13 L16,35 M28,13 L28,35 M22,13 L22,18" fill="none" stroke="WHITE" stroke-width="2.6" stroke-linecap="round"/>',
        "map_pin": '<path d="M22,4 C13,4 7,10.5 7,18.5 C7,28.5 22,41 22,41 C22,41 37,28.5 37,18.5 C37,10.5 31,4 22,4 Z" fill="{fg}" stroke="WHITE" stroke-width="2"/>',
    }
    if icon_name == "map_pin" or lbl.isdigit():
        pin_txt = lbl or "1"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 44 46">'
            f'<path d="M22,3 C13,3 7,9.5 7,17.5 C7,27.5 22,43 22,43 C22,43 37,27.5 37,17.5 C37,9.5 31,3 22,3 Z" fill="{fg}" stroke="{bg}" stroke-width="2.2"/>'
            f'<circle cx="22" cy="17.5" r="9.5" fill="{bg}"/>'
            f'<text x="22" y="21" text-anchor="middle" font-family="sans-serif" font-size="11.5" font-weight="bold" fill="{fg}">{pin_txt}</text>'
            f'</svg>'
        )
    inner = glyphs.get(icon_name, glyphs["camera"]).replace("WHITE", bg).replace("{fg}", fg)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 44 44">'
        f'<circle cx="22" cy="22" r="20" fill="{fg}" stroke="{bg}" stroke-width="2"/>'
        f'{inner}'
        f'</svg>'
    )


@tool("Generate vector SVG graphics (QR code, curved ribbon banner, numbered map pins, tourist icons, stepped side-tabs, or multi-column directory cards) "
      "into the project scratch/ folder and optionally place onto a print layout.",
      {"kind": S("string", "'qr_code' | 'ribbon_banner' | 'tourist_icon' | 'stepped_tab' | 'directory_card'"),
       "text": S("string", "URL for qr_code, title for banner/tab/card, or pin number"),
       "subtitle": S("string", "subtitle for banner, or icon type: onsen|hiking|camera|dining|hotel|train|flower|shrine|map_pin"),
       "color": S("string", "primary hex colour, default '#d97373'"),
       "accent_color": S("string", "secondary/text colour, default '#ffffff'"),
       "items": S("array", "directory_card entries: [{num, name, grid, meta, phone}]"),
       "columns": S("integer", "directory_card columns (1..4, default 2)"),
       "layout": S("string", "optional layout name to immediately place the generated graphic"),
       "x": S("number", "mm (if layout is given)"), "y": S("number", "mm"),
       "width": S("number", "mm"), "height": S("number", "mm"),
       "item_id": S("string", "optional layout item id"),
       "rotation": S("number", "clockwise rotation degrees (0, 90, 180, 270)")})
def generate_layout_graphic(kind="tourist_icon", text="", subtitle="", color="#d97373", accent_color="#ffffff",
                            items=None, columns=2, layout=None, x=0, y=0, width=20, height=20, item_id=None, rotation=0):
    svg = _build_layout_graphic_svg(kind, text=text, subtitle=subtitle, color=color, accent_color=accent_color, items=items, columns=columns)
    svg_path = _project_scratch_file(f"gfx_{kind}", ".svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    res = {"path": svg_path, "kind": kind}
    if layout:
        placed = add_layout_picture(layout=layout, x=x, y=y, width=width, height=height, path=svg_path, item_id=item_id, fit="zoom", rotation=rotation)
        res["item_id"] = placed["item_id"]
    return res


@tool("Add an alpha-numeric tourist reference grid (columns A, B, C... and rows 1, 2, 3...) over a layout map frame and optionally stamp 'map_grid' (e.g. 'G-3') onto POI layers.",
      {"layout": S("string", "layout name"),
       "map_id": S("string", "map item id (default first map)"),
       "cols": S("integer", "number of lettered columns (default 8 -> A..H)"),
       "rows": S("integer", "number of numbered rows (default 6 -> 1..6)"),
       "poi_layers": S("array", "optional POI layer names/ids to populate 'map_grid' field", items={"type": "string"}),
       "grid_color": S("string", "hairline grid colour, default '#64748b'"),
       "header_fill": S("string", "strip background, default '#f1f5f9'"),
       "header_color": S("string", "strip text colour, default '#1e293b'"),
       "strip_size": S("number", "mm width/height of index border strips, default 4.2")})
def add_map_reference_grid(layout=None, map_id=None, cols=8, rows=6, poi_layers=None,
                           grid_color="#64748b", header_fill="#f1f5f9", header_color="#1e293b", strip_size=4.2):
    lay = _lay(layout)
    mp = _map(lay, map_id)
    ext = mp.extent()
    ew, eh = float(ext.width()), float(ext.height())
    if ew <= 0 or eh <= 0:
        raise ValueError("Map item has empty extent; set map extent before adding reference grid.")

    ncols = max(2, min(26, int(cols or 8)))
    nrows = max(2, min(20, int(rows or 6)))
    pos, sz = mp.positionWithUnits(), mp.sizeWithUnits()
    mx, my, mw, mh = float(pos.x()), float(pos.y()), float(sz.width()), float(sz.height())
    s = float(strip_size or 4.2)

    # Configure QGIS map grid lines synced to exact extent intervals
    grid = QgsLayoutItemMapGrid(f"RefGrid_{mp.id() or 'main'}", mp)
    grid.setEnabled(True)
    grid.setStyle(QgsLayoutItemMapGrid.GridStyle.Solid)
    grid.setIntervalX(ew / ncols)
    grid.setIntervalY(eh / nrows)
    grid.setOffsetX(ext.xMinimum())
    grid.setOffsetY(ext.yMinimum())
    grid.setLineSymbol(QgsLineSymbol.createSimple({"color": grid_color, "width": "0.18", "line_style": "solid"}))
    grid.setAnnotationEnabled(False)
    mp.grids().addGrid(grid)
    mp.refresh()

    col_labels = [chr(65 + i) for i in range(ncols)]
    cw = mw / ncols
    rh = mh / nrows
    top_y = max(0.0, my - s)
    left_x = max(0.0, mx - s)
    base_tag = mp.id() or "main"

    for ci, ch in enumerate(col_labels):
        add_layout_text(lay.name(), mx + ci * cw, top_y, cw, s, ch, item_id=f"{base_tag}_grid_col_{ch}",
                        size=7.5, color=header_color, bold=True, align="center", valign="middle",
                        background=header_fill, frame_color="#cbd5e1", padding=0.2)
    for ri in range(nrows):
        rnum = str(ri + 1)
        add_layout_text(lay.name(), left_x, my + ri * rh, s, rh, rnum, item_id=f"{base_tag}_grid_row_{rnum}",
                        size=7.5, color=header_color, bold=True, align="center", valign="middle",
                        background=header_fill, frame_color="#cbd5e1", padding=0.2)

    stamped = {}
    if poi_layers:
        p = QgsProject.instance()
        map_crs = mp.crs() or p.crs()
        for lref in poi_layers:
            lyr = _layer(lref)
            if not lyr or not lyr.isValid():
                continue
            tr = QgsCoordinateTransform(lyr.crs(), map_crs, p) if lyr.crs() != map_crs else None
            idx = lyr.fields().indexFromName("map_grid")
            if idx < 0:
                lyr.startEditing()
                lyr.dataProvider().addAttributes([QgsField("map_grid", QMetaType.Type.QString)])
                lyr.updateFields()
                idx = lyr.fields().indexFromName("map_grid")
            if not lyr.isEditable():
                lyr.startEditing()
            count = 0
            for ft in lyr.getFeatures():
                geom = ft.geometry()
                if geom.isNull() or geom.isEmpty():
                    continue
                pt = geom.centroid().asPoint()
                if tr:
                    try:
                        pt = tr.transform(pt)
                    except Exception as exc:
                        LOGGER.debug(f"Skipping grid stamp transform on feature {ft.id()}: {exc}")
                        pt = None
                if pt is None or not ext.contains(pt):
                    continue
                ci = min(ncols - 1, max(0, int((pt.x() - ext.xMinimum()) / (ew / ncols))))
                ri = min(nrows - 1, max(0, int((ext.yMaximum() - pt.y()) / (eh / nrows))))
                code = f"{col_labels[ci]}-{ri + 1}"
                lyr.changeAttributeValue(ft.id(), idx, code)
                count += 1
            lyr.commitChanges()
            stamped[lyr.name()] = count

    lay.refresh()
    return {"layout": lay.name(), "cols": col_labels, "rows": list(range(1, nrows + 1)), "stamped_layers": stamped}


