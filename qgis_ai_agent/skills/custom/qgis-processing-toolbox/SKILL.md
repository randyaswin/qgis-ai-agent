---
name: qgis-processing-toolbox
description: Authoritative guide to the QGIS Processing Framework (440+ algorithms across native C++, GDAL, GRASS, PDAL, and visual models). Covers discovering algorithms, inspecting parameter schemas with describe_algorithm, automated headless execution via run_processing, parameter type conversions, chaining multistep pipelines, and launching interactive GUI dialogs.
keywords: processing, toolbox, geoprocessing, native, gdal, pdal, grass, buffer, clip, intersection, dissolve, contour, hillshade, slope, zonal statistics, raster calculator, shortest path, heatmap, modeler, model designer, spatial pipeline
license: MIT
---

# QGIS Processing Toolbox & Automation Master Guide

The QGIS Processing Framework is the core analytical computational engine of QGIS. It unifies hundreds of algorithms across native C++ algorithms, GDAL/OGR raster and vector utilities, GRASS GIS, PDAL point cloud tools, and custom graphical models (`.model3`).

---

## 1. Algorithm Discovery & Inspection Protocol

> [!CRITICAL]
> **Never guess parameter names.** Different algorithms use different parameter conventions (e.g. `INPUT` vs `INPUT_RASTER`, `DISTANCE` vs `RADIUS`, integer enum codes vs strings). Always follow this discovery sequence:

1. **Find Providers:** `list_processing_providers()` (e.g. `native`, `gdal`, `qgis`, `grass`, `pdal`, `model`).
2. **Search Algorithms by Keyword:**
   ```python
   list_algorithms(query="contour")
   # Returns: [{'id': 'gdal:contour', 'name': 'Contour', 'provider': 'gdal'}, ...]
   ```
3. **Inspect Exact Schema (`describe_algorithm`):**
   ```python
   describe_algorithm("native:buffer")
   ```
   Examine:
   - `parameters`: Name, type (`QgsProcessingParameterDistance`, `QgsProcessingParameterEnum`, etc.), default values, and optional vs mandatory flags.
   - `outputs`: Output parameter key (`OUTPUT`, `OUTPUT_HTML_FILE`, etc.).

---

## 2. Key Execution Rules (`run_processing`)

### A. Layer Referencing
- The algorithm accepts a **layer name** (e.g. `"Roads_Network"`) or a **layer ID** (e.g. `"Roads_Network_3a2b4c..."`).
- If passing a newly created layer to a subsequent processing step, you can directly use the `output_name` assigned in the previous step.

### B. Output Destination (`OUTPUT`)
- By default, `run_processing` routes `OUTPUT` to `"TEMPORARY_OUTPUT"`. This creates an in-memory scratch layer loaded instantly into QGIS without writing unneeded files to disk.
- To save directly to disk, provide an absolute path: e.g. `OUTPUT: "/path/to/project_dir/output.gpkg|layername=result"`.

### C. Layer Stacking & Labeling
- Always provide `output_name="Descriptive_Layer_Name"` so the resulting layer appears clearly labeled in the user's QGIS Layers dock panel.

---

## 3. Essential Algorithm Reference Matrix

### Vector Geometry & Geoprocessing
| Algorithm ID | Purpose | Essential Parameters |
| :--- | :--- | :--- |
| `native:buffer` | Fixed distance buffer | `INPUT`, `DISTANCE` (meters in projected CRS), `SEGMENTS` (default 5, use 8-16 for smooth circles), `DISSOLVE` (bool) |
| `native:multiringconstantbuffer`| Concentric distance rings | `INPUT`, `RINGS` (int), `DISTANCE` (float step size) |
| `native:clip` | Cut vector by polygon mask | `INPUT`, `OVERLAY` |
| `native:difference` | Geometric subtraction (gaps) | `INPUT`, `OVERLAY` |
| `native:intersection` | Overlap & combine attributes | `INPUT`, `OVERLAY`, `INPUT_FIELDS`, `OVERLAY_FIELDS` |
| `native:dissolve` | Merge features by attribute | `INPUT`, `FIELD` (list of attribute names) |
| `native:fixgeometries` | Repair topological self-intersections | `INPUT`, `METHOD` (1 = Structure repair) |
| `native:multiparttosingleparts`| Split multipart into singleparts | `INPUT` |
| `native:reprojectlayer` | Transform vector CRS | `INPUT`, `TARGET_CRS` (e.g. `'EPSG:32653'`) |

### Spatial Joins & Statistics
| Algorithm ID | Purpose | Essential Parameters |
| :--- | :--- | :--- |
| `native:joinattributestable` | 1:1 or 1:m attribute table join | `INPUT`, `FIELD`, `INPUT_2`, `FIELD_2`, `METHOD` (1) |
| `native:countpointsinpolygon` | Aggregate points per polygon | `POLYGONS`, `POINTS`, `FIELD_NAME` |
| `native:joinbynearest` | Nearest facility distance | `INPUT`, `INPUT_2`, `NEIGHBORS` (1), `MAX_DISTANCE` |
| `qgis:statisticsbycategories` | Grouped attribute summary | `INPUT`, `VALUES_FIELD_NAME`, `CATEGORIES_FIELD_NAME` |

### Raster & Terrain Processing
| Algorithm ID | Purpose | Essential Parameters |
| :--- | :--- | :--- |
| `gdal:contour` | Elevation contour lines | `INPUT`, `BAND` (1), `INTERVAL` (e.g. 25.0), `FIELD_NAME` ('ELEV') |
| `native:hillshade` | 3D topographic shaded relief | `INPUT`, `Z_FACTOR` (1.0–2.0), `AZIMUTH` (315.0), `V_ANGLE` (45.0) |
| `native:slope` | Terrain slope calculation | `INPUT`, `Z_FACTOR` (1.0 in metric CRS) |
| `native:aspect` | Compass slope direction | `INPUT`, `Z_FACTOR` (1.0) |
| `gdal:warpreproject` | Raster reprojection & resampling | `INPUT`, `TARGET_CRS`, `RESAMPLING` (0=Nearest, 1=Bilinear, 2=Cubic) |
| `gdal:cliprasterbymasklayer` | Clip raster by polygon mask | `INPUT`, `MASK`, `TARGET_CRS`, `NODATA` |
| `native:rastercalc` | Arbitrary map algebra | `LAYERS`, `EXPRESSION` (e.g. `"elev@1" * 0.5 + ...`) |
| `native:reclassifybytable` | Reclass raster by value breaks | `INPUT_RASTER`, `RASTER_BAND`, `TABLE` (list of [min, max, value]) |
| `native:zonalstatisticsfb` | Zonal raster sum/mean/stddev | `INPUT`, `INPUT_RASTER`, `RASTER_BAND`, `COLUMN_PREFIX`, `STATISTICS` |

### Spatial Density & Interpolation
| Algorithm ID | Purpose | Essential Parameters |
| :--- | :--- | :--- |
| `qgis:heatmapkerneldensityestimation` | Continuous point heatmaps | `INPUT`, `RADIUS` (meters), `PIXEL_SIZE` (meters), `KERNEL` (0=Quartic) |
| `qgis:idwinterpolation` | Inverse Distance Weighting | `INTERPOLATION_DATA`, `DISTANCE_COEFFICIENT` (2.0), `EXTENT`, `PIXEL_SIZE` |
| `native:creategrid` | Regular Hexagonal or Square Grid | `TYPE` (2=Hexagon, 1=Rectangle), `EXTENT`, `HSPACING`, `VSPACING`, `CRS` |
| `native:dbscanclustering` | Density-based point clustering | `INPUT`, `MIN_SIZE`, `EPS` (search distance in meters) |

---

## 4. Multi-Step Pipeline Chaining Example

A typical analytical pipeline chains outputs from one algorithm into the input of the next:

```python
# Step 1: Repair invalid boundary polygons
step1 = run_processing("native:fixgeometries", {
    "INPUT": "raw_admin_districts"
}, output_name="clean_districts")

# Step 2: Reproject to metric UTM
step2 = run_processing("native:reprojectlayer", {
    "INPUT": "clean_districts",
    "TARGET_CRS": "EPSG:32653"
}, output_name="districts_metric")

# Step 3: Compute Zonal Statistics from Population Raster
step3 = run_processing("native:zonalstatisticsfb", {
    "INPUT": "districts_metric",
    "INPUT_RASTER": "worldpop_2026_metric",
    "RASTER_BAND": 1,
    "COLUMN_PREFIX": "pop_",
    "STATISTICS": [1]  # 1 = Sum
}, output_name="districts_with_population")

# Step 4: Calculate Population Density per Square Kilometer
calculate_field(
    layer="districts_with_population",
    field="pop_per_km2",
    expression="\"pop_sum\" / (area($geometry) / 1000000.0)"
)
```

---

## 5. Graphical User Interface (GUI) Triggers

When the user asks to visually see tools, test parameters interactively, or edit processing models:
1. `open_processing_toolbox()`: Reveals the Processing Toolbox dock panel in QGIS.
2. `open_processing_algorithm_dialog(algorithm, params)`: Opens the dedicated graphical dialog for an algorithm with pre-populated parameters ready for the user to review.
3. `open_model_designer()`: Opens the QGIS Graphical Model Designer canvas.
