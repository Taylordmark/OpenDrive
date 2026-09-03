"""Imports the procedurally-generated water ripple normal map
(content_src/textures/water/T_WaterRipple_N.png, generate_water_normal.py +
manifest.json - WATER visual-overhaul pass, 2026-09-03) as a UE Texture2D
asset under /Game/OpenDrive/Textures/Water/ - apply_water_material.py (run
after this) builds the actual material graph against it.

Same AssetImportTask + explicit compression/sRGB override pattern
unreal/import_ground_textures.py already established for this project's
other CC0/procedural PBR-style sets - a plain default import would treat
this as an sRGB color map (UE's importer only auto-detects "_N"/"Normal"
suffixed *source filenames* by default, which this filename DOES actually
match, but the override is made explicit here anyway rather than relying on
that heuristic, same "don't trust the auto-detect" stance the ground script
documents).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC_DIR = Path(__file__).resolve().parents[1] / "content_src" / "textures" / "water"
PNG_PATH = SRC_DIR / "T_WaterRipple_N.png"
TEX_DIR = "/Game/OpenDrive/Textures/Water"
ASSET_NAME = "T_WaterRipple_N"


def log(msg):
    unreal.log(f"WATERTEX {msg}")


if not PNG_PATH.exists():
    raise RuntimeError(
        f"{PNG_PATH} not found - run "
        f"content_src/textures/water/generate_water_normal.py first")

tools = unreal.AssetToolsHelpers.get_asset_tools()

t = unreal.AssetImportTask()
t.filename = str(PNG_PATH)
t.destination_path = TEX_DIR
t.destination_name = ASSET_NAME
t.automated = True
t.save = False
t.replace_existing = True
tools.import_asset_tasks([t])

tex = unreal.load_asset(f"{TEX_DIR}/{ASSET_NAME}")
if tex is None:
    raise RuntimeError(f"texture import failed: {PNG_PATH}")

tex.set_editor_property("srgb", False)
tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
# Tiles seamlessly by construction (generate_water_normal.py) - Wrap is the
# default address mode already, set explicitly so this doesn't silently
# regress if the project default ever changes.
tex.set_editor_property("address_x", unreal.TextureAddress.TA_WRAP)
tex.set_editor_property("address_y", unreal.TextureAddress.TA_WRAP)

unreal.EditorAssetLibrary.save_loaded_asset(tex, only_if_is_dirty=False)
log(f"imported {TEX_DIR}/{ASSET_NAME} (srgb=False, compression=TC_NORMALMAP, wrap)")
