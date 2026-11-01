# Authoritative National and Regional CRS Quick Reference

When mapping specific countries or regions, local national grids often provide higher metric precision and lower edge distortion than standard UTM zones.

---

## 1. Europe
| Country / Region | CRS Name | EPSG Code | Notes |
| :--- | :--- | :--- | :--- |
| **European Union (Areal Analysis)** | ETRS89 / LAEA Europe | `EPSG:3035` | Standard for all Eurostat & EEA statistical grids |
| **United Kingdom (Great Britain)** | OSGB36 / British National Grid | `EPSG:27700` | Standard for Ordnance Survey data |
| **France** | RGF93 / Lambert-93 | `EPSG:2154` | Official nationwide projection |
| **Germany** | DHDN / 3-degree Gauss-Kruger | `EPSG:31467` / `EPSG:31468` | Or UTM32N / ETRS89 (`EPSG:25832`) |
| **Switzerland** | CH1903+ / LV95 | `EPSG:2056` | Official swisstopo metric grid |
| **Netherlands** | Amersfoort / RD New | `EPSG:28992` | Official Kadaster grid |
| **Spain** | ETRS89 / UTM Zone 30N | `EPSG:25830` | Official mainland IGN grid |
| **Italy** | Monte Mario / Italy zone 1 & 2 | `EPSG:3003` / `EPSG:3004` | Or WGS 84 / UTM 32N (`EPSG:32632`) |

---

## 2. North America
| Country / Region | CRS Name | EPSG Code | Notes |
| :--- | :--- | :--- | :--- |
| **Continental United States (US-wide)** | NAD83 / Conus Albers | `EPSG:5070` | Standard for USGS National Land Cover & US Census |
| **California, USA** | NAD83 / California Albers | `EPSG:3310` | Standard for state agencies |
| **Canada (National)** | Canada Albers Equal Area Conic | `EPSG:102001` | Nationwide mapping |

---

## 3. Asia & Oceania
| Country / Region | CRS Name | EPSG Code | Notes |
| :--- | :--- | :--- | :--- |
| **Japan (Plane Rectangular Zones I–XIX)** | JGD2011 / Japan Plane Rectangular | `EPSG:6669` – `EPSG:6687` | Zone VIII (`EPSG:6676`): Tokyo/Shizuoka; Zone VII (`EPSG:6675`): Aichi/Nagoya |
| **Singapore** | SVY21 / Singapore TM | `EPSG:3414` | Official SLA metric cadastral projection |
| **Australia (National GDA2020)** | GDA2020 / Geoscience Australia Lambert | `EPSG:7844` / `EPSG:7845` | Equal-area continental mapping |
| **Australia (UTM Zones 49–56)** | GDA2020 / MGA Zone 49–56 | `EPSG:7849` – `EPSG:7856` | Local cadastral and engineering grids |
| **Indonesia (UTM Zones 46S–54S)** | WGS 84 / DGN95 / SRGI2013 UTM | `EPSG:32746` – `EPSG:32754` | Java/Bali (`EPSG:32748` / `EPSG:32749` / `EPSG:32750`) |

---

## 4. Global Equal-Area Projections
- **Mollweide:** `ESRI:54009` (Elliptical, excellent for world distribution maps).
- **World Cylindrical Equal Area (Behrmann / Gall-Peters):** `ESRI:54034`.
- **Eckert IV:** `ESRI:54012` (Graceful curved pole lines, excellent balance).
