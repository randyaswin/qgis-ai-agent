---
name: public-cog-stac-duckdb
description: Use when discovering, querying, streaming, or analyzing public geospatial datasets without API keys — including Overture Maps Foundation (buildings, places, transportation, water) via DuckDB over remote S3 GeoParquet, public STAC catalogs (Earth Search Sentinel-2, Landsat, Copernicus DEM GLO-30), and Cloud-Optimized GeoTIFFs (COGs) via GDAL /vsicurl/.
keywords: cog, stac, overture, duckdb, geoparquet, parquet, s3, sentinel, copernicus, dem, glo-30, earth search, planetary computer, open data, data publik, citra satelit, bangunan, poi, jalan, kontur publik
license: MIT
---

# Public Open Geospatial Data: Overture Maps, STAC & COG via DuckDB

Keahlian ini memandu pemanfaatan data geospasial terbuka berskala global secara instan, efisien, dan **100% bebas API key**.

---

## 1. Arsitektur Akses Data Publik Bebas Kunci

1. **Overture Maps Foundation (Meta, Microsoft, Amazon, TomTom):**
   - Data vektor global terlengkap (miliaran bangunan, titik minat/POI, jalan, batas administratif).
   - Diakses langsung dari *public S3 bucket* (`s3://overturemaps-us-west-2/release/...`) dalam format GeoParquet berpartisi spasial.
   - Dieksekusi secara instan menggunakan mesin spasial internal **DuckDB** (`spatial` + `httpfs`), hanya mengunduh byte yang berada di dalam *bounding box* kanvas aktif tanpa mengunduh seluruh dataset.
2. **SpatioTemporal Asset Catalogs (STAC APIs):**
   - Menggunakan endpoint publik terbuka seperti **Earth Search by Element84** (`https://earth-search.aws.element84.com/v1`) atau Planetary Computer.
   - Menelusuri metadata citra satelit optik (Sentinel-2 L2A), radar (Sentinel-1), dan elevasi global (Copernicus DEM 30m).
3. **Cloud-Optimized GeoTIFF (COG) Streaming:**
   - QGIS memuat raster citra dan elevasi resolusi tinggi langsung melalui protokol virtual GDAL `/vsicurl/https://...`.
   - Hanya membaca rentang byte *tiles* dan *overview pyramiding* yang sedang ditampilkan di layar QGIS. Tidak memerlukan unduhan file gigabyte ke disk lokal.

---

## 2. Katalog & Pencarian Sumber Data Publik

Sebelum memuat data, periksa katalog sumber publik yang tersedia:
```python
sources = list_public_geodata_sources()
```
Menampilkan ringkasan koleksi STAC, tema Overture Maps, dan URL COG publik siap pakai.

---

## 3. Mengakses Vektor Overture Maps via DuckDB

Gunakan fungsi `query_overture_maps`:

### A. Parameter Kunci:
- `theme`: `"places"` | `"buildings"` | `"transportation"` | `"base"` | `"divisions"`.
- `type`: Subtipe (otomatis dipilih jika dikosongkan, misal `"place"`, `"building"`, `"segment"`, `"water"`).
- `bbox`: `[xmin, ymin, xmax, ymax]` dalam WGS84 EPSG:4326. Jika `None`, otomatis menggunakan koordinat cakupan kanvas QGIS saat ini.
- `filter_sql`: Kondisi SQL DuckDB opsional untuk menyaring atribut.
- `limit`: Batas jumlah fitur (default 500, dapat dinaikkan sesuai kebutuhan analisis).
- `output_name`: Nama layer yang ditampilkan di panel Layers QGIS.

### B. Contoh Kasus Penggunaan:
1. **Memuat Fasilitas Kesehatan / Hotel / Restoran di Wilayah Kanvas:**
   ```python
   query_overture_maps(
       theme="places",
       filter_sql="taxonomy.primary IN ('hospital', 'clinic', 'pharmacy')",
       limit=300,
       output_name="Fasilitas_Kesehatan_Overture"
   )
   ```
2. **Memuat Jejak Bangunan (Building Footprints) & Estimasi Ketinggian:**
   ```python
   query_overture_maps(
       theme="buildings",
       filter_sql="height IS NOT NULL OR num_floors > 2",
       limit=1000,
       output_name="Gedung_Tinggi_Overture"
   )
   ```
3. **Memuat Jaringan Jalan & Jalur Transportasi:**
   ```python
   query_overture_maps(
       theme="transportation",
       filter_sql="class IN ('primary', 'secondary', 'tertiary')",
       limit=800,
       output_name="Jalan_Utama_Overture"
   )
   ```
4. **Memuat Badan Air & Pesisir:**
   ```python
   query_overture_maps(
       theme="base",
       type="water",
       limit=500,
       output_name="Badan_Air_Overture"
   )
   ```

---

## 4. Pencarian STAC & Streaming COG Satelit/Elevasi

### A. Mencari Item Citra Satelit atau DEM
Gunakan `search_stac_items`:
```python
items = search_stac_items(
    collections=["cop-dem-glo-30"],     # Elevasi global 30m
    # atau collections=["sentinel-2-l2a"] # Citra optik bebas awan
    limit=2
)
```
Fungsi ini mengembalikan daftar item STAC lengkap dengan URL aset COG langsung (`data` untuk DEM, `visual` untuk komposit RGB Sentinel-2, `red`, `nir`, dll.).

### B. Memuat COG Langsung ke Kanvas QGIS
Gunakan `load_public_cog`:
```python
load_public_cog(
    url="https://copernicus-dem-30m.s3.amazonaws.com/.../DEM.tif",
    name="Copernicus_DEM_30m"
)
```
QGIS secara transparan menambahkan prefiks `/vsicurl/` dan mengalirkan data raster secara instan.

---

## 5. Query SQL Bebas dengan DuckDB Spatial

Untuk query analisis spasial mutakhir yang menggabungkan file parquet eksternal di internet atau S3:
```python
query_duckdb_spatial(
    query="""
        SELECT id, names.primary AS nama, geometry 
        FROM read_parquet('s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*', hive_partitioning=1)
        WHERE bbox.xmin >= 115.10 AND bbox.xmax <= 115.30
          AND bbox.ymin >= -8.75 AND bbox.ymax <= -8.55
          AND taxonomy.primary = 'hotel'
        LIMIT 200
    """,
    layer_name="Hotel_Denpasar_DuckDB"
)
```
Hasil query otomatis dikonversi menjadi GeoJSON sementara dan dimuat ke QGIS sebagai layer vektor asli yang dapat di-style, dianalisis, dan dijadikan input algoritma processing.

---

## 6. Pola Alur Kerja Analisis Terpadu

1. **Pengayaan Peta Tematik / Bencana:**
   - Muat data kejadian bencana atau batas wilayah.
   - Gunakan `query_overture_maps` untuk menarik gedung dan fasilitas kritis (rumah sakit, sekolah) yang terdampak di dalam radius bahaya.
2. **Pembuatan Relief & Kemiringan Lereng:**
   - Cari dan muat `cop-dem-glo-30` menggunakan `search_stac_items` dan `load_public_cog`.
   - Jalankan `native:hillshade` atau `native:slope` dari QGIS Processing Toolbox untuk menghasilkan relief 3D yang hidup tanpa mengunduh DEM manual.
3. **Kartografi Modern / Infografis:**
   - Tampilkan layer gedung 3D Overture dengan pewarnaan bertingkat (*graduated*) berdasarkan tinggi/jumlah lantai (`height`, `num_floors`).
   - Gabungkan dengan basemap XYZ bebas kunci (Carto Light / Voyager) untuk estetika visual standar profesional.
