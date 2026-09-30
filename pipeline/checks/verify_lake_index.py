"""Offline check for AODForestManager's lake segment index (ODForestManager.cpp
BuildLakeIndex / IsInsideLake vs IsInsideLakeBruteForce), ported line for line.

Loads the real ODL1 water tiles, builds the same 50 m cell / row index, and
compares indexed vs brute-force answers on random points near shores, inside
rings and across the whole zone. Also counts segments tested per query, which
is what the game-thread cost scales with.

    python pipeline/checks/verify_lake_index.py [water_tile_prefix]
"""
import glob
import math
import os
import random
import struct
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from build_terrain_mesh_tiles import _extract_boundary_rings  # noqa: E402

MASKS = os.path.join(os.path.dirname(__file__), "..", "..", "OpenDriveUE", "Content", "OpenDrive", "Masks")
M = 1500.0            # LakeShoreExclusionMarginCm
CELL = 5000.0         # LakeGridCellCm
MAX_EXTENT = 3000000.0


def load_rings(prefix):
    rings = []
    for path in sorted(glob.glob(os.path.join(MASKS, prefix + "_tile_*.bin"))):
        b = open(path, "rb").read()
        cur = 4 + 16
        (lake_count,) = struct.unpack_from("<I", b, cur); cur += 4
        for _ in range(lake_count):
            vc, tc = struct.unpack_from("<II", b, cur); cur += 8
            verts = struct.unpack_from("<%df" % (vc * 3), b, cur)
            tris_off = cur + vc * 20
            tris = struct.unpack_from("<%dI" % (tc * 3), b, tris_off)
            cur = tris_off + tc * 12
            if not vc or not tc:
                continue
            rs = _extract_boundary_rings([tris[i:i + 3] for i in range(0, len(tris), 3)])
            for idx in rs:
                pts = [(verts[i * 3], verts[i * 3 + 1]) for i in idx]
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                if len(pts) >= 3 and max(xs) - min(xs) <= MAX_EXTENT and max(ys) - min(ys) <= MAX_EXTENT:
                    rings.append((pts, min(xs), max(xs), min(ys), max(ys)))
    return rings


def dist_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    t = min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / l2)) if l2 > 0 else 0.0
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def crosses(pi, pj, x, y):
    return (pi[1] > y) != (pj[1] > y) and x < (pj[0] - pi[0]) * (y - pi[1]) / (pj[1] - pi[1]) + pi[0]


def build_index(rings):
    cells, rows = defaultdict(list), defaultdict(list)
    fl = math.floor
    for r, (ring, *_bb) in enumerate(rings):
        n = len(ring)
        for i in range(n):
            j = i - 1 if i else n - 1
            x0, x1 = sorted((ring[i][0], ring[j][0])); y0, y1 = sorted((ring[i][1], ring[j][1]))
            for cy in range(fl((y0 - M) / CELL), fl((y1 + M) / CELL) + 1):
                for cx in range(fl((x0 - M) / CELL), fl((x1 + M) / CELL) + 1):
                    cells[(cx, cy)].append((r, i))
            for row in range(fl(y0 / CELL), fl(y1 / CELL) + 1):
                rows[row].append((r, i))
    return cells, rows


def inside_indexed(rings, cells, rows, x, y, cost):
    def ends(s):
        ring = rings[s[0]][0]
        return ring[s[1]], ring[s[1] - 1 if s[1] else len(ring) - 1]
    for s in cells.get((math.floor(x / CELL), math.floor(y / CELL)), ()):
        cost[0] += 1
        pi, pj = ends(s)
        if dist_seg(x, y, pi[0], pi[1], pj[0], pj[1]) <= M:
            return True
    cur, inside = None, False
    for s in rows.get(math.floor(y / CELL), ()):
        if s[0] != cur:
            if inside:
                return True
            cur = s[0]
        cost[0] += 1
        pi, pj = ends(s)
        if crosses(pi, pj, x, y):
            inside = not inside
    return inside


def inside_brute(rings, x, y, cost):
    for ring, x0, x1, y0, y1 in rings:
        cost[0] += 1   # bbox reject
        if x < x0 - M or x > x1 + M or y < y0 - M or y > y1 + M:
            continue
        inside, md, n = False, float("inf"), len(ring)
        for i in range(n):
            pi, pj = ring[i], ring[i - 1 if i else n - 1]
            cost[0] += 1
            if crosses(pi, pj, x, y):
                inside = not inside
            md = min(md, dist_seg(x, y, pi[0], pi[1], pj[0], pj[1]))
        if inside or md <= M:
            return True
    return False


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else "water_summit_county_test"
    rings = load_rings(prefix)
    assert rings, "no lake rings for " + prefix
    t = time.perf_counter()
    cells, rows = build_index(rings)
    print("%d rings, %d segs; index %d cells, %d rows (%.1fs)" % (
        len(rings), sum(len(r[0]) for r in rings), len(cells), len(rows), time.perf_counter() - t))

    rng = random.Random(1234)
    gx0 = min(r[1] for r in rings); gx1 = max(r[2] for r in rings)
    gy0 = min(r[3] for r in rings); gy1 = max(r[4] for r in rings)
    pts = []
    for _ in range(1500):   # around random rings' shore margins (same as the in-engine self-check)
        _, x0, x1, y0, y1 = rings[rng.randrange(len(rings))]
        pts.append((rng.uniform(x0 - 2 * M, x1 + 2 * M), rng.uniform(y0 - 2 * M, y1 + 2 * M)))
    for _ in range(1500):   # right on/near a vertex: exercises the 15 m band and cell edges
        ring = rings[rng.randrange(len(rings))][0]
        vx, vy = ring[rng.randrange(len(ring))]
        pts.append((vx + rng.uniform(-2 * M, 2 * M), vy + rng.uniform(-2 * M, 2 * M)))
    for _ in range(1000):   # anywhere in the zone
        pts.append((rng.uniform(gx0, gx1), rng.uniform(gy0, gy1)))

    mism, n_in = [], 0
    stats = {True: [0, 0, 0, 0.0, 0.0], False: [0, 0, 0, 0.0, 0.0]}   # n, brute cost, indexed cost, brute s, indexed s
    for x, y in pts:
        cb, ci = [0], [0]
        t = time.perf_counter(); b = inside_brute(rings, x, y, cb); t1 = time.perf_counter()
        i = inside_indexed(rings, cells, rows, x, y, ci); t2 = time.perf_counter()
        st = stats[b]
        st[0] += 1; st[1] += cb[0]; st[2] += ci[0]; st[3] += t1 - t; st[4] += t2 - t1
        n_in += b
        if b != i:
            mism.append((x, y, b, i))
    print("%d points, %d inside; %d mismatch(es)" % (len(pts), n_in, len(mism)))
    for label, key in (("dry land (tree kept)", False), ("water/shore (tree dropped)", True)):
        k, c_b, c_i, s_b, s_i = stats[key]
        if k:
            print("  %-27s n=%4d  tests/query brute %6.0f indexed %4.0f (%4.0fx)  py ms brute %.2f indexed %.3f" % (
                label, k, c_b / k, c_i / k, c_b / max(c_i, 1), s_b / k * 1e3, s_i / k * 1e3))
    worst_row = max(len(v) for v in rows.values()); worst_cell = max(len(v) for v in cells.values())
    print("worst row %d segs, worst cell %d segs" % (worst_row, worst_cell))
    for m in mism[:10]:
        print("  MISMATCH x=%.0f y=%.0f brute=%s indexed=%s" % m)
    assert not mism, "indexed IsInsideLake disagrees with brute force"
    print("OK")


if __name__ == "__main__":
    main()
