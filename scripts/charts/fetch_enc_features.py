#!/usr/bin/env python3
"""Fetch nav-relevant ENC features from NOAA ENC Direct REST services as GeoJSON.

Query pattern VERIFIED live 2026-07-03 (see scripts/charts/README.md honesty ledger).
Full end-to-end run is UNTESTED until the first Actions run.

Usage: fetch_enc_features.py regions/swfl.json out/enc/
"""
from __future__ import annotations
import json, sys, time, urllib.parse, urllib.request

BASE = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/{service}/MapServer/{layer}/query"

# Layer ids VERIFIED for enc_coastal (service JSON fetched 2026-07-03).
# For enc_approach / enc_harbour run discover_layers.py first and fill these in —
# ids differ per service. Do NOT guess them.
FEATURE_SETS = {
    "enc_coastal": {
        # ATONs (point)
        "beacon_lateral": 1, "beacon_safe_water": 2, "beacon_special": 3,
        "buoy_isolated_danger": 4, "buoy_lateral": 5, "buoy_safe_water": 6,
        "buoy_special": 7, "daymark": 8, "light": 10, "light_float": 11,
        # Dangers / structure (the engine-value layers)
        "wreck_point": 33, "obstruction_point": 30, "rock_underwater": 31,
        "wreck_area": 120, "obstruction_area": 118, "obstruction_line": 78,
        # Context / honesty
        "fairway_area": 150, "recommended_track": 102, "unsurveyed_area": 168,
        "caution_area": 116, "restricted_area": 141,
    },
    # "enc_approach": {...},  # fill from discover_layers.py output
    # "enc_harbour": {...},
}

PAGE = 1000  # server maxRecordCount (verified)


def fetch_layer(service: str, layer: int, bbox: list[float]) -> list[dict]:
    feats, offset = [], 0
    while True:
        params = {
            "where": "1=1",
            "geometry": ",".join(str(v) for v in bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326, "outSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "resultOffset": offset, "resultRecordCount": PAGE,
            "f": "geojson",
        }
        url = BASE.format(service=service, layer=layer) + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = json.load(r)
        if "error" in data:
            raise RuntimeError(f"{service}/{layer}: {data['error']}")
        page = data.get("features", [])
        feats.extend(page)
        # geojson responses signal continuation via exceededTransferLimit (verified in json mode;
        # geojson mode places it top-level or in properties — handle both, else stop on short page)
        more = data.get("exceededTransferLimit") or data.get("properties", {}).get("exceededTransferLimit")
        if not more and len(page) < PAGE:
            break
        offset += len(page)
        time.sleep(0.5)  # be polite to a public service
    return feats


def main(region_path: str, out_dir: str) -> None:
    import os
    region = json.load(open(region_path))
    bbox = region["bbox"]  # [w, s, e, n]
    os.makedirs(out_dir, exist_ok=True)
    for service, layers in FEATURE_SETS.items():
        for name, layer_id in layers.items():
            feats = fetch_layer(service, layer_id, bbox)
            fc = {"type": "FeatureCollection",
                  "features": feats,
                  "bw_meta": {"service": service, "layer": layer_id,
                              "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                              "count": len(feats)}}
            path = f"{out_dir}/{service}__{name}.geojson"
            json.dump(fc, open(path, "w"))
            print(f"{path}: {len(feats)} features")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
