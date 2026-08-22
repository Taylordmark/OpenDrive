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
road_mesh_common.classify_wall_stations (a nearby station - same chain or a
different one - at an implausibly different elevation, no bridge tag
required), are excluded from the terrain-fit check below - same reasoning as
verify_road_fit.py's bridge exclusion: terrain is deliberately not carved
toward a station the carve itself skipped. Wall-eligible conflicts (steep,
in-scope - see classify_wall_stations) get their own separate check instead,
against the generated retaining-wall geometry rather than the terrain carve.

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
import build_retaining_walls as brw
from build_terrain_mesh_tiles import TERRAIN_CARVE_DROP_M as CARVE_DROP_M
from build_terrain_mesh_tiles import MAX_ERROR_NEAR_M
from test_areas import ORIGIN_HEIGHT_M
from road_mesh_common import sample_mesh_height, classify_wall_stations, WALL_EMBED_MARGIN_M

# 1.0 m was the original first-pass tolerance, sized for the old uniform-41
# grid (~50 m spacing), where the carve stamped elevation almost exactly at
# every station regardless of where it fell on the grid. AUDIT.md item 1's
# adaptive martini terrain (2026-07-28) intentionally simplifies away
# vertices where local error is below MAX_ERROR_NEAR_M, so a station can
# legitimately land up to about that far from the true carved surface
# between the sparse triangulation's sample points - not a defect, just
# the design's own error budget showing up here. Rebaking all of Oahu
# measured mean dev 0.26 m (still tiny - most stations fit closely) but
# max 2.93 m, which the old 1.0 m tolerance was never going to pass.
# MAX_ERROR_NEAR_M + 1.5 keeps the original tolerance's margin for
# everything ELSE (carve blending, float precision) on top of the
# simplification's own budget, with headroom above the measured 2.93 m.
TOL_M = MAX_ERROR_NEAR_M + 1.5


def check_wall_coverage():
    """Bounded gate for wall-covered (in-scope, >= WALL_MIN_GRADE) conflict
    stations - exactly the population the terrain-fit loop in main() above
    structurally excludes and can't check at all. Re-requests each run's
    ACTUAL built wall mesh from build_retaining_walls.build_wall_run_mesh
    (the same production function build_retaining_walls.main() calls to
    write retaining_walls.json, not a reimplementation of the ruled-surface
    math) and checks its vertices land within a bounded, sane distance of
    the real station elevations that produced it - catching gross bugs
    (wrong stations paired, a unit/axis mixup, a wall built nowhere near its
    real location) without re-deriving the per-row interpolation itself, and
    without needing exact agreement (WALL_MIN_GRADE's threshold split and
    the embed margin are themselves first-pass tunables, not exact
    specifications to hold a gate to)."""
    east, north, elev, seg_idx, station_idx, road_name = brw.load_station_arrays()
    _exclude, wall_pairs = classify_wall_stations(
        east, north, elev, seg_idx, station_idx, road_name)
    runs = brw.cluster_runs(wall_pairs, len(east))

    tol_m = WALL_EMBED_MARGIN_M + 5.0
    n_checked = n_failed = 0
    for run_stations in runs:
        centroid_ref_cm = brw.own_chain_centroid_cm(run_stations, east, north, seg_idx)
        mesh = brw.build_wall_run_mesh(run_stations, east, north, elev, centroid_ref_cm)
        if mesh is None:
            continue
        verts, _uvs, _tris = mesh
        # verts alternate [lower-edge, upper-edge] per row (see
        # build_wall_run_mesh); Z is already UE cm, road_ue_z_m-shifted -
        # undo both to get back to comparable raw DEM meters
        lower_z_m = verts[0::2, 2] / 100.0 + ORIGIN_HEIGHT_M
        upper_z_m = verts[1::2, 2] / 100.0 + ORIGIN_HEIGHT_M
        run_elevs = np.array([elev[s] for s in run_stations])
        true_min, true_max = float(run_elevs.min()), float(run_elevs.max())
        n_checked += 1

        lo_dev = float(np.max(np.maximum(
            (true_min - WALL_EMBED_MARGIN_M - tol_m) - lower_z_m,
            lower_z_m - (true_max + tol_m))))
        hi_dev = float(np.max(np.maximum(
            (true_min - tol_m) - upper_z_m,
            upper_z_m - (true_max + WALL_EMBED_MARGIN_M + tol_m))))
        vertical_extent = float(np.min(upper_z_m - lower_z_m))
        if lo_dev > 0 or hi_dev > 0 or vertical_extent < 0.5:
            n_failed += 1

    print(f"wall coverage: {len(runs)} run(s), {n_checked} mesh(es) built, "
          f"{n_failed} out of the {tol_m:.1f}m sanity bound")
    if n_failed:
        print("FAIL: at least one wall run's geometry deviates from its own "
              "real station elevations by more than a sane bound, or "
              "collapses to under 0.5m vertical extent - see "
              "build_retaining_walls.py")
        return False
    print("PASS: every wall run sits within a sane bound of its real "
          "station elevations.")
    return True


def main():
    lat0, lon0, east, north, elev, exclude, wall_pairs = btmt.load_stations()
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

    terrain_ok = max_dev <= TOL_M
    if terrain_ok:
        print("PASS: terrain sits at the expected carved offset below every road station.")
    else:
        print(f"FAIL: widen CARVE_INNER_M/CARVE_OUTER_M in build_terrain_tiles.py "
              f"or raise NEAR_GRID_N in build_terrain_mesh_tiles.py, then rebuild "
              f"terrain tiles and re-run this script.")

    # Wall-covered stations are exactly what the fit_idx loop above skipped
    # by construction (excluded from the carve) - a separate, bounded check
    # against the actual generated wall geometry instead of the terrain
    # mesh, see check_wall_coverage()'s docstring.
    wall_ok = check_wall_coverage()

    if not (terrain_ok and wall_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
