# Digitizing & Georeferencing — Anti-Patterns & Best Practices

Panduan penting mengenai kesalahan umum (*anti-patterns*) pada proses georeferensi dan digitasi peta di QGIS beserta solusi praktisnya.

---

## 1. Anti-Pattern Georeferensi: Mengikat GCP pada Objek Bukan Titik Grid Koordinat (Menghasilkan Peta Gepeng / Distorsi Ekstrem)

### Kesalahan yang Terjadi:
- Mengambil GCP hanya dari objek fisik di dalam peta (misalnya atap rumah, tikungan jalan kecil, atau muara sungai) tanpa menggunakan grid koordinat (*graticule*) atau perpotongan garis batas lembar peta (*neatline*).
- **Dampaknya:**
  1. Peta menjadi **gepeng, terpuntir miring, atau terdistorsi melengkung** karena objek darat tidak memiliki kontrol matematis terhadap proyeksi kartografis lembar peta.
  2. Rasio aspek (*aspect ratio*) sumbu X dan Y rusak sehingga skala horizontal dan vertikal menjadi tidak seragam.
  3. Objek darat pada peta cetak sering kali disederhanakan (*cartographic generalization*) oleh kartografer aslinya sehingga koordinat aslinya berbeda dari posisi gambar.

### Solusi Praktis yang Benar:
1. **Gunakan 4 Titik Sudut Grid Koordinat Peta (Map Frame / Graticule Intersections):**
   - Lembar peta selalu memiliki garis grid koordinat (misalnya interval 1.000 meter UTM atau interval derajat menit geografis) dengan angka koordinat yang tercetak jelas di tepi peta (*neatline*).
   - Selalu ambil minimal **4 sudut grid terluar** sebagai GCP utama ($[X_{pixel}, Y_{pixel}] \rightarrow [X_{map}, Y_{map}]$).
   - Tarik koordinat langsung ke koordinat angka yang tertera di grid tersebut.
2. **Pilih Model Transformasi yang Tepat:**
   - Gunakan **Helmert** (jika hanya rotasi, translasi, dan skala seragam tanpa merusak bentuk asli) atau **Affine / Polynomial 1** (untuk menjaga garis paralel grid tetap lurus dan paralel).
   - Hindari Polynomial order 2/3 atau Thin Plate Spline jika titik GCP sedikit, karena akan menimbulkan efek tarikan "karet" (*rubber-sheeting*) yang meliuk-liuk atau gepeng di luar titik ikat.

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
