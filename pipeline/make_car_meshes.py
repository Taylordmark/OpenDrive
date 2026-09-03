"""Procedural low-poly car body generator - Phase 4a replica-style rework
(2026-09-03, badge-free silhouette pass). Five selectable car types built
from the SAME parametric body builder introduced in the 2026-08-28/09-01
hull-builder reworks (loft profile stations + a beveled, shouldered
cross-section hull + fender bulges + tapered/rounded nose-tail closure),
retargeted from the earlier generic Sedan/Coupe/Sport/Proto set onto five
archetypes chosen to read as recognizable REAL-WORLD SILHOUETTES at a
glance, with no badges and no real names anywhere (file names, display
names, or geometry):

    MUSCLE - long-hood/short-deck pony coupe (Mustang/Camaro-flavored):
             very long hood, cabin set well back, short fastback deck,
             wide rear haunches, duckbill decklid lip, dual chrome exhaust
             tips, chrome-grille nose band.
    JDM    - smooth 90s sport coupe (Supra/RX-7-flavored): shorter hood
             than Muscle, roomier greenhouse, long clean fastback taper to
             a narrow hatch tail, twin stacked "pop-up" headlight pods,
             thin hatch lip spoiler.
    SUPER  - mid-engine wedge supercar (Ferrari/Lambo-flavored): very low
             (~115 cm) cab-forward wedge, short low nose, huge rear haunch
             crest over the rear axle, wide flat engine deck, big fixed
             wing, diffuser stripe, side intake patches ahead of the rear
             wheel.
    PICKUP - off-road crew-cab pickup (Raptor-flavored): raised ride
             height, tall boxy cab, separate open PICKUP BED welded on
             behind the cab (side rails + floor + tailgate - the one
             archetype whose rear is NOT a lofted taper), big flared
             wheel arches, chrome brush-guard nose band, roof light bar.
    RALLY  - compact rally hatch (WRX/Group-A-hatch-flavored): short
             overhangs both ends, tall greenhouse for its length, flared
             arches, twin round auxiliary lights low on the front bumper,
             steep hatch glass, small roof-trailing-edge spoiler.

Same ONE shared wheel mesh (visually rescaled per class) reused by all
five, same faceted-solid + palette-strip conventions as
pipeline/make_car_mesh.py / pipeline/make_car_gt.py (untouched placeholder
cars - this is still a fully additive generation, not a replacement).

2026-09-03 material-slot split (this pass): earlier generations wrote every
triangle into ONE OBJ material group sharing a single flat-shaded palette
texture (fine for color, but "body paint / glass / chrome / black plastic /
tire rubber / wheel rim / lights" all want different ROUGHNESS/METALLIC/
OPACITY, which a single material can't express per-triangle). Mesh.save()
now groups triangles by MATERIAL CATEGORY (see CATEGORY_OF below) and OBJ
`usemtl`s between groups, writing a companion .mtl - so Blender-style DCC
tools AND Unreal's OBJ importer (which creates one material slot per
`usemtl` name) both see seven real material slots per car, still sampling
the SAME shared palette texture (so no new texture work), differing only in
which UE material (with its own metallic/roughness/opacity) each slot's
importer stub gets pointed at. See unreal/import_car_meshes.py for the
importer-side half of this.

Geometry contract with AODVehiclePawn / UODVehicleMovement (UNCHANGED by
this rework): OBJ pivot sits at wheelbase center at WHEEL AXLE height z=0.
Per-car axle geometry (front/rear axle x, half-track y, wheel radius) is
printed at the end of each preset's build and also written to
obj/cars_manifest.json - transfer/read these into that car's UODVehicleDef
fields so the visual wheel positions match the physics wheel table
UODVehicleMovement::SetupWheels builds from the same def. Wheel ARCHES here
are cosmetic dark inset patches on the (bulged) hull surface at the axle
positions - a real boolean cutout is not attempted, keeping every body but
Pickup's a single clean loft with no holes; Pickup additionally welds on a
non-lofted bed structure behind the cab (see build_pickup_bed()).

The wheel mesh (SM_CarWheel) is built ONCE at REF_WHEEL_RADIUS_CM; each
car's own WheelRadiusCm may differ (Pickup rides on the biggest wheels,
Super on the lowest-profile ones) - AODVehiclePawn::ApplyVehicleDef
visually rescales the shared mesh per car (WheelRadiusCm / REF_WHEEL_
RADIUS_CM) rather than baking five near-identical wheel meshes.

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
# shared across all five cars + the wheel: each car's BODY (and DARK
# lower-panel accent) gets its own swatch pair so a future recolor is a
# one-pixel edit; everything else (glass/chrome/plastic/tire/hub/lights/
# accent) is shared cell-for-cell the way tree species share trunk/leaf
# cells in make_tree_meshes.py.
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
# backward-compat aliases used throughout this file
TRIM, HUB = PLASTIC, RIM

# ---------------------------------------------------------- material slots
# Seven UE-facing material categories (see module docstring). Every palette
# cell index maps to exactly one category; Mesh.save() groups triangles by
# category so each becomes its own OBJ usemtl group / UE import material
# slot, independent of which of the 18 palette cells (i.e. which car/which
# color) a given triangle happens to sample.
CATEGORY_OF = {
    0: "body", 1: "plastic", 2: "body", 3: "plastic", 4: "body", 5: "plastic",
    6: "body", 7: "plastic", 8: "body", 9: "plastic",
    GLASS: "glass", PLASTIC: "plastic", TIRE: "tire", RIM: "rim",
    HEADLIGHT: "lights", TAILLIGHT: "lights", ACCENT: "plastic", CHROME: "chrome",
}
CATEGORY_ORDER = ["body", "plastic", "glass", "chrome", "tire", "rim", "lights"]
# rough Kd per category for the companion .mtl (viewport-preview only; UE
# import overrides these with real M_Car* materials - see import script)
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
    """Same faceted-solid Mesh class as make_car_mesh.py/make_car_gt.py -
    flat per-face normals, one UV per face into the shared palette strip -
    plus (2026-09-03) grouping triangles by material CATEGORY on save() so
    the OBJ carries real multi-material-slot structure (see module
    docstring's "material-slot split" note)."""

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
# Named style parameters - defaults here, per-preset overrides in each
# build_*() below via style_for(). Nothing here is baked into station data;
# the (x, half_width, z_bottom, z_top) station lists in each preset stay
# exactly the hand-tunable interface they were.
STYLE_DEFAULTS = dict(
    # cross-section: a 4-point profile - bottom, sill top, shoulder
    # (beltline, full width), top deck edge. Real low-poly cars have
    # NEAR-VERTICAL sides: the fractions below stay within ~10% of full
    # width, the crease at the shoulder does the work.
    shoulder_frac=0.62,
    sill_frac=0.20,          # dark rocker band tops out here (fraction of band height)
    bottom_width_frac=0.90,
    top_width_frac=0.90,
    # fender bulges: gaussian width multiplier centered on each axle x;
    # resample_spacing controls how finely the loft is resampled along x
    # before the bulge is applied.
    resample_spacing=20.0,
    bulge_strength=0.14,
    bulge_sigma=54.0,
    front_bulge_mult=1.0,   # per-axle relative strength multipliers
    rear_bulge_mult=1.0,
    # nose/tail closure: extend the loft this far past the preset's own
    # first/last station, tapering width down to w * taper_frac and the
    # (z_top - z_bottom) band down to band * height_frac at the very tip.
    # Ends stay chamfered-box: the tip keeps most of its width/height and
    # the small remaining flat cap carries the headlight/taillight bands
    # (cap_banded).
    nose_extend=24.0,
    nose_taper_frac=0.62,
    nose_height_frac=0.55,
    tail_extend=20.0,
    tail_taper_frac=0.68,
    tail_height_frac=0.62,
    tail_style="bevel",   # "pinch" | "bevel" | "flat"
    cap_trim_cell=None,    # None -> PLASTIC (default); some presets use CHROME
)


def style_for(overrides):
    return {**STYLE_DEFAULTS, **overrides}


def cross_profile(w, zb, zt, mult, style):
    """4-point (bottom, sill top, shoulder, top) cross-section profile for
    one station. Near-vertical sides: bottom/top tuck in only slightly, the
    beltline crease at the shoulder carries the shape. `mult` is this
    station's local fender-bulge multiplier (1.0 = no bulge); applied in
    full at the shoulder and below, attenuated at the deck edge."""
    band = zt - zb
    sill_z = zb + band * style["sill_frac"]
    shoulder_z = zb + band * style["shoulder_frac"]
    top_mult = 1.0 + (mult - 1.0) * 0.35
    bw = w * style["bottom_width_frac"] * mult
    sw = w * 0.985 * mult
    shw = w * mult
    tw = w * style["top_width_frac"] * top_mult
    return [(zb, bw), (sill_z, sw), (shoulder_z, shw), (zt, tw)]


def resample_stations(sections, spacing):
    """Linear-interpolate the (sparse, hand-tuned) station list along x to
    ~`spacing` cm steps, preserving every original station exactly (each
    segment's f=1 endpoint is the next original station verbatim) so fender
    bulges have enough resolution to read as a smooth bulge, not a kink."""
    out = [tuple(sections[0])]
    for s0, s1 in zip(sections, sections[1:]):
        x0, w0, b0, t0 = s0
        x1, w1, b1, t1 = s1
        n = max(1, int(round(abs(x1 - x0) / spacing)))
        for i in range(1, n + 1):
            f = i / n
            out.append((x0 + (x1 - x0) * f, w0 + (w1 - w0) * f,
                        b0 + (b1 - b0) * f, t0 + (t1 - t0) * f))
    return out


def extend_ends(sections, style):
    """Two synthetic tapering stations per end, extending the loft past the
    preset's own first ("nose") and last ("tail") station so width AND
    height both close down smoothly instead of a flat end wall. Returns
    (nose_stations, tail_stations), each ordered outward-to-inward /
    inward-to-outward respectively so concatenating
    nose_stations + sections + tail_stations stays x-monotonic."""
    x0, w0, b0, t0 = sections[0]
    x1, w1, b1, t1 = sections[-1]
    dir_sign = 1.0 if x0 >= x1 else -1.0

    def taper(x_base, w_base, b_base, t_base, extend, taper_frac, height_frac, sign):
        mid_w = w_base * (1.0 + taper_frac) / 2.0
        tip_w = w_base * taper_frac
        midc = (b_base + t_base) / 2.0
        hh = (t_base - b_base) / 2.0
        mid_hh = hh * (1.0 + height_frac) / 2.0
        tip_hh = hh * height_frac
        mid = (x_base + sign * extend * 0.5, mid_w, midc - mid_hh, midc + mid_hh)
        tip = (x_base + sign * extend, tip_w, midc - tip_hh, midc + tip_hh)
        return mid, tip

    nose_mid, nose_tip = taper(x0, w0, b0, t0, style["nose_extend"],
                                style["nose_taper_frac"], style["nose_height_frac"], dir_sign)
    tail_mid, tail_tip = taper(x1, w1, b1, t1, style["tail_extend"],
                                style["tail_taper_frac"], style["tail_height_frac"], -dir_sign)
    return [nose_tip, nose_mid], [tail_mid, tail_tip]


def build_hull(m, sections, cab, body_cell, dark_cell, style,
                front_bulge_xy=None, rear_bulge_xy=None, skip_tail_cap=False):
    """Loft SECTIONS (front->back stations of (x, half_width, z_bottom,
    z_top)) into a beveled, fender-bulged, taper-closed hull, then plug the
    CAB quad (glass + roof) across the two stations that bound the cabin.
    Generalized over any station list + style dict so one function serves
    all five presets. Returns local_width(x): the ACTUAL bulged/tapered
    shoulder half-width at any x (plus local_width.band(x) for the
    (z_bottom, z_top) hull band at x), for wheel arches / mirrors / light
    patches to key off instead of a hand-guessed constant.

    skip_tail_cap=True (Pickup only) omits the rear cap_banded pass - the
    non-lofted bed structure (build_pickup_bed()) enclosess/replaces it, so
    a lit tail cap there would be wasted (and instantly-hidden) geometry."""
    nose_ext, tail_ext = extend_ends(sections, style)
    full = nose_ext + list(sections) + tail_ext
    stations = resample_stations(full, style["resample_spacing"])

    bulges = []
    if front_bulge_xy is not None:
        bulges.append((front_bulge_xy, style["bulge_sigma"],
                       style["bulge_strength"] * style["front_bulge_mult"]))
    if rear_bulge_xy is not None:
        bulges.append((rear_bulge_xy, style["bulge_sigma"],
                       style["bulge_strength"] * style["rear_bulge_mult"]))

    def mult_at(x):
        mult = 1.0
        for ax, sigma, strength in bulges:
            mult += strength * math.exp(-((x - ax) / sigma) ** 2)
        return mult

    xs_base = [s[0] for s in stations]
    ws_base = [s[1] for s in stations]
    bs_base = [s[2] for s in stations]
    ts_base = [s[3] for s in stations]
    order = sorted(range(len(xs_base)), key=lambda i: xs_base[i])
    xs_sorted = [xs_base[i] for i in order]
    ws_sorted = [ws_base[i] for i in order]
    bs_sorted = [bs_base[i] for i in order]
    ts_sorted = [ts_base[i] for i in order]

    def local_width(x):
        w = float(np.interp(x, xs_sorted, ws_sorted))
        return w * mult_at(x)

    def local_band(x):
        return (float(np.interp(x, xs_sorted, bs_sorted)),
                float(np.interp(x, xs_sorted, ts_sorted)))
    local_width.band = local_band

    profiles = [cross_profile(w, b, t, mult_at(x), style) for (x, w, b, t) in stations]

    # side bands are BODY color except the thin sill band - painting
    # everything below the shoulder dark made whole cars read as black
    # blobs in-game.
    band_cells = [dark_cell, body_cell, body_cell]
    for i in range(len(stations) - 1):
        x0 = stations[i][0]
        x1 = stations[i + 1][0]
        p0, p1 = profiles[i], profiles[i + 1]
        for k in range(3):
            (za0, wa0), (zb0_, wb0) = p0[k], p0[k + 1]
            (za1, wa1), (zb1_, wb1) = p1[k], p1[k + 1]
            for side in (1, -1):
                a = (x0, side * wa0, za0)
                b = (x1, side * wa1, za1)
                c = (x1, side * wb1, zb1_)
                d = (x0, side * wb0, zb0_)
                if side > 0:
                    m.quad(a, b, c, d, band_cells[k])
                else:
                    m.quad(d, c, b, a, band_cells[k])
        (zt0, tw0), (zt1, tw1) = p0[3], p1[3]
        (zf0, bw0), (zf1, bw1) = p0[0], p1[0]
        m.quad((x0, -tw0, zt0), (x1, -tw1, zt1), (x1, tw1, zt1), (x0, tw0, zt0), body_cell)   # roof/hood/deck
        m.quad((x0, bw0, zf0), (x1, bw1, zf1), (x1, -bw1, zf1), (x0, -bw0, zf0), dark_cell)    # floor pan

    def cap_banded(st, prof, light_cell, flip, trim_cell):
        """Small flat end cap split into light / trim / light vertical bands
        (like a bumper face with lights at the corners). `flip` fixes the
        winding so the face points outward at each end."""
        x, w, b, t = st
        w = max(pt[1] for pt in prof)
        lw = w * 0.42   # trim band half-width (lights outboard of this)
        bands = [(-w, -lw, light_cell), (-lw, lw, trim_cell), (lw, w, light_cell)]
        for y0, y1, cell in bands:
            a, bq, c, d = (x, y0, b), (x, y0, t), (x, y1, t), (x, y1, b)
            if flip:
                m.quad(d, c, bq, a, cell)
            else:
                m.quad(a, bq, c, d, cell)

    trim_cell = style["cap_trim_cell"] if style["cap_trim_cell"] is not None else PLASTIC
    cap_banded(stations[0], profiles[0], HEADLIGHT, flip=False, trim_cell=trim_cell)
    if not skip_tail_cap:
        cap_banded(stations[-1], profiles[-1], TAILLIGHT, flip=True, trim_cell=trim_cell)

    (cx0, cw0, cz0), (rx0, rw0, rz0), (rx1, rw1, rz1), (cx1, cw1, cz1) = cab
    m.quad((cx0, -cw0, cz0), (cx0, cw0, cz0), (rx0, rw0, rz0),
           (rx0, -rw0, rz0), GLASS)                                  # windshield
    m.quad((cx1, cw1, cz1), (cx1, -cw1, cz1), (rx1, -rw1, rz1),
           (rx1, rw1, rz1), GLASS)                                   # rear glass
    for side in (1, -1):
        a = (cx0, side * cw0, cz0)
        b = (rx0, side * rw0, rz0)
        c = (rx1, side * rw1, rz1)
        d = (cx1, side * cw1, cz1)
        if side > 0:
            m.quad(a, b, c, d, GLASS)
        else:
            m.quad(d, c, b, a, GLASS)
    m.quad((rx0, -rw0, rz0), (rx0, rw0, rz0), (rx1, rw1, rz1),
           (rx1, -rw1, rz1), body_cell)                              # roof

    return local_width


def wheel_arch(m, axle_x, local_width_fn, wheel_r, z_center, flare=1.0):
    """Dark inset patch flush on each side of the hull at one axle position,
    y computed from the LOCAL bulged surface width so it still sits just
    proud of the (now curved/bulged) hull - the "fender-bulge box with dark
    inset" fallback for a wheel-well cutout (see module docstring). `flare`
    widens/heightens the patch (Pickup/Rally off-road flares)."""
    arch_w = wheel_r * 1.5 * flare
    x0, x1 = axle_x - arch_w / 2, axle_x + arch_w / 2
    # clamp the patch INSIDE the hull side band - an unclamped patch hangs
    # below the floor pan and reads as a floating black mudflap
    hb, ht = local_width_fn.band(axle_x)
    z0 = hb + 1.5
    z1 = min(ht - 3.0, z_center + wheel_r * 0.50 * flare)
    if z1 <= z0:
        return
    y = local_width_fn(axle_x) + 0.4   # sit just proud of the hull to avoid z-fight
    for side in (1, -1):
        a = (x0, side * y, z0)
        b = (x1, side * y, z0)
        c = (x1, side * y, z1)
        d = (x0, side * y, z1)
        if side > 0:
            m.quad(a, b, c, d, PLASTIC)
        else:
            m.quad(d, c, b, a, PLASTIC)


def light_patch(m, x, local_width_fn, z_center, cell, size=(16.0, 9.0)):
    """Small proud rectangular patch on the flank near x, used for headlight
    / taillight / auxiliary-light bands on the raked nose/tail surfaces -
    same "decal proud of the hull" construction as wheel_arch, just
    sized/colored differently."""
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
           (x1, y0, wing_z + 2), ACCENT)                              # top
    m.quad((x0, y0, wing_z - 2), (x1, y0, wing_z - 2), (x1, y1, wing_z - 2),
           (x0, y1, wing_z - 2), ACCENT)                              # bottom


def diffuser_stripe(m, tail_x, half_width, z_bottom):
    """Dark full-width diffuser suggestion under the tail (Super)."""
    x0, x1 = tail_x + 16, tail_x - 16
    m.quad((x0, -half_width, z_bottom), (x1, -half_width, z_bottom),
           (x1, half_width, z_bottom), (x0, half_width, z_bottom), ACCENT)


def lip_spoiler(m, x, half_width, z, thickness=3.0, chord=14.0, cell=ACCENT):
    """Thin full(ish)-width blade hugging the decklid/hatch trailing edge -
    Muscle's duckbill and JDM/Rally's hatch lip, as opposed to Super's
    raised stanchion wing."""
    w = half_width * 0.92
    x0, x1 = x - chord / 2, x + chord / 2
    z0, z1 = z, z + thickness
    m.quad((x0, -w, z0), (x1, -w, z0), (x1, w, z0), (x0, w, z0), cell)   # bottom
    m.quad((x0, -w, z1), (x0, w, z1), (x1, w, z1), (x1, -w, z1), cell)   # top
    m.quad((x1, -w, z0), (x1, -w, z1), (x1, w, z1), (x1, w, z0), cell)   # trailing edge


def exhaust_tips(m, x, half_track, z, cell=CHROME, r=4.0, length=10.0):
    """Two small chrome box "pipes" poking from the rear bumper."""
    for side in (1, -1):
        y = side * half_track * 0.45
        x0, x1 = x, x - length
        y0, y1 = y - r, y + r
        z0, z1 = z - r, z + r
        m.quad((x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0), cell)   # bottom
        m.quad((x1, y0, z1), (x1, y1, z1), (x0, y1, z1), (x0, y0, z1), cell)   # top
        m.quad((x1, y0, z0), (x1, y0, z1), (x1, y1, z1), (x1, y1, z0), cell)   # rear face
        m.quad((x1, y0, z0), (x0, y0, z0), (x0, y0, z1), (x1, y0, z1), cell)
        m.quad((x1, y1, z0), (x1, y1, z1), (x0, y1, z1), (x0, y1, z0), cell)


def build_pickup_bed(m, cab_tail_x, floor_z, wall_top_z, half_width, bed_length,
                      body_cell, dark_cell):
    """Non-lofted structure welded on directly behind the cab - a truck bed
    doesn't taper into a canoe stern, it is an open box: floor + two side
    rails + a flat tailgate cap. Returns tail_x (tailgate x) for
    taillight/wheel-arch placement plus a local_width(x)-compatible
    callable for the rear wheel arch."""
    tail_x = cab_tail_x - bed_length
    rail_w = half_width

    # floor (bed-liner dark plastic)
    m.quad((cab_tail_x, -rail_w, floor_z), (tail_x, -rail_w, floor_z),
           (tail_x, rail_w, floor_z), (cab_tail_x, rail_w, floor_z), dark_cell)
    # side rails (outer face body-colored, thin dark cap on top edge)
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
        # inner face of the rail (visible looking down into the bed)
        yi = y * 0.94
        ai, bi = (cab_tail_x, yi, floor_z), (tail_x, yi, floor_z)
        ci, di = (tail_x, yi, wall_top_z), (cab_tail_x, yi, wall_top_z)
        if side > 0:
            m.quad(bi, ai, di, ci, dark_cell)
        else:
            m.quad(ai, bi, ci, di, dark_cell)
    # tailgate (flat cap, body-colored with a thin dark handle band)
    handle_z = floor_z + (wall_top_z - floor_z) * 0.85
    m.quad((tail_x, -rail_w, floor_z), (tail_x, -rail_w, handle_z - 1.5),
           (tail_x, rail_w, handle_z - 1.5), (tail_x, rail_w, floor_z), body_cell)
    m.quad((tail_x, -rail_w, handle_z - 1.5), (tail_x, -rail_w, handle_z + 1.5),
           (tail_x, rail_w, handle_z + 1.5), (tail_x, rail_w, handle_z - 1.5), dark_cell)
    m.quad((tail_x, -rail_w, handle_z + 1.5), (tail_x, -rail_w, wall_top_z),
           (tail_x, rail_w, wall_top_z), (tail_x, rail_w, handle_z + 1.5), body_cell)
    return tail_x


def tailgate_lights(m, tail_x, half_width, z_center, cell, size=(14.0, 10.0)):
    """Taillight rectangles proud of the flat tailgate face (Pickup) - same
    "proud patch" trick as light_patch, but against a flat plane instead of
    local_width_fn's curved hull."""
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


# ================================================================== presets
def build_muscle():
    """Long-hood/short-deck pony coupe: long hood, cabin set well back,
    short fastback deck, wide rear haunches, duckbill lip + dual exhaust +
    chrome grille band."""
    body, dark = 0, 1
    sections = [
        (216.0, 60.0, 2.0, 30.0),    # nose - wide aggressive face
        (198.0, 70.0, -4.0, 38.0),   # bumper/splitter
        (150.0, 78.0, -6.0, 46.0),   # front fender
        (30.0, 79.0, -8.0, 50.0),    # cowl - LONG hood run (216->30 = 186 cm)
        (-40.0, 79.0, -8.0, 50.0),   # cabin rear base - short greenhouse (70 cm)
        (-110.0, 83.0, -6.0, 46.0),  # rear fender - wide muscular haunches
        (-200.0, 70.0, 0.0, 34.0),   # short squared-off deck
    ]
    cab = [(30.0, 79.0, 50.0), (6.0, 60.0, 118.0),
           (-26.0, 60.0, 118.0), (-40.0, 79.0, 50.0)]   # short fastback greenhouse

    front_axle_x, rear_axle_x, half_track, wheel_r = 140.0, -150.0, 90.0, 36.0
    style = style_for(dict(
        bottom_width_frac=0.92, top_width_frac=0.90, shoulder_frac=0.58,
        nose_extend=14.0, nose_taper_frac=0.84, nose_height_frac=0.74,
        tail_extend=12.0, tail_taper_frac=0.90, tail_height_frac=0.84,
        tail_style="bevel",
        bulge_strength=0.15, bulge_sigma=56.0,
        front_bulge_mult=0.85, rear_bulge_mult=1.25,   # emphasize rear haunches
        cap_trim_cell=CHROME,   # chrome grille band across the nose
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 24.0, local_w, 58.0)

    wheel_arch(m, front_axle_x, local_w, wheel_r, 6.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 4.0)
    lip_spoiler(m, -196.0, local_w(-196.0), 40.0)
    exhaust_tips(m, -222.0, half_track, 6.0)
    tris = m.save("SM_Car_Muscle")
    length = sections[0][0] - sections[-1][0]
    print(f"  Muscle: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Muscle", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1620.0)


def build_jdm():
    """Smooth 90s sport coupe: shorter hood than Muscle, roomier greenhouse,
    long clean fastback taper to a narrow hatch tail, twin pop-up headlight
    pods, thin hatch lip."""
    body, dark = 2, 3
    sections = [
        (206.0, 54.0, 0.0, 26.0),    # nose - narrower/lower than Muscle
        (190.0, 64.0, -4.0, 34.0),   # bumper
        (146.0, 72.0, -6.0, 42.0),   # front fender
        (66.0, 73.0, -8.0, 46.0),    # cowl (206->66 = 140 cm hood)
        (-10.0, 73.0, -8.0, 46.0),   # cabin rear base - roomier 76 cm greenhouse
        (-92.0, 66.0, -4.0, 36.0),   # fastback taper 1
        (-160.0, 54.0, 0.0, 24.0),   # fastback taper 2
        (-206.0, 42.0, 2.0, 16.0),   # tail - narrow pinched hatch
    ]
    cab = [(66.0, 73.0, 46.0), (28.0, 56.0, 112.0),
           (-50.0, 56.0, 112.0), (-92.0, 66.0, 40.0)]   # long smooth fastback roof

    front_axle_x, rear_axle_x, half_track, wheel_r = 124.0, -114.0, 80.0, 32.0
    style = style_for(dict(
        bottom_width_frac=0.90, top_width_frac=0.86, shoulder_frac=0.62,
        nose_extend=16.0, nose_taper_frac=0.78, nose_height_frac=0.66,
        tail_extend=18.0, tail_taper_frac=0.64, tail_height_frac=0.58,
        tail_style="pinch",
        bulge_strength=0.09, bulge_sigma=46.0,
        front_bulge_mult=1.0, rear_bulge_mult=1.05,
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 62.0, local_w, 50.0)
    # twin stacked "pop-up" headlight pods, replacing the single wide band
    light_patch(m, 204.0, local_w, 20.0, HEADLIGHT, size=(12.0, 8.0))
    light_patch(m, 204.0, local_w, 32.0, HEADLIGHT, size=(12.0, 6.0))

    wheel_arch(m, front_axle_x, local_w, wheel_r, 4.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 0.0)
    lip_spoiler(m, -156.0, local_w(-156.0), 26.0, chord=10.0)
    tris = m.save("SM_Car_JDM")
    length = sections[0][0] - sections[-1][0]
    print(f"  JDM: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="JDM", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1380.0)


def build_super():
    """Mid-engine wedge supercar: very low cab-forward wedge, short low
    nose, huge rear haunch crest over the rear axle, wide flat engine deck,
    big fixed wing."""
    body, dark = 4, 5
    sections = [
        (210.0, 50.0, -2.0, 14.0),   # pointed low nose
        (192.0, 64.0, -6.0, 20.0),   # splitter
        (150.0, 78.0, -8.0, 26.0),   # front fender - wide, low
        (100.0, 80.0, -8.0, 30.0),   # cowl - SHORT nose (210->100 = 110 cm)
        (20.0, 80.0, -8.0, 30.0),    # cabin rear base - cab pushed forward
        (-60.0, 84.0, -8.0, 32.0),   # engine deck start widening
        (-150.0, 92.0, -6.0, 34.0),  # rear haunch CREST (at rear axle)
        (-215.0, 86.0, 0.0, 30.0),   # wide flat engine-cover tail
    ]
    cab = [(100.0, 80.0, 30.0), (60.0, 50.0, 78.0),
           (-10.0, 50.0, 78.0), (-60.0, 84.0, 32.0)]   # low narrow canopy

    front_axle_x, rear_axle_x, half_track, wheel_r = 118.0, -150.0, 94.0, 36.0
    style = style_for(dict(
        bottom_width_frac=0.92, top_width_frac=0.84, shoulder_frac=0.48,
        nose_extend=18.0, nose_taper_frac=0.70, nose_height_frac=0.52,
        tail_extend=10.0, tail_taper_frac=0.95, tail_height_frac=0.90,
        tail_style="flat",
        bulge_strength=0.11, bulge_sigma=42.0,
        front_bulge_mult=0.75, rear_bulge_mult=1.35,   # huge rear haunches
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 96.0, local_w, 34.0)

    wheel_arch(m, front_axle_x, local_w, wheel_r, -2.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, -2.0)
    light_patch(m, -105.0, local_w, 4.0, PLASTIC, size=(20.0, 14.0))   # side intake, ahead of rear wheel
    rear_wing(m, deck_x=-198.0, deck_z=30.0, half_span=76.0, stanchion_h=26.0)
    diffuser_stripe(m, tail_x=-210.0, half_width=80.0, z_bottom=0.0)
    tris = m.save("SM_Car_Super")
    length = sections[0][0] - sections[-1][0]
    height = cab[1][2] + wheel_r
    print(f"  Super: length {length:.0f} cm, height ~{height:.0f} cm, wheelbase "
          f"{front_axle_x - rear_axle_x:.0f} cm, axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, "
          f"half-track {half_track:.0f}, wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Super", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1430.0)


def build_pickup():
    """Off-road crew-cab pickup: raised ride height, tall boxy cab, a
    separate non-lofted bed welded on behind the cab, flared arches, chrome
    brush-guard nose band, roof light bar."""
    body, dark = 6, 7
    sections = [
        (250.0, 58.0, 14.0, 44.0),   # nose - tall flat brush-guard face
        (230.0, 68.0, 10.0, 52.0),   # bumper (raised ride height)
        (185.0, 76.0, 8.0, 60.0),    # front fender flare
        (120.0, 78.0, 8.0, 66.0),    # cowl (250->120 = 130 cm hood)
        (10.0, 78.0, 8.0, 66.0),     # crew-cab rear base (110 cm cabin)
        (-15.0, 74.0, 8.0, 64.0),    # cab back wall - near-flat, hidden behind the bed
    ]
    cab = [(120.0, 78.0, 66.0), (78.0, 58.0, 132.0),
           (18.0, 58.0, 132.0), (10.0, 78.0, 66.0)]   # tall boxy crew-cab greenhouse

    front_axle_x, rear_axle_x, half_track, wheel_r = 185.0, -160.0, 98.0, 42.0
    style = style_for(dict(
        bottom_width_frac=0.94, top_width_frac=0.92, shoulder_frac=0.55,
        nose_extend=12.0, nose_taper_frac=0.88, nose_height_frac=0.82,
        tail_extend=6.0, tail_taper_frac=0.96, tail_height_frac=0.96,
        tail_style="flat",
        bulge_strength=0.16, bulge_sigma=50.0,   # big flared arches
        front_bulge_mult=1.1, rear_bulge_mult=1.15,
        cap_trim_cell=CHROME,   # chrome brush-guard band
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x,
                          skip_tail_cap=True)
    mirrors(m, 116.0, local_w, 72.0)

    cab_tail_x = -15.0 - style["tail_extend"]   # actual x of the last (tapered) hull station
    floor_z, wall_top_z = 8.0, 58.0
    bed_half_width = local_w(cab_tail_x) * 0.94
    tail_x = build_pickup_bed(m, cab_tail_x, floor_z, wall_top_z, bed_half_width,
                               bed_length=175.0, body_cell=body, dark_cell=dark)
    tailgate_lights(m, tail_x, bed_half_width, (floor_z + wall_top_z) / 2.0, TAILLIGHT)

    wheel_arch(m, front_axle_x, local_w, wheel_r, 22.0, flare=1.25)
    # rear wheel sits under the welded bed, outside the hull loft's domain -
    # local_w extrapolates by clamping to the cab-tail's width, which is
    # close enough to the bed's own width for this stylized flare patch.
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 22.0, flare=1.25)
    # roof light bar
    m.quad((90.0, -30.0, 133.0), (66.0, -30.0, 133.0), (66.0, 30.0, 133.0),
           (90.0, 30.0, 133.0), CHROME)
    tris = m.save("SM_Car_Pickup")
    length = sections[0][0] - tail_x
    height = cab[1][2] + wheel_r
    print(f"  Pickup: length {length:.0f} cm, height ~{height:.0f} cm, wheelbase "
          f"{front_axle_x - rear_axle_x:.0f} cm, axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, "
          f"half-track {half_track:.0f}, wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Pickup", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=2380.0)


def build_rally():
    """Compact rally hatch: short overhangs both ends, tall greenhouse for
    its length, flared arches, twin round auxiliary lights low on the front
    bumper, steep hatch glass, small roof-trailing-edge spoiler."""
    body, dark = 8, 9
    sections = [
        (198.0, 52.0, 4.0, 28.0),    # nose - short overhang
        (182.0, 62.0, 0.0, 36.0),    # bumper (aux lights live here)
        (150.0, 70.0, -2.0, 44.0),   # front fender - flared
        (86.0, 71.0, -4.0, 48.0),    # cowl (198->86 = 112 cm hood, short)
        (10.0, 71.0, -4.0, 48.0),    # cabin rear base (76 cm cabin)
        (-70.0, 68.0, 0.0, 40.0),    # rear fender - flared
        (-140.0, 58.0, 4.0, 30.0),   # hatch taper
        (-192.0, 48.0, 6.0, 20.0),   # tail - short overhang, steep hatch
    ]
    cab = [(86.0, 71.0, 48.0), (52.0, 56.0, 110.0),
           (-40.0, 56.0, 110.0), (-70.0, 68.0, 44.0)]   # tall-for-its-length greenhouse

    front_axle_x, rear_axle_x, half_track, wheel_r = 112.0, -118.0, 78.0, 32.0
    style = style_for(dict(
        bottom_width_frac=0.92, top_width_frac=0.88, shoulder_frac=0.60,
        nose_extend=12.0, nose_taper_frac=0.86, nose_height_frac=0.78,
        tail_extend=12.0, tail_taper_frac=0.68, tail_height_frac=0.62,
        tail_style="pinch",
        bulge_strength=0.17, bulge_sigma=44.0,   # flared arches both ends
        front_bulge_mult=1.15, rear_bulge_mult=1.15,
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 60.0, local_w, 52.0)
    # twin round rally lights, low on the front bumper, inboard of the main headlight band
    light_patch(m, 190.0, local_w, 12.0, HEADLIGHT, size=(9.0, 9.0))

    wheel_arch(m, front_axle_x, local_w, wheel_r, 8.0, flare=1.2)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 6.0, flare=1.2)
    lip_spoiler(m, -136.0, local_w(-136.0), 32.0, chord=8.0)
    tris = m.save("SM_Car_Rally")
    length = sections[0][0] - sections[-1][0]
    print(f"  Rally: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Rally", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1310.0)


def build_wheel():
    """Shared wheel mesh: 16-gon cylinder, dark tire tread + lighter rim
    face, built once at REF_WHEEL_RADIUS_CM (34 cm). Each car visually
    rescales this per its own WheelRadiusCm - see AODVehiclePawn::
    ApplyVehicleDef's wheel-scale line."""
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
