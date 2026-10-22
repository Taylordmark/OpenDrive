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
                        "wall_set","flat_roof","roof_set","roof_rgb",
                        "facade","n_solid","levels","roof"}, ...]}
        (roofs baked here since realism B3, see roof_shape below)
        (same {"vertices","uvs","triangles"} chunk schema
        road_mesh_common.build_road_chunks already produces, so
        export_building_mesh_bin.py can bucket it the same way
        export_road_mesh_bin.py buckets road chunks)
"""

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from bake_job import tile_in_job
from building_roofs import clean_ring, make_roof, triangulate
from classify_settlements import MIN_BUILDINGS_PER_TILE, SETTLEMENT_CELL_CM
from road_mesh_common import ROAD_HALF_WIDTH, SKIRT_EXTRA, bake_map, half_width_for_stations, road_ue_z_m
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


# Facade pick per building: wall_set indexes WALL_LAYERS in
# unreal/apply_building_material*.py (0 red brick, 1 tan brick, 2 wood
# siding, 3 plaster/stucco, 4 concrete, 5 stacked stone, 6 log); flat_roof
# 1 = concrete flat roof, 0 = shingle. Anything 8+ stories is concrete + flat.
# Realism B4 (2026-10-15): the wall palette is per region, not one national
# brick-heavy mix - stucco/CMU for Oahu and SoCal, painted wood flats and
# stucco for SF, wood/log/stone for the Rockies. Repeats in a list weight it.
BUILDING_CATEGORY = {
    **{t: "civic" for t in ("office", "government", "public", "school")},
    **{t: "apartments" for t in ("apartments", "hotel", "dormitory")},
    **{t: "commercial" for t in ("commercial", "retail", "shop")},
    **{t: "house" for t in (
        "house", "detached", "semidetached_house", "residential", "terrace",
        "bungalow", "cabin", "farm", "static_caravan", "houseboat",
        "construction")},
    **{t: "outbuilding" for t in ("garage", "shed", "carport", "greenhouse", "roof")},
    "church": "church",
}
CATEGORY_FLAT = {"civic": 1, "apartments": 1, "commercial": 1, "mid": 1}   # the rest pitched
REGION_PALETTE = {
    "generic": dict(house=[2, 3, 0, 1], outbuilding=[2, 4], commercial=[0, 1, 3], apartments=[0, 1, 3, 4],
                    civic=[4, 3, 0, 1], church=[0, 1, 3], mid=[0, 3, 4], low=[0, 1, 2, 3, 4]),
    "tropical": dict(house=[3, 3, 3, 3, 2, 2, 4], outbuilding=[2, 3, 4], commercial=[3, 3, 4, 4, 1],
                     apartments=[3, 4, 4], civic=[4, 3, 3], church=[3, 3, 5], mid=[3, 4, 4], low=[3, 3, 3, 2, 4]),
    "socal": dict(house=[3, 3, 3, 3, 3, 3, 2, 1], outbuilding=[3, 2, 4], commercial=[3, 3, 4, 1],
                  apartments=[3, 3, 4], civic=[4, 3, 1], church=[3, 3, 1], mid=[3, 4], low=[3, 3, 3, 3, 2, 1]),
    "sf": dict(house=[2, 2, 2, 3, 3], outbuilding=[2, 3], commercial=[0, 1, 3, 2], apartments=[3, 3, 2, 0, 1],
               civic=[4, 1, 3, 5], church=[5, 1, 3], mid=[3, 0, 1, 4], low=[2, 2, 3, 3, 0]),
    "rockies": dict(house=[2, 2, 2, 6, 6, 5, 3], outbuilding=[2, 6, 2], commercial=[2, 0, 5, 6, 1],
                    apartments=[2, 3, 5, 4, 2], civic=[5, 4, 0, 2], church=[5, 0, 2], mid=[2, 3, 5, 4],
                    low=[2, 2, 6, 6, 5, 3]),
}
AREA_REGION = {"oahu": "tropical", "green_valley": "socal", "sf_russian_hill": "sf", "summit_county": "rockies",
               "breckenridge": "rockies"}
HIGH_RISE_LEVELS = 8


def pick_facade(building_tag, levels, rng, region="generic"):
    """(wall_set, flat_roof) for one building - see REGION_PALETTE."""
    if levels >= HIGH_RISE_LEVELS:
        return 4, 1
    cat = BUILDING_CATEGORY.get(building_tag) or ("mid" if levels >= 4 else "low")
    return rng.choice(REGION_PALETTE[region][cat]), CATEGORY_FLAT.get(cat, 0)


REGION = AREA_REGION.get(AREA_NAME, "generic")

# Facade archetypes (realism B2, 2026-10-15): the module set the Realistic
# wall material draws (unreal/apply_building_material_real.py FACADE_FLOORS,
# same order) and the (ground, upper) floor heights the building is extruded
# with - a shop's 4.5 m ground floor, a house's 2.8 m storeys, an office
# tower's 3.8 m. Towers: apartments / residential get balconies, offices and
# shops a glass curtain wall, untagged ones by the region's share; shops get
# a storefront, as do a share of the untagged low/mid buildings at a City
# tile's sidewalk (within STOREFRONT_SETBACK_M of the road);
# houses, outbuildings and small pitched buildings get garage / entry doors.
FACADE_PUNCHED, FACADE_HOUSE, FACADE_STOREFRONT, FACADE_CURTAIN, FACADE_BALCONY = range(5)
FACADE_FLOORS = [(3.6, 3.2), (2.9, 2.8), (4.5, 3.3), (5.0, 3.8), (4.0, 3.0)]
TOWER_BALCONY_SHARE = {"tropical": 0.6, "socal": 0.4, "sf": 0.4, "rockies": 0.5, "generic": 0.4}
CITY_STOREFRONT_SHARE = 0.35
STOREFRONT_SETBACK_M = 6.0   # SF street-front buildings sit 4-6 m from the carriageway edge
STOREFRONT_MAX_LEVELS = 6


def pick_archetype(building_tag, levels, shape, city_street, rng, region="generic"):
    """FACADE_* for one building (see the block above)."""
    cat = BUILDING_CATEGORY.get(building_tag)
    if levels >= HIGH_RISE_LEVELS:
        if cat in ("apartments", "house"):
            return FACADE_BALCONY
        if cat in ("civic", "commercial"):
            return FACADE_CURTAIN
        return FACADE_BALCONY if rng.random() < TOWER_BALCONY_SHARE[region] else FACADE_CURTAIN
    if cat == "commercial":
        return FACADE_STOREFRONT
    if cat in ("house", "outbuilding") or (cat is None and shape != "flat" and levels <= 2):
        return FACADE_HOUSE
    if cat == "apartments" and levels >= 5:
        return FACADE_BALCONY
    if (cat is None and city_street and levels <= STOREFRONT_MAX_LEVELS
            and rng.random() < CITY_STOREFRONT_SHARE):
        return FACADE_STOREFRONT
    return FACADE_PUNCHED


def facade_height_m(archetype, levels):
    ground, upper = FACADE_FLOORS[archetype]
    return ground + (levels - 1) * upper

# Roofs (realism B3, 2026-10-15): baked here, not pitched at runtime. Shape:
# OSM roof:shape when tagged, else pitched for the categories pick_facade
# leaves non-flat (houses, outbuildings, churches, small untagged) and for
# any small low footprint, gable or hip by the region's HIP_SHARE; SF's row
# houses are half flat. Footprints building_roofs.make_roof can't pitch stay
# flat. roof_set indexes the roof material's sets (unreal/
# apply_building_material_real.py ROOF_LAYERS); roof colour is linear RGB,
# OSM roof:colour when tagged.
ROOF_SHAPE_TAG = {
    "flat": "flat",
    **{t: "gable" for t in ("gabled", "skillion", "lean_to", "saltbox", "quadruple_saltbox", "gambrel")},
    **{t: "hip" for t in ("hipped", "pyramidal", "hipped-and-gabled", "half-hipped", "equal_hipped",
                          "side_hipped", "mansard")},
}
SMALL_PITCHED_M2 = 150.0
HIP_SHARE = {"tropical": 0.7, "socal": 0.6, "sf": 0.3, "rockies": 0.25, "generic": 0.45}
SF_FLAT_HOUSES = 0.5
ROOF_TILE, ROOF_ASPHALT, ROOF_METAL, ROOF_FLAT = range(4)
REGION_ROOF_SETS = {   # (pitched roof set, weight)
    "tropical": [(ROOF_METAL, 0.35), (ROOF_ASPHALT, 0.45), (ROOF_TILE, 0.2)],
    "socal": [(ROOF_TILE, 0.6), (ROOF_ASPHALT, 0.4)],
    "sf": [(ROOF_ASPHALT, 0.8), (ROOF_TILE, 0.2)],
    "rockies": [(ROOF_ASPHALT, 0.55), (ROOF_METAL, 0.45)],
    "generic": [(ROOF_ASPHALT, 0.6), (ROOF_TILE, 0.15), (ROOF_METAL, 0.25)],
}
ROOF_MATERIAL_TAG = {
    **{t: ROOF_TILE for t in ("roof_tiles", "tile", "tiles", "clay")},
    **{t: ROOF_ASPHALT for t in ("asphalt", "asphalt_shingle", "shingle", "shake", "slate", "wood")},
    **{t: ROOF_METAL for t in ("metal", "metal_sheet", "steel", "tin", "copper", "zinc")},
}
ROOF_COLOURS = {   # linear albedo
    ROOF_TILE: [(0.30, 0.09, 0.04), (0.38, 0.14, 0.06), (0.25, 0.10, 0.06)],
    ROOF_ASPHALT: [(0.06, 0.06, 0.06), (0.09, 0.085, 0.08), (0.12, 0.11, 0.10), (0.10, 0.07, 0.05),
                   (0.05, 0.055, 0.065)],
    ROOF_METAL: [(0.25, 0.05, 0.04), (0.07, 0.15, 0.08), (0.33, 0.33, 0.33), (0.18, 0.22, 0.26)],
    ROOF_FLAT: [(0.34, 0.34, 0.33), (0.45, 0.45, 0.43), (0.27, 0.27, 0.26)],
}
ROOF_LUMA = (0.03, 0.6)   # a tagged "white" or "black" roof still reads as a material


def roof_shape(b, flat, area_m2, levels, rng, region="generic"):
    """"flat", "gable" or "hip" for one building (see the block above)."""
    shape = ROOF_SHAPE_TAG.get(b.get("roof:shape"))
    if shape:
        return shape
    pitched = levels < HIGH_RISE_LEVELS and (not flat or (area_m2 < SMALL_PITCHED_M2 and levels < 4))
    if pitched and region == "sf" and rng.random() < SF_FLAT_HOUSES:
        pitched = False
    return ("hip" if rng.random() < HIP_SHARE[region] else "gable") if pitched else "flat"


def parse_colour(value):
    """OSM colour (name or #hex) -> linear RGB with luma in ROOF_LUMA, or None."""
    from matplotlib.colors import to_rgb
    try:
        srgb = to_rgb(value.strip().lower())
    except (AttributeError, ValueError):
        return None
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in srgb]
    luma = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    k = min(max(luma, ROOF_LUMA[0]), ROOF_LUMA[1]) / max(luma, 1e-4)
    return tuple(min(c * k, 1.0) if luma > 1e-4 else ROOF_LUMA[0] for c in lin)


def roof_look(b, shape, rng, region="generic"):
    """(roof_set, linear rgb) - OSM roof:material / roof:colour when tagged."""
    roof_set = ROOF_FLAT
    if shape != "flat":
        roof_set = ROOF_MATERIAL_TAG.get(b.get("roof:material"))
        if roof_set is None:
            sets, weights = zip(*REGION_ROOF_SETS[region])
            roof_set = rng.choices(sets, weights)[0]
    return roof_set, parse_colour(b.get("roof:colour")) or rng.choice(ROOF_COLOURS[roof_set])
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


# Per-process state for build_chunk (main() in-process, or each bake_map
# worker): stations, their tile buckets, settlement + known-storey lookups.
_SHARED = None
CHUNK_BUILDINGS = 2000
# a worker holds the station arrays/buckets and one chunk's footprints + meshes
WORKER_GB = 1.0


def _init_shared(shared):
    global _SHARED
    _SHARED = shared


def nearby_station_indices(x_cm, y_cm):
    """Indices of stations in the 3x3 tile neighborhood around (x,y),
    widening the search ring if that neighborhood happens to be empty
    (sparse/edge-of-network area) - returns None if nothing found even
    after a generous widen, which the caller treats as "no road nearby"."""
    station_tiles = _SHARED["station_tiles"]
    cx, cy = int(x_cm // TERRAIN_TILE_SIZE_CM), int(y_cm // TERRAIN_TILE_SIZE_CM)
    for ring in (1, 3, 8):
        idx = [i for dx in range(-ring, ring + 1) for dy in range(-ring, ring + 1)
               for i in station_tiles.get((cx + dx, cy + dy), [])]
        if idx:
            return np.array(idx)
    return None


def build_chunk(chunk):
    """chunk: [(osm building, ring_xy_cm, (cx_cm, cy_cm), known levels)] ->
    (building dicts in order, Counter of the stats main() prints). Each
    building depends only on itself and _SHARED, so chunks are independent."""
    S = _SHARED
    st_x_cm, st_y_cm, st_half_w_cm, st_elev = S["st_x_cm"], S["st_y_cm"], S["st_half_w_cm"], S["st_elev"]
    known_tree, known_vals = S["known_tree"], S["known_vals"]
    out, stats = [], Counter()
    for b, ring_xy_cm, (cx_cm, cy_cm), levels in chunk:
        tx = int(cx_cm // SETTLEMENT_CELL_CM)
        ty = int(cy_cm // SETTLEMENT_CELL_CM)
        tile_key = f"{tx},{ty}"
        archetype_tile = S["settlement"].get(tile_key)
        if archetype_tile is None and S["tile_counts"][tile_key] >= MIN_BUILDINGS_PER_TILE:
            archetype_tile = "AmericanTown"   # B6: settlement tiles count OSM only; MS fill populates too
        if archetype_tile is None:
            stats["not_populated"] += 1
            continue

        # road-corridor exclusion gate: nearest station to ANY footprint
        # vertex (not just centroid - a long/thin building could have its
        # centroid clear of the road while a corner still overlaps it)
        cand = nearby_station_indices(cx_cm, cy_cm)
        if cand is None:
            # no road station anywhere near this building even after
            # widening the search - can't place it without a ground height
            stats["not_populated"] += 1
            continue
        clearance_cm = min(
            float(np.min(np.hypot(st_x_cm[cand] - x, st_y_cm[cand] - y) - st_half_w_cm[cand]))
            for x, y in ring_xy_cm)
        min_clear_m = CITY_ROAD_CLEARANCE_M if archetype_tile == "City" else ROAD_CLEARANCE_M
        if clearance_cm / 100.0 < min_clear_m:
            stats["excluded_road"] += 1
            continue

        # Placeholder base Z only: AODBuildingManager::ComputeBuildingZOffset
        # re-plants every building on traced terrain at runtime and uses this
        # value just as the reference its offset is measured from. Sampling
        # terrain here would change nothing in-game.
        nearest_i = cand[int(np.argmin(np.hypot(st_x_cm[cand] - cx_cm, st_y_cm[cand] - cy_cm)))]
        ground_z_cm = road_ue_z_m(float(st_elev[nearest_i]), 0.0, ORIGIN_HEIGHT_M) * 100.0

        rng = random.Random(b["way_id"])
        area_m2 = abs(sum(ring_xy_cm[i][0] * ring_xy_cm[i - 1][1] - ring_xy_cm[i - 1][0] * ring_xy_cm[i][1]
                          for i in range(len(ring_xy_cm)))) / 2e4
        stats["levels", level_source(b) or "estimated"] += 1
        if levels is None:
            neighbours = []
            if known_tree is not None:
                d, j = known_tree.query((cx_cm, cy_cm), k=min(NEIGHBOUR_K, len(known_vals)),
                                        distance_upper_bound=NEIGHBOUR_RADIUS_M * 100.0)
                neighbours = [known_vals[i] for i in np.atleast_1d(j)[np.isfinite(np.atleast_1d(d))]]
            levels = estimate_levels(b.get("building_tag"), area_m2, neighbours, rng)
        levels = min(max(1, levels), MAX_BUILDING_LEVELS)   # sanity backstop, see MAX_BUILDING_LEVELS
        # drawn AFTER the levels roll so existing heights don't shift
        wall_set, flat_roof = pick_facade(b.get("building_tag"), levels, rng, REGION)
        stats["facade", (wall_set, flat_roof)] += 1
        shape = roof_shape(b, flat_roof, area_m2, levels, rng, REGION)
        # own stream, so the facade / roof rolls above stay as they were
        city_street = archetype_tile == "City" and clearance_cm / 100.0 < STOREFRONT_SETBACK_M
        archetype = pick_archetype(b.get("building_tag"), levels, shape, city_street,
                                   random.Random(b["way_id"] ^ 0x5F3759DF), REGION)
        stats["archetype", archetype] += 1
        height_cm = facade_height_m(archetype, levels) * 100.0

        verts, uvs, tris = extrude_footprint(ring_xy_cm, ground_z_cm, height_cm, b["way_id"])
        n_solid = len(verts)
        if shape != "flat":
            roofed = make_roof(verts, uvs, tris, shape == "hip")
            if roofed:
                verts, uvs, tris, n_solid = roofed
            else:
                shape = "flat"
        roof_set, roof_rgb = roof_look(b, shape, rng, REGION)
        stats["roof", (shape, roof_set)] += 1
        out.append({
            "name": f"bldg_{b['way_id']}",
            "tile": tile_key,
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "triangles": [i for t in tris for i in t],
            "wall_set": wall_set,
            "flat_roof": int(shape == "flat"),
            "roof_set": roof_set,
            "roof_rgb": [round(c * 255) for c in roof_rgb],
            "facade": archetype,
            "n_solid": n_solid,
            "levels": levels,
            "roof": shape,
        })
        stats["ms"] += b.get("source") == "ms"
    return out, stats


def main():
    st_east, st_north, st_elev, st_half_w = load_stations()
    st_x_cm, st_y_cm = st_east * 100.0, -st_north * 100.0

    # Tile-bucket every station once (same TERRAIN_TILE_SIZE_CM grid the
    # settlement classification already uses) so the per-building nearest-
    # station lookup in build_chunk only ever scans a 3x3 tile neighborhood
    # instead of every station in the whole network - at whole-island scale
    # (a real city has orders of magnitude more stations AND buildings than
    # the Mount-Vernon-only proof this replaces) a brute-force O(buildings x
    # stations) scan is actually too slow, not just inelegant.
    station_tiles = defaultdict(list)
    for i in range(len(st_x_cm)):
        key = (int(st_x_cm[i] // TERRAIN_TILE_SIZE_CM), int(st_y_cm[i] // TERRAIN_TILE_SIZE_CM))
        station_tiles[key].append(i)

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
    tile_counts = defaultdict(int)
    for cx, cy in centroids_cm:
        tile_counts[f"{int(cx // SETTLEMENT_CELL_CM)},{int(cy // SETTLEMENT_CELL_CM)}"] += 1

    # buildings are independent: CHUNK_BUILDINGS at a time over bake_map
    # (2026-10-15), merged back in input order
    shared = {"st_x_cm": st_x_cm, "st_y_cm": st_y_cm, "st_half_w_cm": st_half_w * 100.0,
              "st_elev": st_elev, "station_tiles": dict(station_tiles), "settlement": settlement,
              "tile_counts": tile_counts, "known_tree": known_tree,
              "known_vals": [known[i] for i in known_idx]}
    items = list(zip(buildings_raw, rings, centroids_cm.tolist(), known))
    # (2026-10-21) a pass-2 job builds only the buildings its tiles own (by
    # centroid tile, the tile export_building_mesh_bin.py files them under)
    items = [it for it in items if tile_in_job(int(it[2][0] // TERRAIN_TILE_SIZE_CM),
                                               int(it[2][1] // TERRAIN_TILE_SIZE_CM), TERRAIN_TILE_SIZE_CM)]
    chunks = [items[i:i + CHUNK_BUILDINGS] for i in range(0, len(items), CHUNK_BUILDINGS)]
    out, stats = [], Counter()
    for part, part_stats in bake_map(build_chunk, chunks, WORKER_GB, _init_shared, (shared,)):
        out += part
        stats.update(part_stats)
    level_sources = {k[1]: v for k, v in stats.items() if k[0] == "levels"}
    facade_counts = {k[1]: v for k, v in stats.items() if k[0] == "facade"}
    roof_counts = {k[1]: v for k, v in stats.items() if k[0] == "roof"}
    archetype_counts = {k[1]: v for k, v in stats.items() if k[0] == "archetype"}

    OUT_PATH.write_text(json.dumps({"buildings": out}))
    print(f"{len(buildings_raw)} footprint(s) -> {len(out)} building(s) built "
          f"({stats['not_populated']} outside populated tiles, {stats['excluded_road']} "
          f"excluded - too close to the road) -> {OUT_PATH}")
    print(f"  {stats['ms']} of them Microsoft gap-fill footprints")
    print("storeys from: " + ", ".join(f"{k}={v}" for k, v in sorted(level_sources.items())))
    n_pitched = sum(v for (shape, _), v in roof_counts.items() if shape != "flat")
    print(f"roofs: {n_pitched / max(len(out), 1):.1%} pitched; (shape, roof_set) counts: "
          + ", ".join(f"{k}={roof_counts[k]}" for k in sorted(roof_counts)))
    print("facade archetypes (punched, house, storefront, curtain, balcony): "
          + ", ".join(str(archetype_counts.get(i, 0)) for i in range(len(FACADE_FLOORS))))
    print("facade (wall_set, flat_roof) counts: "
          + ", ".join(f"{k}={facade_counts[k]}" for k in sorted(facade_counts)))


if __name__ == "__main__":
    main()
