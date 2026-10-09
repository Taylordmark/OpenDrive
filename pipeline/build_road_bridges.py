"""Bridge deck geometry prep (Arc 7.21, follow-on to embankment skirts §7.19
and the elevation root-cause fix §7.20): reads the "bridge"-class runs
build_road_embankments.py's own §7.21 sub-classification pass already wrote
into road_embankments.json - by the time this script runs, that "bridge"
class only contains GENUINE short spans (water crossings, or a short run
with drop on both sides of the road) - every long sidehill-cut run was
already demoted back to "embankment" upstream, see that module's docstring
for the discriminator design.

This script does NOT re-detect or re-classify anything - it only adds the
one thing bridge geometry needs that embankment skirts didn't: PIER
placement. Same "pipeline ships points, C++ derives geometry" split as every
other streamed layer in this project (AODBridgeManager builds the deck
fascia/underside slab and pier columns at runtime from these points, exactly
the way AODEmbankmentManager derives the skirt slope from build_road_
embankments.py's own points) - this script's only real job is deciding WHERE
along each run a pier belongs.

Pier placement (Taylor-approved design, "next steps.md" §7.21): a pier every
PIER_SPACING_STATIONS stations (5 stations * STATION_SPACING 5m =
25m along-run spacing, inside the brief's 20-30m target), but ONLY where
clearance actually permits - a pier whose top (deck underside, DECK_DEPTH_M
below the pavement edge) sits less than MIN_PIER_CLEARANCE_M above the
sampled terrain (drop_cm from the SAME station point build_road_embankments.py
already shipped) is skipped. This naturally thins piers out near a span's own
ends (abutments) where the drop tapers back toward the classifier's own low
threshold, without a separate abutment-detection pass - a real bridge's
abutment is exactly where the deck meets grade, which is exactly where this
clearance test starts failing on its own.

Usage:  python build_road_bridges.py   (needs road_embankments.json first,
                                         itself needs road_stations.json)

Reads   data/road_tiles/<AREA_NAME>_test/road_embankments.json
Outputs data/road_tiles/<AREA_NAME>_test/road_bridges.json
        {"runs": [{"tile","side","station_xyzd_cm","piers_xyzh_cm"}, ...]}
        (station_xyzd_cm: same flat [x,y,z,drop_cm, ...] shape build_road_
        embankments.py ships - x,y,z = pavement-edge seat point, drop_cm =
        road-above-terrain drop at that station; piers_xyzh_cm: flat
        [x,y,z_top,height_cm, ...] per eligible pier - x,y,z_top = pier TOP
        (deck underside point), height_cm = pier's own vertical extent down
        to terrain)
"""

import json
from pathlib import Path

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_embankments.json"
OUT_PATH = SRC.parent / "road_bridges.json"

DECK_DEPTH_M = 1.25              # deck fascia depth below the pavement edge (task brief: 1-1.5m)
PIER_SPACING_STATIONS = 5        # 5 * STATION_SPACING (5m) = 25m along-run spacing
MIN_PIER_CLEARANCE_M = 1.0       # skip a pier whose own height would be under this


def build_piers(xyzd_cm):
    """xyzd_cm: flat [x,y,z,drop_cm, ...] for one run. Returns a flat
    [x,y,z_top,height_cm, ...] pier list, sampling every PIER_SPACING_STATIONS
    stations and skipping any candidate too close to terrain to read as a
    real column (see module docstring)."""
    scount = len(xyzd_cm) // 4
    deck_depth_cm = DECK_DEPTH_M * 100.0
    min_height_cm = MIN_PIER_CLEARANCE_M * 100.0
    piers = []
    for i in range(0, scount, PIER_SPACING_STATIONS):
        x, y, z, drop_cm = xyzd_cm[4 * i:4 * i + 4]
        height_cm = drop_cm - deck_depth_cm
        if height_cm >= min_height_cm:
            piers.extend([x, y, z - deck_depth_cm, height_cm])
    return piers


def main():
    data = json.loads(SRC.read_text())
    bridge_runs = [r for r in data["runs"] if r["cls"] == "bridge"]

    out_runs = []
    total_piers = 0
    total_stations = 0
    for r in bridge_runs:
        xyzd = r["station_xyzd_cm"]
        # ponytail: overpasses ship deck-only (no column dropped onto the road
        # below); a median pier needs the lower road's footprint here
        piers = [] if r.get("overpass") else build_piers(xyzd)
        total_piers += len(piers) // 4
        total_stations += len(xyzd) // 4
        out_runs.append({
            "tile": r["tile"], "side": r["side"],
            "station_xyzd_cm": xyzd, "piers_xyzh_cm": piers,
        })

    OUT_PATH.write_text(json.dumps({"runs": out_runs}))
    total_len_km = 0.0  # informational only - stations are ~5m apart within a run
    n_no_pier = sum(1 for r in out_runs if len(r["piers_xyzh_cm"]) == 0)
    print(f"{len(out_runs)} genuine bridge run(s) ({total_stations} station(s), "
          f"{total_piers} pier(s)) -> {OUT_PATH}")
    print(f"  {n_no_pier} run(s) got zero piers (deck-only, e.g. a short span whose whole "
          f"length stays under {MIN_PIER_CLEARANCE_M:.1f}m clearance after the "
          f"{DECK_DEPTH_M:.2f}m deck depth - reads as abutment-to-abutment, no mid-span support)")


if __name__ == "__main__":
    main()
