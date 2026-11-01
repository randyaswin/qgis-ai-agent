---
name: natgeo-cartography-style
description: Use when the user requests a National Geographic (NatGeo) style map. Covers classic serif typography, warm parchment/antique ocean background, double-line neatlines, subdued earthy hypsometric tints, warm-lit relief shading, cased highways, and elegant framed legends.
keywords: natgeo, national geographic, classic cartography, antique, parchment, serif, elegant, neatline, relief, gaya natgeo, kartografi klasik, indah, publikasi buku, majalah
license: MIT
---

# National Geographic (NatGeo) Cartographic Style

Gaya kartografi legendaris National Geographic mengutamakan keanggunan visual, kejelasan hierarki, dan nada warna alami yang hangat.

## 1. Palet Warna & Tipografi
- **Latar Laut / Samudra:** Bukan biru terang; gunakan abu-abu kebiruan lembut (`#e4ecef`) atau nada perkamen antik (`#f5f2eb`). Jika memuat basemap eksternal, wajib gunakan XYZ tile bebas API key: `add_basemap(preset="carto_voyager")` atau `add_basemap(preset="esri_topo")` dengan transparansi 35%.
- **Daratan & Elevasi (Hypsometric Tint):** Palet bumi lembut:
  - Dataran rendah: hijau zaitun lembut (`#d8e2dc` ke `#c5d5cb`)
  - Dataran sedang: krem kekuningan / buff (`#f0e6d2`)
  - Dataran tinggi / pegunungan: terakota / sienna hangat (`#d4a373` ke `#8c5835`)
  - Puncak tertinggi: abu-abu putih salju (`#f8f9fa`)
- **Pewarnaan Relief (Hillshade):**
  - Pencahayaan hangat dari Barat Laut (315°), sudut elevasi 40-45°.
  - Mode blending `Multiply` atau transparansi 40% di atas layer elevasi.
- **Tipografi:** Wajib menggunakan font bertipe **Serif Klasik** (seperti *Georgia*, *Garamond*, *Playfair Display*, atau *PT Serif*):
  - Judul: Huruf kapital berjarak (*tracked*), bold, warna hitam pekat atau navy tua (`#1a2332`).
  - Wilayah perairan (sungai/danau/laut): Huruf miring (*italic*), warna biru tua keabuan (`#2c5282`).
  - Pegunungan / Bentang alam: Huruf kapital condong, spasi renggang.

## 2. Garis Batas & Jaringan Jalan
- **Garis Batas Negara / Provinsi:** Garis putus-titik (*dash-dot*) berwarna abu-abu arang atau ungu anggrek lembut (`#6b5b95`).
- **Jaringan Jalan (Cased Roads):** Garis berlapis ganda (jalan utama menggunakan isian putih/oranye dengan garis tepi tipis abu-abu).

## 3. Tata Letak (Layout) NatGeo
1. **Bingkai Rapi Ganda (Double Neatline):**
   - Bingkai luar tipis, bingkai dalam sedikit lebih tebal dengan jarak 2-3 mm.
2. **Inisialisasi Template:**
   - Gunakan `create_styled_layout(name="Peta NatGeo", style_theme="natgeo", title="PULAU BALI", subtitle="Atlas Geografis & Bentang Alam", page_size="A3")`.
3. **Kompas Arah Utara & Skala:**
   - Skala bertipe garis tunggal klasik bertakik (*single box* hitam-putih).
   - Panah utara menggunakan simbol bintang kompas atau *fleur-de-lis* elegan.
4. **Legenda Berbingkai Antik:**
   - Kotak legenda berlatar belakang krem lembut (`#fbf9f4`) dengan garis tepi dobel tipis.
