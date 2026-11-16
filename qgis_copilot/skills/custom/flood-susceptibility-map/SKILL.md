---
name: flood-susceptibility-map
description: Complete methodology for constructing flood and landslide susceptibility maps using Multi-Criteria Decision Analysis (MCDA) and weighted raster overlay. Covers factor derivation (slope, HAND, river distance buffers, land use permeability, rainfall), common grid alignment, standardization to 1–5 scoring scales, raster calculator weighted overlay, validation against historical incident points, and area quantification.
keywords: flood, susceptibility, flood hazard, hazard map, inundation, weighted overlay, mcda, ahp, hand, slope, river buffer, permeability, risk assessment, disaster mitigation
license: MIT
---

# Flood Susceptibility Mapping via Multi-Criteria Weighted Overlay

> [!NOTE]
> A spatial susceptibility map is a **screening-level relative hazard index** based on physical predispositions (topography, drainage proximity, soil permeability, rainfall). It is **not** a calibrated 1D/2D hydrodynamic fluid simulation (e.g., HEC-RAS or TUFLOW). Always disclose this scope clearly in reports and layout metadata.

---

## 1. Key Evaluation Factors & Reclassification Matrix

Every factor is reclassified into a standardized **1 to 5 susceptibility rating**:
- **1:** Very Low Susceptibility
- **2:** Low Susceptibility
- **3:** Moderate Susceptibility
- **4:** High Susceptibility
- **5:** Very High Susceptibility

### Factor Breakdown & Scoring Tiers
| Factor | Processing Tool | Derivation Method | High Susceptibility (Score 4–5) | Low Susceptibility (Score 1–2) |
| :--- | :--- | :--- | :--- | :--- |
| **Slope Gradient** | `native:slope` | Derived from projected metric DEM | Flat terrain (< 2° or 0–3%), water ponds naturally | Steep slopes (> 15°–25°), rapid gravity runoff |
| **Elevation / HAND** | `native:rastercalc` | Height Above Nearest Drainage or raw DEM | Low elevations, river floodplains, coastal lowlands | Ridge lines, hills, plateaus |
| **River Distance** | `native:multiringconstantbuffer` | Buffered stream network rasterized to grid | Within 0–100 m (Score 5), 100–300 m (Score 4) | > 1,000–2,000 m away from riverbank |
| **Land Cover Permeability** | `native:rasterize` | Categorized land use polygons | Impervious asphalt/concrete (Urban), water bodies | Dense forest, permeable natural soils, wetlands |
| **Rainfall Intensity** | `qgis:idwinterpolation` | Interpolated meteorological station precipitation | Extreme precipitation zones (> 2,500 mm/yr) | Arid or low precipitation zones |

---

## 2. Step-by-Step Processing Pipeline

### Step 1: Metric Projection & Common Grid Alignment
All rasters must share the exact same projected CRS, cell resolution (e.g., 25m or 30m), and spatial bounding extent:
```python
# Match grid extent and cell size across all factor rasters
run_processing("gdal:warpreproject", {
    "INPUT": "raw_factor_raster",
    "TARGET_CRS": "EPSG:32653",
    "TARGET_RESOLUTION": 30.0,
    "RESAMPLING": 1  # Bilinear for continuous data
}, output_name="factor_aligned_30m")
```

### Step 2: Factor Standardization (`native:reclassifybytable`)
Reclassify continuous parameters into discrete 1–5 integer scores:
```python
# Reclassify Slope: [Min, Max, Score]
# 0-2 deg -> 5, 2-5 deg -> 4, 5-10 deg -> 3, 10-20 deg -> 2, >20 deg -> 1
slope_table = [
    0.0, 2.0, 5,
    2.0, 5.0, 4,
    5.0, 10.0, 3,
    10.0, 20.0, 2,
    20.0, 90.0, 1
]
run_processing("native:reclassifybytable", {
    "INPUT_RASTER": "slope_aligned",
    "RASTER_BAND": 1,
    "TABLE": slope_table,
    "NO_DATA": -9999,
    "RANGE_BOUNDARIES": 0  # min < value <= max
}, output_name="score_slope")
```

### Step 3: Weighted Overlay via Raster Calculator
Combine standardized factor rasters with assigned percentage weights (weights sum to $1.00$ / $100\%$):
- **Slope:** $25\%$ ($0.25$)
- **Elevation / HAND:** $25\%$ ($0.25$)
- **Distance to Drainage:** $20\%$ ($0.20$)
- **Land Cover Permeability:** $15\%$ ($0.15$)
- **Rainfall:** $15\%$ ($0.15$)

```python
# Execute weighted raster calculation
calc_expression = (
    "(\"score_slope@1\" * 0.25) + "
    "(\"score_elevation@1\" * 0.25) + "
    "(\"score_river_dist@1\" * 0.20) + "
    "(\"score_landcover@1\" * 0.15) + "
    "(\"score_rainfall@1\" * 0.15)"
)
run_processing("native:rastercalc", {
    "LAYERS": ["score_slope", "score_elevation", "score_river_dist", "score_landcover", "score_rainfall"],
    "EXPRESSION": calc_expression
}, output_name="raw_susceptibility_index")
```

### Step 4: Classifying Final Susceptibility Tiers
Classify the resulting continuous floating-point index (1.0 to 5.0) into 5 standardized operational hazard levels:
- `1.0 – 1.8`: **Very Low Susceptibility** (Dark Green)
- `1.8 – 2.6`: **Low Susceptibility** (Light Green)
- `2.6 – 3.4`: **Moderate Susceptibility** (Yellow)
- `3.4 – 4.2`: **High Susceptibility** (Orange)
- `4.2 – 5.0`: **Very High Susceptibility** (Red)

Style the layer using `style_raster(layer="raw_susceptibility_index", ramp="YlOrRd", classes=5)`.

---

## 3. Empirical Model Validation

If historical flood event coordinates or rescue reports exist, validate the susceptibility model empirically:
```python
# Sample the susceptibility raster at historical flood point locations
run_processing("native:rastersampling", {
    "INPUT": "historical_flood_points",
    "RASTERCOPY": "raw_susceptibility_index",
    "COLUMN_PREFIX": "sampled_"
}, output_name="flood_validation_points")

# Summarize sampled scores
stats = field_stats("flood_validation_points", "sampled_1")
```
Report validation statistics honestly:
*"Empirical validation against 84 recorded historical inundation points indicates that 72% fell within High or Very High susceptibility zones, 21% in Moderate, and 7% in Low zones."*

---

## 4. Quantitative Area Reporting

Calculate total square kilometers and percentage of land area per susceptibility class:
```python
run_processing("native:rasterlayeruniquevaluesreport", {
    "INPUT": "classified_flood_susceptibility",
    "BAND": 1
}, output_name="flood_area_summary")
```

Format the findings as a clean summary table for the user:
```markdown
| Susceptibility Level | Area (km²) | Percentage of Study Area | Priority Actions |
| :--- | :--- | :--- | :--- |
| **Very High** | 42.5 km² | 14.2% | Immediate evacuation planning, structural levees |
| **High** | 68.1 km² | 22.8% | Flood warning sirens, retention basin zoning |
| **Moderate** | 85.4 km² | 28.6% | Building code enforcement, permeable paving |
| **Low** | 62.0 km² | 20.8% | General monitoring |
| **Very Low** | 40.5 km² | 13.6% | Designated emergency assembly points |
```
