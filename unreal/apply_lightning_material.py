"""Wet weather (PLAN-REALISM Phase 6, 2026-10-09): builds M_LightningBolt, the
glowing material AODAtmosphereController draws each strike's bolt with (a
chain of /Engine/BasicShapes/Cylinder instances). Opaque default-lit with a
black base and all of its look in emissive - sidesteps the Unlit-translucent
black gotcha (apply_snow_particle_material.py) and needs no sorting.

One exposed param, "Glow" (default 0): the controller's MID sets it to the
flash envelope every frame (peak 1.5 far .. 6 near), so the bolt flickers
with the double stroke and goes dark with the flash. Emissive =
BOLT_COLOR x Glow x BOLT_EMISSIVE; bloom does the halo.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_LightningBolt"

BOLT_COLOR = (0.8, 0.85, 1.0)   # blue-white
BOLT_EMISSIVE = 50.0            # x Glow: ~75 far .. ~300 near


def log(msg):
    unreal.log_warning(f"LIGHTNINGMAT {msg}")


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

mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
mat.set_editor_property("used_with_instanced_static_meshes", True)

black = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, -100)
black.set_editor_property("constant", unreal.LinearColor(0.0, 0.0, 0.0, 1.0))
color = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -700, 50)
color.set_editor_property("constant", unreal.LinearColor(*BOLT_COLOR, 1.0))
glow = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -700, 200)
glow.set_editor_property("parameter_name", "Glow")
glow.set_editor_property("default_value", 0.0)
scale = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -700, 300)
scale.set_editor_property("r", BOLT_EMISSIVE)
glow_scaled = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -500, 250)
req(MEL.connect_material_expressions(glow, "", glow_scaled, "A"), "Glow x scale A")
req(MEL.connect_material_expressions(scale, "", glow_scaled, "B"), "Glow x scale B")
emissive = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -300, 100)
req(MEL.connect_material_expressions(color, "", emissive, "A"), "color x glow A")
req(MEL.connect_material_expressions(glow_scaled, "", emissive, "B"), "color x glow B")

req(MEL.connect_material_property(black, "", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
for prop in (unreal.MaterialProperty.MP_BASE_COLOR, unreal.MaterialProperty.MP_EMISSIVE_COLOR):
    n = MEL.get_material_property_input_node(reloaded, prop)
    if n is None:
        raise RuntimeError(f"{MAT_NAME}: {prop} unconnected after save")
    log(f"  {prop}: {n.get_class().get_name()}")
if "Glow" not in [str(p) for p in MEL.get_scalar_parameter_names(reloaded)]:
    raise RuntimeError(f"{MAT_NAME}: Glow param missing after save")
log(f"built + saved {MAT_NAME} (opaque, emissive {BOLT_COLOR} x Glow x {BOLT_EMISSIVE})")
