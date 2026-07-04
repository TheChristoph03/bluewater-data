#!/usr/bin/env bash
# GeoJSON (fetched + decoded) → nav-features-<region>.pmtiles
# UNTESTED composition — first Actions run is the test. Tools: tippecanoe ≥ 2.17 (writes PMTiles directly).
set -euo pipefail
REGION="$1"          # e.g. swfl
IN_DIR="$2"          # dir of decoded *.geojson
OUT="$3"             # e.g. dist/swfl/nav-features.pmtiles

# One tippecanoe layer per feature class (file stem after service prefix).
# ".dec" suffix stripped — spike 2026-07-03 caught layers named "wreck_point.dec".
args=()
for f in "$IN_DIR"/*.geojson; do
  stem="$(basename "$f" .geojson)"
  layer="${stem#*__}"
  layer="${layer%.dec}"
  args+=( -L "$layer:$f" )
done

# Density fix 2026-07-03 (Redfish Pass finding: z11=24/z12=56/z13=102 vs z14=205
# aids — a ~2.5×/zoom ramp, i.e. tippecanoe's DEFAULT point drop-rate below
# basezoom, NOT --drop-densest-as-needed, which only fires on oversized tiles):
#   --base-zoom=10        -> FULL point density z10–z14; default 2.5x thinning
#                            only at z6–9 overview zooms (nobody inspects marks there)
#   drop-densest REMOVED  -> no silent mid-zoom thinning path left
#   --maximum-tile-bytes=2500000 -> guardrail FAILS LOUDLY instead of thinning
#                            if a tile busts 2.5 MB (default 500 KB would abort
#                            on dense ICW z10 tiles without any drop flag)
# --no-tile-size-limit-message is NOT a tippecanoe flag (spike 2026-07-03: binary rejects it).
tippecanoe -o "$OUT" --force \
  --minimum-zoom=6 --maximum-zoom=14 \
  --base-zoom=10 \
  --maximum-tile-bytes=2500000 \
  --generate-ids --read-parallel \
  --name "bluewater-nav-features-$REGION" \
  --attribution "NOAA ENC via ENC Direct (CC0). Not for navigation." \
  "${args[@]}"

echo "wrote $OUT ($(du -h "$OUT" | cut -f1)) — record real size in OFFLINE_CHARTS_STRATEGY.md §B.4"
