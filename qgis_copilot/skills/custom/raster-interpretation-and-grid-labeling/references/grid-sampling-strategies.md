# Grid Sampling & Tessellation Strategies

This reference outlines guidelines for designing square (fishnet) and hexagonal grids over raster datasets, optimizing cell dimensions, handling coordinate reference system (CRS) projections, and balancing spatial resolution against model context.

---

## 1. Selecting Cell Size Based on Survey Objective & Sensor GSD

The Ground Sampling Distance (GSD) of the underlying raster imagery dictates the minimum sensible grid cell size:

| Sensor / Data Source | Typical GSD (Pixel Size) | Recommended Cell Size | Survey / Labeling Purpose |
| :--- | :--- | :--- | :--- |
| **Drone Orthophoto (UAV)** | $2\text{ cm} - 10\text{ cm}$ | $5\text{ m} \times 5\text{ m}$ to $20\text{ m} \times 20\text{ m}$ | Roof asset inspection, rooftop solar, tree canopy health, pothole/crack detection. |
| **Very High-Res Satellite** (WorldView, Pléiades) | $30\text{ cm} - 50\text{ cm}$ | $20\text{ m} \times 20\text{ m}$ to $50\text{ m} \times 50\text{ m}$ | Cadastral building parcel screening, urban damage assessment, informal settlement mapping. |
| **PlanetScope** | $3\text{ m}$ | $50\text{ m} \times 50\text{ m}$ to $100\text{ m} \times 100\text{ m}$ | Crop parcel classification, forest disturbance, flood extent delineation. |
| **Sentinel-2 (MSI)** | $10\text{ m} - 20\text{ m}$ | $100\text{ m} \times 100\text{ m}$ ($1\text{ ha}$) to $250\text{ m} \times 250\text{ m}$ | Regional land cover classification, wildfire burn severity, vegetation phenology. |
| **Landsat 8/9 (OLI)** | $30\text{ m}$ | $250\text{ m} \times 250\text{ m}$ to $500\text{ m} \times 500\text{ m}$ | Macro-scale watershed monitoring, desertification, regional land degradation. |

> **Rule of Thumb:** A grid cell should contain at least $10 \times 10 = 100$ raster pixels so that visual texture or statistical metrics (mean, standard deviation, percentiles) remain statistically robust.

---

## 2. Square vs. Hexagonal Grids

| Characteristic | Square Grid (Fishnet) | Hexagonal Grid |
| :--- | :--- | :--- |
| **Algorithm Parameter** | `TYPE: 2` in `native:creategrid` | `TYPE: 4` in `native:creategrid` |
| **Neighbor Distance** | Unequal: Orthogonal ($1.0\times$) vs. Diagonal ($1.414\times$). | Identical ($1.0\times$) to all 6 adjacent neighbors. |
| **Perimeter-to-Area Ratio** | Higher ($4\sqrt{A}$). | Lower ($\approx 3.72\sqrt{A}$) — minimizes edge effects. |
| **Cognitive Familiarity** | Matches image pixels, raster matrices, and spreadsheet rows. | Naturalistic, preferred for continuous ecological & movement modeling. |
| **Best Application** | Visual patch classification, raster sampling, machine learning tiling. | Habitat connectivity, disease spread, proximity & accessibility surfaces. |

---

## 3. Coordinate Systems & Metric Spacing Integrity

### 3.1 Always Prefer Projected CRS (UTM or Local Conformal Projection)
- If the project is in a geographic coordinate system (e.g. `EPSG:4326`), spacing parameters are interpreted in **degrees**.
- $0.001^\circ \approx 111\text{ m}$ at the equator, but compresses severely toward high latitudes along the X-axis:
  $$\Delta X \approx 111.32 \cdot \cos(\phi) \text{ km per degree}$$
- **Best Practice:** When creating a grid with metric dimensions (e.g. $100\text{ m}$ cells), set the grid CRS to the appropriate local UTM Zone (e.g. `EPSG:32748` for Java/WGS84 UTM Zone 48S, `EPSG:32632` for Europe UTM Zone 32N).

---

## 4. Batch Inspection & Multimodal Vision Workflow

When inspecting grid cells with a Multimodal Vision Agent:
1. **Divide into Sub-Extents / Quadrants:**
   If a study area has 2,000 grid cells, querying cell-by-cell introduces excessive API overhead. Instead:
   - Partition into blocks of $10 \times 10 = 100$ cells.
   - Render a high-resolution snapshot with labeled grid IDs or center coordinates.
   - Direct the Vision model to output structured JSON:
     ```json
     [
       {"grid_id": 1, "class": "Dense Forest", "confidence": 0.95, "notes": "Granular canopy"},
       {"grid_id": 2, "class": "Water Body", "confidence": 0.98, "notes": "Reservoir"}
     ]
     ```
2. **Post-Process & Vectorize:**
   Execute `label_grid_cells(layer="Sampling_Grid", labels=[...], auto_style=True)` to instantly assign attributes and render the thematic map.

