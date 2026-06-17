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

## Subsurface-temperature feed (slice 5.2a)

- **Source:** `cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m` (Copernicus Global
  Physics analysis+**forecast**, ~8 km / 0.083°, daily), variable `thetao`
  (sea-water potential temperature, published in **°F**).
- **Area:** the same SWFL bbox.
- **Depths:** a small thermocline set — model levels nearest **30 / 60 / 90 m**
  (e.g. 29.4 / 55.8 / 92.3 m) where in-range pelagics hold (wahoo ~30 m,
  blackfin ~60 m). 100 m+ is dropped — mostly below the in-range seabed.
- **Horizon:** a forecast product, so it publishes **today + ~6 forecast days**
  (≈ the tide planning window); the app samples the **departure day**.
- **Published file:** [`subsurface-temp/latest.json`](subsurface-temp/latest.json)
  — `https://thechristoph03.github.io/bluewater-data/subsurface-temp/latest.json`
- **Size:** ~7 days × 3 depths over the bbox ≈ **~23 KB gzipped/day**.

### JSON contract (consumed by the app's `SubsurfaceTempProvider`, slice 5.2b)

```jsonc
{
  "dataset": "cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m",
  "variable": "thetao", "units": "degF",
  "data_date": "2026-06-17",            // nowcast day (from the time COORDINATE)
  "depthsM": [29.4, 55.8, 92.3],
  "bbox": { … }, "grid": { … },         // same shape as the chlorophyll grid
  "null_means": "no water at this depth (seafloor) — a real negative, not missing data",
  "days": [
    { "date": "2026-06-17",
      "depths": [ { "depthM": 29.4, "validFraction": 0.63, "values": [[ … ]] }, … ] },
    …
  ]
}
```

**TWO DISTINCT NULLS (the app must tell them apart):**
1. `null` inside a **published** layer = **no water that deep here** (seafloor/land).
   The ocean model fills every wet cell, so a null can only be below the seabed — a
   **real negative** for a species holding at that depth ("too shallow"), NOT missing
   data. (Verified: depth-nulls are monotonic — dry shallow ⇒ dry deep.)
2. A whole **depth/day absent** from `days[]`, or a **stale `data_date`** = data
   **unavailable** → the app falls back to surface SST + flags. Distinguished by
   presence/absence, with per-layer `validFraction` for the coverage guard.

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
