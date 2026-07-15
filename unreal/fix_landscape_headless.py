"""Headless: load TailOfTheDragon, snap Landscape to expected transform, save.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
LEVEL = "/Game/TailOfTheDragon"
ls_meta = json.loads((DATA_DIR / "stage_meta.json").read_text())["landscape"]

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError(f"could not load {LEVEL}")

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
fixed = 0
for a in actor_sub.get_all_level_actors():
    cls = a.get_class().get_name()
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        before = a.get_actor_location()
        a.set_actor_location(unreal.Vector(*ls_meta["location_cm"]), False, False)
        after = a.get_actor_location()
        unreal.log(f"FIX landscape '{a.get_actor_label()}' [{cls}] "
                   f"({before.x:.0f},{before.y:.0f},{before.z:.0f}) -> "
                   f"({after.x:.0f},{after.y:.0f},{after.z:.0f})")
        fixed += 1

if not fixed:
    raise RuntimeError("no Landscape actor found")

if not lls.save_current_level():
    unreal.log_warning("FIX save_current_level returned False")
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
unreal.log("FIX DONE - saved")
