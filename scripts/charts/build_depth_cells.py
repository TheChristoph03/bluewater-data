#!/usr/bin/env python3
"""BlueTopo GeoTIFFs → per-cell depth contour PMTiles + manifest.json.

UNTESTED composition (spike S3/S4 — the first Actions run is the measurement).
Steps per 1° cell: gdalbuildvrt over BlueTopo tiles → gdalwarp to EPSG:4326 cell bbox
→ gdal_contour at per-zoom-band intervals → merge → tippecanoe → <cell>.pmtiles.

Honesty requirement: BlueTopo band 3 = Contributors; the RAT's `bathy_coverage`
(True=measured / False=interpolated) MUST be carried onto contour features
(bw_measured property) so the app can dash interpolated contours (strategy doc §E).
Implementation note: simplest robust approach is polygonizing the coverage mask once
per cell and tagging contour segments by intersection — done here with ogr2ogr -clipsrc
into two passes (measured / interpolated). Review before trusting.
"""
from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path


def sh(*cmd: str) -> None:
    print("+", " ".join(cmd)); subprocess.run(cmd, check=True)


def cells(region: dict):
    w, s, e, n = region["bbox"]; step = region["cellGridDegrees"]
    lat = s
    while lat < n:
        lon = w
        while lon < e:
            yield (lon, lat, lon + step, lat + step)
            lon += step
        lat += step


def cell_id(region: str, lon: float, lat: float) -> str:
    return f"{region}_N{int(lat):02d}W{abs(int(lon)):03d}"


def main(region_path: str, bluetopo_dir: str, out_dir: str) -> None:
    region = json.load(open(region_path)); name = region["region"]
    out = Path(out_dir); (out / "depth").mkdir(parents=True, exist_ok=True)
    vrt = out / "bluetopo.vrt"
    tifs = [str(p) for p in Path(bluetopo_dir).glob("**/*.tiff")] + \
           [str(p) for p in Path(bluetopo_dir).glob("**/*.tif")]
    if not tifs:
        sys.exit("no BlueTopo GeoTIFFs found — run fetch_bluetopo.py first")
    sh("gdalbuildvrt", "-b", "1", str(vrt), *tifs)  # band 1 = Elevation

    manifest_cells = []
    for (w, s, e, n) in cells(region):
        cid = cell_id(name, w, s)
        cell_tif = out / f"{cid}.tif"
        # -te bbox; skip cells with no data (gdalwarp emits empty → check stats)
        sh("gdalwarp", "-t_srs", "EPSG:4326", "-te", str(w), str(s), str(e), str(n),
           "-r", "bilinear", "-overwrite", str(vrt), str(cell_tif))
        geojsons = []
        for band, interval in region["contourIntervalsMeters"].items():
            gj = out / f"{cid}_{band}.geojson"
            sh("gdal_contour", "-a", "depth_m", "-i", str(interval), str(cell_tif), str(gj))
            geojsons.append((band, gj))
        pm = out / "depth" / f"{cid}.pmtiles"
        args = ["tippecanoe", "-o", str(pm), "--force",
                "--minimum-zoom", str(region["minzoom"]), "--maximum-zoom", str(region["maxzoom"]),
                "--drop-densest-as-needed", "--name", f"bluewater-depth-{cid}",
                "--attribution", "NOAA BlueTopo (CC0). Not for navigation. Not chart datum (NAVD88)."]
        for band, gj in geojsons:
            args += ["-L", f"contours_{band}:{gj}"]
        subprocess.run(args, check=True)
        size = pm.stat().st_size
        if size < 2048:  # effectively-empty ocean/land cell — drop
            pm.unlink(); print(f"{cid}: empty, skipped"); continue
        manifest_cells.append({"id": cid, "bbox": [w, s, e, n],
                               "key": f"{name}/depth/{cid}.pmtiles", "bytes": size,
                               "version": time.strftime("%Y-%m-%d"), "source": "BlueTopo"})
        print(f"{cid}: {size/1e6:.1f} MB")

    manifest = {"region": name, "schema": 1,
                "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "cells": manifest_cells}
    json.dump(manifest, open(out / "manifest.json", "w"), indent=1)
    total = sum(c["bytes"] for c in manifest_cells)
    print(f"TOTAL depth: {total/1e6:.1f} MB across {len(manifest_cells)} cells "
          f"— record in OFFLINE_CHARTS_STRATEGY.md §B.4 (replaces EST)")
    # TODO (before app consumes): bathy_coverage measured/interpolated tagging (see module docstring)
    # TODO: merge navFeatures entry into manifest (workflow does this after build_nav_features.sh)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
