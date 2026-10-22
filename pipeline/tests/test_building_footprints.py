"""Footprint cleanup and mesh merge (pipeline/build_building_mesh.py, 2026-10-21):
  - merge() puts every piece's windowed vertices (below its n_solid) first,
    keeps each triangle on the same points, and n_solid covers exactly them;
  - simplify_ring drops jitter within tolerance but never a pinned vertex;
  - square_ring squares a skewed rectangle and an L-shape (corners to
    SQUARE_DONE, area within SQUARE_MAX_AREA), never moves a pinned vertex,
    and leaves non-rectilinear rings (triangle, octagon) and corners off
    90 deg by more than SQUARE_DEG alone;
  - extrude_footprint with a bay width gives every wall a whole number of
    bays, running on round the building, and a short jog the wall between bays.

Run:  python pipeline/tests/test_building_footprints.py"""
import math
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
from build_building_mesh import (FACADE_TILE_M, SQUARE_DONE, SQUARE_MAX_AREA, extrude_footprint,   # noqa: E402
                                 merge, simplify_ring, square_ring)
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
print("test_building_footprints: OK")
