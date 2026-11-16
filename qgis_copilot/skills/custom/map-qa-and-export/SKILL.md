---
name: map-qa-and-export
description: The definitive 10-point cartographic quality assurance checklist and multi-format export protocol. Covers visual hierarchy review, label collision elimination, text contrast & halos, legend hygiene, scale bar division precision, colorblind accessibility, and print-ready PDF/PNG/SVG rendering at 300 DPI.
keywords: qa, quality assurance, checklist, export, print layout, pdf, png, svg, 300 dpi, cartographic review, resolution, contrast, typography, colorblind, delivery
license: MIT
---

# Cartographic Quality Assurance & Export Protocol

Before presenting a map deliverable to the user or declaring a task complete, every AI agent must execute this systematic 10-point Quality Assurance (QA) inspection and export protocol.

---

## 1. The 10-Point Cartographic Review Checklist

### 1. Title Block & Clear Hierarchy
- **What:** The subject or thematic phenomenon (e.g., *"RESIDENTIAL FLOOD HAZARD VULNERABILITY"*).
- **Where:** The geographic region, municipality, or jurisdiction (e.g., *"City of Toyohashi, Aichi Prefecture"*).
- **When:** The temporal date or reference census cycle (e.g., *"October 2026 Situation Report"*).
- **Hierarchy:** Primary title (18–32 pt bold), secondary subtitle (10–13 pt regular), metadata/credits (7–8 pt).

### 2. Map Extent, Framing & Margins
- **Aspect Ratio:** Match the page orientation to the geography (Landscape for broad east-west regions, Portrait for elongated north-south corridors).
- **Breathing Margin:** Ensure at least 5–10 mm of white space between the study area boundary and the map frame edges. Never clip the administrative boundary edge.
- **Center of Gravity:** The primary visual focal point should sit at or slightly above the optical center of the layout.

### 3. Legend Hygiene & Readability
- **No Raw Database Column Names:** Replace cryptic field names like `"pop_dens_2"` with clean human-readable titles like `"Population Density (people / km²)"`.
- **Exclude Non-Thematic Clutter:** Do not include background basemaps, scratch clipping masks, or boundary footprints in the legend. Only display symbols requiring reader interpretation.
- **Ordered Classes:** Sort values in logical progression (lowest on bottom/top consistently).
- **Explicit Missing Data Category:** If NULL values exist, represent them in distinct light gray (`#e0e0e0`) labeled as *"No Data Recorded"* rather than hiding them.

### 4. Scale Bar Divisions & Units
- **Intuitive Round Numbers:** Scale bar divisions must use clean, round numbers (e.g., `0  1  2  3  4 km` or `0  100  200  400 m`). Never leave computer-generated fractional numbers like `0  1.37  2.74  4.11 km`.
- **Unit Selection:** Use meters (`m`) for large-scale urban street plans; use kilometers (`km`) for regional and national territories.

### 5. Orientation & North Arrow
- **Linked Map Frame:** Ensure the north arrow is dynamically linked to the main map item so it rotates accurately if the map frame is rotated.
- **Subtle Placement:** Place the north arrow in an unobtrusive corner (top-left or top-right), avoiding occlusion of key geographic features.

### 6. Typographic Legibility & Contrast
- **Text Buffers / Halos:** Any label rendered over aerial photography, satellite imagery, or shaded relief MUST have a **0.8 to 1.2 mm halo buffer** (`set_labels buffer_size=1.0, buffer_color="#ffffff"`) to prevent contrast collisions.
- **Font Restraint:** Never use more than two font families on a single map (e.g., a Sans-serif like `Arial` / `Helvetica` for labels and UI cards, paired with a Serif like `Georgia` for titles).

### 7. Colorblind Safety & Contrast Compliance
- **Avoid Red-Green Collision:** Never distinguish two critical categories solely by red vs green. If red is required for high hazard, pair it with blue, purple, or distinct geometric shapes (triangles vs circles).
- **Perceptually Uniform Palettes:** Use `Viridis`, `Blues`, or `YlOrRd` for graduated sequential data.

### 8. Coordinate Reference System (CRS) Disclosure
- **Mandatory Metadata:** The credits block must state the official projected CRS and Datum (e.g., *"Projected Coordinate System: WGS 84 / UTM Zone 53N (EPSG:32653)"*).

### 9. Neatlines & Grids
- **Neatlines:** Use clean 0.3–0.5 mm frame strokes around map boxes to contain the visual space.
- **Graticules:** For regional maps, add a coordinate grid (`grid=True`) with Degree-Minute or decimal annotations along canvas borders.

### 10. Data Source Attribution & Disclaimer
- **Credits:** List primary data providers, acquisition dates, and licensing:
  `"Data: Overture Maps Foundation, Copernicus DEM 30m, OSM contributors | Cartography: QGIS AI Agent | Date: October 2026"`

---

## 2. Visual QA Verification Loop

Always run the visual feedback loop before finalizing:

```python
# Step 1: Render layout preview into chat
preview_res = export_layout(
    name="Final_Map_Layout",
    path="/tmp/qa_preview.png",
    preview=True
)

# Step 2: Inspect returned preview image for:
# - Overlapping label text collisions
# - Items overflowing outside page boundary
# - Legend symbols too small or truncated
# - Scale bar units mismatched

# Step 3: Programmatically adjust any flawed elements:
update_layout_item(
    layout="Final_Map_Layout",
    item_id="legend",
    y=140,
    height=80
)
```

---

## 3. Publication Delivery Standards

| Format | Target Use Case | Recommended DPI | Export Tool Call |
| :--- | :--- | :--- | :--- |
| **PDF** | High-precision print, client delivery, press | **300 DPI** | `export_layout(name, path=".../map.pdf", dpi=300)` |
| **PNG** | Slides, web reports, email attachments | **200–300 DPI** | `export_layout(name, path=".../map.png", dpi=250)` |
| **SVG** | Graphic designer editing in Illustrator/Inkscape | **300 DPI** | `export_layout(name, path=".../map.svg", dpi=300)` |

### Finalizing Project State
Always save the complete QGIS project state (`.qgz`) into the output directory alongside deliverables:
```python
save_project(path="output_dir/Project_Deliverable.qgz")
```

### Final Report Summary Format
Report completion clearly, concisely, and without AI filler:
- **Output Files:** Absolute file paths to exported PDF, PNG, and `.qgz` project.
- **Data Grounding:** Full list of sources, sensor platforms, and capture dates used.
- **Assumptions & Parameters:** Normalization formulas, classification methods (e.g. Jenks 5 classes), and buffer distances.
- **Limitations:** Honest statement of any screening-level assumptions or missing attributes.
