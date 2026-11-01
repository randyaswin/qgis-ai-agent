# Vector Digitization Snapping & Topology Rules Guide

A comprehensive manual for configuring QGIS snapping options, topological editing, avoiding overlaps, and validating spatial geometry.

---

## 1. Snapping Environment Configuration

Accurate vector digitization requires configuring the QGIS Snapping Toolbar:

### 1.1 Snapping Modes & Hierarchy
1. **Vertex Snapping:** Snaps the cursor to exact geometric vertices of existing features. Indispensable for connecting parcel corners and road junctions.
2. **Segment Snapping:** Snaps the cursor to the nearest point along an edge or boundary line between vertices. Essential for T-intersections and boundary connections.
3. **Intersection Snapping:** Automatically creates a snap point where two independent lines cross each other, even if no explicit vertex exists at the crossing.
4. **Centroid Snapping:** Snaps to polygon or line visual center of mass.

### 1.2 Tolerance Settings
- **Projected Metric CRS (UTM / National Grid):**
  - High-precision cadastre / engineering: **0.5 – 1.5 meters**.
  - General urban planning: **2.0 – 5.0 meters**.
  - Regional / natural features: **10.0 – 25.0 meters**.
- **Screen Pixel Tolerance:** If working across varying zoom levels, setting snapping tolerance to **10 – 15 screen pixels** provides consistent tactile cursor snap regardless of zoom level.

---

## 2. Topological Editing Workflows

### 2.1 Avoid Overlaps (Adjacent Polygons)
When digitizing parcel fabrics, administrative boundaries, or soil classes:
- Enable **"Avoid Overlap on Active Layer"** (or in Project Snapping Settings: *"Overlap: Follow Advanced Configuration"*).
- When drawing a new adjacent polygon, you do not need to meticulously click along the shared boundary:
  1. Simply draw your new boundary on the open side.
  2. Overlap the cursor generously into the interior of the neighboring polygon.
  3. Right-click to finish. QGIS automatically cuts off the overlap, perfectly tracing the shared boundary without leaving slivers or gaps!

### 2.2 Trace Digitizing
- Enable **Trace Digitizing (T key)** on the Snapping toolbar.
- Click the starting vertex on an existing road or boundary line.
- Move the mouse cursor along the path; QGIS automatically traces all intermediate vertices along the existing feature geometry, ensuring 100% boundary coincidence.

---

## 3. Detecting & Resolving Common Geometric Errors

| Geometric Defect | Visual Appearance | Why It Is Dangerous | Automated QGIS Fix |
| :--- | :--- | :--- | :--- |
| **Self-Intersection (Bowtie)** | Polygon edge crosses over itself forming a figure 8. | Causes area calculation errors; invalidates spatial joins and buffer tools. | `run_processing("native:fixgeometries", ...)` |
| **Sliver Polygons** | Microscopic needle-like gap between adjacent polygons. | Inflates feature counts; distorts zoning queries. | `run_processing("native:eliminatesliverpolygons", ...)` |
| **Dangles / Overshoots** | Road line extends slightly past its intersection. | Breaks network topology and shortest-path routing graphs. | `run_processing("native:snapgeometries", ...)` with segment snap |
| **Unclosed Rings** | Polygon boundary does not return to start point. | Data cannot be saved as valid OGC polygon. | `run_processing("native:fixgeometries", ...)` |
| **Duplicate Vertices** | Two identical coordinates consecutive in line. | Unnecessary file bloat; causes vertex editing errors. | `run_processing("native:simplifygeometries", {"TOLERANCE": 0.001})` |
