#!/usr/bin/env python3
"""Build the BlueWater dense-SST proxy JSON from Copernicus OSTIA L4.

Publishes the **dense** sea-surface temperature analysis (`analysed_sst`, 0.05° gap-free) over
the SW Florida bbox as a compact static JSON the app reads. This is the **break-detection /
overlay** SST (5.3) — feeds SST front/edge detection (5.4) and the break-line overlay (5.7).
It is DISTINCT from the coarse Open-Meteo routing SST (5.1): higher resolution, an
analysis (not a forecast), and not a routing-scoring source.

OSTIA L4 NRT is an OBSERVATION/ANALYSIS product: one gap-free map per day, ~1-day latency, and
**no forward forecast**. So we publish the LATEST analysis day (normally yesterday). SST is slow,
so the freshest analysis map is exactly what break detection needs.

Fetch: `copernicusmarine.subset` (direct NetCDF — the reliable headless path). Auth via the
COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD env vars (reuses the other proxies' secrets) or a
cached ~/.copernicusmarine login locally. Converts K → °F.

NULLS: a `null` inside the published grid = **land / coast** (no SST). OSTIA fills every WET cell,
so within a fetched map a null can only be land. A missing file or a stale `data_date` =
data **.unavailable**.

HARD RULES (same as the other proxies):
  * `data_date` from the `time` COORDINATE (the latest step), never a global attr.
  * Fail-don't-overwrite; every failure prints `repr(e)` (never an empty string).
  * Coverage/staleness guard: the latest map must be ≈ today (≤ MAX age; latency means yesterday
    is normal) AND mostly wet over this mostly-ocean bbox — else exit non-zero and write nothing.
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

DATASET_ID = "METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2"
VARIABLE = "analysed_sst"

# SW Florida bbox — matches the scan grid / the other proxies.
LAT_MIN, LAT_MAX = 24.6, 27.9
LON_MIN, LON_MAX = -84.5, -81.7

LOOKBACK_DAYS = 3           # fetch a few days, publish the LATEST available analysis step

# Guards
MAX_NOWCAST_AGE_DAYS = 2    # analysis product, ~1-day latency → the latest map is normally yesterday
MIN_VALID_FRACTION = 0.80   # this bbox is ~89% ocean; far below ⇒ a bad/empty fetch, not geography

OUT_PATH = Path(__file__).resolve().parent.parent / "sst" / "latest.json"


def fail(msg: str) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    print("Refusing to overwrite the last-good JSON; exiting non-zero.", file=sys.stderr)
    sys.exit(1)


def k_to_f(x: float) -> float:
    return x * 9.0 / 5.0 - 459.67


def main() -> None:
    today = dt.datetime.now(dt.timezone.utc).date()
    start = today - dt.timedelta(days=LOOKBACK_DAYS)

    with tempfile.TemporaryDirectory() as tmp:
        out_name = "swfl_sst.nc"
        try:
            copernicusmarine.subset(
                dataset_id=DATASET_ID,
                variables=[VARIABLE],
                minimum_longitude=LON_MIN, maximum_longitude=LON_MAX,
                minimum_latitude=LAT_MIN, maximum_latitude=LAT_MAX,
                start_datetime=f"{start}T00:00:00", end_datetime=f"{today}T23:59:59",
                output_directory=tmp, output_filename=out_name,
                disable_progress_bar=True,
            )
        except Exception as e:
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

        for dim in ("time", "latitude", "longitude"):
            if dim not in ds.coords or int(ds.sizes.get(dim, 0)) == 0:
                fail(f"subset missing coordinate '{dim}'")

        # --- Latest day from the time COORDINATE (analysis product → newest step = the map) ---
        times = [np.datetime64(t, "D") for t in ds["time"].values]
        latest_idx = int(np.argmax(ds["time"].values))
        data_date = str(times[latest_idx])
        age = (today - dt.date.fromisoformat(data_date)).days
        if age > MAX_NOWCAST_AGE_DAYS or age < 0:
            fail(f"SST analysis {data_date} is not current for today {today} (age {age} d)")

        lat = np.asarray(ds["latitude"].values, dtype=float)
        lon = np.asarray(ds["longitude"].values, dtype=float)
        flip_lat = lat.size > 1 and lat[0] > lat[-1]
        flip_lon = lon.size > 1 and lon[0] > lon[-1]
        if flip_lat:
            lat = lat[::-1]
        if flip_lon:
            lon = lon[::-1]

        arr = np.asarray(ds[VARIABLE].isel(time=latest_idx).values, dtype=float)
        if arr.ndim != 2:
            fail(f"expected 2-D SST map, got shape {arr.shape}")
        if flip_lat:
            arr = arr[::-1, :]
        if flip_lon:
            arr = arr[:, ::-1]
        ds.close()

        finite = np.isfinite(arr)
        valid = float(finite.sum()) / arr.size if arr.size else 0.0
        if valid < MIN_VALID_FRACTION:
            fail(f"latest SST map only {valid:.0%} valid (< {MIN_VALID_FRACTION:.0%}) — suspect fetch, "
                 f"not geography (this bbox is ~89% ocean)")

        values = [[round(k_to_f(float(v)), 2) if np.isfinite(v) else None for v in row] for row in arr]

        out = {
            "dataset": DATASET_ID,
            "variable": VARIABLE,
            "units": "degF",
            "data_date": data_date,                 # latest analysis day, from the time COORDINATE
            "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "validFraction": round(valid, 4),
            "bbox": {
                "latMin": round(float(lat.min()), 6), "latMax": round(float(lat.max()), 6),
                "lonMin": round(float(lon.min()), 6), "lonMax": round(float(lon.max()), 6),
            },
            "grid": {
                "lat0": round(float(lat[0]), 6), "lon0": round(float(lon[0]), 6),
                "latStep": round(float(abs(lat[1] - lat[0])), 6) if lat.size > 1 else 0.0,
                "lonStep": round(float(abs(lon[1] - lon[0])), 6) if lon.size > 1 else 0.0,
                "nLat": int(lat.size), "nLon": int(lon.size),
            },
            # null inside the grid = land/coast (no SST); a missing file or stale data_date = .unavailable.
            "null_means": "land / coast (no SST) — OSTIA fills every wet cell; not missing data",
            "values": values,                       # °F, row-major lat-ascending × lon-ascending
        }

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = OUT_PATH.with_name(OUT_PATH.name + ".tmp")
        tmp_out.write_text(json.dumps(out, separators=(",", ":")))
        tmp_out.replace(OUT_PATH)
        print(f"Wrote {OUT_PATH} — data_date={data_date}, grid={out['grid']['nLat']}x{out['grid']['nLon']}, "
              f"valid={valid:.0%}, bytes={OUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
