---
name: advanced-map-layouting
description: Comprehensive cartographic layout design guide. Covers multi-frame layouts (main detail map + regional overview/key map), information and wayfinding signs (tourist boards, station guides, municipal maps), typographic hierarchy, custom legends, scale bars, north arrows, photo insets, coordinate grids, and print-ready export.
keywords: layout, print layout, wayfinding, tourist map, information map, key map, inset map, cartography, graphic design, map frame, typography, poster, banner, legend, scalebar, north arrow, neatline
license: MIT
---

# Advanced Map Layouting & Visual Cartography Guide

This skill provides comprehensive instructions for designing publication-grade cartographic layouts in QGIS, ranging from executive report maps to complex wayfinding information boards and multi-frame atlases.

---

## 1. Composition Architectures & Grid Systems

A professional map layout follows strict proportions, balanced whitespace, and an intuitive visual path for the viewer.

### Standard Layout Archetypes (Strict Rule: Layout Style MUST Vary by Map Theme)

Cartographic layouts must NEVER look identical across different themes. Choose the archetype that strictly matches the map's domain purpose:

1. **Official Spatial Planning & Master Plan (`planning_official` / "Kop Peta"):**
   - **Use For:** City planning, RTRW/RDTR, zoning schemes, land use allocation, municipal master plans (e.g. 都市計画図).
   - **Visual Structure:**
     * Clean outer neatline frame around the entire page.
     * Large Main Map Frame occupying 72–76% width on the left.
     * Full-height structured "Kop Peta" column (24–28% width) on the right:
       1. Agency / Municipal Department Header Box
       2. Formal Map Title & Administrative Subtitle Box
       3. North Arrow & Graphic Scale Bar block
       4. Regional Locator / Inset Key Map with red overview footprint
       5. Categorized Zoning / Land Use Legend
       6. Coordinate System, Datum, & UTM Grid Specifications Box
       7. Official Approval & Data Provenance Box
     * Coordinate grid cross-ticks along map borders.
   - **Tool Call:** `create_styled_layout(name="City_Plan", style_theme="planning_official", title="...", subtitle="...", credits="...", extent_layer="...", key_extent_layer="...")`

2. **Situational Crisis & Hazard Dashboard (`infographic_disaster`):**
   - **Use For:** Flood susceptibility, earthquake risk, wildfire hotspots, emergency management, disaster impact.
   - **Visual Structure:**
     * Dark/High-contrast Top Alert Header with emergency severity indicator badge.
     * Central Hazard Map (70–75% width).
     * Right KPI Metrics Cards (e.g., Total Area Flooded, Vulnerable Population, Evacuation Shelters).
     * Bottom Severity Ramp Dock (color gradient from Low to Extreme) + Emergency Call Center info.
   - **Tool Call:** `create_styled_layout(name="Hazard_Map", style_theme="infographic_disaster", title="...", subtitle="...", credits="...", extent_layer="...")`

3. **National Geographic Classic Editorial (`natgeo`):**
   - **Use For:** Natural reserves, physical geography, environmental conservation, historical atlases.
   - **Visual Structure:**
     * Warm parchment canvas tint (`#fcfbf7`).
     * Elegant double neatline border (thick outer line 0.8mm + thin inner line 0.25mm).
     * Centered classic serif typography (Georgia / Times) with decorative divider line.
     * Floating framed legend inside map with parchment background.
     * Classic single-box scale bar and traditional compass rose.
   - **Tool Call:** `create_styled_layout(name="Nature_Map", style_theme="natgeo", title="...", subtitle="...", credits="...", extent_layer="...")`

4. **Statistical & Demographic Choropleth (`statistical_thematic`):**
   - **Use For:** Population density, census demographics, economic indices, health statistics.
   - **Visual Structure:**
     * Clean, modern minimalist white canvas.
     * Clean sans-serif typography (Arial / Helvetica).
     * Main choropleth map frame (76% width).
     * Right statistical sidebar: Distribution card (Min, Mean, Median, Max) and class break chips (Quantiles / Natural Breaks).
   - **Tool Call:** `create_styled_layout(name="Demographic_Map", style_theme="statistical_thematic", title="...", subtitle="...", credits="...", extent_layer="...")`

5. **Public Transit & Wayfinding Information Board (`wayfinding_transit`):**
   - **Use For:** Railway/bus transit maps, pedestrian wayfinding, tourist boards, university/park navigation.
   - **Visual Structure:**
     * Full-width header banner with transit/directional badge and multilingual subtitles.
     * Left POI Directory / Legend column.
     * Center detail street/station map with "You Are Here" callout marker.
     * Right locator key map and landmark attraction cards.
   - **Tool Call:** `create_wayfinding_layout(...)`

---

## 2. Millimeter Coordinate System & Dimension Math

All QGIS layout tools use millimeter coordinates `(x, y)` where `(0, 0)` is the **top-left corner of the page**:
- `x` increases from left to right.
- `y` increases from top to bottom.

### Standard Page Dimensions (mm)
| Size | Landscape (W × H) | Portrait (W × H) | Recommended Margin (`m`) |
| :--- | :--- | :--- | :--- |
| **A4** | 297 × 210 mm | 210 × 297 mm | 8–10 mm |
| **A3** | 420 × 297 mm | 297 × 420 mm | 10–14 mm |
| **A2** | 594 × 420 mm | 420 × 594 mm | 12–16 mm |
| **A1** | 841 × 594 mm | 594 × 841 mm | 15–20 mm |
| **Letter** | 279.4 × 215.9 mm | 215.9 × 279.4 mm | 8–10 mm |

### Proportional Grid Calculation Template
When composing programmatically, calculate item coordinates relative to canvas width `W` and height `H`:
```python
margin = 12.0
gap = 6.0
header_height = 36.0
footer_height = 12.0

# Available body area
body_y = margin + header_height + gap
body_h = H - body_y - footer_height - margin
available_w = W - 2 * margin

# Left sidebar / Legend (20%)
left_w = available_w * 0.20
left_x = margin

# Main map (52%)
main_w = available_w * 0.52
main_x = left_x + left_w + gap

# Right overview / photos (28% minus gaps)
right_w = available_w - left_w - main_w - 2 * gap
right_x = main_x + main_w + gap
```

---

## 3. Tool Reference & Execution Sequences

### A. The One-Call Wayfinding Builder (`create_wayfinding_layout`)
Creates a complete information sign matching real-world municipal and transit boards:
```python
create_wayfinding_layout(
    name="City_Information_Map",
    title="Welcome to Greenfield",
    subtitles=["Central District · Public Information Map", "Regional Tourism Guide"],
    main_extent_layer="city_center_roads",  # or main_bbox=[xmin, ymin, xmax, ymax]
    key_extent_layer="metropolitan_boundary",
    you_are_here=[lon, lat],
    you_are_here_label="You are here",
    photos=[
        {"path": "/path/to/landmark1.jpg", "caption": "1 Central Railway Station"},
        {"path": "/path/to/landmark2.jpg", "caption": "2 Historic Botanical Gardens"}
    ],
    theme="dark",  # 'dark' | 'earth' | 'light' | 'navy'
    page_size="A2",
    orientation="landscape",
    footer="You are located at Greenfield Central Plaza, Station North Entrance.",
    credits="Data: OpenStreetMap contributors, Overture Maps Foundation | Cartography: QGIS AI Agent"
)
```

### B. Building Custom Compositions From Primitives

#### Step 1: Initialize Empty Canvas
```python
create_blank_layout(
    name="Executive_Report_Map",
    page_size="A3",
    orientation="landscape",
    background="#ffffff"  # or "#fcfbf7" for warm parchment
)
```

#### Step 2: Decorative Header Background & Title
```python
# Solid or tinted header banner shape
add_layout_shape(
    layout="Executive_Report_Map",
    x=10, y=10, width=400, height=22,
    item_id="header_band",
    shape="rectangle",
    fill="#1a237e",
    stroke="none"
)

# Title label with high-contrast text
add_layout_text(
    layout="Executive_Report_Map",
    x=14, y=11, width=280, height=14,
    text="REGIONAL FLOOD IMPACT ASSESSMENT",
    item_id="main_title",
    size=16,
    color="#ffffff",
    bold=True,
    valign="middle"
)

# Subtitle / metadata line
add_layout_text(
    layout="Executive_Report_Map",
    x=14, y=23, width=280, height=7,
    text="Monitoring Cycle: October 2026 · River Basin Authority",
    item_id="subtitle",
    size=9,
    color="#cfd8dc",
    valign="middle"
)
```

#### Step 3: Primary Map Frame
```python
add_layout_map(
    layout="Executive_Report_Map",
    x=10, y=36, width=290, height=230,
    item_id="main_map",
    extent_layer="Study_Area",
    margin=0.03,
    frame_color="#374151",
    frame_width=0.4,
    grid=True,
    grid_interval=5000  # in project CRS units (e.g. 5 km for UTM)
)
```

#### Step 4: Overview / Inset Map with Link Rectangle
```python
# Key map in top-right of layout
add_layout_map(
    layout="Executive_Report_Map",
    x=306, y=36, width=104, height=85,
    item_id="overview_map",
    extent_layer="Province_Boundary",
    overview_of="main_map",      # Automatically draws main map's footprint!
    overview_color="#d32f2f",
    frame_color="#374151",
    frame_width=0.4
)
```

#### Step 5: Legends, Scale Bars, and North Arrows
```python
# Legend locked to specific layers
add_layout_legend(
    layout="Executive_Report_Map",
    x=306, y=126, width=104, height=110,
    item_id="legend",
    map_id="main_map",
    title="Legend",
    layers=["Incident_Points", "Flood_Inundation_Zones", "Road_Network"],
    columns=1,
    title_size=11,
    item_size=8.5,
    symbol_width=6,
    symbol_height=4,
    background="#ffffff",
    frame_color="#d1d5db"
)

# High-Visibility Scale Bar on Main Map (Safe positioning inside frame)
add_layout_scalebar(
    layout="Executive_Report_Map",
    position="bottom-left",  # Or explicit x, y with auto-bounds clamp
    item_id="scalebar",
    map_id="main_map",
    style="Double Box",      # Professional alternating fill (never use plain monotonous lines)
    units="km",
    segments=4,
    units_per_segment=2.5,   # 0, 2.5, 5.0, 7.5, 10.0 km
    font_size=8,
    color="#1e293b",
    secondary_color="#ffffff",
    background="#ffffff",    # Crisp card backdrop so it never blends into raster/vector layers
    frame_color="#cbd5e1"
)

# North Arrow
add_layout_north_arrow(
    layout="Executive_Report_Map",
    x=286, y=42, size=12,
    item_id="north_arrow",
    map_id="main_map",
    style="default",
    color="#1f2937"
)
```

#### Step 6: Map Markers & Callouts ("You Are Here" or Specific POIs)
```python
add_map_markers(
    name="Inspection_Station",
    points=[{"x": 138.614, "y": 35.221, "label": "Observation Post A"}],
    crs="EPSG:4326",
    shape="diamond",
    color="#d32f2f",
    size=6,
    label_size=9,
    label_box="#d32f2f",
    label_position="right"
)
```

#### Step 7: Inspecting & Modifying Items Interactively
Always verify layout item placement before exporting:
```python
# Review all items on page
items = list_layout_items(layout="Executive_Report_Map")
# Programmatically tweak an item position or text
update_layout_item(
    layout="Executive_Report_Map",
    item_id="main_title",
    size=18,
    text="FINAL VALIDATED FLOOD IMPACT MAP"
)
```

#### Step 8: Visual Verification & Deliverable Export
```python
# Step 1: Render inline preview into chat for verification
export_layout(name="Executive_Report_Map", path="preview.png", preview=True)

# Step 2: High-resolution PDF deliverable for client/print
export_layout(name="Executive_Report_Map", path="output_dir/Flood_Report.pdf", dpi=300)
```

---

## 4. Typography Hierarchy & Contrast Rules

1. **Font Pairings:**
   - **Modern / Technical / Wayfinding:** Clean Sans-serif headers (`Arial`, `Helvetica`, `Segoe UI`, `Trebuchet MS`).
   - **Classic / Academic / NatGeo:** High-readability Serif (`Georgia`, `Palatino`, `Times New Roman`).
2. **Size Hierarchy:**
   - Primary Title: 20–32 pt (A4) / 32–48 pt (A2/A1).
   - Subtitle / Region: 11–14 pt.
   - Section Headings (Legend, Inset, Charts): 10–12 pt, Bold, uppercase or small caps.
   - Legend Items & Table Content: 8–9.5 pt.
   - Footnotes, Projection, Credits: 6.5–8 pt.
3. **Contrast & Halos:**
   - Dark text (`#1f2937` or `#000000`) on white or ivory parchment.
   - Light text (`#ffffff` or `#f3f4f6`) on dark navy or charcoal header bars.
   - Any labels placed directly over satellite or raster hillshade **must have a 0.8–1.2 mm text buffer/halo** in a contrasting color.

---

## 5. Quality Checklist Before Export
- [ ] Has the map scale been verified? Are scalebar units appropriate (meters for urban streets, km for regional views)?
- [ ] Does the overview map display an overview boundary rectangle?
- [ ] Are legend items limited to relevant thematic layers, with cryptic code names renamed into readable English?
- [ ] Is the north arrow pointing true to the map frame rotation?
- [ ] Are credits clearly stated (Sources, Date, CRS/Datum)?
- [ ] Is text cleanly separated from map edges with at least 4 mm padding?
