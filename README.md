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
ToolServer (QGIS Main Thread) ──▶ tools.py (37 Tools)
```

---

## Fitur Utama

1. **37 Tools MCP Lengkap**:
   - **Proyek & CRS**: `get_project_state`, `set_project_crs`, `new_project`, `open_project`, `save_project`, `zoom_to`.
   - **Inspeksi Data**: `describe_layer` (vektor, raster, mesh, point cloud, vector tile), `describe_all_layers`, `get_features`, `get_selected_features`, `field_stats`, `sample_raster`.
   - **Manipulasi & Processing**: `calculate_field`, `set_layer_filter`, `list_algorithms`, `describe_algorithm`, `run_processing`.
   - **Kartografi & Simbologi**: `style_single`, `style_categorized`, `style_graduated`, `style_raster`, `load_style`, `list_color_ramps`, `set_labels`.
   - **Verifikasi Visual (Loop)**: `snapshot_canvas` (render PNG dikirim langsung ke agent untuk self-correction).
   - **Tata Letak & Ekspor**: `create_layout` (judul, legenda, skala, arah utara, grid, kredit sumber), `edit_layout`, `export_layout` (PDF/PNG/SVG + preview), `open_layout_designer`.
   - **Skills Discovery & Loading**: `list_skills`, `load_skill`.
   - **Sandbox Escape Hatch**: `exec_pyqgis` (eksekusi kode PyQGIS bebas; dinonaktifkan secara default).

2. **Dukungan 45+ GIS Skills Berstandar `SKILL.md`**:
   - **11 Custom Use Case Skills**:
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
