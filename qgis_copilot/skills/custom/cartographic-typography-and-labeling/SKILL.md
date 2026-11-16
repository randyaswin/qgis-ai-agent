---
name: cartographic-typography-and-labeling
description: Authoritative guide for cartographic lettering, typography hierarchy, label placement engines (QGIS PAL), text halos/buffers, curved hydrographic lettering, tracking, callouts, and collision avoidance. Covers typeface pairings (Serif for physical features, Sans-serif for cultural features, Italics for water), scale-dependent text sizing, obstacle weighting, and leader lines.
keywords: cartography, typography, labeling, map lettering, font hierarchy, pal engine, text halos, curved text, hydrographic labels, leader lines, callouts, collision detection, label priority, tracking, letter spacing
license: MIT
---

# Cartographic Typography & Labeling Guide

Text on a map is not mere decoration; it is a vital spatial symbol that anchors geographic features, guides eye movement, and establishes an intuitive informational hierarchy. Poor labeling creates visual noise, obscures map geometry, and degrades map readability.

---

## 1. Cartographic Typeface Conventions & Pairing

Cartography follows strict, battle-tested typographic conventions established over centuries:

### 1.1 The Golden Feature-Typeface Pairing Rules

| Geographic Feature Class | Type Style | Recommended Typefaces | Color & Styling | Example |
| :--- | :--- | :--- | :--- | :--- |
| **Physical Landforms** (Mountains, Valleys, Ridges) | **Serif**, Upright / Small Caps | *Georgia*, *Palatino*, *Baskerville*, *Times* | Warm dark brown (`#4a3728`) or deep charcoal; wide tracking (+2 to +5) | `A P P A L A C H I A N   M O U N T A I N S` |
| **Hydrography** (Rivers, Lakes, Seas, Oceans) | **Serif or Sans**, **Italics** | *Georgia Italic*, *Minion Italic*, *Segoe UI Italic* | Deep oceanic blue (`#1e40af` or `#0369a1`); curved along line | *Mississippi River*, *Lake Michigan* |
| **Administrative & Cities** (Countries, States, Cities) | **Sans-Serif**, Upright | *Roboto*, *Inter*, *Helvetica*, *Arial*, *Open Sans* | Solid black (`#111827`) or deep slate (`#1e293b`); bold for capitals | **Tokyo**, **Geneva**, San Francisco |
| **Infrastructure & POIs** (Roads, Stations, Amenities)| **Sans-Serif**, Medium/Regular | *Roboto Condensed*, *Inter*, *DIN*, *Arial Narrow* | Neutral charcoal (`#374151`) or dark gray (`#4b5563`); condensed | `Central Station`, `Main St` |

### 1.2 Letter-Spacing (Tracking) for Expansive Features
Features that occupy broad spatial areas (mountain chains, deserts, regional seas, large territories) must use expanded letter-spacing (tracking) spanning their spatial footprint:
- Set letter-spacing between 2.0 pt and 8.0 pt depending on scale.
- Use uppercase characters (`ALL CAPS`) with generous tracking to clearly signal that the label refers to an extensive region rather than a single point.

---

## 2. Typographic Hierarchy & Sizing Matrix

A well-designed map never uses more than 4–5 distinct visual weight steps. Sizing must relate directly to geographic importance and scale:

### Typographic Scale for Standard Print (A3 / A4)

```
[Level 1] Primary Map Title:         24–32 pt | Bold Sans/Serif | #0f172a
   └── [Level 2] Country / Major Region: 14–18 pt | Bold / All Caps | #1e293b
          └── [Level 3] Capital / Metro City:   11–13 pt | Bold Sans | #111827
                 └── [Level 4] Towns & Districts:    8.5–10 pt | Regular Sans | #334155
                        └── [Level 5] Local POIs / Roads:  6.5–7.5 pt | Condensed/Regular | #475569
```

---

## 3. Label Placement Engines (QGIS PAL)

QGIS uses the **PAL (Point Area Line) Placement Engine**, one of the most sophisticated optimization engines in GIS. Proper PAL configuration prevents labels from overlapping each other and underlying critical symbols.

### 3.1 Point Feature Placement
- **Standard Cartographic Rule:** The preferred placement for a point label is **Top-Right (Above & Right)** of the marker point.
- **Placement Priorities:**
  1. Top-Right (Weight 10 - Best)
  2. Top-Left (Weight 8)
  3. Bottom-Right (Weight 6)
  4. Bottom-Left (Weight 4)
  5. Directly Right or Left (Fallback)
  6. Never directly on top of the point symbol unless labeling a large polygon centroid.
- **Distance Offset:** Set a minimum offset of 1.0–2.0 mm between the marker perimeter and the label text to maintain visual breathing room.

### 3.2 Line Feature Placement (Roads & Rivers)
- **Road Networks:**
  - Set placement mode to **Parallel** or **Curved**.
  - Position: **On line** (with line clipping) or **Above line** (0.5 mm offset).
  - Repetition: For long highways or arterial roads, repeat labels every 100–150 mm to orient viewers across large map layouts.
- **Hydrography (Rivers & Streams):**
  - Set placement mode to **Curved**.
  - Follow the general sinusoidal flow of the watercourse; avoid jagged, high-frequency wiggles.
  - Text orientation: Keep text upright or sloping upward along the visual flow direction.

### 3.3 Polygon Feature Placement
- **Horizontal:** Preferred for administrative districts, parcels, and land cover zones.
- **Free Angle / Along Skeleton:** Ideal for elongated geographic features (e.g., long valleys, island chains, lakes).
- **Interior Fallback:** Always enable *"Only place label inside polygon"* for small polygons to avoid stray labels floating outside their administrative boundaries.

---

## 4. Text Halos (Buffers) & Readability

A text halo (buffer) creates a micro-cushion around characters, allowing them to remain legible over complex, high-contrast imagery, hillshades, or road intersections.

### 4.1 Halo Sizing & Styling Formulas
- **Halo Size Rule:** Buffer size should be approximately **10% to 15% of the text font size**.
  - For 10 pt text: Buffer size = 0.8 mm to 1.2 mm.
  - For 7 pt text: Buffer size = 0.6 mm to 0.8 mm.
- **Halo Color Harmony:**
  - Light base maps (white / beige / soft gray): Use solid white (`#ffffff`) or background-matched off-white.
  - Dark mode / satellite base maps: Use deep charcoal (`#0f172a`) or black (`#000000`) buffer around white text.
- **Soft Buffers (Transparency):** Set buffer opacity to 80%–90% rather than 100% to soften the boundary and prevent a "cookie-cutter" cutout effect.

```python
# Applying high-readability labels with soft halos via set_labels
set_labels(
    layer="district_centers",
    column="district_name",
    font_family="Roboto",
    font_size=9.5,
    font_weight="Bold",
    font_color="#0f172a",
    halo_color="#ffffff",
    halo_size=1.0,
    placement="around_point",
    offset_x=1.5,
    offset_y=-1.5
)
```

---

## 5. Callouts & Leader Lines for Dense Map Regions

When feature points are densely clustered (e.g., transit stations in a downtown core or tourist sites in a historic quarter), conventional labels collide or get suppressed.

### Callout Configuration Best Practices
1. **Leader Lines:** Thin, elegant lines (0.2–0.3 mm, `#64748b` or `#94a3b8`) connecting the label anchor to the feature centroid.
2. **Anchor Points:** Anchor the leader line to the closest corner or midpoint of the text box.
3. **Offset Radius:** Allow labels to automatically push outward outside the dense cluster, leaving the focal map features visible.

---

## 6. Label Collision & Priority Management

When the map canvas contains multiple competing text layers, establish unambiguous priority tiers in QGIS:

```
[Layer Priority Order: High (10) to Low (1)]
1. Capital Cities & Major Towns      -> Priority: 10 | Obstacle Weight: 10
2. Primary Physical Features (Peaks) -> Priority: 8  | Obstacle Weight: 8
3. Highways & Primary Arterials      -> Priority: 6  | Obstacle Weight: 6
4. Local Towns & Neighborhoods       -> Priority: 4  | Obstacle Weight: 4
5. Minor Roads & Streams             -> Priority: 2  | Obstacle Weight: 2
```

### Obstacle Weighting
- Make point marker symbols and primary road casings **Obstacles** so the PAL engine routes labels around them rather than printing text across icons.
