"""Move the Landscape actor to the expected stage transform. Run in editor."""

import json
from pathlib import Path

import unreal

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
loc = json.loads((DATA_DIR / "stage_meta.json").read_text())["landscape"]["location_cm"]

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
fixed = 0
for a in actor_sub.get_all_level_actors():
    cls = a.get_class().get_name()
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        a.set_actor_location(unreal.Vector(*loc), False, False)
        unreal.log(f"OpenDrive: moved '{a.get_actor_label()}' to {loc}")
        fixed += 1
if not fixed:
    unreal.log_warning("OpenDrive: no Landscape actor found in this level")
