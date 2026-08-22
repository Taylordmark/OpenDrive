"""Headless level builder - Arc 7.1 proof slice. Run via UnrealEditor-Cmd
against OpenDriveUE with the editor CLOSED:

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout

Creates a NEW, separate level (/Game/OpenWorldTest) - the open-world
free-roam level, kept apart from TailOfTheDragon.umap. Renamed 2026-07-24
from an earlier live-streamed-terrain-tileset-based version: the Arc 7.0
pivot (2026-07-23, next steps.md sec 7.0) replaced that entirely - terrain
is baked offline into world-space tiles (pipeline/build_terrain_mesh_tiles.py)
and streamed by AODTerrainManager, the same DynamicMesh-tile pattern
AODRoadManager already proved for roads - no live tileset, no
georeference actor, nothing to reconcile against at runtime. Places
AODTerrainManager + AODRoadManager, plus a PlayerStart so AODVehiclePawn
(the GameMode's default pawn) spawns on the baked terrain/road. This level
is DefaultEngine.ini's EditorStartupMap/GameDefaultMap as of the rename.

ROADS_ENABLED/BUILDINGS_ENABLED/BARRIER_ENABLED/FOREST_ENABLED (2026-07-27,
staged Oahu troubleshooting - Taylor's call): originally one combined
ROADS_ENABLED flag gating all four systems together, so terrain-only issues
(ground material, lighting, elevation feel) weren't tangled up with
road-network ones (missing segments, barrier walls through towns, etc.) in
the same test pass. Split 2026-07-28 into four independent flags once
terrain checked out, so roads can come back on their own before buildings/
barrier/forest do - each stage adds one more system to isolate. NOTE:
with ROADS_ENABLED=True but BARRIER_ENABLED=False, nothing physically stops
the player from driving off the road onto a "far" terrain tile, which is
visual-only/no-collision beyond NEAR_MARGIN_M of a road station (pipeline/
build_terrain_mesh_tiles.py) - that's the actual mechanism behind the
"fell into a void, no log trace at all" report from the terrain-only pass.
Expect the same off-road void-fall risk here until BARRIER_ENABLED comes
back on too.

To REBUILD after the level already exists: delete
OpenDriveUE/Content/OpenWorldTest.umap from disk yourself BEFORE
launching this script, not from inside it. Deleting it from Python in the
same process that then calls new_level() does not work reliably here - the
asset registry's on-disk cache still reports the package as present for the
rest of that process's lifetime (confirmed 2026-07-20: os.remove() +
EditorAssetLibrary.delete_asset() + a forced AssetRegistry
scan_paths_synchronous(force_rescan=True) all still leave new_level()
failing "an asset already exists" in the SAME process; a fresh process that
starts with the file already gone works fine every time). load_level() as
a "does it exist" probe doesn't work either - on a missing package it
silently returns True against the engine's blank transient startup level
instead of failing, which then can't be saved ("no filename").
"""

import json
import sys
from pathlib import Path

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import test_areas   # noqa: E402 - BBOX_SNWE/ORIGIN_HEIGHT_M for the sea-level water plane below

LEVEL = "/Game/OpenWorldTest"
FOLDER = "OpenWorldTest"

# must match pipeline/test_areas.py's AREA_NAME for whichever area was built
# last (both build_terrain_mesh_tiles.py and build_road_network.py)
AREA_NAME = "oahu"

# see module docstring - each flag independently gates its own manager.
# PlayerStart still uses road_test_meta.json's point (a real, already-
# verified point on land) for positioning whenever ROADS_ENABLED, regardless
# of the other three - only each system's own actor is skipped when False.
# BARRIER_ENABLED back on 2026-07-28 (AUDIT.md 5.1/rec. 3 fixed the ODW2 bake
# size problem this was staged off to isolate from; the mechanism itself was
# never in question, just its 213 MB disk cost - see ODBarrierManager).
ROADS_ENABLED = True
BUILDINGS_ENABLED = True
BARRIER_ENABLED = True
FOREST_ENABLED = False
# Phase 1 switchback retaining-wall geometry (2026-07-30) - see
# ODWallManager.h and pipeline/build_retaining_walls.py. Independent of the
# other flags: a tile with no walls_*.bin file is an ordinary tile with no
# flagged switchback conflict nearby, same "no hole" semantics as buildings.
WALL_ENABLED = True

SPAWN_HEIGHT_ABOVE_ORIGIN_CM = 2000.0   # 20 m - clearance for tile streaming to catch up before landing
                                         # (was 300 m; cut down 2026-07-20 once -odtrace showed both
                                         # terrain and road tiles actually stream in well under a
                                         # second, not the ~8 s a 300 m fall gave. road_test_meta.json's
                                         # elevation is now the FINAL answer, not a placeholder needing
                                         # runtime correction - the Arc 7.2 restructure (2026-07-20,
                                         # since carried forward by the Arc 7.0 baked-terrain pivot on
                                         # 2026-07-23) moved terrain/road reconciliation entirely offline
                                         # (pipeline/build_terrain_mesh_tiles.py carves the terrain to the
                                         # road, verified by pipeline/verify_terrain_fit.py against the
                                         # actual built mesh), so there is no runtime ground-snap step
                                         # left to race. This clearance now exists only so the car
                                         # doesn't spawn embedded in terrain that hasn't streamed in yet
                                         # at BeginPlay, not to buy time for an elevation correction.)

# Arc 7.2: road network mesh (pipeline/build_road_network.py +
# export_road_mesh_bin.py, streamed in tiles as of slice 2) - PlayerStart
# moves onto the actual generated road instead of bare origin-above-terrain
# (still with the same streaming catch-up clearance, just relative to the
# road's own point, not the origin).
#
# ROADS_ENABLED=False override (2026-07-27): road_test_meta.json's own pick
# (closest station to the bbox center) lands near the Wahiawa plateau/
# central saddle - real terrain, but with more relief right at the spawn
# point than a flat coastal spot, and the map-select screen has nothing to
# click without AODRoadManager's town-node data anyway (reported live: "the
# map had no waypoints"). Ala Moana (flat, low-elevation, real Honolulu
# coastal park/mall area) is a known-good, low-drama single spawn point for
# a terrain-only pass - real elevation sampled directly (1.0 m, z14
# Terrarium), not guessed, same discipline as ORIGIN_HEIGHT_M.
ALA_MOANA_LATLON = (21.2908, -157.8420)
ALA_MOANA_ELEV_M = 1.0

if ROADS_ENABLED:
    ROAD_META_PATH = (Path(__file__).resolve().parents[1] / "data" / "road_tiles"
                       / f"{AREA_NAME}_test" / "road_test_meta.json")
    road_meta = json.loads(ROAD_META_PATH.read_text())
    road_start = road_meta["player_start"]
else:
    import math
    M_PER_DEG_LAT = 111132.0
    origin_lat0, origin_lon0 = 21.479999999999997, -157.97500000000002   # test_areas.BBOX_SNWE center
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(origin_lat0))
    lat, lon = ALA_MOANA_LATLON
    east_m = (lon - origin_lon0) * m_per_deg_lon
    north_m = (lat - origin_lat0) * M_PER_DEG_LAT
    road_start = {
        "location_cm": [east_m * 100.0, -north_m * 100.0,
                         (ALA_MOANA_ELEV_M - 306.0) * 100.0],   # 306.0 = ORIGIN_HEIGHT_M
        "yaw_deg": 0.0,
    }


def log(msg):
    unreal.log(f"OPENWORLDBUILD {msg}")


# ---------------------------------------------------------------- new level
# The .umap must already be gone from disk before this process starts - see
# the module docstring for why deleting it from inside this same process
# doesn't reliably work.
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

if not lls.new_level(LEVEL):
    raise RuntimeError(
        f"could not create new level {LEVEL} - if it already exists, "
        f"delete OpenDriveUE/Content/{LEVEL.split('/')[-1]}.umap from disk "
        f"yourself first, then rerun this script as a fresh process")
log(f"created level {LEVEL}")


def spawn(cls, loc, rot, label):
    a = actor_sub.spawn_actor_from_class(cls, unreal.Vector(*loc), unreal.Rotator(*rot))
    a.set_actor_location(unreal.Vector(*loc), False, False)
    a.set_actor_rotation(unreal.Rotator(*rot), False)
    a.set_actor_label(label)
    a.set_folder_path(FOLDER)
    return a


# ---------------------------------------------------------------- terrain
# Arc 7.0 pivot (2026-07-23): baked/tiled DynamicMesh terrain, streamed by
# AODTerrainManager the same way AODRoadManager streams road tiles - no
# georeference actor needed, since every pipeline stage already writes
# UE-frame cm coordinates directly (road_mesh_common.road_ue_z_m applies
# the same ORIGIN_HEIGHT_M shift build_terrain_mesh_tiles.py used when
# baking, so there's nothing left for a runtime actor to reconcile).
terrain_mgr = spawn(unreal.ODTerrainManager, (0, 0, 0), (0, 0, 0), "TerrainManager")
terrain_mgr.set_editor_property("terrain_tile_prefix", f"terrain_{AREA_NAME}_test")
# AODTerrainManager's SpawnRadius default (400,000 cm = 4 km) is well short
# of build_terrain_mesh_tiles.py's RENDER_DISTANCE_M (6,000 m) - the far/
# background tiles specifically baked so distant mountains stay visible
# (no collision, coarse grid, "the player can never reach it") were sitting
# on disk built and fine, just never REQUESTED at runtime because nothing
# closer than 6 km ever entered the 4 km load radius. Found live 2026-07-28
# (Taylor: a gap "way in the distance") - not a bad/missing tile, just a
# runtime radius that didn't reach as far as the offline bake did. 620,000 cm
# gives a small margin past the 6 km build radius so the edge doesn't clip.
terrain_mgr.set_editor_property("spawn_radius", 620000.0)
log(f"spawned TerrainManager (tile prefix: terrain_{AREA_NAME}_test, "
    f"spawn_radius=6.2km to match build_terrain_mesh_tiles.py's 6km far-tile bake)")

spawn(unreal.ODScreenshotHook, (0, 0, 0), (0, 0, 0), "ScreenshotHook")
log("spawned ScreenshotHook (-odshot works on this Landscape-free level too)")

if not BUILDINGS_ENABLED:
    log("BUILDINGS_ENABLED=False - skipping BuildingManager")
if not WALL_ENABLED:
    log("WALL_ENABLED=False - skipping WallManager (switchback locations "
        "fall back to whatever the terrain carve alone produces)")
if not BARRIER_ENABLED:
    log("BARRIER_ENABLED=False - skipping BarrierManager (nothing physically "
        "stops off-road driving onto no-collision 'far' terrain - see module "
        "docstring)")
if not ROADS_ENABLED:
    log("ROADS_ENABLED=False - skipping RoadManager")
if not FOREST_ENABLED:
    log("FOREST_ENABLED=False - skipping ForestManager")

if BUILDINGS_ENABLED:
    # ------------------------------------------------------------ buildings
    # Arc 7.0 step 3 (2026-07-24): procedural buildings on populated tiles,
    # streamed the same tile-by-tile way as terrain/roads (pipeline/
    # build_building_mesh.py + export_building_mesh_bin.py). Proof scope is
    # Mount Vernon only - most of the map has no buildings_*.bin file at all,
    # which AODBuildingManager already treats as an ordinary unpopulated tile,
    # not an error.
    building_mgr = spawn(unreal.ODBuildingManager, (0, 0, 0), (0, 0, 0), "BuildingManager")
    building_mgr.set_editor_property("building_tile_prefix", f"buildings_{AREA_NAME}_test")
    if not unreal.EditorAssetLibrary.does_asset_exist("/Game/OpenDrive/Materials/M_BuildingFacade"):
        log("WARNING: M_BuildingFacade not found - run unreal/apply_building_material.py "
            "at least once to create it (Content/ is git-ignored, doesn't survive a fresh checkout)")
    log(f"spawned BuildingManager (tile prefix: buildings_{AREA_NAME}_test)")

if WALL_ENABLED:
    # ------------------------------------------------------------ retaining walls
    # Phase 1 switchback geometry (2026-07-30, pipeline/build_retaining_walls.py
    # + export_retaining_wall_bin.py) - reuses M_GroundStylized (no dedicated
    # material authored yet), so no does_asset_exist warning needed the way
    # BuildingManager's M_BuildingFacade check has (that material ships with
    # every fresh checkout, it's the engine's own Landscape-style stylized ground).
    wall_mgr = spawn(unreal.ODWallManager, (0, 0, 0), (0, 0, 0), "WallManager")
    wall_mgr.set_editor_property("wall_tile_prefix", f"walls_{AREA_NAME}_test")
    log(f"spawned WallManager (tile prefix: walls_{AREA_NAME}_test)")

if BARRIER_ENABLED:
    # ------------------------------------------------------------ road-corridor barrier
    # Guaranteed "wall of scenery" (2026-07-24) - found necessary live: a hard
    # cornering slide let the car reach open space and fall through within a
    # few meters, since the organic forest scatter (AODForestManager) is only
    # probabilistic. Deterministic, tightly-spaced tree/bush points just
    # outside the existing 11.5 m shoulder clearance, each with guaranteed
    # (not pooled) collision - see pipeline/build_road_barrier.py.
    barrier_mgr = spawn(unreal.ODBarrierManager, (0, 0, 0), (0, 0, 0), "BarrierManager")
    barrier_mgr.set_editor_property("barrier_tile_prefix", f"barrier_{AREA_NAME}_test")
    log(f"spawned BarrierManager (tile prefix: barrier_{AREA_NAME}_test)")

if ROADS_ENABLED:
    # ------------------------------------------------------------ road network
    # Arc 7.2 slice 2: streamed in tiles (road_<AREA>_test_tile_<tx>_<ty>.bin),
    # not one whole-network file - RoadManager just needs the shared prefix and
    # discovers/streams tiles itself at runtime as the player moves.
    road_mgr = spawn(unreal.ODRoadManager, (0, 0, 0), (0, 0, 0), "RoadManager")
    road_mgr.set_editor_property("road_tile_prefix", f"road_{AREA_NAME}_test")
    # same full-density centerline sidecar ForestManager loads below, just for
    # ODRaceHUD's map fallback (2026-07-20: mini-map no longer needs a
    # checkpoint race, see ODRaceHUD.cpp) - see unreal/apply_road_centerline.py
    # for setting this on an already-built level without a full rebuild.
    road_mgr.set_editor_property("centerline_file_prefix", f"centerline_{AREA_NAME}_test")
    # map-drawing-only centerline (2026-07-28, pipeline/
    # export_road_centerline_bin.py's ODM1 sidecar) - per-segment-simplified
    # with break markers, fixes both the "connects unrelated streets" bug and
    # the ~780,000-line-draws-a-frame lag the raw centerline_file_prefix data
    # caused on Oahu's 8,848-segment network. See ODRoadManager.h's
    # MapCenterlineFilePrefix comment.
    road_mgr.set_editor_property("map_centerline_file_prefix", f"centerline_map_{AREA_NAME}_test")
    # road-name/city label above the mini-map (2026-07-23, pipeline/
    # build_place_labels.py + export_place_labels_bin.py) - empty prefix would
    # just mean no label draws, same optional-feature convention as the two
    # properties above.
    road_mgr.set_editor_property("place_label_file_prefix", f"labels_{AREA_NAME}_test")
    log(f"spawned RoadManager (tile prefix: road_{AREA_NAME}_test, "
        f"{road_meta['segments']} segments, {road_meta['total_length_km']} km)")

    # ------------------------------------------------------------ map texture
    # Stylized terrain-color map background (pipeline/build_map_texture.py) -
    # folded in here 2026-07-28 (same "one-off script doesn't survive a
    # from-scratch rebuild" class of bug as AODMainMenu/M_BuildingFacade/the
    # PlayerStart fix above - this used to be a separate follow-up script,
    # unreal/apply_map_texture.py, never re-run after this level got rebuilt
    # from scratch again. Symptom: the mini-map/full map silently fell back to
    # flat near-black with no coastline/terrain color at all, found live
    # 2026-07-28 - Taylor: the map reads "unbounded" with nothing on it).
    MAP_TEX_DIR = "/Game/OpenDrive/Textures"
    map_tex_png = (Path(__file__).resolve().parents[1] / "data" / "road_tiles"
                   / f"{AREA_NAME}_test" / "map_texture.png")
    map_tex_meta_path = map_tex_png.parent / "map_texture_meta.json"
    if map_tex_png.exists() and map_tex_meta_path.exists():
        map_tex_meta = json.loads(map_tex_meta_path.read_text())
        import_task = unreal.AssetImportTask()
        import_task.filename = str(map_tex_png)
        import_task.destination_path = MAP_TEX_DIR
        import_task.automated = True
        import_task.save = False
        import_task.replace_existing = True
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([import_task])
        map_tex = unreal.load_asset(f"{MAP_TEX_DIR}/map_texture")
        if map_tex is None:
            log("WARNING: map_texture.png import failed - mini-map/full map "
                "will fall back to flat-color background")
        else:
            road_mgr.set_editor_property("map_texture", map_tex)
            road_mgr.set_editor_property(
                "map_texture_center_cm",
                unreal.Vector2D(map_tex_meta["center_x_cm"], map_tex_meta["center_y_cm"]))
            road_mgr.set_editor_property("map_texture_span_cm", map_tex_meta["span_cm"])
            log(f"RoadManager.map_texture = {map_tex.get_name()} "
                f"(center={map_tex_meta['center_x_cm']:.0f},{map_tex_meta['center_y_cm']:.0f} "
                f"span={map_tex_meta['span_cm']:.0f})")
    else:
        log(f"WARNING: {map_tex_png} not found - run "
            f"pipeline/build_map_texture.py first for a map background")

if FOREST_ENABLED:
    # ------------------------------------------------------------ forest
    # Arc 7.3 (2026-07-20): open-world forest, unblocked from needing an
    # ALandscapeProxy - BiomeFilePrefix points AODForestManager at the point-
    # classified biome sidecar (pipeline/classify_biomes.py + export_biome_bin.py)
    # instead of the Landscape-mask path Tail of the Dragon uses. Empty on that
    # level's actor instance, so this doesn't touch it. Gated separately from
    # ROADS_ENABLED as of 2026-07-28 (used to be the same flag) - the
    # elevation grid this reads (pipeline/build_forest_elevation.py) is itself
    # a fan sampled ALONG the road corridor, so it still needs a road network
    # built at least once, but can now be switched off independently while
    # ROADS_ENABLED stays True.
    forest_mgr = spawn(unreal.ODForestManager, (0, 0, 0), (0, 0, 0), "ForestManager")
    forest_mgr.set_editor_property("biome_file_prefix", f"biome_{AREA_NAME}_test")
    # Ground-truth elevation grid + road centerline (2026-07-20, pipeline/
    # build_forest_elevation.py + export_road_centerline_bin.py) - replaces a
    # physics raycast against whatever streamed terrain LOD happens to be
    # resident, which could hit a stale/coarse LOD or genuinely wrong low-zoom
    # source data far from the road (found via floating tree patches - whole
    # regions off the ground together, not scattered outliers).
    forest_mgr.set_editor_property("elevation_file_prefix", f"elev_{AREA_NAME}_test")
    forest_mgr.set_editor_property("centerline_file_prefix", f"centerline_{AREA_NAME}_test")
    # building-centroid tree exclusion (2026-07-24, Arc 7.0 step 3) - found
    # necessary live: the biome classifier has no concept of "town", so without
    # this trees were scattering at max density straight through Mount Vernon's
    # buildings. Same optional-prefix convention as everything else here.
    forest_mgr.set_editor_property("building_centroid_file_prefix", f"building_centroids_{AREA_NAME}_test")
    log(f"spawned ForestManager (biome/elevation/centerline/building-centroid file "
        f"prefixes: biome_{AREA_NAME}_test / elev_{AREA_NAME}_test / "
        f"centerline_{AREA_NAME}_test / building_centroids_{AREA_NAME}_test)")

# ---------------------------------------------------------------- gameplay managers
# These previously only existed on Tail of the Dragon, placed by one-off
# unreal/apply_*.py scripts hardcoded to that level - this level never got
# them (found 2026-07-20: HUD/audio/tuning/pause all silently missing on
# this level, traced to the actors themselves never being spawned here, not
# a deeper bug). RaceManager is deliberately NOT spawned - it exists for
# checkpoint-gate races, which don't apply to this open-world free-roam
# loop; ODRaceHUD.cpp was updated the same day so tuning/pause/speed no
# longer depend on a RaceManager being present (only the mini-map still does).
spawn(unreal.ODVehicleAudio, (0, 0, 0), (0, 0, 0), "VehicleAudio")
log("spawned VehicleAudio")

spawn(unreal.ODPauseMenu, (0, 0, 0), (0, 0, 0), "PauseMenu")
log("spawned PauseMenu")

# Front-end menu (2026-07-25, unreal/apply_main_menu.py) - folded in here too
# after a from-scratch rebuild (2026-07-26) silently dropped it, since this
# builder never spawned it itself and apply_main_menu.py is a separate
# follow-up script. Same lesson as the Sun-mobility bug above: a one-off
# patch script's effect is lost on the next full rebuild unless it's in the
# from-scratch builder too.
spawn(unreal.ODMainMenu, (0, 0, 0), (0, 0, 0), "MainMenu")
log("spawned MainMenu")

tuning_mgr = spawn(unreal.ODTuningManager, (0, 0, 0), (0, 0, 0), "TuningManager")
tuning_assets = []
for preset_name in ("Street", "Rally", "Drift", "Drag", "Offroad"):
    asset_path = f"/Game/OpenDrive/Tuning/DA_Tuning_{preset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(asset_path):
        tuning_assets.append(unreal.load_asset(asset_path))
    else:
        log(f"WARNING: {asset_path} not found - run unreal/apply_tuning_presets.py "
            f"against TailOfTheDragon at least once to create the shared preset assets")
tuning_mgr.set_editor_property("presets", tuning_assets)
log(f"spawned TuningManager ({len(tuning_assets)} preset(s))")

daynight = spawn(unreal.ODDayNightController, (0, 0, 0), (0, 0, 0), "DayNight")
log("spawned DayNight controller")

# ---------------------------------------------------------------- spawn + lights
rx, ry, rz = road_start["location_cm"]
# unreal.Rotator ctor order is (roll, pitch, yaw) - see the Sun spawn below
spawn(unreal.PlayerStart, (rx, ry, rz + SPAWN_HEIGHT_ABOVE_ORIGIN_CM),
      (0, 0, road_start["yaw_deg"]), "Start_TestArea")
log(f"PlayerStart moved onto the generated road at ({rx:.0f}, {ry:.0f}), "
    f"yaw={road_start['yaw_deg']}deg, {SPAWN_HEIGHT_ABOVE_ORIGIN_CM/100:.0f}m "
    f"above the road surface for streaming catch-up")
sun = spawn(unreal.DirectionalLight, (0, 0, 100000), (0, -35, 40), "Sun")
sun_comp = sun.get_component_by_class(unreal.DirectionalLightComponent)
sun_comp.set_editor_property("atmosphere_sun_light", True)
sun_comp.set_editor_property("intensity", 75000.0)
# Editor-spawned lights default to STATIONARY mobility, which silently
# rejects AODDayNightController's runtime SetActorRotation (found in Arc
# 2.1, fixed via apply_flora_light_fixes.py on the OLD level - but that was
# a one-off PATCH script, never folded back into this from-scratch BUILD
# script, so every full rebuild silently reintroduced the bug. Found again
# 2026-07-23 rebuilding for the Arc 7.0 terrain pivot: PIE log spammed
# "Mobility of ... has to be 'Movable'" every frame, meaning the sun was
# stuck at its spawn angle the whole session - a real, if minor,
# contributor to how washed-out/flat some distant terrain screenshots
# looked that session. Set explicitly here now instead of depending on a
# separate patch script to catch it after the fact.
sun_comp.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)
spawn(unreal.SkyAtmosphere, (0, 0, 0), (0, 0, 0), "SkyAtmosphere")
sky = spawn(unreal.SkyLight, (0, 0, 100000), (0, 0, 0), "SkyLight")
sky.get_component_by_class(unreal.SkyLightComponent).set_editor_property(
    "real_time_capture", True)
log("spawned PlayerStart + lighting (Sun mobility=MOVABLE)")

# ---------------------------------------------------------------- color grade
# Found missing 2026-07-24: this level never got the equivalent of Tail of
# the Dragon's "Grade" PostProcessVolume (build_visuals_headless.py) or its
# later exposure fix (apply_light_wet_fix.py) - both were built for/applied
# to TailOfTheDragon.umap specifically and never carried over here. Running
# on raw engine auto-exposure defaults in a scene with a huge sky + water +
# mixed light/dark forest masses (arguably a harder metering case than the
# Dragon) is exactly the kind of setup that already caused the Dragon's
# "roads look whitewashed, nothing reads as its real color" issue -
# reported live 2026-07-24 as flat/dark terrain and "no grass" showing.
# Same proven values as the Dragon's fix, not re-derived from scratch.
grade = spawn(unreal.PostProcessVolume, (0, 0, 0), (0, 0, 0), "Grade")
grade.set_editor_property("unbound", True)
gs = grade.get_editor_property("settings")
gs.set_editor_property("override_color_saturation", True)
gs.set_editor_property("color_saturation", unreal.Vector4(1.22, 1.22, 1.22, 1.0))
gs.set_editor_property("override_color_contrast", True)
gs.set_editor_property("color_contrast", unreal.Vector4(1.06, 1.06, 1.06, 1.0))
gs.set_editor_property("override_auto_exposure_bias", True)
gs.set_editor_property("auto_exposure_bias", -1.0)
gs.set_editor_property("override_local_exposure_highlight_contrast_scale", True)
gs.set_editor_property("local_exposure_highlight_contrast_scale", 0.6)
grade.set_editor_property("settings", gs)
log("spawned Grade post-process volume (saturation/contrast/exposure bias, "
    "same values as Tail of the Dragon's fix)")

# ---------------------------------------------------------------- water (Arc 7.7 Tier 0)
# AUDIT.md 2026-07-27 sec 7.7: "water doesn't exist" - build_terrain_mesh_tiles.py
# only bakes terrain within RENDER_DISTANCE_M of a road station, so the ocean
# on an island bbox is a genuine geometry hole, not a missing-material issue.
# Tier 0 (this): one big flat plane at sea level sized to the whole bbox - a
# real fix for "no water anywhere," not yet the Tier 1 real coastline/lake
# fetch (next steps.md sec 7.7 has that scoped separately). Won't flood
# inland water sitting above sea level (e.g. Wahiawa Reservoir) - a flat
# plane can't tell "below sea level" from "an inland lake at elevation."
if not unreal.EditorAssetLibrary.does_asset_exist("/Game/OpenDrive/Materials/M_WaterStylized"):
    log("WARNING: M_WaterStylized not found - run unreal/apply_water_material.py "
        "at least once to create it (Content/ is git-ignored, doesn't survive a fresh checkout)")
water_mat = unreal.load_asset("/Game/OpenDrive/Materials/M_WaterStylized")

import math
south, west, north, east = test_areas.BBOX_SNWE
lat0 = (south + north) / 2.0
lon0 = (west + east) / 2.0
M_PER_DEG_LAT = 111132.0
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
# generous margin past the real bbox so the shoreline never clips into a
# visible flat-plane edge at the horizon
WATER_MARGIN_M = 20000.0
half_width_cm = ((east - west) * m_per_deg_lon / 2.0 + WATER_MARGIN_M) * 100.0
half_height_cm = ((north - south) * M_PER_DEG_LAT / 2.0 + WATER_MARGIN_M) * 100.0
sea_level_z_cm = -test_areas.ORIGIN_HEIGHT_M * 100.0

water = actor_sub.spawn_actor_from_class(
    unreal.StaticMeshActor, unreal.Vector(0.0, 0.0, sea_level_z_cm), unreal.Rotator(0, 0, 0))
water.set_actor_label("Ocean")
water.set_folder_path(FOLDER)
water_mesh_comp = water.get_component_by_class(unreal.StaticMeshComponent)
# /Engine/BasicShapes/Plane is 100x100 uu (1x1 m) - scale to the bbox's
# real-world half-extents (scale = size in cm / 100).
water_mesh_comp.set_static_mesh(unreal.load_asset("/Engine/BasicShapes/Plane"))
water.set_actor_scale3d(unreal.Vector(half_width_cm / 50.0, half_height_cm / 50.0, 1.0))
if water_mat:
    water_mesh_comp.set_material(0, water_mat)
water_mesh_comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
log(f"spawned Ocean water plane at z={sea_level_z_cm:.0f}cm "
    f"({half_width_cm/100.0*2:.0f}m x {half_height_cm/100.0*2:.0f}m, "
    f"material={'M_WaterStylized' if water_mat else 'MISSING - flat default gray'})")

# ---------------------------------------------------------------- save
if not lls.save_current_level():
    log("WARNING: save_current_level returned False")
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - level saved")
