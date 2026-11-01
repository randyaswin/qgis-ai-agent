---
name: public-cog-stac-duckdb
description: Use when discovering, querying, streaming, or analyzing public geospatial datasets without API keys — including Overture Maps Foundation (buildings, places, transportation, water) via DuckDB over remote S3 GeoParquet, public STAC catalogs (Earth Search Sentinel-2, Landsat, Copernicus DEM GLO-30), and Cloud-Optimized GeoTIFFs (COGs) via GDAL /vsicurl/.
keywords: cog, stac, overture, duckdb, geoparquet, parquet, s3, sentinel, copernicus, dem, glo-30, earth search, planetary computer, open data, public data, satellite imagery, buildings, poi, places, roads, contours, open geodata
license: MIT
---

# Public Open Geospatial Data: Overture Maps, STAC & COG via DuckDB

This skill guides instant, efficient, and **100% keyless** access to global open geospatial datasets.

---

## 1. Keyless Open Data Architecture

1. **Overture Maps Foundation (Meta, Microsoft, Amazon, TomTom):**
   - Comprehensive global vector features (billions of buildings, POIs, roads, administrative divisions).
   - Queried directly from open AWS S3 buckets (`s3://overturemaps-us-west-2/release/...`) in spatially-partitioned GeoParquet format.
   - Executed via embedded **DuckDB** (`spatial` + `httpfs`), reading only the byte ranges matching the active canvas bounding box without downloading the full dataset.
2. **SpatioTemporal Asset Catalogs (STAC APIs):**
   - Access public endpoints like **Earth Search by Element84** (`https://earth-search.aws.element84.com/v1`) or Planetary Computer.
   - Search optical satellite imagery (Sentinel-2 L2A), radar (Sentinel-1), and global elevation (Copernicus DEM 30m).
3. **Cloud-Optimized GeoTIFF (COG) Streaming:**
   - QGIS loads high-resolution rasters directly via virtual GDAL protocol `/vsicurl/https://...`.
   - Fetches only the required tiles and overview pyramids for the current screen viewport on the fly. No multi-gigabyte local file downloads needed.

---

## 2. Catalog & Discovery

Before fetching data, inspect curated public sources:
```python
sources = list_public_geodata_sources()
```
Returns a summary of open STAC collections, Overture themes, and public COG URLs.

---

## 3. Querying Overture Maps via DuckDB

Use `query_overture_maps`:

### A. Key Parameters:
- `theme`: `"places"` | `"buildings"` | `"transportation"` | `"base"` | `"divisions"`.
- `type`: Subtype (auto-selected if omitted, e.g. `"place"`, `"building"`, `"segment"`, `"water"`).
- `bbox`: `[xmin, ymin, xmax, ymax]` in WGS84 EPSG:4326. Defaults to current QGIS canvas extent if omitted.
- `filter_sql`: Optional DuckDB SQL condition to filter attributes.
- `limit`: Maximum features to fetch (default 500, can be increased as needed).
- `output_name`: Resulting layer name in QGIS.

### B. Example Use Cases:
1. **Health Facilities / Hotels / POIs in Canvas Extent:**
   ```python
   query_overture_maps(
       theme="places",
       filter_sql="taxonomy.primary IN ('hospital', 'clinic', 'pharmacy')",
       limit=300,
       output_name="Overture_Hospitals"
   )
   ```
2. **Building Footprints & Heights:**
   ```python
   query_overture_maps(
       theme="buildings",
       filter_sql="height IS NOT NULL OR num_floors > 2",
       limit=1000,
       output_name="Overture_Buildings"
   )
   ```
3. **Road Networks & Primary Highways:**
   ```python
   query_overture_maps(
       theme="transportation",
       filter_sql="class IN ('primary', 'secondary', 'tertiary')",
       limit=800,
       output_name="Overture_Roads"
   )
   ```
4. **Water Bodies & Shorelines:**
   ```python
   query_overture_maps(
       theme="base",
       type="water",
       limit=500,
       output_name="Overture_Water"
   )
   ```

---

## 4. STAC Search & COG Streaming

### A. Searching Satellite Imagery or DEM
Use `search_stac_items`:
```python
items = search_stac_items(
    collections=["cop-dem-glo-30"],     # Global 30m elevation DEM
    # or collections=["sentinel-2-l2a"] # Cloudless optical imagery
    limit=2
)
```
Returns STAC items with direct COG asset URLs (`data` for DEM, `visual` for true-color RGB composite, `red`, `nir`, etc.).

### B. Streaming COG Directly into QGIS
Use `load_public_cog`:
```python
load_public_cog(
    url="https://copernicus-dem-30m.s3.amazonaws.com/.../DEM.tif",
    name="Copernicus_DEM_30m"
)
```
QGIS automatically applies `/vsicurl/` and streams raster data on the fly.

---

## 5. Arbitrary Spatial Queries with DuckDB

For advanced queries joining remote Parquet files on S3 or the web:
```python
query_duckdb_spatial(
    query="""
        SELECT id, names.primary AS name, categories.primary AS category, geometry 
        FROM read_parquet('s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*', hive_partitioning=1)
        WHERE bbox.xmin >= 138.60 AND bbox.xmax <= 138.63
          AND bbox.ymin >= 35.21 AND bbox.ymax <= 35.24
          AND categories.primary IN ('hotel', 'restaurant', 'museum')
        LIMIT 200
    """,
    layer_name="DuckDB_Local_POIs"
)
```
The query result is automatically converted into a temporary GeoJSON and loaded into QGIS as a native vector layer.

---

## 6. Integrated Workflow Patterns

1. **Hazard & Disaster Infographic Enrichment:**
   - Load hazard or administrative boundary layers.
   - Use `query_overture_maps` to pull buildings and critical facilities within hazard zones.
2. **Terrain Shading & Topographic Contours:**
   - Search and load `cop-dem-glo-30` using `search_stac_items` and `load_public_cog`.
   - Run `native:hillshade` or `gdal:contour` from the Processing Toolbox to generate 3D relief without manual elevation downloads.
3. **Modern 3D / Graduated Cartography:**
   - Display Overture buildings with graduated colors by height or floor count (`height`, `num_floors`).
   - Combine with keyless XYZ basemaps (CartoDB Light or Voyager) for clean editorial cartography.
