"""Import the CC0 road PBR texture sets (content_src/textures/road/,
content_src/textures/road/download_road_textures.py + manifest.json) as UE
Texture2D assets under /Game/OpenDrive/Textures/Road/<Set>/ - Phase 2a
road-visual overhaul (worn asphalt + real lane markings, The Crew 2
art-direction target). unreal/apply_road_material.py (run after this) builds
the actual M_Road material graph against these.

Same AssetImportTask pattern unreal/import_ground_textures.py already
established for the ground overhaul, adapted for this set's JPG source files
(ambientCG "2K-JPG" download, not "2K-PNG") and its slightly different map
set - Asphalt032/Concrete034 ship no AmbientOcclusion map (manifest.json
documents this per-asset), so AO import is best-effort/optional per set, not
required like Color/NormalGL/Roughness.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

SRC_DIR = Path(__file__).resolve().parents[1] / "content_src" / "textures" / "road"
MANIFEST = json.loads((SRC_DIR / "manifest.json").read_text())
TEX_DIR = "/Game/OpenDrive/Textures/Road"

# ambientCG map suffix -> (UE asset-name suffix, srgb, compression_settings)
MAP_KINDS = {
    "Color":            ("_A", True,  unreal.TextureCompressionSettings.TC_DEFAULT),
    "NormalGL":         ("_N", False, unreal.TextureCompressionSettings.TC_NORMALMAP),
    "Roughness":        ("_R", False, unreal.TextureCompressionSettings.TC_MASKS),
    "AmbientOcclusion": ("_AO", False, unreal.TextureCompressionSettings.TC_MASKS),
}


def log(msg):
    unreal.log_warning(f"ROADTEX {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
imported = 0
missing = []

for entry in MANIFEST["textures"]:
    asset_id = entry["asset_id"]
    local_dir = SRC_DIR / asset_id
    dest_dir = f"{TEX_DIR}/{asset_id}"

    if not local_dir.exists():
        missing.append(asset_id)
        log(f"SKIP {asset_id}: {local_dir} not found - run "
            f"content_src/textures/road/download_road_textures.py first")
        continue

    for suffix, (name_suffix, srgb, compression) in MAP_KINDS.items():
        jpg_path = local_dir / f"{asset_id}_2K-JPG_{suffix}.jpg"
        if not jpg_path.exists():
            log(f"  WARN {asset_id}: missing {jpg_path.name}, skipping this map "
                f"(expected for AO on Asphalt032/Concrete034 - see manifest.json)")
            continue

        asset_name = f"T_{asset_id}{name_suffix}"
        t = unreal.AssetImportTask()
        t.filename = str(jpg_path)
        t.destination_path = dest_dir
        t.destination_name = asset_name
        t.automated = True
        t.save = False
        t.replace_existing = True
        tools.import_asset_tasks([t])

        tex = unreal.load_asset(f"{dest_dir}/{asset_name}")
        if tex is None:
            raise RuntimeError(f"texture import failed: {jpg_path}")
        tex.set_editor_property("srgb", srgb)
        tex.set_editor_property("compression_settings", compression)
        unreal.EditorAssetLibrary.save_loaded_asset(tex)
        imported += 1
        log(f"  imported {dest_dir}/{asset_name} (srgb={srgb})")

if missing:
    raise RuntimeError(
        f"{len(missing)} road texture set(s) missing locally: {missing} - "
        f"run content_src/textures/road/download_road_textures.py first")

log(f"DONE - imported {imported} road texture(s)")
