---
name: cartographic-symbology-and-styling
description: Authoritative guide for cartographic symbolization, visual hierarchy, graphic variables, and vector layer styling in QGIS. Covers Bertin's visual variables, figure-ground separation, point symbol scaling (Flannery proportional vs graduated), cased road networks and line hierarchy, polygon boundary rendering, opacity, layer blending modes, and rule-based styling workflows.
keywords: cartography, map, make map, peta, symbology, styling, cartographic styling, visual hierarchy, figure ground, bertin variables, proportional symbols, graduated symbols, cased roads, road hierarchy, line casing, layer blend modes, rule based rendering, stroke, fill, opacity
license: MIT
---

# Cartographic Symbology & Visual Styling Guide

Symbology is the graphical grammar of a map. Effective cartographic styling transforms raw spatial features into an immediately legible visual narrative by establishing clear visual hierarchy, intuitive visual variables, and harmonious figure-ground relationships.

---

## 1. The Core Principles of Cartographic Design

### 1.1 Visual Hierarchy & Figure-Ground Separation
Every map has a foreground (the focal subject), an informational middle ground (supporting contextual layers), and a background (spatial orientation, e.g., water, land relief, borders).

- **Focal Layers (Subject):** High contrast, saturated colors, thicker stroke weights, crisp edges.
- **Contextual Layers (Middle Ground):** Moderate contrast, muted or desaturated tones, medium stroke weights.
- **Base Layers (Background):** Low contrast, pastel or subtle neutral tints (`#f0f2f5`, `#e8ecef`), hairline strokes (0.1–0.15 mm).

### 1.2 Bertin's Visual Variables Applied to Spatial Data
Jacques Bertin established 7 core visual variables for graphic information representation:

| Visual Variable | Best Suited Data Level | Cartographic Application | Best Practices & Constraints |
| :--- | :--- | :--- | :--- |
| **Position** | Point, Line, Polygon | Geographic coordinates | Inherent to GIS layers; do not distort unless making cartograms. |
| **Size** | Quantitative / Ratio | Proportional circles, line widths | Use perceptual scaling (Flannery); avoid area distortion. |
| **Value (Lightness)**| Ordered / Quantitative | Density, elevation, counts | Darker = more intense/dense (on light base); reverse on dark base. |
| **Color Hue** | Categorical / Nominal | Land use, zoning, geology | Limit to 5–8 distinct, culturally harmonious hues. |
| **Color Saturation** | Ordered / Quantitative | Intensity, certainty, focus | Reserve vibrant/saturated colors for focal classes; desaturate context. |
| **Shape** | Categorical / Nominal | POI icons, facility types | Use simple geometric or ISO standardized pictograms. |
| **Texture / Pattern**| Categorical / Status | Wetlands, parks, disputed zones | Subtle hatches or stipples; avoid moiré patterns and visual vibration. |

---

## 2. Point Symbolization & Scaling

### 2.1 Graduated vs. Proportional Symbols
- **Graduated Symbols:** Features are partitioned into discrete size classes (e.g., 4–5 bins: Small, Medium, Large, Extra Large). Easier to cross-reference with a standard legend.
- **Proportional Symbols:** Symbol size scales continuously and mathematically with feature magnitude:
  $$\text{Radius} = R_{\min} \times \left(\frac{\text{Value}}{\text{Value}_{\min}}\right)^\gamma$$
  *(Flannery's perceptual compensation uses $\gamma = 0.57$ instead of Euclidean $0.5$ because the human eye consistently underestimates circular area differences).*

### 2.2 Point Styling Best Practices
1. **Fill & Stroke Separation:**
   - Always apply a semi-transparent fill (70%–85% opacity) to prevent larger overlapping symbols from completely obscuring smaller underlying ones.
   - Always apply a crisp, darker boundary stroke (0.2–0.4 mm width) to maintain shape definition against varying backgrounds.
2. **Nested Legend Circles:**
   - Display graduated/proportional circles concentric or stacked from largest at bottom to smallest at top, aligned along their southern tangent.

```python
# Applying graduated circle styling via QGIS tools
style_graduated(
    layer="city_populations",
    column="pop_total",
    classes=5,
    mode="jenks",
    ramp="YlOrRd",
    method="size",       # Scales circle radius rather than color ramp
    min_size=2.0,        # Minimum radius in mm
    max_size=14.0        # Maximum radius in mm
)
```

---

## 3. Linear Feature Symbology & Road Hierarchy

Lines convey direction, connectivity, and hierarchical flow. Road and transit networks must follow an unambiguous visual tier:

### 3.1 Road Network Hierarchy Matrix

| Road Class | Fill Color | Fill Width (mm) | Casing Color | Casing Width (mm) | Total Visual Width |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Motorway / Highway** | `#ff9800` (Amber) | 1.2 mm | `#bf360c` | 0.35 mm each side | 1.9 mm |
| **Primary / Arterial** | `#ffc107` (Warm Gold)| 0.8 mm | `#616161` | 0.25 mm each side | 1.3 mm |
| **Secondary / Collector**| `#ffffff` (Clean White)| 0.6 mm | `#9e9e9e` | 0.20 mm each side | 1.0 mm |
| **Local / Residential** | `#ffffff` (White) | 0.35 mm | `#bdbdbd` | 0.15 mm each side | 0.65 mm |
| **Railway** | `#424242` solid line | 0.4 mm | Hash cross-ties | 0.2 mm dashes | 0.8 mm total |

### 3.2 Cased Roads (Stroke-on-Stroke)
Cased roads ensure roads pop above aerial imagery, hillshading, or dense urban building footprints:
- In QGIS, cased lines are constructed using a two-symbol layer stack:
  1. **Bottom Layer (Casing):** Dark stroke with wider width (e.g., 1.4 mm, `#374151`).
  2. **Top Layer (Core):** Bright stroke with narrower width (e.g., 0.9 mm, `#fef08a`).
- **Symbol Levels:** Enable Symbol Levels on the layer so that intersections merge seamlessly without casing lines cutting across road junctions.

### 3.3 Hydrographic Flow Lines
- Streams and rivers should vary in width according to Strahler stream order:
  - 1st Order (Headwaters): 0.15 mm hairline (`#60a5fa`).
  - 2nd–3rd Order: 0.35–0.6 mm (`#3b82f6`).
  - 4th+ Order (Major Rivers): 1.0–2.5 mm with subtle boundary stroke (`#1d4ed8`).

---

## 4. Polygon Symbology & Land Surface Treatment

Polygons define zones, administrative units, land covers, and natural boundaries.

### 4.1 Boundary Line Weight & Color Balance
- **Administrative Borders:**
  - National / State: Dash-dot pattern (`- · - ·`), width 0.7–1.0 mm, dark plum or neutral slate (`#334155`).
  - District / County: Dashed pattern (`- - -`), width 0.4–0.5 mm, `#64748b`.
  - Municipality / Village: Fine dotted pattern (`· · ·`), width 0.2–0.3 mm, `#94a3b8`.
- **Polygon Fill Restraint:**
  - Avoid heavy, 100% opaque, saturated fills unless producing a dedicated thematic choropleth.
  - For baseline territorial boundaries, use transparent fills (`#00000000`) with styled perimeter strokes, or an interior buffer tint (vignette).

### 4.2 Pattern Fills (Stipples & Hatches)
- **Conservation / Protected Areas:** 45° green diagonal hatch (`#10b981`, stroke 0.2 mm, spacing 1.5 mm).
- **Wetlands / Marshes:** Traditional horizontal dash-and-reed stipple pattern.
- **Urban Built-up / Footprints:** Solid neutral warm gray (`#e2e8f0`) with 0.15 mm darker perimeter (`#94a3b8`).

---

## 5. Layer Blending Modes & Transparency

Blending modes (analogous to Photoshop/Illustrator) resolve complex multi-layer interactions without muddying the cartography:

| Blend Mode | Cartographic Use Case | Typical Layer Stacking |
| :--- | :--- | :--- |
| **Multiply** | Combining shaded relief (hillshade) with land cover or hypsometric tints. | Hillshade (top) on Multiply (40%–60% opacity) over color DEM or landcover. |
| **Overlay** | Intensifying contrast between illuminated mountain slopes and valley shadows. | Secondary directional hillshade over primary hypsometric tint. |
| **Screen / Lighten** | Highlighting emergency search zones, beacon radii, or glowing heat centers. | Buffer radius or hotspot aura on a dark base. |
| **Darken** | Preserving fine black linework or labels while overlaying tint polygons. | Cadastral parcel boundaries over satellite imagery. |

---

## 6. Practical QGIS Tool Workflows

### 6.1 Categorized Land Use Styling
```python
# Style categorical vector layer with standard harmonious palette
style_categorized(
    layer="landuse_polygons",
    column="class_name",
    palette={
        "Residential": "#fed7aa",
        "Commercial":  "#fca5a5",
        "Industrial":  "#cbd5e1",
        "Forest":      "#86efac",
        "Water":       "#93c5fd",
        "Agriculture": "#fef08a"
    }
)
```

### 6.2 The Complete Symbology Quality Checklist
1. **Figure-Ground:** Does the main subject immediately detach from the background?
2. **Color Balance:** Are there no more than 6–8 distinct qualitative hues?
3. **Line Casing:** Do roads merge cleanly at intersections without overlapping stroke artifacts?
4. **Perceptual Sizing:** Are proportional symbols scaled so that small features remain visible and large features do not swallow the canvas?
5. **Transparency Check:** Can underlying geographic context be seen beneath dense point or polygon overlays?
