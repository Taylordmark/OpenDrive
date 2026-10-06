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
the nearest directed OSM way. Only along-segment edges get a direction; link
edges stay two-way. AODRoadManager loads this at
BeginPlay (path derived from CenterlineFilePrefix, centerline_ -> routegraph_)
and runs A* on it; pipeline/checks/verify_route_graph.py is the Python mirror.
"""

import json
import struct
from pathlib import Path

import numpy as np

from test_areas import AREA_NAME, to_local

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
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


def build_route_graph(segments, station_dirs=None):
    """-> (nodes_cm Nx2 float64, edges list of (a, b, len_cm, dir)). dir is
    +1 a->b only, -1 b->a only, 0 both; station_dirs (station_oneway_dirs())
    None = all two-way. See the module docstring for the node/link rules."""
    from scipy.spatial import cKDTree

    xy, z, seg_id, s_m, structural, ends = [], [], [], [], [], []
    sdir = (np.concatenate(station_dirs) if station_dirs is not None
            else np.zeros(sum(len(seg["east_m"]) for seg in segments), dtype=np.int8))
    off = 0
    for k, seg in enumerate(segments):
        n = len(seg["east_m"])
        xy.append(np.stack([seg["east_m"], seg["north_m"]], axis=1))
        z.extend(seg["elev_m"])
        s_m.extend(seg["s_m"])
        seg_id.extend([k] * n)
        # is_struct (real OSM bridge/tunnel ways), NOT is_bridge - that one
        # flags ~49% of summit_county stations (see build_road_stations.py)
        structural.extend(bool(a or b) for a, b in zip(seg["is_struct"], seg["is_tunnel"]))
        if n >= 2:
            ends.extend([off, off + n - 1])
        off += n
    xy = np.concatenate(xy)
    z, s_m, seg_id = np.array(z), np.array(s_m), np.array(seg_id)
    structural = np.array(structural)
    starts = np.concatenate([[0], np.flatnonzero(seg_id[1:] != seg_id[:-1]) + 1])
    local = np.arange(len(xy)) - np.repeat(starts, np.diff(np.append(starts, len(xy))))
    is_last = np.append(seg_id[1:] != seg_id[:-1], True)
    keep = (local % ROUTE_STRIDE == 0) | is_last

    tree = cKDTree(xy)
    links = set()
    for e in ends:
        best = {}   # other segment -> (dist, station)
        for j in tree.query_ball_point(xy[e], ROUTE_ENDPOINT_JOIN_M):
            if seg_id[j] == seg_id[e] or abs(z[j] - z[e]) > ROUTE_ENDPOINT_DZ_M:
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
    is_first = np.append(True, seg_id[1:] != seg_id[:-1])
    sink = (is_last & (sdir == 1)) | (is_first & (sdir == -1))
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
    edges = []
    for i0, i1 in zip(kept[:-1], kept[1:]):
        if seg_id[i0] == seg_id[i1]:
            # one-way only when both ends agree (a way change mid-edge = two-way)
            d = int(sdir[i0]) if sdir[i0] == sdir[i1] else 0
            edges.append((int(node_of[i0]), int(node_of[i1]),
                          max(float(s_m[i1] - s_m[i0]) * 100.0, 1.0), d))
    for a, b in sorted(links):
        edges.append((int(node_of[a]), int(node_of[b]),
                      max(float(np.hypot(*(xy[a] - xy[b]))) * 100.0, 1.0), 0))
    nodes_cm = np.stack([xy[kept, 0] * 100.0, -xy[kept, 1] * 100.0], axis=1)
    return nodes_cm, edges


def write_route_graph(path, nodes_cm, edges):
    with open(path, "wb") as f:
        f.write(b"ODG1")
        f.write(struct.pack("<I", len(nodes_cm)))
        f.write(np.asarray(nodes_cm, dtype="<f4").tobytes())
        f.write(struct.pack("<I", len(edges)))
        f.write(np.array([e[:3] for e in edges], dtype=[("a", "<u4"), ("b", "<u4"), ("l", "<f4")]).tobytes())
        f.write(np.array([e[3] for e in edges], dtype="i1").tobytes())   # oneway block


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

    station_dirs = station_oneway_dirs(data["segments"], json.loads(OSM_PATH.read_text()))
    nodes_cm, edges = build_route_graph(data["segments"], station_dirs)
    graph_path = OUT_DIR / f"routegraph_{AREA_NAME}_test.bin"
    write_route_graph(graph_path, nodes_cm, edges)
    print(f"wrote {graph_path} ({len(nodes_cm)} nodes, {len(edges)} edges, "
          f"{sum(1 for e in edges if e[3])} one-way)")


if __name__ == "__main__":
    main()
