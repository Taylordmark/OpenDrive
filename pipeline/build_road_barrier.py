"""Guaranteed road-corridor barrier ("wall of scenery") - a CONTINUOUS
collision wall just outside AODForestManager's existing PavementExclusionCm
(11.5 m) shoulder clearance, dressed with deterministic tree/bush
placement so it reads as scenery, not a fence.

Real bug found and fixed 2026-07-24 (v1 of this script, discrete point
capsules every BARRIER_SPACING_M): individual capsule obstacles do NOT
form a solid wall - two capsules 2.5 m apart (radius 26 cm each) leave a
~2 m gap between them, close to or wider than the car itself, so driving
off-road at a slightly different point/angle than the one test that
happened to hit a capsule could slip straight through the gap and reach
the far-tier (no-collision) terrain beyond, falling through exactly as
before. A row of discrete objects was never going to guarantee no gap
regardless of spacing tightness - the fix is a genuinely CONTINUOUS
collision mesh (a thin vertical ribbon following the road, same "thin
sheet with working collision" pattern the road's own skirt already uses
successfully), not more points.

The deterministic tree/bush POINTS from v1 are kept - they're still useful
for the visual "wall of scenery" look via HISM instances - they just no
longer carry any collision responsibility themselves; the new continuous
wall mesh does that instead.

Usage:  python build_road_barrier.py   (needs road_stations.json,
                                          settlement_tiles.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
        data/road_tiles/<AREA_NAME>_test/settlement_tiles.json (if present -
        towns need open access via their own streets, not a wall)
Outputs data/road_tiles/<AREA_NAME>_test/road_barrier.json
        {"points": [{"x_cm","y_cm","z_cm","seed"}, ...],
         "wall_runs": [{"tile", "station_xyz_cm"}, ...]}

AUDIT.md 2026-07-27 finding 5.1 / recommendation 3: this used to also emit a
fully triangulated wall_chunks mesh (verts+uvs+tris) here, baking the single
largest disk consumer in the project (213 MB on Oahu) even though every byte
of it was mechanically derivable from the station polyline already computed
below (a 13 m offset + 4 m vertical extrusion). Fixed 2026-07-28: this script
now only writes the offset STATION POINTS ("wall_runs") - AODBarrierManager
builds the actual ribbon mesh (2 verts + 2 tris per station step) at runtime
from those points, the same "thin sheet, real collision" extrusion this
script used to do in Python. Cuts baked barrier size by ~5x (no per-vertex
UV/triangle-index arrays shipped, no duplicated top/bottom vertex data on
disk) for identical resulting geometry.
"""

import json
import zlib
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from classify_settlements import SETTLEMENT_CELL_CM
from road_mesh_common import road_ue_z_m
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM
# Lake exclusion (2026-09-03, reviewer defect: a barrier/scenery wall run - and
# its dressed tree/bush points - could land squarely in open water anywhere a
# road's BARRIER_OFFSET_M lateral offset happens to cross a lake polygon (seen
# at Dillon Reservoir along Swan Mountain Rd). Reuses
# build_terrain_mesh_tiles.py's own lake-bed reader/point-in-polygon test
# (the water bake's OWN baked output, same "don't re-derive" reasoning that
# module's lake-bed-depression comment already documents) rather than
# re-fetching OSM/DEM here - build_water_tiles.py must have already run for
# this to have any effect; load_lake_beds() returns [] (no exclusion, same as
# today) if it hasn't.
from build_terrain_mesh_tiles import lakes_near, load_lake_beds, _point_in_polygon, _dist_to_ring, WATER_SHORE_BLEND_M

# Lake shore-blend margin (2026-09-03, second pass, same reviewer defect):
# matches AODForestManager::LakeShoreExclusionMarginCm and the reasoning in
# its comment - a strict point-in-polygon test alone left wall runs/scenery
# points standing just outside the lake ring but inside the terrain bake's
# own WATER_SHORE_BLEND_M exterior blend band, where the ground is only
# centimeters above the water plane and reads as "in the lake" from a low
# shore-level camera. A few meters over WATER_SHORE_BLEND_M for safety.
LAKE_SHORE_EXCLUSION_MARGIN_M = WATER_SHORE_BLEND_M + 3.0

BARRIER_SPACING_M = 2.5      # tree/bush visual placement spacing
BARRIER_OFFSET_M = 13.0      # just outside PavementExclusionCm (11.5 m)
# (2026-10-22, Taylor: trees in the Copp Circle junction at Calderwood) each
# road's 13 m offset row ran straight across every road it meets and over
# the junction pads. A point this near ANY road station or pad point is
# open, the forest's own PavementExclusionCm rule.
BARRIER_ROAD_CLEAR_M = 11.5
WALL_CHUNK_STATIONS = 80     # ~200 m per run at BARRIER_SPACING_M spacing (tile-bucketing granularity)
# Wall HEIGHT is no longer decided here - AODBarrierManager::WallHeightM (C++)
# extrudes each run's station points into the vertical ribbon at runtime now.

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
SETTLEMENT_PATH = STATIONS_PATH.parent / "settlement_tiles.json"
OUT_PATH = STATIONS_PATH.parent / "road_barrier.json"
PADPTS_PATH = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks" / f"padpts_{AREA_NAME}_test.bin"


def load_settlement_tiles():
    if not SETTLEMENT_PATH.exists():
        return set()
    tiles = json.loads(SETTLEMENT_PATH.read_text())["tiles"]
    return {tuple(int(p) for p in k.split(",")) for k in tiles}


def road_tree(segments, padpts_path=PADPTS_PATH):
    """KD tree (UE cm) over every road station and, when baked
    (export_pad_points_bin.py runs first), every junction-pad point."""
    xy = [np.c_[np.array(seg["east_m"]) * 100.0, -np.array(seg["north_m"]) * 100.0] for seg in segments]
    if padpts_path.is_file():
        b = padpts_path.read_bytes()
        xy.append(np.frombuffer(b, "<f4", int.from_bytes(b[4:8], "little") * 2, 8).reshape(-1, 2))
    else:
        print(f"no {padpts_path.name} - junction pads not kept clear")
    return cKDTree(np.concatenate(xy))


def contiguous_runs(mask):
    """Yields (start, end) exclusive index pairs of contiguous True runs."""
    diff = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    return list(zip(starts, ends))


def build_wall_runs(ox, oy, oz, tile_size_cm):
    """Splits one side's offset station polyline into tile-bucketable runs -
    just the station centerline points (ground-level, one per station), no
    mesh data. AODBarrierManager extrudes each run into the actual vertical-
    ribbon collision mesh (2 verts/2 tris per station step) at runtime - see
    this module's docstring for why the mesh itself is no longer baked."""
    n = len(ox)
    runs = []
    for c0 in range(0, n - 1, WALL_CHUNK_STATIONS):
        c1 = min(c0 + WALL_CHUNK_STATIONS, n - 1)
        pts = [(ox[i], oy[i], oz[i]) for i in range(c0, c1 + 1)]
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        tile_key = f"{int(cx // tile_size_cm)},{int(cy // tile_size_cm)}"
        runs.append({
            "tile": tile_key,
            "station_xyz_cm": [round(v, 1) for p in pts for v in p],
        })
    return runs


def main():
    data = json.loads(STATIONS_PATH.read_text())
    populated_tiles = load_settlement_tiles()
    if populated_tiles:
        print(f"{len(populated_tiles)} populated tile(s) will be left open (no barrier)")
    lakes = load_lake_beds()
    if lakes:
        print(f"{len(lakes)} lake bed(s) loaded - barrier wall/scenery points over "
              f"open water will be skipped")

    roads = road_tree(data["segments"])

    points = []
    wall_runs = []
    n_skipped_bridge = n_skipped_town = n_skipped_lake = n_skipped_road = 0
    for seg in data["segments"]:
        east = np.array(seg["east_m"])
        north = np.array(seg["north_m"])
        elev = np.array(seg["elev_m"])
        s = np.array(seg["s_m"])
        is_bridge = np.array(seg.get("is_bridge", [False] * len(east)), dtype=float)

        if s[-1] < BARRIER_SPACING_M:
            continue
        s_fine = np.arange(0, s[-1], BARRIER_SPACING_M)
        east_f = np.interp(s_fine, s, east)
        north_f = np.interp(s_fine, s, north)
        elev_f = np.interp(s_fine, s, elev)
        bridge_f = np.interp(s_fine, s, is_bridge) > 0.05

        n = len(s_fine)
        cx, cy = east_f * 100.0, -north_f * 100.0   # UE frame cm
        tx_dir = np.gradient(cx)
        ty_dir = np.gradient(cy)
        tl = np.hypot(tx_dir, ty_dir)
        tl[tl < 1e-6] = 1.0
        tx_dir /= tl
        ty_dir /= tl
        rx, ry = -ty_dir, tx_dir   # right-of-travel, horizontal (UE frame)

        z_cm = road_ue_z_m(elev_f, 0.0, ORIGIN_HEIGHT_M) * 100.0
        offset_cm = BARRIER_OFFSET_M * 100.0

        for side in (-1.0, 1.0):
            ox = cx + rx * offset_cm * side
            oy = cy + ry * offset_cm * side

            # a point is "open" (no wall/no trees) if it's a bridge span or
            # its tile is a populated town - skip mask feeds BOTH the wall
            # (must be a genuinely continuous run, broken at every gap) and
            # the visual points below
            tile_tx = (ox // SETTLEMENT_CELL_CM).astype(int)
            tile_ty = (oy // SETTLEMENT_CELL_CM).astype(int)
            in_town = np.array([(tx, ty) in populated_tiles
                                 for tx, ty in zip(tile_tx, tile_ty)])
            # Lake exclusion (2026-09-03): same treatment as bridge/town -
            # open water gets no wall run and no dressed tree/bush point.
            # Checked against every baked lake's bbox first (cheap) before
            # paying for the exact ray-cast polygon test.
            in_lake = np.zeros(n, dtype=bool)
            margin_cm = LAKE_SHORE_EXCLUSION_MARGIN_M * 100.0
            for lake in lakes_near(lakes, ox, oy, margin_cm):
                bx0, bx1, by0, by1 = lake["bbox"]
                bb = ((ox >= bx0 - margin_cm) & (ox <= bx1 + margin_cm) &
                      (oy >= by0 - margin_cm) & (oy <= by1 + margin_cm))
                if not bb.any():
                    continue
                idx = np.where(bb)[0]
                inside = _point_in_polygon(ox[idx], oy[idx], lake["ring_x"], lake["ring_y"])
                dist_cm = _dist_to_ring(ox[idx], oy[idx], lake["ring_x"], lake["ring_y"])
                in_lake[idx] |= inside | (dist_cm <= margin_cm)

            on_road = roads.query(np.c_[ox, oy], distance_upper_bound=BARRIER_ROAD_CLEAR_M * 100.0)[0] < np.inf
            open_mask = bridge_f | in_town | in_lake | on_road
            keep_mask = ~open_mask

            for run_start, run_end in contiguous_runs(keep_mask):
                if run_end - run_start >= 2:
                    wall_runs.extend(build_wall_runs(
                        ox[run_start:run_end], oy[run_start:run_end],
                        z_cm[run_start:run_end], TERRAIN_TILE_SIZE_CM))

            for i in range(n):
                if open_mask[i]:
                    if bridge_f[i]:
                        n_skipped_bridge += 1
                    elif in_lake[i]:
                        n_skipped_lake += 1
                    elif on_road[i]:
                        n_skipped_road += 1
                    else:
                        n_skipped_town += 1
                    continue
                # Python's built-in hash() salts str hashes per-process
                # (PYTHONHASHSEED) unless explicitly fixed, so this seed
                # changed on every rebuild - crc32 over an explicit byte
                # encoding is deterministic across runs/machines.
                seed = zlib.crc32(f"{seg['name']}|{i}|{side}".encode()) & 0xFFFFFFFF
                points.append({
                    "x_cm": round(float(ox[i]), 1), "y_cm": round(float(oy[i]), 1),
                    "z_cm": round(float(z_cm[i]), 1), "seed": seed,
                })

    OUT_PATH.write_text(json.dumps({"points": points, "wall_runs": wall_runs}))
    total_stations = sum(len(r["station_xyz_cm"]) // 3 for r in wall_runs)
    print(f"{len(points)} tree/bush point(s), {len(wall_runs)} wall run(s) "
          f"({total_stations} station(s)) "
          f"({n_skipped_bridge} station-sides skipped near bridges/tunnels, "
          f"{n_skipped_town} skipped inside populated tiles, "
          f"{n_skipped_lake} skipped over open water, "
          f"{n_skipped_road} within {BARRIER_ROAD_CLEAR_M} m of a road or pad) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
