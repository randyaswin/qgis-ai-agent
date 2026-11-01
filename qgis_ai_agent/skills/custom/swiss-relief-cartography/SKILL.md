---
name: swiss-relief-cartography
description: Use when the user requests a Swiss-style alpine topographic map, Eduard Imhof relief shading, or high-precision mountain cartography. Covers multidirectional shaded relief, cool shadow tinting, elevation contour hierarchy, rock drawing, and Swiss Federal Topography (swisstopo) conventions.
keywords: swiss, imhof, swisstopo, relief style, alpine, topography, shaded relief, swiss style, alpine cartography, mountain contours, elevation
license: MIT
---

# Swiss Alpine & Eduard Imhof Topographic Relief Cartography

Inspired by the gold standard of Swiss alpine cartography (*Federal Office of Topography swisstopo* and the foundational works of Professor Eduard Imhof).

---

## 1. Principles of Imhof Shaded Relief

- **Natural Illumination:** Azimuth 315° (Northwest), sun elevation 45°. Slopes facing northwest receive bright direct illumination, while southeast-facing slopes cast deep shadows.
- **Shadow Color Theory:**
  - Illuminated slopes: Warm hues (ivory white to soft buff).
  - Shadowed slopes: Cool tones (not flat black, but atmospheric slate-blue and cool gray).
- **Multi-Layer Relief Composition in QGIS:**
  1. Base layer: Shaded relief (`native:hillshade`).
  2. Accent layer: Slope steepness overlay with transparency to accentuate cliffs and ravines.
  3. Hypsometric tint layer: Soft elevation color progression (`style_raster` transitioning from meadow green to warm beige to snow white).

---

## 2. Topographic Contour Hierarchy

Generate contours with `gdal:contour` following strict visual weight tiers:
- **Intermediate Contours:** Regular interval (e.g. 25m or 50m), hairline width (0.12–0.15mm), sepia or warm olive brown (`#7a6348`).
- **Index Contours:** Every 5th contour (e.g. 100m or 250m), bold width (0.28–0.35mm).
- **Contour Labeling:** Place elevation numbers exclusively on index contours (`set_labels` with modulus filter), curved along the line, protected by a crisp 0.8mm white text halo.

---

## 3. Layout Composition

- Call `create_styled_layout(name="Topographic_Map", style_theme="relief_topographic", title="ALPINE TOPOGRAPHY", page_size="A3")`.
- Clean minimalist neatline with high-precision tick marks along all four canvas edges.
- High-contrast tick scale bar with metric kilometer/meter intervals.
- Include a horizontal elevation gradient bar (`add_layout_gradient_legend`) indicating meters above sea level (m a.s.l.).
