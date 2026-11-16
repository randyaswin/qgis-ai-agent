# Cartographic Typographic Hierarchy Matrix

A complete reference matrix for map typography across scale ranges, document formats, and geographic feature classes.

---

## 1. Scale-Dependent Typographic Matrix (A3 / A4 Print)

| Feature Level | Target Scale | Font Family | Style / Weight | Size (pt) | Tracking | Halo (mm) | Color (Hex) |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Map Main Title** | Layout Header | Sans or Serif | Bold | 26–34 pt | +0.5 pt | None | `#0f172a` |
| **Map Subtitle / Meta** | Layout Header | Sans-Serif | Medium | 11–14 pt | 0.0 pt | None | `#475569` |
| **Country / Sovereign State**| 1:5M – 1:25M | Sans-Serif | Bold / Uppercase | 14–18 pt | +4.0 pt | 1.5 mm | `#1e293b` |
| **State / Province** | 1:500k – 1:5M | Sans-Serif | Bold / Small Caps| 11–13 pt | +2.5 pt | 1.2 mm | `#334155` |
| **Capital City** | 1:50k – 1:500k | Sans-Serif | Bold | 10–12 pt | +0.5 pt | 1.0 mm | `#09090b` |
| **Secondary City / District**| 1:25k – 1:100k | Sans-Serif | Medium / Semibold| 8.5–9.5 pt| 0.0 pt | 0.8 mm | `#18181b` |
| **Town / Village / Suburb** | 1:10k – 1:50k | Sans-Serif | Regular | 7.0–8.0 pt| 0.0 pt | 0.7 mm | `#3f3f46` |
| **Major Mountain / Peak** | Any | Serif | Bold / Small Caps| 9.0–11 pt | +1.5 pt | 1.0 mm | `#451a03` |
| **Mountain Range** | Regional | Serif | Italic / All Caps| 12–16 pt | +6.0 pt | 1.2 mm | `#78350f` |
| **Major River (Curved)** | Any | Serif or Sans | Italic | 8.5–10 pt | +1.0 pt | 0.8 mm | `#1d4ed8` |
| **Lake / Reservoir** | Any | Serif or Sans | Italic | 8.0–10 pt | +0.5 pt | 0.8 mm | `#2563eb` |
| **Ocean / Sea / Gulf** | Regional/Global | Serif | Italic / All Caps| 14–22 pt | +8.0 pt | None | `#1e3a8a` |
| **Motorway / Highway Code** | 1:10k – 1:100k | Sans Condensed | Bold (Shield) | 6.5–7.5 pt| 0.0 pt | None | `#ffffff` on `#dc2626` |
| **Local Street Name** | 1:5k – 1:20k | Sans Condensed | Regular | 6.0–7.0 pt| 0.0 pt | 0.5 mm | `#52525b` |
| **Contour Line Elevation** | Topographic | Sans or Monospace| Medium | 5.5–6.5 pt| 0.0 pt | 0.4 mm | `#78350f` |

---

## 2. Text Placement Geometry Rules

### Point Features
```
       [Top-Left: 8]       [Top-Center: 2]       [Top-Right: 10 (Preferred)]
                     \            |            /
                       \          |          /
     [Mid-Left: 5] ----- [ FEATURE POINT ] ----- [Mid-Right: 6]
                       /          |          \
                     /            |            \
    [Bottom-Left: 4]    [Bottom-Center: 1]    [Bottom-Right: 3]
```

### Linear Features
- **Roads:** Always place text above the line (`offset = 0.3 mm`) or directly in-line with automatic stroke knock-out.
- **Rivers:** Flow direction alignment. If the river flows east-to-west, ensure QGIS is configured to prevent upside-down text (*"Show upside-down labels: Never"*).
