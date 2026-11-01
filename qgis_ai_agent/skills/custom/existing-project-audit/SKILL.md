---
name: existing-project-audit
description: Comprehensive diagnostic and quality assurance audit for pre-existing QGIS projects. Performs systematic read-only inspection of layer validity, CRS consistency, coordinate extent sanity, geometry topology, attribute integrity, sentinel values, duplicate layers, and existing symbology before making any non-destructive modifications.
keywords: audit, project audit, data quality, data hygiene, invalid geometry, crs mismatch, broken data, attribute check, fix geometries, topology, inspection, data forensics
license: MIT
---

# Pre-Existing Project & Layer Diagnostic Audit Guide

When a user opens an existing QGIS project or loads unfamiliar datasets, an AI agent must never immediately modify, restyle, or delete layers blindly. Instead, execute a systematic, non-destructive audit to diagnose data health, geometric validity, coordinate consistency, and attribute integrity.

---

## 1. Five-Stage Systematic Audit Framework

### Stage 1: Discovery & State Capture (Read-Only)
1. **Query Project Baseline:**
   ```python
   project_state = get_project_state()
   ```
   Inspect:
   - Project CRS (`project_state["crs"]`).
   - Project file path (`project_state["project_file"]`).
   - Layer count and stacking order (`project_state["layers_top_to_bottom"]`).
   - Existing print layouts (`project_state["layouts"]`).
2. **Deep Layer Inspection:**
   ```python
   all_layers = describe_all_layers()
   ```
   Inspect each layer's validity, provider type, feature count, CRS, and attribute schema.

---

### Stage 2: Geometric & Topological Health Audit
For each vector layer, check for common geometric defects:
- **Invalid Geometries:** Self-intersections, bow-tie polygons, unclosed rings, duplicate consecutive vertices.
- **Empty Geometries:** Features with attributes but `None` or empty geometry.
- **Multi-Part vs Single-Part:** Inconsistent geometry types (e.g., `MultiPolygon` vs `Polygon`).

#### Diagnostic & Fix Recipes
```python
# Inspect single layer details
layer_info = describe_layer("Study_Parcels")

# If invalid geometries exist, repair them non-destructively:
run_processing("native:fixgeometries", {
    "INPUT": "Study_Parcels",
    "METHOD": 1  # 1 = Structure repair
}, output_name="Study_Parcels_Cleaned")

# Decompose multipart geometries if individual unit area/center is required:
run_processing("native:multiparttosingleparts", {
    "INPUT": "Study_Parcels_Cleaned"
}, output_name="Study_Parcels_Singlepart")
```

---

### Stage 3: Coordinate Reference System (CRS) & Extent Sanity
1. **Mixed CRS Detection:**
   - Review the CRSs across all layers. Mixed CRSs are visually reprojected on the fly by QGIS, but will cause errors or silent inaccuracies in geometric processing (buffers, intersections, zonal stats).
   - If layers are in `EPSG:4326`, plan reprojection to local UTM or an equal-area metric CRS.
2. **Coordinate Swapping & Outlier Detection:**
   - Inspect bounding box (`canvas_extent` and layer extents).
   - If coordinates for a regional map range from `-180` to `+180` while the study area is a small town, an outlier point (e.g. at `0,0`) is corrupting the canvas zoom extent.
   - Filter or delete the stray outlier feature.

---

### Stage 4: Attribute Table Forensics & Hygiene
Run `field_stats` on critical analysis fields:
1. **NULL & Missing Values:**
   - Count of `NULL`, `NaN`, or empty string `""` entries.
   - Determine whether NULLs represent genuine zeros or missing survey data.
2. **Sentinel Values:**
   - Look out for legacy sentinel codes: `-999`, `-9999`, `999`, `9999`, or `N/A` stored in numerical columns. If fed into averages, sums, or graduated classification, these distort results catastrophically!
   - Remediate sentinel values using `calculate_field`:
     ```sql
     CASE WHEN "income" < 0 THEN NULL ELSE "income" END
     ```
3. **Data Type Mismatches:**
   - Numerical values (e.g. population, area, rates) stored as String/Text columns (`VARCHAR`). Text fields cannot be graduated numerically until cast.
   - Date/time values stored as raw strings formatted inconsistently.
   - Administrative join codes stored as integers that lost their leading zero (e.g. `102` instead of `"0102"`).

---

### Stage 5: Symbology, Visibility & Stacking Order
1. **Layer Occlusion (Bad Stacking):**
   - Polygons sitting on top of lines and points, obscuring critical roads and POIs.
   - Fix with `update_layer(layer=..., position=...)` to enforce standard cartographic order:
     1. Points / POIs / Labels (Top)
     2. Lines / Streams / Roads
     3. Thematic Polygons
     4. Shaded Relief / DEM
     5. Basemap Tile Layer (Bottom)
2. **Hidden Subset Filters:**
   - Check if a layer has a subset filter active (`"status" = 'Active'`). If the user complains that "features went missing", check and report subset filters.

---

## 2. Non-Destructive Remediation Protocol

> [!IMPORTANT]
> - **Never overwrite original raw files** or source databases unless explicitly instructed by the user.
> - Always output cleaned, repaired, or reprojected data to temporary scratch layers or designated files in `{output_dir}`.
> - Preserve existing user symbology: if the user spent time styling a layer, avoid overwriting its renderer unless they specifically requested a restyle.

---

## 3. Professional Audit Findings Report Template

Present the audit summary to the user clearly before executing changes:

```markdown
### 📋 Project Diagnostic Audit Summary

| Layer Name | Type | Feature Count | CRS | Geometric Health | Attribute Issues | Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Boundaries** | Polygon | 42 | EPSG:4326 | 3 Self-intersections | Clean | ⚠️ Needs Fix & Reproject |
| **Incidents** | Point | 1,420 | EPSG:32653 | Clean | 12 NULL categories | ℹ️ Minor NULLs |
| **Roads** | Line | 8,910 | EPSG:32653 | Clean | Clean | ✅ Ready |
| **Elevation** | Raster | 3601×3601 | EPSG:4326 | Clean | Min: -2m, Max: 3776m | ⚠️ Needs Reproject |

**Recommended Actions:**
1. Repair 3 invalid polygons in `Boundaries` via `native:fixgeometries`.
2. Reproject `Boundaries` and `Elevation` into projected metric `EPSG:32653` (UTM Zone 53N).
3. Reorder layer stack so `Incidents` points are above `Boundaries` fill.
```
