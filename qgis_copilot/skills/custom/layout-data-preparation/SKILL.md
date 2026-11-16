---
name: layout-data-preparation
description: Data acquisition, spatial processing, and cartographic styling recipes for specialized global map layout archetypes: Figure-Ground / Nolli Plan, Architectural CAD Blueprint, Small Multiples (2x2 time-series/scenario grid), Transit & Isochrone Accessibility Map, Editorial Vertical Briefing, and Retro Vignette Poster.
keywords: figure ground, nolli, schwarzplan, blueprint, cad, small multiples, multi panel, time series, transit, isochrone, accessibility, editorial vertical, retro poster, vignette, urban morphology
keywords_id: figure ground, nolli, blueprint, cetak biru, multi panel, deret waktu, transportasi, aksesibilitas, isochrone, editorial vertikal, poster retro
license: MIT
---

# Data Preparation & Styling Recipes for Global Layout Archetypes

Use this skill when preparing geospatial data and symbology for specialized global layout styles in `create_styled_layout(...)`. Always keep `fetch_all=True` / `paginate=True` so 100% of features inside the study `bbox` are retrieved, and match `bbox` aspect ratio to the target paper orientation.

---

## 1. Figure-Ground / Nolli Plan (`style_theme="figure_ground"`)
**Goal:** Pure binary solid-void urban morphology (black buildings on pure white ground).
1. **Data Needed:**
   - Building footprints only (no basemap, no labels, no POI dots).
   - `query_overture_maps(theme="buildings", bbox=..., fetch_all=True, output_name="Urban_Buildings")`
   - Optional: add `base/water` only if a river/canal shapes the urban void (`#e2e8f0` light grey).
2. **Styling:**
   - Reproject to local UTM (`set_project_crs`).
   - `style_single(layer="Urban_Buildings", color="#111111", outline_color="none", width=0)` — **critical**: zero outline stroke so dense footprints stay crisp at print scale.
   - Do NOT add `add_basemap(...)`.
3. **Layout:**
   - `create_styled_layout(name="Figure_Ground", style_theme="figure_ground", orientation="portrait", bbox=..., title="...", subtitle="...", credits="Data Sources: Overture Maps Foundation")`

---

## 2. Architectural & Engineering Blueprint (`style_theme="blueprint_technical"`)
**Goal:** Deep Prussian blue CAD drawing (`#0b2545`) with crisp white/cyan vector linework.
1. **Data Needed:**
   - Buildings, street network, and water/parcels:
     `query_overture_maps(theme="buildings", bbox=..., fetch_all=True)`
     `query_overture_maps(theme="transportation", bbox=..., fetch_all=True)`
     `query_overture_maps(theme="base", type="water", bbox=..., fetch_all=True)`
2. **Styling:**
   - Do NOT add `add_basemap(...)` (the `#0d2c54` blueprint canvas provides the background).
   - Buildings: `style_single(layer="...", color="rgba(56,189,248,0.12)", outline_color="#7dd3fc", width=0.18)`
   - Roads: `style_single(layer="...", color="#e0f2fe", width=0.35)` (or `style_categorized` with white/cyan `color_map`).
   - Water: `style_single(layer="...", color="rgba(14,165,233,0.22)", outline_color="#38bdf8", width=0.25)`
3. **Layout:**
   - `create_styled_layout(name="CAD_Blueprint", style_theme="blueprint_technical", orientation="landscape", bbox=..., title="...", subtitle="...", credits="Data Sources: Overture Maps Foundation")`

---

## 3. Small Multiples 2x2 Grid (`style_theme="small_multiples"`)
**Goal:** Compare 4 time slices, scenarios, or thematic indicators on a synchronized extent & scale.
1. **Data Needed:**
   - 4 distinct layers (or 4 filtered/classified layers) representing the 4 panels:
     * **Time-series imagery/land-cover:** `search_stac_items(collections=["sentinel-2-l2a"], bbox=..., datetime="...")` across 4 periods + `load_public_cog(...)` / `run_processing("gdal:...", ...)`
     * **Scenario/metric comparison:** 4 vector layers or 4 `native:extractbyexpression` / `native:buffer` outputs (e.g., 5/10/15/20-min coverage or Year 2010/2015/2020/2025).
2. **Styling:**
   - Apply a **consistent** color ramp (`style_graduated` or `style_raster` with identical class breaks) across all 4 layers so viewers can compare panels fairly.
3. **Layout:**
   - Pass `panel_titles=["2010", "2015", "2020", "2025"]` and `panel_layers=[["Lyr_2010"], ["Lyr_2015"], ["Lyr_2020"], ["Lyr_2025"]]` to:
     `create_styled_layout(name="Temporal_Grid", style_theme="small_multiples", orientation="landscape", bbox=..., panel_titles=[...], panel_layers=[...], title="...", subtitle="...", credits="...")`

---

## 4. Transit, Mobility & Isochrone Accessibility (`style_theme="transit_schematic"`)
**Goal:** Highlight transit lines, stations/stops, and catchment zones (5/10/15-min walking-sheds).
1. **Data Needed:**
   - Road & rail network: `query_overture_maps(theme="transportation", bbox=..., fetch_all=True, output_name="Transit_Network")`
   - Stations/stops: `query_overture_maps(theme="places", bbox=..., filter_sql="taxonomy.primary IN ('train_station','subway_station','bus_station','tram_stop','ferry_terminal')", fetch_all=True, output_name="Transit_Stations")`
   - **Generate Isochrones / Catchments** in projected UTM CRS (`set_project_crs`):
     * Prefer network service areas via `run_processing("native:serviceareafromlayer", {"INPUT": "Transit_Network", "START_POINTS": "Transit_Stations", "TRAVEL_COST": 400, ...})` (400m ≈ 5 min walk, 800m ≈ 10 min, 1200m ≈ 15 min), OR Euclidean walk-radius rings via `native:buffer` when network topology is incomplete.
2. **Styling:**
   - Place catchments at bottom (`opacity=0.35`, stepped teal/amber palette), muted context streets below, bold transit lines above (`#0284c7`, `#dc2626`), and stations on top (`size=2.6`, white halo).
3. **Layout:**
   - `create_styled_layout(name="Transit_Access", style_theme="transit_schematic", orientation="landscape", bbox=..., title="...", subtitle="...", credits="Data Sources: Overture Maps Foundation")`

---

## 5. Editorial Vertical & Retro Vignette Poster (`editorial_vertical`, `retro_poster`)
**Goal:** Publication-grade vertical storytelling or warm cream art print with edge gradient masking.
1. **Data Needed:**
   - Match `bbox` to Portrait A4 (`width / height ≈ 0.68–0.75`).
   - `retro_poster`: `buildings` + `transportation` + `base/water` (warm sepia roads `#4a3b2c` / `#8c7355`, cream-tan buildings `#e6dcc8`, soft sage river `#b5c9c3`, no POI clutter).
   - `editorial_vertical`: focal thematic layer + muted `esri_light` or Overture context, bold single-hue highlight, clean `set_labels` on key landmarks only.
2. **Layout:**
   - `create_styled_layout(style_theme="retro_poster", orientation="portrait", bbox=..., vignette=True, title="...", subtitle="...", credits="...")`
   - `create_styled_layout(style_theme="editorial_vertical", orientation="portrait", bbox=..., title="...", subtitle="...", credits="...")`
