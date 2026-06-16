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
2. **Fail, don't overwrite.** If the fetch/auth fails, the grid is empty/all-null,
   or the latest `data_date` is older than **5 days** (the staleness alarm), the
   script exits non-zero and writes nothing. The previous good `latest.json` stays
   in place, so the app never ingests garbage into fish scoring; the app's own
   freshness rule (5.0b) downgrades stale data to a fallback. Every failure prints
   `repr(e)` (never an empty string) so the alert is actionable.

## Automation

- **Schedule:** runs **daily at `09:00 UTC`** (`cron: '0 9 * * *'` in
  `.github/workflows/chlorophyll.yml`) — timed for the gap-free NRT product's
  ~1–2 day latency. `workflow_dispatch` also allows a manual run any time.
- **Fetch:** `copernicusmarine.subset` (direct NetCDF download). The job has a
  hard `timeout-minutes: 10` so a hung fetch fails fast.
- **Secrets** (already set; Settings → Secrets and variables → Actions):
  `COPERNICUSMARINE_SERVICE_USERNAME`, `COPERNICUSMARINE_SERVICE_PASSWORD`.

### Alarms (how you find out it broke)
1. **Email** — GitHub emails the repo owner whenever a *scheduled* run fails.
   The staleness guard (>5 d old) makes "Copernicus went stale" a hard failure,
   so it triggers that email instead of silently shipping aging data.
2. **Issue** — on failure the workflow opens an issue titled
   *"chlorophyll proxy build failing"*, deduped by title (one issue per outage,
   not one per day). Close it once fixed.

### Manual re-run
- UI: Actions → **Build chlorophyll proxy** → *Run workflow*.
- CLI: `gh workflow run chlorophyll.yml --repo TheChristoph03/bluewater-data`.
- Local (debug): `python scripts/build_chlorophyll.py` with a cached
  `~/.copernicusmarine` login or the two env vars set.

Costs $0 — the repo is public, so Actions minutes are free.
