"""Headless: swap landscape-grass trees for the C++ runtime forest.

- spawn AODForestManager ("Forest") into the level
- remove the retired ForestCollision actor (class deleted; skipped on load)
- empty GT_Forest varieties (trees/bushes now come from the manager;
  GT_Ground tufts stay grass-system as pure visuals)

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
LEVEL = "/Game/TailOfTheDragon"


def log(msg):
    unreal.log(f"FOREST {msg}")


gt = unreal.load_asset(f"{MAT_DIR}/GT_Forest")
if gt:
    gt.set_editor_property("grass_varieties", [])
    unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/GT_Forest")
    log("GT_Forest emptied (trees now spawned by AODForestManager)")

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

labels = {}
for a in actor_sub.get_all_level_actors():
    labels[a.get_actor_label()] = a

if "ForestCollision" in labels:
    actor_sub.destroy_actor(labels["ForestCollision"])
    log("removed retired ForestCollision actor")

if "Forest" not in labels:
    a = actor_sub.spawn_actor_from_class(
        unreal.ODForestManager, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    a.set_actor_label("Forest")
    a.set_folder_path("TailOfTheDragon")
    log("spawned Forest (AODForestManager)")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=True)
lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
