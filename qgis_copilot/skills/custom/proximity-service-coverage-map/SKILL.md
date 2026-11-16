---
name: proximity-service-coverage-map
description: Comprehensive guide for geographic accessibility, catchment area modeling, and public service coverage analysis. Covers Euclidean distance buffers, multi-ring catchment zones, road network travel-time isochrones, underserved gap extraction (difference analysis), nearest-facility assignment, and served population quantification.
keywords: buffer, service area, catchment, accessibility, coverage, nearest facility, isochrone, travel time, hospitals, fire stations, schools, public services, spatial equity, underserved gap
license: MIT
---

# Proximity, Service Coverage & Catchment Area Analysis Guide

Accessibility and spatial coverage analysis evaluates whether communities have equitable access to essential facilities—such as hospitals, clinics, fire stations, public transit stops, schools, and parks.

---

## 1. Choosing the Appropriate Metric: Euclidean vs Network

> [!IMPORTANT]
> - **Euclidean Straight-Line Distance (`native:buffer` / `native:multiringconstantbuffer`):**
>   - Fast, screening-level approximation.
>   - *Limitation:* Ignores terrain barriers, rivers, highway fences, and real street networks.
>   - *Terminology:* Always describe this as **"Radial / As-the-crow-flies Distance"**, never as "Walking / Driving Distance".
> - **Road Network Service Areas / Isochrones (`native:serviceareafrompoint`):**
>   - Measures real travel distance or travel time along a connected road graph.
>   - *Requirement:* Requires a topologically clean line network with speed or cost attributes.
>   - *Terminology:* Describe as **"Network Distance (km)"** or **"Travel-Time Isochrone (minutes)"**.

---

## 2. Multi-Ring Catchment Workflow (Euclidean)

To evaluate progressive tiers of accessibility (e.g. 500 m walking, 1,000 m transit catchment, 2,000 m cycling, 5,000 m vehicular buffer):

```python
# 1. Reproject facilities into local metric projected CRS
run_processing("native:reprojectlayer", {
    "INPUT": "health_facilities_layer",
    "TARGET_CRS": "EPSG:32653"
}, output_name="facilities_metric")

# 2. Generate Concentric Multi-Ring Buffers
# e.g., 3 rings at 1000m, 3000m, and 5000m
run_processing("native:multiringconstantbuffer", {
    "INPUT": "facilities_metric",
    "RINGS": 3,
    "DISTANCE": 1000.0  # 1000m intervals: 1000m, 2000m, 3000m
}, output_name="catchment_rings_multi")

# 3. Dissolve overlapping rings across facilities to prevent visual chaos
run_processing("native:dissolve", {
    "INPUT": "catchment_rings_multi",
    "FIELD": ["distance"]
}, output_name="catchment_dissolved")

# 4. Style with graduated translucent fills
style_categorized(
    layer="catchment_dissolved",
    field="distance"
)
update_layer(layer="catchment_dissolved", opacity=0.45)
```

---

## 3. Detecting Underserved Deficit Gaps (`native:difference`)

To pinpoint geographic areas that fall outside acceptable service coverage thresholds:

```python
# Compute geometric difference: Boundary MINUS Service Catchment
run_processing("native:difference", {
    "INPUT": "study_area_boundary_metric",
    "OVERLAY": "catchment_dissolved"
}, output_name="underserved_gap_zones")

# Calculate area of the unserved deficit zone
calculate_field(
    layer="underserved_gap_zones",
    field="unserved_area_km2",
    expression="area($geometry) / 1000000.0"
)

# Style gap zones with contrasting warning hatch or red tint
style_single(
    layer="underserved_gap_zones",
    color="#d32f2f",
    opacity=0.35,
    outline_color="#b71c1c",
    width=0.5
)
```

---

## 4. Nearest-Facility Assignment (`native:joinbynearest`)

To calculate the exact straight-line distance from every residential neighborhood centroid to its closest operational facility:

```python
run_processing("native:joinbynearest", {
    "INPUT": "neighborhood_centroids",
    "INPUT_2": "facilities_metric",
    "FIELDS_TO_COPY": ["facility_name", "facility_type"],
    "DISCARD_NONMATCHING": True,
    "PREFIX": "nearest_",
    "MAX_DISTANCE": 25000.0,  # Search up to 25 km
    "NEIGHBORS": 1
}, output_name="neighborhoods_with_nearest_facility")

# Field 'nearest_distance' contains the exact distance in meters
# Convert to kilometers:
calculate_field(
    layer="neighborhoods_with_nearest_facility",
    field="distance_km",
    expression="\"nearest_distance\" / 1000.0"
)
```

---

## 5. Served Population Quantification (`native:zonalstatisticsfb`)

To estimate the actual number of residents living inside vs outside the service catchment zones using a gridded population raster (e.g. WorldPop or GHS-POP):

```python
run_processing("native:zonalstatisticsfb", {
    "INPUT": "catchment_dissolved",
    "INPUT_RASTER": "population_density_raster",
    "RASTER_BAND": 1,
    "COLUMN_PREFIX": "pop_",
    "STATISTICS": [1]  # 1 = Sum
}, output_name="catchment_with_population")
```

---

## 6. Cartographic Presentation & Summary Reporting

### Symbology Rules
1. **Facility Markers:** Dark, high-contrast point markers (`size=4.5 mm`, e.g. dark navy `#0d47a1` or crimson `#b71c1c`) with recognizable SVG icons (hospital cross, flame, academic cap).
2. **Buffer Rings:** Sequential color ramp with lower opacity (`0.40–0.50`) so underlying roads and residential areas remain readable.
3. **Deficit Zones:** Label clearly in the legend: *"Beyond 5 km Service Threshold"*.

### Summary Reporting Table
```markdown
### 🏥 Healthcare Accessibility & Coverage Summary

| Distance Threshold | Catchment Area (km²) | Population Served | % of Municipal Population |
| :--- | :--- | :--- | :--- |
| **Within 1.0 km (Walking)** | 24.5 km² | 68,400 residents | 42.1% |
| **1.0 km – 3.0 km (Bicycle/Transit)** | 48.2 km² | 54,200 residents | 33.4% |
| **3.0 km – 5.0 km (Short Drive)** | 35.1 km² | 26,100 residents | 16.1% |
| **Underserved Deficit (> 5.0 km)** | **18.7 km²** | **13,800 residents** | **8.4%** |
| **Total Study Area** | **126.5 km²** | **162,500 residents** | **100.0%** |
```
