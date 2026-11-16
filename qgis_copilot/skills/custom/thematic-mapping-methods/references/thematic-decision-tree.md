# Thematic Mapping Decision Tree & Classification Guide

Use this decision tree to select the optimal quantitative thematic mapping technique based on data attributes and mapping objectives.

---

## 1. Decision Flowchart

```
Start: What is your primary variable to map?
  │
  ├── Is it an absolute count / total sum? (e.g. 15,200 vehicles, 450 fires)
  │     ├── Do you want to emphasize distinct locations / cluster density?
  │     │     └── [PROPORTIONAL / GRADUATED SYMBOLS] (Flannery scaled circles)
  │     └── Do you want to show spatial dispersion within zones?
  │           └── [DOT DENSITY MAP] (1 dot = N units, masked by landcover)
  │
  ├── Is it a rate, ratio, percentage, or density? (e.g. % poverty, persons/km²)
  │     ├── Single variable across bounded polygons:
  │     │     └── [CHOROPLETH MAP] (Normalized by area or total population)
  │     └── Two intersecting variables (e.g. Vulnerability × Hazard):
  │           └── [BIVARIATE CHOROPLETH (3x3)] (Tercile cross-matrix)
  │
  ├── Is it movement between origins and destinations?
  │     └── [FLOW / DESIRE LINE MAP] (Graduated width geodesic corridors)
  │
  └── Is it a continuous spatial surface? (e.g. Elevation, Temperature, Rainfall)
        ├── Elevation / Terrain relief:
        │     └── [HYPSOMETRIC TINTS + HILLSHADE] (Analytical hillshade on Multiply)
        └── Discrete point observations:
              └── [INTERPOLATION / ISARITHMIC MAP] (IDW, Kriging, or KDE Heatmap)
```

---

## 2. Choropleth Classification Method Selection

| Method | When to Choose | Mathematical Basis | Handling Outliers |
| :--- | :--- | :--- | :--- |
| **Natural Breaks (Jenks)**| Default for most socio-economic and demographic distributions. | Minimizes within-class variance; maximizes between-class variance. | Isolates extreme outliers naturally into separate classes. |
| **Quantiles (Equal Count)**| Comparing relative rank across equal numbers of geographic units (e.g. quintiles). | Every bin contains the exact same number of polygon features ($20\%$ each for 5 bins). | Can group widely disparate numbers into the same class at the tails. |
| **Equal Interval** | Evenly spaced intuitive increments (e.g., Temperature: 0–10°C, 10–20°C, 20–30°C). | Class width is constant: $\Delta = \frac{\max - \min}{N}$. | Severely skewed if extreme outliers exist. |
| **Standard Deviation** | Highlighting deviation from the mean (e.g., Above/Below Average). | Classes defined at $\pm 0.5 \sigma$, $\pm 1.0 \sigma$, $\pm 2.0 \sigma$ from $\mu$. | Best paired with a **diverging** color scheme. |
