"""Builds M_Rock030, a real CC0 rock material for AODForestManager's ground-
cover rock/boulder scatter (bonus scope, building-facade art-direction pass,
2026-09-03 - see next steps.md §7.15). Those rocks are two /Engine/
BasicShapes meshes (Cube, Sphere - see ODForestManager.h's RockMeshes
comment for why) that never had ANY material set before this - they rendered
with the engine's own flat grey default, the untextured-boulder look flagged
live. This script just wires up the CC0 Rock030 PBR set
(content_src/textures/ground/Rock030/, already imported by
unreal/import_ground_textures.py for the terrain material - reused here
verbatim, no re-download needed) onto the basic shapes' own UV0.

CRITICAL gotcha (already hit once on this project's tree-bark material, see
next steps.md §7.15's Flora entry): AODForestManager places every rock
through an UHierarchicalInstancedStaticMeshComponent - a material used on an
HISM without used_with_instanced_static_meshes=True silently falls back to
the flat grey engine default at runtime, with no error anywhere. Set below.

Run unreal/import_ground_textures.py FIRST if Rock030 isn't already
imported (apply_ground_material.py already requires this, so on any repo
where terrain has been built at least once this is a no-op check).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_Rock030"
ROCK_TEX_DIR = "/Game/OpenDrive/Textures/Ground/Rock030"
ASSET_ID = "Rock030"
TILE_SCALE = 0.6   # UV multiplier - the basic-shape UV0 is unit-cube/sphere
                    # sized, so a value <1 spreads the 2K texture's detail
                    # across a whole rock instance instead of it looking like
                    # one giant zoomed-in texel.


def log(msg):
    unreal.log_warning(f"ROCKMAT {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

albedo = unreal.load_asset(f"{ROCK_TEX_DIR}/T_{ASSET_ID}_A")
normal = unreal.load_asset(f"{ROCK_TEX_DIR}/T_{ASSET_ID}_N")
rough = unreal.load_asset(f"{ROCK_TEX_DIR}/T_{ASSET_ID}_R")
if albedo is None or normal is None or rough is None:
    raise RuntimeError(
        f"Rock030 textures missing under {ROCK_TEX_DIR} - run "
        f"unreal/import_ground_textures.py first (after "
        f"content_src/textures/ground/download_ground_textures.py)")

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

# see module docstring - without this an HISM-placed rock silently falls
# back to the flat grey engine default at runtime.
mat.set_editor_property("used_with_instanced_static_meshes", True)


def node(cls, x, y, **props):
    n = MEL.create_material_expression(mat, cls, x, y)
    for k, v in props.items():
        n.set_editor_property(k, v)
    return n


def req(ok, what):
    if not ok:
        raise RuntimeError(f"could not connect {what} - pin name mismatch?")


uv = node(unreal.MaterialExpressionTextureCoordinate, -900, 0, u_tiling=TILE_SCALE, v_tiling=TILE_SCALE)

albedo_n = node(unreal.MaterialExpressionTextureSample, -600, -200, texture=albedo)
req(MEL.connect_material_expressions(uv, "", albedo_n, "UVs"), "albedo UV")

normal_n = node(unreal.MaterialExpressionTextureSample, -600, 100, texture=normal,
                sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
req(MEL.connect_material_expressions(uv, "", normal_n, "UVs"), "normal UV")

rough_n = node(unreal.MaterialExpressionTextureSample, -600, 400, texture=rough,
               sampler_type=unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
req(MEL.connect_material_expressions(uv, "", rough_n, "UVs"), "rough UV")
rough_mask = node(unreal.MaterialExpressionComponentMask, -350, 400, r=True)
req(MEL.connect_material_expressions(rough_n, "RGB", rough_mask, ""), "rough mask")

req(MEL.connect_material_property(albedo_n, "RGB", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(normal_n, "RGB", unreal.MaterialProperty.MP_NORMAL), "Normal")
req(MEL.connect_material_property(rough_mask, "", unreal.MaterialProperty.MP_ROUGHNESS), "Roughness")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Normal", unreal.MaterialProperty.MP_NORMAL),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
):
    n = MEL.get_material_property_input_node(reloaded, prop_enum)
    if n is None:
        all_ok = False
        log(f"  MP_{prop_name}: UNCONNECTED (FAIL)")
    else:
        log(f"  MP_{prop_name}: driven by {n.get_class().get_name()}")
if not reloaded.get_editor_property("used_with_instanced_static_meshes"):
    all_ok = False
    log("  used_with_instanced_static_meshes: FALSE (FAIL) - would silently "
        "render grey on the rock HISMs")
if not all_ok:
    raise RuntimeError(
        "M_Rock030: one or more required properties failed to save - see "
        "the post-save verification log above")

log(f"built + saved {MAT_NAME} (asset_id={ASSET_ID}, tile_scale={TILE_SCALE})")
