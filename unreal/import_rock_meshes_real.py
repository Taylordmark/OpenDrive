"""PLAN-VISUALS Track D: Realistic twins of the 12 boulder meshes, so
ODForestManager's Upgrade(Pick(SM_RockBoulderX)) lands on them:

  SM_RockBoulderX_real        Standard tier: the stylized OBJ as-is, Nanite
                              OFF -> no displacement (Model Detail Standard
                              stays as cheap as Stylized)
  SM_RockBoulderX_real_high   smooth-normal High/Ultra geometry
  SM_RockBoulderX_real_ultra  (pipeline/make_rock_meshes.py REAL_TIERS),
                              Nanite ON -> M_Rock030_real's tessellated
                              displacement applies

Pick() runs before Upgrade(), so the Standard _real twin must exist for the
High/Ultra ones to be found. Smooth normals because Nanite displaces along
the vertex normal: the stylized flat-shaded split vertices would crack.
The stylized SM_RockBoulder* assets are never touched.

Run pipeline/make_rock_meshes.py, then apply_terrain_real_materials.py
(M_Rock030_real must exist), then this:
UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "data" / "flora" / "obj"
ROCK_DIR = "/Game/OpenDrive/Rocks"
MAT = "/Game/OpenDrive/Materials/M_Rock030_real"
# mesh name -> (obj, nanite)
TWINS = {}
for m, o in {"A": "a", "B": "b", "C": "c", "D": "d"}.items():
    TWINS[f"SM_RockBoulder{m}_real"] = (f"rock_boulder_{o}.obj", False)
    for sfx in ("_high", "_ultra"):
        TWINS[f"SM_RockBoulder{m}_real{sfx}"] = (f"rock_boulder_{o}_real{sfx}.obj", True)


def log(msg):
    unreal.log_warning(f"ROCKREAL {msg}")


mat = unreal.load_asset(MAT)
if mat is None or not mat.get_editor_property("used_with_nanite"):
    raise RuntimeError(f"{MAT} missing or not Nanite-flagged - run apply_terrain_real_materials.py first")

tasks = []
for name, (obj, _) in TWINS.items():
    path = SRC / obj
    if not path.exists():
        raise RuntimeError(f"{path} missing - run pipeline/make_rock_meshes.py first")
    t = unreal.AssetImportTask()
    t.filename = str(path)
    t.destination_path = ROCK_DIR
    t.destination_name = name
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

for name, (_, nanite) in TWINS.items():
    path = f"{ROCK_DIR}/{name}"
    mesh = unreal.load_asset(path)
    if mesh is None:
        raise RuntimeError(f"import failed: {path}")
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", nanite)
    mesh.set_editor_property("nanite_settings", ns)
    mesh.set_material(0, mat)
    if not unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {path}")
    log(f"{path} nanite={mesh.get_editor_property('nanite_settings').enabled} "
        f"tris={mesh.get_num_triangles(0)} "
        f"mat={mesh.get_material(0).get_path_name()}")
log(f"DONE {len(TWINS)} twins")
