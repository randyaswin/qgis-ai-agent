---
name: peta-bahasa-indonesia
description: Use when the user writes in Indonesian or the map is for an Indonesian audience. Standard Indonesian map vocabulary, number and unit formatting, title/legend/credit templates, and label conventions.
keywords: bahasa indonesia, peta, judul, legenda, skala, arah utara, sumber, keterangan, istilah, label, pembuatan peta, laporan, skripsi, tesis, presentasi
license: MIT
---

# Teks peta berbahasa Indonesia

## Istilah baku
| English | Indonesia |
| :--- | :--- |
| Legend | Legenda / Keterangan |
| Scale | Skala |
| North arrow | Arah utara (panah utara) |
| Source | Sumber |
| Projection / Datum | Proyeksi / Datum |
| Administrative boundary | Batas administrasi (provinsi, kabupaten/kota, kecamatan, desa/kelurahan) |
| Road, river, building | Jalan, sungai, bangunan |
| Elevation, slope | Ketinggian (m dpl), kemiringan lereng |
| Land cover / land use | Tutupan lahan / penggunaan lahan |
| Low, medium, high | Rendah, sedang, tinggi |
| No data | Tidak ada data |

## Format
- Decimal comma and thousand dot in text for Indonesian audiences: `1.234,5`. Say so in the answer if you change from the data's native format; layout labels can use `format_number()` with locale in expressions.
- Units: km², ha, jiwa/km², m dpl. Write "dpl" (di atas permukaan laut), not "asl".
- Dates: `1 November 2026` or `2026`. Name the data year always.

## Templates
- Title: `Peta <Tema> <Wilayah> Tahun <Tahun>` (e.g. "Peta Kepadatan Penduduk Kabupaten Bogor Tahun 2023").
- Subtitle: `<Indikator> (<satuan>), klasifikasi <metode>`.
- Credits: `Sumber: <Instansi, dataset, tahun>. Sistem koordinat: WGS 84 / UTM Zona 48S (EPSG:32748). Dibuat dengan QGIS <versi>.`
- Legend title: "Keterangan" or the indicator name with unit; classes as "0 – 100", "101 – 500", with en dash.

## Labels
Use proper case as in official names ("Kab. Bogor", "Kota Bandung"), keep administrative level prefixes consistent, and avoid ALL CAPS except for provinces if the user's institution requires it.
