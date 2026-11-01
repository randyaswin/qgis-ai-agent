# QGIS AI Map Agent

A full-featured QGIS plugin to control End-to-End (E2E) cartography and geospatial workflows using AI CLIs (`agy`, Claude Code, Gemini CLI, Codex) through the Model Context Protocol (MCP).

```
QGIS Dock (Chat Panel)
     │
     ├── Automatic Injection: System Prompt + Live State (Layers, CRS) + Relevant Skills
     ▼
QProcess (CLI: agy / claude / gemini / codex)
     │
     ├── stdio MCP
     ▼
mcp_bridge.py
     │
     ├── Local TCP Socket + Token (~/.qgis_ai_agent/endpoint.json)
     ▼
ToolServer (QGIS Main Thread) ──▶ tools.py (51 Tools)
```

---

## Key Features

1. **51 Comprehensive MCP Tools**:
   - **Project & CRS**: `get_project_state`, `set_project_crs`, `new_project`, `open_project`, `save_project`, `zoom_to`.
   - **Deep Data Inspection**: `describe_layer` (vector, raster, mesh, point cloud, vector tile), `describe_all_layers`, `get_features`, `get_selected_features`, `field_stats`, `sample_raster`.
   - **Public Open Geospatial Data (Overture, STAC, COG, DuckDB)**:
     - `list_public_geodata_sources`: Curated catalog of 100% free, keyless open geodata (Overture Maps S3, Earth Search, Copernicus DEM, ESA WorldCover).
     - `search_stac_items`: Search public STAC catalogs (Earth Search Sentinel-2, Landsat, Copernicus DEM 30m) matching canvas bbox and date.
     - `load_public_cog`: Stream Cloud-Optimized GeoTIFFs (COGs) via HTTP/S3 (`/vsicurl/`) on the fly without downloading massive files to disk.
     - `query_overture_maps`: Directly query global Overture Maps Foundation features (buildings, places/POI, transportation, water) from S3 parquet using DuckDB spatial.
     - `query_duckdb_spatial`: Execute arbitrary spatial DuckDB SQL queries on remote GeoParquet, S3, or tabular sources into QGIS layers.
   - **100% Free & Keyless XYZ Tile Basemaps**:
     - `add_basemap`: Instant presets without API keys or accounts: `carto_light` (CartoDB Positron, default for thematic maps), `osm` (OpenStreetMap), `esri_satellite` (high-res aerial imagery), `carto_voyager`, `esri_topo`, `carto_dark`.
   - **Processing Toolbox & Geoprocessing**:
     - `list_processing_providers`: List active processing providers (native, gdal, pdal, qgis, grass, model).
     - `list_algorithms`: Search 440+ algorithms by keyword or provider ID.
     - `describe_algorithm`: Full parameter and output sink schema documentation.
     - `run_processing`: Execute algorithms with automated temporary outputs and clean layer naming.
     - `open_processing_toolbox`: Open the QGIS Processing Toolbox dock in the main interface.
     - `open_processing_algorithm_dialog`: Open interactive parameter GUI for any algorithm.
     - `open_model_designer`: Launch the QGIS Graphical Model Designer for visual pipelines.
   - **Cartography & Symbology**: `style_single`, `style_categorized`, `style_graduated`, `style_raster`, `load_style`, `list_color_ramps`, `set_labels`.
   - **Visual Verification Loop**: `snapshot_canvas` (renders PNG image back to agent context for self-correction).
   - **Modern Layouts & Themed Styles (Composite / NatGeo / Relief / Infographics)**:
     - `create_layout`: Clean single-frame print layout.
     - `create_styled_layout`: Themed publication layouts (`infographic_disaster`, `natgeo`, `relief_topographic`, `modern_clean`).
     - `add_layout_inset_map`: Inset detail map (urban zoom) with linked overview rectangle on the main map.
     - `add_layout_infographic_chart`: Infographic horizontal ranking bar chart (high-res HTML/CSS).
     - `add_layout_gradient_legend`: Horizontal continuous gradient color bar (elevation, hazard, heatmap).
     - `add_layout_callout_box`: Cluster callout cards (A, B, C, D, E) with pointer lines to map coordinates.
     - `edit_layout`, `export_layout` (PDF/PNG/SVG + preview), `open_layout_designer`.
   - **Skills Discovery & Loading**: `list_skills`, `load_skill`.
   - **Sandbox Escape Hatch**: `exec_pyqgis` (arbitrary PyQGIS execution; disabled by default).

2. **Support for 50 Standard `SKILL.md` GIS Skills**:
   - **16 Custom Specialized Skills**:
     - `public-cog-stac-duckdb`: Query Overture Maps Foundation via DuckDB S3 GeoParquet, search public STAC catalogs, and stream COGs with zero API keys.
     - `disaster-infographic-dashboard`: Comprehensive disaster & hazard situational map architecture (main map + inset + KDE heatmap + 10km buffer cluster + callouts + bar chart + gradient bars).
     - `natgeo-cartography-style`: National Geographic classic style (warm parchment canvas, double neatline border, classic serif typography, muted earth tones).
     - `swiss-relief-cartography`: Eduard Imhof & Swiss Topo style (NW 315° lighting, cool shadows, contour hierarchy, elevation gradient strip).
     - `qgis-processing-toolbox`: Discovery and automation of 440+ algorithms across Native C++, GDAL, GRASS, PDAL, and visual modelers.
     - `choropleth-density-map`: Thematic ratio/density normalization and Jenks/quantile classification.
     - `indonesia-geodata-crs`: Indonesian coordinate systems (UTM 46–54, SRGI2013, DGN95), BPS and Kemendagri boundary standards.
     - `terrain-relief-map`: DEM, contour generation, slope, aspect, and hillshade shading.
     - `flood-susceptibility-map`: Multi-criteria weighted overlay for flood and landslide hazard analysis.
     - `proximity-service-coverage-map`: Facility service areas, distance buffers, and accessibility gaps.
     - `point-density-heatmap`: Kernel Density Estimation (KDE) vs hexbin aggregation.
     - `landcover-area-statistics`: Land cover area breakdown and regional zonal intersection.
     - `satellite-index-map`: Spectral index calculation (NDVI, NDWI, NBR) from Sentinel-2 / Landsat.
     - `existing-project-audit`: Comprehensive audit of existing project layers before making changes.
     - `map-qa-and-export`: Cartographic review checklist, contrast validation, and print-ready export.
     - `peta-bahasa-indonesia`: Guidelines and terminology conventions when producing Indonesian-language maps.
   - **34 Vendored Open-Source Skills** (MIT License):
     - `geoai-skills` (terrain-hydrology, point-cloud-lidar, geostatistics-interpolation, remote-sensing-analysis, postgis-spatial-sql, change-detection, mcda-suitability-analysis, movement-trajectory, spatial-statistics, etc.).
     - `qgis-claude-skill-package` (print-layouts, raster-analysis, vector-analysis, network-analysis, 3d-visualization, coordinate-systems, expressions, etc.).
     - `openmapstack-skills` (spatial-sql, geospatial-data-discovery, open-map-stack).

3. **Intelligent & Manual Skills Management**:
   - **Auto (Smart Select)**: Analyzes prompt keywords, bilingual synonyms, and TF-IDF scores to dynamically inject the most relevant skills into the context.
   - **Manual (Pinned Only)**: Select and pin specific skills via the **Skills Manager** dialog.
   - **Install New Skills**: Install skills from local folders or Git repositories directly from the GUI.

4. **Dynamic Context & System Prompt Injection**:
   - Every prompt is automatically augmented with operational instructions ([AGENT.md](qgis_ai_agent/AGENT.md)), live layer schemas, active CRS, and relevant skills.
   - Bi-directional chat synchronization renders CLI thoughts, tool calls, results, and inline map previews directly inside the QGIS dock.

---

## Getting Started

1. **Open QGIS** (Version 3.34+ LTS or 3.40+).
2. Go to **Plugins** → **Manage and Install Plugins...** → **Installed** tab → check **AI Map Agent**.
3. The **AI Map Agent** panel will appear on the right side:
   - Select your installed AI CLI (`agy`, `Claude Code`, `Gemini CLI`, or `Codex`).
   - Click **Register MCP** (only needed once).
   - Choose model and reasoning effort (e.g. `High` / `Max`).
   - Set skill mode (`Auto`, `Manual`, or `Off`).
   - Enter your map prompt (e.g.: *"Query Overture buildings in Bali, compute elevation relief from Copernicus DEM, and build a situational disaster infographic layout"*).
   - Press **Ctrl+Enter** or click **▶ Run**.
