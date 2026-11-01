---
name: qgis-processing-toolbox
description: Use when discovering, configuring, automating, or visually interacting with the QGIS Processing Toolbox (440+ algorithms across Native C++, GDAL, GRASS, PDAL, QGIS, and custom models). Covers discovering algorithm IDs, inspecting parameters, automated execution, opening algorithm dialogs, opening the toolbox panel, and the graphical model designer.
keywords: processing, toolbox, geoprocessing, native, gdal, pdal, grass, buffer, clip, intersection, dissolve, contour, hillshade, slope, zonal statistics, raster calculator, shortest path, heatmap, modeler, model designer, spatial workflow, algoritma
license: MIT
---

# QGIS Processing Toolbox & Automation

The QGIS Processing Framework provides over 440 geoprocessing algorithms across native C++, GDAL, PDAL, GRASS, and custom graphical models.

---

## 1. Discovering the Right Algorithm
Never guess algorithm IDs blindly:
1. `list_processing_providers()`: List all active providers (e.g. `native`, `gdal`, `pdal`, `qgis`, `model`).
2. `list_algorithms(query="...", provider="...")`:
   - Example search queries: `"buffer"`, `"clip"`, `"contour"`, `"slope"`, `"raster"`, `"join"`, `"heatmap"`, `"statistics"`.
3. `describe_algorithm(algorithm)`:
   - **Must be called** before executing a new algorithm to inspect exact parameter names, accepted types, default values, and output sinks.

---

## 2. Key Parameters & Execution Rules (`run_processing`)
- **Input Layers:** Accepts either a layer name (e.g. `"districts"`) or a layer ID.
- **Automated Outputs:** The `OUTPUT` parameter defaults to `"TEMPORARY_OUTPUT"` (creates an instant scratch memory layer without cluttering disk storage).
- **Layer Naming:** Provide `output_name="Result_Layer_Name"` so the newly added layer appears clearly labeled in the user's Layers panel.
- **CRS Consistency:**
  - For distance- or area-based operations (buffers, area calculations, kernel density), ensure the layer is in a projected metric CRS (e.g. UTM). If in EPSG:4326 (degrees), reproject first with `native:reprojectlayer`.

---

## 3. Graphical User Interface (GUI) Interaction
When the user asks to see tools or fill forms interactively:
- `open_processing_toolbox()`: Shows or activates the Processing Toolbox dock panel in the main QGIS window.
- `open_processing_algorithm_dialog(algorithm, params)`: Opens the interactive GUI dialog for a specific algorithm with pre-populated parameters.
- `open_model_designer()`: Opens the QGIS Graphical Model Designer window for visual processing workflows (`.model3`).

---

## 4. Frequently Used Algorithms
| Category | Algorithm Name | Algorithm ID | Key Parameters |
| :--- | :--- | :--- | :--- |
| Buffer | Vector Buffer | `native:buffer` | `INPUT`, `DISTANCE`, `SEGMENTS`, `DISSOLVE` |
| Clip | Vector Clip | `native:clip` | `INPUT`, `OVERLAY` |
| Overlay | Intersection | `native:intersection` | `INPUT`, `OVERLAY` |
| Aggregation | Dissolve | `native:dissolve` | `INPUT`, `FIELD` |
| Geometry | Fix Geometries | `native:fixgeometries` | `INPUT` |
| Contours | Contour Lines | `gdal:contour` | `INPUT`, `BAND`, `INTERVAL`, `FIELD_NAME` |
| Shading | Hillshade | `native:hillshade` | `INPUT`, `Z_FACTOR`, `AZIMUTH`, `V_ANGLE` |
| Slope | Slope Calculation | `native:slope` | `INPUT`, `Z_FACTOR` |
| Zonal Stats | Zonal Statistics | `native:zonalstatisticsfb` | `INPUT`, `INPUT_RASTER`, `RASTER_BAND`, `STATISTICS` |
| Density | Heatmap (KDE) | `qgis:heatmapkerneldensityestimation` | `INPUT`, `RADIUS`, `PIXEL_SIZE`, `KERNEL` |
| Routing | Shortest Path | `native:shortestpathpointtopoint` | `INPUT`, `START_POINT`, `END_POINT`, `STRATEGY` |
| Catchment | Service Area | `native:serviceareafrompoint` | `INPUT`, `START_POINT`, `TRAVEL_COST` |
