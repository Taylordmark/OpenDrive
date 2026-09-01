"""Procedural low-poly car body generator (2026-08-27, hull-builder rework
2026-08-28): four selectable car types built from ONE parametric body builder
(loft profile stations + a beveled, shouldered cross-section hull + fender
bulges + tapered/rounded nose-tail closure) with four distinct presets, plus
ONE shared wheel mesh reused (visually rescaled per class) by all four. Same
faceted-solid + palette-strip conventions as pipeline/make_car_mesh.py /
pipeline/make_car_gt.py (which this does not touch - those two placeholder
cars stay exactly as they are; this is a fully additive third generation,
wired in as extra selectable cars, not a replacement).

    SEDAN  - 4 doors, ~4.76 m, mild teardrop taper, long flat cabin roof.
    COUPE S- 2 doors, ~4.24 m, aggressive teardrop: roof peak forward of
             center, long smooth fastback taper to a low, narrow, PINCHED
             tail point.
    SPORT  - R34-style: boxy near-constant-width greenhouse, long hood,
             the most muscular fender bulges (esp. rear haunches), tail
             SQUARED-BUT-BEVELED (barely tapers, stays wide), raised rear
             wing on stanchions.
    PROTO  - Furai-style: ~1.10 m tall cab-forward wedge, pointed low nose,
             short hood, long rear deck, WIDE FLAT full-width tail with fins
             + a dark diffuser stripe.

2026-08-28 hull-builder rework (Taylor: "the cars look really really bad" -
every car read as an extruded soap bar). The PRESET STATION INTERFACE is
UNCHANGED - each preset is still a hand-tunable 7-row list of
(x, half_width, z_bottom, z_top) stations, so Taylor can keep tuning them in
the live previewer another agent is building. All new shaping comes from the
BUILDER, driven by named per-preset STYLE dicts (see STYLE_DEFAULTS below),
not from extra per-station data:
  1. Beveled cross-section: each station's flat 2-point vertical wall is
     replaced by a 3-point (bottom / shoulder / top) profile with tumblehome
     - bottom and top both tuck in relative to a wider shoulder/beltline
     partway up (cross_profile()).
  2. Fender bulges: the station list is resampled to ~20 cm spacing along x
     (resample_stations()) and a smooth per-station width multiplier
     (gaussian, centered on each axle x, stronger at/below the shoulder,
     attenuated at the roofline) is applied (station_bulge_mult() / the mult
     handling inside build_hull()). Sport's bulge is the strongest
     (especially at the rear axle - "muscular haunches"); Sedan's is the
     mildest.
  3. Nose/tail closure: the loft is extended past the preset's own first/last
     station with two synthetic tapering stations per end (extend_ends()) -
     width AND height both close down over these, so there is no more flat
     full-face end cap. Per-style tail shape ('pinch' / 'bevel' / 'flat')
     controls how much the tail tapers. Headlight/taillight material now
     lives on small proud flank patches (light_patch(), same "decal proud of
     the hull" trick as the wheel arches) placed near the original nose/tail
     control stations, not as a full flat wall - except Proto's tail_style
     'flat', where a real (now much smaller, still tapered) end cap IS the
     intended wide, flat, full-width tail.
  4. Wheel arches, mirrors, and light patches all read their half-width from
     build_hull()'s returned local_width() closure, which reports the
     ACTUAL bulged/tapered shoulder width at any x - so they stay flush
     against the real surface instead of a hand-guessed constant.

Geometry contract with AODVehiclePawn / UODVehicleMovement (same convention
as make_car_mesh.py / make_car_gt.py, UNCHANGED by this rework): OBJ pivot
sits at wheelbase center at WHEEL AXLE height z=0. Per-car axle geometry
(front/rear axle x, half-track y, wheel radius) is printed at the end of each
preset's build and also written to obj/cars_manifest.json (see bottom of
file) - transfer/read these into that car's UODVehicleDef fields so the
visual wheel positions match the physics wheel table
UODVehicleMovement::SetupWheels builds from the same def. Wheel ARCHES here
are cosmetic dark inset patches on the (now-bulged) hull surface at the axle
positions - a real boolean cutout is not attempted, keeping the mesh a
single clean loft with no holes.

The wheel mesh (SM_CarWheel) is built ONCE at REF_WHEEL_RADIUS_CM; each car's
own WheelRadiusCm may differ (Sport rides on bigger wheels, Proto on a lower-
profile smaller one) - AODVehiclePawn::ApplyVehicleDef visually rescales the
shared mesh per car (WheelRadiusCm / REF_WHEEL_RADIUS_CM) rather than baking
four near-identical wheel meshes.

Outputs (data/cars/procedural/, single shared palette - same "many meshes,
one palette texture" pattern as pipeline/make_tree_meshes.py):
    obj/SM_Car_Sedan.obj, SM_Car_Coupe.obj, SM_Car_Sport.obj, SM_Car_Proto.obj
    obj/SM_CarWheel.obj
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
# shared across all four cars + the wheel: each car's BODY (and DARK lower-
# panel accent) gets its own swatch pair so a future recolor is a one-pixel
# edit; everything else (glass/trim/tire/hub/lights/wing-fin-accent) is
# shared cell-for-cell the way tree species share trunk/leaf cells in
# make_tree_meshes.py.
PALETTE = [
    (0.60, 0.63, 0.68),   # 0  sedan body - silvery blue-grey
    (0.29, 0.30, 0.33),   # 1  sedan dark (lower panel)
    (0.55, 0.05, 0.04),   # 2  coupe body - RED (continuity: today's placeholder car is red)
    (0.27, 0.03, 0.02),   # 3  coupe dark
    (0.09, 0.20, 0.40),   # 4  sport body - steel blue (R34-flavored)
    (0.045, 0.10, 0.20),  # 5  sport dark
    (0.21, 0.06, 0.27),   # 6  proto body - deep purple-black (Furai-flavored)
    (0.085, 0.03, 0.11),  # 7  proto dark
    (0.17, 0.25, 0.34),   # 8  glass - cool blue-grey; near-black (0.05,0.08,0.11)
                          #    made every greenhouse read as a black void in-game
    (0.04, 0.04, 0.05),   # 9  trim / bumpers / rockers / wheel-arch inset
    (0.03, 0.03, 0.035),  # 10 tire
    (0.50, 0.50, 0.53),   # 11 hub
    (0.93, 0.85, 0.60),   # 12 headlight
    (0.62, 0.05, 0.03),   # 13 taillight
    (0.16, 0.16, 0.18),   # 14 wing/fin/diffuser accent
]
N_CELL = len(PALETTE)
GLASS, TRIM, TIRE, HUB, HEADLIGHT, TAILLIGHT, ACCENT = 8, 9, 10, 11, 12, 13, 14


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
    flat per-face normals, one UV per face into the shared palette strip."""

    def __init__(self):
        self.tris = []   # (a, b, c, cell)

    def tri(self, a, b, c, cell):
        self.tris.append((np.array(a, float), np.array(b, float),
                          np.array(c, float), cell))

    def quad(self, a, b, c, d, cell):
        self.tri(a, b, c, cell)
        self.tri(a, c, d, cell)

    def save(self, name):
        lines, f_lines, vi = [], [], 1
        for (a, b, c, cell) in self.tris:
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
        nv = len(self.tris) * 3
        print(f"wrote {name}.obj ({len(self.tris)} tris, {nv} verts)")
        return len(self.tris)


# ============================================================== body builder
# Named style parameters - defaults here, per-preset overrides in each
# build_*() below via style_for(). Nothing here is baked into station data;
# the (x, half_width, z_bottom, z_top) station lists in each preset stay
# exactly the hand-tunable interface they were.
STYLE_DEFAULTS = dict(
    # cross-section (item 1): a 4-point profile - bottom, sill top, shoulder
    # (beltline, full width), top deck edge. 2026-09-01 rework (Taylor:
    # "cars still look like garbo"): the previous 0.48-0.74 bottom/top width
    # fractions gave every car a boat-hull almond cross-section. Real
    # low-poly cars have NEAR-VERTICAL sides: the fractions below stay
    # within ~10% of full width, the crease at the shoulder does the work.
    shoulder_frac=0.62,
    sill_frac=0.20,          # dark rocker band tops out here (fraction of band height)
    bottom_width_frac=0.90,
    top_width_frac=0.90,
    # fender bulges (item 2): gaussian width multiplier centered on each
    # axle x; resample_spacing controls how finely the loft is resampled
    # along x before the bulge is applied.
    resample_spacing=20.0,
    bulge_strength=0.14,
    bulge_sigma=54.0,
    front_bulge_mult=1.0,   # per-axle relative strength multipliers
    rear_bulge_mult=1.0,
    # nose/tail closure (item 3): extend the loft this far past the
    # preset's own first/last station, tapering width down to
    # w * taper_frac and the (z_top - z_bottom) band down to
    # band * height_frac at the very tip.
    # 2026-09-01: taper fracs raised from 0.20-0.30 - closing the loft to a
    # fifth of its width made canoe prows, not car noses. Ends now stay
    # chamfered-box: the tip keeps most of its width/height and the small
    # remaining flat cap carries the headlight/taillight bands (cap_banded).
    nose_extend=24.0,
    nose_taper_frac=0.62,
    nose_height_frac=0.55,
    tail_extend=20.0,
    tail_taper_frac=0.68,
    tail_height_frac=0.62,
    tail_style="bevel",   # "pinch" (Coupe S) | "bevel" (Sport) | "flat" (Proto)
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
    """Two synthetic tapering stations per end (item 3), extending the loft
    past the preset's own first ("nose") and last ("tail") station so width
    AND height both close down smoothly instead of a flat end wall. Returns
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


def build_hull(m, sections, cab, body_cell, dark_cell, dark_thresh, style,
                front_bulge_xy=None, rear_bulge_xy=None):
    """Loft SECTIONS (front->back stations of (x, half_width, z_bottom,
    z_top)) into a beveled, fender-bulged, taper-closed hull, then plug the
    CAB quad (glass + roof) across the two stations that bound the cabin.
    Generalized over any station list + style dict so one function serves
    all four presets. Returns local_width(x): the ACTUAL bulged shoulder
    half-width at any x, for wheel arches / mirrors / light patches to key
    off instead of a hand-guessed constant."""
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

    # (z_bottom, z_top) of the hull band at any x - lets wheel arches and
    # other decals clamp themselves INSIDE the body side instead of hanging
    # below the floor pan (the "black mudflap" bug, 2026-09-01)
    def local_band(x):
        return (float(np.interp(x, xs_sorted, bs_sorted)),
                float(np.interp(x, xs_sorted, ts_sorted)))
    local_width.band = local_band

    profiles = [cross_profile(w, b, t, mult_at(x), style) for (x, w, b, t) in stations]

    # 2026-09-01: side bands are BODY color except the thin sill band -
    # painting everything below the shoulder dark (old dark_thresh logic)
    # made whole cars read as black blobs in-game.
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

    def cap_banded(st, prof, light_cell, flip):
        """Small flat end cap split into light / trim / light vertical bands
        (like a bumper face with lights at the corners). `flip` fixes the
        winding so the face points outward at each end."""
        x, w, b, t = st
        w = max(pt[1] for pt in prof)
        lw = w * 0.42   # trim band half-width (lights outboard of this)
        bands = [(-w, -lw, light_cell), (-lw, lw, TRIM), (lw, w, light_cell)]
        for y0, y1, cell in bands:
            a, bq, c, d = (x, y0, b), (x, y0, t), (x, y1, t), (x, y1, b)
            if flip:
                m.quad(d, c, bq, a, cell)
            else:
                m.quad(a, bq, c, d, cell)

    cap_banded(stations[0], profiles[0], HEADLIGHT, flip=False)
    cap_banded(stations[-1], profiles[-1], TAILLIGHT, flip=True)

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


def wheel_arch(m, axle_x, local_width_fn, wheel_r, z_center):
    """Dark inset patch flush on each side of the hull at one axle position,
    y computed from the LOCAL bulged surface width so it still sits just
    proud of the (now curved/bulged) hull - the "fender-bulge box with dark
    inset" fallback for a wheel-well cutout (see module docstring)."""
    arch_w = wheel_r * 1.5
    x0, x1 = axle_x - arch_w / 2, axle_x + arch_w / 2
    # clamp the patch INSIDE the hull side band - an unclamped patch hangs
    # below the floor pan and reads as a floating black mudflap (2026-09-01)
    hb, ht = local_width_fn.band(axle_x)
    z0 = hb + 1.5
    z1 = min(ht - 3.0, z_center + wheel_r * 0.50)
    if z1 <= z0:
        return
    y = local_width_fn(axle_x) + 0.4   # sit just proud of the hull to avoid z-fight
    for side in (1, -1):
        a = (x0, side * y, z0)
        b = (x1, side * y, z0)
        c = (x1, side * y, z1)
        d = (x0, side * y, z1)
        if side > 0:
            m.quad(a, b, c, d, TRIM)
        else:
            m.quad(d, c, b, a, TRIM)


def light_patch(m, x, local_width_fn, z_center, cell, size=(16.0, 9.0)):
    """Small proud rectangular patch on the flank near x, used for headlight
    / taillight bands on the raked nose/tail surfaces - same "decal proud of
    the hull" construction as wheel_arch, just sized/colored differently."""
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
            m.quad(*top, TRIM)
            m.quad(bot[3], bot[2], bot[1], bot[0], TRIM)
            m.quad(*front, TRIM)
        else:
            m.quad(top[3], top[2], top[1], top[0], TRIM)
            m.quad(*bot, TRIM)
            m.quad(front[3], front[2], front[1], front[0], TRIM)


def rear_wing(m, deck_x, deck_z, half_span, stanchion_h):
    """R34-style raised wing on two stanchions above the decklid."""
    wing_z = deck_z + stanchion_h
    chord = 20.0
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


def tail_fins(m, tail_x, half_width, z_base, fin_h=16.0):
    """Furai-style small vertical fins at the tail's outer top edge."""
    for side in (1, -1):
        y = side * half_width * 0.72
        a = (tail_x + 22, y, z_base)
        b = (tail_x - 14, y, z_base)
        c = (tail_x + 4, y, z_base + fin_h)
        m.tri(a, b, c, ACCENT)
        m.tri(c, b, a, ACCENT)


def diffuser_stripe(m, tail_x, half_width, z_bottom):
    """Furai-style dark full-width diffuser suggestion under the tail."""
    x0, x1 = tail_x + 16, tail_x - 16
    m.quad((x0, -half_width, z_bottom), (x1, -half_width, z_bottom),
           (x1, half_width, z_bottom), (x0, half_width, z_bottom), ACCENT)


# ================================================================== presets
def build_sedan():
    body, dark = 0, 1
    sections = [
        (238.0, 62.0, 2.0, 34.0),    # nose tip - stays wide: bumper corners, not a prow
        (220.0, 70.0, -2.0, 40.0),   # bumper
        (155.0, 77.0, -6.0, 48.0),   # front fender
        (84.0, 79.0, -10.0, 54.0),   # cowl (windshield base)
        (-116.0, 79.0, -10.0, 54.0), # cabin rear base
        (-176.0, 76.0, -6.0, 50.0),  # rear fender - teardrop: mild taper
        (-238.0, 66.0, 0.0, 38.0),   # tail
    ]
    cab = [(84.0, 79.0, 54.0), (46.0, 62.0, 130.0),
           (-78.0, 62.0, 130.0), (-116.0, 79.0, 54.0)]   # long flat 4-door roof

    front_axle_x, rear_axle_x, half_track, wheel_r = 150.0, -142.0, 86.0, 34.0
    style = style_for(dict(
        bottom_width_frac=0.92, top_width_frac=0.90, shoulder_frac=0.60,
        nose_extend=18.0, nose_taper_frac=0.68, nose_height_frac=0.60,
        tail_extend=16.0, tail_taper_frac=0.74, tail_height_frac=0.66,
        tail_style="bevel",
        bulge_strength=0.05, bulge_sigma=50.0,   # least muscular of the four
        front_bulge_mult=1.0, rear_bulge_mult=0.85,
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, dark_thresh=44, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 78.0, local_w, 58.0)

    wheel_arch(m, front_axle_x, local_w, wheel_r, 6.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 4.0)
    tris = m.save("SM_Car_Sedan")
    length = sections[0][0] - sections[-1][0]
    print(f"  Sedan: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Sedan", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1550.0)


def build_coupe():
    body, dark = 2, 3
    sections = [
        (212.0, 56.0, 0.0, 28.0),    # nose - low but full-width
        (196.0, 66.0, -4.0, 36.0),   # bumper
        (150.0, 73.0, -6.0, 44.0),   # front fender
        (72.0, 74.0, -8.0, 48.0),    # cowl
        (10.0, 74.0, -8.0, 48.0),    # cabin rear base - short 2-door cabin
        (-70.0, 70.0, -6.0, 40.0),   # fastback taper 1
        (-150.0, 62.0, -2.0, 30.0),  # fastback taper 2
        (-212.0, 50.0, 2.0, 22.0),   # tail - narrower + lower, but still a car, not a canoe
    ]
    # rear base must sit BEHIND the roof rear edge (x more negative) - the
    # old value (10.0) leaned the rear glass forward over the cabin (2026-09-01)
    cab = [(72.0, 74.0, 48.0), (30.0, 58.0, 118.0),
           (-56.0, 58.0, 118.0), (-118.0, 68.0, 42.0)]  # fastback: long roof, long sloped rear glass

    front_axle_x, rear_axle_x, half_track, wheel_r = 128.0, -118.0, 82.0, 33.0
    style = style_for(dict(
        bottom_width_frac=0.90, top_width_frac=0.86, shoulder_frac=0.62,
        nose_extend=20.0, nose_taper_frac=0.58, nose_height_frac=0.50,
        tail_extend=22.0, tail_taper_frac=0.52, tail_height_frac=0.48,
        tail_style="pinch",   # teardrop lean, expressed as a harder (but bounded) taper
        bulge_strength=0.09, bulge_sigma=46.0,
        front_bulge_mult=1.0, rear_bulge_mult=1.05,
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, dark_thresh=40, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 66.0, local_w, 52.0)

    wheel_arch(m, front_axle_x, local_w, wheel_r, 4.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 0.0)
    tris = m.save("SM_Car_Coupe")
    length = sections[0][0] - sections[-1][0]
    print(f"  Coupe S: length {length:.0f} cm, wheelbase {front_axle_x - rear_axle_x:.0f} cm, "
          f"axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    # DisplayName "Coupe S" - deliberately NOT "Coupe": the placeholder
    # coupe (DA_Vehicle_Coupe / make_car_mesh.py) still exists and still
    # sorts first alphabetically as today's spawn car (additive pass, see
    # unreal/import_car_meshes.py header comment) - "Coupe S" reads cleanly
    # in the menu, doesn't collide, and sorts after "Coupe".
    return dict(DisplayName="Coupe S", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1420.0)


def build_sport():
    body, dark = 4, 5
    sections = [
        (230.0, 62.0, 0.0, 32.0),    # nose - wide flat R34 face
        (214.0, 72.0, -4.0, 40.0),   # bumper
        (170.0, 82.0, -8.0, 50.0),   # front fender - wide, muscular
        (92.0, 82.0, -10.0, 54.0),   # cowl (long hood: 230->92 = 138 cm)
        (-98.0, 82.0, -10.0, 54.0),  # cabin rear base - boxy constant width
        (-168.0, 84.0, -8.0, 52.0),  # rear fender - WIDENS (muscular haunches)
        (-230.0, 80.0, 0.0, 40.0),   # squared-off tail - stays wide, brick-not-teardrop
    ]
    cab = [(92.0, 82.0, 54.0), (54.0, 66.0, 128.0),
           (-64.0, 66.0, 128.0), (-98.0, 82.0, 54.0)]   # boxy greenhouse, minimal taper

    front_axle_x, rear_axle_x, half_track, wheel_r = 145.0, -140.0, 92.0, 35.0
    style = style_for(dict(
        bottom_width_frac=0.93, top_width_frac=0.90, shoulder_frac=0.58,
        nose_extend=20.0, nose_taper_frac=0.66, nose_height_frac=0.58,
        tail_extend=14.0, tail_taper_frac=0.88, tail_height_frac=0.80,
        tail_style="bevel",   # squared-but-beveled: barely tapers, stays wide
        bulge_strength=0.13, bulge_sigma=60.0,   # the most muscular fenders of the four
        front_bulge_mult=0.80, rear_bulge_mult=1.20,   # emphasis on the rear haunches
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, dark_thresh=42, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 86.0, local_w, 58.0)

    wheel_arch(m, front_axle_x, local_w, wheel_r, 6.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, 4.0)
    rear_wing(m, deck_x=-190.0, deck_z=40.0, half_span=70.0, stanchion_h=22.0)
    tris = m.save("SM_Car_Sport")
    length = sections[0][0] - sections[-1][0]
    print(f"  Sport (R34-style): length {length:.0f} cm, wheelbase "
          f"{front_axle_x - rear_axle_x:.0f} cm, axle X +{front_axle_x:.0f}/{rear_axle_x:.0f}, "
          f"half-track {half_track:.0f}, wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Sport", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1500.0)


def build_proto():
    # 2026-09-01 LMP rework (Taylor: "more like an lmp car with the fenders
    # flaring over the wheels and very thin chassis through the middle,
    # except for the cabin"): the waist shape now lives directly in the
    # station list - explicit wide fender-crest stations AT each axle x,
    # pinched narrow stations between them - so the pontoon/waist contrast is
    # hand-tunable per station. The gaussian bulge is kept small and tight
    # (sigma 40) purely to round the crests, not to carry the flare.
    body, dark = 6, 7
    sections = [
        (220.0, 42.0, 0.0, 16.0),    # low nose tip
        (198.0, 60.0, -4.0, 24.0),   # splitter/bumper
        (150.0, 78.0, -6.0, 34.0),   # front fender leading slope
        (115.0, 86.0, -8.0, 40.0),   # FRONT FENDER CREST (at front axle, arches over the wheel)
        (78.0, 62.0, -8.0, 34.0),    # behind front wheel - tucking in hard
        (30.0, 50.0, -8.0, 32.0),    # WAIST - thin chassis through the middle
        (-70.0, 50.0, -8.0, 30.0),   # waist rear (cabin sits on top of this span)
        (-115.0, 68.0, -8.0, 34.0),  # rear fender rising
        (-155.0, 86.0, -6.0, 38.0),  # REAR FENDER CREST (at rear axle)
        (-200.0, 80.0, 0.0, 26.0),   # deck settling toward the tail
        (-225.0, 76.0, 2.0, 18.0),   # full-width flat tail (fins + diffuser)
    ]
    # narrow canopy bubble - the one thing that stays proud of the waist;
    # base half-widths match the hull's local TOP width so it sits flush
    cab = [(70.0, 48.0, 32.0), (38.0, 34.0, 78.0),
           (-32.0, 34.0, 78.0), (-72.0, 46.0, 30.0)]

    front_axle_x, rear_axle_x, half_track, wheel_r = 115.0, -155.0, 90.0, 30.0
    style = style_for(dict(
        bottom_width_frac=0.92, top_width_frac=0.82, shoulder_frac=0.45,   # low wedge: shoulder sits low
        nose_extend=24.0, nose_taper_frac=0.55, nose_height_frac=0.42,     # low nose, wider than before (LMP snout)
        tail_extend=10.0, tail_taper_frac=0.96, tail_height_frac=0.92,
        tail_style="flat",   # wide, flat, full-width tail (intentional - not a soap-bar flaw)
        bulge_strength=0.08, bulge_sigma=40.0,   # tight: rounds the fender crests only
        front_bulge_mult=1.0, rear_bulge_mult=1.0,
    ))

    m = Mesh()
    local_w = build_hull(m, sections, cab, body, dark, dark_thresh=34, style=style,
                          front_bulge_xy=front_axle_x, rear_bulge_xy=rear_axle_x)
    mirrors(m, 64.0, local_w, 40.0)

    wheel_arch(m, front_axle_x, local_w, wheel_r, -2.0)
    wheel_arch(m, rear_axle_x, local_w, wheel_r, -2.0)
    tail_fins(m, tail_x=-220.0, half_width=78.0, z_base=18.0)
    diffuser_stripe(m, tail_x=-220.0, half_width=78.0, z_bottom=2.0)
    tris = m.save("SM_Car_Proto")
    length = sections[0][0] - sections[-1][0]
    height = cab[1][2] + wheel_r   # roof top z + wheel radius = ground-to-roof
    print(f"  Proto (Furai-style): length {length:.0f} cm, height ~{height:.0f} cm, "
          f"wheelbase {front_axle_x - rear_axle_x:.0f} cm, axle X "
          f"+{front_axle_x:.0f}/{rear_axle_x:.0f}, half-track {half_track:.0f}, "
          f"wheel radius {wheel_r:.0f}, {tris} tris")
    return dict(DisplayName="Proto", FrontAxleXCm=front_axle_x, RearAxleXCm=rear_axle_x,
                HalfTrackYCm=half_track, WheelRadiusCm=wheel_r, MassKg=1280.0)


def build_wheel():
    """Shared wheel mesh: 16-gon cylinder, dark tire tread + lighter hub
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
                w.tri(q0, q1, hub0, HUB)
            else:
                w.quad(q0, q1, f1, f0, TIRE)
                w.tri(hub0, q1, q0, HUB)
    w.save("SM_CarWheel")


if __name__ == "__main__":
    print("Building procedural car set (4 presets, 1 shared wheel)...")
    sedan = build_sedan()
    coupe = build_coupe()
    sport = build_sport()
    proto = build_proto()
    build_wheel()
    write_palette()
    (OUT / "LICENSE.txt").write_text(
        "Procedural car meshes generated by pipeline/make_car_meshes.py.\n"
        "Original procedural content, no third-party assets.\n")
    print("wrote LICENSE.txt")

    mesh_names = {
        "SM_Car_Sedan": sedan, "SM_Car_Coupe": coupe,
        "SM_Car_Sport": sport, "SM_Car_Proto": proto,
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
    for d in (sedan, coupe, sport, proto):
        print(f"  {d}")
