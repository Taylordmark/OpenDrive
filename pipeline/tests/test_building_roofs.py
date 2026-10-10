"""Track G pitched roofs (pipeline/building_roofs.py, the mirror of
AODBuildingManager's PitchRoof): on rectangles, squares, chamfered and odd
convex footprints, both gable and hip,
  - the roof faces tile the footprint exactly (no overlap = no
    self-intersection, no hole) and all face up past the 0.55 roof cut,
  - roof + end walls are watertight: every edge is shared by two faces
    except the eave line at the wall top, which runs exactly round the
    footprint (where the walls meet it),
  - end walls are vertical and keep the wall's U;
dents and bays included; L-shapes, triangles, octagons, flat-roof buildings
stay flat.
With --tiles, also runs every ODU2 tile under Content/OpenDrive/Masks (or
the ones given) through it: the same checks on every pitched building, the
counts (compare with AODBuildingManager::ProfileRoofTile) and the time.

Run:  python pipeline/tests/test_building_roofs.py [--tiles [file ...]]"""
import math
import sys
import time
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPE))
from build_building_mesh import extrude_footprint   # noqa: E402
from building_roofs import pitch_roof, read_tile    # noqa: E402

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
            res = pitch_roof(verts, uvs, tris, 0, hip=hip)
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


def test_tiles(paths):
    total_t = 0.0
    n_b = n_p = n_tri_before = n_tri_after = 0
    worst = (0.0, None)
    for path in paths:
        t0 = time.perf_counter()
        bs = read_tile(path)
        res = [pitch_roof(v, uv, t, flat) for v, uv, t, _, flat in bs]
        dt = time.perf_counter() - t0
        total_t += dt
        worst = max(worst, (dt, path.name))
        n_b += len(bs)
        for (v, uv, t, _, _), r in zip(bs, res):
            n_tri_before += len(t)
            if r is None:
                n_tri_after += len(t)
                continue
            n_p += 1
            n_tri_after += len(r[2])
            top = max(p[2] for p in v)
            from building_roofs import clean_ring, footprint_ring
            ring = clean_ring(footprint_ring(v, t, top)[0])
            check(ring, *r, top)
        print(f"  {path.name}: {len(bs)} buildings, {sum(r is not None for r in res)} pitched, {dt * 1e3:.1f} ms")
    print(f"tiles {len(paths)}: buildings {n_b}, pitched {n_p} ({100 * n_p / max(n_b, 1):.0f}%), "
          f"tris {n_tri_before} -> {n_tri_after}, python {total_t * 1e3:.0f} ms total, "
          f"worst tile {worst[0] * 1e3:.0f} ms ({worst[1]})")


if __name__ == "__main__":
    test_shapes()
    print("PASS shapes")
    if "--tiles" in sys.argv:
        given = [Path(a) for a in sys.argv[sys.argv.index("--tiles") + 1:]]
        test_tiles(given or sorted((PIPE.parent / "OpenDriveUE/Content/OpenDrive/Masks").glob("buildings_*_tile_*.bin")))
        print("PASS tiles")
