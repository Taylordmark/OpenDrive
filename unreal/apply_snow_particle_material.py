"""Seasons feature part 1 (2026-09-07): builds M_SnowFlake, the tiny
translucent white sphere material AODSeasonController's runtime falling-snow
effect uses (see ODSeasonController.cpp's EnsureSnowfallComponent - a plain
HISM of /Engine/BasicShapes/Sphere instances the controller animates by hand
every tick, NOT Niagara - see that file's own header comment for why: Niagara
asset creation from python is finicky in this UE 5.8 project and a from-
scratch C++ Niagara system still needs an authored data asset to be useful,
so a directly-animated HISM was the simplest robust way to guarantee visible
falling snow for this pass).

Translucent (a solid opaque sphere reads as a hailstone, not a soft flake) +
a real (near-white) BaseColor lit normally by the scene's directional light +
a modest emissive push so flakes stay readable against a dark evergreen
backdrop without needing to catch direct light square-on.

IMPORTANT UE 5.8 GOTCHA found this session (headless QA screenshot -
`-odseason=fall -odseasonphase=0.25` at the Dillon Dam Rd vantage): an
MSM_UNLIT + BLEND_TRANSLUCENT material built exactly this way (constants
verified correctly saved and connected via
MaterialEditingLibrary.get_material_property_input_node, and the flakes were
confirmed genuinely present/animating via a pixel-diff against a zero-
snowfall baseline) rendered as solid black circles in every real `-game`
render regardless of emissive strength - retested at an intentionally
blown-out EMISSIVE_STRENGTH=8.0 diagnostic value and it was STILL solid
black. Switching only the shading model to MSM_DEFAULT_LIT (everything else
identical) immediately fixed it - the flakes render as bright, correctly
colored dots. Root cause not fully isolated (possibly an Unlit-translucent
shader-permutation issue specific to this project's renderer config), but
the fix is simple and free (a HISM this small has no meaningful lit-vs-unlit
perf difference), so this material is Default Lit, not Unlit, going
forward. Documented here so nobody "simplifies" this back to Unlit without
re-hitting the exact same bug.

Same in-place-rebuild MaterialEditingLibrary approach this project's other
apply_*_material.py scripts already established.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_SnowFlake"

SNOW_COLOR = (0.95, 0.96, 0.98)
OPACITY = 0.92
# Modest push on top of the lit BaseColor (see MSM_DEFAULT_LIT note above) -
# keeps flakes readable in shadow/backlit conditions without blowing out to
# a glowing orb in direct sun.
EMISSIVE_STRENGTH = 1.2


def log(msg):
    unreal.log_warning(f"SNOWFXMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"M_SnowFlake: failed to connect {what}")


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

# Pre-multiplying in Python and wiring a single Constant3Vector straight to
# each property (rather than a Constant3Vector*Constant Multiply node)
# sidesteps a separate, narrower gotcha also found this session: a Multiply
# node's own A/B inputs aren't Python-readable afterward to verify directly
# (get_material_property_input_node only confirms the OUTER property
# connection, not that an intermediate node's own inputs landed), so a
# silently-unconnected Multiply input would be invisible to this script's
# own verification. One constant per property removes that whole class of
# bug and needs no runtime tuning (this material has no exposed parameters).
final_emissive = tuple(c * EMISSIVE_STRENGTH for c in SNOW_COLOR)
emissive_node = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, 0)
emissive_node.set_editor_property("constant", unreal.LinearColor(*final_emissive, 1.0))
req(MEL.connect_material_property(emissive_node, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR),
    "Constant3Vector -> MP_EMISSIVE_COLOR")

basecolor_node = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, -150)
basecolor_node.set_editor_property("constant", unreal.LinearColor(*SNOW_COLOR, 1.0))
req(MEL.connect_material_property(basecolor_node, "", unreal.MaterialProperty.MP_BASE_COLOR),
    "Constant3Vector -> MP_BASE_COLOR")

opacity_node = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -400, 300)
opacity_node.set_editor_property("r", OPACITY)
req(MEL.connect_material_property(opacity_node, "", unreal.MaterialProperty.MP_OPACITY),
    "Constant -> MP_OPACITY")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
node = MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
if node is None:
    raise RuntimeError("M_SnowFlake: MP_EMISSIVE_COLOR failed to connect - see script")
node2 = MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_BASE_COLOR)
if node2 is None:
    raise RuntimeError("M_SnowFlake: MP_BASE_COLOR failed to connect - see script")
log(f"built + saved {MAT_NAME} (translucent default-lit, base_color={SNOW_COLOR}, "
    f"emissive={final_emissive}, opacity={OPACITY})")
