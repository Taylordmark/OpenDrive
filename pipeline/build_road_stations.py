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
         "is_tunnel": [...],  # bool per station (2026-07-28, AUDIT.md item 3)
                               # - the tunnel subset of is_bridge's "structure"
                               # bit, kept separate so a consumer can tell a
                               # tunnel (no pavement should be drawn through
                               # solid rock) apart from a bridge (should draw
                               # an elevated deck). build_road_network.py uses
                               # this to truncate the visible road ribbon at
                               # each portal while elev_m stays populated for
                               # a future real-bore pass.
         "road_name": [...],  # display name per station (2026-07-23, Arc
                               # "place labels" feature) - the source way's
                               # OSM name tag, falling back to ref (e.g.
                               # "WA-20") - nearest-original-station lookup,
                               # not interpolated (see main()).
         "highway_class": [...],  # OSM highway= value per station
                               # (2026-07-28) - "" if the way had no highway
                               # tag. Nearest-original-station lookup, same
                               # as road_name. Drives per-class grade/grade-
                               # change limits in road_mesh_common's
                               # ROAD_CLASS_GRADE; kept in the output too so
                               # downstream consumers (speed limits, HUD)
                               # aren't stuck re-deriving it from raw OSM.
         "east_m": [...], "north_m": [...], "s_m": [...],
         "elev_m": [...],  # grade-CONFORMING absolute DEM elevation (rides
                             # the real ground; only deviates via cut/bridge/
                             # tunnel where max_grade forces it to - never
                             # terrain-smoothed) or interpolated, for
                             # is_bridge stations - NOT yet shifted by
                             # ROAD_LIFT or ORIGIN_HEIGHT_M (that's a UE-
                             # frame concern, applied by consumers via
                             # road_mesh_common.road_ue_z_m()). ALREADY
                             # flattened toward any real graph-junction's
                             # shared elevation the station is within the
                             # influence radius of (see junction_suppress).
         "junction_suppress": [...],  # 0..1 per station (2026-09-04, Arc
                             # 7.17) - 1.0 deep inside a real junction's flush
                             # box, smoothstep-easing to 0.0 over the
                             # JUNCTION_APPROACH_M blend zone, 0.0 elsewhere.
                             # Threaded through build_road_network.py into
                             # road_mesh_common.build_road_chunks()'s
                             # junction_suppress param -> ODR3 uv1.y, which
                             # unreal/apply_road_material.py reads to stop
                             # painting lane markings through the junction.
         "in_junction_box": [...]}  # bool per station (2026-09-04) - hard
                             # "inside the flush box" flag (no approach-zone
                             # blend); build_road_guardrails.py/build_road_
                             # barrier.py can use this to skip placing rail/
                             # barrier through open junction pavement.
      ]
    }

Geographic/DEM-sampling logic (chain merging, DEM fetch, grade smoothing)
moved here from build_road_network.py, which now only does UE-frame mesh
assembly from this file's output. DEM fetch itself is road-gated (sparse
per-tile cache near actual road nodes, 2026-07-20), not one mosaic over the
whole bbox - see fetch_dem_tiles_sparse()'s docstring for why that stopped
being viable once test areas grew past Palmdale's compact scale.
"""

import hashlib
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

from test_areas import AREA_NAME, BBOX_SNWE
from terrain_common import fetch_terrarium_tile, lonlat_to_tilepx, terrarium_to_elevation
from road_mesh_common import (STATION_SPACING, resample_stations,
                               grade_smooth_elevation, road_class_grade_arrays,
                               despike_ground, clamp_plausible_elevation,
                               despike_road_profile,
                               classify_wall_stations, reconcile_junction_elevations,
                               ROAD_HALF_WIDTH, JUNCTION_BOX_MARGIN_M, JUNCTION_APPROACH_M,
                               JUNCTION_MAX_DISAGREEMENT_M)

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
# CONFLICT_RADIUS_M/CONFLICT_MAX_GRADE/CONFLICT_MIN_GAP_M/
# CONFLICT_SAME_CHAIN_GAP_STATIONS now live in road_mesh_common.py as the
# single canonical set (2026-07-30 consolidation - see
# classify_wall_stations()'s docstring), not duplicated here.
CONFLICT_EXCLUDE_STATIONS = 8      # dilate each flagged station's carve-exclusion
                                  # this many stations each way along its OWN
                                  # chain (~40m at 5m spacing) so the excluded
                                  # zone doesn't end on a single hard station -
                                  # a distinct, along-chain-index concept from
                                  # the spatial CONFLICT_RADIUS_M above, not
                                  # meant to track its value

# AUDIT.md item 3 (2026-07-28, bridges/tunnels): a plausibility floor, not
# derived from any specific vertical-clearance code - just enough that a
# bridge visibly clears the ground it crosses instead of grazing it.
MIN_BRIDGE_CLEARANCE_M = 4.0
# Generous vs MAX_GRADE (0.10) on purpose - same reasoning as
# CONFLICT_MAX_GRADE below: only flag a structure span too steep for ANY
# plausible real bridge/tunnel, not just an unusually steep one. This is a
# WARNING, not a rejection - a real OSM road never gets deleted for being
# steep, see the 2026-07-28 conversation for why.
STRUCTURE_GRADE_WARN = 0.15

M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / f"{AREA_NAME}_test"
OUT = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- DEM
DEM_NODE_MARGIN_DEG = 0.02   # ~2 km - buffer around actual road nodes, not the whole bbox

# AUDIT-RELIABILITY.md SS3.1 (2026-08-21): dem_tiles.npz used to be keyed on
# existence only - re-fetching OSM (different node set), tweaking BBOX_SNWE,
# or changing DEM_ZOOM/DEM_NODE_MARGIN_DEG all still served the same stale
# tile dict with no invalidation, and any lat/lon that fell outside what was
# actually cached silently read back as 0 m via dem_sample_sparse's own
# tile_px() fallback - new roads at sea level with no error. A fingerprint
# of everything that determines which tiles get fetched (and from where) is
# stored alongside the cache; a mismatch on load means "this cache was built
# for a different request" and forces a re-fetch. Follows generation_id.py's
# sha256-of-repr()-truncated-to-16-hex convention for consistency with the
# project's other cache/staleness stamps.
DEM_CACHE_FP_KEY = "__fingerprint__"


def _dem_cache_fingerprint(lat_arr, lon_arr):
    """Fingerprint over everything that determines which DEM tiles
    fetch_dem_tiles_sparse fetches and how it fetches them: the zoom, the
    node margin, the configured bbox, and the actual requested node lat/lon
    (which already reflect the current OSM fetch - a re-fetch that changes
    the node set changes this too, even if BBOX_SNWE itself didn't move).
    Rounded to ~0.1 m (1e-6 deg) to strip float round-trip noise, not
    because sub-metre precision matters here."""
    h = hashlib.sha256()
    h.update(repr((DEM_ZOOM, DEM_NODE_MARGIN_DEG, BBOX_SNWE)).encode("utf-8"))
    lat_r = [round(float(v), 6) for v in lat_arr]
    lon_r = [round(float(v), 6) for v in lon_arr]
    h.update(repr(lat_r).encode("utf-8"))
    h.update(repr(lon_r).encode("utf-8"))
    return h.hexdigest()[:16]


def fetch_dem_tiles_sparse(lat_arr, lon_arr):
    """Sparse per-tile DEM cache, gated to tiles near actual road geometry
    - NOT one mosaic covering the whole bbox (2026-07-20: the Skagit/
    Cascades loop's own extent is nearly as big as the whole test bbox, a
    wide loop through 3 towns rather than a compact area like Palmdale, so
    the original single-rectangle-mosaic approach meant ~6000 tile fetches
    including everything between the loop's legs - same class of problem
    build_terrain_tiles.py's road_near_tile() already solved for z15
    terrain, applied here to the DEM fetch instead). Returns a dict keyed
    by (tx, ty) -> 256x256 float32 elevation array.

    Cache-invalidated on bbox/zoom/margin/node-set change - see
    _dem_cache_fingerprint()'s docstring (AUDIT-RELIABILITY.md SS3.1)."""
    cache_path = RAW / "dem_tiles.npz"
    fp = _dem_cache_fingerprint(lat_arr, lon_arr)
    if cache_path.exists():
        with np.load(cache_path) as z:
            stored_fp = str(z[DEM_CACHE_FP_KEY]) if DEM_CACHE_FP_KEY in z.files else None
            if stored_fp == fp:
                return {tuple(int(v) for v in k.split(",")): z[k]
                        for k in z.files if k != DEM_CACHE_FP_KEY}
            print(f"dem_tiles.npz fingerprint mismatch (stored={stored_fp!r}, "
                  f"current={fp!r}) - bbox/zoom/margin/OSM node set changed "
                  f"since this cache was built; re-fetching instead of "
                  f"serving stale tiles (AUDIT-RELIABILITY.md SS3.1)")

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
    np.savez(cache_path, **{DEM_CACHE_FP_KEY: np.array(fp)},
             **{f"{tx},{ty}": v for (tx, ty), v in tiles.items()})
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


# detect_elevation_conflicts used to live here (lon/lat frame, 40m/flat-5m-
# delta threshold) as an independent, differently-parameterized twin of
# build_terrain_mesh_tiles.py's own local-meter/grade-based version - the two
# disagreed with each other by construction. Both are now
# road_mesh_common.classify_wall_stations() (single canonical
# implementation, called here AND by build_terrain_mesh_tiles.py against the
# same final station data) - see that function's docstring.


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
    docstring for why that's wrong, not just noisy).

    Called TWICE by main() (2026-07-28, AUDIT.md item 3): once on raw DEM
    ground as a placeholder BEFORE grade_smooth_elevation(), purely so that
    function's median-filter/gaussian windows don't ingest raw under-
    structure DEM noise (river/valley/another-road elevation) near a span's
    edges; then AGAIN on the smoothed result, to recompute the structure
    span from the FINAL smoothed abutment elevations. The second call is
    the one that matters - it overwrites whatever the median filter and
    grade clamp did to the first call's placeholder ramp, since those passes
    have no idea a bridge deck's climb onto its abutment is deliberate and
    will otherwise chop it back down toward the surrounding low ground
    (measured: this, not the interpolation itself, is why 39% of H-3 used to
    sit within a metre of the dirt)."""
    ground = ground.copy()
    idx = np.arange(len(ground))
    good = ~is_structure
    if good.sum() < 2:
        return ground   # nothing to anchor interpolation to - leave as-is
    ground[~good] = np.interp(idx[~good], idx[good], ground[good])
    return ground


def _contiguous_true_runs(mask):
    """Yields (first_idx, last_idx) - inclusive - for each contiguous True
    run in a bool array. Used to find each bridge/tunnel span's abutment/
    portal station pair for the grade sanity check."""
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return
    breaks = np.where(np.diff(idx) > 1)[0]
    starts = np.concatenate([[0], breaks + 1])
    ends = np.concatenate([breaks, [len(idx) - 1]])
    for a, b in zip(starts, ends):
        yield int(idx[a]), int(idx[b])


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


def is_tunnel_way(tags):
    """True for the tunnel subset of is_structure_way() - kept as its own
    flag (2026-07-28, AUDIT.md item 3) so downstream consumers can tell a
    tunnel (no pavement should be drawn through solid rock - the terrain
    isn't carved for it either) apart from a bridge (should draw an
    elevated deck). Both used to collapse into one "structure" bit, which
    meant nothing downstream could treat them differently."""
    return tags.get("tunnel") not in (None, "no")


def way_name(tags):
    """Display name for a way - real street/route name if OSM has one,
    falling back to the route number (e.g. "WA-20") so an unnamed state
    route still shows something meaningful on the HUD label. Empty string
    if neither tag exists (rare for anything merge_way_chains kept)."""
    return tags.get("name") or tags.get("ref") or ""


def highway_class(tags):
    """This way's OSM highway= value ("motorway", "residential", ...) -
    road_mesh_common.ROAD_CLASS_GRADE keys off this to pick per-class grade/
    grade-change limits (2026-07-28: fast roads need a gentler grade AND a
    much gentler grade-change than slow ones). Empty string (falls back to
    DEFAULT_ROAD_CLASS_GRADE) if the way has no highway tag at all."""
    return tags.get("highway") or ""


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
    of (node_id_chain, is_structure_chain, is_tunnel_chain, name_chain,
    is_oneway_chain, highway_chain) 6-tuples - the 2nd array flags which
    nodes came from a bridge/tunnel way, the 3rd flags the tunnel subset
    specifically (see is_tunnel_way()), the 4th carries each node's source
    way's display name (way_name()), the 5th flags one-way carriageways
    (is_oneway_way()), the 6th carries each node's source way's OSM
    highway= class (highway_class()) - used to pick per-class grade/grade-
    change limits, not thrown away even though it isn't rendered directly -
    all parallel to the first.

    ALSO returns `degree` (2026-09-04, Arc 7.17 junction-unification fix -
    see road_mesh_common.py's "junction unification" section): the node id
    -> incident-way-count dict this function already computes to decide
    where to break a chain. A node with degree>=3 is a REAL junction (3+
    road edges meet, or two roads cross/join at a shared node) by exactly
    the same rule this function already uses to break a chain there; a
    degree-2 node is always a same-road continuation (this function chains
    straight through it) and must never be flagged as a junction. main()
    below uses this to build the `junctions` list build_road_chunks()/
    flatten_junction_elevations() consume."""
    edges = []
    edge_structure = []
    edge_tunnel = []
    edge_name = []
    edge_oneway = []
    edge_highway = []
    adj = {}
    for w in ways:
        nds = w["nodes"]
        if len(nds) < 2:
            continue
        i = len(edges)
        edges.append(nds)
        edge_structure.append(is_structure_way(w.get("tags", {})))
        edge_tunnel.append(is_tunnel_way(w.get("tags", {})))
        edge_name.append(way_name(w.get("tags", {})))
        edge_oneway.append(is_oneway_way(w.get("tags", {})))
        edge_highway.append(highway_class(w.get("tags", {})))
        adj.setdefault(nds[0], []).append(i)
        if nds[-1] != nds[0]:
            adj.setdefault(nds[-1], []).append(i)
    degree = {nid: len(lst) for nid, lst in adj.items()}
    used = [False] * len(edges)

    def walk_from(i, forward):
        nds = edges[i]
        chain = list(nds) if forward else list(reversed(nds))
        struct = [edge_structure[i]] * len(nds)
        tunnel = [edge_tunnel[i]] * len(nds)
        names = [edge_name[i]] * len(nds)
        oneway = [edge_oneway[i]] * len(nds)
        highway = [edge_highway[i]] * len(nds)
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
            tunnel.extend([edge_tunnel[wi]] * (len(nxt) - 1))
            names.extend([edge_name[wi]] * (len(nxt) - 1))
            oneway.extend([edge_oneway[wi]] * (len(nxt) - 1))
            highway.extend([edge_highway[wi]] * (len(nxt) - 1))
        return chain, struct, tunnel, names, oneway, highway

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
    return chains, degree


# ---------------------------------------------------------------- main build
def main():
    data = json.loads((RAW / "osm.json").read_text())
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"] if e["type"] == "way"]
    print(f"Loaded {len(ways)} ways, {len(nodes)} nodes")

    chains, node_degree = merge_way_chains(ways)
    print(f"Merged into {len(chains)} chains (real junctions/dead-ends)")

    # local frame anchored at the test-area bbox center - the SAME origin
    # the old georeference actor used (test_areas.BBOX_SNWE via
    # build_terrain_tiles.py's origin_latlon), so every consumer's local
    # east/north math stays consistent with everyone else's.
    lat0 = (BBOX_SNWE[0] + BBOX_SNWE[2]) / 2
    lon0 = (BBOX_SNWE[1] + BBOX_SNWE[3]) / 2
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    scored = []
    for chain, struct, tunnel, names, oneway, highway in chains:
        latlon = np.array([nodes[n] for n in chain])
        is_struct = np.array(struct, dtype=bool)
        is_tunnel = np.array(tunnel, dtype=bool)
        length = float(np.sum(np.hypot(np.diff(latlon[:, 0]) * M_PER_DEG_LAT,
                                        np.diff(latlon[:, 1]) * m_per_deg_lon)))
        # chain[0]/chain[-1] (2026-09-04, Arc 7.17): the OSM node ids this
        # chain starts/ends at - kept through scored/kept below so main()'s
        # junction pass can tell exactly which real junction node (if any,
        # via node_degree) each KEPT segment's own two ends touch, with no
        # geometric guessing (see merge_way_chains()'s docstring).
        scored.append((length, latlon, is_struct, is_tunnel, names, oneway, highway,
                        chain[0], chain[-1]))
    scored.sort(key=lambda t: -t[0])

    # only fetch DEM tiles for chains that will actually become segments
    # (top MAX_SEGMENTS by length, above the minimum) - not every short
    # fragment/link-ramp the ref-filtered OSM fetch happened to include.
    kept = [t for t in scored
            if t[0] >= MIN_SEGMENT_STATIONS * STATION_SPACING][:MAX_SEGMENTS]
    all_lat = np.concatenate([latlon[:, 0] for _, latlon, _, _, _, _, _, _, _ in kept])
    all_lon = np.concatenate([latlon[:, 1] for _, latlon, _, _, _, _, _, _, _ in kept])
    dem_tiles = fetch_dem_tiles_sparse(all_lat, all_lon)
    n_struct_nodes = sum(int(is_struct.sum()) for _, _, is_struct, _, _, _, _, _, _ in kept)
    if n_struct_nodes:
        print(f"{n_struct_nodes} bridge/tunnel node(s) across kept chains - "
              f"DEM will be skipped and interpolated across those spans")

    segments = []
    total_km = 0.0
    n_grade_warnings = 0
    n_spike_repairs = 0
    for idx, (length, latlon, is_struct, is_tunnel, names, oneway, highway,
              node_start, node_end) in enumerate(kept):
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
        is_tunnel_s = np.interp(s, d, is_tunnel.astype(float)) > 0.5
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
        # highway class is categorical too (nearest-station lookup, same as
        # road_name_s/is_oneway_s) - feeds road_class_grade_arrays() below
        # so grade/grade-change limits track the real OSM road class instead
        # of one flat constant for every road.
        highway_s = [highway[j] for j in nearest_orig]
        max_grade_s, max_grade_delta_s = road_class_grade_arrays(highway_s)

        raw_ground = dem_sample_sparse(dem_tiles, lat_s, lon_s)
        # 2026-07-29: two independent guards against corrupt Terrarium DEM
        # data before anything downstream - including fill_structure_gaps()'s
        # solid-ground anchors and the bridge clearance floor below - trusts
        # raw_ground. Order matters: clamp the impossible-range case first
        # (a sustained bad tile stretch, which despike can't see because
        # nothing "bounces back") so despike's own neighbor comparisons
        # aren't themselves poisoned by an unclamped extreme value. See
        # ELEV_PLAUSIBLE_MIN_M/MAX_M and DEM_SPIKE_THRESHOLD_M in
        # road_mesh_common.py.
        raw_ground = clamp_plausible_elevation(raw_ground)
        raw_ground = despike_ground(raw_ground)
        # AUDIT.md item 3 (2026-07-28): placeholder-fill first so
        # grade_smooth_elevation's envelope clamp near a span's edges
        # doesn't ingest raw under-structure DEM noise, THEN re-fill from
        # the final elevation-conforming abutment elevations, overwriting
        # whatever the clamp did inside the span - see fill_structure_gaps()'s
        # docstring for why calling it only once (before the clamp, as this
        # used to) let the deck sag toward the ground it should clear.
        placeholder = fill_structure_gaps(raw_ground, is_struct_s)
        # OD_RAW_ROAD_STATIONS=1 (Taylor, 2026-08-24 diagnostic pass): skip
        # grade_smooth_elevation entirely and keep the DEM-sampled elevation
        # (bridge/tunnel gap-filled only) so the road mesh shows exactly
        # what the raw per-station elevation profile looks like, unsmoothed -
        # temporary debug toggle, not meant to stay on for normal rebuilds.
        if os.environ.get("OD_RAW_ROAD_STATIONS") == "1":
            h = placeholder
        else:
            h = grade_smooth_elevation(placeholder, STATION_SPACING, max_grade_s, max_grade_delta_s)
        h = fill_structure_gaps(h, is_struct_s)

        # Minimum-clearance floor, bridges only - tunnels belong at/below
        # grade, lifting one would be exactly backwards. Safety net on top
        # of the reorder above: a straight abutment-to-abutment line should
        # already clear a normal valley by construction, but a valley that
        # bulges up mid-span, or a badly-anchored abutment, could still
        # leave the deck closer to the ground than a real bridge would be.
        is_bridge_only_s = is_struct_s & ~is_tunnel_s
        if is_bridge_only_s.any():
            clearance_floor = raw_ground + MIN_BRIDGE_CLEARANCE_M
            h = np.where(is_bridge_only_s, np.maximum(h, clearance_floor), h)

        # Final despike pass (2026-08-27, Lake Hughes Road "vertical wall"
        # bug - see despike_road_profile()'s docstring in road_mesh_common.py
        # for the full root-cause writeup): the clearance floor just above
        # (and, more rarely, a residual DEM/interpolation artifact) can jump
        # a short run of stations straight up/down with no grade limiting at
        # all, bypassing every check that ran earlier in this function. Runs
        # LAST, after every other elevation step, so it sees exactly the
        # profile that's about to ship - repairing narrow bounce-back spikes
        # by interpolation without touching any real sustained climb.
        h, spike_repairs = despike_road_profile(h, s)
        if spike_repairs:
            n_spike_repairs += len(spike_repairs)
            for r in spike_repairs:
                si = r["station_idx"]
                rn = road_name_s[si] if si < len(road_name_s) else "?"
                print(f"  SPIKE REPAIR: seg{idx:03d} '{rn}' station {si}: "
                      f"{r['before_m']:.2f}m -> {r['after_m']:.2f}m")

        # Log-only grade sanity check (2026-07-28): an abutment-to-abutment
        # (or portal-to-portal) grade steeper than any plausible real
        # structure usually means a mis-tagged way or a bad abutment pick,
        # not a real bridge/tunnel that just happens to be steep - worth a
        # human look, but never a reason to delete a real OSM road.
        for run_lo, run_hi in _contiguous_true_runs(is_struct_s):
            span_m = s[run_hi] - s[run_lo]
            if span_m <= 0:
                continue
            grade = abs(h[run_hi] - h[run_lo]) / span_m
            if grade > STRUCTURE_GRADE_WARN:
                n_grade_warnings += 1
                kind = "tunnel" if is_tunnel_s[run_lo] else "bridge"
                print(f"  WARNING: seg{idx:03d} {kind} span [{run_lo}:{run_hi}] "
                      f"({span_m:.0f} m) grade {grade * 100:.1f}% exceeds "
                      f"{STRUCTURE_GRADE_WARN * 100:.0f}% sanity ceiling - "
                      f"check the OSM tag/abutment detection")

        name = f"seg{idx:03d}"
        segments.append({
            "name": name, "length": length, "lat_s": lat_s, "lon_s": lon_s,
            "is_struct_s": is_struct_s, "is_tunnel_s": is_tunnel_s,
            "road_name_s": road_name_s,
            "is_oneway_s": is_oneway_s,
            "highway_s": highway_s,
            "e": e, "n": n, "s": s, "h": h,
            "max_grade_s": max_grade_s, "max_grade_delta_s": max_grade_delta_s,
            "node_start": node_start, "node_end": node_end,
        })
        total_km += length / 1000.0

    if not segments:
        raise SystemExit("No segments met the minimum length - check the OSM fetch/bbox.")
    if n_grade_warnings:
        print(f"{n_grade_warnings} structure span(s) exceeded the "
              f"{STRUCTURE_GRADE_WARN * 100:.0f}% grade sanity ceiling (see WARNING lines above)")
    if n_spike_repairs:
        print(f"{n_spike_repairs} station(s) despiked by the final grade-spike "
              f"repair pass (see SPIKE REPAIR lines above)")

    # cross-segment elevation-conflict pass (2026-07-25) - needs every
    # segment's final resampled stations first, hence deferred until after
    # the loop above instead of computed inline per-segment like is_struct_s
    # (which only ever needs its OWN chain's data). See
    # road_mesh_common.classify_wall_stations()'s docstring for what this catches.
    all_east = np.concatenate([seg["e"] for seg in segments])
    all_north = np.concatenate([seg["n"] for seg in segments])
    all_elev = np.concatenate([seg["h"] for seg in segments])
    all_seg_idx = np.concatenate([np.full(len(seg["e"]), i) for i, seg in enumerate(segments)])
    all_station_idx = np.concatenate([np.arange(len(seg["e"])) for seg in segments])
    all_road_name = [name for seg in segments for name in seg["road_name_s"]]
    # exclude here folds BOTH wall-eligible (in-scope, steep) and out-of-scope
    # (e.g. freeway/ramp) conflicts into is_bridge-style carve exclusion -
    # this file doesn't build wall geometry itself (that's
    # build_retaining_walls.py, run later against this script's own output),
    # it only needs to know which stations the terrain carve must skip.
    conflict_flat, _wall_pairs_unused = classify_wall_stations(
        all_east, all_north, all_elev, all_seg_idx, all_station_idx, all_road_name)
    n_conflict_stations = int(conflict_flat.sum())
    offsets = np.concatenate([[0], np.cumsum([len(seg["lon_s"]) for seg in segments])])
    if n_conflict_stations:
        print(f"{n_conflict_stations} station(s) in {len(np.unique(all_seg_idx[conflict_flat]))} "
              f"segment(s) flagged as untagged elevation conflicts (switchback/hairpin terrain) - "
              f"excluded from carving like bridges, elevation left as-is")

    # Junction smoothing pass (2026-08-24, Taylor: "start at the highest
    # point and work our way down making sure they connect" - see
    # road_mesh_common.reconcile_junction_elevations()'s docstring). Runs on
    # every rebake automatically, no separate stage needed - snaps the lower
    # station of each disagreeing at-grade road-road pair up to match the
    # higher one, then re-smooths just the touched segment(s) so the snap
    # becomes a rate-limited approach ramp instead of a new vertical kink.
    #
    # Position alone isn't enough (same date, Taylor: "cant be two sections
    # of road on the same xyz axis with different slopes... after departing
    # the road width they can SLOWLY start changing gradient") - pinned_grade
    # carries the higher-priority side's own local slope at each seam, which
    # gets threaded into grade_smooth_elevation as pinned_edge_grades so the
    # rate limiter eases away from a MATCHED slope instead of a free one.
    # is_struct_s (bridge OR tunnel - see is_structure_way()) is passed as
    # is_bridge so a structure station's DEM-independent, span-interpolated
    # elevation never gets matched by XY proximity against an unrelated
    # same-named-road station elsewhere in the network (2026-09-04 - see
    # reconcile_junction_elevations()'s is_bridge docstring; found via the
    # same 51-jump bridge-adjacent survey that motivated the graph-junction
    # pass's own structure exclusion below).
    all_is_struct = np.concatenate([seg["is_struct_s"] for seg in segments])
    reconciled_elev, touched, pinned_grade = reconcile_junction_elevations(
        all_east, all_north, all_elev, all_seg_idx, all_station_idx, all_road_name,
        is_bridge=all_is_struct)
    n_touched = int(touched.sum())
    if n_touched and os.environ.get("OD_RAW_ROAD_STATIONS") == "1":
        print(f"OD_RAW_ROAD_STATIONS=1: skipping the junction re-smoothing pass "
              f"({n_touched} station(s) would otherwise have been touched)")
        n_touched = 0
    if n_touched:
        touched_segs = sorted(set(all_seg_idx[touched].tolist()))
        print(f"{n_touched} station(s) across {len(touched_segs)} segment(s) snapped to a "
              f"higher-priority junction elevation - re-smoothing those segment(s)")
        for idx in touched_segs:
            seg = segments[idx]
            lo, hi = offsets[idx], offsets[idx + 1]
            local_touched = np.nonzero(touched[lo:hi])[0]
            # both edges adjacent to the touched station get pinned to the
            # same grade - a straight-through slope at the seam, not a peak
            # or valley there - letting pass 2's own rate limiter ease each
            # side back to the road's natural profile a few stations out.
            pinned_edges = {}
            for local_i in local_touched.tolist():
                g = float(pinned_grade[lo + local_i])
                pinned_edges[local_i - 1] = g
                pinned_edges[local_i] = g
            seg["h"] = grade_smooth_elevation(
                reconciled_elev[lo:hi], STATION_SPACING,
                seg["max_grade_s"], seg["max_grade_delta_s"],
                pinned_edge_grades=pinned_edges)

    # Real (graph) junction detection + flatten (2026-09-04, Arc 7.17 - Taylor's
    # Blue River Parkway/Silverthorne screenshot; see next steps.md SS7.17).
    # The pass above only ever reconciles a road against ITSELF (same seg_idx
    # or same road_name) - a real intersection of two DIFFERENTLY NAMED roads
    # never enters it at all, which is exactly the reported bug (crossing
    # roads baked as independently-stacked ribbons, lane markings running
    # straight through). Junction NODES come straight from merge_way_chains()'s
    # own OSM graph degree (node_degree, returned above) - a node with 3+
    # incident ways is a real junction by the exact same rule that function
    # already uses to break a chain there; a degree-2 node is always a same-
    # road continuation and is never flagged. Each KEPT segment's chain-start/
    # chain-end node id (node_start/node_end, carried through since the
    # `scored`/`kept` loop above) is exactly the node its chain broke at, so
    # matching those against node_degree finds every real junction a baked
    # segment actually touches with no geometric/proximity guessing.
    current_elev = np.concatenate([seg["h"] for seg in segments])
    # Bridge/tunnel endpoints are excluded from junction participation
    # entirely (2026-09-04) - caught via a 51-jump, all-bridge-adjacent
    # survey after the per-segment flatten fix above: a bridge deck's
    # endpoint elevation is structurally pinned by its own span
    # interpolation (fill_structure_gaps, is_struct_s), and forcing it to
    # blend toward an unrelated surface road's target elevation over just
    # JUNCTION_APPROACH_M produced implausible near-vertical jumps (up to
    # 34 m over 20 m of arc-length at seg208, an overpass). A bridge ramp
    # meeting a graph node of degree>=3 is not the flush-pavement,
    # shared-grade intersection this pass is meant to flatten.
    junction_incident = defaultdict(list)   # node_id -> [(seg_idx, 'start'|'end')]
    for seg_i, seg in enumerate(segments):
        is_struct_s = seg["is_struct_s"]
        start_ok = not (len(is_struct_s) and is_struct_s[0])
        end_ok = not (len(is_struct_s) and is_struct_s[-1])
        if start_ok and node_degree.get(seg["node_start"], 0) >= 3:
            junction_incident[seg["node_start"]].append((seg_i, "start"))
        if end_ok and node_degree.get(seg["node_end"], 0) >= 3:
            junction_incident[seg["node_end"]].append((seg_i, "end"))

    junctions = []
    junction_target_elev = {}   # node_id -> target elevation, for the flatten pass below
    for node_id, incident in junction_incident.items():
        if len(incident) < 2:
            # only one KEPT segment actually reaches this junction node (the
            # other incident way(s) were too short/filtered by MAX_SEGMENTS
            # or MIN_SEGMENT_STATIONS) - nothing to unify against.
            continue
        lat_n, lon_n = nodes[node_id]
        ej = (lon_n - lon0) * m_per_deg_lon
        nj = (lat_n - lat0) * M_PER_DEG_LAT
        elevs = []
        for seg_i, end in incident:
            lo, hi = offsets[seg_i], offsets[seg_i + 1]
            elevs.append(current_elev[lo if end == "start" else hi - 1])
        # Outlier exclusion (2026-09-04, bug #3 - see JUNCTION_MAX_
        # DISAGREEMENT_M's docstring): a participant stranded more than that
        # far from the REST of this node's incident elevations (median,
        # robust to a single bad outlier the way max()/mean() aren't) isn't
        # part of a real flush intersection - drop it from this node
        # entirely rather than let it drag the others toward it (or get
        # dragged toward them into a fake vertical transition itself).
        median_elev = float(np.median(elevs))
        ok = [(seg_i, end, z) for (seg_i, end), z in zip(incident, elevs)
              if abs(z - median_elev) <= JUNCTION_MAX_DISAGREEMENT_M]
        incident_ok = [(seg_i, end) for seg_i, end, z in ok]
        junction_incident[node_id] = incident_ok
        if len(incident_ok) < 2:
            # every other participant got excluded (or there was only ever
            # one plausible one) - nothing left to unify, same as a node
            # with <2 incident segments to begin with.
            continue
        elevs_ok = [z for seg_i, end, z in ok]
        # highest incident approach wins (same "never lower a station, only
        # raise" doctrine reconcile_junction_elevations() above already
        # uses) - every other road meeting here blends UP to it, never down.
        target_elev = float(max(elevs_ok))
        junction_target_elev[node_id] = target_elev
        junctions.append({"east_m": float(ej), "north_m": float(nj),
                           "elev_m": target_elev, "num_roads": len(incident_ok)})

    n_junctions = len(junctions)
    if n_junctions:
        box_r = ROAD_HALF_WIDTH + JUNCTION_BOX_MARGIN_M
        print(f"{n_junctions} real junction node(s) detected (3+ incident roads, graph "
              f"degree) - flattening pavement elevation within {box_r:.1f} m of each "
              f"junction center, blending back over {JUNCTION_APPROACH_M:.0f} m")
        # Per-SEGMENT, arc-length-restricted blend (2026-09-04 bugfix - first
        # cut of this pass called road_mesh_common.flatten_junction_elevations()
        # network-wide, matching every station to its nearest junction by raw
        # XY distance regardless of which road it's actually on. That's wrong
        # whenever a DIFFERENT, unrelated road happens to pass within the
        # ~28m box+approach radius in plan view but sits at a very different
        # elevation - a switchback stacked over itself or an overpass, exactly
        # what classify_wall_stations()/reconcile_junction_elevations() above
        # already exist to detect and exclude. Caught live: verify_terrain_
        # fit.py's PRE-BAKE CONSISTENCY CHECK failed with a 108.91 m max
        # deviation - a station on some other road got yanked to a nearby
        # junction's target elevation. Fixed by only ever touching a
        # junction's own KNOWN participant segments (junction_incident,
        # already resolved via the graph, not proximity), blended along that
        # segment's OWN arc-length distance from its OWN endpoint - so a
        # station can only be pulled toward a junction its road structurally
        # connects to, never one it merely passes near in plan view.
        final_elev = current_elev.copy()
        best_w = np.ones(len(current_elev))   # 1.0 = untouched; smaller = more pulled
        for node_id, incident in junction_incident.items():
            if len(incident) < 2:
                continue
            target = junction_target_elev[node_id]
            for seg_i, end in incident:
                lo, hi = offsets[seg_i], offsets[seg_i + 1]
                s_seg = np.asarray(segments[seg_i]["s"], dtype=np.float64)
                d = (s_seg - s_seg[0]) if end == "start" else (s_seg[-1] - s_seg)
                w = np.ones_like(d)
                w[d <= box_r] = 0.0
                ease = (d > box_r) & (d <= box_r + JUNCTION_APPROACH_M)
                t = (d[ease] - box_r) / JUNCTION_APPROACH_M
                w[ease] = t * t * (3.0 - 2.0 * t)
                seg_slice = best_w[lo:hi]
                pull_here = w < seg_slice   # this junction pulls harder than
                                             # whatever (if anything) already
                                             # claimed this station
                seg_slice[pull_here] = w[pull_here]
                best_w[lo:hi] = seg_slice
                final_elev[lo:hi] = np.where(
                    pull_here, target * (1.0 - w) + current_elev[lo:hi] * w, final_elev[lo:hi])
        all_junction_suppress_elev = 1.0 - best_w   # kept for the print below only
    else:
        print("0 real junction nodes detected (3+ incident roads) - no elevation flattening applied")
        final_elev = current_elev
        best_w = np.ones(len(current_elev))

    for idx, seg in enumerate(segments):
        lo, hi = offsets[idx], offsets[idx + 1]
        seg["h"] = final_elev[lo:hi]

    # per-station "inside a junction's flush box" mask (2026-09-04) - exported
    # below so build_road_guardrails.py can skip placing rail through open
    # junction pavement the same way it already skips reconcile_junction_
    # elevations()'s same-road `touched` mask (see that script's docstring).
    # junction_suppress (1 - blend_t) is the SMOOTH 0..1 companion - 1.0 deep
    # inside the flush box, smoothstep-easing to 0.0 over the approach zone -
    # threaded through build_road_network.py -> road_mesh_common.
    # build_road_chunks(junction_suppress=...) into the ODR3 uv1.y channel,
    # which is what unreal/apply_road_material.py actually reads to stop
    # painting lane markings through the junction (a hard in/out flag would
    # pop at the approach boundary instead of easing out).
    # Reuse best_w from the per-segment elevation-flatten pass above rather
    # than re-deriving via compute_junction_influence() (network-wide
    # XY-nearest match - same class of bug as the elevation case: it would
    # suppress lane markings on an unrelated road that merely passes near a
    # junction in plan view). best_w is already the correct per-segment,
    # graph-participant-restricted weight (1.0 = untouched, 0.0 = deep
    # inside a junction box this station's own road actually connects to).
    all_junction_suppress = 1.0 - best_w
    all_in_box = best_w <= 0.0

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
            "is_tunnel": [bool(v) for v in seg["is_tunnel_s"]],
            "road_name": seg["road_name_s"],
            "oneway": seg["is_oneway_s"],
            "highway_class": seg["highway_s"],
            "east_m": [round(float(v), 2) for v in seg["e"]],
            "north_m": [round(float(v), 2) for v in seg["n"]],
            "s_m": [round(float(v), 2) for v in seg["s"]],
            "elev_m": [round(float(v), 2) for v in seg["h"]],
            "junction_suppress": [round(float(v), 4) for v in all_junction_suppress[lo:hi]],
            "in_junction_box": [bool(v) for v in all_in_box[lo:hi]],
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
