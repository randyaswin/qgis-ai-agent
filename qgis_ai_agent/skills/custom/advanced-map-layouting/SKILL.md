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

### Standard Layout Archetypes (Strict Rule: Layout Style MUST Match Map Theme)

Cartographic layouts must NEVER look identical or follow a one-size-fits-all rigid template. Choose the archetype that strictly matches the map's visual intent, or compose freely using negative space:

1. **Minimalist Modern City Art Poster (`poster_minimalist`):**
   - **Inspired By:** Tokyo City Map Art Print (Reference Image 1).
   - **Use For:** Urban wall art posters, street network graphics, tourism branding, real estate city prints.
   - **Visual Structure:**
     * Clean, full-coverage map frame filling 88–92% of the page area.
     * Elegant white framing mat margin (10–14 mm) around all sides.
     * Minimalist letter-spaced bold uppercase title (e.g. `T O K Y O` with tracked spacing).
     * Secondary subtitle line: Sub-region / Country (e.g. `J A P A N`).
     * Geographic centroid coordinates line (e.g. `35° 41' 22" N · 139° 41' 30" E`).
     * Ultra-discrete copyright / data attribution in tiny 6pt font at bottom corners.
     * Zero distracting legends, zero scale bars, zero north arrows.
   - **Tool Call:** `create_styled_layout(name="Tokyo_Poster", style_theme="poster_minimalist", title="TOKYO", subtitle="JAPAN", extent_layer="city_roads")`

2. **Historical & Antique Engraved Plan (`antique_historical`):**
   - **Inspired By:** Rome Historical Plan & Cadastre (Reference Image 2).
   - **Use For:** Heritage conservation, historical urban evolution, archaeological sites, vintage atlas prints.
   - **Visual Structure:**
     * Warm sepia/parchment background (`#f4ecd8` / `#ede2c8`).
     * Classic ornamental double neatline border (outer 0.8mm + inner 0.25mm line).
     * Coordinate folding / reference grid overlaid across the map (folding folds / quadrant lines).
     * Architectural corner cartouche tucked into open negative space with Roman/Serif typography (`PLAN OF ROME`).
     * Antique single/double-bar ornamental scale bar with classic subdivisions.
     * Historical landmark directory / index table (Roman numerals / numbered POIs) placed along outer margins.
   - **Tool Call:** `create_styled_layout(name="Rome_Antique_Plan", style_theme="antique_historical", title="PLAN OF ROME", subtitle="Surveyed & Engraved", extent_layer="rome_boundary")`

3. **Organic Site Master Plan & Landscape (`planning_official`):**
   - **Inspired By:** Resort / Campus / Park Master Plan (Reference Image 3).
   - **Use For:** Architectural site plans, golf/resort master plans, environmental zoning, campus design.
   - **Visual Structure:**
     * Site-boundary hugging canvas with generous clean whitespace surrounding the organic land parcels.
     * Soft watercolor / natural earth tones (emerald greens, water blues, terracotta roofs).
     * Subtle, unobtrusive title and minimalist legend tucked into the natural whitespace of the page.
     * Transparent or delicate line frames with zero heavy boxes.
   - **Tool Call:** `create_styled_layout(name="Site_Master_Plan", style_theme="planning_official", title="ECO-PARK MASTER PLAN", extent_layer="site_boundary")`

4. **Scientific Dark Mode Thematic Atlas (`scientific_dark`):**
   - **Inspired By:** Cambridge Risk Studies / Global Climate Atlas (Reference Image 4).
   - **Use For:** Climate change projections, extreme temperature days, global risk studies, night-light emissions, oceanic currents.
   - **Visual Structure:**
     * Deep dark canvas (`#181f2a` or `#0f172a`) providing maximum color pop for vibrant magma/inferno/turbo ramps.
     * Full-width continental / world map filling the entire canvas.
     * Floating explanatory text block positioned directly in open ocean negative space (no opaque white box).
     * Continuous horizontal gradient colorbar dock (with Min, Mean, Max values and probability thresholds).
     * Clean institutional / sponsor attribution badges floating along bottom margins.
   - **Tool Call:** `create_styled_layout(name="Global_Risk_Atlas", style_theme="scientific_dark", title="Global Mean Temperature Anomaly", subtitle="Probability of Exceedance 10% · CMIP6 Projections", extent_layer="world_clim")`

5. **Editorial Magazine & Multi-Frame Infographic (`editorial_infographic`):**
   - **Inspired By:** African Soils Magazine Infographic (Reference Image 5).
   - **Use For:** Policy briefs, environmental journalism, UN/World Bank reports, agricultural infographics.
   - **Visual Structure:**
     * Editorial top headline banner with serif/editorial title and 2–3 sentence narrative summary.
     * Primary Hero Map occupying 65–70% width or height.
     * Secondary Inset Map (e.g. desertification vulnerability, regional breakdown) in a corner quadrant.
     * Bold KPI Callout Chips (e.g. `US$ 4 billion lost annually`, `250 million people affected`) with highlight colors floating in negative ocean/desert space.
     * Multi-column compact legend with clear categorization swatches.
     * Built-in horizontal or vertical infographic bar chart comparing statistics across regions.
   - **Tool Call:** `create_styled_layout(name="African_Soils_Report", style_theme="editorial_infographic", title="African Soils", subtitle="Soil degradation and ecological challenges across the African continent", extent_layer="africa_boundary")`

6. **Situational Crisis & Hazard Dashboard (`infographic_disaster`):**
   - **Use For:** Flood susceptibility, earthquake risk, wildfire hotspots, emergency management, disaster impact.
   - **Visual Structure:**
     * High-contrast Top Alert Header with emergency severity indicator badge.
     * Central Hazard Map (filling ~75% of the page width as the dominant Hero element).
     * Right KPI Metrics Cards (Total Area Flooded, Vulnerable Population, Shelter Status).
     * Auto-pruned legend showing strictly hazard tiers, roads, and key landmarks (hillshades & basemaps excluded).
   - **Tool Call:** `create_styled_layout(name="Hazard_Map", style_theme="infographic_disaster", title="TOYOHASHI FLOOD RISK", subtitle="Multi-Criteria Susceptibility & Population Exposure", extent_layer="Flood_Hazard_Zones")`

7. **Bento Grid Modular Dashboard (`bento_dashboard`):**
   - **Inspired By:** Apple & Linear Design System / Executive Geospatial Dashboards.
   - **Use For:** Executive briefings, smart city analytics, multi-criteria suitability dashboards, ESG spatial metrics.
   - **Visual Structure:**
     * Clean slate or dark canvas (`#f1f5f9` or `#0f172a`).
     * Divided into modular bento cards with soft 1px borders (`#cbd5e1` / `#334155`) and rounded corners.
     * Large Hero Map Card (occupying 62% width on the left).
     * Right Stack of Bento Cards:
       1. Title & Executive Summary Card.
       2. Dual KPI Metric Chips (bold numbers, status indicators).
       3. Filtered Auto-Pruned Thematic Legend Card.
       4. CRS, Provenance, & Methodology Metadata Card.
   - **Tool Call:** `create_styled_layout(name="City_Dashboard", style_theme="bento_dashboard", title="URBAN RESILIENCE INDEX", subtitle="Multi-Criteria Analytics", extent_layer="resilience_zone")`

8. **Dual Synchronized Comparison (`dual_comparison`):**
   - **Inspired By:** Scientific Change Detection / Disaster Assessment Atlases.
   - **Use For:** Temporal change (2010 vs 2026), land use transition, pre- vs post-disaster flood inundation, urban expansion.
   - **Visual Structure:**
     * Clean top header with comparison title and subtitle.
     * Dual synchronized map frames side-by-side with locked geographic extent.
     * Left Map (Baseline / Pre-Event) with `PHASE A · BASELINE` badge.
     * Right Map (Scenario / Post-Event) with `PHASE B · IMPACT` badge.
     * Shared bottom dock with unified scale bar, comparison legend, and differential change statistics.
   - **Tool Call:** `create_styled_layout(name="Flood_Comparison", style_theme="dual_comparison", title="TOYOHASHI FLOOD INUNDATION", subtitle="Baseline Dry Season vs 100-Year Flood Scenario", extent_layer="dry_extent", key_extent_layer="flood_extent")`

9. **Corridor & Trail Strip Map (`corridor_trail`):**
   - **Inspired By:** National Park Service Trail Guides / Linear Infrastructure Engineering.
   - **Use For:** River basins, hiking trails, railway corridors, highway pipelines, marathon/cycling routes.
   - **Visual Structure:**
     * Elongated horizontal panoramic aspect ratio.
     * Main route map occupying top 65% of the page.
     * Bottom 25% dedicated to an Elevation Profile & Milestone Strip:
       - Step markers: `[KM 0.0] Trailhead`, `[KM 25.0] Mid-Valley`, `[KM 50.0] Summit`, `[KM 75.0] Terminus`.
       - Key route statistics: Total distance, max elevation, cumulative ascent.
   - **Tool Call:** `create_styled_layout(name="Trail_Route", style_theme="corridor_trail", title="ALPINE CREST TRAIL GUIDE", subtitle="Distance 78.4 km · Cumulative Ascent 3,420 m", extent_layer="trail_line")`

10. **Cyberpunk & Synthwave Neon HUD (`cyberpunk_hud`):**
    - **Inspired By:** Dark Sci-Fi Geospatial Interfaces / High-Tech Command Systems.
    - **Use For:** Telecommunications coverage, night-time satellite radar, cybersecurity spatial feeds, autonomous drone telemetry.
    - **Visual Structure:**
      * Deep obsidian black canvas (`#090a0f`).
      * Glowing neon cyan (`#00f0ff`) neatline and HUD wireframe corner brackets.
      * Monospace typography (`Courier`, `Consolas`, `JetBrains Mono`).
      * Floating telemetry terminal box with real-time indicators (`// HUD RADAR SCAN ACTIVE`).
      * Terminal green (`#10b981`) and neon magenta (`#ff007f`) data callouts.
    - **Tool Call:** `create_styled_layout(name="Cyber_Grid", style_theme="cyberpunk_hud", title="SECTOR 7 TELEMETRY MATRIX", subtitle="Sensor Network Coverage", extent_layer="telemetry_pts")`

11. **Nautical Bathymetric Marine Chart (`nautical_bathymetric`):**
    - **Inspired By:** British Admiralty / NOAA Hydrographic Charts.
    - **Use For:** Coastal navigation, bathymetric surveys, maritime shipping lanes, port authority dredging.
    - **Visual Structure:**
      * Pale hydrographic yellow land tint (`#fbf5dc`).
      * Double outer neatline with alternating nautical mile/degree tick borders.
      * 360-degree traditional compass rose in open water negative space.
      * Formal Hydrographic Cartouche: Chart title, soundings in meters, depth warning notices.
    - **Tool Call:** `create_styled_layout(name="Harbor_Chart", style_theme="nautical_bathymetric", title="TOYOHASHI HARBOR & COASTAL APPROACHES", subtitle="Depths & Soundings in Meters", extent_layer="coastal_bounds")`

12. **National Geographic Classic Editorial (`natgeo`):**
    - **Use For:** Natural reserves, physical geography, environmental conservation, historical atlases.
    - **Visual Structure:** Warm ivory parchment (`#fcfbf7`), elegant double neatline border, centered classic serif typography (Georgia), floating framed legend.
    - **Tool Call:** `create_styled_layout(name="Nature_Reserve", style_theme="natgeo", title="SOUTHERN ALPS BIOSPHERE", extent_layer="reserve_boundary")`

13. **Statistical & Demographic Choropleth (`statistical_thematic`):**
    - **Use For:** Population density, census demographics, economic indices, health statistics.
    - **Visual Structure:** Clean white canvas, sans-serif typography, right statistical sidebar with distribution summary card (Min, Mean, Median, Max) and class break chips.
    - **Tool Call:** `create_styled_layout(name="Census_Choropleth", style_theme="statistical_thematic", title="POPULATION DENSITY BY TRACT", extent_layer="census_tracts")`

14. **Swiss Topographic & Alpine Relief (`relief_topographic`):**
    - **Use For:** Mountain topography, contour analysis, hiking relief.
    - **Visual Structure:** Shaded relief hillshade, fine contour line rendering, coordinate cross-ticks, Swiss Helvetica typography.
    - **Tool Call:** `create_styled_layout(name="Alpine_Relief", style_theme="relief_topographic", title="MOUNT FUJI RELIEF PROFILE", extent_layer="topo_contours")`

15. **Public Transit & Wayfinding Information Board (`wayfinding_transit`):**
    - **Use For:** Railway/bus transit maps, pedestrian wayfinding, tourist boards, university/park navigation.
    - **Visual Structure:** Full-width header banner with directional badge, left POI directory, center detail street map with "You Are Here" callouts, right locator map and photo attraction cards.
    - **Tool Call:** `create_wayfinding_layout(...)`

---

## 2. Negative Space Composition & Freeform Layouting

A major flaw of amateur cartography is forcing every map into a rigid template with a side column. Professional maps use the natural **negative space** (oceans, unpopulated plains, or outer margins) of the subject geometry:

- **Island / Continental Shapes (e.g. Japan, Africa, Rome, UK):** The ocean or surrounding land provides abundant natural empty space. Place titles, legends, north arrows, and KPI chips directly inside these areas with transparent or subtle semi-transparent backgrounds.
- **Zero Opaque Box Clutter:** Never place solid white boxes with heavy black borders unless required by formal government standards. Let elements float naturally.
- **Hero Map Dominance:** The primary map should almost always occupy 70–90% of the visual hierarchy. Ancillary elements (legends, credits, scale bars) must remain subordinate and clean.
- **Spatial Geometry Adaptation:**
  * **Vertical Elongated (e.g. Chile, Vietnam, Italy, Trans-Sumatra):** Use Portrait A4, placing legend and title in the lateral ocean space.
  * **Horizontal Elongated (e.g. Java, Trans-Siberian, Mediterranean):** Use Landscape A4 or panoramic 16:9, placing annotations along bottom gutters.
  * **Circular / Clustered (e.g. Bali, Singapore, Tokyo Metropolitan):** Use full-page square or landscape framing with elements nested in the four outer corners.

---

## 3. Procedural Layout Building: Composing Without Templates

When the map requires a unique artistic or storytelling layout that doesn't fit standard presets, compose programmatically from scratch using layout primitives:

```python
# 1. Initialize Blank Page
create_blank_layout(name="Custom_Magazine_Spread", page_size="A4", orientation="landscape", background="#ffffff")

# 2. Add Hero Map Frame filling 75% width
add_layout_map(layout="Custom_Magazine_Spread", x=8, y=8, width=205, height=194, item_id="main_map", extent_layer="study_area", frame_color="#cbd5e1", frame_width=0.3)

# 3. Add Floating Title directly inside map negative space (e.g. ocean)
add_layout_text(layout="Custom_Magazine_Spread", x=16, y=16, width=90, height=20, text="KWAZULU-NATAL\nCOASTAL VULNERABILITY", item_id="title", size=15, bold=True, color="#0f172a")

# 4. Add Floating KPI Card
add_layout_callout_box(layout="Custom_Magazine_Spread", x=16, y=40, width=60, height=16, title="HIGH RISK POPULATION", subtitle="420,000 residents exposed to coastal surge", badge="TIER 1")

# 5. Add Right Column Legend & Inset Map
add_layout_legend(layout="Custom_Magazine_Spread", x=217, y=8, width=72, height=110, item_id="legend", map_id="main_map", title="RISK CLASSIFICATION", columns=1)
add_layout_inset_map(layout="Custom_Magazine_Spread", x=217, y=122, width=72, height=55, extent_layer="national_boundary", title="REGIONAL OVERVIEW", show_overview=True)

# 6. Add High-Visibility Scale Bar inside map frame
add_layout_scalebar(layout="Custom_Magazine_Spread", position="bottom-left", map_id="main_map", style="Double Box", units="km")

# 7. MANDATORY VISUAL INSPECTION
snapshot_layout(name="Custom_Magazine_Spread")
```

---

## 4. Millimeter Coordinate System & Dimension Math

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

## 5. Tool Reference & Execution Sequences

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
Always verify layout item placement before concluding:
```python
# Review all items on page
items = list_layout_items(layout="Executive_Report_Map")
# Programmatically tweak an item position or text
update_layout_item(
    layout="Executive_Report_Map",
    item_id="main_title",
    size=16,
    text="FINAL VALIDATED FLOOD IMPACT MAP"
)
```

#### Step 8: Visual Verification Loop (`snapshot_layout`)
Never blindly trust template generation. Always call `snapshot_layout()` to view the rendered layout image:
```python
# Capture layout preview directly into chat and agent context:
snapshot_layout(name="Executive_Report_Map")

# Inspect the returned image:
# 1. Does the map fill the page as the hero element (70-85% page coverage)?
# 2. Are all legend items within the container with NO clipping or spilling outside?
# 3. Are background basemaps and hillshades excluded from the legend?
# 4. Are text sizes proportional (title 12-16pt, body 7.5-9pt, footer 6.5-7pt)?
# 5. Is the scale bar clean and not overlapping with other numbers or borders?
# If flaws exist, iteratively fix them before finishing!
```

#### Step 9: Deliverable Export (Only when requested by user)
```python
# High-resolution PDF or image deliverable for client/print:
export_layout(name="Executive_Report_Map", path="output_dir/Flood_Report.pdf", dpi=300)
```

---

## 6. Typography Hierarchy & Contrast Rules

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

## 7. Quality Checklist Before Export
- [ ] Has the map scale been verified? Are scalebar units appropriate (meters for urban streets, km for regional views)?
- [ ] Does the overview map display an overview boundary rectangle?
- [ ] Are legend items limited to relevant thematic layers, with cryptic code names renamed into readable English?
- [ ] Is the north arrow pointing true to the map frame rotation?
- [ ] Are credits clearly stated (Sources, Date, CRS/Datum)?
- [ ] Is text cleanly separated from map edges with at least 4 mm padding?
