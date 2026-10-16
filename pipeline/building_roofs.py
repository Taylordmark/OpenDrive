"""Pitched roofs, baked by build_building_mesh.py into the building tiles
(realism B3, 2026-10-15; until then AODBuildingManager re-pitched them at
runtime from a C++ twin of this file, Realistic only, gated by the
footprint alone). The shape now comes from the pipeline (OSM roof:shape,
else the building category and size, see build_building_mesh.roof_shape):
  1. take the flat roof cap build_building_mesh.extrude_footprint() made (the
     triangles with all three corners at the top), chain its boundary into
     the footprint ring, drop near-collinear points;
  2. pitch_roof(): if the ring is at most MAX_AREA_M2, fills at least
     MIN_FILL of its minimum-area bounding rectangle (OBB; porches and dents
     ok) and the OBB is MIN_SPAN_M..MAX_SPAN_M across and at most
     MAX_LENGTH_M long, build a gable (ridge along the OBB's long axis) or a
     hip roof at PITCH_DEG (rise capped at MAX_RISE_CM), as a height field
     over the real footprint: the footprint is ear-clipped into triangles and
     each triangle clipped to each face's half-planes (convex pieces ->
     fans), so the faces tile the footprint exactly and meet along the
     ridge/hips (crossings computed per line pair, T-junctions filled in, and
     a self-check that keeps the flat cap if a near-degenerate footprint
     still leaves a crack or fold);
  3. otherwise (L-, T-, U-shapes) cover_roof(): up to COVER_MAX_RECTS
     rectangles on the footprint's own OBB grid, each with its own gable or
     hip, over the kept flat cap - cross-gables, the flat cap showing in what
     they leave (at most 1 - COVER_MIN of the footprint);
  4. vertical end walls ("skirts") under every edge a roof rises above (the
     gable ends), with the wall's own UV continued so bricks/siding line up.
Units cm, UE world frame. Faces are emitted with their own vertices so the
per-vertex normals stay flat. Vertices from n_solid on are end walls (no
windows)."""
import math

EPS_Z = 1.0             # cm: "at the roof height"
EPS_DUP = 1.0           # cm: duplicate ring point
COLLINEAR_SIN = 0.02    # |sin| of the turn below which a ring point is dropped
EPS_SNAP = 0.01         # cm: a point this close to a crease line is on it
EPS_WELD = 1e-3         # cm: clip points closer than this are one point; lower skirts are skipped
MAX_AREA_M2 = 600.0
MIN_FILL = 0.85
MIN_SPAN_M, MAX_SPAN_M, MAX_LENGTH_M = 3.0, 20.0, 40.0
HIP_FRACTION = 0.45     # hashed pick when the caller passes hip=None (tests)
PITCH_DEG = (25.0, 35.0)
MAX_RISE_CM = 500.0     # a 20 m span at 35 deg would put a 7 m attic on a bungalow
FACADE_TILE_CM = 400.0
COVER_SNAP_CM = 50.0    # footprint coordinates closer than this are one grid line
COVER_MAX_LINES = 13    # grid lines per axis, else the footprint is too ragged to cover
COVER_CELL_IN = 0.97    # share of a grid cell inside the footprint to count as inside
COVER_MIN = 0.8         # rectangles must cover this share of the footprint
COVER_MAX_RECTS = 4


def _hash(ix, iy):
    """lowbias32 of the building's floored min corner (cm) - order-independent
    and exact in float32, so C++ and Python agree."""
    h = ((ix * 73856093) ^ (iy * 19349663)) & 0xFFFFFFFF
    h ^= h >> 16
    h = (h * 0x7FEB352D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x846CA68B) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def _cross(ax, ay, bx, by):
    return ax * by - ay * bx


def footprint_ring(verts, tris, top):
    """CCW footprint ring [(x, y)] from the flat cap's boundary, or None."""
    cap = [t for t in tris if all(abs(verts[i][2] - top) < EPS_Z for i in t)]
    if not cap:
        return None, cap
    count = {}
    for t in cap:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            key = (min(a, b), max(a, b))
            count[key] = count.get(key, 0) + 1
    nxt = {}
    for t in cap:
        for k in range(3):
            a, b = t[k], t[(k + 1) % 3]
            if count[(min(a, b), max(a, b))] == 1:
                if a in nxt:
                    return None, cap
                nxt[a] = b
    if len(nxt) < 3:
        return None, cap
    start = min(nxt)
    loop, cur = [start], nxt[start]
    while cur != start:
        if cur not in nxt or len(loop) > len(nxt):
            return None, cap
        loop.append(cur)
        cur = nxt[cur]
    if len(loop) != len(nxt):
        return None, cap
    ring = [(verts[i][0], verts[i][1]) for i in loop]
    area2 = sum(_cross(*ring[i], *ring[(i + 1) % len(ring)]) for i in range(len(ring)))
    return (ring if area2 > 0 else ring[::-1]), cap


def clean_ring(ring):
    changed = True
    while changed and len(ring) >= 3:
        changed = False
        n = len(ring)
        for i in range(n):
            p, q, r = ring[i - 1], ring[i], ring[(i + 1) % n]
            ax, ay, bx, by = q[0] - p[0], q[1] - p[1], r[0] - q[0], r[1] - q[1]
            la, lb = math.hypot(ax, ay), math.hypot(bx, by)
            if la < EPS_DUP or (abs(_cross(ax, ay, bx, by)) <= COLLINEAR_SIN * la * lb and ax * bx + ay * by > 0):
                del ring[i]
                changed = True
                break
    return ring


def triangulate(ring):
    """Ear clipping of a CCW ring -> [(i, j, k)] CCW, or None if it finds no
    ear (self-touching ring). Clips the fattest ear first (area over the sum
    of squared sides): a sliver ear along a footprint edge would put two
    nearly coincident edges through one point, and the clip lines can't
    tell them apart."""
    idx = list(range(len(ring)))
    out = []
    while len(idx) > 3:
        m = len(idx)
        best = None
        for k in range(m):
            a, b, c = idx[k - 1], idx[k], idx[(k + 1) % m]
            pa, pb, pc = ring[a], ring[b], ring[c]
            if _cross(pb[0] - pa[0], pb[1] - pa[1], pc[0] - pb[0], pc[1] - pb[1]) <= 0:
                continue
            if any(_cross(pb[0] - pa[0], pb[1] - pa[1], q[0] - pa[0], q[1] - pa[1]) >= 0
                   and _cross(pc[0] - pb[0], pc[1] - pb[1], q[0] - pb[0], q[1] - pb[1]) >= 0
                   and _cross(pa[0] - pc[0], pa[1] - pc[1], q[0] - pc[0], q[1] - pc[1]) >= 0
                   for q in (ring[j] for j in idx if j not in (a, b, c))):
                continue
            fat = _cross(pb[0] - pa[0], pb[1] - pa[1], pc[0] - pa[0], pc[1] - pa[1]) / (
                math.dist(pa, pb) ** 2 + math.dist(pb, pc) ** 2 + math.dist(pc, pa) ** 2)
            if best is None or fat > best[0]:
                best = (fat, k)
        if best is None:
            return None
        k = best[1]
        out.append((idx[k - 1], idx[k], idx[(k + 1) % m]))
        del idx[k]
    out.append(tuple(idx))
    return out


def obb(ring):
    """(cx, cy, dLx, dLy, HL, HS): minimum-area rectangle over the edge
    directions, l = long axis, s = (-dLy, dLx)."""
    best = None
    n = len(ring)
    for i in range(n):
        ex, ey = ring[(i + 1) % n][0] - ring[i][0], ring[(i + 1) % n][1] - ring[i][1]
        le = math.hypot(ex, ey)
        dx, dy = ex / le, ey / le
        ls = [x * dx + y * dy for x, y in ring]
        ss = [-x * dy + y * dx for x, y in ring]
        area = (max(ls) - min(ls)) * (max(ss) - min(ss))
        if best is None or area < best[0]:
            best = (area, dx, dy)
    _, dx, dy = best
    ls = [x * dx + y * dy for x, y in ring]
    ss = [-x * dy + y * dx for x, y in ring]
    if max(ls) - min(ls) < max(ss) - min(ss):
        dx, dy = -dy, dx
        ls = [x * dx + y * dy for x, y in ring]
        ss = [-x * dy + y * dx for x, y in ring]
    lm, sm = (max(ls) + min(ls)) / 2, (max(ss) + min(ss)) / 2
    return (lm * dx - sm * dy, lm * dy + sm * dx, dx, dy,
            (max(ls) - min(ls)) / 2, (max(ss) - min(ss)) / 2)


def _clip(poly, plane, meet):
    """Sutherland-Hodgman of [(l, s, line)] against plane (a, b, c) =
    a*l + b*s <= c; line = the line of the edge arriving at the point, ("r",
    i, j) for ring vertices i < j or ("c", a, b, c) for a crease. Crossings
    come from meet(line, line), which is exact per pair, so two ears cutting
    the same crease through a shared diagonal make the identical point.
    Points within EPS_SNAP of the plane count as on it, so a crease passing
    a footprint corner goes through it instead of cutting slivers off it."""
    a, b, c = plane
    cl = ("c", a, b, c)
    snap = EPS_SNAP * math.hypot(a, b)

    def side(pt):
        d = a * pt[0] + b * pt[1] - c
        return 0.0 if abs(d) < snap else d

    out = []
    for i in range(len(poly)):
        p, q = poly[i - 1], poly[i]
        dp, dq = side(p), side(q)
        if dq <= 0:
            if dp > 0 and dq == 0:   # q on the plane is the crossing
                q = (q[0], q[1], cl)
            elif dp > 0:
                out.append((*meet(q[2], cl), cl))
            out.append(q)
        elif dp < 0:
            out.append((*meet(q[2], cl), q[2]))
    return out


def _top_edges(verts, uvs, tris, top):
    """Wall top edges (a wall triangle with exactly two corners at the top),
    to continue the wall U onto the end walls."""
    out = []
    for t in tris:
        at_top = [i for i in t if abs(verts[i][2] - top) < EPS_Z]
        if len(at_top) == 2:
            (a, b) = at_top
            out.append((verts[a][0], verts[a][1], verts[b][0], verts[b][1], uvs[a][0], uvs[b][0]))
    return out


def _end_walls(out_v, out_uv, out_t, skirts, top_edges, top, base, keep_order=False):
    """Vertical end walls [(p0, p1, h0, h1)] from the eave at `top` up to the
    roof, U continued from the nearest wall top edge. keep_order: p0 -> p1
    already runs counter-clockwise round the roof (outward face)."""
    for p0, p1, h0, h1 in skirts:
        if max(h0, h1) < EPS_WELD or math.dist(p0, p1) < EPS_WELD:
            continue
        mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
        best, u0, u1 = None, 0.0, math.dist(p0, p1) / FACADE_TILE_CM
        for ax, ay, bx, by, ua, ub in top_edges:
            ex, ey = bx - ax, by - ay
            ll = ex * ex + ey * ey
            if ll <= 0:
                continue
            t = min(max(((mx - ax) * ex + (my - ay) * ey) / ll, 0.0), 1.0)
            dist = math.hypot(ax + ex * t - mx, ay + ey * t - my)
            if best is None or dist < best:
                best = dist
                u0 = ua + (ub - ua) * ((p0[0] - ax) * ex + (p0[1] - ay) * ey) / ll
                u1 = ua + (ub - ua) * ((p1[0] - ax) * ex + (p1[1] - ay) * ey) / ll
        if u1 < u0 and not keep_order:   # wind like the wall below (U increasing along the edge)
            p0, p1, h0, h1, u0, u1 = p1, p0, h1, h0, u1, u0
        corners = [(p0, top, u0), (p1, top, u1)]
        if h1 >= EPS_WELD:
            corners.append((p1, top + h1, u1))
        if h0 >= EPS_WELD:
            corners.append((p0, top + h0, u0))
        first = len(out_v)
        for (x, y), z, u in corners:
            out_v.append((x, y, z))
            out_uv.append((u, (z - base) / FACADE_TILE_CM))
        for j in range(1, len(corners) - 1):
            out_t.append((first, first + j, first + j + 1))


def pitch_roof(verts, uvs, tris, flat_roof, hip=None):
    """(verts, uvs, tris, n_solid) with the flat cap replaced by a pitched
    roof, or None to keep the flat cap. Vertices from n_solid on are the end
    walls (no windows). hip=True/False overrides the hashed pick (tests)."""
    if flat_roof:
        return None
    top = max(v[2] for v in verts)
    base = min(v[2] for v in verts)
    ring, cap = footprint_ring(verts, tris, top)
    if ring is None:
        return None
    ring = clean_ring(ring)
    n = len(ring)
    if n < 3:
        return None
    area = sum(_cross(*ring[i], *ring[(i + 1) % n]) for i in range(n)) / 2
    if area > MAX_AREA_M2 * 1e4:
        return None
    cx, cy, dx, dy, hl, hs = obb(ring)
    if (area < MIN_FILL * 4 * hl * hs or 2 * hs < MIN_SPAN_M * 100
            or 2 * hs > MAX_SPAN_M * 100 or 2 * hl > MAX_LENGTH_M * 100):
        return None
    ears = triangulate(ring)
    if ears is None:
        return None

    h = _hash(math.floor(min(v[0] for v in verts)), math.floor(min(v[1] for v in verts)))
    if hip is None:
        hip = (h & 0xFFFF) / 65536.0 < HIP_FRACTION
    pitch = math.radians(PITCH_DEG[0] + (PITCH_DEG[1] - PITCH_DEG[0]) * (h >> 16) / 65536.0)
    k = min(math.tan(pitch), MAX_RISE_CM / hs)
    slope_cm = FACADE_TILE_CM / math.hypot(1.0, k)
    d = hl - hs
    # one height field for every face, so a point on a crease gets the same
    # height from both sides; faces = (half-planes a*l + b*s <= c, long side)
    if hip:
        height = lambda l, s: max(k * min(hs - abs(s), hl - abs(l)), 0.0)
        faces = [([(0, -1, 0), (1, -1, d), (-1, -1, d)], True), ([(0, 1, 0), (1, 1, d), (-1, 1, d)], True),
                 ([(-1, 1, -d), (-1, -1, -d)], False), ([(1, 1, -d), (1, -1, -d)], False)]
    else:
        height = lambda l, s: max(k * (hs - abs(s)), 0.0)
        faces = [([(0, -1, 0)], True), ([(0, 1, 0)], True)]

    # walls kept, cap dropped (its vertices are only used by the cap)
    cap_set = set(map(tuple, cap))
    keep_tris = [t for t in tris if tuple(t) not in cap_set]
    used = sorted({i for t in keep_tris for i in t})
    remap = {old: new for new, old in enumerate(used)}
    out_v = [tuple(verts[i]) for i in used]
    out_uv = [tuple(uvs[i]) for i in used]
    out_t = [tuple(remap[i] for i in t) for t in keep_tris]

    top_edges = _top_edges(verts, uvs, keep_tris, top)

    def world(l, s):
        return cx + l * dx - s * dy, cy + l * dy + s * dx

    local = [((x - cx) * dx + (y - cy) * dy, -(x - cx) * dy + (y - cy) * dx) for x, y in ring]

    def implicit(line):   # (A, B, C): A*l + B*s = C
        if line[0] == "c":
            return line[1:]
        (li, si), (lj, sj) = local[line[1]], local[line[2]]
        return si - sj, lj - li, (si - sj) * li + (lj - li) * si

    def meet(m, k):
        (a1, b1, c1), (a2, b2, c2) = (implicit(x) for x in sorted((m, k)))
        det = a1 * b2 - a2 * b1
        return (c1 * b2 - c2 * b1) / det, (a1 * c2 - a2 * c1) / det

    def footprint(line):
        return line[0] == "r" and (line[2] - line[1] == 1 or (line[1] == 0 and line[2] == n - 1))

    pieces = []
    for (a, b, c), (planes, long_side) in ((e, f) for e in ears for f in faces):
        poly = [(*local[i], ("r", min(i, j), max(i, j))) for j, i in ((c, a), (a, b), (b, c))]
        for plane in planes:
            poly = _clip(poly, plane, meet)
            if len(poly) < 3:
                break
        # a clip through a corner repeats it: drop the zero-length edge
        poly = [q for j, q in enumerate(poly) if math.dist(q[:2], poly[j - 1][:2]) >= EPS_WELD]
        if len(poly) >= 3:
            pieces.append((poly, long_side))

    # a crease end on a diagonal can be a corner on one side of
    # it only: put it on the other side's edge too (no T-junction crack)
    # ponytail: O(pieces * edges * points), a roof has tens of each
    points = {q[:2] for poly, _ in pieces for q in poly}
    fixed_pieces = []
    for poly, long_side in pieces:
        fixed = []
        for j in range(len(poly)):
            p, q = poly[j - 1], poly[j]
            ex, ey = q[0] - p[0], q[1] - p[1]
            ll = ex * ex + ey * ey
            on = []
            for r in points:
                t = ((r[0] - p[0]) * ex + (r[1] - p[1]) * ey) / ll
                if (0 < t < 1 and abs(_cross(ex, ey, r[0] - p[0], r[1] - p[1])) < EPS_WELD * math.sqrt(ll)
                        and math.dist(r, p[:2]) >= EPS_WELD and math.dist(r, q[:2]) >= EPS_WELD):
                    on.append((t, r))
            fixed += [(*r, q[2]) for _, r in sorted(on)] + [q]
        if len(fixed) > len(poly):   # fan from the centre, the corners now have collinear neighbours
            cl = sum(q[0] for q in poly) / len(poly)
            cs = sum(q[1] for q in poly) / len(poly)
            poly = [(cl, cs, None)] + fixed
            fan = [(0, j, j % (len(poly) - 1) + 1) for j in range(1, len(poly))]
        else:
            fan = [(0, j, j + 1) for j in range(1, len(poly) - 1)]
        fixed_pieces.append((poly, fan, long_side, fixed))

    # near-degenerate footprints (a crease grazing a corner at a shallow
    # angle, float32 corners 1 cm apart far from the origin) can still leave
    # a crack or a folded sliver: check the pieces tile the footprint edge to
    # edge (ends within EPS_WELD, as welded), else keep the flat cap
    # ponytail: O(edges^2), a roof has tens
    edges = [(p, q) for *_, f in fixed_pieces for p, q in zip(f[-1:] + f[:-1], f)]
    tiled = 0.0
    for poly, fan, _, f in fixed_pieces:
        for p, q in zip(f[-1:] + f[:-1], f):
            if not footprint(q[2]) and not any(math.dist(q[:2], p2[:2]) < EPS_WELD and
                                               math.dist(p[:2], q2[:2]) < EPS_WELD for p2, q2 in edges):
                return None
            tiled += _cross(p[0], p[1], q[0], q[1]) / 2
        for i0, i1, i2 in fan:
            p0, p1, p2 = poly[i0], poly[i1], poly[i2]
            if _cross(p1[0] - p0[0], p1[1] - p0[1], p2[0] - p0[0], p2[1] - p0[1]) < -1e-6:
                return None
    if abs(tiled - area) > 1.0 + 1e-6 * area:
        return None

    skirts = []
    for poly, fan, long_side, _ in fixed_pieces:
        first = len(out_v)
        for l, s, _ in poly:
            x, y = world(l, s)
            out_v.append((x, y, top + height(l, s)))
            # shingle rows along the eave, V down the slope
            out_uv.append((l / FACADE_TILE_CM, (abs(s) - hs) / slope_cm) if long_side else
                          (s / FACADE_TILE_CM, (abs(l) - hl) / slope_cm))
        for i0, i1, i2 in fan:
            p0, p1, p2 = poly[i0], poly[i1], poly[i2]
            if abs(_cross(p1[0] - p0[0], p1[1] - p0[1], p2[0] - p0[0], p2[1] - p0[1])) > 1e-6:
                out_t.append((first + i0, first + i1, first + i2))
        ring_part = poly[1:] if poly[0][2] is None else poly
        for j in range(len(ring_part)):
            p, q = ring_part[j - 1], ring_part[j]
            if footprint(q[2]):
                skirts.append((world(p[0], p[1]), world(q[0], q[1]), height(p[0], p[1]), height(q[0], q[1])))

    n_solid = len(out_v)
    _end_walls(out_v, out_uv, out_t, skirts, top_edges, top, base)
    return out_v, out_uv, out_t, n_solid


def _cluster(vals):
    """Sorted values merged within COVER_SNAP_CM -> the clusters' means."""
    groups = []
    for v in sorted(vals):
        if groups and v - groups[-1][-1] < COVER_SNAP_CM:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def rect_cover(local):
    """Up to COVER_MAX_RECTS disjoint rectangles [(l0, l1, s0, s1)] on the
    grid of a footprint's own (OBB-frame) coordinates, largest first, each
    MIN_SPAN_M..MAX_SPAN_M across and at most MAX_LENGTH_M long, covering at
    least COVER_MIN of it; None if they can't."""
    import numpy as np
    import shapely
    ls, ss = np.array(_cluster(p[0] for p in local)), np.array(_cluster(p[1] for p in local))
    if not (2 <= len(ls) <= COVER_MAX_LINES and 2 <= len(ss) <= COVER_MAX_LINES):
        return None
    poly = shapely.Polygon(local)
    if not poly.is_valid:
        return None
    L0, S0 = np.meshgrid(ls[:-1], ss[:-1], indexing="ij")
    L1, S1 = np.meshgrid(ls[1:], ss[1:], indexing="ij")
    cells = shapely.box(L0, S0, L1, S1)
    free = shapely.area(shapely.intersection(cells, poly)) >= COVER_CELL_IN * shapely.area(cells)
    ia, ib = np.triu_indices(len(ls), 1)   # every grid-aligned rectangle: l lines ia < ib, s lines ja < jb
    ja, jb = np.triu_indices(len(ss), 1)
    ia, ib, ja, jb = ia[:, None], ib[:, None], ja[None, :], jb[None, :]
    wl, ws = ls[ib] - ls[ia], ss[jb] - ss[ja]
    span, length = np.minimum(wl, ws), np.maximum(wl, ws)
    fits = (span >= MIN_SPAN_M * 100) & (span <= MAX_SPAN_M * 100) & (length <= MAX_LENGTH_M * 100)
    rects = []
    for _ in range(COVER_MAX_RECTS):
        pre = np.pad(free.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
        full = pre[ib, jb] - pre[ia, jb] - pre[ib, ja] + pre[ia, ja] == (ib - ia) * (jb - ja)
        area = np.where(full & fits, wl * ws, 0.0)
        k = np.unravel_index(np.argmax(area), area.shape)
        if area[k] <= 0:
            break
        a, b, c, d = ia[k[0], 0], ib[k[0], 0], ja[0, k[1]], jb[0, k[1]]
        free[a:b, c:d] = False
        rects.append((ls[a], ls[b], ss[c], ss[d]))
    covered = sum((l1 - l0) * (s1 - s0) for l0, l1, s0, s1 in rects)
    return rects if rects and covered >= COVER_MIN * poly.area else None


def cover_roof(verts, uvs, tris, hip=None):
    """(verts, uvs, tris, n_solid) with a gable or hip roof on each of the
    footprint's rect_cover() rectangles, over the kept flat cap; or None."""
    top = max(v[2] for v in verts)
    base = min(v[2] for v in verts)
    ring, _ = footprint_ring(verts, tris, top)
    if ring is None:
        return None
    ring = clean_ring(ring)
    if len(ring) < 4:
        return None
    area = sum(_cross(*ring[i], *ring[(i + 1) % len(ring)]) for i in range(len(ring))) / 2
    if area > MAX_AREA_M2 * 1e4:
        return None
    cx, cy, dx, dy, _, _ = obb(ring)
    local = [((x - cx) * dx + (y - cy) * dy, -(x - cx) * dy + (y - cy) * dx) for x, y in ring]
    rects = rect_cover(local)
    if rects is None:
        return None
    h = _hash(math.floor(min(v[0] for v in verts)), math.floor(min(v[1] for v in verts)))
    if hip is None:
        hip = (h & 0xFFFF) / 65536.0 < HIP_FRACTION
    tan_pitch = math.tan(math.radians(PITCH_DEG[0] + (PITCH_DEG[1] - PITCH_DEG[0]) * (h >> 16) / 65536.0))

    out_v, out_uv, out_t = [tuple(v) for v in verts], [tuple(uv) for uv in uvs], [tuple(t) for t in tris]
    gables = []
    for l0, l1, s0, s1 in rects:
        ml, ms = (l0 + l1) / 2, (s0 + s1) / 2
        along_l = l1 - l0 >= s1 - s0
        a, b = ((l1 - l0) / 2, (s1 - s0) / 2) if along_l else ((s1 - s0) / 2, (l1 - l0) / 2)
        k = min(tan_pitch, MAX_RISE_CM / b)
        slope_cm = FACADE_TILE_CM / math.hypot(1.0, k)
        r = max(a - b, 0.0) if hip else a   # ridge half length

        def world(u, w):   # rect frame (u along the ridge) -> local -> world, a rotation (keeps CCW)
            l, s = (ml + u, ms + w) if along_l else (ml - w, ms + u)
            return cx + l * dx - s * dy, cy + l * dy + s * dx

        def z(u, w):
            return top + k * (min(b - abs(w), a - abs(u)) if hip else b - abs(w))

        # faces, counter-clockwise from above: the two long slopes, then the hip ends
        faces = [([(-a, -b), (a, -b), (r, 0.0), (-r, 0.0)], True), ([(a, b), (-a, b), (-r, 0.0), (r, 0.0)], True)]
        if hip:
            faces += [([(a, -b), (a, b), (r, 0.0)], False), ([(-a, b), (-a, -b), (-r, 0.0)], False)]
        for pts, long_side in faces:
            pts = [q for j, q in enumerate(pts) if math.dist(q, pts[j - 1]) >= EPS_WELD]
            first = len(out_v)
            for u, w in pts:
                out_v.append((*world(u, w), z(u, w)))
                out_uv.append((u / FACADE_TILE_CM, (abs(w) - b) / slope_cm) if long_side else
                              (w / FACADE_TILE_CM, (abs(u) - a) / slope_cm))
            out_t += [(first, first + j, first + j + 1) for j in range(1, len(pts) - 1)]
        if not hip:   # gable ends, counter-clockwise round the rect
            gables += [(world(-a, b), world(-a, -b), k * b), (world(a, -b), world(a, b), k * b)]
    n_solid = len(out_v)
    top_edges = _top_edges(verts, uvs, tris, top)
    for p0, p1, rise in gables:   # a triangle: eave corners + the ridge end over their middle
        mid = ((p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2)
        _end_walls(out_v, out_uv, out_t, [(p0, mid, 0.0, rise), (mid, p1, rise, 0.0)], top_edges, top, base,
                   keep_order=True)
    return out_v, out_uv, out_t, n_solid


def make_roof(verts, uvs, tris, hip):
    """pitch_roof(), else cover_roof() for footprints it can't take."""
    return pitch_roof(verts, uvs, tris, 0, hip) or cover_roof(verts, uvs, tris, hip)


def read_tile(path):
    """Building tile -> [dict(verts, uvs, tris, wall_set, flat_roof, roof_set,
    roof_rgb, facade, n_solid)] (ODU4; see export_building_mesh_bin.py).
    path may be the tile's bytes (the blob inside a landmark tile)."""
    import struct
    data = path if isinstance(path, bytes) else open(path, "rb").read()
    assert data[:4] == b"ODU4", f"{path}: magic {data[:4]!r}"
    off = 4 + 16
    (count,) = struct.unpack_from("<I", data, off)
    off += 4
    out = []
    for _ in range(count):
        vc, tc, wall, flat, roof_set, r, g, b, facade, n_solid = struct.unpack_from("<IIBBBBBBBI", data, off)
        off += 19
        v = struct.unpack_from(f"<{vc * 3}f", data, off); off += vc * 12
        uv = struct.unpack_from(f"<{vc * 2}f", data, off); off += vc * 8
        t = struct.unpack_from(f"<{tc * 3}I", data, off); off += tc * 12
        out.append(dict(verts=[v[i:i + 3] for i in range(0, len(v), 3)], uvs=[uv[i:i + 2] for i in range(0, len(uv), 2)],
                        tris=[t[i:i + 3] for i in range(0, len(t), 3)], wall_set=wall, flat_roof=flat,
                        roof_set=roof_set, roof_rgb=(r, g, b), facade=facade, n_solid=n_solid))
    assert off == len(data), f"{path}: {len(data) - off} trailing/short bytes"
    return out
