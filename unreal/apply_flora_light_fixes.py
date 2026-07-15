"""Headless fixes from Taylor's 2026-07-15 drive test:
- Sun/SkyLight mobility -> Movable (day/night rotation was being rejected)
- reimport T_TerrainMask/T_Terrain (in-engine mask may predate corridor carve)
- GT_Forest densified ~5x + understory bushes ("wall of trees")

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
TEX_DIR = "/Game/OpenDrive/Textures"
MAT_DIR = "/Game/OpenDrive/Materials"
LEVEL = "/Game/TailOfTheDragon"

tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"FIX2 {msg}")


# ------------------------------------------------------------- mask reimport
for name in ("T_TerrainMask.png", "T_Terrain.png"):
    t = unreal.AssetImportTask()
    t.filename = str(STAGE_DIR / "textures" / name)
    t.destination_path = TEX_DIR
    t.automated = True
    t.save = True
    t.replace_existing = True
    tools.import_asset_tasks([t])
    asset = unreal.load_asset(f"{TEX_DIR}/{name[:-4]}")
    if asset is None:
        raise RuntimeError(f"reimport failed: {name}")
    if name == "T_TerrainMask.png":
        asset.set_editor_property("srgb", False)
    log(f"reimported {name}")

# ------------------------------------------------- GT_Forest wall-of-trees
gt = unreal.load_asset(f"{MAT_DIR}/GT_Forest")
if not gt:
    raise RuntimeError("GT_Forest not found")
varieties = list(gt.get_editor_property("grass_varieties"))
DENSITY = {"tree_blob": 0.85, "tree_olive": 0.55, "tree_conifer": 0.40}
for v in varieties:
    mesh = v.get_editor_property("grass_mesh")
    mname = mesh.get_name() if mesh else "?"
    if mname in DENSITY:
        v.set_editor_property("grass_density",
                              unreal.PerPlatformFloat(DENSITY[mname]))
        log(f"GT_Forest {mname} density -> {DENSITY[mname]}")

# understory: dense bushes at forest floor = the eye-level "wall"
bush = unreal.load_asset("/Game/OpenDrive/Trees/bush")
have_bush = any(
    v.get_editor_property("grass_mesh") is not None
    and v.get_editor_property("grass_mesh").get_name() == "bush"
    for v in varieties)
if bush and not have_bush:
    u = unreal.GrassVariety()
    u.set_editor_property("grass_mesh", bush)
    u.set_editor_property("grass_density", unreal.PerPlatformFloat(1.5))
    u.set_editor_property("start_cull_distance", unreal.PerPlatformInt(20000))
    u.set_editor_property("end_cull_distance", unreal.PerPlatformInt(45000))
    u.set_editor_property("random_rotation", True)
    u.set_editor_property("align_to_surface", False)
    u.set_editor_property("placement_jitter", 1.0)
    iv = unreal.FloatInterval()
    iv.set_editor_property("min", 1.0)
    iv.set_editor_property("max", 2.0)
    u.set_editor_property("scale_x", iv)
    varieties.append(u)
    log("GT_Forest understory bush variety added (density 1.5)")

gt.set_editor_property("grass_varieties", varieties)
unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/GT_Forest")

# ------------------------------------------------------------- light mobility
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
for a in actor_sub.get_all_level_actors():
    cls = a.get_class().get_name()
    if cls in ("DirectionalLight", "SkyLight"):
        comp = a.get_component_by_class(unreal.LightComponentBase)
        before = comp.get_editor_property("mobility")
        comp.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
        log(f"{cls} '{a.get_actor_label()}' mobility {before} -> MOVABLE")

# flush any serialized grass placement so it rebuilds from the fresh mask
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
unreal.SystemLibrary.execute_console_command(world, "grass.FlushCache")
try:
    unreal.get_editor_subsystem(unreal.LandscapeSubsystem).build_grass_maps()
    log("grass maps rebuilt")
except Exception as exc:
    log(f"build_grass_maps unavailable ({exc}); relying on runtime generation")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=True)
lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
