"""Headless: import the roadside furniture meshes (pipeline/make_furniture_
meshes.py: data/furniture/obj/SM_Furn_*.obj) into /Game/OpenDrive/Furniture/,
build their flat-colour materials, and assign them by slot name.

  M_Furniture            parent: "Color" vector -> BaseColor, Color * "Glow"
                         scalar -> Emissive; two-sided, and
                         used_with_instanced_static_meshes (AODFurnitureManager
                         draws every prop through HISMs - without the flag the
                         material silently renders engine grey, §7.15)
  MI_Furn_<slot>         one instance per OBJ usemtl colour (gray, white, red,
                         yellow, black, green, lamp). AODFurnitureManager makes
                         a dynamic instance of MI_Furn_lamp and raises its Glow
                         with the headlights.

Nanite is disabled on every mesh (tiny low-poly, HISM-placed - same reasoning
as import_rock_meshes.py). Slots are matched by material_slot_name, falling
back to OBJ encounter order (import_car_meshes.py's same fallback).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "data" / "furniture" / "obj"
FURN_DIR = "/Game/OpenDrive/Furniture"
MAT_DIR = "/Game/OpenDrive/Materials"
COLORS = {"gray": (0.45, 0.46, 0.48), "white": (0.9, 0.9, 0.9), "red": (0.7, 0.05, 0.05),
          "yellow": (0.95, 0.75, 0.05), "black": (0.03, 0.03, 0.03), "green": (0.05, 0.6, 0.2),
          "lamp": (1.0, 0.85, 0.6)}

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"FURNITURE {msg}")


# ------------------------------------------------------------- materials
parent_path = f"{MAT_DIR}/M_Furniture"
if unreal.EditorAssetLibrary.does_asset_exist(parent_path):
    parent = unreal.load_asset(parent_path)
    for _e in list(MEL.get_material_expressions(parent)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
        MEL.delete_material_expression(parent, _e)
else:
    parent = tools.create_asset("M_Furniture", MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
parent.set_editor_property("two_sided", True)
parent.set_editor_property("used_with_instanced_static_meshes", True)
color = MEL.create_material_expression(parent, unreal.MaterialExpressionVectorParameter, -600, -100)
color.set_editor_property("parameter_name", "Color")
color.set_editor_property("default_value", unreal.LinearColor(0.5, 0.5, 0.5, 1.0))
glow = MEL.create_material_expression(parent, unreal.MaterialExpressionScalarParameter, -600, 150)
glow.set_editor_property("parameter_name", "Glow")
glow.set_editor_property("default_value", 0.0)
mul = MEL.create_material_expression(parent, unreal.MaterialExpressionMultiply, -300, 150)
rough = MEL.create_material_expression(parent, unreal.MaterialExpressionConstant, -300, 50)
rough.set_editor_property("r", 0.6)
ok = (MEL.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)
      and MEL.connect_material_expressions(color, "", mul, "A")
      and MEL.connect_material_expressions(glow, "", mul, "B")
      and MEL.connect_material_property(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
      and MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS))
if not ok:
    raise RuntimeError("M_Furniture: could not connect Color/Glow/Roughness - pin name mismatch?")
MEL.recompile_material(parent)
unreal.EditorAssetLibrary.save_asset(parent_path, only_if_is_dirty=False)
if not unreal.load_asset(parent_path).get_editor_property("used_with_instanced_static_meshes"):
    raise RuntimeError("M_Furniture: used_with_instanced_static_meshes FALSE after save")

mats = {}
for slot, rgb in COLORS.items():
    mi_path = f"{MAT_DIR}/MI_Furn_{slot}"
    mi = (unreal.load_asset(mi_path) if unreal.EditorAssetLibrary.does_asset_exist(mi_path) else
          tools.create_asset(f"MI_Furn_{slot}", MAT_DIR, unreal.MaterialInstanceConstant,
                             unreal.MaterialInstanceConstantFactoryNew()))
    MEL.set_material_instance_parent(mi, parent)
    MEL.set_material_instance_vector_parameter_value(mi, "Color", unreal.LinearColor(*rgb, 1.0))
    unreal.EditorAssetLibrary.save_asset(mi_path, only_if_is_dirty=False)
    mats[slot] = mi
log(f"built M_Furniture + {len(mats)} colour instances")

# ------------------------------------------------------------- meshes
objs = sorted(SRC.glob("SM_Furn_*.obj"))
if not objs:
    raise RuntimeError(f"no SM_Furn_*.obj in {SRC} - run pipeline/make_furniture_meshes.py first")
tasks = []
for obj in objs:
    t = unreal.AssetImportTask()
    t.filename = str(obj)
    t.destination_path = FURN_DIR
    t.destination_name = obj.stem
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)
tools.import_asset_tasks(tasks)

for obj in objs:
    mesh = unreal.load_asset(f"{FURN_DIR}/{obj.stem}")
    if mesh is None:
        raise RuntimeError(f"{obj.stem}: import failed")
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", False)
    mesh.set_editor_property("nanite_settings", ns)
    order = [l.split()[1] for l in obj.read_text().splitlines() if l.startswith("usemtl ")]
    slots = [str(s.get_editor_property("material_slot_name"))
             for s in mesh.get_editor_property("static_materials")]
    names = slots if slots and all(n in mats for n in slots) else order[:len(slots)]
    for i, n in enumerate(names):
        mesh.set_material(i, mats[n])
    log(f"{obj.stem}: {len(names)} slot(s) {names}{'' if names == slots else ' (by encounter order)'}")

unreal.EditorAssetLibrary.save_directory(FURN_DIR, only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log(f"DONE - {len(objs)} furniture meshes in {FURN_DIR}")
