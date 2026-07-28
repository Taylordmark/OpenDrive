"""Chaos rip-out follow-up (2026-07-18): UODVehicleMovement's suspension rays
trace COMPLEX geometry, and complex hits return the render material's phys
material - not the body_setup one Arc 1.4 assigned. Give M_Road (and any
road-dir materials) PM_Asphalt so asphalt reads 1.0 grip instead of the
0.85 unknown-surface fallback.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

PM = unreal.load_asset("/Game/OpenDrive/Physics/PM_Asphalt")
if not PM:
    raise RuntimeError("PM_Asphalt missing - run apply_arc1_headless.py")


def log(msg):
    unreal.log(f"ROADPM {msg}")


n = 0
for path in ("/Game/OpenDrive/Materials/M_Road",):
    m = unreal.load_asset(path)
    if not m:
        log(f"missing {path}")
        continue
    m.set_editor_property("phys_material", PM)
    unreal.EditorAssetLibrary.save_loaded_asset(m)
    n += 1
    log(f"PM_Asphalt -> {path}")

log(f"DONE ({n} materials)")
