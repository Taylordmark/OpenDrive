"""Whitewash + invisible-wet-road fixes (drive test 2026-07-16).

Diagnosis from screenshots: the moisture-darkened asphalt is fine in shadow
but blows to near-white in direct sun — auto-exposure meters the dark forest
mass and over-brightens sunlit surfaces. Fixes:
  1. "Grade" PostProcessVolume: exposure bias -1.0 EV (stops highlight
     clipping, lets colors saturate), highlight local-exposure contrast up.
  2. M_Road roughness 0.9-0.35*m -> 0.85-0.55*m (~0.39 wet gloss so the sky
     actually reflects; wetness reads as sheen, not just darkness).

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
ROAD_DIR = "/Game/OpenDrive/Road"
LEVEL = "/Game/TailOfTheDragon"

moisture = json.loads((STAGE_DIR / "climate.json").read_text())["road_moisture"]
tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"LIGHTWET {msg}")


# --------------------------------------------------- M_Road wet gloss rebuild
tex_road = unreal.load_asset(f"{TEX_DIR}/T_Road")
if tex_road is None:
    raise RuntimeError("T_Road not found")

road_mat_path = f"{MAT_DIR}/M_Road"
if unreal.EditorAssetLibrary.does_asset_exist(road_mat_path):
    unreal.EditorAssetLibrary.delete_asset(road_mat_path)
m_road = tools.create_asset("M_Road", MAT_DIR, unreal.Material,
                            unreal.MaterialFactoryNew())
ts = MEL.create_material_expression(
    m_road, unreal.MaterialExpressionTextureSample, -600, 0)
ts.set_editor_property("texture", tex_road)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
rough_val = round(0.85 - 0.55 * moisture, 3)
rough = MEL.create_material_expression(
    m_road, unreal.MaterialExpressionConstant, -600, 300)
rough.set_editor_property("r", rough_val)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
# road meshes render via Nanite; without this usage flag the material only
# works in-editor (editor auto-adds it transiently and warns)
m_road.set_editor_property("used_with_nanite", True)
MEL.recompile_material(m_road)

n = 0
for ap in unreal.EditorAssetLibrary.list_assets(ROAD_DIR):
    a = unreal.load_asset(ap)
    if isinstance(a, unreal.StaticMesh):
        a.set_material(0, m_road)
        n += 1
log(f"rebuilt M_Road (roughness {rough_val}, moisture {moisture}) "
    f"-> {n} road meshes")

# ------------------------------------------------------------- exposure bias
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

fixed = False
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == "Grade":
        s = a.get_editor_property("settings")
        s.set_editor_property("override_auto_exposure_bias", True)
        s.set_editor_property("auto_exposure_bias", -1.0)
        # pull blown highlights back toward midtones (local exposure)
        s.set_editor_property("override_local_exposure_highlight_contrast_scale", True)
        s.set_editor_property("local_exposure_highlight_contrast_scale", 0.6)
        a.set_editor_property("settings", s)
        fixed = True
        log("Grade PPV: exposure bias -1.0 EV, highlight contrast 0.6")
if not fixed:
    raise RuntimeError("PostProcessVolume 'Grade' not found")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
