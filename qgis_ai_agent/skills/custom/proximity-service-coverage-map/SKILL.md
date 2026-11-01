---
name: proximity-service-coverage-map
description: Use for access and coverage questions: which areas are within a distance or travel time of facilities (hospitals, schools, puskesmas, fire stations), underserved zones, nearest-facility maps. Covers buffers vs network service areas, spatial joins and summaries, and presentation of coverage gaps.
keywords: buffer, service area, coverage, accessibility, catchment, nearest facility, puskesmas, rumah sakit, sekolah, jangkauan, cakupan, layanan, jarak, waktu tempuh, isochrone, fasilitas, aksesibilitas, radius, wilayah terlayani
license: MIT
---

# Proximity and coverage

## Choose the measure honestly
- **Straight-line buffer** (`native:buffer`, `native:multiringconstantbuffer`): fast screening, ignores roads and barriers. Say "radius", not "travel distance".
- **Network service area** (`native:serviceareafrompoint`, `native:shortestpathpointtopoint`): needs a connected road line layer; set strategy (shortest/fastest) and speed. Prefer it when the user says travel time or driving distance. For the method details `load_skill("network-accessibility-analysis")`.
- Always run in a projected metre CRS (`indonesia-geodata-crs`); a buffer of 0.01 in degrees is not 1 km.

## Workflow
1. `describe_all_layers`: find facility points, boundary polygons, road lines, population layer.
2. Fix geometries if needed: `native:fixgeometries`. Reproject everything to one UTM CRS.
3. Coverage: buffer facilities by the standard distance (ask or state an assumption, e.g. 1, 3, 5 km rings via `native:multiringconstantbuffer`), `native:dissolve` the result.
4. Gap: `native:difference` (boundary minus coverage) gives uncovered areas. Area by unit: `native:intersection` then `native:aggregate` or `calculate_field` with `$area`.
5. Per-unit statistics: `native:countpointsinpolygon` (facilities per unit) or `native:joinbylocationsummary` (summarise facility attributes per polygon). Population served: zonal sum of a population raster with `native:zonalstatisticsfb`.
6. Per-feature nearest distance: `describe_algorithm` for `native:joinbynearest` if available.

## Cartography
- Facilities: `style_single` point, size 3 to 4 mm, dark colour, label facility names only if few.
- Coverage rings: `style_categorized` on ring distance, sequential ramp, opacity 0.5.
- Uncovered zone: a contrasting hatch or red tint, named in the legend ("Di luar jangkauan 5 km").
- Report numbers: percent of area and population covered, units with zero facilities. State the distance, measure type and data year on the map.
