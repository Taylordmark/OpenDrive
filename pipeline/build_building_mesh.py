"""Extrude OSM building footprints into simple facade meshes (Arc 7.0 step
3, 2026-07-24) - "simple extruded facades" per Taylor's scope decision:
real footprint polygon -> archetype-appropriate height band -> wall quads
+ a fan-triangulated roof cap, tiled facade UV. Deterministic per building
(seeded off the OSM way ID), zero per-instance storage beyond the
footprint polygon itself (already compact vector data) - same
zero-per-instance-storage logic as the tree HISM scatter, just
footprint-driven instead of tile-seed-scatter-driven.

Also the offline "no building in the road" verification gate (in-line,
not a separate script - see next steps.md Arc 7.0 step 3): any footprint
with a vertex or centroid within ROAD_EXCLUSION_M of the nearest road
station is skipped and counted, not silently placed on top of the road.

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
        {"buildings": [{"name","tile","vertices","uvs","triangles"}, ...]}
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

from road_mesh_common import ROAD_HALF_WIDTH, SKIRT_EXTRA, road_ue_z_m
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

M_PER_DEG_LAT = 111132.0
ROAD_EXCLUSION_M = ROAD_HALF_WIDTH + SKIRT_EXTRA + 2.0   # +2 m slop margin

LEVEL_HEIGHT_M = 3.2
# stories, when the OSM way has no building:levels tag - "City" (2026-07-27,
# real downtown density per classify_settlements.py) deliberately reads as
# a genuine high-rise range, not a taller version of a town building; real
# per-building OSM levels/height tags (parsed in fetch_osm_buildings.py)
# still win over this fallback whenever a way actually has one.
DEFAULT_LEVEL_RANGE = {"AmericanTown": (1, 3), "City": (8, 35)}
FACADE_TILE_M = 4.0   # UV repeat period on walls/roof, matches this
                       # project's DETAIL_TILE_CM-style tiling convention

ROOT = Path(__file__).resolve().parent.parent
BUILDINGS_PATH = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm_buildings.json"
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
SETTLEMENT_PATH = STATIONS_PATH.parent / "settlement_tiles.json"
OUT_PATH = STATIONS_PATH.parent / "building_mesh.json"


def load_stations():
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    return lat0, lon0, east, north, elev


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

    # ---- roof: fan triangulation from centroid (fine for the simple,
    # mostly-convex footprints this first pass targets - see module
    # docstring, "simple extruded facades" is the deliberate scope) ----
    cx = sum(p[0] for p in ring_xy_cm) / n
    cy = sum(p[1] for p in ring_xy_cm) / n
    # signed area sets a consistent winding so the roof faces up regardless
    # of the source ring's own winding direction
    signed_area = sum(ring_xy_cm[i][0] * ring_xy_cm[(i + 1) % n][1]
                       - ring_xy_cm[(i + 1) % n][0] * ring_xy_cm[i][1] for i in range(n))
    roof_z = base_z_cm + height_cm
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
    lat0, lon0, st_east, st_north, st_elev = load_stations()
    st_x_cm, st_y_cm = st_east * 100.0, -st_north * 100.0
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

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

    out = []
    n_excluded_road = n_not_populated = 0
    for b in buildings_raw:
        ring_ll = b["nodes"]
        east_m = [(lon - lon0) * m_per_deg_lon for _, lon in ring_ll]
        north_m = [(lat - lat0) * M_PER_DEG_LAT for lat, _ in ring_ll]
        ring_xy_cm = [(e * 100.0, -n * 100.0) for e, n in zip(east_m, north_m)]

        cx_cm = sum(p[0] for p in ring_xy_cm) / len(ring_xy_cm)
        cy_cm = sum(p[1] for p in ring_xy_cm) / len(ring_xy_cm)
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
        min_dist_cm = min(
            float(np.min(np.hypot(st_x_cm[cand] - x, st_y_cm[cand] - y))) for x, y in ring_xy_cm)
        if min_dist_cm / 100.0 < ROAD_EXCLUSION_M:
            n_excluded_road += 1
            continue

        nearest_i = cand[int(np.argmin(np.hypot(st_x_cm[cand] - cx_cm, st_y_cm[cand] - cy_cm)))]
        ground_z_cm = road_ue_z_m(float(st_elev[nearest_i]), 0.0, ORIGIN_HEIGHT_M) * 100.0

        rng = random.Random(b["way_id"])
        levels = b["levels"]
        if levels is None:
            lo, hi = DEFAULT_LEVEL_RANGE.get(archetype, (1, 2))
            levels = rng.randint(lo, hi)
        height_cm = max(1, levels) * LEVEL_HEIGHT_M * 100.0

        verts, uvs, tris = extrude_footprint(ring_xy_cm, ground_z_cm, height_cm, b["way_id"])
        out.append({
            "name": f"bldg_{b['way_id']}",
            "tile": tile_key,
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "triangles": [i for t in tris for i in t],
        })

    OUT_PATH.write_text(json.dumps({"buildings": out}))
    print(f"{len(buildings_raw)} footprint(s) -> {len(out)} building(s) built "
          f"({n_not_populated} outside populated tiles, {n_excluded_road} "
          f"excluded - too close to the road) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
