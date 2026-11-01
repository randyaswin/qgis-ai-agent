# Ground Control Point (GCP) Transformation & Residual Analysis Guide

A mathematical and practical reference for selecting transformation models, evaluating residuals, and georeferencing historical or aerial imagery.

---

## 1. Transformation Algorithm Comparison

| Method | Min Points | Equations & Degrees of Freedom | Geometric Invariants | Susceptibility to Edge Runaway |
| :--- | :--- | :--- | :--- | :--- |
| **Linear / Helmert** | 2 | 4 DOF: $X' = aX - bY + c$, $Y' = bX + aY + d$ | Angles and shape preserved; uniform scale. | None (extrapolates linearly). |
| **Affine (Polynomial 1)**| 3 | 6 DOF: $X' = aX + bY + c$, $Y' = dX + eY + f$ | Parallel lines remain parallel; scale can vary by axis. | Minimal. |
| **Polynomial 2** | 6 | 12 DOF: Includes $X^2, Y^2, XY$ quadratic terms | Models parabolic lens distortion or curved paper. | Moderate outside GCP hull. |
| **Polynomial 3** | 10 | 20 DOF: Includes cubic terms $X^3, Y^3, X^2Y, XY^2$ | Models complex non-linear warping. | **Severe** outside GCP hull. |
| **Thin Plate Spline (TPS)**| 4 | Non-parametric radial basis function ($r^2 \ln r$)| Exact zero-residual fit at GCPs; smooth energy minimization. | Moderate (smoothly relaxes). |
| **Projective** | 4 | 8 DOF: Rational linear fraction ($Z$ perspective)| Straight lines remain straight; models perspective tilt. | High if points are near vanishing line. |

---

## 2. GCP Distribution Rules

### 2.1 The Convex Hull Principle
The area of reliable georeferencing is strictly bounded by the **convex hull** of the collected GCPs:
- Any raster pixels lying outside the convex hull are **extrapolated**. For high-degree polynomials (orders 2 and 3), extrapolation causes dramatic, violent distortion (flaring, twisting, or folding).
- **Rule:** Never digitize or measure features outside the outermost GCP perimeter.

### 2.2 Error Budget & Residual Analysis
Residual for GCP $i$:
$$dx_i = X_{\text{calculated}} - X_{\text{target}}, \quad dy_i = Y_{\text{calculated}} - Y_{\text{target}}$$
$$r_i = \sqrt{dx_i^2 + dy_i^2}$$

- **Outlier Detection:** If one GCP has a residual $r_i > 3 \times \text{mean}(r)$, it is almost certainly a typographical blunder (swapped X/Y coordinates) or a misidentified landmark. Disable or relocate the outlier before re-evaluating.

---

## 3. Resampling Method Selection

When transforming pixel grids during warping:

| Resampling Method | Visual Sharpness | Computation Cost | Best Data Type |
| :--- | :--- | :--- | :--- |
| **Nearest Neighbor** | Coarse / Blocky | Lowest | Categorical rasters, land cover classification, parcel index masks. |
| **Bilinear** | Smooth / Subtle blur| Low | Continuous surfaces, Digital Elevation Models (DEMs), slope rasters. |
| **Cubic** | Very Sharp / Crisp | Moderate | Aerial photography, scanned topographic maps, fine text linework. |
| **CubicSpline** | Ultra Smooth | High | High-resolution satellite panchromatic imagery. |
| **Lanczos** | Extreme Sharpness | High | Aerial photography with high frequency detail. |
