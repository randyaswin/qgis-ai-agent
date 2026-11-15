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
   - `query_overture_maps` uses **Overture PMTiles v3 (`tiles.overturemaps.org`)** byte-range tile fetching first (sub-second to ~3s for city neighborhoods), with automatic **STAC-indexed single-pass GeoParquet** fallback (`stac.overturemaps.org` + DuckDB `httpfs`/`spatial`) for wide regional extents or advanced nested SQL filters.
   - `query_duckdb_spatial` also automatically rewrites raw Overture S3 globs with `bbox` filters into STAC-pruned HTTPS partition URLs so ad-hoc DuckDB queries skip full-bucket scans.
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
- `limit`: Per-batch/page size when `fetch_all=True` (default `true` iterates across all tiles/pages until 100% of features in `bbox` are collected), or hard cap when `fetch_all=False`. Never truncate map layers to partial samples unless explicitly exploring a preview.
- `fetch_all`: `True` (default) — iterates across all covering PMTiles tile batches or STAC GeoParquet offset pages until 100% complete.
- `output_name`: Resulting layer name in QGIS.
- `source`: Backend selector (`"auto"` [default: PMTiles -> STAC Parquet], `"pmtiles"`, or `"parquet"`).

> [!NOTE] Iterative Tiled & Paginated Completeness Across All Data Sources:
> Small per-request batch sizes protect host memory and prevent timeouts, while `fetch_all=True` / `paginate=True` guarantees **complete** spatial coverage by iterating across all tiles/pages into an indexed GeoPackage (`.gpkg`):
> - `query_overture_maps(..., fetch_all=True)`: Processes covering PMTiles in tile batches (or paginates STAC GeoParquet via `LIMIT`/`OFFSET`) until the entire `bbox` is extracted.
> - `query_duckdb_spatial(..., page_size=2000, fetch_all=True)`: Paginates arbitrary remote Parquet/S3 queries batch-by-batch into `.gpkg` until all matching rows are returned.
> - `add_vector_layer(path, bbox=..., paginate=True, page_size=1000)`: Automatically iterates `resultOffset`/`resultRecordCount` (ArcGIS REST FeatureServer/MapServer), `STARTINDEX`/`COUNT` (OGC WFS), and spatial bbox tiles (templated GeoJSON APIs) until complete.
> - `search_stac_items(..., limit=20, fetch_all=True)`: Iterates across STAC catalog pages so all overlapping granules/scenes are discovered.
> If the user's requested geographic scope spans an entire country or province, clarify first with the user to narrow the bounding box or filter by attribute class before querying.

### B. Example Use Cases:
1. **Complete Health Facilities / Hotels / POIs in Study Area (`bbox`):**
   ```python
   query_overture_maps(
       theme="places",
       filter_sql="taxonomy.primary IN ('hospital', 'clinic', 'pharmacy')",
       output_name="Overture_Hospitals"
   )
   ```
2. **All Building Footprints (with Heights & Floor Attributes):**
   ```python
   query_overture_maps(
       theme="buildings",
       output_name="Overture_Buildings"
   )
   ```
3. **Complete Connected Road Networks:**
   ```python
   query_overture_maps(
       theme="transportation",
       output_name="Overture_Roads"
   )
   ```
4. **All Water Bodies & Shorelines:**
   ```python
   query_overture_maps(
       theme="base",
       type="water",
       output_name="Overture_Water"
   )
   ```
5. **Paginated ArcGIS REST / OGC WFS Public Services:**
   ```python
   add_vector_layer(
       path="https://services.example.org/arcgis/rest/services/Admin/FeatureServer/0/query",
       name="Public_Admin_Boundaries",
       bbox=[110.35, -7.81, 110.38, -7.78],
       paginate=True,
       page_size=1000
   )
   ```

---

## 4. STAC Search & COG Streaming

### A. Searching Satellite Imagery or DEM
Use `search_stac_items` (paginates across pages up to `limit`, default `20`):
```python
items = search_stac_items(
    collections=["cop-dem-glo-30"],     # Global 30m elevation DEM
    # or collections=["sentinel-2-l2a"] # Cloudless optical imagery
    limit=20
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

## 5. Paginated Arbitrary Spatial Queries with DuckDB

For advanced queries joining remote Parquet files on S3 or the web (`fetch_all=True` iterates `page_size=2000` batches until all rows in the `bbox` are exported):
```python
query_duckdb_spatial(
    query="""
        SELECT id, names.primary AS name, taxonomy.primary AS category, geometry
        FROM read_parquet('s3://overturemaps-us-west-2/release/2026-10-21.0/theme=places/type=place/*', hive_partitioning=1)
        WHERE bbox.xmin >= 138.60 AND bbox.xmax <= 138.63
          AND bbox.ymin >= 35.21 AND bbox.ymax <= 35.24
          AND taxonomy.primary IN ('hotel', 'restaurant', 'museum')
    """,
    layer_name="DuckDB_Local_POIs",
    page_size=2000,
    fetch_all=True
)
```
The query iterates across all matching pages into an indexed GeoPackage (`.gpkg`) and loads into QGIS as a complete native vector layer.

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
