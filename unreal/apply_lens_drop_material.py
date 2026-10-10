"""On-lens precipitation (2026-10-06): builds M_LensDrop, the material
AODWeatherController::TickLens draws its camera-attached drop/flake quads
(/Engine/BasicShapes/Plane instances, 20 cm in front of the camera) with.

Translucent + MSM_DEFAULT_LIT (NOT Unlit - see apply_snow_particle_material.py
for the solid-black Unlit-translucent gotcha), two-sided, depth test off (the
layer is always on top), responsive AA (small screen-fixed translucency).

Per-instance custom data (set by TickLens):
  [0] Kind  1 = snow flake .. 0 = water drop (a melting flake ramps 1 -> 0)
  [1] Fade  0..1 overall alpha (landing pop-in, end-of-life fade)

Looks (stylized, one Custom HLSL node per property - the bodies were checked
with the Windows SDK dxc, ps_6_0):
  water - a nearly clear disc with a faint rim and a highlight spot; the
          dome Normal + Refraction (IOR mode, "RefractionIOR" param) bends
          the scene through it like a real drop;
  snow  - an opaque soft six-lobed white blob, no refraction.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_LensDrop"
REFRACTION_IOR = 1.33

# Custom node bodies: inputs UV (TexCoord 0), Kind, Fade (custom data 0/1).
OPACITY_HLSL = """
float2 d = UV - 0.5;
float r = length(d) * 2.0;
float rs = r * (1.0 + 0.22 * cos(6.0 * atan2(d.y, d.x)));
float flake = 1.0 - smoothstep(0.5, 0.8, rs);
float disc = 1.0 - smoothstep(0.8, 1.0, r);
float rim = smoothstep(0.55, 0.95, r) * disc;
float hl = 1.0 - smoothstep(0.0, 0.3, length(d + 0.15) * 2.0);
float water = disc * 0.12 + rim * 0.3 + hl * 0.5;
return saturate(lerp(water, flake * 0.9, Kind) * Fade);
"""
EMISSIVE_HLSL = """
float2 d = UV - 0.5;
float hl = 1.0 - smoothstep(0.0, 0.3, length(d + 0.15) * 2.0);
return lerp(hl * float3(0.6, 0.65, 0.7), float3(0.33, 0.34, 0.35), Kind);
"""
COLOR_HLSL = """
return lerp(float3(0.75, 0.8, 0.88), float3(0.95, 0.96, 0.98), Kind);
"""
NORMAL_HLSL = """
float2 d = (UV - 0.5) * 2.0;
float disc = 1.0 - smoothstep(0.64, 1.0, dot(d, d));
float2 n = d * (disc * (1.0 - Kind) * Fade);
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
SOURCES = {"UV": uv, "Kind": kind, "Fade": fade}

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
custom("LensOpacity", OPACITY_HLSL, F1, ["UV", "Kind", "Fade"], unreal.MaterialProperty.MP_OPACITY, 0)
custom("LensEmissive", EMISSIVE_HLSL, F3, ["UV", "Kind"], unreal.MaterialProperty.MP_EMISSIVE_COLOR, 200)
custom("LensColor", COLOR_HLSL, F3, ["Kind"], unreal.MaterialProperty.MP_BASE_COLOR, 400)
custom("LensNormal", NORMAL_HLSL, F3, ["UV", "Kind", "Fade"], unreal.MaterialProperty.MP_NORMAL, 600)

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
