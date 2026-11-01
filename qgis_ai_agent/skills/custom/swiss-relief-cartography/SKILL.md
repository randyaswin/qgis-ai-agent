---
name: swiss-relief-cartography
description: Use when the user requests a Swiss-style alpine topographic map, Eduard Imhof relief shading, or high-precision mountain cartography. Covers multidirectional shaded relief, cool shadow tinting, elevation contour hierarchy, rock drawing, and Swiss Federal Topography (swisstopo) conventions.
keywords: swiss, imhof, swisstopo, relief style, alpine, topography, topografi, pegunungan, kontur, shaded relief, swiss style, bayangan gunung, kemiringan lereng, elevasi presisi
license: MIT
---

# Swiss Alpine & Imhof Topographic Relief Cartography

Terinspirasi oleh standar emas kartografi topografi Swiss (*Federal Office of Topography swisstopo* dan karya Profesor Eduard Imhof).

## 1. Prinsip Relief & Pencahayaan Imhof
- **Arah Cahaya Alami:** Sudut azimuth 315° (Barat Laut), elevasi matahari 45°. Lereng yang menghadap Barat Laut menerima cahaya terang, lereng Tenggara berada dalam bayangan.
- **Teori Warna Bayangan:**
  - Bagian terkena cahaya: nada hangat (kuning gading / putih lembut).
  - Bagian bayangan: nada sejuk kebiruan (bukan hitam pekat, melainkan abu-abu kebiruan *slate blue*).
- **Kombinasi Layer Relief di QGIS:**
  1. Layer dasar: Shaded relief reguler (`native:hillshade`).
  2. Layer kedua: Slope (kemiringan lereng) dengan transparansi untuk mempertegas tebing curam.
  3. Layer ketiga: Hypsometric tinting lembut (`style_raster` dengan palet hijau lembut ke krem ke putih).

## 2. Hierarki Garis Kontur (Topographic Contours)
Gunakan `gdal:contour` dengan pembagian berjenjang:
- **Garis Kontur Standar (*Intermediate*):** Interval reguler (misal 25 m atau 50 m), ketebalan garis sangat tipis (0.12 - 0.15 mm), warna cokelat zaitun atau sepia (`#7a6348`).
- **Garis Kontur Indeks (*Index Contours*):** Setiap kelipatan ke-5 (misal 100 m atau 250 m), ketebalan garis 0.28 - 0.35 mm.
- **Pelabelan Kontur:** Hanya berikan label angka pada garis kontur indeks (`set_labels` dengan filter kelipatan), arah label melengkung mengikuti garis kontur (*curved placement*), dengan halo putih tipis (0.8 mm).

## 3. Komposisi Tata Letak (Layout)
- Panggil `create_styled_layout(name="Peta Topografi", style_theme="relief_topographic", title="TOPOGRAFI PEGUNUNGAN", page_size="A3")`.
- Bingkai tepi minimalis dengan takik koordinat lintang/bujur presisi di keempat sisi kanvas.
- Skala numerik tajam (*line ticks up*) dengan satuan kilometer/meter.
- Tambahkan bar profil elevasi horizontal (`add_layout_gradient_legend`) untuk rentang meter di atas permukaan laut (m dpl).
