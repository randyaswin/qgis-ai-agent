#!/usr/bin/env python3
"""Georeference an unreferenced raster image using Ground Control Points (GCPs).

Usage:
    python georeference_raster.py --input raw_scan.jpg --output georeferenced.tif \
        --gcp 100 150 138.61 35.22 \
        --gcp 3900 160 138.65 35.22 \
        --gcp 110 4100 138.61 35.18 \
        --gcp 3910 4090 138.65 35.18 \
        --crs EPSG:4326 --transform polynomial1
"""
import argparse
import json
import math
import os
import sys

try:
    from osgeo import gdal, osr
except ImportError:
    sys.exit("Error: GDAL Python bindings ('osgeo.gdal') are required.")


def georeference(input_path, output_path, gcp_list, crs="EPSG:4326",
                  transform="polynomial1", resample="bilinear"):
    """Georeference input raster with Ground Control Points.

    gcp_list: list of [pixel_x, pixel_y, map_x, map_y] or (pixel_x, pixel_y, map_x, map_y)
    transform: 'polynomial1' (Affine) | 'polynomial2' | 'polynomial3' | 'tps' | 'helmert'
    resample: 'bilinear' | 'nearest' | 'cubic' | 'cubicspline'
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input image not found: {input_path}")

    # Minimum GCP verification
    min_gcps = {"helmert": 2, "polynomial1": 3, "polynomial2": 6, "polynomial3": 10, "tps": 4}
    req = min_gcps.get(transform.lower(), 3)
    if len(gcp_list) < req:
        raise ValueError(f"Transformation '{transform}' requires at least {req} GCPs, but only {len(gcp_list)} provided.")

    # 1. Build GDAL GCP list
    gcps = []
    for item in gcp_list:
        if isinstance(item, dict):
            px, py = float(item["pixel_x"]), float(item["pixel_y"])
            mx, my = float(item["map_x"]), float(item["map_y"])
        else:
            px, py, mx, my = float(item[0]), float(item[1]), float(item[2]), float(item[3])
        gcps.append(gdal.GCP(mx, my, 0, px, py))

    # 2. Set spatial reference
    srs = osr.SpatialReference()
    srs.SetFromUserInput(crs)

    # 3. Translate to in-memory VRT with GCPs
    vrt_path = f"/vsimem/temp_gcp_{os.getpid()}.vrt"
    temp_ds = gdal.Translate(vrt_path, input_path, GCPs=gcps, outputSRS=srs)
    if not temp_ds:
        raise RuntimeError("Failed to attach GCPs to dataset.")

    # 4. Map resample algorithm
    resample_map = {
        "nearest": gdal.GRA_NearestNeighbour,
        "bilinear": gdal.GRA_Bilinear,
        "cubic": gdal.GRA_Cubic,
        "cubicspline": gdal.GRA_CubicSpline,
        "lanczos": gdal.GRA_Lanczos
    }
    alg = resample_map.get(resample.lower(), gdal.GRA_Bilinear)

    # 5. Configure warp transformation
    poly_order = None
    use_tps = False
    t_low = transform.lower()
    if t_low in ("polynomial1", "affine"):
        poly_order = 1
    elif t_low == "polynomial2":
        poly_order = 2
    elif t_low == "polynomial3":
        poly_order = 3
    elif t_low == "tps":
        use_tps = True

    warp_options = gdal.WarpOptions(
        format="GTiff",
        dstSRS=crs,
        resampleAlg=alg,
        polynomialOrder=poly_order,
        tps=use_tps
    )

    # 6. Execute warp
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    out_ds = gdal.Warp(output_path, temp_ds, options=warp_options)
    temp_ds = None
    gdal.Unlink(vrt_path)

    if not out_ds:
        raise RuntimeError(f"Warping failed to produce {output_path}")

    gt = out_ds.GetGeoTransform()
    width, height = out_ds.RasterXSize, out_ds.RasterYSize
    out_ds = None

    return {
        "output_path": output_path,
        "crs": crs,
        "transform": transform,
        "width": width,
        "height": height,
        "extent": [gt[0], gt[3] + gt[5] * height, gt[0] + gt[1] * width, gt[3]],
        "pixel_size": [abs(gt[1]), abs(gt[5])],
        "gcp_count": len(gcp_list)
    }


def main():
    parser = argparse.ArgumentParser(description="Georeference an image using Ground Control Points (GCPs).")
    parser.add_argument("--input", "-i", required=True, help="Input raw raster image")
    parser.add_argument("--output", "-o", required=True, help="Output GeoTIFF path")
    parser.add_argument("--gcp", action="append", nargs=4, type=float, metavar=("PX", "PY", "MX", "MY"),
                        help="Ground Control Point: pixel_x pixel_y map_x map_y")
    parser.add_argument("--gcp-file", help="Path to JSON file with list of GCPs")
    parser.add_argument("--crs", default="EPSG:4326", help="Target CRS (default: EPSG:4326)")
    parser.add_argument("--transform", default="polynomial1", choices=["polynomial1", "polynomial2", "polynomial3", "tps", "affine"],
                        help="Transformation algorithm (default: polynomial1)")
    parser.add_argument("--resample", default="bilinear", choices=["bilinear", "nearest", "cubic", "cubicspline"],
                        help="Resampling algorithm (default: bilinear)")

    args = parser.parse_args()

    gcps = []
    if args.gcp_file:
        with open(args.gcp_file, encoding="utf-8") as f:
            gcps = json.load(f)
    elif args.gcp:
        gcps = args.gcp
    else:
        sys.exit("Error: Provide at least 3 GCPs via --gcp or --gcp-file.")

    res = georeference(args.input, args.output, gcps, crs=args.crs,
                       transform=args.transform, resample=args.resample)
    print("Georeferencing complete:")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
