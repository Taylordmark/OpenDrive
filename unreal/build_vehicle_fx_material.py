"""Track E (PLAN-VISUALS.md, 2026-10-09): builds M_FXPuff, the material
UODVehicleFX draws its tire smoke / dust / spray puffs with (instanced
/Engine/BasicShapes/Sphere, animated in C++ - see ODVehicleFX.h for why not
Niagara). Realistic-only helper, so it lives in Realistic/FX, not beside a
stylized twin.

  BaseColor = PerInstanceCustomData[0..2]   (per-effect color, set by C++)
  Opacity   = DepthFade((1 - Fresnel) * PerInstanceCustomData[3])
              soft-edged ball, no hard line where it meets the road

Translucent, Default Lit (NOT Unlit - apply_snow_particle_material.py's
solid-black Unlit-translucent gotcha) with Volumetric NonDirectional
translucency lighting, the usual particle mode: a puff is lit as a volume,
not shaded like a ball.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Realistic/FX"
MAT_NAME = "M_FXPuff"
FRESNEL_EXPONENT = 2.0      # higher = softer, smaller-looking core
DEPTH_FADE_CM = 60.0


def log(msg):
    unreal.log_warning(f"VEHICLEFXMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"M_FXPuff: failed to connect {what}")


MEL = unreal.MaterialEditingLibrary
unreal.EditorAssetLibrary.make_directory(MAT_DIR)
mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
mat.set_editor_property("translucency_lighting_mode",
                        unreal.TranslucencyLightingMode.TLM_VOLUMETRIC_NON_DIRECTIONAL)
mat.set_editor_property("used_with_instanced_static_meshes", True)


def node(cls, x, y, **props):
    n = MEL.create_material_expression(mat, cls, x, y)
    for k, v in props.items():
        n.set_editor_property(k, v)
    return n


cd = [node(unreal.MaterialExpressionPerInstanceCustomData, -1000, 120 * i,
           data_index=i, const_default_value=0.8 if i < 3 else 0.0) for i in range(4)]

rg = node(unreal.MaterialExpressionAppendVector, -700, 0)
req(MEL.connect_material_expressions(cd[0], "", rg, "A"), "rg.A")
req(MEL.connect_material_expressions(cd[1], "", rg, "B"), "rg.B")
rgb = node(unreal.MaterialExpressionAppendVector, -500, 60)
req(MEL.connect_material_expressions(rg, "", rgb, "A"), "rgb.A")
req(MEL.connect_material_expressions(cd[2], "", rgb, "B"), "rgb.B")
req(MEL.connect_material_property(rgb, "", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")

fres = node(unreal.MaterialExpressionFresnel, -1000, 600, exponent=FRESNEL_EXPONENT,
            base_reflect_fraction=0.0)
soft = node(unreal.MaterialExpressionOneMinus, -800, 600)
req(MEL.connect_material_expressions(fres, "", soft, ""), "OneMinus")  # single input: "" (its name "Input" is rejected)
mul = node(unreal.MaterialExpressionMultiply, -600, 500)
req(MEL.connect_material_expressions(soft, "", mul, "A"), "mul.A")
req(MEL.connect_material_expressions(cd[3], "", mul, "B"), "mul.B")
fade = node(unreal.MaterialExpressionDepthFade, -400, 500, fade_distance_default=DEPTH_FADE_CM)
req(any(MEL.connect_material_expressions(mul, "", fade, name) for name in ("Opacity", "InOpacity", "")),
    "DepthFade.Opacity")
req(MEL.connect_material_property(fade, "", unreal.MaterialProperty.MP_OPACITY), "Opacity")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# verify the saved graph end to end (intermediate inputs too)
reloaded = unreal.load_asset(mat_path)


def inputs(n):
    return [i for i in MEL.get_inputs_for_material_expression(reloaded, n) if i is not None]


base = MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_BASE_COLOR)
op = MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_OPACITY)
if base is None or op is None:
    raise RuntimeError("M_FXPuff: BaseColor/Opacity not connected")
if len(inputs(base)) != 2 or len(inputs(inputs(base)[0])) + len(inputs(inputs(base)[1])) != 2:
    raise RuntimeError(f"M_FXPuff: color append chain incomplete ({inputs(base)})")
m = inputs(op)
if len(m) != 1 or len(inputs(m[0])) != 2:
    raise RuntimeError(f"M_FXPuff: opacity chain incomplete ({m})")
log(f"built + saved {mat_path} (translucent volumetric-lit, fresnel {FRESNEL_EXPONENT}, "
    f"depth fade {DEPTH_FADE_CM} cm, color+alpha from custom data 0..3)")
