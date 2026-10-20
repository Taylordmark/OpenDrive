"""Headless, Realistic only (PLAN-VISUALS.md Track K): build
M_GroundCover_real and import the two ground cover card meshes into
/Game/OpenDrive/Realistic/GroundCover/. Adds new assets only; nothing
Stylized (or Track F's M_Foliage_real) is touched.

M_GroundCover_real: masked two-sided foliage on T_FoliageAtlas_real (Track F,
generated) with
  - per-instance custom data 0 = dryness (ODGroundCover, by biome): lush
    atlas colour -> straw keeping the texture's light/dark;
  - root shade (no shadows on ground cover) from the card's UV height;
  - distance fade: PerInstanceFadeAmount vs PerInstanceRandom drops cards
    out over each HISM's cull band, so a cell thins instead of popping;
  - SnowCover / Wetness scalars (ODForestManager copies them in from the
    foliage season MID): snow buries most cards and leaves the rest as
    frosted straw (no white subsurface glow);
  - root shade eases off past ~15 m so the field doesn't read as dots;
  - tip flutter by world position offset from the Time node (frame-rate
    independent), off past WpoDisableCm.

Run pipeline/make_ground_cover_meshes.py first, then:
UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

OBJ = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon" / "obj"
DIR = "/Game/OpenDrive/Realistic/GroundCover"
ATLAS = "/Game/OpenDrive/Realistic/Flora/T_FoliageAtlas_real"
MESHES = {"SM_GCTallGrass_real": "gc_tall_grass_real.obj", "SM_GCWeed_real": "gc_weed_real.obj"}
WIND_CM = 5.0

MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"GROUNDCOVER {msg}")


# ------------------------------------------------------------------ material
path = f"{DIR}/M_GroundCover_real"
m = unreal.load_asset(path) if EAL.does_asset_exist(path) else None
if m is not None:
    for e in list(MEL.get_material_expressions(m)):   # not delete_all_material_expressions (UE 5.8 bug)
        MEL.delete_material_expression(m, e)
else:
    m = tools.create_asset("M_GroundCover_real", DIR, unreal.Material, unreal.MaterialFactoryNew())
if m is None:
    raise RuntimeError(f"could not create {path}")
m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
m.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE)
m.set_editor_property("two_sided", True)
m.set_editor_property("opacity_mask_clip_value", 0.35)
m.set_editor_property("used_with_instanced_static_meshes", True)
m.set_editor_property("max_world_position_offset_displacement", WIND_CM * 1.1)

_y = [0]


def node(cls, *inputs, **props):
    """New expression; inputs are (expr, out_pin) or expr, wired to the
    node's pins in order (A, B, Alpha for 3-input lerp; '' for one input)."""
    _y[0] += 110
    n = MEL.create_material_expression(m, cls, -600, _y[0])
    for k, v in props.items():
        n.set_editor_property(k, v)
    pins = {0: [], 1: [""], 2: ["A", "B"], 3: ["A", "B", "Alpha"]}[len(inputs)]
    for src, pin in zip(inputs, pins):
        e, out = src if isinstance(src, tuple) else (src, "")
        if not MEL.connect_material_expressions(e, out, n, pin):
            raise RuntimeError(f"connect {e.get_class().get_name()}.{out!r} -> {cls.__name__}.{pin!r}")
    return n


def c1(v):
    return node(unreal.MaterialExpressionConstant, r=v)


def c3(r, g, b):
    return node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(r, g, b, 1.0))


def mul(a, b): return node(unreal.MaterialExpressionMultiply, a, b)
def add(a, b): return node(unreal.MaterialExpressionAdd, a, b)
def sub(a, b): return node(unreal.MaterialExpressionSubtract, a, b)
def lerp(a, b, t): return node(unreal.MaterialExpressionLinearInterpolate, a, b, t)
def sat(a): return node(unreal.MaterialExpressionClamp, a, min_default=0.0, max_default=1.0)


def param(name):
    return node(unreal.MaterialExpressionScalarParameter, parameter_name=name, default_value=0.0)


def out(e, prop):
    if not MEL.connect_material_property(e, "", prop):
        raise RuntimeError(f"connect -> {prop}")


atlas = unreal.load_asset(ATLAS)
if atlas is None:
    raise RuntimeError(f"{ATLAS} missing - run build_flora_materials.py with OD_FLORA_REAL=1 first")
tex = node(unreal.MaterialExpressionTextureSample, texture=atlas)
pir = node(unreal.MaterialExpressionPerInstanceRandom)
dry = node(unreal.MaterialExpressionPerInstanceCustomData, data_index=0)   # unconnected default = 0 (lush)
uv = node(unreal.MaterialExpressionTextureCoordinate)
# atlas cells are 0.5 V tall and UE V = 1 - OBJ v: frac(2V) is 0.92 at a
# card's base and 0.08 at its tip (same as M_Foliage_real's flutter)
tip = sat(node(unreal.MaterialExpressionDivide,
               sub(c1(0.92), node(unreal.MaterialExpressionFrac, mul(node(unreal.MaterialExpressionComponentMask, uv, r=False, g=True), c1(2.0)))),
               c1(0.6)))

lum = node(unreal.MaterialExpressionDotProduct, (tex, "RGB"), c3(0.3, 0.59, 0.11))
straw = mul(lum, c3(2.3, 1.9, 1.15))
col = lerp((tex, "RGB"), straw, dry)
col = mul(col, lerp(c3(0.86, 0.88, 0.82), c3(1.08, 1.06, 1.0), pir))     # per-clump tint
# root shade, eased off past ~15 m: dark-rooted cards repeated across the
# ground read as a dot pattern there (terrain survey 2026-10-16, gap 5)
depth = node(unreal.MaterialExpressionPixelDepth)
far = sat(mul(sub(depth, c1(1500.0)), c1(1.0 / 2500.0)))
col = mul(col, lerp(lerp(c1(0.5), c1(0.85), far), c1(1.0), tip))
wet = param("Wetness")
col = mul(col, lerp(c1(1.0), c1(0.7), wet))
# snow: most cards are buried (opacity below); the ones still standing are
# dead straw with a little frost on the tips - not glowing white cards
cover = param("SnowCover")
snow = sat(mul(mul(cover, lerp(c1(0.82), c1(1.0), pir)), c1(1.45)))
col = lerp(col, mul(straw, c1(0.8)), snow)
col = lerp(col, c3(0.62, 0.64, 0.68), mul(mul(snow, tip), c1(0.35)))
out(col, unreal.MaterialProperty.MP_BASE_COLOR)
out(mul(col, mul(c1(0.75), sub(c1(1.0), mul(snow, c1(0.7))))), unreal.MaterialProperty.MP_SUBSURFACE_COLOR)
out(lerp(c1(0.85), c1(0.45), wet), unreal.MaterialProperty.MP_ROUGHNESS)

fade = node(unreal.MaterialExpressionPerInstanceFadeAmount)
buried = sat(mul(sub(sub(mul(cover, c1(1.1)), c1(0.35)), mul(pir, c1(0.8))), c1(4.0)))
keep = mul(sat(mul(sub(fade, pir), c1(8.0))), sub(c1(1.0), buried))
out(mul((tex, "A"), keep), unreal.MaterialProperty.MP_OPACITY_MASK)

t = node(unreal.MaterialExpressionTime)
wxy = node(unreal.MaterialExpressionComponentMask, node(unreal.MaterialExpressionWorldPosition), r=True, g=True)
phase = add(pir, node(unreal.MaterialExpressionDotProduct, wxy,
                      node(unreal.MaterialExpressionConstant2Vector, r=0.0011, g=0.0007)))
wave = add(c1(0.4), mul(c1(0.6), node(unreal.MaterialExpressionSine, add(mul(t, c1(0.6)), phase))))
out(mul(c3(0.86 * WIND_CM, 0.45 * WIND_CM, 0.0), mul(tip, wave)), unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)

MEL.recompile_material(m)
EAL.save_asset(path, only_if_is_dirty=False)
params = sorted(str(n) for n in MEL.get_scalar_parameter_names(m))
if params != ["SnowCover", "Wetness"]:
    raise RuntimeError(f"M_GroundCover_real params {params}")
log(f"built {path}, {len(MEL.get_material_expressions(m))} nodes, params {params}")

# -------------------------------------------------------------------- meshes
tasks = []
for name, obj in MESHES.items():
    if not (OBJ / obj).exists():
        raise RuntimeError(f"{OBJ / obj} missing - run pipeline/make_ground_cover_meshes.py")
    ti = unreal.AssetImportTask()
    ti.filename = str(OBJ / obj)
    ti.destination_path = DIR
    ti.destination_name = name
    ti.automated = True
    ti.save = False
    ti.replace_existing = True
    tasks.append(ti)
tools.import_asset_tasks(tasks)
for name in MESHES:
    mesh = unreal.load_asset(f"{DIR}/{name}")
    if mesh is None:
        raise RuntimeError(f"{name} import failed")
    if mesh.get_num_sections(0) != 1:
        raise RuntimeError(f"{name}: {mesh.get_num_sections(0)} sections, want 1")
    mesh.set_material(0, m)
    ns = mesh.get_editor_property("nanite_settings")
    if ns.enabled:   # PerInstanceFadeAmount and the HISM cull band need the classic path
        ns.enabled = False
        mesh.set_editor_property("nanite_settings", ns)
    box = mesh.get_bounding_box()
    EAL.save_asset(f"{DIR}/{name}", only_if_is_dirty=False)
    log(f"{name}: {mesh.get_num_triangles(0)} tris, z {box.min.z:.1f}..{box.max.z:.1f} cm, mat {mesh.get_material(0).get_name()}, "
        f"nanite {mesh.get_editor_property('nanite_settings').enabled}")

# reused assets ODGroundCover loads (read only): pebbles must be the
# non-Nanite Standard twin, and every material must take HISM instances
for path, want_nanite in (("/Game/OpenDrive/Rocks/SM_RockBoulderA_real", False),
                          ("/Game/OpenDrive/Trees/bush_real", None), ("/Game/OpenDrive/Trees/sagebrush_real", None)):
    mesh = unreal.load_asset(path)
    if mesh is None:
        raise RuntimeError(f"{path} missing")
    nanite = mesh.get_editor_property("nanite_settings").enabled
    if want_nanite is not None and nanite != want_nanite:
        raise RuntimeError(f"{path}: nanite {nanite}")
    log(f"reuse {path}: {mesh.get_num_triangles(0)} tris, nanite {nanite}")
for path in ("/Game/OpenDrive/Materials/M_Rock030_real", "/Game/OpenDrive/Materials/M_Foliage_real"):
    mat = unreal.load_asset(path)
    if mat is None or not mat.get_editor_property("used_with_instanced_static_meshes"):
        raise RuntimeError(f"{path} missing or not flagged for instanced meshes")

log("assets in " + DIR + ": " + ", ".join(sorted(a.split(".")[-1] for a in EAL.list_assets(DIR, recursive=True))))
log("DONE")
