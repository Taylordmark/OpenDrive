"""Bakes real 3D retaining-wall/cut-face geometry at road switchback and
crossing locations flagged by road_mesh_common.classify_wall_stations() -
both same-road (same-chain / cross-chain-same-road-name) conflicts steep
enough to need a wall, AND out-of-scope conflicts (different/missing
road_name - e.g. an unnamed rural road climbing near a named one, or a
freeway ramp crossing an unrelated street) which ALWAYS get a wall
regardless of grade, since two unrelated roads never have a shared-slope
relationship that would justify blending their terrain toward each other -
see that function's docstring for the full scope breakdown (changed
2026-08-24; out-of-scope conflicts used to be a dead end - excluded from
the carve with no wall, leaving whichever side lost the exclusion as bare
raw DEM).

Usage:  python build_retaining_walls.py   (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Writes  data/road_tiles/<AREA_NAME>_test/retaining_walls.json

Must run BEFORE build_terrain_mesh_tiles.py in the rebuild order - not
because it reads this script's output (it doesn't; both independently call
classify_wall_stations against the same road_stations.json and get
identical answers by construction, same pattern road_ue_z_m/carve_heightmap
already establish for other shared math), but because the threshold split
this script's classification implies (un-excluding below-WALL_MIN_GRADE
conflicts from the terrain carve) is exactly what build_terrain_mesh_tiles.py
needs to already be doing when it carves.

Geometry model: cluster wall-eligible conflict PAIRS into connected-
component "runs" (Union-Find - correctly merges transitively, e.g. many
overlapping pairs along one real hairpin collapsing into a single wall
instead of dozens of fragments), split each run into "upper"/"lower" station
sets via 1-D 2-means on elevation, order both sides along the run's own
dominant spatial axis (1st principal component of all run stations - robust
to whichever mix of same-chain/cross-chain-same-name stations make up the
run, unlike sorting by each side's own per-chain arc-length `s_m`, which
isn't comparable across a chain break), resample both sides to a shared
axis-projection range, and stitch corresponding samples into a ruled-surface
mesh via the same row-stitching triangulation idiom as
road_mesh_common.build_road_chunks (two independent 3-D paths, not a single
centerline + lateral cross-section).

retaining_walls.json schema (same convention as building_mesh.json):
    {"area": "<AREA_NAME>",
     "walls": [{"name","tile","road_names","vertices","uvs","triangles"}, ...]}
A run can be long and cross tile boundaries, so each run is chunked into
WALL_CHUNK_STATIONS-sized pieces before tile-bucketing, mirroring
build_road_barrier.py's build_wall_runs()/road_mesh_common.build_road_chunks'
{name_prefix}_{c0:04d} naming.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from road_mesh_common import (classify_wall_stations, road_ue_z_m, WALL_EMBED_MARGIN_M,
                              half_width_for_stations, ROAD_HALF_WIDTH, WRAP_LIP_DROP_M, ROAD_LIFT)
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "retaining_walls.json"

WALL_CHUNK_STATIONS = 80     # matches build_road_barrier.py's WALL_CHUNK_STATIONS -
                              # tile-bucketing granularity for a long run
WALL_SAMPLE_SPACING_M = 5.0  # matches road_mesh_common.STATION_SPACING - how
                              # densely to resample each run's ruled surface
WALL_MIN_SAMPLES = 3         # a run shorter than this along its own axis
                              # still gets at least a minimal wall, not zero
WALL_MAX_SAMPLES = 400       # sanity ceiling (a genuinely enormous run would
                              # otherwise produce an unbounded triangle count)

# 2026-09-07 fix (Taylor's live find: ground-to-sky wall slabs at Copper
# Mountain and Piney Lake Road/Vail). Union-Find's transitive merging can
# percolate one "run" across an entire town's road network (the worst
# summit_county run touched ~100 distinct Vail roads), and
# build_wall_run_mesh then fits ONE principal axis and ONE upper/lower
# 2-means split to that whole blob - the ruled surface interpolates
# between unrelated wall sites kilometers apart, which renders as a giant
# slab from ground to sky (176 of 447 baked walls had a triangle edge
# > 200 m; worst single edge 8.5 km). Two-stage split before meshing:
#   1. WALL_RUN_GAP_M: break a run into spatially-contiguous components -
#      two stations belong to the same wall only if connected by hops of
#      at most this distance. Catches genuinely disjoint sites that only
#      chained together through the shared Union-Find.
#   2. WALL_PIECE_MAX_EXTENT_M: recursively bisect any component whose
#      spatial extent still exceeds this, along its own PCA axis, until
#      every piece is short enough that a single dominant axis + a single
#      upper/lower elevation split is a valid local model. Catches the
#      contiguous-but-curved/branched case (a run snaking through a road
#      network is connected at 40 m hops yet spans kilometers).
WALL_RUN_GAP_M = 40.0
WALL_PIECE_MAX_EXTENT_M = 250.0

# 2026-09-07 wedge-slab fix (Taylor's Tennis Club Road black wedge; see
# build_wall_run_mesh's intersection comment for the full account):
#  - WALL_SIDE_RANGE_SLACK_M: how far past the two sides' projection-range
#    INTERSECTION sampling may extend - big enough that a single conflict
#    pair (two single-point "ranges" a few meters apart along the run axis)
#    still builds, small enough that a side can never be flat-clamped into
#    a giant fan again.
#  - WALL_MAX_ROW_SPAN_M: max horizontal distance between a row's lower and
#    upper vertex. A real retaining wall is a steep face a few meters deep;
#    rows spanning tens of meters horizontally (wall_0738_0000 reached
#    175 m) are the ruled surface bridging unrelated geometry - such rows
#    are dropped (no quad emitted), splitting the mesh at the bogus rows.
WALL_SIDE_RANGE_SLACK_M = 10.0
WALL_MAX_ROW_SPAN_M = 30.0


def load_station_arrays():
    data = json.loads(STATIONS_PATH.read_text())
    segs = data["segments"]
    east = np.concatenate([np.array(seg["east_m"]) for seg in segs])
    north = np.concatenate([np.array(seg["north_m"]) for seg in segs])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in segs])
    seg_idx = np.concatenate([
        np.full(len(seg["east_m"]), i, dtype=np.int32) for i, seg in enumerate(segs)])
    station_idx = np.concatenate([np.arange(len(seg["east_m"])) for seg in segs])
    road_name = [name for seg in segs for name in seg["road_name"]]
    return east, north, elev, seg_idx, station_idx, road_name


def load_station_half_widths():
    """Per-station pavement half-width (m), same rule build_road_chunks and
    the terrain carve use, in load_station_arrays' station order."""
    segs = json.loads(STATIONS_PATH.read_text())["segments"]
    return np.concatenate([
        half_width_for_stations(seg["highway_class"], seg.get("oneway"), seg.get("lanes"))
        if seg.get("highway_class") else np.full(len(seg["east_m"]), ROAD_HALF_WIDTH)
        for seg in segs])


# --------------------------------------------------------------- clustering
class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def cluster_runs(wall_pairs, n_stations):
    """Union-Find over wall-eligible pairs -> connected components ("runs").
    Two stations already share a pair only because classify_wall_stations
    flagged them in-scope + above WALL_MIN_GRADE, so this step is purely
    about merging TRANSITIVELY-connected pairs into one run - e.g. Tantalus
    Drive's many overlapping pairs (each comparing a slightly different
    station combination along the same real hairpin) collapsing into a
    small number of real runs instead of hundreds of 2-station fragments.
    Returns a list of station-index lists, one per run."""
    uf = _UnionFind(n_stations)
    touched = set()
    for i, j in wall_pairs:
        uf.union(i, j)
        touched.add(i)
        touched.add(j)
    runs = defaultdict(list)
    for s in touched:
        runs[uf.find(s)].append(s)
    return list(runs.values())


def split_run_spatial(run_stations, east, north, gap_m=WALL_RUN_GAP_M):
    """Break one Union-Find run into spatially-contiguous components: two
    stations share a component only if reachable via hops of <= gap_m.
    Grid-hash accelerated (cell = gap_m, check 3x3 neighborhood) so the
    pathological town-spanning runs (thousands of stations) stay cheap."""
    pts = np.array([[east[s], north[s]] for s in run_stations])
    n = len(run_stations)
    if n <= 1:
        return [run_stations]
    cells = defaultdict(list)
    for k in range(n):
        cells[(int(pts[k, 0] // gap_m), int(pts[k, 1] // gap_m))].append(k)
    uf = _UnionFind(n)
    gap_sq = gap_m * gap_m
    for (cx, cy), members in cells.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(cells.get((cx + dx, cy + dy), []))
        neigh = np.array(neigh)
        for k in members:
            d2 = ((pts[neigh] - pts[k]) ** 2).sum(axis=1)
            for m in neigh[d2 <= gap_sq]:
                uf.union(k, int(m))
    comps = defaultdict(list)
    for k in range(n):
        comps[uf.find(k)].append(run_stations[k])
    return list(comps.values())


def split_run_extent(run_stations, east, north, max_extent_m=WALL_PIECE_MAX_EXTENT_M):
    """Recursively bisect a spatially-contiguous component along its own
    PCA axis until each piece's extent along that axis is <= max_extent_m.
    Keeps every piece short enough for build_wall_run_mesh's single-axis
    ruled-surface model to be locally valid."""
    if len(run_stations) < 2:
        return [run_stations]
    pts = np.array([[east[s], north[s]] for s in run_stations])
    centered = pts - pts.mean(axis=0)
    cov = centered.T @ centered
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, int(np.argmax(evals))]
    proj = centered @ axis
    if proj.max() - proj.min() <= max_extent_m:
        return [run_stations]
    cut = float(np.median(proj))
    left = [s for s, p in zip(run_stations, proj) if p <= cut]
    right = [s for s, p in zip(run_stations, proj) if p > cut]
    if not left or not right:   # degenerate median split - stop recursing
        return [run_stations]
    return (split_run_extent(left, east, north, max_extent_m)
            + split_run_extent(right, east, north, max_extent_m))


def kmeans_1d_2(values):
    """Lloyd's-algorithm 2-means over a 1-D array. Returns a bool mask, True
    = the higher-mean ("upper") cluster. Every value here comes from a run
    of stations already known to span a real elevation gap (that's what
    made them wall-eligible), so a degenerate single-cluster result should
    not occur in practice - guarded anyway."""
    values = np.asarray(values, dtype=np.float64)
    c_lo, c_hi = float(values.min()), float(values.max())
    if c_hi - c_lo < 1e-6:
        med = float(np.median(values))
        return values >= med
    for _ in range(20):
        upper_mask = np.abs(values - c_hi) < np.abs(values - c_lo)
        new_c_hi = float(values[upper_mask].mean()) if upper_mask.any() else c_hi
        new_c_lo = float(values[~upper_mask].mean()) if (~upper_mask).any() else c_lo
        if abs(new_c_hi - c_hi) < 1e-9 and abs(new_c_lo - c_lo) < 1e-9:
            c_lo, c_hi = new_c_lo, new_c_hi
            break
        c_lo, c_hi = new_c_lo, new_c_hi
    return np.abs(values - c_hi) < np.abs(values - c_lo)


def face_outward_flip(tris, verts, centroid_ref):
    """Flips the whole wall's winding (once, per-run - not per-quad, so
    internal winding stays consistent either way) if its average face
    normal points TOWARD centroid_ref rather than away from it. centroid_ref
    approximates "where the rest of this run's own road chain(s) already
    are" - a switchback's retaining wall generally sits on the outside of
    the curve relative to that. First-pass heuristic, not a guaranteed-
    correct solve for every possible switchback shape - verify visually
    during the screenshot pass (implementation plan) and revisit if a wall
    renders backward; a uniformly-backward wall is a one-line fix (this
    function already does the flip, so a wrong reference just needs
    updating), not a per-quad zebra-stripe bug."""
    v0 = verts[tris[:, 0]]
    v1 = verts[tris[:, 1]]
    v2 = verts[tris[:, 2]]
    normals = np.cross(v2 - v0, v1 - v0)   # UE convention: cross(V2-V0, V1-V0)
    avg_normal = normals.mean(axis=0)
    wall_centroid = verts.mean(axis=0)
    outward_ref = wall_centroid - centroid_ref
    if np.dot(avg_normal[:2], outward_ref[:2]) < 0:
        tris = tris.copy()
        tris[:, [1, 2]] = tris[:, [2, 1]]
    return tris


def own_chain_centroid_cm(run_stations, east, north, seg_idx):
    """UE-cm-frame centroid of every station belonging to any seg_idx the
    run touches (not just the run's own stations) - the "outward" reference
    face_outward_flip() flips away from. Approximates "where the rest of
    this road chain already is," on the reasoning that a switchback's
    retaining wall generally sits on the outside of the curve relative to
    the chain's own overall path."""
    touched_segs = sorted({int(seg_idx[s]) for s in run_stations})
    mask = np.isin(seg_idx, touched_segs)
    return np.array([
        float(east[mask].mean()) * 100.0,
        -float(north[mask].mean()) * 100.0,
        0.0,
    ])


def build_wall_run_mesh(run_stations, east, north, elev, hw, centroid_ref_cm):
    """Ruled-surface retaining-wall mesh (UE cm frame) between a run's
    upper and lower station sets. Returns (verts Nx3 float32 cm, uvs Nx2,
    tris Mx3 uint32) or None if the run is too degenerate to build (fewer
    than 2 stations on either side, or the two sides don't overlap along
    the run's own spatial axis)."""
    n = len(run_stations)
    if n < 2:
        return None
    elevs = np.array([elev[s] for s in run_stations])
    upper_mask = kmeans_1d_2(elevs)
    lower_idx = [s for s, up in zip(run_stations, upper_mask) if not up]
    upper_idx = [s for s, up in zip(run_stations, upper_mask) if up]
    if len(lower_idx) < 1 or len(upper_idx) < 1:
        return None

    pts = np.array([[east[s], north[s]] for s in run_stations])
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    cov = centered.T @ centered
    evals, evecs = np.linalg.eigh(cov)
    axis = evecs[:, int(np.argmax(evals))]
    if np.linalg.norm(axis) < 1e-9:
        return None   # degenerate (all stations coincident) - shouldn't happen

    def side_arrays(idx_list):
        arr = np.array([[east[s], north[s], elev[s], hw[s]] for s in idx_list])
        proj = (arr[:, :2] - centroid) @ axis
        order = np.argsort(proj)
        return proj[order], arr[order]

    lo_proj, lo_pts = side_arrays(lower_idx)
    hi_proj, hi_pts = side_arrays(upper_idx)

    # INTERSECTION of both sides' projection ranges, padded by a small slack
    # (2026-09-07, Taylor's Tennis Club Road "terrain above ground" black
    # wedge). This used to be the UNION, relying on np.interp's flat
    # clamping past the shorter side's data - which was "acceptable for a
    # first pass" when sides were similar lengths, but when one side is
    # much shorter, dozens of sample rows all collapse onto that side's
    # single clamped endpoint: a giant triangular fan (wall_0738_0000 near
    # Tennis Club Road had rows 0-15 sharing ONE upper vertex and rows
    # 30-45 sharing ONE lower vertex - a 24,000 m^2 slab draped over the
    # hillside, shading near-black off its degenerate normals). A wall only
    # exists where BOTH sides actually have data; the slack keeps the
    # original union-fix's motivating case working (exactly one station per
    # side = two single-point "ranges" that never literally intersect - a
    # nearby pair still builds, a far-apart junk pair is now skipped).
    slack = WALL_SIDE_RANGE_SLACK_M
    lo_min, lo_max = float(lo_proj.min()), float(lo_proj.max())
    hi_min, hi_max = float(hi_proj.min()), float(hi_proj.max())
    p_min = max(max(lo_min, hi_min) - slack, min(lo_min, hi_min))
    p_max = min(min(lo_max, hi_max) + slack, max(lo_max, hi_max))
    if p_max <= p_min:
        return None   # sides don't overlap along the run axis - junk pairing

    n_samples = int(round((p_max - p_min) / WALL_SAMPLE_SPACING_M)) + 1
    n_samples = max(WALL_MIN_SAMPLES, min(WALL_MAX_SAMPLES, n_samples))
    fracs = np.linspace(p_min, p_max, n_samples)

    lo_x = np.interp(fracs, lo_proj, lo_pts[:, 0])
    lo_y = np.interp(fracs, lo_proj, lo_pts[:, 1])
    lo_z = np.interp(fracs, lo_proj, lo_pts[:, 2])
    hi_x = np.interp(fracs, hi_proj, hi_pts[:, 0])
    hi_y = np.interp(fracs, hi_proj, hi_pts[:, 1])
    hi_z = np.interp(fracs, hi_proj, hi_pts[:, 2])
    lo_hw = np.interp(fracs, lo_proj, lo_pts[:, 3])
    hi_hw = np.interp(fracs, hi_proj, hi_pts[:, 3])

    # Edge anchors (2026-09-28, Taylor's "single plane shards in the road"):
    # rows used to run centerline to centerline, so every wall stood across
    # half of BOTH roads' lanes - a one-sided ground-material plane with
    # BlockAll collision in the driving lane. Now the bottom sits on the
    # lower road's edge facing the upper road, buried WALL_EMBED_MARGIN_M,
    # and the top tucks inside the upper road's wrap slab (between its lip
    # and underside), so no part of the face is above either pavement.
    # Offsets run along each road's own lateral normal (toward the other
    # road), not along the centerline-to-centerline line - two roads meeting
    # at an angle would otherwise leave the anchor inside the pavement.
    row_span = np.hypot(hi_x - lo_x, hi_y - lo_y)

    def side_normal(x, y, ox, oy):
        tx, ty = np.gradient(x), np.gradient(y)
        tn = np.maximum(np.hypot(tx, ty), 1e-6)
        nx, ny = -ty / tn, tx / tn
        sgn = np.where(nx * (ox - x) + ny * (oy - y) >= 0.0, 1.0, -1.0)
        return nx * sgn, ny * sgn

    lnx, lny = side_normal(lo_x, lo_y, hi_x, hi_y)
    hnx, hny = side_normal(hi_x, hi_y, lo_x, lo_y)
    # lateral clearance of each road's centerline from the OTHER road's
    # facing edge - under 0.5 m means the pavements overlap in plan (a
    # stacked crossing: the upper road is effectively a bridge, and a wall
    # there would stand in the lower road's lanes)
    lo_clear = lnx * (hi_x - lo_x) + lny * (hi_y - lo_y) - hi_hw - lo_hw
    hi_clear = hnx * (lo_x - hi_x) + hny * (lo_y - hi_y) - lo_hw - hi_hw
    lo_x, lo_y = lo_x + lnx * lo_hw, lo_y + lny * lo_hw
    hi_x, hi_y = hi_x + hnx * hi_hw, hi_y + hny * hi_hw
    lo_z = lo_z - WALL_EMBED_MARGIN_M
    hi_z = hi_z - 0.5 * WRAP_LIP_DROP_M

    lo_z_ue = road_ue_z_m(lo_z, 0.0, ORIGIN_HEIGHT_M)
    hi_z_ue = road_ue_z_m(hi_z, 0.0, ORIGIN_HEIGHT_M)

    verts = []
    uvs = []
    span = max(1e-6, p_max - p_min)
    for k in range(n_samples):
        u = (fracs[k] - p_min) / span
        # UE frame: X=east, Y=south(-north), Z=up, cm (matches build_road_chunks)
        verts.append((lo_x[k] * 100.0, -lo_y[k] * 100.0, lo_z_ue[k] * 100.0))
        uvs.append((u, 0.0))
        verts.append((hi_x[k] * 100.0, -hi_y[k] * 100.0, hi_z_ue[k] * 100.0))
        uvs.append((u, 1.0))

    # Per-row plausibility gate (see WALL_MAX_ROW_SPAN_M): a row whose lower
    # and upper vertices sit implausibly far apart horizontally is the ruled
    # surface bridging unrelated geometry, not a wall face - no quad is
    # emitted on either side of it, splitting the mesh there instead of
    # draping a shallow sheet over the hillside.
    row_ok = (row_span <= WALL_MAX_ROW_SPAN_M) & (lo_clear >= 0.5) & (hi_clear >= 0.5)

    tris = []
    for k in range(n_samples - 1):
        if not (row_ok[k] and row_ok[k + 1]):
            continue
        b = k * 2
        lo0, hi0 = b, b + 1
        lo1, hi1 = b + 2, b + 3
        tris.append((lo0, lo1, hi0))
        tris.append((lo1, hi1, hi0))
    if not tris:
        return None

    verts = np.array(verts, dtype=np.float64)
    tris = np.array(tris, dtype=np.int64)
    # Drop zero/near-zero-area triangles (duplicate clamped vertices,
    # coincident rows): they contribute garbage to the runtime's per-vertex
    # normal averaging, and M_GroundStylized's slope blend reads those
    # normals - degenerate triangles are a big part of why broken walls
    # shaded streaky near-black.
    va, vb, vc = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    area2 = np.linalg.norm(np.cross(vb - va, vc - va), axis=1)   # cm^2 * 2
    tris = tris[area2 > 100.0]   # > 0.005 m^2
    if len(tris) == 0:
        return None
    tris = face_outward_flip(tris, verts, centroid_ref_cm)
    return verts.astype(np.float32), np.array(uvs, dtype=np.float32), tris.astype(np.uint32)


# Catch-all lane gate (2026-09-28): a wall triangle with any part standing
# over a road's pavement, LANE_GATE_MIN..MAX_M above it, is dropped - a hole
# in a wall beats a collidable plane in a driving lane. Covers junction
# rows the per-row edge anchoring can't (a third road passing between the
# wall's own two, or a curve the row interpolation cuts across).
LANE_GATE_MIN_M = 0.2
LANE_GATE_MAX_M = 5.0
_BARY = np.array([(a, b, 1.0 - a - b) for a in np.linspace(0, 1, 7)
                  for b in np.linspace(0, 1, 7) if a + b <= 1.0 + 1e-9])


def build_lane_index(east, north, elev, hw):
    """All road centerlines densified to ~1 m: (KD-tree over UE-cm XY,
    half-width cm, pavement z UE cm). Station order = load_station_arrays."""
    segs = json.loads(STATIONS_PATH.read_text())["segments"]
    P, HW, Z = [], [], []
    off = 0
    for seg in segs:
        n = len(seg["east_m"])
        sl = slice(off, off + n)
        off += n
        t = np.linspace(0, n - 1, max(1, (n - 1) * 5 + 1))
        i = np.arange(n)
        P.append(np.c_[np.interp(t, i, east[sl]) * 100.0, -np.interp(t, i, north[sl]) * 100.0])
        HW.append(np.interp(t, i, hw[sl]) * 100.0)
        Z.append(road_ue_z_m(np.interp(t, i, elev[sl]), ROAD_LIFT, ORIGIN_HEIGHT_M) * 100.0)
    P = np.concatenate(P)
    return cKDTree(P), P, np.concatenate(HW), np.concatenate(Z)


def drop_in_lane_tris(verts, tris, lane_index):
    """tris minus any triangle with a sample point over a road's pavement
    (lateral < half-width) and LANE_GATE_MIN..MAX_M above it."""
    tree, P, HW, Z = lane_index
    pts = np.einsum("kj,tjc->tkc", _BARY, verts[tris].astype(np.float64))
    flat = pts.reshape(-1, 3)
    bad = np.zeros(len(flat), dtype=bool)
    for k, idxs in enumerate(tree.query_ball_point(flat[:, :2], 1500.0)):
        if idxs:
            idxs = np.asarray(idxs)
            d = np.hypot(P[idxs, 0] - flat[k, 0], P[idxs, 1] - flat[k, 1])
            h = flat[k, 2] - Z[idxs]
            bad[k] = np.any((d < HW[idxs]) & (h > LANE_GATE_MIN_M * 100.0)
                            & (h < LANE_GATE_MAX_M * 100.0))
    return tris[~bad.reshape(len(tris), -1).any(axis=1)]


def chunk_and_bucket(name_prefix, verts, uvs, tris, road_names):
    """Splits one run's mesh into WALL_CHUNK_STATIONS-row pieces (mirrors
    build_road_chunks' chunking) and tags each with its own tile key (same
    TERRAIN_TILE_SIZE_CM convention as build_building_mesh.py), since a long
    run can cross a 2km tile boundary and a single JSON object wouldn't
    bucket correctly."""
    n_rows = len(verts) // 2   # 2 verts (lower, upper) per row
    chunks = []
    for c0 in range(0, max(1, n_rows - 1), WALL_CHUNK_STATIONS):
        c1 = min(c0 + WALL_CHUNK_STATIONS, n_rows - 1)
        if c1 <= c0:
            continue
        v0, v1 = c0 * 2, (c1 + 1) * 2
        sub_verts = verts[v0:v1]
        sub_uvs = uvs[v0:v1]
        # a triangle belongs to this chunk only if ALL THREE of its vertex
        # indices fall in [v0, v1) - filtering on just the first index would
        # silently drop the last (lo1, hi1, hi0)-style triangle of each
        # chunk, whose first index sits exactly on the v1 boundary
        in_range = (tris >= v0).all(axis=1) & (tris < v1).all(axis=1)
        sub_tris = tris[in_range] - v0
        if len(sub_tris) == 0:
            continue
        cx_cm = float(sub_verts[:, 0].mean())
        cy_cm = float(sub_verts[:, 1].mean())
        tx = int(cx_cm // TERRAIN_TILE_SIZE_CM)
        ty = int(cy_cm // TERRAIN_TILE_SIZE_CM)
        chunks.append({
            "name": f"{name_prefix}_{c0:04d}",
            "tile": f"{tx},{ty}",
            "road_names": road_names,
            "vertices": [round(float(v), 1) for xyz in sub_verts for v in xyz],
            "uvs": [round(float(v), 4) for uv in sub_uvs for v in uv],
            "triangles": [int(i) for t in sub_tris for i in t],
        })
    return chunks


def main():
    east, north, elev, seg_idx, station_idx, road_name = load_station_arrays()
    hw = load_station_half_widths()
    lane_index = build_lane_index(east, north, elev, hw)
    _exclude, wall_pairs = classify_wall_stations(
        east, north, elev, seg_idx, station_idx, road_name, half_width_m=hw)
    print(f"{len(wall_pairs)} wall-eligible conflict pair(s) over {len(east)} station(s)")

    runs = cluster_runs(wall_pairs, len(east))
    print(f"{len(runs)} run(s) after clustering")

    # 2026-09-07 giant-slab fix (see WALL_RUN_GAP_M's comment): break each
    # transitively-merged run into spatially-contiguous, bounded-extent
    # pieces BEFORE fitting the single-axis ruled-surface model.
    pieces = []
    for run_stations in runs:
        for comp in split_run_spatial(run_stations, east, north):
            pieces.extend(split_run_extent(comp, east, north))
    print(f"{len(pieces)} piece(s) after spatial-gap + extent splitting")

    walls = []
    n_skipped = 0
    n_lane_dropped = 0
    max_edge_m = 0.0
    for piece_idx, piece_stations in enumerate(pieces):
        centroid_ref_cm = own_chain_centroid_cm(piece_stations, east, north, seg_idx)
        mesh = build_wall_run_mesh(piece_stations, east, north, elev, hw, centroid_ref_cm)
        if mesh is None:
            n_skipped += 1
            continue
        verts, uvs, tris = mesh
        n_before = len(tris)
        tris = drop_in_lane_tris(verts, tris, lane_index)
        n_lane_dropped += n_before - len(tris)
        if len(tris) == 0:
            n_skipped += 1
            continue
        va, vb, vc = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
        piece_max = max(np.linalg.norm(va - vb, axis=1).max(),
                        np.linalg.norm(vb - vc, axis=1).max(),
                        np.linalg.norm(vc - va, axis=1).max()) / 100.0
        max_edge_m = max(max_edge_m, float(piece_max))
        names = sorted({road_name[s] for s in piece_stations if road_name[s]})
        chunks = chunk_and_bucket(f"wall_{piece_idx:04d}", verts, uvs, tris, names)
        walls.extend(chunks)

    if n_skipped:
        print(f"{n_skipped} piece(s) skipped (degenerate - fewer than 2 stations "
              f"on one side, or non-overlapping sides)")

    print(f"{n_lane_dropped} wall triangle(s) dropped by the lane gate")

    OUT_PATH.write_text(json.dumps({"area": AREA_NAME, "walls": walls}))
    total_verts = sum(len(w["vertices"]) // 3 for w in walls)
    total_tris = sum(len(w["triangles"]) // 3 for w in walls)
    print(f"Built {len(walls)} wall chunk(s) from {len(pieces) - n_skipped} piece(s), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_PATH}")
    print(f"max triangle edge: {max_edge_m:.1f} m")
    if max_edge_m > 2.0 * WALL_PIECE_MAX_EXTENT_M:
        raise SystemExit(
            f"GATE FAIL: max wall triangle edge {max_edge_m:.1f} m exceeds "
            f"{2.0 * WALL_PIECE_MAX_EXTENT_M:.0f} m - giant-slab regression")


if __name__ == "__main__":
    main()
