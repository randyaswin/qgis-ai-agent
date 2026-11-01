---
name: terrain-relief-map
description: Use for elevation and relief maps from a DEM: hillshade, hypsometric tint, slope, aspect, contour lines, elevation classes. Covers DEM preparation, z-factor pitfalls in geographic CRS, layer order, and cartographic styling.
keywords: DEM, DEMNAS, SRTM, elevation, relief, hillshade, slope, aspect, contour, hypsometric, terrain, topography, ketinggian, kontur, lereng, kemiringan, topografi, bayangan, relief, peta elevasi
license: MIT
---

# Terrain / relief map

## Prepare
1. `describe_layer` the DEM: CRS, pixel size, NoData, min/max. Elevation unit must be metres.
2. If the DEM is in degrees (EPSG:4326), slope/hillshade need either a reprojected DEM or a z-factor. Preferred: `run_processing gdal:warpreproject` to the local UTM CRS (`indonesia-geodata-crs`) with bilinear resampling, then derive products. Do not derive slope with `Z_FACTOR=1` on degree data: it gives nonsense.
3. Clip to the area of interest with `gdal:cliprasterbymasklayer` (mask polygon) to keep rendering fast.

## Products (`run_processing`, confirm params with `describe_algorithm`)
| Product | Algorithm | Notes |
| :--- | :--- | :--- |
| Hillshade | `native:hillshade` | AZIMUTH 315, V_ANGLE 45 by default; Z_FACTOR 1.5 to 2 exaggerates flat areas |
| Slope (degrees) | `native:slope` | classify to 0-3, 3-8, 8-15, 15-25, 25-40, >40 |
| Aspect | `native:aspect` | circular data, use a cyclic or categorical ramp |
| Contours | `gdal:contour` | INTERVAL by relief: 10 m flat, 25 to 50 m hilly, 100 m mountainous |

## Layer order (top to bottom)
labels / contours (thin, 0.15 to 0.3 mm, brown or grey) / hillshade at about 50% opacity / DEM hypsometric tint.
Apply with `add_raster_layer`, `style_raster ramp="Spectral"` (or `Viridis`/`YlOrBr`; pick a green to brown to white hypsometric ramp for land, never rainbow for publication), `update_layer opacity=0.5` on the hillshade, then `update_layer position=...`.

## Checks
- Hillshade lit from the upper left (315°) so relief reads as raised, not inverted.
- Contour labels only on index contours (every 5th line), otherwise clutter. Style with `set_labels` on the contour layer, field `ELEV`.
- Add a legend with elevation classes and units (m dpl). State the DEM name, resolution and year in credits.
- `snapshot_canvas` and look for banding or striping (artifact of low-resolution data); mention it rather than hiding it.
