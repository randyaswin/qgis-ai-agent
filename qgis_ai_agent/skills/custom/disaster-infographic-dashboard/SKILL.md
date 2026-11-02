---
name: disaster-infographic-dashboard
description: Comprehensive guide for constructing multi-hazard disaster situational reports (SITREPs) and emergency management dashboards. Covers incident point classification, terrain relief overlays, Kernel Density Estimation (KDE) heatmaps, 10km buffer hazard clusters, urban detail insets, analytical ranking charts, continuous gradient bars, and cluster callout cards.
keywords: disaster, hazard, emergency, incident, sitrep, situational map, flood, landslide, wildfire, earthquake, storm, callout, cluster, inset, bar chart, heatmap, relief, composite map, dashboard, asap, kabut asap, kebakaran, terdampak, smoke, haze, fire, air quality, pollution, affected population
license: MIT
---

# Situational Disaster & Emergency Management Dashboard Guide

Situational disaster maps and emergency dashboards (SITREPs) communicate critical spatial intelligence to decision-makers, relief agencies, and the public under time pressure. They combine relief terrain, multi-category incident symbology, spatial density hot-spots, high-risk cluster annotations, and summary analytics into a single coherent visual document.

---

## 1. Data Layers & Spatial Preparation

### A. Coordinate Reference System (CRS)
All buffers, heatmaps, and spatial counts must be calculated in a local metric projected CRS:
- Check existing CRS with `get_project_state()`.
- Reproject layers to the local UTM zone (`coordinate-systems-and-crs` skill) using `native:reprojectlayer`.

### B. Terrain Relief Context (DEM + Hillshade)
Natural hazards (floods, landslides, storm surges) are strongly governed by topography:
1. **Derive Hillshade:**
   ```python
   run_processing("native:hillshade", {
       "INPUT": "dem_metric",
       "Z_FACTOR": 1.5,
       "AZIMUTH": 315.0,  # Standard northwest illumination
       "V_ANGLE": 45.0
   }, output_name="dem_hillshade")
   ```
2. **Hypsometric Tinting & Blending:**
   - Style underlying DEM with `style_raster(layer="dem_metric", ramp="Spectral", invert_ramp=True, classes=8)` or warm topographic tints (`YlOrBr`).
   - Move hillshade immediately above DEM in layer stack (`update_layer position=...`).
   - Set hillshade opacity to 45–50% (`update_layer(layer="dem_hillshade", opacity=0.48)`).

### C. Multi-Category Incident Symbology (UN-OCHA / FEMA Standards)
Classify incident points by hazard type (`style_categorized`) using standardized shapes and high-contrast emergency cartography colors:

| Hazard Category | Recommended Symbol | Hex Color | Symbology Details |
| :--- | :--- | :--- | :--- |
| **Inundation / Flash Flood** | Circle | `#1976d2` (Deep Blue) | White 0.3mm outline |
| **Landslide / Mudflow / Rockfall** | Triangle | `#d32f2f` (Crimson Red)| Warning point up |
| **Wildfire / Forest Fire** | Diamond | `#f57c00` (Vivid Orange)| Bold contrast |
| **Urban / Industrial Fire** | Square | `#c2185b` (Deep Pink) | Saturated warning |
| **Extreme Storm / Wind / Cyclone**| Star | `#7b1fa2` (Purple) | Multi-point star |
| **Earthquake / Seismic Shake** | Hexagon | `#5d4037` (Dark Brown) | Earth tone |
| **Drought / Water Scarcity** | Circle | `#fbc02d` (Golden Yellow)| Distinct from flood |

### D. Incident Density Hot-Spots (Kernel Density Estimation)
To visualize concentration of events beyond discrete points:
```python
run_processing("qgis:heatmapkerneldensityestimation", {
    "INPUT": "incidents_layer",
    "RADIUS": 8000,        # 8 km search radius
    "PIXEL_SIZE": 100,     # 100 m raster cell resolution
    "KERNEL": 0            # 0 = Quartic
}, output_name="incident_kde_density")

# Apply sequential heat ramp with transparent low-density base
style_raster(
    layer="incident_kde_density",
    ramp="YlOrRd",
    classes=6
)
# Lower layer opacity so base roads and topography remain visible
update_layer(layer="incident_kde_density", opacity=0.65)
```

### E. High-Risk Cluster Buffer Zones (e.g. 10 km Radius)
```python
run_processing("native:buffer", {
    "INPUT": "cluster_centroids",
    "DISTANCE": 10000.0,   # 10,000 meters = 10 km
    "DISSOLVE": False
}, output_name="hazard_cluster_10km")

# Style with clean outline only (no solid fill)
style_single(
    layer="hazard_cluster_10km",
    color="#00000000",       # Transparent fill
    outline_color="#b71c1c", # Deep red boundary
    width=0.6
)
```

---

## 2. Dashboard Layout Assembly Sequence

Use the composite layouting engine (`create_styled_layout` or layout primitives):

### Step 1: Initialize Dashboard Canvas
```python
create_styled_layout(
    name="Disaster_Situation_Dashboard",
    style_theme="infographic_disaster",
    title="PROVINCIAL DISASTER SITUATION REPORT",
    subtitle="Emergency Management Operations Center · Verified Incidents",
    credits="Data: Regional Disaster Management Center & Public Open Geodata · Coordinate System: WGS 84 / UTM",
    extent_layer="Administrative_Boundary",
    page_size="A3",
    orientation="landscape",
    grid=True
)
```

### Step 2: Urban / Critical Inset Detail Map
Add a high-resolution inset focused on the hardest-hit municipality or capital city:
```python
add_layout_inset_map(
    layout="Disaster_Situation_Dashboard",
    x=12, y=36, width=82, height=90,
    extent_layer="Capital_District",
    title="DETAIL CAPITAL METROPOLITAN AREA",
    show_overview_on_main=True,
    grid=True
)
```

### Step 3: High-Priority Cluster Callout Badges (A, B, C...)
Anchor callout cards with badges to highlight primary disaster impact zones:
```python
# Cluster A Callout Card
add_layout_callout_box(
    layout="Disaster_Situation_Dashboard",
    x=110, y=70, width=54, height=18,
    badge="A",
    title="NORTH BASIN CLUSTER",
    subtitle="42 incidents · Inundation alert",
    pointer_x=135, pointer_y=95  # Connects pointer line to map coordinates
)

# Cluster B Callout Card
add_layout_callout_box(
    layout="Disaster_Situation_Dashboard",
    x=215, y=140, width=54, height=18,
    badge="B",
    title="MOUNTAIN PASS SECTOR",
    subtitle="18 landslides · Roads severed",
    pointer_x=205, pointer_y=165
)
```

### Step 4: Continuous Gradient Legend Bars
Continuous environmental layers (elevation, flood hazard index, heat density) need clean horizontal color strips:
```python
# Elevation Gradient Bar
add_layout_gradient_legend(
    layout="Disaster_Situation_Dashboard",
    x=12, y=242, width=65, height=18,
    title="Elevation (m a.s.l.)",
    ramp="Spectral",
    min_val="0 m",
    max_val="2,850 m",
    invert_ramp=True
)

# Incident Density Gradient Bar
add_layout_gradient_legend(
    layout="Disaster_Situation_Dashboard",
    x=82, y=242, width=65, height=18,
    title="Incident Concentration",
    ramp="YlOrRd",
    min_val="Low Density",
    max_val="Extreme Cluster"
)
```

### Step 5: Summary Analytical Ranking Bar Chart
Quantify incident distribution across administrative divisions using an integrated HTML bar chart card:
```python
add_layout_infographic_chart(
    layout="Disaster_Situation_Dashboard",
    x=324, y=36, width=84, height=140,
    title="INCIDENTS BY DISTRICT",
    subtitle="Official verified reports",
    data=[
        {"label": "District North", "value": 58},
        {"label": "District East", "value": 44},
        {"label": "District Central", "value": 37},
        {"label": "District West", "value": 26},
        {"label": "District South", "value": 19}
    ],
    bar_color="#1565c0"
)
```

---

## 3. Pre-Flight Verification & Delivery

1. **Inline Chat Inspection:** Call `export_layout(name="Disaster_Situation_Dashboard", path="preview.png", preview=True)` to inspect the rendered composite visually.
2. **Review Checks:**
   - Do callout lines point accurately to the corresponding incident cluster?
   - Is text readable against the underlying topography?
   - Are colorblind-sensitive red and green combinations mitigated by distinct geometric shapes (triangles for landslides, circles for floods)?
3. **High-Resolution Production Export:** Call `export_layout(name="Disaster_Situation_Dashboard", path="output_dir/Disaster_Situation_Report.pdf", dpi=300)`.
