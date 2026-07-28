"""Offline, engine-free gate for the baked terrain tiles (Arc 7.0 pivot,
2026-07-23) - same philosophy as the old verify_road_fit.py gate (since
retired along with the live-streaming pipeline it checked), applied to the
new tile format that replaces live-streamed terrain: does the road
actually sit at a sane
height above the ACTUAL built terrain mesh, not the pre-mesh elevation
grid. Re-requests the exact tile covering each road station from
build_terrain_mesh_tiles.build_tile() (the same production function, not a
reimplementation) and barycentric-samples the real mesh.

Usage:  python verify_terrain_fit.py

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
        (rebuilds the specific covering NEAR tiles via build_terrain_mesh_tiles)

Every station is inside a NEAR tile by construction (a station IS a road
point, so its own tile always classifies as near - see classify_tiles());
FAR tiles are informational-only distant backdrop and have no fit
requirement to check. Bridge/tunnel stations, AND stations auto-flagged by
build_terrain_mesh_tiles.detect_elevation_conflicts (a different segment
passing close by at an implausibly different elevation, no bridge tag
required), are excluded - same reasoning as verify_road_fit.py's bridge
exclusion: terrain is deliberately not carved toward a station the carve
itself skipped.

Expected relationship: build_terrain_mesh_tiles.carve_grid_m stamps
(station_elev - CARVE_DROP_M) at the road's exact location, so a healthy
station reads close to -CARVE_DROP_M, not 0 - this is terrain (the ground
beside the road), not the road driving surface itself (a separate mesh,
ROAD_LIFT above raw elevation, that AODRoadManager builds) - the two are
verified against each other separately by verify_road_fit.py.

Frame note: unlike the old live-streaming version (where a georeference
actor applied the ORIGIN_HEIGHT_M shift at render time, so quantized-mesh
tiles stored absolute elevation and verify_road_fit.py could compare directly),
build_terrain_mesh_tiles.py bakes that shift into Z at BUILD time via
road_mesh_common.road_ue_z_m() - there's no georeference actor left to do
it at runtime. So a sampled mesh height has to have ORIGIN_HEIGHT_M added
back before it's comparable to a station's raw (unshifted) elev_m.
"""

import sys
from collections import defaultdict

import numpy as np
import requests

import build_terrain_mesh_tiles as btmt
from build_terrain_mesh_tiles import TERRAIN_CARVE_DROP_M as CARVE_DROP_M
from test_areas import ORIGIN_HEIGHT_M
from road_mesh_common import sample_mesh_height

TOL_M = 1.0   # first-pass tolerance around the expected -CARVE_DROP_M offset


def main():
    lat0, lon0, east, north, elev, exclude = btmt.load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0
    station_data = (lat0, lon0, st_x_cm, st_y_cm, elev, exclude)

    fit_idx = np.where(~exclude)[0]
    n_excluded = int(np.sum(exclude))
    print(f"{len(east)} road station(s) loaded"
          + (f" ({n_excluded} excluded from the fit check - bridge/tunnel or "
             "an auto-detected different-segment elevation conflict)" if n_excluded else ""))

    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    tx_all = np.floor(st_x_cm / tile_cm).astype(int)
    ty_all = np.floor(st_y_cm / tile_cm).astype(int)

    tiles = defaultdict(list)   # (tx,ty) -> [station index, ...]
    for i in fit_idx:
        tiles[(int(tx_all[i]), int(ty_all[i]))].append(i)

    session = requests.Session()
    caches = ({}, {})
    dzs, misses = [], 0
    for (tx, ty), idxs in tiles.items():
        d = btmt.tile_min_dist_m(st_x_cm, st_y_cm, tx, ty, tile_cm)
        is_near = d <= btmt.NEAR_MARGIN_M
        if not is_near:
            # a station's own tile should always classify near by
            # construction (dist to its own tile's bbox is 0) - if this
            # ever fires, classify_tiles()/tile_min_dist_m disagree with
            # each other, which is itself the bug worth surfacing loudly
            print(f"WARNING: station tile ({tx},{ty}) did not classify as "
                  f"near (d={d:.1f}m) - skipping, but this shouldn't happen")
            continue
        tile = btmt.build_tile(tx, ty, True, station_data, session, caches)
        positions = tile["verts"][:, [0, 1, 2]]   # x_cm, y_cm, z_cm
        triangles = tile["tris"]
        for i in idxs:
            h_cm = sample_mesh_height(positions, triangles, float(st_x_cm[i]), float(st_y_cm[i]))
            if h_cm is None:
                misses += 1
                continue
            # + ORIGIN_HEIGHT_M: undo the shift road_ue_z_m() baked into Z at
            # build time, so this is comparable to the station's raw elev_m
            dzs.append(h_cm / 100.0 + ORIGIN_HEIGHT_M - float(elev[i]))   # meters

    dzs = np.array(dzs) if dzs else np.array([0.0])
    mean_abs = float(np.mean(np.abs(dzs + CARVE_DROP_M)))
    max_dev = float(np.max(np.abs(dzs + CARVE_DROP_M))) if len(dzs) else 0.0
    print(f"{len(tiles)} tile(s), {len(dzs)}/{len(fit_idx)} station(s) sampled "
          f"({misses} missed) -> mean dev from -{CARVE_DROP_M}m = {mean_abs:.2f}m, "
          f"max dev = {max_dev:.2f}m (tolerance {TOL_M}m)")

    if max_dev <= TOL_M:
        print("PASS: terrain sits at the expected carved offset below every road station.")
    else:
        print(f"FAIL: widen CARVE_INNER_M/CARVE_OUTER_M in build_terrain_tiles.py "
              f"or raise NEAR_GRID_N in build_terrain_mesh_tiles.py, then rebuild "
              f"terrain tiles and re-run this script.")
        sys.exit(1)


if __name__ == "__main__":
    main()
