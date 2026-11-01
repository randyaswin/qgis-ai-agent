---
name: map-qa-and-export
description: Use as the final stage of every map: layout composition checklist, visual QA via snapshots, accessibility, scale and legend correctness, export formats and DPI, and what to report. Apply before telling the user a map is done.
keywords: layout, export, PDF, PNG, legend, scale bar, north arrow, QA, checklist, cetak, ekspor, finalisasi, kualitas peta, A4, A3, DPI, judul, legenda, skala, sumber data, atlas
license: MIT
---

# Final QA and export

## Layout checklist (`create_layout`, then fix with `edit_layout`)
- Title states what, where, when. Subtitle states the metric/unit/method (e.g. "Kepadatan penduduk (jiwa/km²), klasifikasi Natural Breaks, 2023").
- Map frame fits the area with margin (`extent_layer`); no large empty areas; study area not clipped.
- Legend: only meaningful layers (`legend_layers`), readable labels, units, no raw field names, no leftover "Layer 1".
- Scale bar and north arrow present; omit the scale bar on small-scale (country+) maps in geographic display and say the projection instead.
- Credits: data sources, year, author/organisation, CRS, and "dibuat dengan QGIS".
- Page: A4 landscape default, A3 for many labels; portrait for elongated areas.

## Visual QA loop
1. `export_layout(preview=true)` and look at the returned image. Check against the list: overlapping labels, unreadable text on dark fills, legend overflow, wrong class colours, missing layers, tiny symbols.
2. Fix, re-export, re-check. At most three iterations; list remaining flaws honestly if any.
3. Accessibility: avoid red/green only; keep contrast of text over fills (halo via `set_labels buffer_size`); symbols distinguishable when printed grey.

## Honesty checks
- Densities/rates normalised? Equal-area CRS used for measures? Class method stated?
- No classes with zero features in the legend; NULL shown as "Tidak ada data".
- Numbers in the summary match the data (re-query with `get_features`/`field_stats`, do not recall from memory).

## Export
- PDF at 300 dpi for print; PNG 200 to 300 dpi for slides/docs; SVG when the user wants to edit in Inkscape/Illustrator.
- Filename: `<topic>_<area>_<yyyy>.pdf` into the output folder, plus `save_project` as `.qgz` next to it.
- Final answer: output paths, one line per data source, assumptions made, known limitations. No marketing language.
