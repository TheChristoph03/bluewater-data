#!/usr/bin/env python3
"""Build the BlueWater chlorophyll proxy JSON from Copernicus Marine.

Pulls the latest daily gap-free 4 km chlorophyll-a (CHL) over the SW Florida
bounding box and writes it as a compact static JSON the app reads.

HARD RULES (do not relax — a Copernicus hiccup must never publish garbage into
fish scoring):
  * data_date is derived from the `time` COORDINATE, never the global
    `time_coverage_end` attribute (which is stale 2023 boilerplate on this
    product and would falsely brand today's data as 3 years old).
  * If the fetch fails, the grid is empty / all-null, or the data isn't recent,
    the script exits NON-ZERO and writes NOTHING. The last-good latest.json is
    left untouched so the app keeps reading honest (if older) data, and 5.0b's
    freshness rule decides .live-vs-climatology on the stale date.

Auth: the Copernicus Marine Toolbox reads COPERNICUSMARINE_SERVICE_USERNAME /
COPERNICUSMARINE_SERVICE_PASSWORD from the environment (set as repo secrets in
CI), or a cached login under ~/.copernicusmarine for local runs.
"""
import sys
import json
import datetime as dt
from pathlib import Path

import numpy as np
import copernicusmarine

DATASET_ID = "cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D"
VARIABLE = "CHL"

# SW Florida scan bbox — matches HabitatScanCandidateSource's scan grid
# (latMin/latMax 24.6/27.9, lonMin/lonMax -84.5/-81.7) in the app repo.
LAT_MIN, LAT_MAX = 24.6, 27.9
LON_MIN, LON_MAX = -84.5, -81.7

# Validation thresholds (the "fail-don't-overwrite" guardrails).
MAX_AGE_DAYS = 7          # data_date must be within this many days of today (UTC)
MIN_VALID_FRACTION = 0.5  # at least this fraction of cells must be finite

OUT_PATH = Path(__file__).resolve().parent.parent / "chlorophyll" / "latest.json"


def fail(msg: str) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    print("Refusing to overwrite the last-good JSON; exiting non-zero.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    # --- Fetch (lazy ARCO/zarr subset; no NetCDF temp file, no h5py needed) ---
    try:
        ds = copernicusmarine.open_dataset(
            dataset_id=DATASET_ID,
            variables=[VARIABLE],
            minimum_longitude=LON_MIN, maximum_longitude=LON_MAX,
            minimum_latitude=LAT_MIN, maximum_latitude=LAT_MAX,
        )
    except Exception as e:  # network / auth / catalogue error
        fail(f"open_dataset failed: {e}")

    if "time" not in ds.coords or int(ds.sizes.get("time", 0)) == 0:
        fail("dataset returned no time steps")

    # --- Latest day: read the TIME COORDINATE, never the stale global attr ---
    ds_last = ds.isel(time=-1)
    data_date = str(np.datetime64(ds_last["time"].values, "D"))  # YYYY-MM-DD

    today = dt.datetime.now(dt.timezone.utc).date()
    age = (today - dt.date.fromisoformat(data_date)).days
    if age < 0:
        fail(f"latest data_date {data_date} is in the future vs {today} (clock/source error)")
    if age > MAX_AGE_DAYS:
        fail(f"latest data_date {data_date} is {age} d old (> {MAX_AGE_DAYS}); treating as stale")

    try:
        chl = ds_last[VARIABLE].load()
    except Exception as e:
        fail(f"loading the CHL slice failed: {e}")

    lat = np.asarray(chl["latitude"].values, dtype=float)
    lon = np.asarray(chl["longitude"].values, dtype=float)
    arr = np.asarray(chl.values, dtype=float).squeeze()
    if arr.ndim != 2:
        fail(f"expected a 2-D CHL grid, got shape {arr.shape}")

    # Orient ascending in both axes so the contract is unambiguous.
    if lat.size > 1 and lat[0] > lat[-1]:
        lat = lat[::-1]; arr = arr[::-1, :]
    if lon.size > 1 and lon[0] > lon[-1]:
        lon = lon[::-1]; arr = arr[:, ::-1]

    finite = np.isfinite(arr)
    n_valid = int(finite.sum())
    frac = (n_valid / arr.size) if arr.size else 0.0
    if arr.size == 0 or n_valid == 0:
        fail("CHL grid is empty / all-null")
    if frac < MIN_VALID_FRACTION:
        fail(f"only {frac:.1%} of cells valid (< {MIN_VALID_FRACTION:.0%}); suspect a bad fetch")

    # --- Build the contract (row-major, lat ascending, lon ascending) ---
    values = [[round(float(v), 4) if np.isfinite(v) else None for v in row] for row in arr]
    lat_step = round(float(abs(lat[1] - lat[0])), 6) if lat.size > 1 else 0.0
    lon_step = round(float(abs(lon[1] - lon[0])), 6) if lon.size > 1 else 0.0

    out = {
        "dataset": DATASET_ID,
        "variable": VARIABLE,
        "units": "mg m-3",
        "data_date": data_date,  # from the time COORDINATE (see HARD RULES)
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bbox": {
            "latMin": round(float(lat.min()), 6), "latMax": round(float(lat.max()), 6),
            "lonMin": round(float(lon.min()), 6), "lonMax": round(float(lon.max()), 6),
        },
        "grid": {
            "lat0": round(float(lat[0]), 6), "lon0": round(float(lon[0]), 6),
            "latStep": lat_step, "lonStep": lon_step,
            "nLat": int(lat.size), "nLon": int(lon.size),
        },
        "valid_fraction": round(frac, 4),
        "values": values,
    }

    # Atomic write — only reached after EVERY validation passed.
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")))
    tmp.replace(OUT_PATH)
    print(f"Wrote {OUT_PATH} — data_date={data_date}, grid={lat.size}x{lon.size}, "
          f"valid={frac:.1%}, bytes={OUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
