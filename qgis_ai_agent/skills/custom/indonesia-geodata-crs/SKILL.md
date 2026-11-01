---
name: indonesia-geodata-crs
description: Use whenever the study area is in Indonesia, or data has Indonesian admin codes, DGN95/SRGI2013/ID74 datums, or UTM zones 46-54. Gives the correct projected CRS per region, datum notes, admin-code join rules, and where to find authoritative Indonesian datasets.
keywords: indonesia, UTM zone, EPSG 327, DGN95, SRGI2013, ID74, BIG, BPS, kemendagri, kode wilayah, RBI, DEMNAS, geoportal, provinsi, kabupaten, kecamatan, desa, proyeksi, sistem koordinat, jawa, sumatera, kalimantan, sulawesi, papua, bali, maluku
license: MIT
---

# Indonesia: CRS, datums and data

## Pick the projected CRS (for area, distance, buffers, density)
Zone = floor((longitude + 180) / 6) + 1. Use the **S** code south of the equator, **N** north of it.

| Region (approx. lon) | Zone | South | North |
| :--- | :--- | :--- | :--- |
| Aceh, west Sumatra coast (90 to 96 E) | 46 | EPSG:32746 | EPSG:32646 |
| Sumatra, west Kalimantan edge (96 to 102 E) | 47 | EPSG:32747 | EPSG:32647 |
| Jakarta, West Java, Banten, south Sumatra, west Kalimantan (102 to 108 E) | 48 | EPSG:32748 | EPSG:32648 |
| Central/East Java, central Kalimantan (108 to 114 E) | 49 | EPSG:32749 | EPSG:32649 |
| Bali, NTB, east Kalimantan, south Sulawesi (114 to 120 E) | 50 | EPSG:32750 | EPSG:32650 |
| NTT, central Sulawesi, Maluku Utara west (120 to 126 E) | 51 | EPSG:32751 | EPSG:32651 |
| Maluku, west Papua edge (126 to 132 E) | 52 | EPSG:32752 | EPSG:32652 |
| West Papua (132 to 138 E) | 53 | EPSG:32753 | EPSG:32653 |
| Papua (138 to 144 E) | 54 | EPSG:32754 | n/a |
- Region spanning several zones (national map): use EPSG:4326 for display only, and compute areas with an equal-area CRS or ellipsoidal measures, never in degrees.
- Verify the choice from `describe_layer` `extent_wgs84`, then `set_project_crs`.
- Web basemaps are EPSG:3857; do not measure area or distance in it.

## Datums (all verified in QGIS)
- EPSG:4326 WGS 84: default for GPS, OSM, web data.
- EPSG:9468 SRGI2013 (current national geodetic reference), EPSG:4755 DGN95, EPSG:4238 ID74 (legacy). SRGI2013/DGN95 are within about a metre of WGS 84 for mapping, so no datum shift is needed at map scale. Only treat ID74 data as offset (tens of metres) and reproject through QGIS rather than assuming equality.
- DGN95 national projection zones exist (TM-3, e.g. EPSG:23834 = zone 48.2). Treat as a source CRS: reproject to WGS 84 / UTM for analysis unless the user requires TM-3.
- If a layer reports no CRS, do not guess silently: inspect extent values (degrees vs metres), state the assumption, and set it only with `exec_pyqgis`/processing `native:assignprojection`.

## Administrative codes and joins
- Kode wilayah: province 2 digits, kabupaten/kota 4 (`33.74`), kecamatan 6, desa/kelurahan 10. Store and join as **text**, keep leading zeros, strip dots consistently on both sides (`replace("code",'.','')`).
- Kemendagri and BPS code lists differ for some units and change after pemekaran. Confirm which system each table uses before joining, and report the number of unmatched rows.
- Name matching is a last resort: normalise case, remove "KAB."/"KOTA", then list leftovers instead of dropping them.

## Where to get data (if network access is available)
- BIG InaGeoportal / tanahair.indonesia.go.id: RBI base maps, admin boundaries, DEMNAS (about 8 m DEM).
- BPS (bps.go.id): population and statistics tables.
- KLHK geoportal: forest and land cover. Geofabrik Indonesia extract: OSM roads/buildings. WorldPop and HDX: population grids and humanitarian layers.
- Always record source, year and licence in the map credits.
