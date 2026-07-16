"""Headless Arc 1.5: create tuning preset data assets + spawn the manager.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

TUNE_DIR = "/Game/OpenDrive/Tuning"
LEVEL = "/Game/TailOfTheDragon"
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"TUNE {msg}")


# name: (damping, spring_scale, ride_delta, f_grip, r_grip, torque, down, com_z)
PRESETS = {
    "Street":  (0.90, 1.00,  0.0, 1.00, 1.00, 1.00, 1.1, 42.0),
    "Rally":   (0.75, 0.75,  4.0, 0.95, 0.95, 1.00, 0.5, 48.0),
    "Drift":   (0.85, 1.15, -1.0, 1.05, 0.72, 1.30, 0.6, 40.0),
    "Drag":    (0.95, 1.20, -1.0, 1.00, 1.25, 1.60, 0.9, 38.0),
    "Offroad": (0.65, 0.60,  8.0, 0.90, 0.90, 1.10, 0.2, 50.0),
}

assets = []
for name, (damp, spring, ride, fg, rg, tq, down, com) in PRESETS.items():
    path = f"{TUNE_DIR}/DA_Tuning_{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        a = unreal.load_asset(path)
    else:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.ODTuningPreset)
        a = tools.create_asset(f"DA_Tuning_{name}", TUNE_DIR,
                               unreal.ODTuningPreset, factory)
    a.set_editor_property("display_name", name)
    a.set_editor_property("damping_ratio", damp)
    a.set_editor_property("spring_rate_scale", spring)
    a.set_editor_property("ride_height_delta", ride)
    a.set_editor_property("front_friction_scale", fg)
    a.set_editor_property("rear_friction_scale", rg)
    a.set_editor_property("torque_scale", tq)
    a.set_editor_property("downforce", down)
    a.set_editor_property("center_of_mass_z", com)
    unreal.EditorAssetLibrary.save_asset(path)
    assets.append(a)
    log(f"preset {name} saved")

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

mgr = None
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == "TuningManager":
        mgr = a
if mgr is None:
    mgr = actor_sub.spawn_actor_from_class(
        unreal.ODTuningManager, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    mgr.set_actor_label("TuningManager")
    mgr.set_folder_path("TailOfTheDragon")
    log("spawned TuningManager")
mgr.set_editor_property("presets", assets)
log(f"TuningManager presets set ({len(assets)})")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
