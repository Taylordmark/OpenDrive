"""Read-only diag: arcade stability assists, steering curve, angular damping
on the sports car (hunting the 'car won't rotate' stability, 2026-07-17).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

CAR_BP = "/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar"


def log(msg):
    unreal.log(f"DIAGASSIST {msg}")


bp = unreal.load_asset(CAR_BP)
cdo = unreal.get_default_object(bp.generated_class())
move = cdo.get_editor_property("vehicle_movement_component")

for section, fields in (
    ("torque_control", ("enabled", "yaw_torque_scaling", "yaw_from_steering",
                        "yaw_from_roll_torque_scaling", "roll_torque_scaling",
                        "pitch_torque_scaling", "rotation_damping")),
    ("target_rotation_control", ("enabled", "roll_vs_speed_enabled",
                                 "rotation_stiffness", "rotation_damping",
                                 "max_accel", "auto_centre_roll_strength",
                                 "auto_centre_pitch_strength",
                                 "auto_centre_yaw_strength")),
    ("stabilize_control", ("enabled", "altitude_hold_z", "position_hold_xy")),
):
    try:
        cfg = move.get_editor_property(section)
        for f in fields:
            try:
                log(f"{section}.{f} = {cfg.get_editor_property(f)}")
            except Exception as exc:
                log(f"{section}.{f} UNREADABLE")
    except Exception as exc:
        log(f"{section} UNREADABLE: {exc}")

try:
    steer = move.get_editor_property("steering_setup")
    log(f"steering_setup: {steer}")
    curve = steer.get_editor_property("angle_ratio_vs_speed")
    log(f"angle_ratio_vs_speed: {curve}")
except Exception as exc:
    log(f"steering UNREADABLE: {exc}")

mesh = cdo.get_editor_property("mesh")
body = mesh.get_editor_property("body_instance")
log(f"mesh angular_damping = {body.get_editor_property('angular_damping')}")
log(f"mesh linear_damping = {body.get_editor_property('linear_damping')}")

for wheel_bp in ("SportsCar_WheelsFront", "SportsCar_WheelsRear"):
    wbp = unreal.load_asset(
        f"/Game/VehicleTemplate/Blueprints/SportsCar/{wheel_bp}")
    wcdo = unreal.get_default_object(wbp.generated_class())
    for prop in ("max_steer_angle", "cornering_stiffness", "side_slip_modifier",
                 "slip_threshold", "skid_threshold", "max_wheelspin_rotation",
                 "wheel_radius", "wheel_mass"):
        try:
            log(f"{wheel_bp}.{prop} = {wcdo.get_editor_property(prop)}")
        except Exception as exc:
            log(f"{wheel_bp}.{prop} UNREADABLE: {exc}")

log("DONE")
