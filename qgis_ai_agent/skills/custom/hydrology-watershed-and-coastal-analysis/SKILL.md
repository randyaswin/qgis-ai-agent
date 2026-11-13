---
name: hydrology-watershed-and-coastal-analysis
description: Complete terrain hydrology, watershed/DAS delineation, stream network extraction, Strahler order morphometry, topographic wetness index (TWI), reservoir/dam submergence volume, bathymetry contours, and coastal SLR inundation or shoreline buffer modeling.
keywords: hydrology, watershed, das, catchment, basin, river, stream order, strahler, flow accumulation, flow direction, sink fill, dem, twi, hand, drainage, coastal, sea level rise, slr, inundation, bathymetry, shoreline, coastline, reservoir, submergence
license: MIT
---

# Hydrology, Watershed (DAS) & Coastal Spatial Analysis

Use this skill for terrain-based catchment delineation (**DAS/Sub-DAS**), drainage extraction, **TWI / HAND** wetness indices, **sea-level rise (SLR) bathtub inundation**, bathymetric contours, and coastal setback analysis.

---

## 1. DEM Pre-Conditioning (Mandatory Before Hydrology)
Raw DEMs (SRTM, FABDEM, Copernicus GLO-30, DEMNAS) contain sinks/pits and often ship in `EPSG:4326`:
1. Reproject DEM to a **metric projected CRS** (local UTM) using bilinear/cubic resampling (`gdal:warpreproject`) so slope and flow lengths compute in real meters.
2. **Fill Sinks / Depression Removal**: Never extract streams directly from an unfilled DEM!
   Search available algorithms first with `search_processing("fill sinks")` or `search_processing("watershed")`:
   - SAGA (`sagang:fillsinkswangliu` or `saga:fillsinksxxlwangliu`) — preferred; computes filled DEM + flow direction (`FDIR`) + watershed basins in one step.
   - GRASS (`grass7:r.watershed` / `grass:r.watershed`) — robust single-call extraction for accumulation (`accumulation`), drainage direction (`drainage`), streams (`stream`), and half-basins (`basin`) with no external sink-fill required (`threshold` in cells, e.g., `500`–`5000` depending on DEM pixel resolution).
   - Native GDAL/QGIS fallback if SAGA/GRASS is unavailable in environment: check `search_processing("drainage")` / `native:slope` before running.

---

## 2. Watershed (DAS) & Morphometry Pipeline
1. **Determine Stream Threshold in Metric Area**:
   For a `30 m` DEM (`900 m²/pixel`), `1 km²` contributing area $\approx 1{,}111$ cells (`threshold=1000`); for `10 m` DEMNAS (`100 m²/pixel`), `1 km²` $= 10{,}000$ cells.
2. **Delineate Catchment from Outlet Pour Point**:
   - Snap pour point to high-accumulation stream cell before tracing upstream catchment (`grass:r.water.outlet` or SAGA `upslopearea`), then vectorize via `gdal:polygonize`.
   - Compute DAS morphometric parameters via `calculate_field` & `native:zonalstatisticsfb`:
     - Basin Area (`km²`), Perimeter (`km`), Min/Mean/Max Elevation (`m`), Mean Slope (`°`), Elongation/Circularity Ratio.
     - **Drainage Density ($D_d$)**: `total_stream_length_km / basin_area_km2`.
3. **Topographic Wetness Index (`TWI`)**:
   $$\text{TWI} = \ln\left(\frac{a}{\tan(\beta)}\right)$$
   where $a$ is specific catchment area (`flow_accum * cell_size`) and $\beta$ is slope in radians (clamp minimum slope $\ge 0.001$ rad to prevent division-by-zero!). High TWI marks saturated riparian valleys and groundwater recharge/flood-prone zones.

---

## 3. Coastal SLR Inundation, Shoreline Setback & Bathymetry
- **Hydrologically Connected Bathtub SLR (1 m / 2 m / 5 m scenarios)**:
  Do **NOT** just filter `DEM <= sea_level_m` across the whole raster — inland closed depressions below sea level would falsely show flooded!
  Instead:
  1. Reclassify `DEM <= scenario_m` to binary `1` (`native:rastercalc` or `native:reclassifybytable`), vectorize polygons (`gdal:polygonize`), keep `DN = 1`, and run `native:extractbylocation` against coastline/ocean polygon (`intersects`) so **only sea-connected inundation polygons** survive!
  2. Intersect flood extent with Overture buildings/roads to quantify exposed infrastructure.
- **Coastal Setback (Sempadan Pantai — 100 m min per Perpres 51/2016 / local bylaw)**:
  Buffer mean high-water coastline inward (`native:buffer` + clip to land polygon) for zoning audit.
- **Bathymetry & Contours**:
  Generate smooth depth contours (`gdal:contour`, interval e.g. `-5, -10, -20, -50, -100`) and style ocean depth with sequential deep-blue palette (`Blues` or custom teal `#80DEEA` -> navy `#0D47A1`).

---

## 4. Cartography for Hydrology & Coastal Maps
- Style streams by **Strahler Order** or discharge class with tapered line widths (`0.25 mm` order 1–2 -> `1.4 mm` mainstem) in crisp hydro blue `#1565C0` with subtle lighter highlight `#4FC3F7`.
- Shade DAS boundary as distinct dark dashed/casing outline (`#1B365D`, `0.6 mm`), softly dimming outside areas using an inverted polygon mask (`20–30%` white/slate veil) so the watershed pops immediately!
