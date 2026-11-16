# Cartographic Symbol Library & Stroke Specifications

This reference provides exact dimensions, stroke weights, dash patterns, and QGIS symbol definitions for high-precision cartography across standard map scales (1:10,000 to 1:250,000).

---

## 1. Linear Feature Stroke & Dash Specifications

All dimensions in millimeters (mm) at target print scale.

### 1.1 Administrative Boundaries

| Boundary Rank | Line Pattern | Stroke Width | Primary Color | Secondary/Buffer Color | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **National / International** | Dash-Dot-Dot (`8 1.5 1 1.5 1 1.5`) | 0.8 mm | `#1e293b` (Deep Slate) | `#f1f5f9` (0.5 mm halo) | Prominent border line |
| **State / Province** | Dash-Dot (`6 1.5 1.5 1.5`) | 0.6 mm | `#334155` (Slate) | None | Secondary jurisdictional border |
| **County / District** | Long Dash (`4 1.5`) | 0.4 mm | `#475569` | None | Local government limit |
| **Municipality / Sub-district**| Short Dash (`2 1.0`) | 0.25 mm | `#64748b` | None | Minor administrative division |
| **Cadastral / Parcel** | Solid (`solid`) | 0.15 mm | `#94a3b8` | None | Property boundary hairline |

### 1.2 Transportation Networks (Cased Road Specifications)

In QGIS, apply a multi-layer line symbol where Layer 0 is the casing and Layer 1 is the inner core.

```python
# Cased Motorway Symbol Definition
casing_layer = {
    "color": "#991b1b",       # Dark Burgundy Red
    "width": "1.8",           # Total exterior width (mm)
    "capstyle": "square",
    "joinstyle": "miter"
}
core_layer = {
    "color": "#ef4444",       # Bright Crimson Red
    "width": "1.2",           # Core interior width (mm)
    "capstyle": "square",
    "joinstyle": "miter"
}

# Cased Secondary / Urban Arterial
casing_layer_secondary = {
    "color": "#475569",       # Slate Gray
    "width": "1.2",
    "capstyle": "round",
    "joinstyle": "round"
}
core_layer_secondary = {
    "color": "#ffffff",       # Crisp White
    "width": "0.7",
    "capstyle": "round",
    "joinstyle": "round"
}
```

---

## 2. Standard Point Markers & Sizing Hierarchy

| Feature Type | Geometry / Icon | Size (mm) | Fill Color | Stroke Color & Width |
| :--- | :--- | :--- | :--- | :--- |
| **National Capital** | Star inside circle (`star`) | 5.5 mm | `#fbbf24` (Gold) | `#1f2937`, 0.4 mm |
| **Major City / Metropolis** | Circle with inner dot (`circle`) | 4.0 mm | `#ffffff` | `#111827`, 0.6 mm |
| **Town / District Center** | Filled Circle (`circle`) | 2.8 mm | `#1f2937` | `#ffffff`, 0.3 mm halo |
| **Hospital / Medical Clinic**| Cross (`cross`) | 3.5 mm | `#ef4444` (Red) | `#ffffff`, 0.3 mm |
| **Fire / Emergency Station** | Diamond (`diamond`) | 3.5 mm | `#f97316` (Orange)| `#ffffff`, 0.3 mm |
| **Police Station** | Shield / Square (`square`)| 3.2 mm | `#2563eb` (Blue) | `#ffffff`, 0.3 mm |
| **Transit / Rail Station** | Square with circle (`square`)| 3.0 mm | `#0284c7` | `#ffffff`, 0.3 mm |

---

## 3. Transparency & Blending Reference

```
[Layer Stack Order: Top to Bottom]
1. Labels & Annotations          -> Normal (100% Opacity)
2. POI Marker Points             -> Normal (90% Opacity, with white halos)
3. Infrastructure & Roads        -> Normal (100% Opacity)
4. Thematic Vector Polygons      -> Multiply (75% Opacity)
5. Shaded Relief / Hillshade     -> Multiply (45%-55% Opacity)
6. Elevation Tints (DEM)         -> Normal (100% Opacity)
7. Water Bodies & Oceans         -> Normal (100% Opacity, #dbeafe)
8. Page Canvas Background        -> Normal (#ffffff or #fdfdfd)
```
