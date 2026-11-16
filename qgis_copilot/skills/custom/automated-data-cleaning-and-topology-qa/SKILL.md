---
name: automated-data-cleaning-and-topology-qa
description: Non-destructive vector and raster data cleaning, topology validation, duplicate geometry removal, sliver/gap/overlap elimination, multipart splitting, coordinate outlier quarantine, attribute schema normalization, sentinel value scrubbing, and table-join key matching before spatial analysis or cartography.
keywords: clean, cleaning, topology, topological, overlap, gap, sliver, duplicate, fix geometries, repair, invalid geometry, null, sentinel, normalize, attribute cleanup, schema, join mismatch, outlier, snap, dissolved, multipart, qa, data hygiene
license: MIT
---

# Automated GIS Data Cleaning & Topology QA Pipeline

Run this workflow **before** heavy geoprocessing (`intersection`, `union`, `dissolve`, `zonal statistics`) or when user datasets exhibit GEOS validity errors, slivers, duplicate features, failed table joins, or broken statistics. Never overwrite original user source files in place unless explicitly instructed — produce clean working layers/GeoPackages.

---

## 1. Stage 1 — Automated Diagnostics (Read-Only)
1. Run `describe_layer(layer)` (or `describe_all_layers()`):
   Check:
   - `valid` status and CRS (`EPSG:4326` vs projected metric).
   - `empty_geometries` and `invalid_geometries_sampled`.
   - Extent sanity (`extent`): catch stray `(0, 0)` or `9999` null-island outliers stretching a city map to global extent!
   - Per-field `nulls`, `unique_count`, and suspicious min/max sentinel numbers (`-9999`, `-999`, `9999`, `0` where missing).

---

## 2. Stage 2 — Geometry & Topology Repair Sequence
Execute steps in order — skip steps already confirmed clean:

1. **Drop Null / Empty Geometries**:
   ```python
   run_processing("native:removenullgeometries", {
       "INPUT": "raw_layer",
       "REMOVE_EMPTY": True
   }, output_name="step1_nonnull")
   ```
2. **Fix Self-Intersections & Ring Orientation (`GEOS Structure`)**:
   ```python
   run_processing("native:fixgeometries", {
       "INPUT": "step1_nonnull",
       "METHOD": 1  # Structure method (handles bow-ties & self-crossings reliably)
   }, output_name="step2_valid")
   ```
3. **Deduplicate Exact Identical Geometries**:
   ```python
   run_processing("native:deleteduplicategeometries", {
       "INPUT": "step2_valid"
   }, output_name="step3_dedup")
   ```
4. **Snap Near-Coincident Vertices & Eliminate Slivers (Polygons)**:
   - When adjacent boundary polygons exhibit micro-gaps/overlaps:
     - Snap layer to itself (`native:snapgeometries`, tight tolerance e.g. `0.5 m` in projected CRS or `0.000005°` — NEVER snap with large tolerance or small features collapse!).
     - Run `native:checkvalidity` (`METHOD: 2` GEOS) to isolate any remaining invalid/error features.
     - Eliminate micro-slivers below minimum mapping area threshold (e.g., `$area < 100` m²) using `native:extractbyexpression` + `native:eliminateselectedpolygons` (`MODE: 2` largest common boundary).
5. **Remove Stranded Coordinate Outliers ("Null Island" Filter)**:
   If bounding box shows an isolated point far outside study extent (e.g. near `0, 0`), inspect coordinates and filter valid extent (`native:extractbyextent` or attribute query) instead of deleting silently — report how many features were quarantined.

---

## 3. Stage 3 — Attribute Hygiene & Table Join Key Alignment
Common reasons `CSV <-> Admin Polygon` joins fail (`NULL` rates across half the map!):
- **Case & Whitespace Mismatch**: `"Kec. Menteng "` vs `"MENTENG"`.
  Create a clean normalized join key field via `calculate_field`:
  ```sql
  upper(trim(regexp_replace("nama_kec", '^(kec\\.?|kecamatan)\\s+', '')))
  ```
- **Leading Zero Strip on BPS / Admin Codes**: Excel saves `"3171010"` as integer `3171010`, or drops `"01"` -> `1`. Pad cleanly back to fixed width (`lpad(to_string("kode"), 10, '0')`).
- **Sentinel Value Scrubbing**:
  Replace `-999`, `-9999`, `999999` with `NULL` before graduated classification (`calculate_field`: `CASE WHEN "val" <= -999 THEN NULL ELSE "val" END`), otherwise Jenks/Min-Max breaks distort around `-9999`!
- **Safe Text-to-Numeric Casting**:
  Strip commas/spaces before converting imported CSV strings to `double` (`to_real(replace(trim("raw_num"), ',', '.'))`).

---

## 4. Verification Gate Before Returning to Downstream Analysis
Re-run `describe_layer("cleaned_layer")` and confirm:
- `empty_geometries == 0`, `invalid_geometries_sampled == 0`
- Feature count delta (`raw -> cleaned`) matches expected dropped empty/duplicate rows (log exact counts in final summary).
- Target join coverage reaches expected `%` match before styling choropleth or running overlay math.
