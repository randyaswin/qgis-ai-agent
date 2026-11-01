---
name: coordinate-systems-and-crs
description: Authoritative guide for selecting, calculating, and transforming Coordinate Reference Systems (CRS) in QGIS. Covers UTM zone calculation worldwide, equal-area vs conformal projections, national grids (State Plane, British National Grid, Japan Plane Rectangular, ETRS89), raster/vector reprojection algorithms, and diagnosing coordinate distortion and missing CRS issues.
keywords: crs, coordinate system, projection, utm, epsg, reproject, datum, wgs84, web mercator, epsg 4326, epsg 3857, equal area, distance, distortion, state plane, bng, srs
license: MIT
---

# Coordinate Reference Systems (CRS) & Projections Master Guide

In GIS, spatial accuracy depends entirely on working with the correct Coordinate Reference System. Every distance measurement, buffer, area calculation, slope derivation, and density surface is distorted if performed in the wrong CRS.

---

## 1. The Three Golden Rules of GIS Projections

> [!WARNING]
> 1. **Never perform spatial operations (buffer, distance, area, slope, kernel density) in Geographic CRS (`EPSG:4326` WGS 84).** Degrees are angular measurements, not units of linear distance. 1 degree of longitude is ~111.3 km at the Equator, ~78.8 km at 45° latitude, and 0 km at the poles.
> 2. **Never measure area or distance in Web Mercator (`EPSG:3857` / Pseudo-Mercator).** While standard for web tile basemaps (Google, OSM, Carto), Web Mercator distorts areal scale by a factor of $1 / \cos^2(\text{latitude})$. At 60° latitude, areas appear **4 times larger** than reality!
> 3. **Always reproject data to a local projected metric CRS (UTM or National Grid) before running geometric geoprocessing.**

---

## 2. Universal UTM Zone Calculation Worldwide

The Universal Transverse Mercator (UTM) system divides the globe between 80°S and 84°N into 60 longitudinal zones, each 6° wide:

### Mathematical Formula
Given longitude $\lambda$ (in decimal degrees between $-180.0$ and $+180.0$):
$$\text{Zone} = \lfloor \frac{\lambda + 180}{6} \rfloor + 1$$

### EPSG Lookup Rule
- **Northern Hemisphere (Latitude $\ge 0$):**
  $$\text{EPSG Code} = 32600 + \text{Zone}$$
- **Southern Hemisphere (Latitude $< 0$):**
  $$\text{EPSG Code} = 32700 + \text{Zone}$$

### Practical Examples
| Location | Coordinates | UTM Zone | Target EPSG Code |
| :--- | :--- | :--- | :--- |
| **New York City, USA** | 74.0° W, 40.7° N | Zone 18 North | `EPSG:32618` |
| **London, United Kingdom** | 0.1° W, 51.5° N | Zone 30 North (or BNG `EPSG:27700`) | `EPSG:32630` |
| **Paris, France** | 2.3° E, 48.8° N | Zone 31 North | `EPSG:32631` |
| **Berlin, Germany** | 13.4° E, 52.5° N | Zone 33 North | `EPSG:32633` |
| **Tokyo, Japan** | 139.7° E, 35.7° N | Zone 54 North | `EPSG:32654` |
| **Toyohashi / Aichi, Japan**| 137.4° E, 34.7° N | Zone 53 North | `EPSG:32653` |
| **Singapore** | 103.8° E, 1.3° N | Zone 48 North (or SVY21 `EPSG:3414`) | `EPSG:32648` |
| **Jakarta, Indonesia** | 106.8° E, 6.2° S | Zone 48 South | `EPSG:32748` |
| **Sydney, Australia** | 151.2° E, 33.8° S | Zone 56 South (or MGA56 `EPSG:7856`)| `EPSG:32756` |
| **São Paulo, Brazil** | 46.6° W, 23.5° S | Zone 23 South | `EPSG:32723` |

---

## 3. Large-Scale, National, & Continental Projections

When a study area spans multiple UTM zones (e.g. continental Europe, continental USA, national territory):

1. **Equal-Area Projections (Mandatory for Choropleths & Land Area Statistics):**
   - Preserves true relative area across the entire extent.
   - **Europe:** ETRS89-LAEA Europe (`EPSG:3035`).
   - **Continental United States:** USA Contiguous Albers Equal Area Conic (`ESRI:102003` or `EPSG:5070`).
   - **Global:** Mollweide (`ESRI:54009`) or World Equal Area (`ESRI:54034`).
2. **Conformal Projections (Preserves Local Shapes and Angles):**
   - **Lambert Conformal Conic (LCC):** Standard for mid-latitude countries stretching east-west (e.g., France Lambert-93 `EPSG:2154`, Russia LCC).

---

## 4. Reprojection Workflows in QGIS

### A. Reprojecting Vector Layers (`native:reprojectlayer`)
```python
# Reproject raw vector layer to local UTM metric CRS
run_processing("native:reprojectlayer", {
    "INPUT": "roads_wgs84",
    "TARGET_CRS": "EPSG:32653"
}, output_name="roads_utm53n")
```

### B. Reprojecting Raster Data (`gdal:warpreproject`)
> [!IMPORTANT]
> **Resampling Choice is Critical for Rasters:**
> - For **Continuous Elevation / DEM / Bathymetry / Temperature:** Use **Bilinear** (`RESAMPLING: 1`) or **Cubic** (`RESAMPLING: 2`). Never use Nearest Neighbor, which creates stepped stair-casing artifacts on slopes.
> - For **Discrete Categorical Data (Land Cover, Soils, Zones):** Use **Nearest Neighbor** (`RESAMPLING: 0`). Bilinear creates false fractional intermediate class values (e.g. averaging class 1 water and class 3 forest into class 2 urban)!

```python
# Reproject DEM to metric CRS with Bilinear resampling
run_processing("gdal:warpreproject", {
    "INPUT": "copernicus_dem_30m",
    "TARGET_CRS": "EPSG:32653",
    "RESAMPLING": 1  # 1 = Bilinear
}, output_name="dem_utm53n_metric")
```

---

## 5. Diagnosing & Fixing CRS Issues

### Symptom 1: Vector Layer Does Not Overlap Basemap
- **Cause A (Swapped Lat/Lon Coordinates):** Coordinates like `[35.2, 137.4]` instead of `[137.4, 35.2]`. Extent inspection (`describe_layer`) will show values outside standard longitude ranges.
- **Cause B (Missing / Undefined CRS):** Layer has coordinates in meters (e.g., `[500000, 4200000]`) but CRS is reported as `EPSG:4326` or empty.
- **Fix:** Assign the correct projection without reprojecting data coordinates using `native:assignprojection`.

### Symptom 2: Buffer Creates an Oval Instead of a Circle
- **Cause:** Buffering was performed on a layer in `EPSG:4326` using degree distance (e.g., `0.01`). Because 1 degree latitude $\ne$ 1 degree longitude, the resulting geometry stretches along the north-south axis.
- **Fix:** Reproject to UTM first, then buffer with a metric distance (e.g., `1000` meters).
