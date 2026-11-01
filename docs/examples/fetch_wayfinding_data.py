"""Fetch Overture Maps data for the wayfinding documentation sample (cached in /tmp)."""
import os
import sys

import duckdb

OUT = os.environ.get("WAYFINDING_DATA", "/tmp/wayfinding_sample_data")
BBOX = (138.596, 35.211, 138.630, 35.234)   # small town centre, detail map extent (lon/lat)
RELEASE = "2026-10-21.0"
LAYERS = {
    "roads": ("transportation", "segment", "id, class, subtype, names.primary AS name, geometry", "subtype = 'road' OR subtype = 'rail'"),
    "buildings": ("buildings", "building", "id, class, height, geometry", None),
    "places": ("places", "place", "id, names.primary AS name, categories.primary AS category, confidence, geometry", "confidence > 0.6"),
    "water": ("base", "water", "id, class, subtype, names.primary AS name, geometry", None),
    "landuse": ("base", "land_use", "id, class, subtype, names.primary AS name, geometry", None),
}


def main(names):
    os.makedirs(OUT, exist_ok=True)
    con = duckdb.connect()
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    x0, y0, x1, y1 = BBOX
    for name in names:
        theme, typ, cols, extra = LAYERS[name]
        path = os.path.join(OUT, f"{name}.geojson")
        if os.path.exists(path):
            print("cached", path)
            continue
        where = f"bbox.xmin < {x1} AND bbox.xmax > {x0} AND bbox.ymin < {y1} AND bbox.ymax > {y0}"
        if extra:
            where += f" AND ({extra})"
        src = f"s3://overturemaps-us-west-2/release/{RELEASE}/theme={theme}/type={typ}/*"
        con.execute(f"COPY (SELECT {cols} FROM read_parquet('{src}', hive_partitioning=1) WHERE {where}) "
                    f"TO '{path}' WITH (FORMAT GDAL, DRIVER 'GeoJSON')")
        print("wrote", path)


if __name__ == "__main__":
    main(sys.argv[1:] or list(LAYERS))

