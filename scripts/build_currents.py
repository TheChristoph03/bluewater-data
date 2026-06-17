#!/usr/bin/env python3
"""Build the BlueWater surface-current proxy JSON from Copernicus Global Physics.

Publishes the **total** surface current (`utotal`/`vtotal` = geostrophic + tide + Stokes +
Ekman — what a surface vessel / a drifting boat actually feels) over the SW Florida bbox,
3-hourly across the routing/trip window, as `speedKnots` + `bearingToward` (oceanographic set,
matching the app's `MarineSample.currentDirectionDegrees` convention). Fetch via
`copernicusmarine.subset`. Auth reuses the COPERNICUSMARINE_SERVICE_* secrets.

Cadence: native data is hourly; we publish every 3rd hour. 3-hourly (Nyquist period 6 h)
resolves the diurnal + semidiurnal tidal set (M2 12.42 h, S2 12.00 h, K1/O1 ~24 h) — ample for
a ~2 h drift dwell. Shallow-water overtides (M4 6.21 h ≈ Nyquist, M6 4.14 h) are under-resolved;
they are minor on the open shelf (larger only in inlets), so this is acceptable here.

HARD RULES (same as the other proxies):
  * `data_date` from the `time` COORDINATE (first/nowcast step), never a global attr.
  * Fail-don't-overwrite; every failure prints `repr(e)`.
  * Coverage/staleness guard: the nowcast must cover today AND the first step must have
    plausible offshore coverage — else exit non-zero, write nothing.
"""
import sys
import os
import glob
import json
import math
import tempfile
import datetime as dt
from pathlib import Path

import numpy as np
import xarray as xr
import copernicusmarine

DATASET_ID = "cmems_mod_glo_phy_anfc_merged-uv_PT1H-i"
VARIABLES = ["utotal", "vtotal"]

LAT_MIN, LAT_MAX = 24.6, 27.9
LON_MIN, LON_MAX = -84.5, -81.7

HORIZON_HOURS = 48          # match the Open-Meteo routing window (a trip beyond 48 h has no surface data)
CADENCE_HOURS = 3           # publish every 3rd hour
MS_TO_KN = 1.94384

MAX_NOWCAST_AGE_DAYS = 1    # forecast product → the first step must be ~today
MIN_VALID_FRACTION = 0.50   # first step should be mostly wet offshore; far below ⇒ bad fetch

OUT_PATH = Path(__file__).resolve().parent.parent / "currents" / "latest.json"


def fail(msg: str) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    print("Refusing to overwrite the last-good JSON; exiting non-zero.", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    start = now.replace(minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(hours=HORIZON_HOURS)

    with tempfile.TemporaryDirectory() as tmp:
        out_name = "swfl_uv.nc"
        try:
            copernicusmarine.subset(
                dataset_id=DATASET_ID, variables=VARIABLES,
                minimum_longitude=LON_MIN, maximum_longitude=LON_MAX,
                minimum_latitude=LAT_MIN, maximum_latitude=LAT_MAX,
                start_datetime=start.strftime("%Y-%m-%dT%H:%M:%S"),
                end_datetime=end.strftime("%Y-%m-%dT%H:%M:%S"),
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
        if "depth" in ds.dims:
            ds = ds.isel(depth=0)

        if "time" not in ds.coords or int(ds.sizes.get("time", 0)) == 0:
            fail("subset returned no time steps")

        # Decimate hourly → 3-hourly.
        ds = ds.isel(time=slice(0, None, CADENCE_HOURS))
        times = [np.datetime64(t, "s") for t in ds["time"].values]
        data_date = str(np.datetime64(times[0], "D"))
        today = now.date()
        age = (today - dt.date.fromisoformat(data_date)).days
        if age > MAX_NOWCAST_AGE_DAYS or age < 0:
            fail(f"currents nowcast {data_date} does not cover today {today} (age {age} d)")

        lat = np.asarray(ds["latitude"].values, dtype=float)
        lon = np.asarray(ds["longitude"].values, dtype=float)
        flip_lat = lat.size > 1 and lat[0] > lat[-1]
        flip_lon = lon.size > 1 and lon[0] > lon[-1]
        if flip_lat:
            lat = lat[::-1]
        if flip_lon:
            lon = lon[::-1]

        def speed_bearing(ti: int):
            u = np.asarray(ds["utotal"].isel(time=ti).values, dtype=float)
            v = np.asarray(ds["vtotal"].isel(time=ti).values, dtype=float)
            if flip_lat:
                u = u[::-1, :]; v = v[::-1, :]
            if flip_lon:
                u = u[:, ::-1]; v = v[:, ::-1]
            spd = np.hypot(u, v) * MS_TO_KN
            brg = (np.degrees(np.arctan2(u, v)) + 360.0) % 360.0   # toward (u=east, v=north)
            sp = [[round(float(s), 2) if np.isfinite(s) else None for s in row] for row in spd]
            bg = [[round(float(b)) if np.isfinite(b) else None for b in row] for row in brg]   # integer ° (set)
            valid = float(np.isfinite(spd).sum()) / spd.size if spd.size else 0.0
            return sp, bg, round(valid, 4)

        sp0, bg0, valid0 = speed_bearing(0)
        if valid0 < MIN_VALID_FRACTION:
            fail(f"first step only {valid0:.0%} valid (< {MIN_VALID_FRACTION:.0%}) — suspect fetch")

        steps = []
        for ti, t in enumerate(times):
            sp, bg, valid = (sp0, bg0, valid0) if ti == 0 else speed_bearing(ti)
            steps.append({
                "time": str(t) + "Z",
                "validFraction": valid,
                "speedKnots": sp,
                "bearingToward": bg,
            })
        ds.close()

        out = {
            "dataset": DATASET_ID,
            "variables": "utotal,vtotal (total surface current)",
            "units": "speedKnots (kn); bearingToward (° set, oceanographic 'flowing toward')",
            "data_date": data_date,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "cadence_hours": CADENCE_HOURS,
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
            "steps": steps,
        }

        OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = OUT_PATH.with_name(OUT_PATH.name + ".tmp")
        tmp_out.write_text(json.dumps(out, separators=(",", ":")))
        tmp_out.replace(OUT_PATH)
        print(f"Wrote {OUT_PATH} — data_date={data_date}, {len(steps)} steps @ {CADENCE_HOURS}h, "
              f"first-step valid={valid0:.0%}, bytes={OUT_PATH.stat().st_size}")


if __name__ == "__main__":
    main()
