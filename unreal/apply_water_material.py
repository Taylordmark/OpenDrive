"""Builds M_Water, the reservoir-water material for baked lake surfaces
(WATER visual-overhaul pass, 2026-09-03 - supersedes the 2026-08-27
"glass-flat stylized" version, see git history on this file for that one's
own long-since-superseded reasoning). Target: summit_county's Dillon
Reservoir reading like real mountain-reservoir water at The Crew 2's bar -
deep blue-green up close, sky-tinted at grazing angles (fresnel), animated
ripple-normal detail (not "glass flat"), low roughness for a sharp sun
glint. See unreal/import_water_textures.py (run FIRST - imports the
procedural T_WaterRipple_N normal map this script loads by path) and
content_src/textures/water/generate_water_normal.py for that texture's own
seamless-tiling construction.

Two-layer panning normal: ONE texture (T_WaterRipple_N), sampled TWICE at
different UV tiling + pan direction/speed (WATER_TARGET's design ask) -
cheaper than two textures and this project has no second water-normal asset
anyway. Blended via a straight Lerp, same simplification apply_ground_
material.py's own veg_normal blend already uses for combining two normal
samples in this codebase (a proper per-normal reoriented blend would need
BlendAngleCorrectedNormals; the flat lerp reads fine at this material's
scale/distance and keeps this script's node graph consistent with the
established local convention).

OPAQUE, not translucent (decided after in-game testing, 2026-09-03): lake
meshes are a single flat cap baked directly at the DEM-percentile surface
elevation with NO lake-bed geometry underneath (see build_water_tiles.py) -
translucency would let the bare terrain mesh show through at whatever
(unknown, un-baked) clearance sits below the water plane, which is a
correctness risk (visible z-fighting/terrain-poking-through) this pass has
no data to guard against. Fresnel-driven color/specular already delivers
the "grazing angle reads like sky, steep angle reads like water" look
without needing real transparency - same "opaque is fine and cheaper" call
apply_ground_material.py/the original apply_water_material.py both made,
just with a real fresnel+ripple graph behind it now instead of a flat color.

Same in-place-rebuild MaterialEditingLibrary approach + re-runnability this
project's other apply_*_material.py scripts already established. KNOWN UE
5.8 python-binding pin-name gotchas this file works around (see the G
helper below, same shape as apply_ground_material.py's own G class):
MaterialExpressionPower's exponent pin is "Exp" not "Exponent";
MaterialExpressionClamp/ComponentMask's primary input pin is unnamed ("");
MaterialExpressionVertexColor's RGB output is not addressable as "RGB" (use
"" instead - unused by this material, restated for the next editor of this
file); MaterialExpressionPanner's UV input pin is "Coordinate". Every
connection is verified post-save via get_inputs_for_material_expression()
(see the VERIFY block at the bottom) - a silent pin no-op is a real,
recurring failure in this codebase's material scripts, not a hypothetical.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_Water"
TEX_PATH = "/Game/OpenDrive/Textures/Water/T_WaterRipple_N"

# Deep blue-green reservoir water, close-up/steep-angle color - a real
# mountain reservoir (glacial-fed, some algae/sediment) reads closer to
# blue-green than open-ocean blue; stylized enough to read as "obviously
# water" at a glance but no longer the flat placeholder desaturated blue.
# 2026-09-03 tuning round 3 (reviewer defect: "flat, oversaturated
# swimming-pool cyan... reads as painted plastic, not open water" - target
# The Crew 2's deep, slightly desaturated blue-green): darkened and pulled
# the green channel down relative to blue so the hue reads as a genuine deep
# reservoir rather than a bright pool tile.
WATER_COLOR = (0.010, 0.042, 0.055)

# Grazing-angle color stand-in for a sky reflection - this project has no
# reflection captures/SSR guarantee wired up for this pass, so a fixed
# sky-ish blue-grey tint, faded in via Fresnel, is the cheap stylized
# substitute for a real reflection (same "fake it with a color, not a real
# reflection" tradeoff the rest of this material's fresnel work makes).
# 2026-09-03 tuning round 2 (dillon_dam.png / dillon_shore3.png QA findings,
# confirmed by an in-game A/B: neither the roughness/specular knobs below NOR
# a much darker SKY_TINT_COLOR alone changed anything - every shore/overlook
# vantage on this reservoir views the water at a near-grazing angle almost by
# construction (camera close to the water's own elevation, looking OUT across
# a flat plane), and Fresnel genuinely approaches 1.0 across nearly the ENTIRE
# visible surface at that geometry, not just near true grazing incidence -
# that's correct physics (this is why a real lake often looks pale/sky-toned
# from standing height), but it meant base_color was blending almost fully to
# SKY_TINT_COLOR everywhere in frame, and a merely "darker blue" sky tint
# still read as washed-out pale blue-white once lit by a bright midday sun.
# Two changes instead of endlessly darkening the tint further: (1) SKY_TINT_
# COLOR pushed to a properly saturated sky blue (not just "less white"), and
# (2) the fresnel driving the BASE COLOR blend (not the specular term) is
# clamped below 1.0, so even at the shallowest visible angle the deep-water
# color always keeps a real minority share of the blend instead of the sky
# tint ever going 100%.
# 2026-09-03 tuning round 3: round 2's sky tint (0.05, 0.22, 0.42) was picked
# to fix a washed-out-white/no-water look, but overshot the other direction -
# combined with a 0.72 fresnel cap that lets it cover most of a grazing-angle
# shore view, it read as a bright, fully-saturated cyan/blue "pool" rather
# than a believable sky reflection. A real sky reflection off water is
# desaturated and dimmer than the sky itself (it's reflectance, not the sky
# color directly) - darkened and desaturated toward a cool grey-blue, and the
# cap pulled down so the deep water color keeps a much larger share of the
# blend even at the shallowest visible angles.
SKY_TINT_COLOR = (0.09, 0.15, 0.21)
BASE_COLOR_FRESNEL_CAP = 0.55   # clamp ceiling for the base-color fresnel blend (specular still uses raw fresnel)
                                 # bumped from round 3's 0.50 - see the shore-cove gray-slab CORRECTION note below.

# Roughness lowered from 0.35 (round 2) - at 0.35 the ripple normal's
# perturbation barely dents specular response, so the surface reads as a
# uniform flat fill with no sparkle/glint variation regardless of how much
# ripple-normal detail is baked in. 0.12 (round 3) kept a believable (not
# mirror-flat) open-water response while letting individual highlights
# actually pick out the ripple pattern - see NORMAL_STRENGTH below for the
# matching normal-map amplification this needed to be visible at gameplay
# (driving-speed, mid camera distance) scale.
#
# CORRECTION (round 4, same day): 0.12 was too low for the Swan Mountain Rd
# shoreline cove - at the near-grazing, tree-canopy-shadowed viewing angle
# used there, a roughness this low turns the surface into a near-mirror.
# Diagnosed by temporarily swapping WATER_COLOR to pure red and
# re-rendering: the "gray slab" DID tint red in the portion of the surface
# viewed at a shallower angle, but stayed near-white right at the
# near-grazing foreground - proving this is UE's own built-in grazing-angle
# specular Fresnel (Schlick's F0 + (1-F0)(1-cosTheta)^5 - goes to ~1.0
# reflectance at any grazing angle regardless of Specular/F0) reflecting the
# bright gap-of-sky-through-canopy above it, layered ON TOP of - and
# independent from - the manual BaseColor fresnel blend/cap graph below.
# That built-in term only responds to Roughness (spreads/dims the mirror
# highlight) and Specular (only moves the near-normal F0, barely touches the
# grazing asymptote), so BASE_COLOR_FRESNEL_CAP alone can't fix this - it
# was never the thing producing the wash. 0.22 (the first correction attempt)
# wasn't enough; 0.4 - close to round 2's original flat value - actually
# knocks the mirror-hotspot down to where the cove reads as shaded water
# again, and NORMAL_STRENGTH=1.7 (added this same round) keeps the ripple
# sparkle visible on top of it that round 2 lacked, so this isn't a return
# to the original flatness complaint. SPECULAR_GRAZING pulled back with it
# to further tame the same near-mirror response.
ROUGHNESS = 0.4
SPECULAR_BASE = 0.35
SPECULAR_GRAZING = 0.4

FRESNEL_EXPONENT = 4.6    # steeper still than round 2's 4.2 - together with the lower BASE_COLOR_FRESNEL_CAP,
                           # keeps more of a normally-viewed lake surface reading as the deep water color
FRESNEL_BASE_REFLECT = 0.04   # ~real water's normal-incidence reflectance (Schlick F0 for water is ~0.02-0.04)

# Visual amplification of the sampled ripple normal's X/Y (tangent-plane)
# components before lighting - added 2026-09-03 (reviewer defect: "ensure
# the panned dual normals actually contribute visible surface variation").
# T_WaterRipple_N's baked perturbation is subtle by design (a seamless-tiling
# procedural normal, see content_src/textures/water/generate_water_normal.py)
# and at ROUGHNESS 0.35 it was invisible; even at the lowered 0.12 roughness
# above, scaling the tangent XY channels before the lighting pass (same
# unnormalized-amplify trick apply_ground_material.py's normal blend already
# uses in this codebase - visually fine at this material's scale even though
# it isn't a true renormalized strength blend) makes the pan/tile pattern
# genuinely visible instead of a barely-perceptible shimmer.
NORMAL_STRENGTH = 1.7

# Ripple normal: two samples of the SAME texture (see module docstring),
# different world-space tile size + pan direction/speed so they don't read
# as one repeating pattern. Values tuned by eye against a moving-water look,
# not derived analytically.
RIPPLE_A_TILE_CM = 900.0
RIPPLE_A_SPEED = (0.015, 0.010)
RIPPLE_B_TILE_CM = 260.0
RIPPLE_B_SPEED = (-0.028, 0.019)
NORMAL_BLEND_ALPHA = 0.5   # even mix - both layers contribute (large rolling shape + fine chop)

# ---- seasons: lake freeze (2026-09-07, seasons feature part 1) ----------
# AODSeasonController drives FreezeBlend (0 baseline - byte-for-byte the
# pre-seasons look, same "unconnected param defaults to the old behavior"
# convention as M_GroundStylized's SnowLineLowZCm/HighZCm). Pale blue-white
# ice color, rougher (matte ice, not glossy open water), ripple normal
# strength faded toward flat (a frozen surface doesn't show wind chop).
ICE_COLOR = (0.62, 0.68, 0.72)
ICE_ROUGHNESS = 0.75
# Frozen-lake look (2026-10-16): a real frozen lake is not one pale sheet -
# wind-scoured patches of dark clear ("black") ice show the water under
# them and stay glossy, and snow ("SnowCover", AODSeasonController's cover,
# 0 = none) lies in drifts on the rest. Both are 12 m / 5 m world-space
# value noise in one Custom node, only where FreezeBlend is past ~0.7.
CLEAR_ICE_COLOR = (0.06, 0.09, 0.11)
CLEAR_ICE_ROUGHNESS = 0.12
LAKE_SNOW_COLOR = (0.86, 0.89, 0.93)
LAKE_SNOW_ROUGHNESS = 0.85
LAKE_ICE_HLSL = """
float2 m = 0;
for (int k = 0; k < 2; k++) {
  float2 p = W.xy / (k == 0 ? 1200.0 : 500.0) + k * 17.3;
  float2 i = floor(p), f = frac(p);
  f = f * f * (3 - 2 * f);
  float a = frac(sin(dot(i, float2(127.1, 311.7))) * 43758.5453);
  float b = frac(sin(dot(i + float2(1, 0), float2(127.1, 311.7))) * 43758.5453);
  float c = frac(sin(dot(i + float2(0, 1), float2(127.1, 311.7))) * 43758.5453);
  float d = frac(sin(dot(i + float2(1, 1), float2(127.1, 311.7))) * 43758.5453);
  m[k] = lerp(lerp(a, b, f.x), lerp(c, d, f.x), f.y);
}
float frozen = saturate((Freeze - 0.7) * 3.33);
float n = lerp(m.x, m.y, 0.35);
float snow = saturate((Cover * 1.4 - 0.4 + (n - 0.5) * 0.9) * 4) * frozen;
float clear = saturate((0.32 - n) * 6) * frozen * (1 - snow);
return float2(snow, clear);
"""

# ---- Weather W5: storm chop (2026-09-25, next steps.md sec 7.42) ---------
# AODSeasonController pushes WaveRoughness 0..1 from AODWeatherController's
# wind speed (hurricane = 1). 0 = byte-for-byte the calm graph above. Ripple
# normal pushed much harder and the deep color greyed toward churned water.
STORM_NORMAL_STRENGTH = 4.5
STORM_COLOR = (0.035, 0.050, 0.050)


def log(msg):
    # unreal.log() doesn't reliably surface in this project's headless
    # -stdout captures (apply_water_material.py's original 2026-08-27
    # finding, still true) - log_warning does, used here purely to get real
    # evidence into the captured log, not because these are actual warnings.
    unreal.log_warning(f"WATERMAT {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


class G:
    """Trimmed node-soup helper, same shape as apply_ground_material.py's own
    G class (see its docstring for the pin-name gotchas this works around) -
    duplicated rather than imported since these are standalone
    -run=pythonscript commandlets, not a shared package."""

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

    def _connect_any(self, src, dst, dst_pins, src_out=""):
        for pin in dst_pins:
            if MEL.connect_material_expressions(src, src_out, dst, pin):
                return True
        return False

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), f"{cls.__name__}.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def scalar_param(self, name, default):
        n = self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name)
        n.set_editor_property("default_value", default)
        return n

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self._req(MEL.connect_material_expressions(alpha, al_out, n, "Alpha"), "Lerp.Alpha")
        return n

    def tex_coord(self, index=0):
        return self.node(unreal.MaterialExpressionTextureCoordinate, coordinate_index=index)

    def panner(self, uv, speed_x, speed_y, uv_out=""):
        n = self.node(unreal.MaterialExpressionPanner, speed_x=speed_x, speed_y=speed_y)
        ok = self._connect_any(uv, n, ("Coordinate", ""), uv_out)
        self._req(ok, "Panner.Coordinate")
        return n

    def sample(self, tex, uv, is_normal=False, uv_out=""):
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex)
        if is_normal:
            n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        ok = self._connect_any(uv, n, ("UVs", "Coordinates"), uv_out)
        self._req(ok, "TextureSample UV")
        return n

    def fresnel(self, exponent, base_reflect_fraction):
        # NB (2026-09-03): the exponent property is named "exponent" in this
        # UE 5.8 build's python bindings, not "exponent_in" (the C++ pin
        # label) - confirmed via a get_editor_property probe; "exponent_in"
        # raises "Failed to find property" immediately (loud, not silent).
        return self.node(unreal.MaterialExpressionFresnel,
                          exponent=exponent, base_reflect_fraction=base_reflect_fraction)

    def append(self, a, b, a_out="", b_out=""):
        n = self.node(unreal.MaterialExpressionAppendVector)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "AppendVector.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "AppendVector.B")
        return n

    def clamp(self, a, lo, hi, a_out=""):
        # NB: MaterialExpressionClamp's primary input pin is unnamed ("") in
        # this UE 5.8 build's python bindings, not "Input" (module docstring).
        n = self.node(unreal.MaterialExpressionClamp, min_default=lo, max_default=hi)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), "Clamp.Input")
        return n

    def two_sided_sign_vec(self):
        # 2026-09-03 fix (dillon_shore3.png QA finding: a dark cliff-like
        # wedge under the water's leading edge from some shore angles): this
        # material sets two_sided=True (untrusted OSM-derived triangle
        # winding, see module docstring) so backfaces render, but UE's
        # MSM_DEFAULT_LIT shading model does NOT auto-flip the tangent-space
        # normal for a backface the way MSM_TWO_SIDED_FOLIAGE does (same root
        # cause next_steps.md 7.15 documents for M_Foliage's near-black
        # crowns, fixed there by switching shading model - not an option
        # here, water wants real specular/fresnel, not foliage subsurface).
        # The standard DefaultLit fix instead: multiply the sampled tangent
        # normal by (1, 1, TwoSidedSign) so only Z flips on a backface - for
        # a near-flat water plane the tangent Z channel IS effectively "away
        # from the visible face", so this alone corrects N.L from negative
        # (unlit black) to positive on the mesh's underside without
        # perturbing the X/Y ripple detail.
        sign = self.node(unreal.MaterialExpressionTwoSidedSign)
        ones = self.const2(1.0, 1.0)
        return self.append(ones, sign)


mat_path = f"{MAT_DIR}/{MAT_NAME}"

water_normal_tex = unreal.load_asset(TEX_PATH)
if water_normal_tex is None:
    raise RuntimeError(
        f"{TEX_PATH} not found - run unreal/import_water_textures.py first "
        f"(after content_src/textures/water/generate_water_normal.py)")

if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    log("--- pre-edit M_Water expression graph ---")
    try:
        for expr in MEL.get_material_expressions(mat):
            log(f"  {expr.get_class().get_name()} @ {MEL.get_material_expression_node_position(expr)}")
    except Exception as exc:   # inspection only, never blocks the rebuild
        log(f"  (inspection failed, proceeding with rebuild anyway: {exc})")
    log("--- end pre-edit graph ---")
    for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
        MEL.delete_material_expression(mat, _e)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

# Water is a thin flat cap baked from a possibly-untrustworthy ear-clip/fan
# triangulation (pipeline/build_water_tiles.py runtime-fixes winding to face
# up under UE's own normal convention, but two-sided is a cheap, harmless
# safety net for a handful of triangles per lake either way - same reasoning
# apply_building_material.py already documents for its own untrusted-OSM-
# winding two-sided flag).
mat.set_editor_property("two_sided", True)
# OPAQUE (see module docstring for why translucent was tried and rejected
# for this pass) - explicit even though Opaque is the engine default, so
# this reads as a deliberate choice if a later pass revisits it.
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)

g = G(mat)

# ---------------------------------------------------------------- ripple normal (two panned samples, one texture)
uv0 = g.tex_coord(0)

uv_a = g.mul(uv0, g.const(2000.0 / RIPPLE_A_TILE_CM))   # baked UVs are world-meters/UV_TILE_M(=20m) per
                                                          # build_water_tiles.py -> *100 to get back to cm, /tile_cm
uv_a_panned = g.panner(uv_a, RIPPLE_A_SPEED[0], RIPPLE_A_SPEED[1])
ripple_a = g.sample(water_normal_tex, uv_a_panned, is_normal=True)

uv_b = g.mul(uv0, g.const(2000.0 / RIPPLE_B_TILE_CM))
uv_b_panned = g.panner(uv_b, RIPPLE_B_SPEED[0], RIPPLE_B_SPEED[1])
ripple_b = g.sample(water_normal_tex, uv_b_panned, is_normal=True)

ripple_normal_raw = g.lerp(ripple_a, ripple_b, g.const(NORMAL_BLEND_ALPHA), a_out="RGB", b_out="RGB")
# Amplify the tangent-plane (X/Y) perturbation before anything else touches
# it - see NORMAL_STRENGTH's own comment for why (ROUGHNESS alone wasn't
# enough to make the ripple pattern read at gameplay scale/distance). Z
# deliberately left at 1x - only X/Y encode the actual ripple direction/
# steepness, scaling Z would just dim the overall normal length.
ripple_normal_amplified = g.mul(ripple_normal_raw, g.const3(NORMAL_STRENGTH, NORMAL_STRENGTH, 1.0))

# seasons: fade the ripple perturbation toward a flat normal (0,0,1) as the
# lake freezes over - ice doesn't show wind-driven surface chop.
freeze_blend = g.scalar_param("FreezeBlend", 0.0)
flat_normal = g.const3(0.0, 0.0, 1.0)
wave_rough = g.scalar_param("WaveRoughness", 0.0)
ripple_normal_storm = g.mul(ripple_normal_raw, g.const3(STORM_NORMAL_STRENGTH, STORM_NORMAL_STRENGTH, 1.0))
ripple_normal_chop = g.lerp(ripple_normal_amplified, ripple_normal_storm, wave_rough)
ripple_normal_frozen = g.lerp(ripple_normal_chop, flat_normal, freeze_blend, a_out="", b_out="")

# flip Z on backfaces (see two_sided_sign_vec() docstring) - fixes the
# unlit-black backface wedge a two-sided flat water plane otherwise shows
# from below/behind its shoreline edge.
ripple_normal = g.mul(ripple_normal_frozen, g.two_sided_sign_vec())

# ---------------------------------------------------------------- fresnel-driven color/specular
fresnel = g.fresnel(FRESNEL_EXPONENT, FRESNEL_BASE_REFLECT)

deep_color_liquid = g.lerp(g.const3(*WATER_COLOR), g.const3(*STORM_COLOR), wave_rough)
ice_color = g.const3(*ICE_COLOR)
deep_color = g.lerp(deep_color_liquid, ice_color, freeze_blend, b_out="")
sky_color = g.const3(*SKY_TINT_COLOR)
base_fresnel = g.clamp(fresnel, 0.0, BASE_COLOR_FRESNEL_CAP)
base_color = g.lerp(deep_color, sky_color, base_fresnel)

specular = g.lerp(g.const(SPECULAR_BASE), g.const(SPECULAR_GRAZING), fresnel)
roughness = g.lerp(g.const(ROUGHNESS), g.const(ICE_ROUGHNESS), freeze_blend)

lake_ice = g.node(unreal.MaterialExpressionCustom, code=LAKE_ICE_HLSL,
                  output_type=unreal.CustomMaterialOutputType.CMOT_FLOAT2)
_pins = []
for _name in ("W", "Freeze", "Cover"):
    _i = unreal.CustomInput()
    _i.set_editor_property("input_name", _name)
    _pins.append(_i)
lake_ice.set_editor_property("inputs", _pins)
for _name, _src in (("W", g.node(unreal.MaterialExpressionWorldPosition)), ("Freeze", freeze_blend),
                    ("Cover", g.scalar_param("SnowCover", 0.0))):
    g._req(MEL.connect_material_expressions(_src, "", lake_ice, _name), f"Custom.{_name}")
lake_snow = g.node(unreal.MaterialExpressionComponentMask, r=True, g=False, b=False, a=False)
lake_clear = g.node(unreal.MaterialExpressionComponentMask, r=False, g=True, b=False, a=False)
for _m in (lake_snow, lake_clear):
    g._req(MEL.connect_material_expressions(lake_ice, "", _m, ""), "ComponentMask(lake_ice)")
base_color = g.lerp(g.lerp(base_color, g.const3(*CLEAR_ICE_COLOR), lake_clear), g.const3(*LAKE_SNOW_COLOR), lake_snow)
roughness = g.lerp(g.lerp(roughness, g.const(CLEAR_ICE_ROUGHNESS), lake_clear), g.const(LAKE_SNOW_ROUGHNESS), lake_snow)

MEL.connect_material_property(base_color, "", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.connect_material_property(specular, "", unreal.MaterialProperty.MP_SPECULAR)
MEL.connect_material_property(ripple_normal, "", unreal.MaterialProperty.MP_NORMAL)

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# ---- VERIFY: confirm the saved graph actually wired what the script above
# thinks it wired - apply_ground_material.py's Clamp/VertexColor bugs both
# "succeeded" in headless -nullrhi mode (material created/compiled/saved,
# every log line printed clean) while silently leaving a required pin
# unconnected; the failure only ever surfaced at real (non -nullrhi) compile
# time or live in-game. Reload the just-saved asset fresh (not the in-memory
# `mat` reference) and probe each property input node's own inputs so this
# script can't fool itself the same way.
reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
    ("Specular", unreal.MaterialProperty.MP_SPECULAR),
    ("Normal", unreal.MaterialProperty.MP_NORMAL),
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
        "M_Water: one or more base material property pins failed to connect - "
        "see the post-save verification log above (same silent-pin-no-op class "
        "of bug apply_ground_material.py's Clamp/VertexColor notes document)")

log(f"built + saved {MAT_NAME} (color={WATER_COLOR}, sky_tint={SKY_TINT_COLOR}, "
    f"fresnel_cap={BASE_COLOR_FRESNEL_CAP}, roughness={ROUGHNESS}, "
    f"specular={SPECULAR_BASE}-{SPECULAR_GRAZING}, fresnel_exp={FRESNEL_EXPONENT}, "
    f"normal_strength={NORMAL_STRENGTH}, two_sided=True+TwoSidedSign normal fix, "
    f"blend=Opaque, ripple tiles={RIPPLE_A_TILE_CM}/{RIPPLE_B_TILE_CM}cm, "
    f"seasons_param=FreezeBlend, storm_param=WaveRoughness)")
