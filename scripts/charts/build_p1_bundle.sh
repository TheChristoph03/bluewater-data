#!/usr/bin/env bash
# P1 "never-blank" bundle (strategy doc §F, spike S3 for the bundle):
#   OSM coastline/land/POI basemap z0–z12 (Protomaps daily-build extract, ODbL)
# + GEBCO offshore contours (annual grid, public domain)
# → ONE .pmtiles. Target ≤ 60 MB for the swfl box.
#
# ONE-OFF build (GEBCO is annual) — dispatched manually via charts-p1-bundle.yml,
# NOT part of the monthly charts cron.
#
# STATUS after first real run (2026-07-03): extract + fetch legs reached real data.
# Findings: unfiltered Protomaps extract = 59 MB (blows the whole budget) → basemap
# is now LAYER-FILTERED (marine app: keep coastline/water/land shape + POI/places,
# drop roads/buildings/transit/landuse). Old BODC open_download GEBCO URL is DEAD →
# GEBCO moved distribution to CEDA (verified gebco.net 2026-07-03).
#
# Tools required: curl, jq or python3, gdal_translate/gdal_contour/ogr2ogr (gdal-bin),
# tippecanoe + tile-join (>=2.17, pmtiles-native), pmtiles CLI (go-pmtiles).
set -euo pipefail

REGION_FILE="$1"                  # e.g. regions/swfl.json
OUT="$2"                          # e.g. dist/swfl/p1-bundle.pmtiles
WORK="${3:-work-p1}"
# GEBCO_2025 ice-surface netCDF zip: 4 GB compressed, 7.5 GB unpacked (~12 GB scratch).
GEBCO_URL="${GEBCO_URL:-https://dap.ceda.ac.uk/bodc/gebco/global/gebco_2025/ice_surface_elevation/netcdf/gebco_2025.zip?download=1}"
BASE_MAXZOOM="${BASE_MAXZOOM:-12}"
# Marine-slim keep-list (Protomaps basemap layer ids): land shape + water + labels
# + POIs (marinas etc.) + boundaries. Explicitly dropped: roads, buildings, transit,
# landuse, landcover — they were ~2/3 of the unfiltered 59 MB.
KEEP_LAYERS="${BASE_KEEP_LAYERS:-earth,water,places,pois,boundaries}"

mkdir -p "$WORK" "$(dirname "$OUT")"
read -r W S E N <<<"$(python3 -c "
import json; b=json.load(open('$REGION_FILE'))['bbox']; print(*b)")"
BBOX="$W,$S,$E,$N"
echo "== P1 bundle for bbox $BBOX"

# ---- 1. OSM basemap z0–z12 (coastline/land/water/POI/places) ----------------
# Latest daily build discovered from builds.json; fall back to yesterday's date
# if the listing schema changed (fails loudly if neither works).
BUILD_KEY=$(curl -fsSL https://build.protomaps.com/builds.json \
  | python3 -c "import json,sys; bs=json.load(sys.stdin); print(sorted(b['key'] for b in bs)[-1])" \
  || date -u -d yesterday +%Y%m%d.pmtiles)
echo "== basemap build: $BUILD_KEY"
# NOTE: pmtiles extract cannot filter layers — layer slimming happens in the final
# tile-join. This intermediate file is the FULL basemap (~59 MB for swfl, measured).
pmtiles extract "https://build.protomaps.com/$BUILD_KEY" "$WORK/base.pmtiles" \
  --bbox="$BBOX" --maxzoom="$BASE_MAXZOOM"
du -h "$WORK/base.pmtiles"

# Guard: Protomaps layer ids drift across basemap versions. Verify the keep-list
# against what the build actually contains — fail loudly rather than silently
# shipping a blank map.
python3 - "$WORK/base.pmtiles" "$KEEP_LAYERS" <<'PY'
import json, subprocess, sys
meta = json.loads(subprocess.check_output(["pmtiles", "show", "--metadata", sys.argv[1]]))
have = {l["id"] for l in meta.get("vector_layers", [])}
keep = sys.argv[2].split(",")
missing = [k for k in keep if k not in have]
print("basemap layers present:", sorted(have))
print("keep-list:", keep)
if set(keep).isdisjoint(have):
    sys.exit(f"FATAL: no keep-layer exists in this build — layer ids changed; set BASE_KEEP_LAYERS from: {sorted(have)}")
if not {"earth", "water"} <= have:
    sys.exit("FATAL: 'earth'/'water' missing — refusing to build a blank marine basemap")
if missing:
    print(f"::warning::keep-layers not in this build (skipped): {missing}")
PY

# ---- 2. GEBCO grid, clipped to bbox -----------------------------------------
if [ ! -f "$WORK/gebco_clip.tif" ]; then
  echo "== fetching GEBCO ($GEBCO_URL)"
  curl -fL --retry 3 -o "$WORK/gebco.zip" "$GEBCO_URL"
  unzip -o "$WORK/gebco.zip" -d "$WORK/gebco" "*.nc"
  NC=$(ls "$WORK"/gebco/*.nc | head -1)
  # -projwin is ulx uly lrx lry
  gdal_translate -projwin "$W" "$N" "$E" "$S" "NETCDF:\"$NC\":elevation" "$WORK/gebco_clip.tif"
  rm -rf "$WORK/gebco.zip" "$WORK/gebco"   # global grid is ~7.5 GB unpacked — free it fast
fi

# ---- 3. Offshore contours (depth_m = GEBCO elevation, negative below MSL) ----
gdal_contour -f GeoJSON -a depth_m -i 100 "$WORK/gebco_clip.tif" "$WORK/major_raw.geojson"
gdal_contour -f GeoJSON -a depth_m -i 20  "$WORK/gebco_clip.tif" "$WORK/minor_raw.geojson"
ogr2ogr -f GeoJSON -where "depth_m < 0"                       "$WORK/major.geojson" "$WORK/major_raw.geojson"
ogr2ogr -f GeoJSON -where "depth_m < 0 AND depth_m >= -300"   "$WORK/minor.geojson" "$WORK/minor_raw.geojson"

ATTR="Basemap © OpenStreetMap contributors (ODbL) via Protomaps. Bathymetry: GEBCO 2025 Grid (public domain). Not for navigation."
tippecanoe -q -o "$WORK/contours_major.pmtiles" --force -l contours_major \
  -Z6 -z12 --drop-densest-as-needed --simplification=10 \
  --attribution "$ATTR" "$WORK/major.geojson"
tippecanoe -q -o "$WORK/contours_minor.pmtiles" --force -l contours_minor \
  -Z9 -z12 --drop-densest-as-needed --simplification=10 \
  --attribution "$ATTR" "$WORK/minor.geojson"

# ---- 4. Single-file bundle (basemap layer-filtered here) ---------------------
# tile-join -l keeps ONLY the named layers across all inputs, so the contour
# layers must be in the keep flags too.
KEEP_FLAGS=()
for l in ${KEEP_LAYERS//,/ } contours_major contours_minor; do KEEP_FLAGS+=( -l "$l" ); done
tile-join -o "$OUT" --force -pk "${KEEP_FLAGS[@]}" \
  "$WORK/base.pmtiles" "$WORK/contours_major.pmtiles" "$WORK/contours_minor.pmtiles"

BYTES=$(stat -c%s "$OUT")
echo "== P1 bundle: $OUT — $((BYTES / 1000000)) MB (target ≤ 60 MB, basemap goal 20–30 MB)"
echo "   components: base-unfiltered=$(du -h "$WORK/base.pmtiles" | cut -f1) major=$(du -h "$WORK/contours_major.pmtiles" | cut -f1) minor=$(du -h "$WORK/contours_minor.pmtiles" | cut -f1)"
echo "   basemap: maxzoom=$BASE_MAXZOOM kept-layers=$KEEP_LAYERS"
if [ "$BYTES" -gt 60000000 ]; then
  echo "::warning::P1 bundle exceeds 60 MB target ($((BYTES / 1000000)) MB) — next knobs: BASE_MAXZOOM=11, drop 'pois' from BASE_KEEP_LAYERS, or drop minor contours"
fi
echo "Record the REAL size in OFFLINE_CHARTS_STRATEGY.md §B.4 (spike S3, replaces EST)."
