"""Set AODRoadManager's PlaceLabelFilePrefix on an existing level's
RoadManager actor, in place - no new_level(), no re-spawning. Same
idempotent-by-label edit pattern as apply_road_centerline.py.

Adds the road-name/city label above the mini-map (2026-07-23) - pipeline/
build_place_labels.py + export_place_labels_bin.py already produced the
ODL1 sidecar; this just points the already-built level's RoadManager at it
instead of requiring a full build_openworld_test_level.py rebuild.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

LEVEL = "/Game/OpenWorldTest"
LABEL = "RoadManager"

# must match build_openworld_test_level.py's AREA_NAME (pipeline/test_areas.py)
# for whichever area was built last - same convention that script already
# uses for the road/terrain/centerline prefixes.
AREA_NAME = "skagit_cascades"
PLACE_LABEL_PREFIX = f"labels_{AREA_NAME}_test"


def log(msg):
    unreal.log(f"PLACELABELS {msg}")


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

mgr.set_editor_property("place_label_file_prefix", PLACE_LABEL_PREFIX)
log(f"{LABEL}.place_label_file_prefix = {PLACE_LABEL_PREFIX}")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
