---
name: digitizing-and-georeferencing
description: Comprehensive operational guide and script toolkit for raster georeferencing and vector digitization in QGIS. Covers Ground Control Point (GCP) collection, transformation models (Helmert, Affine, Polynomial 2/3, Thin Plate Spline), RMSE residual analysis, world files (.tfw/.pgw), snapping environment configuration, topological editing (avoid overlap, gap detection), programmatic feature creation, automated raster vectorization (binarization, polygonization, centerline tracing), and geometry repair (native:fixgeometries). Includes ready-to-run Python scripts.
keywords: digitizing, georeferencing, gcp, ground control points, affine, polynomial, thin plate spline, tps, helmert, world file, raster warping, vector digitizing, snapping, topological editing, auto trace, polygonize, vectorization, fix geometries, make valid, qgis digitization
license: MIT
---

# Vector Digitization & Raster Georeferencing Guide

Transforming analog maps, drone photography, architectural site plans, or sketches into topologically rigorous spatial vectors requires two essential capabilities:
1. **Georeferencing:** Warping raw raster pixel coordinates $(X_p, Y_p)$ into real-world geographic coordinates $(X_g, Y_g)$ using Ground Control Points (GCPs).
2. **Digitization:** Creating clean vector geometry (points, lines, polygons) with topological integrity, accurate snapping, attribute schemas, and automated vectorization workflows.

---

## 1. Raster Georeferencing Fundamentals

### 1.1 The Mathematical Transformation Models

When aligning a non-spatial raster (e.g., scanned historical map, JPEG drone survey, cadastral scan) to a geographic coordinate reference system:

| Transformation Method | Min GCPs | Mathematical Nature | Distortion Modeled | Best Application |
| :--- | :--- | :--- | :--- | :--- |
| **Linear / Helmert** | 2 | Conformal (similarity) | Uniform scaling, rotation, translation | Preserves original shape; drone imagery with minimal distortion. |
| **Affine (Polynomial 1)** | 3 | Affine | Independent X/Y scale, shear, rotation, translation | Standard for flat scans and orthogonal aerial imagery. Parallel lines remain parallel. |
| **Polynomial 2** | 6 | 2nd-degree curve | Parabolic curvature, barrel/pincushion lens distortion | Scanned paper maps with uneven paper stretch or shrinkage. |
| **Polynomial 3** | 10 | 3rd-degree curve | Complex non-linear distortion | Uneven photographic plates or extreme lens curvature. (Avoid near image edges). |
| **Thin Plate Spline (TPS)** | 4 | Local rubber-sheeting | Exact fit at GCPs with smooth minimal-energy deformation | Historical maps, severely warped paper, local non-linear stretching. |
| **Projective** | 4 | Homography | Perspective tilt and oblique view angles | Oblique drone or handheld building facade photographs. |

### 1.2 Ground Control Point (GCP) Selection Strategy
- **Corner Anchors:** Always place GCPs close to the four corners of the image to anchor the transformation bounding box.
- **Uniform Grid Distribution:** Distribute interior GCPs evenly across the scene. Avoid clustering points in one quadrant.
- **Non-Collinear Rule:** Never place all GCPs along a single linear road or river, as collinear points cause mathematical matrix singularity.
- **Identifiable Landmarks:** Use sharp, unambiguous intersections:
  - Road center junctions, bridge abutments, canal corners, historical monument bases.
  - Avoid seasonal features like tree tops, shoreline water edges, or movable parking lines.

### 1.3 Residual Error & Root Mean Square Error (RMSE)
For each GCP, the residual error represents the distance between its true map position and its transformed position:
$$\text{Residual}_i = \sqrt{(X_{\text{true}} - X_{\text{calc}})^2 + (Y_{\text{true}} - Y_{\text{calc}})^2}$$
$$\text{RMSE} = \sqrt{\frac{1}{N} \sum_{i=1}^N \text{Residual}_i^2}$$
- **Quality Target:** For high-precision mapping, target an $\text{RMSE} < 1.0$ pixel. If RMSE is high, sort GCPs by residual descending and remove or recalibrate blunder points.

---

## 2. Scripted Georeferencing Workflows

### 2.1 Direct Tool Execution via `georeference_raster`
The agent provides a dedicated tool for warping images with GCPs:

```python
# Georeference an image directly via tool call
georeference_raster(
    image_path="/path/to/scanned_map.jpg",
    output_path="/path/to/output_georeferenced.tif",
    gcps=[
        [150, 220, 138.6105, 35.2210],   # [pixel_x, pixel_y, map_x, map_y]
        [3850, 240, 138.6520, 35.2205],
        [160, 4200, 138.6110, 35.1850],
        [3840, 4180, 138.6515, 35.1845]
    ],
    crs="EPSG:4326",
    transform="polynomial1",   # 'polynomial1' (Affine) | 'polynomial2' | 'tps'
    resample="bilinear",
    load_to_canvas=True
)
```

### 2.2 Georeferencing with Python and GDAL Bindings
Use the standalone script in `scripts/georeference_raster.py` or execute programmatically:

```python
from osgeo import gdal, osr

# 1. Define GCPs: gdal.GCP(map_x, map_y, elevation, pixel_x, pixel_y)
gcp_list = [
    gdal.GCP(138.6105, 35.2210, 0, 150, 220),
    gdal.GCP(138.6520, 35.2205, 0, 3850, 240),
    gdal.GCP(138.6110, 35.1850, 0, 160, 4200),
    gdal.GCP(138.6515, 35.1845, 0, 3840, 4180)
]

# 2. Attach Spatial Reference
srs = osr.SpatialReference()
srs.SetFromUserInput("EPSG:4326")

# 3. Create VRT with GCPs
vrt_ds = gdal.Translate("/vsimem/gcp_temp.vrt", "raw_image.jpg", GCPs=gcp_list, outputSRS=srs)

# 4. Warp to final GeoTIFF
warp_options = gdal.WarpOptions(
    format="GTiff",
    dstSRS="EPSG:4326",
    resampleAlg=gdal.GRA_Bilinear,
    polynomialOrder=1
)
gdal.Warp("georeferenced_output.tif", vrt_ds, options=warp_options)
```

### 2.3 World Files (.tfw / .pgw / .wld)
If an image is already orthogonal, unrotated, and aligned to grid coordinates, an affine **World File** defines its georeferencing without altering the pixel bytes.
A 6-line world file contains:
- Line 1: Pixel dimension in X direction (ground resolution, e.g. `0.5`)
- Line 2: Rotation term Y (usually `0.0`)
- Line 3: Rotation term X (usually `0.0`)
- Line 4: Negative pixel dimension in Y direction (e.g. `-0.5`)
- Line 5: X coordinate of the center of the upper-left pixel
- Line 6: Y coordinate of the center of the upper-left pixel

---

## 3. Vector Digitization Best Practices

### 3.1 Establishing Snapping & Topological Rules
Manual tracing without snapping produces microscopic gaps, overlapping slivers, and unjoined intersections that break network routing and zonal calculations.

1. **Snapping Tolerance:**
   - In metric CRS (e.g. UTM): Set tolerance to **1.0–2.5 meters** for urban features, **5.0–10.0 meters** for regional features.
   - Snapping mode: Enable **Vertex and Segment** snapping.
2. **Topological Editing Mode:**
   - Enable *"Avoid Overlap on Active Layer"* when digitizing land parcels, administrative boundaries, or zoning zones. When drawing a new polygon over a shared boundary, QGIS automatically clips the overlapping portion to maintain seamless adjacency.
3. **Trace Digitizing:**
   - Enable the **Tracing Tool** to automatically follow existing road centerlines or river vectors when digitizing adjacent administrative boundaries.

---

## 4. Programmatic Vector Layer Creation & Feature Ingestion

### 4.1 Creating Vector Layers via `create_vector_layer`
```python
# Scaffold a new GeoPackage vector layer
create_vector_layer(
    layer_name="infrastructure_parcels",
    geometry_type="Polygon",
    crs="EPSG:32654",
    fields=[
        {"name": "parcel_id", "type": "string"},
        {"name": "owner", "type": "string"},
        {"name": "zoning", "type": "string"},
        {"name": "area_sqm", "type": "double"}
    ],
    output_path="/path/to/project_data.gpkg",
    load_to_canvas=True
)
```

### 4.2 Digitizing Features Programmatically via `digitize_features`
```python
# Add features with coordinates and attributes
digitize_features(
    layer="infrastructure_parcels",
    features=[
        {
            "geometry": [
                [138.612, 35.215],
                [138.618, 35.215],
                [138.618, 35.210],
                [138.612, 35.210],
                [138.612, 35.215]
            ],
            "attributes": {
                "parcel_id": "P-101",
                "owner": "Public Works",
                "zoning": "Utility",
                "area_sqm": 25400.0
            }
        }
    ],
    auto_fix_geometry=True
)
```

---

## 5. Automated Raster-to-Vector Digitization (Vectorization)

When digitizing high-contrast scanned plans, flood masks, building footprint masks, or land cover classifications:

### The Vectorization Pipeline
```
[Raw Scanned Map / Mask] 
       │
       ▼  (Raster Thresholding & Binarization)
[Binary Single-Band Raster (0/1)] 
       │
       ▼  (gdal:polygonize)
[Raw Vector Polygons] 
       │
       ▼  (native:fixgeometries)
[Valid Vector Geometries] 
       │
       ▼  (native:simplifygeometries / Chaikin Smoothing)
[Clean Digitized Boundaries]
```

### Execution Example:
```python
# Step 1: Polygonize classified raster
run_processing("gdal:polygonize", {
    "INPUT": "classified_mask.tif",
    "BAND": 1,
    "FIELD": "class_val",
    "OUTPUT": "memory:raw_polygons"
})

# Step 2: Repair self-intersections and topological invalidities
run_processing("native:fixgeometries", {
    "INPUT": "raw_polygons",
    "OUTPUT": "memory:valid_polygons"
})

# Step 3: Remove pixel staircase stepping with subtle Douglas-Peucker simplification
run_processing("native:simplifygeometries", {
    "INPUT": "valid_polygons",
    "METHOD": 0,
    "TOLERANCE": 1.5,   # meters
    "OUTPUT": "output_digitized_vectors.gpkg"
})
```

---

## 6. Geometry Validation & Quality Checklist

Before finalizing any digitized dataset:
1. **Geometry Validity:** Run `native:fixgeometries` to resolve bowtie self-intersections, duplicate consecutive vertices, and unclosed polygon rings.
2. **Sliver Elimination:** Filter or dissolve polygons where $\frac{\text{Area}}{\text{Perimeter}^2} < 0.01$ into neighboring polygons.
3. **Multi-to-Single Part:** Use `native:multiparttosingleparts` if individual parcel/feature units must possess distinct attribute records.
4. **Vertex Snap:** Snap endpoint vertices of digitized road or river networks with `native:snapgeometries` to guarantee continuous network topology.
