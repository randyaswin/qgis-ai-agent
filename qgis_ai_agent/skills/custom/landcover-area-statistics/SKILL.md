---
name: landcover-area-statistics
description: Use for land cover / land use maps and for computing area statistics per class or per administrative unit, overlay of two polygon layers (change, zoning vs actual use), and area tables. Covers geometry repair, equal-area CRS, intersection and dissolve, and categorized styling with standard colours.
keywords: land cover, land use, tutupan lahan, penggunaan lahan, RTRW, zonasi, luas, area statistics, hectare, hektar, overlay, intersection, dissolve, perubahan lahan, deforestasi, hutan, sawah, permukiman, kategori
license: MIT
---

# Land cover and area statistics

## Prepare
1. `describe_layer` the polygons: class field, unique values (`field_stats`), invalid geometries, CRS.
2. `native:fixgeometries` on every input before overlay: invalid polygons make intersection fail or silently drop area.
3. Reproject to an equal-area or local UTM CRS before measuring (`indonesia-geodata-crs`). Never sum `$area` in degrees.

## Area tables
- Per class: `native:dissolve` by class field, then `calculate_field field="area_ha" expression="$area/10000"` (check the project ellipsoid; with a projected CRS use `area($geometry)/10000`).
- Per administrative unit and class: `native:intersection` (landcover x boundary), then `native:aggregate` grouped by unit + class, or `qgis:statisticsbycategories`. Compute area after the intersection.
- Cross-check: the sum of class areas should equal the boundary area within about 0.5%. If not, report gaps/overlaps instead of hiding them.
- Present results as a compact markdown table in the final answer and, if useful, save a CSV into the output folder via `run_processing native:savefeatures` (confirm with `describe_algorithm`).

## Change between two dates
`native:intersection` of date A and date B (prefix fields), `calculate_field` a `change` string `"cls_a" || ' -> ' || "cls_b"`, filter `"cls_a" <> "cls_b"`, aggregate area per transition pair; map with `style_categorized` on the changed classes only and grey for unchanged.

## Styling
- `style_categorized` with semantic colours, not random ones: forest dark green, plantation/agri light green or yellow, rice paddy pale yellow-green, built-up red/grey, water blue, bare land tan, mangrove teal.
- Keep the class list short; merge classes below about 1% into "Lainnya" with `calculate_field` plus a reclass expression.
- Order the legend by area or by logical group; include area (ha) in labels when helpful.
- Credits: dataset, year, classification system (e.g. KLHK land cover scheme), and any generalisation done.
