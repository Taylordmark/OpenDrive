"""Headless Arc 2+3 apply: narrow-carve heightmap re-import, wet-look road,
day/night controller, race manager (checkpoints + HUD + respawn).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -AllowCommandletRendering -unattended -nosplash
    -nop4 -stdout

NOTE: -AllowCommandletRendering (and no -nullrhi) is required — the
heightmap update draws through a render target; commandlets otherwise run
with a null RHI and the RT never gets a resource.
"""

import json
import math
from pathlib import Path

import unreal

STAGE = "tail_of_the_dragon"
STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / STAGE
TEX_DIR = "/Game/OpenDrive/Textures"
MAT_DIR = "/Game/OpenDrive/Materials"
ROAD_DIR = "/Game/OpenDrive/Road"
LEVEL = "/Game/TailOfTheDragon"
GATE_EVERY_STATIONS = 60          # 5 m stations -> gate every 300 m

meta = json.loads((STAGE_DIR / "stage_meta.json").read_text())
climate = json.loads((STAGE_DIR / "climate.json").read_text())
moisture = climate["road_moisture"]

tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"ARC23 {msg}")


# ---------------------------------------------------------------- textures
def import_texture(src_path, srgb=True, nearest=False):
    t = unreal.AssetImportTask()
    t.filename = str(src_path)
    t.destination_path = TEX_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tools.import_asset_tasks([t])
    asset = unreal.load_asset(f"{TEX_DIR}/{src_path.stem}")
    if asset is None:
        raise RuntimeError(f"texture import failed: {src_path.name}")
    if not srgb:
        asset.set_editor_property("srgb", False)
    if nearest:
        asset.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
        asset.set_editor_property(
            "mip_gen_settings", unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
        asset.set_editor_property(
            "compression_settings",
            unreal.TextureCompressionSettings.TC_VECTOR_DISPLACEMENTMAP)
    log(f"imported {src_path.name}")
    return asset


tex_road = import_texture(STAGE_DIR / "textures" / "T_Road.png")

# ------------------------------------------------- M_Road (wet-climate gloss)
# moisture already darkens the albedo texture upstream; here it lowers
# roughness so wet-climate pavement reads slightly glossy (wishlist #1)
road_mat_path = f"{MAT_DIR}/M_Road"
if unreal.EditorAssetLibrary.does_asset_exist(road_mat_path):
    unreal.EditorAssetLibrary.delete_asset(road_mat_path)
m_road = tools.create_asset("M_Road", MAT_DIR, unreal.Material,
                            unreal.MaterialFactoryNew())
ts = MEL.create_material_expression(
    m_road, unreal.MaterialExpressionTextureSample, -600, 0)
ts.set_editor_property("texture", tex_road)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
rough = MEL.create_material_expression(
    m_road, unreal.MaterialExpressionConstant, -600, 300)
rough.set_editor_property("r", round(0.9 - 0.35 * moisture, 3))
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.recompile_material(m_road)

n = 0
for ap in unreal.EditorAssetLibrary.list_assets(ROAD_DIR):
    a = unreal.load_asset(ap)
    if isinstance(a, unreal.StaticMesh):
        a.set_material(0, m_road)
        n += 1
log(f"rebuilt M_Road (roughness {0.9 - 0.35 * moisture:.2f}, "
    f"moisture {moisture}) -> {n} road meshes")

# ------------------------------------------------ M_Ghost (best-run replay)
ghost_path = f"{MAT_DIR}/M_Ghost"
if not unreal.EditorAssetLibrary.does_asset_exist(ghost_path):
    m_ghost = tools.create_asset("M_Ghost", MAT_DIR, unreal.Material,
                                 unreal.MaterialFactoryNew())
    m_ghost.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    m_ghost.set_editor_property("shading_model",
                                unreal.MaterialShadingModel.MSM_UNLIT)
    m_ghost.set_editor_property("two_sided", True)
    em = MEL.create_material_expression(
        m_ghost, unreal.MaterialExpressionConstant3Vector, -500, 0)
    em.set_editor_property("constant", unreal.LinearColor(0.25, 0.7, 1.0, 1.0))
    MEL.connect_material_property(em, "",
                                  unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    op = MEL.create_material_expression(
        m_ghost, unreal.MaterialExpressionConstant, -500, 300)
    op.set_editor_property("r", 0.3)
    MEL.connect_material_property(op, "", unreal.MaterialProperty.MP_OPACITY)
    MEL.recompile_material(m_ghost)
    log("built M_Ghost (translucent unlit cyan)")

# ---------------------------------------------------------------- level
lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()

# ------------------------------------------- heightmap (1.3b cut-wall carve)
# heightmap_rg.png is written by the pipeline as R=hi/G=lo bytes exactly for
# LandscapeImportHeightmapFromRenderTarget(import_height_from_rg_channel=True)
ls_meta = meta["landscape"]
res = ls_meta["resolution"]
tex_hm = import_texture(STAGE_DIR / "heightmap_rg.png", srgb=False, nearest=True)

if unreal.EditorAssetLibrary.does_asset_exist(f"{MAT_DIR}/M_HeightmapBlit"):
    unreal.EditorAssetLibrary.delete_asset(f"{MAT_DIR}/M_HeightmapBlit")
m_hm = tools.create_asset("M_HeightmapBlit", MAT_DIR, unreal.Material,
                          unreal.MaterialFactoryNew())
hs = MEL.create_material_expression(
    m_hm, unreal.MaterialExpressionTextureSample, -400, 0)
hs.set_editor_property("texture", tex_hm)
hs.set_editor_property("sampler_type",
                       unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)
MEL.connect_material_property(hs, "RGB",
                              unreal.MaterialProperty.MP_EMISSIVE_COLOR)
MEL.recompile_material(m_hm)

rt = unreal.RenderingLibrary.create_render_target2d(
    world, res, res, unreal.TextureRenderTargetFormat.RTF_RGBA8)
unreal.RenderingLibrary.clear_render_target2d(world, rt)
unreal.RenderingLibrary.draw_material_to_render_target(world, rt, m_hm)

n_ls = 0
for a in actor_sub.get_all_level_actors():
    cls = a.get_class().get_name()
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        ok = a.landscape_import_heightmap_from_render_target(rt, True)
        # new carve shifts the height range -> transform must follow stage_meta
        a.set_actor_location(unreal.Vector(*ls_meta["location_cm"]), False, False)
        a.set_actor_scale3d(unreal.Vector(*ls_meta["scale"]))
        log(f"heightmap re-import on '{a.get_actor_label()}': {ok}; "
            f"transform synced to stage_meta")
        n_ls += 1
if not n_ls:
    raise RuntimeError("no Landscape actor found for heightmap re-import")

# ------------------------------------------ checkpoints from road centerline
road = json.loads((STAGE_DIR / "road_mesh.json").read_text())
stations = {}
for chunk in road["chunks"]:
    c0 = int(chunk["name"].split("_")[1])
    v = chunk["vertices"]
    for r in range(len(v) // 12):          # 4 verts * xyz per station row
        b = (r * 4 + 1) * 3                # verts 1,2 = pavement edges
        stations[c0 + r] = ((v[b] + v[b + 3]) / 2.0,
                            (v[b + 1] + v[b + 4]) / 2.0,
                            (v[b + 2] + v[b + 5]) / 2.0)
pts = [stations[i] for i in sorted(stations)]
gate_idx = list(range(0, len(pts) - 1, GATE_EVERY_STATIONS)) + [len(pts) - 1]
gates = []
for gi in gate_idx:
    x, y, z = pts[gi]
    nx, ny, _ = pts[gi + 1] if gi + 1 < len(pts) else pts[gi - 1]
    sign = 1.0 if gi + 1 < len(pts) else -1.0
    yaw = math.degrees(math.atan2(sign * (ny - y), sign * (nx - x)))
    gates.append(unreal.Transform(
        location=unreal.Vector(x, y, z),
        rotation=unreal.Rotator(0.0, 0.0, yaw),
        scale=unreal.Vector(1, 1, 1)))
log(f"built {len(gates)} checkpoint gates from {len(pts)} stations")

# ------------------------------------------------------------ manager actors
labels = {a.get_actor_label(): a for a in actor_sub.get_all_level_actors()}

if "DayNight" not in labels:
    a = actor_sub.spawn_actor_from_class(
        unreal.ODDayNightController, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    a.set_actor_label("DayNight")
    a.set_folder_path("TailOfTheDragon")
    log("spawned DayNight controller (1h cycle, start 10:00)")

race = labels.get("RaceManager")
if race is None:
    race = actor_sub.spawn_actor_from_class(
        unreal.ODRaceManager, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    race.set_actor_label("RaceManager")
    race.set_folder_path("TailOfTheDragon")
race.set_editor_property("checkpoints", gates)
log(f"RaceManager checkpoints set ({len(gates)} gates)")

# ------------------------------------------------------------------ HUD
gm_path = "/Game/VehicleTemplate/Blueprints/BP_VehicleAdvGameMode"
gm = unreal.load_asset(gm_path)
if gm:
    cdo = unreal.get_default_object(gm.generated_class())
    cdo.set_editor_property("hud_class", unreal.ODRaceHUD.static_class())
    unreal.EditorAssetLibrary.save_asset(gm_path)
    log("game mode HUD class -> ODRaceHUD")
else:
    log("WARNING game mode BP not found; HUD not wired")

# ------------------------------------------------- night auto-exposure range
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == "Grade":
        s = a.get_editor_property("settings")
        try:
            s.set_editor_property("override_auto_exposure_min_brightness", True)
            s.set_editor_property("auto_exposure_min_brightness", -3.0)
            s.set_editor_property("override_auto_exposure_max_brightness", True)
            s.set_editor_property("auto_exposure_max_brightness", 16.0)
            a.set_editor_property("settings", s)
            log("auto-exposure range widened for night (EV100 -3..16)")
        except Exception as exc:
            log(f"WARNING exposure range failed: {exc}")

# ---------------------------------------------------------------- save
unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
