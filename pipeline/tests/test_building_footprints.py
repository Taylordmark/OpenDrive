"""Footprint cleanup and mesh merge (pipeline/build_building_mesh.py, 2026-10-21):
  - merge() puts every piece's windowed vertices (below its n_solid) first,
    keeps each triangle on the same points, and n_solid covers exactly them;
  - simplify_ring drops jitter within tolerance but never a pinned vertex;
  - square_ring squares a skewed rectangle and an L-shape (corners to
    SQUARE_DONE, area within SQUARE_MAX_AREA), never moves a pinned vertex,
    and leaves non-rectilinear rings (triangle, octagon) and corners off
    90 deg by more than SQUARE_DEG alone;
  - extrude_footprint with a bay width gives every wall a whole number of
    bays, running on round the building, and a short jog the wall between bays;
  - parapet() on either ring winding: coping faces up at the parapet top,
    cornice undersides face down, the inner ring sits inside the footprint,
    the outer face keeps the wall's U and continues its V; a footprint too
    thin or too sharp to offset gets none.

Run:  python pipeline/tests/test_building_footprints.py"""
import math
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
from build_building_mesh import (CORNICE_H_CM, CORNICE_OUT_CM, FACADE_TILE_M, SQUARE_DONE,   # noqa: E402
                                 SQUARE_MAX_AREA, extrude_footprint, merge, parapet, simplify_ring, square_ring)
import shapely   # noqa: E402
from build_building_mesh import _corners   # noqa: E402
import numpy as np   # noqa: E402


def area(r):
    return abs(sum(r[i - 1][0] * r[i][1] - r[i][0] * r[i - 1][1] for i in range(len(r)))) / 2


# ---- merge
a = ([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 0)] * 3, [(0, 1, 2)], 2)
b = ([(5, 0, 0), (6, 0, 0), (7, 0, 0), (8, 0, 0)], [(1, 1)] * 4, [(0, 2, 3), (1, 2, 3)], 1)
c = ([(9, 0, 0), (9, 1, 0), (9, 2, 0)], [(2, 2)] * 3, [(0, 1, 2)], 0)   # no windows at all
v, uv, t, n = merge([a, b, c])
assert n == 3 and sorted(x[0] for x in v[:n]) == [0, 1, 5], (n, v)
assert len(v) == len(uv) == 10
got = sorted(tuple(v[i][0] * 10 + v[i][1] for i in tri) for tri in t)
want = sorted(tuple(p[0][i][0] * 10 + p[0][i][1] for i in tri) for p in (a, b, c) for tri in p[2])
assert got == want, (got, want)
assert merge([c])[3] == 0

# ---- simplify
rect = [(0, 0), (500, 8), (1000, 0), (1000, 600), (0, 600)]
r, _ = simplify_ring(rect, [False] * 5, 25.0)
assert len(r) == 4 and (500, 8) not in r, r
r, _ = simplify_ring(rect, [False, True, False, False, False], 25.0)
assert (500, 8) in r, r
r, _ = simplify_ring([(0, 0), (500, 60), (1000, 0), (1000, 600), (0, 600)], [False] * 5, 25.0)
assert len(r) == 5, "a 60 cm jog is real"


def squared(r):
    return np.abs(_corners(np.array(r, float))[0]).max() < SQUARE_DONE


# ---- square: skewed rectangle, L-shape, pinned corner
skew = [(0, 0), (1000, 15), (1010, 600), (-10, 590)]
r, st = square_ring(skew, [False] * 4)
assert st == "squared" and squared(r), (st, r)
assert abs(area(r) - area(skew)) <= SQUARE_MAX_AREA * area(skew)
r, st = square_ring(skew, [True, False, False, False])
assert st == "squared" and r[0] == (0.0, 0.0), r
ell = [(0, 0), (1200, 10), (1205, 500), (600, 505), (595, 1000), (-5, 1000)]
r, st = square_ring(ell, [False] * 6)
assert st == "squared" and squared(r), (st, r)

# ---- square: left alone
tri = [(0, 0), (1000, 0), (500, 800)]
assert square_ring(tri, [False] * 3) == (tri, "not_square")
octa = [(math.cos(k * math.pi / 4) * 800, math.sin(k * math.pi / 4) * 800) for k in range(8)]
assert square_ring(octa, [False] * 8)[1] == "not_square"
off = [(0, 0), (1000, 0), (1000 + 600 * math.tan(math.radians(20)), 600), (0, 600)]   # 20 deg off
r, st = square_ring(off, [False] * 4)
assert not squared(r), "a corner 20 deg off square is a real corner"

# ---- whole-bay wall U
ring = [(0, 0), (910, 0), (910, 100), (1010, 100), (1010, 600), (0, 600)]   # 9.1, 1, 1, 5, 10.1, 6 m
v, uv, t = extrude_footprint(ring, 0.0, 600.0, 0, bay_m=3.0)
bays = []
for e in range(len(ring)):
    u0, u1 = uv[4 * e][0] * FACADE_TILE_M, uv[4 * e + 1][0] * FACADE_TILE_M
    bays.append((round(u0 / 3.0, 6), round(u1 / 3.0, 6)))
    assert v[4 * e][:2] == ring[e], "positions untouched"
assert bays[0] == (0, 3), bays
assert bays[1] == (3 - 1 / 6, 3 + 1 / 6) or abs(bays[1][0] - (3 - 1 / 6)) < 1e-5, "1 m jog straddles bay line 3"
assert bays[3] == (3, 5) and bays[4] == (5, 8) and bays[5] == (8, 10), bays

# ---- parapet + cornice
def nz(v, tri):
    (x0, y0, _), (x1, y1, _), (x2, y2, _) = (v[i] for i in tri)
    return (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)


for ring in ([(0, 0), (1000, 0), (1000, 600), (600, 600), (600, 1000), (0, 1000)],
             [(0, 0), (0, 1000), (600, 1000), (600, 600), (1000, 600), (1000, 0)]):   # CCW, CW
    wv, wuv, wt = extrude_footprint(ring, 50.0, 700.0, 0, bay_m=3.0)
    for cornice in (False, True):
        pv, puv, pt = parapet(wv, wuv, len(ring), 750.0, 100.0, cornice)
        foot = shapely.Polygon(ring)
        assert max(z for *_, z in pv) == 850.0 and min(z for *_, z in pv) == 750.0
        top = [t for t in pt if all(pv[i][2] == 850.0 for i in t)]
        assert top and all(nz(pv, t) > 0 for t in top), "coping faces up"
        inner = {pv[i][:2] for t in top for i in t} - {p for p in ring}
        out = [q for q in inner if not foot.contains(shapely.Point(q))]
        assert len(out) == (len(ring) if cornice else 0), (cornice, out)   # cornice ring is outside
        under = [t for t in pt if all(pv[i][2] == 850.0 - CORNICE_H_CM for i in t) and abs(nz(pv, t)) > 0]
        assert all(nz(pv, t) < 0 for t in under) and (len(under) > 0) == cornice, "cornice underside faces down"
        # outer wall face of edge 0: wall U, V carried on from the wall top
        assert puv[0] == (wuv[0][0], wuv[3][1]) and puv[1][0] == wuv[1][0], (puv[:4], wuv[:4])
    v, uv, t, n = merge([(wv, wuv, wt, len(wv)), (pv, puv, pt, 0)])
    assert n == len(wv) and len(v) == len(wv) + len(pv)
assert CORNICE_OUT_CM > 0

thin = [(0, 0), (1000, 0), (1000, 40), (0, 40)]   # 40 cm wide: a 25 cm parapet each side won't fit
wv, wuv, _ = extrude_footprint(thin, 0.0, 300.0, 0)
assert parapet(wv, wuv, 4, 300.0, 100.0, False) is None
spike = [(0, 0), (1000, 0), (1000, 1000), (990, 0.0001)]
wv, wuv, _ = extrude_footprint(spike, 0.0, 300.0, 0)
assert parapet(wv, wuv, 4, 300.0, 100.0, False) is None
print("test_building_footprints: OK")
