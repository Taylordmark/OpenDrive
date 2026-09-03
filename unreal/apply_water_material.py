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
WATER_COLOR = (0.015, 0.07, 0.075)

# Grazing-angle color stand-in for a sky reflection - this project has no
# reflection captures/SSR guarantee wired up for this pass, so a fixed
# sky-ish blue-grey tint, faded in via Fresnel, is the cheap stylized
# substitute for a real reflection (same "fake it with a color, not a real
# reflection" tradeoff the rest of this material's fresnel work makes).
SKY_TINT_COLOR = (0.58, 0.66, 0.70)

ROUGHNESS = 0.06          # near-mirror - sharp sun glint, not a soft sheen
SPECULAR_BASE = 0.5
SPECULAR_GRAZING = 1.0    # boosted further at grazing angles, on top of Fresnel's own base_reflect_fraction

FRESNEL_EXPONENT = 3.2
FRESNEL_BASE_REFLECT = 0.04   # ~real water's normal-incidence reflectance (Schlick F0 for water is ~0.02-0.04)

# Ripple normal: two samples of the SAME texture (see module docstring),
# different world-space tile size + pan direction/speed so they don't read
# as one repeating pattern. Values tuned by eye against a moving-water look,
# not derived analytically.
RIPPLE_A_TILE_CM = 900.0
RIPPLE_A_SPEED = (0.015, 0.010)
RIPPLE_B_TILE_CM = 260.0
RIPPLE_B_SPEED = (-0.028, 0.019)
NORMAL_BLEND_ALPHA = 0.5   # even mix - both layers contribute (large rolling shape + fine chop)


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
    MEL.delete_all_material_expressions(mat)
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

ripple_normal = g.lerp(ripple_a, ripple_b, g.const(NORMAL_BLEND_ALPHA), a_out="RGB", b_out="RGB")

# ---------------------------------------------------------------- fresnel-driven color/specular
fresnel = g.fresnel(FRESNEL_EXPONENT, FRESNEL_BASE_REFLECT)

deep_color = g.const3(*WATER_COLOR)
sky_color = g.const3(*SKY_TINT_COLOR)
base_color = g.lerp(deep_color, sky_color, fresnel)

specular = g.lerp(g.const(SPECULAR_BASE), g.const(SPECULAR_GRAZING), fresnel)
roughness = g.const(ROUGHNESS)

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
    f"roughness={ROUGHNESS}, specular={SPECULAR_BASE}-{SPECULAR_GRAZING}, "
    f"fresnel_exp={FRESNEL_EXPONENT}, two_sided=True, blend=Opaque, "
    f"ripple tiles={RIPPLE_A_TILE_CM}/{RIPPLE_B_TILE_CM}cm)")
