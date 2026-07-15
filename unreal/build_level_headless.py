"""Headless level builder - run via UnrealEditor-Cmd against OpenDriveUE
with the editor CLOSED:

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout

Imports road OBJ chunks as StaticMesh assets (/Game/OpenDrive/Road), sets
complex-as-simple collision, places them in the TailOfTheDragon level, and
saves everything. Existing road actors (any kind) are replaced; lighting and
PlayerStart are left alone if present, created if missing.
"""

import math
from pathlib import Path

import unreal

STAGE = "tail_of_the_dragon"
OBJ_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / STAGE / "obj"
ASSET_DIR = "/Game/OpenDrive/Road"
LEVEL = "/Game/TailOfTheDragon"
FOLDER = "TailOfTheDragon"

import json
meta = json.loads((OBJ_DIR.parent / "stage_meta.json").read_text())


def log(msg):
    unreal.log(f"ROAD {msg}")


# ---------------------------------------------------------------- load level
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError(f"could not load level {LEVEL}")
log(f"loaded level {LEVEL}")

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

# ---------------------------------------------------------------- import objs
if unreal.EditorAssetLibrary.does_directory_exist(ASSET_DIR):
    unreal.EditorAssetLibrary.delete_directory(ASSET_DIR)

tasks = []
for obj in sorted(OBJ_DIR.glob("road_*.obj")):
    t = unreal.AssetImportTask()
    t.filename = str(obj)
    t.destination_path = ASSET_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

road_mat = unreal.load_asset("/Game/OpenDrive/Materials/M_Road") \
    or unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial")
meshes = []
for ap in sorted(unreal.EditorAssetLibrary.list_assets(ASSET_DIR)):
    asset = unreal.load_asset(ap)
    if not isinstance(asset, unreal.StaticMesh):
        continue
    bs = asset.get_editor_property("body_setup")
    bs.set_editor_property("collision_trace_flag",
                           unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
    if road_mat:
        asset.set_material(0, road_mat)
    meshes.append(asset)
log(f"imported {len(meshes)} road meshes")
if len(meshes) < 25:
    log("WARNING: expected 25 meshes")

# ---------------------------------------------------------------- place actors
removed = 0
for a in list(actor_sub.get_all_level_actors()):
    cls = a.get_class().get_name()
    label = a.get_actor_label()
    if cls == "DynamicMeshActor" or label.startswith("road_"):
        actor_sub.destroy_actor(a)
        removed += 1
log(f"removed {removed} old road actors")

for m in meshes:
    actor = actor_sub.spawn_actor_from_class(
        unreal.StaticMeshActor, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    actor.set_actor_location(unreal.Vector(0, 0, 0), False, False)
    actor.static_mesh_component.set_static_mesh(m)
    actor.set_actor_label(m.get_name())
    actor.set_folder_path(f"{FOLDER}/Road")
log(f"placed {len(meshes)} road actors")

# ---------------------------------------------------------------- env actors
existing_labels = {a.get_actor_label() for a in actor_sub.get_all_level_actors()}


def spawn(cls, loc, rot, label):  # rot = (roll, pitch, yaw)
    if label in existing_labels:
        return None
    a = actor_sub.spawn_actor_from_class(
        cls, unreal.Vector(*loc), unreal.Rotator(*rot))
    a.set_actor_location(unreal.Vector(*loc), False, False)
    a.set_actor_rotation(unreal.Rotator(*rot), False)
    a.set_actor_label(label)
    a.set_folder_path(FOLDER)
    log(f"spawned missing {label}")
    return a


ps = meta["player_start"]
spawn(unreal.PlayerStart, ps["location_cm"], (0, 0, ps["yaw_deg"]),
      "Start_DealsGap")
sun = spawn(unreal.DirectionalLight, (0, 0, 100000), (0, -35, 40), "Sun")
if sun:
    c = sun.get_component_by_class(unreal.DirectionalLightComponent)
    c.set_editor_property("atmosphere_sun_light", True)
    c.set_editor_property("intensity", 75000.0)
spawn(unreal.SkyAtmosphere, (0, 0, 0), (0, 0, 0), "SkyAtmosphere")
sky = spawn(unreal.SkyLight, (0, 0, 100000), (0, 0, 0), "SkyLight")
if sky:
    sky.get_component_by_class(unreal.SkyLightComponent).set_editor_property(
        "real_time_capture", True)
fog = spawn(unreal.ExponentialHeightFog, (0, 0, 30000), (0, 0, 0), "Fog")
if fog:
    fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
    fc.set_editor_property("fog_density", 0.005)
    fc.set_editor_property("start_distance", 10000.0)

# ---------------------------------------------------------------- save all
unreal.EditorAssetLibrary.save_directory(ASSET_DIR, only_if_is_dirty=False)
if not lls.save_current_level():
    log("WARNING: save_current_level returned False")
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - level saved")
