"""Extrude OSM building footprints into simple facade meshes (Arc 7.0 step
3, 2026-07-24) - "simple extruded facades" per Taylor's scope decision:
real footprint polygon -> archetype-appropriate height band -> wall quads
+ an ear-clipped flat roof cap, tiled facade UV. Deterministic per building
(seeded off the OSM way ID), zero per-instance storage beyond the
footprint polygon itself (already compact vector data) - same
zero-per-instance-storage logic as the tree HISM scatter, just
footprint-driven instead of tile-seed-scatter-driven.

Also the offline "no building in the road" verification gate (in-line,
not a separate script - see next steps.md Arc 7.0 step 3): any footprint
with a vertex closer than ROAD_CLEARANCE_M (CITY_ROAD_CLEARANCE_M in City
tiles) to the nearest road station's pavement edge is skipped and counted, not silently placed on top of the road.

Generalized 2026-07-27 (for the Oahu testbed) beyond the Mount-Vernon-only
proof: "City" tiles (classify_settlements.py's new density split) get a
real high-rise height range instead of the one-size-fits-all 1-3 stories,
and the nearest-road-station lookup (needed for both ground height and the
road-exclusion check) is now tile-bucketed instead of a brute-force scan
against a single town's pre-filtered station subset - a whole island's
station count makes an O(buildings x all stations) scan actually too slow,
not just inelegant.

Usage:  python build_building_mesh.py    (needs osm_buildings.json,
                                            road_stations.json,
                                            settlement_tiles.json first)

Reads   data/raw/<AREA_NAME>_test/osm_buildings.json
        data/road_tiles/<AREA_NAME>_test/road_stations.json
        data/road_tiles/<AREA_NAME>_test/settlement_tiles.json
Outputs data/road_tiles/<AREA_NAME>_test/building_mesh.json
        {"buildings": [{"name","tile","vertices","uvs","triangles",
                        "wall_set","flat_roof"}, ...]}
        (same {"vertices","uvs","triangles"} chunk schema
        road_mesh_common.build_road_chunks already produces, so
        export_building_mesh_bin.py can bucket it the same way
        export_road_mesh_bin.py buckets road chunks)
"""

import json
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from building_roofs import clean_ring, triangulate
from road_mesh_common import ROAD_HALF_WIDTH, SKIRT_EXTRA, half_width_for_stations, road_ue_z_m
from test_areas import AREA_NAME, MAX_BUILDING_LEVELS, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, to_local

# Clearance a footprint corner must keep beyond the nearest station's OWN
# pavement edge (per-station half-width, same rule the road mesh and the
# terrain carve use). Was a flat ROAD_HALF_WIDTH + 5 m = 9.5 m from the
# centreline everywhere (2026-10-14): real downtown blocks sit at the back
# of the sidewalk ~6-9 m from centre, so that gate deleted ~1/3 of
# downtown Honolulu incl. tagged towers. City tiles only keep a sidewalk;
# elsewhere stay clear of the terrain-carve skirt (the old +2 m slop
# covered the fixed-width guess, which the real width now replaces).
ROAD_CLEARANCE_M = SKIRT_EXTRA
CITY_ROAD_CLEARANCE_M = 1.5

LEVEL_HEIGHT_M = 3.2
# Storeys per building (realism B1, 2026-10-15), first hit wins:
#   1. OSM building:levels / levels (fetch_osm_buildings.py);
#   2. OSM height=*, else Microsoft Global ML Buildings' imagery-derived
#      height (ms_height_m, ~80% of US footprints), both to the roof top:
#      levels_from_height() takes ROOF_ALLOWANCE_M off first, or a 9 m
#      two-storey house with a pitched roof reads as three storeys;
#   3. estimate_levels(): small-house types roll their BUILDING_TYPE range;
#      anything else copies a random one of its K nearest neighbours with
#      real data (1. or 2.) within NEIGHBOUR_RADIUS_M - so it lands on the
#      town's own height mix - capped by footprint area (a 9 m x 9 m
#      footprint is not a tower); no neighbours -> its type range or 1-2.
# Replaces the uniform 1..MAX_BUILDING_LEVELS roll City tiles used to get,
# which built ~21.7k 8+ storey towers on Oahu (OSM tags ~500), 8-12 storeys
# on Russian Hill and 5-6 in Frisco/Breck. MAX_BUILDING_LEVELS
# (test_areas.py) stays the final backstop.
BUILDING_TYPE_LEVEL_RANGE = {
    # small residential / outbuildings - one or two stories, never more
    "house": (1, 2), "detached": (1, 2), "semidetached_house": (1, 2),
    "residential": (1, 2), "terrace": (1, 2), "bungalow": (1, 1),
    "farm": (1, 2), "cabin": (1, 1), "static_caravan": (1, 1),
    "houseboat": (1, 1), "garage": (1, 1), "shed": (1, 1), "roof": (1, 1),
    "carport": (1, 1), "greenhouse": (1, 1), "construction": (1, 2),
    # small commercial / retail - storefronts, not office towers
    "commercial": (1, 2), "retail": (1, 2), "shop": (1, 2),
    # multi-unit residential - genuinely taller than a house, still modest
    "apartments": (3, 4), "dormitory": (3, 4),
    # civic / hospitality
    "church": (2, 3), "school": (2, 3), "public": (2, 3),
    "government": (2, 3), "fire_station": (1, 2), "office": (3, 6),
    "hotel": (3, 5),
}
NEIGHBOUR_K = 12
NEIGHBOUR_RADIUS_M = 250.0
# (footprint m2 below which, max storeys) - sheds, houses, flats, mid-rise
AREA_LEVEL_CAPS = ((40.0, 1), (100.0, 2), (250.0, 4), (500.0, 10))


ROOF_ALLOWANCE_M = 1.5


def levels_from_height(h):
    return max(1, round((h - ROOF_ALLOWANCE_M) / LEVEL_HEIGHT_M))


def level_source(b):
    return next((k for k in ("levels", "height_m", "ms_height_m") if b.get(k)), None)


def known_levels(b):
    """OSM levels, else OSM / Microsoft height in storeys, else None."""
    k = level_source(b)
    return None if k is None else b[k] if k == "levels" else levels_from_height(b[k])


def estimate_levels(building_tag, area_m2, neighbour_levels, rng):
    """Storeys for a building with no height data (see the table above)."""
    tag_range = BUILDING_TYPE_LEVEL_RANGE.get(building_tag)
    if tag_range and tag_range[1] <= 2:
        return rng.randint(*tag_range)
    levels = rng.choice(neighbour_levels) if neighbour_levels else rng.randint(*(tag_range or (1, 2)))
    for max_area, cap in AREA_LEVEL_CAPS:
        if area_m2 < max_area:
            return min(levels, cap)
    return levels


# Facade pick per building: (wall_set choices, flat_roof). wall_set indexes
# WALL_LAYERS in unreal/apply_building_material.py (0 red brick, 1 tan
# brick, 2 wood siding, 3 plaster, 4 concrete); flat_roof 1 = concrete flat
# roof, 0 = shingle. Anything 8+ stories is always concrete + flat.
_RESIDENTIAL_FACADE = ([2, 3, 0, 1], 0)
_OUTBUILDING_FACADE = ([2, 4], 0)
BUILDING_TYPE_FACADE = {
    **{t: ([4, 3, 0, 1], 1) for t in ("office", "government", "public", "school")},
    **{t: ([0, 1, 3, 4], 1) for t in ("apartments", "hotel", "dormitory")},
    **{t: ([0, 1, 3], 1) for t in ("commercial", "retail", "shop")},
    **{t: _RESIDENTIAL_FACADE for t in (
        "house", "detached", "semidetached_house", "residential", "terrace",
        "bungalow", "cabin", "farm", "static_caravan", "houseboat",
        "construction")},
    **{t: _OUTBUILDING_FACADE for t in ("garage", "shed", "carport", "greenhouse", "roof")},
    "church": ([0, 1, 3], 0),
}
HIGH_RISE_LEVELS = 8


def pick_facade(building_tag, levels, rng):
    """(wall_set, flat_roof) for one building - see BUILDING_TYPE_FACADE."""
    if levels >= HIGH_RISE_LEVELS:
        return 4, 1
    choices, flat = BUILDING_TYPE_FACADE.get(building_tag) or (
        ([0, 3, 4], 1) if levels >= 4 else ([0, 1, 2, 3, 4], 0))
    return rng.choice(choices), flat


FACADE_TILE_M = 4.0   # UV repeat period on walls/roof, matches this
                       # project's DETAIL_TILE_CM-style tiling convention

ROOT = Path(__file__).resolve().parent.parent
BUILDINGS_PATH = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm_buildings.json"
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
SETTLEMENT_PATH = STATIONS_PATH.parent / "settlement_tiles.json"
OUT_PATH = STATIONS_PATH.parent / "building_mesh.json"


def load_stations():
    data = json.loads(STATIONS_PATH.read_text())
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    half_w = np.concatenate([
        half_width_for_stations(seg["highway_class"], seg.get("oneway"), seg.get("lanes"))
        if seg.get("highway_class") else np.full(len(seg["east_m"]), ROAD_HALF_WIDTH)
        for seg in data["segments"]])
    return east, north, elev, half_w


def extrude_footprint(ring_xy_cm, base_z_cm, height_cm, seed):
    """ring_xy_cm: list of (x_cm, y_cm), CCW or CW as OSM gives it (not
    guaranteed) - wall winding is derived per-edge from the edge direction
    so it doesn't matter; the roof fan uses the polygon's own signed area
    to pick a consistent winding for its own faces."""
    n = len(ring_xy_cm)
    verts, uvs, tris = [], [], []

    # ---- walls: one quad (2 tris) per edge ----
    s = 0.0   # cumulative perimeter distance, for wall U
    for i in range(n):
        x0, y0 = ring_xy_cm[i]
        x1, y1 = ring_xy_cm[(i + 1) % n]
        edge_len_m = math.hypot(x1 - x0, y1 - y0) / 100.0
        u0, u1 = s / FACADE_TILE_M, (s + edge_len_m) / FACADE_TILE_M
        s += edge_len_m
        v_top = height_cm / 100.0 / FACADE_TILE_M

        b = len(verts)
        verts.extend([(x0, y0, base_z_cm), (x1, y1, base_z_cm),
                      (x1, y1, base_z_cm + height_cm), (x0, y0, base_z_cm + height_cm)])
        uvs.extend([(u0, 0.0), (u1, 0.0), (u1, v_top), (u0, v_top)])
        tris.append((b, b + 1, b + 2))
        tris.append((b, b + 2, b + 3))

    # ---- roof: ear-clipped flat cap (2026-10-15, realism B5). The old
    # centroid fan crossed outside 33-60% of real (concave) footprints;
    # building_roofs.triangulate is the same ear clipper the pitched roofs
    # use. Cap triangles wind CCW in (x, y) whatever the ring's own winding,
    # so the roof faces up. ----
    signed_area = sum(ring_xy_cm[i][0] * ring_xy_cm[(i + 1) % n][1]
                       - ring_xy_cm[(i + 1) % n][0] * ring_xy_cm[i][1] for i in range(n))
    roof_z = base_z_cm + height_cm
    ccw = clean_ring(list(ring_xy_cm) if signed_area >= 0 else list(ring_xy_cm)[::-1])
    ears = triangulate(ccw) if len(ccw) >= 3 else None
    if ears is not None:
        cb = len(verts)
        for x, y in ccw:
            verts.append((x, y, roof_z))
            uvs.append((x / 100.0 / FACADE_TILE_M, y / 100.0 / FACADE_TILE_M))
        tris.extend((cb + a, cb + b, cb + c) for a, b, c in ears)
        return verts, uvs, tris

    # self-touching ring the ear clipper can't split: centroid fan fallback
    cx = sum(p[0] for p in ring_xy_cm) / n
    cy = sum(p[1] for p in ring_xy_cm) / n
    cb = len(verts)
    verts.append((cx, cy, roof_z))
    uvs.append((cx / 100.0 / FACADE_TILE_M, cy / 100.0 / FACADE_TILE_M))
    ring_base = len(verts)
    for x, y in ring_xy_cm:
        verts.append((x, y, roof_z))
        uvs.append((x / 100.0 / FACADE_TILE_M, y / 100.0 / FACADE_TILE_M))
    for i in range(n):
        a = ring_base + i
        bpt = ring_base + (i + 1) % n
        if signed_area >= 0:
            tris.append((cb, a, bpt))
        else:
            tris.append((cb, bpt, a))

    return verts, uvs, tris


def main():
    st_east, st_north, st_elev, st_half_w = load_stations()
    st_x_cm, st_y_cm = st_east * 100.0, -st_north * 100.0
    st_half_w_cm = st_half_w * 100.0

    # Tile-bucket every station once (same TERRAIN_TILE_SIZE_CM grid the
    # settlement classification already uses) so the per-building nearest-
    # station lookup below only ever scans a 3x3 tile neighborhood instead
    # of every station in the whole network - at whole-island scale (a real
    # city has orders of magnitude more stations AND buildings than the
    # Mount-Vernon-only proof this replaces) a brute-force O(buildings x
    # stations) scan is actually too slow, not just inelegant.
    station_tiles = defaultdict(list)
    for i in range(len(st_x_cm)):
        key = (int(st_x_cm[i] // TERRAIN_TILE_SIZE_CM), int(st_y_cm[i] // TERRAIN_TILE_SIZE_CM))
        station_tiles[key].append(i)

    def nearby_station_indices(x_cm, y_cm):
        """Indices of stations in the 3x3 tile neighborhood around (x,y),
        widening the search ring if that neighborhood happens to be empty
        (sparse/edge-of-network area) - returns None if nothing found even
        after a generous widen, which the caller treats as "no road nearby"."""
        cx, cy = int(x_cm // TERRAIN_TILE_SIZE_CM), int(y_cm // TERRAIN_TILE_SIZE_CM)
        for ring in (1, 3, 8):
            idx = [i for dx in range(-ring, ring + 1) for dy in range(-ring, ring + 1)
                   for i in station_tiles.get((cx + dx, cy + dy), [])]
            if idx:
                return np.array(idx)
        return None

    settlement = json.loads(SETTLEMENT_PATH.read_text())["tiles"]
    buildings_raw = json.loads(BUILDINGS_PATH.read_text())

    rings = []
    for b in buildings_raw:
        ring_ll = b["nodes"]
        east_m, north_m = to_local(np.array([lon for _, lon in ring_ll]),
                                   np.array([lat for lat, _ in ring_ll]))
        rings.append([(e * 100.0, -n * 100.0) for e, n in zip(east_m.tolist(), north_m.tolist())])
    centroids_cm = np.array([[sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)] for r in rings])
    known = [known_levels(b) for b in buildings_raw]
    known_idx = np.array([i for i, lv in enumerate(known) if lv], dtype=np.int64)
    known_tree = cKDTree(centroids_cm[known_idx]) if len(known_idx) else None
    level_sources = defaultdict(int)

    out = []
    n_excluded_road = n_not_populated = 0
    facade_counts = defaultdict(int)
    for bi, b in enumerate(buildings_raw):
        ring_xy_cm = rings[bi]
        cx_cm, cy_cm = centroids_cm[bi]
        tx = int(cx_cm // TERRAIN_TILE_SIZE_CM)
        ty = int(cy_cm // TERRAIN_TILE_SIZE_CM)
        tile_key = f"{tx},{ty}"
        archetype = settlement.get(tile_key)
        if archetype is None:
            n_not_populated += 1
            continue

        # road-corridor exclusion gate: nearest station to ANY footprint
        # vertex (not just centroid - a long/thin building could have its
        # centroid clear of the road while a corner still overlaps it)
        cand = nearby_station_indices(cx_cm, cy_cm)
        if cand is None:
            # no road station anywhere near this building even after
            # widening the search - can't place it without a ground height
            n_not_populated += 1
            continue
        clearance_cm = min(
            float(np.min(np.hypot(st_x_cm[cand] - x, st_y_cm[cand] - y) - st_half_w_cm[cand]))
            for x, y in ring_xy_cm)
        min_clear_m = CITY_ROAD_CLEARANCE_M if archetype == "City" else ROAD_CLEARANCE_M
        if clearance_cm / 100.0 < min_clear_m:
            n_excluded_road += 1
            continue

        # Placeholder base Z only: AODBuildingManager::ComputeBuildingZOffset
        # re-plants every building on traced terrain at runtime and uses this
        # value just as the reference its offset is measured from. Sampling
        # terrain here would change nothing in-game.
        nearest_i = cand[int(np.argmin(np.hypot(st_x_cm[cand] - cx_cm, st_y_cm[cand] - cy_cm)))]
        ground_z_cm = road_ue_z_m(float(st_elev[nearest_i]), 0.0, ORIGIN_HEIGHT_M) * 100.0

        rng = random.Random(b["way_id"])
        levels = known[bi]
        level_sources[level_source(b) or "estimated"] += 1
        if levels is None:
            neighbours = []
            if known_tree is not None:
                d, j = known_tree.query((cx_cm, cy_cm), k=min(NEIGHBOUR_K, len(known_idx)),
                                        distance_upper_bound=NEIGHBOUR_RADIUS_M * 100.0)
                neighbours = [known[known_idx[i]] for i in np.atleast_1d(j)[np.isfinite(np.atleast_1d(d))]]
            area_m2 = abs(sum(ring_xy_cm[i][0] * ring_xy_cm[i - 1][1] - ring_xy_cm[i - 1][0] * ring_xy_cm[i][1]
                              for i in range(len(ring_xy_cm)))) / 2e4
            levels = estimate_levels(b.get("building_tag"), area_m2, neighbours, rng)
        levels = min(max(1, levels), MAX_BUILDING_LEVELS)   # sanity backstop, see MAX_BUILDING_LEVELS
        height_cm = levels * LEVEL_HEIGHT_M * 100.0
        # drawn AFTER the levels roll so existing heights don't shift
        wall_set, flat_roof = pick_facade(b.get("building_tag"), levels, rng)
        facade_counts[(wall_set, flat_roof)] += 1

        verts, uvs, tris = extrude_footprint(ring_xy_cm, ground_z_cm, height_cm, b["way_id"])
        out.append({
            "name": f"bldg_{b['way_id']}",
            "tile": tile_key,
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "triangles": [i for t in tris for i in t],
            "wall_set": wall_set,
            "flat_roof": flat_roof,
        })

    OUT_PATH.write_text(json.dumps({"buildings": out}))
    print(f"{len(buildings_raw)} footprint(s) -> {len(out)} building(s) built "
          f"({n_not_populated} outside populated tiles, {n_excluded_road} "
          f"excluded - too close to the road) -> {OUT_PATH}")
    print("storeys from: " + ", ".join(f"{k}={v}" for k, v in sorted(level_sources.items())))
    print("facade (wall_set, flat_roof) counts: "
          + ", ".join(f"{k}={facade_counts[k]}" for k in sorted(facade_counts)))


if __name__ == "__main__":
    main()
