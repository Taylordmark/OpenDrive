"""Dirt/gravel road material (2026-09-30): M_Road_Gravel + PM_Gravel.

AODRoadManager puts M_Road_Gravel on every road chunk whose ODR3 flags byte
has bit1 set (OSM surface=dirt/gravel/etc. - see pipeline/road_mesh_common.
unpaved_stations()). Plain Gravel023 (the same set M_Road already uses for
its shoulder, so paved->gravel seams match), world-XY tiled, no markings.

PM_Gravel: its name is what the C++ keys on - "Gravel" counts as road for
the spawn ground-snap and tree exclusion (like "Asphalt"), grips at the
off-road tier (ODVehicleMovement::SurfaceGrip), and plays the gravel rumble
(ODVehicleAudio).

Needs unreal/import_road_textures.py to have run (M_Road needs it too).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
PM_DIR = "/Game/OpenDrive/Physics"
MAT_NAME = "M_Road_Gravel"
GRAVEL_ID = "Gravel023"
GRAVEL_TILE_CM = 140.0          # apply_road_material.py's GRAVEL tile (1.4 m)

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"GRAVELROAD {msg}")


# ------------------------------------------------------------- physmat
pm_path = f"{PM_DIR}/PM_Gravel"
if unreal.EditorAssetLibrary.does_asset_exist(pm_path):
    pm = unreal.load_asset(pm_path)
else:
    pm = tools.create_asset("PM_Gravel", PM_DIR, unreal.PhysicalMaterial,
                            unreal.PhysicalMaterialFactoryNew())
pm.set_editor_property("friction", 0.7)   # chassis contact only; wheel grip is SurfaceGrip's
unreal.EditorAssetLibrary.save_loaded_asset(pm)

# ------------------------------------------------------------- textures
base = f"/Game/OpenDrive/Textures/Road/{GRAVEL_ID}"
tex = {k: unreal.load_asset(f"{base}/T_{GRAVEL_ID}_{k}") for k in ("A", "N", "R")}
if None in tex.values():
    raise RuntimeError(f"{GRAVEL_ID} missing under {base} - run unreal/import_road_textures.py")

# ------------------------------------------------------------- material
mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())


def node(cls, x, y, **props):
    n = MEL.create_material_expression(mat, cls, x, y)
    for k, v in props.items():
        n.set_editor_property(k, v)
    return n


def link(a, a_out, b, b_in):
    if not MEL.connect_material_expressions(a, a_out, b, b_in):
        raise RuntimeError(f"could not connect {b_in} - pin name mismatch?")


wp = node(unreal.MaterialExpressionWorldPosition, -1200, 0)
xy = node(unreal.MaterialExpressionComponentMask, -1000, 0, r=True, g=True)
link(wp, "", xy, "")
scale = node(unreal.MaterialExpressionConstant, -1000, 150, r=1.0 / GRAVEL_TILE_CM)
uv = node(unreal.MaterialExpressionMultiply, -800, 0)
link(xy, "", uv, "A")
link(scale, "", uv, "B")

samples = {}
for i, k in enumerate(("A", "N", "R")):
    s = node(unreal.MaterialExpressionTextureSample, -500, i * 300, texture=tex[k])
    if k == "N":
        s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
    if not any(MEL.connect_material_expressions(uv, "", s, pin) for pin in ("UVs", "Coordinates")):
        raise RuntimeError("could not connect TextureSample UV")
    samples[k] = s

ok = (MEL.connect_material_property(samples["A"], "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
      and MEL.connect_material_property(samples["N"], "RGB", unreal.MaterialProperty.MP_NORMAL)
      and MEL.connect_material_property(samples["R"], "R", unreal.MaterialProperty.MP_ROUGHNESS))
if not ok:
    raise RuntimeError(f"{MAT_NAME}: could not connect BaseColor/Normal/Roughness")

mat.set_editor_property("phys_material", pm)
MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
log(f"built + saved {MAT_NAME} ({GRAVEL_ID}, tile {GRAVEL_TILE_CM} cm) + PM_Gravel")
log("DONE")
