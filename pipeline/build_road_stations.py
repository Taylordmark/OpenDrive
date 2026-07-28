"""Build the road's geographic profile - the ONE place elevation gets sampled
from the DEM and grade-smoothed - for the Arc 7 restructure (2026-07-20):
load terrain, load road, THEN overlay. Elevation used to be sampled twice
independently (once implicitly via the carved Landscape heightmap for the
Dragon's model, once by build_road_network.py for the streamed-terrain
network) with no shared source of truth; this script IS that source of
truth for the streamed-terrain network. build_terrain_mesh_tiles.py carves
it into the terrain tiles; build_road_network.py lifts it into the road
mesh; verify_terrain_fit.py checks the two actually agree once built. All
three read this file's output, none of them re-samples the DEM
independently.

Usage:  python build_road_stations.py

Reads   data/raw/<AREA_NAME>_test/osm.json          (fetch_osm_network.py)
Writes  data/road_tiles/<AREA_NAME>_test/road_stations.json
    {
      "area": AREA_NAME,
      "origin_latlon": [lat0, lon0],   # bbox center - shared anchor for every
                                        # local east/north frame in this project
      "segments": [
        {"name": "seg000", "lat": [...], "lon": [...],
         "is_bridge": [...],  # bool per station (2026-07-20) - True means
                               # "don't carve this station's elevation into
                               # the terrain" (build_terrain_tiles.py).
                               # EXPANDED (expand_carve_exclusion()): grows
                               # CARVE_BUFFER_FRAC past each tagged bridge/
                               # tunnel span's own boundary, since the real
                               # conflict a carve exclusion needs to avoid
                               # (two different roads' stations landing
                               # close together at different elevations)
                               # starts on the untagged APPROACH, not at the
                               # literal tag boundary. elev_m is interpolated
                               # (not a real DEM sample) across just the true
                               # tagged span, regardless of this flag - DEM
                               # under a bridge is whatever's really there
                               # (river, valley), not deck height.
         "road_name": [...],  # display name per station (2026-07-23, Arc
                               # "place labels" feature) - the source way's
                               # OSM name tag, falling back to ref (e.g.
                               # "WA-20") - nearest-original-station lookup,
                               # not interpolated (see main()).
         "east_m": [...], "north_m": [...], "s_m": [...],
         "elev_m": [...]}   # grade-smoothed absolute DEM elevation (or
                             # interpolated, for is_bridge stations) - NOT
                             # yet shifted by ROAD_LIFT or ORIGIN_HEIGHT_M
                             # (that's a UE-frame concern, applied by
                             # consumers via road_mesh_common.road_ue_z_m())
      ]
    }

Geographic/DEM-sampling logic (chain merging, DEM fetch, grade smoothing)
moved here from build_road_network.py, which now only does UE-frame mesh
assembly from this file's output. DEM fetch itself is road-gated (sparse
per-tile cache near actual road nodes, 2026-07-20), not one mosaic over the
whole bbox - see fetch_dem_tiles_sparse()'s docstring for why that stopped
being viable once test areas grew past Palmdale's compact scale.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

from test_areas import AREA_NAME, BBOX_SNWE
from terrain_common import fetch_terrarium_tile, lonlat_to_tilepx, terrarium_to_elevation
from road_mesh_common import STATION_SPACING, MAX_GRADE, resample_stations, grade_smooth_elevation

# ---------------------------------------------------------------- config
DEM_ZOOM = 15                   # matches build_terrain_tiles.py's MAX_ZOOM
MIN_SEGMENT_STATIONS = 20       # drop tiny fragments (driveway stubs etc.)
MAX_SEGMENTS = None             # no cap (2026-07-25, Taylor) - was 12 as a
                                 # slice-1 scope cap bounding mesh-chunk/
                                 # component count to a small network; now
                                 # building the full bbox network (ramps,
                                 # underpasses, town grids included), so every
                                 # chain above MIN_SEGMENT_STATIONS is kept.
                                 # None slices cleanly via [:None] below.

# see expand_carve_exclusion()'s docstring - grows each tagged bridge/
# tunnel span's carve-exclusion by this fraction of its own length on each
# end, to reach approach-grade conflicts just past the literal tag boundary.
# 0.10 got z15 from FAIL to MARGINAL (max dz +0.93->+0.40, min -2.29->-2.12)
# but not a clean pass - trying 0.30 next (2026-07-20, Taylor's suggestion).
CARVE_BUFFER_FRAC = 0.30

# Cross-segment elevation-conflict detection (2026-07-25, full-network
# rebuild with ROAD_REF_REGEX/MAX_SEGMENTS removed): untagged mountain
# switchback/hairpin roads (SR-530/WA-20 in the Cascades foothills) put two
# stations from DIFFERENT chains (or two far-apart points on the SAME
# chain looping back on itself) within meters of each other horizontally
# but tens to hundreds of meters apart in real elevation - found via
# verify_road_fit.py FAILing at z15 with max dz +83m/-71m even after the
# full network rebuild, traced to ~120 such locations (out of 918k
# stations) via a nearby-station elevation-delta scan. Same root cause as
# is_structure_way()'s bridge/tunnel fix (a single terrain heightmap can't
# represent two real road elevations at nearly the same lon/lat), just
# triggered by real terrain instead of a missing bridge tag - so it gets
# the same fix (exclude from carving), NOT elevation interpolation (unlike
# a bridge deck, each side's elev_m IS correct DEM elevation already, so
# fill_structure_gaps() must NOT run on these - only carve-exclusion).
CONFLICT_RADIUS_M = 40.0   # matches build_terrain_tiles.py's CARVE_OUTER_M (not imported - that module imports FROM this one's output, not the reverse)
CONFLICT_MIN_ELEV_DELTA_M = 5.0   # was 15.0 - verify_road_fit.py's ok range is only [-2.0,+0.2]m, so ANY
                                  # cross-chain elevation gap much past that within CONFLICT_RADIUS_M can blow
                                  # the gate; 15.0 missed a second, smaller-magnitude wave of the same failure
                                  # class (7.7-11.2m deltas at 2-9m range, found 2026-07-25 re-running the gate
                                  # after the first fix). 5.0 stays safely below MAX_GRADE's (0.10) own natural
                                  # same-chain climb over CONFLICT_RADIUS_M (~4m) to avoid flagging normal grade.
CONFLICT_SAME_CHAIN_GAP_STATIONS = 8   # ~40m at 5m spacing - skip flagging merely-adjacent stations on the same chain (already MAX_GRADE-clamped)
CONFLICT_EXCLUDE_STATIONS = 8      # dilate each flagged station's carve-exclusion this many stations each way (~CONFLICT_RADIUS_M at 5m spacing)

M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / f"{AREA_NAME}_test"
OUT = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- DEM
DEM_NODE_MARGIN_DEG = 0.02   # ~2 km - buffer around actual road nodes, not the whole bbox

def fetch_dem_tiles_sparse(lat_arr, lon_arr):
    """Sparse per-tile DEM cache, gated to tiles near actual road geometry
    - NOT one mosaic covering the whole bbox (2026-07-20: the Skagit/
    Cascades loop's own extent is nearly as big as the whole test bbox, a
    wide loop through 3 towns rather than a compact area like Palmdale, so
    the original single-rectangle-mosaic approach meant ~6000 tile fetches
    including everything between the loop's legs - same class of problem
    build_terrain_tiles.py's road_near_tile() already solved for z15
    terrain, applied here to the DEM fetch instead). Returns a dict keyed
    by (tx, ty) -> 256x256 float32 elevation array."""
    cache_path = RAW / "dem_tiles.npz"
    if cache_path.exists():
        with np.load(cache_path) as z:
            return {tuple(int(v) for v in k.split(",")): z[k] for k in z.files}

    needed = set()
    for lat, lon in zip(lat_arr, lon_arr):
        x0, y1 = lonlat_to_tilepx(lon - DEM_NODE_MARGIN_DEG, lat - DEM_NODE_MARGIN_DEG, DEM_ZOOM)
        x1, y0 = lonlat_to_tilepx(lon + DEM_NODE_MARGIN_DEG, lat + DEM_NODE_MARGIN_DEG, DEM_ZOOM)
        for tx in range(int(x0 // 256), int(x1 // 256) + 1):
            for ty in range(int(y0 // 256), int(y1 // 256) + 1):
                needed.add((tx, ty))
    print(f"Fetching {len(needed)} DEM tile(s) (z{DEM_ZOOM}, road-gated, "
          f"{DEM_NODE_MARGIN_DEG}deg margin) - was ~6000 for the full bbox ...")
    session = requests.Session()
    tiles = {}
    for i, (tx, ty) in enumerate(sorted(needed)):
        img = fetch_terrarium_tile(DEM_ZOOM, tx, ty, session)
        tiles[(tx, ty)] = terrarium_to_elevation(img)
        if (i + 1) % 200 == 0 or i + 1 == len(needed):
            print(f"  {i + 1}/{len(needed)} tile(s)")
    np.savez(cache_path, **{f"{tx},{ty}": v for (tx, ty), v in tiles.items()})
    return tiles


def expand_carve_exclusion(is_structure, buffer_frac=0.10):
    """Grow each bridge/tunnel span's carve-exclusion outward by
    buffer_frac of its OWN length on each end - ONLY for carve-exclusion
    purposes (elevation interpolation via fill_structure_gaps() still uses
    the untapered flag). Two things were tried and rejected before this
    (2026-07-20): a hard is_structure cutoff (excluding exactly the tagged
    span) made z15's fit WORSE, and shrinking that cutoff inward near the
    boundary (tapering) barely changed it. The real conflict turned out to
    be geometric, not tag-based: a road's plain, untagged APPROACH station
    right before a bridge starts can sit within centimeters of a different
    road passing underneath at grade, at a meaningfully different
    elevation - carve_heightmap()'s nearest-station stamp then has two
    competing targets fighting over the same pixels. Neither prior attempt
    touched this because the conflicting station was never tagged bridge/
    tunnel in the first place - it's on the approach, not the deck. Growing
    the exclusion PAST the tagged boundary (not shrinking it) is what
    actually reaches that station. Scales with span length (a short culvert
    gets a small buffer, a long bridge a bigger one) rather than a fixed
    distance, on the assumption that approach-grade conflicts scale
    similarly - verify_road_fit.py is the actual proof, not this docstring."""
    out = is_structure.copy()
    n = len(out)
    if not is_structure.any() or is_structure.all():
        return out
    # find contiguous True runs, grow each by buffer_frac * its own length
    diff = np.diff(np.concatenate([[0], is_structure.astype(int), [0]]))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]   # exclusive
    for s, e in zip(starts, ends):
        buf = int(math.ceil((e - s) * buffer_frac))
        out[max(0, s - buf):min(n, e + buf)] = True
    return out


def detect_elevation_conflicts(seg_lon, seg_lat, seg_elev, seg_idx, station_idx,
                                radius_m=CONFLICT_RADIUS_M,
                                min_delta_m=CONFLICT_MIN_ELEV_DELTA_M,
                                same_chain_gap=CONFLICT_SAME_CHAIN_GAP_STATIONS):
    """Flag stations (across the WHOLE network, not per-segment) that sit
    within radius_m of another station - on a different chain, or far along
    the SAME chain (a real switchback looping back on itself) - whose real
    elevation differs by more than min_delta_m. See CONFLICT_RADIUS_M's
    comment for why this exists. Grid-bucketed (cell size = radius_m) so
    this stays roughly linear instead of O(n^2) across ~900k stations.
    Returns a flat bool array parallel to the (seg_lon, seg_lat, ...) input,
    True = this station's real elevation can't be reconciled with a nearby
    station's within one carved terrain surface."""
    n = len(seg_lon)
    conflict = np.zeros(n, dtype=bool)
    if n == 0:
        return conflict
    lat0 = float(np.mean(seg_lat))
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    cell_deg_lat = radius_m / M_PER_DEG_LAT
    cell_deg_lon = radius_m / m_per_deg_lon
    cell_lat = np.floor(seg_lat / cell_deg_lat).astype(np.int64)
    cell_lon = np.floor(seg_lon / cell_deg_lon).astype(np.int64)
    buckets = defaultdict(list)
    for i in range(n):
        buckets[(int(cell_lon[i]), int(cell_lat[i]))].append(i)
    for i in range(n):
        cx, cy = int(cell_lon[i]), int(cell_lat[i])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((cx + dx, cy + dy), ()):
                    if j <= i:
                        continue
                    if (seg_idx[i] == seg_idx[j]
                            and abs(int(station_idx[i]) - int(station_idx[j])) <= same_chain_gap):
                        continue   # merely-adjacent stations on the same chain, already grade-clamped
                    if abs(seg_elev[i] - seg_elev[j]) < min_delta_m:
                        continue
                    dlat_m = (seg_lat[j] - seg_lat[i]) * M_PER_DEG_LAT
                    dlon_m = (seg_lon[j] - seg_lon[i]) * m_per_deg_lon
                    if math.hypot(dlat_m, dlon_m) <= radius_m:
                        conflict[i] = True
                        conflict[j] = True
    return conflict


def dilate_1d(mask, radius_stations):
    """Grow each True run in a per-segment (station-ordered) bool array by
    radius_stations on each side - the flagged station alone isn't enough
    to keep it out of a neighboring station's carve stamp (CARVE_OUTER_M in
    build_terrain_tiles.py), same reasoning as expand_carve_exclusion() but
    a fixed station-count radius instead of a fraction of run length, since
    conflict flags are typically a single isolated station, not a tagged
    span with its own meaningful length."""
    if not mask.any():
        return mask.copy()
    n = len(mask)
    out = mask.copy()
    for i in np.where(mask)[0]:
        out[max(0, i - radius_stations):min(n, i + radius_stations + 1)] = True
    return out


def fill_structure_gaps(ground, is_structure):
    """Replace DEM samples at bridge/tunnel stations with a straight-line
    interpolation between the nearest solid-ground station on each side,
    instead of trusting DEM elevation there (see is_structure_way()'s
    docstring for why that's wrong, not just noisy). Runs BEFORE
    grade_smooth_elevation() so the existing median-filter/grade-clamp
    passes smooth the corrected ramp the same way they smooth everything
    else - no separate bridge-specific smoothing needed."""
    ground = ground.copy()
    idx = np.arange(len(ground))
    good = ~is_structure
    if good.sum() < 2:
        return ground   # nothing to anchor interpolation to - leave as-is
    ground[~good] = np.interp(idx[~good], idx[good], ground[good])
    return ground


def dem_sample_sparse(tiles, lat_arr, lon_arr):
    """Bilinear sample across the sparse tile dict - same math as the old
    dem_sample()'s single-mosaic version, just resolving each point's
    containing (and neighboring, for the bilinear taps) tile from the dict
    instead of indexing one big contiguous array. Per-point Python loop
    (not vectorized) - station counts here (thousands, not millions) make
    that fine, and it avoids allocating a mostly-empty dense array sized to
    the full sparse tile-index range (a real cost at this bbox's scale)."""
    n = 256 * (2 ** DEM_ZOOM)
    lat_r = np.radians(lat_arr)
    px_glob = (lon_arr + 180.0) / 360.0 * n
    py_glob = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n

    def tile_px(tx, ty, x, y):
        t = tiles.get((tx, ty))
        return 0.0 if t is None else float(t[y, x])

    out = np.empty(len(lat_arr), dtype=np.float32)
    for i in range(len(lat_arr)):
        tx, ty = int(px_glob[i] // 256), int(py_glob[i] // 256)
        lx, ly = px_glob[i] - tx * 256, py_glob[i] - ty * 256
        x0, y0 = min(int(lx), 255), min(int(ly), 255)
        fx, fy = lx - x0, ly - y0
        tx1, x1 = (tx + 1, 0) if x0 == 255 else (tx, x0 + 1)
        ty1, y1 = (ty + 1, 0) if y0 == 255 else (ty, y0 + 1)
        v00 = tile_px(tx, ty, x0, y0)
        v01 = tile_px(tx1, ty, x1, y0)
        v10 = tile_px(tx, ty1, x0, y1)
        v11 = tile_px(tx1, ty1, x1, y1)
        out[i] = (v00 * (1 - fx) * (1 - fy) + v01 * fx * (1 - fy)
                  + v10 * (1 - fx) * fy + v11 * fx * fy)
    return out


# ---------------------------------------------------------------- OSM -> network
def is_structure_way(tags):
    """True if this OSM way is a bridge or tunnel - DEM elevation under it
    is whatever's really there (river, valley, another road), NOT deck/bore
    height, so it's systematically wrong to sample there (found 2026-07-20:
    I-5's Skagit River bridge crossing another I-5 carriageway at ground
    level - a single terrain heightmap can't represent both, and no amount
    of MAX_ERROR_NEAR/CARVE_DROP_M tuning in build_terrain_tiles.py could
    fix it, because the bug was upstream DEM data, not simplification)."""
    return tags.get("bridge") not in (None, "no") or tags.get("tunnel") not in (None, "no")


def way_name(tags):
    """Display name for a way - real street/route name if OSM has one,
    falling back to the route number (e.g. "WA-20") so an unnamed state
    route still shows something meaningful on the HUD label. Empty string
    if neither tag exists (rare for anything merge_way_chains kept)."""
    return tags.get("name") or tags.get("ref") or ""


def is_oneway_way(tags):
    """True if this OSM way only carries traffic in one direction - a
    divided highway's carriageways are separate physical OSM ways each
    tagged oneway=yes, not one way with two travel directions (2026-07-25,
    Taylor: driving north on I-5's SOUTHBOUND carriageway showed a
    double-yellow centerline, which only belongs on a genuine two-way road).
    "-1"/"reverse" both still mean one-way, just walked against the way's
    own node order - direction doesn't matter here, only that it IS one-way."""
    return tags.get("oneway") not in (None, "no", "0", "false")


def merge_way_chains(ways):
    """Chain consecutive OSM ways through degree-2 nodes into maximal
    polylines, breaking only at real junctions (degree >= 3) or dead ends
    (degree 1). Generalizes build_stage.py's build_centerline() (Dijkstra
    between two fixed endpoints, one route) into an actual road NETWORK -
    the graph itself is the same "way = edge, endpoint = node" model, just
    walked as connected chains instead of shortest-pathed. Returns a list
    of (node_id_chain, is_structure_chain, name_chain, is_oneway_chain)
    4-tuples - the 2nd array flags which nodes came from a bridge/tunnel
    way, the 3rd carries each node's source way's display name
    (way_name()), the 4th flags one-way carriageways (is_oneway_way()), all
    parallel to the first."""
    edges = []
    edge_structure = []
    edge_name = []
    edge_oneway = []
    adj = {}
    for w in ways:
        nds = w["nodes"]
        if len(nds) < 2:
            continue
        i = len(edges)
        edges.append(nds)
        edge_structure.append(is_structure_way(w.get("tags", {})))
        edge_name.append(way_name(w.get("tags", {})))
        edge_oneway.append(is_oneway_way(w.get("tags", {})))
        adj.setdefault(nds[0], []).append(i)
        if nds[-1] != nds[0]:
            adj.setdefault(nds[-1], []).append(i)
    degree = {nid: len(lst) for nid, lst in adj.items()}
    used = [False] * len(edges)

    def walk_from(i, forward):
        nds = edges[i]
        chain = list(nds) if forward else list(reversed(nds))
        struct = [edge_structure[i]] * len(nds)
        names = [edge_name[i]] * len(nds)
        oneway = [edge_oneway[i]] * len(nds)
        used[i] = True
        while True:
            tail = chain[-1]
            if degree.get(tail, 0) != 2:
                break
            candidates = [wi for wi in adj[tail] if not used[wi]]
            if len(candidates) != 1:
                break
            wi = candidates[0]
            wnds = edges[wi]
            nxt = wnds if wnds[0] == tail else list(reversed(wnds))
            if nxt[0] != tail:
                break
            used[wi] = True
            chain.extend(nxt[1:])
            struct.extend([edge_structure[wi]] * (len(nxt) - 1))
            names.extend([edge_name[wi]] * (len(nxt) - 1))
            oneway.extend([edge_oneway[wi]] * (len(nxt) - 1))
        return chain, struct, names, oneway

    chains = []
    for i, nds in enumerate(edges):
        if used[i]:
            continue
        if degree.get(nds[0], 0) != 2:
            chains.append(walk_from(i, True))
        elif degree.get(nds[-1], 0) != 2:
            chains.append(walk_from(i, False))
    for i in range(len(edges)):  # remaining: isolated loops with no junction
        if not used[i]:
            chains.append(walk_from(i, True))
    return chains


# ---------------------------------------------------------------- main build
def main():
    data = json.loads((RAW / "osm.json").read_text())
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"] if e["type"] == "way"]
    print(f"Loaded {len(ways)} ways, {len(nodes)} nodes")

    chains = merge_way_chains(ways)
    print(f"Merged into {len(chains)} chains (real junctions/dead-ends)")

    # local frame anchored at the test-area bbox center - the SAME origin
    # the old georeference actor used (test_areas.BBOX_SNWE via
    # build_terrain_tiles.py's origin_latlon), so every consumer's local
    # east/north math stays consistent with everyone else's.
    lat0 = (BBOX_SNWE[0] + BBOX_SNWE[2]) / 2
    lon0 = (BBOX_SNWE[1] + BBOX_SNWE[3]) / 2
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    scored = []
    for chain, struct, names, oneway in chains:
        latlon = np.array([nodes[n] for n in chain])
        is_struct = np.array(struct, dtype=bool)
        length = float(np.sum(np.hypot(np.diff(latlon[:, 0]) * M_PER_DEG_LAT,
                                        np.diff(latlon[:, 1]) * m_per_deg_lon)))
        scored.append((length, latlon, is_struct, names, oneway))
    scored.sort(key=lambda t: -t[0])

    # only fetch DEM tiles for chains that will actually become segments
    # (top MAX_SEGMENTS by length, above the minimum) - not every short
    # fragment/link-ramp the ref-filtered OSM fetch happened to include.
    kept = [t for t in scored
            if t[0] >= MIN_SEGMENT_STATIONS * STATION_SPACING][:MAX_SEGMENTS]
    all_lat = np.concatenate([latlon[:, 0] for _, latlon, _, _, _ in kept])
    all_lon = np.concatenate([latlon[:, 1] for _, latlon, _, _, _ in kept])
    dem_tiles = fetch_dem_tiles_sparse(all_lat, all_lon)
    n_struct_nodes = sum(int(is_struct.sum()) for _, _, is_struct, _, _ in kept)
    if n_struct_nodes:
        print(f"{n_struct_nodes} bridge/tunnel node(s) across kept chains - "
              f"DEM will be skipped and interpolated across those spans")

    segments = []
    total_km = 0.0
    for idx, (length, latlon, is_struct, names, oneway) in enumerate(kept):
        east = (latlon[:, 1] - lon0) * m_per_deg_lon
        north = (latlon[:, 0] - lat0) * M_PER_DEG_LAT
        s, e, n = resample_stations(east, north, STATION_SPACING)
        if len(s) < MIN_SEGMENT_STATIONS:
            continue
        lat_s = lat0 + n / M_PER_DEG_LAT
        lon_s = lon0 + e / m_per_deg_lon
        # same arc-length parametrization resample_stations() used
        # internally, recomputed here to carry is_struct onto the
        # resampled grid (nearest-ish via linear interp + threshold - a
        # bridge span is many stations long, so blurring its exact start/
        # end by a station or two doesn't matter).
        d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
        is_struct_s = np.interp(s, d, is_struct.astype(float)) > 0.5
        # road_name is categorical (not a 0..1 signal like is_struct), so
        # nearest-original-station lookup, not interpolation - a road label
        # doesn't need sub-station precision at a name-change boundary the
        # way elevation/carve-exclusion do. searchsorted gives the left
        # insertion index into d; the true nearest is that index or the one
        # before it, whichever's cumulative distance is closer to s.
        right = np.clip(np.searchsorted(d, s), 0, len(d) - 1)
        left = np.clip(right - 1, 0, len(d) - 1)
        nearest_orig = np.where(np.abs(d[right] - s) <= np.abs(d[left] - s), right, left)
        road_name_s = [names[j] for j in nearest_orig]
        # oneway is a discrete per-way flag (not a smooth 0..1 signal like
        # is_struct), so nearest-original-station lookup like road_name_s,
        # not interp+threshold - a carriageway doesn't partially become
        # one-way mid-station.
        is_oneway_s = [bool(oneway[j]) for j in nearest_orig]
        ground = dem_sample_sparse(dem_tiles, lat_s, lon_s)
        ground = fill_structure_gaps(ground, is_struct_s)
        h = grade_smooth_elevation(ground, STATION_SPACING, MAX_GRADE)
        name = f"seg{idx:03d}"
        segments.append({
            "name": name, "length": length, "lat_s": lat_s, "lon_s": lon_s,
            "is_struct_s": is_struct_s, "road_name_s": road_name_s,
            "is_oneway_s": is_oneway_s,
            "e": e, "n": n, "s": s, "h": h,
        })
        total_km += length / 1000.0

    if not segments:
        raise SystemExit("No segments met the minimum length - check the OSM fetch/bbox.")

    # cross-segment elevation-conflict pass (2026-07-25) - needs every
    # segment's final resampled stations first, hence deferred until after
    # the loop above instead of computed inline per-segment like is_struct_s
    # (which only ever needs its OWN chain's data). See
    # detect_elevation_conflicts()'s docstring for what this catches.
    all_lon = np.concatenate([seg["lon_s"] for seg in segments])
    all_lat = np.concatenate([seg["lat_s"] for seg in segments])
    all_elev = np.concatenate([seg["h"] for seg in segments])
    all_seg_idx = np.concatenate([np.full(len(seg["lon_s"]), i) for i, seg in enumerate(segments)])
    all_station_idx = np.concatenate([np.arange(len(seg["lon_s"])) for seg in segments])
    conflict_flat = detect_elevation_conflicts(all_lon, all_lat, all_elev, all_seg_idx, all_station_idx)
    n_conflict_stations = int(conflict_flat.sum())
    offsets = np.concatenate([[0], np.cumsum([len(seg["lon_s"]) for seg in segments])])
    if n_conflict_stations:
        print(f"{n_conflict_stations} station(s) in {len(np.unique(all_seg_idx[conflict_flat]))} "
              f"segment(s) flagged as untagged elevation conflicts (switchback/hairpin terrain) - "
              f"excluded from carving like bridges, elevation left as-is")

    out_segments = []
    for idx, seg in enumerate(segments):
        lo, hi = offsets[idx], offsets[idx + 1]
        conflict_s = dilate_1d(conflict_flat[lo:hi], CONFLICT_EXCLUDE_STATIONS)
        # expanded separately from is_struct_s (used above for elevation -
        # every bridge/tunnel station's elev_m stays interpolated over just
        # the real tagged span) - only the carve-exclusion flag written out
        # below grows past it, see expand_carve_exclusion()'s docstring.
        # conflict_s is OR'd in AFTER expansion, not before - it's a
        # different mechanism (fixed station-count dilation, not
        # buffer_frac-of-run-length) and must never feed fill_structure_gaps
        # (these stations' elev_m is already correct real DEM elevation).
        is_bridge_out = expand_carve_exclusion(seg["is_struct_s"], CARVE_BUFFER_FRAC) | conflict_s
        out_segments.append({
            "name": seg["name"],
            "lat": [round(float(v), 7) for v in seg["lat_s"]],
            "lon": [round(float(v), 7) for v in seg["lon_s"]],
            "is_bridge": [bool(v) for v in is_bridge_out],
            "road_name": seg["road_name_s"],
            "oneway": seg["is_oneway_s"],
            "east_m": [round(float(v), 2) for v in seg["e"]],
            "north_m": [round(float(v), 2) for v in seg["n"]],
            "s_m": [round(float(v), 2) for v in seg["s"]],
            "elev_m": [round(float(v), 2) for v in seg["h"]],
        })
        n_bridge = int(np.sum(seg["is_struct_s"]))
        n_carve_excluded = int(np.sum(is_bridge_out))
        bridge_note = (f", {n_bridge} bridge/tunnel station(s), "
                        f"{n_carve_excluded} excluded from carving (buffer expanded)"
                        if n_bridge else "")
        print(f"  {seg['name']}: {seg['length'] / 1000:.2f} km, {len(seg['s'])} station(s){bridge_note}")
    segments = out_segments

    out = {
        "area": AREA_NAME,
        "origin_latlon": [lat0, lon0],
        "segments": segments,
    }
    (OUT / "road_stations.json").write_text(json.dumps(out))
    print(f"{len(segments)} segments, {total_km:.2f} km total -> {OUT / 'road_stations.json'}")


if __name__ == "__main__":
    main()
