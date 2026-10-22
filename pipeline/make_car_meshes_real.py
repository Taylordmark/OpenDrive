"""Realistic car meshes (PLAN-VISUALS Track B, models pass), built headless
in Blender from the same seven presets as pipeline/make_car_meshes.py.

    blender.exe -b --factory-startup --python pipeline/make_car_meshes_real.py -- [Car ...] [--preview]

Run from the repo root (or anywhere: paths are relative to this file). With
no car names all seven are built. --preview also renders 3/4 front and rear
and an orthographic side view (side_<Car>.png) into data/cars/preview_real/
(offline asset renders at the static ride height, no game).

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

Shape (CHAR, per car): plan taper to nose and tail with rounded corners, a
waist between the arches, lower flank flared over the arches, rear-arch hips,
shoulder character line, tumblehome and a crowned roof/hood.

Side silhouette (PROFILE, per car, _real only): its own crest line, glass
start/end, side-glass end and a beltline rising toward the tail, in place of
the stylized one. Nose / tail x and floor are the stylized ones, so the
bounding box holds.

Arches: built for every garage setup's tyre (SETUPS, read from
unreal/apply_tuning_presets.py). The in-game static wheel centre is the
authored axle - RideHeightDelta (UODVehicleMovement derives the strut tops
from the sag, 2026-10-13). The lip, with a rolled turned-in edge, is the union
of a circle ARCH_GAP over the Street tyre and LIP_MIN over each setup's; the
well roof clears the tyre at the bump stop (tyre top BUMP_TRAVEL over the
authored one, body-fixed, = BumpTravelCm, any tyre size). The fender stands
FENDER_OUT outboard of every setup tyre point above the lip, static and at
the bump stop, front ones swept to full lock (tyre_sweep) - so the fenders
may flare past the stylized width. arch_clearance() ray-tests static / bump /
lock / bump + lock per setup into the manifest.

Wheels: SM_CarWheel_real (road tyre: grooves, shoulder blocks) and one
SM_CarWheel_real_<Kind> per garage-setup tyre (SETUP_TYRES: Knobby for
Rally / Offroad, Stretched for Drift, Rib front / Slick rear for Drag,
Semislick for Track), each with a 6-spoke dished alloy sized to its rim on
BOTH faces (the pawn mounts the same mesh left and right), lug nuts, brake
disc. All authored at REF_WHEEL_RADIUS_CM / WHEEL_HW; the pawn scales them by
the setup's tyre size / width. No caliper: it must not spin with the wheel,
so it needs its own pawn component. --preview adds tyres_lineup / _tread
and side_<Car>_<Setup>.png (each setup's tyres, ride height and camber).

Contract (unchanged from the stylized set, see make_car_meshes.py): pivot at
the wheelbase centre at axle height z=0, cm, +x forward; axle x / half-track
/ wheel radius are the presets' (= unreal/import_car_meshes.py) values.

Interface: material slots are the same categories (body/plastic/glass/
chrome/tire/rim/lights) named by the Blender material, UV0 points every face
at its T_CarPalette2 cell (lamps are told apart by cell, see
unreal/apply_car_lights_material.py), UV1 is a non-overlapping unwrap.

Tiers: <name>_real_ultra is the full mesh (cage subdivided twice),
_real_high is Decimate-collapsed from it (UV0 is re-snapped to the palette
cells afterwards: collapse interpolates UVs across cell borders), and _real
(Standard) is its own build on the cage subdivided once (CAR_TIERS). Wheels
are all decimated (TIER_RATIO).

Outputs (data/cars/real/, gitignored like the rest of data/):
    fbx/SM_Car_<Car>_real{,_high,_ultra}.fbx
    fbx/SM_CarWheel_real{,_Knobby,_Stretched,_Slick,_Rib,_Semislick}{,_high,_ultra}.fbx
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
import tuning_setups  # noqa: E402

SETUPS = tuning_setups.setups()     # garage setups' tyre size / width / ride height / camber / lock

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
CAGE_SPACING = 10.0      # cm between loft stations in the cage (key stations come on top)
GAP = 0.5                # panel gap width, cm
LIP = 1.2                # turned-in panel edge depth, cm
TIER_RATIO = {"_ultra": 1.0, "_high": 0.5, "": 0.22}   # Decimate ratio per tier suffix (wheels)
# car tiers per Catmull-Clark level on the body cage: Standard is its own
# once-subdivided build, not a decimated ultra (collapse faceted the curves
# and chewed the panel gaps)
CAR_TIERS = {2: {"_ultra": 1.0, "_high": 0.5}, 1: {"": 1.0}}
CREASE = {0: 1.0, 1: 0.0, 2: 0.8, 3: 0.45, 4: 0.6, 5: 0.6}   # key section point -> edge crease

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
        # side profile (PROFILE): the _real silhouette replaces the stylized
        # one here only; same nose / tail x and the same floor, so the bbox holds
        pr = PROFILE[name]
        self.dgs = pr["gs"] - self.gs                 # cab shift, moves the mirrors with it
        self.profile, self.gs, self.ge = pr["profile"], pr["gs"], pr["ge"]
        self.sge = pr.get("sge", self.ge)             # side glass ends here (C-pillar / sail behind)
        self.belt_fr, self.hood_crown = pr["belt"], pr.get("hood_crown", 0.06)
        self.valley = pr.get("valley", 0.0)            # fender crest over the hood gutter, cm
        # arches (module docstring): lip circles (centre z, radius), Street
        # first; the well roof clears the tyre at the bump stop
        self.zs = (0.0, 0.0)      # Street static wheel centres
        # per axle (front, rear); a front circle encloses the tyre steered to lock
        # (centre z, x semi-axis, z semi-axis, flat-top half length): the side
        # view of a tyre steered by l is its ellipses (R cos l, R) swept
        # +-hw sin l along x
        m = self.wr + ARCH_GAP
        self.arcs = ([(0.0, m, m, 0.0)], [(0.0, m, m, 0.0)])
        for g in SETUPS.values():
            for a in (0, 1):
                R, hw, zs, _, lock = setup_tyre(self, g, a == 0)
                for l in {0.0, math.radians(lock)}:
                    self.arcs[a].append((zs, R * math.cos(l) + LIP_MIN, R + LIP_MIN, hw * math.sin(l)))
        self.r_arch = max(ax + s for arcs in self.arcs for _, ax, _, s in arcs)     # opening half length
        self.r_bump = self.wr + BUMP_CLEAR
        self.tyre_out = self.ht + WHEEL_HW * self.wr / R_REF
        out, self.tyre_in, sx, self.swept = tyre_sweep(self)
        self.need_y = tuple(self.ht + o + FENDER_OUT for o in out)    # front, rear fender skin
        self.well_clear = max(WELL_CLEAR, max(self.tyre_in) + 2.0)    # well inner wall inboard of the half track
        self.r_well = max(self.r_arch, sx + 3.0)                       # well roof x half-length
        self.has_tail = st["tail_z"] is not None
        # a short nose (Concept) would put the last ring on the arch: shallower cap
        self.bulge = min(BULGE, self.x0 - self.fx - self.r_arch - 4.0)
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

    def lip(self, dx, a):
        """Arch lip z at dx from axle a (0 front, 1 rear; -1e9 outside the opening)."""
        dx = np.asarray(dx, float)
        out = np.full(dx.shape, -1e9)
        for cz, ex, ez, s in self.arcs[a]:
            e = 1.0 - (np.maximum(np.abs(dx) - s, 0.0) / ex) ** 2
            out = np.maximum(out, np.where(e > 0.0, cz + ez * np.sqrt(np.maximum(e, 0.0)), -1e9))
        return out

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

ARCH_GAP = 3.0       # arch lip radius = wheel radius + this, round the Street static wheel centre
LIP_MIN = 2.0        # ... and at least this over every setup's static tyre (the slammed Track one)
BUMP_CLEAR = 2.5     # well roof radius = wheel radius + this, round the full-bump wheel centre
BUMP_TRAVEL = 8.0    # UODVehicleMovement::BumpTravelCm: the bump stop, tyre top over the authored one
FENDER_OUT = 3.0     # fender skin this far outboard of any tyre point above the lip (tucks under at bump / lock)
FLARE_TAPER = 0.3    # fender flare width falls off this many cm per cm fore / aft of where it is needed
WELL_CLEAR = 34.0    # well inner wall at least this far inboard of the half-track (tyre_sweep widens it)
MAX_STEER = 38.0     # ODVehicleDef MaxSteerAngleDeg default (no car overrides it); x SteerLockScale


def tyre_points(R, hw, steer, camber):
    """Tyre surface samples (cylinder, a little outside the rounded real one)
    round its centre, y+ = outboard: camber leans the top in, then steer."""
    T, U = np.meshgrid(np.radians(np.arange(0.0, 360.0, 3.0)), np.linspace(-hw, hw, 7))
    x, y, z = R * np.sin(T).ravel(), U.ravel(), R * np.cos(T).ravel()
    c, s = math.cos(math.radians(camber)), math.sin(math.radians(camber))
    y, z = y * c - z * s, y * s + z * c
    c, s = math.cos(math.radians(steer)), math.sin(math.radians(steer))
    return np.stack([x * c - y * s, x * s + y * c, z], 1)


def setup_tyre(car, g, front):
    """(radius, half width, static centre z, bump-stop centre z, lock deg) of a setup's tyre."""
    R = car.wr * g["tire_diameter_scale"]
    hw = WHEEL_HW * R / R_REF * g["front_tire_width_scale" if front else "rear_tire_width_scale"]
    return R, hw, -g["ride_height_delta"], BUMP_TRAVEL + car.wr - R, (MAX_STEER * g["steer_lock_scale"] if front else 0.0)


def tyre_sweep(car):
    """Per axle (front, rear): how far outboard of the half track any setup's
    tyre reaches above the arch lip (inside the body: the fender must cover
    it), static and at the bump stop, front ones at straight and full lock;
    how far inboard any reaches (the well's inner wall); and how far fore /
    aft any reaches above the lip (the well roof's length)."""
    inn, swept = [0.0, 0.0], ([], [])
    for g in SETUPS.values():
        for a in (0, 1):
            R, hw, zs, zb, lock = setup_tyre(car, g, a == 0)
            for zc in (zs, zb):
                for steer in {0.0, lock, -lock}:
                    p = tyre_points(R, hw, steer, g["camber_deg"])
                    p[:, 2] += zc
                    swept[a].append(p[p[:, 2] > car.lip(p[:, 0], a) - 2.0])
                    inn[a] = max(inn[a], float(-p[:, 1].min()))
    swept = tuple(np.concatenate(s) for s in swept)
    out = [float(s[:, 1].max()) for s in swept]
    sx = max(float(np.abs(s[:, 0]).max()) for s in swept)
    return out, inn, sx, swept


# per-car side silhouette (x, floor z, top z), glass start / end, where the
# side glass stops, beltline (z at glass start, z at glass end: rising to the
# rear), hood crown (fraction of the hood half width; < 0 = fenders above
# the hood) and the fender crest's rise over the hood gutter (valley, cm).
# Ends and floor are the stylized ones (bbox), the rest is new.
PROFILE = {
    # long hood, slight power bulge, upright-ish screen, fastback, high ducktail
    "Muscle": dict(profile=[(236, -12, 38), (226, -20, 50), (190, -22, 55), (140, -22, 58), (60, -22, 62),
                            (20, -22, 99), (-45, -22, 102), (-120, -22, 72), (-165, -22, 66),
                            (-205, -18, 66), (-222, -12, 59)],
                   gs=60.0, ge=-120.0, sge=-82.0, belt=(58.0, 64.0), hood_crown=0.10, valley=3.0),
    # long hood, short deck: fastback glass onto a short ducktail deck
    "JDM": dict(profile=[(214, -8, 22), (204, -18, 34), (160, -20, 44), (124, -20, 48), (56, -20, 53),
                         (16, -20, 89), (-35, -20, 95), (-75, -20, 85), (-122, -20, 62), (-190, -18, 59),
                         (-210, -12, 53)],
                gs=56.0, ge=-122.0, sge=-78.0, belt=(50.0, 56.0), valley=2.5),
    # low sharp nose, hood falling to the splitter, cab back, fast rake,
    # buttressed roof onto a kammback tail
    "Super": dict(profile=[(212, -16, 5), (204, -22, 15), (185, -24, 27), (160, -24, 37), (140, -24, 43),
                           (118, -24, 45), (90, -24, 44), (62, -24, 44), (5, -24, 79), (-25, -24, 83),
                           (-60, -24, 78), (-95, -24, 67), (-130, -24, 60), (-170, -24, 57), (-200, -20, 54),
                           (-214, -14, 51)],
                  gs=62.0, ge=-95.0, sge=-48.0, belt=(41.0, 50.0), hood_crown=0.02, valley=4.0,
                  intake=(-97.0, 22.0, 24.0, 22.0)),
    # cab forward over a tall flat hood; the bed is its own part
    "Pickup": dict(profile=[(268, 4, 66), (258, -6, 80), (230, -8, 86), (185, -8, 90), (130, -8, 97),
                            (86, -8, 148), (-30, -8, 152), (-38, -8, 146), (-44, -8, 92)],
                   gs=130.0, ge=-36.0, belt=(93.0, 97.0), hood_crown=0.04, valley=1.5),
    # short-overhang hatch: rounded-off ends, tall cowl, near-vertical hatch
    "Rally": dict(profile=[(190, -4, 30), (182, -13, 41), (150, -18, 50), (112, -18, 55), (70, -18, 62),
                           (24, -18, 109), (-100, -18, 112), (-140, -18, 107), (-160, -16, 64), (-180, -10, 56)],
                  gs=70.0, ge=-162.0, sge=-140.0, belt=(58.0, 64.0), valley=2.0),
    # smooth teardrop: softened fender humps under the stylized canopy
    "Concept": dict(profile=[(176, -24, 14), (169, -28, 26), (154, -28, 42), (128, -28, 50), (100, -28, 48),
                             (78, -28, 45), (0, -28, 43), (-70, -28, 46), (-110, -28, 52), (-140, -28, 54),
                             (-175, -28, 52), (-205, -26, 48), (-228, -20, 42)],
                    gs=86.0, ge=-70.0, belt=(48.0, 52.0),
                    intake_f=(62.0, 9.0, 50.0, 22.0)),     # scoop kept above the tucked rocker
    # the Super, more extreme: knife nose, tall fender pods, low tub
    "LMP": dict(profile=[(236, -27, 5), (226, -29, 16), (200, -29, 38), (175, -29, 50), (150, -29, 55),
                         (118, -29, 48), (100, -29, 26), (20, -29, 26), (-70, -29, 34), (-105, -29, 50),
                         (-140, -29, 56), (-180, -29, 54), (-215, -27, 46), (-248, -22, 40)],
                gs=100.0, ge=-95.0, belt=(48.0, 52.0)),
}


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
    well = np.full_like(xs, -1e9)
    for a, ax in enumerate((car.fx, car.rx)):
        zb = np.maximum(zb, car.lip(xs - ax, a))
        # well roof: half-ellipse r_bump high and wider than the lip opening,
        # so the station chords at its ends still clear the bumped tyre (and
        # the tread corner of a steered one, swung forward / back)
        e = 1.0 - ((xs - ax) / (car.r_well + 10.0)) ** 2
        well = np.maximum(well, np.where(np.abs(xs - ax) <= car.r_well, BUMP_TRAVEL + car.r_bump * np.sqrt(np.maximum(e, 0.0)), -1e9))
    well = np.maximum(well, zb)
    # the fender top clears the well roof (the tyre at full bump): a
    # fender hump where the silhouette is lower than that
    zt = np.maximum(zt, well + 7.0)
    bf, br = car.belt_fr
    belt_fn = bf + (br - bf) * (car.gs - xs) / (car.gs - car.ge)
    belt = np.maximum(np.minimum(belt_fn, zt - 4.0), well + 2.5)
    zb = np.minimum(zb, belt - 8.0)
    H = belt - zb
    cabin = (xs <= car.gs) & (xs >= car.ge)
    # plan shape (CHAR): nose/tail taper, rounded corners, a waist between
    # the arches; then scaled back so the widest point is the stylized one
    c = CHAR[car.name]
    end = np.maximum(np.clip((xs - car.fx) / (car.x0 - car.fx), 0, 1),
                     np.clip((car.rx - xs) / (car.rx - car.x1), 0, 1))
    corner = np.clip(1.0 - np.minimum(car.x0 - xs, xs - car.x1) / 45.0, 0, 1)
    arch_f, arch_r = (np.cos(0.5 * np.pi * np.clip(np.abs(xs - ax) / (r + 25.0), 0, 1)) ** 2
                      for ax in (car.fx, car.rx))
    arch = np.maximum(arch_f, arch_r)
    between = np.clip((xs - car.rx) / (car.fx - car.rx), 0, 1)
    waist = c["waist"] * np.sin(np.pi * between) * (1.0 - arch)
    wb = w * (1.0 - c["taper"] * end ** 2 - 0.15 * corner ** 2.5 - waist)
    wb *= w.max() / wb.max()
    over_tyre = np.min([np.abs(xs - ax) for ax in (car.fx, car.rx)], axis=0) < car.wr
    wb = np.where(over_tyre, np.maximum(wb, np.minimum(w, car.ht + WHEEL_HW + 1.0)), wb)   # never bare a tyre
    # side section: tucked rocker, lower flank curving out to the shoulder
    # (character line, the widest point), shoulder top rolling in to the
    # belt; the lower flank and the belt flare out over the arches
    fl = c["flare"] * arch
    hips = c["hips"] * arch_r
    # roof-edge width ramps over ~24 cm at the glass ends (the stylized step
    # pinches once subdivided); tumblehome pulls the greenhouse in further
    ramp = np.clip(np.minimum(car.gs - xs, xs - car.ge) / 24.0 + 0.5, 0.0, 1.0)
    roof = st["roof_top_frac"] * (1.0 - c["tumble"])
    top_w = wb * (st["hood_top_frac"] + (roof - st["hood_top_frac"]) * ramp)
    hood = np.clip((xs - car.gs) / 20.0, 0.0, 1.0)
    crown = 0.0 if car.center else (0.06 + (car.hood_crown - 0.06) * hood) * top_w
    Y = [wb * (0.82 + 0.17 * fl), wb * (0.93 + 0.07 * fl), wb, wb * (0.88 + 0.08 * hips), top_w]
    near = np.min([np.abs(xs - ax) for ax in (car.fx, car.rx)], axis=0)
    t = np.clip((r + 18.0 - near) / 18.0, 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    t = t * np.clip(np.minimum(car.x0 - xs, xs - car.x1) / 8.0, 0.0, 1.0)    # no fender blade past a short nose
    Z = [zb, zb + 0.32 * H, zb + 0.78 * H, belt, zt - 0.6 * crown]
    # fender flares: each section point stands FENDER_OUT outboard of every
    # setup's tyre point (tyre_sweep: static / bump stop, steered) that rises
    # above the lip near it in x and between its neighbours in z - widest at
    # the lip, tucking in over the tyre top - tapered off fore / aft
    for k in range(5):
        req = np.full(len(xs), -1e9)
        for i, x in enumerate(xs):
            a = int(abs(x - car.fx) >= abs(x - car.rx))
            p = car.swept[a]
            lo, hi = (Z[k - 1][i] if k else -1e9) - 1.0, (Z[k + 1][i] if k < 4 else 1e9) + 1.0
            m = p[(np.abs(p[:, 0] - (x - (car.fx, car.rx)[a])) < 8.0) & (p[:, 2] >= lo) & (p[:, 2] <= hi)]
            if len(m):
                req[i] = car.ht + m[:, 1].max() + FENDER_OUT
        req = np.max(req[None, :] - FLARE_TAPER * np.abs(xs[:, None] - xs[None, :]), axis=1)
        Y[k] = np.maximum(Y[k], req)
    w = wb
    if car.center:
        # fender pods: over the arches the pod top reaches in past the
        # steering sweep, so its inner wall drops inboard of the well
        Y[4] = Y[4] + (np.minimum(Y[4], car.ht - car.well_clear + 2.0) - Y[4]) * t
        cx, cz, cw = (np.array(c) for c in zip(*sorted(car.center)))
        Y.append(np.minimum(np.interp(xs, cx, cw), Y[4] - 2.0))
        Z.append(np.interp(xs, cx, cz))
    Y, Z, key = densify(np.stack(Y, 1), np.stack(Z, 1))
    if car.valley and not car.center:
        # hood / fender valley: the belt-to-top midpoint stands up as the
        # fender crest, the top edge (hood shut) is the gutter, the hood
        # crowns again inboard; faded out over the last 30 cm to the nose
        fade = hood * np.clip((car.x0 - car.bulge - xs) / 30.0, 0.0, 1.0)
        m = key[3] + 1
        Z[:, m] += fade * np.maximum(Z[:, key[4]] + car.valley - Z[:, m], 0.0)
    return dict(Y=Y, Z=Z, key=key, zb=zb, zb_floor=zb_floor, w=w, belt=belt,
                cabin=cabin, H=H, crown=crown, well=well)


SIDE_EXTRA = (1, 2, 1, 1)   # extra side points between key points S0..S4 (none up to a crest)


def densify(Y, Z):
    """Insert SIDE_EXTRA points per band on a Hermite curve through the key
    section points (Catmull-Rom tangents, one-sided at hard creases so the
    rocker / shoulder stay sharp; straight up the belt-to-top band, the side
    glass), clamped inside each band's z range and never inboard of it. Returns dense Y, Z and the key points' indices."""
    P = np.stack([Y, Z], 2)
    nk = P.shape[1]
    T = np.empty_like(P)
    T[:, 1:-1] = 0.5 * (P[:, 2:] - P[:, :-2])
    T[:, 0], T[:, -1] = P[:, 1] - P[:, 0], P[:, -1] - P[:, -2]
    out, key = [P[:, 0]], [0]
    for k in range(nk - 1):
        a, b = P[:, k], P[:, k + 1]
        ta = b - a if k == 3 or CREASE.get(k, 0.0) >= 0.8 else T[:, k]
        tb = b - a if k == 3 or CREASE.get(k + 1, 0.0) >= 0.8 else T[:, k + 1]
        m = SIDE_EXTRA[k] if k < len(SIDE_EXTRA) else 0
        for j in range(1, m + 1):
            s = j / (m + 1)
            p = ((2 * s ** 3 - 3 * s ** 2 + 1) * a + (s ** 3 - 2 * s ** 2 + s) * ta
                 + (3 * s ** 2 - 2 * s ** 3) * b + (s ** 3 - s ** 2) * tb)
            p[:, 0] = np.maximum(p[:, 0], np.minimum(a[:, 0], b[:, 0]))
            p[:, 1] = np.clip(p[:, 1], np.minimum(a[:, 1], b[:, 1]), np.maximum(a[:, 1], b[:, 1]))
            out.append(p)
        out.append(b)
        key.append(len(out) - 1)
    D = np.stack(out, 1)
    return D[..., 0], D[..., 1], key


# per-car body character (sections): plan taper at nose/tail, waist between
# the arches (coke bottle), lower-flank flare over the arches, rear-arch hips,
# extra tumblehome on the greenhouse
CHAR = {
    "Muscle":  dict(taper=0.07, waist=0.08, flare=0.9, hips=1.0, tumble=0.04),
    "JDM":     dict(taper=0.10, waist=0.06, flare=0.8, hips=0.3, tumble=0.06),
    "Super":   dict(taper=0.16, waist=0.08, flare=1.0, hips=0.6, tumble=0.10),
    "Pickup":  dict(taper=0.04, waist=0.03, flare=0.6, hips=0.0, tumble=0.0),
    "Rally":   dict(taper=0.08, waist=0.06, flare=1.0, hips=0.4, tumble=0.05),
    "Concept": dict(taper=0.14, waist=0.07, flare=0.9, hips=0.5, tumble=0.06),
    "LMP":     dict(taper=0.12, waist=0.08, flare=1.0, hips=0.5, tumble=0.0),
}


def stations(car):
    keys = [car.xbf, car.x_hf, car.gs, car.ge, car.sge, *car.door_x]
    if car.has_tail:
        keys += [car.xbr, car.x_tr]
    for p in car.style["pillars_x"]:
        keys += [p - 5.0, p + 5.0]
    for ax in (car.fx, car.rx):
        keys += [ax - car.r_well, ax + car.r_well, ax - car.r_arch, ax + car.r_arch, ax, ax - 0.93 * car.r_arch, ax + 0.93 * car.r_arch]
    nose, tail = car.x0 - car.bulge, car.x1 + (BULGE if car.has_tail else 0.0)
    ks = []
    for k in sorted(keys, reverse=True):          # key lines, merged when < 1 cm apart
        if tail + 2.0 < k < nose - 2.0 and (not ks or ks[-1] - k > 1.0):
            ks.append(k)
    n = max(8, int(round((nose - tail) / CAGE_SPACING)))
    free = [x for x in np.linspace(nose, tail, n + 1)[1:-1]
            if all(abs(x - k) > 0.45 * CAGE_SPACING for k in ks)]
    return np.array([nose] + sorted(free + ks, reverse=True) + [tail])


BULGE = 10.0     # nose/tail cap bulge: last ring set back this much, cap centre at the old end
# top-cap points, fraction of the cap half width (as many as the underside
# points, for the end-cap Coons patches)
CAP_FR = [0.86, 0.645, 0.43, 0.215, 0.0, -0.215, -0.43, -0.645, -0.86]


def build_cage(car):
    """The closed quad cage (Part) of the hull, with cell / panel / crease.

    Ring per station (closed loop): right side S0..Stop, top cap, left side
    Stop..S0, then the underside: lip L, well top W1, well foot W2, floor,
    W2, W1, L. Over the axles S0 is the arch lip, S0 -> L the turned-in lip
    face (2 cm, up to the well roof), L -> W1 the well roof at full-bump
    height and W1 -> W2 the inner wall; elsewhere they lie flat in the floor."""
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
    w_in = car.ht - car.well_clear

    rings, desc = [], []
    for i, x in enumerate(xs):
        y0, zf = Y[i, 0], S["zb_floor"][i]
        w1y, w2y = min(0.93 * y0, w_in), min(0.86 * y0, w_in - 4.0)
        w1z = min(S["well"][i], Z[i, S["key"][4]] - 4.0)     # under the outer top (not a centre-line car's crest)
        ly = y0 - 2.0
        crown = 0.8 if car.center else float(S["crown"][i])
        pts = [(x, Y[i, k], Z[i, k]) for k in range(ns)]
        pts += [(x, f * Y[i, -1], zc[i] + crown * (1 - f * f)) for f in CAP_FR]
        pts += [(x, -Y[i, k], Z[i, k]) for k in range(ns - 1, -1, -1)]
        pts += [(x, -ly, w1z), (x, -w1y, w1z), (x, -w2y, zf), (x, -0.45 * w2y, zf), (x, 0.0, zf),
                (x, 0.45 * w2y, zf), (x, w2y, zf), (x, w1y, w1z), (x, ly, w1z)]
        rings.append([part.v(p) for p in pts])
    nr = len(rings[0])
    nc = len(CAP_FR)
    for k in range(ns - 1):
        desc.append(("side", k, 1))
    for k in range(nc + 1):
        desc.append(("cap", k, 0))
    for k in range(ns - 1):
        desc.append(("side", ns - 2 - k, -1))
    desc += [("well", 2, -1), ("well", 0, -1), ("well", 1, -1), ("floor", 0, 0), ("floor", 1, 0),
             ("floor", 2, 0), ("floor", 3, 0), ("well", 1, 1), ("well", 0, 1), ("well", 2, 1)]
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
        bulge = car.bulge if sgn > 0 else (BULGE if car.has_tail else 0.0)
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
    # character lines: creases along the key section points
    crease = {S["key"][k]: c for k, c in CREASE.items() if k < len(S["key"])}
    for i in range(len(xs) - 1):
        for k in range(ns):
            c = crease.get(k, 0.0)
            for idx in (k, 2 * ns + nc - 1 - k):
                e = part.bm.edges.get((rings[i][idx], rings[i + 1][idx]))
                if e and c:
                    e[part.crease] = c
        for idx, c in ((2 * ns + nc, 1.0), (nr - 1, 1.0), (2 * ns + nc + 1, 0.7), (nr - 2, 0.7)):
            e = part.bm.edges.get((rings[i][idx], rings[i + 1][idx]))   # rolled lip, well top
            if e:
                e[part.crease] = c
    bmesh.ops.recalc_face_normals(part.bm, faces=part.bm.faces)
    for f in part.bm.faces:
        f.smooth = True
    return part, xs, S


def face_cell_panel(car, S, i, xc, kind, b, side, in_cabin, pillar, steep, ns, nc):
    body, dark = car.body_cell, car.dark_cell
    if kind in ("floor", "well"):
        return PLASTIC, P_FLOOR
    reg = car.region(xc)
    key = S["key"]
    if kind == "side":
        is_crest = car.center is not None and b >= key[4]
        if in_cabin and not pillar and xc > car.sge:
            if car.center is not None:
                glass = is_crest and min(S["Z"][i, -1] - S["Z"][i, -2],
                                         S["Z"][i + 1, -1] - S["Z"][i + 1, -2]) > 3.0
            else:
                glass = key[3] <= b < key[4]
            if glass:
                return GLASS, p_glass(10 * (side > 0) + car.seg_of(xc))
        if not is_crest:
            cell = dark if b < key[1] else body
            if reg == "bf":
                return cell, P_BUMF
            if reg == "br":
                return cell, P_BUMR
            if reg == "front":
                return cell, p_fender(side)
            if isinstance(reg, int) and b >= key[1]:
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
        side = tag == "side"          # side elevation: orthographic, 3 deg down so the ground shows
        cam.data.type = "ORTHO" if side else "PERSP"
        cam.data.ortho_scale = 1.12 * length
        cam.location = (d * math.cos(a), d * math.sin(a), ground_z + (70.0 + 0.05 * d if side else 0.42 * length))
        tgt = Vector((0, 0, ground_z + (70.0 if side else 55.0)))
        cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
        sc.render.filepath = str(path_stem.parent / f"side_{path_stem.name}.png") if side \
            else str(path_stem) + f"_{tag}.png"
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
    ends = [(+1, car.x0 - car.bulge, st["lamp_z"], st["lamp_y"])]
    if car.has_tail:
        ends.append((-1, car.x1 + BULGE, st["tail_z"], st["tail_y"]))
    for sgn, x, lz, ly in ends:
        S = sections(car, np.array([x]))
        lo, h, hw = float(S["zb"][0]), float(S["H"][0]), float(S["w"][0])
        out.append(("grille" if sgn > 0 else "valance", sgn, -0.44 * hw, 0.44 * hw, lo + 0.12 * h, lo + 0.5 * h))
        for s in (1, -1):
            y0, y1 = sorted((s * ly[0] * hw, s * ly[1] * hw))
            over = max(abs(y0), abs(y1)) - 0.8 * hw      # keep off the rounded corner
            if over > 0:
                y0, y1 = y0 - s * over, y1 - s * over
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
            key = "intake" if x < 0 else "intake_f"
            if key in PROFILE[car.name]:                     # the _real side intakes
                x, zc, sw, sh = PROFILE[car.name][key]
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


def pickup_bed(p, x0, length, z_bottom, floor_z, rail_z, w, body_cell, dark_cell, rear_axle_x, car,
               n_seg=48):
    """The stylized bed (make_car_meshes.build_pickup_bed) as solid bevelled
    parts: side walls arch-cut over the rear wheel (the same static lip as
    the cab's arches, walls outboard of the tyre), floor, a hollow wheel
    house over the full-bump tyre, tailgate and bumper."""
    x1 = x0 - length
    xs = np.linspace(x0, x1, n_seg + 1)
    arch_r = car.r_well
    bot = np.maximum(z_bottom, car.lip(xs - rear_axle_x, 1))
    wi = max(w - 5.0, car.need_y[1] - FENDER_OUT + 0.6)
    yi = min(wi * 0.62, car.ht - car.tyre_in[1] - 5.0)    # wheel-house inner wall, inboard of every setup's tyre
    side = frame_matrix(Vector((0, 0, 0)), Vector((1, 0, 0)), Vector((0, 0, 1)), Vector((0, -1, 0)))
    poly = [(float(x), float(z)) for x, z in zip(xs[::-1], bot[::-1])] + [(x0, rail_z), (x1, rail_z)]
    for s in (1, -1):
        sub = Part()
        # local z = -y: outer face at y = s*w, inner at s*wi
        prism(sub, poly, *sorted((-s * w, -s * wi)), side, body_cell,
              *((body_cell, dark_cell) if s > 0 else (dark_cell, body_cell)))
        bmesh.ops.recalc_face_normals(sub.bm, faces=sub.bm.faces)
        bmesh.ops.bevel(sub.bm, geom=list(sub.bm.edges), offset=1.6, segments=3, profile=0.5,
                        affect='EDGES', clamp_overlap=True)
        merge(p, sub)
        # wheel house: a lid over the full-bump tyre, inner wall and end
        # walls, open underneath (the floor stops at the inner wall)
        xa, xb = rear_axle_x - arch_r, rear_axle_x + arch_r
        top = BUMP_TRAVEL + car.r_bump
        rbox(p, xa, xb, *sorted((s * yi, s * wi)), top, top + 4.0, dark_cell, r=1.5)
        rbox(p, xa, xb, *sorted((s * yi, s * (yi + 3.0))), floor_z - 3.0, top + 4.0, dark_cell, r=1.0)
        for xe in (xa, xb - 3.0):
            rbox(p, xe, xe + 3.0, *sorted((s * yi, s * wi)), floor_z - 3.0, top + 4.0, dark_cell, r=1.0)
    xa, xb = rear_axle_x - arch_r, rear_axle_x + arch_r
    for fa, fb, fw in ((x1 + 1.0, xa, wi), (xa, xb, yi), (xb, x0, wi)):
        rbox(p, fa, fb, -fw, fw, floor_z - 3.0, floor_z, dark_cell, r=0.5)
    rbox(p, x1, x1 + 4.0, -w, w, z_bottom, rail_z, body_cell, r=1.6)                 # tailgate
    rbox(p, x1 - 1.0, x1 + 0.5, -12, 12, rail_z - 16, rail_z - 12, dark_cell, r=0.4)   # handle
    rbox(p, x1 - 3.0, x1 + 6.0, -w, w, z_bottom - 2.0, z_bottom + 12, PLASTIC, r=3.0)   # bumper


def build_parts(car):
    """Everything the preset adds on top of its hull, rebuilt rounded."""
    p = Part()
    st = car.style
    fins = []
    for name, a, k, _ in car.calls:
        if name == "mirrors":
            x, lwf, cell = a[:3]
            x += car.dgs                                 # stays at the A-pillar foot
            Sm = sections(car, np.array([x]))
            belt = float(Sm["belt"][0])
            y = float(Sm["Y"][0, Sm["key"][3]]) - 1.0               # on the shaped belt line,
            y = min(y, max(w for _, w in car.plan) - 18.0)   # inside the stylized width over a flared fender
            for s in (1, -1):
                ya, yb = sorted((s * (y + 6), s * (y + 20)))
                rbox(p, x - 12, x, ya, yb, belt + 3, belt + 11, cell, r=3.0)
                ya, yb = sorted((s * y, s * (y + 7)))
                rbox(p, x - 9, x - 4, ya, yb, belt + 3, belt + 6, PLASTIC, r=1.0)
                ya, yb = sorted((s * (y + 7.2), s * (y + 18.8)))
                rbox(p, x - 12.4, x - 11.6, ya, yb, belt + 4.2, belt + 9.8, CHROME, r=0.3)
        elif name == "wing":
            x, z_deck, z_wing, hs = a[:4]
            z_deck = float(sections(car, np.array([x]))["Z"][0, -1])   # on the _real deck
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
            pickup_bed(p, *a[:5], max(a[5], car.need_y[1]), *a[6:9], car, **k)
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
        if x - 40.0 < car.rx + car.r_well:      # over the rear arch: inboard of every setup's tyre
            ys = min(ys, car.ht - car.tyre_in[1] - 24.0)
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
RIM_FRAC = {"": 0.64, "Knobby": 0.55, "Stretched": 0.75, "Slick": 0.60, "Rib": 0.62, "Semislick": 0.70}
RIM_R = RIM_FRAC[""] * R_REF
SEG_KNOBBY = 144     # finer ring for crisp knobs
# garage setup -> (front, rear) tyre kind; "" = the road tyre, SM_CarWheel_real.
# Kind K is SM_CarWheel_real_K; the pawn's TyreKind() (ODVehiclePawn.cpp) keeps the same table.
SETUP_TYRES = {"Rally": ("Knobby", "Knobby"), "Offroad": ("Knobby", "Knobby"),
               "Drift": ("Stretched", "Stretched"), "Drag": ("Rib", "Slick"),
               "Track": ("Semislick", "Semislick")}


def tyre_shape(kind):
    """(rim radius, (r, y) half profile bead -> centre for y > 0, pattern) of a
    tyre kind; pattern(r, y, k) moves a vertex of ring segment k (tread
    blocks). Every kind stays inside radius R_REF / half width WHEEL_HW, so
    the arch checks (sized on those) hold for all of them."""
    R, HW = R_REF, WHEEL_HW
    rim = RIM_FRAC[kind] * R
    same = (lambda r, y, k: (r, y))
    if kind == "Knobby":        # tall sidewall, staggered blocks over a sunken base, shoulder lugs
        side = [(rim + 0.6, HW - 1.2), (rim + 3.0, HW - 0.2), (R - 7.0, HW), (R - 4.0, HW), (R - 1.4, HW - 0.4)]
        tread = [(R - 2.2, y) for y in np.linspace(HW - 1.2, 0.4, 10)]

        def pat(r, y, k):       # k of SEG_KNOBBY: 4-segment blocks, steep walls (sharp at shade()'s 40 deg)
            if r < R - 2.1:
                return (r, y) if r < R - 7.5 or (k // 4) % 2 == 0 else (r - 1.4, y - math.copysign(1.0, y))
            return (R, y) if (k // 4 + int((y + HW) // 3.2)) % 2 == 0 else (r, y)
        return rim, side + tread, pat
    if kind == "Stretched":     # low profile on a wide rim: sidewall slants in from the rim lip
        side = [(rim + 0.6, HW - 0.8), (rim + 1.8, HW - 0.6), (R - 4.0, HW - 2.6), (R - 1.6, HW - 3.6),
                (R - 0.3, HW - 4.6)]
        tread = [(R, HW - 5.6), (R, 3.0), (R - 0.8, 2.8), (R - 0.8, 1.8), (R, 1.6)]
        return rim, side + tread, same
    if kind == "Slick":         # smooth, square shoulders
        side = [(rim + 0.6, HW - 1.2), (rim + 3.0, HW - 0.2), (R - 6.0, HW), (R - 1.6, HW - 0.1), (R - 0.3, HW - 0.9)]
        return rim, side + [(R, HW - 1.8), (R, 6.0), (R, 0.6)], same
    if kind == "Rib":           # skinny front-runner: round shoulders, narrow circumferential ribs
        side = [(rim + 0.6, HW - 1.2), (rim + 3.0, HW - 0.2), (R - 8.0, HW), (R - 4.0, HW - 1.0), (R - 1.6, HW - 2.8),
                (R - 0.3, HW - 4.4)]
        tread = [(R, 8.2), (R, 6.7), (R - 1.0, 6.6), (R - 1.0, 6.1), (R, 6.0), (R, 2.7), (R - 1.0, 2.6),
                 (R - 1.0, 2.1), (R, 2.0), (R, 0.4)]
        return rim, side + tread, same
    if kind == "Semislick":     # square shoulders, two grooves, low profile
        side = [(rim + 0.6, HW - 1.0), (rim + 2.4, HW - 0.2), (R - 4.0, HW), (R - 1.0, HW - 0.3), (R - 0.2, HW - 1.2)]
        tread = [(R, HW - 1.8), (R, 5.8), (R - 1.0, 5.6), (R - 1.0, 4.4), (R, 4.2), (R, 0.6)]
        return rim, side + tread, same
    # road: grooved tread, lateral notches in the shoulder blocks every other segment
    side = [(rim + 0.6, HW - 1.2), (rim + 3.0, HW - 0.2), (R - 8.0, HW), (R - 3.5, HW - 0.6),
            (R - 1.2, HW - 2.2), (R - 0.2, HW - 4.0)]
    tread = [(R, 9.6), (R, 8.6), (R - 0.9, 8.4), (R - 0.9, 7.2), (R, 7.0), (R, 2.8), (R - 0.9, 2.6),
             (R - 0.9, 1.4), (R, 1.2)]
    return rim, side + tread, (lambda r, y, k: (r - 0.7 if abs(y) > 9.0 and r > R - 4.0 and k % 2 else r, y))


def build_wheel(kind=""):
    """Realistic wheel at R_REF, axle on local Y, symmetric about y = 0 (the
    pawn mounts it unmirrored on both sides): tyre of the given kind
    (tyre_shape), a six-spoke dished alloy sized to its rim on both faces,
    barrel, lug nuts / centre cap and a brake disc on y = 0."""
    p = Part()
    SEG = SEG_KNOBBY if kind == "Knobby" else 96
    rim, half, pat = tyre_shape(kind)
    prof = half + [(r, -y) for r, y in half[::-1]]
    rings = []
    for i, (r, y) in enumerate(prof):
        ring = []
        for k in range(SEG):
            t = 2 * math.pi * k / SEG
            rr, yy = pat(r, y, k)
            ring.append(p.v((rr * math.cos(t), yy, rr * math.sin(t))))
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
    HW = WHEEL_HW
    barrel = [(rim + 1.4, HW - 0.6), (rim + 1.4, HW - 1.6), (rim - 0.2, HW - 2.0), (rim - 1.2, 6.0),
              (rim - 1.2, -6.0), (rim - 0.2, -(HW - 2.0)), (rim + 1.4, -(HW - 1.6)), (rim + 1.4, -(HW - 0.6)),
              (rim - 1.6, -(HW - 0.6)), (rim - 2.4, -6.0), (rim - 2.4, 6.0), (rim - 1.6, HW - 0.6)]
    revolve(p, barrel, SEG, lambda i: RIM, closed=True)
    # --- spoke faces on both sides
    for s in (1, -1):
        spoke_face(p, s, r_out=rim - 1.4)
    # --- brake disc + hat on the centre plane (inside the smallest rim)
    rd = min(0.47 * R_REF, rim - 3.0)
    cylinder(p, (0, -1.3, 0), (0, 1.3, 0), rd, rd, 64, RIM)
    cylinder(p, (0, -3.0, 0), (0, 3.0, 0), 0.2 * R_REF, 0.2 * R_REF, 32, ACCENT)
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
                             apply_scale_options="FBX_SCALE_UNITS", mesh_smooth_type="FACE",
                             use_mesh_modifiers=False, add_leaf_bones=False, bake_anim=False,
                             colors_type="NONE", use_tspace=False)


def make_tiers(src, base_name, tiers=TIER_RATIO):
    """Decimate the full mesh into the tiers {suffix: ratio}, fix up
    UVs/normals, export each; returns ({tier name: triangles}, the _ultra
    object or None)."""
    out, ultra = {}, None
    for suffix, ratio in tiers.items():
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
    car = Car(name)
    tris, ultra = {}, None
    for subdiv, tiers in CAR_TIERS.items():
        body = build_body(car, subdiv)
        t, u = make_tiers(body, f"SM_Car_{name}_real", tiers)
        tris.update(t)
        ultra = ultra or u
        bpy.data.objects.remove(body)
    return car, tris, ultra


def build_body(car, subdiv):
    """The joined car (body, lamp internals, parts, interior) on the cage
    subdivided `subdiv` times."""
    t0 = time.time()
    name = car.name
    part, xs, S = build_cage(car)
    body = to_object(part, f"SM_Car_{name}_src")
    m = body.modifiers.new("ss", "SUBSURF")
    m.levels = m.render_levels = subdiv
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
    log(name, "built", len(xs), "stations", gaps, "gap edges", f"subdiv {subdiv}", f"{time.time() - t0:.1f}s")
    return body


def arch_clearance(car, ob):
    """Measured on the finished mesh, per axle and garage setup: the visible
    lip gap above the tyre at static ride height (horizontal rays from
    outside find the lip's underside over the axle), and the smallest body
    clearance around the tyre (rays from the wheel centre through tread /
    shoulder points; < 0 = intersection) at static, at the bump stop and, for
    the front, both at full steering lock both ways."""
    bm = bm_of(ob)
    bvh = BVHTree.FromBMesh(bm)
    bm.free()
    th = np.radians(np.linspace(-100.0, 100.0, 41))       # 0 = top of the tyre

    def clear(ax, zc, steer, R, hw, camber, tag):
        c, sn = math.cos(math.radians(steer)), math.sin(math.radians(steer))
        cc, sc = math.cos(math.radians(camber)), math.sin(math.radians(camber))
        best = 1e9
        for side in (1, -1):
            C = Vector((ax, side * car.ht, zc))
            for t in th:
                for u in np.linspace(-hw, hw, 9):
                    px, py, pz = R * math.sin(t), u, R * math.cos(t)
                    py, pz = py * cc - pz * sc, py * sc + pz * cc        # top leans in
                    d = Vector((px * c - py * sn, side * (px * sn + py * c), pz))
                    n = d.length
                    hit, _, fi, _ = bvh.ray_cast(C, d / n, n + 40.0)
                    if hit is not None and (hit - C).length - n < best:
                        best = (hit - C).length - n
                        worst = (round(hit.x - ax, 1), round(abs(hit.y), 1), round(hit.z, 1),
                                 ob.data.materials[ob.data.polygons[fi].material_index].name)
        if best < 1.0:
            log(car.name, tag, "arch closest", round(best, 2), "at dx/|y|/z", worst, "wheel z", round(zc, 1),
                "steer", round(steer, 1))
        return round(best, 2)

    out = {}
    for end, ax in (("front", car.fx), ("rear", car.rx)):
        out[end] = {}
        for name, g in SETUPS.items():
            R, hw, zs, zb, lock = setup_tyre(car, g, end == "front")
            cb, tag = g["camber_deg"], f"{name} {end}"
            top, lip = zs + R, None
            for z in np.arange(top, top + 30.0, 0.1):
                hit = bvh.ray_cast(Vector((ax, 500.0, z)), Vector((0, -1, 0)), 600.0)[0]
                if hit is not None and hit.y > car.ht + hw - 1.0:
                    lip = round(float(z - top), 2)
                    break
            r = dict(lip_gap=lip, static=clear(ax, zs, 0.0, R, hw, cb, tag), bump=clear(ax, zb, 0.0, R, hw, cb, tag))
            if lock:
                r["lock"] = min(clear(ax, zs, s * lock, R, hw, cb, tag) for s in (1, -1))
                r["bump_lock"] = min(clear(ax, zb, s * lock, R, hw, cb, tag) for s in (1, -1))
            out[end][name] = r
    log(car.name, "arches", json.dumps(out))
    return out


def place_wheels(car, wheel):
    """At the in-game static wheel centres (what the pawn shows at rest)."""
    obs = []
    sc = car.wr / R_REF
    for x, z in zip((car.fx, car.rx), car.zs):
        for s in (1, -1):
            o = bpy.data.objects.new("w", wheel.data)
            o.location = (x, s * car.ht, z)
            o.scale = (sc, sc, sc)
            bpy.context.scene.collection.objects.link(o)
            obs.append(o)
    return obs


def setup_wheels(car, setup, g, wheels):
    """A garage setup's tyres as the pawn shows them at rest: its kind per
    axle, centre at -RideHeightDelta, scaled by diameter / width, camber
    leaning the top inboard."""
    obs = []
    kinds = SETUP_TYRES.get(setup, ("", ""))
    for a, x in enumerate((car.fx, car.rx)):
        R, hw, zs, _, _ = setup_tyre(car, g, a == 0)
        S = R / R_REF
        for s in (1, -1):
            o = bpy.data.objects.new("w", wheels[kinds[a]].data)
            o.location = (x, s * car.ht, zs)
            o.scale = (S, hw / WHEEL_HW, S)
            o.rotation_euler = (math.radians(s * g["camber_deg"]), 0.0, 0.0)
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
    wheels, wt = {}, {}
    for kind in RIM_FRAC:
        wsrc = to_object(build_wheel(kind), "SM_CarWheel_src")
        t, wheels[kind] = make_tiers(wsrc, "SM_CarWheel_real" + (f"_{kind}" if kind else ""))
        wt.update(t)
        bpy.data.objects.remove(wsrc)
    wheel = wheels[""]
    manifest["wheel"] = dict(tris=wt, RefRadiusCm=R_REF, HalfWidthCm=WHEEL_HW, RimRadiusCm=RIM_R,
                             Kinds={k or "Road": round(f * R_REF, 1) for k, f in RIM_FRAC.items()},
                             SetupTyres=SETUP_TYRES)
    if DO_PREVIEW:      # the tyre line-up, 3/4 view
        PREVIEW.mkdir(parents=True, exist_ok=True)
        for i, w in enumerate(wheels.values()):
            w.location = ((2.5 - i) * 85.0, 0.0, 0.0)
        render_views(list(wheels.values()), -R_REF, 340.0, PREVIEW / "tyres", views=(("lineup", 65.0), ("tread", 25.0)))
        for w in wheels.values():
            w.location = (0.0, 0.0, 0.0)
    for name in cars:
        car, tris, ultra = build_car(name)
        manifest["cars"][name] = dict(tris=tris, arches=arch_clearance(car, ultra),
                                      FrontAxleXCm=car.fx, RearAxleXCm=car.rx,
                                      HalfTrackYCm=car.ht, WheelRadiusCm=car.wr,
                                      FenderYCm=[round(v, 1) for v in car.need_y], Setups=list(SETUPS),
                                      bbox=[[round(min((ultra.matrix_world @ Vector(c))[i] for c in ultra.bound_box), 2)
                                             for i in range(3)],
                                            [round(max((ultra.matrix_world @ Vector(c))[i] for c in ultra.bound_box), 2)
                                             for i in range(3)]])
        if DO_PREVIEW:
            PREVIEW.mkdir(parents=True, exist_ok=True)
            ws = place_wheels(car, wheel)
            length = ultra.dimensions.x     # the bed / wing count too
            gz = 0.5 * sum(car.zs) - car.wr      # the in-game ground at rest
            render_views([ultra] + ws, gz, length, PREVIEW / f"SM_Car_{name}_real")
            render_views([ultra] + ws, gz, length, PREVIEW / name, views=(("side", 90.0),))
            if True:   # old-vs-new for every car
                old = stylized_object(f"SM_Car_{name}")
                old.location.y = -2.6 * car.ht
                ultra.location.y = 1.3 * car.ht
                for w in ws:
                    w.location.y += 1.3 * car.ht
                ws_old = place_wheels(car, wheel)
                for w in ws_old:
                    w.location.y -= 2.6 * car.ht
                render_views([ultra, old] + ws + ws_old, gz, 1.5 * length,
                             PREVIEW / f"compare_{name}", views=(("old_vs_new", 60.0),))
                bpy.data.objects.remove(old)
                for w in ws_old:
                    bpy.data.objects.remove(w)
                ultra.location.y = 0.0
            for w in ws:
                bpy.data.objects.remove(w)
            for s, g in SETUPS.items():     # side view per garage setup: its tyres, ride height, camber
                ws = setup_wheels(car, s, g, wheels)
                render_views([ultra] + ws, -g["ride_height_delta"] - car.wr * g["tire_diameter_scale"], length,
                             PREVIEW / f"{name}_{s}", views=(("side", 90.0),))
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


