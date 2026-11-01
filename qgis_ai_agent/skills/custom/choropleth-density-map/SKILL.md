---
name: choropleth-density-map
description: Use for thematic polygon maps that colour administrative or zonal units by a rate or density (population density, poverty rate, cases per 1000, income, yield). Handles normalisation, equal-area CRS, classification choice, colour ramp, and legend. Do not use for raw counts on unequal-size units without normalising.
keywords: choropleth, thematic map, density, rate, per capita, population density, kepadatan penduduk, peta tematik, kecamatan, kabupaten, provinsi, desa, persentase, rasio, indikator, klasifikasi, gradasi
license: MIT
---

# Choropleth / density map

## Decide before styling
1. `describe_layer` the polygon layer and any table to join. Identify the value field, the unit-code field, NULL counts, geometry validity.
2. **Normalise.** Never colour raw counts across units of different size or population. Compute a rate/density with `calculate_field`:
   - density per km²: `"pop" / ($area / 1000000)` only if the layer CRS is projected in metres. In EPSG:4326 `$area` follows the project ellipsoid setting; prefer `area($geometry)` after reprojecting to an equal-area/UTM CRS.
   - rate per 1000: `"cases" / "pop" * 1000`.
3. **CRS.** Area-based maps need an equal-area or local UTM CRS (see `indonesia-geodata-crs`). `run_processing native:reprojectlayer` then work on the result.
4. **Joins.** Join tabular data with `native:joinattributestable` (INPUT, FIELD, INPUT_2, FIELD_2). Join keys must have the same type; admin codes are text with leading zeros. Check `describe_layer` afterwards: unmatched rows show as NULLs, report their count.

## Classification (use `field_stats` first)
| Data shape | Method |
| :--- | :--- |
| Skewed, a few extreme values | `jenks` (natural breaks) or `quantile` |
| Roughly uniform | `equal` |
| Diverging around a meaningful value (mean, 0) | `stddev` or manual breaks, diverging ramp |
| Need comparable maps | same fixed breaks on every map |
- 4 to 6 classes. More than 7 is unreadable. State the method in the legend/subtitle.
- Treat NULL/zero as a separate "no data" class instead of the lowest class.

## Colour
- Sequential data: `style_graduated` with ramp `YlOrRd`, `Blues`, `Greens`, `Viridis`. Diverging data: `RdBu`, `Spectral`. Use `list_color_ramps` if unsure.
- Light = low, dark = high. Thin light-grey outline (`outline_color="#ffffff"` or `#999999`) so units stay distinct.
- Colour-blind safe: avoid red-green pairs; Viridis and Blues are safe.

## Steps
`style_graduated(layer, field, classes=5, method="jenks", ramp="YlOrRd", outline_color="#ffffff")`, then `set_labels` only if there are few units (< 40) or label with the value; add a keyless XYZ basemap below at low opacity only if it helps (`add_basemap(preset="carto_light")` then `update_layer opacity=0.45`). Never use basemaps that require API keys. Verify with `snapshot_canvas`, then `create_layout` and `export_layout`.

## Report
Name the value field, normalisation formula, class breaks, data year and source on the map credits line.
