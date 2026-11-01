---
name: disaster-infographic-dashboard
description: Use when building comprehensive disaster, hazard, or incident infographic maps (like BNPB / BPBD / BMKG situational dashboards). Covers combining relief DEM, multi-category incident symbols, KDE heatmap density, 10km cluster buffer zones, capital city inset map, callout cards A-B-C-D-E, horizontal bar chart ranking, and continuous gradient legend bars.
keywords: disaster, hazard, incident, infographic, dashboard, bnpb, bpbd, situational map, flood, landslide, fire, callout, cluster, inset, bar chart, heatmap, relief, composite map, bencana, infografis, kejadian
license: MIT
---

# Situational Disaster & Hazard Infographic Dashboard

Comprehensive guide for creating professional disaster and hazard situational infographic maps (such as provincial/national disaster situational reports):

---

## 1. Data Preparation & Geometry

1. **Coordinate Reference System (CRS) & Basemap:**
   - Always use a local metric projected CRS (e.g. UTM Zone). Call `set_project_crs` before calculating buffers or KDE heatmaps.
   - Use 100% free, keyless XYZ tile basemaps: call `add_basemap(preset="carto_light")` (CartoDB Positron with soft gray tones so thematic layers stand out) or `add_basemap(preset="esri_satellite")` for aerial imagery. Adjust opacity if needed (`update_layer(opacity=0.4)`).
2. **Terrain Relief (DEM):**
   - Derive hillshade from DEM (`native:hillshade`, Azimuth 315°, V_Angle 45°).
   - Apply smooth elevation pseudocolor symbology (`style_raster` with `Spectral` or inverted `YlOrBr` ramp), overlaying hillshade on top with 45–50% opacity (`update_layer opacity=0.5`).
3. **Incident Points (Categorized Symbology):**
   - Apply `style_categorized` based on incident type column using standard emergency cartography shapes and colors:
     - **Flood**: Blue circle (`#1976d2`)
     - **Landslide**: Brown/red triangle (`#d32f2f`)
     - **Wildfire / Forest Fire**: Orange diamond (`#f57c00`)
     - **Building / Urban Fire**: Crimson square (`#c2185b`)
     - **Extreme Weather / Storm**: Purple star (`#7b1fa2`)
     - **Drought**: Golden yellow circle (`#fbc02d`)
4. **Heatmap Density (KDE):**
   - Run `qgis:heatmapkerneldensityestimation` with an appropriate search radius (e.g., 10,000 meters / 10 km) and 100m pixel size.
   - Style with `style_raster` using `YlOrRd` (yellow to dark red) ramp and lower-end transparency so base features remain visible.
5. **Cluster Ring Buffers (10 km):**
   - Aggregate points or select primary cluster centroids, then generate 10 km buffers (`native:buffer` distance 10,000 m).
   - Apply thin outline symbology with no fill (`style_single` with 0.8 mm outline width and 0.7 opacity).

---

## 2. Assembling Composite Infographic Layouts

Use the modern composite layout tools step-by-step:

1. **Initialize Layout Template:**
   - Call `create_styled_layout(name="Disaster_Map", style_theme="infographic_disaster", title="PROVINCIAL DISASTER SITUATION REPORT", subtitle="OCTOBER 2026", page_size="A3", orientation="landscape", grid=True)`.
   - Automatically sets up a neatline frame with graticules, north arrow, scale bar, and reserved space for insets and analytics cards.
2. **Detail Urban / Regional Inset Map:**
   - Call `add_layout_inset_map(layout="Disaster_Map", x=12, y=60, width=80, height=85, extent_layer="Capital_City", title="DETAIL CAPITAL CITY", show_overview_on_main=True, grid=True)`.
   - The inset map maintains its own scale and automatically renders an overview rectangle on the main map canvas.
3. **Cluster Callout Cards (A, B, C, D, E):**
   - Call `add_layout_callout_box` for significant hazard clusters:
     - `badge="A"`, `title="NORTH CLUSTER"`, `subtitle="18 incidents · 10 km radius"`.
     - `badge="B"`, `title="EAST CLUSTER"`, `subtitle="18 incidents · 10 km radius"`.
     - `badge="C"`, `title="METROPOLITAN CLUSTER"`, `subtitle="15 incidents · 10 km radius"`.
4. **Horizontal Gradient Legend Bars:**
   - Call `add_layout_gradient_legend` for continuous variables along the bottom dock:
     - Elevation: `title="Elevation (m)"`, `ramp="Spectral"`, `min_val="-3"`, `max_val="2,945"`, `invert_ramp=True`.
     - Flood Hazard: `title="Flood hazard index"`, `ramp="Blues"`, `min_val="0 Low"`, `max_val="1 High"`.
     - Incident Density: `title="Incident density"`, `ramp="YlOrRd"`, `min_val="Low"`, `max_val="High"`.
5. **Infographic Bar Chart:**
   - Aggregate incidents per administrative division.
   - Call `add_layout_infographic_chart(layout="Disaster_Map", x=325, y=200, width=85, height=85, title="INCIDENTS BY DISTRICT", subtitle="October 2026 · total 143 incidents", data=[{"label":"District A","value":36}, {"label":"District B","value":27}, ...], bar_color="#2b5c8f")`.

---

## 3. Verification & Export

1. Call `export_layout(name="Disaster_Map", path=".../disaster_map_preview.png", preview=True)` to inspect the rendered map directly in the agent chat.
2. Check typography hierarchy, label readability, and color contrast.
3. Export publication-quality print deliverable: `export_layout(name="Disaster_Map", path=".../disaster_map.pdf", dpi=300)`.
