#!/usr/bin/env bash
# P1 "never-blank" bundle (strategy doc §F, spike S3 for the bundle):
#   OSM coastline/land/POI basemap z0–z12 (Protomaps daily-build extract, ODbL)
# + GEBCO offshore contours (annual grid, public domain)
# → ONE .pmtiles. Target ≤ 60 MB for the swfl box.
#
# ONE-OFF build (GEBCO is annual) — dispatched manually via charts-p1-bundle.yml,
# NOT part of the monthly charts cron.
#
# STATUS: UNVERIFIED composition — written 2026-07-03 in a sandbox whose network
# blocked every data host (Geofabrik/GEBCO/protomaps/NOAA). Tippecanoe/tile-join/
# pmtiles legs were verified locally with synthetic data; the fetch legs and real
# sizes are measured by the first Actions run. See README honesty ledger.
#
# Tools required: curl, jq or python3, gdal_translate/gdal_contour/ogr2ogr (gdal-bin),
# tippecanoe + tile-join (>=2.17, pmtiles-native), pmtiles CLI (go-pmtiles).
set -euo pipefail

REGION_FILE="$1"                  # e.g. regions/swfl.json
OUT="$2"                          # e.g. dist/swfl/p1-bundle.pmtiles
WORK="${3:-work-p1}"
GEBCO_URL="${GEBCO_URL:-https://www.bodc.ac.uk/data/open_download/gebco/gebco_2025/zip/}"

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
pmtiles extract "https://build.protomaps.com/$BUILD_KEY" "$WORK/base.pmtiles" \
  --bbox="$BBOX" --maxzoom=12
du -h "$WORK/base.pmtiles"

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

# ---- 4. Single-file bundle ----------------------------------------------------
tile-join -o "$OUT" --force -pk \
  "$WORK/base.pmtiles" "$WORK/contours_major.pmtiles" "$WORK/contours_minor.pmtiles"

BYTES=$(stat -c%s "$OUT")
echo "== P1 bundle: $OUT — $((BYTES / 1000000)) MB (target ≤ 60 MB)"
echo "   components: base=$(du -h "$WORK/base.pmtiles" | cut -f1) major=$(du -h "$WORK/contours_major.pmtiles" | cut -f1) minor=$(du -h "$WORK/contours_minor.pmtiles" | cut -f1)"
if [ "$BYTES" -gt 60000000 ]; then
  echo "::warning::P1 bundle exceeds 60 MB target ($((BYTES / 1000000)) MB) — trim maxzoom to 11, drop minor contours, or exclude heavy basemap layers via tile-join -L/-l"
fi
echo "Record the REAL size in OFFLINE_CHARTS_STRATEGY.md §B.4 (spike S3, replaces EST)."
