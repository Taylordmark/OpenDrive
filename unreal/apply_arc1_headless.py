"""Headless Arc 1 apply: physical materials, vehicle tuning, manager actors.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Physics"
ROAD_DIR = "/Game/OpenDrive/Road"
LEVEL = "/Game/TailOfTheDragon"
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"ARC1 {msg}")


# ---------------------------------------------------------------- phys mats
def make_pm(name, friction):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.load_asset(path)
    pm = tools.create_asset(name, MAT_DIR, unreal.PhysicalMaterial,
                            unreal.PhysicalMaterialFactoryNew())
    pm.set_editor_property("friction", friction)
    log(f"created {name}")
    return pm


pm_asphalt = make_pm("PM_Asphalt", 0.9)
pm_dirt = make_pm("PM_Dirt", 0.7)
make_pm("PM_Grass", 0.55)

n = 0
for ap in unreal.EditorAssetLibrary.list_assets(ROAD_DIR):
    a = unreal.load_asset(ap)
    if isinstance(a, unreal.StaticMesh):
        a.get_editor_property("body_setup").set_editor_property(
            "phys_material", pm_asphalt)
        n += 1
log(f"assigned PM_Asphalt to {n} road meshes")

# ---------------------------------------------------------------- tuning
# floaty-car fix (diag 2026-07-14: damping 0.5 underdamped, no COM override,
# downforce 0.3): well-damped suspension, lowered COM, real downforce
for wheel_bp in ("/Game/VehicleTemplate/Blueprints/SportsCar/SportsCar_WheelsFront",
                 "/Game/VehicleTemplate/Blueprints/SportsCar/SportsCar_WheelsRear"):
    bp = unreal.load_asset(wheel_bp)
    if not bp:
        log(f"WARNING wheel BP not found: {wheel_bp}")
        continue
    cdo = unreal.get_default_object(bp.generated_class())
    cdo.set_editor_property("suspension_damping_ratio", 0.9)
    cdo.set_editor_property("suspension_max_raise", 8.0)
    cdo.set_editor_property("suspension_max_drop", 12.0)
    unreal.EditorAssetLibrary.save_asset(wheel_bp)
    log(f"tuned + saved {wheel_bp.split('/')[-1]}")

car_bp_path = "/Game/VehicleTemplate/Blueprints/SportsCar/BP_VehicleAdvSportsCar"
car_bp = unreal.load_asset(car_bp_path)
if car_bp:
    cdo = unreal.get_default_object(car_bp.generated_class())
    move = cdo.get_editor_property("vehicle_movement_component")
    move.set_editor_property("enable_center_of_mass_override", True)
    move.set_editor_property("center_of_mass_override",
                             unreal.Vector(0.0, 0.0, 42.0))
    move.set_editor_property("downforce_coefficient", 1.1)
    unreal.EditorAssetLibrary.save_asset(car_bp_path)
    log("tuned + saved BP_VehicleAdvSportsCar (COM override, downforce 1.1)")
else:
    log("WARNING sports car BP not found")

# ---------------------------------------------------------------- level
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
labels = {a.get_actor_label() for a in actor_sub.get_all_level_actors()}

# landscape physmat
for a in actor_sub.get_all_level_actors():
    cls = a.get_class().get_name()
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        a.set_editor_property("default_phys_material", pm_dirt)
        log("landscape default phys material -> PM_Dirt")

# manager actors (idempotent by label)
for cls_name, label in (("ODSurfaceTractionManager", "TractionManager"),
                        ("ODForestCollisionManager", "ForestCollision")):
    if label in labels:
        continue
    cls = getattr(unreal, cls_name, None)
    if cls is None:
        log(f"WARNING C++ class {cls_name} not visible to python")
        continue
    a = actor_sub.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0),
                                         unreal.Rotator(0, 0, 0))
    a.set_actor_label(label)
    a.set_folder_path("TailOfTheDragon")
    log(f"spawned {label}")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
