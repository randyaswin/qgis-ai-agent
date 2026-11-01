# QGIS AI Map Agent

Plugin QGIS untuk mengendalikan alur kerja pembuatan peta secara End-to-End (E2E) menggunakan AI CLI (`agy`, Claude Code, Gemini CLI, Codex) melalui Model Context Protocol (MCP).

```
QGIS Dock (Chat Panel)
     │
     ├── Injeksi Otomatis: System Prompt + Live State (Layers, CRS) + Relevant Skills
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

## Fitur Utama

1. **51 Tools MCP Lengkap**:
   - **Proyek & CRS**: `get_project_state`, `set_project_crs`, `new_project`, `open_project`, `save_project`, `zoom_to`.
   - **Inspeksi Data**: `describe_layer` (vektor, raster, mesh, point cloud, vector tile), `describe_all_layers`, `get_features`, `get_selected_features`, `field_stats`, `sample_raster`.
   - **Data Geospasial Publik Bebas Kunci (Overture, STAC, COG, DuckDB)**:
     - `list_public_geodata_sources`: Katalog sumber geodata terbuka (Overture, Earth Search, Copernicus DEM, ESA WorldCover).
     - `search_stac_items`: Pencarian item STAC (Earth Search Sentinel-2, Landsat, Copernicus DEM 30m) berdasarkan bounding box & waktu.
     - `load_public_cog`: Streaming raster Cloud-Optimized GeoTIFF (COG) via HTTP/S3 (`/vsicurl/`) tanpa unduh file raksasa ke disk lokal.
     - `query_overture_maps`: Akses Overture Maps Foundation (bangunan, tempat/POI, jalan, air) langsung dari S3 parquet via DuckDB spatial.
     - `query_duckdb_spatial`: Eksekusi query spasial DuckDB bebas pada GeoParquet, S3, atau file tabular remote.
   - **Basemap XYZ Bebas Kunci (100% Free & Keyless)**:
     - `add_basemap`: Preset siap pakai tanpa akun/API key: `carto_light` (Positron), `osm` (OpenStreetMap), `esri_satellite` (citra satelit resolusi tinggi), `carto_voyager`, `esri_topo`, `carto_dark`.
   - **Processing Toolbox & Geoprocessing**:
     - `list_processing_providers`: Daftar provider aktif (native, gdal, pdal, qgis, grass, model).
     - `list_algorithms`: Pencarian 440+ algoritma berdasarkan kata kunci atau provider.
     - `describe_algorithm`: Skema lengkap parameter input/output algoritma.
     - `run_processing`: Eksekusi algoritma dengan temporary output otomatis & penamaan layer.
     - `open_processing_toolbox`: Membuka dock panel Processing Toolbox langsung di antarmuka QGIS.
     - `open_processing_algorithm_dialog`: Membuka dialog GUI interaktif algoritma tertentu dengan parameter terisi.
     - `open_model_designer`: Membuka Graphical Model Designer untuk alur kerja visual.
   - **Kartografi & Simbologi**: `style_single`, `style_categorized`, `style_graduated`, `style_raster`, `load_style`, `list_color_ramps`, `set_labels`.
   - **Verifikasi Visual (Loop)**: `snapshot_canvas` (render PNG dikirim langsung ke agent untuk self-correction).
   - **Tata Letak & Desain Modern (Komposit / NatGeo / Relief / Infografis)**:
     - `create_layout`: Layout standar satu kanvas.
     - `create_styled_layout`: Layout tematik profesional (`infographic_bencana`, `natgeo`, `relief_topographic`, `modern_clean`).
     - `add_layout_inset_map`: Peta inset detail (zoom perkotaan) dengan kotak penunjuk lokasi (*overview rectangle*) di peta utama.
     - `add_layout_infographic_chart`: Kartu infografis diagram batang horizontal (HTML/CSS resolusi tinggi).
     - `add_layout_gradient_legend`: Bar gradien legenda horizontal kontinu (ketinggian, bahaya, heatmap).
     - `add_layout_callout_box`: Kartu callout cluster (A, B, C, D, E) dengan garis penunjuk ke titik peta.
     - `edit_layout`, `export_layout` (PDF/PNG/SVG + preview), `open_layout_designer`.
   - **Skills Discovery & Loading**: `list_skills`, `load_skill`.
   - **Sandbox Escape Hatch**: `exec_pyqgis` (eksekusi kode PyQGIS bebas; dinonaktifkan secara default).

2. **Dukungan 50 GIS Skills Berstandar `SKILL.md`**:
   - **16 Custom Use Case Skills**:
     - `public-cog-stac-duckdb`: Panduan query data Overture Maps Foundation (bangunan, tempat, jalan) via DuckDB S3 GeoParquet, pencarian katalog STAC terbuka, dan streaming raster COG tanpa API key.
     - `disaster-infographic-dashboard`: Panduan arsitektur layout infografis situasi bencana (peta utama + inset detail + KDE heatmap + buffer 10 km + callout kluster + diagram batang + bar gradien).
     - `natgeo-cartography-style`: Panduan kartografi gaya National Geographic (kanvas perkamen hangat, border neatline ganda, tipografi serif klasik, palet bumi halus).
     - `swiss-relief-cartography`: Panduan relief topografi gaya Swiss / Imhof (pencahayaan NW 315°, bayangan sejuk, hierarki garis kontur, strip gradien elevasi).
     - `qgis-processing-toolbox`: Panduan komprehensif penemuan & otomatisasi 440+ algoritma geoprocessing QGIS, pemanggilan dialog GUI, dan modeler visual.
     - `choropleth-density-map`: Peta tematik rasio/kepadatan & normalisasi data.
     - `indonesia-geodata-crs`: Database CRS lokal Indonesia (UTM 46–54, SRGI2013, DGN95), kaidah kode wilayah BPS/Kemendagri.
     - `terrain-relief-map`: DEM, kontur, kemiringan lereng, aspek, dan hillshade.
     - `flood-susceptibility-map`: Analisis kerawanan banjir dengan weighted overlay.
     - `proximity-service-coverage-map`: Analisis jangkauan fasilitas, buffer, dan gap wilayah.
     - `point-density-heatmap`: Kernel density heatmap vs grid hexbin untuk sebaran titik.
     - `landcover-area-statistics`: Statistik luas tutupan lahan & overlay per wilayah.
     - `satellite-index-map`: Indeks spektral Sentinel-2 / Landsat (NDVI, NDWI, NBR).
     - `existing-project-audit`: Audit komprehensif data dan layer yang sudah ada sebelum diubah.
     - `map-qa-and-export`: Checklist kartografi, kontras warna, dan ekspor resolusi tinggi.
     - `peta-bahasa-indonesia`: Kaidah tata bahasa, istilah baku, dan format angka Indonesia.
   - **34 Vendored Skills** (Open-source MIT):
     - `geoai-skills` (terrain-hydrology, point-cloud-lidar, geostatistics-interpolation, remote-sensing-analysis, postgis-spatial-sql, change-detection, mcda-suitability-analysis, movement-trajectory, spatial-statistics, dll).
     - `qgis-claude-skill-package` (print-layouts, raster-analysis, vector-analysis, network-analysis, 3d-visualization, coordinate-systems, expressions, dll).
     - `openmapstack-skills` (spatial-sql, geospatial-data-discovery, open-map-stack).

3. **Manajemen Skills (Otomatis & Manual)**:
   - **Auto (Smart Select)**: Sistem otomatis menganalisis prompt pengguna (pencocokan kata kunci, sinonim dwibahasa, dan pembobotan TF-IDF) untuk menginjeksi skill yang paling relevan.
   - **Manual (Pinned Only)**: Pengguna dapat memilih dan mengunci (*Pin*) skill tertentu melalui dialog **Skills Manager**.
   - **Pasang Skill Baru**: Pengguna dapat memasang skill baru dari folder lokal atau repositori Git langsung lewat GUI.

4. **Injeksi Konteks Proyek & System Prompt**:
   - Setiap prompt baru secara otomatis dilengkapi instruksi operasional ([AGENT.md](qgis_ai_agent/AGENT.md)), ringkasan kondisi layer & kanvas saat ini, serta panduan skill yang terpilih.

---

## Panduan Penggunaan

1. **Buka QGIS** (Versi 3.34+ LTS / 3.40+).
2. Di menu bar: **Plugins** → **Manage and Install Plugins...** → tab **Installed** → centang **AI Map Agent**.
3. Panel **AI Map Agent** akan muncul di sisi kanan:
   - Pilih AI CLI yang terpasang (`agy`, `Claude Code`, `Gemini CLI`, atau `Codex`).
   - Klik **Register MCP** (cukup sekali di awal).
   - Atur mode skill (`Auto`, `Manual`, atau `Off`).
   - Ketik perintah Anda di kotak prompt (misal: *"Buat peta kepadatan penduduk kecamatan di Jawa Barat, gunakan klasifikasi jenks 5 kelas dan ekspor ke layout PDF A3"*).
   - Tekan **Ctrl+Enter** atau klik **▶ Run**.
