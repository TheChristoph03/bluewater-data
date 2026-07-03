#!/usr/bin/env python3
"""Fetch BlueTopo tiles intersecting the region bbox via NOAA's official package.

Package: https://github.com/noaa-ocs-hydrography/BlueTopo (verified to exist, 2026-07-02).
Its API surface was NOT exercised in the research session — check its README when this
first runs; the call below follows its documented usage but is UNTESTED.

Usage: fetch_bluetopo.py regions/swfl.json data/bluetopo/
"""
import json, sys

def main(region_path: str, out_dir: str) -> None:
    region = json.load(open(region_path))
    w, s, e, n = region["bbox"]
    try:
        from bluetopo import fetch_tiles  # pip install bluetopo
    except ImportError:
        sys.exit("pip install bluetopo (see github.com/noaa-ocs-hydrography/BlueTopo)")
    # Documented entry point takes a destination and a polygon/bbox selector —
    # verify signature against the installed version on first run.
    fetch_tiles(out_dir, [(w, s), (e, s), (e, n), (w, n)])
    print(f"BlueTopo tiles → {out_dir}")
    # TODO S4: read tile-scheme GeoPackage from the S3 bucket and log per-tile
    # resolution + %measured (bathy_coverage) for the region → OFFLINE_CHARTS_STRATEGY.md §A.2/§B.4.

if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
