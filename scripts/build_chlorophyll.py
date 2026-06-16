#!/usr/bin/env python3
"""Build the BlueWater chlorophyll proxy JSON from Copernicus Marine.

Pulls the latest daily gap-free 4 km chlorophyll-a (CHL) over the SW Florida
bounding box and writes it as a compact static JSON the app reads.

Fetch: a direct `copernicusmarine.subset` NetCDF download. (The lazy
`open_dataset`/zarr-streaming path hung ~20 min and failed in CI; the subset
download is the path verified to return in ~9 s.) Auth via the
COPERNICUSMARINE_SERVICE_USERNAME / COPERNICUSMARINE_SERVICE_PASSWORD env vars
(repo secrets in CI) or a cached ~/.copernicusmarine login locally.

HARD RULES (do not relax — a Copernicus hiccup must never publish garbage into
fish scoring):
  * data_date is derived from the `time` COORDINATE, never the global
    `time_coverage_end` attribute (stale 2023 boilerplate on this product).
  * If the fetch fails, the grid is empty / all-null, or the data isn't recent,
    the script exits NON-ZERO and writes NOTHING. The last-good latest.json is
    left untouched so the app keeps reading honest (if older) data, and 5.0b's
    freshness rule decides .live-vs-fallback on the stale date.
  * Every failure prints an actionable message (repr of the exception, never an
    empty string) so the GitHub failure email / issue alarm is actionable.
"""
import sys
import os
import glob
import json
import tempfile
import datetime as dt
from pathlib import Path

import numpy as np
import xarray as xr
import copernicusmarine

DATASET_ID = "cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D"
VARIABLE = "CHL"

# SW Florida scan bbox — matches HabitatScanCandidateSource's scan grid
# (latMin/latMax 24.6/27.9, lonMin/lonMax -84.5/-81.7) in the app repo.
LAT_MIN, LAT_MAX = 24.6, 27.9
LON_MIN, LON_MAX = -84.5, -81.7

# Validation thresholds (the "fail-don't-overwrite" guardrails).
MAX_AGE_DAYS = 5          # data_date must be within this many days of today (UTC) — staleness alarm
MIN_VALID_FRACTION = 0.5  # at least this fraction of cells must be finite

OUT_PATH = Path(__file__).resolve().parent.parent / "chlorophyll" / "latest.json"


def fail(msg: str) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    print("Refusing to overwrite the last-good JSON; exiting non-zero.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out_name = "swfl_chl.nc"
        # --- Fetch: direct NetCDF subset (reliable headless) ---
        try:
            copernicusmarine.subset(
                dataset_id=DATASET_ID,
                variables=[VARIABLE],
                minimum_longitude=LON_MIN, maximum_longitude=LON_MAX,
                minimum_latitude=LAT_MIN, maximum_latitude=LAT_MAX,
                output_directory=tmp,
                output_filename=out_name,
                disable_progress_bar=True,
            )
        except Exception as e:
            # repr(e) so a real message reaches the log/alarm even when str(e) is empty.
            fail(f"copernicusmarine subset failed: {e!r}")

        nc_path = os.path.join(tmp, out_name)
        if not os.path.exists(nc_path):
            cands = sorted(glob.glob(os.path.join(tmp, "*.nc")))
            if not cands:
                fail("subset produced no NetCDF output")
            nc_path = cands[0]

        try:
            ds = xr.open_dataset(nc_path)
        except Exception as e:
            fail(f"opening the subset NetCDF failed: {e!r}")

        if "time" not in ds.coords or int(ds.sizes.get("time", 0)) == 0:
            fail("subset returned no time steps")

        # --- Latest day: read the TIME COORDINATE, never the stale global attr ---
        ds_last = ds.isel(time=-1)
        data_date = str(np.datetime64(ds_last["time"].values, "D"))  # YYYY-MM-DD

        today = dt.datetime.now(dt.timezone.utc).date()
        age = (today - dt.date.fromisoformat(data_date)).days
        if age < 0:
            fail(f"latest data_date {data_date} is in the future vs {today} (clock/source error)")
        if age > MAX_AGE_DAYS:
            fail(f"Copernicus chlorophyll stale: latest {data_date} ({age} d old > {MAX_AGE_DAYS})")

        try:
            chl = ds_last[VARIABLE].load()
        except Exception as e:
            fail(f"loading the CHL slice failed: {e!r}")

        lat = np.asarray(chl["latitude"].values, dtype=float)
        lon = np.asarray(chl["longitude"].values, dtype=float)
        arr = np.asarray(chl.values, dtype=float).squeeze()
        ds.close()

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
        tmp_out = OUT_PATH.with_name(OUT_PATH.name + ".tmp")
        tmp_out.write_text(json.dumps(out, separators=(",", ":")))
        tmp_out.replace(OUT_PATH)
        print(f"Wrote {OUT_PATH} — data_date={data_date}, grid={lat.size}x{lon.size}, "
              f"valid={frac:.1%}, bytes={OUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
