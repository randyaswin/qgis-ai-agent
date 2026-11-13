---
name: multi-hazard-and-environmental-modeling
description: Multi-hazard risk index and environmental modeling workflows beyond riverine flooding: landslide susceptibility (longsor), wildfire / forest fire risk (karhutla / FWI), urban heat island (UHI / LST + NDVI + NDBI), drought severity (SPI / NDWI / VCI), and composite multi-hazard risk assessment (Hazard x Vulnerability / Exposure).
keywords: landslide, longsor, slope stability, wildfire, forest fire, karhutla, hotspot, urban heat island, uhi, land surface temperature, lst, thermal, drought, kekeringan, multi hazard, multihazard, risk index, kerentanan, kapasitas, inarisk, bnpb, environmental modeling, erosion, rusle
license: MIT
---

# Multi-Hazard & Environmental Modeling Workflow

Use this skill for terrain-triggered hazards (**landslide/longsor**), climate/thermal hazards (**wildfire/karhutla, UHI, drought**), soil erosion (**RUSLE**), and **composite Multi-Hazard Risk** (`Risk = Hazard × Vulnerability ÷ Capacity`, aligned with BNPB/UNDRR frameworks). *(For dedicated floodplain/MCDA flood mapping, pair with `flood-susceptibility-map`.)*

---

## 1. Domain-Specific Parameter Matrices

### A. Landslide Susceptibility (Gerakan Tanah / Longsor)
Derive from metric DEM + geology/soil/landcover in a projected metric CRS:
- **Slope Gradient** (`native:slope`, weights ~30%): Very High `> 30°–45°`, Moderate `15°–30°`, Low `< 8°`.
- **Lithology / Soil Depth & Texture** (20%): Weathered volcanic/clay shales score highest; intact granite/basalt lowest.
- **Rainfall Intensity** (20%): Annual/peak wet-month precipitation interpolated or from CHIRPS/BMKG.
- **Land Cover / Root Cohesion** (15%): Bare soil, open agriculture on steep terrain (Score 5) vs. deep-rooted primary forest (Score 1).
- **Road Cut & Fault Proximity** (15%): Road network buffer `0–50 m` (Score 5) + lineament/fault proximity (`0–200 m`).
- Always verify Aspect/Curvature and exclude flat alluvial plains `< 3°` unless modeling debris-flow runout zones!

### B. Wildfire / Karhutla Risk & Burn Severity
- **Pre-fire Risk (MCDA)**: NDVI fuel dryness / peatland presence (**Gambut/Peat** = top risk!), Land Surface Temperature (LST), distance to roads/settlements (ignition source), seasonal rainfall deficit.
- **Post-fire Burn Severity (`dNBR`)**: `dNBR = NBR_prefire - NBR_postfire` where `NBR = (NIR - SWIR2) / (NIR + SWIR2)`:
  Classify with `native:reclassifybytable`: Unburned (`< 0.1`), Low (`0.1–0.27`), Moderate-Low (`0.27–0.44`), Moderate-High (`0.44–0.66`), High (`> 0.66`).

### C. Urban Heat Island (UHI) & Microclimate
- **Land Surface Temperature (`LST`)**: Derived from Landsat Band 10/11 brightness temperature + NDVI-based emissivity correction (`PV = ((NDVI - NDVI_min)/(NDVI_max - NDVI_min))^2`, `epsilon = 0.004 * PV + 0.986`).
- **UHI Anomaly (`°C`)**: `LST - mean(LST_rural_reference)` or standardized z-score `(LST - mean) / std` to delineate hotspots vs. urban green cool islands (`RTH`).
- **Correlation Check**: Sample raster values across urban zones (`native:rastersampling` or `native:zonalstatisticsfb`) to quantify correlation between `NDBI` (+warming) and `NDVI` (-cooling).

### D. Meteorological & Agricultural Drought (Kekeringan)
- **VCI (Vegetation Condition Index)** & **TCI** / **NDWI** / **MSI**: Identify moisture stress hotspots across crop polygons (`native:zonalstatisticsfb` mean + p10).

---

## 2. Composite Multi-Hazard Risk Pipeline (`BNPB / UNDRR`)
1. **Normalize Every Hazard Layer** to `[0.0 .. 1.0]` on a common metric grid (`gdal:warpreproject`, bilinear for continuous, nearest for categorical).
2. **Maximum-Dominant or Weighted Combination:**
   ```python
   # Multi-Hazard Index: blends weighted intensity with peak-hazard dominance
   expr = "0.6 * ((0.4*A + 0.35*B + 0.25*C)) + 0.4 * max(A, max(B, C))"
   ```
3. **Exposure & Vulnerability Overlay (`Keterpaparan`):**
   Use `native:zonalstatisticsfb` to aggregate hazard grids onto administrative units (`Desa/Kelurahan` or `Kecamatan`), building footprints, or road segments — reporting exact **population / building count / road km in High & Very High classes**!

---

## 3. Classification & Cartographic Standards
- Disclose explicitly in map subtitle/credits that index outputs represent **relative susceptibility/screening indices** and state input factor weights.
- Use sequential **5-tier hazard ramp** (CVD-safe YlOrRd / RdYlBu-reversed, or custom emerald `#2ECC71` -> amber `#F1C40F` -> orange `#E67E22` -> crimson `#C0392B` -> deep maroon `#7B1F1F`).
- For **dNBR / UHI anomalies**, use a diverging palette centered cleanly on neutral neutral-white/cream at 0 anomaly (`RdBu_r` for UHI thermal: blue cool -> red hot).
