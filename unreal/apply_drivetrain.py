"""Spawn AODDrivetrainManager in the level (drivetrain stage 1, 2026-07-16).
Idempotent by label, same pattern as the other manager actors.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

LEVEL = "/Game/TailOfTheDragon"
LABEL = "DrivetrainManager"


def log(msg):
    unreal.log(f"DRIVETRAIN {msg}")


lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
labels = {a.get_actor_label() for a in actor_sub.get_all_level_actors()}

if LABEL in labels:
    log(f"{LABEL} already present")
else:
    cls = getattr(unreal, "ODDrivetrainManager", None)
    if cls is None:
        raise RuntimeError("ODDrivetrainManager not visible to python - build?")
    a = actor_sub.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0),
                                         unreal.Rotator(0, 0, 0))
    a.set_actor_label(LABEL)
    a.set_folder_path("TailOfTheDragon")
    log(f"spawned {LABEL}")

lls.save_current_level()
log("DONE - saved")
