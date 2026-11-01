---
name: satellite-index-map
description: Use for maps from multispectral imagery (Sentinel-2, Landsat): NDVI, NDWI, NBR, true/false colour composites, index classification, clipping to a study area. Covers band selection, reflectance scaling, cloud masking checks, raster calculator expressions and colour ramps.
keywords: NDVI, NDWI, NBR, Sentinel-2, Landsat, satellite, citra satelit, citra, indeks vegetasi, vegetasi, band, raster calculator, komposit, true color, false color, cloud, awan, reflectance, remote sensing, penginderaan jauh
license: MIT
---

# Satellite index maps

For method depth `load_skill("remote-sensing-analysis")`; for before/after maps `load_skill("change-detection")`.

## Inspect first
`describe_layer` each band file: data type, NoData, CRS, resolution, min/max, histogram. Sentinel-2 L2A reflectance is usually integers scaled by 10000 (offset may apply); L1C vs L2A matters. State what you assume.

## Sentinel-2 bands
B2 blue, B3 green, B4 red, B8 NIR (10 m), B11 SWIR1 (20 m), B12 SWIR2 (20 m), SCL scene classification (20 m).
- NDVI = (B8 - B4) / (B8 + B4)
- NDWI (water) = (B3 - B8) / (B3 + B8)
- NBR = (B8 - B12) / (B8 + B12); resample SWIR to 10 m first (`gdal:warpreproject`) or compute at 20 m.
Landsat 8/9: NIR = B5, red = B4, green = B3, SWIR2 = B7; apply the collection-2 scale factors.

## Steps
1. Put all bands on the same CRS/grid: `gdal:warpreproject` (nearest for categorical, bilinear for reflectance), clip with `gdal:cliprasterbymasklayer`.
2. Compute with `native:rastercalc` using float output. Reference syntax like `"B8@1"`; confirm with `describe_algorithm`. Cast before dividing; guard `(B8+B4) = 0`.
3. Mask clouds/shadows using SCL (keep classes 4 vegetation, 5 bare, 6 water, 7 unclassified; drop 3, 8, 9, 10) or report that no mask was applied and clouds may bias the index.
4. Classify: NDVI fixed breaks are easier to read than stretched values: < 0 water/cloud, 0 to 0.2 bare/built, 0.2 to 0.4 sparse, 0.4 to 0.6 moderate, > 0.6 dense. Use `style_raster` with `classes` and ramp `RdYlGn`, `invert_ramp=false`. Use fixed 0 to 1 limits so dates compare.
5. Composite maps: true colour B4/B3/B2, false colour B8/B4/B3 (vegetation red). Multiband styling beyond the tools may need `exec_pyqgis` if enabled; otherwise create a VRT/composite via `gdal:merge` with separate bands and describe the render limitation.

## Reporting
Acquisition date, sensor, processing level, cloud assumptions, and index formula go on the map credits. Do not call NDVI "crop health" unless validated.
