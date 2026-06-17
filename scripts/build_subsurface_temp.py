#!/usr/bin/env python3
"""Build the BlueWater subsurface-temperature proxy JSON from Copernicus Global Physics.

Publishes `thetao` (sea-water potential temperature) over the SW Florida bbox at a SMALL set
of depths bracketing the thermocline, across the planning horizon (nowcast + forecast days),
as a compact static JSON the app reads. Converts °C → °F.

Fetch: `copernicusmarine.subset` (direct NetCDF — the reliable headless path). Auth via the
COPERNICUSMARINE_SERVICE_USERNAME / _PASSWORD env vars (reuses the chlorophyll proxy's secrets)
or a cached ~/.copernicusmarine login locally.

TWO DISTINCT NULLS (the app MUST tell them apart):
  * `null` inside a PUBLISHED layer = **no water that deep here** (seafloor/land). The ocean
    model fills every wet cell, so within a fetched layer a null can only mean the seabed is
    shallower than this depth. That is a REAL negative for a species holding at that depth
    ("too shallow — no water at its depth"), NOT missing data.
  * A whole depth/day ABSENT from the JSON, or a stale `data_date` = data **.unavailable** →
    the app falls back to surface SST + flags. Encoded by PRESENCE/ABSENCE (a layer that was
    fetched appears in `days[].depths[]`), plus per-layer `validFraction` for a sanity guard.

HARD RULES (same as the chlorophyll proxy):
  * `data_date` from the `time` COORDINATE, never a global attr.
  * Fail-don't-overwrite; every failure prints `repr(e)` (never an empty string).
  * Coverage/staleness guard: must COVER THE TARGET DAY (nowcast ≈ today) AND the shallowest
    published layer must have plausible coverage (else the fetch is suspect, not just deep
    bathymetry) — otherwise exit non-zero and write nothing.
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

DATASET_ID = "cmems_mod_glo_phy-thetao_anfc_0.083deg_P1D-m"
VARIABLE = "thetao"

# SW Florida bbox — matches the scan grid / chlorophyll proxy.
LAT_MIN, LAT_MAX = 24.6, 27.9
LON_MIN, LON_MAX = -84.5, -81.7

# Small depth set bracketing the thermocline where in-range pelagics hold (wahoo ~30 m,
# blackfin ~60 m, deeper ~90 m). Snapped to the nearest model levels at fetch time.
TARGET_DEPTHS_M = [30.0, 60.0, 90.0]
DEPTH_FETCH_MAX_M = 100.0          # subset 0..100 m, then select the nearest levels
FORECAST_DAYS = 7                  # nowcast + 6 forecast days (≈ the tide planning window)

# Guards
MAX_NOWCAST_AGE_DAYS = 2           # the earliest published day must be ≈ today (covers it)
MIN_SHALLOW_VALID_FRACTION = 0.40  # the SHALLOWEST layer should be mostly wet; far below ⇒ bad fetch

OUT_PATH = Path(__file__).resolve().parent.parent / "subsurface-temp" / "latest.json"


def fail(msg: str) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    print("Refusing to overwrite the last-good JSON; exiting non-zero.", file=sys.stderr)
    sys.exit(1)


def c_to_f(x: float) -> float:
    return x * 9.0 / 5.0 + 32.0


def main() -> None:
    today = dt.datetime.now(dt.timezone.utc).date()
    end = today + dt.timedelta(days=FORECAST_DAYS - 1)

    with tempfile.TemporaryDirectory() as tmp:
        out_name = "swfl_thetao.nc"
        try:
            copernicusmarine.subset(
                dataset_id=DATASET_ID,
                variables=[VARIABLE],
                minimum_longitude=LON_MIN, maximum_longitude=LON_MAX,
                minimum_latitude=LAT_MIN, maximum_latitude=LAT_MAX,
                minimum_depth=0.0, maximum_depth=DEPTH_FETCH_MAX_M,
                start_datetime=f"{today}T00:00:00", end_datetime=f"{end}T23:59:59",
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

        for dim in ("time", "depth", "latitude", "longitude"):
            if dim not in ds.coords or int(ds.sizes.get(dim, 0)) == 0:
                fail(f"subset missing coordinate '{dim}'")

        # --- Dates from the time COORDINATE (forecast product → first day is the nowcast) ---
        times = [np.datetime64(t, "D") for t in ds["time"].values]
        dates = [str(t) for t in times]
        data_date = dates[0]
        age = (today - dt.date.fromisoformat(data_date)).days
        if age > MAX_NOWCAST_AGE_DAYS or age < 0:
            fail(f"subsurface temp nowcast {data_date} does not cover today {today} (age {age} d)")

        # --- Select the model depth levels nearest the targets (dedup, ascending) ---
        depths = np.asarray(ds["depth"].values, dtype=float)
        sel = sorted({int(np.argmin(np.abs(depths - tgt))) for tgt in TARGET_DEPTHS_M})
        sel_depths = [round(float(depths[i]), 1) for i in sel]

        lat = np.asarray(ds["latitude"].values, dtype=float)
        lon = np.asarray(ds["longitude"].values, dtype=float)
        flip_lat = lat.size > 1 and lat[0] > lat[-1]
        flip_lon = lon.size > 1 and lon[0] > lon[-1]
        if flip_lat:
            lat = lat[::-1]
        if flip_lon:
            lon = lon[::-1]

        def layer(ti: int, di: int):
            arr = np.asarray(ds[VARIABLE].isel(time=ti, depth=di).values, dtype=float)
            if arr.ndim != 2:
                fail(f"expected 2-D layer, got shape {arr.shape}")
            if flip_lat:
                arr = arr[::-1, :]
            if flip_lon:
                arr = arr[:, ::-1]
            finite = np.isfinite(arr)
            frac = float(finite.sum()) / arr.size if arr.size else 0.0
            values = [[round(c_to_f(float(v)), 2) if np.isfinite(v) else None for v in row] for row in arr]
            return values, round(frac, 4)

        # Coverage guard on the shallowest layer of the nowcast day: a wet, shallow depth
        # should be mostly non-null; far below ⇒ the fetch is suspect (not just deep bathymetry).
        _, shallow_frac = layer(0, sel[0])
        if shallow_frac < MIN_SHALLOW_VALID_FRACTION:
            fail(f"shallowest layer ({sel_depths[0]} m) only {shallow_frac:.0%} valid "
                 f"(< {MIN_SHALLOW_VALID_FRACTION:.0%}) — suspect fetch, not bathymetry")

        days = []
        for ti, d in enumerate(dates):
            depth_layers = []
            for di, dm in zip(sel, sel_depths):
                values, frac = layer(ti, di)
                depth_layers.append({"depthM": dm, "validFraction": frac, "values": values})
            days.append({"date": d, "depths": depth_layers})
        ds.close()

        out = {
            "dataset": DATASET_ID,
            "variable": VARIABLE,
            "units": "degF",
            "data_date": data_date,                 # nowcast day, from the time COORDINATE
            "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "horizon_days": len(dates),
            "depthsM": sel_depths,                  # actual model levels (≈ targets)
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
            # null inside a published layer = no water that deep (seafloor); a missing day/depth
            # or stale data_date = .unavailable. See module docstring.
            "null_means": "no water at this depth (seafloor) — a real negative, not missing data",
            "days": days,
        }

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = OUT_PATH.with_name(OUT_PATH.name + ".tmp")
        tmp_out.write_text(json.dumps(out, separators=(",", ":")))
        tmp_out.replace(OUT_PATH)
        print(f"Wrote {OUT_PATH} — data_date={data_date}, {len(dates)} days, depths={sel_depths} m, "
              f"shallow valid={shallow_frac:.0%}, bytes={OUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
