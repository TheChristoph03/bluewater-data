#!/usr/bin/env python3
"""Verify nav-aid POINT density parity across zoom levels in a PMTiles pack.

Root cause this guards against (2026-07-03, Redfish Pass): tippecanoe's default
point drop-rate (2.5x per zoom below basezoom) silently thinned z10–z13. The
build now uses --base-zoom=10; this script decodes every tile covering the
given bbox at each zoom, counts unique point features (dedup across tile
buffers by rounded lon/lat), and FAILS if any mid-zoom count falls below the
max-zoom baseline.

Usage: verify_density.py pack.pmtiles --bbox=w,s,e,n --zooms 11 12 13 14
Deps:  pip install pmtiles mapbox-vector-tile
"""
from __future__ import annotations
import argparse, gzip, math, sys

from pmtiles.reader import Reader, MmapSource
import mapbox_vector_tile


def lon_to_x(lon: float, z: int) -> int:
    return int((lon + 180.0) / 360.0 * (1 << z))


def lat_to_y(lat: float, z: int) -> int:
    r = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * (1 << z))


def tile_px_to_lonlat(z: int, x: int, y: int, px: float, py: float, extent: int,
                      y_down: bool = False) -> tuple[float, float]:
    n = 1 << z
    lon = (x + px / extent) / n * 360.0 - 180.0
    # mapbox_vector_tile default y_coord_down=False: py origin is the tile's SOUTH edge
    frac_from_top = (py / extent) if y_down else (1.0 - py / extent)
    v = (y + frac_from_top) / n
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * v))))
    return lon, lat


def count_points(reader: Reader, z: int, bbox: list[float]) -> int:
    w, s, e, n = bbox
    seen: set[tuple[str, float, float]] = set()
    for x in range(lon_to_x(w, z), lon_to_x(e, z) + 1):
        for y in range(lat_to_y(n, z), lat_to_y(s, z) + 1):
            data = reader.get(z, x, y)
            if not data:
                continue
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            for lname, layer in mapbox_vector_tile.decode(data).items():
                extent = layer.get("extent", 4096)
                for f in layer.get("features", []):
                    g = f.get("geometry") or {}
                    if g.get("type") == "Point":
                        coords = [g["coordinates"]]
                    elif g.get("type") == "MultiPoint":
                        coords = g["coordinates"]
                    else:
                        continue
                    for px, py in coords:
                        lon, lat = tile_px_to_lonlat(z, x, y, px, py, extent)
                        if w <= lon <= e and s <= lat <= n:
                            seen.add((lname, round(lon, 6), round(lat, 6)))
    return len(seen)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("pmtiles")
    ap.add_argument("--bbox", required=True, help="w,s,e,n")
    ap.add_argument("--zooms", nargs="+", type=int, required=True)
    ap.add_argument("--tolerance", type=float, default=1.0,
                    help="min fraction of the max-zoom baseline (default 1.0 = exact parity)")
    args = ap.parse_args()
    bbox = [float(v) for v in args.bbox.split(",")]
    zooms = sorted(args.zooms)

    with open(args.pmtiles, "rb") as f:
        reader = Reader(MmapSource(f))
        counts = {z: count_points(reader, z, bbox) for z in zooms}

    base_z = zooms[-1]
    base = counts[base_z]
    print(f"## density parity — bbox {bbox}, baseline z{base_z} = {base} point features")
    ok = True
    for z in zooms:
        verdict = "OK" if counts[z] >= args.tolerance * base else "THINNED"
        if verdict == "THINNED":
            ok = False
        print(f"  z{z}: {counts[z]:5d}  {verdict}")
    if base == 0:
        sys.exit("FAIL: baseline zoom has 0 points in bbox — wrong bbox or empty pack")
    if not ok:
        sys.exit(f"FAIL: mid-zoom density below z{base_z} baseline — thinning regressed")
    print("PASS: full mark density at all checked zooms")


if __name__ == "__main__":
    main()
