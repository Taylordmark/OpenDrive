"""Lift-off pitch-flare fix (drive test 2026-07-16): the auto box downshifted
at 2000 rpm during coast-down, rev-matching the engine UP ~1.4x mid-lift.
ChangeDownRPM -> 1150 (idle is 900) so coast downshifts land near-silent.
Full transmission modes (DCT/manual/shift-cut) are a separate arc.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

CAR_BP = "/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar"


def log(msg):
    unreal.log(f"TRANSFIX {msg}")


bp = unreal.load_asset(CAR_BP)
cdo = unreal.get_default_object(bp.generated_class())
move = cdo.get_editor_property("vehicle_movement_component")
trans = move.get_editor_property("transmission_setup")

before = trans.get_editor_property("change_down_rpm")
trans.set_editor_property("change_down_rpm", 1150.0)
move.set_editor_property("transmission_setup", trans)

if not unreal.EditorAssetLibrary.save_asset(CAR_BP):
    raise RuntimeError("save failed")
log(f"change_down_rpm {before} -> 1150.0, saved {CAR_BP}")
log("DONE")
