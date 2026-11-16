---
name: thematic-mapping-methods
description: Authoritative guide for selecting, constructing, and mastering quantitative thematic mapping methods in QGIS. Covers choropleths (rates and densities), proportional/graduated symbols (absolute totals and Flannery scaling), dot density maps with dasymetric masking, bivariate choropleths (3x3 correlation matrices), flow/desire lines, and cartograms.
keywords: cartography, thematic mapping, choropleth, proportional symbols, graduated symbols, dot density, bivariate choropleth, flow map, desire lines, dasymetric, cartogram, normalization, maup, flannery scaling
license: MIT
---

# Quantitative Thematic Mapping Methods Guide

Thematic cartography communicates spatial patterns, statistical relationships, and geographical distributions. Choosing the wrong thematic method distorts reality, misleads policymakers, and produces visual artifacts like the Modifiable Areal Unit Problem (MAUP).

---

## 1. The Thematic Method Selection Matrix

Match your underlying data attribute structure directly to the correct cartographic representation:

| Data Type | Example Variables | Correct Thematic Method | Prohibited / Flawed Method |
| :--- | :--- | :--- | :--- |
| **Normalized / Ratio / Density** | Pop / km², Poverty %, Disease Rate | **Choropleth Map** | Raw Counts in Choropleth |
| **Absolute Totals / Counts** | Total Population, Total Deaths, Revenue | **Proportional / Graduated Symbols** | Choropleth Map (Distorts by area) |
| **Dispersed Discrete Counts** | Agricultural livestock, Voter distribution| **Dot Density Map** | Homogeneous Choropleth |
| **Dual Correlated Variables** | Flood Risk × Social Vulnerability | **Bivariate Choropleth (3×3)** | Two conflicting overlapping fills |
| **Origin — Destination Flow** | Commuter trips, Trade volume, Migration | **Flow Lines / Desire Lines** | Static point symbols |
| **Structural Redistribution** | Census totals downscaled to residential zones | **Dasymetric Mapping** | Uninhabited administrative boundary |

---

## 2. Choropleth Mapping: The Normalization Rule

### 2.1 The Golden Cardinal Rule
**NEVER map raw counts, totals, or absolute sums directly with a choropleth.**
A large rural district with 10,000 residents will visually overpower a compact urban district with 500,000 residents simply because the rural district has more square kilometers of colored ink.

### 2.2 Normalization Techniques
Always normalize counts by a denominator before styling:
1. **By Area (Density):** $\text{Density} = \frac{\text{Count}}{\text{Area in km}^2}$
2. **By Population (Per Capita / Rate):** $\text{Rate} = \frac{\text{Count}}{\text{Total Population}} \times 1,000$ (or 100,000)
3. **By Total Group (Percentage / Proportion):** $\text{Pct} = \frac{\text{Count}}{\text{Total Census}} \times 100\%$

```python
# Compute normalized density field before styling
calculate_field(
    layer="districts",
    field_name="pop_density_sqkm",
    expression="pop_total / ($area / 1000000.0)",
    field_type="double"
)

# Apply graduated classification
style_graduated(
    layer="districts",
    column="pop_density_sqkm",
    classes=5,
    mode="jenks",
    ramp="YlGnBu"
)
```

---

## 3. Proportional & Graduated Symbol Maps

When visualizing **absolute numbers** (e.g., total tons of cargo, total cases, total population), proportional symbols place circles, squares, or bars at feature locations:

### 3.1 Flannery Perceptual Sizing Formula
Because human perception underestimates circular areas, use Flannery's power law exponent ($\gamma = 0.57$) to scale circle radii:
$$R = R_{\min} \times \left(\frac{V}{V_{\min}}\right)^{0.57}$$

### 3.2 Managing Overlap & Legend Stacking
- **Symbol Transparency:** Apply 70%–80% fill opacity with crisp 0.3 mm dark outlines.
- **Z-Ordering:** Sort features descending by value so smaller circles draw on top of larger circles.
- **Nested Legend:** Display circles aligned along a shared bottom tangent or concentric circles with leader lines to their corresponding values.

---

## 4. Dot Density Mapping

Dot density maps distribute uniform dots within enumeration polygons, where each dot represents a fixed number of individuals or events (e.g., 1 dot = 500 people).

### 4.1 Calibration of Dot Value & Dot Radius
- **Dot Value:** Choose a value where the lowest-count unit has at least 2–3 dots, and the densest unit reaches approximately 60%–70% visual saturation without complete black coalescence.
- **Dot Radius:** Typically 0.4 mm to 0.8 mm at print scale.

### 4.2 Ancillary Masking (Dasymetric Dot Placement)
Never scatter population dots across lakes, steep mountain slopes, or industrial zones:
1. Clip enumeration units by an **Inhabited Land Cover Mask** (e.g., residential/built-up land use).
2. Generate random points only within the masked sub-polygons:
   ```python
   run_processing("native:randompointsinpolygons", {
       "INPUT": "inhabited_zones",
       "STRATEGY": 0,  # Point count per polygon
       "VALUE": "ceil(pop_total / 250.0)",  # 1 dot = 250 people
       "MIN_DISTANCE": 15.0  # meters
   }, output_name="population_dots")
   ```

---

## 5. Bivariate Choropleth Mapping (Two-Variable Correlation)

Bivariate choropleths illustrate the spatial relationship between two distinct continuous variables (e.g., Variable A: Flood Exposure × Variable B: Social Vulnerability).

### 5.1 The $3 \times 3$ Color Matrix
Variables are classified into 3 terciles (Low = 1, Medium = 2, High = 3):
- **Axis 1 (Cyan / Blue):** Low $\to$ High on Variable A.
- **Axis 2 (Pink / Magenta):** Low $\to$ High on Variable B.
- **Intersection (Purple / Slate):** High on both variables.

```
       HIGH B [3]  │  #a5add3 (Low A, High B)    #8b6ca9 (Mid A, High B)    #5c3566 (High A, High B)
       MED  B [2]  │  #c8d7e6 (Low A, Mid B)     #b39eb5 (Mid A, Mid B)     #8c6280 (High A, Mid B)
       LOW  B [1]  │  #e8e8e8 (Low A, Low B)     #cca5b4 (Mid A, Low B)     #a1496a (High A, Low B)
                   └──────────────────────────────────────────────────────────────────────────
                                LOW A [1]                 MED A [2]                 HIGH A [3]
```

### 5.2 Implementation Workflow
1. Calculate 3-class quantile breaks for both Variable A and Variable B.
2. Create a combined 2-digit key field: `to_string(class_A) || to_string(class_B)` (yields `"11"`, `"12"`, ..., `"33"`).
3. Apply `style_categorized` using the $3 \times 3$ color matrix.

---

## 6. Flow Lines & Desire Lines

Flow maps visualize movement across space:
- **Line Width:** Scale stroke width proportionally to volume/passenger count:
  $$\text{Width (mm)} = W_{\min} + \left(\frac{\text{Flow}}{\text{Flow}_{\max}}\right) \times (W_{\max} - W_{\min})$$
- **Geodesic Great Circle Lines:** For intercontinental air or maritime routes, project paths as curved geodesic arcs rather than straight lines across Mercator planes.
- **Directional Styling:** Use gradient fills (bright start $\to$ dark destination) or subtle arrowhead end-markers.
