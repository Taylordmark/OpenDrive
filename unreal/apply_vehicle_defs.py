"""Garage groundwork (2026-07-24): import the GT car meshes and author the two
UODVehicleDef data assets the pawn discovers at runtime (asset registry) and
cycles with V. The base Coupe def points at the already-imported placeholder
meshes with all-default stats (= today's car exactly); the GT def points at
the new orange meshes with a faster/grippier AWD envelope.

Run make_car_gt.py first (writes data/cars/gt/*), then:

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout

No level edit: AODVehiclePawn finds these defs via the asset registry, so a
PIE run picks them up with no re-regenerate.
"""

from pathlib import Path

import unreal

GT_SRC = Path(__file__).resolve().parents[1] / "data" / "cars" / "gt"
CAR_DIR = "/Game/OpenDrive/Car"
tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"VEHDEFS {msg}")


# ---------------------------------------------------------------- import GT
tasks = []
for fname in ("chassis_gt.obj", "wheel_gt.obj", "T_CarPalette_GT.png"):
    t = unreal.AssetImportTask()
    t.filename = str(GT_SRC / fname)
    t.destination_path = CAR_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)
tools.import_asset_tasks(tasks)

gt_chassis = unreal.load_asset(f"{CAR_DIR}/chassis_gt")
gt_wheel = unreal.load_asset(f"{CAR_DIR}/wheel_gt")
gt_palette = unreal.load_asset(f"{CAR_DIR}/T_CarPalette_GT")
if not gt_chassis or not gt_wheel or not gt_palette:
    raise RuntimeError("GT car asset import failed - run make_car_gt.py first")
gt_palette.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
gt_palette.set_editor_property(
    "mip_gen_settings", unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)

unreal.EditorStaticMeshLibrary.remove_collisions(gt_chassis)
n_col = unreal.EditorStaticMeshLibrary.add_simple_collisions(
    gt_chassis, unreal.ScriptingCollisionShapeType.BOX)
log(f"imported GT meshes; chassis box collision ({n_col} prim)")

# ---------------------------------------------------------------- M_Car_GT
mat_path = f"{CAR_DIR}/M_Car_GT"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    unreal.EditorAssetLibrary.delete_asset(mat_path)
m_gt = tools.create_asset("M_Car_GT", CAR_DIR, unreal.Material,
                          unreal.MaterialFactoryNew())
ts = MEL.create_material_expression(
    m_gt, unreal.MaterialExpressionTextureSample, -500, 0)
ts.set_editor_property("texture", gt_palette)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
rough = MEL.create_material_expression(
    m_gt, unreal.MaterialExpressionConstant, -500, 300)
rough.set_editor_property("r", 0.35)   # a touch glossier than the coupe (0.42)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
m_gt.set_editor_property("used_with_nanite", True)
MEL.recompile_material(m_gt)
gt_chassis.set_material(0, m_gt)
gt_wheel.set_material(0, m_gt)
log("built M_Car_GT and assigned")

# ---------------------------------------------------- the two vehicle defs
coupe_chassis = unreal.load_asset(f"{CAR_DIR}/chassis")
coupe_wheel = unreal.load_asset(f"{CAR_DIR}/wheel")
if not coupe_chassis or not coupe_wheel:
    raise RuntimeError("coupe meshes missing - run apply_vehicle_pawn.py first")


def make_def(name, display, chassis, wheel, props):
    path = f"{CAR_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    factory = unreal.DataAssetFactory()
    factory.set_editor_property("data_asset_class", unreal.ODVehicleDef)
    a = tools.create_asset(name, CAR_DIR, unreal.ODVehicleDef, factory)
    a.set_editor_property("display_name", display)
    a.set_editor_property("chassis_mesh", chassis)
    a.set_editor_property("wheel_mesh", wheel)
    for k, v in props.items():
        a.set_editor_property(k, v)
    unreal.EditorAssetLibrary.save_asset(path)
    log(f"wrote {name} ('{display}')")


# Coupe: defaults are the baseline front-engine coupe; split/DI spelled out
# (= UODVehicleDef defaults) since the COM now sits at 53% front, not mid-axle.
make_def("DA_Vehicle_Coupe", "Coupe", coupe_chassis, coupe_wheel, {
    "front_weight_frac": 0.53,
    "yaw_dynamic_index": 1.0,
})

# GT: faster (lower drag, taller final, more power/rpm, lighter) AND grippier
# (AWD, higher mu, much more downforce, lower COM, stiffer springs).
make_def("DA_Vehicle_GT", "GT", gt_chassis, gt_wheel, {
    "drive_layout": unreal.ODDriveLayout.AWD,
    "mass_kg": 1350.0,
    "peak_torque_nm": 520.0,
    "peak_torque_rpm": 5200.0,
    "max_rpm": 7800.0,
    "limiter_rpm": 7650.0,
    "final_ratio": 2.50,
    "mu_peak": 1.32,
    "slide_frac": 0.80,
    "peak_slip_ratio": 0.13,
    "peak_slip_angle_deg": 8.0,
    "spring_rate": 72000.0,
    "damping_ratio": 0.95,
    "com_local_z": 6.0,
    "drag_coeff": 0.32,
    "downforce_coeff": 2.40,
    "max_steer_angle_deg": 36.0,
    "front_brake_nm": 5200.0,
    "rear_brake_nm": 3600.0,
    "front_weight_frac": 0.52,     # front-engine GT, slightly less nose than the coupe
    "yaw_dynamic_index": 0.95,
})

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive/Car", only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved GT meshes + both vehicle defs")
