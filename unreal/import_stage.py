"""OpenDrive stage importer - run INSIDE the Unreal editor.

Tools > Execute Python Script... > select this file
(requires plugins: "Python Editor Script Plugin" and "Geometry Script")

Spawns: road mesh chunks (DynamicMeshActors with complex collision),
a PlayerStart at Deals Gap, and basic lighting/atmosphere if missing.
The Landscape itself is imported manually first - see README_PHASE1.md.
"""

import json
import math
from pathlib import Path

import unreal

STAGE = "tail_of_the_dragon"
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / STAGE
FOLDER = "TailOfTheDragon"

meta = json.loads((DATA_DIR / "stage_meta.json").read_text())
road = json.loads((DATA_DIR / "road_mesh.json").read_text())

actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
road_mat = unreal.load_asset("/Engine/BasicShapes/BasicShapeMaterial")

def gs_function(func_name):
    """Find the GeometryScript library exposing func_name — the python class
    names differ between UE versions (GeometryScriptLibrary_* vs
    GeometryScript_*), so discover instead of hardcoding."""
    for n in dir(unreal):
        if "GeometryScript" not in n:
            continue
        fn = getattr(getattr(unreal, n, None), func_name, None)
        if fn is not None and callable(fn):
            unreal.log(f"OpenDrive: using unreal.{n}.{func_name}")
            return fn
    libs = [n for n in dir(unreal)
            if "GeometryScript" in n and not n.startswith("F")]
    raise RuntimeError(
        f"No GeometryScript library exposes '{func_name}'. "
        f"Candidate classes seen: {libs}")


APPEND_BUFFERS = gs_function("append_buffers_to_mesh")
RECOMPUTE_NORMALS = gs_function("recompute_normals")


def cleanup_previous():
    """Remove actors from earlier runs so the script is safe to re-run."""
    removed = 0
    for a in actor_sub.get_all_level_actors():
        if str(a.get_folder_path()) == FOLDER:
            actor_sub.destroy_actor(a)
            removed += 1
    if removed:
        unreal.log(f"OpenDrive: removed {removed} actors from a previous run")


def spawn(cls, loc=(0, 0, 0), rot=(0, 0, 0), label=None):
    a = actor_sub.spawn_actor_from_class(
        cls, unreal.Vector(*loc), unreal.Rotator(*rot))
    # some spawn paths place actors at the camera pivot; force the transform
    a.set_actor_location(unreal.Vector(*loc), False, False)
    a.set_actor_rotation(unreal.Rotator(*rot), False)
    if label:
        a.set_actor_label(label)
    a.set_folder_path(FOLDER)
    return a


def append_buffers(mesh, buffers):
    """GeometryScript's python signature has shifted across versions; try the
    known arg patterns so this works on 5.x variants."""
    fn = APPEND_BUFFERS
    attempts = (
        lambda: fn(mesh, buffers),
        lambda: fn(mesh, buffers, unreal.GeometryScriptIndexList()),
        lambda: fn(target_mesh=mesh, buffers=buffers),
    )
    last = None
    for attempt in attempts:
        try:
            return attempt()
        except TypeError as exc:
            last = exc
    raise RuntimeError(f"append_buffers_to_mesh signature mismatch: {last}\n"
                       f"docstring:\n{fn.__doc__}")


def build_road():
    n_done = 0
    for chunk in road["chunks"]:
        v = chunk["vertices"]
        t = chunk["triangles"]
        uv = chunk["uvs"]
        buffers = unreal.GeometryScriptSimpleMeshBuffers()
        buffers.vertices = [unreal.Vector(v[i], v[i + 1], v[i + 2])
                            for i in range(0, len(v), 3)]
        buffers.triangles = [unreal.IntVector(t[i], t[i + 1], t[i + 2])
                             for i in range(0, len(t), 3)]
        buffers.uv0 = [unreal.Vector2D(uv[i], uv[i + 1])
                       for i in range(0, len(uv), 2)]

        actor = spawn(unreal.DynamicMeshActor, label=chunk["name"])
        comp = actor.dynamic_mesh_component
        # collision config first, so the mesh-change event cooks collision
        comp.set_editor_property("enable_complex_collision", True)
        comp.set_editor_property(
            "collision_type",
            unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE)
        comp.set_collision_profile_name("BlockAll")

        mesh = comp.get_dynamic_mesh()
        append_buffers(mesh, buffers)
        try:
            opts = unreal.GeometryScriptCalculateNormalsOptions()
            RECOMPUTE_NORMALS(mesh, opts)
        except Exception as exc:  # normals are cosmetic; keep going
            unreal.log_warning(f"recompute_normals failed: {exc}")

        if road_mat:
            comp.set_material(0, road_mat)
        for refresh in ("notify_mesh_updated", "mark_render_state_dirty"):
            m = getattr(comp, refresh, None)
            if callable(m):
                m()
                break
        n_done += 1
    unreal.log(f"OpenDrive: built {n_done} road chunks")


def build_spawn_and_lighting():
    ps = meta["player_start"]
    # unreal.Rotator is (roll, pitch, yaw)
    spawn(unreal.PlayerStart, ps["location_cm"], (0, 0, ps["yaw_deg"]),
          label="Start_DealsGap")

    existing = {a.get_class().get_name()
                for a in actor_sub.get_all_level_actors()}
    if "DirectionalLight" not in existing:
        light = spawn(unreal.DirectionalLight, (0, 0, 100000),
                      (0, -35, 40), label="Sun")
        c = light.get_component_by_class(unreal.DirectionalLightComponent)
        c.set_editor_property("atmosphere_sun_light", True)
        # physically based sun (~bright day, EV ~14); the actor default of
        # ~10 lux makes the atmosphere black and auto-exposure bottom out
        c.set_editor_property("intensity", 75000.0)
    if "SkyAtmosphere" not in existing:
        spawn(unreal.SkyAtmosphere, (0, 0, 0), label="SkyAtmosphere")
    if "SkyLight" not in existing:
        sky = spawn(unreal.SkyLight, (0, 0, 100000), label="SkyLight")
        c = sky.get_component_by_class(unreal.SkyLightComponent)
        c.set_editor_property("real_time_capture", True)
    if "ExponentialHeightFog" not in existing:
        fog = spawn(unreal.ExponentialHeightFog, (0, 0, 30000), label="Fog")
        fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
        fc.set_editor_property("fog_density", 0.005)   # default 0.02 is soup at 9 km scale
        fc.set_editor_property("start_distance", 10000.0)


def move_viewport_to_start():
    ps = meta["player_start"]
    x, y, z = ps["location_cm"]
    yaw = math.radians(ps["yaw_deg"])
    loc = unreal.Vector(x - math.cos(yaw) * 3000, y - math.sin(yaw) * 3000, z + 1500)
    rot = unreal.Rotator(0.0, -15.0, ps["yaw_deg"])  # (roll, pitch, yaw)
    for sub in (getattr(unreal, "UnrealEditorSubsystem", None),):
        if sub:
            try:
                unreal.get_editor_subsystem(sub).set_level_viewport_camera_info(loc, rot)
                unreal.log("OpenDrive: viewport camera moved to Deals Gap")
                return
            except Exception as exc:
                unreal.log_warning(f"camera move failed: {exc}")
    unreal.log_warning("OpenDrive: could not move viewport camera automatically")


if __name__ == "__main__":
    cleanup_previous()
    build_road()
    build_spawn_and_lighting()
    move_viewport_to_start()
    ls = meta["landscape"]
    unreal.log("OpenDrive: import complete.")
    unreal.log(f"Reminder - Landscape transform: location {ls['location_cm']}, "
               f"scale {ls['scale']} (see README_PHASE1.md)")
