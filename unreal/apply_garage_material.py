"""Garage/showroom materials (2026-08-27, garage-scene task): three small,
plain materials for AODGarageEnvironment's procedural room (black walls,
glossy dark concrete floor, honeycomb ceiling of cool-white emissive hex
panels) - no textures, no biome/terrain logic, just constants wired straight
to the relevant material outputs. Same idempotent load-or-create +
delete_all_material_expressions()-then-rebuild-in-place pattern as
apply_ground_material.py (see that file's own comment for why: repeatedly
deleting and recreating the asset is unreliable in headless
-run=pythonscript mode, something keeps a just-compiled UMaterial alive a
little longer than a plain load+delete expects).

  M_GarageFloorGlossy - near-black albedo, low roughness (clear-ish
    reflections of the car/hex ceiling; SSR + AODGarageEnvironment's runtime
    box reflection capture both read this).
  M_GarageWallDark    - near-black albedo, higher roughness (matte enough
    that wall corners still read instead of turning into a mirror box).
  M_HexPanelEmissive  - near-black albedo, cool-white (~6500K) emissive,
    strength as a scalar parameter (EmissiveStrength) so it can be tuned
    per-instance without touching the graph, multiplied by the mesh's own
    vertex color - AODGarageEnvironment::BuildHexCeiling() paints a handful
    of hex tiles a dimmer vertex color for "one diode's going" realism, the
    same runtime vertex-color-multiply-into-emissive/base-color trick
    AODTerrainManager's ground material already uses for its biome tint (see
    apply_ground_material.py). Two-sided: BuildHexCeiling()'s per-hex
    triangle fans don't guarantee a consistent winding/normal direction
    relative to "down into the room", and this material can never be
        checked against a real (non -nullrhi) render before Taylor's own
    live look - two-sided removes "wrong winding -> invisible/black ceiling"
    as a failure mode entirely, at the cost of a normal that isn't always
    physically correct, which doesn't matter for a flat, unlit-feeling LED
    panel.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"

FLOOR_COLOR = (0.015, 0.015, 0.02)     # near-black, faint cool tint
FLOOR_ROUGHNESS = 0.2                  # low - glossy, clear-ish reflections

WALL_COLOR = (0.008, 0.008, 0.01)      # near-black
WALL_ROUGHNESS = 0.7                   # matte - corners/seams still read

HEX_BASE_COLOR = (0.01, 0.01, 0.01)
HEX_ROUGHNESS = 0.4
HEX_EMISSIVE_COLOR = (0.55, 0.75, 1.0)     # cool ~6500K white
HEX_EMISSIVE_STRENGTH_DEFAULT = 14.0        # ScalarParameter - tune live via an MID

log_prefix = "GARAGEMAT"


def log(msg):
    unreal.log(f"{log_prefix} {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def get_or_create(name):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mat = unreal.load_asset(path)
        log(f"{name}: existing asset found, clearing expressions in place")
        for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
            MEL.delete_material_expression(mat, _e)
    else:
        mat = tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
        log(f"{name}: created new asset")
    return path, mat


def build_flat_material(name, base_color, roughness):
    path, mat = get_or_create(name)

    color_node = MEL.create_material_expression(
        mat, unreal.MaterialExpressionConstant3Vector, -300, -100)
    color_node.set_editor_property("constant", unreal.LinearColor(*base_color, 1.0))
    MEL.connect_material_property(color_node, "", unreal.MaterialProperty.MP_BASE_COLOR)

    rough_node = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 100)
    rough_node.set_editor_property("r", roughness)
    MEL.connect_material_property(rough_node, "", unreal.MaterialProperty.MP_ROUGHNESS)

    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
    log(f"built + saved {name} (base_color={base_color}, roughness={roughness})")


build_flat_material("M_GarageFloorGlossy", FLOOR_COLOR, FLOOR_ROUGHNESS)
build_flat_material("M_GarageWallDark", WALL_COLOR, WALL_ROUGHNESS)

# ---- M_HexPanelEmissive - flat base/roughness + vertex-color-scaled emissive
hex_path, hex_mat = get_or_create("M_HexPanelEmissive")
hex_mat.set_editor_property("two_sided", True)   # see module docstring

base_node = MEL.create_material_expression(
    hex_mat, unreal.MaterialExpressionConstant3Vector, -500, -300)
base_node.set_editor_property("constant", unreal.LinearColor(*HEX_BASE_COLOR, 1.0))
MEL.connect_material_property(base_node, "", unreal.MaterialProperty.MP_BASE_COLOR)

rough_node = MEL.create_material_expression(hex_mat, unreal.MaterialExpressionConstant, -500, -150)
rough_node.set_editor_property("r", HEX_ROUGHNESS)
MEL.connect_material_property(rough_node, "", unreal.MaterialProperty.MP_ROUGHNESS)

emissive_color = MEL.create_material_expression(
    hex_mat, unreal.MaterialExpressionConstant3Vector, -500, 100)
emissive_color.set_editor_property("constant", unreal.LinearColor(*HEX_EMISSIVE_COLOR, 1.0))

strength = MEL.create_material_expression(hex_mat, unreal.MaterialExpressionScalarParameter, -500, 300)
strength.set_editor_property("parameter_name", "EmissiveStrength")
strength.set_editor_property("default_value", HEX_EMISSIVE_STRENGTH_DEFAULT)

color_times_strength = MEL.create_material_expression(hex_mat, unreal.MaterialExpressionMultiply, -300, 150)
MEL.connect_material_expressions(emissive_color, "", color_times_strength, "A")
MEL.connect_material_expressions(strength, "", color_times_strength, "B")

vertex_color = MEL.create_material_expression(hex_mat, unreal.MaterialExpressionVertexColor, -300, 350)

final_emissive = MEL.create_material_expression(hex_mat, unreal.MaterialExpressionMultiply, -100, 250)
MEL.connect_material_expressions(color_times_strength, "", final_emissive, "A")
MEL.connect_material_expressions(vertex_color, "RGB", final_emissive, "B")
MEL.connect_material_property(final_emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

MEL.recompile_material(hex_mat)
unreal.EditorAssetLibrary.save_asset(hex_path, only_if_is_dirty=False)
log(f"built + saved M_HexPanelEmissive (emissive_color={HEX_EMISSIVE_COLOR}, "
    f"EmissiveStrength param default={HEX_EMISSIVE_STRENGTH_DEFAULT}, two_sided=True, "
    f"vertex-color multiply wired into MP_EMISSIVE_COLOR)")

log("done: M_GarageFloorGlossy, M_GarageWallDark, M_HexPanelEmissive")
