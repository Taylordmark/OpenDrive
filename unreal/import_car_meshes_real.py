"""Headless: import the Realistic car meshes (pipeline/make_car_meshes_real.py,
data/cars/real/fbx/) as _real twins of the stylized ones:
SM_Car_<Car>_real{,_high,_ultra} and SM_CarWheel_real{,_high,_ultra} in
/Game/OpenDrive/Cars.

ADD-only (Content is a shared junction): no stylized mesh, material, texture
or data asset is written. The FBX goes into a staging folder first (so any
material the importer makes on its own can't land on /Game/OpenDrive/Cars/
body etc.), then each mesh is moved next to its stylized twin and the staging
folder is deleted.

Slots keep the stylized names (body/plastic/glass/chrome/tire/rim/lights) and
get the stylized materials (M_Car<Cat> on Standard, M_Car<Cat>_Hi on
_high/_ultra, from import_car_meshes.py) - AODVehiclePawn's
ODVisualStyle::PickSlots then swaps each for its M_Car*_real twin, exactly as
it does on the stylized meshes. UV0 = palette cell, UV1 = unique unwrap;
Nanite off (translucent glass), box collision on the chassis, none on the wheel.
M_CarLights is not touched, so apply_car_lights_material.py needs no re-run.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "data" / "cars" / "real" / "fbx"
CAR_DIR = "/Game/OpenDrive/Cars"
STAGE = "/Game/OpenDrive/Cars/_RealImportStaging"
EAL = unreal.EditorAssetLibrary
CHASSIS = ("Muscle", "JDM", "Super", "Pickup", "Rally", "Concept", "LMP")
BASES = [f"SM_Car_{c}_real" for c in CHASSIS] + ["SM_CarWheel_real"]
NAMES = [b + s for b in BASES for s in ("", "_high", "_ultra")]
CATS = ("body", "plastic", "glass", "chrome", "tire", "rim", "lights")


def log(msg):
    unreal.log_warning(f"CARMESH_REAL {msg}")


opts = unreal.FbxImportUI()
opts.set_editor_property("import_mesh", True)
opts.set_editor_property("import_as_skeletal", False)
opts.set_editor_property("import_materials", False)
opts.set_editor_property("import_textures", False)
opts.set_editor_property("import_animations", False)
opts.set_editor_property("mesh_type_to_import", unreal.FBXImportType.FBXIT_STATIC_MESH)
sm = opts.get_editor_property("static_mesh_import_data")
sm.set_editor_property("combine_meshes", True)
sm.set_editor_property("auto_generate_collision", False)
sm.set_editor_property("generate_lightmap_u_vs", False)     # UV1 is already a unique unwrap
sm.set_editor_property("normal_import_method", unreal.FBXNormalImportMethod.FBXNIM_IMPORT_NORMALS)
sm.set_editor_property("convert_scene", True)
sm.set_editor_property("convert_scene_unit", False)

tasks = []
for n in NAMES:
    t = unreal.AssetImportTask()
    t.filename = str(SRC / f"{n}.fbx")
    t.destination_path = STAGE
    t.destination_name = n
    t.automated = True
    t.save = False
    t.replace_existing = True
    t.options = opts
    tasks.append(t)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

mats = {hi: {c: unreal.load_asset(f"{CAR_DIR}/M_Car{c.capitalize()}{'_Hi' if hi else ''}") for c in CATS}
        for hi in (False, True)}
missing = [c for hi in mats for c, m in mats[hi].items() if m is None]
if missing:
    raise RuntimeError(f"stylized car materials missing {missing} - run import_car_meshes.py first")

report = []
for n in NAMES:
    mesh = unreal.load_asset(f"{STAGE}/{n}")
    if mesh is None:
        raise RuntimeError(f"{n}: import failed - run make_car_meshes_real.py first")
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", False)
    mesh.set_editor_property("nanite_settings", ns)
    slots = [str(s.get_editor_property("material_slot_name")) for s in mesh.get_editor_property("static_materials")]
    bad = [s for s in slots if s not in CATS]
    if bad:
        raise RuntimeError(f"{n}: unexpected slot names {bad} (want {CATS})")
    hi = n.endswith(("_high", "_ultra"))
    for i, s in enumerate(slots):
        mesh.set_material(i, mats[hi][s])
    if not n.startswith("SM_CarWheel"):
        unreal.EditorStaticMeshLibrary.remove_collisions(mesh)
        unreal.EditorStaticMeshLibrary.add_simple_collisions(mesh, unreal.ScriptingCollisionShapeType.BOX)
    b = mesh.get_bounding_box()
    report.append(f"{n}: {mesh.get_num_triangles(0)} tris, {len(slots)} slots {slots}, bbox "
                  f"({b.min.x:.0f},{b.min.y:.0f},{b.min.z:.0f})..({b.max.x:.0f},{b.max.y:.0f},{b.max.z:.0f})")
    dst = f"{CAR_DIR}/{n}"
    if EAL.does_asset_exist(dst):
        EAL.delete_asset(dst)                 # our own earlier import; nothing references it by asset
    if not EAL.rename_asset(f"{STAGE}/{n}", dst):
        raise RuntimeError(f"{n}: move to {dst} failed")
    EAL.save_asset(dst, only_if_is_dirty=False)

EAL.delete_directory(STAGE)
for line in report:
    log(line)
log(f"DONE - {len(NAMES)} _real car meshes in {CAR_DIR}")
