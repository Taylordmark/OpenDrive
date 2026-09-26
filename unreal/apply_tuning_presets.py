"""Headless Arc 1.5: create tuning preset data assets + spawn the manager.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

TUNE_DIR = "/Game/OpenDrive/Tuning"
LEVEL = "/Game/TailOfTheDragon"
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"TUNE {msg}")


# Garage setups (2026-09-25). Values validated in scratchpad/esc_sim.py -
# change them there first. Unlisted fields keep the Street default (the
# ODTuningPreset C++ defaults). COM: Rally/Offroad sit LOWER than the old
# 48/50 because ride height + tire diameter already lift the body; the old
# values put the COM 10-12 cm up and brake-into-turn spun past what brakes-
# only ESC can catch.
H, L, O = (unreal.ODTractionMode.HIGH, unreal.ODTractionMode.LOW,
           unreal.ODTractionMode.OFF)
PRESETS = {
    "Street": dict(),
    "Rally": dict(
        damping_ratio=0.75, spring_rate_scale=0.75, ride_height_delta=4.0,
        front_friction_scale=0.95, rear_friction_scale=0.95, downforce=0.5,
        center_of_mass_z=36.0,
        on_road_grip=0.95, off_road_grip=0.90, slip_angle_add_deg=3.0,
        slide_frac_min=0.92, default_traction_mode=L, esc_slide_allow_low_deg=15.0,
        steer_lock_scale=1.1, handbrake_scale=1.4,
        tire_diameter_scale=1.04, front_tire_width_scale=0.9,
        rear_tire_width_scale=0.9),
    "Offroad": dict(
        damping_ratio=0.65, spring_rate_scale=0.60, ride_height_delta=8.0,
        front_friction_scale=0.90, rear_friction_scale=0.90, torque_scale=1.10,
        downforce=0.2, center_of_mass_z=38.0,
        on_road_grip=0.90, off_road_grip=1.00, slip_angle_add_deg=7.0,
        top_speed_cap_kph=145.0,
        tire_diameter_scale=1.12, front_tire_width_scale=1.2,
        rear_tire_width_scale=1.2),
    "Drift": dict(
        damping_ratio=0.85, spring_rate_scale=1.15, ride_height_delta=-1.0,
        front_friction_scale=1.05, rear_friction_scale=0.72, torque_scale=1.30,
        downforce=0.6, center_of_mass_z=40.0,
        off_road_grip=0.5, slide_frac_min=0.95, default_traction_mode=L,
        esc_slide_allow_low_deg=25.0, steer_lock_scale=1.3,
        rear_brake_scale=1.2, handbrake_scale=1.5,
        tire_diameter_scale=0.97, camber_deg=7.0, front_tire_width_scale=0.9),
    "Drag": dict(
        damping_ratio=0.95, spring_rate_scale=1.20, ride_height_delta=-1.0,
        rear_friction_scale=1.25, torque_scale=1.60, downforce=0.9,
        center_of_mass_z=38.0,
        off_road_grip=0.45, steer_lock_scale=0.85, rear_brake_scale=0.8,
        front_tire_width_scale=0.7, rear_tire_width_scale=1.45),
}
CDO = unreal.get_default_object(unreal.ODTuningPreset)
ALL_FIELDS = sorted({k for f in PRESETS.values() for k in f})

assets = []
for name, fields in PRESETS.items():
    path = f"{TUNE_DIR}/DA_Tuning_{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        a = unreal.load_asset(path)
    else:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.ODTuningPreset)
        a = tools.create_asset(f"DA_Tuning_{name}", TUNE_DIR,
                               unreal.ODTuningPreset, factory)
    a.set_editor_property("display_name", name)
    # reset every field to the C++ default first so a value dropped from
    # this table doesn't linger in an existing asset
    for prop in ALL_FIELDS:
        a.set_editor_property(prop, CDO.get_editor_property(prop))
    for prop, value in fields.items():
        a.set_editor_property(prop, value)
    unreal.EditorAssetLibrary.save_asset(path)
    assets.append(a)
    log(f"preset {name} saved")

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

mgr = None
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == "TuningManager":
        mgr = a
if mgr is None:
    mgr = actor_sub.spawn_actor_from_class(
        unreal.ODTuningManager, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    mgr.set_actor_label("TuningManager")
    mgr.set_folder_path("TailOfTheDragon")
    log("spawned TuningManager")
mgr.set_editor_property("presets", assets)
log(f"TuningManager presets set ({len(assets)})")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
