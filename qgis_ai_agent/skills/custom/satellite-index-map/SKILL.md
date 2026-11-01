---
name: satellite-index-map
description: Authoritative guide for processing multispectral satellite imagery (Sentinel-2, Landsat 8/9, PlanetScope) and computing spectral environmental indices. Covers band mathematics for NDVI, NDWI, NBR, SAVI, and NDMI, cloud/shadow masking via Scene Classification Layers (SCL), resolution harmonization, raster calculator syntax, and standardized ecological color classification.
keywords: ndvi, ndwi, nbr, savi, ndmi, sentinel-2, landsat, remote sensing, multispectral, vegetation index, water index, burn severity, false color, raster calculator, surface reflectance, earth observation
license: MIT
---

# Satellite Spectral Indices & Remote Sensing Cartography Guide

Multispectral Earth observation satellites capture electromagnetic energy across visible, red-edge, near-infrared (NIR), and shortwave infrared (SWIR) wavelengths. Computing normalized mathematical ratios between these bands isolates distinct biophysical phenomena: chlorophyll concentration, vegetative health, water body boundaries, canopy moisture stress, and wildfire burn severity.

---

## 1. Satellite Sensor Band Reference Matrix

### Sentinel-2 (MSI) vs Landsat 8/9 (OLI)
| Spectral Band Name | Sentinel-2 Band & Resolution | Landsat 8/9 Band & Resolution | Primary Biophysical Sensitivity |
| :--- | :--- | :--- | :--- |
| **Blue** | Band 2 (10 m) | Band 2 (30 m) | Atmospheric scattering, deep water bodies |
| **Green** | Band 3 (10 m) | Band 3 (30 m) | Peak vegetation reflectance, turbid water |
| **Red** | Band 4 (10 m) | Band 4 (30 m) | Strong chlorophyll-a absorption |
| **Red Edge 1/2/3** | Bands 5, 6, 7 (20 m) | N/A | Crop canopy nitrogen, vegetation transition |
| **Near Infrared (NIR)** | **Band 8** (10 m) / Band 8A (20 m) | **Band 5** (30 m) | Internal spongy mesophyll leaf cell structure |
| **Shortwave IR 1 (SWIR-1)**| Band 11 (20 m) | Band 6 (30 m) | Foliar water content, soil moisture |
| **Shortwave IR 2 (SWIR-2)**| Band 12 (20 m) | Band 7 (30 m) | Burn scar charring, rock/mineral types |
| **Quality / Cloud Mask** | SCL (20 m) | QA_PIXEL (30 m) | Clouds, cloud shadows, cirrus, snow |

---

## 2. Key Spectral Indices & Mathematical Formulations

### A. Normalized Difference Vegetation Index (NDVI)
The industry standard measure of photosynthetic activity and green biomass density:
$$\text{NDVI} = \frac{\text{NIR} - \text{Red}}{\text{NIR} + \text{Red}}$$
- **Sentinel-2:** `("B08@1" - "B04@1") / ("B08@1" + "B04@1")`
- **Landsat 8/9:** `("B5@1" - "B4@1") / ("B5@1" + "B4@1")`

### B. Normalized Difference Water Index (NDWI)
1. **McFeeters Surface Water Delineation (Open Water Bodies):**
   $$\text{NDWI}_{\text{water}} = \frac{\text{Green} - \text{NIR}}{\text{Green} + \text{NIR}} = \frac{\text{B03} - \text{B08}}{\text{B03} + \text{B08}}$$
   - Values $> 0$ represent open water bodies (reservoirs, rivers, lakes).
2. **Gao Normalized Difference Moisture Index (NDMI / Canopy Water Content):**
   $$\text{NDMI} = \frac{\text{NIR} - \text{SWIR}_1}{\text{NIR} + \text{SWIR}_1} = \frac{\text{B08} - \text{B11}}{\text{B08} + \text{B11}}$$
   - Indicates plant water stress and drought vulnerability in agricultural fields and forests.

### C. Normalized Burn Ratio (NBR) & Wildfire Severity
$$\text{NBR} = \frac{\text{NIR} - \text{SWIR}_2}{\text{NIR} + \text{SWIR}_2} = \frac{\text{B08} - \text{B12}}{\text{B08} + \text{B12}}$$
- Healthy green vegetation reflects strongly in NIR and absorbs SWIR-2. Burned areas absorb NIR and reflect strongly in SWIR-2.
- **Delta NBR ($\text{dNBR}$):**
  $$\text{dNBR} = \text{NBR}_{\text{pre-fire}} - \text{NBR}_{\text{post-fire}}$$

---

## 3. Data Scaling & Resolution Harmonization

> [!WARNING]
> - **Sentinel-2 L2A BOA Surface Reflectance:** Reflectance values are stored as integers scaled by $10,000$ (e.g. reflectance $0.25 = 2500$). Because normalized difference formulas divide by sum, the scale factor cancels out algebraically, but you must ensure float calculations!
> - **Resolution Mismatch:** Sentinel-2 NIR (B08) is 10 m, while SWIR bands (B11, B12) and SCL are 20 m. Always resample 20 m bands to 10 m before combining in `native:rastercalc`!

```python
# Harmonize 20m SWIR-1 band to match 10m NIR grid
run_processing("gdal:warpreproject", {
    "INPUT": "sentinel2_B11_20m",
    "TARGET_CRS": "EPSG:32653",
    "TARGET_RESOLUTION": 10.0,
    "RESAMPLING": 1  # 1 = Bilinear for continuous reflectance
}, output_name="sentinel2_B11_10m")
```

---

## 4. Cloud & Shadow Masking via SCL

Raw optical imagery often contains cumulus clouds, high cirrus, and dark cloud shadows that distort index values. Sentinel-2 Level-2A includes a 20m Scene Classification Layer (SCL):

| SCL Value | Description | Action in Masking |
| :--- | :--- | :--- |
| **0** | No Data | Exclude |
| **1, 2** | Saturated / Defective | Exclude |
| **3** | Dark Area / Shadow | Exclude |
| **4** | Vegetation | **Keep** |
| **5** | Bare Soil / Not Vegetated | **Keep** |
| **6** | Water Bodies | **Keep** |
| **7** | Unclassified | **Keep** |
| **8, 9, 10**| Medium/High Cloud, Cirrus | Exclude |
| **11** | Snow / Ice | Exclude (or keep for glaciers) |

```sql
-- Cloud Mask Condition in Raster Calculator:
CASE WHEN "scl@1" IN (4, 5, 6, 7) THEN 1.0 ELSE 0.0 END
```

---

## 5. Computing NDVI in QGIS Raster Calculator

```python
# Compute NDVI with zero-division protection:
calc_expr = (
    "(\"B08@1\" - \"B04@1\") / "
    "(\"B08@1\" + \"B04@1\" + ((\"B08@1\" + \"B04@1\") = 0) * 0.0001)"
)

run_processing("native:rastercalc", {
    "LAYERS": ["sentinel2_B08", "sentinel2_B04"],
    "EXPRESSION": calc_expr
}, output_name="ndvi_calculated")
```

---

## 6. Standardized Ecological Interpretation & Classification

Never stretch NDVI arbitrarily between local min and max, as this prevents comparison across different seasons. Use fixed ecological thresholds:

| NDVI Value Range | Ecological Classification | Color Palette |
| :--- | :--- | :--- |
| **$< 0.00$** | Water bodies, snow, or thick cloud | Deep Azure Blue (`#0d47a1`) |
| **$0.00 – 0.20$** | Bare soil, sand, rock, urban impervious surfaces | Sandy Buff / Tan (`#d7ccc8`) |
| **$0.20 – 0.40$** | Shrubland, sparse vegetation, senescent crops | Pale Yellow-Green (`#dce775`) |
| **$0.40 – 0.60$** | Moderate canopy density, healthy agricultural crops| Medium Leaf Green (`#66bb6a`) |
| **$> 0.60$ ($0.60 – 1.00$)**| Dense forest canopy, peak agricultural vigor | Deep Forest Green (`#1b5e20`) |

Style the raster with `style_raster(layer="ndvi_calculated", ramp="RdYlGn", classes=5)`.

---

## 7. Composite Color Presentations (RGB Stacks)

To render true-color or false-color infrared composites from individual band GeoTIFFs:
1. **True Color (Natural View):** Red = Band 4, Green = Band 3, Blue = Band 2.
2. **Standard False Color Infrared (Vegetation Vitality):**
   - Red = **Band 8 (NIR)**, Green = **Band 4 (Red)**, Blue = **Band 3 (Green)**.
   - Healthy green vegetation reflects strongly in NIR and appears **brilliant crimson red**. Clear water absorbs NIR and appears **deep black or navy**.
3. **Agriculture & Moisture:** Red = Band 11 (SWIR), Green = Band 8 (NIR), Blue = Band 2 (Blue).

---

## 8. Map Metadata & Disclosures
Every satellite index map deliverable must state:
- **Satellite Platform & Sensor:** (e.g. *Copernicus Sentinel-2B MSI* or *USGS/NASA Landsat 9 OLI-2*).
- **Processing Level:** Level-2A Bottom-Of-Atmosphere (BOA) Surface Reflectance.
- **Acquisition Date:** Exact capture timestamp (e.g. *Acquired 2026-10-14 02:35 UTC*).
- **Cloud Cover Percentage:** (e.g. *Scene Cloud Cover: 1.2%*).
- **Index Formula:** (e.g. *$\text{NDVI} = (\text{B08} - \text{B04}) / (\text{B08} + \text{B04})$*).
- **Caution:** Never label raw NDVI as "crop yield" or "disease outbreak" without field-validated ground truth.
