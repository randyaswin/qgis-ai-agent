# Statistical Classification Methods for Thematic Cartography

In QGIS, graduated symbol renderers partition a continuous attribute into discrete numerical bins. The choice of classification method drastically alters the narrative told by the map.

---

## 1. Mathematical Breakdown of Methods

### Jenks Natural Breaks (`jenks`)
- **Algorithm:** Goodness of Variance Fit (GVF). Iteratively minimizes squared deviations from class means while maximizing variance between classes.
- **When to use:** Data with inherent natural clusters, bi-modal distributions, or severe skewness (e.g., household income, real estate prices).
- **Caution:** Class boundaries are data-specific, meaning you cannot directly compare two different years or two different study areas classified with independent Jenks breaks.

### Quantile Classification (`quantile`)
- **Algorithm:** Divides total observations $N$ by class count $k$, placing exactly $N/k$ features into each category.
- **When to use:** Highlighting relative standing, ranking, or percentiles (e.g., lowest 20% vs highest 20%).
- **Caution:** Features with nearly identical values near class borders may end up in different bins; conversely, features with massive absolute differences in long tails are grouped into the same bin.

### Equal Interval (`equal`)
- **Algorithm:** Step size $S = (X_{\max} - X_{\min}) / k$. Each class has identical range width.
- **When to use:** Continuous physical variables with intuitive intervals (e.g., percentage 0–20%, 20–40%; or rainfall 0–100mm, 100–200mm).
- **Caution:** Severely skewed data will leave most bins empty, with almost all features clustered into a single class.

### Standard Deviation (`stddev`)
- **Algorithm:** Breaks are set at intervals of $0.5\sigma$ or $1.0\sigma$ around the dataset mean $\mu$.
- **When to use:** Highlighting anomalous geographic areas that deviate significantly from average behavior (e.g., mortality rates, economic growth/recession).
- **Palette:** Must be paired with a diverging color ramp (e.g., `RdBu` or `Spectral`).

### Pretty Breaks (`pretty`)
- **Algorithm:** Employs the R/Wilkinson pretty breaks heuristic to generate rounded numbers (multiples of 1, 2, 5, 10, 20, 50, 100).
- **When to use:** Deliverables intended for executive, public, or non-technical audiences where clean round legend numbers enhance trust and readability.

---

## 2. Handling Missing and Special Values
- **True Zero vs NULL:** A count of 0 represents absence of a phenomenon; `NULL` represents absence of data. Color `NULL` polygons in distinct light gray (`#e0e0e0`) with hatch styling or clear legend annotation.
- **Extreme Outliers:** Consider isolating top 1% outliers into an explicit top tier (e.g., "Over 5,000 / km²") so the remaining 99% of units can be classified meaningfully across 4 classes.
