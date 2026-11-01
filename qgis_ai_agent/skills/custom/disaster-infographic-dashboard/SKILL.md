---
name: disaster-infographic-dashboard
description: Use when building comprehensive disaster, hazard, or incident infographic maps (like BNPB / BPBD / BMKG situational dashboards). Covers combining relief DEM, multi-category incident symbols, KDE heatmap density, 10km cluster buffer zones, capital city inset map, callout cards A-B-C-D-E, horizontal bar chart ranking, and continuous gradient legend bars.
keywords: disaster, bencana, infografis, dashboard, bnpb, bpbd, bali, banjir, longsor, kebakaran, gempa, kejadian, callout, cluster, inset, denpasar, diagram batang, bar chart, heatmap, relief, peta komposit
license: MIT
---

# Peta Infografis & Situational Disaster Dashboard

Panduan komprehensif membuat peta infografis bencana setara standar BNPB/BPBD (seperti Peta Kejadian Bencana Provinsi Bali):

## 1. Persiapan Data & Geometri
1. **Sistem Proyeksi (CRS) & Peta Dasar (Basemap):**
   - Wajib gunakan proyeksi metrik UTM lokal (misal Bali = EPSG:32750 / UTM Zona 50S; Jawa = Zona 48S/49S). Panggil `set_project_crs` sebelum menghitung buffer atau KDE.
   - Peta dasar wajib menggunakan XYZ tile tanpa API key: panggil `add_basemap(preset="carto_light")` (Carto Positron berona abu-abu lembut agar titik tematik menonjol) atau `add_basemap(preset="esri_satellite")` jika membutuhkan citra satelit tanpa kunci. Atur transparansi dengan `update_layer(opacity=0.4)`.
2. **Relief Medan (DEM):**
   - Turunkan hillshade dari DEM (`native:hillshade`, Azimuth 315°, V_Angle 45°).
   - Terapkan simbologi pseudocolor elevasi halus (`style_raster` dengan ramp `Spectral` atau `YlOrBr` dibalik), lalu letakkan hillshade di atasnya dengan transparansi 45-50% (`update_layer opacity=0.5`).
3. **Titik Kejadian Bencana (Multi-simbol):**
   - Terapkan `style_categorized` berdasarkan kolom jenis bencana dengan bentuk simbol standar kartografi bencana:
     - **Banjir**: Lingkaran biru (`#1976d2`)
     - **Tanah Longsor**: Segitiga cokelat/merah (`#d32f2f`)
     - **Kebakaran Hutan/Lahan**: Belah ketupat oranye (`#f57c00`)
     - **Kebakaran Gedung/Permukiman**: Kotak merah bata (`#c2185b`)
     - **Cuaca Ekstrem / Angin**: Bintang ungu (`#7b1fa2`)
     - **Kekeringan**: Lingkaran kuning/emas (`#fbc02d`)
4. **Heatmap Kepadatan (KDE):**
   - Jalankan `qgis:heatmapkerneldensityestimation` dengan radius pencarian 10 km (10.000 meter) dan ukuran piksel 100 m.
   - Beri simbologi `style_raster` dengan ramp `YlOrRd` (kuning ke merah tua) dan transparansi bawah agar peta dasar tetap terlihat.
5. **Zonasi Lingkaran Cluster (10 km):**
   - Lakukan agregasi titik atau pilih titik pusat kluster utama, lalu buat buffer 10 km (`native:buffer` jarak 10.000 m).
   - Beri style garis lingkar tipis putih/merah tanpa isian (`style_single` dengan outline 0.8 mm dan opacity 0.7).

## 2. Penyusunan Layout Infografis Komposit
Gunakan tool layout modern bertahap:
1. **Inisialisasi Template Peta:**
   - Panggil `create_styled_layout(name="Peta Bencana", style_theme="infographic_bencana", title="PETA KEJADIAN BENCANA PROVINSI BALI", subtitle="OKTOBER 2026", page_size="A3", orientation="landscape", grid=True)`.
   - Tool ini otomatis membuat frame peta utama bergrid derajat/menit, skala, arah utara, dan ruang khusus untuk inset dan diagram.
2. **Peta Inset Detail (Ibu Kota / Titik Kritis):**
   - Panggil `add_layout_inset_map(layout="Peta Bencana", x=12, y=60, width=80, height=85, extent_layer="Denpasar", title="DETAIL KOTA DENPASAR", show_overview_on_main=True, grid=True)`.
   - Inset map otomatis memiliki skala sendiri dan memunculkan kotak penunjuk lokasi (*overview rectangle*) di peta utama.
3. **Kartu Callout Cluster (A, B, C, D, E):**
   - Panggil `add_layout_callout_box` untuk kluster-kluster kritis:
     - `badge="A"`, `title="SAMBANGAN · BULELENG"`, `subtitle="18 kejadian · radius 10 km"`.
     - `badge="B"`, `title="TULIKUP DAN SEKITARNYA"`, `subtitle="18 kejadian · radius 10 km"`.
     - `badge="C"`, `title="PEMECUTAN KAJA · DENPASAR"`, `subtitle="15 kejadian · radius 10 km"`.
4. **Legenda Bar Gradien Horizontal:**
   - Panggil `add_layout_gradient_legend` untuk variabel kontinu di bagian bawah peta:
     - Ketinggian: `title="Ketinggian (m)"`, `ramp="Spectral"`, `min_val="-3"`, `max_val="2.945"`, `invert_ramp=True`.
     - Bahaya Banjir: `title="Indeks bahaya banjir"`, `ramp="Blues"`, `min_val="0 Rendah"`, `max_val="1 Tinggi"`.
     - Kepadatan: `title="Kepadatan kejadian"`, `ramp="YlOrRd"`, `min_val="Rendah"`, `max_val="Tinggi"`.
5. **Kartu Infografis Diagram Batang:**
   - Kumpulkan jumlah kejadian per kabupaten (bisa dari `qgis:statisticsbycategories` atau agregasi).
   - Panggil `add_layout_infographic_chart(layout="Peta Bencana", x=325, y=200, width=85, height=85, title="KEJADIAN PER KABUPATEN/KOTA", subtitle="Oktober 2026 · total 143 kejadian", data=[{"label":"Buleleng","value":36}, {"label":"Karangasem","value":27}, ...], bar_color="#2b5c8f")`.

## 3. Verifikasi & Ekspor
1. Panggil `export_layout(name="Peta Bencana", path=".../peta_bencana_bali.png", preview=True)` untuk menginspeksi visual peta langsung di chat.
2. Periksa keselarasan teks, keterbacaan label, dan kontras warna.
3. Ekspor versi cetak resolusi tinggi: `export_layout(name="Peta Bencana", path=".../peta_bencana_bali.pdf", dpi=300)`.
