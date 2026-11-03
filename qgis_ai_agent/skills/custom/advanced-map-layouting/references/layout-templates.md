# Cartographic Layout Templates & Recipes

This document provides ready-to-use recipes for common layout compositions in QGIS AI Agent.

---

## 1. Wayfinding & Transit Board (Japanese Station / Tourism Style)

### Features:
- Large dark header banner with circled "i" pictogram.
- Dual-language / multilingual title and subtitle lines.
- Left panel: full POI category legend with colored symbols.
- Center frame: high-resolution urban street and walking network.
- Right frame: wide-area regional map with red overview boundary rectangle, plus photo cards of major landmarks.
- Bottom footer: exact current location statement and north arrow.

### Implementation:
```python
create_wayfinding_layout(
    name="Transit_Wayfinding_Board",
    title="Central District Information Map",
    subtitles=["Shizuoka Prefecture · Walking & Transit Guide", "Welcoming All Visitors"],
    main_extent_layer="Urban_Core_Roads",
    key_extent_layer="Prefecture_Boundary",
    you_are_here=[138.614, 35.223],
    you_are_here_label="You are here (North Station Gate)",
    photos=[
        {"path": "photos/castle_park.jpg", "caption": "1 Historic Castle Park"},
        {"path": "photos/museum.jpg", "caption": "2 City Art & Cultural Museum"}
    ],
    theme="dark",
    page_size="A2",
    orientation="landscape",
    footer="Current Location: North Gate Plaza, Station Central District.",
    credits="Sources: OpenStreetMap contributors, Overture Maps Foundation | Date: October 2026"
)
```

---

## 2. National Geographic Classic Style Map

### Features:
- Warm parchment / ivory page tone (`#fcfbf7`).
- Double neatline border around map frame with corner styling.
- Traditional Serif typography (`Georgia` or `Baskerville`).
- Framed legend box with subtle drop shadow or double border.
- Antique decorative north arrow.

### Implementation:
```python
create_styled_layout(
    name="NatGeo_Explorer_Map",
    style_theme="natgeo",
    title="ISLAND OF SUMATRA",
    subtitle="A PHYSICAL AND BIOGEOGRAPHICAL SURVEY",
    credits="Cartography based on SRTM Elevation & GEBCO Bathymetry · WGS 84 / UTM Zone 47N",
    extent_layer="Sumatra_Boundary",
    page_size="A3",
    orientation="landscape",
    grid=True
)
```

---

## 3. Executive Hazard Dashboard (Infographic Style)

### Features:
- Clean white corporate aesthetic with blue/slate accent colors.
- Top title block with date stamp and authoring agency badge.
- Main map with thematic flood or landslide zones.
- Detail capital city / district inset map in top-left or top-right.
- Continuous horizontal gradient bar along the bottom dock showing elevation or incident density.
- Right-hand analytical ranking bar chart showing incident counts per administrative division.

### Implementation:
```python
# 1. Initialize layout frame
create_styled_layout(
    name="Hazard_Situation_Dashboard",
    style_theme="infographic_disaster",
    title="PROVINCIAL DISASTER SITUATION REPORT",
    subtitle="October 2026 Incident Monitoring Cycle",
    credits="National Disaster Management Agency · Copernicus Sentinel-1 & 30m DEM",
    extent_layer="Province_Boundary",
    page_size="A3",
    orientation="landscape",
    grid=True
)

# 2. Add Detail City Inset
add_layout_inset_map(
    layout="Hazard_Situation_Dashboard",
    x=14, y=42, width=80, height=85,
    extent_layer="Capital_District",
    title="DETAIL CAPITAL METROPOLITAN AREA",
    show_overview_on_main=True,
    grid=True
)

# 3. Add Continuous Gradient Color Bar
add_layout_gradient_legend(
    layout="Hazard_Situation_Dashboard",
    x=14, y=245, width=65, height=18,
    title="Elevation (m a.s.l.)",
    ramp="Spectral",
    min_val="0 m",
    max_val="3,142 m",
    invert_ramp=True
)

# 4. Add Summary Bar Chart Card
add_layout_infographic_chart(
    layout="Hazard_Situation_Dashboard",
    x=324, y=42, width=84, height=120,
    title="INCIDENTS BY DISTRICT",
    subtitle="Verified reports · Total 184 cases",
    data=[
        {"label": "District North", "value": 54},
        {"label": "District East", "value": 41},
        {"label": "District Central", "value": 36},
        {"label": "District West", "value": 29},
        {"label": "District South", "value": 24}
    ],
    bar_color="#1565c0"
)
```

---

## 4. Modern Minimalist City Art Poster (`poster_minimalist`)

### Inspired by: Tokyo City Map Art Print (Reference Image 1)
- Full-bleed urban street network filling 88-92% of page.
- Clean black framing mat border.
- Centered modern luxury typography at bottom: Tracked uppercase title (`T O K Y O`), country (`J A P A N`), geographic centroid coordinates.
- Zero legend clutter, zero bulky scale bars, zero north arrow distraction.

### Implementation:
```python
create_styled_layout(
    name="Tokyo_Art_Poster",
    style_theme="poster_minimalist",
    title="TOKYO",
    subtitle="JAPAN",
    credits="35° 41' 22\" N · 139° 41' 30\" E · OpenStreetMap contributors",
    extent_layer="Tokyo_Road_Network",
    page_size="A3",
    orientation="portrait"
)
```

---

## 5. Historical & Antique Engraved Plan (`antique_historical`)

### Inspired by: Plan of Rome (Reference Image 2)
- Warm aged sepia parchment tone (`#f4ecd8`).
- Double neatline border (outer 0.8mm + inner 0.25mm line).
- Authentically referenced coordinate/quadrant grid across the map.
- Classical Roman/Serif corner cartouche title in negative space.
- Antique single box scale bar with serif typography and authentic ink tones.

### Implementation:
```python
create_styled_layout(
    name="Rome_Antique_Plan",
    style_theme="antique_historical",
    title="PLAN OF ROME",
    subtitle="Accurately delineated from authentic historical surveys",
    credits="Monumenta Cartographica Vaticana · Historical Survey Archive",
    extent_layer="Historic_Rome_Boundary",
    page_size="A3",
    orientation="landscape",
    grid=True
)
```

---

## 6. Landscape Architecture & Watercolor Master Plan (`landscape_masterplan`)

### Inspired by: Organic Campus / Resort Master Plan (Reference Image 3)
- Natural warm paper canvas (`#fcfbf7`) with generous whitespace.
- Soft watercolor palette (lush greens, water blues, terracotta roofs).
- Floating architectural title stamp (`MASTER PLAN & DESIGN PROPOSAL`).
- Floating semi-transparent legend badge with rounded borders.
- Delicate `Line Ticks Up` scale bar in forest slate `#2e4033`, zero heavy boxes.

### Implementation:
```python
create_styled_layout(
    name="EcoPark_Master_Plan",
    style_theme="landscape_masterplan",
    title="BOTANIC ECO-PARK MASTER PLAN",
    subtitle="Landscape Architecture & Environmental Zoning",
    credits="Survey & Planning Bureau · Topo Datum: WGS 84",
    extent_layer="Park_Site_Boundary",
    page_size="A3",
    orientation="portrait"
)
```

---

## 7. Scientific Dark Mode Thematic Atlas (`scientific_dark`)

### Inspired by: Cambridge Risk Studies / BAS Climatology Map (Reference Image 4)
- Deep slate charcoal canvas (`#181f2a`).
- High-contrast continuous heat / risk gradient on continental landmasses.
- Floating research context text card in negative ocean space.
- Horizontal continuous gradient ramp legend with discrete threshold markers (`0`, `>200`).
- Clean institutional / partner logo attribution row at bottom.

### Implementation:
```python
create_styled_layout(
    name="Climate_Risk_Atlas",
    style_theme="scientific_dark",
    title="Mean Daily Temperature Exceedance",
    subtitle="1979-2018 vs 2018-2059 Projections · Prob. Exceedance 10%",
    credits="Centre for Risk Studies · Cambridge Zero · British Antarctic Survey",
    extent_layer="World_Clim_Grid",
    page_size="A3",
    orientation="landscape"
)
```

---

## 8. Editorial Magazine & Multi-Frame Infographic (`editorial_infographic`)

### Inspired by: African Soils Magazine Spread (Reference Image 5)
- Editorial headline in elegant serif font (`African soils`) with introductory narrative deck.
- Dominant primary thematic map.
- Secondary inset map in bottom-left corner with overview frame.
- Floating bold KPI callout chips (`250 million`, `US$ 4 billion`) with highlight colors.
- Built-in multi-column classification legend.

### Implementation:
```python
# 1. Base editorial composition with hero map and right column
create_styled_layout(
    name="African_Soils_Report",
    style_theme="editorial_infographic",
    title="African Soils",
    subtitle="Soil degradation is one of the most serious ecological problems facing the African continent.",
    credits="FAO Land Degradation Assessment · ISRIC World Soil Information",
    extent_layer="Africa_Soil_Types",
    key_extent_layer="Africa_Desertification_Risk",
    page_size="A3",
    orientation="landscape"
)

# 2. Add floating bold KPI callout cards inside negative ocean space
add_layout_callout_box(
    layout="African_Soils_Report",
    x=18, y=55, width=60, height=18,
    title="250 MILLION",
    subtitle="People depend on desert soils for subsistence",
    badge="IMPACT"
)

add_layout_callout_box(
    layout="African_Soils_Report",
    x=18, y=130, width=60, height=18,
    title="US$ 4 BILLION",
    subtitle="Lost each year in soil nutrients due to degradation",
    badge="ECONOMIC"
)
```

---

## 9. Procedural Freeform Composition (Custom Bespoke Layout from Scratch)

When none of the presets match the user's custom layout or reference sketch, compose freely using layout primitives:

```python
# Step 1: Create Blank Page
create_blank_layout(name="Bespoke_Atlas", page_size="A4", orientation="landscape", background="#ffffff")

# Step 2: Add Main Map Frame (takes 72% width)
add_layout_map(layout="Bespoke_Atlas", x=8, y=8, width=200, height=194, item_id="main_map",
               extent_layer="Study_Area", frame_color="#334155", frame_width=0.3)

# Step 3: Add Inset Regional Map (bottom-right of right column)
add_layout_inset_map(layout="Bespoke_Atlas", x=214, y=125, width=75, height=65,
                     extent_layer="Regional_Context", title="REGIONAL OVERVIEW", show_overview=True)

# Step 4: Add Floating Title in negative ocean space
add_layout_text(layout="Bespoke_Atlas", x=16, y=16, width=90, height=22,
                text="<b>REGIONAL WATER STRESS ATLAS</b><br><span style='color:#64748b; font-size:8px;'>Multi-basin hydrologic analysis</span>",
                html=True, item_id="title_card")

# Step 5: Add Filtered Legend in right sidebar
add_layout_legend(layout="Bespoke_Atlas", x=214, y=8, width=75, height=110,
                  map_id="main_map", title="HYDROLOGIC BASINS", columns=1)

# Step 6: Add Clean Safe Scalebar (never spills outside frame)
add_layout_scalebar(layout="Bespoke_Atlas", map_id="main_map", position="bottom-left",
                    style="Stepped Line", segments=2, font_size=6.5, color="#0f172a")

# Step 7: Inspect Finished Layout
snapshot_layout(name="Bespoke_Atlas")
```
