"""Import the CC0 building-facade PBR texture sets
(content_src/textures/building/, download_building_textures.py + manifest.json)
as UE Texture2D assets under /Game/OpenDrive/Textures/Building/<Set>/ -
building-facade art-direction overhaul (2026-09-03, see next steps.md §7.15).
apply_building_material.py (run after this) builds the actual material
graph against these.

Same AssetImportTask pattern unreal/import_ground_textures.py already uses -
loops over every Color/NormalGL/Roughness/AmbientOcclusion PNG in every set
and sets the correct per-map compression/sRGB settings, which a plain
default import gets wrong for normal/roughness/AO maps (UE's importer only
auto-detects "_N"/"Normal" suffixed *source filenames* as normal maps by
default, and these ambientCG names don't match that heuristic, so every
non-Color map would otherwise import sRGB-on/default-compressed - visibly
wrong: a roughness map read as sRGB darkens the mid-tones, and a normal map
without TC_Normalmap compression's derived-Z-reconstruction breaks
entirely). AmbientOcclusion is optional per-set (Plaster001/Concrete034's
ambientCG zips didn't include one) - missing maps are skipped with a
warning, not fatal.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

SRC_DIR = Path(__file__).resolve().parents[1] / "content_src" / "textures" / "building"
MANIFEST = json.loads((SRC_DIR / "manifest.json").read_text())
TEX_DIR = "/Game/OpenDrive/Textures/Building"

# ambientCG map suffix -> (UE asset-name suffix, srgb, compression_settings)
MAP_KINDS = {
    "Color":            ("_A", True,  unreal.TextureCompressionSettings.TC_DEFAULT),
    "NormalGL":         ("_N", False, unreal.TextureCompressionSettings.TC_NORMALMAP),
    "Roughness":        ("_R", False, unreal.TextureCompressionSettings.TC_MASKS),
    "AmbientOcclusion": ("_AO", False, unreal.TextureCompressionSettings.TC_MASKS),
}


def log(msg):
    unreal.log(f"BUILDINGTEX {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
imported = 0
missing_dirs = []

for entry in MANIFEST["textures"]:
    asset_id = entry["asset_id"]
    local_dir = SRC_DIR / asset_id
    dest_dir = f"{TEX_DIR}/{asset_id}"

    if not local_dir.exists():
        missing_dirs.append(asset_id)
        log(f"SKIP {asset_id}: {local_dir} not found - run "
            f"content_src/textures/building/download_building_textures.py first")
        continue

    for suffix, (name_suffix, srgb, compression) in MAP_KINDS.items():
        png_path = local_dir / f"{asset_id}_2K-PNG_{suffix}.png"
        if not png_path.exists():   # Poly Haven sets download as JPG
            png_path = local_dir / f"{asset_id}_2K-JPG_{suffix}.jpg"
        if not png_path.exists():
            log(f"  WARN {asset_id}: missing {png_path.name}, skipping this map")
            continue

        asset_name = f"T_{asset_id}{name_suffix}"
        t = unreal.AssetImportTask()
        t.filename = str(png_path)
        t.destination_path = dest_dir
        t.destination_name = asset_name
        t.automated = True
        t.save = False
        t.replace_existing = True
        tools.import_asset_tasks([t])

        tex = unreal.load_asset(f"{dest_dir}/{asset_name}")
        if tex is None:
            raise RuntimeError(f"texture import failed: {png_path}")
        tex.set_editor_property("srgb", srgb)
        tex.set_editor_property("compression_settings", compression)
        unreal.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=False)
        imported += 1
        log(f"  imported {dest_dir}/{asset_name} (srgb={srgb}, compression={compression})")

if missing_dirs:
    raise RuntimeError(
        f"building texture source folders missing: {missing_dirs} - run "
        f"content_src/textures/building/download_building_textures.py first")

log(f"done - imported {imported} texture(s) across {len(MANIFEST['textures'])} set(s)")
