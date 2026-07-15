"""Minimal OpenDrive setup - PlayerStart + lighting + camera ONLY.

No mesh building: UE 5.8's embedded Python crashes under the heavy object
load of the full importer (see crash dumps, python311 access violations),
and the road corridor is carved into the landscape anyway, so the stage is
drivable without road meshes. Leaves any existing road_* actors untouched.

Tools > Execute Python Script... > this file.
"""

import json
import math
from pathlib import Path

import unreal

STAGE = "tail_of_the_dragon"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / STAGE
FOLDER = "TailOfTheDragon"
LABELS = {"Start_DealsGap", "Sun", "SkyAtmosphere", "SkyLight", "Fog"}

meta = json.loads((DATA_DIR / "stage_meta.json").read_text())
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

for a in list(actor_sub.get_all_level_actors()):
    if a.get_actor_label() in LABELS:
        actor_sub.destroy_actor(a)


def spawn(cls, loc=(0, 0, 0), rot=(0, 0, 0), label=None):
    a = actor_sub.spawn_actor_from_class(
        cls, unreal.Vector(*loc), unreal.Rotator(*rot))
    a.set_actor_location(unreal.Vector(*loc), False, False)
    a.set_actor_rotation(unreal.Rotator(*rot), False)
    if label:
        a.set_actor_label(label)
    a.set_folder_path(FOLDER)
    return a


# NOTE: unreal.Rotator is (roll, pitch, yaw)
ps = meta["player_start"]
spawn(unreal.PlayerStart, ps["location_cm"], (0, 0, ps["yaw_deg"]),
      label="Start_DealsGap")

sun = spawn(unreal.DirectionalLight, (0, 0, 100000), (0, -35, 40), label="Sun")
c = sun.get_component_by_class(unreal.DirectionalLightComponent)
c.set_editor_property("atmosphere_sun_light", True)
c.set_editor_property("intensity", 75000.0)

spawn(unreal.SkyAtmosphere, (0, 0, 0), label="SkyAtmosphere")

sky = spawn(unreal.SkyLight, (0, 0, 100000), label="SkyLight")
sky.get_component_by_class(unreal.SkyLightComponent).set_editor_property(
    "real_time_capture", True)

fog = spawn(unreal.ExponentialHeightFog, (0, 0, 30000), label="Fog")
fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
fc.set_editor_property("fog_density", 0.005)
fc.set_editor_property("start_distance", 10000.0)

x, y, z = ps["location_cm"]
yaw = math.radians(ps["yaw_deg"])
cam_loc = unreal.Vector(x - math.cos(yaw) * 3000, y - math.sin(yaw) * 3000, z + 1500)
cam_rot = unreal.Rotator(0.0, -15.0, ps["yaw_deg"])
try:
    unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem) \
        .set_level_viewport_camera_info(cam_loc, cam_rot)
except Exception as exc:
    unreal.log_warning(f"camera move failed: {exc}")

unreal.log("OpenDrive: minimal setup complete - save and press Play")
