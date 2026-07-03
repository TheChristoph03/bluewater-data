# Bluewater charts pipeline (`charts.yml` job)

Implements P2/P3 of the app repo's `docs/OFFLINE_CHARTS_STRATEGY.md`: nav-features + cell-chunked depth packs → Cloudflare R2.
Workflow: `.github/workflows/charts.yml` · scripts: `scripts/charts/` · region defs: `regions/`.

## Honesty ledger — what is tested vs not

| Piece | Status |
|---|---|
| ENC Direct REST query pattern (bbox, geoJSON, pagination via `resultOffset`/`exceededTransferLimit`, `maxRecordCount=1000`) | **VERIFIED live 2026-07-03** from the research session (counts for padded SWFL bbox, coastal band: 356 wrecks, 217 obstructions, 141 lights, 17 lateral buoys; wreck attribute schema incl. OBJNAM/VALSOU/CATWRK/SORDAT/DSNM confirmed) |
| Scale-band strategy (query `enc_coastal` + `enc_approach` + `enc_harbour`, dedupe) | Design decision from verified fact (coastal band alone has almost no buoys); **band service layer IDs other than coastal are UNVERIFIED — run `discover_layers.py` first** |
| `fetch_enc_features.py` | Written against the verified pattern; **not executed end-to-end** (sandbox had no NOAA network for bulk pulls) |
| BlueTopo fetch (`bluetopo` pip pkg), GDAL contour, tippecanoe, pmtiles steps | Tools verified to exist and do what's claimed (see strategy doc §B.3 cites); **this composition is UNTESTED — first Actions run IS spike S3/S4** |
| Sizes | Unknown until first run. Record them in the strategy doc §B.4 (replace EST values) |

## Layout

```
.github/workflows/charts.yml  # monthly cron + workflow_dispatch; publishes to R2
scripts/charts/
  discover_layers.py          # enumerate layer ids per ENC Direct band service (run once, commit output)
  fetch_enc_features.py       # ENC Direct → per-class GeoJSON (nav features)
  decode_s57.py               # integer-code → text lookup for fields ENC Direct leaves undecoded
  build_nav_features.sh       # GeoJSON → nav-features-<region>.pmtiles (tippecanoe)
  fetch_bluetopo.py           # BlueTopo tiles for bbox → data/bluetopo/ (uses official `bluetopo` package)
  build_depth_cells.py        # VRT → per-cell warp → gdal_contour (interval per zoom band) → tippecanoe → cells + manifest.json
regions/swfl.json             # region definition (bbox, cell grid, contour intervals)
```

## Region & cell scheme

`regions/swfl.json` defines: bbox `-85.5,23.5,-80.0,28.5`, cell grid = **1°×1°** aligned to integer degrees
(30 cells; ocean-only cells skipped), contour intervals `{"z7-z10": 20, "z11-z12": 10, "z13-z14": 2}` metres.
Cell id = `swfl_N24W082` style. Everything downstream (manifest, R2 keys, app) uses these ids.

## `manifest.json` schema (per region, published to R2)

```json
{
  "region": "swfl",
  "schema": 1,
  "generated": "2026-07-03T12:00:00Z",
  "navFeatures": { "key": "swfl/nav-features.pmtiles", "bytes": 0, "version": "2026-07-03", "dataDate": "2026-06-30" },
  "cells": [
    { "id": "swfl_N26W083", "bbox": [-83,26,-82,27], "key": "swfl/depth/N26W083.pmtiles",
      "bytes": 0, "version": "2026-07-03", "source": "BlueTopo", "dataDate": "…" }
  ]
}
```

`dataDate` feeds the in-app "Chart data: <date>" badge (honesty requirement, strategy doc §E).

## Secrets required (repo settings)

`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` — upload step uses `rclone` S3-compatible config.

## Not in this scaffold (deliberate)

- No S-57/ZIP parsing (ENC Direct gives us decoded GIS features; S-101 transition makes raw S-57 plumbing a bad investment).
- No custom-area server extraction (rejected — fixed cells on static storage, see BACKLOG note 2026-07-03).
- Attribution/credits file generation for BlueTopo per-source CC-BY licenses — TODO before first public release (strategy doc §E.2).
