---
name: urban-planning-and-zoning-rtrw
description: Spatial planning, land-use zoning (RTRW/RDTR, Pola Ruang & Struktur Ruang), parcel/cadastral overlay compliance, KDB/KLB/RTH green open space ratios, Transit-Oriented Development (TOD) catchment modeling, and official ATR/BPN-aligned cartographic styling.
keywords: rtrw, rdtr, zoning, tata ruang, pola ruang, struktur ruang, urban planning, land use, zonasi, kawasan lindung, budidaya, rth, green space, kdb, klb, gsb, parcel, persil, cadastre, tod, transit oriented, kesesuaian lahan,atr bpn, permukiman, sempadan, industrial zone
license: MIT
---

# Urban Planning, Zoning (RTRW / RDTR) & Land Compliance Workflow

This skill guides spatial planning, zoning overlay compliance, urban land-use capacity analysis, and official **RTRW / RDTR** (Pola Ruang & Struktur Ruang) cartography inside QGIS.

---

## 1. Metric CRS & Boundary Integrity First
All planning area calculations (hectares, RTH %, FAR/KLB, BCR/KDB) and setback buffers **must** execute in a local projected metric CRS (e.g., UTM zone matching study extent, never `EPSG:4326` degrees):
1. Inspect existing project layers via `describe_all_layers()` or `describe_layer(layer)`.
2. Check CRS via `get_project_state()` and reproject vectors before buffering/intersecting:
   ```python
   run_processing("native:reprojectlayer", {
       "INPUT": "existing_landuse",
       "TARGET_CRS": "EPSG:32748"  # e.g., UTM 48S for Western Java / Sumatra
   }, output_name="landuse_utm")
   ```
3. Clean zoning topology before overlay so sliver polygons do not inflate area tables (`native:fixgeometries`, `native:snapgeometries` if needed).

---

## 2. Pola Ruang vs. Existing Land Use Compliance Matrix
To audit conformity (**Kesesuaian Pemanfaatan Ruang** / KKPR):
1. Normalize category names in both Existing Land Use and Planned Zoning (`RDTR_PolaRuang` / `RTRW`) into clean uppercase/trimmed tokens using `calculate_field`.
2. Intersect existing parcels/land use with the statutory zoning plan:
   ```python
   run_processing("native:intersection", {
       "INPUT": "existing_landuse_utm",
       "OVERLAY": "rtrw_pola_ruang_utm",
       "INPUT_FIELDS": ["existing_class", "parcel_id"],
       "OVERLAY_FIELDS": ["zona_rtrw", "kode_zona", "peruntukan"]
   }, output_name="zoning_compliance_utm")
   ```
3. Compute exact metric area (`luas_ha = $area / 10000.0`) and classify compliance status (`status_kesesuaian`):
   ```python
   calculate_field("zoning_compliance_utm", "luas_ha", "$area / 10000.0", "double")
   calculate_field("zoning_compliance_utm", "status_kesesuaian",
       "CASE WHEN lower(\"existing_class\") LIKE '%hutan%' AND lower(\"zona_rtrw\") LIKE '%lindung%' THEN 'Sesuai (Konservasi)' "
       "WHEN lower(\"existing_class\") = lower(\"zona_rtrw\") THEN 'Sesuai' "
       "WHEN lower(\"zona_rtrw\") LIKE '%sempadan%' OR lower(\"zona_rtrw\") LIKE '%lindung%' THEN 'Indikasi Pelanggaran (Kawasan Lindung)' "
       "ELSE 'Perlu Evaluasi / Bersyarat' END",
       "string")
   ```

---

## 3. Urban Indicators: RTH (30% Target), KDB/BCR & TOD Service Catchments
- **Green Open Space Ratio (Ruang Terbuka Hijau / RTH):**
  Verify statutory urban target (UU 26/2007 / UU Cipta Kerja: **>= 30% RTH**, subdivided into **>= 20% RTH Publik** and **>= 10% RTH Privat**) by summing `luas_ha` per category vs. administrative area.
- **Setback / Riparian Buffers (Sempadan Sungai, Jalan, Rel KA):**
  Use `native:buffer` on linear rivers/roads/coastlines and flag encroaching building footprints (`Overture_Buildings` or municipal footprints) with `native:extractbylocation` (predicate `[0]` intersects).
- **TOD & Public Facility Catchment (Iso-Distance):**
  Use multi-ring buffers (`400m` primary walk, `800m` secondary walk) or `native:serviceareafromlayer` along the street network for transit stations, schools, and hospitals.

---

## 4. Standard RTRW / RDTR Symbology Palette
When styling **Pola Ruang** with `style_categorized`, follow canonical Indonesian/international planning color conventions (soft readable saturation so roads and labels stay legible above fills):
- **Kawasan Lindung / Hutan Lindung (`HL`)**: Deep forest olive-green `#2E6F40`
- **Sempadan Sungai / Pantai / Danau (`SS`/`SP`)**: Pale aqua-cyan `#9FE2BF` (or hatched cyan)
- **RTH / Taman Kota (`RTH`)**: Fresh emerald green `#4CAF50`
- **Pertanian / LP2B (`P1` / `LBS`)**: Warm chartreuse / pastoral yellow-green `#C5E1A5`
- **Permukiman Perkotaan / Perumahan (`R-1`..`R-3`)**: Soft warm amber/yellow `#FFE066` (high density -> deeper orange `#FFB74D`)
- **Perdagangan & Jasa / Komersial (`K`)**: Coral-rose red `#EF5350`
- **Industri & Pergudangan (`I`)**: Neutral muted grey-purple `#9575CD`
- **Perkantoran & Fasilitas Umum (`KT`/`SPU`)**: Slate violet `#7986CB` / cobalt `#5C6BC0`
- **Badan Air (`BA`)**: Clear azure blue `#81D4FA`

Apply thin borders (`0.15–0.25 mm`, slightly darker stroke) and `opacity=0.80` when draped over hillshade or basemap imagery; overlay **Struktur Ruang** (Arteri/Kolektor road hierarchy and Pusat Pelayanan PPK/SPK nodes) as crisp top-of-stack symbols.
