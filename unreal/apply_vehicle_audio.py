"""Headless: spawn the Arc 5.1 vehicle audio conductor into the level.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

LEVEL = "/Game/TailOfTheDragon"

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

labels = {a.get_actor_label() for a in actor_sub.get_all_level_actors()}
if "VehicleAudio" not in labels:
    a = actor_sub.spawn_actor_from_class(
        unreal.ODVehicleAudio, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    a.set_actor_label("VehicleAudio")
    a.set_folder_path("TailOfTheDragon")
    unreal.log("AUDIO spawned VehicleAudio")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
unreal.log("AUDIO DONE - saved")
