"""Offline, engine-free gate for the baked terrain tiles (Arc 7.0 pivot,
2026-07-23; rewritten 2026-08-21 per AUDIT-RELIABILITY.md SS4).

Runs THREE separate, clearly-labeled checks:

  1. SHIPPED ARTIFACT CHECK (check_shipped_terrain_fit) - the primary gate.
     Opens the ACTUAL ODT1 .bin files in Content/OpenDrive/Masks/ (the same
     files AODTerrainManager streams at runtime) via bin_tile_io.py, which
     mirrors ODTerrainManager.cpp's ParseTerrainTileFile byte-for-byte, and
     barycentric-samples the REAL parsed vertex/triangle data with
     road_mesh_common.sample_mesh_height. This is the check that answers "is
     the world on disk right now good," which is what a PASS here always
     should have meant and, per AUDIT-RELIABILITY.md SS4.1, never did.

  2. GENERATION-ID CHECK (check_generation_ids) - AUDIT-RELIABILITY.md SS2's
     mixed-bake bug (barrier/buildings baked against a stale
     road_stations.json next to fresh terrain/road/walls) produced a
     perfectly-shaped, individually-valid-looking world that was still
     wrong. Reads every tile's 16-byte generation id
     (pipeline/generation_id.py) straight from its header - cheap, format
     agnostic - and fails loudly if: terrain tiles disagree with each other,
     OR they don't match compute_generation_id() run against the
     road_stations.json currently on disk. Also sweeps every other Masks/
     prefix (road/walls/barrier/buildings) as a bonus cross-artifact check,
     since that's precisely the SS2 failure mode and this check has all the
     machinery needed to catch it for free.

  3. PRE-BAKE CONSISTENCY CHECK (check_prebake_consistency) - the ORIGINAL
     (pre-2026-08-21) version of this gate, kept and clearly relabeled, not
     deleted: it re-derives terrain tiles in memory straight from
     build_terrain_mesh_tiles.build_tile() and the current
     road_stations.json, with fresh DEM fetches. This answers a genuinely
     different question than check #1 - "if I baked RIGHT NOW against
     today's stations and today's code, would the result be internally
     consistent" - independent of whatever happens to be sitting in
     Content/OpenDrive/Masks/ from a previous run. That's still useful
     signal (e.g. it catches a bug in the carve/tessellation logic itself,
     even before a rebake), so it stays as its own check, run last, clearly
     not a substitute for #1.

Usage:  python verify_terrain_fit.py

Every station is inside a NEAR tile by construction (a station IS a road
point, so its own tile always classifies as near - see classify_tiles());
FAR tiles are informational-only distant backdrop and have no fit
requirement to check. Bridge/tunnel stations, AND stations auto-flagged by
road_mesh_common.classify_wall_stations (a nearby station - same chain or a
different one - at an implausibly different elevation, no bridge tag
required), are excluded from the terrain-fit checks below - terrain is
deliberately not carved toward a station the carve itself skipped.
Wall-eligible conflicts (steep, in-scope - see classify_wall_stations) get
their own separate check instead, against the generated retaining-wall
geometry rather than the terrain carve (check_wall_coverage, unchanged).

Expected relationship: build_terrain_mesh_tiles.carve_grid_m stamps
(station_elev - CARVE_DROP_M) at the road's exact location, so a healthy
station reads close to -CARVE_DROP_M, not 0 - this is terrain (the ground
beside the road), not the road driving surface itself (a separate mesh,
ROAD_LIFT above raw elevation, that AODRoadManager builds) - the two are
verified against each other separately by verify_road_fit.py.

Frame note: build_terrain_mesh_tiles.py bakes the ORIGIN_HEIGHT_M shift into
Z at BUILD time via road_mesh_common.road_ue_z_m() - there's no georeference
actor left to do it at runtime. So a sampled mesh height has to have
ORIGIN_HEIGHT_M added back before it's comparable to a station's raw
(unshifted) elev_m.
"""

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

import build_terrain_mesh_tiles as btmt
import build_retaining_walls as brw
from bin_tile_io import parse_terrain_tile_bin, read_generation_id_header
from build_terrain_mesh_tiles import TERRAIN_CARVE_DROP_M as CARVE_DROP_M
from build_terrain_mesh_tiles import MAX_ERROR_NEAR_M
from generation_id import compute_generation_id
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M
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
# MAX_ERROR_NEAR_M + 1.5 kept the original tolerance's margin for
# everything ELSE (carve blending, float precision) on top of the
# simplification's own budget, with headroom above the measured 2.93 m.
#
# Margin widened 1.5 -> 2.5 (2026-09-01, first summit_county bake): when
# MAX_ERROR_NEAR_M tightened 2.0 -> 0.3 (405de3c, two-tier streaming) this
# formula silently dropped the gate 3.5m -> 1.8m, below what the
# DOCUMENTED residual class near elevation-conflict exclusions can
# legitimately produce at ANY mesh resolution: stations inside the
# TERRAIN_CARVE_OUTER_M blend radius of a conflict-excluded (crossing/
# switchback) zone get partially-raw terrain - 'next steps.md' section 7.0
# records ~2.4m of this class at Oahu's known crossings, and summit_county's
# first bake measured 2.02m at exactly such a spot (Hwy 9 south of Frisco,
# 2 stations of 134,040 over the old gate, a different segment 8m higher
# ~90m away, verified via per-station diagnosis before touching this
# number - mean dev there was 0.06m, so the carve itself is healthy).
# That residual is a carve-blend property, not mesh simplification, so it
# belongs in the fixed margin, not the MAX_ERROR_NEAR_M term.
TOL_M = MAX_ERROR_NEAR_M + 2.5

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def _fit_station_data():
    """Shared station loading + tile bucketing for both the shipped-artifact
    and pre-bake checks below - same station set, same exclusions, so the
    two checks are directly comparable."""
    lat0, lon0, east, north, elev, exclude, wall_pairs = btmt.load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0

    fit_idx = np.where(~exclude)[0]
    n_excluded = int(np.sum(exclude))
    print(f"{len(east)} road station(s) loaded"
          + (f" ({n_excluded} excluded from the fit checks - bridge/tunnel or "
             "an auto-detected different-segment elevation conflict)" if n_excluded else ""))

    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    tx_all = np.floor(st_x_cm / tile_cm).astype(int)
    ty_all = np.floor(st_y_cm / tile_cm).astype(int)

    tiles = defaultdict(list)   # (tx,ty) -> [station index, ...]
    for i in fit_idx:
        tiles[(int(tx_all[i]), int(ty_all[i]))].append(i)

    return st_x_cm, st_y_cm, elev, fit_idx, tiles, tile_cm


def check_shipped_terrain_fit():
    """SHIPPED ARTIFACT CHECK - reads the actual ODT1 .bin tiles in
    Content/OpenDrive/Masks/ and samples the real parsed mesh, per this
    module's docstring section 1."""
    st_x_cm, st_y_cm, elev, fit_idx, tiles, tile_cm = _fit_station_data()

    dzs = []
    misses = 0
    missing_tiles = 0
    for (tx, ty), idxs in tiles.items():
        d = btmt.tile_min_dist_m(st_x_cm, st_y_cm, tx, ty, tile_cm)
        is_near = d <= btmt.NEAR_MARGIN_M
        if not is_near:
            print(f"WARNING: station tile ({tx},{ty}) did not classify as "
                  f"near (d={d:.1f}m) - skipping, but this shouldn't happen")
            misses += len(idxs)
            continue

        path = MASKS_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        parsed = parse_terrain_tile_bin(path)
        if parsed is None:
            print(f"MISSING/UNREADABLE: {path.name} - {len(idxs)} station(s) "
                  f"in this tile can't be checked against the shipped bake")
            missing_tiles += 1
            misses += len(idxs)
            continue

        positions = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
        triangles = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
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
    print(f"[SHIPPED] {len(tiles)} tile(s) ({missing_tiles} missing/unreadable), "
          f"{len(dzs)}/{len(fit_idx)} station(s) sampled from disk ({misses} missed) "
          f"-> mean dev from -{CARVE_DROP_M}m = {mean_abs:.2f}m, "
          f"max dev = {max_dev:.2f}m (tolerance {TOL_M}m)")

    ok = (max_dev <= TOL_M) and (missing_tiles == 0)
    if ok:
        print("PASS [SHIPPED ARTIFACT CHECK]: the terrain tiles actually on disk "
              "sit at the expected carved offset below every road station.")
    else:
        if missing_tiles:
            print(f"FAIL [SHIPPED ARTIFACT CHECK]: {missing_tiles} tile(s) that "
                  f"should exist for the current road_stations.json are missing "
                  f"from {MASKS_DIR} - rebuild terrain (build_terrain_mesh_tiles.py) "
                  f"or run pipeline/rebuild_area.py.")
        if max_dev > TOL_M:
            print(f"FAIL [SHIPPED ARTIFACT CHECK]: the SHIPPED terrain deviates "
                  f"from the expected carve offset by more than {TOL_M}m at at "
                  f"least one station - the disk artifacts don't match the current "
                  f"road_stations.json (stale bake, partial rebake, or a real carve "
                  f"bug). Rebuild terrain tiles and re-run this script.")
    return ok


def check_generation_ids():
    """GENERATION-ID CHECK - reads the 16-byte generation id out of every
    tile header in Content/OpenDrive/Masks/ for the active area and flags:
      (a) terrain tiles that disagree with each other, and
      (b) terrain tiles that don't match compute_generation_id() run against
          the road_stations.json currently on disk.
    Also sweeps road/walls/barrier/buildings tiles as a bonus cross-artifact
    check - AUDIT-RELIABILITY.md SS2's actual bug was barrier/buildings
    baked against an OLDER generation than terrain/road/walls, which this
    catches directly."""
    current_gen_id = compute_generation_id()
    print(f"[GENID] current road_stations.json generation id: {current_gen_id}")

    prefixes = ["terrain", "road", "walls", "barrier", "buildings"]
    by_prefix = {}
    ok = True

    for prefix in prefixes:
        paths = sorted(MASKS_DIR.glob(f"{prefix}_{AREA_NAME}_test_tile_*.bin"))
        gen_ids = set()
        unreadable = 0
        for p in paths:
            head = read_generation_id_header(p)
            if head is None:
                unreadable += 1
                continue
            _magic, gen_id = head
            gen_ids.add(gen_id)
        by_prefix[prefix] = (paths, gen_ids, unreadable)

        if not paths:
            print(f"[GENID] {prefix}: no tiles found for {AREA_NAME}_test "
                  f"(nothing to check)")
            continue

        if unreadable:
            print(f"[GENID] {prefix}: {unreadable}/{len(paths)} tile header(s) "
                  f"unreadable/too short")
            ok = False

        if len(gen_ids) > 1:
            sample = sorted(gen_ids)[:5]
            more = f" (+{len(gen_ids) - 5} more)" if len(gen_ids) > 5 else ""
            print(f"[GENID] {prefix}: {len(gen_ids)} DIFFERENT generation ids "
                  f"across {len(paths)} tile(s), e.g. {sample}{more} - either a "
                  f"partial rebake left stale tiles on disk, OR (if every tile "
                  f"reports a distinct id, one per file) these tiles predate "
                  f"pipeline/generation_id.py entirely and have no real id field "
                  f"at this offset - garbage read from raw payload bytes, not a "
                  f"per-tile generation mismatch. Rebuild via "
                  f"pipeline/rebuild_area.py either way.")
            ok = False
        else:
            (gid,) = tuple(gen_ids) if gen_ids else (None,)
            match = "matches" if gid == current_gen_id else "DOES NOT MATCH"
            print(f"[GENID] {prefix}: {len(paths)} tile(s), single generation "
                  f"id {gid!r} - {match} current road_stations.json ({current_gen_id!r})")

    # The primary requirement from AUDIT-RELIABILITY.md item 2: terrain
    # tiles specifically must agree with each other AND with the current
    # stations file - this is the exact SS2 mixed-bake signature.
    terrain_paths, terrain_gen_ids, terrain_unreadable = by_prefix.get("terrain", ([], set(), 0))
    if terrain_paths and (len(terrain_gen_ids) != 1 or current_gen_id not in terrain_gen_ids):
        ok = False

    # Bonus cross-artifact check: every prefix that has tiles at all should
    # be on the SAME generation id as every other prefix, not just
    # internally consistent - this is exactly the barrier/buildings-vs-
    # terrain/road/walls split AUDIT-RELIABILITY.md SS2 found by hand.
    all_gen_ids_seen = set()
    for prefix, (paths, gen_ids, _unreadable) in by_prefix.items():
        if paths:
            all_gen_ids_seen |= gen_ids
    if len(all_gen_ids_seen) > 1:
        sample = sorted(all_gen_ids_seen)[:5]
        more = f" (+{len(all_gen_ids_seen) - 5} more)" if len(all_gen_ids_seen) > 5 else ""
        print(f"[GENID] CROSS-ARTIFACT MISMATCH: {len(all_gen_ids_seen)} distinct "
              f"generation ids present across different tile prefixes in "
              f"{MASKS_DIR}, e.g. {sample}{more} - this is the mixed-bake "
              f"signature (AUDIT-RELIABILITY.md SS2): some artifact types were "
              f"baked against a different road_stations.json than others (or, if "
              f"every prefix above reported one-id-per-tile, the whole shipped "
              f"bake simply predates generation_id.py and none of it has a real "
              f"id at all). Rebuild everything via pipeline/rebuild_area.py.")
        ok = False

    if ok:
        print("PASS [GENERATION-ID CHECK]: every tile prefix present is internally "
              "consistent and matches the current road_stations.json.")
    else:
        print("FAIL [GENERATION-ID CHECK]: see mismatches above.")
    return ok


def check_prebake_consistency():
    """PRE-BAKE CONSISTENCY CHECK (the original, pre-2026-08-21 version of
    this gate) - re-derives terrain tiles in memory from
    build_terrain_mesh_tiles.build_tile() (the same production function, not
    a reimplementation) against the CURRENT road_stations.json, with fresh
    DEM fetches, and checks that result for internal consistency. This is
    deliberately NOT a check of what's on disk (see check_shipped_terrain_fit
    for that) - it answers "would baking right now, from today's code and
    today's stations, produce a consistent world," independent of whatever
    partial/stale state Content/OpenDrive/Masks/ happens to be in."""
    lat0, lon0, east, north, elev, exclude, wall_pairs = btmt.load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0
    station_data = (lat0, lon0, st_x_cm, st_y_cm, elev, exclude)

    fit_idx = np.where(~exclude)[0]

    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    tx_all = np.floor(st_x_cm / tile_cm).astype(int)
    ty_all = np.floor(st_y_cm / tile_cm).astype(int)

    tiles = defaultdict(list)
    for i in fit_idx:
        tiles[(int(tx_all[i]), int(ty_all[i]))].append(i)

    session = requests.Session()
    caches = ({}, {})
    dzs, misses = [], 0
    for (tx, ty), idxs in tiles.items():
        d = btmt.tile_min_dist_m(st_x_cm, st_y_cm, tx, ty, tile_cm)
        is_near = d <= btmt.NEAR_MARGIN_M
        if not is_near:
            continue
        tile = btmt.build_tile(tx, ty, True, station_data, session, caches)
        positions = tile["verts"][:, [0, 1, 2]]
        triangles = tile["tris"]
        for i in idxs:
            h_cm = sample_mesh_height(positions, triangles, float(st_x_cm[i]), float(st_y_cm[i]))
            if h_cm is None:
                misses += 1
                continue
            dzs.append(h_cm / 100.0 + ORIGIN_HEIGHT_M - float(elev[i]))

    dzs = np.array(dzs) if dzs else np.array([0.0])
    mean_abs = float(np.mean(np.abs(dzs + CARVE_DROP_M)))
    max_dev = float(np.max(np.abs(dzs + CARVE_DROP_M))) if len(dzs) else 0.0
    print(f"[PRE-BAKE] {len(tiles)} tile(s), {len(dzs)}/{len(fit_idx)} station(s) "
          f"sampled from a fresh in-memory rebuild ({misses} missed) -> "
          f"mean dev from -{CARVE_DROP_M}m = {mean_abs:.2f}m, "
          f"max dev = {max_dev:.2f}m (tolerance {TOL_M}m)")

    ok = max_dev <= TOL_M
    if ok:
        print("PASS [PRE-BAKE CONSISTENCY CHECK]: baking right now against the "
              "current road_stations.json would be internally consistent.")
    else:
        print("FAIL [PRE-BAKE CONSISTENCY CHECK]: widen CARVE_INNER_M/CARVE_OUTER_M "
              "in build_terrain_tiles.py or raise NEAR_GRID_N in "
              "build_terrain_mesh_tiles.py - this is a code/algorithm issue, not a "
              "stale-disk issue (see the SHIPPED ARTIFACT CHECK above for that).")
    return ok


def check_wall_coverage():
    """Bounded gate for wall-covered (in-scope, >= WALL_MIN_GRADE) conflict
    stations - exactly the population the terrain-fit checks above
    structurally exclude and can't check at all. Re-requests each run's
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
    shipped_ok = check_shipped_terrain_fit()
    print()
    genid_ok = check_generation_ids()
    print()
    prebake_ok = check_prebake_consistency()
    print()
    wall_ok = check_wall_coverage()

    print()
    print(f"=== verify_terrain_fit.py summary: "
          f"SHIPPED ARTIFACT {'PASS' if shipped_ok else 'FAIL'}, "
          f"GENERATION-ID {'PASS' if genid_ok else 'FAIL'}, "
          f"PRE-BAKE CONSISTENCY {'PASS' if prebake_ok else 'FAIL'}, "
          f"WALL COVERAGE {'PASS' if wall_ok else 'FAIL'} ===")

    if not (shipped_ok and genid_ok and prebake_ok and wall_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
