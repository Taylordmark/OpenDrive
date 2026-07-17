"""One-off: add the Nanite usage flag to M_Road and re-save (editor warned
'missing the usage flag Nanite ... may not render correctly outside the
editor'). apply_light_wet_fix.py / future rebuilds now set it at creation.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

PATH = "/Game/OpenDrive/Materials/M_Road"

m = unreal.load_asset(PATH)
if m is None:
    raise RuntimeError("M_Road not found")
m.set_editor_property("used_with_nanite", True)
unreal.MaterialEditingLibrary.recompile_material(m)
if not unreal.EditorAssetLibrary.save_asset(PATH):
    raise RuntimeError("save failed")
unreal.log(f"NANITEFIX used_with_nanite set + saved on {PATH}")
