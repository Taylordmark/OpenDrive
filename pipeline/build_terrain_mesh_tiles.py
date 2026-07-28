"""Arc 7.0 pivot (2026-07-23): bake terrain as offline, world-space-tiled
DynamicMesh geometry that AODTerrainManager streams the same way
AODRoadManager already streams road tiles - replacing the old
live-streaming plugin's quantized-mesh streaming entirely. One static,
deterministic dataset per
region, verified (verify_terrain_fit.py) before the engine ever loads it -
the same "carve once offline" model that fixed Arc 7.2's road/terrain fit
problem, just with no live-streamed terrain to reconcile against at all
this time.

Usage:  python build_terrain_mesh_tiles.py   (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/terrain_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        (one ODT1 file per built tile; combines what a separate
        build+export pair does for roads into one script, since - unlike
        the road network - there's no intermediate JSON representation
        worth keeping around here)

Distance-from-route tiling (Taylor, 2026-07-23): players never need to be
more than ~5-10 m from the road, but distant terrain still needs to be
VISIBLE (mountain backdrops etc.), just not driveable. Every tile is
classified against the nearest road station's distance to the tile's own
bounds:
  - NEAR   (<= NEAR_MARGIN_M): full-resolution grid, DEM sampled at
    NEAR_DEM_ZOOM (matches the road's own elevation-sampling zoom), carved
    to the road profile (road_mesh_common's carve, adapted to work in the
    local east/north METER frame directly - road_stations.json already
    carries east_m/north_m per station, so there's no need to round-trip
    through lon/lat and fight the pymartini transpose/row-col gotcha that
    bit the old live-streaming version), collision enabled.
  - FAR    (<= RENDER_DISTANCE_M): coarse grid (FAR_GRID_N, same
    FAR_DEM_ZOOM as near tiles - see the constant's own comment for why a
    mismatched zoom here is a real bug, not a style choice). The sparse
    tessellation alone gives the "average/smooth distant terrain" look;
    no collision - the player can never reach it.
  - Beyond RENDER_DISTANCE_M: not built at all (same "no hole, just a
    missing file the runtime never retries" semantics AODRoadManager
    already relies on for MissingTiles).

ODT1 format (Content/OpenDrive/Masks/terrain_<AREA>_test_tile_<tx>_<ty>.bin):
    'O','D','T','1'                                   magic (4 bytes)
    uint8      has_collision   (0 or 1)
    uint32 LE  vertex_count
    uint32 LE  tri_count
    vertex_count * (float32 x, float32 y, float32 z)   cm, UE world frame
    vertex_count * (float32 u, float32 v)
    tri_count   * (uint32 i0, uint32 i1, uint32 i2)
"""

import json
import math
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

from road_mesh_common import road_ue_z_m
from terrain_common import fetch_terrarium_tile, terrarium_to_elevation
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

M_PER_DEG_LAT = 111132.0

NEAR_MARGIN_M = 60.0        # tile-granularity buffer beyond the literal
                             # 5-10 m stray corridor
RENDER_DISTANCE_M = 6000.0  # still-visible-but-not-driveable horizon
NEAR_GRID_N = 41            # ~50 m spacing across a 2 km tile
# 100 m spacing (2026-07-28, was 9/~250m) - Taylor: hillsides visible off to
# the side of the road read as hard-faceted, obviously low-poly ("looks like
# Windows XP") at the old spacing. MUST keep (NEAR_GRID_N-1) an integer
# multiple of (FAR_GRID_N-1) - see FAR_DEM_ZOOM's comment below for the real
# seam bug (2026-07-25) this divisibility avoids; 40/20=2 keeps every other
# near-tile boundary vertex landing exactly on a far-tile one, same
# guarantee as the original 40/8=5. Not pushed all the way to NEAR_GRID_N's
# own density (41) - far tiles cover far more ground per tile and have no
# collision, so tripling+ their triangle count for a horizon backdrop isn't
# worth the render/build-time cost the near tiles' precision actually needs.
FAR_GRID_N = 21
NEAR_DEM_ZOOM = 15          # matches build_road_stations.py's DEM_ZOOM
# FAR_DEM_ZOOM used to be 10 (coarse/pre-generalized Terrarium data, on the
# idea that it'd read as "averaged distant terrain" for free). Real bug,
# found live 2026-07-25: a near tile's grid and its neighboring far tile's
# grid share exact boundary vertex positions (linspace(x0,x1,n) always hits
# both endpoints, and 40 near-segments / 8 far-segments = 5, so every 5th
# near vertex lands exactly on a far vertex) - but at zoom 10 vs 15 those
# shared points sampled DIFFERENT elevation data entirely, so every
# near/far seam carried a real, often large step (worse in the Cascades'
# steep relief) - not a soft LOD crack, a wall/gap the width of the whole
# tile edge. Matching the zoom makes every shared boundary vertex
# numerically identical (same DEM sample + same carve at the same lat/lon);
# FAR_GRID_N's sparse tessellation alone still gives the coarse/averaged
# distant-terrain look, so nothing is lost by dropping the second zoom.
FAR_DEM_ZOOM = NEAR_DEM_ZOOM

# Carve radius, sized to THIS grid's own ~50 m sample spacing (2000 m /
# (NEAR_GRID_N-1)) - NOT reused from build_terrain_tiles.py's
# CARVE_INNER_M/OUTER_M (10/40 m), which were tuned for a ~4 m/px
# live-streamed raster. Found 2026-07-23 via verify_terrain_fit.py: with the
# 40 m live-streamed-era value, many grid vertices near a station sat far
# enough away that the
# carve never fully saturated (w<1) before the next vertex, so barycentric
# interpolation at the station's exact point blended in raw hillside
# elevation - harmless on gentle ground but a real 10-14 m spike in the
# Cascades foothills' steeper terrain, since DEM height can vary a lot over
# just 25-40 m there. INNER (50 m) comfortably covers a grid cell's worst-
# case half-diagonal so the nearest vertex to any station saturates fully;
# OUTER (150 m, 3x spacing) gives a smooth taper back to raw terrain beyond
# that instead of a hard edge.
TERRAIN_CARVE_INNER_M = 50.0
TERRAIN_CARVE_OUTER_M = 150.0
TERRAIN_CARVE_DROP_M = 0.15   # bias the carved surface just below the road so
                              # it can't z-fight/poke through - matches
                              # road_mesh_common.carve_heightmap's proven 0.15
                              # default (Arc 7.1/7.2) and the drop
                              # build_forest_elevation.py's tree ground-truth is
                              # already baked at, so terrain/road/trees all agree.
                              # (Was 1.5 - an unjustified inflation that left the
                              # road+trees floating ~1.35 m above the visible
                              # terrain, fixed 2026-07-24. No larger margin is
                              # needed for banking: road_mesh_common raises only
                              # the OUTER edge through curves, never dropping any
                              # edge below the centerline the carve references.)

# Auto-detected elevation conflicts (2026-07-23): found via verify_terrain_fit.py
# even after the carve-radius fix above, one tile still failed - two DIFFERENT
# road segments passing within ~90 m of each other with a 16 m elevation gap,
# same failure class as the original Skagit bridge crossing (Arc 7.2), except
# this pair carries no OSM bridge/tunnel tag at all. Rather than rely on OSM
# tagging (which can miss real crossings, as this proved), detect these
# geometrically: any two stations within CONFLICT_RADIUS_M of each other
# whose elevation gap exceeds what a plausible shared grade could explain
# over that distance are flagged and excluded from the carve, the same way
# is_bridge stations already are - a strict superset of tag-based detection,
# not a replacement for it (is_bridge stations are still excluded too; this
# catches what tags miss).
#
# 2026-07-26 (full-network rebuild, Taylor's ref-filter/segment-cap removal):
# this originally only compared stations from DIFFERENT segments, on the
# assumption a single chain (merge_way_chains only breaks at real OSM
# junctions) can't double back close to itself. Wrong for real mountain
# switchback/hairpin roads (SR-530 in the Cascades foothills) - the SAME
# chain climbing a hillside can pass within meters of its own earlier/later
# self at a very different elevation. verify_terrain_fit.py FAILed at 15.2 m
# on exactly this (station 149.7 m from another station 149 STATIONS later
# on the identical segment, same_seg=True, 56 m elevation gap - confirmed via
# a nearby-station dump before assuming this fix, not guessed). Now compares
# ALL station pairs regardless of segment, just excluding trivially-adjacent
# same-chain stations (SAME_SEG_GAP_STATIONS) so ordinary along-route
# progression is never even evaluated - the max_grade/min_gap_m physics
# check below is what actually decides "conflict" either way, so this isn't
# a looser detector, just a correctly-scoped one.
CONFLICT_RADIUS_M = TERRAIN_CARVE_OUTER_M   # matches the carve's own reach -
                                             # anything farther can't influence
                                             # the same grid vertices anyway
CONFLICT_MAX_GRADE = 0.15    # generous vs. road_mesh_common.MAX_GRADE (0.10)
                              # - only flag gaps too steep for ANY plausible
                              # single road profile, not just an unusual one
CONFLICT_MIN_GAP_M = 3.0     # floor so two stations meters apart with a
                              # sub-meter difference (ordinary local noise)
                              # never gets flagged
SAME_SEG_GAP_STATIONS = 8    # ~40m at 5m station spacing - skip evaluating
                              # trivially-adjacent same-chain stations at all
                              # (ordinary progression); anything past this gap
                              # is a real candidate, same-segment or not - see
                              # 2026-07-26 comment above.

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def detect_elevation_conflicts(seg_ids, x_cm, y_cm, elev_m, station_idx,
                                radius_m=CONFLICT_RADIUS_M,
                                max_grade=CONFLICT_MAX_GRADE,
                                min_gap_m=CONFLICT_MIN_GAP_M,
                                same_seg_gap_stations=SAME_SEG_GAP_STATIONS):
    """Geometric (not tag-based) detection of station pairs that are close in
    XY but too far apart in elevation to be the same piece of ground - see
    the CONFLICT_* constants' comment for why this exists, and the
    2026-07-26 comment for why this checks ALL pairs (not just different-
    segment ones) minus trivially-adjacent same-chain neighbors. Grid-
    bucketed (radius_m cells, 3x3 neighborhood per query) since a brute-
    force all-pairs check doesn't scale to tens of thousands of stations;
    returns a bool array, True = exclude from the carve."""
    n = len(x_cm)
    radius_cm = radius_m * 100.0
    cx = np.floor(x_cm / radius_cm).astype(int)
    cy = np.floor(y_cm / radius_cm).astype(int)
    buckets = defaultdict(list)
    for i in range(n):
        buckets[(cx[i], cy[i])].append(i)

    conflict = np.zeros(n, dtype=bool)
    for (bx, by), idxs in buckets.items():
        neighbor_idxs = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_idxs.extend(buckets.get((bx + dx, by + dy), []))
        if not neighbor_idxs:
            continue
        ni = np.array(neighbor_idxs)
        for i in idxs:
            d_m = np.hypot(x_cm[ni] - x_cm[i], y_cm[ni] - y_cm[i]) / 100.0
            candidate = (seg_ids[ni] != seg_ids[i]) | (
                np.abs(station_idx[ni] - station_idx[i]) > same_seg_gap_stations)
            close = (d_m > 0.01) & (d_m <= radius_m) & candidate
            if not close.any():
                continue
            dz = np.abs(elev_m[ni[close]] - elev_m[i])
            allowed = max_grade * d_m[close] + min_gap_m
            if (dz > allowed).any():
                conflict[i] = True
    return conflict


def load_stations():
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    is_bridge = np.concatenate([
        np.array(seg.get("is_bridge", [False] * len(seg["east_m"])), dtype=bool)
        for seg in data["segments"]])
    seg_ids = np.concatenate([
        np.full(len(seg["east_m"]), si, dtype=np.int32)
        for si, seg in enumerate(data["segments"])])
    station_idx = np.concatenate([
        np.arange(len(seg["east_m"])) for seg in data["segments"]])

    x_cm, y_cm = east * 100.0, -north * 100.0
    conflict = detect_elevation_conflicts(seg_ids, x_cm, y_cm, elev, station_idx)
    n_new = int(np.sum(conflict & ~is_bridge))
    if n_new:
        print(f"detect_elevation_conflicts: {n_new} station(s) auto-flagged "
              f"(crossing or same-chain switchback within {CONFLICT_RADIUS_M:.0f}m, "
              f"no bridge/tunnel tag) - excluded from the terrain carve the same "
              f"way is_bridge stations are")
    exclude = is_bridge | conflict
    return lat0, lon0, east, north, elev, exclude


def sample_dem(lat_arr, lon_arr, zoom, session, cache):
    """Bilinear-sample Terrarium elevation at `zoom` for arrays of lat/lon,
    fetching+caching whichever 256x256 tiles are needed into `cache` (dict
    keyed by (zoom, tx, ty) -> elevation array or None for an unfetchable
    tile, treated as 0 m - same convention as build_road_stations.py's
    sparse DEM cache, just self-fetching instead of pre-populated)."""
    n = 256 * (2 ** zoom)
    lat_r = np.radians(lat_arr)
    px_glob = (lon_arr + 180.0) / 360.0 * n
    py_glob = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    tx_arr = (px_glob // 256).astype(int)
    ty_arr = (py_glob // 256).astype(int)

    needed = set(zip(tx_arr.tolist(), ty_arr.tolist()))
    for (tx, ty) in list(needed):
        needed.update({(tx + 1, ty), (tx, ty + 1), (tx + 1, ty + 1)})
    for (tx, ty) in needed:
        key = (zoom, tx, ty)
        if key in cache:
            continue
        try:
            img = fetch_terrarium_tile(zoom, tx, ty, session)
            cache[key] = terrarium_to_elevation(img)
        except requests.RequestException:
            cache[key] = None

    def px(tx, ty, x, y):
        t = cache.get((zoom, tx, ty))
        return 0.0 if t is None else float(t[y, x])

    out = np.empty(len(lat_arr), dtype=np.float32)
    for i in range(len(lat_arr)):
        tx, ty = int(tx_arr[i]), int(ty_arr[i])
        lx, ly = px_glob[i] - tx * 256, py_glob[i] - ty * 256
        x0, y0 = min(int(lx), 255), min(int(ly), 255)
        fx, fy = lx - x0, ly - y0
        tx1, x1 = (tx + 1, 0) if x0 == 255 else (tx, x0 + 1)
        ty1, y1 = (ty + 1, 0) if y0 == 255 else (ty, y0 + 1)
        v00, v01 = px(tx, ty, x0, y0), px(tx1, ty, x1, y0)
        v10, v11 = px(tx, ty1, x0, y1), px(tx1, ty1, x1, y1)
        out[i] = (v00 * (1 - fx) * (1 - fy) + v01 * fx * (1 - fy)
                  + v10 * (1 - fx) * fy + v11 * fx * fy)
    return out


def tile_min_dist_m(station_x_cm, station_y_cm, tx, ty, tile_cm):
    """Min distance (m) from ANY station to this tile's bounding box - the
    gating signal for near/far/skip. Clips each station to the box first
    (matches build_terrain_tiles.py's road_near_tile bbox-pad approach, just
    exact-distance instead of a margin-padded bbox overlap test)."""
    x0, x1 = tx * tile_cm, (tx + 1) * tile_cm
    y0, y1 = ty * tile_cm, (ty + 1) * tile_cm
    cx = np.clip(station_x_cm, x0, x1)
    cy = np.clip(station_y_cm, y0, y1)
    d_cm = np.hypot(station_x_cm - cx, station_y_cm - cy)
    return float(d_cm.min()) / 100.0


def carve_grid_m(elev_grid, east_grid, north_grid, st_east, st_north, st_elev,
                  inner=TERRAIN_CARVE_INNER_M, outer=TERRAIN_CARVE_OUTER_M,
                  drop=TERRAIN_CARVE_DROP_M):
    """Same inverse-distance-stamp + smoothstep blend as
    road_mesh_common.carve_heightmap, but operating directly in the local
    east/north METER frame instead of a lon/lat-bounded pixel grid - no
    row/col transpose ambiguity to get backwards (the old live-streaming
    version's 2026-07-20 bug) since there's no pymartini array-order convention to
    match here at all, just plain 2-D arrays this script fully controls."""
    if len(st_east) == 0:
        return elev_grid
    gx, gy = east_grid.reshape(-1, 1), north_grid.reshape(-1, 1)
    sx, sy = st_east.reshape(1, -1), st_north.reshape(1, -1)
    d = np.hypot(gx - sx, gy - sy)             # (P, S)
    nearest = np.argmin(d, axis=1)
    nearest_d = d[np.arange(d.shape[0]), nearest]
    nearest_elev = st_elev[nearest]
    w = np.clip((outer - nearest_d) / (outer - inner), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    out = w * (nearest_elev - drop) + (1 - w) * elev_grid.reshape(-1)
    return out.reshape(elev_grid.shape)


def build_tile(tx, ty, is_near, station_data, session, caches):
    """Builds one tile's regular-grid mesh. Returns a dict with has_collision
    plus (verts Px3 cm, uvs Px2, tris Tx3) - the single production function
    both main() (writes the ODT1 sidecar) and verify_terrain_fit.py (checks
    the actual built mesh, not a reimplementation) call into."""
    lat0, lon0, st_x_cm, st_y_cm, st_elev, st_exclude = station_data
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    tile_cm = TERRAIN_TILE_SIZE_CM
    near_cache, far_cache = caches

    n = NEAR_GRID_N if is_near else FAR_GRID_N
    zoom = NEAR_DEM_ZOOM if is_near else FAR_DEM_ZOOM
    cache = near_cache if is_near else far_cache

    x0_cm, x1_cm = tx * tile_cm, (tx + 1) * tile_cm
    y0_cm, y1_cm = ty * tile_cm, (ty + 1) * tile_cm
    xs_cm = np.linspace(x0_cm, x1_cm, n)
    ys_cm = np.linspace(y0_cm, y1_cm, n)
    gx_cm, gy_cm = np.meshgrid(xs_cm, ys_cm)   # (n, n), row j = north, col i = east

    east_m = gx_cm / 100.0
    north_m = -gy_cm / 100.0
    lat = lat0 + north_m / M_PER_DEG_LAT
    lon = lon0 + east_m / m_per_deg_lon

    elev = sample_dem(lat.reshape(-1), lon.reshape(-1), zoom, session, cache).reshape(n, n)

    # prefilter stations to ones that could plausibly matter (cheap - exact
    # carve_grid_m call below is what actually matters for correctness, this
    # is purely a speed optimization for far tiles with thousands of
    # stations elsewhere on the route)
    prefilter_m = TERRAIN_CARVE_OUTER_M + 500.0
    # st_exclude = is_bridge OR an auto-detected different-segment elevation
    # conflict (see detect_elevation_conflicts) - both mean "don't carve
    # toward this station, the ground here belongs to something else"
    keep = (~st_exclude) & (
        np.hypot(np.clip(st_x_cm, x0_cm, x1_cm) - st_x_cm,
                 np.clip(st_y_cm, y0_cm, y1_cm) - st_y_cm) / 100.0 <= prefilter_m)
    if keep.any():
        elev = carve_grid_m(elev, east_m, north_m,
                             st_x_cm[keep] / 100.0, -st_y_cm[keep] / 100.0, st_elev[keep])

    z_cm = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M) * 100.0

    verts = np.stack([gx_cm, gy_cm, z_cm], axis=-1).reshape(-1, 3).astype(np.float32)
    ii, jj = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
    uvs = np.stack([ii, jj], axis=-1).reshape(-1, 2).astype(np.float32)

    # Winding must put the front face / computed vertex normal UP (+Z). UE's
    # triangle-normal convention (VectorUtil::Normal) is
    # normalize(cross(V2-V0, V1-V0)) - reversed vs. the usual right-handed
    # cross for its left-handed coordinate system - so a grid quad emitted as
    # (p, p+1, q) actually faces DOWN (fixed 2026-07-24: that inverted winding
    # made FMeshNormals::ComputeVertexNormals fill AODTerrainManager's normal
    # overlay pointing down, so the ground material's sun lighting hit the terrain
    # as a back-face and every tile read as flat blue sky-ambient - the "no
    # terrain / no grass" bug). Emitting (p, q, p+1) / (p+1, q, q+1) faces the
    # top up, matching the road pipeline's own convention (road tiles verify
    # 100% up-normals, terrain now does too).
    tris = []
    for j in range(n - 1):
        b = j * n
        for i in range(n - 1):
            p, q = b + i, b + i + n
            tris.append((p, q, p + 1))
            tris.append((p + 1, q, q + 1))
    tris = np.array(tris, dtype=np.uint32)

    return {"has_collision": bool(is_near), "verts": verts, "uvs": uvs, "tris": tris}


def write_tile_bin(path, tile):
    verts, uvs, tris = tile["verts"], tile["uvs"], tile["tris"]
    with open(path, "wb") as f:
        f.write(b"ODT1")
        f.write(struct.pack("<BII", 1 if tile["has_collision"] else 0,
                             len(verts), len(tris)))
        f.write(struct.pack(f"<{verts.size}f", *verts.reshape(-1)))
        f.write(struct.pack(f"<{uvs.size}f", *uvs.reshape(-1)))
        f.write(struct.pack(f"<{tris.size}I", *tris.reshape(-1)))


def classify_tiles(station_data):
    """Every tile key within RENDER_DISTANCE_M of the route, tagged near/far."""
    _, _, st_x_cm, st_y_cm, _, _ = station_data
    tile_cm = TERRAIN_TILE_SIZE_CM
    pad = RENDER_DISTANCE_M * 100.0
    tx0 = int((st_x_cm.min() - pad) // tile_cm)
    tx1 = int((st_x_cm.max() + pad) // tile_cm)
    ty0 = int((st_y_cm.min() - pad) // tile_cm)
    ty1 = int((st_y_cm.max() + pad) // tile_cm)

    tiles = {}
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            d = tile_min_dist_m(st_x_cm, st_y_cm, tx, ty, tile_cm)
            if d <= NEAR_MARGIN_M:
                tiles[(tx, ty)] = True
            elif d <= RENDER_DISTANCE_M:
                tiles[(tx, ty)] = False
    return tiles


def main():
    lat0, lon0, east, north, elev, exclude = load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0
    station_data = (lat0, lon0, st_x_cm, st_y_cm, elev, exclude)

    tiles = classify_tiles(station_data)
    n_near = sum(1 for v in tiles.values() if v)
    n_far = len(tiles) - n_near
    print(f"{len(tiles)} tile(s) to build ({n_near} near/collision, {n_far} far/no-collision)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    caches = ({}, {})   # (near_cache, far_cache), keyed (zoom, tx, ty)
    total_verts = total_tris = 0
    for i, ((tx, ty), is_near) in enumerate(tiles.items()):
        tile = build_tile(tx, ty, is_near, station_data, session, caches)
        out_path = OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile_bin(out_path, tile)
        total_verts += len(tile["verts"])
        total_tris += len(tile["tris"])
        if (i + 1) % 10 == 0 or i + 1 == len(tiles):
            print(f"  {i + 1}/{len(tiles)} tile(s) built")

    print(f"Built {len(tiles)} terrain tile(s), {total_verts} verts, {total_tris} tris -> {OUT_DIR}")


if __name__ == "__main__":
    main()
