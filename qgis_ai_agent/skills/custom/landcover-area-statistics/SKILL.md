---
name: landcover-area-statistics
description: Comprehensive guide for land use / land cover (LULC) analysis, zonal area accounting, and temporal change detection. Covers geometry validation, equal-area metric reprojection, zonal intersection, category aggregation, transition matrices (epoch-to-epoch changes), semantic color palettes (FAO/USGS standards), and report generation.
keywords: land cover, land use, lulc, area statistics, zonal statistics, hectares, square kilometers, overlay, intersection, dissolve, change detection, deforestation, urban expansion, corine, worldcover
license: MIT
---

# Land Use & Land Cover (LULC) Zonal Analysis and Accounting

Land use and land cover (LULC) analysis quantifies environmental distribution, urban sprawl, agricultural footprint, and ecological transitions across administrative or watershed boundaries.

---

## 1. Geometric Hygiene & CRS Requirements

> [!CAUTION]
> 1. **Never sum area in angular degrees (`EPSG:4326`).** In degrees, `$area` calculates spherical radians or relies on project ellipsoid approximation that can yield severe distortion away from the equator.
> 2. **Always reproject to an Equal-Area or local UTM CRS** before measuring area (`coordinate-systems-and-crs` skill).
> 3. **Fix geometries before any intersection.** Unrepaired self-intersections or bow-tie rings will cause `native:intersection` to fail or silently drop polygon fragments, resulting in missing land area!

```python
# 1. Clean geometries
run_processing("native:fixgeometries", {
    "INPUT": "raw_landcover_layer"
}, output_name="landcover_fixed")

# 2. Reproject to metric projected CRS
run_processing("native:reprojectlayer", {
    "INPUT": "landcover_fixed",
    "TARGET_CRS": "EPSG:32653"
}, output_name="landcover_metric")
```

---

## 2. Area Calculation & Accounting Workflows

### Method A: Total Area Breakdown by Land Cover Class
To calculate the total area and proportion occupied by each land cover class across the entire study area:

1. **Dissolve by Land Cover Class Field:**
   ```python
   run_processing("native:dissolve", {
       "INPUT": "landcover_metric",
       "FIELD": ["class_name"]
   }, output_name="landcover_dissolved")
   ```
2. **Calculate Area in Hectares and Square Kilometers:**
   ```python
   # Calculate Hectares (1 ha = 10,000 m²)
   calculate_field(
       layer="landcover_dissolved",
       field="area_ha",
       expression="area($geometry) / 10000.0"
   )
   # Calculate Square Kilometers (1 km² = 1,000,000 m²)
   calculate_field(
       layer="landcover_dissolved",
       field="area_km2",
       expression="area($geometry) / 1000000.0"
   )
   ```

---

### Method B: Zonal Breakdown per Administrative Unit
To determine the land cover composition inside each administrative municipality, district, or watershed:

1. **Geometric Intersection:**
   ```python
   run_processing("native:intersection", {
       "INPUT": "landcover_metric",
       "OVERLAY": "district_boundaries",
       "INPUT_FIELDS": ["class_name"],
       "OVERLAY_FIELDS": ["district_id", "district_name"]
   }, output_name="landcover_by_district")
   ```
2. **Calculate Intersected Fragment Area:**
   ```python
   calculate_field(
       layer="landcover_by_district",
       field="frag_area_ha",
       expression="area($geometry) / 10000.0"
   )
   ```
3. **Statistical Aggregation by Category:**
   ```python
   run_processing("qgis:statisticsbycategories", {
       "INPUT": "landcover_by_district",
       "VALUES_FIELD_NAME": "frag_area_ha",
       "CATEGORIES_FIELD_NAME": ["district_name", "class_name"]
   }, output_name="district_lulc_statistics")
   ```

---

## 3. Land Cover Change Detection (Transition Matrix)

To measure how land use transformed between two observation epochs (e.g., Year 2015 vs Year 2025):

1. **Geometric Intersection of Two Epochs:**
   ```python
   run_processing("native:intersection", {
       "INPUT": "landcover_2015_metric",
       "OVERLAY": "landcover_2025_metric",
       "INPUT_FIELDS": ["class_name"],
       "OVERLAY_FIELDS": ["class_name"],
       "OVERLAY_FIELDS_PREFIX": "y2025_"
   }, output_name="lulc_transition_fragments")
   ```
2. **Construct Transition Description Attribute:**
   ```python
   calculate_field(
       layer="lulc_transition_fragments",
       field="transition",
       expression="\"class_name\" || ' -> ' || \"y2025_class_name\""
   )
   calculate_field(
       layer="lulc_transition_fragments",
       field="change_ha",
       expression="area($geometry) / 10000.0"
   )
   ```
3. **Isolate Areas of Real Change:**
   ```python
   # Filter out unchanged polygons (where 2015 == 2025)
   run_processing("native:extractbyexpression", {
       "INPUT": "lulc_transition_fragments",
       "EXPRESSION": "\"class_name\" != \"y2025_class_name\""
   }, output_name="real_landcover_conversions")
   ```

---

## 4. Semantic Cartographic Color Palette

Never assign random automated categorical colors to land cover classes. Follow international cartographic conventions (FAO / USGS / ESA WorldCover):

| Land Cover Class | Conventional Color | Hex Code | Visual Rationale |
| :--- | :--- | :--- | :--- |
| **Dense Tree Cover / Forest** | Deep Forest Green | `#1b5e20` | High canopy density |
| **Open Forest / Woodlands** | Medium Leaf Green | `#388e3c` | Moderate canopy |
| **Shrubland / Scrub** | Olive / Khaki | `#827717` | Low woody vegetation |
| **Grassland / Savanna** | Pale Yellow-Green | `#c0ca33` | Herbaceous vegetation |
| **Cropland / Agriculture** | Warm Golden Amber | `#fbc02d` | Cultivated fields |
| **Rice Paddy / Irrigated** | Pale Chartreuse | `#d4e157` | Wet agricultural surface |
| **Built-Up / Urban / Concrete**| Brick / Crimson Red | `#c62828` | High thermal / human footprint |
| **Bare Soil / Sand / Rocks** | Sandy Beige / Tan | `#d7ccc8` | Exposed ground |
| **Permanent Water Bodies** | Azure Blue | `#0288d1` | Lakes, rivers, reservoirs |
| **Wetland / Mangroves** | Cyan Teal | `#00897b` | Saturated vegetative cover |

Apply styling via `style_categorized(layer="landcover_metric", field="class_name")`.

---

## 5. Output Reporting & Verification

### A. Mathematical Conservation Check
Verify that the sum of all individual land cover class areas equals the official total boundary area:
$$\Delta_{\text{error}} = \left| \sum \text{Class Area} - \text{Total Boundary Area} \right|$$
If $\Delta_{\text{error}} > 0.5\%$, investigate unclosed boundary slivers or clipping extent mismatches.

### B. Summary Table Template for Reports
```markdown
### 📊 Land Cover Accounting Summary

| Class Name | Area (Hectares) | Area (km²) | Proportion (%) | Primary Change Driver |
| :--- | :--- | :--- | :--- | :--- |
| **Dense Forest** | 45,210 ha | 452.1 km² | 38.2% | Stable conservation |
| **Cropland** | 32,150 ha | 321.5 km² | 27.1% | Converted from shrubland |
| **Built-Up Urban** | 18,400 ha | 184.0 km² | 15.5% | +12.4% expansion |
| **Grassland** | 12,300 ha | 123.0 km² | 10.4% | Pasture grazing |
| **Water Bodies** | 6,240 ha | 62.4 km² | 5.3% | Reservoirs and rivers |
| **Bare Ground** | 4,200 ha | 42.0 km² | 3.5% | Quarry and construction |
| **Total Area** | **118,500 ha** | **1,185.0 km²**| **100.0%** | |
```
Export tabular results to CSV with `run_processing("native:savefeatures", {"INPUT": "landcover_dissolved", "OUTPUT": "output_dir/landcover_report.csv"})`.
