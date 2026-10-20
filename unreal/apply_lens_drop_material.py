"""On-lens precipitation (2026-10-06): builds M_LensDrop, the material
AODWeatherController::TickLens draws its camera-attached drop/flake quads
(/Engine/BasicShapes/Plane instances, 20 cm in front of the camera) with.

Translucent + MSM_DEFAULT_LIT (NOT Unlit - see apply_snow_particle_material.py
for the solid-black Unlit-translucent gotcha), two-sided, depth test off (the
layer is always on top), responsive AA (small screen-fixed translucency).

Per-instance custom data (set by TickLens):
  [0] Kind  1 = snow flake .. 0 = water drop (a melting flake ramps 1 -> 0)
  [1] Fade  0..1 overall alpha (landing pop-in, end-of-life fade)
  [2] Trail 1 = the faint wet streak a sliding drop leaves (2026-10-20)
  [3] Seed  0..1 per-drop outline wobble, so no two beads are the same
TickLens turns each quad's local X to the drop's slide axis and stretches it,
so the round outline here becomes the sliding bead's ellipse.

Looks (stylized, one Custom HLSL node per property - the bodies were checked
with the Windows SDK dxc, ps_6_0):
  water - a nearly clear bead with an irregular outline, a dark meniscus
          edge and a highlight spot; the dome Normal + Refraction (IOR mode,
          "RefractionIOR" param) bends the scene through it like a real drop;
  trail - a faint, softly refracting streak;
  snow  - an opaque soft six-lobed white blob, no refraction.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_LensDrop"
REFRACTION_IOR = 1.33

# Custom node bodies: inputs UV (TexCoord 0), Kind, Fade, Trail, Seed
# (custom data 0..3). RW = the water bead's radius with a seeded two-lobe
# wobble, so beads read as irregular pools, not stamped circles.
WOBBLE = ("float2 d = UV - 0.5;\n"
          "float r = length(d) * 2.0;\n"
          "float th = atan2(d.y, d.x);\n"
          "float rw = r * (1.0 + 0.07 * sin(3.0 * th + Seed * 6.283) + 0.04 * sin(5.0 * th + Seed * 17.0));\n"
          "float disc = 1.0 - smoothstep(0.85, 1.0, rw);\n"
          "float rim = smoothstep(0.78, 0.97, rw) * disc;\n")
OPACITY_HLSL = WOBBLE + """
float rs = r * (1.0 + 0.22 * cos(6.0 * th));
float flake = 1.0 - smoothstep(0.5, 0.8, rs);
float hl = 1.0 - smoothstep(0.0, 0.22, length(d + float2(0.14, 0.16)) * 2.0);
float water = disc * 0.04 + rim * 0.5 + hl * 0.55;   // clear body, dark meniscus
float streak = (1.0 - smoothstep(0.3, 1.0, r)) * 0.06;
return saturate(lerp(lerp(water, streak, Trail), flake * 0.9, Kind) * Fade);
"""
EMISSIVE_HLSL = """
float2 d = UV - 0.5;
float hl = 1.0 - smoothstep(0.0, 0.22, length(d + float2(0.14, 0.16)) * 2.0);
return lerp(hl * (1.0 - Trail) * float3(0.6, 0.65, 0.7), float3(0.33, 0.34, 0.35), Kind);
"""
COLOR_HLSL = WOBBLE + """
float3 water = lerp(float3(0.35, 0.38, 0.42), float3(0.03, 0.035, 0.04), rim * (1.0 - Trail));
return lerp(water, float3(0.95, 0.96, 0.98), Kind);
"""
NORMAL_HLSL = WOBBLE + """
float2 n = (d * 2.0) * (disc * (1.0 - Kind) * Fade * lerp(1.0, 0.3, Trail));
return normalize(float3(n, 1.0));
"""


def log(msg):
    unreal.log_warning(f"LENSDROPMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"M_LensDrop: failed to connect {what}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
        MEL.delete_material_expression(mat, _e)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
mat.set_editor_property("two_sided", True)
mat.set_editor_property("used_with_instanced_static_meshes", True)
mat.set_editor_property("disable_depth_test", True)
mat.set_editor_property("enable_responsive_aa", True)
mat.set_editor_property("refraction_method", unreal.RefractionMode.RM_INDEX_OF_REFRACTION)

uv = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -900, 0)
kind = MEL.create_material_expression(mat, unreal.MaterialExpressionPerInstanceCustomData, -900, 150)
kind.set_editor_property("data_index", 0)
kind.set_editor_property("const_default_value", 0.0)
fade = MEL.create_material_expression(mat, unreal.MaterialExpressionPerInstanceCustomData, -900, 300)
fade.set_editor_property("data_index", 1)
fade.set_editor_property("const_default_value", 1.0)
trail = MEL.create_material_expression(mat, unreal.MaterialExpressionPerInstanceCustomData, -900, 450)
trail.set_editor_property("data_index", 2)
trail.set_editor_property("const_default_value", 0.0)
seed = MEL.create_material_expression(mat, unreal.MaterialExpressionPerInstanceCustomData, -900, 600)
seed.set_editor_property("data_index", 3)
seed.set_editor_property("const_default_value", 0.0)
SOURCES = {"UV": uv, "Kind": kind, "Fade": fade, "Trail": trail, "Seed": seed}

customs = []


def custom(name, code, out_type, inputs, prop, y):
    n = MEL.create_material_expression(mat, unreal.MaterialExpressionCustom, -400, y)
    n.set_editor_property("description", name)
    n.set_editor_property("code", code)
    n.set_editor_property("output_type", out_type)
    pins = []
    for i in inputs:
        ci = unreal.CustomInput()
        ci.set_editor_property("input_name", i)
        pins.append(ci)
    n.set_editor_property("inputs", pins)
    for i in inputs:
        req(MEL.connect_material_expressions(SOURCES[i], "", n, i), f"{name}.{i}")
    req(MEL.connect_material_property(n, "", prop), f"{name} -> {prop}")
    customs.append((name, prop, len(inputs)))


F1 = unreal.CustomMaterialOutputType.CMOT_FLOAT1
F3 = unreal.CustomMaterialOutputType.CMOT_FLOAT3
custom("LensOpacity", OPACITY_HLSL, F1, ["UV", "Kind", "Fade", "Trail", "Seed"], unreal.MaterialProperty.MP_OPACITY, 0)
custom("LensEmissive", EMISSIVE_HLSL, F3, ["UV", "Kind", "Trail"], unreal.MaterialProperty.MP_EMISSIVE_COLOR, 200)
custom("LensColor", COLOR_HLSL, F3, ["UV", "Kind", "Trail", "Seed"], unreal.MaterialProperty.MP_BASE_COLOR, 400)
custom("LensNormal", NORMAL_HLSL, F3, ["UV", "Kind", "Fade", "Trail", "Seed"], unreal.MaterialProperty.MP_NORMAL, 600)

ior = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -400, 800)
ior.set_editor_property("parameter_name", "RefractionIOR")
ior.set_editor_property("default_value", REFRACTION_IOR)
req(MEL.connect_material_property(ior, "", unreal.MaterialProperty.MP_REFRACTION), "RefractionIOR -> Refraction")

errors = MEL.recompile_material(mat)
if errors:
    raise RuntimeError(f"M_LensDrop: compile errors {list(errors)}")
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
for name, prop, n_in in customs:
    n = MEL.get_material_property_input_node(reloaded, prop)
    ins = MEL.get_inputs_for_material_expression(reloaded, n) if n else []
    if n is None or len([i for i in ins if i is not None]) != n_in:
        raise RuntimeError(f"M_LensDrop: {name} -> {prop} not fully wired ({ins})")
    log(f"  {prop}: {name} inputs={[i.get_class().get_name() for i in ins]}")
if MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_REFRACTION) is None:
    raise RuntimeError("M_LensDrop: MP_REFRACTION unconnected after save")
log(f"built + saved {MAT_NAME} (translucent default-lit, depth test off, IOR refraction {REFRACTION_IOR})")
