"""Headless: import drop-in replacement meshes from data/mods/<Cars|Trees|Rocks>/
over the stock asset of the same name (file stem = stock asset name, e.g.
data/mods/Rocks/SM_RockBoulderA.glb). The game loads meshes by fixed asset
path, so overwriting the asset in place swaps it everywhere with no C++
change. See ASSET-MODDING.md.

Every Asset Detail tier that exists (<name>, <name>_high, <name>_ultra) gets
the same mod mesh - otherwise ODAssetDetail::Upgrade would keep showing the
stock _high/_ultra variant. Run AFTER import_cars.bat / import_rock_meshes.py
/ import_biome_flora.py: those re-import the stock meshes over your mods.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

MODS = Path(__file__).resolve().parents[1] / "data" / "mods"
FOLDERS = {"Cars": "/Game/OpenDrive/Cars",
           "Trees": "/Game/OpenDrive/Trees",
           "Rocks": "/Game/OpenDrive/Rocks"}
EXTS = {".glb", ".gltf", ".fbx", ".obj"}
TIERS = ("", "_high", "_ultra")
EAL = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"MODMESH {msg}")   # warning level: shows in the .bat window


def size(mesh):
    b = mesh.get_bounding_box()
    return [round(hi - lo) for lo, hi in ((b.min.x, b.max.x), (b.min.y, b.max.y), (b.min.z, b.max.z))]


done, failed = [], []
for folder, ue_dir in FOLDERS.items():
    for src in sorted((MODS / folder).glob("*")):
        if src.suffix.lower() not in EXTS:
            continue
        name = src.stem
        if not EAL.does_asset_exist(f"{ue_dir}/{name}"):
            stock = sorted(a.split(".")[-1] for a in EAL.list_assets(ue_dir, recursive=False)
                           if isinstance(unreal.load_asset(a), unreal.StaticMesh)
                           and not a.endswith(("_high", "_ultra")))
            log(f"SKIP {src.name}: no stock mesh {ue_dir}/{name} - name it one of {stock}")
            failed.append(src.name)
            continue
        old = size(unreal.load_asset(f"{ue_dir}/{name}"))
        targets = [name + t for t in TIERS if EAL.does_asset_exist(f"{ue_dir}/{name}{t}")]
        tasks = []
        for target in targets:
            t = unreal.AssetImportTask()
            t.filename = str(src)
            t.destination_path = ue_dir
            t.destination_name = target
            t.automated = True
            t.save = False
            t.replace_existing = True
            tasks.append(t)
        tools.import_asset_tasks(tasks)

        for target in targets:
            mesh = unreal.load_asset(f"{ue_dir}/{target}")
            if not isinstance(mesh, unreal.StaticMesh):
                raise RuntimeError(
                    f"{src.name} -> {target} did not come in as one static mesh - "
                    "join everything into a single object in Blender (Ctrl+J) and re-export")
            # off for everything: car glass is translucent, and trees/rocks
            # are HISM-instanced (see import_rock_meshes.py)
            ns = mesh.get_editor_property("nanite_settings")
            ns.set_editor_property("enabled", False)
            mesh.set_editor_property("nanite_settings", ns)
            if folder == "Cars":   # same box collision as import_car_meshes.py
                unreal.EditorStaticMeshLibrary.remove_collisions(mesh)
                unreal.EditorStaticMeshLibrary.add_simple_collisions(
                    mesh, unreal.ScriptingCollisionShapeType.BOX)
        new = size(unreal.load_asset(f"{ue_dir}/{name}"))
        log(f"{src.name} -> {targets}  size cm (X,Y,Z) stock {old} -> mod {new}")
        if any(n > 2 * o or n < o / 2 for n, o in zip(new, old) if o > 0):
            log(f"WARNING {src.name}: size is way off the stock mesh - wrong scale "
                "or rotated (cars must face +X; see ASSET-MODDING.md)")
        done.append(src.name)
    EAL.save_directory(ue_dir, only_if_is_dirty=False)

log(f"DONE - {len(done)} imported {done}, {len(failed)} skipped {failed}")
