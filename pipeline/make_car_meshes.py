"""Procedural low-poly car body generator - Phase 4a replica-style rework
(2026-09-03, 3-curve rearchitecture pass). Five selectable car types, all
badge-free, no real names anywhere (file names, display names, geometry):

    MUSCLE - long-hood/short-deck pony coupe (Mustang/Camaro-flavored).
    JDM    - smooth 90s fastback sport coupe (Supra/RX-7-flavored).
    SUPER  - mid-engine wedge supercar (Ferrari/Lambo-flavored), roofline
             peaking ~114 cm.
    PICKUP - off-road crew-cab pickup (Raptor-flavored): raised ride
             height, upright cab, then a non-lofted welded-on PICKUP BED
             (side rails + floor + tailgate) - the one archetype whose rear
             is not part of the continuous hull loft.
    RALLY  - compact rally hatch: short overhangs, tall greenhouse for its
             length, flared arches, steep hatch glass.

2026-09-03 REARCHITECTURE (this pass, replacing the 2026-08-28..09-01
"loft stations + bolted-on cab quad" builder): that construction always
built a lofted body-width envelope FIRST and then stuck a separate
glass-and-roof quad on top of it as an independent primitive. Reviewed
against real renders it reads exactly like that: a smooth "bathtub" hull
with a traffic-cone greenhouse glued on, and flat proud dark rectangles
standing in for wheel arches - none of the five read as their archetype
from a pure side silhouette. This file now builds every car from classic
3-curve construction, all in numpy, with the greenhouse and the wheel
arches as GENUINE PARTS of the same continuous hull surface:

  1. SIDE PROFILE - one continuous (x, z_bottom, z_top) polyline per car,
     authored front bumper -> hood -> cowl -> A-pillar -> roof -> C-pillar/
     fastback -> decklid/tailgate -> rear bumper. z_top(x) IS the roofline;
     there is no separate cab primitive. The A-/C-pillar rake happens
     automatically wherever this authored curve rises/falls steeply between
     control points - see PROFILE lists in each build_*() below.
  2. PLAN VIEW - a separate half_width(x) polyline (nose taper, fender
     bulges, cabin waist, rear haunches) - see PLAN lists below.
  3. CROSS-SECTION - a 5-point rounded-rectangle profile per station
     (bottom / round shoulder / sill / beltline crease / top), computed in
     build_hull() from the two curves above plus a roughly-constant BELT_Z
     (beltline height). Wherever the authored roofline z_top(x) rises above
     BELT_Z, the shoulder-to-top band is real GLASS material, inset ~4 cm
     narrower than the beltline (tumblehome) - so the windshield/roof/rear
     glass are literal parts of the swept hull, not a bolted-on quad, and
     they vanish smoothly (zero-height band) wherever the roofline drops
     back below the beltline (hood/decklid) - see weight() in build_hull().
  4. WHEEL ARCHES - the lower hull curve is CLIPPED by a real arc at each
     axle: local_bottom(x) = max(base_bottom(x), arch(x)), where arch(x) is
     a semicircle of radius wheel_r + ~10 cm centered at axle height
     (z=0, the OBJ pivot convention) - see the "wheel-arch clipping" block
     in build_hull(). This bites a genuine scalloped opening into the
     rocker/side silhouette (visible from pure side-on) so the wheel sits
     INSIDE an opening instead of being half-hidden under a flat slab side;
     arch_lip() then adds a thin proud flare-lip trim tracing that same arc.
  5. A beltline crease falls out of the cross-section directly (the
     shoulder point sits at full plan-view width, sill/top both tuck in
     relative to it - true for both the glass band and the hood/deck band).

Detail meshes (mirrors, spoilers/wing, exhaust tips, the pickup bed) are
unchanged from the previous pass - they were fine and are kept as-is,
still keyed off local_width(x) returned by build_hull().

Geometry contract with AODVehiclePawn / UODVehicleMovement (UNCHANGED):
OBJ pivot sits at wheelbase center at WHEEL AXLE height z=0. Per-car axle
geometry (front/rear axle x, half-track y, wheel radius) is printed at the
end of each preset's build and also written to obj/cars_manifest.json -
transfer/read these into that car's UODVehicleDef fields so the visual
wheel positions match the physics wheel table UODVehicleMovement::
SetupWheels builds from the same def.

The wheel mesh (SM_CarWheel) is built ONCE at REF_WHEEL_RADIUS_CM; each
car's own WheelRadiusCm may differ - AODVehiclePawn::ApplyVehicleDef
visually rescales the shared mesh per car (WheelRadiusCm / REF_WHEEL_
RADIUS_CM) rather than baking five near-identical wheel meshes.

Material-slot split (kept from the prior pass, unaffected by this
rearchitecture): Mesh.save() groups triangles by material CATEGORY (body/
plastic/glass/chrome/tire/rim/lights) into OBJ usemtl groups + a companion
.mtl, so Unreal's OBJ importer creates one real material slot per category
per mesh - see unreal/import_car_meshes.py for the importer-side half.

Outputs (data/cars/procedural/, single shared palette - same "many meshes,
one palette texture" pattern as pipeline/make_tree_meshes.py):
    obj/SM_Car_Muscle.obj, SM_Car_JDM.obj, SM_Car_Super.obj,
        SM_Car_Pickup.obj, SM_Car_Rally.obj  (each with its own .mtl)
    obj/SM_CarWheel.obj (+ .mtl)
    obj/cars_manifest.json   (per-car DisplayName/axle/half-track/wheel-radius,
                               read by the live previewer to place wheels)
    textures/T_CarPalette2.png
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
]
N_CELL = len(PALETTE)
GLASS, PLASTIC, TIRE, RIM, HEADLIGHT, TAILLIGHT, ACCENT, CHROME = 10, 11, 12, 13, 14, 15, 16, 17
TRIM, HUB = PLASTIC, RIM   # backward-compat aliases

# ---------------------------------------------------------- material slots
CATEGORY_OF = {
    0: "body", 1: "plastic", 2: "body", 3: "plastic", 4: "body", 5: "plastic",
    6: "body", 7: "plastic", 8: "body", 9: "plastic",
    GLASS: "glass", PLASTIC: "plastic", TIRE: "tire", RIM: "rim",
    HEADLIGHT: "lights", TAILLIGHT: "lights", ACCENT: "plastic", CHROME: "chrome",
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
                    lines.append(f"v {p[0]:.1f} {-p[1]:.1f} {p[2]:.1f}")
                    lines.append(f"vt {u:.4f} {v:.4f}")
                    lines.append(f"vn {n[0]:.3f} {-n[1]:.3f} {n[2]:.3f}")
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
    resample_spacing=10.0,      # cm between resampled loft stations
    # plan-view fender bulge (gaussian, centered on each axle x)
    bulge_strength=0.12,
    bulge_sigma=50.0,
    front_bulge_mult=1.0,
    rear_bulge_mult=1.0,
    # 5-point cross-section width fractions (of the plan-view half-width at
    # that x): bottom (narrow, rounded underbody) -> round (rounded lower
    # corner, slightly proud of bottom) -> sill (near-vertical rocker) ->
    # shoulder (beltline crease, full width) -> top (tumblehome/glass or
    # hood/deck, see weight() in build_hull).
    bottom_width_frac=0.72,
    round_width_frac=0.80,
    sill_width_frac=0.94,
    hood_top_width_frac=0.90,   # top-band width when NOT glass (hood/deck)
    glass_inset_cm=4.0,         # top-band width when glass: shoulder - this
    round_offset_cm=9.0,        # z height of the "round" point above local bottom
    sill_height_cm=17.0,        # z height of the "sill" point above local bottom
    min_band_cm=14.0,           # safety clamp: bottom stays this far under top
    belt_z=48.0,                # beltline (shoulder) height - roughly constant
    arch_margin_cm=10.0,        # wheel-arch radius = wheel_r + this
    cap_trim_cell=None,         # None -> PLASTIC; some presets use CHROME
)


def style_for(overrides):
    return {**STYLE_DEFAULTS, **overrides}


def build_hull(m, profile, plan, body_cell, dark_cell, style,
               front_axle_x=None, rear_axle_x=None, wheel_r=34.0):
    """3-curve hull builder (see module docstring items 1-5).

    profile: [(x, z_bottom, z_top), ...] front(large x) -> tail(negative x) -
        THE side-profile silhouette (item 1). z_top(x) already includes the
        hood/cowl/roof/decklid shape; there is no separate cab primitive.
    plan: [(x, half_width), ...] - the top-down width curve (item 2).
    Returns local_width(x) (the plan-view half-width at x, post-bulge) with
    a .band(x) -> (local_bottom, top) attribute, for detail meshes
    (mirrors/spoilers/wing/bed) to key off.
    """
    prof_sorted = sorted(profile, key=lambda p: p[0])
    plan_sorted = sorted(plan, key=lambda p: p[0])
    px = np.array([p[0] for p in prof_sorted])
    pzb = np.array([p[1] for p in prof_sorted])
    pzt = np.array([p[2] for p in prof_sorted])
    wx = np.array([p[0] for p in plan_sorted])
    wh = np.array([p[1] for p in plan_sorted])

    x_nose, x_tail = profile[0][0], profile[-1][0]
    n = max(8, int(round(abs(x_nose - x_tail) / style["resample_spacing"])))
    xs = np.linspace(x_nose, x_tail, n + 1)   # descending: nose -> tail

    base_bottom = np.interp(xs, px, pzb)
    base_top = np.interp(xs, px, pzt)
    half_w = np.interp(xs, wx, wh)

    # plan-view fender bulge (item 2's flare)
    bulge = np.ones_like(xs)
    if front_axle_x is not None:
        bulge = bulge + style["bulge_strength"] * style["front_bulge_mult"] * \
            np.exp(-((xs - front_axle_x) / style["bulge_sigma"]) ** 2)
    if rear_axle_x is not None:
        bulge = bulge + style["bulge_strength"] * style["rear_bulge_mult"] * \
            np.exp(-((xs - rear_axle_x) / style["bulge_sigma"]) ** 2)
    half_w = half_w * bulge

    # --- wheel-arch clipping (item 4): clip the lower hull with a real arc
    # around each axle, radius = wheel_r + arch_margin_cm, centered at axle
    # height (z=0). max() with the base rocker line means the arc only
    # lifts material inside its own footprint and rejoins the flat rocker
    # exactly at the tangent points (sqrt term -> 0 there).
    arch = np.full_like(xs, -1.0e9)
    r = wheel_r + style["arch_margin_cm"]
    for axle_x in (front_axle_x, rear_axle_x):
        if axle_x is None:
            continue
        d2 = r * r - (xs - axle_x) ** 2
        vals = np.where(d2 > 0, np.sqrt(np.maximum(d2, 0.0)), -1.0e9)
        arch = np.maximum(arch, vals)
    local_bottom = np.maximum(base_bottom, arch)
    local_bottom = np.minimum(local_bottom, base_top - style["min_band_cm"])

    # --- glass band (item 3): wherever the authored roofline rises above
    # the (roughly constant) beltline, that band is glass, inset and
    # narrower than the beltline; it vanishes smoothly (zero height)
    # wherever the roofline drops back to hood/deck height.
    belt = style["belt_z"]
    shoulder_z = np.minimum(belt, base_top)
    glass_band = np.maximum(0.0, base_top - shoulder_z)
    ref_h = max(1.0, float(base_top.max()) - belt)
    weight = np.clip(glass_band / ref_h, 0.0, 1.0)

    round_z = np.minimum(local_bottom + style["round_offset_cm"], shoulder_z - 2.0)
    sill_z = np.clip(local_bottom + style["sill_height_cm"], round_z + 1.0, shoulder_z - 1.0)

    bottom_w = half_w * style["bottom_width_frac"]
    round_w = half_w * style["round_width_frac"]
    sill_w = half_w * style["sill_width_frac"]
    shoulder_w = half_w * 1.0
    hood_top_w = half_w * style["hood_top_width_frac"]
    # Glass band: the SHOULDER->TOP flank is the visible windshield/side-
    # glass/backlight surface, so it must stay a substantial tumblehome
    # slope (shoulder_w down to a narrow roof-ridge width) rather than
    # nearly full width - a wide top_w here just makes the flat top cap
    # (the actual painted roof panel, always body_cell below) balloon into
    # a giant opaque lid that hides the glass entirely. glass_inset_cm is
    # reused as the roof-ridge half-width.
    glass_top_w = np.maximum(style["glass_inset_cm"], half_w * 0.14)
    top_w = hood_top_w + (glass_top_w - hood_top_w) * weight

    Zs = np.stack([local_bottom, round_z, sill_z, shoulder_z, base_top], axis=1)
    Ws = np.stack([bottom_w, round_w, sill_w, shoulder_w, top_w], axis=1)
    band_glass = weight > 0.35

    def local_width(x):
        return float(np.interp(x, xs[::-1], half_w[::-1]))

    def local_band(x):
        return (float(np.interp(x, xs[::-1], local_bottom[::-1])),
                float(np.interp(x, xs[::-1], base_top[::-1])))
    local_width.band = local_band

    band_cells = [dark_cell, dark_cell, body_cell, None]   # None -> resolved per-quad (glass or body)
    for i in range(len(xs) - 1):
        x0, x1 = xs[i], xs[i + 1]
        for k in range(4):
            za0, wa0 = Zs[i, k], Ws[i, k]
            zb0, wb0 = Zs[i, k + 1], Ws[i, k + 1]
            za1, wa1 = Zs[i + 1, k], Ws[i + 1, k]
            zb1, wb1 = Zs[i + 1, k + 1], Ws[i + 1, k + 1]
            cell = band_cells[k]
            if cell is None:
                cell = GLASS if (band_glass[i] and band_glass[i + 1]) else body_cell
            for side in (1, -1):
                a = (x0, side * wa0, za0)
                b = (x1, side * wa1, za1)
                c = (x1, side * wb1, zb1)
                d = (x0, side * wb0, zb0)
                if side > 0:
                    m.quad(a, b, c, d, cell)
                else:
                    m.quad(d, c, b, a, cell)
        m.quad((x0, -Ws[i, 4], Zs[i, 4]), (x1, -Ws[i + 1, 4], Zs[i + 1, 4]),
               (x1, Ws[i + 1, 4], Zs[i + 1, 4]), (x0, Ws[i, 4], Zs[i, 4]), body_cell)   # roof/hood/deck
        m.quad((x0, Ws[i, 0], Zs[i, 0]), (x1, Ws[i + 1, 0], Zs[i + 1, 0]),
               (x1, -Ws[i + 1, 0], Zs[i + 1, 0]), (x0, -Ws[i, 0], Zs[i, 0]), dark_cell)  # floor pan

    # nose/tail bumper caps come straight from the authored profile ends -
    # item 1's front-bumper/rear-cutoff control points ARE the end faces,
    # no synthetic taper needed.
    def cap(idx, light_cell, flip):
        w = float(Ws[idx].max())
        lw = w * 0.42
        x = xs[idx]
        zb, zt = float(Zs[idx, 0]), float(Zs[idx, 4])
        trim_cell = style["cap_trim_cell"] if style["cap_trim_cell"] is not None else PLASTIC
        for y0, y1, cell in ((-w, -lw, light_cell), (-lw, lw, trim_cell), (lw, w, light_cell)):
            a, bq, c, d = (x, y0, zb), (x, y0, zt), (x, y1, zt), (x, y1, zb)
            if flip:
                m.quad(d, c, bq, a, cell)
            else:
                m.quad(a, bq, c, d, cell)
    cap(0, HEADLIGHT, flip=False)
    cap(-1, TAILLIGHT, flip=True)

    return local_width


def arch_lip(m, axle_x, wheel_r, local_width_fn, margin, n=12, cell=PLASTIC):
    """Thin proud flare-lip trim tracing the SAME arc build_hull() clipped
    the lower hull with (item 4's "small flare lip") - sits just outboard
    of the hull surface along the arch opening."""
    r = wheel_r + margin
    xs = np.linspace(axle_x - r * 0.92, axle_x + r * 0.92, n)
    pts = []
    for x in xs:
        d2 = r * r - (x - axle_x) ** 2
        z = math.sqrt(d2) if d2 > 0 else 0.0
        y = local_width_fn(x) + 0.6
        pts.append((x, y, z))
    for i in range(len(pts) - 1):
        x0, y0, z0 = pts[i]
        x1, y1, z1 = pts[i + 1]
        for side in (1, -1):
            a = (x0, side * y0, z0 - 2.5)
            b = (x1, side * y1, z1 - 2.5)
            c = (x1, side * y1, z1 + 2.5)
            d = (x0, side * y0, z0 + 2.5)
            if side > 0:
                m.quad(a, b, c, d, cell)
            else:
                m.quad(d, c, b, a, cell)


def mirrors(m, x, local_width_fn, z):
    """Small trim-colored box off each cabin front corner."""
    half_width = local_width_fn(x)
    sx, sy, sz = 7.0, 5.0, 4.0
    for side in (1, -1):
        y0 = side * half_width
        y1 = side * (half_width + sy)
        x0, x1 = x - sx, x + sx
        z0, z1 = z - sz, z + sz
        top = [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        bot = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)]
        front = [(x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0)]
        if side > 0:
            m.quad(*top, PLASTIC)
            m.quad(bot[3], bot[2], bot[1], bot[0], PLASTIC)
            m.quad(*front, PLASTIC)
        else:
            m.quad(top[3], top[2], top[1], top[0], PLASTIC)
            m.quad(*bot, PLASTIC)
            m.quad(front[3], front[2], front[1], front[0], PLASTIC)


def rear_wing(m, deck_x, deck_z, half_span, stanchion_h):
    """Raised wing on two stanchions above the decklid (Super)."""
    wing_z = deck_z + stanchion_h
    chord = 22.0
    for side in (1, -1):
        y = side * half_span * 0.55
        a, b = (deck_x - 3, y, deck_z), (deck_x + 3, y, deck_z)
        c, d = (deck_x + 3, y, wing_z), (deck_x - 3, y, wing_z)
        m.quad(a, b, c, d, ACCENT)
        m.quad(d, c, b, a, ACCENT)
    x0, x1 = deck_x - chord / 2, deck_x + chord / 2
    y0, y1 = -half_span, half_span
    m.quad((x0, y0, wing_z + 2), (x0, y1, wing_z + 2), (x1, y1, wing_z + 2),
           (x1, y0, wing_z + 2), ACCENT)
    m.quad((x0, y0, wing_z - 2), (x1, y0, wing_z - 2), (x1, y1, wing_z - 2),
           (x0, y1, wing_z - 2), ACCENT)


def diffuser_stripe(m, tail_x, half_width, z_bottom):
    """Dark full-width diffuser suggestion under the tail (Super)."""
    x0, x1 = tail_x + 16, tail_x - 16
    m.quad((x0, -half_width, z_bottom), (x1, -half_width, z_bottom),
           (x1, half_width, z_bottom), (x0, half_width, z_bottom), ACCENT)


def lip_spoiler(m, x, half_width, z, thickness=3.0, chord=14.0, cell=ACCENT):
    """Thin blade hugging the decklid/hatch trailing edge (Muscle's
    duckbill, JDM/Rally's hatch lip)."""
    w = half_width * 0.92
    x0, x1 = x - chord / 2, x + chord / 2
    z0, z1 = z, z + thickness
    m.quad((x0, -w, z0), (x1, -w, z0), (x1, w, z0), (x0, w, z0), cell)
    m.quad((x0, -w, z1), (x0, w, z1), (x1, w, z1), (x1, -w, z1), cell)
    m.quad((x1, -w, z0), (x1, -w, z1), (x1, w, z1), (x1, w, z0), cell)


def exhaust_tips(m, x, half_track, z, cell=CHROME, r=4.0, length=10.0):
    """Two small chrome box "pipes" poking from the rear bumper."""
    for side in (1, -1):
        y = side * half_track * 0.45
        x0, x1 = x, x - length
        y0, y1 = y - r, y + r
        z0, z1 = z - r, z + r
        m.quad((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0), cell)
        m.quad((x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1), cell)
        m.quad((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0), cell)
        m.quad((x1, y0, z0), (x0, y0, z0), (x0, y0, z1), (x1, y0, z1), cell)
        m.quad((x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (x0, y1, z0), cell)


def build_pickup_bed(m, cab_tail_x, floor_z, wall_top_z, half_width, bed_length,
                      body_cell, dark_cell):
    """Non-lofted structure welded on directly behind the cab - a truck bed
    doesn't taper into a canoe stern, it is an open box: floor + two side
    rails + a flat tailgate cap."""
    tail_x = cab_tail_x - bed_length
    rail_w = half_width
    m.quad((cab_tail_x, -rail_w, floor_z), (tail_x, -rail_w, floor_z),
           (tail_x, rail_w, floor_z), (cab_tail_x, rail_w, floor_z), dark_cell)
    cap_h = 3.0
    for side in (1, -1):
        y = side * rail_w
        a = (cab_tail_x, y, floor_z)
        b = (tail_x, y, floor_z)
        c = (tail_x, y, wall_top_z - cap_h)
        d = (cab_tail_x, y, wall_top_z - cap_h)
        if side > 0:
            m.quad(a, b, c, d, body_cell)
            m.quad(d, c, (tail_x, y, wall_top_z), (cab_tail_x, y, wall_top_z), dark_cell)
        else:
            m.quad(d, c, b, a, body_cell)
            m.quad((cab_tail_x, y, wall_top_z), (tail_x, y, wall_top_z), c, d, dark_cell)
        yi = y * 0.94
        ai, bi = (cab_tail_x, yi, floor_z), (tail_x, yi, floor_z)
        ci, di = (tail_x, yi, wall_top_z), (cab_tail_x, yi, wall_top_z)
        if side > 0:
            m.quad(bi, ai, di, ci, dark_cell)
        else:
            m.quad(ai, bi, ci, di, dark_cell)
    handle_z = floor_z + (wall_top_z - floor_z) * 0.85
    m.quad((tail_x, -rail_w, floor_z), (tail_x, -rail_w, handle_z - 1.5),
           (tail_x, rail_w, handle_z - 1.5), (tail_x, rail_w, floor_z), body_cell)
    m.quad((tail_x, -rail_w, handle_z - 1.5), (tail_x, -rail_w, handle_z + 1.5),
           (tail_x, rail_w, handle_z + 1.5), (tail_x, rail_w, handle_z - 1.5), dark_cell)
    m.quad((tail_x, -rail_w, handle_z + 1.5), (tail_x, -rail_w, wall_top_z),
           (tail_x, rail_w, wall_top_z), (tail_x, rail_w, handle_z + 1.5), body_cell)
    return tail_x


def tailgate_lights(m, tail_x, half_width, z_center, cell, size=(14.0, 10.0)):
    """Taillight rectangles proud of the flat tailgate face."""
    hw, hh = size
    y_out = half_width - hw - 4.0
    for side in (1, -1):
        y0 = side * y_out
        y1 = side * (y_out + hw)
        z0, z1 = z_center - hh / 2, z_center + hh / 2
        a, b, c, d = (tail_x - 0.5, y0, z0), (tail_x - 0.5, y1, z0), \
                     (tail_x - 0.5, y1, z1), (tail_x - 0.5, y0, z1)
        if side > 0:
            m.quad(a, b, c, d, cell)
        else:
            m.quad(d, c, b, a, cell)


def light_patch(m, x, local_width_fn, z_center, cell, size=(16.0, 9.0)):
    """Small proud rectangular patch on the flank near x - twin headlight
    pods, rally auxiliary lights, side intakes."""
    hw, hh = size
    y = local_width_fn(x) + 0.35
    x0, x1 = x - hw / 2, x + hw / 2
    z0, z1 = z_center - hh / 2, z_center + hh / 2
    for side in (1, -1):
        a, b, c, d = (x0, side * y, z0), (x1, side * y, z0), \
                     (x1, side * y, z1), (x0, side * y, z1)
        if side > 0:
            m.quad(a, b, c, d, cell)
        else:
            m.quad(d, c, b, a, cell)


# ================================================================== presets
def build_muscle():
    """Long-hood/short-deck pony coupe. Hood 230->44 = 186 cm (~44% of the
    420 cm body) with the cowl/A-pillar rising steeply at x=44->20 (52->118
    cm), a low long roof peaking just behind center, then a fastback slope
    from x=-40 down to a short squared decklid."""
    body, dark = 0, 1
    profile = [
        (230.0, 4.0, 32.0), (214.0, -2.0, 40.0), (170.0, -6.0, 48.0),
        (44.0, -8.0, 52.0), (20.0, -8.0, 118.0), (-40.0, -8.0, 122.0),
        (-90.0, -6.0, 112.0), (-150.0, -2.0, 62.0), (-190.0, 2.0, 40.0),
    ]
    plan = [
        (230.0, 54.0), (214.0, 66.0), (170.0, 78.0), (44.0, 79.0),
        (-40.0, 79.0), (-90.0, 80.0), (-150.0, 84.0), (-190.0, 68.0),
    ]
    front_axle_x, rear_axle_x, half_track, wheel_r = 140.0, -150.0, 90.0, 36.0
    style = style_for(dict(
        belt_z=48.0, bottom_width_frac=0.74, round_width_frac=0.82,
        sill_width_frac=0.95, hood_top_width_frac=0.90, glass_inset_cm=5.0,
        bulge_strength=0.15, bulge_sigma=56.0,
        front_bulge_mult=0.85, rear_bulge_mult=1.25,   # emphasize rear haunches
        arch_margin_cm=10.0, cap_trim_cell=CHROME,
    ))

    m = Mesh()
    local_w = build_hull(m, profile, plan, body, dark, style,
                          front_axle_x, rear_axle_x, wheel_r)
    mirrors(m, 30.0, local_w, 58.0)
    arch_lip(m, front_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    arch_lip(m, rear_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    lip_spoiler(m, -186.0, local_w(-186.0), 42.0)
    exhaust_tips(m, -206.0, half_track, 8.0)
    tris = m.save("SM_Car_Muscle")
    length = profile[0][0] - profile[-1][0]
    print(f"  Muscle: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Muscle", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1620.0)


def build_jdm():
    """Smooth 90s fastback: shorter hood than Muscle (214->64 = 150 cm,
    ~36% of 420 cm), a roomier 122 cm roof (42 to -80), then a long clean
    fastback taper (70 cm, -80 to -150) to a narrow hatch tail."""
    body, dark = 2, 3
    profile = [
        (214.0, 2.0, 28.0), (200.0, -4.0, 34.0), (160.0, -6.0, 42.0),
        (64.0, -8.0, 46.0), (42.0, -8.0, 110.0), (-20.0, -8.0, 114.0),
        (-80.0, -6.0, 104.0), (-150.0, -2.0, 58.0), (-206.0, 2.0, 30.0),
    ]
    plan = [
        (214.0, 50.0), (200.0, 60.0), (160.0, 70.0), (64.0, 72.0),
        (-20.0, 72.0), (-80.0, 68.0), (-150.0, 56.0), (-206.0, 42.0),
    ]
    front_axle_x, rear_axle_x, half_track, wheel_r = 124.0, -114.0, 80.0, 32.0
    style = style_for(dict(
        belt_z=46.0, bottom_width_frac=0.72, round_width_frac=0.80,
        sill_width_frac=0.92, hood_top_width_frac=0.86, glass_inset_cm=4.5,
        bulge_strength=0.09, bulge_sigma=46.0,
        front_bulge_mult=1.0, rear_bulge_mult=1.05,
        arch_margin_cm=9.0,
    ))

    m = Mesh()
    local_w = build_hull(m, profile, plan, body, dark, style,
                          front_axle_x, rear_axle_x, wheel_r)
    mirrors(m, 60.0, local_w, 50.0)
    arch_lip(m, front_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    arch_lip(m, rear_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    light_patch(m, 210.0, local_w, 18.0, HEADLIGHT, size=(11.0, 7.0))
    light_patch(m, 210.0, local_w, 29.0, HEADLIGHT, size=(11.0, 6.0))
    lip_spoiler(m, -150.0, local_w(-150.0), 30.0, chord=10.0)
    tris = m.save("SM_Car_JDM")
    length = profile[0][0] - profile[-1][0]
    print(f"  JDM: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="JDM", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1380.0)


def build_super():
    """Mid-engine wedge: short 102 cm cab-forward hood (206->104), a steep
    18 cm windshield rise to a 114 cm roof peak (per spec), a short canopy,
    then a long flat 130 cm engine deck/tail (x=-10 to -195, mostly flat
    z 64->60)."""
    body, dark = 4, 5
    profile = [
        (206.0, -2.0, 14.0), (190.0, -6.0, 20.0), (150.0, -8.0, 26.0),
        (104.0, -8.0, 30.0), (86.0, -8.0, 110.0), (40.0, -8.0, 114.0),
        (-10.0, -8.0, 100.0), (-140.0, -6.0, 64.0), (-195.0, -4.0, 60.0),
        (-215.0, 0.0, 54.0),
    ]
    plan = [
        (206.0, 48.0), (190.0, 62.0), (150.0, 76.0), (104.0, 78.0),
        (40.0, 78.0), (-10.0, 80.0), (-80.0, 86.0), (-150.0, 94.0),
        (-195.0, 90.0), (-215.0, 82.0),
    ]
    front_axle_x, rear_axle_x, half_track, wheel_r = 118.0, -150.0, 94.0, 36.0
    style = style_for(dict(
        belt_z=28.0, bottom_width_frac=0.76, round_width_frac=0.84,
        sill_width_frac=0.96, hood_top_width_frac=0.84, glass_inset_cm=5.0,
        bulge_strength=0.13, bulge_sigma=44.0,
        front_bulge_mult=0.75, rear_bulge_mult=1.35,   # huge rear haunches
        arch_margin_cm=9.0,
    ))

    m = Mesh()
    local_w = build_hull(m, profile, plan, body, dark, style,
                          front_axle_x, rear_axle_x, wheel_r)
    mirrors(m, 92.0, local_w, 36.0)
    arch_lip(m, front_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    arch_lip(m, rear_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    light_patch(m, -105.0, local_w, 4.0, PLASTIC, size=(20.0, 14.0))   # side intake, ahead of rear wheel
    rear_wing(m, deck_x=-198.0, deck_z=58.0, half_span=76.0, stanchion_h=26.0)
    diffuser_stripe(m, tail_x=-210.0, half_width=80.0, z_bottom=0.0)
    tris = m.save("SM_Car_Super")
    length = profile[0][0] - profile[-1][0]
    height = 114.0 + wheel_r
    print(f"  Super: length {length:.0f} cm, roof peak 114 cm (height ~{height:.0f} cm), "
          f"wheelbase {front_axle_x - rear_axle_x:.0f} cm, axle X +{front_axle_x:.0f}/"
          f"{rear_axle_x:.0f}, half-track {half_track:.0f}, wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Super", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1430.0)


def build_pickup():
    """Upright crew cab (steep A-pillar to a flat 138 cm roof) then an
    UPRIGHT cab-back wall dropping straight to bed-rail height (x=8->-2,
    130->64 cm over just 10 cm - deliberately near-vertical, not a taper) -
    the non-lofted bed (build_pickup_bed) picks up flush from there."""
    body, dark = 6, 7
    profile = [
        (248.0, 16.0, 46.0), (230.0, 10.0, 54.0), (188.0, 8.0, 62.0),
        (130.0, 8.0, 68.0), (112.0, 8.0, 136.0), (20.0, 8.0, 138.0),
        (8.0, 8.0, 130.0), (-2.0, 8.0, 64.0),
    ]
    plan = [
        (248.0, 56.0), (230.0, 66.0), (188.0, 74.0), (130.0, 76.0),
        (112.0, 76.0), (20.0, 76.0), (8.0, 74.0), (-2.0, 72.0),
    ]
    front_axle_x, rear_axle_x, half_track, wheel_r = 185.0, -160.0, 98.0, 42.0
    style = style_for(dict(
        belt_z=64.0, bottom_width_frac=0.78, round_width_frac=0.86,
        sill_width_frac=0.96, hood_top_width_frac=0.92, glass_inset_cm=4.0,
        bulge_strength=0.16, bulge_sigma=50.0,   # big flared arches
        front_bulge_mult=1.1, rear_bulge_mult=1.15,
        arch_margin_cm=12.0, cap_trim_cell=CHROME,
    ))

    m = Mesh()
    # rear_axle_x is outside the cab's own profile domain (it's under the
    # welded-on bed) - passed through anyway so build_hull's bulge/arch math
    # is a harmless no-op there (its gaussian falls off well before the cab).
    local_w = build_hull(m, profile, plan, body, dark, style,
                          front_axle_x, rear_axle_x, wheel_r)
    mirrors(m, 108.0, local_w, 72.0)
    arch_lip(m, front_axle_x, wheel_r, local_w, style["arch_margin_cm"])

    cab_tail_x = -2.0
    floor_z, wall_top_z = 8.0, 58.0
    bed_half_width = local_w(cab_tail_x) * 0.94
    tail_x = build_pickup_bed(m, cab_tail_x, floor_z, wall_top_z, bed_half_width,
                               bed_length=175.0, body_cell=body, dark_cell=dark)
    tailgate_lights(m, tail_x, bed_half_width, (floor_z + wall_top_z) / 2.0, TAILLIGHT)
    # rear wheel sits under the welded bed, outside the hull loft's domain -
    # local_w extrapolates by clamping to the cab-tail's width, close enough
    # for this stylized flare-lip trim.
    arch_lip(m, rear_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    m.quad((90.0, -30.0, 139.0), (66.0, -30.0, 139.0), (66.0, 30.0, 139.0),
           (90.0, 30.0, 139.0), CHROME)   # roof light bar
    tris = m.save("SM_Car_Pickup")
    length = profile[0][0] - tail_x
    height = 138.0 + wheel_r
    print(f"  Pickup: length {length:.0f} cm, height ~{height:.0f} cm, wheelbase "
          f"{front_axle_x - rear_axle_x:.0f} cm, axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, "
          f"half-track {half_track:.0f}, wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Pickup", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=2380.0)


def build_rally():
    """Compact hatch: short 90 cm front overhang (nose 204 to axle 112),
    tall-for-its-length 114 cm greenhouse (72 to -60), a STEEP 40 cm hatch
    drop (-60 to -100, 104->54 cm), short 32 cm rear overhang (axle -118 to
    tail -150)."""
    body, dark = 8, 9
    profile = [
        (204.0, 4.0, 28.0), (190.0, 0.0, 36.0), (150.0, -2.0, 44.0),
        (88.0, -4.0, 48.0), (72.0, -4.0, 116.0), (6.0, -4.0, 120.0),
        (-60.0, -2.0, 104.0), (-100.0, 0.0, 54.0), (-150.0, 4.0, 34.0),
    ]
    plan = [
        (204.0, 50.0), (190.0, 60.0), (150.0, 68.0), (88.0, 70.0),
        (6.0, 70.0), (-60.0, 66.0), (-100.0, 62.0), (-150.0, 50.0),
    ]
    front_axle_x, rear_axle_x, half_track, wheel_r = 112.0, -118.0, 78.0, 32.0
    style = style_for(dict(
        belt_z=44.0, bottom_width_frac=0.74, round_width_frac=0.82,
        sill_width_frac=0.94, hood_top_width_frac=0.88, glass_inset_cm=4.0,
        bulge_strength=0.18, bulge_sigma=42.0,   # flared arches both ends
        front_bulge_mult=1.15, rear_bulge_mult=1.15,
        arch_margin_cm=11.0,
    ))

    m = Mesh()
    local_w = build_hull(m, profile, plan, body, dark, style,
                          front_axle_x, rear_axle_x, wheel_r)
    mirrors(m, 58.0, local_w, 50.0)
    arch_lip(m, front_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    arch_lip(m, rear_axle_x, wheel_r, local_w, style["arch_margin_cm"])
    light_patch(m, 194.0, local_w, 12.0, HEADLIGHT, size=(9.0, 9.0))
    lip_spoiler(m, -96.0, local_w(-96.0), 56.0, chord=8.0)
    tris = m.save("SM_Car_Rally")
    length = profile[0][0] - profile[-1][0]
    print(f"  Rally: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Rally", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1310.0)


def build_wheel():
    """Shared wheel mesh: 16-gon cylinder, dark tire tread + lighter rim
    face, built once at REF_WHEEL_RADIUS_CM (34 cm)."""
    w = Mesh()
    R, HW, SEG, HUB_R = REF_WHEEL_RADIUS_CM, 13.0, 16, 16.0
    for k in range(SEG):
        a0 = 2 * math.pi * k / SEG
        a1 = 2 * math.pi * (k + 1) / SEG
        p0 = (R * math.cos(a0), 0, R * math.sin(a0))
        p1 = (R * math.cos(a1), 0, R * math.sin(a1))
        w.quad((p0[0], -HW, p0[2]), (p1[0], -HW, p1[2]),
               (p1[0], HW, p1[2]), (p0[0], HW, p0[2]), TIRE)
        for side in (1, -1):
            q0 = (HUB_R * math.cos(a0), side * HW, HUB_R * math.sin(a0))
            q1 = (HUB_R * math.cos(a1), side * HW, HUB_R * math.sin(a1))
            f0 = (p0[0], side * HW, p0[2])
            f1 = (p1[0], side * HW, p1[2])
            hub0 = (0, side * HW, 0)
            if side > 0:
                w.quad(f0, f1, q1, q0, TIRE)
                w.tri(q0, q1, hub0, RIM)
            else:
                w.quad(q0, q1, f1, f0, TIRE)
                w.tri(hub0, q1, q0, RIM)
    w.save("SM_CarWheel")


if __name__ == "__main__":
    print("Building procedural car set (5 presets, 1 shared wheel)...")
    muscle = build_muscle()
    jdm = build_jdm()
    super_ = build_super()
    pickup = build_pickup()
    rally = build_rally()
    build_wheel()
    write_palette()
    (OUT / "LICENSE.txt").write_text(
        "Procedural car meshes generated by pipeline/make_car_meshes.py.\n"
        "Original procedural content, no third-party assets.\n")
    print("wrote LICENSE.txt")

    mesh_names = {
        "SM_Car_Muscle": muscle, "SM_Car_JDM": jdm, "SM_Car_Super": super_,
        "SM_Car_Pickup": pickup, "SM_Car_Rally": rally,
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
    for d in (muscle, jdm, super_, pickup, rally):
        print(f"  {d}")
