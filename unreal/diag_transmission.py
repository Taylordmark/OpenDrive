"""Log the sports car's Chaos transmission + engine setup (read-only diag).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

CAR_BP = "/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar"


def log(msg):
    unreal.log(f"DIAGTRANS {msg}")


bp = unreal.load_asset(CAR_BP)
cdo = unreal.get_default_object(bp.generated_class())
move = cdo.get_editor_property("vehicle_movement_component")

trans = move.get_editor_property("transmission_setup")
for prop in ("gear_change_time", "change_up_rpm", "change_down_rpm",
             "forward_gear_ratios", "final_ratio", "transmission_efficiency",
             "b_use_automatic_gears", "b_use_auto_reverse"):
    try:
        log(f"transmission.{prop} = {trans.get_editor_property(prop)}")
    except Exception as exc:  # property name drift between engine versions
        log(f"transmission.{prop} UNREADABLE: {exc}")

eng = move.get_editor_property("engine_setup")
for prop in ("max_rpm", "engine_idle_rpm", "engine_brake_effect",
             "engine_rev_up_moi", "engine_rev_down_rate"):
    try:
        log(f"engine.{prop} = {eng.get_editor_property(prop)}")
    except Exception as exc:
        log(f"engine.{prop} UNREADABLE: {exc}")

log("DONE")
