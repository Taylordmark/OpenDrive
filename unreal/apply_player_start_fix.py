"""Move the already-built OpenWorldTest level's PlayerStart to the corrected
road_test_meta.json player_start (2026-07-26) - in place, no full level
rebuild. Same "patch an existing level" pattern as apply_place_labels.py.

Fixes a real spawn bug: the OLD player_start (picked as "closest station to
the local origin") sat only ~2 m from a road-tile boundary, so the road
CHUNK covering it centroid-bucketed into a neighboring tile instead of the
one the spawn point itself sits in, leaving that tile with no road mesh at
all. AODVehiclePawn's ground-snap (which waits specifically for a
PM_Asphalt hit) then hovered for its full 20 s timeout before settling onto
bare terrain instead of pavement - reported as "spawn straight into the
air". pipeline/build_road_network.py's player-start picker now requires
real margin from every road-tile boundary (TILE_MARGIN_CM); this script
just re-points the level's existing PlayerStart actor at the new pick
without requiring a from-scratch rebuild.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

LEVEL = "/Game/OpenWorldTest"
LABEL = "Start_TestArea"

AREA_NAME = "oahu"
META_PATH = (Path(__file__).resolve().parents[1] / "data" / "road_tiles"
             / f"{AREA_NAME}_test" / "road_test_meta.json")

# must match build_openworld_test_level.py's own constant - clearance for
# tile streaming to catch up before the ground-snap trace starts.
SPAWN_HEIGHT_ABOVE_ORIGIN_CM = 2000.0


def log(msg):
    unreal.log(f"PLAYERSTARTFIX {msg}")


meta = json.loads(META_PATH.read_text())
player_start = meta["player_start"]
rx, ry, rz = player_start["location_cm"]
yaw = player_start["yaw_deg"]

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

start = None
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == LABEL:
        start = a
        break
if start is None:
    raise RuntimeError(f"{LABEL} not found in {LEVEL} - run "
                        f"build_openworld_test_level.py first")

new_loc = unreal.Vector(rx, ry, rz + SPAWN_HEIGHT_ABOVE_ORIGIN_CM)
new_rot = unreal.Rotator(0, 0, yaw)
start.set_actor_location(new_loc, False, False)
start.set_actor_rotation(new_rot, False)
log(f"{LABEL} moved to ({rx:.0f},{ry:.0f},{rz + SPAWN_HEIGHT_ABOVE_ORIGIN_CM:.0f}) yaw={yaw:.1f}")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
