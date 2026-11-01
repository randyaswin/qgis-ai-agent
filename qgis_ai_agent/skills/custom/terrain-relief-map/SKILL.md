---
name: terrain-relief-map
description: Comprehensive operational guide for Digital Elevation Model (DEM) processing and terrain morphometry cartography. Covers DEM acquisition (Copernicus 30m, SRTM, LiDAR), metric reprojection to resolve z-factor distortion, hillshade derivation, slope and aspect calculation, topographic contour hierarchies, hypsometric tinting, and relief layer blending.
keywords: dem, dtm, dsm, elevation, relief, hillshade, slope, aspect, contour, hypsometric, terrain, topography, copernicus, srtm, lidar, z-factor, geomorphology
license: MIT
---

# Digital Elevation Model (DEM) & Terrain Cartography Guide

Digital Elevation Models (DEMs) are the foundational grid for geomorphology, watershed hydrology, hazard mapping, and landscape cartography.

---

## 1. DEM Fundamentals: DTM vs DSM

Understanding the elevation model type is critical:
- **Digital Terrain Model (DTM / Bare Earth):** Represents the bare ground surface with trees, vegetation canopy, buildings, and bridges removed. Essential for hydrology, flood inundation modeling, and geological fault mapping.
- **Digital Surface Model (DSM):** Captures the first reflective surface encountered by the sensor, including forest canopies, industrial roofs, and urban infrastructure (e.g. raw Copernicus GLO-30, AW3D30).

---

## 2. The Critical Z-Factor & Reprojection Requirement

> [!CRITICAL]
> **The Z-Factor Catastrophe on Degree Datasets:**
> Global DEMs (SRTM, Copernicus 30m) are distributed in Geographic Coordinates (`EPSG:4326` WGS 84), where horizontal coordinates $(X, Y)$ are measured in **degrees**, but vertical elevation $(Z)$ is measured in **meters**.
>
> At mid-latitudes, $1^\circ \approx 111,320\text{ m}$. If you calculate slope or hillshade directly on degree rasters with the default `Z_FACTOR = 1.0`, QGIS computes $\frac{\Delta Z \text{ (meters)}}{\Delta X \text{ (degrees)}}$. This produces completely corrupted results (slopes of 89.9° everywhere, and pitch-black hillshade)!
>
> **The Universal Solution:** Always reproject the raw DEM into a local projected metric CRS (e.g. UTM) using Bilinear resampling before deriving any terrain products:

```python
# Reproject raw DEM into projected metric CRS with Bilinear resampling
run_processing("gdal:warpreproject", {
    "INPUT": "copernicus_dem_wgs84",
    "TARGET_CRS": "EPSG:32653",
    "RESAMPLING": 1  # 1 = Bilinear (preserves continuous slope smoothly)
}, output_name="dem_metric_utm")
```

---

## 3. Terrain Morphometry Product Derivations

### A. Shaded Relief (Hillshade)
Creates a three-dimensional visual illusion of illuminated mountain relief:
```python
run_processing("native:hillshade", {
    "INPUT": "dem_metric_utm",
    "Z_FACTOR": 1.5,       # 1.2 to 2.0 provides natural vertical exaggeration
    "AZIMUTH": 315.0,      # Northwest lighting prevents pseudoscopic relief inversion
    "V_ANGLE": 45.0        # Solar elevation angle
}, output_name="terrain_hillshade")
```

### B. Terrain Slope Gradient (Degrees & Percent)
Measures the maximum rate of elevation change across adjacent raster cells:
```python
run_processing("native:slope", {
    "INPUT": "dem_metric_utm",
    "Z_FACTOR": 1.0        # 1.0 because both horizontal and vertical units are meters
}, output_name="terrain_slope_degrees")
```

#### Standard Geomorphological Slope Classes
- `0° – 2°` (0–3.5%): **Flat / Plain / Water Accumulation Zone**
- `2° – 5°` (3.5–8.7%): **Gently Undulating / Agricultural Lowlands**
- `5° – 15°` (8.7–26.8%): **Rolling Hills / Moderate Erosion Potential**
- `15° – 25°` (26.8–46.6%): **Steep Slopes / High Runoff / Landslide Susceptibility**
- `> 25°` (> 46.6%): **Very Steep Escarpments / Mountain Cliffs / Rockfaces**

### C. Terrain Aspect (Compass Orientation)
Measures the downhill slope azimuth (0° to 360°, clockwise from North):
```python
run_processing("native:aspect", {
    "INPUT": "dem_metric_utm",
    "Z_FACTOR": 1.0
}, output_name="terrain_aspect")
```
- Flat terrain = `-1`
- North: `337.5° – 22.5°`
- East: `67.5° – 112.5°`
- South (Maximum solar insolation in Northern Hemisphere): `157.5° – 202.5°`
- West: `247.5° – 292.5°`

### D. Topographic Contour Generation
Generate elevation contour lines with `gdal:contour`:
```python
# Select interval based on topographic relief:
# - Lowland flat terrain: 5 m or 10 m
# - Rolling hills: 25 m
# - High mountains: 50 m or 100 m
run_processing("gdal:contour", {
    "INPUT": "dem_metric_utm",
    "BAND": 1,
    "INTERVAL": 50.0,
    "FIELD_NAME": "ELEV"
}, output_name="contours_50m")
```

---

## 4. Professional Cartographic Layer Stacking & Blending

To produce publication-ready terrain cartography, stack and style layers in this exact visual order:

```markdown
[TOP]
1. Elevation Labels (Index contours only, 0.8mm white halo)
2. Vector Contour Lines (Hairline sepia/gray, 0.15mm)
3. Shaded Relief / Hillshade (45–50% Opacity)
4. Hypsometric Elevation Tint Raster (100% Opacity)
5. Muted Keyless Basemap (CartoDB Positron / Voyager at 35% opacity)
[BOTTOM]
```

### Applying Hypsometric Tinting
```python
# Apply natural elevation color ramp
style_raster(
    layer="dem_metric_utm",
    ramp="Spectral",
    invert_ramp=True,
    classes=8
)

# Position hillshade directly above elevation and adjust opacity
update_layer(layer="terrain_hillshade", opacity=0.48)
```

> [!TIP]
> **Never use Rainbow/Jet color ramps for elevation.** Rainbow ramps create false perceptual boundaries and are inaccessible to colorblind viewers. Use `Spectral` (inverted: green lowland -> yellow midland -> red/brown highland), `Viridis`, or `YlOrBr`.

---

## 5. Artifact Diagnostics & Verification
- **Terracing / Stair-stepping:** Low vertical resolution (e.g. 16-bit integer DEMs in flat coastal plains). Smooth using Gaussian filtering if available.
- **Speckle / Spikes:** Cloud artifacts or radar reflection anomalies in global DSMs.
- **Relief Inversion:** If mountains look like sunken trenches, check `AZIMUTH` immediately—it must be set to `315.0°` (Northwest).
- **Report Units:** Always state elevation units clearly as meters above sea level (`m a.s.l.`).
