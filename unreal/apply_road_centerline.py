"""Set AODRoadManager's CenterlineFilePrefix on an existing level's
RoadManager actor, in place - no new_level(), no re-spawning. Same
idempotent-by-label edit pattern as apply_vehicle_audio.py/apply_pause_menu.py.

Closes the last gap from the 2026-07-20 HUD decoupling work: ODRaceHUD's
mini-map/full map now fall back to AODRoadManager's own centerline when no
AODRaceManager exists on the level (open-world levels don't have
one - checkpoint races don't apply there). RoadManager already streams the
road mesh on the open-world test level; this just points it at the SAME
full-density centerline sidecar AODForestManager already loads for tree exclusion
(pipeline/export_road_centerline_bin.py), so the HUD has a route to draw.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

LEVEL = "/Game/OpenWorldTest"
LABEL = "RoadManager"

# must match build_openworld_test_level.py's AREA_NAME (pipeline/test_areas.py)
# for whichever area was built last - same convention that script already
# uses for the road/forest tile prefixes.
AREA_NAME = "skagit_cascades"
CENTERLINE_PREFIX = f"centerline_{AREA_NAME}_test"


def log(msg):
    unreal.log(f"ROADCENTERLINE {msg}")


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

mgr.set_editor_property("centerline_file_prefix", CENTERLINE_PREFIX)
log(f"{LABEL}.centerline_file_prefix = {CENTERLINE_PREFIX}")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
