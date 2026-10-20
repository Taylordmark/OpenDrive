"""Import the CC0 ground PBR texture sets (content_src/textures/ground/,
content_src/textures/ground/download_ground_textures.py + manifest.json) as
UE Texture2D assets under /Game/OpenDrive/Textures/Ground/<Set>/ - Phase 3a
ground-visual overhaul (real biome-driven terrain surfaces, The Crew 2
art-direction target). apply_ground_material.py (run after this) builds the
actual material graph against these.

Same AssetImportTask pattern apply_map_texture.py already uses for the
single map-background PNG - extended here to loop over every
Color/NormalGL/Roughness/AmbientOcclusion PNG in every set and set the
correct per-map compression/sRGB settings, which a plain default import
gets wrong for normal/roughness/AO maps (UE's importer only auto-detects
"_N"/"Normal" suffixed *source filenames* as normal maps by default, and
these ambientCG names don't match that heuristic, so every non-Color map
would otherwise import sRGB-on/default-compressed - visibly wrong: a
roughness map read as sRGB darkens the mid-tones, and a normal map without
TC_Normalmap compression's derived-Z-reconstruction breaks entirely).

The Realistic twins reuse this importer for their own ambientCG sets
(PLAN-VISUALS Tracks C/D): runpy.run_path(this file, init_globals={
"SRC_DIR": <content_src folder with manifest.json>, "TEX_DIR": "/Game/..."})
- 2K-JPG sets are picked up as well as 2K-PNG, and manifest entries without
a download_url (generated textures) are skipped.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

SRC_DIR = Path(globals().get("SRC_DIR") or
               Path(__file__).resolve().parents[1] / "content_src" / "textures" / "ground")
MANIFEST = json.loads((SRC_DIR / "manifest.json").read_text())
TEX_DIR = globals().get("TEX_DIR") or "/Game/OpenDrive/Textures/Ground"

# ambientCG map suffix -> (UE asset-name suffix, srgb, compression_settings)
MAP_KINDS = {
    "Color":            ("_A", True,  unreal.TextureCompressionSettings.TC_DEFAULT),
    "NormalGL":         ("_N", False, unreal.TextureCompressionSettings.TC_NORMALMAP),
    "Roughness":        ("_R", False, unreal.TextureCompressionSettings.TC_MASKS),
    "AmbientOcclusion": ("_AO", False, unreal.TextureCompressionSettings.TC_MASKS),
    "Displacement":     ("_H", False, unreal.TextureCompressionSettings.TC_GRAYSCALE),
}


def log(msg):
    unreal.log(f"GROUNDTEX {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
imported = 0
missing = []

entries = [e for e in MANIFEST["textures"] if e.get("download_url")]
for entry in entries:
    asset_id = entry["asset_id"]
    local_dir = SRC_DIR / asset_id
    dest_dir = f"{TEX_DIR}/{asset_id}"

    if not local_dir.exists():
        # source is gitignored: a checkout that already imported it can rebuild materials without re-downloading
        if unreal.EditorAssetLibrary.does_directory_have_assets(dest_dir):
            log(f"SKIP {asset_id}: no source, keeping the already-imported {dest_dir}")
            continue
        missing.append(asset_id)
        log(f"SKIP {asset_id}: {local_dir} not found - run "
            f"content_src/textures/ground/download_ground_textures.py first")
        continue

    for suffix in entry.get("maps") or ("Color", "NormalGL", "Roughness", "AmbientOcclusion"):
        name_suffix, srgb, compression = MAP_KINDS[suffix]
        png_path = local_dir / f"{asset_id}_2K-PNG_{suffix}.png"
        if not png_path.exists():
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
        if not unreal.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=False):
            raise RuntimeError(f"save failed (file in use?): {dest_dir}/{asset_name}")
        imported += 1
        log(f"  imported {dest_dir}/{asset_name} (srgb={srgb}, compression={compression})")

if missing:
    raise RuntimeError(
        f"ground texture source folders missing: {missing} - run "
        f"content_src/textures/ground/download_ground_textures.py {SRC_DIR / 'manifest.json'} first")

log(f"done - imported {imported} texture(s) across {len(entries)} set(s)")
