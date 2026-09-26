"""Weather W4 (2026-09-25, next steps.md sec 7.42): builds M_TornadoFunnel,
the translucent dust/cloud grey AODWeatherController draws its tornado
funnel (a stack of /Engine/BasicShapes/Cylinder instances) and debris ring
(Cube instances) with. Same shape as apply_snow_particle_material.py:
translucent + MSM_DEFAULT_LIT (NOT Unlit - see that file's docstring for the
solid-black Unlit-translucent gotcha), two-sided, one constant per property.

One exposed param, "Opacity" (default FUNNEL_OPACITY): the controller's MID
fades the funnel in/out with it, wired straight to MP_OPACITY so the
post-save check can see it.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_TornadoFunnel"

FUNNEL_COLOR = (0.16, 0.16, 0.15)   # dark condensation/debris grey
FUNNEL_OPACITY = 0.55
EMISSIVE = (0.02, 0.02, 0.02)       # keeps it from going pitch black under storm light


def log(msg):
    unreal.log_warning(f"TORNADOMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"M_TornadoFunnel: failed to connect {what}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
mat.set_editor_property("two_sided", True)
mat.set_editor_property("used_with_instanced_static_meshes", True)

color = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, -100)
color.set_editor_property("constant", unreal.LinearColor(*FUNNEL_COLOR, 1.0))
emissive = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, 50)
emissive.set_editor_property("constant", unreal.LinearColor(*EMISSIVE, 1.0))
opacity = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -400, 200)
opacity.set_editor_property("parameter_name", "Opacity")
opacity.set_editor_property("default_value", FUNNEL_OPACITY)

req(MEL.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")
req(MEL.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY), "Opacity")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
for prop in (unreal.MaterialProperty.MP_BASE_COLOR, unreal.MaterialProperty.MP_EMISSIVE_COLOR,
             unreal.MaterialProperty.MP_OPACITY):
    n = MEL.get_material_property_input_node(reloaded, prop)
    if n is None:
        raise RuntimeError(f"M_TornadoFunnel: {prop} unconnected after save")
    log(f"  {prop}: {n.get_class().get_name()}")
log(f"built + saved {MAT_NAME} (translucent default-lit, color={FUNNEL_COLOR}, Opacity param={FUNNEL_OPACITY})")
