"""Arc 4.2 v3 (next steps.md SS7.37) offline verification of the world-map
navigation routing. Loads the SAME runtime sidecars the C++ loads
(routegraph_<area>_test.bin ODG1, labels_<area>_test.bin ODL1,
centerline_<area>_test.bin ODC1, centerline_map_<area>_test.bin ODM1),
rebuilds the town nodes the way AODRoadManager::BuildTownNodes does, and
runs a faithful mirror of FODRouteGraph::FindPath (A*, Euclidean heuristic)
between real town pairs. Cross-checks every A* length against scipy's
Dijkstra, runs the sanity checks, and writes a PNG + log.

Mirror of AODRoadManager::FindRoutePath / RecomputeRoute (turn list) /
UpdateRouteProgress in OpenDriveUE/Source/OpenDriveUE/ODRoadManager.cpp -
keep in sync.

Usage: python verify_route_graph.py [routegraph.bin]   (default: summit_county's)
"""
import heapq
import struct
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components, dijkstra

ROOT = Path(__file__).resolve().parents[2]
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
AREA = "summit_county"
OUT = ROOT / "scratchpad"
OUT.mkdir(exist_ok=True)


def load_graph(path):
    b = path.read_bytes()
    assert b[:4] == b"ODG1", "bad magic"
    n = struct.unpack_from("<I", b, 4)[0]
    nodes = np.frombuffer(b, "<f4", n * 2, 8).reshape(n, 2).astype(np.float64)
    o = 8 + n * 8
    e = struct.unpack_from("<I", b, o)[0]
    edges = np.frombuffer(b, [("a", "<u4"), ("b", "<u4"), ("l", "<f4")], e, o + 4)
    # optional oneway block (2026-10-06): E * int8, +1 a->b only, -1 b->a only
    assert len(b) in (o + 4 + e * 12, o + 4 + e * 13), "trailing/short bytes"
    dirs = (np.frombuffer(b, "i1", e, o + 4 + e * 12) if len(b) == o + 4 + e * 13
            else np.zeros(e, dtype=np.int8))
    return nodes, edges, dirs


# AODRoadManager.cpp WrongWayCostMult: a wrong-way arc costs this much more,
# so a legal route always wins and a graph gap still routes (minimal wrong-way)
WRONG_WAY_COST_MULT = 20.0


def build_csr(n, edges, dirs):
    """Same CSR build as AODRoadManager::LoadRouteGraph (both directions per
    edge, so junction degree ignores oneway; the wrong-way arc costs more).
    Entries are (node, cost, legal)."""
    adj = [[] for _ in range(n)]
    for (a, b, l), d in zip(edges, dirs):
        adj[a].append((int(b), float(l) * (1.0 if d >= 0 else WRONG_WAY_COST_MULT), d >= 0))
        adj[b].append((int(a), float(l) * (1.0 if d <= 0 else WRONG_WAY_COST_MULT), d <= 0))
    return adj


def find_path(nodes, adj, start, goal):
    """Mirror of FODRouteGraph::FindPath."""
    g = np.full(len(nodes), np.inf)
    prev = np.full(len(nodes), -1, dtype=np.int64)
    closed = np.zeros(len(nodes), dtype=bool)
    h = lambda i: float(np.hypot(*(nodes[i] - nodes[goal])))
    g[start] = 0.0
    heap = [(h(start), start)]
    while heap:
        _, u = heapq.heappop(heap)
        if u == goal:
            break
        if closed[u]:
            continue
        closed[u] = True
        for v, l, _ in adj[u]:
            ng = g[u] + l
            if ng < g[v]:
                g[v] = ng
                prev[v] = u
                heapq.heappush(heap, (ng + h(v), v))
    if not np.isfinite(g[goal]):
        return None, None
    path = [goal]
    while path[-1] != start:
        path.append(int(prev[path[-1]]))
    return path[::-1], g[goal]


# ODRoadManager.cpp SS7.37 constants
TURN_LOOK_CM, TURN_MIN_DEG, TURN_MERGE_CM = 2500.0, 35.0, 3000.0
OFF_ROUTE_CM, ARRIVE_CM = 5000.0, 3000.0


def route_turns(pts, cum, deg_path):
    """Mirror of the turn loop in AODRoadManager::RecomputeRoute."""
    turns = []
    n = len(pts)
    for k in range(1, n - 1):
        if deg_path[k] < 3:
            continue
        j, m = k - 1, k + 1
        while j > 0 and cum[k] - cum[j] < TURN_LOOK_CM:
            j -= 1
        while m < n - 1 and cum[m] - cum[k] < TURN_LOOK_CM:
            m += 1
        a = pts[k] - pts[j]; a = a / max(np.hypot(*a), 1e-9)
        b = pts[m] - pts[k]; b = b / max(np.hypot(*b), 1e-9)
        ang = np.degrees(np.arctan2(a[0] * b[1] - a[1] * b[0], a @ b))
        if abs(ang) < TURN_MIN_DEG:
            continue
        if turns and cum[k] - turns[-1][0] < TURN_MERGE_CM:
            if abs(ang) > abs(turns[-1][1]):
                turns[-1] = (cum[k], ang)
            continue
        turns.append((cum[k], ang))
    return turns


def progress(pts, cum, seg, p):
    """Mirror of AODRoadManager::UpdateRouteProgress's windowed search ->
    (seg, progress_cm, remaining_cm, off_route_dist_cm)."""
    lo, hi = max(0, seg - 3), min(len(pts) - 2, seg + 60)
    best = (np.inf, seg, 0.0)
    for i in range(lo, hi + 1):
        a, d = pts[i], pts[i + 1] - pts[i]
        dd = d @ d
        t = min(max((p - a) @ d / dd, 0.0), 1.0) if dd > 1.0 else 0.0
        d2 = float(((a + d * t - p) ** 2).sum())
        if d2 < best[0]:
            best = (d2, i, cum[i] + t * np.sqrt(dd))
    return best[1], best[2], max(0.0, cum[-1] - best[2]), np.sqrt(best[0])


def nearest(nodes, p):
    return int(np.argmin(((nodes - p) ** 2).sum(axis=1)))


def load_points(path, magic):
    b = path.read_bytes()
    assert b[:4] == magic
    n = struct.unpack_from("<I", b, 4)[0]
    return np.frombuffer(b, "<f4", n * 2, 8).reshape(n, 2).astype(np.float64)


def load_towns():
    """AODRoadManager::LoadPlaceLabels + BuildTownNodes (centroid per city,
    snapped to nearest ODC1 centerline point; the C++ end-margin walk of
    NearestRoadPoint moves it <=12 m, irrelevant at route scale)."""
    b = (MASKS / f"labels_{AREA}_test.bin").read_bytes()
    assert b[:4] == b"ODL1"
    n = struct.unpack_from("<I", b, 20)[0]
    cur, acc = 24, {}
    for _ in range(n):
        x, y = struct.unpack_from("<ff", b, cur); cur += 8
        strs = []
        for _ in range(2):
            ln = struct.unpack_from("<H", b, cur)[0]; cur += 2
            strs.append(b[cur:cur + ln].decode("utf-8")); cur += ln
        if strs[1]:
            acc.setdefault(strs[1], []).append((x, y))
    cl = load_points(MASKS / f"centerline_{AREA}_test.bin", b"ODC1")
    towns = {}
    for name, pts in acc.items():
        c = np.mean(pts, axis=0)
        towns[name] = cl[nearest(cl, c)]
    return towns


def main():
    log = []
    say = lambda s: (print(s), log.append(s))
    nodes, edges, dirs = load_graph(Path(sys.argv[1]) if len(sys.argv) > 1
                                    else MASKS / f"routegraph_{AREA}_test.bin")
    n = len(nodes)
    adj = build_csr(n, edges, dirs)
    deg = np.array([len(a) for a in adj])
    say(f"graph: {n} nodes, {len(edges)} edges, junction nodes (deg>=3): {(deg >= 3).sum()}, "
        f"dead ends (deg==1): {(deg == 1).sum()}, isolated: {(deg == 0).sum()}")
    say(f"one-way edges: {(dirs != 0).sum()}")
    m = coo_matrix((edges["l"].astype(np.float64), (edges["a"], edges["b"])), shape=(n, n)).tocsr()
    # directed, wrong-way-penalized copy for the A* cross-check
    lw = edges["l"].astype(np.float64)
    md = coo_matrix((np.concatenate([lw * np.where(dirs >= 0, 1.0, WRONG_WAY_COST_MULT),
                                     lw * np.where(dirs <= 0, 1.0, WRONG_WAY_COST_MULT)]),
                     (np.concatenate([edges["a"], edges["b"]]),
                      np.concatenate([edges["b"], edges["a"]]))), shape=(n, n)).tocsr()
    ncomp, labels = connected_components(m, directed=False)
    sizes = np.bincount(labels)
    say(f"connected components: {ncomp}; largest holds {sizes.max()} nodes "
        f"({100.0 * sizes.max() / n:.1f}%)")

    towns = load_towns()
    say(f"towns: {len(towns)} -> {', '.join(sorted(towns))}")
    main_c = np.argmax(sizes)
    off_main = [t for t, p in towns.items() if labels[nearest(nodes, p)] != main_c]
    say(f"towns whose nearest graph node is in the largest component: "
        f"{len(towns) - len(off_main)}/{len(towns)} (not: {off_main})")
    wanted = [("Frisco", "Breckenridge"), ("Frisco", "Vail"), ("Dillon", "Keystone"),
              ("Silverthorne", "Copper Mountain"), ("Breckenridge", "Vail"),
              ("Keystone", "Breckenridge"), ("Silverthorne", "Frisco"), ("Vail", "Keystone")]
    pairs = [(a, b) for a, b in wanted if a in towns and b in towns]
    missing = [p for p in wanted if p not in pairs]
    if missing:
        say(f"skipped (town not in data): {missing}")

    ok_all = True
    routes = []
    sim_pair = None
    for a, b in pairs:
        s, t = nearest(nodes, towns[a]), nearest(nodes, towns[b])
        path, length = find_path(nodes, adj, s, t)
        if path is None:
            say(f"FAIL {a} -> {b}: no path (components {labels[s]} vs {labels[t]})")
            ok_all = False
            continue
        dj = dijkstra(md, directed=True, indices=s)[t]
        straight = float(np.hypot(*(towns[a] - towns[b])))
        pts = nodes[path]
        # connected: every consecutive pair is an actual graph edge
        connected = all(any(v == path[i + 1] for v, _, _ in adj[path[i]]) for i in range(len(path) - 1))
        # (cost, legal) of the arc A* took per step -> real length + wrong-way part
        steps = [min((c, ok) for v, c, ok in adj[path[i]] if v == path[i + 1]) for i in range(len(path) - 1)]
        wrong = sum(c / WRONG_WAY_COST_MULT for c, ok in steps if not ok)
        real = sum(c for c, ok in steps if ok) + wrong
        poly_len = float(np.hypot(*np.diff(pts, axis=0).T).sum())
        end_s = float(np.hypot(*(pts[0] - towns[a])))
        end_t = float(np.hypot(*(pts[-1] - towns[b])))
        checks = {
            "connected": connected,
            "A*==Dijkstra": abs(length - dj) < 1.0,   # cm, penalized cost
            "len>=straight": real >= float(np.hypot(*(pts[0] - pts[-1]))),
            "polyline<=len": poly_len <= real + 1.0,
            "endpoints<=50m": end_s <= 5000 and end_t <= 5000,
        }
        ok = all(checks.values())
        ok_all &= ok
        length = real
        say(f"{'OK  ' if ok else 'FAIL'} {a} -> {b}: route {length / 1e5:.2f} km (wrong-way {wrong / 100:.0f} m), "
            f"straight {straight / 1e5:.2f} km (ratio {length / max(straight, 1):.2f}), "
            f"{len(path)} nodes, {int((deg[path] >= 3).sum())} junction nodes, "
            f"end offsets {end_s / 100:.1f} m / {end_t / 100:.1f} m, dijkstra {dj / 1e5:.2f} km "
            + " ".join(f"{k}={'y' if v else 'N'}" for k, v in checks.items()))
        cum = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
        turns = route_turns(pts, cum, deg[path])
        say(f"     {len(turns)} turns: " + ", ".join(
            f"{'R' if t[1] > 0 else 'L'}{abs(t[1]):.0f}@{t[0] / 1e5:.2f}km" for t in turns[:12])
            + (" ..." if len(turns) > 12 else ""))
        routes.append((a, b, pts))
        if (a, b) == ("Frisco", "Breckenridge"):
            sim_pair = (path, pts, cum, turns)

    # ---- drive simulation (mirror of UpdateRouteProgress + reroute) on
    # Frisco -> Breckenridge: follow the route at 20 m steps; remaining must
    # fall monotonically and arrival must fire; then jump 150+ m off-route
    # halfway and confirm the off-route test trips and the reroute from
    # there reaches the same destination.
    if sim_pair:
        path, pts, cum, turns = sim_pair
        seg, rem_prev, mono, arrived, max_off = 0, np.inf, True, False, 0.0
        for s_cm in np.arange(0.0, cum[-1] + 1, 2000.0):
            i = min(np.searchsorted(cum, s_cm, side="right") - 1, len(pts) - 2)
            t = (s_cm - cum[i]) / max(cum[i + 1] - cum[i], 1e-9)
            p = pts[i] + (pts[i + 1] - pts[i]) * t
            seg, _, rem, off = progress(pts, cum, seg, p)
            max_off = max(max_off, off)
            mono &= rem <= rem_prev + 1.0
            rem_prev = rem
            if rem < ARRIVE_CM and off < OFF_ROUTE_CM:
                arrived = True
                break
        mid = pts[len(pts) // 2]
        cand = np.flatnonzero((np.hypot(*(nodes - mid).T) > 15000) & (np.hypot(*(nodes - mid).T) < 40000)
                              & (labels == labels[path[0]]))
        stray = nodes[cand[np.argmin(np.hypot(*(nodes[cand] - mid).T))]]
        _, _, _, off = progress(pts, cum, len(pts) // 2, stray)
        new_path, new_len = find_path(nodes, adj, nearest(nodes, stray), path[-1])
        sim_ok = mono and arrived and max_off < 1.0 and off > OFF_ROUTE_CM and new_path is not None
        ok_all &= sim_ok
        say(f"{'OK  ' if sim_ok else 'FAIL'} drive sim Frisco -> Breckenridge: remaining monotonic={mono}, "
            f"arrived={arrived}, max off-route on-line={max_off:.2f} cm; stray point {off / 100:.0f} m off "
            f"(> {OFF_ROUTE_CM / 100:.0f} m -> reroute) -> new route {new_len / 1e5 if new_len else float('nan'):.2f} km "
            f"to the same destination")

    fig, ax = plt.subplots(figsize=(14, 14))
    mc = load_points(MASKS / f"centerline_map_{AREA}_test.bin", b"ODM1")
    ax.plot(mc[:, 0] / 1e5, -mc[:, 1] / 1e5, color="#9aa7b8", lw=0.5)
    colors = plt.cm.tab10(np.linspace(0, 1, max(len(routes), 1)))
    for (a, b, pts), c in zip(routes, colors):
        ax.plot(pts[:, 0] / 1e5, -pts[:, 1] / 1e5, color=c, lw=2.5, label=f"{a} -> {b}")
    for name, p in towns.items():
        ax.plot(p[0] / 1e5, -p[1] / 1e5, "ko", ms=3)
        ax.annotate(name, (p[0] / 1e5, -p[1] / 1e5), fontsize=7)
    ax.set_aspect("equal"); ax.set_xlabel("east km"); ax.set_ylabel("north km")
    ax.set_title(f"{AREA} route graph ({n} nodes) - A* routes between towns")
    ax.legend(loc="lower left", fontsize=8)
    fig.savefig(OUT / "routes_summit_county.png", dpi=110, bbox_inches="tight")
    say(f"wrote {OUT / 'routes_summit_county.png'}")
    say("ALL CHECKS PASSED" if ok_all else "SOME CHECKS FAILED")
    (OUT / "routes_summit_county.log").write_text("\n".join(log) + "\n")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
