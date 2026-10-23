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
from build_landmarks import revolve, paint_seed, pick_pois, MAX_POIS   # noqa: E402
from verify_landmarks import paint_of   # noqa: E402


def test_paint_seed():
    for paint in (None, 0, 1, 2, 3, 4):
        assert paint_of(paint_seed(paint)) == paint, paint


def test_pick_pois():
    def row(name, x, road_m=10.0, **kw):
        return {**dict(name=name, kind="tower", h=20.0, osm="n1", road_m=road_m, xy_cm=(x, 0.0),
                       built=False, part=False, notable=True), **kw}
    rows = [row("Built", 0.0, built=True), row("Near built", 5000.0), row("Far off road", 50000.0, road_m=500.0),
            row("Part", 60000.0, part=True), row("Tower", 70000.0), row("Tower", 90000.0), row("Next to tower", 75000.0),
            row("Same name", 200000.0), row("Old Road/5", 300000.0, kind="bridge"), row("Hyatt", 400000.0, kind="hotel"),
            row("Regency Tower", 500000.0, kind="apartments", notable=False)]
    got = [p["name"] for p in pick_pois(rows, [(0.0, 0.0)], {"Same name"})]
    assert got == ["Tower"], got
    many = [row(f"P{i}", i * 20000.0) for i in range(MAX_POIS + 5)]
    out = pick_pois(many, [], set())
    assert len(out) == MAX_POIS and all(p["kind"] == "poi" and p["radius_cm"] > 0 for p in out)


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
    test_pick_pois()
    print("PASS pick_pois")
