---
name: natgeo-cartography-style
description: Use when the user requests a National Geographic (NatGeo) style map. Covers classic serif typography, warm parchment/antique ocean background, double-line neatlines, subdued earthy hypsometric tints, warm-lit relief shading, cased highways, and elegant framed legends.
keywords: natgeo, national geographic, classic cartography, antique, parchment, serif, elegant, neatline, relief, atlas style, editorial map, gaya natgeo, kartografi klasik
license: MIT
---

# National Geographic (NatGeo) Cartographic Style

The iconic National Geographic cartographic aesthetic emphasizes visual warmth, refined typographic hierarchy, and natural muted tones.

---

## 1. Color Palette & Typography

- **Water / Oceans:** Not bright saturated blue; use soft muted slate-blue (`#e4ecef`) or warm antique parchment tones (`#f5f2eb`). If loading an external basemap, always use keyless XYZ tile presets: `add_basemap(preset="carto_voyager")` or `add_basemap(preset="esri_topo")` at 35% opacity.
- **Land & Hypsometric Tints:** Muted, natural earthy tones:
  - Lowlands: Soft sage / olive green (`#d8e2dc` to `#c5d5cb`)
  - Mid elevations: Warm buff / cream (`#f0e6d2`)
  - Highlands / Mountains: Warm sienna / terracotta (`#d4a373` to `#8c5835`)
  - Mountain peaks: Subtle snowy off-white (`#f8f9fa`)
- **Relief Shading (Hillshade):**
  - Northwest illumination (Azimuth 315°, Altitude 40–45°).
  - Blend mode `Multiply` or 40% layer transparency over elevation tints.
- **Typography:** Always use classic **Serif** fonts (*Georgia*, *Garamond*, *Playfair Display*, or *PT Serif*):
  - Titles: Spaced uppercase (*tracked*), bold, deep charcoal or dark navy (`#1a2332`).
  - Water bodies (rivers, lakes, seas): Italicized, dark slate blue (`#2c5282`).
  - Mountain ranges & landforms: Tracking-spaced small-caps or italics.

---

## 2. Boundaries & Cased Roads

- **Administrative Boundaries:** Dash-dot charcoal or muted plum lines (`#6b5b95`).
- **Road Network (Cased Roads):** Dual-stroke lines (e.g. warm orange/yellow road fill encased in crisp 0.2mm dark gray casings).

---

## 3. NatGeo Layout Setup

1. **Double Neatline Border:**
   - Thin outer stroke, slightly thicker inner stroke with 2–3mm margin between them.
2. **Layout Initialization:**
   - Call `create_styled_layout(name="NatGeo_Map", style_theme="natgeo", title="REGIONAL ATLAS", subtitle="Topography and Geographic Features", page_size="A3")`.
3. **North Arrow & Scale Bar:**
   - Single-box alternating black-and-white scale bar.
   - Refined compass star or classic north arrow symbol.
4. **Framed Legend:**
   - Antique off-white card background (`#fbf9f4`) with subtle double neatline border.
