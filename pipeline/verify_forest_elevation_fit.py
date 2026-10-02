"""SHIPPED ARTIFACT CHECK for the forest elevation grid (backend-only, no
app launch - see memory/feedback_no_run_app_for_testing.md).

build_forest_elevation.py's own docstring documents the exact failure mode
this guards against: elev_<AREA>_test.bin (AODForestManager's tree
ground-truth grid) is baked SEPARATELY from the terrain tiles, from whatever
road_stations.json/terrain-mesh code happened to be current at the time it
was last run. When terrain gets rebaked (a rebuild_area.py run, a carve
tweak, a new DEM fetch) and nobody re-runs build_forest_elevation.py after,
the grid goes stale and every tree spawns at the OLD terrain's height -
found live 2026-07-25 as trees floating above the actual ground. Unlike
terrain/road/walls/barrier/buildings, this file has no generation-id header
(see build_forest_elevation.py's binary format) for
verify_terrain_fit.py's check_generation_ids() to catch, so staleness here
was previously invisible to any automated check.

Method: read every (x_cm, y_cm, z_cm) point baked into elev_<AREA>_test.bin
and re-sample the CURRENTLY SHIPPED terrain tile mesh
(bin_tile_io.parse_terrain_tile_bin + road_mesh_common.sample_mesh_height,
the same production functions verify_terrain_fit.py's shipped check uses) at
that same (x, y). If the grid matches today's terrain, the two heights
agree; if the grid is stale, they don't - directly answering "would a tree
spawn at the ground, or floating/buried" without needing to launch anything.

Usage:  python verify_forest_elevation_fit.py
"""
import struct
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from bin_tile_io import parse_terrain_tile_bin
from build_terrain_mesh_tiles import TERRAIN_TILE_SIZE_CM as TILE_CM
from build_terrain_mesh_tiles import MAX_ERROR_NEAR_M
from road_mesh_common import sample_mesh_heights
from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
ELEV_PATH = MASKS_DIR / f"elev_{AREA_NAME}_test.bin"

# A tree's grid point can land anywhere in a NEAR tile's simplified mesh, not
# just at a station - same reasoning as verify_terrain_fit.py's TOL_M, sized
# off the martini simplification budget rather than assuming an exact match.
# FAR tiles are an exact uniform grid (no simplification), so NEAR's budget
# is already a safe upper bound for either.
TOL_M = MAX_ERROR_NEAR_M + 2.5


def read_elev_bin(path):
    data = path.read_bytes()
    magic = data[0:4]
    if magic != b"ODE1":
        print(f"FAIL: {path.name} has unexpected magic {magic!r} (expected b'ODE1')")
        return None
    (n,) = struct.unpack_from("<I", data, 4)
    arr = np.frombuffer(data, dtype="<f4", count=n * 3, offset=8).reshape(n, 3)
    return arr  # (N, 3): x_cm, y_cm, z_cm


def main():
    if not ELEV_PATH.exists():
        print(f"FAIL: {ELEV_PATH} does not exist - run build_forest_elevation.py")
        sys.exit(1)

    pts = read_elev_bin(ELEV_PATH)
    if pts is None:
        sys.exit(1)
    print(f"{len(pts)} forest ground-truth point(s) loaded from {ELEV_PATH.name}")

    x_cm, y_cm, z_cm = pts[:, 0], pts[:, 1], pts[:, 2]
    tx_all = np.floor(x_cm / TILE_CM).astype(int)
    ty_all = np.floor(y_cm / TILE_CM).astype(int)

    tiles = defaultdict(list)
    for i in range(len(pts)):
        tiles[(int(tx_all[i]), int(ty_all[i]))].append(i)

    cache = {}
    devs = []
    misses = 0
    missing_tiles = 0
    for (tx, ty), idxs in tiles.items():
        if (tx, ty) not in cache:
            path = MASKS_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
            parsed = parse_terrain_tile_bin(path)
            if parsed is None:
                cache[(tx, ty)] = None
            else:
                positions = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
                triangles = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
                cache[(tx, ty)] = (positions, triangles)
        mesh = cache[(tx, ty)]
        if mesh is None:
            missing_tiles += 1
            misses += len(idxs)
            continue
        positions, triangles = mesh
        idxs = np.array(idxs)
        h_cm = sample_mesh_heights(positions, triangles, x_cm[idxs], y_cm[idxs])
        hit = ~np.isnan(h_cm)
        misses += int((~hit).sum())
        devs.extend(np.abs(h_cm[hit] - z_cm[idxs][hit]) / 100.0)  # cm -> m

    devs = np.array(devs) if len(devs) else np.array([0.0])
    mean_dev = float(np.mean(devs))
    max_dev = float(np.max(devs)) if len(devs) else 0.0
    print(f"{len(tiles)} distinct terrain tile(s) referenced ({missing_tiles} missing/unreadable)")
    print(f"{len(devs)}/{len(pts)} point(s) checked against shipped terrain "
          f"({misses} missed) -> mean |tree_z - terrain_z| = {mean_dev:.2f}m, "
          f"max = {max_dev:.2f}m (tolerance {TOL_M:.2f}m)")

    ok = (max_dev <= TOL_M) and (missing_tiles == 0)
    if ok:
        print("PASS: the forest elevation grid on disk matches the currently "
              "shipped terrain - trees should spawn on the ground, not "
              "floating or buried.")
    else:
        if missing_tiles:
            print(f"FAIL: {missing_tiles} terrain tile(s) referenced by the forest "
                  f"grid are missing from {MASKS_DIR} - terrain and forest grid "
                  f"are out of sync (rebuild terrain, then re-run "
                  f"build_forest_elevation.py).")
        if max_dev > TOL_M:
            print(f"FAIL: the forest grid deviates from the SHIPPED terrain by more "
                  f"than {TOL_M:.2f}m in at least one spot - almost certainly a stale "
                  f"bake (terrain was rebuilt after elev_{AREA_NAME}_test.bin was last "
                  f"generated). Re-run: python build_forest_elevation.py")
        sys.exit(1)


if __name__ == "__main__":
    main()
