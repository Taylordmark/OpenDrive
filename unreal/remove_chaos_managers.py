"""Chaos rip-out (2026-07-18): delete the level's AODDrivetrainManager and
AODSurfaceTractionManager actors. Their jobs move into UODVehicleMovement on
the pawn, and the classes are deleted from C++ - this must run BEFORE the
rebuild so the actors can still be resolved and removed cleanly.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

LEVEL = "/Game/TailOfTheDragon"
DOOMED = {"DrivetrainManager", "TractionManager"}
DOOMED_CLASSES = {"ODDrivetrainManager", "ODSurfaceTractionManager"}


def log(msg):
    unreal.log(f"RIPOUT {msg}")


lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
removed = 0
for a in actor_sub.get_all_level_actors():
    label = a.get_actor_label()
    cls = a.get_class().get_name()
    if label in DOOMED or cls in DOOMED_CLASSES:
        log(f"deleting {label} ({cls})")
        actor_sub.destroy_actor(a)
        removed += 1

log(f"removed {removed} actors")
lls.save_current_level()
log("DONE - saved")
