#!/usr/bin/env python3
"""Fetch BlueTopo tiles intersecting the region bbox via NOAA's official package.

Package: https://github.com/noaa-ocs-hydrography/BlueTopo (pip name `bluetopo`,
import name `nbs`). API verified against installed v0.7.0 (spike 2026-07-03):
`nbs.bluetopo.fetch_tiles(project_dir, desired_area_filename, ...)` — the area
selector is a GEOMETRY FILE path (gpkg/geojson/shp), not a coordinate list.
NOTE: `nbs` imports `osgeo.gdal` at import time but does NOT declare GDAL as a
pip dependency — the runner must provide python3-gdal for the SAME interpreter.

Usage: fetch_bluetopo.py regions/swfl.json data/bluetopo/
"""
import json, os, sys, tempfile


def main(region_path: str, out_dir: str) -> None:
    region = json.load(open(region_path))
    w, s, e, n = region["bbox"]
    try:
        from nbs.bluetopo import fetch_tiles  # pip install bluetopo (+ system python3-gdal)
    except ImportError as e:
        sys.exit(f"bluetopo import failed ({e!r}) — pip install bluetopo AND ensure "
                 "python3-gdal is importable from this interpreter")
    os.makedirs(out_dir, exist_ok=True)
    ring = [[w, s], [e, s], [e, n], [w, n], [w, s]]
    gj = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {},
        "geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    with tempfile.NamedTemporaryFile("w", suffix=".geojson", delete=False) as f:
        json.dump(gj, f)
        area_file = f.name
    try:
        fetch_tiles(out_dir, area_file)
    finally:
        os.unlink(area_file)
    print(f"BlueTopo tiles → {out_dir}")
    # TODO S4: read tile-scheme GeoPackage from the S3 bucket and log per-tile
    # resolution + %measured (bathy_coverage) for the region → OFFLINE_CHARTS_STRATEGY.md §A.2/§B.4.


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
