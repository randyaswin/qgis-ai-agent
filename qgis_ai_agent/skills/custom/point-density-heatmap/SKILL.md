---
name: point-density-heatmap
description: Use to show concentration of point events (incidents, crimes, accidents, cases, POIs, hotspots) via kernel heatmap, hexagon/grid counts, or point clustering. Explains when to prefer each and how to avoid misleading density surfaces.
keywords: heatmap, kernel density, hotspot, hexbin, hexagon, grid count, cluster, point density, incidents, kejadian, titik panas, kepadatan titik, sebaran titik, kriminalitas, kecelakaan, kasus, POI, konsentrasi
license: MIT
---

# Point density

## Pick a representation
| Goal | Method |
| :--- | :--- |
| Quick visual of where points concentrate | Kernel density: `qgis:heatmapkerneldensityestimation` |
| Quantified, comparable cells | Hexagon grid + count (`native:creategrid` TYPE hexagon, then `native:countpointsinpolygon`) |
| Few hundred points, precise locations matter | Plain symbols, optionally `style_single` with low opacity |
| Per administrative unit | Count then normalise per area/population; see `choropleth-density-map` |
Raw hexbin counts are quantitative; kernel surfaces are smooth and depend on the radius, so state the radius.

## Steps
1. `describe_layer` the points: count, CRS, NULL or duplicate coordinates, optional weight field (`field_stats`). Reproject to metre UTM (`indonesia-geodata-crs`).
2. Radius or cell size: start around 1/50 to 1/20 of the extent's long side, or a meaningful distance (500 m for urban incidents). Try two values if unsure and show the more stable one.
3. Kernel: params INPUT, RADIUS, PIXEL_SIZE (about radius/10), optional WEIGHT_FIELD, KERNEL (quartic default), then `style_raster ramp="YlOrRd"`, make the lowest class transparent (clip nodata/0) so the basemap shows.
4. Hex grid: build grid over the points' extent buffered by one cell, count points per cell, drop zero cells with `native:extractbyexpression "NUMPOINTS" > 0`, `style_graduated method="jenks" classes=5`.
5. Add a basemap (`add_basemap`) below; keep it muted.

## Pitfalls
- Dense urban points are often reported at a village centroid or geocoded to one spot: check duplicates, mention that hotspots can be a geocoding artifact.
- Do not compare maps made with different radius or grid sizes.
- A hotspot is a descriptive pattern, not statistical significance; for tested hotspots `load_skill("spatial-statistics")`.
