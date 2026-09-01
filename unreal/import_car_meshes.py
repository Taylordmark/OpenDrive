"""Headless: import the four procedural car meshes (pipeline/make_car_meshes.py)
plus the shared wheel mesh into /Game/OpenDrive/Cars/, build one shared
material off the shared palette texture, and author four new UODVehicleDef
data assets (Sedan, Coupe S, Sport, Proto) - same make_def() pattern as
unreal/apply_vehicle_defs.py used for the GT car.

Fully ADDITIVE (2026-08-27): DA_Vehicle_Coupe / DA_Vehicle_GT and their
meshes in /Game/OpenDrive/Car (singular - the older folder) are untouched.
This writes into /Game/OpenDrive/Cars (plural) instead, specifically so nothing
here can collide with or overwrite the existing placeholder/GT assets. The
existing "Coupe" stays the alphabetically-first spawn car for this pass; a
later pass will retarget/replace it once these four have been drive-tested
(see task history - not done here).

Run make_car_meshes.py first (writes data/cars/procedural/*), then:

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "data" / "cars" / "procedural"
CAR_DIR = "/Game/OpenDrive/Cars"
tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"CARMESH {msg}")


# ------------------------------------------------------------------ import
MESH_NAMES = ("SM_Car_Sedan", "SM_Car_Coupe", "SM_Car_Sport", "SM_Car_Proto", "SM_CarWheel")
tasks = []
for name in MESH_NAMES:
    t = unreal.AssetImportTask()
    t.filename = str(SRC / "obj" / f"{name}.obj")
    t.destination_path = CAR_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)

tex_task = unreal.AssetImportTask()
tex_task.filename = str(SRC / "textures" / "T_CarPalette2.png")
tex_task.destination_path = CAR_DIR
tex_task.automated = True
tex_task.save = False
tex_task.replace_existing = True
tasks.append(tex_task)

tools.import_asset_tasks(tasks)

meshes = {n: unreal.load_asset(f"{CAR_DIR}/{n}") for n in MESH_NAMES}
palette = unreal.load_asset(f"{CAR_DIR}/T_CarPalette2")
missing = [n for n, a in meshes.items() if a is None]
if missing or palette is None:
    raise RuntimeError(
        f"car asset import failed - missing meshes {missing}, "
        f"palette {'ok' if palette else 'MISSING'} - run make_car_meshes.py first")
palette.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
palette.set_editor_property(
    "mip_gen_settings", unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
log(f"imported {len(meshes)} meshes + T_CarPalette2")

# box collision on all four chassis (SM_CarWheel has no collision - matches
# WheelVisuals' NoCollision setup in AODVehiclePawn's constructor)
for name in ("SM_Car_Sedan", "SM_Car_Coupe", "SM_Car_Sport", "SM_Car_Proto"):
    mesh = meshes[name]
    unreal.EditorStaticMeshLibrary.remove_collisions(mesh)
    n_col = unreal.EditorStaticMeshLibrary.add_simple_collisions(
        mesh, unreal.ScriptingCollisionShapeType.BOX)
    log(f"{name}: box collision ({n_col} prim)")

# ---------------------------------------------------------------- M_Car2
# Rebuild the expression graph in place on the existing asset instead of
# delete/recreate: delete_asset can silently fail while 5 meshes still
# reference the material (same failure mode as make_def below), and in-place
# rebuild is idempotent either way. (Note: MEL.get_used_textures reports []
# even on a correctly-bound material in a commandlet - do not use it to
# "verify" this material; it produced a false broken-texture diagnosis on
# 2026-09-01.)
mat_path = f"{CAR_DIR}/M_Car2"
m_car2 = None
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    m_car2 = unreal.load_asset(mat_path)
    if m_car2 is not None:
        MEL.delete_all_material_expressions(m_car2)
if m_car2 is None:
    m_car2 = tools.create_asset("M_Car2", CAR_DIR, unreal.Material,
                                unreal.MaterialFactoryNew())
if m_car2 is None:
    raise RuntimeError(f"could not create or load {mat_path}")
ts = MEL.create_material_expression(
    m_car2, unreal.MaterialExpressionTextureSample, -500, 0)
ts.set_editor_property("texture", palette)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
rough = MEL.create_material_expression(
    m_car2, unreal.MaterialExpressionConstant, -500, 300)
rough.set_editor_property("r", 0.40)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
m_car2.set_editor_property("used_with_nanite", True)
# the procedural OBJ writer's y-negation mirrors the mesh, so roughly half
# the hull faces end up wound inward - the car material MUST be two-sided or
# the body renders "not enclosed" (seen live 2026-09-01: flat car with only
# the double-wound decal rectangles visible). Same convention as M_Car/M_Car_GT.
m_car2.set_editor_property("two_sided", True)
MEL.recompile_material(m_car2)
for mesh in meshes.values():
    mesh.set_material(0, m_car2)
# explicit save here (not just the trailing save_directory/save_dirty_packages
# below) - observed once in this environment that a freshly-created Material
# package didn't make it into that blanket save if anything later in the
# script raised (e.g. a stale-asset delete_asset failure in make_def() on a
# re-run) before reaching it; cheap insurance since this call is idempotent.
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
log("built M_Car2 and assigned to all 5 meshes")

# ------------------------------------------------------ the four vehicle defs
wheel_mesh = meshes["SM_CarWheel"]


def make_def(name, props):
    path = f"{CAR_DIR}/{name}"
    a = None
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            # delete_asset can silently fail to remove an asset still
            # considered referenced (seen once in this environment on a
            # same-session re-run) - rather than let the next create_asset
            # return None and crash on set_editor_property, just update the
            # existing asset's properties in place. Re-running this script
            # is then idempotent either way.
            log(f"{name}: delete_asset did not remove it - updating in place instead")
            a = unreal.load_asset(path)
    if a is None:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.ODVehicleDef)
        a = tools.create_asset(name, CAR_DIR, unreal.ODVehicleDef, factory)
    if a is None:
        raise RuntimeError(f"could not create or load {path}")
    a.set_editor_property("wheel_mesh", wheel_mesh)
    for k, v in props.items():
        a.set_editor_property(k, v)
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
    log(f"wrote {name} ('{props.get('display_name')}')")


# geometry/mass straight from make_car_meshes.py's printed per-car dicts -
# visual-only pass: everything else (engine/tire/suspension/aero) stays at
# UODVehicleDef's defaults, i.e. today's baseline coupe physics envelope,
# per the "do not touch tuned physics" constraint on this task. Axle
# geometry + WheelRadiusCm DO need to match each mesh's own footprint (that
# is geometry, not tuning) so UODVehicleMovement's wheel table lines up with
# where the visual wheels actually are.
make_def("DA_Vehicle_Sedan", {
    "display_name": "Sedan",
    "chassis_mesh": meshes["SM_Car_Sedan"],
    "front_axle_x_cm": 150.0, "rear_axle_x_cm": -142.0,
    "half_track_y_cm": 86.0, "wheel_radius_cm": 34.0,
    "mass_kg": 1550.0,
})
make_def("DA_Vehicle_CoupeS", {
    "display_name": "Coupe S",
    "chassis_mesh": meshes["SM_Car_Coupe"],
    "front_axle_x_cm": 128.0, "rear_axle_x_cm": -118.0,
    "half_track_y_cm": 82.0, "wheel_radius_cm": 33.0,
    "mass_kg": 1420.0,
})
make_def("DA_Vehicle_Sport", {
    "display_name": "Sport",
    "chassis_mesh": meshes["SM_Car_Sport"],
    "front_axle_x_cm": 145.0, "rear_axle_x_cm": -140.0,
    "half_track_y_cm": 92.0, "wheel_radius_cm": 35.0,
    "mass_kg": 1500.0,
})
make_def("DA_Vehicle_Proto", {
    "display_name": "Proto",
    "chassis_mesh": meshes["SM_Car_Proto"],
    "front_axle_x_cm": 115.0, "rear_axle_x_cm": -155.0,
    "half_track_y_cm": 90.0, "wheel_radius_cm": 30.0,
    "mass_kg": 1280.0,
})

unreal.EditorAssetLibrary.save_directory(CAR_DIR, only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved 5 meshes, M_Car2, and 4 new vehicle defs")
