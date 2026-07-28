"""Import the stylized terrain-color map background (pipeline/build_map_texture.py)
and wire it onto the level's AODRoadManager, in place - no new_level(), no
re-spawning. Same idempotent edit pattern as apply_place_labels.py.

Adds water/grass/forest/rock color behind the road line on the mini-map/full
map (2026-07-26) - ODRaceHUD::DrawStageMap draws MapTexture when set, falling
back to its original flat-black background otherwise.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

LEVEL = "/Game/OpenWorldTest"
LABEL = "RoadManager"
TEX_DIR = "/Game/OpenDrive/Textures"

# must match test_areas.AREA_NAME for whichever area was built last - same
# convention every other apply_*.py script in this project already uses.
AREA_NAME = "oahu"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "road_tiles" / f"{AREA_NAME}_test"
PNG_PATH = DATA_DIR / "map_texture.png"
META_PATH = DATA_DIR / "map_texture_meta.json"


def log(msg):
    unreal.log(f"MAPTEXTURE {msg}")


meta = json.loads(META_PATH.read_text())

tools = unreal.AssetToolsHelpers.get_asset_tools()
t = unreal.AssetImportTask()
t.filename = str(PNG_PATH)
t.destination_path = TEX_DIR
t.automated = True
t.save = False
t.replace_existing = True
tools.import_asset_tasks([t])
tex = unreal.load_asset(f"{TEX_DIR}/map_texture")
if tex is None:
    raise RuntimeError(f"texture import failed: {PNG_PATH}")
log(f"imported {PNG_PATH.name}")

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

mgr = None
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == LABEL:
        mgr = a
        break
if mgr is None:
    raise RuntimeError(f"{LABEL} not found in {LEVEL} - run "
                        f"build_openworld_test_level.py first")

mgr.set_editor_property("map_texture", tex)
mgr.set_editor_property("map_texture_center_cm",
                         unreal.Vector2D(meta["center_x_cm"], meta["center_y_cm"]))
mgr.set_editor_property("map_texture_span_cm", meta["span_cm"])
log(f"{LABEL}.map_texture = {tex.get_name()}, center={meta['center_x_cm']:.0f},"
    f"{meta['center_y_cm']:.0f} span={meta['span_cm']:.0f}")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
