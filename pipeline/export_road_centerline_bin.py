"""Exports road_stations.json's precise centerline positions as a runtime
sidecar, so AODForestManager can reject tree candidates by geometric
distance-to-centerline instead of a physics raycast against the road
mesh's PM_Asphalt tag (2026-07-20, alongside build_forest_elevation.py -
same motivation: make tree placement fully data-driven, not dependent on
whatever's currently loaded/resident at runtime). Full 5 m station spacing,
not downsampled - the exclusion zone this feeds (road half-width + skirt +
clearance, ~11.5 m) is narrow enough that coarser spacing would introduce
real slop, and the file size is trivial either way (a few hundred KB).

Usage:  python export_road_centerline_bin.py    (needs road_stations.json)

Writes  OpenDriveUE/Content/OpenDrive/Masks/centerline_<AREA_NAME>_test.bin
        'ODC1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm)

ALSO writes a second, map-drawing-only sidecar (2026-07-28, Oahu map lag):
    OpenDriveUE/Content/OpenDrive/Masks/centerline_map_<AREA_NAME>_test.bin
    'ODM1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm)
The ODC1 file above is a single flat concatenation of every segment with NO
break markers - fine for AODForestManager/AODRaceManager's nearest-point
distance queries (adjacency across a segment boundary doesn't matter for a
"how close is the nearest point" check), but wrong for AODRaceHUD::
DrawStageMap, which used to walk it as one continuous polyline and draw a
straight line between the LAST point of one segment and the FIRST point of
the next - two real, unrelated streets, possibly clear across the map,
connected as if they were the same road. On Oahu's 8,848-segment network
this produced thousands of bogus "teleport" lines AND, since it's the full
5 m-station density with no simplification, roughly 780,000 line draws
every frame the map was visible - the actual cause of the reported lag.
ODM1 fixes both by (a) simplifying each segment independently (Douglas-
Peucker, see simplify_rdp()) so nearly-straight runs collapse to a couple of
points instead of one every 5 m, and (b) inserting a NaN sentinel point
between segments that AODRoadManager/DrawStageMap must skip past rather
than draw through - never generated from a flattened array, always from
each segment's own point list, so it can't reintroduce the bug.

ALSO writes a third, navigation-only sidecar (2026-09-25, Arc 4.2 v3 world
map + navigation, next steps.md SS7.37):
    OpenDriveUE/Content/OpenDrive/Masks/routegraph_<AREA_NAME>_test.bin
    'ODG1' + uint32 N + N * (float32 X_cm, float32 Y_cm)
           + uint32 E + E * (uint32 a, uint32 b, float32 len_cm)   (undirected)
road_stations.json has no junction topology: segments only break at OSM
way-END nodes of degree>=3 (merge_way_chains), so a side street whose end
lands on the INTERIOR of a through-road, or two through-ways crossing at a
shared interior node, share no explicit node here. build_route_graph()
recovers that by proximity: nodes are every ROUTE_STRIDE-th station (plus
segment ends and any station a link lands on); along-segment edges carry
the real arc length; link edges join (a) each segment end to the nearest
station of every other segment within ROUTE_ENDPOINT_JOIN_M, and (b) any two
interior stations of different segments within ROUTE_CROSS_JOIN_M, (c) two
segment ends within ROUTE_END_END_JOIN_M (junction pullback), (d) the
shortest end-end pair within ROUTE_BRIDGE_JOIN_M that joins two still-separate
pieces (Kruskal-style) - all
gated on elevation (and, for (b)-(d), neither being a bridge/tunnel/structure)
so a grade-separated overpass never becomes a fake intersection.
Oneway (2026-10-06): an optional trailing block E * int8 follows the edges,
+1 = a->b only, -1 = b->a only, 0 = both (old files without it = all two-way).
road_stations.json's "oneway" bool has no direction (chains reverse ways), so
station_oneway_dirs() re-reads the raw osm.json and matches each station to
the nearest directed OSM way. AODRoadManager loads this at
BeginPlay (path derived from CenterlineFilePrefix, centerline_ -> routegraph_)
and runs A* on it; pipeline/checks/verify_route_graph.py is the Python mirror.

Directed lane graph (2026-10-06, PLAN-REALISM 3a - nav and traffic read this
one file, so they can't drift). An optional lane block follows the oneway
block:
    N * float32 z_cm     shipped road surface Z (junction-pad patched, via
                         verify_road_fit._shipped_stations); node XY is the
                         shipped (pad-snapped) position too
    N * uint8 control    0 = not a junction, 1 signal, 2 stop, 3 give way
                         (road_furniture.json "controls"), 4 = uncontrolled,
                         priority by class - per junction cluster
                         (JUNCTION_CLUSTER_M)
    E * uint8 class      bits 0-2 road_mesh_common.ROAD_CLASS_ID, bit 3 ramp
                         (*_link), bit 4 no pavement (dropped connector, below)
    E * uint8 lanes      per travel direction
    E * uint8 kmh        maxspeed, else build_road_furniture.DEFAULT_MPH
    E * uint8 yield      bit 0: a->b traffic stops / yields at a (the stop
                         line is the arc's tail), bit 1: b->a traffic at b
                         (OSM stop/give-way approaches; class priority at
                         uncontrolled junctions: the arc entering it)
len_cm is now the 3D chord between the shipped nodes (was the stations'
arc length, which pad snapping made shorter than the A* straight line).
Each edge is a station-to-station piece (5 m), so a lane polyline is the run
of degree-2 nodes between junctions. Link edges now carry direction too: a
link off a one-way carriageway end flows out of a sink end / into a source
end. Dropped connectors: build_road_stations drops OSM chains under
MIN_SEGMENT_STATIONS (100 m), which left islands (Blue River) and unlinked
carriageway ends; osm_topology() adds them back here by OSM node id, and links
every OSM node two kept roads share.
"""

import json
import struct
from pathlib import Path

import numpy as np

from road_mesh_common import (DEFAULT_ROAD_CLASS_ID, DEFAULT_ROAD_CLASS_LANES_TWOWAY, ROAD_CLASS_ID,
                              ROAD_CLASS_LANES_TWOWAY, ROAD_LIFT, STATION_SPACING)
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, to_local

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
FURNITURE_PATH = STATIONS_PATH.with_name("road_furniture.json")
OSM_PATH = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

# Map-only simplification tolerance (cm). Was 30 m (fine for the whole-area
# overview), but the mini-map shows ~90 m radius, where 30 m cut corners and
# the road no longer lined up with the route line (2026-10-06). 1.5 m is
# under a pixel at mini-map zoom; straights still collapse to their ends.
MAP_SIMPLIFY_TOLERANCE_CM = 150.0

# Route graph (see module docstring). Stride 1 = a node per 5 m station, so
# the route line drawn from the nodes follows the road through hairpins
# (stride 4's 20 m chords cut them; 2026-10-06). ~4x the A* nodes.
ROUTE_STRIDE = 1
ROUTE_ENDPOINT_JOIN_M = 12.0   # segment ends sit 3-10 m off the road they join
ROUTE_ENDPOINT_DZ_M = 12.0     # motorway_link ends disagree with the motorway by 5-10 m
ROUTE_CROSS_JOIN_M = 4.0       # stations are <=5 m apart, so a real shared node is <~3.5 m
ROUTE_CROSS_DZ_M = 2.5
# segment ends pulled back from a junction (e.g. Silverthorne exit 205:
# Blue River Pkwy / US 6 ends sit 14-30 m apart) - end-to-end only, so a
# 30 m radius can't snag a random interior station of a passing road
ROUTE_END_END_JOIN_M = 30.0
ROUTE_END_END_DZ_M = 3.0
# last resort, Kruskal-style: an end-end pair up to this far apart is added
# ONLY when it joins two still-disconnected pieces (road filtering drops some
# short interchange connectors, leaving e.g. Silverthorne an island)
ROUTE_BRIDGE_JOIN_M = 60.0
ROUTE_ONEWAY_END_JOIN_M = 100.0   # rule (e): unlinked one-way dead ends only
CLS_RAMP, CLS_NO_PAVEMENT = 8, 16  # class byte flags (module docstring)
CONTROL_KIND = {"traffic_signals": 1, "stop": 2, "give_way": 3}
CONTROL_PRIORITY = 4
JUNCTION_CLUSTER_M = 20.0     # junction nodes this close (and level) = one junction
APPROACH_WALK = 12            # stations (60 m) from a sign to the junction it guards


def simplify_rdp(points, epsilon):
    """Douglas-Peucker simplification of one Nx2 polyline (iterative, not
    recursive - some real segments run thousands of stations long and a
    recursive version can blow Python's call-stack limit on a pathologically
    curvy one). Returns a subset of the input rows, always keeping the first
    and last point."""
    n = len(points)
    if n < 3:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        p0, p1 = points[i0], points[i1]
        seg = p1 - p0
        seg_len = np.hypot(seg[0], seg[1])
        chunk = points[i0 + 1:i1]
        if seg_len < 1e-6:
            d = np.hypot(chunk[:, 0] - p0[0], chunk[:, 1] - p0[1])
        else:
            # perpendicular distance from each point to the line p0->p1
            cross = seg[0] * (chunk[:, 1] - p0[1]) - seg[1] * (chunk[:, 0] - p0[0])
            d = np.abs(cross) / seg_len
        rel_idx = int(np.argmax(d))
        if d[rel_idx] > epsilon:
            mid = i0 + 1 + rel_idx
            keep[mid] = True
            stack.append((i0, mid))
            stack.append((mid, i1))
    return points[keep]


ONEWAY_MATCH_M = 3.0   # station -> own OSM way (densified at <=5 m) is <=2.5 m


def oneway_travel_dir(tags):
    """+1 = travel along the way's node order, -1 = against, 0 = two-way.
    OSM: yes/1/true, -1/reverse; implied for motorway, motorway_link and
    roundabouts unless explicitly tagged; reversible/alternating = 0."""
    v = tags.get("oneway")
    if v is None:
        implied = (tags.get("highway") in ("motorway", "motorway_link")
                   or tags.get("junction") in ("roundabout", "circular"))
        return 1 if implied else 0
    return {"yes": 1, "1": 1, "true": 1, "-1": -1, "reverse": -1}.get(v, 0)


def station_oneway_dirs(segments, osm):
    """Per segment, an int8 array: +1 = travel in increasing station order,
    -1 = decreasing, 0 = two-way. Each station takes the nearest point of a
    densified directed OSM way within ONEWAY_MATCH_M whose heading is near-
    parallel to the station's; its sign is the travel direction."""
    from scipy.spatial import cKDTree

    nodes = {e["id"]: (e["lon"], e["lat"]) for e in osm["elements"] if e["type"] == "node"}
    pts, vec = [], []
    for w in osm["elements"]:
        d = oneway_travel_dir(w.get("tags", {})) if w["type"] == "way" else 0
        ll = np.array([nodes[n] for n in w.get("nodes", []) if n in nodes]) if d else []
        if len(ll) < 2:
            continue
        p = np.stack(to_local(ll[:, 0], ll[:, 1]), axis=1)
        for a, b in zip(p[:-1], p[1:]):
            k = max(int(np.ceil(np.hypot(*(b - a)) / 5.0)), 1)
            t = np.arange(k + 1)[:, None] / k
            pts.append(a + (b - a) * t)
            vec.append(np.repeat([(b - a) * d], k + 1, axis=0))
    out = []
    if not pts:
        return [np.zeros(len(seg["east_m"]), dtype=np.int8) for seg in segments]
    pts, vec = np.concatenate(pts), np.concatenate(vec)
    vec /= np.maximum(np.hypot(vec[:, 0], vec[:, 1]), 1e-9)[:, None]
    tree = cKDTree(pts)
    for seg in segments:
        xy = np.stack([seg["east_m"], seg["north_m"]], axis=1)
        dirs = np.zeros(len(xy), dtype=np.int8)
        if len(xy) >= 2:
            tan = np.gradient(xy, axis=0)
            tan /= np.maximum(np.hypot(tan[:, 0], tan[:, 1]), 1e-9)[:, None]
            dist, j = tree.query(xy, distance_upper_bound=ONEWAY_MATCH_M)
            hit = np.isfinite(dist)
            dot = np.zeros(len(xy))
            dot[hit] = (tan[hit] * vec[j[hit]]).sum(axis=1)
            dirs[dot > 0.7] = 1
            dirs[dot < -0.7] = -1
        out.append(dirs)
    return out


def osm_topology(segments, osm):
    """Junction topology from shared OSM node ids (not proximity) ->
    (connectors, node_links). node_links [(segment, station, segment,
    station)]: every OSM node two kept chains share (a crossing or T at a
    way's interior, which proximity rule (b) misses when smoothing pulls the
    stations >4 m apart; a chain closing on itself). connectors: pseudo
    segments for the OSM chains build_road_stations dropped as too short
    (< MIN_SEGMENT_STATIONS) whose both ends reach a kept road or another
    such connector - same keys build_route_graph() reads, plus "pseudo" and
    "attach" [(own station, segment index, station)] (segment index into
    segments + connectors).
    ponytail: no pavement (graph only) and Z interpolated between the roads it
    joins; the root fix is MIN_SEGMENT_STATIONS in build_road_stations (a full
    rebuild)."""
    from collections import defaultdict
    from scipy.spatial import cKDTree
    from build_road_stations import MIN_SEGMENT_STATIONS, merge_way_chains

    lonlat = {e["id"]: (e["lon"], e["lat"]) for e in osm["elements"] if e["type"] == "node"}
    chains, _ = merge_way_chains([e for e in osm["elements"] if e["type"] == "way"])
    first = cKDTree(np.array([[seg["east_m"][0], seg["north_m"][0]] for seg in segments]))
    at = defaultdict(list)           # OSM node id -> [(kept segment, station)]
    short = []
    for chain, struct, tunnel, names, oneway, highway, meta in chains:
        if any(n not in lonlat for n in chain):
            continue
        ll = np.array([lonlat[n] for n in chain])
        e, n = to_local(ll[:, 0], ll[:, 1])
        d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(e), np.diff(n)))])
        if d[-1] < MIN_SEGMENT_STATIONS * STATION_SPACING:
            if chain[0] != chain[-1]:
                short.append((chain, e, n, d, struct, tunnel, names, highway, meta))
            continue
        # kept chain -> its segment: first station on the chain start, last
        # station near the chain end (several chains can start at one node)
        best = None
        for k in first.query_ball_point((e[0], n[0]), 4.0):   # ends smooth ~2.2 m inward
            seg = segments[k]
            dl = np.hypot(seg["east_m"][-1] - e[-1], seg["north_m"][-1] - n[-1])
            if dl < 8.0 and (best is None or dl < best[0]):
                best = (dl, k)
        if best:
            m = len(segments[best[1]]["east_m"])
            for nid, dk in zip(chain, d):
                at[nid].append((best[1], min(int(round(dk / STATION_SPACING)), m - 1)))
    alive = [True] * len(short)
    while True:            # both ends on a kept road or a surviving connector
        uses = defaultdict(int)
        for c, ok in zip(short, alive):
            for nid in set(c[0]) if ok else ():
                uses[nid] += 1
        drop = [k for k, c in enumerate(short) if alive[k]
                and any(nid not in at and uses[nid] < 2 for nid in (c[0][0], c[0][-1]))]
        if not drop:
            break
        for k in drop:
            alive[k] = False
    short = [c for c, ok in zip(short, alive) if ok]
    near = lambda s, j, pe, pn: (np.hypot(segments[s]["east_m"][j] - pe, segments[s]["north_m"][j] - pn)
                                 < ROUTE_ENDPOINT_JOIN_M)   # a mispaired chain can't link far away
    node_links = [(s, j, s2, j2) for lst in at.values() for (s, j), (s2, j2) in zip(lst[:-1], lst[1:])
                  if near(s2, j2, segments[s]["east_m"][j], segments[s]["north_m"][j])
                  and abs(segments[s]["elev_m"][j] - segments[s2]["elev_m"][j2]) <= ROUTE_ENDPOINT_DZ_M]

    out, pat = [], defaultdict(list)     # pat: OSM node id -> [(pseudo k, station)]
    for k, (chain, e, n, d, struct, tunnel, names, highway, meta) in enumerate(short):
        cnt = max(int(np.ceil(d[-1] / STATION_SPACING)) + 1, 2)
        s = np.linspace(0.0, d[-1], cnt)
        src = np.clip(np.searchsorted(d, s, side="right"), 1, len(d) - 1)  # node carrying the way
        st = [int(round(dk / (s[1] - s[0]))) if s[1] > 0 else 0 for dk in d]
        for nid, j in zip(chain, st):
            pat[nid].append((k, j))
        out.append({"east_m": np.interp(s, d, e).tolist(), "north_m": np.interp(s, d, n).tolist(),
                    "s_m": s.tolist(), "elev_m": [np.nan] * cnt,
                    "is_struct": [bool(struct[i]) for i in src], "is_tunnel": [bool(tunnel[i]) for i in src],
                    "road_name": [names[i] for i in src], "highway_class": [highway[i] for i in src],
                    "lanes": [int(meta[i][0]) for i in src], "maxspeed": [int(meta[i][3]) for i in src],
                    "pseudo": True,
                    "attach": [(j, s2, j2) for nid, j, pe, pn in zip(chain, st, e, n)
                               for s2, j2 in at.get(nid, ()) if near(s2, j2, pe, pn)]})
    for nid, lst in pat.items():         # connector <-> connector (both sides list it)
        for (k, j), (k2, j2) in zip(lst[:-1], lst[1:]):
            if k != k2:
                out[k]["attach"].append((j, len(segments) + k2, j2))
                out[k2]["attach"].append((j2, len(segments) + k, j))
    # Z: interpolate between the attached roads, connectors anchored on
    # connectors resolve over a few passes, the rest take the nearest station
    elev = [np.array(seg["elev_m"]) for seg in segments]
    for _ in range(4):
        for ps in out:
            if np.isfinite(ps["elev_m"][0]):
                continue
            anch = {}
            for j, s2, j2 in ps["attach"]:
                z = elev[s2][j2] if s2 < len(segments) else out[s2 - len(segments)]["elev_m"][j2]
                if np.isfinite(z):
                    anch[j] = z
            if anch:
                js = sorted(anch)
                ps["elev_m"] = np.interp(np.arange(len(ps["s_m"])), js, [anch[j] for j in js]).tolist()
    allxy = np.concatenate([np.c_[seg["east_m"], seg["north_m"]] for seg in segments])
    allz = np.concatenate(elev)
    tree = cKDTree(allxy)
    for ps in out:
        if not np.isfinite(ps["elev_m"][0]):
            ps["elev_m"] = allz[tree.query(np.c_[ps["east_m"], ps["north_m"]])[1]].tolist()
    return out, node_links


def build_route_graph(segments, station_dirs=None, ship=None, controls=(), node_links=()):
    """-> (nodes_cm Nx3 float64 [x, y, z], edges list of (a, b, len_cm, dir,
    class, lanes, kmh, yield), control per node uint8). dir is +1 a->b only,
    -1 b->a only, 0 both; station_dirs (station_oneway_dirs()) None = all
    two-way. ship = (east, north, z_abs) per station as shipped (None = the
    stations' own, elev + ROAD_LIFT); controls = road_furniture.json
    "controls"; node_links and pseudo segments (last, linked only through
    their "attach" list) from osm_topology(). See the module docstring for the rules."""
    from scipy.spatial import cKDTree

    xy, z, seg_id, s_m, structural, ends = [], [], [], [], [], []
    sdir = (np.concatenate(station_dirs).astype(np.int8) if station_dirs is not None
            else np.zeros(sum(len(seg["east_m"]) for seg in segments), dtype=np.int8))
    off, offs, n_real = 0, [], 0
    for k, seg in enumerate(segments):
        n = len(seg["east_m"])
        offs.append(off)
        xy.append(np.stack([seg["east_m"], seg["north_m"]], axis=1))
        z.extend(seg["elev_m"])
        s_m.extend(seg["s_m"])
        seg_id.extend([k] * n)
        # is_struct (real OSM bridge/tunnel ways), NOT is_bridge - that one
        # flags ~49% of summit_county stations (see build_road_stations.py)
        structural.extend(bool(a or b) for a, b in zip(seg["is_struct"], seg["is_tunnel"]))
        if not seg.get("pseudo"):
            assert n_real == off, "pseudo segments go last"
            n_real = off + n
            if n >= 2:
                ends.extend([off, off + n - 1])
        off += n
    xy = np.concatenate(xy)
    z, s_m, seg_id = np.array(z), np.array(s_m), np.array(seg_id)
    structural = np.array(structural)
    starts = np.concatenate([[0], np.flatnonzero(seg_id[1:] != seg_id[:-1]) + 1])
    local = np.arange(len(xy)) - np.repeat(starts, np.diff(np.append(starts, len(xy))))
    is_last = np.append(seg_id[1:] != seg_id[:-1], True)
    is_first = np.append(True, seg_id[1:] != seg_id[:-1])
    keep = (local % ROUTE_STRIDE == 0) | is_last
    # the OSM match is unreliable at a segment's last 2 stations (they sit off
    # the way's line, and other ways cross there): they take the direction of
    # the 3rd station in - unmatched, a one-way's end was a two-way 5 m stub
    # (a legal U-turn spot); matched to a crossing ramp, a two-way road's end
    # turned one-way. Holes in a one-way run (a tight curve fails the heading
    # test) take the run's direction.
    # A one-way run of <=3 stations on a way not tagged oneway is a channelized
    # turn lane / slip ramp beside it (Montezuma Rd, US 6 at Avon), not the road.
    own = np.array([bool(ow) or hc in ("motorway", "motorway_link") or bool(seg.get("pseudo"))
                    for seg in segments for ow, hc in zip(seg.get("oneway", seg["highway_class"]),
                                                          seg["highway_class"])])
    chg = np.append(True, (sdir[1:] != sdir[:-1]) | (seg_id[1:] != seg_id[:-1]))
    run = np.cumsum(chg) - 1
    sdir = np.where((sdir != 0) & (np.bincount(run)[run] <= 3) & ~own, 0, sdir).astype(np.int8)
    n_seg = np.bincount(seg_id)[seg_id]
    i = np.arange(len(sdir))
    inner = np.where(local < 2, i - local + 2, np.where(local > n_seg - 3, i - local + n_seg - 3, i))
    sdir = sdir[np.where(n_seg >= 5, inner, i)]
    for _ in range(2):
        p = np.where(is_first, 0, np.roll(sdir, 1))
        q = np.where(is_last, 0, np.roll(sdir, -1))
        fill = (sdir == 0) & (p * q > 0)
        sdir = np.where(fill, np.sign(p + q), sdir).astype(np.int8)

    tree = cKDTree(xy[:n_real])      # proximity rules: real stations only
    links = set()
    for e in ends:
        best = {}   # other segment -> (dist, station)
        for j in tree.query_ball_point(xy[e], ROUTE_ENDPOINT_JOIN_M):
            # own segment only where it loops back (a way chained through a
            # closed one-way loop ends on its own interior)
            if ((seg_id[j] == seg_id[e] and abs(s_m[j] - s_m[e]) < 3 * ROUTE_ENDPOINT_JOIN_M)
                    or abs(z[j] - z[e]) > ROUTE_ENDPOINT_DZ_M):
                continue
            d = float(np.hypot(*(xy[j] - xy[e])))
            if seg_id[j] not in best or d < best[seg_id[j]][0]:
                best[seg_id[j]] = (d, j)
        for _, j in best.values():
            links.add((min(e, j), max(e, j)))
    ends_arr = np.array(ends)
    etree = cKDTree(xy[ends_arr])
    for a, b in etree.query_pairs(ROUTE_END_END_JOIN_M, output_type="ndarray"):
        a, b = ends_arr[a], ends_arr[b]
        if (seg_id[a] != seg_id[b] and not structural[a] and not structural[b]
                and abs(z[a] - z[b]) <= ROUTE_END_END_DZ_M):
            links.add((min(a, b), max(a, b)))
    for a, b in tree.query_pairs(ROUTE_CROSS_JOIN_M, output_type="ndarray"):
        if (seg_id[a] != seg_id[b] and not structural[a] and not structural[b]
                and abs(z[a] - z[b]) <= ROUTE_CROSS_DZ_M):
            links.add((min(a, b), max(a, b)))
    node_links = list(node_links) + [(k, j, s2, j2) for k, seg in enumerate(segments)
                                     for j, s2, j2 in seg.get("attach", ())]
    for s1, j, s2, j2 in node_links:       # OSM node topology (osm_topology())
        a, b = offs[s1] + j, offs[s2] + j2
        if a != b:
            links.add((min(a, b), max(a, b)))
    # (d) bridge still-separate pieces: union-find over segments (each segment
    # is internally connected), shortest candidate first
    parent = list(range(len(segments)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in links:
        parent[find(seg_id[a])] = find(seg_id[b])
    cands = []
    for a, b in etree.query_pairs(ROUTE_BRIDGE_JOIN_M, output_type="ndarray"):
        a, b = ends_arr[a], ends_arr[b]
        if (not structural[a] and not structural[b]
                and abs(z[a] - z[b]) <= ROUTE_END_END_DZ_M):
            cands.append((float(np.hypot(*(xy[a] - xy[b]))), a, b))
    for _, a, b in sorted(cands):
        ra, rb = find(seg_id[a]), find(seg_id[b])
        if ra != rb:
            parent[ra] = rb
            links.add((min(a, b), max(a, b)))
    # (e) one-way dead ends (2026-10-06): a still-unlinked carriageway end that
    # travel runs into (sink) or out of (source) joins the nearest other end
    # within ROUTE_ONEWAY_END_JOIN_M that isn't the same kind. Dropped short
    # connectors (e.g. CO 9 / Swan Mtn Rd, 72 m) otherwise trap one-way
    # routing; two-way routing just reversed down the other carriageway.
    sink =(is_last & (sdir == 1)) | (is_first & (sdir == -1))
    source = (is_first & (sdir == 1)) | (is_last & (sdir == -1))
    linked = {i for ab in links for i in ab}
    for e in ends_arr[(sink | source)[ends_arr]]:
        if e in linked or structural[e]:
            continue
        best = None
        for jj in etree.query_ball_point(xy[e], ROUTE_ONEWAY_END_JOIN_M):
            j = ends_arr[jj]
            if (seg_id[j] == seg_id[e] or structural[j] or abs(z[j] - z[e]) > ROUTE_END_END_DZ_M
                    or (sink[e] and sink[j]) or (source[e] and source[j])):
                continue
            d = float(np.hypot(*(xy[j] - xy[e])))
            if best is None or d < best[0]:
                best = (d, j)
        if best:
            links.add((min(e, best[1]), max(e, best[1])))
    for a, b in links:
        keep[a] = keep[b] = True

    kept = np.flatnonzero(keep)
    node_of = np.full(len(xy), -1, dtype=np.int64)
    node_of[kept] = np.arange(len(kept))

    # per-station lane attributes (module docstring)
    from build_road_furniture import DEFAULT_MPH    # lazy: it imports this module
    cat = lambda key: [v for seg in segments for v in seg[key]]
    hc = cat("highway_class")
    pseudo = np.concatenate([[bool(seg.get("pseudo"))] * len(seg["east_m"]) for seg in segments])
    cls = (np.array([ROAD_CLASS_ID.get(c, DEFAULT_ROAD_CLASS_ID) | (CLS_RAMP if c.endswith("_link") else 0)
                     for c in hc]) | np.where(pseudo, CLS_NO_PAVEMENT, 0)).astype(np.uint8)
    tag = np.array(cat("lanes"))
    two = np.array([ROAD_CLASS_LANES_TWOWAY.get(c, DEFAULT_ROAD_CLASS_LANES_TWOWAY) for c in hc])
    ow = sdir != 0      # a tagged lanes= on a oneway counts its own carriageway only
    lanes = np.clip(np.where(tag > 0, np.where(ow, tag, tag // 2), np.where(ow, (two + 1) // 2, two // 2)), 1, 8)
    # stations built before maxspeed existed (2026-10-06) read as untagged -> class default
    ms = np.array([v for seg in segments for v in seg.get("maxspeed", [0] * len(seg["east_m"]))], dtype=float)
    kmh = np.clip(np.round(np.where(ms > 0, ms, [DEFAULT_MPH.get(c, 25) * 1.609344 for c in hc])), 1, 255)

    edges = []
    for i0, i1 in zip(kept[:-1], kept[1:]):
        if seg_id[i0] == seg_id[i1]:
            # one-way only when both ends agree (a way change mid-edge = two-way)
            d = int(sdir[i0]) if sdir[i0] == sdir[i1] else 0
            edges.append((int(node_of[i0]), int(node_of[i1]),
                          max(float(s_m[i1] - s_m[i0]) * 100.0, 1.0), d,
                          int(cls[i0]), int(lanes[i0]), int(kmh[i0]), 0))
    n_along = len(edges)
    # link direction from its end kinds: out of a sink end, into a source end
    # (k = +1 / -1); two-way when neither (or both agree) - ponytail: a sink
    # end linked to the opposite carriageway's interior is still allowed
    # (only the carriageway's own direction stops wrong-way travel there)
    kind = np.where(sink, 1, np.where(source, -1, 0))
    for a, b in sorted(links):
        lo = a if (cls[a] & 7) >= (cls[b] & 7) else b   # the lower road class
        edges.append((int(node_of[a]), int(node_of[b]),
                      max(float(np.hypot(*(xy[a] - xy[b]))) * 100.0, 1.0),
                      int(np.sign(kind[a] - kind[b])),
                      int((cls[lo] & 7) | ((cls[a] | cls[b]) & (CLS_RAMP | CLS_NO_PAVEMENT))),
                      int(min(lanes[a], lanes[b])), int(min(kmh[a], kmh[b])), 0))

    if ship is None:
        ship = (xy[:, 0], xy[:, 1], z + ROAD_LIFT)
    nodes_cm = np.stack([ship[0][kept] * 100.0, -ship[1][kept] * 100.0,
                         (ship[2][kept] - ORIGIN_HEIGHT_M) * 100.0], axis=1)
    control, yld = junction_controls(xy[kept], z[kept], edges, n_along, controls,
                                     lambda s, j: node_of[offs[s] + j])
    # length = 3D chord between the shipped nodes (pad snapping moves them, so
    # station s_m would undercut the straight-line A* heuristic)
    ln = np.linalg.norm(nodes_cm[[e[0] for e in edges]] - nodes_cm[[e[1] for e in edges]], axis=1)
    edges = [(e[0], e[1], max(float(l), 1.0)) + e[3:7] + (int(y),) for e, l, y in zip(edges, ln, yld)]
    return nodes_cm, edges, control


def junction_controls(xy, z, edges, n_along, controls, node_at):
    """-> (control per node uint8, yield bits per edge uint8) - see the module
    docstring. Junction = degree>=3 nodes clustered within JUNCTION_CLUSTER_M
    (a divided road's two carriageway crossings are one junction). An OSM
    control's approach [segment, station, dir] (its stop line) walks up to
    APPROACH_WALK stations along dir to a junction node; stop/give-way set the
    yield bit on that stop-line arc. Junctions with no OSM control get
    CONTROL_PRIORITY: arcs entering them on a lower class than the best one
    (or a ramp merging at the same class) yield.
    ponytail: no geometric conflict zones inside a cluster, lanes:forward /
    backward ignored, equal-class crossings get no priority (traffic decides)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree

    n = len(xy)
    ea = np.array([e[0] for e in edges], dtype=np.int64)
    eb = np.array([e[1] for e in edges], dtype=np.int64)
    dirs = np.array([e[3] for e in edges])
    ecls = np.array([e[4] for e in edges], dtype=np.uint8)
    deg = np.bincount(np.r_[ea, eb], minlength=n)
    jn = np.flatnonzero(deg >= 3)
    cluster = np.full(n, -1)
    control = np.zeros(n, dtype=np.uint8)
    yld = np.zeros(len(edges), dtype=np.uint8)
    if not len(jn):
        return control, yld
    pr = cKDTree(xy[jn]).query_pairs(JUNCTION_CLUSTER_M, output_type="ndarray").reshape(-1, 2)
    pr = pr[np.abs(z[jn[pr[:, 0]]] - z[jn[pr[:, 1]]]) <= ROUTE_END_END_DZ_M]
    ncl, lab = connected_components(coo_matrix((np.ones(len(pr)), (pr[:, 0], pr[:, 1])),
                                               shape=(len(jn), len(jn))), directed=False)
    cluster[jn] = lab
    best = np.full(ncl, CONTROL_PRIORITY, dtype=np.uint8)

    along = {(int(a), int(b)): k for k, (a, b) in enumerate(zip(ea[:n_along], eb[:n_along]))}
    nbr = {}
    for a, b in zip(ea[n_along:], eb[n_along:]):
        nbr.setdefault(int(a), []).append(int(b))
        nbr.setdefault(int(b), []).append(int(a))
    stops, missed = [], 0
    for c in controls:
        kind = CONTROL_KIND.get(c["kind"])
        for s, j, d in c["approaches"] if kind else ():
            u, v = int(node_at(s, j)), int(node_at(s, j + d))
            i, hit = u, -1
            for _ in range(APPROACH_WALK):
                if cluster[i] >= 0:
                    hit = i
                    break
                if (min(i, i + d), max(i, i + d)) not in along:     # segment end
                    hit = next((w for w in nbr.get(i, ()) if cluster[w] >= 0), -1)
                    break
                i += d
            if hit < 0:
                missed += 1
                continue
            best[cluster[hit]] = min(best[cluster[hit]], kind)
            if kind != CONTROL_KIND["traffic_signals"]:
                stops.append((u, v))
    control[jn] = best[lab]
    for u, v in stops:
        k = along.get((u, v), along.get((v, u)))
        if k is not None:
            yld[k] |= 1 if ea[k] == u else 2
    # priority by class where OSM has no control
    src, dst = np.r_[ea, eb], np.r_[eb, ea]
    bit = np.r_[np.ones(len(ea), dtype=np.uint8), np.full(len(ea), 2, dtype=np.uint8)]
    eid = np.r_[np.arange(len(ea)), np.arange(len(ea))]
    ent = (np.r_[dirs >= 0, dirs <= 0] & (cluster[dst] >= 0) & (cluster[src] != cluster[dst])
           & (control[dst] == CONTROL_PRIORITY))
    k = cluster[dst[ent]]
    c = (ecls[eid[ent]] & 7).astype(int)
    ramp = (ecls[eid[ent]] & CLS_RAMP) > 0
    mn = np.full(ncl, 99)
    np.minimum.at(mn, k, c)
    plain = np.zeros(ncl, dtype=bool)          # a non-ramp arc enters at the best class
    np.logical_or.at(plain, k, ~ramp & (c == mn[k]))
    y = (c > mn[k]) | (ramp & (c == mn[k]) & plain[k])
    np.bitwise_or.at(yld, eid[ent][y], bit[ent][y])
    if controls:
        print(f"  junctions: {ncl} clusters, {np.bincount(best, minlength=5)[1:4].tolist()} "
              f"signal/stop/give-way, {missed} OSM approach(es) found no junction")
    return control, yld


def write_route_graph(path, nodes_cm, edges, control):
    nodes_cm = np.asarray(nodes_cm)
    with open(path, "wb") as f:
        f.write(b"ODG1")
        f.write(struct.pack("<I", len(nodes_cm)))
        f.write(np.asarray(nodes_cm[:, :2], dtype="<f4").tobytes())
        f.write(struct.pack("<I", len(edges)))
        f.write(np.array([e[:3] for e in edges], dtype=[("a", "<u4"), ("b", "<u4"), ("l", "<f4")]).tobytes())
        f.write(np.array([e[3] for e in edges], dtype="i1").tobytes())   # oneway block
        f.write(np.asarray(nodes_cm[:, 2], dtype="<f4").tobytes())       # lane block
        f.write(np.asarray(control, dtype="u1").tobytes())
        for k in (4, 5, 6, 7):
            f.write(np.array([e[k] for e in edges], dtype="u1").tobytes())


def main():
    data = json.loads(STATIONS_PATH.read_text())
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    x_cm = (east * 100.0).astype(np.float32)
    y_cm = (-north * 100.0).astype(np.float32)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"centerline_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODC1")
        f.write(struct.pack("<I", len(x_cm)))
        for i in range(len(x_cm)):
            f.write(struct.pack("<ff", float(x_cm[i]), float(y_cm[i])))
    print(f"wrote {out_path} ({len(x_cm)} points, {8 + len(x_cm) * 8} bytes)")

    # ---- map-only sidecar: per-segment simplify, NaN break between segments
    nan_pair = (float("nan"), float("nan"))
    map_points = []
    total_raw = 0
    for seg in data["segments"]:
        seg_east = np.array(seg["east_m"], dtype=np.float64)
        seg_north = np.array(seg["north_m"], dtype=np.float64)
        total_raw += len(seg_east)
        if len(seg_east) < 2:
            continue
        pts = np.stack([seg_east * 100.0, -seg_north * 100.0], axis=1)
        simplified = simplify_rdp(pts, MAP_SIMPLIFY_TOLERANCE_CM)
        if map_points:
            map_points.append(nan_pair)
        map_points.extend((float(p[0]), float(p[1])) for p in simplified)

    map_out_path = OUT_DIR / f"centerline_map_{AREA_NAME}_test.bin"
    with open(map_out_path, "wb") as f:
        f.write(b"ODM1")
        f.write(struct.pack("<I", len(map_points)))
        for x, y in map_points:
            f.write(struct.pack("<ff", x, y))
    print(f"wrote {map_out_path} ({len(map_points)} points incl. breaks, "
          f"simplified from {total_raw} raw stations across "
          f"{len(data['segments'])} segment(s), {8 + len(map_points) * 8} bytes)")

    # route graph: shipped (junction-pad patched) XYZ - read, never written
    from verify_road_fit import _shipped_stations
    osm = json.loads(OSM_PATH.read_text())
    extra, node_links = osm_topology(data["segments"], osm)
    segs = data["segments"] + extra
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    ship = [np.concatenate([a, *(np.array(ps[k]) + lift for ps in extra)])
            for a, k, lift in zip(_shipped_stations(east, north, elev),
                                  ("east_m", "north_m", "elev_m"), (0.0, 0.0, ROAD_LIFT))]
    controls = (json.loads(FURNITURE_PATH.read_text()).get("controls", [])
                if FURNITURE_PATH.exists() else [])
    nodes_cm, edges, control = build_route_graph(segs, station_oneway_dirs(segs, osm), ship, controls, node_links)
    graph_path = OUT_DIR / f"routegraph_{AREA_NAME}_test.bin"
    write_route_graph(graph_path, nodes_cm, edges, control)
    print(f"wrote {graph_path} ({len(nodes_cm)} nodes, {len(edges)} edges, "
          f"{sum(1 for e in edges if e[3])} one-way, {len(extra)} dropped connector(s) "
          f"{sum(ps['s_m'][-1] for ps in extra) / 1000:.1f} km, {len(node_links)} shared-node link(s), "
          f"{len(controls)} OSM control(s))")


if __name__ == "__main__":
    main()
