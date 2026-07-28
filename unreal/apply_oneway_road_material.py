"""One-off: import T_Road_OneWay.png and build M_Road_OneWay (2026-07-25,
Taylor: one-way divided-highway carriageways like I-5 southbound were
showing a double-yellow centerline, which only belongs on a genuine two-way
road - see AODRoadManager.cpp's per-chunk material pick, which now needs a
second material to switch to). Mirrors M_Road's CURRENT graph exactly
(texture sample -> base color, moisture-derived roughness constant, Nanite
usage flag) as last built by apply_light_wet_fix.py/fix_road_nanite_flag.py
- same wet-gloss look, different texture only.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

STAGE = "tail_of_the_dragon"
STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / STAGE
TEX_DIR = "/Game/OpenDrive/Textures"
MAT_DIR = "/Game/OpenDrive/Materials"

moisture = json.loads((STAGE_DIR / "climate.json").read_text())["road_moisture"]
tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"ONEWAYROAD {msg}")


# ------------------------------------------------------------- import texture
t = unreal.AssetImportTask()
t.filename = str(STAGE_DIR / "textures" / "T_Road_OneWay.png")
t.destination_path = TEX_DIR
t.automated = True
t.save = False
t.replace_existing = True
tools.import_asset_tasks([t])
tex_road_oneway = unreal.load_asset(f"{TEX_DIR}/T_Road_OneWay")
if tex_road_oneway is None:
    raise RuntimeError("T_Road_OneWay import failed")
log("imported T_Road_OneWay.png")

# --------------------------------------------------------- M_Road_OneWay
mat_path = f"{MAT_DIR}/M_Road_OneWay"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    unreal.EditorAssetLibrary.delete_asset(mat_path)
m_road_oneway = tools.create_asset("M_Road_OneWay", MAT_DIR, unreal.Material,
                                   unreal.MaterialFactoryNew())
ts = MEL.create_material_expression(
    m_road_oneway, unreal.MaterialExpressionTextureSample, -600, 0)
ts.set_editor_property("texture", tex_road_oneway)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
rough_val = round(0.85 - 0.55 * moisture, 3)   # same formula as M_Road (apply_light_wet_fix.py)
rough = MEL.create_material_expression(
    m_road_oneway, unreal.MaterialExpressionConstant, -600, 300)
rough.set_editor_property("r", rough_val)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
m_road_oneway.set_editor_property("used_with_nanite", True)
MEL.recompile_material(m_road_oneway)
log(f"built M_Road_OneWay (roughness {rough_val}, moisture {moisture})")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
