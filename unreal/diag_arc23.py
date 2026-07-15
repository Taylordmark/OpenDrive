"""Headless verify for Arc 2+3: re-read level in a fresh process and report.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
LEVEL = "/Game/TailOfTheDragon"
ls_meta = json.loads((STAGE_DIR / "stage_meta.json").read_text())["landscape"]

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

found = {}
for a in actor_sub.get_all_level_actors():
    label = a.get_actor_label()
    cls = a.get_class().get_name()
    if label in ("DayNight", "RaceManager", "TractionManager", "ForestCollision"):
        found[label] = cls
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        loc = a.get_actor_location()
        scl = a.get_actor_scale3d()
        unreal.log(f"DIAG landscape loc=({loc.x:.1f},{loc.y:.1f},{loc.z:.1f}) "
                   f"scale=({scl.x:.4f},{scl.y:.4f},{scl.z:.4f}) "
                   f"expected loc={ls_meta['location_cm']} scale={ls_meta['scale']}")
    if label == "RaceManager":
        gates = a.get_editor_property("checkpoints")
        first = gates[0].translation if len(gates) else None
        unreal.log(f"DIAG RaceManager gates={len(gates)} first={first}")

unreal.log(f"DIAG managers present: {found}")

gm = unreal.load_asset("/Game/VehicleTemplate/Blueprints/BP_VehicleAdvGameMode")
cdo = unreal.get_default_object(gm.generated_class())
unreal.log(f"DIAG game mode hud_class = {cdo.get_editor_property('hud_class')}")
unreal.log("DIAG DONE")
