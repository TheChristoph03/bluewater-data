# ENC Direct layer map — nav-relevant classes (VERIFIED live 2026-07-03)

Enumerated from `https://encdirect.noaa.gov/arcgis/rest/services/encdirect/<service>/MapServer?f=pjson`
for all three bands used by the nav-features pack. Layer ids DIFFER per band —
never reuse ids across services. Name prefixes: `Coastal.`, `Approach.`, `Harbor.`
(sic — the service is `enc_harbour`, the layer prefix is `Harbor.`).
`maxRecordCount=1000`, `supportedQueryFormats: JSON, geoJSON`, SR 4326 — all confirmed.

| Canonical class | enc_coastal | enc_approach | enc_harbour |
|---|---|---|---|
| beacon_cardinal | — | 1 | — |
| beacon_isolated_danger | — | 2 | — |
| beacon_lateral | 1 | 3 | 1 |
| beacon_safe_water | 2 | 4 | 2 |
| beacon_special | 3 | 5 | 3 |
| buoy_cardinal | — | 6 | 4 |
| buoy_isolated_danger | 4 | 7 | 5 |
| buoy_lateral | 5 | 8 | 6 |
| buoy_safe_water | 6 | 9 | 7 |
| buoy_special | 7 | 10 | 8 |
| daymark | 8 | 11 | 9 |
| light | 10 | 13 | 11 |
| light_float | 11 | 14 | 12 |
| caution_area_point | 29 | 34 | 31 |
| obstruction_point | 30 | 36 | 33 |
| rock_underwater | 31 | 37 | 34 |
| wreck_point | 33 | 39 | 36 |
| obstruction_line | 78 | 103 | 99 |
| recommended_track | 102 | 139 | 134 |
| caution_area | 116 | 159 | 154 |
| obstruction_area | 118 | 161 | 156 |
| wreck_area | 120 | 163 | 158 |
| restricted_area | 141 | 202 | 197 |
| fairway_area | 150 | 213 | 208 |
| recommended_track_area | — | 218 | 213 |
| unsurveyed_area | 168 | 235 | 230 |

Notes:
- Cardinal marks exist only in approach/harbour — a coastal-only fetch misses them.
- Bands overlap in coverage (adjacent ENC cells also overlap within a band);
  `fetch_enc_features.py` dedupes by (class, rounded geometry), keeping the
  most-detailed band: harbour > approach > coastal.
- Soundings/Depth_Contour layers deliberately excluded — depth comes from the
  GEBCO/BlueTopo pipelines, not chart soundings.
