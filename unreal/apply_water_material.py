"""Arc 7.7 Tier 0 water fix (AUDIT.md, scoped 2026-07-27): builds a simple
stylized water material for the single large sea-level plane
build_openworld_test_level.py spawns. Deliberately NOT the real Tier 1 fix
(OSM coastline/lake fetch + AODWaterManager tile streaming, see next
steps.md sec 7.7) - this is the cheap immediate visual fix for "there is no
water anywhere on the island," a flat plane at sea level with a stylized
translucent material (subtle animated ripple via a panned Noise node, no
texture asset needed - matches this project's low-poly storybook direction
and the same MaterialEditingLibrary node-graph approach every other
generated material here already uses).

Same "one-off script must be called from the from-scratch level builder to
survive a rebuild" lesson this project has hit repeatedly (Sun mobility,
AODMainMenu, M_BuildingFacade, PlayerStart pick) - build_openworld_test_level.py
calls this directly rather than depending on a separate apply step.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_WaterStylized"

WATER_COLOR = (0.02, 0.12, 0.18)     # deep stylized teal, not literal ocean-photo blue
OPACITY = 0.82
ROUGHNESS = 0.06                      # glossy - stylized water reads as a mirror-ish surface
RIPPLE_SCALE = 0.0006                 # world-units noise frequency (cm) - broad, gentle swell
RIPPLE_SPEED = 0.03                   # how fast the ripple pans
RIPPLE_STRENGTH = 0.15                # normal perturbation magnitude


def log(msg):
    unreal.log(f"WATERMAT {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    unreal.EditorAssetLibrary.delete_asset(mat_path)
mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
mat.set_editor_property("two_sided", True)

color = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, -200)
color.set_editor_property("constant", unreal.LinearColor(*WATER_COLOR, 1.0))
MEL.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)

opacity = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -400, 0)
opacity.set_editor_property("r", OPACITY)
MEL.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)

roughness = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -400, 100)
roughness.set_editor_property("r", ROUGHNESS)
MEL.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)

# ---- gentle scrolling ripple: pan world XY by Time before feeding Noise,
# then wire the (small) noise output into the normal's X/Y so the surface
# reads as subtly moving instead of a dead-flat mirror.
world_pos = MEL.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -900, 300)
time_node = MEL.create_material_expression(mat, unreal.MaterialExpressionTime, -900, 500)
speed = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -900, 600)
speed.set_editor_property("r", RIPPLE_SPEED)
pan_amount = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -750, 550)
MEL.connect_material_expressions(time_node, "", pan_amount, "A")
MEL.connect_material_expressions(speed, "", pan_amount, "B")

pos_plus_pan = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -600, 400)
MEL.connect_material_expressions(world_pos, "", pos_plus_pan, "A")
MEL.connect_material_expressions(pan_amount, "", pos_plus_pan, "B")

noise = MEL.create_material_expression(mat, unreal.MaterialExpressionNoise, -400, 400)
noise.set_editor_property("scale", 1.0 / RIPPLE_SCALE)
MEL.connect_material_expressions(pos_plus_pan, "", noise, "Position")

strength = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -250, 500)
strength.set_editor_property("r", RIPPLE_STRENGTH)
ripple = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -100, 450)
MEL.connect_material_expressions(noise, "", ripple, "A")
MEL.connect_material_expressions(strength, "", ripple, "B")

normal = MEL.create_material_expression(mat, unreal.MaterialExpressionAppendVector, 100, 400)
MEL.connect_material_expressions(ripple, "", normal, "A")
MEL.connect_material_expressions(ripple, "", normal, "B")
MEL.connect_material_property(normal, "", unreal.MaterialProperty.MP_NORMAL)

MEL.recompile_material(mat)
# see apply_ground_material.py's own hard-won note: an explicit save is
# required or the asset vanishes the instant this commandlet exits (Content/
# is otherwise only ever populated by real editor saves, never implicit).
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
log(f"built + saved {MAT_NAME} (color={WATER_COLOR}, opacity={OPACITY}, roughness={ROUGHNESS})")
