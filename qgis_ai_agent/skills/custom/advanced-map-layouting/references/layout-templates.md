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
