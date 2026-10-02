"""Fetch a small OSM road NETWORK (not one named route) for the Arc 7.2
slice-1 test area, generalizing fetch_data.py's single-`ref`-regex query
(which only works for a single stitched route like the Dragon).

Usage:  python fetch_osm_network.py

Outputs to  data/raw/<AREA_NAME>_test/ :
    osm.json    raw Overpass response (ways + nodes for every drivable road
                in the bbox) - same element shape fetch_data.py already
                produces, so build_road_network.py's parsing matches
                build_stage.py's build_centerline().
"""

import json
from pathlib import Path

from fetch_cache import overpass_json

from test_areas import AREA_NAME, BBOX_SNWE

try:
    from test_areas import ROAD_REF_REGEX
except ImportError:
    ROAD_REF_REGEX = None

# ---------------------------------------------------------------- config
# Drivable OSM highway classes; excludes footway/cycleway/path/track/steps
# etc. Same regex-in-Overpass-QL approach fetch_data.py already uses, just
# matching the `highway` tag itself (any drivable class) instead of a `ref`.
HIGHWAY_CLASS_REGEX = (
    "^(motorway|trunk|primary|secondary|tertiary|unclassified|residential"
    "|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link)$"
)

HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def fetch_osm_network():
    s, w, n_, e = BBOX_SNWE
    # ROAD_REF_REGEX scopes to named routes (e.g. a specific highway loop)
    # instead of every drivable road in the bbox - needed once a bbox
    # contains real towns with dense residential grids (2026-07-20, the
    # Skagit/Cascades area), where "every drivable road" would mean whole
    # town street networks, not the loop actually being tested.
    ref_clause = (f'["ref"~"{ROAD_REF_REGEX}"]' if ROAD_REF_REGEX else "")
    # 90s (tuned for the rural Skagit loop) times out on a bbox containing a
    # real city's full street grid (Oahu/Honolulu, 2026-07-27) - bumped same
    # as fetch_osm_buildings.py's equivalent change.
    query = f"""
    [out:json][timeout:180];
    way["highway"~"{HIGHWAY_CLASS_REGEX}"]{ref_clause}({s},{w},{n_},{e});
    (._;>;);
    out body;
    """
    print(f"Querying Overpass for drivable roads in {AREA_NAME} bbox "
          f"{'(ref-filtered: ' + ROAD_REF_REGEX + ')' if ROAD_REF_REGEX else ''} ...")
    data = overpass_json(query, HEADERS)
    ways = [e for e in data["elements"] if e["type"] == "way"]
    nodes = [e for e in data["elements"] if e["type"] == "node"]
    if not ways:
        raise SystemExit(f"No drivable ways matched in the {AREA_NAME} bbox.")
    (RAW_DIR / "osm.json").write_text(json.dumps(data))
    print(f"  got {len(ways)} ways, {len(nodes)} nodes -> {RAW_DIR / 'osm.json'}")
    return data


if __name__ == "__main__":
    fetch_osm_network()
