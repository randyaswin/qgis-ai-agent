---
name: choropleth-density-map
description: End-to-end guide for creating publication-quality choropleth and density maps. Covers attribute data normalization (rate, density, ratio, percentage), statistical classification methods (Jenks, Quantile, Equal Interval, Pretty, StdDev), color theory (ColorBrewer sequential vs diverging ramps), table joins, equal-area projection handling, and legend cartography.
keywords: choropleth, thematic map, density, rate, per capita, population density, administrative, district, census, statistics, normalization, classification, jenks, quantile, color ramp, table join
license: MIT
---

# Choropleth & Thematic Density Mapping Guide

A choropleth map portrays quantitative spatial data aggregated into administrative, political, or statistical polygon units. 

---

## 1. The Fundamental Cartographic Rule: Never Map Raw Counts

> [!IMPORTANT]
> **Never color polygons directly by raw counts** (e.g., total population, total COVID cases, total crimes) across administrative units of unequal size or population. Doing so produces a misleading map that merely reflects unit land area or population size rather than true intensity.

### Mandatory Normalization Strategies
Always calculate a normalized density, rate, ratio, or proportion using `calculate_field`:

1. **Areal Density (e.g., People per km²):**
   ```sql
   "population" / (area($geometry) / 1000000.0)
   ```
   *Prerequisite:* The layer MUST be in a projected metric CRS (e.g., UTM or Equal-Area). In `EPSG:4326`, `area($geometry)` returns square degrees, which is completely invalid.
2. **Per Capita Rate (e.g., Incidents per 10,000 residents):**
   ```sql
   ("incidents" / "population") * 10000.0
   ```
3. **Percentage or Proportion of a Total:**
   ```sql
   ("agricultural_area" / "total_area") * 100.0
   ```
4. **Location Quotient (LQ) or Relative Index:**
   ```sql
   ("local_industry_jobs" / "local_total_jobs") / ("national_industry_jobs" / "national_total_jobs")
   ```

---

## 2. Table Joins & Attribute Hygiene

Most thematic maps involve joining an external statistical table (CSV, Excel, GeoJSON) to a spatial boundary layer:

1. **Key Matching:** Join keys must share identical data types and formatting. Administrative codes often have leading zeros (e.g., `"0102"` vs `102`). Ensure both columns are formatted as String/Text:
   ```python
   # Join boundary layer with census table
   run_processing("native:joinattributestable", {
       "INPUT": "districts_boundary",
       "FIELD": "district_code",
       "INPUT_2": "census_data_table",
       "FIELD_2": "code",
       "METHOD": 1,  # 1:1 join
       "PREFIX": "stat_"
   }, output_name="districts_joined")
   ```
2. **Audit Post-Join Integrity:**
   - Call `describe_layer("districts_joined")` or `field_stats("districts_joined", "stat_population")`.
   - Inspect NULL counts: unmatched rows indicate code discrepancies or boundary version mismatches.
   - Separate NULLs or zero values into an explicit "No Data" category rather than lumping them into the lowest statistical class.

---

## 3. Selecting the Correct Classification Method

Use `field_stats` first to inspect the data distribution (min, max, mean, standard deviation, skewness):

| Data Distribution | Recommended Method (`method=`) | Characteristics & Use Cases |
| :--- | :--- | :--- |
| **Skewed / Long-tailed** | `jenks` (Natural Breaks) | Minimizes within-class variance and maximizes between-class variance. Ideal for real-world census and economic data with extreme outliers. |
| **Rankings / Percentiles**| `quantile` | Places an equal number of polygons in each class. Highlights relative standing (e.g., top 20%, bottom 20%), but may place vastly different values together in sparse tails. |
| **Uniform / Familiar Scales** | `equal` | Divides range `(max - min)` into equal intervals (e.g. 0–20, 20–40). Ideal for percentages or standardized indices. Sensitive to outliers. |
| **Public-Facing / Rounded** | `pretty` | Creates human-friendly round class boundaries (e.g., 10, 25, 50, 100). Highly readable on general public maps. |
| **Diverging / Critical Mean** | `stddev` | Shows distance from the mean in standard deviation units (+1σ, +2σ, -1σ). Requires a diverging color ramp centered on the mean. |

### Number of Classes Rule
- **Use 5 classes as standard** (minimum 4, maximum 7).
- Human visual perception struggles to distinguish more than 7 graduated tints reliably.

---

## 4. Color Theory & Palette Selection

Use ColorBrewer-tested palettes:

1. **Sequential Color Ramps (Single Direction: Low to High):**
   - Population density, wealth, elevation, precipitation:
     - `YlOrRd` (Yellow -> Orange -> Red): High visual energy, warmth, urgency.
     - `Blues` (Light Blue -> Deep Navy): Water, rainfall, calm demographics.
     - `Greens` (Pale Green -> Deep Forest): Agriculture, vegetation, income.
     - `Viridis` (Yellow -> Green -> Purple): Perceptually uniform, excellent for colorblind accessibility and black-and-white printing.
2. **Diverging Color Ramps (Two Extremes Around a Critical Neutral Center):**
   - Population change (+ / -), election swings, standard deviations, temperature anomalies:
     - `RdBu` (Red = Negative, Blue = Positive; or inverted).
     - `Spectral` (Warm Red to Cool Blue with neutral Yellow center).
     - `PiYG` (Pink to Yellow-Green).
3. **Polygon Boundary Outlines:**
   - Thin white outline (`outline_color="#ffffff"`, width 0.2–0.3 mm) provides crisp visual separation between dark saturated units.
   - On light basemaps, use subtle slate gray (`outline_color="#b0bec5"`).

---

## 5. End-to-End Execution Sequence

```python
# 1. Reproject to an equal-area or local metric CRS
run_processing("native:reprojectlayer", {
    "INPUT": "admin_units",
    "TARGET_CRS": "EPSG:32653"
}, output_name="admin_metric")

# 2. Compute density per square kilometer
calculate_field(
    layer="admin_metric",
    field="pop_density",
    expression="\"population\" / (area($geometry) / 1000000.0)"
)

# 3. Apply Graduated Symbology (5 classes, Jenks Natural Breaks)
style_graduated(
    layer="admin_metric",
    field="pop_density",
    classes=5,
    method="jenks",
    ramp="YlOrRd",
    outline_color="#ffffff"
)

# 4. Optional background context: add muted, unobtrusive basemap below
add_basemap(preset="carto_light")
update_layer(layer="CartoDB Positron (No Key)", opacity=0.4)
update_layer(layer="CartoDB Positron (No Key)", position=-1)  # Bottom of stack

# 5. Build Print Layout
create_layout(
    name="Population_Density_Map",
    title="POPULATION DENSITY BY ADMINISTRATIVE DISTRICT",
    subtitle="Normalized Inhabitants per Square Kilometer · 2026 Census",
    credits="Sources: National Bureau of Statistics · Projected CRS: WGS 84 / UTM Zone 53N",
    page_size="A3",
    orientation="landscape",
    legend=True,
    scalebar=True,
    north_arrow=True
)

# 6. Verify and Export
export_layout(name="Population_Density_Map", path="preview.png", preview=True)
```
