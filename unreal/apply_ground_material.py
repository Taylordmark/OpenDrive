"""Phase 3a ground-visual overhaul (2026-09-03): replaces the old placeholder
stylized ground pass (flat GRASS_COLOR/ROCK_COLOR/SAND_COLOR constants +
two procedurally-generated detail textures, see git history on this file for
that version's own long tuning history) with real CC0 PBR texture sets
(content_src/textures/ground/, ambientCG, see manifest.json for per-asset
provenance/license) blended by SLOPE, ALTITUDE, and the existing per-vertex
BIOME tint (AODTerrainManager::ApplyBiomeTint - unchanged, already reaches
the runtime mesh as vertex color, no format/plumbing changes needed for
biome itself). Target: The Crew 2's grounded semi-photoreal look, active
area summit_county (Frisco/Breckenridge/Vail, CO - alpine grass/meadow,
conifer forest, exposed rock, scree/talus above treeline, snow on peaks).

Layer stack (see LAYERS below for the actual texture sets):
  - VEGETATED base: grass/meadow <-> forest-floor (lichen/moss) picked by the
    baked vertex tint's luma (dark biome tint = forest floor; brighter =
    open meadow), tinted by that same vertex color so the existing biome hue
    design (AODTerrainManager::BiomeGroundTintTable) still drives the overall
    color - only the per-pixel DETAIL is new. A dirt-trail texture blends in
    via large-scale position noise for organic patch breakup (not tied to
    any biome/road signal - purely a "don't tile obviously" texture-variety
    knob). Above treeline, the vegetated base ALSO blends toward the scree
    layer even on flat ground (real alpine tundra is patchy rock/gravel, not
    solid grass) - see ALPINE_BARE_MAX_BLEND.
  - STEEP (slope-driven) layer: rock cliff (Rock030, TRIPLANAR so it doesn't
    stretch on near-vertical faces - the only triplanar layer, see
    sample_triplanar) below treeline, blending to scree/talus (Gravel030,
    planar - accepted stretch tradeoff on moderate scree slopes, see module
    notes) above it - both signals keyed off world Z the same way the snow
    line is, so "elevation" reads consistently across every layer.
  - SNOW: altitude-driven (SnowLineLowZCm/SnowLineHighZCm, derived from
    test_areas.ORIGIN_HEIGHT_M - see that constant's own comment for why a
    hardcoded absolute Z would silently be wrong per-area) with a
    position-hash noise breakup so the line isn't a hard band, reduced on
    steep faces (snow doesn't stick to cliffs).
  - SAND/beach blend (2026-07-27, Oahu era) kept verbatim from the old
    version - harmless no-op for summit_county (SeaLevelZCm sits ~34 km
    below any real terrain here) but still correct for a coastal area if
    this script is re-run against one.

Same in-place-rebuild MaterialEditingLibrary approach + re-runnability this
project's other apply_*_material.py scripts already established (2026-08-26
on this file originally) - load-and-clear via delete_all_material_
expressions() rather than delete_asset()+recreate. KNOWN UE 5.8
python-binding pin-name gotchas this file's own G helper class works around
(see its docstring): Clamp/ComponentMask/Abs/Floor/Frac/Sine's primary input
pin is unnamed ("" not "Input"); VertexColor's RGB output is not
addressable as "RGB" (use "" instead); TextureSample's UV input pin is
"UVs" in some contexts, "Coordinates" in others.

Run unreal/import_ground_textures.py FIRST (imports the PBR sets as
Texture2D assets this script loads by path) - this script raises if any
layer's textures are missing.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import sys
from pathlib import Path

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_GroundStylized"
GROUND_TEX_DIR = "/Game/OpenDrive/Textures/Ground"

# (asset_id, world-space tile size cm) - tuned by feel: smaller tiles read
# as fine detail up close, bigger ones for textures with larger natural
# features (rock veining, snow drift patterns) or ones only ever seen from
# farther away (the triplanar rock cliff).
LAYERS = {
    "grass": ("Grass001", 700.0),
    "forest": ("Ground068", 750.0),
    "dirt": ("Ground037", 850.0),
    "rock": ("Rock030", 1400.0),
    "scree": ("Gravel030", 1000.0),
    "snow": ("Snow006", 1800.0),
}

SLOPE_SOFTNESS = 4.0          # higher = sharper flat/steep transition
TRIPLANAR_SHARPNESS = 4.0     # higher = sharper triplanar axis blend

# ---- elevation bands, all derived from ORIGIN_HEIGHT_M (test_areas.py) -
# summit_county's real Rockies treeline sits ~3500 m ASL; UE world Z at
# this area's origin is (absolute_elevation_m - ORIGIN_HEIGHT_M) * 100, so
# a hardcoded Z constant copied from another area (like the old
# ElevLowCm/ElevHighCm in ODTerrainManager.cpp before this task's C++ fix)
# would silently be meaningless here - ORIGIN_HEIGHT_M=3435.3 alone shifts
# every absolute-meter constant by ~343500 cm versus a sea-level-origin area.
TREELINE_LOW_M = 3350.0
TREELINE_HIGH_M = 3650.0
TREELINE_LOW_CM = (TREELINE_LOW_M - ORIGIN_HEIGHT_M) * 100.0
TREELINE_HIGH_CM = (TREELINE_HIGH_M - ORIGIN_HEIGHT_M) * 100.0
ALPINE_BARE_MAX_BLEND = 0.55  # never fully replace flat alpine ground with scree - patchy, not solid gravel

SNOW_START_M = 3500.0    # patchy snow begins here (~treeline)
SNOW_FULL_M = 3950.0     # full coverage by here (Quandary Peak's 4348 m stays snowy)
SNOW_LOW_CM = (SNOW_START_M - ORIGIN_HEIGHT_M) * 100.0
SNOW_HIGH_CM = (SNOW_FULL_M - ORIGIN_HEIGHT_M) * 100.0
SNOW_NOISE_SCALE_CM = 4000.0   # 40 m patchiness at the snow line
# 2026-09-08 polish-pass fix: was 0.5 (bias range +/-0.25 on the 0..1 raw
# altitude-coverage fraction). AODSeasonController's melt-compression fix
# (identity-ramp SnowLineLowZCm) already makes the RAW fraction sit right at
# SnowCover for the vantage's own elevation - but snow_t_raw feeds a plain
# Lerp(base_albedo, snow_albedo, snow_factor), a smooth per-pixel BLEND, not
# a threshold. With only +/-0.25 of noise wiggle around a mid-transition
# SnowCover=0.5, snow_t never left the [0.25, 0.75] range anywhere on
# screen, so instead of some 40 m cells reading fully white and others fully
# green (real patchiness), the whole visible area rendered one uniform pale
# 50%-blended wash - looking like a global dissolve, not melting snow.
# Raising this to 1.3 (bias range +/-0.65) means at SnowCover=0.5 the noise
# does push individual 40 m cells all the way to 0 (bare) and 1 (full snow)
# while others stay in between, producing genuine chunky patches; at the
# S~0.16/0.84 checkpoints it still leaves most of the frame dominated by one
# extreme with real (if partial) breakthrough at the other, matching the
# "mostly snowy with patches opening" / "mostly green with drifts" spec.
SNOW_NOISE_STRENGTH = 1.3
SNOW_STEEP_REDUCTION = 0.65    # snow coverage cut on near-vertical rock

MACRO_NOISE_SCALE_CM = 12000.0  # 120 m - large-scale dirt-patch breakup, not tied to any biome/road signal
DIRT_PATCH_STRENGTH = 0.30      # max blend toward the dirt-trail texture from macro noise alone

# Beach/sand blend (2026-07-27, Oahu era) - kept verbatim, see module docstring.
SAND_COLOR = (0.76, 0.69, 0.52)
SAND_ROUGHNESS = 0.55
BEACH_BAND_CM = 3.0 * 0.3048 * 100.0
SEA_LEVEL_Z_CM = -ORIGIN_HEIGHT_M * 100.0


def log(msg):
    # unreal.log() doesn't reliably surface in this project's headless
    # -stdout captures (apply_water_material.py's 2026-08-27 finding) -
    # log_warning does, so it's used here purely to get real evidence into
    # the captured log, not because these are actual warnings.
    unreal.log_warning(f"GROUNDMAT {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def load_layer_textures():
    out = {}
    for key, (asset_id, tile_cm) in LAYERS.items():
        base = f"{GROUND_TEX_DIR}/{asset_id}"
        albedo = unreal.load_asset(f"{base}/T_{asset_id}_A")
        normal = unreal.load_asset(f"{base}/T_{asset_id}_N")
        rough = unreal.load_asset(f"{base}/T_{asset_id}_R")
        if albedo is None or normal is None or rough is None:
            raise RuntimeError(
                f"ground texture layer '{key}' ({asset_id}) missing under {base} - "
                f"run unreal/import_ground_textures.py first (after "
                f"content_src/textures/ground/download_ground_textures.py)")
        out[key] = dict(albedo=albedo, normal=normal, rough=rough, tile_cm=tile_cm)
    return out


TEXTURES = load_layer_textures()


class G:
    """Node-soup helper around MaterialEditingLibrary, wrapping this file's
    known UE 5.8 python-binding pin-name gotchas (see module docstring) so
    every call site doesn't have to re-derive them. Node positions are
    auto-laid-out (cosmetic only) - same approach build_visuals_headless.py's
    own Graph helper uses.
    """

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 200
        n = MEL.create_material_expression(
            self.mat, cls, -2200 + (self._y % 1600) * 2, self._y // 8)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def const2(self, a, b):
        return self.node(unreal.MaterialExpressionConstant2Vector, r=a, g=b)

    def const3(self, r, gg, b):
        return self.node(unreal.MaterialExpressionConstant3Vector,
                          constant=unreal.LinearColor(r, gg, b, 1.0))

    def _req(self, ok, what):
        if not ok:
            raise RuntimeError(f"could not connect {what} - pin name mismatch?")

    def unary(self, cls, a, a_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), cls.__name__)
        return n

    def absv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionAbs, a, a_out)

    def floorv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionFloor, a, a_out)

    def fracv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionFrac, a, a_out)

    def sinev(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionSine, a, a_out)

    def clamp01(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionClamp, a, a_out)

    def mask(self, a, r=False, gg=False, b=False, alpha=False, a_out=""):
        n = self.node(unreal.MaterialExpressionComponentMask, r=r, g=gg, b=b, a=alpha)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), "ComponentMask")
        return n

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), f"{cls.__name__}.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def div(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDivide, a, b, a_out, b_out)

    def dotp(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDotProduct, a, b, a_out, b_out)

    def power(self, base, exp, base_out="", exp_out=""):
        n = self.node(unreal.MaterialExpressionPower)
        self._req(MEL.connect_material_expressions(base, base_out, n, "Base"), "Power.Base")
        # NB (2026-09-03): MaterialExpressionPower's exponent pin is named
        # "Exp" in this UE 5.8 build's python bindings, not "Exponent" -
        # confirmed via MEL.get_material_expression_input_names() probe.
        # The pre-rewrite script's literal "Exponent" call was actually a
        # silent no-op the whole time (connect_material_expressions()
        # doesn't raise on a bad pin name) - it never wired the constant
        # softness exponent in, it just left Power's default Exp=2.0.
        self._req(MEL.connect_material_expressions(exp, exp_out, n, "Exp"), "Power.Exp")
        return n

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self._req(MEL.connect_material_expressions(alpha, al_out, n, "Alpha"), "Lerp.Alpha")
        return n

    def scalar_param(self, name, default):
        n = self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name)
        n.set_editor_property("default_value", default)
        return n

    def vertex_color(self):
        return self.node(unreal.MaterialExpressionVertexColor)

    def world_pos(self):
        return self.node(unreal.MaterialExpressionWorldPosition)

    def world_normal(self):
        return self.node(unreal.MaterialExpressionVertexNormalWS)

    def sample(self, tex, uv, is_normal=False, uv_out=""):
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex)
        if is_normal:
            n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        ok = False
        for pin in ("UVs", "Coordinates"):
            if MEL.connect_material_expressions(uv, uv_out, n, pin):
                ok = True
                break
        self._req(ok, "TextureSample UV")
        return n


mat_path = f"{MAT_DIR}/{MAT_NAME}"

if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    log("--- pre-edit M_GroundStylized expression graph ---")
    try:
        for expr in MEL.get_material_expressions(mat):
            log(f"  {expr.get_class().get_name()} @ {MEL.get_material_expression_node_position(expr)}")
    except Exception as exc:  # inspection is best-effort/diagnostic only - never block the rebuild
        log(f"  (inspection failed, proceeding with rebuild anyway: {exc})")
    log("--- end pre-edit graph ---")
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

g = G(mat)

# ---------------------------------------------------------------- shared signals
world_pos = g.world_pos()
world_xy = g.mask(world_pos, r=True, gg=True)
world_z = g.mask(world_pos, b=True)
normal = g.world_normal()
up = g.const3(0.0, 0.0, 1.0)
slope_dot = g.dotp(normal, up)
flat_w = g.power(slope_dot, g.const(SLOPE_SOFTNESS))   # ~1 flat, ~0 steep
one_c = g.const(1.0)
steep_w = g.sub(one_c, flat_w)

vtint = g.vertex_color()  # "" is the RGB output on this UE version - see module docstring
luma = g.dotp(vtint, g.const3(0.2126, 0.7152, 0.0722))


def make_uv(tile_cm):
    return g.mul(world_xy, g.const(1.0 / tile_cm))


def wrap_lattice_uv(uv2, period=97.0):
    """Wrap a lattice-space UV (already divided by the noise's world-space
    cell size) into [0, period) before it reaches value_noise's hash.

    2026-09-08 polish-pass fix: the snow-noise hash (see value_noise below)
    is the classic dot/sin/frac trick, which is only well-behaved for small
    hash-input magnitudes - GPU sin() loses precision once its argument gets
    into the thousands, and this area's absolute world coordinates (real
    Colorado UTM-scale positions, hundreds of thousands of cm) divided by
    SNOW_NOISE_SCALE_CM still land the lattice cell index in the ~100-200
    range, which after the hash's own *(12.9898, 78.233) dot product lands
    well into five-figure radians. Empirically (2026-09-08 QA render at the
    Dillon Dam Road vantage) this produced a near-DEGENERATE hash - all 4
    corners of the local cell landing at nearly the same value - so raising
    SNOW_NOISE_STRENGTH from 0.5 all the way to 4.0 in isolated testing
    changed the rendered look by almost nothing (still one uniform tone, no
    patches), which is the fingerprint of a saturated/constant hash input,
    not merely "not enough amplitude." Wrapping the lattice coordinate into a
    small repeating range keeps the hash's own inputs small (and therefore
    numerically well-behaved) while still varying with position at the same
    40 m cell size - it just repeats the pattern every `period` cells
    (97 * 40 m = ~3.9 km here), far beyond anything a single frame can see.
    """
    scaled = g.mul(uv2, g.const(1.0 / period))
    wrapped01 = g.fracv(scaled)
    return g.mul(wrapped01, g.const(period))


def value_noise(uv2, seed_offset=(0.0, 0.0)):
    """Cheap tileable-lattice value noise (floor/frac + 4-corner hash-lerp) -
    a material-graph port of ODTerrainManager.cpp's own HashLattice/
    ValueNoise2D idea (same "deterministic position hash" shape, different
    constants - this is a classic GLSL-style sin/frac hash, not required to
    numerically match the C++ pass, just needs to be smooth and cheap).
    Returns a scalar in ~[0,1]. seed_offset decorrelates two calls sharing
    the same uv2 source."""
    uv2 = g.add(uv2, g.const2(*seed_offset)) if seed_offset != (0.0, 0.0) else uv2
    i = g.floorv(uv2)
    f = g.fracv(uv2)

    def hash2(v):
        d = g.dotp(v, g.const2(12.9898, 78.233))
        s = g.sinev(d)
        m = g.mul(s, g.const(43758.5453))
        return g.fracv(m)

    h00 = hash2(i)
    h10 = hash2(g.add(i, g.const2(1.0, 0.0)))
    h01 = hash2(g.add(i, g.const2(0.0, 1.0)))
    h11 = hash2(g.add(i, g.const2(1.0, 1.0)))
    fx = g.mask(f, r=True)
    fy = g.mask(f, gg=True)
    top = g.lerp(h00, h10, fx)
    bot = g.lerp(h01, h11, fx)
    return g.lerp(top, bot, fy)


def sample_layer(key):
    t = TEXTURES[key]
    uv = make_uv(t["tile_cm"])
    return dict(
        albedo=g.sample(t["albedo"], uv),
        normal=g.sample(t["normal"], uv, is_normal=True),
        rough=g.sample(t["rough"], uv),
        uv=uv,
    )


def rough_scalar(rough_tex_node):
    return g.mask(rough_tex_node, r=True, a_out="RGB")


def sample_triplanar(key):
    """World-space triplanar sample (albedo+normal+roughness) blended by
    abs(vertex normal)^TRIPLANAR_SHARPNESS, normalized - the only triplanar
    layer (rock cliff), so steep/vertical faces don't stretch. Axis-blend
    only, no per-axis normal-map reorientation ("whiteout" blend) - a known
    simplification (the normal detail on a near-vertical face is slightly
    off-axis rather than perfectly reprojected), acceptable at this material's
    scale/viewing distance and much cheaper to build than the full technique."""
    t = TEXTURES[key]
    scale = g.const(1.0 / t["tile_cm"])
    uv_z = g.mul(g.mask(world_pos, r=True, gg=True), scale)  # top: XY
    uv_y = g.mul(g.mask(world_pos, r=True, b=True), scale)   # front: XZ
    uv_x = g.mul(g.mask(world_pos, gg=True, b=True), scale)  # side: YZ

    albedo_x = g.sample(t["albedo"], uv_x)
    albedo_y = g.sample(t["albedo"], uv_y)
    albedo_z = g.sample(t["albedo"], uv_z)
    normal_x = g.sample(t["normal"], uv_x, is_normal=True)
    normal_y = g.sample(t["normal"], uv_y, is_normal=True)
    normal_z = g.sample(t["normal"], uv_z, is_normal=True)
    rough_x = rough_scalar(g.sample(t["rough"], uv_x))
    rough_y = rough_scalar(g.sample(t["rough"], uv_y))
    rough_z = rough_scalar(g.sample(t["rough"], uv_z))

    abs_n = g.absv(normal)
    pow_n = g.power(abs_n, g.const(TRIPLANAR_SHARPNESS))
    sum_n = g.dotp(pow_n, g.const3(1.0, 1.0, 1.0))
    wx = g.div(g.mask(pow_n, r=True), sum_n)
    wy = g.div(g.mask(pow_n, gg=True), sum_n)
    wz = g.div(g.mask(pow_n, b=True), sum_n)

    def blend3(a, b, c, a_out="RGB", b_out="RGB", c_out="RGB"):
        return g.add(g.add(g.mul(a, wx, a_out=a_out), g.mul(b, wy, a_out=b_out)),
                     g.mul(c, wz, a_out=c_out))

    albedo = blend3(albedo_x, albedo_y, albedo_z)
    normal_blend = blend3(normal_x, normal_y, normal_z)
    rough = blend3(rough_x, rough_y, rough_z, a_out="", b_out="", c_out="")
    return dict(albedo=albedo, normal=normal_blend, rough=rough)


def hue_from_tint(albedo_tex_node):
    """Desaturate the detail albedo to pure brightness variation, regain it
    toward ~1.0, and let the baked vertex color BE the hue - same rework
    rationale AODTerrainManager's ApplyBiomeTint / this file's 2026-08-27
    history already established (a multiply can only darken, so the tint
    must drive hue, not be multiplied over an already-colored texture)."""
    desat = g.node(unreal.MaterialExpressionDesaturation)
    g._req(MEL.connect_material_expressions(albedo_tex_node, "RGB", desat, ""), "Desaturation input")
    gain = g.mul(desat, g.const(2.0))
    return g.mul(gain, vtint)


# ---------------------------------------------------------------- vegetated base
grass = sample_layer("grass")
forest = sample_layer("forest")
dirt = sample_layer("dirt")
scree = sample_layer("scree")

# 2026-09-04 (§7.15 pale-background-hills follow-up): Gravel030's raw albedo
# measures mean RGB (140,138,138)/255 ~= (0.55,0.54,0.54) - a near-neutral
# pale grey, confirmed (content_src/textures/ground sampled directly) to be
# the actual color painting the pale background hills the task set out to
# fix, not a slope/tint bug: those hills are real background ridges at/near
# real treeline elevation, so bare_amount/treeline_t are legitimately
# non-zero there and this IS the correct layer choice - it just reads as an
# unrealistically flat, washed-out grey next to The Crew 2's warmer alpine
# talus. A subtle warm/dark multiply (not a texture swap - every other layer
# already uses this same CC0 set, see manifest.json) keeps it visibly paler
# than the vegetated/rock layers (still reads as "bare rock", still lighter
# than Rock030's own mean (79,77,70)/255) while losing the flat neutral-grey
# cast; ~22-35% per-channel darkening, biased warmer (less blue) so it leans
# buff/tan (iron-stained talus) rather than cool concrete-grey.
scree_albedo_tinted = g.mul(scree["albedo"], g.const3(0.78, 0.72, 0.62), a_out="RGB")

grass_tinted = hue_from_tint(grass["albedo"])
forest_tinted = hue_from_tint(forest["albedo"])

# forest floor wins under dark/dense biome tints (Evergreen/Deciduous forest),
# open meadow wins under brighter ones - luma thresholds tuned against
# BiomeGroundTintTable's own entries (forest tints ~0.06-0.16 luma range).
FOREST_LUMA_LOW = 0.075
FOREST_LUMA_HIGH = 0.16
forest_weight = g.clamp01(
    g.div(g.sub(g.const(FOREST_LUMA_HIGH), luma), g.const(FOREST_LUMA_HIGH - FOREST_LUMA_LOW)))

veg_albedo = g.lerp(grass_tinted, forest_tinted, forest_weight)
veg_normal = g.lerp(grass["normal"], forest["normal"], forest_weight, a_out="RGB", b_out="RGB")
veg_rough = g.lerp(rough_scalar(grass["rough"]), rough_scalar(forest["rough"]), forest_weight)

# ---------------------------------------------------------------- seasons: spring fresh-green tint
# (2026-09-07, seasons feature part 1) - AODSeasonController drives this
# scalar param (0 baseline, unconnected to anything else so SnowCover=0/
# SpringGreen=0 is a byte-for-byte no-op back to the pre-seasons look).
# Deliberately capped low (SPRING_GREEN_MAX_BLEND) and only applied to the
# vegetated base, never the steep/rock/scree layers - a subtle, temporary
# "new growth" brightening of the existing biome-tinted grass/forest-floor
# color, not a texture swap or a full-saturation cartoon green.
SPRING_GREEN_COLOR = (0.32, 0.52, 0.16)
SPRING_GREEN_MAX_BLEND = 0.35
spring_green_param = g.scalar_param("SpringGreen", 0.0)
spring_green_color = g.const3(*SPRING_GREEN_COLOR)
spring_green_amount = g.mul(spring_green_param, g.const(SPRING_GREEN_MAX_BLEND))

# large-scale (120 m) position noise picks organic dirt-trail patches -
# texture-variety only, not tied to any biome/road signal (the C++ tint's
# own road-verge blend already handles the "near road" cue as a color shift;
# this is purely "don't tile obviously" breakup).
patch_noise = value_noise(g.mul(world_xy, g.const(1.0 / MACRO_NOISE_SCALE_CM)))
dirt_amount = g.mul(patch_noise, g.const(DIRT_PATCH_STRENGTH))
veg_albedo = g.lerp(veg_albedo, dirt["albedo"], dirt_amount, b_out="RGB")
veg_normal = g.lerp(veg_normal, dirt["normal"], dirt_amount, b_out="RGB")
veg_rough = g.lerp(veg_rough, rough_scalar(dirt["rough"]), dirt_amount)

veg_albedo = g.lerp(veg_albedo, spring_green_color, spring_green_amount, b_out="")

# ---- above treeline, blend the vegetated base toward scree even on FLAT
# ground (real alpine tundra is patchy rock/gravel, not solid grass) - same
# elevation band the steep-layer rock/scree pick below uses, so every
# elevation-driven signal in this material agrees on where "alpine" starts.
treeline_t = g.clamp01(
    g.div(g.sub(world_z, g.const(TREELINE_LOW_CM)), g.const(TREELINE_HIGH_CM - TREELINE_LOW_CM)))
bare_amount = g.mul(treeline_t, g.const(ALPINE_BARE_MAX_BLEND))
veg_albedo = g.lerp(veg_albedo, scree_albedo_tinted, bare_amount, b_out="")
veg_normal = g.lerp(veg_normal, scree["normal"], bare_amount, b_out="RGB")
veg_rough = g.lerp(veg_rough, rough_scalar(scree["rough"]), bare_amount)

# ---------------------------------------------------------------- steep (slope) layer
rock = sample_triplanar("rock")
steep_albedo = g.lerp(rock["albedo"], scree_albedo_tinted, treeline_t, b_out="")
steep_normal = g.lerp(rock["normal"], scree["normal"], treeline_t, b_out="RGB")
steep_rough = g.lerp(rock["rough"], rough_scalar(scree["rough"]), treeline_t)

# ---------------------------------------------------------------- flat/steep composite
base_albedo = g.lerp(steep_albedo, veg_albedo, flat_w)
base_normal = g.lerp(steep_normal, veg_normal, flat_w)
base_rough = g.lerp(steep_rough, veg_rough, flat_w)

# ---------------------------------------------------------------- sand/beach blend (kept verbatim, see module docstring)
sea_level = g.scalar_param("SeaLevelZCm", SEA_LEVEL_Z_CM)
height_above_sea = g.sub(world_z, sea_level)
beachiness = g.clamp01(g.div(g.sub(g.const(BEACH_BAND_CM), height_above_sea), g.const(BEACH_BAND_CM)))
sand_factor = g.mul(beachiness, flat_w)
sand_color = g.const3(*SAND_COLOR)

base_albedo = g.lerp(base_albedo, sand_color, sand_factor)
base_rough = g.lerp(base_rough, g.const(SAND_ROUGHNESS), sand_factor)

# ---------------------------------------------------------------- snow (altitude, noise-broken-up, reduced on cliffs)
snow = sample_layer("snow")
snow_low = g.scalar_param("SnowLineLowZCm", SNOW_LOW_CM)
snow_high = g.scalar_param("SnowLineHighZCm", SNOW_HIGH_CM)
snow_t_raw = g.clamp01(g.div(g.sub(world_z, snow_low), g.sub(snow_high, snow_low)))

snow_noise = value_noise(
    wrap_lattice_uv(g.mul(world_xy, g.const(1.0 / SNOW_NOISE_SCALE_CM))), seed_offset=(91.0, 37.0))
noise_bias = g.mul(g.sub(snow_noise, g.const(0.5)), g.const(SNOW_NOISE_STRENGTH))
snow_t = g.clamp01(g.add(snow_t_raw, noise_bias))

steep_reduction = g.sub(g.const(1.0), g.mul(steep_w, g.const(SNOW_STEEP_REDUCTION)))
snow_factor = g.mul(snow_t, steep_reduction)

final_albedo = g.lerp(base_albedo, snow["albedo"], snow_factor, b_out="RGB")
final_normal = g.lerp(base_normal, snow["normal"], snow_factor, b_out="RGB")
final_rough = g.lerp(base_rough, rough_scalar(snow["rough"]), snow_factor)

MEL.connect_material_property(final_albedo, "", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(final_normal, "", unreal.MaterialProperty.MP_NORMAL)
MEL.connect_material_property(final_rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

MEL.recompile_material(mat)

# 2026-07-27 finding (still true): without an explicit save the material is
# created/compiled/logged successfully but stays transient in-memory only,
# vanishing the instant this commandlet process exits.
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# ---- VERIFY: confirm the saved graph actually wired what this script thinks
# it wired - apply_ground_material.py's own Clamp/VertexColor bugs both
# "succeeded" in headless -nullrhi mode while silently leaving a required pin
# unconnected (see apply_water_material.py's identical VERIFY block, adopted
# here for the same reason). Reload the just-saved asset fresh, not the
# in-memory `mat` reference.
reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Normal", unreal.MaterialProperty.MP_NORMAL),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
):
    node = MEL.get_material_property_input_node(reloaded, prop_enum)
    if node is None:
        all_ok = False
        log(f"  MP_{prop_name}: UNCONNECTED (FAIL)")
        continue
    inputs = MEL.get_inputs_for_material_expression(reloaded, node)
    log(f"  MP_{prop_name}: driven by {node.get_class().get_name()} own_inputs={inputs}")
if not all_ok:
    raise RuntimeError(
        "M_GroundStylized: one or more base material property pins failed to "
        "connect - see the post-save verification log above")

log(f"built + saved {MAT_NAME} for area '{AREA_NAME}' - "
    f"treeline={TREELINE_LOW_M:.0f}-{TREELINE_HIGH_M:.0f}m ({TREELINE_LOW_CM:.0f}-{TREELINE_HIGH_CM:.0f}cm), "
    f"snowline={SNOW_START_M:.0f}-{SNOW_FULL_M:.0f}m ({SNOW_LOW_CM:.0f}-{SNOW_HIGH_CM:.0f}cm), "
    f"layers={list(LAYERS.keys())}, triplanar=rock, "
    f"seasons_params=[SnowLineLowZCm,SnowLineHighZCm,SpringGreen]")
