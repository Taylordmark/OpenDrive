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
                           "height_m": float|None, "way_id": int,
                           "building_tag": str}, ...]
    plus "ms_height_m": float|None (see ms_building_heights below)
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

import csv
import gzip
import io
import json
from pathlib import Path

import mercantile
import numpy as np
import requests
import shapely

from build_water_tiles import assemble_outer_rings
from fetch_cache import cached_bytes, overpass_json

from test_areas import AREA_NAME, BBOX_SNWE

HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def parse_levels(tags):
    """building:levels / levels."""
    for key in ("building:levels", "levels"):
        if key in tags:
            try:
                return int(float(tags[key]))
            except ValueError:
                return None
    return None


def parse_height(tags):
    """height=* in m (often "45 m"), or None; build_building_mesh turns it
    into storeys (levels_from_height)."""
    try:
        h = float(tags["height"].split()[0])
    except (KeyError, ValueError, IndexError):
        return None
    return h if h > 0 else None


# Microsoft Global ML Building Footprints (ODbL 1.0, credited in
# build_credits.py + README): ~80% of US footprints carry an imagery-derived
# height (m; -1 = none). Realism B1 (2026-10-15): untagged OSM heights were a
# uniform 1..area-cap roll; an OSM footprint now takes the tallest MS height
# whose footprint centroid falls inside it. Fetched per z9 quadkey through
# fetch_cache (data/cache/ms_buildings/).
MS_LINKS_URL = "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"


def ms_building_heights(bbox_snwe):
    """(lon[], lat[], height_m[]) of MS footprint centroids with a height."""
    s, w, n_, e = bbox_snwe
    get = lambda url: lambda: requests.get(url, headers=HEADERS, timeout=600).content
    links = csv.DictReader(io.StringIO(cached_bytes("ms_buildings", "dataset-links.csv",
                                                    get(MS_LINKS_URL)).decode()))
    qks = {mercantile.quadkey(t) for t in mercantile.tiles(w, s, e, n_, 9)}
    lon, lat, hgt = [], [], []
    for row in links:
        if row["QuadKey"] not in qks:
            continue
        data = cached_bytes("ms_buildings", f"{row['Location']}_{row['QuadKey']}.csv.gz", get(row["Url"]))
        for line in gzip.open(io.BytesIO(data), "rt"):
            f = json.loads(line)
            h = f["properties"].get("height") or -1
            if h <= 0:
                continue
            ring = f["geometry"]["coordinates"][0]
            x = sum(p[0] for p in ring) / len(ring)
            y = sum(p[1] for p in ring) / len(ring)
            if w <= x <= e and s <= y <= n_:
                lon.append(x); lat.append(y); hgt.append(h)
    print(f"  {len(hgt)} Microsoft building height(s) in the bbox")
    return np.array(lon), np.array(lat), np.array(hgt)


def attach_ms_heights(buildings, ms):
    """Sets b["ms_height_m"]: tallest MS height whose centroid is inside b."""
    lon, lat, hgt = ms
    polys = [shapely.Polygon([(p[1], p[0]) for p in b["nodes"]]) for b in buildings]
    best = np.full(len(buildings), -1.0)
    if len(hgt):
        tree = shapely.STRtree(shapely.points(lon, lat))
        bi, pi = tree.query([shapely.make_valid(p) for p in polys], predicate="contains")
        np.maximum.at(best, bi, hgt[pi])
    for b, h in zip(buildings, best):
        b["ms_height_m"] = round(float(h), 1) if h > 0 else None
    print(f"  {int((best > 0).sum())} of {len(buildings)} footprint(s) got a Microsoft height")


# Realism B3: roof/wall look tags, kept only when set (build_building_mesh
# honours roof:shape and the colours/materials)
LOOK_TAGS = ("roof:shape", "roof:colour", "roof:material", "building:colour", "building:material")


def building_entry(ring, tags, way_id):
    return {"nodes": ring, "levels": parse_levels(tags), "height_m": parse_height(tags), "way_id": way_id,
            "building_tag": tags.get("building", "yes"), **{k: tags[k] for k in LOOK_TAGS if k in tags}}


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
        buildings.append(building_entry(ring, tags, w_el["id"]))

    ways_by_id = {w_el["id"]: w_el.get("nodes", []) for w_el in ways}
    n_rel_rings = 0
    for rel in relations:
        tags = rel["tags"]
        for ring_ids in assemble_outer_rings(outer_ids[rel["id"]], ways_by_id):
            ring = [nodes[n] for n in ring_ids if n in nodes]
            if len(ring) < 3:
                continue
            buildings.append(building_entry(ring, tags, -rel["id"]))
            n_rel_rings += 1

    attach_ms_heights(buildings, ms_building_heights(BBOX_SNWE))
    (RAW_DIR / "osm_buildings.json").write_text(json.dumps(buildings))
    print(f"  {n_rel_rings} of them from {len(relations)} multipolygon relation(s)")
    print(f"  got {len(buildings)} building footprint(s) ({skipped} skipped, "
          f"not closed rings) -> {RAW_DIR / 'osm_buildings.json'}")
    return buildings


if __name__ == "__main__":
    fetch_osm_buildings()
