---
name: swiss-relief-cartography
description: Masterclass in Swiss alpine cartography and Eduard Imhof relief shading techniques. Covers natural northwest illumination (315°), atmospheric cool-shadow tinting, multi-layer relief blending (hillshade + slope cliff mask + hypsometric tint), topographic contour line hierarchies (intermediate, index, auxiliary), and Swiss Federal Topography (swisstopo) conventions.
keywords: swiss, imhof, swisstopo, alpine, mountain cartography, shaded relief, topographic map, elevation contours, hillshade, hypsometric tint, rock drawing, terrain
license: MIT
---

# Swiss Alpine & Eduard Imhof Cartographic Relief Guide

Swiss topographic cartography, perfected by the Federal Office of Topography (*swisstopo*) and Professor Eduard Imhof (*Cartographic Relief Presentation*, 1965), represents the gold standard of mountain landscape visualization. It balances three-dimensional plastic relief illusion with mathematical geometric rigor.

---

## 1. Principles of Imhof Shaded Relief

### A. Natural Northwest Illumination
- **Light Direction:** Azimuth **315° (Northwest)**, Sun Elevation **45°**.
- *Visual Psychology:* The human brain perceives northwest lighting as raised terrain. Southern or eastern lighting causes pseudoscopic relief inversion (valleys look like ridges, and mountain peaks look like craters).

### B. Atmospheric Shadow Color Theory
- **Illuminated Slopes (Northwest Facing):**
  - Bathed in direct sunlight. Render in warm, light hues: soft ivory, pale buff, or warm meadow green.
- **Shadowed Slopes (Southeast Facing):**
  - **Never use flat black or muddy charcoal.** Pitch black obscures contour lines, hiking trails, and scree symbols.
  - In high alpine air, shadows are illuminated by diffuse skylight. Use **cool atmospheric tones**: slate-blue, alpine blue-gray (`#455a64`), or soft cobalt gray.

---

## 2. Multi-Layer Alpine Relief Blending in QGIS

True swisstopo relief is not a single hillshade raster; it is a composite stack of four complementary raster layers:

### Layer Stack (Bottom to Top)
1. **Layer 1 (Bottom): Hypsometric Elevation Tint:**
   - Soft, low-saturation elevation gradient:
     - 0–500 m (Valleys): Soft pale spring green (`#dcedc8`)
     - 500–1,500 m (Subalpine pastures): Warm cream / light wheat (`#f0f4c3` to `#fff9c4`)
     - 1,500–2,500 m (Alpine scree & rock): Soft limestone gray / warm stone (`#cfd8dc` to `#eceff1`)
     - > 2,500 m (Glaciers & peaks): Pristine snow white (`#ffffff`)
2. **Layer 2: Primary Shaded Relief (NW 315°):**
   ```python
   run_processing("native:hillshade", {
       "INPUT": "dem_metric",
       "Z_FACTOR": 1.5,
       "AZIMUTH": 315.0,
       "V_ANGLE": 45.0
   }, output_name="swiss_hillshade_primary")
   ```
   - Set Layer Opacity to **45%** over the hypsometric tint (`update_layer(opacity=0.45)`).
3. **Layer 3: Slope Steepness Cliff Accent:**
   - Near-vertical cliffs and ravines require accentuated darkening to mimic hand-drawn rock hachures:
   ```python
   run_processing("native:slope", {
       "INPUT": "dem_metric",
       "Z_FACTOR": 1.0
   }, output_name="swiss_slope_degrees")
   ```
   - Reclassify slopes $> 35^\circ$ with dark sepia or cool slate gray, overlaying at 25% opacity to accentuate rock faces and gorges.
4. **Layer 4 (Top): Hydrography & Streams:**
   - Glacial streams and alpine lakes styled in crisp cyan/azure (`#0288d1`).

---

## 3. swisstopo Topographic Contour Hierarchy

Generate elevation contours using `gdal:contour` adhering to strict line-weight tiers:

```python
# Generate 25m interval contours with elevation attribute 'ELEV'
run_processing("gdal:contour", {
    "INPUT": "dem_metric",
    "BAND": 1,
    "INTERVAL": 25.0,
    "FIELD_NAME": "ELEV"
}, output_name="topo_contours_25m")
```

### Contour Line Tiers
1. **Intermediate Contours (Every 25 m or 50 m):**
   - **Line Width:** Hairline **0.12–0.15 mm**.
   - **Color:** Warm sepia or alpine olive brown (`#795548` or `#6d4c41`).
   - **Labels:** Never label intermediate contours (prevents visual clutter).
2. **Index Contours (Every 5th Line: 100 m, 125 m, 250 m, or 500 m):**
   - **Line Width:** Bold **0.28–0.35 mm**.
   - **Color:** Dark sepia brown (`#4e342e`).
   - **Labels:** Labeled exclusively on index lines (`set_labels`) using expression `ELEV % 100 = 0` (or `% 250 = 0`).
   - **Placement:** Curved along the contour line, oriented so text reads **uphill into the slope**.
   - **Halo:** 0.8 mm crisp white text halo buffer (`buffer_color="#ffffff"`).
3. **Glacial / Ice Contours:**
   - For contours traversing glaciers or permanent snowfields, render in soft cyan-blue (`#29b6f6`) with a dashed stroke.

---

## 4. Swiss Topographic Layout Composition

Swiss topographic map sheets emphasize precision, minimalist typography, and crisp border tick marks:

```python
# 1. Initialize Swiss Topographic Layout Template
create_styled_layout(
    name="Alpine_Topographic_Sheet",
    style_theme="relief_topographic",
    title="VALLEY OF THE MATTERHORN",
    subtitle="Swiss National Topographic Survey · 1:25,000 Scale Series",
    credits="Federal Office of Topography swisstopo · Projected Metric Coordinate System",
    extent_layer="Mountain_Core_Area",
    page_size="A3",
    orientation="landscape",
    grid=True
)

# 2. Add Horizontal Elevation Gradient Legend
add_layout_gradient_legend(
    layout="Alpine_Topographic_Sheet",
    x=14, y=265, width=70, height=18,
    title="Elevation (m a.s.l.)",
    ramp="Spectral",
    min_val="480 m",
    max_val="4,478 m",
    invert_ramp=True
)

# 3. Add High-Precision Metric Scale Bar
add_layout_scalebar(
    layout="Alpine_Topographic_Sheet",
    x=280, y=265,
    item_id="scalebar",
    style="Line Ticks Up",  # Crisp Swiss surveyor ticks
    units="km",
    segments=4,
    units_per_segment=1.0,  # 0, 1, 2, 3, 4 km
    color="#263238"
)
```

---

## 5. Visual Quality Review
- [ ] Is illumination coming strictly from the Northwest (315°)?
- [ ] Are slope shadows cool blue-gray rather than solid black?
- [ ] Are elevation numbers placed only on index contours and oriented reading uphill?
- [ ] Does the neatline feature clean coordinate edge tick marks?
- [ ] Is the typography set in clean, neutral Swiss sans-serif (`Helvetica`, `Univers`, or `Arial`)?
