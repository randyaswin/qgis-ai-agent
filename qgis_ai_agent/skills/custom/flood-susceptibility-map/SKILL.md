---
name: flood-susceptibility-map
description: Use to build a flood or landslide susceptibility (kerawanan / rawan) map by weighted overlay of terrain and environmental factors. Covers factor selection, reclassification to scores, weights, raster calculator, classification into hazard levels, and honest limitations. Not a hydraulic simulation.
keywords: flood, banjir, rawan banjir, kerawanan, susceptibility, hazard, landslide, longsor, weighted overlay, overlay berbobot, HAND, slope, rainfall, curah hujan, jarak sungai, tutupan lahan, risiko, mitigasi, genangan
license: MIT
---

# Flood susceptibility by weighted overlay

State up front that this is a **screening-level susceptibility index**, not modelled inundation depth. For hydro-conditioning and HAND use `load_skill("terrain-hydrology")`; for the MCDA method use `load_skill("mcda-suitability-analysis")`.

## Factors (use what exists; say which are missing)
| Factor | Source / algorithm | Higher susceptibility |
| :--- | :--- | :--- |
| Slope | `native:slope` on projected DEM | flatter |
| Elevation or HAND | DEM / terrain-hydrology skill | lower |
| Distance to river | `native:buffer` or `native:multiringconstantbuffer` on rivers, rasterize | closer |
| Rainfall | raster or interpolated stations (`qgis:idwinterpolation`) | wetter |
| Land cover / soil | polygons rasterized with `native:rasterize` | impervious, clay, water |

## Method
1. Work in a projected CRS and one common grid: resample all rasters to the DEM extent and cell size (`gdal:warpreproject`).
2. Reclassify every factor to the same 1 to 5 score with `native:reclassifybytable` (document the break table in the answer).
3. Weights sum to 1. Defaults if the user gives none: slope 0.25, elevation/HAND 0.25, river distance 0.2, rainfall 0.15, land cover 0.15. Say they are defaults and invite revision; do not claim calibration.
4. Combine with `native:rastercalc`, e.g. `"slope_s@1"*0.25 + "elev_s@1"*0.25 + ...` (confirm the layer reference syntax with `describe_algorithm`).
5. Classify the index into 5 levels (very low to very high) with `native:reclassifybytable` or by `style_raster` with fixed classes. Use a single-hue to red ramp.
6. If flood points/events exist, check them: `sample_raster` at event points and report how many fall in high/very high classes. If no validation data exists, say so.

## Map and reporting
- Overlay admin boundaries and rivers on top, hillshade underneath (`terrain-relief-map`).
- Provide area per hazard class (`native:rasterlayeruniquevaluesreport`) as a short table in the final answer.
- Legend labels in plain words: Sangat Rendah / Rendah / Sedang / Tinggi / Sangat Tinggi.
- Credits list every input dataset, year and the weighting assumption.
