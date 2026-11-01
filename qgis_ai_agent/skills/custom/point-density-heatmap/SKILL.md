---
name: point-density-heatmap
description: Comprehensive guide for point pattern density mapping, clustering, and hotspot analysis. Covers Kernel Density Estimation (KDE) parameterization, hexagonal binning (hexbins), DBSCAN spatial clustering, geocoding artifact detection, and transparent heat ramp cartography.
keywords: heatmap, kernel density, kde, hexbin, hexagon grid, clustering, dbscan, point density, hotspot, incident density, spatial pattern, spatial aggregation
license: MIT
---

# Point Density, Heatmap & Spatial Clustering Guide

Point datasets representing discrete events (traffic collisions, emergency incidents, retail customers, crime reports, disease occurrences) frequently suffer from visual overcrowding ("hairball effect") when displayed as raw point symbols.

Density mapping and spatial binning aggregate discrete points into continuous surfaces or uniform cells to reveal spatial clusters, intensity hotspots, and spatial distribution patterns.

---

## 1. Selecting the Right Density Representation

| Representation | Processing Tools | Primary Strengths | Limitations |
| :--- | :--- | :--- | :--- |
| **Kernel Density (KDE Heatmap)** | `qgis:heatmapkerneldensityestimation` | Produces smooth, continuous intensity surfaces. Visually intuitive for public and executive dashboards. | Continuous surface suggests presence in unpopulated areas; value is descriptive intensity, not discrete count. |
| **Hexagonal Binning (Hexbins)** | `native:creategrid` + `native:countpointsinpolygon` | True quantitative counts per unit area. Regular geometry minimizes directional bias and boundary distortion (MAUP). | Bins have fixed geographic borders; cell size choice influences visual perception. |
| **Algorithmic Clustering (DBSCAN)** | `native:dbscanclustering` | Statistically groups points into distinct spatial clusters while labeling isolated points as noise. | Does not produce a continuous visual surface; outputs cluster ID attributes. |
| **Administrative Zonal Density** | `native:countpointsinpolygon` + `calculate_field` | Normalizes points per administrative unit area or population (see `choropleth-density-map`). | Sensitive to arbitrary administrative unit shapes and sizes. |

---

## 2. Kernel Density Estimation (KDE) Deep Dive

### Mathematical Parameters
The KDE algorithm places a 3D curved surface (kernel) over each point and sums values across overlapping surfaces:
1. **Search Radius / Bandwidth ($h$):**
   - The single most critical parameter.
   - **Too small:** Produces disjointed, fragmented "islands" around individual points.
   - **Too large:** Over-smooths the surface, obscuring local neighborhood clusters into a vague blob.
   - **Rule of Thumb:** For urban municipal studies (e.g., 10–20 km extent), use a search radius between **500 m and 1,500 m**. For regional/provincial studies (50–150 km extent), use **5,000 m to 10,000 m**.
2. **Pixel Resolution ($\Delta x$):**
   - Set pixel size to approximately **$\frac{1}{10}$ to $\frac{1}{20}$ of the search radius** (e.g. for a 1,000 m radius, use 50 m to 100 m pixels). Setting pixel size too small dramatically slows rendering without gaining useful information.
3. **Weight Field (Optional):**
   - Weight incidents by severity, magnitude, or monetary cost (e.g. 3-alarm fire = 3, minor incident = 1).

### Execution Recipe in QGIS
```python
# 1. Ensure points are in metric UTM CRS
run_processing("native:reprojectlayer", {
    "INPUT": "incident_points",
    "TARGET_CRS": "EPSG:32653"
}, output_name="points_metric")

# 2. Run Kernel Density Estimation
run_processing("qgis:heatmapkerneldensityestimation", {
    "INPUT": "points_metric",
    "RADIUS": 1200.0,       # 1.2 km search radius
    "PIXEL_SIZE": 50.0,      # 50 m raster cell resolution
    "WEIGHT_FIELD": "",      # Optional numeric attribute
    "KERNEL": 0              # 0 = Quartic (biweight)
}, output_name="kde_heatmap_raster")

# 3. Apply Heat Color Ramp
style_raster(
    layer="kde_heatmap_raster",
    ramp="YlOrRd",
    classes=6
)

# 4. Set opacity so underlying streets and geography remain visible
update_layer(layer="kde_heatmap_raster", opacity=0.68)
```

---

## 3. Hexagonal Binning (Hexbin) Workflow

Hexagonal grids offer the lowest perimeter-to-area ratio of any regular tessellation, providing uniform distance between adjacent cell centroids:

```python
# Step 1: Generate regular hexagonal grid over the study extent
run_processing("native:creategrid", {
    "TYPE": 2,              # 2 = Hexagon (1 = Rectangle)
    "EXTENT": "points_metric",
    "HSPACING": 500.0,      # Horizontal cell width in meters
    "VSPACING": 500.0,      # Vertical cell width in meters
    "CRS": "EPSG:32653"
}, output_name="hex_grid_500m")

# Step 2: Count points falling inside each hexagon
run_processing("native:countpointsinpolygon", {
    "POLYGONS": "hex_grid_500m",
    "POINTS": "points_metric",
    "FIELD_NAME": "point_count"
}, output_name="hex_density_counts")

# Step 3: Filter out empty zero-count hexagons to keep map clean
run_processing("native:extractbyexpression", {
    "INPUT": "hex_density_counts",
    "EXPRESSION": "\"point_count\" > 0"
}, output_name="active_hex_hotspots")

# Step 4: Style with graduated choropleth
style_graduated(
    layer="active_hex_hotspots",
    field="point_count",
    classes=5,
    method="jenks",
    ramp="YlOrRd",
    outline_color="#ffffff"
)
```

---

## 4. Algorithmic Cluster Extraction (DBSCAN)

Density-Based Spatial Clustering of Applications with Noise (DBSCAN) groups tightly clustered points without forcing noisy outlier points into false clusters:

```python
run_processing("native:dbscanclustering", {
    "INPUT": "points_metric",
    "MIN_SIZE": 10,          # Minimum 10 points to form a cluster
    "EPS": 800.0,            # Maximum distance between points in meters
    "DBSCAN*": False
}, output_name="points_dbscan_clusters")

# Style clustered points: CLUSTER_ID = NULL indicates background noise
style_categorized(
    layer="points_dbscan_clusters",
    field="CLUSTER_ID"
)
```

---

## 5. Detecting & Mitigating Common Spatial Pitfalls

1. **Centroid Stacking / Geocoding Artifacts:**
   - *Problem:* When addresses lack street-level numbers, geocoders often place hundreds of points at the exact same town-hall or village-center coordinate.
   - *Diagnosis:* Run `field_stats` or check for duplicate coordinates. A massive artificial hotspot at the town center is almost always a geocoding artifact.
   - *Mitigation:* Apply jitter or disclose that points represent aggregated zip-code centroids.
2. **Comparing Incompatible Heatmaps:**
   - Never compare two heatmaps generated with different search radii or different pixel sizes; their absolute density surfaces are mathematically non-comparable.
3. **Correlation with Population:**
   - In incident and crime mapping, raw point density almost always mirrors population density (more people = more incidents). When comparing risk between regions, normalize per capita using `choropleth-density-map`.
