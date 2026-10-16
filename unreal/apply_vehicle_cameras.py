"""Write the per-car camera mounts (cockpit eye, steering wheel, hood, bumper)
into the seven DA_Vehicle_* defs. Values are pipeline/car_camera_mounts.py's
output (UE car-local cm) - re-run that and paste here if the car presets move.
A def left at zero falls back to AODVehiclePawn::ResolveCameraMounts' bounds
guess, so this is optional but makes the cockpit sit in the actual seat.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout

import_car_meshes.py recreates the defs and runs this at its end.
"""

import unreal

CARS = {
    "Muscle": dict(eye=(-34, -42, 77), wheel=(18, -42, 61), hood=(72, 0, 72), bumper=(244, 0, 13)),
    "JDM": dict(eye=(-12, -38, 68), wheel=(40, -38, 53), hood=(68, 0, 64), bumper=(222, 0, 7)),
    "Super": dict(eye=(-56, -42, 54), wheel=(-4, -42, 44), hood=(74, 0, 56), bumper=(220, 0, -6)),
    "Pickup": dict(eye=(46, -48, 124), wheel=(98, -48, 95), hood=(142, 0, 107), bumper=(276, 0, 35)),
    "Rally": dict(eye=(16, -37, 85), wheel=(50, -37, 61), hood=(82, 0, 72), bumper=(198, 0, 13)),
    "Concept": dict(eye=(-47, -18, 55), wheel=(5, -18, 43), hood=(98, 0, 36), bumper=(184, 0, -5)),
    "LMP": dict(eye=(-48, -13, 43), wheel=(4, -13, 24), hood=(112, 0, 27), bumper=(244, 0, -11)),
}
FIELDS = dict(eye="cockpit_eye_cm", wheel="steering_wheel_cm", hood="hood_cam_cm", bumper="bumper_cam_cm")

for name, m in CARS.items():
    path = f"/Game/OpenDrive/Cars/DA_Vehicle_{name}"
    a = unreal.load_asset(path)
    if a is None:
        unreal.log_warning(f"CARCAM {path} missing - skipped")
        continue
    for k, prop in FIELDS.items():
        a.set_editor_property(prop, unreal.Vector(*m[k]))
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
    unreal.log(f"CARCAM {name}: {m}")
