"""Custom tracks (2026-10-13). One GeoJSON per track in
content_src/tracks/<name>.geojson - draw a LineString at geojson.io and save.
Feature properties:
    area            test_areas.AREAS key the track lives in (required)
    loop            true = circuit: finish on the start line, laps run back
                    to back. Default false (point to point).
    road            true = the line IS a new road, not a route over real
                    ones: fetch_osm_network.py merges it into osm.json
                    (custom_road_elements below), so rebuild_area.py builds
                    it on the real terrain like any OSM way. Optional "tags"
                    (dict) override the OSM tags (default highway=secondary).
    gate_spacing_m  default 300 (the Dragon's)

Either way the race follows the line's vertices over the area's route graph
(A* between consecutive vertices), so a handful of clicks on real roads is a
whole track.

Usage:  python pipeline/build_track.py <name>
        (needs the area's routegraph_<area>_test.bin; a road track needs
        `OD_AREA=<area> python pipeline/rebuild_area.py` first)
Writes  OpenDriveUE/Content/OpenDrive/Masks/track_<name>.bin
            'ODK1' + uint32 flags (bit 0 loop)
            + uint32 G + G * (float32 x, y, z cm, yaw deg)  gates, start
              first, finish last (a loop's finish = its start gate)
            + uint32 N + N * (float32 x, y cm)  track line, spawn at [0]
        Masks/tracks.json  {name: {area, title, loop, km}} - the menu's
            Track row lists these (this track's entry added/updated)
        scratchpad/track_<name>.png
Play:   -odtrack=<name> (AODGameMode spawns the AODRaceManager).
"""
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
TRACKS = ROOT / "content_src" / "tracks"
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

START_GATE_M = 200.0     # spawn at the line's start, flying start gate this far on
SNAP_WARN_M = 50.0       # a vertex farther than this from any road is probably a misclick
CUSTOM_ID0 = 10 ** 15    # synthetic OSM ids: far above real ones (~1e10), under 2^53
ROAD_STEP_M = 10.0       # custom road vertex spacing after the spline
END_SNAP_M = 15.0        # a custom road end this close to a fetched node joins it


def load_track(path):
    gj = json.loads(Path(path).read_text())
    feat = gj["features"][0] if gj.get("type") == "FeatureCollection" else gj
    if feat["geometry"]["type"] != "LineString":
        raise SystemExit(f"{path}: need a LineString, got {feat['geometry']['type']}")
    return np.array([c[:2] for c in feat["geometry"]["coordinates"]], float), feat.get("properties") or {}


def place_gates(L, spacing, loop, start=START_GATE_M):
    """Gate arc-length positions (same units as L): start gate, every
    `spacing`, finish. A loop's finish is start + L (the start line again)."""
    start = min(start, 0.25 * L)
    end = start + L if loop else L
    s = np.arange(start, end, spacing)
    if len(s) > 1 and end - s[-1] < 0.5 * spacing:
        s = s[:-1]   # no sliver sector before the finish
    return np.append(s, end)


def catmull_rom(p, step, closed):
    """Centripetal Catmull-Rom through every point of p (k,2), sampled about
    every `step`. A click every 50-100 m would otherwise be a kink per click
    (resample_stations' Gaussian only rounds ~5 m). Closed: no repeated end."""
    pts = (np.vstack([p[-1], p, p[0], p[1]]) if closed
           else np.vstack([2 * p[0] - p[1], p, 2 * p[-1] - p[-2]]))
    out = []
    for i in range(1, len(pts) - 2):
        p0, p1, p2, p3 = pts[i - 1:i + 3]
        t1 = np.linalg.norm(p1 - p0) ** 0.5
        t2 = t1 + np.linalg.norm(p2 - p1) ** 0.5
        t3 = t2 + np.linalg.norm(p3 - p2) ** 0.5
        n = max(1, int(np.ceil(np.linalg.norm(p2 - p1) / step)))
        for t in np.linspace(t1, t2, n, endpoint=False):
            a1 = (t1 - t) / t1 * p0 + t / t1 * p1
            a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
            a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
            b1 = (t2 - t) / t2 * a1 + t / t2 * a2
            b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
            out.append((t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2)
    if not closed:
        out.append(p[-1])
    return np.array(out)


def _dedup(ll):
    keep = np.r_[True, np.any(np.diff(ll, axis=0) != 0, axis=1)]
    return ll[keep]


def custom_road_elements(area, osm_nodes):
    """Synthetic OSM (nodes, ways) for every road track of `area`. Ends
    within END_SNAP_M of a fetched node reuse that node, so they become real
    junctions. ponytail: only the ends join - a drawn road crossing a real
    one mid-line overlaps it (the route graph still links them by
    proximity); split the line at the crossing if that matters."""
    out_nodes, out_ways = [], []
    real = np.array([(n["lon"], n["lat"]) for n in osm_nodes]) if osm_nodes else np.zeros((0, 2))
    next_id = CUSTOM_ID0
    for k, path in enumerate(sorted(TRACKS.glob("*.geojson"))):
        ll, props = load_track(path)
        if not props.get("road") or props.get("area") != area:
            continue
        ll = _dedup(ll)
        closed = len(ll) > 3 and np.allclose(ll[0], ll[-1])
        if closed:
            ll = ll[:-1]
        # local metres (equirect about the line's centre - plenty for a spline)
        lat0 = np.radians(ll[:, 1].mean())
        sx, sy = 111320.0 * np.cos(lat0), 110540.0
        o = ll.mean(axis=0)
        xy = catmull_rom((ll - o) * [sx, sy], ROAD_STEP_M, closed)
        lonlat = xy / [sx, sy] + o
        ids = []
        for lon, lat in lonlat:
            out_nodes.append({"type": "node", "id": next_id, "lat": float(lat), "lon": float(lon)})
            ids.append(next_id)
            next_id += 1
        if closed:
            ids.append(ids[0])
        elif len(real):
            for end in (0, -1):
                d = np.hypot((real[:, 0] - lonlat[end, 0]) * sx, (real[:, 1] - lonlat[end, 1]) * sy)
                j = int(np.argmin(d))
                if d[j] < END_SNAP_M:
                    ids[end] = osm_nodes[j]["id"]
        tags = {"highway": "secondary", "name": props.get("name", path.stem)}
        tags.update(props.get("tags") or {})
        out_ways.append({"type": "way", "id": CUSTOM_ID0 + k, "nodes": ids, "tags": tags})
        print(f"  custom road '{path.stem}': {len(ids)} nodes, {tags['highway']}")
    return out_nodes, out_ways


def build_route(nodes, adj, find_path, nearest_heading, wp_cm, loop):
    """Snap each waypoint to the graph and A* between them -> (node path,
    per-waypoint snap distance cm). The snap faces the next waypoint (the
    nav's route-start rule), so a click on a divided road takes the
    carriageway going the right way instead of a legal loop back."""
    nxt = np.roll(wp_cm, -1, axis=0) if loop else np.vstack([wp_cm[1:], 2 * wp_cm[-1] - wp_cm[-2]])
    snap = np.array([nearest_heading(nodes, adj, p, h - p) for p, h in zip(wp_cm, nxt)])
    d = np.hypot(*(nodes[snap] - wp_cm).T)
    if loop:
        snap = np.append(snap, snap[0])
    path = [int(snap[0])]
    for i, (a, b) in enumerate(zip(snap[:-1], snap[1:])):
        if a == b:
            continue
        p, _ = find_path(nodes, adj, int(a), int(b))
        if p is None:
            raise SystemExit(f"no road from waypoint {i} to {i + 1}")
        path += p[1:]
    # a loop whose start snapped onto a side stub leaves and re-enters it at
    # the seam - trim that shared in-and-out
    while loop and len(path) > 3 and path[1] == path[-2]:
        path = path[1:-1]
    return path, d


def main(name):
    ll, props = load_track(TRACKS / f"{name}.geojson")
    area = props.get("area")
    if not area:
        raise SystemExit(f"{name}: properties.area is required")
    os.environ["OD_AREA"] = area   # test_areas reads it at import
    from test_areas import to_local
    sys.path.insert(0, str(Path(__file__).resolve().parent / "checks"))
    from verify_route_graph import build_csr, find_path, load_graph, nearest_heading

    nodes, edges, dirs, lane = load_graph(MASKS / f"routegraph_{area}_test.bin")
    if lane is None:
        raise SystemExit("route graph has no Z block - re-export it "
                         "(rebuild_area.py --from export_road_centerline_bin.py)")
    adj = build_csr(len(nodes), edges, dirs)
    loop = bool(props.get("loop"))
    ll = _dedup(ll)
    if loop and len(ll) > 2 and np.allclose(ll[0], ll[-1]):
        ll = ll[:-1]
    e, n = to_local(ll[:, 0], ll[:, 1])
    path, snap_cm = build_route(nodes, adj, find_path, nearest_heading, np.c_[e * 100.0, -n * 100.0], loop)
    for i in np.flatnonzero(snap_cm > SNAP_WARN_M * 100):
        print(f"  WARNING: vertex {i} is {snap_cm[i] / 100:.0f} m from the nearest road")
    wrong = sum(not next(ok for v, _, ok in adj[a] if v == b) for a, b in zip(path, path[1:]))
    if wrong:
        print(f"  WARNING: {wrong} arc(s) run the wrong way up a one-way road")
    seen = set()
    for i in path[:-1] if loop else path:
        if i in seen:
            x, y = nodes[i] / 100
            print(f"  WARNING: route doubles back at ({x:.0f}, {-y:.0f}) m east/north - "
                  f"a vertex is probably on a dead end or side road")
            break
        seen.add(i)

    xy = nodes[path]
    z = lane["z"][path]
    cum = np.r_[0.0, np.cumsum(np.hypot(*np.diff(xy, axis=0).T))]
    L = cum[-1]
    gs = place_gates(L, props.get("gate_spacing_m", 300.0) * 100.0, loop, START_GATE_M * 100.0)
    at = lambda s, arr: np.interp(s % L if loop else s, cum, arr)
    look = 1000.0   # cm either side for the gate heading
    yaw = np.degrees(np.arctan2(at(gs + look, xy[:, 1]) - at(gs - look, xy[:, 1]),
                                at(gs + look, xy[:, 0]) - at(gs - look, xy[:, 0])))
    gates = np.c_[at(gs, xy[:, 0]), at(gs, xy[:, 1]), at(gs, z), yaw].astype("<f4")

    dst = MASKS / f"track_{name}.bin"
    with open(dst, "wb") as f:
        f.write(b"ODK1" + struct.pack("<II", int(loop), len(gates)))
        f.write(gates.tobytes())
        f.write(struct.pack("<I", len(xy)) + xy.astype("<f4").tobytes())
    print(f"wrote {dst}: {L / 1e5:.2f} km {'loop' if loop else 'point to point'}, "
          f"{len(gates)} gates, max vertex snap {snap_cm.max() / 100:.0f} m")
    manifest = MASKS / "tracks.json"
    tracks = json.loads(manifest.read_text()) if manifest.exists() else {}
    tracks[name] = {"area": area, "title": props.get("name", name), "loop": loop,
                    "km": round(L / 1e5, 2)}
    manifest.write_text(json.dumps(tracks, indent=2, sort_keys=True))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.plot(xy[:, 0] / 100, -xy[:, 1] / 100, "-", color="goldenrod", lw=2)
    ax.plot(e, n, "x", color="k", ms=6, label="vertices")
    ax.plot(gates[1:-1, 0] / 100, -gates[1:-1, 1] / 100, "o", color="y", mec="k", label="gates")
    ax.plot(gates[0, 0] / 100, -gates[0, 1] / 100, "o", color="lime", mec="k", ms=10, label="start")
    ax.plot(gates[-1, 0] / 100, -gates[-1, 1] / 100, "s", color="red", mec="k", ms=7, mfc="none", label="finish")
    ax.set_aspect("equal")
    ax.set_title(f"{name} ({area}): {L / 1e5:.2f} km, {len(gates)} gates")
    ax.legend()
    (ROOT / "scratchpad").mkdir(exist_ok=True)
    fig.savefig(ROOT / "scratchpad" / f"track_{name}.png", dpi=110)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
