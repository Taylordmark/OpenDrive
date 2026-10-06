"""Unpaved road materials (2026-10-06, PLAN-REALISM Phase 2; replaces the
never-run 2026-09-30 apply_gravel_road_material.py): M_Road_Gravel +
PM_Gravel, M_Road_Dirt + PM_DirtRoad.

AODRoadManager puts these on road chunks whose ODR3 flags bits1-2 say
gravel (1) / dirt (2) - see pipeline/road_mesh_common.unpaved_stations().
Each material is M_Road's own graph (apply_road_material.py re-run with
SURFACE injected), so Wetness, SnowDepth/SideSnowDepth CPD and the berm WPO
all work; the base is Gravel023 / Ground037 and there are no lane markings.

The physmat NAMES are what the C++ keys on: "Gravel" / "DirtRoad" count as
road for the spawn ground-snap and tree exclusion, grip at the unpaved tier
(ODVehicleMovement::SurfaceGrip, GravelGrip/DirtGrip), and drive the road
noise crunch (ODVehicleAudio). Not "PM_Dirt": that one already exists as the
Dragon landscape's off-road physmat (apply_arc1_headless.py).

Needs unreal/import_road_textures.py + import_ground_textures.py to have run.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import runpy
from pathlib import Path

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
PM_DIR = "/Game/OpenDrive/Physics"
ROAD_SCRIPT = str(Path(__file__).resolve().parent / "apply_road_material.py")
tools = unreal.AssetToolsHelpers.get_asset_tools()

for surface, mat_name, pm_name in (("gravel", "M_Road_Gravel", "PM_Gravel"),
                                   ("dirt", "M_Road_Dirt", "PM_DirtRoad")):
    pm_path = f"{PM_DIR}/{pm_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(pm_path):
        pm = unreal.load_asset(pm_path)
    else:
        pm = tools.create_asset(pm_name, PM_DIR, unreal.PhysicalMaterial,
                                unreal.PhysicalMaterialFactoryNew())
    pm.set_editor_property("friction", 0.7)   # chassis contact only; wheel grip is SurfaceGrip's
    unreal.EditorAssetLibrary.save_loaded_asset(pm)

    runpy.run_path(ROAD_SCRIPT, init_globals={"SURFACE": surface})
    mat_path = f"{MAT_DIR}/{mat_name}"
    mat = unreal.load_asset(mat_path)
    mat.set_editor_property("phys_material", pm)
    unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
    unreal.log_warning(f"UNPAVEDROAD built + saved {mat_name} + {pm_name}")
unreal.log_warning("UNPAVEDROAD DONE")
