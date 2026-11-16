# Digitizing & Georeferencing — Anti-Patterns & Best Practices

Panduan penting mengenai kesalahan umum (*anti-patterns*) pada proses georeferensi dan digitasi peta di QGIS beserta solusi praktisnya.

---

## 1. Strategi Pemilihan GCP: Grid Koordinat vs. Membandingkan Objek (Image-to-Map Matching)

### Skenario 1: Jika Peta Memiliki Grid Koordinat / Neatline
- Selalu prioritaskan **4 sudut grid koordinat** atau perpotongan garis kisi koordinat (graticule intersection) yang tercetak pada lembar peta.
- Tarik dari piksel sudut grid peta langsung ke angka koordinat referensi kartografinya.

### Skenario 2: Jika Peta/Citra TIDAK Memiliki Grid Koordinat
- Gunakan teknik **Image-to-Map Matching** (membandingkan fitur objek fisik yang identik antara citra mentah dan peta dasar bergeoreferensi seperti Google Satellite / OSM):
  1. **Pilih Objek Permanen & Tidak Berubah:**
     - Perempatan / pertigaan jalan utama permanen.
     - Sudut jembatan beton atau ujung dermaga.
     - Sudut lapangan olahraga atau sudut bangunan fasilitas publik yang tegas.
     - Percabangan saluran irigasi atau sungai berbatu (hindari tepian air lumpur/pasir yang mudah berpindah).
  2. **Kunci 4 Sudut Luar (Corner Anchors):**
     - Cari objek pembanding yang letaknya sedekat mungkin dengan 4 sudut terluar citra agar batas tepi citra tidak mengalami flaring/melenceng.
  3. **Gunakan Transformasi Helmert (Similarity) atau Affine:**
     - Helmert mempertahankan bentuk objek asli (aspek rasio 1:1, tidak gepeng).
     - Hindari Polynomial derajat tinggi jika titik objek sedikit, karena bisa membuat area di antara objek tertarik melengkung secara anomali.

---

## 2. Anti-Pattern Digitasi: Menggunakan Raster-to-Vector (`polygonize`) pada Raster Berpola / Tekstur / Arsir

### Kesalahan yang Terjadi:
- Menjalankan `gdal:polygonize` atau raster-to-vector langsung pada gambar peta tata ruang / zonasi / geologi yang memiliki **pola arsiran (hatching), garis strip-strip miring, atau stippled dots (tekstur titik-titik)**.
- **Dampaknya:**
  1. Tool menganggap setiap goresan garis arsiran dan celah putih di antaranya sebagai batas poligon independen.
  2. Menghasilkan ribuan poligon kurus bergerigi mirip "sisir" atau benang ruwet dengan puluhan ribu vertex yang merusak data.
  3. Membebani memori QGIS dan menghasilkan file vektor yang sama sekali tidak dapat digunakan untuk analisis tata ruang.

### Solusi Praktis yang Benar:
1. **Digitasi Titik Sudut Poligon (Boundary Corner Digitizing):**
   - Ingat bahwa zona tersebut sejatinya adalah **satu blok poligon besar dengan sedikit sudut (vertex)**.
   - Lakukan digitasi hanya pada titik-titik sudut terluar batas zonasi (*boundary vertices*) menggunakan `digitize_features()` atau tools digitasi manual QGIS.
   - Jumlah vertex untuk 1 blok zonasi biasanya hanya 4 hingga 15 titik saja, menghasilkan geometri bersih, ringan, dan akurat.
2. **Topologi Tanpa Celah (Avoid Overlap):**
   - Ketika mendigitasi poligon zonasi yang bersebelahan (misalnya zona perumahan berdampingan dengan zona perdagangan atau sempadan sungai), aktifkan fitur **Avoid Overlap** / snapping topologi agar batas bersama (*shared boundary*) saling menempel rapat tanpa celah (*slivers*) dan tanpa poligon bertumpuk.
3. **Kapan Boleh Menggunakan `polygonize`?**
   - Hanya gunakan `polygonize` jika raster adalah **solid raster mask** satu warna penuh tanpa arsiran tekstur (misal masking biner air hasil thresholding NDWI, atau mask bangunan solid hitam-putih).

