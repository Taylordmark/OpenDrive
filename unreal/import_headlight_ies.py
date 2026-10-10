"""Import content_src/lights/headlight.ies (pipeline/make_headlight_ies.py)
as /Game/OpenDrive/Lights/IES_Headlight, the low-beam profile
AODDayNightController puts on every car's head spots.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""
from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "content_src" / "lights" / "headlight.ies"
DEST, NAME = "/Game/OpenDrive/Lights", "IES_Headlight"

t = unreal.AssetImportTask()
t.filename = str(SRC)
t.destination_path = DEST
t.destination_name = NAME
t.automated = True
t.save = True
t.replace_existing = True
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
prof = unreal.load_asset(f"{DEST}/{NAME}")
if not isinstance(prof, unreal.TextureLightProfile):
    raise RuntimeError(f"IES import failed: {SRC} -> {prof}")
unreal.EditorAssetLibrary.save_asset(f"{DEST}/{NAME}", only_if_is_dirty=False)
unreal.log_warning(f"HEADIES imported {NAME} {prof.blueprint_get_size_x()}x{prof.blueprint_get_size_y()} "
                   f"brightness {prof.get_editor_property('brightness')}")
