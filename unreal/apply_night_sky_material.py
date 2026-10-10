"""Night sky (2026-10-09, Taylor: "dusk was not dark enough - should add stars,
moon"): builds M_NightSky, the star field AODDayNightController draws on a
500 km inverted sphere that follows the camera. Unlit, additive, two-sided;
a Custom node hashes the view direction into 3D cells and lights a small dot
in ~1.5% of them, most faint and a few bright, tinted warm to cool.

Fogging stays on (per pixel) with cloud fogging, so height fog, weather fog
and the cloud deck dim or hide the stars instead of them showing through.

Params: "Night" (0 day .. 1 night, set by the controller) and "Brightness"
(peak emissive scale). The moon is not here: it is the sky atmosphere's
second sun disk (AtmosphereSunLightIndex 1 on the moon light).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_NightSky"

STARS_HLSL = """
float3 d = -normalize(CamVec);
float3 p = d * 150.0;
float3 c = floor(p);
float h = frac(sin(dot(c, float3(127.1, 311.7, 74.7))) * 43758.5453);
if (h < 0.985) return float3(0, 0, 0);
float3 o = (frac(h * float3(13.13, 71.71, 37.37)) - 0.5) * 0.5;
float r = length(frac(p) - 0.5 - o);
float core = saturate(1.0 - r / 0.25);
float mag = 0.5 + 4.0 * pow(frac(h * 911.7), 6.0);
float3 tint = lerp(float3(1.0, 0.85, 0.7), float3(0.75, 0.85, 1.0), frac(h * 53.3));
return tint * mag * 8.0 * core;   // 8x, linear core: a diag pass at 20x proved 1x invisible in game
"""


def log(msg):
    unreal.log_warning(f"NIGHTSKYMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"{MAT_NAME}: failed to connect {what}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
        MEL.delete_material_expression(mat, _e)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
mat.set_editor_property("two_sided", True)
mat.set_editor_property("use_translucency_vertex_fog", True)   # "Apply Fogging"
mat.set_editor_property("compute_fog_per_pixel", True)          # 500 km triangles
mat.set_editor_property("apply_cloud_fogging", True)

cam = MEL.create_material_expression(mat, unreal.MaterialExpressionCameraVectorWS, -900, 0)
stars = MEL.create_material_expression(mat, unreal.MaterialExpressionCustom, -650, 0)
stars.set_editor_property("code", STARS_HLSL)
stars.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
inp = unreal.CustomInput()
inp.set_editor_property("input_name", "CamVec")
stars.set_editor_property("inputs", [inp])
req(MEL.connect_material_expressions(cam, "", stars, "CamVec"), "CameraVector -> Custom")

night = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -650, 200)
night.set_editor_property("parameter_name", "Night")
night.set_editor_property("default_value", 0.0)
bright = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -650, 300)
bright.set_editor_property("parameter_name", "Brightness")
bright.set_editor_property("default_value", 3.0)
scale = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -450, 250)
req(MEL.connect_material_expressions(night, "", scale, "A"), "Night x Brightness A")
req(MEL.connect_material_expressions(bright, "", scale, "B"), "Night x Brightness B")
emissive = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -250, 100)
req(MEL.connect_material_expressions(stars, "", emissive, "A"), "stars x scale A")
req(MEL.connect_material_expressions(scale, "", emissive, "B"), "stars x scale B")
req(MEL.connect_material_property(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
if MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_EMISSIVE_COLOR) is None:
    raise RuntimeError(f"{MAT_NAME}: Emissive unconnected after save")
names = [str(p) for p in MEL.get_scalar_parameter_names(reloaded)]
for want in ("Night", "Brightness"):
    if want not in names:
        raise RuntimeError(f"{MAT_NAME}: {want} param missing after save")
log(f"built + saved {MAT_NAME} (additive unlit star field, params {names})")
