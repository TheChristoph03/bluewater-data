#!/usr/bin/env python3
"""Fetch nav-relevant ENC features from NOAA ENC Direct REST services as GeoJSON.

Queries all three scale bands (coastal + approach + harbour) and merges them into
ONE file per canonical class, deduping across bands/cells by (class, rounded
geometry) with band priority harbour > approach > coastal (most detailed wins).

Every feature gains:  bw_cls  (canonical class, for app styling/toggles)
                      bw_band (winning source band)

Layer ids VERIFIED against live service JSON 2026-07-03 — see enc_layer_map.md.
Query pattern (bbox envelope, geoJSON, EPSG:4326, maxRecordCount 1000, pagination
via resultOffset/exceededTransferLimit) verified live 2026-07-03.

Usage: fetch_enc_features.py regions/swfl.json out/enc/
Writes: out/enc/nav__<class>.geojson + out/enc/counts.json
"""
from __future__ import annotations
import hashlib, json, os, sys, time, urllib.parse, urllib.request

BASE = "https://encdirect.noaa.gov/arcgis/rest/services/encdirect/{service}/MapServer/{layer}/query"

# canonical class -> layer id, per band. Ids DIFFER per band — see enc_layer_map.md.
FEATURE_SETS: dict[str, dict[str, int]] = {
    "enc_coastal": {
        "beacon_lateral": 1, "beacon_safe_water": 2, "beacon_special": 3,
        "buoy_isolated_danger": 4, "buoy_lateral": 5, "buoy_safe_water": 6,
        "buoy_special": 7, "daymark": 8, "light": 10, "light_float": 11,
        "caution_area_point": 29, "obstruction_point": 30, "rock_underwater": 31,
        "wreck_point": 33, "obstruction_line": 78, "recommended_track": 102,
        "caution_area": 116, "obstruction_area": 118, "wreck_area": 120,
        "restricted_area": 141, "fairway_area": 150, "unsurveyed_area": 168,
    },
    "enc_approach": {
        "beacon_cardinal": 1, "beacon_isolated_danger": 2, "beacon_lateral": 3,
        "beacon_safe_water": 4, "beacon_special": 5, "buoy_cardinal": 6,
        "buoy_isolated_danger": 7, "buoy_lateral": 8, "buoy_safe_water": 9,
        "buoy_special": 10, "daymark": 11, "light": 13, "light_float": 14,
        "caution_area_point": 34, "obstruction_point": 36, "rock_underwater": 37,
        "wreck_point": 39, "obstruction_line": 103, "recommended_track": 139,
        "caution_area": 159, "obstruction_area": 161, "wreck_area": 163,
        "restricted_area": 202, "fairway_area": 213, "recommended_track_area": 218,
        "unsurveyed_area": 235,
    },
    "enc_harbour": {
        "beacon_lateral": 1, "beacon_safe_water": 2, "beacon_special": 3,
        "buoy_cardinal": 4, "buoy_isolated_danger": 5, "buoy_lateral": 6,
        "buoy_safe_water": 7, "buoy_special": 8, "daymark": 9, "light": 11,
        "light_float": 12, "caution_area_point": 31, "obstruction_point": 33,
        "rock_underwater": 34, "wreck_point": 36, "obstruction_line": 99,
        "recommended_track": 134, "caution_area": 154, "obstruction_area": 156,
        "wreck_area": 158, "restricted_area": 197, "fairway_area": 208,
        "recommended_track_area": 213, "unsurveyed_area": 230,
    },
}

# most detailed band wins when the same feature appears in several bands
BAND_PRIORITY = {"enc_coastal": 0, "enc_approach": 1, "enc_harbour": 2}
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
        more = data.get("exceededTransferLimit") or data.get("properties", {}).get("exceededTransferLimit")
        if not more and len(page) < PAGE:
            break
        offset += len(page)
        time.sleep(0.5)  # be polite to a public service
    return feats


def _round_coords(obj, nd=5):  # ~1 m at these latitudes
    if isinstance(obj, (list, tuple)):
        return [_round_coords(v, nd) for v in obj]
    if isinstance(obj, float):
        return round(obj, nd)
    return obj


def dedupe_key(cls: str, feat: dict) -> str:
    geom = feat.get("geometry") or {}
    payload = json.dumps({"c": cls, "t": geom.get("type"),
                          "g": _round_coords(geom.get("coordinates"))}, sort_keys=True)
    return hashlib.sha1(payload.encode()).hexdigest()


def main(region_path: str, out_dir: str) -> None:
    region = json.load(open(region_path))
    bbox = region["bbox"]  # [w, s, e, n]
    os.makedirs(out_dir, exist_ok=True)

    merged: dict[str, dict[str, tuple[int, dict]]] = {}   # cls -> key -> (prio, feature)
    raw_counts: dict[str, dict[str, int]] = {}            # cls -> band -> fetched count

    for service, layers in FEATURE_SETS.items():
        prio = BAND_PRIORITY[service]
        for cls, layer_id in layers.items():
            feats = fetch_layer(service, layer_id, bbox)
            raw_counts.setdefault(cls, {})[service] = len(feats)
            bucket = merged.setdefault(cls, {})
            for f in feats:
                f.setdefault("properties", {})["bw_cls"] = cls
                f["properties"]["bw_band"] = service.removeprefix("enc_")
                k = dedupe_key(cls, f)
                if k not in bucket or prio > bucket[k][0]:
                    bucket[k] = (prio, f)
            print(f"  fetched {service}/{layer_id} {cls}: {len(feats)}")

    summary = {}
    for cls, bucket in sorted(merged.items()):
        feats = [f for _, f in bucket.values()]
        fc = {"type": "FeatureCollection", "features": feats,
              "bw_meta": {"class": cls, "bands": raw_counts[cls],
                          "fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                          "count_deduped": len(feats)}}
        path = f"{out_dir}/nav__{cls}.geojson"
        json.dump(fc, open(path, "w"))
        summary[cls] = {"deduped": len(feats), **raw_counts[cls]}

    json.dump(summary, open(f"{out_dir}/counts.json", "w"), indent=1)
    print(f"\n{'class':26s} {'coastal':>8s} {'approach':>9s} {'harbour':>8s} {'DEDUPED':>8s}")
    total = 0
    for cls, s in sorted(summary.items()):
        total += s["deduped"]
        print(f"{cls:26s} {s.get('enc_coastal', 0):8d} {s.get('enc_approach', 0):9d} "
              f"{s.get('enc_harbour', 0):8d} {s['deduped']:8d}")
    print(f"{'TOTAL (deduped)':26s} {'':8s} {'':9s} {'':8s} {total:8d}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
