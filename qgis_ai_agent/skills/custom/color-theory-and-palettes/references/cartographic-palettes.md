# Curated Cartographic Palette Reference

A comprehensive library of hex color ramps optimized for thematic mapping, shaded relief, accessibility, and high-impact infographics.

---

## 1. Sequential Color Ramps (Unipolar / Continuous)

### 1.1 Blues (Water, Depth, Precipitation)
- 5 Classes: `["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"]`
- 7 Classes: `["#eff3ff", "#c6dbef", "#9ecae1", "#6baed6", "#4292c6", "#2171b5", "#084594"]`

### 1.2 YlGnBu (Vegetation Density, Moisture, Access)
- 5 Classes: `["#ffffcc", "#a1dab4", "#41b6c4", "#2c7fb8", "#253494"]`
- 7 Classes: `["#ffffcc", "#c7e9b4", "#7fcdbb", "#41b6c4", "#1d91c0", "#225ea8", "#0c2c84"]`

### 1.3 YlOrRd (Hazard, Wildfire Risk, Temperature, Density)
- 5 Classes: `["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"]`
- 7 Classes: `["#ffffb2", "#fed976", "#feb24c", "#fd8d3c", "#fc4e2a", "#e31a1c", "#b10026"]`

### 1.4 Purples (Socioeconomic, Education, Planning)
- 5 Classes: `["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"]`
- 7 Classes: `["#f2f0f7", "#dadaeb", "#bcbddc", "#9e9ac8", "#807dba", "#6a51a3", "#4a1486"]`

---

## 2. Diverging Color Ramps (Bipolar / Critical Midpoint)

### 2.1 RdBu (Temperature Anomaly, Surplus/Deficit, Political)
- 5 Classes: `["#ca0020", "#f4a582", "#f7f7f7", "#92c5de", "#0571b0"]`
- 7 Classes: `["#b2182b", "#ef8a62", "#fddbc7", "#f7f7f7", "#d1e5f0", "#67a9cf", "#2166ac"]`

### 2.2 BrBG (Drought / Wetness, Economic Index)
- 5 Classes: `["#a6611a", "#dfc27d", "#f5f5f5", "#80cdc1", "#018571"]`
- 7 Classes: `["#8c510a", "#d8b365", "#f6e8c3", "#f5f5f5", "#c7eae5", "#5ab4ac", "#01665e"]`

### 2.3 PiYG (Agricultural Yield Deviation, Vegetation Health Index)
- 5 Classes: `["#d01c8b", "#f1b6da", "#f7f7f7", "#b8e186", "#4dac26"]`
- 7 Classes: `["#c51b7d", "#e9a3c9", "#fde0ef", "#f7f7f7", "#e6f5d0", "#a1d76a", "#4d9221"]`

---

## 3. Qualitative Palettes (Categorical / Nominal)

### 3.1 ColorBrewer Set2 (Subtle, Highly Elegant, CVD-Friendly)
```json
["#66c2a5", "#fc8d62", "#8da0cb", "#e78ac3", "#a6d854", "#ffd92f", "#e5c494", "#b3b3b3"]
```

### 3.2 ColorBrewer Dark2 (Bold, High Contrast, Excellent on Light Maps)
```json
["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02", "#a6761d", "#666666"]
```

### 3.3 Modern Tech / UI Dark Mode Palette
```json
["#38bdf8", "#4ade80", "#f43f5e", "#fbbf24", "#c084fc", "#fb923c", "#2dd4bf", "#94a3b8"]
```

---

## 4. Hypsometric Elevation & Bathymetric Ramps

### 4.1 Earth Elevation (Lowland Green to Alpine White)
```
  0 m -   100 m : #a3c586 (Soft Meadow Green)
100 m -   300 m : #c2d59b (Yellow-Green Lowland)
300 m -   600 m : #e1e4b5 (Warm Cream Low Hills)
600 m - 1,200 m : #d9be8b (Ochre Foothills)
1,200 m - 2,000 m : #b88655 (Sienna Mountain Slope)
2,000 m - 3,500 m : #8a5738 (Deep Sepia High Range)
> 3,500 m         : #f8fafc (Alpine Snow/Ice)
```

### 4.2 Ocean Bathymetric Depth (Shallow to Abyssal Trench)
```
  0 m -   -20 m : #e0f2fe (Shallow Coastal Cyan)
 -20 m -  -100 m : #bae6fd (Continental Shelf Blue)
-100 m -  -500 m : #7dd3fc (Upper Bathyal Blue)
-500 m - -2000 m : #38bdf8 (Mid Bathyal Deep Blue)
-2000 m - -4000 m : #0284c7 (Abyssal Plain Navy)
< -4000 m         : #082f49 (Deep Oceanic Trench)
```
