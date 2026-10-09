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

POKE-THROUGH CHECK (2026-09-28, the "terrain shards in the road" report):
the gap check above skips every excluded station - but the exclusion mask
covers ~half the network (build_road_stations dilates the bridge + conflict
flags), and since the same date the offline carve DOES carve all of those
except true bridges/tunnels (build_terrain_mesh_tiles._get_struct_mask).
So separately, at EVERY non-bridge/tunnel station, the shipped terrain must
not stand more than POKE_TOL_M above the road surface (station elev +
ROAD_LIFT - the road mesh itself isn't sampled here because
sample_mesh_height returns the TOPMOST surface, which at a crossing is the
other road's deck and would hide a poke).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

import build_terrain_mesh_tiles as btmt
import build_retaining_walls as brw
from bin_tile_io import parse_road_tile_bin, parse_terrain_tile_bin
from road_mesh_common import ROAD_LIFT, sample_mesh_heights
from build_terrain_mesh_tiles import TERRAIN_CARVE_DROP_M as CARVE_DROP_M
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, ROAD_TILE_SIZE_CM, owns

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

# Expected road-surface-above-terrain gap at a station's exact centerline
# point, per this module's docstring: ROAD_LIFT above raw elevation, the
# terrain carve CARVE_DROP_M below it - the two "below ground" and "above
# ground" biases stack, they don't cancel.
EXPECTED_GAP_M = ROAD_LIFT + CARVE_DROP_M

# Ratcheted 2026-08-21/22 against a full Oahu rebake (673,043 in-scope
# stations): mean |dev| 0.31m, and all but 2 stations sat under 4.5m. The
# two above it - and the next few below it - are NOT a bug: they're the
# handful of in-scope stations that land within ~5-16m of an EXCLUDED
# bridge/conflict station, where the terrain carve blends toward the
# untouched raw DEM instead of the road's stamped elevation, so the gap
# opens up right at that boundary. Worst case measured: 5.38m at station
# 293559 (Mokapu Road, seg 838, station 109), 7.5m from an is_bridge=True
# station on an unrelated crossing segment (838's own station 100 is also
# a bridge, but the nearer exclusion is the crossing bridge deck). Every
# far-from-any-exclusion station (checked out to 700+m) stayed <=4.0m,
# consistent with the pre-existing formula's terrain-simplification +
# road-banking budget - so that background noise floor is unchanged; only
# the near-exclusion-boundary tail needed headroom. 6.0m covers the
# measured 5.38m max with a ~0.6m margin without being loose enough to
# hide a real fit bug (see AUDIT-RELIABILITY.md SS4.2 diagnostic run).
TOL_M = 6.0

# Terrain above the road's centerline surface by more than this = visible
# poke-through. Normal carve leaves it ROAD_LIFT + CARVE_DROP_M (0.25 m)
# BELOW; see the post-rebake stats printed by check_poke_through().
POKE_TOL_M = 0.3
POKE_EDGE_INSET_M = 0.3   # edge probes sit this far inside the pavement edge

_TERRAIN_CACHE = {}
_ROAD_CACHE = {}


def _load_terrain_tile(tx, ty, prefix="terrain_"):
    key = (prefix, tx, ty)
    if key not in _TERRAIN_CACHE:
        path = MASKS_DIR / f"{prefix}{AREA_NAME}_test_tile_{tx}_{ty}.bin"
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


def _sample_with_neighbors(loader, tx0, ty0, x_cm, y_cm, ring=1):
    """Per point: tries its own tile (tx0,ty0) first, then its neighbors out
    to `ring` tiles, keeping the first hit (NaN = none) - see this module's
    docstring for why a station's own tile isn't guaranteed to hold the
    chunk/triangle covering it. `ring` defaults to 1 (8 neighbors); road
    lookups pass 2 (2026-08-25) since ROAD_TILE_SIZE_CM (500 m) is now
    smaller than a chunk's own span (750 m, CHUNK_STATIONS * station
    spacing), so a chunk centroid up to ~375 m from a covered station can
    land more than one 500 m tile away. Arrays in, batched per tile
    (2026-10-01: was one scalar sample per point, ~56 min a run)."""
    order = [(0, 0)] + [(dx, dy) for dy in range(-ring, ring + 1)
                        for dx in range(-ring, ring + 1) if not (dx == 0 and dy == 0)]
    out = np.full(len(x_cm), np.nan)
    pending = np.arange(len(x_cm))
    for dx, dy in order:
        if not len(pending):
            break
        keys = np.c_[tx0[pending] + dx, ty0[pending] + dy]
        uniq, inv = np.unique(keys, axis=0, return_inverse=True)
        for k, (tx, ty) in enumerate(uniq):
            parsed = loader(int(tx), int(ty))
            if parsed is None or len(parsed["triangles"]) == 0:
                continue
            sel = pending[inv.ravel() == k]
            out[sel] = sample_mesh_heights(parsed["positions"], parsed["triangles"],
                                           x_cm[sel], y_cm[sel])
        pending = pending[np.isnan(out[pending])]
    return out


def _shipped_stations(east, north, elev):
    """(east, north, road z) each station actually ships at. Normally
    road_stations.json's position at elev + ROAD_LIFT, but
    experimental_junction_pads regrades AND XY-snaps junction approaches
    (up to ~25 m) and patches the roadstations sidecar in place over a
    backup of the original - so a backup entry matching the original x/y
    has, at the same index in the live sidecar, the shipped position."""
    from experimental_junction_pads import match_sidecar, read_station_sidecar, SIDECAR_BACKUP_DIR, SIDECAR_PATH
    z = elev + ROAD_LIFT
    backup = SIDECAR_BACKUP_DIR / SIDECAR_PATH.name
    if not backup.exists():
        return east, north, z
    bg, ox, oy, _oz, _h = read_station_sidecar(backup)
    g, sx, sy, sz, _h = read_station_sidecar(SIDECAR_PATH)
    if bg != g:                          # previous bake's backup, sidecar unpatched
        return east, north, z
    j = match_sidecar(ox, oy, east * 100.0, -north * 100.0)   # same one-to-one match the patch made
    m = j >= 0
    east, north = east.copy(), north.copy()
    east[m], north[m], z[m] = sx[j[m]] / 100.0, -sy[j[m]] / 100.0, sz[j[m]] / 100.0 + ORIGIN_HEIGHT_M
    return east, north, z


def check_road_vs_terrain_fit():
    east, north, elev, exclude, _clamp_r, wall_pairs = btmt.load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0

    # owned stations only: the halo's tiles are pruned (2026-10-05, step 3).
    # Road coverage skips only real bridges/tunnels: `exclude` also holds
    # every wall-conflict station (48% of Summit), which hid 246 unpaved
    # centerline stations, e.g. the I-70 on-ramp merge at (-17784, 13378)
    # (2026-10-08). The road-vs-terrain gap still skips all of `exclude`.
    struct = np.concatenate([np.asarray(s["is_struct"], dtype=bool)
                             for s in json.loads(btmt.STATIONS_PATH.read_text())["segments"]])
    road_idx = np.where(~struct & owns(st_x_cm, st_y_cm))[0]
    n_excluded = int(np.sum(exclude))
    print(f"{len(east)} road station(s) loaded"
          + (f" ({n_excluded} excluded - bridge/tunnel or an auto-detected "
             "elevation conflict, same as verify_terrain_fit.py)" if n_excluded else ""))

    # 2026-08-25: road and terrain tiles no longer share one grid size (see
    # ROAD_TILE_SIZE_CM's comment in test_areas.py), so each needs its own
    # tile-coordinate lookup - using the terrain grid for both would probe
    # the wrong road tile filename and read as false road misses.
    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    tx_all = np.floor(st_x_cm / tile_cm).astype(int)
    ty_all = np.floor(st_y_cm / tile_cm).astype(int)
    rtx_all = np.floor(st_x_cm / ROAD_TILE_SIZE_CM).astype(int)
    rty_all = np.floor(st_y_cm / ROAD_TILE_SIZE_CM).astype(int)

    road_h = _sample_with_neighbors(_load_road_tile, rtx_all[road_idx], rty_all[road_idx],
                                    st_x_cm[road_idx], st_y_cm[road_idx], ring=2)
    # A way-end station sits exactly on its ribbon's end edge, and float32 .bin
    # verts far from the origin (4 cm ulp at 650 km) can leave it a hair
    # outside - retry those within the format's own precision (far_gasquet: 4
    # misses, all < 1 cm off the surface).
    miss = np.where(np.isnan(road_h))[0]
    for ang in np.linspace(0, 2 * np.pi, 8, endpoint=False):
        if not len(miss):
            break
        sel = road_idx[miss]
        road_h[miss] = _sample_with_neighbors(
            _load_road_tile, rtx_all[sel], rty_all[sel],
            st_x_cm[sel] + 5.0 * np.cos(ang), st_y_cm[sel] + 5.0 * np.sin(ang), ring=2)
        miss = miss[np.isnan(road_h[miss])]
    road_misses = int(np.isnan(road_h).sum())
    for i in road_idx[np.isnan(road_h)][:5]:
        print(f"  no road surface at station ({east[i]:.0f}, {north[i]:.0f})")
    in_fit = ~exclude[road_idx]
    fit_idx, road_h = road_idx[in_fit], road_h[in_fit]
    terrain_h = _sample_with_neighbors(_load_terrain_tile, tx_all[fit_idx], ty_all[fit_idx],
                                       st_x_cm[fit_idx], st_y_cm[fit_idx])
    terrain_misses = int((~np.isnan(road_h) & np.isnan(terrain_h)).sum())
    ok_pts = ~np.isnan(road_h) & ~np.isnan(terrain_h)
    gaps = ((road_h[ok_pts] / 100.0 + ORIGIN_HEIGHT_M) - (terrain_h[ok_pts] / 100.0 + ORIGIN_HEIGHT_M))
    gaps = gaps if len(gaps) else np.array([EXPECTED_GAP_M])
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


def _station_edge_points(east, north, seg_idx, hw):
    """Left/right pavement-edge points (m), POKE_EDGE_INSET_M inside the
    edge, from each station's own in-segment tangent."""
    tx, ty = np.empty_like(east), np.empty_like(east)
    for s in np.unique(seg_idx):
        m = np.where(seg_idx == s)[0]
        if len(m) < 2:
            tx[m], ty[m] = 1.0, 0.0
            continue
        tx[m], ty[m] = np.gradient(east[m]), np.gradient(north[m])
    tn = np.maximum(np.hypot(tx, ty), 1e-9)
    off = np.maximum(hw - POKE_EDGE_INSET_M, 0.0)
    nx, ny = -ty / tn * off, tx / tn * off
    return (east + nx, north + ny), (east - nx, north - ny)


def check_poke_through():
    east, north, elev, _excl, _clamp_r, _walls = btmt.load_stations()
    _e, _n, _z, seg_idx, _si, _rn = brw.load_station_arrays()
    hw = brw.load_station_half_widths()
    idx_all = np.where(~btmt._get_struct_mask(len(east)))[0]
    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    e0, n0 = east, north
    from experimental_junction_pads import BANK_SCALE_PATH, SIDECAR_PATH, read_station_sidecar
    scale = None      # junction pads fade the bank to 0 into each pad
    if BANK_SCALE_PATH.exists() and SIDECAR_PATH.exists():
        f = np.load(BANK_SCALE_PATH)
        if str(f["gen"]) == read_station_sidecar(SIDECAR_PATH)[0] and len(f["scale"]) == len(east):
            scale = f["scale"]
    east, north, road_z = _shipped_stations(east, north, elev)
    bank = btmt.station_bank(east, north, scale)[:, 0]   # as pads built it, snapped positions
    print(f"junction-pad patch: {int(np.sum((east != e0) | (north != n0) | (np.abs(road_z - elev - ROAD_LIFT) > 0.01)))} "
          f"station(s) probed at their regraded/snapped shipped position")
    # centerline, then both pavement edges - a diagonal terrain triangle
    # from a neighbouring road's carve can clip the edge while the
    # centerline reads clean (2026-09-28 tightened conflict reach)
    probes = [("center", east, north)] + list(zip(("left", "right"),
                                                   *zip(*_station_edge_points(east, north, seg_idx, hw))))
    # coarse tiles too (2026-09-30): they render past CollisionRadius (2 km)
    # and, with the runtime carve off, buried roads up to 12.7 m out there
    # that this check never saw
    probes = [(f"{tag} {label}", prefix, pe, pn)
              for tag, prefix in (("near", "terrain_"), ("coarse", "terrain_coarse_"))
              for label, pe, pn in probes]
    ok = True
    for label, prefix, pe, pn in probes:
        loader = lambda tx, ty: _load_terrain_tile(tx, ty, prefix)
        poke = np.full(len(east), np.nan)
        idx = idx_all[owns(pe[idx_all] * 100.0, -pn[idx_all] * 100.0)]   # owned probes (step 3)
        x_cm, y_cm = pe[idx] * 100.0, -pn[idx] * 100.0
        h = _sample_with_neighbors(loader, np.floor(x_cm / tile_cm).astype(int),
                                   np.floor(y_cm / tile_cm).astype(int), x_cm, y_cm)
        # edges sit on the bank plane, up to ~0.6 m off the centerline either
        # way, and the carve follows it - so each edge compares against its
        # planned banked height. Not the road mesh: chunk seams (float32,
        # ~2 mm), pad rims and way ends read its lip/underside instead.
        rz = road_z[idx]
        if not label.endswith("center"):
            side = -1.0 if label.endswith("left") else 1.0
            rz = rz + side * bank[idx] * hw[idx]
        poke[idx] = h / 100.0 + ORIGIN_HEIGHT_M - rz
        p = poke[idx]
        misses = int(np.isnan(p).sum())
        p = p[~np.isnan(p)]
        bad = np.where(poke > POKE_TOL_M)[0]
        print(f"poke-through [{label}]: {len(idx)} non-bridge/tunnel station(s), {misses} without terrain; "
              f"terrain-minus-road median {np.median(p):+.2f}m, p99.9 {np.percentile(p, 99.9):+.2f}m, "
              f"max {p.max():+.2f}m; {len(bad)} above {POKE_TOL_M}m")
        for i in bad[np.argsort(-poke[bad])][:10]:
            print(f"  station {i}: terrain {poke[i]:+.2f}m above road at "
                  f"({pe[i]:.0f}, {pn[i]:.0f}) m")
        ok = ok and len(bad) == 0 and misses == 0
    print("PASS: no terrain pokes through any non-bridge road." if ok else
          "FAIL: terrain pokes through the road surface (or is missing) - see stations above.")
    return ok


def main():
    ok = check_road_vs_terrain_fit()
    ok = check_poke_through() and ok
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
