"""Builds M_BuildingFacade, the placeholder material for procedurally
extruded buildings (Arc 7.0 step 3, 2026-07-24) - same cheap
"one flat-shaded material, no imagery drape" approach as
apply_ground_material.py's ground material: a simple vertical
window-band pattern from the wall UV's V coordinate (walls' V = height in
FACADE_TILE_M units, from build_building_mesh.py), so it reads as
"building" at a glance without needing an actual texture asset.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_BuildingFacade"

WALL_COLOR = (0.55, 0.52, 0.48)     # stylized concrete/stucco
WINDOW_COLOR = (0.25, 0.32, 0.38)   # stylized window-band tint
ROUGHNESS = 0.7


def log(msg):
    unreal.log(f"BUILDINGMAT {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    unreal.EditorAssetLibrary.delete_asset(mat_path)
mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
# OSM building-footprint rings have no guaranteed winding direction (unlike
# this project's own generated meshes, e.g. build_road_chunks' proven
# convention) - build_building_mesh.py's wall quads inherit whatever
# direction each source way happened to use, so roughly half could face
# inward and backface-cull invisible. Two-sided sidesteps needing to
# normalize/guess winding from untrusted source data.
mat.set_editor_property("two_sided", True)

# vertical window-band pattern: frac(V) < 0.5 -> window, else wall - reads
# as horizontal window rows going up the facade (V is height in
# FACADE_TILE_M-periodic units, see build_building_mesh.py's wall UV)
uv = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -900, 0)
v_chan = MEL.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -700, 0)
v_chan.set_editor_property("r", False)
v_chan.set_editor_property("g", True)
v_chan.set_editor_property("b", False)
v_chan.set_editor_property("a", False)
MEL.connect_material_expressions(uv, "", v_chan, "")

frac = MEL.create_material_expression(mat, unreal.MaterialExpressionFrac, -500, 0)
MEL.connect_material_expressions(v_chan, "", frac, "")

band = MEL.create_material_expression(mat, unreal.MaterialExpressionIf, -300, 0)
MEL.connect_material_expressions(frac, "", band, "A")
half = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -500, 150)
half.set_editor_property("r", 0.5)
MEL.connect_material_expressions(half, "", band, "B")
window_c = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -500, -200)
window_c.set_editor_property("constant", unreal.LinearColor(*WINDOW_COLOR, 1.0))
wall_c = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -500, 300)
wall_c.set_editor_property("constant", unreal.LinearColor(*WALL_COLOR, 1.0))
# UMaterialExpressionIf's branch-output pin NAMES (as the python binding
# reports them via get_material_expression_input_names) are the human-
# readable captions "A > B"/"A == B"/"A < B", NOT the C++ property
# identifiers ("AGreaterThanB"/"AEqualsB"/"ALessThanB") - same class of
# mismatch as MaterialExpressionClamp's unnamed "Input" pin
# (apply_ground_material.py, found earlier the same day). Confirmed via
# get_inputs_for_material_expression(): the old names silently connected
# nothing, leaving AGreaterThanB/ALessThanB (both REQUIRED, no default)
# unconnected, so this material has been failing to compile every launch
# ("Failed to compile Material ... Default Material will be used in game")
# since it was created - the "black and gray checkerboard" building look
# Taylor reported live 2026-07-28.
MEL.connect_material_expressions(window_c, "", band, "A < B")
MEL.connect_material_expressions(wall_c, "", band, "A > B")
MEL.connect_material_expressions(wall_c, "", band, "A == B")

MEL.connect_material_property(band, "", unreal.MaterialProperty.MP_BASE_COLOR)
rough = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -200, 400)
rough.set_editor_property("r", ROUGHNESS)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.recompile_material(mat)

# real bug, found 2026-07-27: this script never actually saved the asset to
# disk - it "succeeded" every headless run (material created, compiled,
# logged) but the package stayed transient/in-memory only and vanished the
# instant that commandlet process exited, silently leaving Content/ without
# the material on the very next run (surfaced as AODBuildingManager's
# "Failed to find object M_BuildingFacade" warning even right after a
# supposedly-successful apply). save_asset() is the fix.
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
log(f"built + saved {MAT_NAME} (wall={WALL_COLOR}, window={WINDOW_COLOR})")
