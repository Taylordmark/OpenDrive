"""Landmark recipes (pipeline/build_landmarks.py):
  - paint_seed() picks a seed byte the material's paint formula maps back
    to the asked-for paint (checks/verify_landmarks.py paint_of), for every
    paint and for unpainted;
  - revolve() side faces point away from the axis, the top cap faces up
    (past the runtime's 0.55 roof cut), an overhang's underside faces down,
    and no triangle is degenerate, square (seg 4) and round alike.

Run:  python pipeline/tests/test_landmarks.py"""
import math
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
sys.path.insert(0, str(PIPE / "checks"))
from build_landmarks import revolve, paint_seed   # noqa: E402
from verify_landmarks import paint_of   # noqa: E402


def test_paint_seed():
    for paint in (None, 0, 1, 2, 3, 4):
        assert paint_of(paint_seed(paint)) == paint, paint


def test_revolve():
    # shaft, overhang out (underside), gallery top in, lantern, cone to the apex
    profile = [(0, 2.0), (10, 1.8), (10, 2.5), (10.4, 2.5), (10.4, 1.2), (12, 1.2)]
    for seg in (4, 12):
        verts, _, tris = revolve(profile, seg, 500.0, -300.0, 0.6, 0.8, 1000.0)
        up = down = 0
        for t in tris:
            a, b, c = (verts[i] for i in t)
            e1 = [b[k] - a[k] for k in range(3)]
            e2 = [c[k] - a[k] for k in range(3)]
            n = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
            ln = math.hypot(*n)
            assert ln > 1e-3, f"seg {seg}: degenerate triangle {t}"
            n = [x / ln for x in n]
            mx, my = (a[0] + b[0] + c[0]) / 3 - 500.0, (a[1] + b[1] + c[1]) / 3 + 300.0
            if abs(n[2]) < 0.55:
                assert n[0] * mx + n[1] * my > 0, f"seg {seg}: side face points inward"
            elif n[2] > 0:
                up += 1
            else:
                down += 1
        assert up > 0 and down > 0, f"seg {seg}: {up} up, {down} down faces"
        assert max(v[2] for v in verts) == 1000.0 + 1200.0


if __name__ == "__main__":
    test_paint_seed()
    print("PASS paint_seed")
    test_revolve()
    print("PASS revolve")
