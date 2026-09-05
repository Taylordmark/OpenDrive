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
from build_road_network import split_excluding_tunnels, write_station_sidecar
from generation_id import compute_generation_id, pack_generation_id
from road_mesh_common import (ROAD_HALF_WIDTH, ROAD_LIFT, SKIRT_DROP, SKIRT_EXTRA,
                               STATION_SPACING, build_road_chunks, road_ue_z_m,
                               sample_mesh_height)
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, ROAD_TILE_SIZE_CM

OUT_DIR = Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
BACKUP_DIR = OUT_DIR / "_road_backup_junction_pads"
TERRAIN_BACKUP_DIR = OUT_DIR / "_terrain_backup_junction_pads"
SIDECAR_PATH = OUT_DIR / f"roadstations_{AREA_NAME}.bin"
SIDECAR_BACKUP_DIR = OUT_DIR / "_sidecar_backup_junction_pads"

# How close (UE-frame cm) a regraded station's ORIGINAL x/y has to land to a
# sidecar entry's x/y to count as "the same station" - both are computed by
# the exact same `east_m*100.0, -north_m*100.0` conversion (road_mesh_common.
# build_road_chunks / write_station_sidecar) from what should be the
# bit-identical underlying float, so a real match is a fraction of a
# millimeter apart; a few cm of slack only guards against float32-rounding
# in the on-disk sidecar (it's written as float32, this script does math in
# float64) without risking a false match to a different, merely-nearby
# station (station spacing is 500cm, so anything under ~50cm is unambiguous).
SIDECAR_MATCH_EPS_CM = 5.0

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
# 2026-08-27/28, Taylor correction: a merged pad's footprint must read as
# ONE smooth oval, not a convex-hull-of-circles polygon with visible
# corners/lobes (see _fit_ellipse_to_circles/build_pad_chunk_ellipse
# below). 24-32 was the requested tessellation density for that to read
# smooth at low-poly scale; 28 splits the difference.
ELLIPSE_SIDES = 28

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


def find_dangling_endpoints(east, north, elev, seg_ids, structural, bounds, junctions=()):
    """Segment start/end stations with no other segment's station within
    JUNCTION_OVERLAP_M (i.e. NOT already handled by find_crossings/clustering)
    but with one within ENDPOINT_SNAP_MAX_M - a real, conservatively-sized
    topology gap worth welding shut, not a crossing to detect.

    2026-08-26, Taylor (Green Valley "Lake Hughes" fork screenshot - a
    perpendicular stub not blending into the main road, dark torn seam where
    they should meet): a road that crosses several others near its own tail
    end (e.g. segment 60 in green_valley, which crosses THREE different
    roads at three separate interior points) can have its very last station
    land within a few meters of one of those SAME crossings' pad centers -
    find_crossings already builds a real junction/pad there from the interior
    edge, but this function used to only check raw distance-to-nearest-
    station, not whether a real junction already covers that point, so it
    ALSO flagged the identical spot as a dangling endpoint and built an
    independent straight connector spur on top of it. Two independently
    regraded/framed pieces of geometry (the pad's fan disc and the
    connector's own short, uncapped ribbon) covering nearly the same
    footprint is exactly what reads as a dark seam plus an abruptly-ending
    stub. `junctions` (already fully built by the time this runs in main())
    lets real coverage be checked directly instead of inferred from
    proximity alone."""
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

    def covered_by_real_junction(gi):
        # Real coverage, not just "something else is close" - a junction's
        # pad footprint (plus the same overlap margin build_pad_chunk itself
        # is drawn with) already caps this point, so an independent
        # connector spur here would only duplicate/conflict with it.
        px, py = east[gi], north[gi]
        for j in junctions:
            if point_in_pad(px, py, j):
                return True
        return False

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
        if covered_by_real_junction(gi) or covered_by_real_junction(best_j):
            continue   # a real crossing/cluster junction already pads this
                       # exact spot (or its target) - see docstring above
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

# 2026-08-27, Taylor screenshot (Lake Hughes town, a complex junction that
# baked as TWO separate pads at slightly different elevations, neither
# covering the whole intersection - a dark gap between them with approach
# roads dead-ending in vertical end caps at each pad's own edge). Root
# cause: cluster_crossings() only folds two pairwise crossings into ONE
# junction when they're within CLUSTER_MERGE_M (27m) of each other; OSM
# frequently digitizes one real, larger/more complex intersection as
# several graph nodes/crossings a bit FARTHER apart than that (wide
# intersection, offset stop lines, a slip lane) but still close enough that
# their drawn pad discs end up touching or nearly touching once
# pad_radius_m() sizes them - two correctly-detected, genuinely-real
# junctions that are nonetheless "the same intersection" and should bake as
# one. PAD_MERGE_NEAR_MARGIN_M is the EXTRA slack (beyond the two pads'
# own drawn radii, which already include PAD_OVERLAP_MARGIN_M) added when
# deciding whether two pads are "close enough to merge" - i.e. two pads
# merge if dist(centers) <= r_i_drawn + r_j_drawn + PAD_MERGE_NEAR_MARGIN_M,
# not only when they already overlap outright (gap<=0). Sized well below
# CLUSTER_MERGE_M (27m) and PAD_RADIUS_MAX_M (18m) - a scan of every
# junction pair in green_valley within 60m of each other found real
# node-split artifacts (same physical intersection, split by OSM) clustered
# tightly at gaps of 0-8m, then a gap to legitimately separate, closely
# strung intersections (subdivision T's every 20-40m) starting around 24m -
# 8.0m sits in that natural valley, catching the former without touching
# the latter.
PAD_MERGE_NEAR_MARGIN_M = 8.0


# --------------------------------------------------- footprint-derived pads
# 2026-09-05, Taylor ("many junction things spawning for a single
# intersection but they aren't merging properly into a single larger
# ellipse. let's talk about how we should be determining ellipse size and
# shape"): the first summit_county run splintered real intersections into
# several pads (nine along ~300m of US 6 at Keystone) because pads were
# circles sized only by crossing angle, and merging required two DRAWN
# circles' edges to come within PAD_MERGE_NEAR_MARGIN_M (8m) - small
# perpendicular-T circles 25m apart at the same real intersection never
# merged. Redesign per the discussion:
#   1. SIZE/SHAPE: every pad's ellipse is fitted to the actual
#      pavement-overlap POINT CLOUD (stations of member roads that sit
#      within pavement-overlap distance of another member road, pushed out
#      to their +-ROAD_HALF_WIDTH pavement-edge corners) instead of being
#      derived from angle-formula circles - a perpendicular T self-sizes to
#      a small near-circle, a shallow Y to a long thin ellipse exactly as
#      long as the real convergence, with no angle formula doing the work.
#   2. MERGE: "same intersection" is decided by participation and road
#      topology, not euclidean disc gap - see merge_same_intersection().
#   3. CAP: a merged footprint whose fitted semi-major axis exceeds
#      PAD_MERGE_MAX_SEMI_AXIS_M is NOT one intersection but a corridor
#      (parallel roads braiding, e.g. CO 9's two carriageways
#      cross-connecting 5 times along 114m, which run 1 fitted as a 93.5m
#      ellipse - a paved oval over a divided highway); the group is split
#      into consecutive under-cap sub-runs along the corridor axis instead.
ALONG_ROAD_MERGE_M = 22.0          # two junctions sharing ONE segment merge
                                    # when the distance ALONG that shared road
                                    # between them is under this - "offset
                                    # stop lines / slip lane", below the
                                    # natural 20-40m spacing of genuinely
                                    # separate subdivision T's (which also
                                    # involve DIFFERENT cross streets).
SAME_ROADS_MERGE_MAX_M = 60.0      # two junctions sharing TWO+ segments (the
                                    # same two roads crossing repeatedly)
                                    # merge out to this center distance.
PAD_MERGE_MAX_SEMI_AXIS_M = 25.0   # cap (semi-major, so 50m end to end) -
                                    # see CAP note above.
SINGLE_PAD_MAX_SEMI_AXIS_M = 15.0  # tighter cap for a SINGLE crossing
                                    # cluster's own pad: one real crossing of
                                    # 9m-wide roads never needs more than
                                    # ~30m end to end, and along a parallel-
                                    # road braid (continuous pavement
                                    # overlap) every crossing's cloud
                                    # otherwise fills the whole search box
                                    # and hits the merged cap - drawing a
                                    # row of giant overlapping ellipses.
                                    # Only a MERGED footprint may grow to
                                    # PAD_MERGE_MAX_SEMI_AXIS_M.
FOOTPRINT_SEARCH_M = 22.0          # box radius around a junction center for
                                    # collecting its members' overlap cloud.
                                    # Deliberately LOCAL (was 45 in the first
                                    # cut): along a parallel-road braid (CO 9's
                                    # two carriageways sit within pavement-
                                    # overlap distance for 114m straight) every
                                    # crossing's own cloud otherwise inhales
                                    # the whole braid, so each of its 5 pads
                                    # independently hit the 25m cap and drew a
                                    # row of giant overlapping ellipses. A pad
                                    # should cover ITS intersection; joining
                                    # neighbors is the merge rules' job, where
                                    # the corridor cap can actually veto.


def _fit_ellipse_to_points(pts, min_axis):
    """PCA-oriented minimal-ish covering ellipse of a plain point cloud -
    the zero-radius analogue of _fit_ellipse_to_circles (same center/
    orientation/axis-extent recipe, same uniform-inflation bisection for
    true containment, but testing the points themselves instead of 64
    boundary samples per member circle). Returns (cx, cy, a, b, theta)
    with margin=0 baked in; both semi-axes floored at min_axis."""
    n = len(pts)
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    cxx = cxy = cyy = 0.0
    for px, py in pts:
        dx, dy = px - cx, py - cy
        cxx += dx * dx
        cxy += dx * dy
        cyy += dy * dy
    cxx /= n
    cxy /= n
    cyy /= n
    if abs(cxx - cyy) < 1e-9 and abs(cxy) < 1e-9:
        theta = 0.0
    else:
        theta = 0.5 * math.atan2(2.0 * cxy, cxx - cyy)
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -math.sin(theta), math.cos(theta)

    dus, dvs = [], []
    a = b = max(min_axis, 1e-6)
    for px, py in pts:
        dx, dy = px - cx, py - cy
        du = dx * ux + dy * uy
        dv = dx * vx + dy * vy
        dus.append(du)
        dvs.append(dv)
        a = max(a, abs(du))
        b = max(b, abs(dv))

    def covers(k):
        aa, bb = a * k, b * k
        return all((du * du) / (aa * aa) + (dv * dv) / (bb * bb) <= 1.0
                   for du, dv in zip(dus, dvs))

    if not covers(1.0):
        hi = 1.0
        while not covers(hi):
            hi *= 2.0
        lo = hi / 2.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            if covers(mid):
                hi = mid
            else:
                lo = mid
        a, b = a * hi * 1.001, b * hi * 1.001
    return cx, cy, a, b, theta


def _junction_footprint_points(segs, center_xy, east, north, elev, bounds, structural,
                                search_m=FOOTPRINT_SEARCH_M):
    """The pavement-overlap point cloud for a junction: every station of a
    member segment (within search_m of the center) that sits within
    JUNCTION_OVERLAP_M of a DIFFERENT member segment's station at-grade,
    plus that station's two +-ROAD_HALF_WIDTH pavement-edge corners - the
    literal footprint where member roads' asphalt coexists."""
    cx, cy = center_xy
    members = {}
    for sgg in segs:
        lo, hi = bounds[sgg]
        idxs = np.arange(lo, hi + 1)
        sel = idxs[(np.abs(east[idxs] - cx) <= search_m)
                   & (np.abs(north[idxs] - cy) <= search_m)
                   & ~structural[idxs]]
        members[sgg] = sel

    pts = []
    ov2 = JUNCTION_OVERLAP_M ** 2
    for sgg, sel in members.items():
        other_list = [members[o] for o in segs if o != sgg and len(members[o])]
        if not other_list or len(sel) == 0:
            continue
        others = np.concatenate(other_list)
        ox, oy, oe = east[others], north[others], elev[others]
        lo, hi = bounds[sgg]
        for i in sel:
            d2 = (ox - east[i]) ** 2 + (oy - north[i]) ** 2
            hit = (d2 <= ov2) & (np.abs(oe - elev[i]) <= JUNCTION_MAX_ELEV_DIFF_M)
            if not hit.any():
                continue
            j0 = i - 1 if i - 1 >= lo else i
            j1 = i + 1 if i + 1 <= hi else i
            tx, ty = east[j1] - east[j0], north[j1] - north[j0]
            length = math.hypot(tx, ty) or 1.0
            nx, ny = -ty / length, tx / length
            pts.append((float(east[i]), float(north[i])))
            pts.append((float(east[i] + nx * ROAD_HALF_WIDTH),
                        float(north[i] + ny * ROAD_HALF_WIDTH)))
            pts.append((float(east[i] - nx * ROAD_HALF_WIDTH),
                        float(north[i] - ny * ROAD_HALF_WIDTH)))
    return pts


def footprint_ellipse(segs, center_xy, fallback_r, east, north, elev, bounds, structural,
                       search_m=FOOTPRINT_SEARCH_M, clamp=True):
    """Fit a pad ellipse to the junction's pavement-overlap cloud (see
    _junction_footprint_points). Falls back to a circle of fallback_r at
    center_xy when the cloud is degenerate (an endpoint-T whose strips
    barely overlap yields <3 points). clamp=False returns the raw fit so
    merge_same_intersection can test the cap before deciding to merge."""
    pts = _junction_footprint_points(segs, center_xy, east, north, elev, bounds,
                                      structural, search_m)
    if len(pts) < 3:
        r = max(fallback_r, PAD_RADIUS_MIN_M)
        return (center_xy[0], center_xy[1], r, r, 0.0)
    cx, cy, a, b, theta = _fit_ellipse_to_points(pts, PAD_RADIUS_MIN_M)
    if clamp:
        a = min(a, SINGLE_PAD_MAX_SEMI_AXIS_M)
        b = min(b, SINGLE_PAD_MAX_SEMI_AXIS_M)
        # Clamping shrinks the fit around the CLOUD centroid, which along a
        # corridor-shaped cloud can drift off the actual crossing anchor the
        # approaches are bent onto (snap_xy == center_xy) - the one point
        # the pad absolutely must cover, or the crossing region ships with
        # overlapping un-cut ribbons and no pad over them. Slide the center
        # toward the anchor just far enough that the anchor sits well
        # inside (>= PAD_RADIUS_MIN_M from the edge along that direction).
        ax_, ay_ = center_xy
        if not _point_in_ellipse(ax_, ay_, (cx, cy, a, b, theta),
                                  -PAD_RADIUS_MIN_M):
            dx, dy = ax_ - cx, ay_ - cy
            dist = math.hypot(dx, dy)
            # conservative: required containment radius along (dx,dy) is at
            # least min(a,b) - PAD_RADIUS_MIN_M; move so the anchor lands
            # within that of the new center
            keep = max(min(a, b) - PAD_RADIUS_MIN_M, 0.5)
            if dist > keep:
                t = (dist - keep) / dist
                cx += dx * t
                cy += dy * t
    return (cx, cy, a, b, theta)


def _ellipses_overlap(e1, e2, margin):
    """Approximate overlap test between two (cx, cy, a, b, theta) ellipses,
    each grown by margin: cheap center-distance reject, then mutual center
    containment, then 24 boundary samples of each tested against the other.
    Good enough for a merge decision (a false negative on a graze merely
    leaves two pads that visibly touch; the bake is still watertight since
    ribbons are cut against the SAME footprints that are drawn)."""
    c1x, c1y, a1, b1, _t1 = e1
    c2x, c2y, a2, b2, _t2 = e2
    if math.hypot(c1x - c2x, c1y - c2y) > (max(a1, b1) + max(a2, b2) + 2.0 * margin):
        return False
    if _point_in_ellipse(c2x, c2y, e1, margin) or _point_in_ellipse(c1x, c1y, e2, margin):
        return True
    for px, py in _ellipse_polygon(e1, margin, sides=24):
        if _point_in_ellipse(px, py, e2, margin):
            return True
    for px, py in _ellipse_polygon(e2, margin, sides=24):
        if _point_in_ellipse(px, py, e1, margin):
            return True
    return False


def merge_same_intersection(junctions, east, north, elev, bounds, structural):
    """Replaces merge_overlapping_pads (kept above for history). "Same real
    intersection" is decided by participation/topology, iterated to a
    fixpoint, instead of by euclidean gap between drawn circles:

      RULE 1 (same participants): two junctions whose road sets share 2+
        segments within SAME_ROADS_MERGE_MAX_M are the same two roads
        crossing repeatedly - dual carriageway cross-connects, loop mouths.
      RULE 2 (along-road distance): junctions sharing ONE segment merge when
        their separation ALONG that shared road is <= ALONG_ROAD_MERGE_M -
        offset stop lines / slip lanes, not two T's down the block (those
        involve different cross streets and sit 20-40m+ apart).
      RULE 3 (drawn overlap): two pads whose drawn footprints physically
        overlap merge regardless - two overlapping fans at two elevations is
        the original double-blob bug, never acceptable.

    Each merged group's ellipse is REFIT from the combined pavement-overlap
    cloud (not from the member ellipses), so the merged shape is the real
    footprint. A group whose raw refit exceeds PAD_MERGE_MAX_SEMI_AXIS_M is
    a corridor, not one intersection - it is SPLIT into consecutive sub-runs
    along the corridor's principal axis, each merged as large as the cap
    allows (see the CORRIDOR SPLIT comment in the body).

    Runs up to 4 rounds so a newly merged footprint that now overlaps or
    newly qualifies against a neighbor gets re-tested (the old single pass
    never re-checked)."""
    n_rounds = 0
    n_members_merged = 0
    n_split = 0
    for _round in range(4):
        n = len(junctions)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        n_unions = 0
        for i in range(n):
            ji = junctions[i]
            xi, yi = ji["xy"]
            for k in range(i + 1, n):
                jk = junctions[k]
                if ji.get("corridor") and jk.get("corridor"):
                    # both emitted by an over-cap corridor split - regrouping
                    # them just recreates the over-cap group every round
                    continue
                xk, yk = jk["xy"]
                dc = math.hypot(xi - xk, yi - yk)
                reach = (max(ji["ellipse"][2], ji["ellipse"][3])
                         + max(jk["ellipse"][2], jk["ellipse"][3])
                         + 2.0 * PAD_OVERLAP_MARGIN_M)
                if dc > max(SAME_ROADS_MERGE_MAX_M, reach):
                    continue
                shared = ji["segs"] & jk["segs"]
                rule = None
                if len(shared) >= 2 and dc <= SAME_ROADS_MERGE_MAX_M:
                    rule = 1
                if rule is None:
                    for sgg in shared:
                        ei, ek = ji["reps"].get(sgg), jk["reps"].get(sgg)
                        if (ei is not None and ek is not None
                                and abs(ei["s"] - ek["s"]) <= ALONG_ROAD_MERGE_M):
                            rule = 2
                            break
                if rule is None and _ellipses_overlap(ji["ellipse"], jk["ellipse"],
                                                       PAD_OVERLAP_MARGIN_M):
                    rule = 3
                if rule is not None:
                    if find(i) != find(k):
                        union(i, k)
                        n_unions += 1
        if n_unions == 0:
            break
        n_rounds += 1

        groups = defaultdict(list)
        for i in range(n):
            groups[find(i)].append(i)

        def build_merged(members, segs=None):
            """One merged junction dict from `members`, its ellipse REFIT
            from the UNION of each member's own locally-bounded pavement-
            overlap cloud (each member's points come from ITS OWN
            FOOTPRINT_SEARCH_M box around ITS OWN center - one big box
            around the group would inhale an entire parallel-road braid and
            over-cap ANY merge along it, even of two directly adjacent
            pads, defeating the corridor split entirely). Returns
            (junction, over_cap_raw) - the returned dict's ellipse is
            already clamped to the merged cap."""
            if segs is None:
                segs = set().union(*(m["segs"] for m in members))
            pts = []
            for m in members:
                pts.extend(_junction_footprint_points(
                    m["segs"], m["xy"], east, north, elev, bounds, structural))
            if len(pts) < 3:
                pts.extend((m["xy"][0], m["xy"][1]) for m in members)
            ell = _fit_ellipse_to_points(pts, PAD_RADIUS_MIN_M)
            over_cap = max(ell[2], ell[3]) > PAD_MERGE_MAX_SEMI_AXIS_M
            if over_cap:
                ell = (ell[0], ell[1], min(ell[2], PAD_MERGE_MAX_SEMI_AXIS_M),
                       min(ell[3], PAD_MERGE_MAX_SEMI_AXIS_M), ell[4])
            flattest = min(members, key=lambda m: m.get("flat_grade", 0.0))
            combined_reps = {}
            for m in members:
                for sgg, e in m["reps"].items():
                    combined_reps.setdefault(sgg, e)
            return ({
                "xy": (ell[0], ell[1]), "elev": flattest["elev"],
                "radius_m": max(ell[2], ell[3]), "shape": "ellipse",
                "ellipse": ell, "merged": True,
                "flat_seg": flattest["flat_seg"],
                "flat_grade": flattest.get("flat_grade", 0.0),
                "snap_xy": flattest["snap_xy"], "reps": combined_reps,
                "n_roads": len(combined_reps), "segs": segs,
            }, over_cap)

        new_junctions = []
        for root, idxs in groups.items():
            if len(idxs) == 1:
                new_junctions.append(junctions[idxs[0]])
                continue
            members = [junctions[x] for x in idxs]
            merged_j, over_cap = build_merged(members)
            if not over_cap:
                new_junctions.append(merged_j)
                n_members_merged += len(members)
                continue

            # CORRIDOR SPLIT: the whole group's footprint is longer than one
            # intersection can be, but leaving the members as-is keeps the
            # exact fragmented look this redesign exists to fix (a chain of
            # overlapping pads down a dual carriageway). Instead, order the
            # members along the corridor's own principal axis and greedily
            # merge consecutive RUNS, each as long as the refit stays under
            # the cap - a 300m braid becomes a few clean <=50m ovals instead
            # of nine overlapping blobs. Emitted pads (merged runs AND
            # leftover singles) are flagged "corridor" so later rounds don't
            # regroup them into the same over-cap group forever.
            mcx = sum(m["xy"][0] for m in members) / len(members)
            mcy = sum(m["xy"][1] for m in members) / len(members)
            cxx = cxy = cyy = 0.0
            for m in members:
                dx, dy = m["xy"][0] - mcx, m["xy"][1] - mcy
                cxx += dx * dx
                cxy += dx * dy
                cyy += dy * dy
            axis = 0.5 * math.atan2(2.0 * cxy, cxx - cyy) \
                if (abs(cxx - cyy) > 1e-9 or abs(cxy) > 1e-9) else 0.0
            axv = (math.cos(axis), math.sin(axis))
            ordered = sorted(members, key=lambda m: m["xy"][0] * axv[0] + m["xy"][1] * axv[1])

            run = [ordered[0]]
            emitted = []
            for m in ordered[1:]:
                cand_j, cand_over = build_merged(run + [m])
                if cand_over:
                    emitted.append(run)
                    run = [m]
                else:
                    run = run + [m]
            emitted.append(run)
            for r in emitted:
                if len(r) == 1:
                    r[0]["corridor"] = True
                    new_junctions.append(r[0])
                else:
                    sub_j, _ = build_merged(r)
                    sub_j["corridor"] = True
                    new_junctions.append(sub_j)
                    n_members_merged += len(r)
            n_split += 1
        junctions = new_junctions

    print(f"merge_same_intersection: {n_members_merged} pad(s) folded into merged "
          f"footprint ellipses over {n_rounds} round(s), {n_split} over-cap corridor "
          f"group(s) split into consecutive sub-runs -> {len(junctions)} junction(s)")
    return junctions


def cluster_crossings(crossings):
    """Groups crossings whose xy falls within CLUSTER_MERGE_M of each other.

    2026-08-27, Taylor (green_valley terrain-cliff investigation): the
    original implementation used plain union-find single-linkage - i.e. two
    crossings merge if EITHER is within CLUSTER_MERGE_M of the OTHER,
    transitively. Single-linkage has no bound on the resulting group's own
    diameter: a dense residential subdivision grid (real, closely-spaced,
    but individually SEPARATE T-intersections every 20-30m along a street)
    chains crossing-to-crossing all the way down the block, collapsing
    dozens of genuinely distinct intersections into ONE "junction" whose
    bounding box can span hundreds of meters (measured live on green_valley:
    a single chained group of 50 pairwise crossings across segments 1780/
    1998/2864/2955/3382/3852 spanning ~220m). That one junction then picks
    ONE flat_seg/elevation/snap-anchor for the whole group, so every OTHER
    member's XY snap distance (see the snap_xy blend in main()) balloons up
    to that group's full span (measured: one station snapped 193m from its
    original position) instead of the intended "meters, not tens of
    meters" a real single intersection produces. That in turn (a) blows a
    canyon-sized hole in the terrain regrade/carve near that spot (a huge
    contributor to the terrain-cliff bug this investigation started from)
    and (b) leaves the ORIGINAL (pre-snap) station position with no nearby
    road geometry at all, showing up as a verify_road_fit.py "no road tile/
    triangle covering this station" miss.

    Fixed by switching to a SEED-RADIUS grouping instead: each group is
    seeded by its lowest-index not-yet-assigned crossing, and only pulls in
    OTHER crossings within CLUSTER_MERGE_M of THAT SAME SEED point (not
    transitively from whatever else has already joined the group). This
    caps every group's bounding diameter at 2*CLUSTER_MERGE_M by
    construction - generous enough to still catch a real, tight multi-way
    cluster (the documented "a real 3+-way where road C meets both A and B
    a few meters apart" case, all within one CLUSTER_MERGE_M-radius disc of
    each other) while making the subdivision-chain failure mode
    geometrically impossible."""
    n = len(crossings)
    assigned = [False] * n
    groups = []
    r2 = CLUSTER_MERGE_M ** 2
    for seed in range(n):
        if assigned[seed]:
            continue
        sx, sy = crossings[seed]["xy"]
        assigned[seed] = True
        group = [seed]
        for j in range(seed + 1, n):
            if assigned[j]:
                continue
            xj, yj = crossings[j]["xy"]
            if (sx - xj) ** 2 + (sy - yj) ** 2 <= r2:
                assigned[j] = True
                group.append(j)
        groups.append([crossings[i] for i in group])
    return groups


def build_pad_chunk(name, center_xy, elev_m, radius_m, sides=PAD_SIDES):
    """A flat circular disc at the pinned junction elevation - "junction is
    at the same grade as the flattest road" means constant Z, not a sloped
    surface, so this is a plain fan, no per-vertex elevation interpolation
    needed (unlike v1/v2's hull-of-corners approach)."""
    cx, cy = center_xy
    z_m = road_ue_z_m(elev_m, ROAD_LIFT, ORIGIN_HEIGHT_M)
    ring = [(cx + radius_m * math.cos(2.0 * math.pi * k / sides),
             cy + radius_m * math.sin(2.0 * math.pi * k / sides)) for k in range(sides)]
    return _build_pad_fan(name, (cx, cy), z_m, ring)


def build_pad_chunk_hull(name, hull_members, elev_m, margin, sides=PAD_SIDES):
    """Like build_pad_chunk, but the ring is the convex-hull-of-circles
    polygon _hull_polygon() computes for a merged pad (see
    merge_overlapping_pads) instead of a plain circle - same fan topology,
    UVs, and winding, just a differently-shaped ring.

    SUPERSEDED 2026-08-27/28 (Taylor correction): a merged pad's footprint
    must read as one smooth oval, not a convex hull with visible corners/
    lobes - see build_pad_chunk_ellipse below, which merge_overlapping_pads
    and main() now actually call. Left this (and _hull_polygon/
    _convex_hull/_point_in_convex_polygon below) in place, unused, only as
    documented history of the approach that was tried and rejected - not
    called from anywhere anymore."""
    ring = _hull_polygon(hull_members, margin, sides)
    cx = sum(p[0] for p in ring) / len(ring)
    cy = sum(p[1] for p in ring) / len(ring)
    z_m = road_ue_z_m(elev_m, ROAD_LIFT, ORIGIN_HEIGHT_M)
    return _build_pad_fan(name, (cx, cy), z_m, ring)


def build_pad_chunk_ellipse(name, ellipse, elev_m, margin, sides=ELLIPSE_SIDES):
    """Like build_pad_chunk, but the ring is the fitted-ellipse boundary
    _ellipse_polygon() computes for a merged pad (see
    _fit_ellipse_to_circles/merge_overlapping_pads) instead of a plain
    circle - same fan topology, UVs, and winding, just a differently-
    shaped ring. The hub vertex is the ellipse's own center (well-defined,
    unlike build_pad_chunk_hull's average-of-ring-points approximation)."""
    cx, cy, _a0, _b0, _theta = ellipse
    ring = _ellipse_polygon(ellipse, margin, sides)
    z_m = road_ue_z_m(elev_m, ROAD_LIFT, ORIGIN_HEIGHT_M)
    return _build_pad_fan(name, (cx, cy), z_m, ring)


def _build_pad_fan(name, hub_xy, z_m, ring):
    """Shared fan-mesh builder for build_pad_chunk/build_pad_chunk_hull:
    hub vertex 0 at hub_xy (UE-frame elevation z_m) plus a ring of (x, y)
    points (pipeline-frame meters), triangulated as a fan."""
    cx, cy = hub_xy
    sides = len(ring)
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
    for vx, vy in ring:
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


def _pad_draw_radius(j):
    """The actual on-disk footprint of a junction's disc - same
    "+PAD_OVERLAP_MARGIN_M" build_pad_chunk itself is always called with, so
    two pads whose DRAWN shapes touch/overlap (not just their bare
    radius_m) are the ones that need merging. Only meaningful for a plain
    circular junction (merge_overlapping_pads' own overlap test, before any
    merged "hull" shapes exist yet)."""
    return j["radius_m"] + PAD_OVERLAP_MARGIN_M


def _convex_hull(points):
    """Monotone-chain (Andrew's algorithm) 2D convex hull. Returns CCW-
    ordered vertices, no repeated closing point. Pure Python, no scipy
    dependency (matches the rest of this file)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _hull_polygon(hull_members, margin, sides):
    """Convex hull of a UNION of circles (cx, cy, r), each grown by
    `margin` first (a disk grown by a scalar is just a bigger disk, so
    margin-then-hull == hull-then-offset for a uniform margin here).

    An earlier version tried to build this straight from each disk's exact
    2D support function - for direction unit-vector d, a disk's support
    value (center-origin)*d + r is exact - by plotting, for `sides` evenly
    spaced angles theta, a point at (support value) along direction theta
    FROM THE CENTROID. That is wrong in general: the support function's
    value at direction d is the position of the true boundary point
    PROJECTED onto d, not that point's distance from the centroid along d
    - the two only coincide when the tangent point happens to lie exactly
    on the ray from the centroid in direction d, which is only true for a
    disk centered on the centroid itself. For every other (off-center)
    member it silently plotted a point INSIDE the true hull, clipping that
    member's own far edge and producing a polygon that failed to cover
    points that are unambiguously inside one of the original disks
    (caught by a coverage unit test, not by the one real dataset this was
    exercised against - none of green_valley's junction clusters happened
    to overlap when this bug was live).

    The robust fix: sample each (margin-grown) disk's circumference
    directly and take the discrete 2D convex hull of all the sample
    points. This has no such projection error for any arrangement of
    members - by construction every sampled boundary point is a real point
    of the union, and the hull of real points can only ever wrap them, not
    clip them. `sides` here means "total boundary samples across all
    members" (split evenly per member, minimum 24 each) rather than the
    output vertex count - the convex hull only keeps whichever samples are
    actually extreme points, so the returned polygon is usually much
    smaller than `sides`. As with the old approach, connecting samples
    with straight chords slightly undershoots each member's true circular
    arc between samples; that sagitta is well inside the slack
    PAD_OVERLAP_MARGIN_M already budgets for this kind of gap elsewhere in
    this file (see that constant's own docstring)."""
    per_member = max(sides // max(len(hull_members), 1), 24)
    pts = []
    for mx, my, mr in hull_members:
        r = mr + margin
        for k in range(per_member):
            theta = 2.0 * math.pi * k / per_member
            pts.append((mx + r * math.cos(theta), my + r * math.sin(theta)))
    return _convex_hull(pts)


def _point_in_convex_polygon(px, py, pts, tol=1e-6):
    """Standard convex-polygon membership test: the point is inside iff
    every polygon edge (traversed in the same order pts is already in) has
    it on the same side. _hull_polygon always emits points in increasing-
    theta (consistently CCW) order, so this is safe to call directly on its
    output.

    _hull_polygon's boundary includes long, nearly-straight runs of samples
    wherever the true hull-of-disks boundary follows a flat tangent-line
    facet between two members (as opposed to an arc). Along such a run,
    consecutive sampled points are almost exactly collinear, so the raw
    cross product is a tiny value whose SIGN is floating-point noise rather
    than signal - comparing it against exactly 0.0 lets that noise flip
    `sign` and reject points that are genuinely inside. Treat any
    near-zero cross product (|cross| <= tol) as "on the line" instead of
    picking a side."""
    sign = 0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        cross = (x2 - x1) * (py - y1) - (y2 - y1) * (px - x1)
        if abs(cross) > tol:
            s = 1 if cross > 0.0 else -1
            if sign == 0:
                sign = s
            elif s != sign:
                return False
    return True


def _fit_ellipse_to_circles(hull_members):
    """Fits a single ellipse over a merged group's member pad circles
    (mx, my, mr) - 2026-08-27/28, Taylor: the hull-of-circles shape
    (see _hull_polygon above) reads as a lumpy multi-cornered blob for a
    real merged intersection, not the single smooth oval a driver expects.
    This replaces it with an actual fitted ellipse, chosen so it fully
    covers every member's drawn circle:

      - center: each member's (x, y) weighted by its OWN radius_m (a
        bigger member pulls the shared center further toward itself, same
        intuition as a mass-weighted centroid).
      - orientation: the principal axis of the (radius-weighted) 2x2
        covariance of member centers around that center, via the closed-
        form 2x2 symmetric-matrix eigenvector angle
        theta = 0.5*atan2(2*cxy, cxx-cyy). A degenerate/near-concentric
        group (covariance ~ 0) falls back to theta=0, which is harmless -
        see the semi-axis sizing below: a near-concentric group naturally
        comes out with a~=b (near-circular) regardless of which angle
        theta lands on, exactly the "a circle IS an ellipse, don't
        special-case it" behavior Taylor asked for.
      - semi-axes, base size: for each member, project its center onto the
        major/minor unit directions (du, dv) and take a = max(|du_i|+r_i),
        b = max(|dv_i|+r_i) - "center-projection extent + that member's
        own radius along each axis", per Taylor's spec.
      - semi-axes, coverage fix-up: the base (a, b) above is necessary but
        NOT sufficient in general - a member circle offset DIAGONALLY from
        the ellipse center can still poke past an ellipse whose axis
        projections merely bound that circle's own axis-aligned bounding
        box, the exact same "support-function-vs-axis-projection" trap
        _hull_polygon's own docstring documents for the old (rejected)
        hull approach (verified against a synthetic 3-circle case while
        implementing this - the literal spec formula alone left ~10% of
        a large member's own boundary outside the ellipse at the diagonal
        points). Fixed by uniformly inflating the base (a, b) - scaling
        both by the same factor k>=1, so the fitted shape/orientation from
        the spec's formula is preserved, just grown - via bisection on k
        against every member's TRUE circular boundary (64 samples/member,
        not just its bounding box), so the final (a, b) provably contains
        every member circle in full, not merely its axis-aligned extent.

    Returns (cx, cy, a, b, theta) with margin=0 baked in - callers add
    PAD_OVERLAP_MARGIN_M (or whatever margin they need) on top of the
    returned (a, b) themselves (see _ellipse_polygon/_point_in_ellipse),
    which is EXACT, not an approximation: shifting every member's r by a
    constant shifts every max() term (and, by the same argument, every
    per-sample containment bound checked below) by that same constant, so
    growing an already-covering (a, b) by a uniform +margin can only ever
    grow its coverage margin further, never shrink it. a/b are NOT ordered
    by magnitude (a is always the extent along the theta axis, b along
    theta+90deg) - an ellipse doesn't care which is numerically bigger,
    and neither does _ellipse_polygon/_point_in_ellipse."""
    wsum = sum(mr for _mx, _my, mr in hull_members)
    if wsum <= 0.0:
        n = float(len(hull_members))
        cx = sum(mx for mx, _my, _mr in hull_members) / n
        cy = sum(my for _mx, my, _mr in hull_members) / n
    else:
        cx = sum(mx * mr for mx, _my, mr in hull_members) / wsum
        cy = sum(my * mr for _mx, my, mr in hull_members) / wsum

    cxx = cxy = cyy = 0.0
    wtot = 0.0
    for mx, my, mr in hull_members:
        w = max(mr, 1e-6)
        dx, dy = mx - cx, my - cy
        cxx += w * dx * dx
        cxy += w * dx * dy
        cyy += w * dy * dy
        wtot += w
    cxx /= wtot
    cxy /= wtot
    cyy /= wtot
    if abs(cxx - cyy) < 1e-9 and abs(cxy) < 1e-9:
        theta = 0.0
    else:
        theta = 0.5 * math.atan2(2.0 * cxy, cxx - cyy)
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -math.sin(theta), math.cos(theta)

    a = b = 0.0
    for mx, my, mr in hull_members:
        dx, dy = mx - cx, my - cy
        du = dx * ux + dy * uy
        dv = dx * vx + dy * vy
        a = max(a, abs(du) + mr)
        b = max(b, abs(dv) + mr)
    a = max(a, 1e-6)
    b = max(b, 1e-6)

    # Coverage fix-up (see docstring): find the smallest uniform scale
    # k >= 1 such that scaling (a, b) by k makes the ellipse contain every
    # sampled point on every member's TRUE circular boundary, not just the
    # base formula's axis-projection bound. covers(k) is monotonic in k
    # (scaling an ellipse up can only ever grow the region it covers), so
    # a doubling search for an upper bound followed by bisection converges
    # to the exact minimal covering k.
    def covers(k):
        aa, bb = a * k, b * k
        for mx, my, mr in hull_members:
            dx, dy = mx - cx, my - cy
            for s in range(64):
                t = 2.0 * math.pi * s / 64
                px, py = mx + mr * math.cos(t), my + mr * math.sin(t)
                pdx, pdy = px - cx, py - cy
                du = pdx * ux + pdy * uy
                dv = pdx * vx + pdy * vy
                if (du * du) / (aa * aa) + (dv * dv) / (bb * bb) > 1.0:
                    return False
        return True

    if not covers(1.0):
        hi = 1.0
        while not covers(hi):
            hi *= 2.0
        lo = hi / 2.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            if covers(mid):
                hi = mid
            else:
                lo = mid
        # tiny safety pad beyond the bisected bound - 64 boundary samples
        # per member approximates the true (continuous) circle closely but
        # not exactly, so nudge past the numerically-found threshold
        # rather than sit exactly on it.
        a, b = a * hi * 1.001, b * hi * 1.001

    return cx, cy, a, b, theta


def _ellipse_polygon(ellipse, margin, sides=ELLIPSE_SIDES):
    """Tessellates the boundary of `ellipse` (cx, cy, a, b, theta, all with
    margin=0 baked in - see _fit_ellipse_to_circles) grown by `margin`,
    into `sides` evenly parametric-angle-spaced points (not true arc-
    length spacing - fine for a rendering/carve boundary at this vertex
    count, same as the plain-circle pads build_pad_chunk already draws
    this way). CCW by construction (angle increases 0->2pi)."""
    cx, cy, a0, b0, theta = ellipse
    a, b = a0 + margin, b0 + margin
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -math.sin(theta), math.cos(theta)
    pts = []
    for k in range(sides):
        t = 2.0 * math.pi * k / sides
        ct, st = math.cos(t), math.sin(t)
        pts.append((cx + a * ct * ux + b * st * vx,
                     cy + a * ct * uy + b * st * vy))
    return pts


def _point_in_ellipse(px, py, ellipse, margin):
    """Exact analytic point-in-ellipse test (no polygon tessellation
    needed) for `ellipse` (margin=0 baked in) grown by `margin` - see
    _fit_ellipse_to_circles for why growing a/b by a constant margin this
    way is exact, not approximate."""
    cx, cy, a0, b0, theta = ellipse
    a, b = a0 + margin, b0 + margin
    if a <= 0.0 or b <= 0.0:
        return px == cx and py == cy
    ux, uy = math.cos(theta), math.sin(theta)
    vx, vy = -math.sin(theta), math.cos(theta)
    dx, dy = px - cx, py - cy
    du = dx * ux + dy * uy
    dv = dx * vx + dy * vy
    return (du * du) / (a * a) + (dv * dv) / (b * b) <= 1.0


def point_in_pad(px, py, j, margin=PAD_OVERLAP_MARGIN_M):
    """True if (px, py) (pipeline-frame meters) falls inside junction j's
    footprint - a plain circle for an ordinary junction, or the fitted
    ellipse merge_overlapping_pads() built for a merged one (see
    _fit_ellipse_to_circles). `margin` defaults to the same
    PAD_OVERLAP_MARGIN_M build_pad_chunk itself always draws with (used by
    covered_by_real_junction(), which wants "is this inside the actual
    drawn pad"); pass margin=0 for the smaller bare-radius keep-out test
    the ribbon-omission code uses (which deliberately stays a bit inside
    the drawn pad edge, not flush with it - see PAD_OVERLAP_MARGIN_M's own
    docstring)."""
    if j.get("shape") == "ellipse":
        return _point_in_ellipse(px, py, j["ellipse"], margin)
    r = j["radius_m"] + margin
    return (px - j["xy"][0]) ** 2 + (py - j["xy"][1]) ** 2 <= r * r


def merge_overlapping_pads(junctions):
    """Union-find junctions whose DRAWN pad discs overlap, nearly touch, or
    (see PAD_MERGE_NEAR_MARGIN_M) sit close enough to be the SAME real
    intersection split across multiple OSM graph nodes, and replace each
    group of 2+ with a single smooth convex pad covering the union instead
    of shipping two (or more) separate, disjoint fans.

    2026-08-26, Taylor screenshot (Elizabeth Lake Road / Andrade Corner):
    two real crossing CLUSTERS a bit too far apart for cluster_crossings'
    CLUSTER_MERGE_M to fold into one junction, but close enough that their
    drawn discs overlapped outright, rendered as a lumpy double blob - each
    disc is individually correct, the problem is purely "two shapes where
    the driver expects one."

    2026-08-27, Taylor screenshot (Lake Hughes town): a related but
    DISTINCT failure mode - two pads that DON'T overlap at all, so the
    original version of this function (gap<=0 only) left them exactly as
    two separate discs at two independently-chosen elevations, with a
    strip of un-padded, un-clipped ground between/around them (the "gap
    reads as a floating dark disc, roads dead-end in vertical caps" report -
    each ribbon is cleanly cut at its own pad's edge, same as always, but
    nothing covers the space between the two pad edges). The merge test
    below now also fires when the gap is small but positive (see
    PAD_MERGE_NEAR_MARGIN_M's own docstring for why that particular slack
    value was chosen and what it deliberately does NOT catch).

    This runs on the crossing-derived `junctions` list BEFORE
    find_dangling_endpoints(), so covered_by_real_junction() (via
    point_in_pad) already sees the merged footprint - a dangling endpoint
    that falls in the gap between the original circles, but inside the
    merged hull, is correctly treated as already covered instead of
    getting an independent connector spur double-padded on top.

    Geometry: the merged footprint is exactly the convex hull of the union
    of the group's own circles (see _hull_polygon) - by construction this
    can only ever be a strict superset of every original disc PLUS the
    space between them, so the merge never regresses coverage, for a pair
    or for a larger multi-junction cluster alike (no PCA/ellipse-fit
    approximation that could clip a member's far edge). Elevation is a
    SINGLE value, not averaged: it reuses the elevation of whichever
    member's own flat_seg had the smallest local grade (the same
    "flattest road keeps its profile, junction sits at its height" rule
    build_road_chunks applies to a single junction, extended across the
    whole merged group). `reps`/`flat_seg` from every member are unioned
    onto the merged junction too (first-seen wins on a shared seg) so the
    caller can re-run the transition-blend regrade for every connected
    road across the WHOLE merged group toward this one elevation - without
    that follow-up pass (done by main(), see its own comment), a member
    that used to be its own cluster's flattest road would still carry its
    ORIGINAL unregraded profile even after being folded into a merged pad
    pinned to a different member's elevation, which would just relocate
    the elevation-mismatch bug from "two discs" to "one disc, one abrupt
    step where an unregraded approach meets it"."""
    n = len(junctions)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        xi, yi = junctions[i]["xy"]
        ri = _pad_draw_radius(junctions[i])
        for k in range(i + 1, n):
            xk, yk = junctions[k]["xy"]
            rk = _pad_draw_radius(junctions[k])
            if (xi - xk) ** 2 + (yi - yk) ** 2 <= (ri + rk + PAD_MERGE_NEAR_MARGIN_M) ** 2:
                union(i, k)

    groups = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    merged = []
    n_groups_merged = 0
    merge_log = []
    for idxs in groups.values():
        if len(idxs) == 1:
            merged.append(junctions[idxs[0]])
            continue
        n_groups_merged += 1
        members = [junctions[i] for i in idxs]
        hull_members = [(j["xy"][0], j["xy"][1], j["radius_m"]) for j in members]
        ellipse = _fit_ellipse_to_circles(hull_members)
        cx, cy, a0, b0, theta = ellipse
        # scalar "reach" for callers that only know about plain circles
        # (regrade_terrain_at_junctions' tile footprint, the end-of-run
        # summary print) - a0/b0 already guarantee full member coverage
        # along their own axes (see _fit_ellipse_to_circles), so the
        # larger of the two is never an undersized bound for a generic
        # "how far from xy could this pad's edge reach" question.
        radius_m = max(a0, b0)
        ellipse_area_m2 = math.pi * a0 * b0

        flattest = min(members, key=lambda j: j.get("flat_grade", 0.0))

        # Union every member's road-approach reps onto the merged junction
        # (first-seen wins on a shared seg, same dedup rule the per-cluster
        # loop already uses) - see the docstring above for why main() needs
        # this to re-blend every OTHER member's approaches toward the one
        # elevation actually used here, not just the flattest member's own.
        combined_reps = {}
        for m in members:
            for seg, e in m.get("reps", {}).items():
                combined_reps.setdefault(seg, e)

        merged.append({
            "xy": (cx, cy),
            "elev": flattest["elev"],
            # generic "reach" for callers that only know about plain
            # circles (regrade_terrain_at_junctions' tile footprint, the
            # end-of-run summary print) - see comment above.
            "radius_m": radius_m,
            "shape": "ellipse",
            "hull_members": hull_members,
            "ellipse": ellipse,
            "flat_seg": flattest["flat_seg"], "flat_grade": flattest.get("flat_grade", 0.0),
            "snap_xy": flattest["xy"],
            "reps": combined_reps,
            "n_roads": len(combined_reps),
            "segs": set().union(*(j["segs"] for j in members)),
        })
        member_pts = ", ".join(f"({j['xy'][0]:.0f},{j['xy'][1]:.0f}) r={j['radius_m']:.1f}m "
                                f"elev={j['elev']:.2f}m segs={sorted(j['segs'])}" for j in members)
        merge_log.append(
            f"merge: {len(members)} pad(s) -> 1 ellipse pad @ ({cx:.0f},{cy:.0f}) "
            f"a={a0:.1f}m b={b0:.1f}m theta={math.degrees(theta):.0f}deg "
            f"elev={flattest['elev']:.2f}m (from seg {flattest['flat_seg']}) "
            f"area={ellipse_area_m2:.0f}m^2 <- [{member_pts}]")

    if n_groups_merged:
        print(f"merged {sum(len(g) for g in groups.values() if len(g) > 1)} overlapping/near pad(s) "
              f"into {n_groups_merged} combined ellipse pad(s) "
              f"({n} junction(s) -> {len(merged)} after merge)")
        for line in merge_log:
            print("  " + line)
    return merged


def _polygon_area(pts):
    """Shoelace formula - used only for the merge-log's human-readable
    footprint size, not for any geometry/collision decision."""
    n = len(pts)
    if n < 3:
        return 0.0
    a = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


# How far (m, box radius) from a junction/connector center an original
# station coordinate can plausibly be left uncovered by the XY snap /
# inside-pad ribbon omission - sized from the first summit_county run's
# measured misses (all within ~70m: TRANSITION_LEN_M of bend plus pad
# reach), with slack. Only stations inside this box get the (relatively
# expensive) per-station coverage sample in add_gate_coverage_fills().
GATE_COVER_SEARCH_M = 120.0


def add_gate_coverage_fills(all_chunks, junctions, east, north, regraded, gate_exclude):
    """verify_road_fit.py checks that every non-excluded ORIGINAL station
    coordinate (road_stations.json, which this script never edits) still has
    road surface directly above it. The XY snap + inside-pad ribbon omission
    can strand an original coordinate just off the rebuilt ribbon and outside
    every pad (measured on summit_county's first pad run, 2026-09-05: 520 of
    137,141 in-scope stations, every one within ~70m of a junction or
    connector weld). Physically the road is fine - the driver is on the
    snapped geometry - but that coverage gate is exactly what catches
    genuinely missing/stale tiles, so rather than teaching the verifier
    about pads (weakening it for everything else), guarantee its invariant
    from this side: sample every near-junction station's original XY against
    the geometry just built, and cap each uncovered one with a small fill
    disc at that station's own regraded elevation, fused into its nearest
    ribbon chunk (same build_pad_chunk/merge_chunk_into machinery the
    prox-overlap fill pads already use, same collision-island reasoning).

    The sampling mirrors verify_road_fit._sample_with_neighbors exactly:
    chunks bucketed by centroid into ROAD_TILE_SIZE_CM tiles, station's own
    tile plus 2 rings of neighbors (a chunk spans up to 750m, more than one
    500m tile)."""
    if not junctions:
        return 0
    near = np.zeros(len(east), dtype=bool)
    for j in junctions:
        jx, jy = j["xy"]
        near |= ((np.abs(east - jx) <= GATE_COVER_SEARCH_M)
                 & (np.abs(north - jy) <= GATE_COVER_SEARCH_M))
    cand = np.where(near & ~gate_exclude)[0]

    tile_chunks = defaultdict(list)
    for chunk in all_chunks:
        cx, cy = chunk_centroid(chunk)
        tile_chunks[(int(cx // ROAD_TILE_SIZE_CM), int(cy // ROAD_TILE_SIZE_CM))].append(chunk)
    flat = {}
    for key, chunks in tile_chunks.items():
        pos, tris, base = [], [], 0
        for c in chunks:
            v = np.asarray(c["vertices"], dtype=np.float64).reshape(-1, 3)
            t = np.asarray(c["triangles"], dtype=np.int64).reshape(-1, 3)
            if len(v) == 0:
                continue
            pos.append(v)
            tris.append(t + base)
            base += len(v)
        if pos:
            flat[key] = (np.concatenate(pos), np.concatenate(tris))

    misses = []
    for i in cand:
        x_cm, y_cm = east[i] * 100.0, -north[i] * 100.0
        tx0, ty0 = int(x_cm // ROAD_TILE_SIZE_CM), int(y_cm // ROAD_TILE_SIZE_CM)
        covered = False
        for dx in range(-2, 3):
            for dy in range(-2, 3):
                geo = flat.get((tx0 + dx, ty0 + dy))
                if geo is not None and sample_mesh_height(geo[0], geo[1], x_cm, y_cm) is not None:
                    covered = True
                    break
            if covered:
                break
        if not covered:
            misses.append(int(i))

    for k, i in enumerate(misses):
        xy = (float(east[i]), float(north[i]))
        pad = build_pad_chunk(f"gatecover_{k:04d}", xy, float(regraded[i]),
                              ROAD_HALF_WIDTH + PAD_OVERLAP_MARGIN_M)
        host = nearest_chunk(all_chunks, xy)
        if host is not None:
            merge_chunk_into(host, pad)
        else:
            all_chunks.append(pad)
    print(f"gate-coverage pass: {len(cand)} near-junction station(s) sampled, "
          f"{len(misses)} uncovered original coordinate(s) capped with a fill disc")
    return len(misses)


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


# ------------------------------------------------------------- pad->terrain rim anchors
# 2026-08-27, Taylor (live drive-test: a junction pad sitting proud of the
# surrounding terrain with a sheer cliff at its boundary). Root cause: the
# normal terrain carve (build_terrain_mesh_tiles.carve_grid_m) blends every
# grid point toward its NEAREST STATION's elevation - which works great
# ALONG a road corridor, where "nearest station" is always a point on that
# same road whose regraded elevation already agrees with the ground it sits
# on. A junction PAD, though, is a flat disc (or hull, for a merged multi-
# junction group) whose radius can reach well past either road's own
# ROAD_HALF_WIDTH - especially a shallow-angle crossing, where pad_radius_m()
# deliberately grows the disc to cover the elongated overlap parallelogram.
# Out at the wide part of that disc, away from either centerline, the
# terrain grid's OWN nearest station can be a perfectly ordinary, un-
# regraded point well down one of the roads (not blended toward the
# junction elevation at all) - so the carve there tracks that other point's
# natural grade instead of the pad's constant height, and the two disagree
# right at the pad edge by however much that road's grade changed over the
# gap. Measured across every junction in green_valley (throwaway analysis,
# not part of this file): median edge-vs-terrain step ~0.4m, worst case
# 6.5m, with ~7% of junctions over 1m - not universal, but a real and
# fairly common defect, worse at bigger/shallower-angle pads.
#
# v1 of this fix (since replaced) added a SECOND, ad-hoc pass after the
# normal carve: any terrain vertex within a per-junction "feather ring"
# outside the pad edge got force-blended toward the pad's flat elevation by
# distance alone, regardless of whether the normal carve already had the
# right answer there. That broke exactly the case it needed to leave alone:
# a real station sitting just past a wide/shallow pad's edge is very often
# already correctly served by the ordinary nearest-station carve (it's
# usually its OWN nearest station, or a nearby one on the same, already-
# regraded approach), but the ad-hoc pass's single "worst-direction" feather
# width got applied all the way around the pad, overriding those
# already-correct points with the pad's constant elevation anyway. Caught by
# re-running verify_terrain_fit.py after the fix landed: SHIPPED max
# deviation from a station's own expected elevation got WORSE (3.02m ->
# 6.80m), and every one of the worst offenders sat 0-20m from a pad edge -
# squarely inside that ring, not the pre-existing, unrelated 50m regrade-
# transition effect.
#
# v2 (this version) fixes the ROOT CAUSE instead of papering over its
# symptom: feed the carve extra synthetic "rim stations" - points sampled
# around each pad's own drawn boundary (circle or hull polygon, same shape
# build_pad_chunk/build_pad_chunk_hull actually draws), each carrying the
# pad's own pinned elevation - into the SAME station arrays
# regrade_terrain_at_junctions() already hands to build_tile()/
# carve_grid_m(). No second pass, no separate blend law, no override: the
# existing nearest-station-wins search (see carve_grid_m's own docstring)
# just gets more candidates to choose from. Where a real corridor station is
# genuinely nearest (the already-working case), argmin keeps picking it,
# completely unchanged. Where the wide part of a pad had no nearby correct
# anchor before (the actual bug), a rim point is now the nearest thing
# around, and the carve's own already-tuned inner/outer smoothstep taper
# (TERRAIN_CARVE_INNER_M/OUTER_M) blends toward it exactly the way it does
# for every other station on the map - not a bespoke width/slope heuristic
# that has to be re-derived and can disagree with the rest of the terrain.
PAD_RIM_SPACING_M = 3.0   # arc-length spacing between synthetic rim anchors -
                          # tight enough that carve_grid_m's per-pixel
                          # nearest-station search (which has no notion of
                          # "the pad edge is a continuous curve", just
                          # discrete points) never sees a gap wider than a
                          # single DEM/grid cell along the boundary.


def _pad_rim_points(j, spacing_m=PAD_RIM_SPACING_M):
    """Points (pipeline-frame meters) sampled around junction j's own DRAWN
    pad boundary - the exact circle build_pad_chunk() fans out for a plain
    junction, or the exact fitted-ellipse boundary build_pad_chunk_ellipse()
    fans out for a merged one (via the same _ellipse_polygon() helper),
    resampled to ~spacing_m apart so a long boundary run gets intermediate
    points, not just the tessellation vertices."""
    if j.get("shape") == "ellipse":
        poly = _ellipse_polygon(j["ellipse"], PAD_OVERLAP_MARGIN_M, sides=64)
    else:
        cx, cy = j["xy"]
        r = j["radius_m"] + PAD_OVERLAP_MARGIN_M
        n = max(8, int(math.ceil(2.0 * math.pi * r / spacing_m)))
        return [(cx + r * math.cos(2.0 * math.pi * k / n),
                 cy + r * math.sin(2.0 * math.pi * k / n)) for k in range(n)]

    pts = []
    n = len(poly)
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        seg_len = math.hypot(x1 - x0, y1 - y0)
        steps = max(1, int(math.ceil(seg_len / spacing_m)))
        for s in range(steps):
            t = s / steps
            pts.append((x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
    return pts


def _augment_station_data_with_pad_rims(station_data, junctions):
    """Appends one synthetic, never-excluded "station" per rim point (see
    _pad_rim_points) to the (lat0, lon0, st_x_cm, st_y_cm, st_elev,
    st_exclude) tuple build_tile()/carve_grid_m() consume, each carrying its
    junction's own pinned elevation - see the module comment above for why
    this replaces a separate feather/blend pass instead of adding one."""
    lat0, lon0, st_x_cm, st_y_cm, st_elev, st_exclude = station_data
    if not junctions:
        return station_data
    rim_x, rim_y, rim_elev = [], [], []
    for j in junctions:
        for (px, py) in _pad_rim_points(j):
            rim_x.append(px)
            rim_y.append(py)
            rim_elev.append(j["elev"])
    rim_x_cm = np.asarray(rim_x, dtype=np.float64) * 100.0
    rim_y_cm = -np.asarray(rim_y, dtype=np.float64) * 100.0
    rim_elev = np.asarray(rim_elev, dtype=np.float64)
    rim_exclude = np.zeros(len(rim_x), dtype=bool)
    return (lat0, lon0,
            np.concatenate([st_x_cm, rim_x_cm]),
            np.concatenate([st_y_cm, rim_y_cm]),
            np.concatenate([st_elev, rim_elev]),
            np.concatenate([st_exclude, rim_exclude]))


def regrade_terrain_at_junctions(junctions, lat0, lon0, regraded_east, regraded_north, regraded_elev,
                                  terrain_exclude):
    """Re-carve the (small) set of terrain tiles a snapped junction actually
    touches, from the SAME regraded station arrays the road pads/ribbons
    above were just built from - reuses build_terrain_mesh_tiles.build_tile()
    /write_tile_bin() as plain library calls rather than re-deriving the DEM-
    sample+carve math here, so the terrain-side result matches exactly what a
    full terrain rebake would produce for these coordinates (2026-08-25,
    Taylor: keep the XY snap, fix the terrain instead of the pad). Synthetic
    pad-rim "stations" (_augment_station_data_with_pad_rims, see the module
    comment above it) are folded into that same station set before the carve
    runs, so the ordinary nearest-station blend - unchanged - closes the
    residual cliff a wide/shallow-angle pad's boundary can otherwise leave.

    Needs network access - build_tile() samples fresh DEM tiles per run (see
    build_terrain_mesh_tiles.sample_dem's docstring on why there's no on-disk
    cache to fall back to); only the handful of 2km tiles junctions actually
    touch get rebuilt, not a full-area rebake."""
    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    # A junction's own pad radius plus TERRAIN_CARVE_OUTER_M (how far a
    # station can still influence the carve - see that constant's own
    # comment) is the true footprint that can change. Enumerate every tile
    # the resulting box actually overlaps (not just the 4 corner tiles) -
    # 2026-08-27: the old corner-only version silently skipped a middle
    # tile whenever the box spanned 3+ tiles in one axis; harmless today
    # (TERRAIN_TILE_SIZE_CM=2km dwarfs any single junction's own radius+150m
    # reach) but a large merged/hull pad or a bigger future radius could hit
    # exactly that gap, so this is now correct for any size box instead of
    # relying on it staying small by coincidence.
    for j in junctions:
        cx_cm, cy_cm = j["xy"][0] * 100.0, -j["xy"][1] * 100.0
        r_cm = (j["radius_m"] + btmt.TERRAIN_CARVE_OUTER_M) * 100.0
        tx0, tx1 = int((cx_cm - r_cm) // tile_cm), int((cx_cm + r_cm) // tile_cm)
        ty0, ty1 = int((cy_cm - r_cm) // tile_cm), int((cy_cm + r_cm) // tile_cm)
        j["_terrain_tiles"] = {(tx, ty) for tx in range(tx0, tx1 + 1) for ty in range(ty0, ty1 + 1)}

    tiles_needed = set().union(*(j["_terrain_tiles"] for j in junctions)) if junctions else set()
    if not tiles_needed:
        print("regrade_terrain: no junctions to re-carve terrain for")
        return

    st_x_cm = regraded_east * 100.0
    st_y_cm = -regraded_north * 100.0
    station_data = _augment_station_data_with_pad_rims(
        (lat0, lon0, st_x_cm, st_y_cm, regraded_elev, terrain_exclude), junctions)

    session = requests.Session()
    caches = ({}, {})
    gen_id_bytes = pack_generation_id(compute_generation_id())

    if not TERRAIN_BACKUP_DIR.exists():
        TERRAIN_BACKUP_DIR.mkdir(parents=True)
    backed_up = 0
    paths = {}
    built = {}
    for (tx, ty) in sorted(tiles_needed):
        path = OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        backup_path = TERRAIN_BACKUP_DIR / path.name
        if path.exists() and not backup_path.exists():
            backup_path.write_bytes(path.read_bytes())
            backed_up += 1
        # every junction-adjacent tile is, by construction, within the road
        # corridor - always a NEAR tile (full-res grid + collision), never
        # the coarse background/far LOD.
        built[(tx, ty)] = btmt.build_tile(tx, ty, True, station_data, session, caches)
        paths[(tx, ty)] = path

    for key, tile in built.items():
        btmt.write_tile_bin(paths[key], tile, gen_id_bytes)
    print(f"regrade_terrain: re-carved {len(tiles_needed)} terrain tile(s) "
          f"around {len(junctions)} junction(s) using pad-rim anchors"
          + (f", backed up {backed_up} original(s) -> {TERRAIN_BACKUP_DIR}" if backed_up else ""))


def read_station_sidecar(path):
    """Parses the ODS1 layout write_station_sidecar() (build_road_network.py)
    writes: magic, gen-id string, station count, then N*(x_cm,y_cm,z_cm)
    float32 triples, UE world frame. Returns (gen_id_str, x_cm, y_cm, z_cm)
    as float64 numpy arrays (upcast for the matching/math below - the file
    itself stays float32, matching write_station_sidecar's own dtype)."""
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != b"ODS1":
            raise ValueError(f"{path}: bad magic {magic!r}, expected b'ODS1'")
        (gen_len,) = struct.unpack("<I", f.read(4))
        gen_id = f.read(gen_len).decode("ascii")
        (n,) = struct.unpack("<I", f.read(4))
        flat = np.frombuffer(f.read(n * 3 * 4), dtype="<f4")
    flat = flat.astype(np.float64)
    return gen_id, flat[0::3].copy(), flat[1::3].copy(), flat[2::3].copy()


def patch_station_sidecar(regraded_mask, orig_east, orig_north, new_elev):
    """Mirrors this script's road-tile patching (write_tile() over the
    backed-up originals) for the OTHER on-disk artifact the runtime terrain
    carve reads: roadstations_{AREA_NAME}.bin. Without this, the sidecar
    keeps shipping PRE-pad station elevations forever (it's written once by
    build_road_network.py, long before this script ever runs), so
    AODTerrainManager's async tile carve digs toward the old, un-regraded
    height right under every junction pad - terrain pokes through the pad,
    and a visible step appears where a regraded approach meets un-recarved
    ground (2026-08-26, Taylor: Johnson Road / Andrade Corner). Matches
    sidecar entries to the regraded station set by ORIGINAL x/y (the
    sidecar was built from unregraded road_stations.json, so its x/y is
    exactly the pre-patch east/north this script also started from) and
    overwrites only their z - x/y are left untouched, same as the road-tile
    patch never moves a station whose ribbon isn't rebuilt for it.

    `regraded_mask` selects which of the flat (all-segments-concatenated)
    station arrays actually changed elevation; `orig_east`/`orig_north` are
    that same flat array's UNREGRADED positions (used as the match key) and
    `new_elev` its POST-regrade elevation (DEM-frame meters, same units
    road_ue_z_m expects)."""
    if not SIDECAR_PATH.exists():
        print(f"no station sidecar at {SIDECAR_PATH} - skipping sidecar patch "
              f"(older build_road_network.py run, or not yet generated)")
        return

    if not SIDECAR_BACKUP_DIR.exists():
        SIDECAR_BACKUP_DIR.mkdir(parents=True)
    backup_path = SIDECAR_BACKUP_DIR / SIDECAR_PATH.name
    if not backup_path.exists():
        backup_path.write_bytes(SIDECAR_PATH.read_bytes())
        print(f"backed up original (unpatched) station sidecar -> {backup_path}")
    else:
        print(f"reusing existing sidecar backup baseline at {backup_path}")

    gen_id, sc_x, sc_y, sc_z = read_station_sidecar(backup_path)

    idxs = np.nonzero(regraded_mask)[0]
    px_cm = orig_east[idxs] * 100.0
    py_cm = -orig_north[idxs] * 100.0
    pz_cm = road_ue_z_m(new_elev[idxs], ROAD_LIFT, ORIGIN_HEIGHT_M) * 100.0

    # Same bucket-by-cell pattern used everywhere else in this file (e.g.
    # find_crossings/find_dangling_endpoints) - N=~29k sidecar stations is
    # cheap to bucket once, then each of the (typically few hundred)
    # regraded stations only checks its own cell's handful of candidates
    # instead of an O(N*M) brute-force scan.
    cell = max(SIDECAR_MATCH_EPS_CM * 4.0, STATION_SPACING * 100.0)
    buckets = defaultdict(list)
    for si in range(len(sc_x)):
        buckets[(int(sc_x[si] // cell), int(sc_y[si] // cell))].append(si)

    eps2 = SIDECAR_MATCH_EPS_CM ** 2
    matched = 0
    ambiguous = 0
    for k in range(len(idxs)):
        bx, by = int(px_cm[k] // cell), int(py_cm[k] // cell)
        best_si, best_d2 = -1, eps2
        hits = 0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for si in buckets.get((bx + dx, by + dy), ()):
                    d2 = (sc_x[si] - px_cm[k]) ** 2 + (sc_y[si] - py_cm[k]) ** 2
                    if d2 <= eps2:
                        hits += 1
                        if d2 < best_d2:
                            best_d2, best_si = d2, si
        if best_si < 0:
            continue   # this regraded station has no sidecar entry (e.g. it
                       # fell inside a tunnel run, which write_station_sidecar
                       # excludes but this script's flat station arrays don't)
        if hits > 1:
            ambiguous += 1   # still patches the single closest match below
        sc_z[best_si] = pz_cm[k]
        matched += 1

    unmatched = len(idxs) - matched
    print(f"sidecar patch: {matched}/{len(idxs)} regraded station(s) matched "
          f"to a sidecar entry within {SIDECAR_MATCH_EPS_CM:.0f}cm"
          + (f", {unmatched} unmatched (likely tunnel-excluded)" if unmatched else "")
          + (f", {ambiguous} had >1 candidate within epsilon (closest used)" if ambiguous else ""))

    write_station_sidecar(SIDECAR_PATH, [(sc_x, sc_y, sc_z)], pack_generation_id(gen_id))
    print(f"rewrote station sidecar ({len(sc_x)} station(s), unchanged count) -> {SIDECAR_PATH}")


def _blend_approaches_to_junction(reps, flat_seg, junction_elev, snap_xy, bounds, structural,
                                   s_m, elev, east, north, regraded, regraded_east, regraded_north):
    """Smoothstep-blend every road in `reps` OTHER than `flat_seg` from its
    ORIGINAL profile (elev/east/north - never a running `regraded*`, so
    repeat calls for overlapping junctions don't compound) down/up to
    (junction_elev, snap_xy) over TRANSITION_LEN_M, writing the result into
    the regraded/regraded_east/regraded_north arrays in place. Pulled out of
    the per-cluster loop in main() (v3/v4's original single-junction blend)
    so the exact same blend can also be re-run, post-merge, for a merged
    hull junction's whole combined member set - see main()'s own comment on
    why a merged junction needs this run a second time instead of trusting
    each member's original (pre-merge, different-elevation) blend."""
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
            regraded[k] = elev[k] * (1.0 - weight) + junction_elev * weight
            regraded_east[k] = east[k] * (1.0 - weight) + snap_xy[0] * weight
            regraded_north[k] = north[k] * (1.0 - weight) + snap_xy[1] * weight


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

        # Footprint-derived ellipse from the very start (see the redesign
        # note above merge_same_intersection): the pad's shape comes from
        # where member roads' pavement actually coexists, not from the
        # crossing-angle circle formula - radius_m (angle formula) survives
        # only as the degenerate-cloud fallback radius and the generic
        # scalar "reach" floor for circle-only callers.
        ell = footprint_ellipse(set(reps.keys()), snap_xy, radius_m,
                                 east, north, elev, bounds, structural)
        junctions.append({"xy": (ell[0], ell[1]), "elev": junction_elev,
                           "radius_m": max(ell[2], ell[3]), "shape": "ellipse",
                           "ellipse": ell,
                           "flat_seg": flat_seg, "flat_grade": grades[flat_seg],
                           "snap_xy": snap_xy, "reps": reps,
                           "n_roads": len(reps), "segs": set(reps.keys())})

        _blend_approaches_to_junction(reps, flat_seg, junction_elev, snap_xy,
                                       bounds, structural, s_m, elev, east, north,
                                       regraded, regraded_east, regraded_north)

    n_regraded = int(np.sum(~np.isclose(regraded, elev)))
    print(f"regraded {n_regraded} station(s) across {len(junctions)} junction(s) (pre-merge)")

    n_junctions_before_merge = len(junctions)
    # Two crossing CLUSTERS too far apart for cluster_crossings' own
    # CLUSTER_MERGE_M to fold into one junction can still have OVERLAPPING
    # (or, per PAD_MERGE_NEAR_MARGIN_M, nearly-overlapping) drawn pad discs -
    # fold those into single fitted-ellipse pads now, before
    # find_dangling_endpoints() runs, so its covered_by_real_junction() guard
    # sees the merged footprint (see merge_overlapping_pads' docstring).
    junctions = merge_same_intersection(junctions, east, north, elev, bounds, structural)
    print(f"junction pad count: {n_junctions_before_merge} before merge -> {len(junctions)} after merge "
          f"(crossing-derived pads only, before dangling-endpoint connector spurs are added below)")

    # A merged (shape="ellipse") junction picked ONE elevation (its flattest
    # member's) but every member OTHER than that flattest one was already
    # regraded, above, toward ITS OWN pre-merge elevation/anchor point - left
    # alone, that's exactly the "two pads at different elevations" bug
    # relocated one step downstream: one pad now, but a step where a
    # not-actually-regraded-to-this-elevation approach meets it. Re-run the
    # identical transition blend for every member's roads (all of
    # combined_reps except the group's own flat_seg) toward the merged
    # elev/snap_xy so the whole merged group shares one consistent graded
    # surface, same as a single cluster always has.
    n_reblended_junctions = 0
    for j in junctions:
        if not j.get("merged"):
            continue
        n_reblended_junctions += 1
        _blend_approaches_to_junction(j["reps"], j["flat_seg"], j["elev"], j["snap_xy"],
                                       bounds, structural, s_m, elev, east, north,
                                       regraded, regraded_east, regraded_north)
    if n_reblended_junctions:
        n_regraded_after = int(np.sum(~np.isclose(regraded, elev)))
        print(f"re-blended approaches for {n_reblended_junctions} merged junction(s) "
              f"toward their single merged elevation ({n_regraded_after} station(s) "
              f"regraded total, was {n_regraded} before merge)")

    # Preview hook for the footprint-ellipse redesign: dump every proposed
    # pad footprint (post-merge, pre-bake) as JSON and stop - nothing on
    # disk is touched, so diagnose_junctions.py can draw the proposed
    # ellipses over the map for eyeballing BEFORE committing to a bake.
    for arg in sys.argv:
        if arg.startswith("--dump-pads="):
            out = Path(arg[len("--dump-pads="):])
            out.write_text(json.dumps([{
                "xy": [float(j["xy"][0]), float(j["xy"][1])],
                "ellipse": [float(v) for v in j["ellipse"]] if "ellipse" in j else None,
                "radius_m": float(j["radius_m"]), "elev": float(j["elev"]),
                "shape": j.get("shape", "circle"), "merged": bool(j.get("merged")),
                "n_roads": int(j["n_roads"]), "segs": sorted(int(s) for s in j["segs"]),
            } for j in junctions], indent=1))
            print(f"--dump-pads: wrote {len(junctions)} proposed pad footprint(s) -> {out} "
                  f"(no tiles/sidecar touched, exiting before bake)")
            return

    # Separate problem from crossing detection: roads whose endpoint was
    # simply never topologically connected to anything (see
    # find_dangling_endpoints' docstring). Weld each one shut with a
    # straight connector spur from the dangling endpoint to its nearest
    # neighbor, regrading the approach the same way a real junction does,
    # plus a small pad to paper over the T-seam where it meets the target
    # road (2026-08-25, Taylor: "I was on a road and it just ended...
    # supposed to be an intersection there").
    snaps = find_dangling_endpoints(east, north, elev, seg_ids, structural, bounds, junctions)
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
                           "flat_seg": tseg, "flat_grade": min(grade_a, grade_b),
                           "n_roads": 2, "segs": {seg, tseg}, "shape": "circle"})

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

    # The runtime terrain carve (AODTerrainManager) doesn't only read the
    # road-mesh tiles patched above - it reads roadstations_{AREA_NAME}.bin,
    # a separate sidecar build_road_network.py writes straight from
    # UNREGRADED road_stations.json, long before this script's pad/regrade
    # pass ever runs. Left unpatched, that sidecar keeps shipping pre-pad
    # elevations forever, so the carve digs to the wrong height near every
    # junction. `regraded`/`elev` are still the SAME flat, all-segments
    # arrays used throughout this function (both cluster regrades and the
    # dangling-endpoint connector welds write into `regraded`), so the mask
    # below covers both.
    patch_station_sidecar(regraded != elev, east, north, regraded)

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
                any(point_in_pad(e[i], nn[i], j, margin=0.0) for j in junctions)
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
        if j.get("shape") == "ellipse":
            pad = build_pad_chunk_ellipse(f"junctionpad_{k:04d}", j["ellipse"],
                                           j["elev"], PAD_OVERLAP_MARGIN_M)
        else:
            pad = build_pad_chunk(f"junctionpad_{k:04d}", j["xy"],
                                   j["elev"], j["radius_m"] + PAD_OVERLAP_MARGIN_M)
        host = nearest_chunk(all_chunks, j["xy"])
        if host is not None:
            merge_chunk_into(host, pad)
            n_merged += 1
        else:
            all_chunks.append(pad)
            n_standalone += 1

    n_gate_fills = add_gate_coverage_fills(all_chunks, junctions, east, north,
                                            regraded, terrain_exclude)

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
          f"({len(junctions)} junction pad(s) + {len(fill_pads)} fill pad(s) "
          f"+ {n_gate_fills} gate-coverage fill(s), "
          f"{n_merged} fused into a host ribbon chunk, {n_standalone} standalone) -> "
          f"{len(written)} tile(s), {n_stale} stale tile(s) removed")


if __name__ == "__main__":
    main()
