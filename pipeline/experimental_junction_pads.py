"""EXPERIMENTAL, standalone junction-blend patch (2026-08-24/25, Taylor).

v1 of this script clustered ANY cross-segment stations within a fixed radius
into a "junction" and hulled their edge corners into a pad - too naive: it
had no elevation check, so it fired on grade-separated crossings (a bridge
passing near another road in plan view but nowhere close in Z) and produced
a sloped wedge between two decks that were never meant to touch (Taylor
screenshot, Lake Hughes Road overpass). v2 added an elevation-agreement
guard and dropped bridge/tunnel/wall-conflict stations from consideration,
which killed the false positives (177 -> 11 clusters) but the pad footprint
itself (a convex hull of every member station's edge corners) still didn't
match "only the part of the road that actually overlaps."

v3 (this version) is a real redesign per Taylor's spec:
  1. Find the actual GEOMETRIC crossing point of each pair of road
     centerlines (line-segment intersection, not station proximity) -
     the junction footprint is a CIRCLE sized from ROAD_HALF_WIDTH and the
     crossing angle (shallow-angle crossings need a bigger circle to cover
     the elongated overlap parallelogram; a near-perpendicular crossing
     needs barely more than ROAD_HALF_WIDTH).
  2. Of the two roads meeting there, whichever is locally FLATTER (smaller
     mean grade in a window around the crossing) keeps its own elevation
     unchanged - the junction sits at that road's interpolated elevation
     right at the crossing point.
  3. The other (steeper) road gets RE-GRADED: its stations within
     TRANSITION_LEN_M of the crossing are smoothstep-blended from their
     original elevation down/up to the pinned junction elevation, so it
     ramps into the flat road instead of just floating a disconnected pad
     over an unchanged, still-clipping ribbon.
  4. The road ribbons themselves are then REBUILT (not just patched with an
     overlay) from the regraded station elevations, using the exact same
     build_road_chunks()/tile-bucketing code the real pipeline uses, so the
     regraded stretch is proper extruded road geometry, not a flat disc
     pretending to be one. A flat circular pad chunk is added on top at each
     crossing purely to paper over the small residual gap the ribbon-vs-
     ribbon miter leaves right at the crossing point itself.

v3's XY snap (bending every non-flat road's last 50m onto the flat road's
exact point, not just its elevation) moved road/pad geometry away from the
row of coordinates build_terrain_mesh_tiles.py reads from the UNMODIFIED
road_stations.json to carve the ground - an XY-snapped pad/ribbon ends up
up to ~9m from where the terrain was actually carved flat, and gets buried
under un-recarved hillside (2026-08-25, headless -odshot/-odpick self-test:
PM_Asphalt found exactly at the un-snapped point, DefaultPhysicalMaterial/
terrain at the snapped one - never visible in a screenshot either way).
v4 (current) keeps the XY snap - it's what makes independently-digitized
roads actually meet at one node instead of needing a pad wide enough to
look like "a road driving through another" - and fixes the terrain mismatch
at the source instead: regrade_terrain_at_junctions() re-carves the small
number of terrain tiles a junction touches from these SAME regraded station
arrays (reusing build_terrain_mesh_tiles.build_tile() directly, so the
terrain-side carving math is never duplicated/re-derived here), so the
ground is recut to match wherever the road actually ended up.

Kept DELIBERATELY separate from the real pipeline per Taylor's "keep it
independent for now... slowly iterate through the issues before folding
anything back in" - this script is never called by build_area.py or any
other production script, and only ever touches the SHIPPED
Content/OpenDrive/Masks/road_*.bin and terrain_*.bin files (never
road_stations.json itself - v4's regrade_terrain_at_junctions() reuses
build_terrain_mesh_tiles.build_tile()/write_tile_bin() as library calls on
in-memory station arrays, it never invokes that script or edits its output
file). The very first pre-patch originals of both are backed up once (to
_road_backup_junction_pads and _terrain_backup_junction_pads) and never
overwritten again, so every run of this script - however different its
algorithm - starts from that same known-good baseline and is trivially
reversible via --restore.

Usage:  python experimental_junction_pads.py [--restore]
    (no args)   rebuild every road tile from a fresh regrade + pad pass
    --restore   copy the backed-up originals back, undoing every patch
"""

import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

import build_terrain_mesh_tiles as btmt
from build_road_network import split_excluding_tunnels
from generation_id import compute_generation_id, pack_generation_id
from road_mesh_common import (ROAD_HALF_WIDTH, ROAD_LIFT, SKIRT_DROP, SKIRT_EXTRA,
                               STATION_SPACING, build_road_chunks, road_ue_z_m)
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, ROAD_TILE_SIZE_CM

OUT_DIR = Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
BACKUP_DIR = OUT_DIR / "_road_backup_junction_pads"
TERRAIN_BACKUP_DIR = OUT_DIR / "_terrain_backup_junction_pads"

JUNCTION_SEARCH_RADIUS_M = 15.0    # broad-phase: how close two DIFFERENT
                                    # segments' stations must be to even try
                                    # an exact edge-vs-edge intersection test
JUNCTION_MAX_ELEV_DIFF_M = 4.0     # at-grade guard - see v2 note above. Real
                                    # overpasses need >=4.5m legal clearance,
                                    # so this still rejects them; bumped from
                                    # 2.5 to give headroom for two
                                    # independently-smoothed roads' DEM noise
                                    # right at a genuine at-grade merge, now
                                    # that `structural` (below) no longer
                                    # pre-filters those candidates away.
GRADE_WINDOW_M = 60.0              # each side, for the flat-vs-steep call
TRANSITION_LEN_M = 50.0            # each side, regrade blend distance
PAD_RADIUS_MIN_M = ROAD_HALF_WIDTH * 1.2
PAD_RADIUS_MAX_M = ROAD_HALF_WIDTH * 4.0
# The disc chunk is drawn a bit BIGGER than the keep-out radius that omits
# ribbon geometry (see the "inside" test in main()) so it always overlaps the
# first still-rendered ribbon station past the cut instead of just touching
# it edge-to-edge - station spacing (STATION_SPACING=5m) means that edge can
# land up to half a station short of the true circle, which read as "roads
# .1m apart... dont actually connect" (2026-08-25, Taylor).
PAD_OVERLAP_MARGIN_M = 2.0
PAD_MIN_ANGLE_DEG = 20.0           # floor for the sin() in the radius formula
                                    # so a near-parallel crossing doesn't blow
                                    # the pad radius up toward infinity
PAD_SIDES = 20

# Separate from crossing detection entirely: a whole cluster of residential
# streets (Lake Hughes subdivision) were digitized as ISOLATED OSM ways whose
# end nodes just don't reach their neighbors - not a crossing to detect, a
# topology gap to weld. A scan of every segment's start/end station found 93
# with no other segment within 25m; distances to the nearest OTHER road
# cluster naturally below ~35m (real missing intersections) and then jump to
# 40-100m+ (almost certainly separate real streets/cul-de-sacs that were
# never meant to connect) - bridging those with a fake paved spur would draw
# roads that don't exist. Only endpoints inside this conservative gap get a
# straight connector (2026-08-25, Taylor: "I was on a road and it just
# ended... supposed to be an intersection there").
ENDPOINT_SNAP_MAX_M = 35.0


# ------------------------------------------------------------------ ODR2 I/O
def write_tile(path, chunks, gen_id_bytes):
    with open(path, "wb") as f:
        f.write(b"ODR2")
        f.write(gen_id_bytes)
        f.write(struct.pack("<I", len(chunks)))
        for c in chunks:
            verts, uvs, tris = c["vertices"], c["uvs"], c["triangles"]
            vcount, tcount = len(verts) // 3, len(tris) // 3
            f.write(struct.pack("<II", vcount, tcount))
            f.write(struct.pack("<B", 1 if c.get("oneway") else 0))
            f.write(struct.pack(f"<{len(verts)}f", *verts))
            f.write(struct.pack(f"<{len(uvs)}f", *uvs))
            f.write(struct.pack(f"<{len(tris)}I", *tris))


def chunk_centroid(chunk):
    verts = chunk["vertices"]
    n = len(verts) // 3
    return sum(verts[0::3]) / n, sum(verts[1::3]) / n


def bridge_pad_to_host(host, guest_base, guest_vcount):
    """Connect the guest pad's outer ring to whichever of the host's OWN
    vertices sit closest to it, using real shared triangle indices - so the
    merged mesh is topologically ONE connected piece instead of two islands
    sharing a buffer.

    2026-08-26, Taylor: merge_chunk_into's plain buffer concatenation
    (below) was proven, via a direct headless A/B test, to NOT be enough on
    its own - a ribbon-only point on the exact same merged component
    resolved ground-snap collision perfectly (onRoad=1) while the pad's own
    disc, a few meters away on the SAME component, missed 100% of the time
    (onRoad=0, same terrain-hit fallback every retest). A follow-up test
    ruled out flatness/coplanarity as an independent factor too (a +-3cm
    ring jitter changed nothing). That leaves disconnection as the sole
    confirmed cause: Chaos's complex-as-simple collision cook, given one
    mesh with two geometrically disconnected triangle islands, appears to
    only produce working collision for the larger/primary island - even
    though rendering and every UE-level component health check (registered,
    visible, collision profile, body validity, triangle count) report the
    WHOLE component as completely fine.

    build_pad_chunk() always emits a fan: local vertex 0 is the hub, already
    connected to every ring vertex (1..N-1) by the fan's own triangles, so
    the whole pad is already one connected piece - only ONE bridge to the
    host is strictly required to merge the two islands into one. This
    connects several (a small triangle per nearby host vertex, capped by
    BRIDGE_MAX_D2) rather than just one, so the seam is spread across a
    short run of the boundary instead of resting on a single triangle.
    Bridge triangles are cheap and near-invisible BY DESIGN, not by luck: a
    junction/fill pad is only ever placed where it already overlaps the
    ribbon it's covering (that's the whole point of PAD_OVERLAP_MARGIN_M),
    so any host vertex within reach of the ring is only centimeters away in
    space - these are tiny slivers, not visible shards."""
    host_vcount = guest_base
    if host_vcount == 0 or guest_vcount < 3:
        return 0
    hv = host["vertices"]
    hx = [hv[i * 3] for i in range(host_vcount)]
    hy = [hv[i * 3 + 1] for i in range(host_vcount)]
    ring_local = list(range(1, guest_vcount))   # skip the fan hub (local index 0)
    gx = [hv[(guest_base + i) * 3] for i in ring_local]
    gy = [hv[(guest_base + i) * 3 + 1] for i in ring_local]

    # 2026-08-26: an earlier version added one bridge triangle per nearby
    # host vertex, keyed only by that host vertex's own nearest ring edge -
    # whenever two or more host vertices shared the same nearest ring edge,
    # that edge ended up used by 3+ triangles (the fan's own use, plus every
    # bridge sharing it), which FDynamicMesh3::AppendTriangle rejects as
    # non-manifold. Confirmed via a headless retest: the runtime component's
    # numTris landed BELOW the pipeline's own tcount (218 vs 223 - 5 bridge
    # triangles silently failed to append, `if (Tid >= 0)` in
    # CreateChunkMeshComponent skips failures rather than erroring), and
    # ground-snap still missed. Deduping to at most ONE bridge triangle per
    # ring edge (the closest host vertex wins ties) keeps every edge at
    # exactly 2 uses - manifold-safe - while still connecting the two
    # islands.
    BRIDGE_MAX_D2 = (300.0) ** 2   # only bridge host verts within ~3m (cm^2) of the ring
    best_by_edge = {}   # ring edge j -> (dist2, host vertex index)
    for hi in range(host_vcount):
        best_j, best_d2 = -1, BRIDGE_MAX_D2
        for j in range(len(ring_local)):
            d2 = (hx[hi] - gx[j]) ** 2 + (hy[hi] - gy[j]) ** 2
            if d2 < best_d2:
                best_d2, best_j = d2, j
        if best_j >= 0:
            cur = best_by_edge.get(best_j)
            if cur is None or best_d2 < cur[0]:
                best_by_edge[best_j] = (best_d2, hi)

    if not best_by_edge:
        # Fallback: guarantee at least one connection even if nothing on
        # this host chunk landed within BRIDGE_MAX_D2 of the ring (e.g. a
        # host whose own vertex density near the pad is sparse) - wider
        # search, no distance cap, single bridge triangle.
        best_hi, best_j, best_d2 = 0, 0, float("inf")
        for hi in range(host_vcount):
            for j in range(len(ring_local)):
                d2 = (hx[hi] - gx[j]) ** 2 + (hy[hi] - gy[j]) ** 2
                if d2 < best_d2:
                    best_d2, best_hi, best_j = d2, hi, j
        best_by_edge = {best_j: (best_d2, best_hi)}

    for j, (_d2, hi) in best_by_edge.items():
        ra = guest_base + ring_local[j]
        rb = guest_base + ring_local[(j + 1) % len(ring_local)]
        host["triangles"].extend([hi, ra, rb])
    return len(best_by_edge)


def merge_chunk_into(host, guest):
    """Fuse guest's geometry into host's vertex/uv/triangle buffers in place,
    so at runtime they ship as ONE UDynamicMeshComponent instead of two
    separate ones (2026-08-26, Taylor: after RecreatePhysicsState() and even
    a full destroy-and-rebuild of a brand-new standalone pad component both
    reproduced the identical open Chaos/UE5.8 phantom-registration bug -
    "build the junctions into the same road mapping data that way theres no
    separation and its impossible for it not to be displayed"). A pad fused
    into an already-solid, already-verified-working ribbon chunk can't be
    dropped independently of that ribbon.

    A plain concatenation alone still leaves TWO disconnected triangle
    islands in one buffer, which turned out to be exactly what breaks
    Chaos's collision cook for the smaller island (see bridge_pad_to_host's
    docstring for the confirmed evidence) - so this always follows the
    concatenation with a bridge pass connecting guest's ring to host's
    nearest vertices via real shared indices."""
    base = len(host["vertices"]) // 3
    guest_vcount = len(guest["vertices"]) // 3
    host["vertices"].extend(guest["vertices"])
    host["uvs"].extend(guest["uvs"])
    host["triangles"].extend(idx + base for idx in guest["triangles"])
    bridge_pad_to_host(host, base, guest_vcount)


def nearest_chunk(chunks, xy_m):
    """The chunk whose centroid is closest to xy_m - used to pick which
    already-built ribbon chunk a pad should be fused into via
    merge_chunk_into. xy_m is (east_m, north_m), the same PIPELINE-frame
    meters every caller already has on hand (junction/fill-pad centers,
    station arrays) - chunk_centroid() returns UE-frame cm with north
    flipped to south (see build_pad_chunk's own cx*100.0/-cy*100.0 vertex
    conversion), so it's converted here rather than assuming callers
    remember to pre-flip.  A first version compared xy_m directly against
    cm-scale centroids (off by ~100x with the wrong sign on Y), which chose
    an essentially-random distant chunk - confirmed by finding the fused
    pad's own geometry landing in a tile file nowhere near the junction
    (2026-08-26 headless verification)."""
    bx, by = xy_m[0] * 100.0, -xy_m[1] * 100.0
    best, best_d2 = None, float("inf")
    for c in chunks:
        cx, cy = chunk_centroid(c)
        d2 = (cx - bx) ** 2 + (cy - by) ** 2
        if d2 < best_d2:
            best_d2, best = d2, c
    return best


# ------------------------------------------------------------------ geometry
# Combined road footprint - two roads' strips physically overlap whenever
# their CENTERLINES come within this distance of each other, not only when
# the centerlines literally cross. This is what actually distinguishes a T/Y
# junction (road B's last few stations end near road A's centerline - the
# centerlines never cross, but the pavement strips overlap) from an X
# crossing (centerlines literally cross partway along both) - most real
# junctions in an OSM-derived network are T/Y, not X, so requiring a literal
# centerline crossing (tried first) found zero real junctions.
JUNCTION_OVERLAP_M = 2.0 * ROAD_HALF_WIDTH


def closest_segment_points(ax, ay, bx, by, cx, cy, dx, dy):
    """Closest points between finite segments AB and CD (Ericson's
    Real-Time Collision Detection algorithm, 2D). Returns (t, u, dist2, ix,
    iy) - t/u in [0,1] are the clamped interpolation fractions along AB/CD,
    dist2 the squared distance between the two closest points, (ix,iy) their
    midpoint."""
    d1x, d1y = bx - ax, by - ay
    d2x, d2y = dx - cx, dy - cy
    rx, ry = ax - cx, ay - cy
    a = d1x * d1x + d1y * d1y
    e = d2x * d2x + d2y * d2y
    f = d2x * rx + d2y * ry
    eps = 1e-9
    if a <= eps and e <= eps:
        t = u = 0.0
    elif a <= eps:
        t = 0.0
        u = max(0.0, min(1.0, f / e))
    else:
        c = d1x * rx + d1y * ry
        if e <= eps:
            u = 0.0
            t = max(0.0, min(1.0, -c / a))
        else:
            b = d1x * d2x + d1y * d2y
            denom = a * e - b * b
            t = max(0.0, min(1.0, (b * f - c * e) / denom)) if abs(denom) > eps else 0.0
            u = (b * t + f) / e
            if u < 0.0:
                u = 0.0
                t = max(0.0, min(1.0, -c / a))
            elif u > 1.0:
                u = 1.0
                t = max(0.0, min(1.0, (b - c) / a))
    p1x, p1y = ax + d1x * t, ay + d1y * t
    p2x, p2y = cx + d2x * u, cy + d2y * u
    dist2 = (p1x - p2x) ** 2 + (p1y - p2y) ** 2
    return t, u, dist2, (p1x + p2x) / 2.0, (p1y + p2y) / 2.0


def crossing_angle_deg(txa, tya, txb, tyb):
    la = math.hypot(txa, tya) or 1.0
    lb = math.hypot(txb, tyb) or 1.0
    dot = max(-1.0, min(1.0, (txa * txb + tya * tyb) / (la * lb)))
    ang = math.degrees(math.acos(dot))
    return min(ang, 180.0 - ang)   # fold to [0, 90] - "how sharp is the X"


def find_crossings(east, north, s_m, elev, seg_ids, structural):
    """Real geometric crossings between DIFFERENT segments' centerlines,
    restricted to at-grade pairs (JUNCTION_MAX_ELEV_DIFF_M) with neither
    edge touching a genuinely structural (is_bridge/is_tunnel) station.

    Deliberately does NOT use btmt.load_stations()'s broader `exclude` array
    here - that array is `is_bridge | conflict`, and `conflict` (from
    classify_wall_stations()) flags almost EVERY cross-road pair within
    150m whose grade disagreement clears a low bar (0.05 for out-of-scope/
    different-named-road pairs, regardless of whether it's a real overpass)
    so real T/Y junctions - two independently-smoothed roads that simply
    disagree a bit right where they meet - were being thrown out before
    ever reaching the elevation check below (the "roads intercept but
    dont properly intersect" bug). `structural` (is_bridge | is_tunnel
    only) is the correct, narrower gate for candidacy; JUNCTION_MAX_ELEV_
    DIFF_M is what actually decides genuine at-grade vs. grade-separated."""
    n = len(east)
    cell = JUNCTION_SEARCH_RADIUS_M
    buckets = defaultdict(list)
    for i in range(n):
        if not structural[i]:
            buckets[(int(east[i] // cell), int(north[i] // cell))].append(i)

    def valid_edge(i, seg):
        return 0 <= i < n - 1 and seg_ids[i] == seg and seg_ids[i + 1] == seg \
            and not structural[i] and not structural[i + 1]

    candidate_edges = set()
    r2 = JUNCTION_SEARCH_RADIUS_M ** 2
    for (cx, cy), idxs in buckets.items():
        neighbor_idxs = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_idxs.extend(buckets.get((cx + dx, cy + dy), []))
        for i in idxs:
            segi = seg_ids[i]
            for j in neighbor_idxs:
                if seg_ids[j] == segi:
                    continue
                d2 = (east[i] - east[j]) ** 2 + (north[i] - north[j]) ** 2
                if d2 > r2:
                    continue
                for ei in (i - 1, i):
                    if not valid_edge(ei, segi):
                        continue
                    for ej in (j - 1, j):
                        if not valid_edge(ej, seg_ids[j]):
                            continue
                        candidate_edges.add((ei, ej))

    overlap2 = JUNCTION_OVERLAP_M ** 2
    crossings = []
    seen_xy = []
    for ei, ej in candidate_edges:
        t, u, dist2, ix, iy = closest_segment_points(
            east[ei], north[ei], east[ei + 1], north[ei + 1],
            east[ej], north[ej], east[ej + 1], north[ej + 1])
        if dist2 > overlap2:
            continue   # strips don't actually overlap here, just close-ish
        elev_a = elev[ei] + t * (elev[ei + 1] - elev[ei])
        elev_b = elev[ej] + u * (elev[ej + 1] - elev[ej])
        if abs(elev_a - elev_b) > JUNCTION_MAX_ELEV_DIFF_M:
            continue   # grade-separated crossing, not a real merge
        if any((ix - sx) ** 2 + (iy - sy) ** 2 < 25.0 for sx, sy in seen_xy):
            continue   # same physical junction already found via another edge pair
        seen_xy.append((ix, iy))
        s_a = s_m[ei] + t * (s_m[ei + 1] - s_m[ei])
        s_b = s_m[ej] + u * (s_m[ej + 1] - s_m[ej])
        ang = crossing_angle_deg(east[ei + 1] - east[ei], north[ei + 1] - north[ei],
                                  east[ej + 1] - east[ej], north[ej + 1] - north[ej])
        crossings.append({
            "xy": (ix, iy), "angle_deg": ang,
            "a": {"seg": int(seg_ids[ei]), "edge": ei, "s": s_a, "elev": elev_a},
            "b": {"seg": int(seg_ids[ej]), "edge": ej, "s": s_b, "elev": elev_b},
        })
    return crossings


def find_dangling_endpoints(east, north, elev, seg_ids, structural, bounds):
    """Segment start/end stations with no other segment's station within
    JUNCTION_OVERLAP_M (i.e. NOT already handled by find_crossings/clustering)
    but with one within ENDPOINT_SNAP_MAX_M - a real, conservatively-sized
    topology gap worth welding shut, not a crossing to detect."""
    n = len(east)
    cell = ENDPOINT_SNAP_MAX_M
    buckets = defaultdict(list)
    for i in range(n):
        if not structural[i]:
            buckets[(int(east[i] // cell), int(north[i] // cell))].append(i)

    endpoints = []
    for seg, (lo, hi) in bounds.items():
        for gi in (lo, hi):
            if structural[gi]:
                continue
            endpoints.append((gi, seg))

    r2 = ENDPOINT_SNAP_MAX_M ** 2
    already2 = JUNCTION_OVERLAP_M ** 2
    snaps = []
    for gi, seg in endpoints:
        bx, by = int(east[gi] // cell), int(north[gi] // cell)
        best_d2, best_j = 1e18, -1
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((bx + dx, by + dy), ()):
                    if seg_ids[j] == seg:
                        continue
                    d2 = (east[gi] - east[j]) ** 2 + (north[gi] - north[j]) ** 2
                    if d2 < best_d2:
                        best_d2, best_j = d2, j
        if best_j < 0 or best_d2 <= already2:
            continue   # already welded/crossing there, or nothing nearby at all
        if best_d2 > r2:
            continue   # beyond the conservative snap distance - leave it alone
        if abs(elev[gi] - elev[best_j]) > JUNCTION_MAX_ELEV_DIFF_M:
            continue   # not actually at-grade with its nearest neighbor
        snaps.append({"gi": gi, "seg": seg, "target_gi": int(best_j),
                       "target_seg": int(seg_ids[best_j]), "dist": best_d2 ** 0.5})
    return snaps


def segment_bounds(seg_ids):
    bounds = {}
    for i, seg in enumerate(seg_ids):
        lo, hi = bounds.get(seg, (i, i))
        bounds[seg] = (min(lo, i), max(hi, i))
    return bounds


def local_grade(elev, bounds_lo, bounds_hi, center_idx):
    half = int(round(GRADE_WINDOW_M / STATION_SPACING))
    a = max(bounds_lo, center_idx - half)
    b = min(bounds_hi, center_idx + half)
    window = elev[a:b + 1]
    if len(window) < 2:
        return 0.0
    return float(np.mean(np.abs(np.diff(window)))) / STATION_SPACING


def pad_radius_m(angle_deg):
    eff_angle = max(angle_deg, PAD_MIN_ANGLE_DEG)
    r = ROAD_HALF_WIDTH / math.sin(math.radians(eff_angle))
    return max(PAD_RADIUS_MIN_M, min(PAD_RADIUS_MAX_M, r))


# Two pairwise crossings within this of each other are the SAME physical
# junction (a real 3+-way where road C meets both A and B a few meters
# apart, found as two separate A-B/A-C pairs) rather than two distinct
# junctions - merging them stops each pair regrading/padding independently
# and clobbering the other's work right where they overlap. 1.5x the
# biggest single-pair pad radius is generous enough to catch a tight
# multi-way cluster without also swallowing two genuinely separate
# junctions strung along the same road.
CLUSTER_MERGE_M = PAD_RADIUS_MAX_M * 1.5


def cluster_crossings(crossings):
    """Union-find crossings whose xy falls within CLUSTER_MERGE_M of each
    other. Returns a list of clusters (each a list of crossing dicts)."""
    parent = list(range(len(crossings)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    r2 = CLUSTER_MERGE_M ** 2
    for i in range(len(crossings)):
        xi, yi = crossings[i]["xy"]
        for j in range(i + 1, len(crossings)):
            xj, yj = crossings[j]["xy"]
            if (xi - xj) ** 2 + (yi - yj) ** 2 <= r2:
                union(i, j)

    groups = defaultdict(list)
    for i, c in enumerate(crossings):
        groups[find(i)].append(c)
    return list(groups.values())


def build_pad_chunk(name, center_xy, elev_m, radius_m, sides=PAD_SIDES):
    """A flat circular disc at the pinned junction elevation - "junction is
    at the same grade as the flattest road" means constant Z, not a sloped
    surface, so this is a plain fan, no per-vertex elevation interpolation
    needed (unlike v1/v2's hull-of-corners approach)."""
    cx, cy = center_xy
    z_m = road_ue_z_m(elev_m, ROAD_LIFT, ORIGIN_HEIGHT_M)
    verts = [cx * 100.0, -cy * 100.0, z_m * 100.0]
    # RoadMat reads U across build_road_chunks()'s skirt-L/pave-L/pave-R/
    # skirt-R profile (U=0..1), with the yellow centerline stripe baked in
    # around U=0.5 - the physical pavement centerline. A radial 0.5+0.5cos/
    # sin UV sweeps this disc through that ENTIRE skirt->stripe->skirt range
    # at every angle, painting a fanned mix of stripe/pavement/skirt texture
    # radiating out from the center instead of plain pavement (2026-08-25,
    # Taylor: "legitimately identical" - the pad was rendering, just with
    # nonsense UVs). Use one constant UV, off-center in the pavement band,
    # for every vertex so the whole disc samples a single flat asphalt texel.
    PAD_UV = (0.58, 0.0)
    uvs = [PAD_UV[0], PAD_UV[1]]
    # 2026-08-26: a +-3cm alternating Z jitter was tried here (testing
    # whether an exactly-coplanar disc, independent of topology, was what
    # broke Chaos's collision cook) and headless-retested at the same
    # junction point - result was identical (still onRoad=0, same terrain
    # fallback height). Flatness ruled out; see merge_chunk_into's docstring
    # for the confirmed cause (disconnected islands) and its fix (bridge
    # triangles).
    for k in range(sides):
        ang = 2.0 * math.pi * k / sides
        vx, vy = cx + radius_m * math.cos(ang), cy + radius_m * math.sin(ang)
        verts.extend([vx * 100.0, -vy * 100.0, z_m * 100.0])
        uvs.extend([PAD_UV[0], PAD_UV[1]])
    # CONFIRMED ROOT CAUSE (2026-08-26): this fan must wind (0,a,b), not
    # (0,b,a). The 2026-08-25 fix that swapped to (0,b,a) was chasing a real
    # symptom (the pad was invisible from normal driving angles) but landed
    # on the wrong mechanism - RoadMat turns out to already be double-sided
    # (rendering is winding-independent either way; a live headless retest
    # confirmed (0,a,b) is STILL fully visible), while Chaos's complex-as-
    # simple collision cook for a UDynamicMeshComponent IS one-sided by
    # winding, and was silently dropping every query aimed at the (0,b,a)
    # orientation from its results. This explains an entire multi-session
    # investigation's "component reports completely healthy by every
    # metric - registered, visible, valid body, correct triangle/vertex
    # counts, correctly single-connected topology - yet ground-snap still
    # misses" pattern; every lifecycle-order and topology theory tried
    # first (component registration before/after EditMesh, collision-type
    # set before/after EditMesh, bridging the pad's ring to the host to fix
    # a disconnected-island theory) changed nothing, because none of them
    # were the actual cause. Headless-verified at the original junction
    # (356900,-540300): (0,b,a) -> onRoad=0, hitting terrain every time;
    # (0,a,b) -> onRoad=1, PM_Asphalt, resolved instantly.
    tris = []
    for k in range(sides):
        a, b = 1 + k, 1 + (k + 1) % sides
        tris.extend([0, a, b])
    return {"name": name, "vertices": [round(v, 1) for v in verts],
            "uvs": [round(v, 4) for v in uvs], "triangles": tris, "oneway": False}


# ------------------------------------------------------------------ main
def restore():
    if not BACKUP_DIR.exists():
        print("no road backup found - nothing to restore there")
    else:
        n = 0
        for p in BACKUP_DIR.glob(f"road_{AREA_NAME}_test_tile_*.bin"):
            (OUT_DIR / p.name).write_bytes(p.read_bytes())
            n += 1
        n_extra = 0
        kept = {p.name for p in BACKUP_DIR.glob(f"road_{AREA_NAME}_test_tile_*.bin")}
        for p in OUT_DIR.glob(f"road_{AREA_NAME}_test_tile_*.bin"):
            if p.name not in kept:
                p.unlink()
                n_extra += 1
        print(f"restored {n} road tile(s) from {BACKUP_DIR}"
              + (f", removed {n_extra} tile(s) with no original" if n_extra else ""))

    if not TERRAIN_BACKUP_DIR.exists():
        print("no terrain backup found - nothing to restore there")
    else:
        n = 0
        for p in TERRAIN_BACKUP_DIR.glob(f"terrain_{AREA_NAME}_test_tile_*.bin"):
            (OUT_DIR / p.name).write_bytes(p.read_bytes())
            n += 1
        print(f"restored {n} terrain tile(s) from {TERRAIN_BACKUP_DIR}")


def build_solo_junction(junctions, raw, target_xy=(-2485.0, 7115.0), stub_len_m=60.0,
                         floor_radius_m=150.0, floor_drop_m=6.0, num_junctions=1):
    """Debug view: wipe every tile and rewrite only the N nearest junction(s)
    to target_xy (short stubs of each's connecting roads + its pad) plus a
    flat reference floor disc below the primary one, so the isolated case can
    be inspected with zero other map geometry as a confounder (2026-08-25,
    Taylor: "build one junction so that I can see it. Set the floor of the
    map to be one meter below the junction."). num_junctions > 1 is a
    2026-08-25 addition (still Taylor's isolation request, extended) - lets a
    headless test step the total resident chunk count up in controlled
    increments to bisect the open Chaos/UE5.8 collision-registration bug's
    trigger threshold, since real-map SpawnRadius bisection hit a floor (a
    junction near a tile-grid corner still pulls in every neighboring tile's
    full chunk count even at a near-zero radius). Reuses build_pad_chunk
    (sides=48) for the floor since its fan winding is already verified
    correct - a big flat circle is just a pad with an oversized radius."""
    ranked = sorted(junctions, key=lambda j: math.hypot(j["xy"][0] - target_xy[0], j["xy"][1] - target_xy[1]))
    chosen = ranked[:max(1, num_junctions)]
    junc = chosen[0]
    print(f"solo mode: isolating junction @ {junc['xy']} radius={junc['radius_m']:.1f}m "
          f"elev={junc['elev']:.1f}m segs={sorted(junc['segs'])}"
          + (f" (+{len(chosen) - 1} more nearest junction(s))" if len(chosen) > 1 else ""))

    solo_chunks = []
    for jidx, j in enumerate(chosen):
        for seg_id in j["segs"]:
            seg = raw["segments"][seg_id]
            s_arr = np.array(seg["s_m"])
            e_arr = np.array(seg["east_m"])
            n_arr = np.array(seg["north_m"])
            h_arr = np.array(seg["elev_m"])
            d = np.hypot(e_arr - j["xy"][0], n_arr - j["xy"][1])
            anchor = int(np.argmin(d))
            s0 = s_arr[anchor]
            idxs = np.where(np.abs(s_arr - s0) <= stub_len_m)[0]
            if len(idxs) < 2:
                continue
            lo, hi = int(idxs.min()), int(idxs.max()) + 1
            z = road_ue_z_m(h_arr[lo:hi], ROAD_LIFT, ORIGIN_HEIGHT_M)
            chunks, _cx, _cy, _cz = build_road_chunks(
                s_arr[lo:hi], e_arr[lo:hi], n_arr[lo:hi], z,
                name_prefix=f"solo_j{jidx:03d}_seg{seg_id:04d}", spacing=STATION_SPACING,
                skirt_extra=SKIRT_EXTRA, skirt_drop=SKIRT_DROP)
            solo_chunks.extend(chunks)

        solo_chunks.append(build_pad_chunk(f"solo_j{jidx:03d}_junctionpad", j["xy"], j["elev"],
                                            j["radius_m"] + PAD_OVERLAP_MARGIN_M))

    solo_chunks.append(build_pad_chunk("solo_floor", junc["xy"], junc["elev"] - floor_drop_m,
                                        floor_radius_m, sides=48))

    buckets = defaultdict(list)
    for chunk in solo_chunks:
        cx, cy = chunk_centroid(chunk)
        tx, ty = int(cx // ROAD_TILE_SIZE_CM), int(cy // ROAD_TILE_SIZE_CM)
        buckets[(tx, ty)].append(chunk)

    removed = 0
    for p in OUT_DIR.glob(f"road_{AREA_NAME}_test_tile_*.bin"):
        p.unlink()
        removed += 1

    gen_id_bytes = pack_generation_id(compute_generation_id())
    written = []
    for (tx, ty), tile_chunks in buckets.items():
        path = OUT_DIR / f"road_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(path, tile_chunks, gen_id_bytes)
        written.append(path)
    print(f"solo mode: wrote {len(solo_chunks)} chunk(s) -> {len(written)} tile(s), "
          f"removed {removed} other tile(s). Rerun without --solo to restore the full map.")


def regrade_terrain_at_junctions(junctions, lat0, lon0, regraded_east, regraded_north, regraded_elev,
                                  terrain_exclude):
    """Re-carve the (small) set of terrain tiles a snapped junction actually
    touches, from the SAME regraded station arrays the road pads/ribbons
    above were just built from - reuses build_terrain_mesh_tiles.build_tile()
    /write_tile_bin() as plain library calls rather than re-deriving the DEM-
    sample+carve math here, so the terrain-side result matches exactly what a
    full terrain rebake would produce for these coordinates (2026-08-25,
    Taylor: keep the XY snap, fix the terrain instead of the pad).

    Needs network access - build_tile() samples fresh DEM tiles per run (see
    build_terrain_mesh_tiles.sample_dem's docstring on why there's no on-disk
    cache to fall back to); only the handful of 2km tiles junctions actually
    touch get rebuilt, not a full-area rebake."""
    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    # A junction's own pad radius plus TERRAIN_CARVE_OUTER_M (how far a
    # station can still influence the carve - see that constant's own
    # comment) is the true footprint that can change; padding out to the
    # tile corners in every direction is simpler than exact overlap math and
    # this only ever adds at most one extra neighboring tile per junction.
    for j in junctions:
        cx_cm, cy_cm = j["xy"][0] * 100.0, -j["xy"][1] * 100.0
        r_cm = (j["radius_m"] + btmt.TERRAIN_CARVE_OUTER_M) * 100.0
        j["_terrain_tiles"] = {
            (int((cx_cm + dx) // tile_cm), int((cy_cm + dy) // tile_cm))
            for dx in (-r_cm, r_cm) for dy in (-r_cm, r_cm)}

    tiles_needed = set().union(*(j["_terrain_tiles"] for j in junctions)) if junctions else set()
    if not tiles_needed:
        print("regrade_terrain: no junctions to re-carve terrain for")
        return

    st_x_cm = regraded_east * 100.0
    st_y_cm = -regraded_north * 100.0
    station_data = (lat0, lon0, st_x_cm, st_y_cm, regraded_elev, terrain_exclude)

    session = requests.Session()
    caches = ({}, {})
    gen_id_bytes = pack_generation_id(compute_generation_id())

    if not TERRAIN_BACKUP_DIR.exists():
        TERRAIN_BACKUP_DIR.mkdir(parents=True)
    backed_up = 0
    for (tx, ty) in sorted(tiles_needed):
        path = OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        backup_path = TERRAIN_BACKUP_DIR / path.name
        if path.exists() and not backup_path.exists():
            backup_path.write_bytes(path.read_bytes())
            backed_up += 1
        # every junction-adjacent tile is, by construction, within the road
        # corridor - always a NEAR tile (full-res grid + collision), never
        # the coarse background/far LOD.
        tile = btmt.build_tile(tx, ty, True, station_data, session, caches)
        btmt.write_tile_bin(path, tile, gen_id_bytes)
    print(f"regrade_terrain: re-carved {len(tiles_needed)} terrain tile(s) "
          f"around {len(junctions)} junction(s)"
          + (f", backed up {backed_up} original(s) -> {TERRAIN_BACKUP_DIR}" if backed_up else ""))


def main():
    if "--restore" in sys.argv:
        restore()
        return

    lat0, lon0, east, north, elev, terrain_exclude, _wp = btmt.load_stations()
    raw = json.loads(btmt.STATIONS_PATH.read_text())
    seg_ids = np.concatenate([
        np.full(len(seg["east_m"]), si, dtype=np.int32)
        for si, seg in enumerate(raw["segments"])])
    s_m = np.concatenate([np.array(seg["s_m"]) for seg in raw["segments"]])
    # Genuinely structural stations only (real bridges/tunnels) - NOT
    # btmt's broader terrain-carve `exclude`, which also flags ordinary
    # cross-road grade disagreements that are often real at-grade
    # junctions. See find_crossings()'s docstring.
    structural = np.concatenate([
        np.array(seg.get("is_bridge", [False] * len(seg["east_m"])), dtype=bool)
        | np.array(seg.get("is_tunnel", [False] * len(seg["east_m"])), dtype=bool)
        for seg in raw["segments"]])
    bounds = segment_bounds(seg_ids)

    crossings = find_crossings(east, north, s_m, elev, seg_ids, structural)
    print(f"found {len(crossings)} real at-grade centerline crossing(s)")

    clusters = cluster_crossings(crossings)
    n_multiway = sum(1 for cl in clusters if len({e["seg"] for c in cl for e in (c["a"], c["b"])}) > 2)
    print(f"grouped into {len(clusters)} junction(s) "
          f"({n_multiway} multi-way, merged from {len(crossings) - len(clusters)} extra pairwise crossing(s))")

    regraded = elev.copy()
    regraded_east = east.copy()
    regraded_north = north.copy()
    junctions = []   # one dict per cluster: xy, elev, radius_m, flat_seg
    for cl in clusters:
        # one representative (seg, edge, s) per unique road touching this
        # junction - a seg can appear in >1 pairwise crossing within the
        # cluster (e.g. the A-B and A-C pairs of a real 3-way both touch
        # road A a couple stations apart); collapse to a single anchor per
        # road so it only gets regraded/graded once, not once per pair.
        reps = {}
        for c in cl:
            for e in (c["a"], c["b"]):
                reps.setdefault(e["seg"], e)

        grades = {seg: local_grade(elev, *bounds[seg], e["edge"]) for seg, e in reps.items()}
        flat_seg = min(grades, key=grades.get)
        junction_elev = reps[flat_seg]["elev"]
        # Snap every other road's approach onto the flat road's own anchor
        # point instead of a shared centroid - independently-digitized OSM
        # ways meeting at "the same" real intersection often land a few
        # meters apart (2026-08-25, Taylor: "they are literally within 1m of
        # each other [pavement-edge-wise]. they intersect...") which used to
        # need a pad wide enough to bridge the whole cluster, wide enough to
        # visibly read as a road driving through another instead of a clean
        # T. Bending the non-flat roads' last 50m onto one exact point (the
        # same cosine-eased blend already used for elevation, just applied
        # to XY too) means they truly meet at a single node and the pad only
        # has to cap that one point, not paper over the gap between three.
        #
        # v4 note: this DOES move road/pad geometry away from the row of
        # coordinates build_terrain_mesh_tiles.py carves the ground from
        # (road_stations.json, on disk, untouched by this script) - left
        # unpatched, the pad ends up buried under un-recarved hillside a few
        # meters off to the side (2026-08-25, headless -odshot/-odpick self-
        # test: PM_Asphalt found exactly at the un-snapped point,
        # DefaultPhysicalMaterial/terrain at the snapped one). Per Taylor's
        # call, that's fixed downstream by regrade_terrain_at_junctions()
        # re-carving the affected terrain tile(s) from these SAME regraded
        # station arrays, not by giving up the snap.
        snap_xy = (east[reps[flat_seg]["edge"]], north[reps[flat_seg]["edge"]])

        cx, cy = snap_xy
        radius_m = pad_radius_m(min(c["angle_deg"] for c in cl))

        junctions.append({"xy": (cx, cy), "elev": junction_elev, "radius_m": radius_m,
                           "flat_seg": flat_seg, "n_roads": len(reps), "segs": set(reps.keys())})

        half = int(round(TRANSITION_LEN_M / STATION_SPACING)) + 1
        for seg, e in reps.items():
            if seg == flat_seg:
                continue   # the flattest road keeps its own profile untouched
            lo, hi = bounds[seg]
            k0, k1 = max(lo, e["edge"] - half), min(hi, e["edge"] + half)
            for k in range(k0, k1 + 1):
                if structural[k]:
                    continue
                d = abs(s_m[k] - e["s"])
                if d > TRANSITION_LEN_M:
                    continue
                t = d / TRANSITION_LEN_M
                weight = 1.0 - (3.0 * t * t - 2.0 * t * t * t)   # 1 at crossing, 0 at edge
                # blend from the ORIGINAL profile (elev/east/north), not a
                # running `regraded*`, so two nearby junctions on the same
                # road don't compound each other's distortion - later writes
                # simply win on any overlap (rare: TRANSITION_LEN_M=50m)
                regraded[k] = elev[k] * (1.0 - weight) + junction_elev * weight
                regraded_east[k] = east[k] * (1.0 - weight) + snap_xy[0] * weight
                regraded_north[k] = north[k] * (1.0 - weight) + snap_xy[1] * weight

    n_regraded = int(np.sum(~np.isclose(regraded, elev)))
    print(f"regraded {n_regraded} station(s) across {len(junctions)} junction(s)")

    # Separate problem from crossing detection: roads whose endpoint was
    # simply never topologically connected to anything (see
    # find_dangling_endpoints' docstring). Weld each one shut with a
    # straight connector spur from the dangling endpoint to its nearest
    # neighbor, regrading the approach the same way a real junction does,
    # plus a small pad to paper over the T-seam where it meets the target
    # road (2026-08-25, Taylor: "I was on a road and it just ended...
    # supposed to be an intersection there").
    snaps = find_dangling_endpoints(east, north, elev, seg_ids, structural, bounds)
    print(f"found {len(snaps)} dangling endpoint(s) within {ENDPOINT_SNAP_MAX_M:.0f}m "
          f"of another road - welding with a connector spur")
    connector_chunks = []
    half_t = int(round(TRANSITION_LEN_M / STATION_SPACING)) + 1
    for k, sp in enumerate(snaps):
        gi, seg, tgi, tseg = sp["gi"], sp["seg"], sp["target_gi"], sp["target_seg"]
        grade_a = local_grade(elev, *bounds[seg], gi)
        grade_b = local_grade(elev, *bounds[tseg], tgi)
        pin_elev = elev[tgi] if grade_b <= grade_a else elev[gi]

        lo, hi = bounds[seg]
        k0, k1 = max(lo, gi - half_t), min(hi, gi + half_t)
        for kk in range(k0, k1 + 1):
            if structural[kk]:
                continue
            d = abs(s_m[kk] - s_m[gi])
            if d > TRANSITION_LEN_M:
                continue
            t = d / TRANSITION_LEN_M
            weight = 1.0 - (3.0 * t * t - 2.0 * t * t * t)
            regraded[kk] = elev[kk] * (1.0 - weight) + pin_elev * weight

        z_ab = road_ue_z_m(np.array([regraded[gi], pin_elev]), ROAD_LIFT, ORIGIN_HEIGHT_M)
        chunks, _cx, _cy, _cz = build_road_chunks(
            np.array([0.0, sp["dist"]]), np.array([east[gi], east[tgi]]),
            np.array([north[gi], north[tgi]]), z_ab,
            name_prefix=f"connector_{k:04d}", spacing=STATION_SPACING,
            skirt_extra=SKIRT_EXTRA, skirt_drop=SKIRT_DROP)
        connector_chunks.extend(chunks)
        junctions.append({"xy": (east[tgi], north[tgi]), "elev": pin_elev,
                           "radius_m": ROAD_HALF_WIDTH + PAD_OVERLAP_MARGIN_M,
                           "flat_seg": tseg, "n_roads": 2, "segs": {seg, tseg}})

    # write regraded elevation/XY back into per-segment lists for build_road_chunks
    off = 0
    for seg in raw["segments"]:
        cnt = len(seg["east_m"])
        seg["elev_m"] = regraded[off:off + cnt].tolist()
        seg["east_m"] = regraded_east[off:off + cnt].tolist()
        seg["north_m"] = regraded_north[off:off + cnt].tolist()
        off += cnt

    regrade_terrain_at_junctions(junctions, lat0, lon0, regraded_east, regraded_north, regraded,
                                  terrain_exclude)

    if "--solo" in sys.argv:
        solo_xy = None
        solo_count = 1
        solo_stub_len = None
        for arg in sys.argv:
            if arg.startswith("--solo-xy="):
                sx, sy = arg[len("--solo-xy="):].split(",")
                solo_xy = (float(sx), float(sy))
            elif arg.startswith("--solo-count="):
                solo_count = int(arg[len("--solo-count="):])
            elif arg.startswith("--solo-stublen="):
                solo_stub_len = float(arg[len("--solo-stublen="):])
        kwargs = {"num_junctions": solo_count}
        if solo_xy:
            kwargs["target_xy"] = solo_xy
        if solo_stub_len:
            kwargs["stub_len_m"] = solo_stub_len
        build_solo_junction(junctions, raw, **kwargs)
        return

    # Detected crossings only cover a single POINT per junction, so the
    # circular "inside" test below misses roads that converge at a shallow
    # angle and run close alongside each other for a real STRETCH before/
    # after that point rather than cutting cleanly through it (Taylor
    # screenshot, 2026-08-25: a curving loop road overlapping the main road
    # well past the circular pad, skirt wedge still poking through, plus
    # "roads .1m apart... dont actually connect"). Build a second,
    # independent test: is THIS station's ribbon within near-touching
    # distance of a road it's ALREADY known to meet at some junction
    # (JUNCTION_PARTNER_GAP_M - a bit looser than JUNCTION_OVERLAP_M to also
    # bridge a near-miss gap, not just literal overlap)? Scoping the search
    # to only the specific segments each junction's cluster already paired
    # together - not every segment on the map - matters a lot: a blanket
    # global widen (tried first) caught ~1900 pairs, almost all of them
    # ordinary roads that run near each other for a while without ever
    # actually meeting (frontage roads, parallel residential streets) and
    # would have papered over huge unrelated stretches with fill discs.
    junction_partners = defaultdict(set)
    for j in junctions:
        for a in j["segs"]:
            for b in j["segs"]:
                if a != b:
                    junction_partners[a].add(b)

    JUNCTION_PARTNER_GAP_M = JUNCTION_OVERLAP_M + 3.0
    overlap_cell = JUNCTION_PARTNER_GAP_M
    overlap_buckets = defaultdict(list)
    for i in range(len(east)):
        if not structural[i] and seg_ids[i] in junction_partners:
            overlap_buckets[(int(east[i] // overlap_cell), int(north[i] // overlap_cell))].append(i)
    overlap2 = JUNCTION_PARTNER_GAP_M ** 2

    def overlaps_other_road(gi, si):
        if structural[gi] or si not in junction_partners:
            return False
        partners = junction_partners[si]
        bx, by = int(east[gi] // overlap_cell), int(north[gi] // overlap_cell)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in overlap_buckets.get((bx + dx, by + dy), ()):
                    if seg_ids[j] not in partners:
                        continue
                    d2 = (east[gi] - east[j]) ** 2 + (north[gi] - north[j]) ** 2
                    if d2 <= overlap2 and abs(elev[gi] - elev[j]) <= JUNCTION_MAX_ELEV_DIFF_M:
                        return True
        return False

    fill_pads = []   # small discs patching proximity-only overlap, outside any pad circle

    all_chunks = list(connector_chunks)
    total_km = 0.0
    goff = 0
    for si, seg in enumerate(raw["segments"]):
        s_full = np.array(seg["s_m"])
        e_full = np.array(seg["east_m"])
        n_full = np.array(seg["north_m"])
        h_full = np.array(seg["elev_m"])
        is_tunnel = np.array(seg.get("is_tunnel", [False] * len(s_full)), dtype=bool)
        is_oneway_full = seg.get("oneway")
        runs = list(split_excluding_tunnels(is_tunnel))
        for run_idx, (lo, hi) in enumerate(runs):
            if hi - lo < 2:
                continue
            s, e, nn, h = s_full[lo:hi], e_full[lo:hi], n_full[lo:hi], h_full[lo:hi]
            z = road_ue_z_m(h, ROAD_LIFT, ORIGIN_HEIGHT_M)
            is_oneway = is_oneway_full[lo:hi] if is_oneway_full else None
            name = seg["name"] if len(runs) == 1 else f"{seg['name']}_p{run_idx}"

            # OMIT this road's ribbon entirely (not just its skirt) wherever
            # a station falls inside another road's junction pad. Flattening
            # only the skirt (the old fix) left the full ROAD_HALF_WIDTH-wide
            # paved ribbon itself running straight through the crossing at
            # its original XY path, unclipped - two full-width pavements
            # cross clean through each other with the disc merely sitting on
            # top, so from any angle but straight-down you still see both
            # ribbons' edges and lane stripes overlapping (2026-08-25,
            # Taylor: "its not just the curtain though, its the full road
            # that continues through"). Skipping ribbon geometry inside the
            # pad radius and relying on the (slightly oversized, see
            # PAD_OVERLAP_MARGIN_M) disc to cover that whole footprint avoids
            # ribbon-vs-ribbon overlap entirely instead of trying to paper
            # over it.
            inside_circle = np.array([
                any((e[i] - j["xy"][0]) ** 2 + (nn[i] - j["xy"][1]) ** 2 <= j["radius_m"] ** 2
                    for j in junctions)
                for i in range(len(e))])
            inside_prox = np.array([overlaps_other_road(goff + lo + i, si) for i in range(len(e))])
            inside = inside_circle | inside_prox
            for i in range(len(e)):
                if inside_prox[i] and not inside_circle[i]:
                    fill_pads.append((f"{name}_fill{i:04d}", (e[i], nn[i]), regraded[goff + lo + i]))
            p0 = 0
            sub_idx = 0
            for p1 in range(1, len(e) + 1):
                if p1 == len(e) or inside[p1] != inside[p0]:
                    if p1 - p0 >= 2 and not inside[p0]:
                        sub_name = name if len(e) == p1 - p0 else f"{name}_s{sub_idx:03d}"
                        sub_oneway = is_oneway[p0:p1] if is_oneway is not None else None
                        chunks, cx, cy, cz = build_road_chunks(
                            s[p0:p1], e[p0:p1], nn[p0:p1], z[p0:p1], name_prefix=sub_name,
                            spacing=STATION_SPACING, is_oneway=sub_oneway,
                            skirt_extra=SKIRT_EXTRA, skirt_drop=SKIRT_DROP)
                        all_chunks.extend(chunks)
                        total_km += float(s[p1 - 1] - s[p0]) / 1000.0
                        sub_idx += 1
                    p0 = p1
        goff += len(seg["east_m"])

    # Small overlapping discs (radius > half the 5m station spacing) patch
    # every proximity-only overlap station not already under a real
    # junction's pad - together they form a continuous strip covering
    # exactly the stretch where two ribbons actually got close, instead of
    # one oversized circle blanketing everything between two roads that
    # only touch at their ends (2026-08-25, Taylor: shallow-angle loop road
    # overlapping the main road well outside the crossing-point pad).
    # 2026-08-26: fuse every pad into its nearest already-built ribbon chunk
    # (merge_chunk_into) instead of appending it as its own standalone chunk -
    # see that function's docstring. Was: all_chunks.append(build_pad_chunk(...)).
    FILL_RADIUS_M = ROAD_HALF_WIDTH + PAD_OVERLAP_MARGIN_M
    n_merged, n_standalone = 0, 0
    for name, xy, elev_m in fill_pads:
        pad = build_pad_chunk(f"fillpad_{name}", xy, elev_m, FILL_RADIUS_M)
        host = nearest_chunk(all_chunks, xy)
        if host is not None:
            merge_chunk_into(host, pad)
            n_merged += 1
        else:
            all_chunks.append(pad)
            n_standalone += 1

    for k, j in enumerate(junctions):
        pad = build_pad_chunk(f"junctionpad_{k:04d}", j["xy"],
                               j["elev"], j["radius_m"] + PAD_OVERLAP_MARGIN_M)
        host = nearest_chunk(all_chunks, j["xy"])
        if host is not None:
            merge_chunk_into(host, pad)
            n_merged += 1
        else:
            all_chunks.append(pad)
            n_standalone += 1

    buckets = defaultdict(list)
    for chunk in all_chunks:
        cx, cy = chunk_centroid(chunk)
        tx, ty = int(cx // ROAD_TILE_SIZE_CM), int(cy // ROAD_TILE_SIZE_CM)
        buckets[(tx, ty)].append(chunk)

    if not BACKUP_DIR.exists():
        BACKUP_DIR.mkdir(parents=True)
        for p in OUT_DIR.glob(f"road_{AREA_NAME}_test_tile_*.bin"):
            (BACKUP_DIR / p.name).write_bytes(p.read_bytes())
        print(f"backed up original (unpatched) road tiles -> {BACKUP_DIR}")
    else:
        print(f"reusing existing backup baseline at {BACKUP_DIR}")

    gen_id_bytes = pack_generation_id(compute_generation_id())
    written = []
    for (tx, ty), tile_chunks in buckets.items():
        path = OUT_DIR / f"road_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(path, tile_chunks, gen_id_bytes)
        written.append(path)
    n_stale = 0
    written_set = {p.resolve() for p in written}
    for p in OUT_DIR.glob(f"road_{AREA_NAME}_test_tile_*.bin"):
        if p.resolve() not in written_set and p.parent != BACKUP_DIR:
            p.unlink()
            n_stale += 1

    for j in junctions:
        print(f"  junction @ ({j['xy'][0]:.0f},{j['xy'][1]:.0f}): {j['n_roads']} road(s) "
              f"radius={j['radius_m']:.1f}m elev={j['elev']:.1f}m flat_seg={j['flat_seg']}")
    print(f"{total_km:.2f} km of road rebuilt, {len(all_chunks)} chunk(s) "
          f"({len(junctions)} junction pad(s) + {len(fill_pads)} fill pad(s), "
          f"{n_merged} fused into a host ribbon chunk, {n_standalone} standalone) -> "
          f"{len(written)} tile(s), {n_stale} stale tile(s) removed")


if __name__ == "__main__":
    main()
