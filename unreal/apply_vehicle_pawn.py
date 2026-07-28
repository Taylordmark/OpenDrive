"""Template exorcism (2026-07-17): import the pipeline-generated placeholder
car for AODVehiclePawn, point the level at ODGameMode, and DELETE
/Game/VehicleTemplate wholesale. Run make_car_mesh.py first.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

CAR_SRC = Path(__file__).resolve().parents[1] / "data" / "cars" / "placeholder"
CAR_DIR = "/Game/OpenDrive/Car"
LEVEL = "/Game/TailOfTheDragon"
tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"CARPAWN {msg}")


# ------------------------------------------------------------------ import
tasks = []
for fname in ("chassis.obj", "wheel.obj", "T_CarPalette.png"):
    t = unreal.AssetImportTask()
    t.filename = str(CAR_SRC / fname)
    t.destination_path = CAR_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)
tools.import_asset_tasks(tasks)

chassis = unreal.load_asset(f"{CAR_DIR}/chassis")
wheel = unreal.load_asset(f"{CAR_DIR}/wheel")
palette = unreal.load_asset(f"{CAR_DIR}/T_CarPalette")
if not chassis or not wheel or not palette:
    raise RuntimeError("car asset import failed")
palette.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
palette.set_editor_property(
    "mip_gen_settings", unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
log("imported chassis, wheel, palette")

# simple box collision so the body can simulate physics
# (StaticMeshEditorSubsystem is unavailable in commandlets)
unreal.EditorStaticMeshLibrary.remove_collisions(chassis)
n_col = unreal.EditorStaticMeshLibrary.add_simple_collisions(
    chassis, unreal.ScriptingCollisionShapeType.BOX)
log(f"chassis box collision ({n_col} prim)")

# ------------------------------------------------------------------ M_Car
mat_path = f"{CAR_DIR}/M_Car"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    unreal.EditorAssetLibrary.delete_asset(mat_path)
m_car = tools.create_asset("M_Car", CAR_DIR, unreal.Material,
                           unreal.MaterialFactoryNew())
ts = MEL.create_material_expression(
    m_car, unreal.MaterialExpressionTextureSample, -500, 0)
ts.set_editor_property("texture", palette)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
rough = MEL.create_material_expression(
    m_car, unreal.MaterialExpressionConstant, -500, 300)
rough.set_editor_property("r", 0.42)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
m_car.set_editor_property("used_with_nanite", True)
MEL.recompile_material(m_car)
chassis.set_material(0, m_car)
wheel.set_material(0, m_car)
log("built M_Car and assigned")

# --------------------------------------------------------- level game mode
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
ws = world.get_world_settings()
ws.set_editor_property("default_game_mode", unreal.ODGameMode)
log("level world settings game mode -> ODGameMode")
lls.save_current_level()

# ------------------------------------------------- delete VehicleTemplate
if unreal.EditorAssetLibrary.does_directory_exist("/Game/VehicleTemplate"):
    ok = unreal.EditorAssetLibrary.delete_directory("/Game/VehicleTemplate")
    log(f"deleted /Game/VehicleTemplate: {ok}")
    if not ok:
        raise RuntimeError("VehicleTemplate delete failed - references remain")
else:
    log("/Game/VehicleTemplate already gone")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
