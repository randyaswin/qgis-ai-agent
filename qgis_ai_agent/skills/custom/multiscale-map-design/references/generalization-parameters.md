# Cartographic Generalization Parameters & Tolerance Matrix

A technical reference guide providing algorithmic tolerance values, minimum feature dimensions, and generalization parameters across standard scale ranges.

---

## 1. Algorithm Tolerance Parameters by Target Scale

Assumes input spatial geometries are projected in a metric projected coordinate system (e.g., UTM, Web Mercator in meters, or local national grid).

| Target Map Scale | Douglas-Peucker Distance Tolerance | Visvalingam-Whyatt Area Tolerance | Minimum Printable Line Width | Minimum Printable Polygon Area | Contour Line Interval |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1:5,000** | 0.5 – 1.0 m | 1 – 2 m² | 0.10 mm (0.5 m ground) | 2.5 mm² (62.5 m² ground) | 1.0 – 2.5 m |
| **1:10,000** | 1.0 – 2.5 m | 5 – 10 m² | 0.12 mm (1.2 m ground) | 4.0 mm² (400 m² ground) | 5.0 m |
| **1:25,000** | 3.0 – 5.0 m | 25 – 50 m² | 0.15 mm (3.75 m ground)| 6.0 mm² (3,750 m² ground)| 10.0 m |
| **1:50,000** | 7.0 – 15.0 m | 100 – 250 m² | 0.18 mm (9.0 m ground) | 8.0 mm² (20,000 m² ground)| 20.0 m |
| **1:100,000** | 20.0 – 35.0 m | 500 – 1,200 m² | 0.20 mm (20.0 m ground)| 10.0 mm² (100,000 m² ground)| 25.0 – 50.0 m |
| **1:250,000** | 50.0 – 100.0 m| 2,500 – 6,000 m²| 0.25 mm (62.5 m ground)| 15.0 mm² (0.93 km² ground)| 50.0 – 100.0 m |
| **1:500,000** | 120.0 – 250.0 m| 10,000 – 25,000 m²| 0.25 mm (125 m ground) | 20.0 mm² (5.0 km² ground) | 100.0 – 200.0 m |
| **1:1,000,000** | 300.0 – 600.0 m| 50,000 – 150,000 m²| 0.30 mm (300 m ground) | 25.0 mm² (25.0 km² ground)| 200.0 – 500.0 m |

---

## 2. Minimum Dimension Limits in Human Perception

When drawing vector features for physical printing or digital display:

- **Minimum Line Width:** 0.1 mm (hairline). Lines narrower than 0.08 mm will break or vanish during printing.
- **Minimum Distance Between Parallel Lines:** 0.25 mm to avoid visual merging (clumping).
- **Minimum Legible Text Size:** 5.5 pt (print), 10 px (screen).
- **Minimum Polygon Area for Separate Identity:** Polygons smaller than 4.0 mm² (e.g., $2 \times 2\text{ mm}$ square on paper) should either be aggregated into surrounding areas or dropped.
