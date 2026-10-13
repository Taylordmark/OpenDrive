"""Realistic car meshes (PLAN-VISUALS Track B, models pass), built headless
in Blender from the same seven presets as pipeline/make_car_meshes.py.

    blender.exe -b --factory-startup --python pipeline/make_car_meshes_real.py -- [Car ...] [--preview]

Run from the repo root (or anywhere: paths are relative to this file). With
no car names all seven are built. --preview also renders 3/4 front and rear
PNGs into data/cars/preview_real/ (offline asset renders, no game).

Our own original, badge-free designs: every body is the preset's loft
(profile / plan / beltline / centerline, recorded straight out of
make_car_meshes.py so proportions can't drift) turned into a subdivision
cage, so nothing here is traced from a real car.

Body, per car:
  cage      one closed quad loft (a 16/18-point section: floor, rocker,
            flank, widest point, beltline, hood/roof edge [, crest] and a
            crowned top) with Coons-patch end caps, wheel arches lifted onto
            a circle round each axle exactly like the stylized hull. Character
            lines are edge creases (S0 sill/arch lip, beltline, hood edge).
  subdiv    Catmull-Clark, then Exact booleans while the shell is still a
            closed manifold: headlamp and tail-lamp housings, the grille
            recess, side intakes and the fuel-door groove. The cutters'
            inner faces become the housing walls / reflectors.
  panels    every cage face carries a panel id (bumpers, hood, trunk, doors,
            fenders, shell, each window). Edges between panels are split,
            each side pulled back 0.25 cm and given a 1.2 cm turned-in lip:
            real door / hood / trunk / bumper gaps. Windows are inset into
            a rubber seal and pushed in 0.6 cm.
  parts     lamp internals (projectors, LED strips, tail / reverse split),
            mirrors, wings (airfoil blade), exhausts, scoops, fins, splitter,
            diffuser, wheel-arch liners, a dark interior (tub, dash, seats,
            steering wheel) and the pickup bed, all bevelled.

Wheels: one mesh per car (SM_CarWheel_<Car>_real): tyre with a rounded
profile, sidewall and real tread grooves (slick on the LMP), a modelled
alloy (a different spoke design per car) on BOTH faces - the pawn mounts the
same unmirrored mesh on left and right, so either face can be the outboard
one - and a brake disc on the centre plane. The caliper must not spin, so it
is its own mesh (SM_CarCaliper_<Car>_real) that the pawn places without the
spin. Wheels are authored at REF_WHEEL_RADIUS_CM like the stylized one.

Contract (unchanged from the stylized set, see make_car_meshes.py): pivot at
the wheelbase centre at axle height z=0, cm, +x forward; axle x / half-track
/ wheel radius are the presets' (= unreal/import_car_meshes.py) values.

Interface: material slots are the same categories (body/plastic/glass/
chrome/tire/rim/lights) named by the Blender material, UV0 points every face
at its T_CarPalette2 cell (lamps are told apart by cell, see
unreal/apply_car_lights_material.py), UV1 is a non-overlapping unwrap.

Tiers: <name>_real_ultra is the full mesh, _real_high and _real (Standard)
are Decimate-collapsed from it (UV0 is re-snapped to the palette cells
afterwards: collapse interpolates UVs across cell borders).

Outputs (data/cars/real/, gitignored like the rest of data/):
    fbx/SM_Car_<Car>_real{,_high,_ultra}.fbx
    fbx/SM_CarWheel_<Car>_real{,_high,_ultra}.fbx, fbx/SM_CarCaliper_<Car>_real.fbx
    real_manifest.json   (triangle counts, axles, wheel shape params)
    LICENSE.txt
"""

import json
import math
import sys
import time
import types
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cars" / "real"
FBX = OUT / "fbx"
PREVIEW = ROOT / "data" / "cars" / "preview_real"

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
DO_PREVIEW = "--preview" in ARGS
CARS_ARG = [a for a in ARGS if not a.startswith("--")]

# --------------------------------------------------------- the stylized presets
# make_car_meshes.py imports PIL for its palette writer, which Blender's
# Python lacks; nothing we call needs it.
sys.modules.setdefault("PIL", types.SimpleNamespace(Image=None))
sys.path.insert(0, str(ROOT / "pipeline"))
import make_car_meshes as mcm  # noqa: E402

mcm.Mesh.save = lambda self, name: len(self.tris)   # record only, never rewrite the stylized OBJs
REC = []
_depth = [0]


def _recorder(name):
    orig = getattr(mcm, name)

    def wrapped(*a, **k):
        top = _depth[0] == 0
        _depth[0] += 1
        try:
            out = orig(*a, **k)
        finally:
            _depth[0] -= 1
        if top:
            REC.append((name, a[1:], k, out))   # a[0] is the stylized Mesh
        return out
    setattr(mcm, name, wrapped)


for _n in ("build_hull", "cabin_blocker", "mirrors", "wing", "exhaust_tips", "side_patch",
           "build_pickup_bed", "box", "vpanel"):
    _recorder(_n)

PRESETS = {"Muscle": mcm.build_muscle, "JDM": mcm.build_jdm, "Super": mcm.build_super,
           "Pickup": mcm.build_pickup, "Rally": mcm.build_rally, "Concept": mcm.build_concept,
           "LMP": mcm.build_lmp}

PAL = mcm.PALETTE
N_CELL = mcm.N_CELL
GLASS, PLASTIC, TIRE, RIM = mcm.GLASS, mcm.PLASTIC, mcm.TIRE, mcm.RIM
HEADLIGHT, TAILLIGHT, ACCENT, CHROME, REVERSE = mcm.HEADLIGHT, mcm.TAILLIGHT, mcm.ACCENT, mcm.CHROME, mcm.REVERSE
CAT_OF = mcm.CATEGORY_OF
CATS = mcm.CATEGORY_ORDER
R_REF = mcm.REF_WHEEL_RADIUS_CM

# --------------------------------------------------------------- design knobs
CAGE_SPACING = 7.0       # cm between loft stations in the cage
SUBDIV = 2               # Catmull-Clark levels on the body cage
GAP = 0.5                # panel gap width, cm
LIP = 1.2                # turned-in panel edge depth, cm
TIER_RATIO = {"_ultra": 1.0, "_high": 0.5, "": 0.22}   # Decimate ratio per tier suffix
CREASE = {0: 1.0, 1: 0.15, 2: 0.35, 3: 0.75, 4: 0.6, 5: 0.6}   # section point -> edge crease

# panel ids (face attribute "panel"); 0 = never split
P_NONE, P_SHELL, P_HOOD, P_TRUNK, P_BUMF, P_BUMR = 0, 1, 2, 3, 4, 5
P_FLOOR = 6        # the floor pan: no gaps against it


def p_fender(side):
    return 8 + (side > 0)


def p_door(k, side):
    return 10 + 2 * k + (side > 0)


def p_glass(code):
    return 100 + code


def log(*a):
    print("CARREAL", *a, flush=True)


# =================================================================== geometry
class Part:
    """A bmesh with the per-face palette cell and panel id plus edge crease."""

    def __init__(self):
        self.bm = bmesh.new()
        self.cell = self.bm.faces.layers.int.new("cell")
        self.panel = self.bm.faces.layers.int.new("panel")
        self.crease = self.bm.edges.layers.float.new("crease_edge")

    def v(self, p):
        return self.bm.verts.new(Vector(p))

    def f(self, verts, cell, panel=P_NONE):
        try:
            face = self.bm.faces.new(verts)
        except ValueError:
            return None
        face[self.cell] = cell
        face[self.panel] = panel
        return face


def to_object(part, name):
    me = bpy.data.meshes.new(name)
    part.bm.to_mesh(me)
    part.bm.free()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def apply_mods(ob):
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    old = ob.data
    ob.modifiers.clear()
    ob.data = me
    bpy.data.meshes.remove(old)
    return ob


def bm_of(ob):
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    return bm


def bm_back(bm, ob):
    bm.to_mesh(ob.data)
    bm.free()
    ob.data.update()


def frame_matrix(origin, a, u, d):
    """Local (across, up, depth) -> world."""
    m = Matrix.Identity(4)
    for i in range(3):
        m[i][0], m[i][1], m[i][2], m[i][3] = a[i], u[i], d[i], origin[i]
    return m


def rounded_rect(w, h, r, n=6):
    """CCW rounded rectangle (w x h, corner radius r) centred at 0, as (x, y)."""
    r = min(r, 0.49 * w, 0.49 * h)
    pts = []
    for cx, cy, a0 in ((w / 2 - r, h / 2 - r, 0.0), (-w / 2 + r, h / 2 - r, 90.0),
                       (-w / 2 + r, -h / 2 + r, 180.0), (w / 2 - r, -h / 2 + r, 270.0)):
        for i in range(n + 1):
            a = math.radians(a0 + 90.0 * i / n)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def prism(part, poly, z0, z1, mat, cell_wall, cell_z0, cell_z1, panel=P_NONE):
    """Closed prism of a CCW 2D polygon from local z0 to z1 (mat: local -> world)."""
    lo = [part.v(mat @ Vector((x, y, z0))) for x, y in poly]
    hi = [part.v(mat @ Vector((x, y, z1))) for x, y in poly]
    part.f(lo[::-1], cell_z0, panel)
    part.f(hi, cell_z1, panel)
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        part.f((lo[i], lo[j], hi[j], hi[i]), cell_wall, panel)


def ring_prism(part, outer, inner, z0, z1, mat, cell, panel=P_NONE):
    """Closed ring solid between two matching CCW polygons, local z0..z1."""
    L = [[part.v(mat @ Vector((x, y, z))) for x, y in poly] for poly in (outer, inner) for z in (z0, z1)]
    o0, o1, i0, i1 = L
    n = len(outer)
    for k in range(n):
        j = (k + 1) % n
        part.f((o0[k], o0[j], o1[j], o1[k]), cell, panel)      # outer wall
        part.f((i0[j], i0[k], i1[k], i1[j]), cell, panel)      # inner wall
        part.f((o1[k], o1[j], i1[j], i1[k]), cell, panel)      # top
        part.f((o0[j], o0[k], i0[k], i0[j]), cell, panel)      # bottom


def rbox(part, x0, x1, y0, y1, z0, z1, cell, r=None, seg=3, mat=None):
    """Bevelled box (axis-aligned, or in `mat`'s frame)."""
    r = min(r if r is not None else 0.25 * min(x1 - x0, y1 - y0, z1 - z0), 4.0)
    sub = Part()
    corners = [Vector((x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    vs = [sub.v(mat @ c if mat else c) for c in corners]
    for a, b, c, d in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        sub.f((vs[a], vs[b], vs[c], vs[d]), cell)
    bmesh.ops.recalc_face_normals(sub.bm, faces=sub.bm.faces)
    if r > 0.05:
        bmesh.ops.bevel(sub.bm, geom=list(sub.bm.edges) + list(sub.bm.verts), offset=r, segments=seg,
                        profile=0.5, affect='EDGES', clamp_overlap=True)
    merge(part, sub)


def merge(part, sub):
    """Append sub's geometry (with attributes) into part."""
    vmap = {}
    for v in sub.bm.verts:
        vmap[v] = part.bm.verts.new(v.co)
    for f in sub.bm.faces:
        nf = part.f([vmap[v] for v in f.verts], f[sub.cell], f[sub.panel])
        if nf is not None:
            nf.smooth = True
    for e in sub.bm.edges:
        c = e[sub.crease]
        if c > 0:
            ne = part.bm.edges.get((vmap[e.verts[0]], vmap[e.verts[1]]))
            if ne:
                ne[part.crease] = c
    sub.bm.free()


def cylinder(part, p0, p1, r0, r1, seg, cell, cap0=None, cap1=None, bevel=0.0):
    """Closed (optionally tapered) cylinder from p0 to p1."""
    sub = Part()
    p0, p1 = Vector(p0), Vector(p1)
    ax = (p1 - p0).normalized()
    a = ax.orthogonal().normalized()
    b = ax.cross(a)
    rings = []
    for p, r in ((p0, r0), (p1, r1)):
        rings.append([sub.v(p + r * (math.cos(t) * a + math.sin(t) * b))
                      for t in np.linspace(0, 2 * math.pi, seg, endpoint=False)])
    for i in range(seg):
        j = (i + 1) % seg
        sub.f((rings[0][i], rings[0][j], rings[1][j], rings[1][i]), cell)
    sub.f(rings[0][::-1], cap0 if cap0 is not None else cell)
    sub.f(rings[1], cap1 if cap1 is not None else cell)
    bmesh.ops.recalc_face_normals(sub.bm, faces=sub.bm.faces)
    if bevel > 0:
        caps = [e for e in sub.bm.edges if len(e.link_faces) == 2 and
                abs(e.link_faces[0].normal.dot(e.link_faces[1].normal)) < 0.5]
        bmesh.ops.bevel(sub.bm, geom=caps, offset=bevel, segments=2, profile=0.5, affect='EDGES',
                        clamp_overlap=True)
    merge(part, sub)


def revolve(part, prof, seg, cell_of_seg, axis="y", closed=False, phase=0.0):
    """Surface of revolution of [(r, a)] (a = position along the axis) about
    the local axis; cell_of_seg(i) gives the cell of profile segment i."""
    rings = []
    for r, a in prof:
        ring = []
        for k in range(seg):
            t = 2 * math.pi * k / seg + phase
            if axis == "y":
                ring.append(part.v((r * math.cos(t), a, r * math.sin(t))))
            else:   # x axis
                ring.append(part.v((a, r * math.cos(t), r * math.sin(t))))
        rings.append(ring)
    nprof = len(prof) if closed else len(prof) - 1
    for i in range(nprof):
        r0, r1 = rings[i], rings[(i + 1) % len(prof)]
        for k in range(seg):
            j = (k + 1) % seg
            part.f((r0[k], r0[j], r1[j], r1[k]), cell_of_seg(i))
    return rings


# =================================================================== the body
class Car:
    """Everything one preset recorded, plus the derived panel layout."""

    def __init__(self, name):
        REC.clear()
        self.name = name
        self.d = PRESETS[name]()
        self.calls = list(REC)
        hull = next(c for c in self.calls if c[0] == "build_hull")
        a, k = hull[1], hull[2]
        (self.profile, self.plan, self.body_cell, self.dark_cell, self.style,
         self.fx, self.rx, self.wr) = a[:8]
        self.center = k.get("center", a[8] if len(a) > 8 else None)
        self.lw = hull[3]
        self.ht = self.d["HalfTrackYCm"]
        st = self.style
        px = [p[0] for p in self.profile]
        self.x0, self.x1 = max(px), min(px)
        self.gs, self.ge = st["glass_x_start"], st["glass_x_end"]
        self.r_arch = self.wr + st["arch_margin_cm"]
        self.has_tail = st["tail_z"] is not None
        # panel layout: the bumper splits and door shuts of the stylized
        # material's GAPS lines, adjusted to the pillars
        self.xbf = min(self.fx + self.wr + 20.0, self.x0 - 15.0)
        self.xbr = max(self.rx - self.wr - 20.0, self.x1 + 15.0) if self.has_tail else -1e9
        self.x_hf = self.x0 - 0.35 * (self.x0 - self.xbf)
        self.x_tr = self.x1 + 0.35 * (self.xbr - self.x1) if self.has_tail else -1e9
        xdf = self.fx - self.wr - 28.0
        bp = [p for p in st["pillars_x"] if self.ge < p < xdf]
        xdr = max(bp) if bp else self.rx + self.wr + 30.0
        self.doors = DOORS.get(name, [(xdf, xdr)])
        self.door_x = sorted({x for d in self.doors for x in d}, reverse=True)

    def zt(self, x):
        px, _, pzt = zip(*sorted(self.profile))
        return float(np.interp(x, px, pzt))

    def width(self, x):
        wx, wh = zip(*sorted(self.plan))
        return float(np.interp(x, wx, wh))

    def seg_of(self, x):
        """x-segment index between door shut lines (splits glass panels)."""
        return sum(1 for d in self.door_x if x < d)

    def region(self, x):
        if x > self.xbf:
            return "bf"
        if x < self.xbr:
            return "br"
        for k, (a, b) in enumerate(self.doors):
            if b < x < a:
                return k
        return "front" if x > self.doors[0][0] else "rear"


DOORS = {"Pickup": [(115.0, 18.0), (18.0, -38.0)]}   # crew cab: two doors a side


def sections(car, xs):
    """Per station: list of (y, z) for the right half section points S0..Slast
    and the top-cap (centre) height/half width - the stylized build_hull
    construction with the same arch lift."""
    st = car.style
    px, pzb, pzt = (np.array(c) for c in zip(*sorted(car.profile)))
    wx, wh = (np.array(c) for c in zip(*sorted(car.plan)))
    zb = np.interp(xs, px, pzb)
    zt = np.interp(xs, px, pzt)
    w = np.interp(xs, wx, wh)
    zb_floor = zb.copy()
    r = car.r_arch
    for ax in (car.fx, car.rx):
        d2 = r * r - (xs - ax) ** 2
        zb = np.where(d2 > 0, np.maximum(zb, np.sqrt(np.maximum(d2, 0.0))), zb)
    belt = np.minimum(st["belt_z"], zt - 4.0)
    zb = np.minimum(zb, belt - 8.0)
    H = belt - zb
    cabin = (xs <= car.gs) & (xs >= car.ge)
    # roof-edge width ramps over ~24 cm at the glass ends (the stylized step
    # pinches once subdivided)
    ramp = np.clip(np.minimum(car.gs - xs, xs - car.ge) / 24.0 + 0.5, 0.0, 1.0)
    top_w = w * (st["hood_top_frac"] + (st["roof_top_frac"] - st["hood_top_frac"]) * ramp)
    Y = [0.80 * w, 0.97 * w, w, 0.97 * w, top_w]
    Z = [zb, zb + 0.3 * H, zb + 0.75 * H, belt, zt]
    if car.center:
        cx, cz, cw = (np.array(c) for c in zip(*sorted(car.center)))
        Y.append(np.interp(xs, cx, cw))
        Z.append(np.interp(xs, cx, cz))
    return dict(Y=np.stack(Y, 1), Z=np.stack(Z, 1), zb=zb, zb_floor=zb_floor, w=w, belt=belt,
                cabin=cabin, H=H)


def stations(car):
    keys = [car.xbf, car.x_hf, car.gs, car.ge, *car.door_x]
    if car.has_tail:
        keys += [car.xbr, car.x_tr]
    for p in car.style["pillars_x"]:
        keys += [p - 5.0, p + 5.0]
    for ax in (car.fx, car.rx):
        keys += [ax - car.r_arch, ax + car.r_arch, ax]
    nose, tail = car.x0 - BULGE, car.x1 + (BULGE if car.has_tail else 0.0)
    ks = []
    for k in sorted(keys, reverse=True):          # key lines, merged when < 1 cm apart
        if tail + 2.0 < k < nose - 2.0 and (not ks or ks[-1] - k > 1.0):
            ks.append(k)
    n = max(8, int(round((nose - tail) / CAGE_SPACING)))
    free = [x for x in np.linspace(nose, tail, n + 1)[1:-1]
            if all(abs(x - k) > 0.45 * CAGE_SPACING for k in ks)]
    return np.array([nose] + sorted(free + ks, reverse=True) + [tail])


BULGE = 6.0      # nose/tail cap bulge: last ring set back this much, cap centre at the old end
CAP_FR = [0.82, 0.55, 0.27, 0.0, -0.27, -0.55, -0.82]   # top-cap points, fraction of the cap half width
WELL_CLEAR = 17.0    # wheel-well inner wall this far inboard of the half-track (wheel half width 13)


def build_cage(car):
    """The closed quad cage (Part) of the hull, with cell / panel / crease.

    Ring per station (closed loop): right side S0..Stop, top cap, left side
    Stop..S0, then the underside: well top W1, well foot W2, floor, W2, W1.
    Over the axles W1 sits at the arch height, so S0 -> W1 -> W2 is the
    wheel-well roof and inner wall; elsewhere they lie flat in the floor."""
    xs = stations(car)
    S = sections(car, xs)
    Y, Z = S["Y"], S["Z"]
    ns = Y.shape[1]
    st = car.style
    part = Part()
    zc = Z[:, -1]
    steep = np.zeros(len(xs), bool)
    for i in range(len(xs) - 1):
        steep[i] = abs(zc[i] - zc[i + 1]) / (xs[i] - xs[i + 1]) > st["steep_slope"]
    w_in = car.ht - WELL_CLEAR

    rings, desc = [], []
    for i, x in enumerate(xs):
        y0, zf = Y[i, 0], S["zb_floor"][i]
        w1y, w2y = min(0.93 * y0, w_in), min(0.86 * y0, w_in - 4.0)
        w1z = min(S["zb"][i], zc[i] - 4.0)
        crown = 1.5 if not car.center else 0.8
        pts = [(x, Y[i, k], Z[i, k]) for k in range(ns)]
        pts += [(x, f * Y[i, -1], zc[i] + crown * (1 - f * f)) for f in CAP_FR]
        pts += [(x, -Y[i, k], Z[i, k]) for k in range(ns - 1, -1, -1)]
        pts += [(x, -w1y, w1z), (x, -w2y, zf), (x, -0.45 * w2y, zf), (x, 0.0, zf), (x, 0.45 * w2y, zf),
                (x, w2y, zf), (x, w1y, w1z)]
        rings.append([part.v(p) for p in pts])
    nr = len(rings[0])
    nc = len(CAP_FR)
    for k in range(ns - 1):
        desc.append(("side", k, 1))
    for k in range(nc + 1):
        desc.append(("cap", k, 0))
    for k in range(ns - 1):
        desc.append(("side", ns - 2 - k, -1))
    desc += [("well", 0, -1), ("well", 1, -1), ("floor", 0, 0), ("floor", 1, 0), ("floor", 2, 0),
             ("floor", 3, 0), ("well", 1, 1), ("well", 0, 1)]
    assert len(desc) == nr, (len(desc), nr)

    for i in range(len(xs) - 1):
        xc = 0.5 * (xs[i] + xs[i + 1])
        in_cabin = bool(S["cabin"][i] and S["cabin"][i + 1])
        pillar = any(abs(xc - p) < 5.0 for p in st["pillars_x"])
        for k in range(nr):
            kind, b, side = desc[k]
            a0, a1 = rings[i][k], rings[i][(k + 1) % nr]
            b0, b1 = rings[i + 1][k], rings[i + 1][(k + 1) % nr]
            cell, panel = face_cell_panel(car, S, i, xc, kind, b, side, in_cabin, pillar, steep[i], ns, nc)
            part.f((a0, a1, b1, b0), cell, panel)

    # end caps: Coons patches over the end rings, centre bulged out to the
    # stylized end so the overall length is unchanged
    for i, sgn in ((0, 1.0), (len(xs) - 1, -1.0)):
        ring = rings[i]
        side_r, capv = ring[:ns], ring[ns:ns + nc]
        side_l, under = ring[ns + nc:2 * ns + nc], ring[2 * ns + nc:]
        bottom = [side_l[-1]] + under + [side_r[0]]
        top = [side_l[0]] + capv[::-1] + [side_r[-1]]
        leftc, rightc = side_l[::-1], side_r
        nu, nv = len(bottom) - 1, len(leftc) - 1
        assert len(top) == len(bottom)
        grid = [[None] * (nu + 1) for _ in range(nv + 1)]
        for u in range(nu + 1):
            grid[0][u], grid[nv][u] = bottom[u], top[u]
        for v in range(nv + 1):
            grid[v][0], grid[v][nu] = leftc[v], rightc[v]
        P = lambda vv: np.array(vv.co)
        bulge = BULGE if (sgn > 0 or car.has_tail) else 0.0
        for v in range(1, nv):
            for u in range(1, nu):
                s, t = u / nu, v / nv
                p = ((1 - t) * P(bottom[u]) + t * P(top[u]) + (1 - s) * P(leftc[v]) + s * P(rightc[v])
                     - ((1 - s) * (1 - t) * P(bottom[0]) + s * (1 - t) * P(bottom[nu])
                        + (1 - s) * t * P(top[0]) + s * t * P(top[nu])))
                p[0] = xs[i] + sgn * bulge * max(0.0, 1 - (2 * s - 1) ** 2) ** 0.6 * (1 - 0.35 * abs(2 * t - 1))
                grid[v][u] = part.v(p)
        pan = P_BUMF if sgn > 0 else (P_BUMR if car.has_tail else P_SHELL)
        for v in range(nv):
            for u in range(nu):
                part.f((grid[v][u], grid[v][u + 1], grid[v + 1][u + 1], grid[v + 1][u]),
                       PLASTIC if v == 0 else car.body_cell, pan)
    # character lines: creases along the section points
    for i in range(len(xs) - 1):
        for k in range(ns):
            c = CREASE.get(k, 0.0)
            for idx in (k, 2 * ns + nc - 1 - k):
                e = part.bm.edges.get((rings[i][idx], rings[i + 1][idx]))
                if e and c:
                    e[part.crease] = c
        for idx in (2 * ns + nc, nr - 1):        # well top edge
            e = part.bm.edges.get((rings[i][idx], rings[i + 1][idx]))
            if e:
                e[part.crease] = 0.7
    bmesh.ops.recalc_face_normals(part.bm, faces=part.bm.faces)
    for f in part.bm.faces:
        f.smooth = True
    return part, xs, S


def face_cell_panel(car, S, i, xc, kind, b, side, in_cabin, pillar, steep, ns, nc):
    body, dark = car.body_cell, car.dark_cell
    if kind in ("floor", "well"):
        return PLASTIC, P_FLOOR
    reg = car.region(xc)
    if kind == "side":
        is_crest = car.center is not None and b == ns - 2
        if in_cabin and not pillar:
            if car.center is not None:
                glass = is_crest and min(S["Z"][i, -1] - S["Z"][i, -2],
                                         S["Z"][i + 1, -1] - S["Z"][i + 1, -2]) > 3.0
            else:
                glass = b == 3
            if glass:
                return GLASS, p_glass(10 * (side > 0) + car.seg_of(xc))
        if not is_crest:
            cell = dark if b == 0 else body
            if reg == "bf":
                return cell, P_BUMF
            if reg == "br":
                return cell, P_BUMR
            if reg == "front":
                return cell, p_fender(side)
            if isinstance(reg, int) and b >= 1:
                return cell, p_door(reg, side)
            return cell, P_SHELL
    # top cap (and the crest band of centre-line cars)
    if kind == "cap" and in_cabin and steep and 1 <= b <= nc - 1:
        return GLASS, p_glass(30 + car.seg_of(xc))
    if xc > car.x_hf:
        return body, P_BUMF
    if xc < car.x_tr:
        return body, P_BUMR
    if xc > car.gs:
        return body, P_HOOD
    if xc < car.ge and car.has_tail:
        return body, P_TRUNK
    return body, P_SHELL


# ================================================================ materials
CAT_LOOK = {   # preview-only Principled settings (UE uses its own _real materials)
    "body": dict(rough=0.25, metal=0.0, coat=1.0), "plastic": dict(rough=0.6, metal=0.0),
    "glass": dict(rough=0.05, metal=0.0, alpha=0.35), "chrome": dict(rough=0.12, metal=1.0),
    "tire": dict(rough=0.85, metal=0.0), "rim": dict(rough=0.3, metal=1.0),
    "lights": dict(rough=0.1, metal=0.0, emit=0.6),
}


def get_material(cat):
    m = bpy.data.materials.get(cat)
    if m:
        return m
    m = bpy.data.materials.new(cat)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    attr = nt.nodes.new("ShaderNodeAttribute")
    attr.attribute_name = "Col"
    nt.links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    look = CAT_LOOK[cat]
    bsdf.inputs["Roughness"].default_value = look["rough"]
    bsdf.inputs["Metallic"].default_value = look["metal"]
    if "coat" in look:
        bsdf.inputs["Coat Weight"].default_value = look["coat"]
    if "alpha" in look:
        bsdf.inputs["Alpha"].default_value = look["alpha"]
        try:
            m.surface_render_method = "BLENDED"
        except (AttributeError, TypeError):
            pass
    if "emit" in look:
        nt.links.new(attr.outputs["Color"], bsdf.inputs["Emission Color"])
        bsdf.inputs["Emission Strength"].default_value = look["emit"]
    return m


def finish_mesh(ob):
    """Material slots by category, UV0 -> palette cell, preview colours."""
    me = ob.data
    nf = len(me.polygons)
    cells = np.zeros(nf, np.int32)
    me.attributes["cell"].data.foreach_get("value", cells)
    present = {CAT_OF.get(int(k), "plastic") for k in np.unique(cells)}
    used = [c for c in CATS if c in present]
    me.materials.clear()
    for c in used:
        me.materials.append(get_material(c))
    lut = np.array([used.index(CAT_OF[k]) if CAT_OF.get(k) in used else 0 for k in range(N_CELL)], np.int32)
    me.polygons.foreach_set("material_index", lut[cells])
    ltot = np.zeros(nf, np.int32)
    me.polygons.foreach_get("loop_total", ltot)
    lc = np.repeat(cells, ltot)
    while len(me.uv_layers):
        me.uv_layers.remove(me.uv_layers[0])
    uv0 = me.uv_layers.new(name="UVMap")
    uv = np.stack([(lc + 0.5) / N_CELL, np.full(len(lc), 0.5)], 1).astype(np.float32)
    uv0.data.foreach_set("uv", uv.ravel())
    me.uv_layers.new(name="UV1")
    if "Col" in me.attributes:
        me.attributes.remove(me.attributes["Col"])
    col = me.attributes.new("Col", "FLOAT_COLOR", "CORNER")
    pal = np.array([(*c, 1.0) for c in PAL], np.float32)
    col.data.foreach_set("color", pal[lc].ravel())
    return used


def unwrap_uv1(ob):
    me = ob.data
    me.uv_layers.active = me.uv_layers["UV1"]
    bpy.ops.object.select_all(action="DESELECT")
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(60), island_margin=0.002, scale_to_bounds=True)
    bpy.ops.object.mode_set(mode="OBJECT")
    me.uv_layers.active = me.uv_layers["UVMap"]
    me.uv_layers["UVMap"].active_render = True


def shade(ob, angle=40.0):
    me = ob.data
    me.shade_smooth()
    me.set_sharp_from_angle(angle=math.radians(angle))
    mod = ob.modifiers.new("wn", "WEIGHTED_NORMAL")
    mod.keep_sharp = True
    mod.weight = 50
    apply_mods(ob)


def tri_count(ob):
    return sum(len(p.vertices) - 2 for p in ob.data.polygons)


# ================================================================== preview
def setup_preview_scene():
    sc = bpy.context.scene
    for eng in ("BLENDER_EEVEE", "BLENDER_EEVEE_NEXT"):
        try:
            sc.render.engine = eng
            break
        except TypeError:
            continue
    sc.render.resolution_x, sc.render.resolution_y = 1280, 720
    sc.render.film_transparent = False
    world = bpy.data.worlds.new("w") if not sc.world else sc.world
    sc.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    sky = world.node_tree.nodes.new("ShaderNodeTexSky") if hasattr(bpy.types, "ShaderNodeTexSky") else None
    if sky is not None:
        try:
            sky.sky_type = "HOSEK_WILKIE"
        except TypeError:
            pass
        world.node_tree.links.new(sky.outputs["Color"], bg.inputs["Color"])
        bg.inputs["Strength"].default_value = 0.6
    else:
        bg.inputs["Color"].default_value = (0.6, 0.7, 0.8, 1)
    if "sun" not in bpy.data.objects:
        sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
        sun.data.energy = 3.5
        sun.data.angle = math.radians(3)
        sun.rotation_euler = (math.radians(40), math.radians(10), math.radians(140))
        sc.collection.objects.link(sun)
    if "ground" not in bpy.data.objects:
        me = bpy.data.meshes.new("ground")
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=3000)
        bm.to_mesh(me)
        g = bpy.data.objects.new("ground", me)
        mat = bpy.data.materials.new("groundmat")
        mat.use_nodes = True
        mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.32, 0.32, 0.31, 1)
        mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"].default_value = 0.8
        me.materials.append(mat)
        sc.collection.objects.link(g)
    if "cam" not in bpy.data.objects:
        cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
        cam.data.lens = 50
        cam.data.clip_end = 100000
        sc.collection.objects.link(cam)
        sc.camera = cam
    return sc


def render_views(objs, ground_z, length, path_stem, views=(("front", 35.0), ("rear", 215.0))):
    sc = setup_preview_scene()
    hide = [o for o in sc.objects if o.type == "MESH" and o.name != "ground" and o not in objs]
    for o in hide:
        o.hide_render = True
    for o in objs:
        o.hide_render = False
    bpy.data.objects["ground"].location.z = ground_z
    cam = bpy.data.objects["cam"]
    out = []
    for tag, az in views:
        d = 2.2 * length
        a = math.radians(az)
        cam.location = (d * math.cos(a), d * math.sin(a), ground_z + 0.42 * length)
        tgt = Vector((0, 0, ground_z + 55))
        cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = str(path_stem) + f"_{tag}.png"
        bpy.ops.render.render(write_still=True)
        out.append(sc.render.filepath)
    for o in hide:
        o.hide_render = False
    return out


# ================================================================== cutters
def surface_cutter(part, bvh, axis, sgn, poly, depth, cell_wall, cell_back, out=12.0):
    """Closed prism to subtract from the body: outline `poly` [(u, v)] is
    (y, z) seen from the nose/tail (axis 0, sgn +1/-1) or (x, z) seen from
    the side (axis 1). Every outline point's back sits `depth` behind the
    surface at that point, so the recess follows the body's curvature."""
    def P(s, u, v):
        return Vector((s, u, v)) if axis == 0 else Vector((u, s, v))

    d = Vector((0.0, 0.0, 0.0))
    d[axis] = -sgn

    def surf(u, v):
        loc = bvh.ray_cast(P(sgn * 2000.0, u, v), d)[0]
        return None if loc is None else loc[axis]

    ss = [surf(u, v) for u, v in poly]
    good = [s for s in ss if s is not None]
    if not good:
        return False
    med = float(np.median(good))
    ss = [med if s is None else min(max(s, med - 10.0), med + 10.0) for s in ss]
    front = [part.v(P(s + sgn * out, u, v)) for s, (u, v) in zip(ss, poly)]
    back = [part.v(P(s - sgn * depth, u, v)) for s, (u, v) in zip(ss, poly)]
    cu, cv = np.mean(poly, axis=0)
    cs = surf(cu, cv)
    c = part.v(P((med if cs is None else cs) - sgn * depth, cu, cv))
    n = len(poly)
    for i in range(n):
        j = (i + 1) % n
        part.f((front[i], front[j], back[j], back[i]), cell_wall)
        part.f((back[j], back[i], c), cell_back)
    part.f(front, cell_wall)
    return True


def surface_x(bvh, y, z, sgn):
    loc = bvh.ray_cast(Vector((sgn * 2000.0, y, z)), Vector((-sgn, 0, 0)))[0]
    return None if loc is None else loc.x


def rrect_at(cu, cv, w, h, r):
    return [(cu + a, cv + b) for a, b in rounded_rect(w, h, r, 4)]


def lamp_layout(car):
    """Stylized end-cap lamp/grille rectangles: (kind, side, y0, y1, z0, z1)."""
    st, out = car.style, []
    ends = [(+1, car.x0 - BULGE, st["lamp_z"], st["lamp_y"])]
    if car.has_tail:
        ends.append((-1, car.x1 + BULGE, st["tail_z"], st["tail_y"]))
    for sgn, x, lz, ly in ends:
        S = sections(car, np.array([x]))
        lo, h, hw = float(S["zb"][0]), float(S["H"][0]), float(S["w"][0])
        out.append(("grille" if sgn > 0 else "valance", sgn, -0.44 * hw, 0.44 * hw, lo + 0.12 * h, lo + 0.5 * h))
        for s in (1, -1):
            y0, y1 = sorted((s * ly[0] * hw, s * ly[1] * hw))
            out.append(("head" if sgn > 0 else "tail", sgn, y0, y1, lo + lz[0] * h, lo + lz[1] * h))
    return out


def cut_body(car, body, extra):
    """Exact-boolean the lamp housings, grille recess and intakes out of the
    closed subdivided shell; returns lamp internals to add as a Part."""
    bm = bm_of(body)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    cut, inner = Part(), Part()
    for kind, sgn, y0, y1, z0, z1 in lamp_layout(car):
        w, h = y1 - y0, z1 - z0
        cu, cv = 0.5 * (y0 + y1), 0.5 * (z0 + z1)
        if kind == "grille":
            surface_cutter(cut, bvh, 0, sgn, rrect_at(cu, cv, w, h, 0.3 * h), 5.0, PLASTIC, PLASTIC)
            if car.style["grille_cell"] == CHROME:   # chrome bars in the recess
                xs = surface_x(bvh, 0.0, cv, sgn)
                for k in range(1, 4):
                    zk = z0 + k * h / 4
                    rbox(inner, xs - 4.0, xs - 1.0, y0 + 2, y1 - 2, zk - 0.9, zk + 0.9, CHROME, r=0.6)
        elif kind == "valance":
            continue
        elif kind == "head":
            surface_cutter(cut, bvh, 0, sgn, rrect_at(cu, cv, w, h, 0.4 * min(w, h)), 4.5, PLASTIC, CHROME)
            n = 2 if w > 2.6 * h else 1
            r = min(0.36 * h, 0.2 * w, 4.5)
            for k in range(n):
                yk = y0 + (k + 0.5) * w / n
                xs = surface_x(bvh, yk, cv, sgn)
                if xs is None:
                    continue
                xb = xs - 4.5
                cylinder(inner, (xb - 0.5, yk, cv), (xb + 0.5 * r, yk, cv), 1.3 * r, 1.3 * r, 24, CHROME)
                sub = Part()
                bmesh.ops.create_uvsphere(sub.bm, u_segments=20, v_segments=10, radius=r,
                                          matrix=Matrix.Translation((xb + 0.4 * r, yk, cv)))
                for f in sub.bm.faces:
                    f[sub.cell] = HEADLIGHT
                merge(inner, sub)
            # LED running-light strip along the housing's lower edge
            ys = np.linspace(y0 + 0.15 * w, y1 - 0.15 * w, 8)
            xl = [surface_x(bvh, yy, z0 + 0.18 * h, sgn) for yy in ys]
            if all(v is not None for v in xl):
                xm = min(xl) - 3.2
                rbox(inner, xm, xm + 1.0, ys[0], ys[-1], z0 + 0.1 * h, z0 + 0.1 * h + 1.0, HEADLIGHT, r=0.3)
        elif kind == "tail":
            # outboard 70% tail, inboard 30% reverse (as the stylized lamp),
            # plus a deeper inset lit area inside the tail part
            inboard_lo = abs(y0) < abs(y1)
            yr = y0 + 0.3 * w if inboard_lo else y1 - 0.3 * w
            ra, rb = sorted((y0 if inboard_lo else y1, yr))
            ta, tb = sorted((yr, y1 if inboard_lo else y0))
            surface_cutter(cut, bvh, 0, sgn, rrect_at(0.5 * (ra + rb) + (0.3 if inboard_lo else -0.3), cv,
                                                       rb - ra + 0.6, h, 0.25 * h), 1.6, PLASTIC, REVERSE)
            surface_cutter(cut, bvh, 0, sgn, rrect_at(0.5 * (ta + tb), cv, tb - ta, h, 0.3 * h), 2.0,
                           PLASTIC, TAILLIGHT)
            if tb - ta > 6 and h > 4:
                surface_cutter(cut, bvh, 0, sgn, rrect_at(0.5 * (ta + tb), cv, tb - ta - 2.4, h - 2.4, 0.25 * h),
                               3.4, TAILLIGHT, TAILLIGHT)
    for name, a, k, _ in car.calls:
        if name == "side_patch":     # side intakes, recessed into the flanks
            x, lwf, zc, cell = a[:4]
            sw, sh = k.get("size", a[4] if len(a) > 4 else (16.0, 9.0))
            for s in (1, -1):
                surface_cutter(cut, bvh, 1, s, rrect_at(x, zc, sw, sh, 0.3 * sh), 7.0, cell, cell)
    for f in cut.bm.faces:
        f.smooth = False
    bmesh.ops.recalc_face_normals(cut.bm, faces=cut.bm.faces)
    # one Exact boolean per cutter shell: a collection operand with the
    # overlapping tail/reverse cutters came back empty
    cutter = to_object(cut, "cutter")
    bpy.ops.object.select_all(action="DESELECT")
    cutter.select_set(True)
    bpy.context.view_layer.objects.active = cutter
    bpy.ops.mesh.separate(type="LOOSE")
    for c in [o for o in bpy.context.scene.objects if o.name.startswith("cutter")]:
        before = body.data.copy()
        mod = body.modifiers.new("cut", "BOOLEAN")
        mod.operation, mod.solver, mod.object = "DIFFERENCE", "EXACT", c
        apply_mods(body)
        if len(body.data.polygons) < 0.8 * len(before.polygons):
            log("boolean failed for", c.name, "- skipped")
            old = body.data
            body.data = before
            bpy.data.meshes.remove(old)
        else:
            bpy.data.meshes.remove(before)
        bpy.data.objects.remove(c)
    return inner


# ============================================================= panel gaps
def panel_gaps(body, dark):
    """Split the shell along panel borders, pull each edge back GAP/2 and
    turn a LIP-deep flange in, so doors / hood / trunk / bumpers read as
    separate panels; windows get a rubber seal and sit 0.6 cm in."""
    bm = bm_of(body)
    cell = bm.faces.layers.int["cell"]
    pan = bm.faces.layers.int["panel"]

    def splittable(p):
        return 0 < p < 100 and p != P_FLOOR

    gap = [e for e in bm.edges if len(e.link_faces) == 2 and
           e.link_faces[0][pan] != e.link_faces[1][pan] and
           splittable(e.link_faces[0][pan]) and splittable(e.link_faces[1][pan])]
    bmesh.ops.split_edges(bm, edges=gap)
    bnd = [e for e in bm.edges if e.is_boundary]
    bverts = {v for e in bnd for v in e.verts}
    bm.normal_update()
    nrm = {}
    for v in bverts:
        d = Vector((0.0, 0.0, 0.0))
        for f in v.link_faces:
            t = f.calc_center_median() - v.co
            d += t - t.project(v.normal)
        if d.length > 1e-6:
            v.co += d.normalized() * min(GAP / 2, 0.4 * d.length)
        nrm[v] = v.normal.copy()
    ret = bmesh.ops.extrude_edge_only(bm, edges=bnd)
    for g in ret["geom"]:
        if isinstance(g, bmesh.types.BMVert):
            src = next((e.other_vert(g) for e in g.link_edges if e.other_vert(g) in nrm), None)
            if src is not None:
                g.co -= nrm[src] * LIP
        elif isinstance(g, bmesh.types.BMFace):
            g[cell], g[pan] = dark, P_NONE
            g.smooth = True
    # windows: inset into a seal, then pushed in
    glass_ids = {f[pan] for f in bm.faces if f[pan] >= 100}
    for gid in glass_ids:
        faces = [f for f in bm.faces if f[pan] == gid]
        ret = bmesh.ops.inset_region(bm, faces=faces, thickness=1.0, depth=0.0, use_even_offset=True)
        for f in ret["faces"]:
            f[cell], f[pan] = PLASTIC, P_NONE
        verts = {v for f in faces for v in f.verts}
        bm.normal_update()
        for v in verts:
            v.co -= v.normal * 0.6
    bm_back(bm, body)
    return len(gap)


# =================================================================== parts
def torus(part, mat, R, r, cell, nu=32, nv=8):
    rings = []
    for i in range(nu):
        a = 2 * math.pi * i / nu
        ring = []
        for j in range(nv):
            b = 2 * math.pi * j / nv
            p = Vector(((R + r * math.cos(b)) * math.cos(a), (R + r * math.cos(b)) * math.sin(a), r * math.sin(b)))
            ring.append(part.v(mat @ p))
        rings.append(ring)
    for i in range(nu):
        for j in range(nv):
            a, b = rings[i], rings[(i + 1) % nu]
            part.f((a[j], b[j], b[(j + 1) % nv], a[(j + 1) % nv]), cell)


def airfoil(part, x, z0, half_span, chord, thick, cell):
    """Inverted cambered blade, leading edge forward, inside the old wing box."""
    pts_u, pts_l = [], []
    for s in np.linspace(0.0, 1.0, 14):
        t = 5 * thick * chord * (0.2969 * math.sqrt(s) - 0.126 * s - 0.3516 * s * s + 0.2843 * s ** 3 - 0.1036 * s ** 4)
        zc = z0 + 1.5 - 0.35 * math.sin(math.pi * s)
        xx = x + chord / 2 - s * chord
        pts_u.append((xx, zc + t / 2))
        pts_l.append((xx, zc - t / 2))
    poly = pts_l + pts_u[::-1][1:-1]
    mat = frame_matrix(Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0)))
    prism(part, poly, -half_span, half_span, mat, cell, cell, cell)


def build_parts(car):
    """Everything the preset adds on top of its hull, rebuilt rounded."""
    p = Part()
    st = car.style
    fins = []
    for name, a, k, _ in car.calls:
        if name == "mirrors":
            x, lwf, cell = a[:3]
            _, belt, _ = lwf.z(x)
            y = lwf(x) * 0.88
            for s in (1, -1):
                ya, yb = sorted((s * (y + 6), s * (y + 20)))
                rbox(p, x - 12, x, ya, yb, belt + 3, belt + 11, cell, r=3.0)
                ya, yb = sorted((s * y, s * (y + 7)))
                rbox(p, x - 9, x - 4, ya, yb, belt + 3, belt + 6, PLASTIC, r=1.0)
                ya, yb = sorted((s * (y + 7.2), s * (y + 18.8)))
                rbox(p, x - 12.4, x - 11.6, ya, yb, belt + 4.2, belt + 9.8, CHROME, r=0.3)
        elif name == "wing":
            x, z_deck, z_wing, hs = a[:4]
            chord = k.get("chord", a[4] if len(a) > 4 else 24.0)
            cell = k.get("cell", a[5] if len(a) > 5 else ACCENT)
            plate = k.get("plate_cell", a[6] if len(a) > 6 else None) or cell
            for s in (1, -1):
                yc = s * hs * 0.55
                rbox(p, x - 4, x + 4, yc - 2, yc + 2, z_deck - 2, z_wing + 1.5, cell, r=1.5)
            airfoil(p, x, z_wing, hs - 1.0, chord, 2.6 / chord, cell)
            for s in (1, -1):
                ya, yb = sorted((s * hs - s * 1.5, s * hs))
                rbox(p, x - chord / 2 - 2, x + chord / 2, ya, yb, z_wing - 6, z_wing + 8, plate, r=0.6)
        elif name == "exhaust_tips":
            x, y, z = a[:3]
            cell = k.get("cell", a[3] if len(a) > 3 else CHROME)
            r = k.get("r", a[4] if len(a) > 4 else 4.0)
            length = k.get("length", a[5] if len(a) > 5 else 10.0)
            for s in (1, -1):
                cylinder(p, (x + 8, s * y, z), (x - length, s * y, z), 0.95 * r, 0.95 * r, 28, cell, bevel=0.4)
                cylinder(p, (x - length + 3.5, s * y, z), (x - length - 0.15, s * y, z), 0.75 * r, 0.75 * r,
                         28, PLASTIC)
        elif name == "box":
            x0, x1, y0, y1, z0, z1, cell = a[:7]
            if abs(y0) <= 1.3 and abs(y1) <= 1.3:
                fins.append((x0, x1, z0, z1, cell))
            elif cell == HEADLIGHT:          # lamp pods / light bar: dark housing, lens in front
                rbox(p, x0 - 1.5, x1 - 0.8, y0, y1, z0, z1, PLASTIC, r=1.2)
                rbox(p, x1 - 1.2, x1, y0 + 1.0, y1 - 1.0, z0 + 1.0, z1 - 1.0, HEADLIGHT, r=0.6)
            else:
                rbox(p, x0, x1, y0, y1, z0, z1, cell)
        elif name == "vpanel":
            x, y0, y1, z0, z1, cell = a[:6]
            ya, yb = sorted((y0, y1))
            rbox(p, x - 0.5, x + 0.5, ya, yb, z0, z1, cell, r=0.4)
        elif name == "build_pickup_bed":
            # ponytail: the bed reuses the stylized flat geometry (it is a box
            # anyway); a lofted/bevelled bed is the upgrade if it reads too flat
            m = mcm.Mesh()
            mcm.build_pickup_bed(m, *a, **k)
            vmap = {}
            for t in m.tris:
                vs = []
                for q in t[:3]:
                    key = tuple(np.round(q, 4))
                    if key not in vmap:
                        vmap[key] = p.v(key)
                    vs.append(vmap[key])
                p.f(vs, t[3])
    if fins:   # shark fin: the stepped fin boxes as one rounded-top blade
        cell = fins[0][4]
        fins.sort(key=lambda f: -f[1])
        top = [(f[1], f[3]) for f in fins] + [(fins[-1][0], fins[-1][3])]
        base = [(f[1], f[2]) for f in fins] + [(fins[-1][0], fins[-1][2])]
        poly = base[::-1] + top
        mat = frame_matrix(Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0)))
        prism(p, [(x, z) for x, z in poly], -1.1, 1.1, mat, cell, cell, cell)
    bmesh.ops.recalc_face_normals(p.bm, faces=p.bm.faces)
    for f in p.bm.faces:
        f.smooth = True
    return p


SEAT_ROWS = {"Muscle": 2, "JDM": 2, "Super": 1, "Pickup": 2, "Rally": 2, "Concept": 1, "LMP": 1}


def build_interior(car, body_bm):
    """Dark cabin: the shell's inside offset 2.5 cm in (door cards, headliner),
    a dash, seats and a steering wheel (on +y: UE -y... left side, LHD)."""
    p = Part()
    pan = body_bm.faces.layers.int["panel"]
    body_bm.normal_update()
    vmap = {}
    for f in body_bm.faces:
        c = f.calc_center_median()
        if f[pan] == P_FLOOR or not (car.ge - 4 < c.x < car.gs + 4):
            continue
        vs = []
        for v in f.verts:
            if v not in vmap:
                vmap[v] = p.v(v.co - v.normal * 2.5)
            vs.append(vmap[v])
        p.f(vs[::-1], PLASTIC)
    seat_y = lambda x: 0.45 * car.width(x) if not car.center else min(0.45 * car.width(x), 0.55 * float(
        np.interp(x, *zip(*sorted((c[0], c[2]) for c in car.center)))))
    S = sections(car, np.array([car.gs - 20.0]))
    belt, ztop = float(S["belt"][0]), float(S["Z"][0, -1])
    zf = float(S["zb_floor"][0])
    xd = car.gs - 8.0
    zdash = min(belt + 3.0, ztop - 5.0)
    yd = 0.72 * car.width(car.gs - 20)
    rbox(p, xd - 26, xd, -yd, yd, zdash - 16, zdash, PLASTIC, r=4.0)
    xh = car.doors[0][1] + 22.0                      # front hip point, ahead of the B pillar
    rows = [xh] + ([xh - 78.0] if SEAT_ROWS[car.name] > 1 and xh - 95.0 > car.ge else [])
    for ri, x in enumerate(rows):
        ys = seat_y(x)
        Sx = sections(car, np.array([x]))
        top = float(Sx["Z"][0, -1]) - 8.0
        for s in (1, -1):
            yc = s * ys
            rbox(p, x, x + 46, yc - 23, yc + 23, zf + 6, zf + 19, PLASTIC, r=5.0)
            mat = Matrix.Translation((x, yc, zf + 16)) @ Matrix.Rotation(math.radians(-14), 4, "Y")
            rbox(p, -12, 0, -23, 23, 0, min(top, belt + 22) - zf - 16, PLASTIC, r=4.0, mat=mat)
        if ri == 0:   # steering wheel ahead of the +y seat, column into the dash
            xw = x + 58.0
            if xw < xd - 4:
                zw = min(belt + 2.0, top - 10.0)
                mat = Matrix.Translation((xw, ys, zw)) @ Matrix.Rotation(math.radians(-62), 4, "Y")
                torus(p, mat, 17.0, 1.6, PLASTIC)
                cylinder(p, (xw, ys, zw), (xd - 10, ys, zw + 8), 2.0, 2.4, 12, PLASTIC)
    for f in p.bm.faces:
        f.smooth = True
    return p


# =================================================================== wheels
WHEEL_HW = 13.0      # wheel half width (stylized SM_CarWheel: 13)
RIM_R = 0.64 * R_REF


def build_wheel():
    """Shared realistic wheel at R_REF, axle on local Y, symmetric about
    y = 0 (the pawn mounts it unmirrored on both sides): tyre with rounded
    shoulders, sidewall and tread grooves, a six-spoke dished alloy on both
    faces, barrel, lug nuts / centre cap and a brake disc on y = 0."""
    p = Part()
    R, HW, SEG = R_REF, WHEEL_HW, 96
    # --- tyre: (r, y) profile, bead -> sidewall -> shoulder -> tread (grooves) -> back
    half = [(RIM_R + 0.6, HW - 1.2), (RIM_R + 3.0, HW - 0.2), (R - 8.0, HW), (R - 3.5, HW - 0.6),
            (R - 1.2, HW - 2.2), (R - 0.2, HW - 4.0)]
    tread = [(R, 9.6), (R, 8.6), (R - 0.9, 8.4), (R - 0.9, 7.2), (R, 7.0), (R, 2.8), (R - 0.9, 2.6),
             (R - 0.9, 1.4), (R, 1.2)]
    prof = half + tread + [(r, -y) for r, y in tread[::-1]] + [(r, -y) for r, y in half[::-1]]
    rings = []
    for i, (r, y) in enumerate(prof):
        ring = []
        for k in range(SEG):
            t = 2 * math.pi * k / SEG
            rr = r
            # shoulder tread blocks: lateral grooves notched into every other segment
            if abs(y) > 9.0 and r > R - 4.0 and k % 2:
                rr -= 0.7
            ring.append(p.v((rr * math.cos(t), y, rr * math.sin(t))))
        rings.append(ring)
    for i in range(len(prof) - 1):
        for k in range(SEG):
            j = (k + 1) % SEG
            p.f((rings[i][k], rings[i][j], rings[i + 1][j], rings[i + 1][k]), TIRE)
    # inner bead closure (tyre is a closed torus-like shell)
    for k in range(SEG):
        j = (k + 1) % SEG
        p.f((rings[-1][k], rings[-1][j], rings[0][j], rings[0][k]), TIRE)
    # --- rim barrel + lips: revolve (r, y), closed
    barrel = [(RIM_R + 1.4, HW - 0.6), (RIM_R + 1.4, HW - 1.6), (RIM_R - 0.2, HW - 2.0), (RIM_R - 1.2, 6.0),
              (RIM_R - 1.2, -6.0), (RIM_R - 0.2, -(HW - 2.0)), (RIM_R + 1.4, -(HW - 1.6)), (RIM_R + 1.4, -(HW - 0.6)),
              (RIM_R - 1.6, -(HW - 0.6)), (RIM_R - 2.4, -6.0), (RIM_R - 2.4, 6.0), (RIM_R - 1.6, HW - 0.6)]
    br = revolve(p, barrel, SEG, lambda i: RIM, closed=True)
    # --- spoke faces on both sides
    for s in (1, -1):
        spoke_face(p, s)
    # --- brake disc + hat on the centre plane
    cylinder(p, (0, -1.3, 0), (0, 1.3, 0), 0.47 * R, 0.47 * R, 64, RIM)
    cylinder(p, (0, -3.0, 0), (0, 3.0, 0), 0.2 * R, 0.2 * R, 32, ACCENT)
    bmesh.ops.recalc_face_normals(p.bm, faces=p.bm.faces)
    for f in p.bm.faces:
        f.smooth = True
    return p


def spoke_face(p, s, n_sp=6, r_hub=7.2, r_out=RIM_R - 1.4, nr=7, m_sp=4, m_win=10):
    """Dished six-spoke face at side s: a polar grid whose angular samples
    follow the spoke edges exactly, windows removed, then given thickness."""
    sub = Part()
    radii = np.linspace(r_hub, r_out, nr)

    def half_ang(r):     # spoke half angle: wide at the hub, slim outside, flared at the lip
        w = np.interp(r, [r_hub, 0.55 * r_out, 0.9 * r_out, r_out], [3.4, 2.1, 2.3, 3.6])
        return w / r

    def ypos(r):         # dish: hub sits 3 cm deeper than the spoke tips
        return s * (WHEEL_HW - 2.2 - 3.0 * (1 - (r - r_hub) / (r_out - r_hub)) ** 1.5)

    rings = []
    for r in radii:
        h = half_ang(r)
        ring = []
        for i in range(n_sp):
            c = 2 * math.pi * i / n_sp
            nxt = c + 2 * math.pi / n_sp
            angs = list(np.linspace(c - h, c + h, m_sp + 1)) + list(np.linspace(c + h, nxt - h, m_win + 1))[1:-1]
            ring += [(a, i * (m_sp + m_win - 1) + q) for q, a in enumerate(angs)]
        rings.append([sub.v((r * math.cos(a), ypos(r), r * math.sin(a))) for a, _ in ring])
    per = m_sp + m_win - 1
    nring = len(rings[0])
    for j in range(nr - 1):
        for q in range(nring):
            qq = (q + 1) % nring
            in_spoke = (q % per) < m_sp
            if not in_spoke and 0 < j < nr - 2:
                continue
            sub.f((rings[j][q], rings[j][qq], rings[j + 1][qq], rings[j + 1][q]), RIM)
    # hub disc
    c = sub.v((0, ypos(r_hub) - s * 0.4, 0))
    for q in range(nring):
        sub.f((rings[0][(q + 1) % nring], rings[0][q], c), RIM)
    sub.bm.normal_update()
    for f in sub.bm.faces:
        if f.normal.y * s < 0:
            f.normal_flip()
    ob = to_object(sub, "spokes")
    sol = ob.modifiers.new("t", "SOLIDIFY")
    sol.thickness = 2.2
    sol.offset = -1.0
    sol.use_even_offset = True
    bev = ob.modifiers.new("b", "BEVEL")
    bev.width = 0.35
    bev.segments = 2
    bev.limit_method = "ANGLE"
    apply_mods(ob)
    bm = bm_of(ob)
    bpy.data.objects.remove(ob)
    tmp = Part()
    tmp.bm.free()
    tmp.bm, tmp.cell = bm, bm.faces.layers.int["cell"]
    tmp.panel, tmp.crease = bm.faces.layers.int["panel"], bm.edges.layers.float.get("crease_edge") or \
        bm.edges.layers.float.new("crease_edge")
    merge(p, tmp)
    # lug nuts + centre cap, outboard of the hub
    yh = ypos(r_hub)
    for i in range(5):
        a = 2 * math.pi * i / 5 + math.pi / 2
        cylinder(p, (4.6 * math.cos(a), yh, 4.6 * math.sin(a)), (4.6 * math.cos(a), yh + s * 1.6, 4.6 * math.sin(a)),
                 0.85, 0.75, 6, CHROME)
    cylinder(p, (0, yh - s * 0.5, 0), (0, yh + s * 1.0, 0), 2.6, 2.2, 24, CHROME, bevel=0.3)


# =================================================================== export
def export_fbx(ob, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    ob.select_set(True)
    bpy.context.view_layer.objects.active = ob
    bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, apply_unit_scale=True,
                             apply_scale_options="FBX_SCALE_UNITS", mesh_smooth_type="OFF",
                             use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False,
                             colors_type="NONE", use_tspace=False)


def make_tiers(src, base_name):
    """Decimate the full mesh into the three tiers, fix up UVs/normals,
    export each; returns {tier name: triangles}."""
    out = {}
    for suffix, ratio in TIER_RATIO.items():
        name = f"{base_name}{suffix}"
        ob = bpy.data.objects.new(name, src.data.copy())
        bpy.context.scene.collection.objects.link(ob)
        bm = bm_of(ob)
        bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4])
        bm_back(bm, ob)
        if ratio < 1.0:
            d = ob.modifiers.new("dec", "DECIMATE")
            d.ratio = ratio
            d.use_collapse_triangulate = True
            apply_mods(ob)
        finish_mesh(ob)            # palette UV0 re-snapped from the "cell" attribute
        unwrap_uv1(ob)
        shade(ob)
        out[name] = tri_count(ob)
        export_fbx(ob, FBX / f"{name}.fbx")
        log(name, out[name], "tris")
        if suffix != "_ultra":
            bpy.data.objects.remove(ob)
        else:
            ultra = ob
    return out, ultra


def build_car(name):
    t0 = time.time()
    car = Car(name)
    part, xs, S = build_cage(car)
    body = to_object(part, f"SM_Car_{name}_src")
    m = body.modifiers.new("ss", "SUBSURF")
    m.levels = m.render_levels = SUBDIV
    apply_mods(body)
    bm = bm_of(body)
    interior = build_interior(car, bm)
    bm.free()
    inner = cut_body(car, body, None)
    gaps = panel_gaps(body, car.dark_cell)
    objs = [body, to_object(inner, "inner"), to_object(build_parts(car), "parts"), to_object(interior, "interior")]
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = body
    bpy.ops.object.join()
    bm = bm_of(body)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.001)
    bm_back(bm, body)
    log(name, "built", len(xs), "stations", gaps, "gap edges", f"{time.time() - t0:.1f}s")
    tris, ultra = make_tiers(body, f"SM_Car_{name}_real")
    bpy.data.objects.remove(body)
    return car, tris, ultra


def place_wheels(car, wheel):
    obs = []
    sc = car.wr / R_REF
    for x in (car.fx, car.rx):
        for s in (1, -1):
            o = bpy.data.objects.new("w", wheel.data)
            o.location = (x, s * car.ht, 0.0)
            o.scale = (sc, 1.0, sc)
            bpy.context.scene.collection.objects.link(o)
            obs.append(o)
    return obs


def stylized_object(name):
    """The stylized OBJ, for the old-vs-new comparison render."""
    path = ROOT / "data" / "cars" / "procedural" / "obj" / f"{name}.obj"
    bpy.ops.wm.obj_import(filepath=str(path), forward_axis="Y", up_axis="Z")
    ob = bpy.context.selected_objects[0]
    ob.scale.y = -1.0          # the OBJ writer negates y
    me = ob.data
    uv = np.zeros(len(me.loops) * 2, np.float32)
    me.uv_layers[0].data.foreach_get("uv", uv)
    lc = np.clip((uv[0::2] * N_CELL).astype(int), 0, N_CELL - 1)
    col = me.attributes.new("Col", "FLOAT_COLOR", "CORNER")
    col.data.foreach_set("color", np.array([(*c, 1.0) for c in PAL], np.float32)[lc].ravel())
    for i, m in enumerate(me.materials):
        me.materials[i] = get_material(m.name.split(".")[0])
    return ob


def main():
    bpy.context.scene.unit_settings.system = "METRIC"
    bpy.context.scene.unit_settings.scale_length = 0.01     # 1 BU = 1 cm in the FBX
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o)
    cars = CARS_ARG or list(PRESETS)
    manifest = {"wheel": {}, "cars": {}}
    wp = build_wheel()
    wsrc = to_object(wp, "SM_CarWheel_src")
    wt, wheel = make_tiers(wsrc, "SM_CarWheel_real")
    bpy.data.objects.remove(wsrc)
    manifest["wheel"] = dict(tris=wt, RefRadiusCm=R_REF, HalfWidthCm=WHEEL_HW, RimRadiusCm=RIM_R)
    for name in cars:
        car, tris, ultra = build_car(name)
        manifest["cars"][name] = dict(tris=tris, FrontAxleXCm=car.fx, RearAxleXCm=car.rx,
                                      HalfTrackYCm=car.ht, WheelRadiusCm=car.wr,
                                      bbox=[[round(min((ultra.matrix_world @ Vector(c))[i] for c in ultra.bound_box), 2)
                                             for i in range(3)],
                                            [round(max((ultra.matrix_world @ Vector(c))[i] for c in ultra.bound_box), 2)
                                             for i in range(3)]])
        if DO_PREVIEW:
            PREVIEW.mkdir(parents=True, exist_ok=True)
            ws = place_wheels(car, wheel)
            length = car.x0 - car.x1
            render_views([ultra] + ws, -car.wr, length, PREVIEW / f"SM_Car_{name}_real")
            if name == (cars[0]):
                old = stylized_object(f"SM_Car_{name}")
                old.location.y = -2.6 * car.ht
                ultra.location.y = 1.3 * car.ht
                for w in ws:
                    w.location.y += 1.3 * car.ht
                ws_old = place_wheels(car, wheel)
                for w in ws_old:
                    w.location.y -= 2.6 * car.ht
                render_views([ultra, old] + ws + ws_old, -car.wr, 1.5 * length,
                             PREVIEW / f"compare_{name}", views=(("old_vs_new", 60.0),))
                bpy.data.objects.remove(old)
                for w in ws_old:
                    bpy.data.objects.remove(w)
                ultra.location.y = 0.0
            for w in ws:
                bpy.data.objects.remove(w)
        bpy.data.objects.remove(ultra)
    OUT.mkdir(parents=True, exist_ok=True)
    mpath = OUT / "real_manifest.json"
    old = json.loads(mpath.read_text()) if mpath.exists() else {"cars": {}}
    old["cars"].update(manifest["cars"])
    old["wheel"] = manifest["wheel"]
    mpath.write_text(json.dumps(old, indent=2))
    (OUT / "LICENSE.txt").write_text(
        "Original procedural content generated by this repo (pipeline/make_car_meshes_real.py, run in\n"
        "Blender). Our own badge-free designs; no third-party models, textures or scans. Same terms as\n"
        "the OpenDrive repository.\n")
    log("done", json.dumps(manifest["cars"] and {k: v["tris"] for k, v in manifest["cars"].items()}))


if __name__ == "__main__":
    main()


