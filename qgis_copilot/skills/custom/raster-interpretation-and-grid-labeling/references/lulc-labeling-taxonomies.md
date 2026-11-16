# LULC & Image Interpretation Labeling Taxonomies

This document defines standardized classification taxonomies, spectral-visual identification keys, and cartographic color palettes for labeling raster imagery and fishnet/square polygon grids.

---

## 1. Core Land Use / Land Cover (LULC) Classification Hierarchy

Aligned with FAO Land Cover Classification System (LCCS) and USGS Anderson Level I/II standards.

| Level I Class | Level II Subclass | Hex Color | Symbology Rule | Visual Interpretation Keys (RGB & False Color) |
| :--- | :--- | :--- | :--- | :--- |
| **Water Bodies** | Deep Water / Ocean | `#0a3875` | Solid `#0a3875` | Very low reflectance across optical spectrum; dark blue/black in true color; pure black in NIR. |
| | Shallow Water / River | `#2b8cbe` | Solid `#2b8cbe` | Cyan to medium blue; visible sediment or shallow bed texture. |
| | Wetland / Marsh | `#67a9cf` | Hatched / Stippled | Mixed water/vegetation response; greenish-blue with fine mottled texture. |
| **Vegetation & Forest** | Dense Forest / Canopy | `#1b5e20` | Solid `#1b5e20` | Dark green, rough granular canopy texture with tree shadow patterns; very high NIR reflectance. |
| | Open Woodland / Shrub | `#4caf50` | Solid `#4caf50` | Medium green, irregular spacing with exposed soil/grass understory visible. |
| | Grassland / Meadow | `#8bc34a` | Solid `#8bc34a` | Uniform light green to yellowish-green; smooth velvety texture. |
| **Agriculture** | Cropland / Active Crop | `#cddc39` | Solid `#cddc39` | Geometric field boundaries, regular row patterns, uniform texture within plots. High seasonal variation. |
| | Paddy Field (Flooded) | `#80deea` | Solid `#80deea` | Flat rectangular parcels with high specular reflection / water signature and emergent vegetation. |
| | Plantation (Palm, Timber) | `#2e7d32` | Grid Stipple | Highly regular orthogonal or triangular tree planting grid patterns; uniform crown size. |
| **Built-Up / Urban** | High-Density Urban | `#d32f2f` | Solid `#d32f2f` | High concentration of rooftops, concrete, asphalt; complex shadows; high spatial frequency. |
| | Residential / Suburban | `#f44336` | Solid `#f44336` | Regular parcel blocks, mixture of roof tiles, driveways, garden trees, and access roads. |
| | Industrial / Logistics | `#9c27b0` | Solid `#9c27b0` | Very large contiguous roof footprints (warehouses, factories), wide paved loading yards. |
| | Roads / Infrastructure | `#78909c` | Solid `#78909c` | Linear contiguous features with consistent width; smooth grey/black tone; high connectivity. |
| **Bare Land & Soil** | Bare Soil / Fallow | `#d7ccc8` | Solid `#d7ccc8` | Light tan to reddish-brown; smooth to plowed texture; high SWIR reflectance. |
| | Sand / Beach / Dune | `#fff59d` | Solid `#fff59d` | Very bright white to pale yellow; ripple or dune wave patterns; no vegetation. |
| | Exposed Rock / Quarry | `#8d6e63` | Solid `#8d6e63` | Angular edges, steep elevation contours, terraced pit cuts, mining access tracks. |

---

## 2. Disaster & Emergency Damage Assessment Taxonomy

For post-disaster rapid assessment grids (floods, earthquakes, landslides, wildfires).

| Damage Grade | Status Label | Hex Color | Operational Criteria |
| :--- | :--- | :--- | :--- |
| **Grade 0** | No Visible Damage | `#2e7d32` | Structure/canopy intact, normal access roads, no debris or water inundation. |
| **Grade 1** | Minor Damage | `#fbc02d` | Partial roof damage, perimeter debris, standing water in yards but building intact. |
| **Grade 2** | Moderate / Substantial | `#f57c00` | Partial roof collapse, significant structural deformation, silt/mud inundation. |
| **Grade 3** | Severe / Destroyed | `#d32f2f` | Total structural collapse, rubble pile, building washed away, foundation only. |
| **Inundated** | Flooded Submerged | `#0284c7` | Standing water covering roads, parcels, and ground level structures. |
| **Burned** | Burn Scar / Ash | `#374151` | Dark charred soil, complete vegetation destruction, white ash residue. |

---

## 3. Urban Asset & Rooftop Solar / Green Infrastructure Taxonomy

| Asset Type | Category | Hex Color | Key Visual Signatures |
| :--- | :--- | :--- | :--- |
| **Solar PV Array** | `solar_pv` | `#1565c0` | Rectangular blue/dark-grey panels mounted on roofs or ground racks; uniform tilt azimuth. |
| **Green Roof** | `green_roof` | `#66bb6a` | Vegetated plant trays on flat building roofs; distinct from ground gardens. |
| **Swimming Pool** | `pool` | `#00e5ff` | Sharp geometric blue/cyan rectangles or curves within residential backyards. |
| **Informal Settlement** | `informal` | `#ff7043` | High building density, organic non-orthogonal layout, corrugated metal roofs, narrow alleys. |

---

## 4. Multi-Band Spectral Index Signatures

When working with multispectral imagery (e.g. Sentinel-2, Landsat 8/9, PlanetScope):

### 4.1 Normalized Difference Vegetation Index (NDVI)
$$\text{NDVI} = \frac{\text{NIR} - \text{Red}}{\text{NIR} + \text{Red}}$$
- **$\text{NDVI} < 0.0$**: Water, snow, clouds.
- **$0.0 \le \text{NDVI} < 0.2$**: Bare soil, sand, rocks, built-up concrete.
- **$0.2 \le \text{NDVI} < 0.5$**: Sparse vegetation, shrubs, senescent crops.
- **$\text{NDVI} \ge 0.5$**: Dense healthy vegetation, closed forest canopy.

### 4.2 Normalized Difference Water Index (NDWI / McFeeters)
$$\text{NDWI} = \frac{\text{Green} - \text{NIR}}{\text{Green} + \text{NIR}}$$
- **$\text{NDWI} > 0.0$**: Open water bodies, rivers, lakes, flooded paddies.
- **$\text{NDWI} \le 0.0$**: Terrestrial surfaces (soil, vegetation, urban).

### 4.3 Normalized Difference Built-up Index (NDBI)
$$\text{NDBI} = \frac{\text{SWIR} - \text{NIR}}{\text{SWIR} + \text{NIR}}$$
- **$\text{NDBI} > 0.0$**: Impervious built-up surface, urban concrete, asphalt.
- **$\text{NDBI} \le 0.0$**: Vegetation, open water, moist soils.

