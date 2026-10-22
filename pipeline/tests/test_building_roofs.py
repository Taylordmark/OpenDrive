"""Pitched roofs (pipeline/building_roofs.py, baked by build_building_mesh):
pitch_roof() on rectangles, squares, chamfered and odd convex footprints,
both gable and hip,
  - the roof faces tile the footprint exactly (no overlap = no
    self-intersection, no hole) and all face up past the 0.55 roof cut,
  - roof + end walls are watertight: every edge is shared by two faces
    except the eave line at the wall top, which runs exactly round the
    footprint (where the walls meet it),
  - end walls are vertical and keep the wall's U;
dents and bays included (no eaves here, see test_eaves); L-shapes, triangles, octagons, flat-roof buildings
stay flat there. cover_roof() then takes the L/T/U-shapes (rectangles on
the footprint grid, each roofed, faces up, end walls vertical and facing out,
>= COVER_MIN covered) and still leaves triangles and octagons flat.
test_eaves: a rectangle's roof runs out past the walls (hip all round, gable
along the ridge sides only), meets the wall top at the wall line, and the
fascia / soffit / gable-end box closes it: every open edge lies on the wall
face; roof:height sets the rise, capped at MAX_K.

Run:  python pipeline/tests/test_building_roofs.py"""
import math
import sys
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
from build_building_mesh import extrude_footprint   # noqa: E402
from building_roofs import COVER_MIN, FASCIA_CM, MAX_K, cover_roof, make_roof, pitch_roof   # noqa: E402

TOL = 1e-3   # cm, = building_roofs.EPS_WELD


def weld(points):
    ids, reps = [], []
    for p in points:
        for j, r in enumerate(reps):
            if math.dist(p, r) < TOL:
                ids.append(j)
                break
        else:
            ids.append(len(reps))
            reps.append(p)
    return ids, reps


def on_ring(p, ring):
    n = len(ring)
    for i in range(n):
        a, b = ring[i], ring[(i + 1) % n]
        ex, ey = b[0] - a[0], b[1] - a[1]
        t = min(max(((p[0] - a[0]) * ex + (p[1] - a[1]) * ey) / (ex * ex + ey * ey), 0.0), 1.0)
        if math.hypot(a[0] + ex * t - p[0], a[1] + ey * t - p[1]) < TOL:
            return True
    return False


def shoelace(ring):
    return sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1]
               for i in range(len(ring))) / 2


def check(ring, verts, uvs, tris, n_solid, top):
    """Asserts the roof/end-wall invariants; ring = footprint (cm)."""
    roof = [t for t in tris if all(verts[i][2] >= top - 1e-6 for i in t)]
    roof_faces = [t for t in roof if any(i < n_solid for i in t)]
    ends = [t for t in roof if all(i >= n_solid for i in t)]
    assert len(roof) == len(roof_faces) + len(ends)
    area = 0.0
    for t in roof_faces:
        a, b, c = (verts[i] for i in t)
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        v = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        nz = u[0] * v[1] - u[1] * v[0]
        nl = math.sqrt((u[1] * v[2] - u[2] * v[1]) ** 2 + (u[2] * v[0] - u[0] * v[2]) ** 2 + nz ** 2)
        assert nz > 0 and (nz < 2.0 or nz / nl > 0.55), "roof faces up, inside the roof section"
        area += nz / 2
    assert abs(area - abs(shoelace(ring))) < 1.0 + 1e-6 * abs(shoelace(ring)), \
        f"roof tiles the footprint: {area:.1f} vs {abs(shoelace(ring)):.1f}"
    for t in ends:
        a, b, c = (verts[i] for i in t)
        assert abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) < 1.0, "end walls vertical"
        for i in t:
            assert on_ring(verts[i], ring), "end walls stand on the footprint"
            assert abs(uvs[i][1] * 400.0 - (verts[i][2] - min(v[2] for v in verts))) < 0.5, "wall V continues"
    ids, reps = weld([verts[i] for t in roof for i in t])
    edges = {}
    for k in range(0, len(ids), 3):
        for j in range(3):
            a, b = ids[k + j], ids[k + (j + 1) % 3]
            edges[(min(a, b), max(a, b))] = edges.get((min(a, b), max(a, b)), 0) + 1
    eave = 0.0
    for (a, b), c in edges.items():
        pa, pb = reps[a], reps[b]
        if c == 1:
            assert abs(pa[2] - top) < TOL and abs(pb[2] - top) < TOL, f"open edge above the eave {pa} {pb}"
            assert on_ring(pa, ring) and on_ring(pb, ring) and on_ring(
                ((pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2), ring), "eave edge on the footprint"
            eave += math.dist(pa, pb)
        else:
            assert c == 2, f"edge shared by {c} faces"
    perim = sum(math.dist(ring[i], ring[(i + 1) % len(ring)]) for i in range(len(ring)))
    assert abs(eave - perim) < 0.1, f"eave runs round the footprint: {eave:.2f} vs {perim:.2f}"


def rect(w, d, ang=0.0, ox=1234.5, oy=-987.25, mid=False):
    pts = [(-w / 2, -d / 2), (w / 2, -d / 2), (w / 2, d / 2), (-w / 2, d / 2)]
    if mid:   # collinear OSM nodes along two sides
        pts = [pts[0], (0.0, -d / 2), pts[1], pts[2], (w / 6, d / 2), pts[3]]
    c, s = math.cos(ang), math.sin(ang)
    return [(ox + x * c - y * s, oy + x * s + y * c) for x, y in pts]


SHAPES = {
    "rect 12x8": (rect(1200, 800), True),
    "rect cw": (rect(1200, 800)[::-1], True),
    "rotated rect + collinear nodes": (rect(1500, 900, 0.7, mid=True), True),
    "square (pyramid hip)": (rect(1000, 1000, 0.3), True),
    "long narrow 30x6": (rect(3000, 600, -1.1), True),
    "chamfered corner": ([(0, 0), (1200, 0), (1200, 600), (1000, 800), (0, 800)], True),
    "trapezoid": ([(0, 0), (1400, 0), (1300, 900), (100, 900)], True),
    "skewed quad": ([(0, 0), (1100, 80), (1180, 870), (60, 800)], True),
    "dented rect (porch notch)": ([(0, 0), (1200, 0), (1200, 800), (800, 800), (800, 700), (400, 700),
                                   (400, 800), (0, 800)], True),
    "bay window": ([(0, 0), (500, 0), (550, -60), (650, -60), (700, 0), (1200, 0), (1200, 800), (0, 800)], True),
    "L-shape": ([(0, 0), (1200, 0), (1200, 500), (500, 500), (500, 1000), (0, 1000)], False),
    "triangle": ([(0, 0), (1200, 0), (600, 900)], False),
    "octagon": ([(math.cos(i * math.pi / 4) * 600, math.sin(i * math.pi / 4) * 600) for i in range(8)], False),
    "big 30x25": (rect(3000, 2500), False),
    "shed 2x2": (rect(200, 200), False),
}


def test_shapes():
    for name, (ring, pitched) in SHAPES.items():
        for hip in (False, True):
            verts, uvs, tris = extrude_footprint(ring, 500.0, 640.0, 0)
            res = pitch_roof(verts, uvs, tris, 0, hip=hip, overhang_cm=0)
            assert (res is not None) == pitched, f"{name}: pitched={res is not None}"
            assert pitch_roof(verts, uvs, tris, 1, hip=hip) is None, "flat_roof stays flat"
            if res:
                v, uv, t, n_solid = res
                check(ring, v, uv, t, n_solid, 1140.0)
                rise = max(p[2] for p in v) - 1140.0
                assert 100.0 < rise < 600.0, f"{name}: rise {rise:.0f} cm"
                walls_before = [x for x in tris if not all(verts[i][2] > 1139 for i in x)]
                assert len(t) > len(walls_before), "walls kept"
                print(f"  {name:32s} {'hip' if hip else 'gable':5s} rise {rise:4.0f} cm  "
                      f"{len(t) - len(walls_before)} roof+end tris")


COVER = {
    "L-shape": ([(0, 0), (1200, 0), (1200, 500), (500, 500), (500, 1000), (0, 1000)], 2),
    "U-shape": ([(0, 0), (1500, 0), (1500, 1000), (1100, 1000), (1100, 400), (400, 400), (400, 1000),
                 (0, 1000)], 3),
    "T-shape rotated": ([(x * math.cos(0.4) - y * math.sin(0.4), x * math.sin(0.4) + y * math.cos(0.4))
                         for x, y in [(0, 0), (1600, 0), (1600, 500), (1050, 500), (1050, 1200), (550, 1200),
                                      (550, 500), (0, 500)]], 2),
    "triangle": (SHAPES["triangle"][0], 0),
    "octagon": (SHAPES["octagon"][0], 0),
}


def normal(a, b, c):
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    ln = math.sqrt(sum(x * x for x in n))
    return tuple(x / ln for x in n)


def roof_z(p, tri):
    """Height of roof triangle tri over point p, or -inf if p is outside it."""
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
    u = ((p[0] - ax) * (cy - ay) - (cx - ax) * (p[1] - ay)) / det
    w = ((bx - ax) * (p[1] - ay) - (p[0] - ax) * (by - ay)) / det
    return az + u * (bz - az) + w * (cz - az) if u >= 0 and w >= 0 and u + w <= 1 else -math.inf


def test_cover():
    for name, (ring, min_rects) in COVER.items():
        for hip in (False, True):
            verts, uvs, tris = extrude_footprint(ring, 500.0, 640.0, 0)
            assert pitch_roof(verts, uvs, tris, 0, hip=hip) is None, f"{name}: not a pitch_roof shape"
            res = cover_roof(verts, uvs, tris, hip=hip)
            assert (res is not None) == (min_rects > 0), f"{name}: covered={res is not None}"
            assert make_roof(verts, uvs, tris, hip) == res
            if not res:
                continue
            v, uv, t, n_solid = res
            assert v[:len(verts)] == [tuple(p) for p in verts] and t[:len(tris)] == [tuple(x) for x in tris],                 "walls and flat cap kept"
            new = t[len(tris):]
            faces = [x for x in new if any(i < n_solid for i in x)]
            ends = [x for x in new if all(i >= n_solid for i in x)]
            assert len(faces) + len(ends) == len(new)
            covered = sum(shoelace([v[i] for i in x]) for x in faces)
            assert covered >= COVER_MIN * abs(shoelace(ring)) - 1.0, f"{name}: covers {covered:.0f}"
            for x in faces:
                assert normal(*(v[i] for i in x))[2] > 0.55, f"{name}: roof face up"
            for x in ends:
                n = normal(*(v[i] for i in x))
                assert abs(n[2]) < 1e-6, f"{name}: end wall vertical"
                assert not hip, "hips have no end walls"
                c = [sum(v[i][k] for i in x) / 3 for k in range(3)]
                inner = (c[0] - 5 * n[0], c[1] - 5 * n[1])
                assert any(roof_z(inner, [v[i] for i in f]) > c[2] for f in faces), f"{name}: end wall faces out"
            assert hip or len(ends) >= 2 * min_rects, f"{name}: {len(ends)} gable tris"
            rise = max(p[2] for p in v) - 1140.0
            assert 50.0 < rise <= 500.0, f"{name}: rise {rise:.0f}"
            print(f"  {name:32s} {'hip' if hip else 'gable':5s} rise {rise:4.0f} cm  "
                  f"{len(faces)} roof + {len(ends)} end tris, {covered / abs(shoelace(ring)):.0%} covered")


def test_eaves():
    o, top = 45.0, 1140.0
    for name, ring in (("rect", rect(1200, 800)), ("rect cw rotated", rect(1200, 800, 0.7)[::-1]),
                       ("square", rect(1000, 1000, 0.3))):
        for hip in (False, True):
            verts, uvs, tris = extrude_footprint(ring, 500.0, 640.0, 0)
            n_walls = sum(1 for x in tris if not all(verts[i][2] > top - 1 for i in x))
            v, uv, t, n_solid = pitch_roof(verts, uvs, tris, 0, hip=hip, overhang_cm=o)
            new = t[n_walls:]
            faces = [x for x in new if any(i < n_solid for i in x)]
            for x in faces:
                assert normal(*(v[i] for i in x))[2] > 0.55, f"{name}: roof face up"
            w, d = math.dist(ring[0], ring[1]), math.dist(ring[1], ring[2])
            long_w = max(w, d)
            want = (w + 2 * o) * (d + 2 * o) if hip else long_w * (min(w, d) + 2 * o)
            area = sum(shoelace([v[i] for i in x]) for x in faces)
            assert abs(area - want) < 1.0, f"{name}: roof covers {area:.0f} vs {want:.0f}"
            soffit = [x for x in new if normal(*(v[i] for i in x))[2] < -0.999]
            assert soffit, f"{name}: soffit faces down"
            zs = min(v[i][2] for x in new for i in x)
            assert top - 150.0 < zs < top - FASCIA_CM, f"{name}: eave box bottom {zs - top:.0f}"
            # roof meets the wall top on the eave sides
            for i in range(4):
                a, b = ring[i], ring[(i + 1) % 4]
                if hip or math.dist(a, b) > min(w, d) + 1:
                    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                    z = max(roof_z(mid, [v[j] for j in f]) for f in faces)
                    assert abs(z - top) < 1.0, f"{name}: roof at the wall line {z - top:.2f}"
            # watertight but for the wall face, which the box closes against
            ids, reps = weld([v[i] for x in new for i in x])
            edges = {}
            for k in range(0, len(ids), 3):
                for j in range(3):
                    e = tuple(sorted((ids[k + j], ids[k + (j + 1) % 3])))
                    edges[e] = edges.get(e, 0) + 1
            for (a, b), c in edges.items():
                pa, pb = reps[a], reps[b]
                if c == 1:
                    mid = tuple((pa[k] + pb[k]) / 2 for k in range(3))
                    assert all(on_ring(q, ring) and zs - TOL <= q[2] <= top + 600 for q in (pa, pb, mid)),                         f"{name}: open edge off the wall {pa} {pb}"
                else:
                    assert c == 2, f"{name}: edge shared by {c} faces"
            print(f"  {name:32s} {'hip' if hip else 'gable':5s} eave box {top - zs:.0f} cm, "
                  f"{len(new) - len(faces)} end/box tris")
    verts, uvs, tris = extrude_footprint(rect(1200, 800), 500.0, 640.0, 0)
    for rise, want in ((300.0, 300.0), (2000.0, MAX_K * 400.0)):
        v = pitch_roof(verts, uvs, tris, 0, hip=False, rise_cm=rise)[0]
        assert abs(max(p[2] for p in v) - top - want) < 1e-3, f"roof:height {rise} -> {max(p[2] for p in v) - top}"


if __name__ == "__main__":
    test_shapes()
    print("PASS shapes")
    test_cover()
    print("PASS cover")
    test_eaves()
    print("PASS eaves")
