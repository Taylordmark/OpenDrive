"""Headless: import textures, build road + distance-blended terrain materials,
assign, save.  v2: satellite drape far, mask-weighted detail textures near.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import json
from pathlib import Path

import unreal

STAGE = "tail_of_the_dragon"
STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / STAGE
TEX_DIR = "/Game/OpenDrive/Textures"
MAT_DIR = "/Game/OpenDrive/Materials"
ROAD_DIR = "/Game/OpenDrive/Road"
LEVEL = "/Game/TailOfTheDragon"

DETAIL_TILE_CM = 400.0        # detail texture repeats every 4 m
BLEND_NEAR_CM = 7500.0        # full detail inside 75 m
BLEND_RANGE_CM = 17500.0      # fully satellite by 250 m

meta = json.loads((STAGE_DIR / "stage_meta.json").read_text())
ls = meta["landscape"]
corner = ls["location_cm"]
side_cm = (ls["resolution"] - 1) * ls["scale"][0]

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"VIS {msg}")


# ---------------------------------------------------------------- textures
def import_texture(png_name, srgb=True, normal=False):
    t = unreal.AssetImportTask()
    t.filename = str(STAGE_DIR / "textures" / png_name)
    t.destination_path = TEX_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tools.import_asset_tasks([t])
    asset = unreal.load_asset(f"{TEX_DIR}/{png_name[:-4]}")
    if asset is None:
        raise RuntimeError(f"texture import failed: {png_name}")
    if normal:
        asset.set_editor_property("compression_settings",
                                  unreal.TextureCompressionSettings.TC_NORMALMAP)
        asset.set_editor_property("srgb", False)
    elif not srgb:
        asset.set_editor_property("srgb", False)
    log(f"imported {png_name}")
    return asset


tex_road = import_texture("T_Road.png")
tex_sat = import_texture("T_Terrain.png")
tex_mask = import_texture("T_TerrainMask.png", srgb=False)
tex_det = {k: import_texture(f"T_Detail{k}.png") for k in ("Forest", "Grass", "Rock")}
tex_det_n = {k: import_texture(f"T_Detail{k}_N.png", normal=True)
             for k in ("Forest", "Grass", "Rock")}
tex_palette = import_texture("T_TreePalette.png")

# stylized foliage meshes (the only stored foliage data - placement is runtime)
TREE_DIR = "/Game/OpenDrive/Trees"
FOL_NAMES = ("tree_blob", "tree_olive", "tree_conifer", "grass_tuft", "bush")
fol_tasks = []
for name in FOL_NAMES:
    ti = unreal.AssetImportTask()
    ti.filename = str(STAGE_DIR / "obj" / f"{name}.obj")
    ti.destination_path = TREE_DIR
    ti.automated = True
    ti.save = False
    ti.replace_existing = True
    fol_tasks.append(ti)
tools.import_asset_tasks(fol_tasks)
fol = {n: unreal.load_asset(f"{TREE_DIR}/{n}") for n in FOL_NAMES}
if any(m is None for m in fol.values()):
    raise RuntimeError("foliage mesh import failed")
log(f"imported {len(fol)} foliage meshes")


# ---------------------------------------------------------------- helpers
def new_material(name):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    return tools.create_asset(name, MAT_DIR, unreal.Material,
                              unreal.MaterialFactoryNew())


class Graph:
    """Tiny helper around MaterialEditingLibrary node soup."""

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 220
        n = MEL.create_material_expression(self.mat, cls,
                                           -1400 + (self._y % 1100), self._y // 4)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def const2(self, x, y):
        return self.node(unreal.MaterialExpressionConstant2Vector, r=x, g=y)

    def const3(self, x, y, z):
        return self.node(unreal.MaterialExpressionConstant3Vector,
                         constant=unreal.LinearColor(x, y, z, 1.0))

    def op(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        MEL.connect_material_expressions(a, a_out, n, "A")
        MEL.connect_material_expressions(b, b_out, n, "B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def div(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionDivide, a, b, a_out, b_out)

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        MEL.connect_material_expressions(a, a_out, n, "A")
        MEL.connect_material_expressions(b, b_out, n, "B")
        MEL.connect_material_expressions(alpha, al_out, n, "Alpha")
        return n

    def sample(self, tex, uv=None, uv_out="", sampler=None):
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex)
        if sampler is not None:
            n.set_editor_property("sampler_type", sampler)
        if uv is not None:
            for pin in ("UVs", "Coordinates"):
                if MEL.connect_material_expressions(uv, uv_out, n, pin):
                    break
        return n


# ---------------------------------------------------------------- M_Road
m_road = new_material("M_Road")
g = Graph(m_road)
ts = g.sample(tex_road)
MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(g.const(0.9), "",
                              unreal.MaterialProperty.MP_ROUGHNESS)
MEL.recompile_material(m_road)
log("built M_Road")

# ---------------------------------------------------------------- M_Foliage
# stylized solid low-poly, palette-colored; masked only to allow per-instance
# dither fade at the cull distance (kills pop-in)
m_fol = new_material("M_Foliage")
m_fol.set_editor_property("used_with_instanced_static_meshes", True)
g = Graph(m_fol)
ta = g.sample(tex_palette)
MEL.connect_material_property(ta, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(g.const(0.85), "",
                              unreal.MaterialProperty.MP_ROUGHNESS)
try:
    fade = g.node(unreal.MaterialExpressionPerInstanceFadeAmount)
    dither_fn = unreal.load_asset(
        "/Engine/Functions/Engine_MaterialFunctions02/Utility/DitherTemporalAA")
    if dither_fn:
        call = g.node(unreal.MaterialExpressionMaterialFunctionCall)
        call.set_editor_property("material_function", dither_fn)
        connected = False
        for pin in ("AlphaThreshold", "Alpha Threshold", ""):
            if MEL.connect_material_expressions(fade, "", call, pin):
                connected = True
                break
        if connected:
            m_fol.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
            MEL.connect_material_property(
                call, "", unreal.MaterialProperty.MP_OPACITY_MASK)
            log("foliage dither-fade wired")
        else:
            log("WARNING dither function pin not found; foliage stays opaque")
    else:
        log("WARNING DitherTemporalAA function not found; foliage stays opaque")
except Exception as exc:
    log(f"WARNING foliage fade skipped: {exc}")
MEL.recompile_material(m_fol)
for m in fol.values():
    m.set_material(0, m_fol)
log("built M_Foliage")


# ---------------------------------------------------------------- grass type
def set_prop(obj, name, value, wrappers=()):
    for wrap in (*wrappers, None):
        try:
            obj.set_editor_property(name, wrap(value) if wrap else value)
            return True
        except Exception:
            continue
    log(f"WARNING could not set {name}={value}")
    return False


def make_variety(mesh, density, cull=(40000, 50000), scale=(0.75, 1.4)):
    v = unreal.GrassVariety()
    set_prop(v, "grass_mesh", mesh)
    set_prop(v, "grass_density", density, (unreal.PerPlatformFloat,))
    set_prop(v, "start_cull_distance", cull[0], (unreal.PerPlatformInt,))
    set_prop(v, "end_cull_distance", cull[1], (unreal.PerPlatformInt,))
    set_prop(v, "random_rotation", True)
    set_prop(v, "align_to_surface", False)
    set_prop(v, "placement_jitter", 1.0)
    try:
        v.set_editor_property("scale_x", unreal.FloatInterval(*scale))
    except Exception:
        try:
            iv = unreal.FloatInterval()
            iv.set_editor_property("min", scale[0])
            iv.set_editor_property("max", scale[1])
            v.set_editor_property("scale_x", iv)
        except Exception:
            log("WARNING could not set scale interval")
    return v


def make_grass_type(name, varieties):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    try:
        gt = tools.create_asset(name, MAT_DIR, unreal.LandscapeGrassType,
                                unreal.LandscapeGrassTypeFactory())
    except Exception as exc:
        log(f"WARNING grass type {name} failed: {exc}")
        return None
    gt.set_editor_property("grass_varieties", varieties)
    log(f"built {name} grass type")
    return gt


# wide start->end cull bands: instances scale/fade in gradually over ~350 m
# instead of popping at a hard draw distance
gt_forest = make_grass_type("GT_Forest", [
    make_variety(fol["tree_blob"], 0.16, cull=(25000, 60000)),
    make_variety(fol["tree_olive"], 0.10, cull=(25000, 60000)),
    make_variety(fol["tree_conifer"], 0.08, cull=(25000, 60000)),
])
gt_ground = make_grass_type("GT_Ground", [
    make_variety(fol["grass_tuft"], 10.0, cull=(5000, 8000), scale=(0.8, 1.6)),
    make_variety(fol["bush"], 1.2, cull=(14000, 20000), scale=(0.8, 1.8)),
])

# ---------------------------------------------------------------- M_Terrain
m_ter = new_material("M_Terrain")
g = Graph(m_ter)

wp = g.node(unreal.MaterialExpressionWorldPosition)
xy = g.node(unreal.MaterialExpressionComponentMask, r=True, g=True,
            b=False, a=False)
MEL.connect_material_expressions(wp, "", xy, "")

uv_macro = g.div(g.sub(xy, g.const2(corner[0], corner[1])), g.const(side_cm))
uv_detail = g.div(xy, g.const(DETAIL_TILE_CM))

macro = g.sample(tex_sat, uv_macro)
mask = g.sample(tex_mask, uv_macro,
                sampler=unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_COLOR)

# mask-weighted detail albedo: F*R + G*G + Rock*B (RGB weights sum to 1)
d_f = g.sample(tex_det["Forest"], uv_detail)
d_g = g.sample(tex_det["Grass"], uv_detail)
d_r = g.sample(tex_det["Rock"], uv_detail)
det = g.add(g.add(g.mul(d_f, mask, "RGB", "R"), g.mul(d_g, mask, "RGB", "G")),
            g.mul(d_r, mask, "RGB", "B"))

# near color = satellite tint * detail * 2 (photo-modulated detail)
near = g.mul(g.mul(macro, det, "RGB"), g.const(2.0))

# distance factor: 0 near -> 1 far
depth = g.node(unreal.MaterialExpressionPixelDepth)
t_far = g.node(unreal.MaterialExpressionSaturate)
MEL.connect_material_expressions(
    g.div(g.sub(depth, g.const(BLEND_NEAR_CM)), g.const(BLEND_RANGE_CM)), "",
    t_far, "")

blended = g.lerp(near, macro, t_far, b_out="RGB")

# water: darken + smooth wherever mask.A says so
water_amt = g.mul(mask, g.const(0.85), "A")
final_col = g.lerp(blended, g.const3(0.02, 0.04, 0.05), water_amt)
MEL.connect_material_property(final_col, "",
                              unreal.MaterialProperty.MP_BASE_COLOR)

rough = g.sub(g.const(0.85), g.mul(mask, g.const(0.7), "A"))
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

# normals: weighted detail normals, fading flat with distance
n_f = g.sample(tex_det_n["Forest"], uv_detail,
               sampler=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
n_g = g.sample(tex_det_n["Grass"], uv_detail,
               sampler=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
n_r = g.sample(tex_det_n["Rock"], uv_detail,
               sampler=unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
det_n = g.add(g.add(g.mul(n_f, mask, "RGB", "R"), g.mul(n_g, mask, "RGB", "G")),
              g.mul(n_r, mask, "RGB", "B"))
final_n = g.lerp(det_n, g.const3(0.0, 0.0, 1.0), t_far)
MEL.connect_material_property(final_n, "", unreal.MaterialProperty.MP_NORMAL)

# runtime foliage: LandscapeGrassOutput; trees from forest weight, ground
# cover from grass weight + thinned forest weight (all road-carved upstream)
if gt_forest and gt_ground:
    try:
        grass_out = g.node(unreal.MaterialExpressionLandscapeGrassOutput)
        inputs = []
        for nm, gt in (("Forest", gt_forest), ("Ground", gt_ground)):
            gi = unreal.GrassInput()
            gi.set_editor_property("name", nm)
            gi.set_editor_property("grass_type", gt)
            inputs.append(gi)
        grass_out.set_editor_property("grass_types", inputs)
        ground_w = g.node(unreal.MaterialExpressionSaturate)
        MEL.connect_material_expressions(
            g.add(g.mul(mask, g.const(0.7), "R"), mask, "", "G"), "",
            ground_w, "")
        ok1 = MEL.connect_material_expressions(mask, "R", grass_out, "Forest")
        ok2 = MEL.connect_material_expressions(ground_w, "", grass_out, "Ground")
        log(f"wired LandscapeGrassOutput (trees={ok1} ground={ok2})")
    except Exception as exc:
        log(f"WARNING grass output failed: {exc}")

MEL.recompile_material(m_ter)
log("built M_Terrain (distance-blended)")

# ---------------------------------------------------------------- assign
count = 0
for ap in unreal.EditorAssetLibrary.list_assets(ROAD_DIR):
    a = unreal.load_asset(ap)
    if isinstance(a, unreal.StaticMesh):
        a.set_material(0, m_road)
        count += 1
log(f"assigned M_Road to {count} road meshes")

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
n_ls = 0
for a in actor_sub.get_all_level_actors():
    cls = a.get_class().get_name()
    if "Landscape" in cls and "Streaming" not in cls and "Gizmo" not in cls:
        a.set_editor_property("landscape_material", m_ter)
        n_ls += 1
log(f"assigned M_Terrain to {n_ls} landscape actor(s)")

# deepen distance haze slightly - softens the far tree fade-in band
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == "Fog":
        fc = a.get_component_by_class(unreal.ExponentialHeightFogComponent)
        if fc:
            fc.set_editor_property("fog_density", 0.008)
            log("fog deepened for distance softening")

# stylized color grade (idempotent by label)
labels = {a.get_actor_label() for a in actor_sub.get_all_level_actors()}
if "Grade" not in labels:
    ppv = actor_sub.spawn_actor_from_class(
        unreal.PostProcessVolume, unreal.Vector(0, 0, 0), unreal.Rotator(0, 0, 0))
    ppv.set_editor_property("unbound", True)
    ppv.set_actor_label("Grade")
    ppv.set_folder_path("TailOfTheDragon")
    s = ppv.get_editor_property("settings")
    try:
        s.set_editor_property("override_color_saturation", True)
        s.set_editor_property("color_saturation",
                              unreal.Vector4(1.22, 1.22, 1.22, 1.0))
        s.set_editor_property("override_color_contrast", True)
        s.set_editor_property("color_contrast",
                              unreal.Vector4(1.06, 1.06, 1.06, 1.0))
        ppv.set_editor_property("settings", s)
        log("spawned Grade post-process volume")
    except Exception as exc:
        log(f"WARNING grade settings failed: {exc}")

# ---------------------------------------------------------------- save
unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
