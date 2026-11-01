---
name: natgeo-cartography-style
description: Authoritative guide for emulating the classic National Geographic (NatGeo) atlas cartographic style. Covers warm parchment/antique backgrounds, double neatline frames with corner rosettes, elegant serif typography hierarchy, warm-lit shaded relief blending, cased road networks, subdued hypsometric earth tints, and framed legend cards.
keywords: natgeo, national geographic, editorial cartography, classic atlas, parchment, serif, elegant, neatline, relief, antique, terrain, typography, cartographic style
license: MIT
---

# National Geographic (NatGeo) Cartographic Style Guide

The National Geographic Society (NGS) cartographic tradition is widely recognized as the pinnacle of editorial map-making. It is defined by warmth, typographic restraint, elegant double neatlines, subtle terrain blending, and muted natural palettes.

---

## 1. The Iconic NatGeo Color Palette

Never use saturated neon colors or pure black borders. NatGeo maps use a warm, organic earth-and-water palette:

### Canvas & Background
- **Page Canvas Fill:** Warm antique parchment (`#fcfbf7` or `#f7f5ed`).
- **Oceans & Water Bodies:** Soft muted slate-blue (`#e3ecf0` or `#d6e5ec`), never electric blue.
- **Inland Lakes & Rivers:** Muted dark slate blue (`#2b5c8f` or `#1e3a5f`).

### Natural Hypsometric Tints (Elevation Progression)
- **Lowlands & Coastal Plains (0–200 m):** Soft sage / olive green (`#d8e2dc` to `#cad8cf`).
- **Low Hills & Foothills (200–800 m):** Warm buff / cream / golden straw (`#f3e8d2` to `#e8d7b8`).
- **Mid-Elevation Mountains (800–2,000 m):** Warm ochre / terracotta sienna (`#cf9d72` to `#b2744c`).
- **High Alpine Ridges (> 2,000 m):** Deep sepia brown (`#7a492d`) culminating in snowy off-white (`#f8f9fa`).

### Urban & Cultural Features
- **Built-Up Areas:** Subtle warm salmon / peach tint (`#f6d5c5` with 0.7 opacity).
- **Road Network (Cased Roads):** Warm yellow/orange interior fill (`#fbc02d`) flanked by crisp 0.25 mm dark charcoal casing strokes (`#374151`).
- **Administrative Boundaries:** Dash-dot muted plum or slate violet lines (`#5c5470`).

---

## 2. Typographic System & Hierarchy

NatGeo maps rely almost exclusively on high-readability classic **Serif** typefaces:
- **Primary Typefaces:** *Georgia*, *Baskerville*, *Palatino*, *Minion Pro*, or *Times New Roman*.

### Text Hierarchy & Styling Rules
1. **Primary Title:**
   - Spaced uppercase letters (letter-spacing / tracking).
   - Deep charcoal (`#1b2430`) or dark navy (`#0d1b2a`).
   - Font size: 24–36 pt (A3), bold serif.
2. **Water Bodies & Hydrography:**
   - Always **Italicized**.
   - Muted navy/slate blue (`#1e3a8a` or `#2563eb`).
   - Curvilinear placement along river paths.
3. **Mountain Ranges & Physiography:**
   - Letter-spaced small caps or wide-tracked italic serif (`#4a3525`).
4. **Cities & Towns:**
   - Capital cities: Bold serif with star marker symbol.
   - Secondary towns: Clean Roman serif, black or dark gray, with 0.8 mm white text halo (`#ffffff`).

---

## 3. Shaded Relief & Mountain Terrain Blending

The NatGeo signature look combines multidirectional terrain relief with soft hypsometric tinting:

1. **Hillshade Derivation:**
   ```python
   run_processing("native:hillshade", {
       "INPUT": "dem_metric",
       "Z_FACTOR": 1.4,
       "AZIMUTH": 315.0,  # Traditional northwest light source
       "V_ANGLE": 42.0
   }, output_name="natgeo_hillshade")
   ```
2. **Layer Blending Order:**
   - Bottom: Hypsometric DEM raster styled with `style_raster(ramp="Spectral", invert_ramp=True)` or custom warm terrain ramps.
   - Middle: `natgeo_hillshade` placed directly above DEM with 40–45% opacity (`update_layer(opacity=0.42)`).
   - Top: Hydrography (rivers/lakes) and boundary vector lines.

---

## 4. NatGeo Print Layout Construction

Build the layout using `create_styled_layout(style_theme="natgeo")` or modular primitives:

```python
# 1. Initialize NatGeo Styled Template
create_styled_layout(
    name="NatGeo_Atlas_Sheet",
    style_theme="natgeo",
    title="ISLANDS OF THE PACIFIC RIM",
    subtitle="A Physical and Environmental Atlas · Special Edition",
    credits="Cartography compiled from SRTM Elevation, GEBCO Bathymetry, and Public Open Data",
    extent_layer="Study_Boundary",
    page_size="A3",
    orientation="landscape",
    grid=True
)

# 2. Keyed Legend Box
# NatGeo legends are framed in elegant double-ruled cards with warm parchment fill:
add_layout_legend(
    layout="NatGeo_Atlas_Sheet",
    x=290, y=160, width=95, height=100,
    item_id="natgeo_legend",
    title="LEGEND",
    title_size=12,
    item_size=9,
    background="#fcfbf7",
    frame_color="#263238"
)

# 3. Classic Scale Bar & Compass
add_layout_scalebar(
    layout="NatGeo_Atlas_Sheet",
    x=20, y=260,
    item_id="scalebar",
    style="Single Box",  # Classic alternating black-and-white blocks
    units="km",
    segments=4,
    units_per_segment=50,
    color="#1b2430"
)

add_layout_north_arrow(
    layout="NatGeo_Atlas_Sheet",
    x=365, y=36, size=15,
    item_id="north_arrow",
    style="circle",      # Classic compass rose design
    color="#1b2430"
)
```

---

## 5. Visual Verification Checklist
- [ ] Is the canvas background set to warm parchment (`#fcfbf7`) rather than sterile blue-white (`#ffffff`)?
- [ ] Are river names italicized in muted blue?
- [ ] Is the scale bar styled with alternating black-and-white `Single Box` blocks?
- [ ] Does the map frame feature a double neatline border?
- [ ] Is hillshade smoothly blended over elevation with 40–45% transparency?
