#!/usr/bin/env python3
"""Decode S-57 integer attribute codes that ENC Direct leaves as numbers.

Verified 2026-07-03: some fields arrive pre-decoded as strings (e.g. CATWRK
"non-dangerous wreck"), others stay integer-coded (e.g. WATLEV=3). Tables below
cover the fields the app symbolizes. Source: IHO S-57 Appendix A attribute
catalogue (verify each table against the official catalogue before shipping —
marked PARTIAL until reviewed).
"""

# PARTIAL — review against IHO S-57 attribute catalogue before release.
WATLEV = {
    1: "partly submerged at high water", 2: "always dry", 3: "always under water",
    4: "covers and uncovers", 5: "awash", 6: "subject to flooding", 7: "floating",
}
QUASOU = {
    1: "depth known", 2: "depth unknown", 3: "doubtful sounding", 4: "unreliable sounding",
    5: "no bottom found", 6: "least depth known", 7: "least depth unknown, safe clearance",
    8: "value reported, not surveyed", 9: "value reported, not confirmed", 10: "maintained depth",
    11: "not regularly maintained",
}
CATOBS = {
    1: "snag/stump", 2: "wellhead", 3: "diffuser", 4: "crib", 5: "fish haven",
    6: "foul area", 7: "foul ground", 8: "ice boom", 9: "ground tackle", 10: "boom",
}
COLOUR = {
    1: "white", 2: "black", 3: "red", 4: "green", 5: "blue", 6: "yellow",
    7: "grey", 8: "brown", 9: "amber", 10: "violet", 11: "orange", 12: "magenta", 13: "pink",
}
CATLAM = {  # lateral mark category — drives red/green symbology (IALA-B in US)
    1: "port-hand lateral", 2: "starboard-hand lateral",
    3: "preferred channel to starboard", 4: "preferred channel to port",
}

TABLES = {"WATLEV": WATLEV, "QUASOU": QUASOU, "CATOBS": CATOBS, "COLOUR": COLOUR, "CATLAM": CATLAM}


def decode(props: dict) -> dict:
    """Return props with <FIELD>_TXT companions for known coded fields."""
    out = dict(props)
    for field, table in TABLES.items():
        v = props.get(field)
        if v is None:
            continue
        try:
            out[f"{field}_TXT"] = table.get(int(float(v)), f"code {v}")
        except (TypeError, ValueError):
            out[f"{field}_TXT"] = str(v)  # already decoded by ENC Direct
    return out


if __name__ == "__main__":
    import json, sys
    fc = json.load(open(sys.argv[1]))
    for f in fc.get("features", []):
        f["properties"] = decode(f.get("properties", {}))
    json.dump(fc, open(sys.argv[2], "w"))
    print(f"decoded → {sys.argv[2]}")
