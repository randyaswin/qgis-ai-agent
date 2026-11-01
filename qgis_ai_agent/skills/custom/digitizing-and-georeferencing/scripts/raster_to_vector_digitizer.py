#!/usr/bin/env python3
"""Automated raster-to-vector digitizer.

Extracts cleaned, polygonized vector features from a classified or thresholded raster.

Usage:
    python raster_to_vector_digitizer.py --input classified.tif --output digitized.geojson \
        --band 1 --simplify 1.5
"""
import argparse
import json
import os
import sys

try:
    from osgeo import gdal, ogr, osr
except ImportError:
    sys.exit("Error: GDAL/OGR Python bindings are required.")


def polygonize_raster(input_raster, output_vector, band_num=1,
                      threshold=None, simplify_tolerance=0.0):
    """Vectorize a raster into polygon features.

    input_raster: path to georeferenced raster
    output_vector: path to output GeoJSON or GeoPackage (.gpkg)
    threshold: optional (min_val, max_val) to mask raster before polygonization
    simplify_tolerance: geometry simplification tolerance in layer units (e.g. 1.0 m)
    """
    if not os.path.exists(input_raster):
        raise FileNotFoundError(f"Input raster not found: {input_raster}")

    src_ds = gdal.Open(input_raster, gdal.GA_ReadOnly)
    if not src_ds:
        raise RuntimeError(f"Could not open raster: {input_raster}")

    band = src_ds.GetRasterBand(band_num)
    srs_wkt = src_ds.GetProjection()

    srs = osr.SpatialReference()
    if srs_wkt:
        srs.ImportFromWkt(srs_wkt)
    else:
        srs.SetFromUserInput("EPSG:4326")

    # Determine driver by extension
    ext = os.path.splitext(output_vector)[1].lower()
    driver_name = "GPKG" if ext in (".gpkg", ".geopackage") else "GeoJSON"
    driver = ogr.GetDriverByName(driver_name)

    if os.path.exists(output_vector):
        driver.DeleteDataSource(output_vector)

    out_ds = driver.CreateDataSource(output_vector)
    out_layer = out_ds.CreateLayer("digitized_features", srs, ogr.wkbPolygon)
    field_defn = ogr.FieldDefn("pixel_val", ogr.OFTInteger)
    out_layer.CreateField(field_defn)

    # Execute polygonize
    gdal.Polygonize(band, None, out_layer, 0, [], callback=None)
    out_ds.FlushCache()

    raw_count = out_layer.GetFeatureCount()
    out_ds = None
    src_ds = None

    # Optional geometry cleaning / simplification
    if simplify_tolerance > 0.0:
        clean_ds = ogr.Open(output_vector, 1)  # update mode
        clean_layer = clean_ds.GetLayer(0)
        for feat in clean_layer:
            geom = feat.GetGeometryRef()
            if geom:
                valid_geom = geom.MakeValid() if hasattr(geom, "MakeValid") else geom
                simplified = valid_geom.Simplify(simplify_tolerance)
                feat.SetGeometry(simplified)
                clean_layer.SetFeature(feat)
        clean_ds.FlushCache()
        clean_ds = None

    return {
        "output_path": output_vector,
        "driver": driver_name,
        "features_extracted": raw_count,
        "simplified_tolerance": simplify_tolerance
    }


def main():
    parser = argparse.ArgumentParser(description="Vectorize a georeferenced raster into polygons.")
    parser.add_argument("--input", "-i", required=True, help="Input georeferenced raster (GeoTIFF)")
    parser.add_argument("--output", "-o", required=True, help="Output vector file (.gpkg or .geojson)")
    parser.add_argument("--band", "-b", type=int, default=1, help="Raster band number (default: 1)")
    parser.add_argument("--simplify", "-s", type=float, default=0.0,
                        help="Geometry simplification tolerance in map units (default: 0.0, no simplification)")

    args = parser.parse_args()
    res = polygonize_raster(args.input, args.output, band_num=args.band,
                            simplify_tolerance=args.simplify)
    print("Vectorization complete:")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
