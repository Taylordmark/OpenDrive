"""build_building_mesh.extrude_footprint flat cap (realism B5): on concave
footprints (L, U, comb, either winding) every cap triangle faces up and the
cap covers exactly the footprint area - the old centroid fan poked outside.

Run:  python pipeline/tests/test_flat_roof_cap.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_building_mesh import extrude_footprint   # noqa: E402


def area2(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
               for i in range(len(pts)))


SHAPES = {
    "L": [(0, 0), (1200, 0), (1200, 500), (500, 500), (500, 1000), (0, 1000)],
    "U": [(0, 0), (900, 0), (900, 900), (600, 900), (600, 300), (300, 300), (300, 900), (0, 900)],
    "comb": [(0, 0), (1000, 0), (1000, 800), (800, 800), (800, 200), (600, 200), (600, 800),
             (400, 800), (400, 200), (200, 200), (200, 800), (0, 800)],
    "collinear": [(0, 0), (500, 0), (1000, 0), (1000, 600), (0, 600)],
}
for name, ring in SHAPES.items():
    for r in (ring, ring[::-1]):
        r = [(x + 123456.7, y - 98765.4) for x, y in r]
        verts, uvs, tris = extrude_footprint(r, 0.0, 640.0, 0)
        cap = [t for t in tris if all(verts[i][2] == 640.0 for i in t)]
        a = [area2([verts[i] for i in t]) for t in cap]
        assert all(x > 0 for x in a), (name, a)
        assert abs(sum(a) - abs(area2(r))) < 1e-3 * abs(area2(r)), (name, sum(a), area2(r))
print("ok")
