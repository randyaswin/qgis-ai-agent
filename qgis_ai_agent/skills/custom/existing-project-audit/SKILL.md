---
name: existing-project-audit
description: Use when the user already has a QGIS project or layers loaded and wants them understood, cleaned, restyled, or combined into a map. Systematic read-only audit of CRS mismatches, broken sources, invalid geometries, NULLs, odd values, duplicate layers, and existing styling before any change.
keywords: existing project, proyek yang ada, layer yang sudah ada, audit, periksa, cek data, perbaiki, rapikan, restyle, ubah gaya, data quality, kualitas data, invalid geometry, CRS mismatch, broken link, merapikan peta
license: MIT
---

# Audit an existing project (read-only first)

1. `get_project_state` then `describe_all_layers`. Never edit anything yet.
2. Build a short findings table per layer:
   - **Valid?** `valid=false` means a broken path or unsupported format: report the source path.
   - **CRS:** list distinct CRSs. Mixed CRS is fine for display but not for overlay/measure; plan reprojection. A layer with an empty CRS is suspect.
   - **Extent sanity:** `extent_wgs84` outside the user's area suggests swapped axes, wrong CRS, or stray features.
   - **Geometry:** `empty_geometries`, `invalid_geometries_in_first_5000`, `has_z`. Plan `native:fixgeometries`.
   - **Attributes:** NULL counts, `unique_count`, numeric min/max (negative populations, 9999 sentinels, text in numeric columns, dates stored as text).
   - **Selection and filter:** an active subset filter or selection may explain missing features; surface it.
   - **Style:** renderer and, for categorized/graduated, the existing classes. Preserve it if the user did not ask to restyle (`load_style` can restore QML).
3. Check duplicates: same source appearing twice, near-identical names.
4. Report concisely: what is wrong, how severe, proposed fix. Ask before destructive actions.
5. Fix non-destructively: write results to new layers (`run_processing` with the default temporary output, `output_name` set), keep originals, and save into the output folder only on request. Never overwrite source files, never edit attributes in place unless asked.
6. After fixes, `describe_layer` the new layers to prove the issue is gone, then continue with the map task (styling skill, then `map-qa-and-export`).
