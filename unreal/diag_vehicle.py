"""Headless: dump the template vehicle's Chaos setup (DIAGV lines).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

PROPS_MOVEMENT = (
    "mass", "chassis_width", "chassis_height", "drag_coefficient",
    "downforce_coefficient", "center_of_mass_override",
    "enable_center_of_mass_override", "inertia_tensor_scale")
PROPS_WHEEL = (
    "wheel_radius", "wheel_width", "friction_force_multiplier",
    "cornering_stiffness", "side_slip_modifier", "slip_threshold",
    "skid_threshold", "suspension_max_raise", "suspension_max_drop",
    "suspension_damping_ratio", "suspension_smoothing",
    "spring_rate", "spring_preload", "suspension_axis",
    "wheel_load_ratio", "max_steer_angle", "affected_by_engine",
    "affected_by_brake", "affected_by_handbrake")


def dump(obj, props, tag):
    for p in props:
        try:
            unreal.log(f"DIAGV {tag}.{p} = {obj.get_editor_property(p)}")
        except Exception:
            pass


assets = unreal.AssetRegistryHelpers.get_asset_registry().get_assets_by_path(
    "/Game/VehicleTemplate", recursive=True)
for ad in assets:
    if ad.asset_class_path.asset_name != "Blueprint":
        continue
    bp = ad.get_asset()
    cls = bp.generated_class()
    cdo = unreal.get_default_object(cls)
    if not isinstance(cdo, unreal.WheeledVehiclePawn):
        continue
    unreal.log(f"DIAGV ===== vehicle BP: {ad.package_name} =====")
    try:
        move = cdo.get_editor_property("vehicle_movement_component")
    except Exception as exc:
        unreal.log(f"DIAGV no movement component: {exc}")
        continue
    dump(move, PROPS_MOVEMENT, "movement")
    try:
        for i, ws in enumerate(move.get_editor_property("wheel_setups")):
            wclass = ws.get_editor_property("wheel_class")
            unreal.log(f"DIAGV wheel[{i}] class = {wclass.get_name()}")
            wcdo = unreal.get_default_object(wclass)
            dump(wcdo, PROPS_WHEEL, f"wheel[{i}]")
    except Exception as exc:
        unreal.log(f"DIAGV wheel dump failed: {exc}")
unreal.log("DIAGV done")
