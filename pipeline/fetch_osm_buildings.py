"""Fetch OSM building footprints (Arc 7.0 step 3, 2026-07-24) - same
Overpass session/retry/mirror pattern as fetch_osm_network.py, querying
`building=*` ways (+ their nodes) instead of drivable ways.

Generalized to the whole test-area bbox (2026-07-27, for the Oahu testbed) -
originally proof-scoped to ONE real town (Mount Vernon, fetch bbox-gated
around its own coordinate) per next-steps.md Arc 7.0 step 3's "prove the
mechanic on one small case first" discipline. Same bbox-widening step
fetch_osm_network.py already took (single named route -> whole-bbox
network); a whole island has buildings spread across many towns/a real
city, not one fetchable town center.

Usage:  python fetch_osm_buildings.py

Outputs to  data/raw/<AREA_NAME>_test/ :
    osm_buildings.json   [{"nodes": [[lat,lon], ...], "levels": int|None,
                           "way_id": int, "building_tag": str}, ...]
    (multipolygon building relations - malls, podium towers - ship one
    entry per assembled outer ring with way_id = -relation id, so ids never
    collide with real ways; inner rings/courtyards are ignored)

building_tag (2026-07-28, added to fix a real Oahu bug): the way's own
`building=*` tag value (e.g. "house", "apartments", "commercial", or the
generic "yes" when unset/uninformative) - classify_settlements.py needs this
to tell a genuine high-rise downtown apart from a merely DENSE residential
suburb. Footprint COUNT per tile alone can't do it: Kailua/Waimanalo-area
windward tiles have hundreds of small single-family-home footprints packed
into a 2 km tile, clearing the same density bar real downtown Honolulu does,
which without this tag made whole beach suburbs extrude as fields of 8-35
story towers (found live 2026-07-28 - Taylor spawned at Waimanalo Beach:
"there are skyscrapers everywhere"). building_tag lets the classifier count
how many buildings in a tile are ACTUALLY tagged commercial/office/
apartments/etc. instead of assuming density implies height.
"""

import json
from pathlib import Path

from build_water_tiles import assemble_outer_rings
from fetch_cache import overpass_json

from test_areas import AREA_NAME, BBOX_SNWE

HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)


LEVEL_HEIGHT_M = 3.2   # same as build_building_mesh.LEVEL_HEIGHT_M


def parse_levels(tags):
    """building:levels / levels, else height=* (m, often "45 m") / 3.2 m."""
    for key in ("building:levels", "levels"):
        if key in tags:
            try:
                return int(float(tags[key]))
            except ValueError:
                return None
    try:
        return max(1, round(float(tags["height"].split()[0]) / LEVEL_HEIGHT_M))
    except (KeyError, ValueError, IndexError):
        return None


def fetch_osm_buildings():
    s, w, n_, e = BBOX_SNWE
    query = f"""
    [out:json][timeout:600];
    (
      way["building"]({s},{w},{n_},{e});
      relation["building"]({s},{w},{n_},{e});
    );
    (._;>>;);
    out body;
    """
    print(f"Querying Overpass for building footprints in the {AREA_NAME} bbox "
          f"({s:.3f},{w:.3f},{n_:.3f},{e:.3f}) ...")
    data = overpass_json(query, HEADERS, timeout=660)

    nodes = {el["id"]: (el["lat"], el["lon"])
             for el in data["elements"] if el["type"] == "node"}
    ways = [el for el in data["elements"] if el["type"] == "way"]
    relations = [el for el in data["elements"]
                 if el["type"] == "relation" and "building" in el.get("tags", {})]
    outer_ids = {rel["id"]: [m["ref"] for m in rel.get("members", [])
                             if m.get("type") == "way" and m.get("role") == "outer"]
                 for rel in relations}
    used_way_ids = {wid for ids in outer_ids.values() for wid in ids}
    if not ways:
        raise SystemExit(f"No building footprints matched in the {AREA_NAME} bbox.")

    buildings = []
    skipped = 0
    for w_el in ways:
        if w_el["id"] in used_way_ids or "building" not in w_el.get("tags", {}):
            continue   # a relation's outline (built below) or a member pulled in for coords
        nd_ids = w_el.get("nodes", [])
        if len(nd_ids) < 4 or nd_ids[0] != nd_ids[-1]:
            skipped += 1   # not a closed ring - not a real footprint polygon
            continue
        ring = [nodes[n] for n in nd_ids[:-1] if n in nodes]   # drop repeated closing node
        if len(ring) < 3:
            skipped += 1
            continue
        tags = w_el["tags"]
        buildings.append({"nodes": ring, "levels": parse_levels(tags), "way_id": w_el["id"],
                           "building_tag": tags.get("building", "yes")})

    ways_by_id = {w_el["id"]: w_el.get("nodes", []) for w_el in ways}
    n_rel_rings = 0
    for rel in relations:
        tags = rel["tags"]
        for ring_ids in assemble_outer_rings(outer_ids[rel["id"]], ways_by_id):
            ring = [nodes[n] for n in ring_ids if n in nodes]
            if len(ring) < 3:
                continue
            buildings.append({"nodes": ring, "levels": parse_levels(tags), "way_id": -rel["id"],
                               "building_tag": tags.get("building", "yes")})
            n_rel_rings += 1

    (RAW_DIR / "osm_buildings.json").write_text(json.dumps(buildings))
    print(f"  {n_rel_rings} of them from {len(relations)} multipolygon relation(s)")
    print(f"  got {len(buildings)} building footprint(s) ({skipped} skipped, "
          f"not closed rings) -> {RAW_DIR / 'osm_buildings.json'}")
    return buildings


if __name__ == "__main__":
    fetch_osm_buildings()
