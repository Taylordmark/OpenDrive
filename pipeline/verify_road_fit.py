"""Offline, engine-free road-vs-terrain fit gate (restored 2026-08-21 per
AUDIT-RELIABILITY.md SS4.2 - the original verify_road_fit.py was deleted in
a prior cleanup and never replaced; verify_terrain_fit.py's own docstring
kept citing it for months after it was gone).

Checks the ACTUAL road ODR2 tiles and ACTUAL terrain ODT1 tiles on disk in
Content/OpenDrive/Masks/ (via bin_tile_io.py, which mirrors
ODRoadManager.cpp's ParseRoadTileFile and ODTerrainManager.cpp's
ParseTerrainTileFile byte-for-byte) against each other, at every non-excluded
road station: does the built ROAD driving surface sit the expected small
amount above the built TERRAIN surface directly beneath it, using real
shipped data on both sides - not a reimplementation, not an in-memory
rebuild.

Expected relationship (see road_mesh_common.road_ue_z_m and
build_terrain_mesh_tiles.carve_grid_m):
  - the road mesh is built at (station_elev + ROAD_LIFT), UE-frame shifted
    by ORIGIN_HEIGHT_M at build time.
  - the terrain carve stamps (station_elev - CARVE_DROP_M) at the same XY,
    same shift.
  - so at a station's exact (x,y), road_h - terrain_h should read close to
    ROAD_LIFT + CARVE_DROP_M, not 0 and not some large arbitrary gap - a
    road floating well above its terrain, or sunk into/through it, is
    exactly the class of bug this gate exists to catch and the in-memory
    terrain-only gate structurally cannot (it never looks at road tiles).

Road tiles are bucketed by CHUNK CENTROID (export_road_mesh_bin.py), not by
station position, and a chunk (<=750m span) can straddle a tile boundary -
so a station's own tile (by position) might not hold the chunk covering it.
This script tries the station's own tile first, then its 8 neighbors, before
counting a station as a miss - matching AODRoadManager's own load-radius
padding for the same reason.

Usage:  python verify_road_fit.py

Bridge/tunnel stations and auto-flagged elevation-conflict stations
(road_mesh_common.classify_wall_stations) are excluded, same reasoning as
verify_terrain_fit.py - the terrain carve (and by extension this gate)
deliberately skips them; retaining-wall-covered stations get their own
check in verify_terrain_fit.py's check_wall_coverage(), not here.
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import build_terrain_mesh_tiles as btmt
from bin_tile_io import parse_road_tile_bin, parse_terrain_tile_bin
from road_mesh_common import ROAD_LIFT, sample_mesh_height
from build_terrain_mesh_tiles import TERRAIN_CARVE_DROP_M as CARVE_DROP_M
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

# Expected road-surface-above-terrain gap at a station's exact centerline
# point, per this module's docstring: ROAD_LIFT above raw elevation, the
# terrain carve CARVE_DROP_M below it - the two "below ground" and "above
# ground" biases stack, they don't cancel.
EXPECTED_GAP_M = ROAD_LIFT + CARVE_DROP_M

# Terrain's own tolerance (verify_terrain_fit.TOL_M) covers the martini
# simplification error budget on the terrain side. The road mesh has no
# equivalent simplification (STATION_SPACING-resolution, no LOD collapse),
# but banking/curve smoothing can shift the sampled height slightly off the
# raw station value near sharp curves - +1.0m covers that without being
# loose enough to hide a real fit bug. Not ratcheted from a full-Oahu
# measurement (unlike verify_terrain_fit.TOL_M) - this is a first-pass
# budget; tighten it once this gate has run against a full rebake and a
# real max-dev number is known.
TOL_M = btmt.MAX_ERROR_NEAR_M + 1.5 + 1.0

_TERRAIN_CACHE = {}
_ROAD_CACHE = {}


def _load_terrain_tile(tx, ty):
    key = (tx, ty)
    if key not in _TERRAIN_CACHE:
        path = MASKS_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        parsed = parse_terrain_tile_bin(path)
        if parsed is not None:
            parsed["positions"] = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
            parsed["triangles"] = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
        _TERRAIN_CACHE[key] = parsed
    return _TERRAIN_CACHE[key]


def _load_road_tile(tx, ty):
    key = (tx, ty)
    if key not in _ROAD_CACHE:
        path = MASKS_DIR / f"road_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        parsed = parse_road_tile_bin(path)
        if parsed is not None:
            # Flatten every chunk's geometry into one combined (positions,
            # triangles) pair for sample_mesh_height, with per-chunk
            # triangle indices offset to stay valid in the combined array -
            # a station only needs "is there ANY road surface here," not
            # which chunk it came from.
            all_pos, all_tris = [], []
            base = 0
            for chunk in parsed["chunks"]:
                verts = np.array(chunk["verts"], dtype=np.float64).reshape(-1, 3)
                tris = np.array(chunk["tris"], dtype=np.int64).reshape(-1, 3)
                if len(verts) == 0:
                    continue
                all_pos.append(verts)
                all_tris.append(tris + base)
                base += len(verts)
            parsed["positions"] = (np.concatenate(all_pos, axis=0)
                                    if all_pos else np.zeros((0, 3)))
            parsed["triangles"] = (np.concatenate(all_tris, axis=0)
                                    if all_tris else np.zeros((0, 3), dtype=np.int64))
        _ROAD_CACHE[key] = parsed
    return _ROAD_CACHE[key]


def _sample_with_neighbors(loader, tx0, ty0, x_cm, y_cm):
    """Tries (tx0,ty0) first, then its 8 neighbors, returning the first
    non-None sample - see this module's docstring for why a station's own
    tile isn't guaranteed to hold the chunk/triangle covering it."""
    order = [(tx0, ty0)] + [
        (tx0 + dx, ty0 + dy)
        for dy in (-1, 0, 1) for dx in (-1, 0, 1)
        if not (dx == 0 and dy == 0)
    ]
    tiles_tried = 0
    for (tx, ty) in order:
        parsed = loader(tx, ty)
        if parsed is None:
            continue
        tiles_tried += 1
        if len(parsed["triangles"]) == 0:
            continue
        h = sample_mesh_height(parsed["positions"], parsed["triangles"], x_cm, y_cm)
        if h is not None:
            return h, tiles_tried
    return None, tiles_tried


def check_road_vs_terrain_fit():
    lat0, lon0, east, north, elev, exclude, wall_pairs = btmt.load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0

    fit_idx = np.where(~exclude)[0]
    n_excluded = int(np.sum(exclude))
    print(f"{len(east)} road station(s) loaded"
          + (f" ({n_excluded} excluded - bridge/tunnel or an auto-detected "
             "elevation conflict, same as verify_terrain_fit.py)" if n_excluded else ""))

    tile_cm = btmt.TERRAIN_TILE_SIZE_CM   # == ROAD_TILE_SIZE_CM today
    tx_all = np.floor(st_x_cm / tile_cm).astype(int)
    ty_all = np.floor(st_y_cm / tile_cm).astype(int)

    gaps = []
    road_misses = terrain_misses = 0
    for i in fit_idx:
        tx, ty = int(tx_all[i]), int(ty_all[i])
        x_cm, y_cm = float(st_x_cm[i]), float(st_y_cm[i])

        road_h, _rt = _sample_with_neighbors(_load_road_tile, tx, ty, x_cm, y_cm)
        terrain_h, _tt = _sample_with_neighbors(_load_terrain_tile, tx, ty, x_cm, y_cm)

        if road_h is None:
            road_misses += 1
            continue
        if terrain_h is None:
            terrain_misses += 1
            continue

        road_m = road_h / 100.0 + ORIGIN_HEIGHT_M
        terrain_m = terrain_h / 100.0 + ORIGIN_HEIGHT_M
        gaps.append(road_m - terrain_m)

    gaps = np.array(gaps) if gaps else np.array([EXPECTED_GAP_M])
    dev = gaps - EXPECTED_GAP_M
    mean_abs = float(np.mean(np.abs(dev)))
    max_dev = float(np.max(np.abs(dev))) if len(dev) else 0.0

    print(f"{len(fit_idx)} in-scope station(s): {len(gaps)} sampled from disk "
          f"({road_misses} no road surface found, {terrain_misses} no terrain "
          f"surface found) -> mean dev from expected {EXPECTED_GAP_M:.2f}m gap "
          f"= {mean_abs:.2f}m, max dev = {max_dev:.2f}m (tolerance {TOL_M}m)")

    ok = max_dev <= TOL_M and road_misses == 0 and terrain_misses == 0
    if ok:
        print("PASS: the shipped road tiles sit the expected small gap above "
              "the shipped terrain tiles at every in-scope station.")
    else:
        if road_misses:
            print(f"FAIL: {road_misses} station(s) had no road tile/triangle "
                  f"covering them in their tile or its 8 neighbors - missing or "
                  f"stale road_*.bin tiles.")
        if terrain_misses:
            print(f"FAIL: {terrain_misses} station(s) had no terrain tile/triangle "
                  f"covering them - missing or stale terrain_*.bin tiles.")
        if max_dev > TOL_M:
            print(f"FAIL: road-vs-terrain gap deviates from the expected "
                  f"{EXPECTED_GAP_M:.2f}m by more than {TOL_M}m at at least one "
                  f"station - road and terrain were likely baked against "
                  f"different road_stations.json generations (check "
                  f"verify_terrain_fit.py's GENERATION-ID CHECK), or one of the "
                  f"two is stale/missing on disk.")
    return ok


def main():
    ok = check_road_vs_terrain_fit()
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
