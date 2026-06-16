# bluewater-data

Public data proxy for the **BlueWater** offshore-fishing app (the app lives in a
separate private repo). This repo's job: pull live ocean data that has no
anonymous public GET, subset it to SW Florida, and republish it as a small
static JSON the app can fetch anonymously over HTTPS via GitHub Pages.

First feed: **daily chlorophyll-a** from Copernicus Marine.

## Why this exists

Copernicus Marine (CMEMS) has no anonymous CSV/ERDDAP endpoint — access needs a
free account + the Python "Marine Toolbox". So a scheduled job here authenticates,
subsets the SWFL box, and commits a tiny JSON that the app reads with no creds.

## Chlorophyll feed

- **Source:** `cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D`
  (Copernicus-GlobColour, gap-free DINEOF, daily, ~4 km), variable `CHL` (mg m⁻³).
- **Area:** SW Florida — lat 24.6…27.9 N, lon −84.5…−81.7 W (matches the app's
  habitat-scan grid).
- **Resolution:** full native ~4 km (0.04167°), ~80×67 cells. Tiny (~10 KB gzipped/day).
- **Published file:** [`chlorophyll/latest.json`](chlorophyll/latest.json)
- **Stable app URL (GitHub Pages):**
  `https://thechristoph03.github.io/bluewater-data/chlorophyll/latest.json`

### JSON contract (consumed by the app's `ChlorophyllProvider`, slice 5.0b)

```jsonc
{
  "dataset": "cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D",
  "variable": "CHL",
  "units": "mg m-3",
  "data_date": "2026-06-15",          // the day the field represents (from the time COORDINATE)
  "generated_at": "2026-06-16T21:24:00Z",
  "bbox":  { "latMin": …, "latMax": …, "lonMin": …, "lonMax": … },
  "grid":  { "lat0": …, "lon0": …, "latStep": 0.041667, "lonStep": 0.041667, "nLat": 80, "nLon": 67 },
  "valid_fraction": 0.887,            // share of non-null cells (nearshore/land are null)
  "values": [ [ /* nLon cols, lon ascending */ ], /* … nLat rows, lat ascending … */ ]
}
```
`values` is row-major: outer index = latitude **ascending** from `lat0`, inner =
longitude **ascending** from `lon0`. `null` marks a cell with no data.

## Hard rules baked into `scripts/build_chlorophyll.py`

1. **`data_date` comes from the `time` coordinate, never the global
   `time_coverage_end` attribute** — on this product that global attr is stale
   2023 boilerplate and would falsely mark today's data 3 years old.
2. **Fail, don't overwrite.** If the fetch fails, the grid is empty/all-null, or
   the latest `data_date` is older than 7 days, the script exits non-zero and
   writes nothing. The previous good `latest.json` stays in place, so the app
   never ingests garbage into fish scoring; the app's own freshness rule (5.0b)
   downgrades stale data to a climatology fallback.

## Enabling the daily job (after review)

The schedule ships **disabled** so nothing runs until secrets exist.

1. Add repo secrets (Settings → Secrets and variables → Actions):
   - `COPERNICUSMARINE_SERVICE_USERNAME`
   - `COPERNICUSMARINE_SERVICE_PASSWORD`
2. Run once manually: Actions → **Build chlorophyll proxy** → *Run workflow*
   (`workflow_dispatch`) — confirm it commits an updated `latest.json`.
3. Turn on the daily cron: uncomment the `schedule:` block in
   `.github/workflows/chlorophyll.yml`.

The `latest.json` currently committed was generated from a real run on
2026-06-16 (data_date 2026-06-15) so Pages has something to serve immediately.
