"""Headless: import the 4 procedural boulder meshes
(pipeline/make_rock_meshes.py: rock_boulder_{a,b,c,d}.obj) into
/Game/OpenDrive/Rocks/ as SM_RockBoulder{A,B,C,D}, disable Nanite (these are
tiny low-poly meshes with zero need for it - same reasoning as
import_car_meshes.py), and assign M_Rock030 (unreal/apply_rock_material.py -
run that FIRST if it hasn't been run yet, this script requires it to already
exist) to each mesh's rock material slot.

Replaces AODForestManager's old RockMeshes source (two /Engine/BasicShapes
primitives, Cube/Sphere - see ODForestManager.h/.cpp) - the reviewer-flagged
"literal cube boulders" defect (next steps.md sec 7.15). No collision is
added (matches the old rocks' deliberate no-collision behavior - see
ODForestManager.cpp BuildTile()'s own comment on why).

Run pipeline/make_rock_meshes.py first (writes data/flora/obj/rock_boulder_*.obj),
and unreal/apply_rock_material.py at some point before or after (order
doesn't matter - this script just assigns whatever M_Rock030 currently
resolves to; re-run this script after re-running apply_rock_material.py if
the material changes and instances need the updated reference, though
set_material() by asset reference already picks up any in-place edit
automatically without needing a re-import).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "data" / "flora" / "obj"
ROCK_DIR = "/Game/OpenDrive/Rocks"
tools = unreal.AssetToolsHelpers.get_asset_tools()

VARIANTS = {
    "SM_RockBoulderA": "rock_boulder_a.obj",
    "SM_RockBoulderB": "rock_boulder_b.obj",
    "SM_RockBoulderC": "rock_boulder_c.obj",
    "SM_RockBoulderD": "rock_boulder_d.obj",
}
# Graphics menu "Asset Detail" variants (make_rock_meshes.py TIERS):
# SM_RockBoulderA_high <- rock_boulder_a_high.obj etc.
VARIANTS = {m + sfx: o.replace(".obj", sfx + ".obj")
            for sfx in ("", "_high", "_ultra") for m, o in VARIANTS.items()}


def log(msg):
    unreal.log(f"ROCKMESH {msg}")


tasks = []
for mesh_name, obj_name in VARIANTS.items():
    obj_path = SRC / obj_name
    if not obj_path.exists():
        raise RuntimeError(
            f"{obj_path} missing - run pipeline/make_rock_meshes.py first")
    t = unreal.AssetImportTask()
    t.filename = str(obj_path)
    t.destination_path = ROCK_DIR
    # Interchange otherwise names the asset after the source OBJ filename
    # (rock_boulder_a, not SM_RockBoulderA) - explicit destination_name
    # pins it to the name this script (and ODForestManager.cpp's
    # LoadObject paths) actually expect.
    t.destination_name = mesh_name
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)

tools.import_asset_tasks(tasks)

meshes = {n: unreal.load_asset(f"{ROCK_DIR}/{n}") for n in VARIANTS}
missing = [n for n, a in meshes.items() if a is None]
if missing:
    raise RuntimeError(f"rock mesh import failed - missing {missing}")
log(f"imported {len(meshes)} boulder meshes")

# Nanite is auto-enabled on import by default in UE 5.8 - these are tiny
# (20-80 tri) stylized low-poly meshes with no benefit from it, and
# AODForestManager places them via UHierarchicalInstancedStaticMeshComponent
# (see used_with_instanced_static_meshes on M_Rock030, apply_rock_material.py)
# which is a separate, already-handled gotcha from Nanite - this one is the
# "used_with_instanced_static_meshes" gotcha's sibling for meshes specifically,
# same class of "silently wrong at runtime, no import-time error" pitfall
# flagged repeatedly across this project (see next steps.md sec 7.15).
for name, mesh in meshes.items():
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", False)
    mesh.set_editor_property("nanite_settings", ns)
log(f"disabled Nanite on all {len(meshes)} boulder meshes")

rock_mat = unreal.load_asset("/Game/OpenDrive/Materials/M_Rock030")
if rock_mat is None:
    log("WARNING: M_Rock030 not found - run unreal/apply_rock_material.py "
        "(AODForestManager.cpp's RockMat LoadObject will just no-op and the "
        "engine default grey material renders instead, same fallback as before)")
else:
    for name, mesh in meshes.items():
        try:
            mesh.set_material(0, rock_mat)
        except Exception as exc:  # pragma: no cover - defensive
            log(f"{name}: could not set material slot 0 ({exc})")
    log(f"assigned M_Rock030 to slot 0 of all {len(meshes)} boulder meshes")

unreal.EditorAssetLibrary.save_directory(ROCK_DIR, only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log(f"DONE - saved {len(meshes)} boulder meshes to " + ROCK_DIR)
