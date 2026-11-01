---
name: qgis-processing-toolbox
description: Use when discovering, configuring, automating, or visually interacting with the QGIS Processing Toolbox (440+ algorithms across Native C++, GDAL, GRASS, PDAL, QGIS, and custom models). Covers discovering algorithm IDs, inspecting parameters, automated execution, opening algorithm dialogs, opening the toolbox panel, and the graphical model designer.
keywords: processing, toolbox, geoprocessing, algoritma, native, gdal, pdal, grass, buffer, clip, intersection, dissolve, contour, hillshade, slope, zonal statistics, raster calculator, shortest path, heatmap, modeler, model designer, alur kerja spasial
license: MIT
---

# QGIS Processing Toolbox & Automation

QGIS Processing Framework menyediakan lebih dari 440 algoritma geoprocessing native, GDAL, PDAL, dan provider lainnya. Seluruh alat ini dapat diakses secara otomatis maupun interaktif.

## 1. Menemukan Algoritma yang Tepat
Jangan menebak ID algoritma secara membabi-buta:
1. `list_processing_providers()`: Melihat semua provider aktif (misal `native`, `gdal`, `pdal`, `qgis`, `model`).
2. `list_algorithms(query="...", provider="...")`:
   - Contoh query pencarian: `"buffer"`, `"clip"`, `"contour"`, `"slope"`, `"raster"`, `"join"`, `"heatmap"`, `"statistics"`.
3. `describe_algorithm(algorithm)`:
   - **Wajib dipanggil** sebelum menjalankan algoritma baru untuk memverifikasi nama persis parameter masukan, tipe data, nilai default, dan parameter keluaran (`sink`).

## 2. Parameter Kunci & Aturan Eksekusi (`run_processing`)
- **Layer Input:** Dapat berupa nama layer (misal `"kecamatan"`) atau layer ID.
- **Keluaran Otomatis:** Parameter `OUTPUT` secara default menggunakan `"TEMPORARY_OUTPUT"` (membuat scratch memory layer instan yang aman dan cepat tanpa mengotori disk).
- **Penamaan Hasil:** Gunakan parameter `output_name="Nama Layer Hasil"` agar layer yang dimuat ke kanvas memiliki nama yang jelas bagi pengguna.
- **Konsistensi Proyeksi (CRS):**
  - Untuk operasi berbasis jarak fisik atau luas (buffer, area, kernel density), pastikan layer berproyeksi metrik lokal (misal UTM / WGS 84 Zona 48S). Jika layer masih dalam EPSG:4326 (derajat), lakukan reproyeksi terlebih dahulu dengan `native:reprojectlayer`.

## 3. Interaksi Antarmuka Pengguna (GUI)
Jika pengguna meminta untuk melihat panel alat atau ingin mengisi formulir secara visual:
- `open_processing_toolbox()`: Menampilkan atau mengaktifkan dock panel Processing Toolbox di jendela utama QGIS.
- `open_processing_algorithm_dialog(algorithm, params)`: Membuka dialog antarmuka interaktif algoritma tertentu langsung di layar pengguna dengan parameter awal yang sudah terisi.
- `open_model_designer()`: Membuka jendela QGIS Graphical Model Designer jika pengguna ingin merancang rantai pemrosesan visual (*graphical model* `.model3`).

## 4. Daftar Algoritma Sering Digunakan
| Kategori | Algoritma | ID Algoritma | Parameter Utama |
| :--- | :--- | :--- | :--- |
| Buffer | Buffer Vektor | `native:buffer` | `INPUT`, `DISTANCE`, `SEGMENTS`, `DISSOLVE` |
| Pemotongan | Clip Vektor | `native:clip` | `INPUT`, `OVERLAY` |
| Irisan | Intersection | `native:intersection` | `INPUT`, `OVERLAY` |
| Peleburan | Dissolve | `native:dissolve` | `INPUT`, `FIELD` |
| Perbaikan | Fix Geometries | `native:fixgeometries` | `INPUT` |
| Kontur | Garis Kontur | `gdal:contour` | `INPUT`, `BAND`, `INTERVAL`, `FIELD_NAME` |
| Shading | Relief Bayangan | `native:hillshade` | `INPUT`, `Z_FACTOR`, `AZIMUTH`, `V_ANGLE` |
| Lereng | Kemiringan Lereng | `native:slope` | `INPUT`, `Z_FACTOR` |
| Statistik | Zonal Statistics | `native:zonalstatisticsfb` | `INPUT`, `INPUT_RASTER`, `RASTER_BAND`, `STATISTICS` |
| Kepadatan | Heatmap (KDE) | `qgis:heatmapkerneldensityestimation` | `INPUT`, `RADIUS`, `PIXEL_SIZE`, `KERNEL` |
| Rute | Shortest Path | `native:shortestpathpointtopoint` | `INPUT`, `START_POINT`, `END_POINT`, `STRATEGY` |
| Jangkauan | Service Area | `native:serviceareafrompoint` | `INPUT`, `START_POINT`, `TRAVEL_COST` |
