#!/usr/bin/env python3
"""Enumerate layer ids/names for the ENC Direct band services. Run once; paste the
relevant ids into FEATURE_SETS in fetch_enc_features.py. Do not guess layer ids —
they differ per service (coastal ids verified 2026-07-03; others not)."""
import json, urllib.request

SERVICES = ["enc_overview", "enc_general", "enc_coastal", "enc_approach", "enc_harbour", "enc_berthing"]

for svc in SERVICES:
    url = f"https://encdirect.noaa.gov/arcgis/rest/services/encdirect/{svc}/MapServer?f=pjson"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.load(r)
    except Exception as e:  # noqa: BLE001 — service list may drift; report and continue
        print(f"## {svc}: FAILED {e}")
        continue
    print(f"## {svc}")
    for lyr in data.get("layers", []):
        if lyr.get("type") == "Feature Layer":
            print(f"  {lyr['id']:4d}  {lyr['name']}")
