"""Headlight beam (2026-10-09, Taylor: "make a transparent beam of light
(that is dimmer than where the lights actually land) to match how light
reflects off of particles in the air ... it should work in all scenarios"):
builds M_HeadlightBeam, which AODDayNightController puts on a sphere off
each car's nose at night. Additive, unlit, two-sided with no depth test;
only back faces draw (TwoSidedSign), so it works with the camera inside or
outside the sphere. Per pixel a Custom node marches the view ray through
the sphere, stopped at the scene depth, summing haze x phase x spot cone /
(d^2 + (15 m)^2) for both lamps (the soft core keeps a chase camera
looking down the beam able to see through it) - the beam fades with distance and off-axis, and ends
where it hits the road, a tree or the car.

All positions are relative to the sphere centre (object position), so large
world coordinates stay precise. Params (set per car by the controller):
"Lamp0", "Lamp1" (cm, from the sphere centre), "BeamDir" (unit), "Beam"
(rgb = colour x candela x haze/m x 100), "Radius" (cm), "CosIn", "CosOut".
pipeline/check_headlight_beam.py mirrors MARCH_HLSL and tunes the haze.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_HeadlightBeam"

MARCH_HLSL = """
if (Side > 0) return float3(0, 0, 0);
float3 rd = P - C;
float tFar = length(rd);
rd /= tFar;
float tEnd = min(tFar, tFar * SceneZ / max(PixelZ, 1.0));
float b = dot(C, rd);
float h = b * b - (dot(C, C) - Radius * Radius);
float tNear = max(0.0, -b - sqrt(max(h, 0.0)));
if (tEnd <= tNear) return float3(0, 0, 0);
float dt = (tEnd - tNear) / 48.0;
float jit = frac(52.9829189 * frac(dot(Px, float2(0.06711056, 0.00583715))));
float s = 0.0;
for (int i = 0; i < 48; i++)
{
    float3 x = C + rd * (tNear + (i + jit) * dt);
    for (int k = 0; k < 2; k++)
    {
        float3 v = x - (k == 0 ? L0 : L1);
        float d2 = dot(v, v);
        float t = saturate((dot(v, Dir) * rsqrt(d2) - CosOut) / (CosIn - CosOut));
        s += t * t * (3.0 - 2.0 * t) / (d2 + 2.25e6);   // + (15 m)^2: soft core, see-through from behind
    }
}
return Beam * s * dt / 12.566;   // isotropic phase
"""


def log(msg):
    unreal.log_warning(f"BEAMMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"{MAT_NAME}: failed to connect {what}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
mat.set_editor_property("two_sided", True)
mat.set_editor_property("disable_depth_test", True)   # the march clips to scene depth itself
mat.set_editor_property("use_translucency_vertex_fog", False)


def node(cls, x, y):
    return MEL.create_material_expression(mat, cls, x, y)


def sub(a, b, y):
    n = node(unreal.MaterialExpressionSubtract, -1000, y)
    req(MEL.connect_material_expressions(a, "", n, "A"), "sub A")
    req(MEL.connect_material_expressions(b, "", n, "B"), "sub B")
    return n


obj = node(unreal.MaterialExpressionObjectPositionWS, -1300, 100)
wp = node(unreal.MaterialExpressionWorldPosition, -1300, 0)
cam = node(unreal.MaterialExpressionCameraPositionWS, -1300, 200)
inputs = {
    "P": (sub(wp, obj, 0), ""),
    "C": (sub(cam, obj, 200), ""),
    "SceneZ": (node(unreal.MaterialExpressionSceneDepth, -1000, 400), ""),
    "PixelZ": (node(unreal.MaterialExpressionPixelDepth, -1000, 500), ""),
    "Px": (node(unreal.MaterialExpressionScreenPosition, -1000, 600), "PixelPosition"),
    "Side": (node(unreal.MaterialExpressionTwoSidedSign, -1000, 700), ""),
}
y = 800
for name, pin in (("Lamp0", "L0"), ("Lamp1", "L1"), ("BeamDir", "Dir"), ("Beam", "Beam")):
    p = node(unreal.MaterialExpressionVectorParameter, -1000, y)
    p.set_editor_property("parameter_name", name)
    mask = node(unreal.MaterialExpressionComponentMask, -800, y)   # float4 param -> float3
    for c in ("r", "g", "b"):
        mask.set_editor_property(c, True)
    req(MEL.connect_material_expressions(p, "", mask, ""), f"{name} mask")
    inputs[pin] = (mask, "")
    y += 120
for name, default in (("Radius", 2200.0), ("CosIn", 0.927), ("CosOut", 0.788)):
    p = node(unreal.MaterialExpressionScalarParameter, -1000, y)
    p.set_editor_property("parameter_name", name)
    p.set_editor_property("default_value", default)
    inputs[name] = (p, "")
    y += 120

march = node(unreal.MaterialExpressionCustom, -500, 300)
march.set_editor_property("code", MARCH_HLSL)
march.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
ins = []
for n in inputs:
    i = unreal.CustomInput()
    i.set_editor_property("input_name", n)
    ins.append(i)
march.set_editor_property("inputs", ins)
for n, (src, out) in inputs.items():
    req(MEL.connect_material_expressions(src, out, march, n), f"{n} -> march")
req(MEL.connect_material_property(march, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
if MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_EMISSIVE_COLOR) is None:
    raise RuntimeError(f"{MAT_NAME}: Emissive unconnected after save")
names = [str(p) for p in MEL.get_scalar_parameter_names(reloaded)] + \
        [str(p) for p in MEL.get_vector_parameter_names(reloaded)]
for want in ("Radius", "CosIn", "CosOut", "Lamp0", "Lamp1", "BeamDir", "Beam"):
    if want not in names:
        raise RuntimeError(f"{MAT_NAME}: {want} param missing after save")
log(f"built + saved {MAT_NAME} (params {names})")
