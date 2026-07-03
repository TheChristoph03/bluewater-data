# Bluewater charts pipeline (`charts.yml` job)

Implements P2/P3 of the app repo's `docs/OFFLINE_CHARTS_STRATEGY.md`: nav-features + cell-chunked depth packs → Cloudflare R2.
Workflow: `.github/workflows/charts.yml` · scripts: `scripts/charts/` · region defs: `regions/`.

## Honesty ledger — what is tested vs not

| Piece | Status |
|---|---|
| ENC Direct REST query pattern (bbox, geoJSON, pagination via `resultOffset`/`exceededTransferLimit`, `maxRecordCount=1000`) | **VERIFIED live 2026-07-03** from the research session (counts for padded SWFL bbox, coastal band: 356 wrecks, 217 obstructions, 141 lights, 17 lateral buoys; wreck attribute schema incl. OBJNAM/VALSOU/CATWRK/SORDAT/DSNM confirmed) |
| Scale-band strategy (query `enc_coastal` + `enc_approach` + `enc_harbour`, dedupe) | Design decision from verified fact (coastal band alone has almost no buoys); **band service layer IDs other than coastal are UNVERIFIED — run `discover_layers.py` first** |
| `fetch_enc_features.py` | Written against the verified pattern; **not executed end-to-end** (sandbox had no NOAA network for bulk pulls) |
| tippecanoe → PMTiles leg | **VERIFIED locally 2026-07-03 (toolchain spike)**: tippecanoe built from source, synthetic contours + nav fixtures → valid PMTiles v3 (zooms/bounds/layers checked with the python `pmtiles` reader). Spike fixed real bugs: bogus `--no-tile-size-limit-message` flag (tippecanoe rejects it) and `.dec` layer-name pollution in `build_nav_features.sh` |
| `bluetopo` pip package | **API verified against installed v0.7.0 (spike 2026-07-03)**: import name is `nbs`, not `bluetopo`; `fetch_tiles(project_dir, desired_area_filename)` takes a geometry FILE, not coord tuples (`fetch_bluetopo.py` fixed); imports `osgeo.gdal` without declaring it — needs apt `python3-gdal` on the SAME interpreter (workflow no longer uses `actions/setup-python`, which would shadow it) |
| GDAL steps (`gdal_contour`, `gdalwarp`, VRT), ENC/BlueTopo network legs | **UNVERIFIED — not installable/reachable from the local sandbox.** First Actions run IS spike S3/S4. Run `workflow_dispatch` with `region=spike-tiny` first (tiny bbox off Naples, never uploaded to R2) |
| P1 never-blank bundle (`build_p1_bundle.sh` + `charts-p1-bundle.yml`) | **UNVERIFIED composition** (data hosts blocked in sandbox). Protomaps daily-build discovery via `builds.json` and the GEBCO_2025 BODC zip URL are best-effort — verify on first dispatch. GEBCO_2025 existence + ~4 GB netCDF size confirmed from gebco.net 2026-07-03 |
| Sizes | Unknown until first run. Record them in the strategy doc §B.4 (replace EST values) |

## Layout

```
.github/workflows/charts.yml           # monthly cron + workflow_dispatch (region input); publishes to R2
.github/workflows/charts-p1-bundle.yml  # ONE-OFF P1 never-blank bundle (GEBCO is annual); dispatch only
scripts/charts/
  build_p1_bundle.sh          # P1: Protomaps basemap extract z0–z12 + GEBCO contours → single pmtiles (≤60 MB target)
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
(6×6 = 36 aligned cells cover the bbox; ocean-only/empty cells skipped), contour intervals
`{"z7-z10": 20, "z11-z12": 10, "z13-z14": 2}` metres. `regions/spike-tiny.json` is the CI toolchain-spike
region (0.2° box off Naples) — dispatch the workflow with `region=spike-tiny` before any full swfl run.
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
