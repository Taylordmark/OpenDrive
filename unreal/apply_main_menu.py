"""Spawn AODMainMenu into the open-world level in place (front-end menu:
car config -> map spawn, 2026-07-25). Idempotent by label, same pattern as
apply_pause_menu.py and this project's other per-level gameplay-manager
apply scripts.

The car-config + map-select UI is drawn by the ODRaceHUD already on the level;
this actor just owns the menu state/input. Needs the road's PlaceLabel data
(already applied via apply_place_labels.py) for the clickable town nodes.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

LEVEL = "/Game/OpenWorldTest"
FOLDER = "OpenWorldTest"
LABEL = "MainMenu"


def log(msg):
    unreal.log(f"MAINMENU {msg}")


lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
labels = {a.get_actor_label() for a in actor_sub.get_all_level_actors()}

if LABEL in labels:
    log(f"{LABEL} already present")
else:
    cls = getattr(unreal, "ODMainMenu", None)
    if cls is None:
        raise RuntimeError("ODMainMenu not visible to python - build the C++ first")
    a = actor_sub.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0),
                                         unreal.Rotator(0, 0, 0))
    a.set_actor_label(LABEL)
    a.set_folder_path(FOLDER)
    log(f"spawned {LABEL}")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
