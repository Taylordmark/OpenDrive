"""Procedural low-poly car body generator. Seven badge-free archetypes:

    MUSCLE - long-hood pony coupe, hood scoop, fastback, duck-lip tail.
    JDM    - 90s fastback sport coupe with a hoop wing.
    SUPER  - mid-engine wedge supercar, side intakes, big wing.
    PICKUP - off-road crew-cab pickup with an open bed (welded on, not lofted).
    RALLY  - compact rally hatch, tall greenhouse, roof scoop, aux lamps.
    CONCEPT- Le Mans-prototype road concept (Furai x GT40): body wrapped
             around the wheels, teardrop canopy, shark fin, wing.
    LMP    - Le Mans prototype racer: near-ground nose between tall pods,
             low sidepods, narrow bubble cockpit, long fin, wide wing.

Construction (2026-09-25 rebuild, see build_hull()): each body is one
continuous loft from an authored side profile (bottom + roofline), a plan
width curve and a beltline height. A 5-point cross-section gives rocker,
flank, shoulder crease and a wide roof with real tumblehome; the greenhouse
is the band above the beltline inside an explicit glass x-range, with
windshield/backlight glass framed by body-colored pillars. Wheel arches are
cut by lifting the bottom line onto a circle around each axle.

Geometry contract with AODVehiclePawn / UODVehicleMovement: OBJ pivot at
wheelbase center at WHEEL AXLE height z=0, centimeters, +x forward. Per-car
axle x / half-track / wheel radius are physics values also hardcoded in
unreal/import_car_meshes.py - keep them in sync. The shared wheel mesh is
built once at REF_WHEEL_RADIUS_CM and rescaled per car by the pawn.

Mesh.save() groups triangles by material CATEGORY (body/plastic/glass/
chrome/tire/rim/lights) into OBJ usemtl groups + .mtl, one UE material slot
per category (see unreal/import_car_meshes.py).

Outputs (data/cars/procedural/):
    obj/SM_Car_{Muscle,JDM,Super,Pickup,Rally,Concept,LMP}.obj (+ .mtl)
    obj/SM_CarWheel.obj (+ .mtl)
    obj/cars_manifest.json   (axle/track/wheel data, read by preview_cars.py)
    textures/T_CarPalette2.png

Live preview: python pipeline/preview_cars.py
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "data" / "cars" / "procedural"
OBJ_OUT = OUT / "obj"
TEX = OUT / "textures"
OBJ_OUT.mkdir(parents=True, exist_ok=True)
TEX.mkdir(parents=True, exist_ok=True)

REF_WHEEL_RADIUS_CM = 34.0

# Graphics menu "Asset Detail" tiers: every mesh is also written as
# <name>_high / <name>_ultra. suffix -> (loft station spacing cm, hull
# cross-section subdivisions, wheel segments, smooth normals). Standard must
# stay (8.0, 1, 20, False) - its OBJs are byte-identical to pre-tier output.
TIERS = {"": (8.0, 1, 20, False), "_high": (4.0, 3, 40, True), "_ultra": (2.5, 4, 60, True)}
SPACING, SECTION_SUB, WHEEL_SEG, SMOOTH = TIERS[""]
SMOOTH_COS = math.cos(math.radians(38))   # auto-smooth crease angle

# ---------------------------------------------------------------- palette --
PALETTE = [
    (0.55, 0.05, 0.04),   # 0  muscle body - RED (continuity: today's placeholder car is red)
    (0.27, 0.03, 0.02),   # 1  muscle dark (lower panel / rocker)
    (0.09, 0.20, 0.40),   # 2  jdm body - steel blue
    (0.045, 0.10, 0.20),  # 3  jdm dark
    (0.85, 0.82, 0.05),   # 4  super body - supercar yellow
    (0.42, 0.40, 0.03),   # 5  super dark
    (0.13, 0.13, 0.15),   # 6  pickup body - gunmetal grey
    (0.06, 0.06, 0.07),   # 7  pickup dark
    (0.21, 0.06, 0.27),   # 8  rally body - deep purple (rally-flavored)
    (0.085, 0.03, 0.11),  # 9  rally dark
    (0.17, 0.25, 0.34),   # 10 glass - cool blue-grey; near-black (0.05,0.08,0.11)
                           #    made every greenhouse read as a black void in-game
    (0.04, 0.04, 0.05),   # 11 plastic - black bumper/rocker/trim/wheel-arch inset
    (0.03, 0.03, 0.035),  # 12 tire
    (0.50, 0.50, 0.53),   # 13 rim/hub
    (0.93, 0.85, 0.60),   # 14 headlight
    (0.62, 0.05, 0.03),   # 15 taillight
    (0.16, 0.16, 0.18),   # 16 accent - wing/fin/diffuser
    (0.78, 0.79, 0.81),   # 17 chrome - grille/exhaust/brightwork/brush-guard
    (0.20, 0.21, 0.23),   # 18 concept body - gunmetal
    (0.04, 0.04, 0.045),  # 19 concept dark
    (0.85, 0.30, 0.02),   # 20 lmp body - Le Mans orange
    (0.03, 0.03, 0.035),  # 21 lmp dark
    (0.90, 0.92, 0.95),   # 22 reverse lamp - the only lights cell with blue > 0.75
                           #    (M_CarLights tells head/tail/reverse apart by colour)
    (1.00, 0.45, 0.02),   # 23 indicator - amber, the only lights cell with blue < 0.2 and
                          #    green > 0.25; left/right by the sign of local Y
]
N_CELL = len(PALETTE)
GLASS, PLASTIC, TIRE, RIM, HEADLIGHT, TAILLIGHT, ACCENT, CHROME = 10, 11, 12, 13, 14, 15, 16, 17
REVERSE, AMBER = 22, 23
TRIM, HUB = PLASTIC, RIM   # backward-compat aliases

# ---------------------------------------------------------- material slots
CATEGORY_OF = {
    0: "body", 1: "plastic", 2: "body", 3: "plastic", 4: "body", 5: "plastic",
    6: "body", 7: "plastic", 8: "body", 9: "plastic", 18: "body", 19: "plastic", 20: "body", 21: "plastic",
    GLASS: "glass", PLASTIC: "plastic", TIRE: "tire", RIM: "rim",
    HEADLIGHT: "lights", TAILLIGHT: "lights", ACCENT: "plastic", CHROME: "chrome",
    REVERSE: "lights", AMBER: "lights",
}
CATEGORY_ORDER = ["body", "plastic", "glass", "chrome", "tire", "rim", "lights"]
CATEGORY_KD = {
    "body": (0.5, 0.5, 0.5), "plastic": (0.05, 0.05, 0.05),
    "glass": (0.17, 0.25, 0.34), "chrome": (0.78, 0.79, 0.81),
    "tire": (0.03, 0.03, 0.035), "rim": (0.5, 0.5, 0.53),
    "lights": (0.8, 0.6, 0.3),
}


def write_palette():
    cell = 32
    img = np.zeros((cell, cell * N_CELL, 3), dtype=np.float32)
    for i, c in enumerate(PALETTE):
        img[:, i * cell:(i + 1) * cell] = c
    Image.fromarray((np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)) \
        .save(TEX / "T_CarPalette2.png")
    print("wrote T_CarPalette2.png")


def cell_uv(i):
    return ((i + 0.5) / N_CELL, 0.5)


class Mesh:
    """Faceted-solid Mesh class - flat per-face normals, one UV per face
    into the shared palette strip - grouping triangles by material CATEGORY
    on save() so the OBJ carries real multi-material-slot structure (see
    module docstring's "material-slot split" note)."""

    def __init__(self):
        self.tris = []   # (a, b, c, cell)

    def tri(self, a, b, c, cell):
        self.tris.append((np.array(a, float), np.array(b, float),
                          np.array(c, float), cell))

    def quad(self, a, b, c, d, cell):
        self.tri(a, b, c, cell)
        self.tri(a, c, d, cell)

    def save(self, name):
        by_cat = {cat: [] for cat in CATEGORY_ORDER}
        for t in self.tris:
            cat = CATEGORY_OF[t[3]]
            by_cat[cat].append(t)

        name += SUFFIX
        smooth = {}
        if SMOOTH:
            # auto-smooth: a corner's normal averages every face at that
            # vertex within SMOOTH_COS of it (sign-aligned - the y-negation
            # leaves hull winding mixed), same category only so paint/glass/
            # trim seams stay crisp.
            for (a, b, c, cell) in self.tris:
                n = np.cross(b - a, c - a)
                ln = np.linalg.norm(n)
                if ln > 1e-6:
                    for q in (a, b, c):
                        smooth.setdefault((CATEGORY_OF[cell], *np.round(q, 1)), []).append(n / ln)

        lines, f_lines, vi = [], [], 1
        lines.append(f"mtllib {name}.mtl")
        for cat in CATEGORY_ORDER:
            group = by_cat[cat]
            if not group:
                continue
            f_lines.append(f"usemtl {cat}")
            for (a, b, c, cell) in group:
                n = np.cross(b - a, c - a)
                ln = np.linalg.norm(n)
                n = n / ln if ln > 1e-6 else np.array([0.0, 0.0, 1.0])
                u, v = cell_uv(cell)
                for p in (a, b, c):
                    pn = n
                    if smooth:
                        acc = np.zeros(3)
                        for o in smooth.get((cat, *np.round(p, 1)), ()):
                            d = float(np.dot(o, n))
                            if abs(d) >= SMOOTH_COS:
                                acc += o if d > 0 else -o
                        la = np.linalg.norm(acc)
                        pn = acc / la if la > 1e-6 else n
                    lines.append(f"v {p[0]:.1f} {-p[1]:.1f} {p[2]:.1f}")
                    lines.append(f"vt {u:.4f} {v:.4f}")
                    lines.append(f"vn {pn[0]:.3f} {-pn[1]:.3f} {pn[2]:.3f}")
                f_lines.append(f"f {vi}/{vi}/{vi} {vi + 1}/{vi + 1}/{vi + 1} "
                               f"{vi + 2}/{vi + 2}/{vi + 2}")
                vi += 3
        (OBJ_OUT / f"{name}.obj").write_text("\n".join(lines + f_lines) + "\n")

        mtl_lines = []
        for cat in CATEGORY_ORDER:
            if not by_cat[cat]:
                continue
            kd = CATEGORY_KD[cat]
            mtl_lines += [
                f"newmtl {cat}",
                f"Kd {kd[0]:.3f} {kd[1]:.3f} {kd[2]:.3f}",
                "Ka 0.0 0.0 0.0",
                "Ks 0.1 0.1 0.1",
                "map_Kd T_CarPalette2.png",
                "",
            ]
        (OBJ_OUT / f"{name}.mtl").write_text("\n".join(mtl_lines))

        nv = len(self.tris) * 3
        used_cats = [c for c in CATEGORY_ORDER if by_cat[c]]
        print(f"wrote {name}.obj ({len(self.tris)} tris, {nv} verts, "
              f"{len(used_cats)} material slots: {', '.join(used_cats)})")
        return len(self.tris)
# ============================================================== body builder
STYLE_DEFAULTS = dict(
    resample_spacing=8.0,     # cm between loft stations
    belt_z=55.0,              # beltline (shoulder crease) height above axle
    arch_margin_cm=5.0,       # wheel-arch radius = wheel_r + this
    glass_x_start=None,       # windshield base (cowl) x - required
    glass_x_end=None,         # backlight base x - required
    pillars_x=(),             # body-colored B/C pillars splitting the side glass
    hood_top_frac=0.80,       # top-cap half-width / body half-width on hood & deck
    roof_top_frac=0.76,       # same on the roof (side-glass tumblehome)
    steep_slope=0.25,         # |dz/dx| above which a cabin top panel is glass
    lamp_z=(0.55, 0.85),      # headlight band, fraction of the nose's rocker->belt height
    lamp_y=(0.50, 0.90),      # headlight span, fraction of the nose half-width
    tail_z=(0.55, 0.85),      # None -> no tail lamps/valance on the hull
    tail_y=(0.50, 0.92),
    grille_cell=PLASTIC,
)


def style_for(overrides):
    return {**STYLE_DEFAULTS, **overrides}


def box(m, x0, x1, y0, y1, z0, z1, cell):
    """Axis-aligned closed box (mirrors, scoops, wings, exhausts, light bars)."""
    p = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    for a, b, c, d in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                       (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        m.quad(p[a], p[b], p[c], p[d], cell)


def vpanel(m, x, y0, y1, z0, z1, cell):
    """Flat panel facing +/-x (lamps, grille, tailgate details)."""
    m.quad((x, y0, z0), (x, y1, z0), (x, y1, z1), (x, y0, z1), cell)


def build_hull(m, profile, plan, body_cell, dark_cell, style,
               front_axle_x, rear_axle_x, wheel_r, center=None):
    """One continuous loft, nose -> tail, from three authored curves:

    profile: [(x, z_bottom, z_top), ...] - side silhouette. z_top is the
        whole roofline (bumper -> hood -> windshield -> roof -> backlight ->
        deck), so the greenhouse is part of the same surface.
    plan:    [(x, half_width), ...] - top-down width, fender flares included.
    style["belt_z"] - beltline; everything above it inside the glass
        x-range is greenhouse.

    Cross-section per station (half side, y >= 0), H = belt - bottom:
        S0 (0.80w, bottom)       underside edge (floor pan between the S0s)
        S1 (0.97w, bottom+0.3H)  rocker        - dark
        S2 (1.00w, bottom+0.75H) widest point  - body
        S3 (0.97w, belt)         beltline      - body
        S4 (top_w, z_top)        hood/deck edge (body) or roof edge (side glass)
    The top cap between the S4s is body, except steep cabin panels
    (windshield/backlight) which are glass framed by body-colored pillars.
    Wheel arches: the bottom line is lifted onto a circle of radius
    wheel_r + arch_margin around each axle, giving a real opening.

    center: optional [(x, z, half_width), ...] - a separate centerline
        (hood valley / canopy / deck). z_top then becomes the fender CREST
        at top_w, and one more band runs crest -> centerline: a low nose and
        tail between raised wheel pods, and a free-standing canopy in the
        cabin (glass where it rises above the crest). Top cap sits at the
        centerline.

    Returns local_width(x) with a .z(x) -> (bottom, belt, top) attribute
    (top = centerline height).
    """
    px, pzb, pzt = (np.array(c) for c in zip(*sorted(profile)))
    wx, wh = (np.array(c) for c in zip(*sorted(plan)))
    n = max(8, int(round((px[-1] - px[0]) / min(style["resample_spacing"], SPACING))))
    xs = np.linspace(px[-1], px[0], n + 1)          # nose -> tail
    zb = np.interp(xs, px, pzb)
    zt = np.interp(xs, px, pzt)
    w = np.interp(xs, wx, wh)

    zb_floor = zb.copy()                             # un-arched floor line
    r = wheel_r + style["arch_margin_cm"]
    for ax in (front_axle_x, rear_axle_x):
        d2 = r * r - (xs - ax) ** 2
        zb = np.where(d2 > 0, np.maximum(zb, np.sqrt(np.maximum(d2, 0.0))), zb)
    belt = np.minimum(style["belt_z"], zt - 4.0)
    zb = np.minimum(zb, belt - 8.0)                  # min fender thickness over the arch
    for ax in (front_axle_x, rear_axle_x):
        if xs[-1] <= ax <= xs[0]:
            top = np.interp(ax, xs[::-1], zb[::-1])
            assert top > wheel_r, f"arch at x={ax} is {top:.0f} cm, inside the {wheel_r} cm wheel"

    H = belt - zb
    cabin = (xs <= style["glass_x_start"]) & (xs >= style["glass_x_end"])
    top_w = w * np.where(cabin, style["roof_top_frac"], style["hood_top_frac"])
    Y = [0.80 * w, 0.97 * w, w, 0.97 * w, top_w]
    Z = [zb, zb + 0.3 * H, zb + 0.75 * H, belt, zt]
    if center:
        cx, cz, cw = (np.array(c) for c in zip(*sorted(center)))
        Y.append(np.interp(xs, cx, cw))
        Z.append(np.interp(xs, cx, cz))
    Y, Z = np.stack(Y, axis=1), np.stack(Z, axis=1)
    band = list(range(Y.shape[1] - 1))   # section segment -> authored band
    if SECTION_SUB > 1:
        # higher tiers: round the cross-section - uniform Catmull-Rom through
        # the authored points, SECTION_SUB segments per band (same end points,
        # so the caps/floor/top attach where they always did)
        Kp = Y.shape[1]
        Pt = np.stack([Y, Z], axis=2)                        # (stations, K, 2)
        rows, band = [], []
        for k in range(Kp - 1):
            p0, p1 = Pt[:, max(k - 1, 0)], Pt[:, k]
            p2, p3 = Pt[:, k + 1], Pt[:, min(k + 2, Kp - 1)]
            for j in range(SECTION_SUB):
                t = j / SECTION_SUB
                rows.append(0.5 * (2 * p1 + (p2 - p0) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t
                                   + (3 * p1 - p0 - 3 * p2 + p3) * t ** 3))
                band.append(k)
        rows.append(Pt[:, -1])
        Y, Z = np.stack([r[:, 0] for r in rows], axis=1), np.stack([r[:, 1] for r in rows], axis=1)
    K = Y.shape[1]          # section points; top cap sits on the last one
    zc = Z[:, -1]

    def local_width(x):
        return float(np.interp(x, xs[::-1], w[::-1]))
    local_width.z = lambda x: tuple(float(np.interp(x, xs[::-1], a[::-1])) for a in (zb, belt, zc))

    for i in range(n):
        x0, x1 = xs[i], xs[i + 1]
        in_cabin = cabin[i] and cabin[i + 1]
        pillar = any(abs((x0 + x1) / 2 - p) < 5.0 for p in style["pillars_x"])
        glass = in_cabin and not pillar
        if center:   # glass only where the canopy stands above the crest
            glass = glass and min(zc[i] - zt[i], zc[i + 1] - zt[i + 1]) > 3.0
            cells = [dark_cell, body_cell, body_cell, body_cell, GLASS if glass else body_cell]
        else:
            cells = [dark_cell, body_cell, body_cell, GLASS if glass else body_cell]
        for k, bk in enumerate(band):
            cell = cells[bk]
            for s in (1, -1):
                q = [(x0, s * Y[i, k], Z[i, k]), (x1, s * Y[i + 1, k], Z[i + 1, k]),
                     (x1, s * Y[i + 1, k + 1], Z[i + 1, k + 1]), (x0, s * Y[i, k + 1], Z[i, k + 1])]
                m.quad(*(q if s > 0 else q[::-1]), cell)
        # top cap: roof/hood/deck, or pillar | glass | pillar on steep cabin panels
        steep = abs(zc[i] - zc[i + 1]) / (x0 - x1) > style["steep_slope"]
        edges = [-1.0, -0.82, 0.82, 1.0] if in_cabin and steep else [-1.0, 1.0]
        for j in range(len(edges) - 1):
            e0, e1 = edges[j], edges[j + 1]
            cell = GLASS if len(edges) == 4 and j == 1 else body_cell
            m.quad((x0, e0 * Y[i, -1], zc[i]), (x1, e0 * Y[i + 1, -1], zc[i + 1]),
                   (x1, e1 * Y[i + 1, -1], zc[i + 1]), (x0, e1 * Y[i, -1], zc[i]), cell)
        if center:   # low centerline: arch-height pan would poke through it, so
            # slope from the arch edge down to the floor (wheel-well liners)
            fy0, fy1 = 0.72 * w[i], 0.72 * w[i + 1]
            for s in (1, -1):
                q = [(x0, s * fy0, zb_floor[i]), (x1, s * fy1, zb_floor[i + 1]),
                     (x1, s * Y[i + 1, 0], zb[i + 1]), (x0, s * Y[i, 0], zb[i])]
                m.quad(*(q if s > 0 else q[::-1]), dark_cell)
            m.quad((x0, fy0, zb_floor[i]), (x1, fy1, zb_floor[i + 1]),
                   (x1, -fy1, zb_floor[i + 1]), (x0, -fy0, zb_floor[i]), dark_cell)
        else:
            m.quad((x0, Y[i, 0], zb[i]), (x1, Y[i + 1, 0], zb[i + 1]),
                   (x1, -Y[i + 1, 0], zb[i + 1]), (x0, -Y[i, 0], zb[i]), dark_cell)   # floor pan

    # end caps: fan-fill the section polygon, then lamps/grille proud of it
    for i, sgn in ((0, 1.0), (n, -1.0)):
        pts = [(Y[i, k], Z[i, k]) for k in range(K)] + [(-Y[i, k], Z[i, k]) for k in range(K - 1, -1, -1)]
        cy, cz = 0.0, float(np.mean([p[1] for p in pts]))
        for a, b in zip(pts, pts[1:] + pts[:1]):
            m.tri((xs[i], cy, cz), (xs[i], *a), (xs[i], *b), body_cell)
        x = xs[i] + sgn * 0.6
        lo, h, hw = zb[i], H[i], w[i]
        lz, ly, lamp = (style["lamp_z"], style["lamp_y"], HEADLIGHT) if sgn > 0 else \
                       (style["tail_z"], style["tail_y"], TAILLIGHT)
        if lz is None:      # e.g. pickup cab back: lamps live on the bed instead
            continue
        for s in (1, -1):   # dark surround so lamps read on any paint color
            vpanel(m, x - sgn * 0.3, s * (ly[0] * hw - 2), s * (ly[1] * hw + 2),
                   lo + lz[0] * h - 2, lo + lz[1] * h + 2, PLASTIC)
            y0, y1, z0, z1 = ly[0] * hw, ly[1] * hw, lo + lz[0] * h, lo + lz[1] * h
            if sgn < 0:     # inboard 30% of each tail lamp is the reverse lamp
                yr = y0 + 0.3 * (y1 - y0)
                vpanel(m, x, s * y0, s * yr, z0, z1, REVERSE)
                y0 = yr
            ya = y1 - 0.2 * (ly[1] - ly[0]) * hw   # outboard 20% is the indicator
            vpanel(m, x, s * ya, s * y1, z0, z1, AMBER)
            vpanel(m, x, s * y0, s * ya, z0, z1, lamp)
        center = style["grille_cell"] if sgn > 0 else PLASTIC
        vpanel(m, x, -0.44 * hw, 0.44 * hw, lo + 0.12 * h, lo + 0.50 * h, center)

    return local_width


def cabin_blocker(m, glass_x_start, glass_x_end, local_width_fn, belt_z,
                  cell=PLASTIC, inset_frac=0.82, drop_cm=8.0, n=6):
    """Flat dark panel just under the beltline across the greenhouse x-range
    so the hollow shell isn't visible through the glass (reads as a dash/
    cabin floor)."""
    z = belt_z - drop_cm
    xs = np.linspace(glass_x_start, glass_x_end, n + 1)
    for i in range(n):
        x0, x1 = float(xs[i]), float(xs[i + 1])
        w0 = local_width_fn(x0) * inset_frac
        w1 = local_width_fn(x1) * inset_frac
        m.quad((x0, -w0, z), (x1, -w1, z), (x1, w1, z), (x0, w0, z), cell)


def mirrors(m, x, local_width_fn, cell):
    """Door mirrors just aft of the A-pillar foot, sitting on the beltline."""
    _, belt, _ = local_width_fn.z(x)
    y = local_width_fn(x) * 0.88    # root buried in the door so it doesn't float
    for s in (1, -1):
        box(m, x - 12, x, s * y, s * (y + 20), belt + 3, belt + 11, cell)


def wing(m, x, z_deck, z_wing, half_span, chord=24.0, cell=ACCENT, plate_cell=None):
    """Rear wing: blade on two uprights."""
    for s in (1, -1):
        box(m, x - 4, x + 4, s * half_span * 0.55 - 2, s * half_span * 0.55 + 2, z_deck - 2, z_wing, cell)
    box(m, x - chord / 2, x + chord / 2, -half_span, half_span, z_wing, z_wing + 3, cell)
    for s in (1, -1):   # end plates
        box(m, x - chord / 2 - 2, x + chord / 2, s * half_span - s * 1.5, s * half_span,
            z_wing - 6, z_wing + 8, plate_cell or cell)


def exhaust_tips(m, x, y, z, cell=CHROME, r=4.0, length=10.0):
    for s in (1, -1):
        box(m, x - length, x, s * y - r, s * y + r, z - r, z + r, cell)


def side_patch(m, x, local_width_fn, z_center, cell, size=(16.0, 9.0)):
    """Flat patch proud of the flank (side intakes)."""
    hw, hh = size
    y = local_width_fn(x) + 0.4
    for s in (1, -1):
        m.quad((x - hw / 2, s * y, z_center - hh / 2), (x + hw / 2, s * y, z_center - hh / 2),
               (x + hw / 2, s * y, z_center + hh / 2), (x - hw / 2, s * y, z_center + hh / 2), cell)


def build_pickup_bed(m, x0, length, z_bottom, floor_z, rail_z, w, body_cell, dark_cell,
                     rear_axle_x, arch_r, n_seg=24):
    """Open box welded behind the cab: outer walls (arch-cut over the rear
    wheel), dark inner walls/floor/wheel-well humps, tailgate. Returns tail x."""
    x1 = x0 - length
    xs = np.linspace(x0, x1, n_seg + 1)
    d2 = arch_r ** 2 - (xs - rear_axle_x) ** 2
    bot = np.where(d2 > 0, np.maximum(z_bottom, np.sqrt(np.maximum(d2, 0.0))), z_bottom)
    wi = w - 5.0
    for i in range(n_seg):
        a, b = float(xs[i]), float(xs[i + 1])
        for s in (1, -1):
            q = [(a, s * w, bot[i]), (b, s * w, bot[i + 1]), (b, s * w, rail_z), (a, s * w, rail_z)]
            m.quad(*(q if s > 0 else q[::-1]), body_cell)
            ib0, ib1 = max(floor_z, bot[i]), max(floor_z, bot[i + 1])
            q = [(a, s * wi, ib0), (a, s * wi, rail_z), (b, s * wi, rail_z), (b, s * wi, ib1)]
            m.quad(*(q if s > 0 else q[::-1]), dark_cell)
            m.quad((a, s * wi, rail_z), (b, s * wi, rail_z), (b, s * w, rail_z), (a, s * w, rail_z), dark_cell)
            # underside strip between outer and inner wall
            m.quad((a, s * wi, bot[i]), (b, s * wi, bot[i + 1]), (b, s * w, bot[i + 1]), (a, s * w, bot[i]), dark_cell)
    m.quad((x0, -wi, floor_z), (x1, -wi, floor_z), (x1, wi, floor_z), (x0, wi, floor_z), dark_cell)
    for s in (1, -1):   # wheel-well humps inside the bed
        box(m, rear_axle_x - arch_r, rear_axle_x + arch_r, s * wi * 0.62, s * wi, floor_z, arch_r + 2, dark_cell)
    handle_z = rail_z - 14
    vpanel(m, x1, -w, w, z_bottom, handle_z - 2, body_cell)
    vpanel(m, x1 - 0.4, -12, 12, handle_z - 2, handle_z + 2, dark_cell)
    vpanel(m, x1, -w, w, handle_z - 2, rail_z, body_cell)
    vpanel(m, x1 - 0.6, -w, w, z_bottom, z_bottom + 12, PLASTIC)   # rear bumper
    return x1


# ================================================================== presets
def finish(m, name, display, front_axle_x, rear_axle_x, half_track, wheel_r, mass, length):
    tris = m.save(name)
    print(f"  {display}: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName=display, FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=mass)


# Axle x / half-track / wheel radius per car are PHYSICS geometry, duplicated
# in unreal/import_car_meshes.py's make_def() calls - change both together.
# Bodies are authored around them. Heights are cm above axle (ground is at
# z = -wheel_r); real-car targets noted per preset.

def build_muscle():
    """Long-hood pony coupe (~4.6 m, roof 138 cm from ground): long flat
    hood with a scoop, upright windshield, short fastback, flat tail with a
    duck-lip spoiler and twin exhausts."""
    body, dark = 0, 1
    fx, rx, ht, wr = 140.0, -150.0, 90.0, 36.0
    profile = [
        (236.0, -12.0, 36.0), (224.0, -20.0, 50.0), (180.0, -22.0, 55.0),
        (140.0, -22.0, 56.0), (60.0, -22.0, 58.0),     # cowl
        (20.0, -22.0, 100.0), (-50.0, -22.0, 102.0),   # roof
        (-100.0, -22.0, 86.0), (-125.0, -22.0, 62.0),  # fastback
        (-205.0, -18.0, 60.0), (-222.0, -12.0, 48.0),
    ]
    plan = [
        (236.0, 78.0), (224.0, 92.0), (180.0, 100.0), (140.0, 102.0), (60.0, 95.0),
        (-50.0, 93.0), (-110.0, 97.0), (-150.0, 104.0), (-205.0, 98.0), (-222.0, 92.0),
    ]
    style = style_for(dict(belt_z=57.0, glass_x_start=60.0, glass_x_end=-122.0,
                           pillars_x=(-62.0,), grille_cell=CHROME,
                           lamp_y=(0.52, 0.88), tail_y=(0.28, 0.90), tail_z=(0.50, 0.80)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr)
    cabin_blocker(m, 60.0, -122.0, lw, 57.0)
    mirrors(m, 50.0, lw, body)
    box(m, 110.0, 165.0, -20.0, 20.0, 50.0, 62.0, dark)                   # hood scoop
    box(m, -222.0, -206.0, -0.85 * lw(-214.0), 0.85 * lw(-214.0), 58.0, 63.0, body)   # duck lip
    exhaust_tips(m, -222.0, 42.0, -12.0)
    return finish(m, "SM_Car_Muscle", "Muscle", fx, rx, ht, wr, 1620.0, 236.0 + 222.0)


def build_jdm():
    """90s fastback sport coupe (~4.3 m, roof 127 cm from ground): low
    pointed nose, long glassy fastback, hoop rear wing."""
    body, dark = 2, 3
    fx, rx, ht, wr = 124.0, -114.0, 80.0, 32.0
    profile = [
        (214.0, -8.0, 26.0), (202.0, -18.0, 38.0), (160.0, -20.0, 46.0),
        (124.0, -20.0, 50.0), (70.0, -20.0, 51.0),     # cowl
        (25.0, -20.0, 88.0), (-35.0, -20.0, 95.0),     # roof
        (-90.0, -20.0, 78.0), (-130.0, -20.0, 58.0),   # fastback
        (-190.0, -18.0, 56.0), (-210.0, -12.0, 48.0),
    ]
    plan = [
        (214.0, 70.0), (202.0, 82.0), (160.0, 88.0), (124.0, 92.0), (70.0, 86.0),
        (-35.0, 84.0), (-90.0, 86.0), (-114.0, 92.0), (-180.0, 88.0), (-210.0, 82.0),
    ]
    style = style_for(dict(belt_z=50.0, glass_x_start=70.0, glass_x_end=-126.0,
                           pillars_x=(-40.0,), lamp_z=(0.60, 0.85), lamp_y=(0.45, 0.92),
                           tail_z=(0.55, 0.78), tail_y=(0.10, 0.94)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr)
    cabin_blocker(m, 70.0, -126.0, lw, 50.0)
    mirrors(m, 60.0, lw, body)
    wing(m, -196.0, 56.0, 76.0, 0.84 * lw(-196.0), chord=20.0, cell=body)
    exhaust_tips(m, -210.0, 55.0, -10.0, r=4.5)
    return finish(m, "SM_Car_JDM", "JDM", fx, rx, ht, wr, 1380.0, 214.0 + 210.0)


def build_super():
    """Mid-engine wedge (~4.5 m, roof ~118 cm from ground): knife nose,
    long raked windshield, low roof, long engine deck, side intakes, big
    wing, very wide rear haunches."""
    body, dark = 4, 5
    fx, rx, ht, wr = 118.0, -150.0, 94.0, 36.0
    profile = [
        (212.0, -16.0, 10.0), (198.0, -22.0, 26.0), (160.0, -24.0, 42.0),
        (118.0, -24.0, 52.0), (80.0, -24.0, 53.0),     # cowl
        (30.0, -24.0, 78.0), (-20.0, -24.0, 82.0),     # roof
        (-70.0, -24.0, 76.0), (-110.0, -24.0, 62.0),   # backlight / engine cover
        (-150.0, -24.0, 60.0), (-200.0, -20.0, 56.0), (-214.0, -12.0, 48.0),
    ]
    plan = [
        (212.0, 72.0), (198.0, 88.0), (160.0, 98.0), (118.0, 104.0), (80.0, 96.0),
        (-20.0, 88.0), (-80.0, 96.0), (-150.0, 108.0), (-200.0, 104.0), (-214.0, 96.0),
    ]
    style = style_for(dict(belt_z=48.0, arch_margin_cm=4.0, glass_x_start=80.0,
                           glass_x_end=-100.0, roof_top_frac=0.72,
                           lamp_z=(0.72, 0.90), lamp_y=(0.40, 0.92),
                           tail_z=(0.62, 0.82), tail_y=(0.20, 0.94)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr)
    cabin_blocker(m, 80.0, -100.0, lw, 48.0)
    mirrors(m, 68.0, lw, body)
    side_patch(m, -85.0, lw, 22.0, PLASTIC, size=(34.0, 18.0))            # side intakes
    wing(m, -196.0, 56.0, 78.0, 0.88 * lw(-196.0), chord=28.0)
    exhaust_tips(m, -214.0, 18.0, -6.0, r=5.0)
    return finish(m, "SM_Car_Super", "Super", fx, rx, ht, wr, 1430.0, 212.0 + 214.0)


def build_pickup():
    """Off-road crew-cab pickup (~5.1 m, roof ~195 cm from ground): tall
    blunt nose with a big chrome grille, flat hood, upright cab, open bed."""
    body, dark = 6, 7
    fx, rx, ht, wr = 185.0, -160.0, 98.0, 42.0
    profile = [
        (268.0, 4.0, 64.0), (258.0, -6.0, 76.0), (230.0, -8.0, 80.0),
        (185.0, -8.0, 82.0), (108.0, -8.0, 86.0),      # cowl
        (70.0, -8.0, 148.0), (-30.0, -8.0, 152.0),     # roof
        (-38.0, -8.0, 146.0), (-44.0, -8.0, 92.0),     # cab back
    ]
    plan = [
        (268.0, 96.0), (258.0, 106.0), (230.0, 112.0), (185.0, 114.0), (140.0, 110.0),
        (108.0, 106.0), (-44.0, 106.0),
    ]
    style = style_for(dict(belt_z=90.0, arch_margin_cm=7.0, glass_x_start=108.0,
                           glass_x_end=-36.0, pillars_x=(18.0,), grille_cell=CHROME, tail_z=None,
                           lamp_z=(0.52, 0.80), lamp_y=(0.56, 0.92)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr)
    cabin_blocker(m, 108.0, -36.0, lw, 90.0)
    mirrors(m, 96.0, lw, dark)
    box(m, 50.0, 58.0, -56.0, 56.0, 152.0, 158.0, HEADLIGHT)              # roof light bar
    bed_w = ht + 14.0
    tail_x = build_pickup_bed(m, -44.0, 200.0, -8.0, 30.0, 90.0, bed_w, body, dark,
                              rx, wr + style["arch_margin_cm"])
    for s in (1, -1):   # vertical corner taillights
        vpanel(m, tail_x - 0.6, s * (bed_w - 14), s * (bed_w - 0.5), 48.0, 86.0, PLASTIC)
        vpanel(m, tail_x - 0.9, s * (bed_w - 12), s * (bed_w - 2), 74.0, 84.0, AMBER)
        vpanel(m, tail_x - 0.9, s * (bed_w - 12), s * (bed_w - 2), 60.0, 74.0, TAILLIGHT)
        vpanel(m, tail_x - 0.9, s * (bed_w - 12), s * (bed_w - 2), 50.0, 60.0, REVERSE)
    return finish(m, "SM_Car_Pickup", "Pickup", fx, rx, ht, wr, 2380.0, 268.0 - tail_x)


def build_rally():
    """Compact rally hatch (~3.7 m, roof ~146 cm from ground): short
    overhangs, tall boxy greenhouse, near-vertical hatch, roof scoop, hatch
    spoiler, auxiliary lamp pod."""
    body, dark = 8, 9
    fx, rx, ht, wr = 112.0, -118.0, 78.0, 32.0
    profile = [
        (190.0, -8.0, 26.0), (180.0, -16.0, 38.0), (140.0, -18.0, 47.0),
        (112.0, -18.0, 50.0), (70.0, -18.0, 52.0),     # cowl
        (25.0, -18.0, 110.0), (-100.0, -18.0, 112.0),  # roof
        (-140.0, -18.0, 106.0), (-165.0, -18.0, 62.0), # hatch
        (-180.0, -12.0, 54.0),
    ]
    plan = [
        (190.0, 72.0), (180.0, 84.0), (140.0, 88.0), (112.0, 92.0), (70.0, 84.0),
        (-60.0, 82.0), (-118.0, 90.0), (-165.0, 86.0), (-180.0, 80.0),
    ]
    style = style_for(dict(belt_z=52.0, glass_x_start=70.0, glass_x_end=-162.0,
                           pillars_x=(-12.0, -104.0), roof_top_frac=0.80,
                           lamp_y=(0.48, 0.90), tail_z=(0.60, 0.95), tail_y=(0.66, 0.96)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr)
    cabin_blocker(m, 70.0, -162.0, lw, 52.0)
    mirrors(m, 60.0, lw, body)
    box(m, -10.0, 24.0, -16.0, 16.0, 110.0, 118.0, dark)                   # roof scoop
    box(m, -146.0, -126.0, -0.82 * lw(-140.0), 0.82 * lw(-140.0), 107.0, 111.0, dark)   # hatch spoiler
    for y in (-42.0, -14.0, 14.0, 42.0):                                  # aux lamp pod
        box(m, 190.0, 194.0, y - 11, y + 11, 6.0, 20.0, HEADLIGHT)
    exhaust_tips(m, -180.0, 50.0, -10.0, r=5.0)
    return finish(m, "SM_Car_Rally", "Rally", fx, rx, ht, wr, 1310.0, 190.0 + 180.0)


def build_concept():
    """Le Mans-prototype road concept (Furai x GT40-flavored, ~4.0 m, ~115
    cm tall): body shrink-wrapped around the wheels - tall fender pods
    front and rear with a very low nose valley and rear deck between them,
    a teardrop canopy standing free in the middle, deep side scoops behind
    the front wheels, shark fin running into a wing on tall end plates."""
    body, dark = 18, 19
    fx, rx, ht, wr = 128.0, -140.0, 92.0, 35.0
    # (x, bottom, fender CREST) - high over each wheel, dipping at the doors
    profile = [
        (176.0, -24.0, 14.0), (169.0, -28.0, 26.0), (154.0, -28.0, 44.0),
        (139.0, -28.0, 54.0), (128.0, -28.0, 57.0), (100.0, -28.0, 52.0),
        (78.0, -28.0, 42.0), (0.0, -28.0, 38.0), (-70.0, -28.0, 44.0),
        (-110.0, -28.0, 56.0), (-140.0, -28.0, 62.0), (-175.0, -28.0, 58.0),
        (-205.0, -26.0, 50.0), (-228.0, -20.0, 42.0),
    ]
    plan = [
        (176.0, 76.0), (169.0, 92.0), (154.0, 104.0), (128.0, 110.0), (95.0, 104.0),
        (60.0, 98.0), (-60.0, 100.0), (-100.0, 108.0), (-140.0, 114.0),
        (-190.0, 112.0), (-228.0, 104.0),
    ]
    # (x, z, half_width) centerline: low nose valley -> teardrop canopy ->
    # low rear deck between the hips
    center = [
        (176.0, 0.0, 48.0), (159.0, 6.0, 54.0), (139.0, 12.0, 54.0),
        (110.0, 18.0, 52.0), (86.0, 26.0, 50.0),       # cowl
        (60.0, 56.0, 48.0), (20.0, 80.0, 45.0), (-20.0, 83.0, 41.0),   # canopy
        (-60.0, 74.0, 32.0), (-100.0, 58.0, 22.0), (-135.0, 42.0, 16.0),  # teardrop tail
        (-160.0, 28.0, 40.0), (-228.0, 22.0, 46.0),    # rear deck
    ]
    style = style_for(dict(belt_z=50.0, arch_margin_cm=4.0, glass_x_start=86.0,
                           glass_x_end=-70.0, hood_top_frac=0.85, roof_top_frac=0.85,
                           steep_slope=0.2, lamp_z=(0.40, 0.98), lamp_y=(0.74, 0.86),   # slit lamps
                           tail_z=(0.66, 0.84), tail_y=(0.56, 0.95)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr, center=center)
    cabin_blocker(m, 86.0, -70.0, lw, 36.0, inset_frac=0.5)
    mirrors(m, 62.0, lw, body)
    box(m, 160.0, 182.0, -0.95 * lw(172.0), 0.95 * lw(172.0), -30.0, -26.0, dark)   # splitter
    side_patch(m, 62.0, lw, 4.0, dark, size=(56.0, 30.0))               # scoop behind front wheel
    side_patch(m, -86.0, lw, 18.0, dark, size=(30.0, 18.0))             # rear intake
    # shark fin: canopy crown back to the wing, top edge sweeping down
    fin = np.linspace(-45.0, -214.0, 12)
    for a, b in zip(fin, fin[1:]):
        base = min(lw.z(a)[2], lw.z(b)[2]) - 1
        top = max(84 + (a + 45) * 16 / 169, 84 + (b + 45) * 16 / 169)
        box(m, b, a, -1.2, 1.2, base, top, body)
    wing(m, -214.0, 40.0, 76.0, 0.96 * lw(-214.0), chord=30.0, cell=dark, plate_cell=TAILLIGHT)
    exhaust_tips(m, -228.0, 16.0, 10.0, r=5.0)
    return finish(m, "SM_Car_Concept", "Concept", fx, rx, ht, wr, 1150.0, 176.0 + 228.0)


def build_lmp():
    """Le Mans prototype (~4.8 m, ~105 cm tall, ~2.1 m wide): nose a few
    cm off the ground between tall front pods, low sidepods through the
    cockpit section (the crest pulls inboard there so the sidepod tops are
    paint and only the narrow bubble cockpit is glass), rear hips, long
    fin into a full-width wing on a long tail."""
    body, dark = 20, 21
    fx, rx, ht, wr = 150.0, -140.0, 88.0, 34.0
    # (x, bottom, fender CREST)
    profile = [
        (236.0, -27.0, 8.0), (222.0, -29.0, 28.0), (190.0, -29.0, 48.0),
        (150.0, -29.0, 54.0), (118.0, -29.0, 48.0), (100.0, -29.0, 26.0),  # front pod
        (20.0, -29.0, 26.0), (-70.0, -29.0, 34.0),                           # sidepods
        (-105.0, -29.0, 50.0), (-140.0, -29.0, 56.0), (-180.0, -29.0, 54.0), # rear hips
        (-215.0, -27.0, 46.0), (-248.0, -22.0, 40.0),
    ]
    plan = [
        (236.0, 78.0), (222.0, 96.0), (190.0, 102.0), (150.0, 104.0), (118.0, 100.0),
        (100.0, 96.0), (20.0, 96.0), (-70.0, 98.0), (-140.0, 106.0),
        (-215.0, 104.0), (-248.0, 98.0),
    ]
    # (x, z, half_width) centerline: near-ground nose -> bubble -> spine
    center = [
        (236.0, -6.0, 54.0), (200.0, -2.0, 56.0), (160.0, 4.0, 54.0),
        (120.0, 10.0, 46.0), (100.0, 18.0, 40.0),      # cowl
        (60.0, 54.0, 36.0), (15.0, 70.0, 32.0), (-30.0, 70.0, 28.0),   # cockpit
        (-70.0, 58.0, 22.0), (-110.0, 46.0, 14.0),     # bubble tail
        (-160.0, 40.0, 12.0), (-248.0, 32.0, 36.0),    # engine-cover spine
    ]
    style = style_for(dict(belt_z=50.0, arch_margin_cm=3.0, glass_x_start=100.0,
                           glass_x_end=-95.0, hood_top_frac=0.85, roof_top_frac=0.50,
                           steep_slope=0.2, lamp_z=(0.45, 0.98), lamp_y=(0.70, 0.88),
                           tail_z=(0.80, 0.90), tail_y=(0.10, 0.95)))
    m = Mesh()
    lw = build_hull(m, profile, plan, body, dark, style, fx, rx, wr, center=center)
    cabin_blocker(m, 100.0, -95.0, lw, 22.0, inset_frac=0.45)
    mirrors(m, 112.0, lw, body)
    box(m, 214.0, 244.0, -0.98 * lw(230.0), 0.98 * lw(230.0), -31.0, -28.0, dark)   # splitter
    side_patch(m, 96.0, lw, 0.0, dark, size=(26.0, 40.0))                # pod exit behind front wheel
    side_patch(m, -82.0, lw, 14.0, dark, size=(34.0, 16.0))              # radiator intake
    fin = np.linspace(-30.0, -236.0, 14)
    for a, b in zip(fin, fin[1:]):
        base = min(lw.z(a)[2], lw.z(b)[2]) - 1
        top = max(76 - (a + 30) * 0.02, 76 - (b + 30) * 0.02)
        box(m, b, a, -1.0, 1.0, base, top, body)
    wing(m, -236.0, 36.0, 74.0, 0.98 * lw(-236.0), chord=28.0, cell=dark, plate_cell=body)
    exhaust_tips(m, -248.0, 7.0, 34.0, r=4.0)
    return finish(m, "SM_Car_LMP", "LMP", fx, rx, ht, wr, 1030.0, 236.0 + 248.0)


def build_wheel():
    """Shared wheel at REF_WHEEL_RADIUS_CM: rounded tire revolved from a
    profile, recessed 5-spoke rim face."""
    w = Mesh()
    R, HW, SEG = REF_WHEEL_RADIUS_CM, 13.0, WHEEL_SEG
    RIM_R, HUB_R = 0.64 * R, 0.18 * R
    # (radius, y) tire profile, one sidewall -> tread -> other sidewall
    prof = [(RIM_R, HW - 1), (R - 4, HW), (R, HW - 4), (R, -(HW - 4)), (R - 4, -HW), (RIM_R, -(HW - 1))]
    if SMOOTH:   # higher tiers: rounded shoulders instead of 45-degree chamfers
        arc = lambda s, a0, a1: [(R - 4 + 4 * math.cos(a), s * (HW - 4) + 4 * math.sin(a) * s)
                                 for a in np.linspace(a0, a1, 5)]
        prof = [prof[0]] + arc(1, math.pi / 2, 0) + arc(-1, 0, math.pi / 2) + [prof[-1]]

    def pt(rad, y, a):
        return (rad * math.cos(a), y, rad * math.sin(a))

    for k in range(SEG):
        a0, a1 = 2 * math.pi * k / SEG, 2 * math.pi * (k + 1) / SEG
        for (r0, y0), (r1, y1) in zip(prof, prof[1:]):
            w.quad(pt(r0, y0, a0), pt(r0, y0, a1), pt(r1, y1, a1), pt(r1, y1, a0), TIRE)
        spoke = RIM if (k * 10 // SEG) % 2 == 0 else TIRE   # 5 spokes
        for s in (1, -1):
            yl, yd = s * (HW - 1), s * (HW - 5)       # lip plane, recessed dish plane
            w.quad(pt(RIM_R, yl, a0), pt(RIM_R, yl, a1), pt(RIM_R * 0.9, yd, a1), pt(RIM_R * 0.9, yd, a0), RIM)
            w.quad(pt(RIM_R * 0.9, yd, a0), pt(RIM_R * 0.9, yd, a1), pt(HUB_R, yd, a1), pt(HUB_R, yd, a0), spoke)
            w.tri(pt(HUB_R, yd, a0), pt(HUB_R, yd, a1), (0, s * (HW - 3), 0), RIM)
    w.save("SM_CarWheel")


SUFFIX = ""

if __name__ == "__main__":
    print("Building procedural car set (7 presets, 1 shared wheel)...")
    for SUFFIX, (SPACING, SECTION_SUB, WHEEL_SEG, SMOOTH) in list(TIERS.items())[::-1]:
        # Standard last so the per-car dicts below (manifest) come from it
        muscle = build_muscle()
        jdm = build_jdm()
        super_ = build_super()
        pickup = build_pickup()
        rally = build_rally()
        concept = build_concept()
        lmp = build_lmp()
        build_wheel()
    write_palette()
    (OUT / "LICENSE.txt").write_text(
        "Procedural car meshes generated by pipeline/make_car_meshes.py.\n"
        "Original procedural content, no third-party assets.\n")
    print("wrote LICENSE.txt")

    mesh_names = {
        "SM_Car_Muscle": muscle, "SM_Car_JDM": jdm, "SM_Car_Super": super_,
        "SM_Car_Pickup": pickup, "SM_Car_Rally": rally, "SM_Car_Concept": concept, "SM_Car_LMP": lmp,
    }
    manifest = {
        name: {
            "DisplayName": d["DisplayName"],
            "FrontAxleXCm": d["FrontAxleXCm"],
            "RearAxleXCm": d["RearAxleXCm"],
            "HalfTrackYCm": d["HalfTrackYCm"],
            "WheelRadiusCm": d["WheelRadiusCm"],
        }
        for name, d in mesh_names.items()
    }
    manifest_path = OBJ_OUT / "cars_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {manifest_path.name}")

    print("\nPer-car UODVehicleDef geometry (also passed directly to "
          "unreal/import_car_meshes.py):")
    for d in (muscle, jdm, super_, pickup, rally, concept, lmp):
        print(f"  {d}")
