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
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py,
               2026-08-21 - see that module's docstring)
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
from pathlib import Path

import numpy as np
import requests
from pymartini import Martini

from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import road_ue_z_m, classify_wall_stations
from terrain_common import fetch_terrarium_tile, terrarium_to_elevation
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

M_PER_DEG_LAT = 111132.0

NEAR_MARGIN_M = 60.0        # tile-granularity buffer beyond the literal
                             # 5-10 m stray corridor
RENDER_DISTANCE_M = 6000.0  # still-visible-but-not-driveable horizon
NEAR_GRID_N = 257           # AUDIT.md item 1 (2026-07-28): base sampling grid for
# adaptive martini triangulation (RTIN requires exactly 2^k+1; 257 -> 7.81 m
# spacing over a 2 km tile, matches z15 Terrarium's ~4.8 m/px so nothing is
# oversampled). This is a CEILING now, not the literal output density - see
# MAX_ERROR_NEAR_M. Replaces the old uniform-41 grid (50 m spacing, no real
# shape off the road corridor - AUDIT.md 3.1).
MAX_ERROR_NEAR_M = 2.0      # martini simplification tolerance, near tiles only.
# Taylor, 2026-07-28: accept this despite AUDIT.md's storage estimate not
# holding up under measurement - probed 3 real Oahu tiles (flat/moderate/
# Koolau-cliff) at base 257 before committing to the full pipeline (the
# audit's own recommended cheap hedge): retention ran 22-44% even at this
# tolerance, well above the "10-15% on steep terrain" guess, because Oahu's
# eroded volcanic ground carries real high-frequency relief almost
# everywhere rather than the smooth-valley/sharp-ridge split the estimate
# assumed. Revisit if this proves too expensive in practice; NEAR_GRID_N=129
# (one-constant Option A fallback) is the documented escape hatch.
SEAM_SKIRT_DROP_M = 10.0    # see build_seam_skirt()'s docstring - hides the
                             # crack an adaptive mesh can produce at a tile
                             # boundary, the same "gap hidden under geometry"
                             # pattern road_mesh_common.py's road skirt uses.
FAR_GRID_N = 21              # 100 m spacing (2026-07-28, was 9/~250m) - Taylor:
# hillsides visible off to the side of the road read as hard-faceted,
# obviously low-poly ("looks like Windows XP") at the old spacing. Far tiles
# stay on the old uniform-grid path (no adaptive triangulation - they're
# already coarse, no collision, and cost isn't the concern there); the
# NEAR/FAR boundary is now protected by NEAR tiles' own seam skirt (below)
# rather than requiring (NEAR_GRID_N-1) to divide evenly into (FAR_GRID_N-1)
# the way the old uniform-uniform pairing did.
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

# Elevation-conflict detection (station pairs close in XY, too far apart in
# Z to be the same piece of ground - e.g. a switchback doubling back on
# itself, or two different chains crossing) used to be reimplemented here
# independently of build_road_stations.py's own copy - the two disagreed by
# construction (different radius, different threshold shape). Both are now
# road_mesh_common.classify_wall_stations() (2026-07-30 consolidation),
# called below against the SAME final station data build_road_stations.py
# already folded its own copy of this decision into `is_bridge` from -
# calling it again here isn't redundant with that, it's what GUARANTEES the
# two agree (same function, same inputs, deterministic), instead of hoping
# two independent implementations happen to.
ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def load_stations():
    """Returns (lat0, lon0, east, north, elev, exclude, wall_pairs). `exclude`
    is True for any station the terrain carve must skip (real bridge/tunnel,
    OR an elevation conflict - either wall-eligible or out-of-scope, see
    classify_wall_stations()). `wall_pairs` is the (i, j) station-index list
    of in-scope, above-WALL_MIN_GRADE conflicts that need generated wall
    geometry - build_retaining_walls.py consumes this same loader so its
    run-clustering always starts from the identical pair list terrain
    carving used to decide what to exclude."""
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
    road_name = [name for seg in data["segments"] for name in seg["road_name"]]

    conflict, wall_pairs = classify_wall_stations(
        east, north, elev, seg_ids, station_idx, road_name)
    n_new = int(np.sum(conflict & ~is_bridge))
    if n_new:
        print(f"classify_wall_stations: {n_new} station(s) auto-flagged "
              f"(crossing or same-chain switchback, no bridge/tunnel tag) - "
              f"excluded from the terrain carve the same way is_bridge "
              f"stations are ({len(wall_pairs)} pair(s) wall-eligible)")
    exclude = is_bridge | conflict
    return lat0, lon0, east, north, elev, exclude, wall_pairs


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


CARVE_STATION_CHUNK = 2000   # see carve_grid_m's docstring - bounds the
                             # (grid_points x stations) distance matrix

def carve_grid_m(elev_grid, east_grid, north_grid, st_east, st_north, st_elev,
                  inner=TERRAIN_CARVE_INNER_M, outer=TERRAIN_CARVE_OUTER_M,
                  drop=TERRAIN_CARVE_DROP_M):
    """Same inverse-distance-stamp + smoothstep blend as
    road_mesh_common.carve_heightmap, but operating directly in the local
    east/north METER frame instead of a lon/lat-bounded pixel grid - no
    row/col transpose ambiguity to get backwards (the old live-streaming
    version's 2026-07-20 bug) since there's no pymartini array-order convention to
    match here at all, just plain 2-D arrays this script fully controls.

    Chunked over stations (2026-07-28) - the naive one-shot version built a
    dense (grid_points x stations) distance matrix (up to 257x257=66,049
    grid points), which is fine for a handful of nearby stations but not
    bounded: a dense mountain switchback area can hand build_tile() several
    thousand "keep" stations for one tile (prefiltered by distance, not
    count), and 66,049 x thousands x 8 bytes is multiple GB for a SINGLE
    tile - found live during the full-Oahu rebake (2026-07-28): process RSS
    swung 5.6->20 GB tile to tile, not a steady climb, which is the
    signature of one-off huge transient allocations on specific dense
    tiles rather than an actual leak. Processing stations in fixed-size
    chunks and keeping a running nearest-distance/elevation bounds peak
    memory to grid_points x CARVE_STATION_CHUNK regardless of how many
    stations a tile has nearby."""
    if len(st_east) == 0:
        return elev_grid
    shape = elev_grid.shape
    gx = east_grid.reshape(-1)
    gy = north_grid.reshape(-1)
    P = gx.shape[0]
    best_d = np.full(P, np.inf, dtype=np.float64)
    best_elev = np.zeros(P, dtype=np.float64)
    for c0 in range(0, len(st_east), CARVE_STATION_CHUNK):
        sx = st_east[c0:c0 + CARVE_STATION_CHUNK].reshape(1, -1)
        sy = st_north[c0:c0 + CARVE_STATION_CHUNK].reshape(1, -1)
        se = st_elev[c0:c0 + CARVE_STATION_CHUNK]
        d = np.hypot(gx.reshape(-1, 1) - sx, gy.reshape(-1, 1) - sy)   # (P, chunk)
        nearest = np.argmin(d, axis=1)
        nearest_d = d[np.arange(P), nearest]
        better = nearest_d < best_d
        best_d = np.where(better, nearest_d, best_d)
        best_elev = np.where(better, se[nearest], best_elev)
    w = np.clip((outer - best_d) / (outer - inner), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    out = w * (best_elev - drop) + (1 - w) * elev_grid.reshape(-1)
    return out.reshape(shape)


_MARTINI_CACHE = {}


def _martini_for(n):
    if n not in _MARTINI_CACHE:
        _MARTINI_CACHE[n] = Martini(n)
    return _MARTINI_CACHE[n]


def fix_up_winding(verts_cm, tris):
    """Flip any triangle whose UE-convention normal
    (normalize(cross(V2-V0, V1-V0))) points down. martini's own winding is
    unpredictable per-triangle (it's not building a regular quad grid), so
    unlike the uniform-grid path below (which gets this right by
    construction via a fixed (p, q, p+1) emission order) each adaptive
    triangle has to be checked individually. See that path's own comment
    for why this matters (2026-07-24 flat-terrain/no-grass bug)."""
    v0 = verts_cm[tris[:, 0]]
    v1 = verts_cm[tris[:, 1]]
    v2 = verts_cm[tris[:, 2]]
    normal_z = np.cross(v2 - v0, v1 - v0)[:, 2]
    flip = normal_z < 0
    out = tris.copy()
    out[flip, 1], out[flip, 2] = tris[flip, 2], tris[flip, 1]
    return out


def build_seam_skirt(gx_cm, gy_cm, z_cm, n, drop_cm=None):
    """Full-resolution (n-point) vertical curtain around all 4 edges of a
    near tile, dropped `drop_cm` below the TRUE boundary elevation (not
    whatever subset of it the adaptive mesh happened to keep).

    Why this exists: adaptive martini triangulation does not reliably pick
    the same sparse boundary vertex subset between two independently-built
    neighboring tiles, even when the underlying elevation data is
    bit-identical - measured 2026-07-28 (see MAX_ERROR_NEAR_M's comment):
    5-11 point symmetric differences out of ~60-150 boundary points, on
    tiles whose shared-edge elevation samples matched to the bit. So the
    straight-line interpolation between one tile's chosen boundary points
    can diverge from its neighbor's by up to MAX_ERROR_NEAR_M on each side -
    a real, if thin, crack. This skirt reproduces the true full-density
    elevation along the boundary and drops it well below that worst case,
    hiding the crack under solid geometry - the same "gap hidden under
    geometry" pattern road_mesh_common.py's own road skirt already uses for
    the road-vs-terrain seam. Also covers the NEAR/FAR tile boundary, which
    has an equivalent (pre-existing, not new) gap between the near tile's
    dense edge and the far tile's coarse one.
    """
    if drop_cm is None:
        drop_cm = SEAM_SKIRT_DROP_M * 100.0

    # One continuous perimeter walk, each corner visited once.
    idx = ([(i, 0) for i in range(n)]
           + [(n - 1, j) for j in range(1, n)]
           + [(i, n - 1) for i in range(n - 2, -1, -1)]
           + [(0, j) for j in range(n - 2, 0, -1)])
    ring_x = np.array([p[0] for p in idx])
    ring_y = np.array([p[1] for p in idx])

    top_x_cm = gx_cm[ring_y, ring_x]
    top_y_cm = gy_cm[ring_y, ring_x]
    top_z_cm = z_cm[ring_y, ring_x]
    bot_z_cm = top_z_cm - drop_cm

    m = len(idx)
    top_verts = np.stack([top_x_cm, top_y_cm, top_z_cm], axis=-1)
    bot_verts = np.stack([top_x_cm, top_y_cm, bot_z_cm], axis=-1)
    verts = np.concatenate([top_verts, bot_verts], axis=0).astype(np.float32)
    ring_uv = np.stack([ring_x / (n - 1), ring_y / (n - 1)], axis=-1)
    uvs = np.concatenate([ring_uv, ring_uv], axis=0).astype(np.float32)

    center_x_cm = float(gx_cm.min() + gx_cm.max()) / 2.0
    center_y_cm = float(gy_cm.min() + gy_cm.max()) / 2.0
    k = np.arange(m)
    k2 = (k + 1) % m
    t0, t1, b0, b1 = k, k2, m + k, m + k2
    quad_tris = np.concatenate([
        np.stack([t0, t1, b1], axis=-1),
        np.stack([t0, b1, b0], axis=-1),
    ], axis=0)

    v0p, v1p, v2p = verts[quad_tris[:, 0]], verts[quad_tris[:, 1]], verts[quad_tris[:, 2]]
    normal = np.cross(v2p - v0p, v1p - v0p)
    mid = (v0p + v1p + v2p) / 3.0
    outward_x = mid[:, 0] - center_x_cm
    outward_y = mid[:, 1] - center_y_cm
    flip = (normal[:, 0] * outward_x + normal[:, 1] * outward_y) < 0
    tris = quad_tris.copy()
    tris[flip, 1], tris[flip, 2] = quad_tris[flip, 2], quad_tris[flip, 1]

    return verts, uvs, tris.astype(np.uint32)


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
    # st_exclude = is_bridge OR an auto-detected elevation conflict (see
    # road_mesh_common.classify_wall_stations) - both mean "don't carve
    # toward this station, the ground here belongs to something else"
    # (either a real structure, or - for wall-eligible conflicts - a
    # generated retaining wall covers the gap instead)
    keep = (~st_exclude) & (
        np.hypot(np.clip(st_x_cm, x0_cm, x1_cm) - st_x_cm,
                 np.clip(st_y_cm, y0_cm, y1_cm) - st_y_cm) / 100.0 <= prefilter_m)
    if keep.any():
        elev = carve_grid_m(elev, east_m, north_m,
                             st_x_cm[keep] / 100.0, -st_y_cm[keep] / 100.0, st_elev[keep])

    z_cm = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M) * 100.0

    if is_near:
        # AUDIT.md item 1: adaptive martini triangulation instead of a
        # uniform grid - see NEAR_GRID_N/MAX_ERROR_NEAR_M's comments. `elev`
        # (meters, pre-UE-Z-shift) is what martini simplifies against;
        # road_ue_z_m is a pure additive shift (road_mesh_common.py) so an
        # error tolerance in DEM meters is identical to one in UE-Z meters.
        tile = _martini_for(n).create_tile(elev.astype(np.float32))
        grid_verts, martini_tris = tile.get_mesh(MAX_ERROR_NEAR_M)
        grid_verts = grid_verts.reshape(-1, 2).astype(np.int64)  # (x_idx, y_idx)
        gxi, gyi = grid_verts[:, 0], grid_verts[:, 1]

        verts = np.stack([gx_cm[gyi, gxi], gy_cm[gyi, gxi], z_cm[gyi, gxi]],
                          axis=-1).astype(np.float32)
        uvs = np.stack([gxi / (n - 1), gyi / (n - 1)], axis=-1).astype(np.float32)
        tris = fix_up_winding(verts, martini_tris.reshape(-1, 3).astype(np.uint32))

        skirt_verts, skirt_uvs, skirt_tris = build_seam_skirt(gx_cm, gy_cm, z_cm, n)
        base = len(verts)
        verts = np.concatenate([verts, skirt_verts], axis=0)
        uvs = np.concatenate([uvs, skirt_uvs], axis=0)
        tris = np.concatenate([tris, skirt_tris + base], axis=0).astype(np.uint32)
    else:
        # Uniform grid, unchanged - far tiles are already coarse/cheap and
        # have no collision, so adaptive triangulation isn't worth it here.
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


def write_tile_bin(path, tile, gen_id_bytes):
    verts, uvs, tris = tile["verts"], tile["uvs"], tile["tris"]
    with open(path, "wb") as f:
        f.write(b"ODT1")
        f.write(gen_id_bytes)
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
    lat0, lon0, east, north, elev, exclude, _wall_pairs_unused = load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0
    station_data = (lat0, lon0, st_x_cm, st_y_cm, elev, exclude)

    tiles = classify_tiles(station_data)
    n_near = sum(1 for v in tiles.values() if v)
    n_far = len(tiles) - n_near
    print(f"{len(tiles)} tile(s) to build ({n_near} near/collision, {n_far} far/no-collision)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    session = requests.Session()
    caches = ({}, {})   # (near_cache, far_cache), keyed (zoom, tx, ty)
    total_verts = total_tris = 0
    written_paths = []
    for i, ((tx, ty), is_near) in enumerate(tiles.items()):
        tile = build_tile(tx, ty, is_near, station_data, session, caches)
        out_path = OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile_bin(out_path, tile, gen_id_bytes)
        written_paths.append(out_path)
        total_verts += len(tile["verts"])
        total_tris += len(tile["tris"])
        if (i + 1) % 10 == 0 or i + 1 == len(tiles):
            print(f"  {i + 1}/{len(tiles)} tile(s) built")

    n_stale = delete_stale_tiles(OUT_DIR, f"terrain_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale terrain tile(s) from a previous run")
    print(f"Built {len(tiles)} terrain tile(s), {total_verts} verts, {total_tris} tris -> {OUT_DIR}")


if __name__ == "__main__":
    main()
