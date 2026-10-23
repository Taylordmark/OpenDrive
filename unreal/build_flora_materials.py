"""Headless: build the four Phase 3b flora materials -
M_Bark_Conifer / M_Bark_Aspen / M_Bark_Willow (opaque, textured bark) and
M_Foliage (alpha-cutout masked, Two Sided) - so unreal/import_biome_flora.py
has them ready to assign to the card-based tree meshes. Run this BEFORE that
script; see its module docstring for the full mesh-slot contract.

Imports its own copy of the four flora textures (same STAGE_DIR/textures
source as import_biome_flora.py, same replace_existing=True idempotent
pattern) so this script is self-sufficient and can run standalone or first
in the pipeline - import_biome_flora.py re-importing them afterward is a
no-op in content terms.

M_Foliage MUST be Two Sided: make_tree_meshes.py's crossed foliage cards have
arbitrary winding relative to the camera by construction, so backface
culling would punch holes in every crown from the "wrong" side. It samples
T_FoliageAtlas's RGB for BaseColor and its alpha channel for the opacity
mask - MaterialExpressionTextureSample's alpha output pin is named "A"
(connect_material_expressions returns False silently on a bad pin name, so
this script checks the return value and raises loudly instead of silently
leaving the mask disconnected and the material fully opaque).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout

Realistic twins (PLAN-VISUALS.md Track F): set OD_FLORA_REAL=1 to build
M_Bark_Conifer_real / M_Bark_Aspen_real / M_Foliage_real instead, from the
build_flora_textures.py --real textures (imported to /Game/OpenDrive/
Realistic/Flora). Same graph and parameter names as the stylized twin plus:
bark normal maps and luminance roughness, per-instance foliage tint, leaf-
coloured subsurface, and fall colour that keeps the leaf texture. The
stylized path is unchanged with the variable unset, and the real path
fails if a twin's scalar parameter list differs (the interface rule).
"""

import os
import sys
from pathlib import Path

import unreal

STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
TEX_DIR = "/Game/OpenDrive/Textures"
REAL = os.environ.get("OD_FLORA_REAL") == "1"
SFX = "_real" if REAL else ""
if REAL:
    TEX_DIR = "/Game/OpenDrive/Realistic/Flora"
MAT_DIR = "/Game/OpenDrive/Materials"

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"FLORAMAT {msg}")


# ------------------------------------------------------------------ textures
TEXTURES = ("T_BarkConifer.png", "T_BarkAspen.png", "T_BarkWillow.png",
            "T_FoliageAtlas.png")
if REAL:
    TEXTURES = ("T_BarkConifer_real.png", "T_BarkConifer_real_N.png", "T_BarkAspen_real.png",
                "T_BarkAspen_real_N.png", "T_BarkWillow_real.png", "T_BarkWillow_real_N.png",
                "T_FoliageAtlas_real.png")
tex_tasks = []
for fname in TEXTURES:
    ti = unreal.AssetImportTask()
    ti.filename = str(STAGE_DIR / "textures" / fname)
    ti.destination_path = TEX_DIR
    ti.automated = True
    ti.save = False
    ti.replace_existing = True
    tex_tasks.append(ti)
tools.import_asset_tasks(tex_tasks)

tex = {}
for fname in TEXTURES:
    asset_name = fname.rsplit(".", 1)[0]
    t = unreal.load_asset(f"{TEX_DIR}/{asset_name}")
    if t is None:
        raise RuntimeError(f"{asset_name} import failed - run "
                            f"pipeline/build_flora_textures.py first")
    tex[asset_name] = t
if REAL:
    for name, t in tex.items():
        if name.endswith("_N"):
            t.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
            t.set_editor_property("srgb", False)
        elif name.startswith("T_FoliageAtlas"):
            # keep cutout coverage in the mips so distant crowns don't thin out
            try:
                t.set_editor_property("do_scale_mips_for_alpha_coverage", True)
                t.set_editor_property("alpha_coverage_thresholds", unreal.Vector4(0.0, 0.0, 0.0, 0.35))
            except Exception as e:  # property names moved between engine versions
                log(f"  alpha-coverage mips not set: {e}")
        unreal.EditorAssetLibrary.save_asset(f"{TEX_DIR}/{name}", only_if_is_dirty=False)
log(f"imported {len(tex)} flora textures")


def new_material(name):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        m = unreal.load_asset(path)
        if m is not None:
            for e in list(MEL.get_material_expressions(m)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
                MEL.delete_material_expression(m, e)
            return m
        unreal.EditorAssetLibrary.delete_asset(path)
    m = tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if m is None:
        raise RuntimeError(f"could not create or load {path}")
    return m


def must_connect_property(expr, out_pin, mat_property):
    if not MEL.connect_material_property(expr, out_pin, mat_property):
        raise RuntimeError(
            f"connect_material_property failed: output pin '{out_pin}' -> {mat_property}")


# ---------------------------------------------------------------------------
# Deciduous-arc follow-up (2026-09, next steps.md sec 7.17) node-soup helpers -
# small, explicit, material-argument-first so the same helpers build graphs on
# both M_Foliage (fall color / leaf drop) and M_Bark_Aspen (twig snow) below.
_ny = {}


def _node(mat, cls, x=-700):
    _ny[mat] = _ny.get(mat, 800) + 120
    return MEL.create_material_expression(mat, cls, x, _ny[mat])


def _must_connect(a, a_out, b, b_out):
    if not MEL.connect_material_expressions(a, a_out, b, b_out):
        raise RuntimeError(
            f"could not connect {a.get_class().get_name()}.{a_out!r} -> "
            f"{b.get_class().get_name()}.{b_out!r}")


def _const(mat, v, x=-700):
    n = _node(mat, unreal.MaterialExpressionConstant, x)
    n.set_editor_property("r", v)
    return n


def _const2(mat, a, b, x=-700):
    n = _node(mat, unreal.MaterialExpressionConstant2Vector, x)
    n.set_editor_property("r", a)
    n.set_editor_property("g", b)
    return n


def _const3(mat, r, gg, b, x=-700):
    n = _node(mat, unreal.MaterialExpressionConstant3Vector, x)
    n.set_editor_property("constant", unreal.LinearColor(r, gg, b, 1.0))
    return n


def _mask(mat, a, r=False, gg=False, b=False, alpha=False, a_out="", x=-700):
    n = _node(mat, unreal.MaterialExpressionComponentMask, x)
    n.set_editor_property("r", r)
    n.set_editor_property("g", gg)
    n.set_editor_property("b", b)
    n.set_editor_property("a", alpha)
    _must_connect(a, a_out, n, "")
    return n


def _binop(mat, cls, a, b, a_out="", b_out="", x=-700):
    n = _node(mat, cls, x)
    _must_connect(a, a_out, n, "A")
    _must_connect(b, b_out, n, "B")
    return n


def _mul(mat, a, b, a_out="", b_out=""):
    return _binop(mat, unreal.MaterialExpressionMultiply, a, b, a_out, b_out)


def _add(mat, a, b, a_out="", b_out=""):
    return _binop(mat, unreal.MaterialExpressionAdd, a, b, a_out, b_out)


def _sub(mat, a, b, a_out="", b_out=""):
    return _binop(mat, unreal.MaterialExpressionSubtract, a, b, a_out, b_out)


def _div(mat, a, b, a_out="", b_out=""):
    return _binop(mat, unreal.MaterialExpressionDivide, a, b, a_out, b_out)


def _dotp(mat, a, b, a_out="", b_out=""):
    return _binop(mat, unreal.MaterialExpressionDotProduct, a, b, a_out, b_out)


def _clamp01(mat, a, a_out="", x=-700):
    n = _node(mat, unreal.MaterialExpressionClamp, x)
    n.set_editor_property("min_default", 0.0)
    n.set_editor_property("max_default", 1.0)
    _must_connect(a, a_out, n, "")
    return n


def _frac(mat, a, a_out="", x=-700):
    n = _node(mat, unreal.MaterialExpressionFrac, x)
    _must_connect(a, a_out, n, "")
    return n


def _floor(mat, a, a_out="", x=-700):
    n = _node(mat, unreal.MaterialExpressionFloor, x)
    _must_connect(a, a_out, n, "")
    return n


def _sine(mat, a, a_out="", x=-700):
    n = _node(mat, unreal.MaterialExpressionSine, x)
    _must_connect(a, a_out, n, "")
    return n


def _lerp(mat, a, b, alpha, a_out="", b_out="", al_out="", x=-700):
    n = _node(mat, unreal.MaterialExpressionLinearInterpolate, x)
    _must_connect(a, a_out, n, "A")
    _must_connect(b, b_out, n, "B")
    _must_connect(alpha, al_out, n, "Alpha")
    return n


def _world_xy(mat):
    wp = _node(mat, unreal.MaterialExpressionWorldPosition)
    return _mask(mat, wp, r=True, gg=True)


def _value_noise(mat, uv2, scale_cm, period=97.0, seed_offset=(0.0, 0.0)):
    """Direct port of apply_ground_material.py's value_noise()/
    wrap_lattice_uv() (see that file's 2026-09-08 polish-pass comment for
    why the lattice-wrap step exists): a tileable-lattice value-noise hash,
    with its input pre-wrapped into a small repeating range so the classic
    sin/dot hash trick stays numerically stable at this project's real
    (large, UTM-scale) world coordinates. Returns a scalar roughly in [0,1]."""
    uv2 = _mul(mat, uv2, _const(mat, 1.0 / scale_cm))
    if seed_offset != (0.0, 0.0):
        uv2 = _add(mat, uv2, _const2(mat, *seed_offset))
    scaled = _mul(mat, uv2, _const(mat, 1.0 / period))
    wrapped01 = _frac(mat, scaled)
    wrapped = _mul(mat, wrapped01, _const(mat, period))

    i = _floor(mat, wrapped)
    f = _frac(mat, wrapped)

    def _hash2(v):
        d = _dotp(mat, v, _const2(mat, 12.9898, 78.233))
        s = _sine(mat, d)
        m = _mul(mat, s, _const(mat, 43758.5453))
        return _frac(mat, m)

    h00 = _hash2(i)
    h10 = _hash2(_add(mat, i, _const2(mat, 1.0, 0.0)))
    h01 = _hash2(_add(mat, i, _const2(mat, 0.0, 1.0)))
    h11 = _hash2(_add(mat, i, _const2(mat, 1.0, 1.0)))
    fx = _mask(mat, f, r=True)
    fy = _mask(mat, f, gg=True)
    top = _lerp(mat, h00, h10, fx)
    bot = _lerp(mat, h01, h11, fx)
    return _lerp(mat, top, bot, fy)


# Wet weather (PLAN-REALISM Phase 6, 2026-10-09): "Wetness" scalar, pushed by
# AODSeasonController to the forest MIDs (conifer foliage, deciduous foliage
# + bark). 0 = the exact dry graph. Species on the static materials keep the
# default 0 (no MID yet - same gap as their snow).
WET_DARKEN = 0.7            # albedo multiplier at Wetness 1
WET_BARK_ROUGHNESS = 0.35   # soaked bark sheen
WET_LEAF_ROUGHNESS = 0.45   # wet leaves catch the sky


def _wetness(mat):
    w = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -900, 900)
    w.set_editor_property("parameter_name", "Wetness")
    w.set_editor_property("default_value", 0.0)
    return w


def _wet_darken(mat, color, w, a_out=""):
    return _mul(mat, color, _lerp(mat, _const(mat, 1.0), _const(mat, WET_DARKEN), w), a_out=a_out)


# Wind (PLAN-VISUALS.md Track F 4, realistic twins only). Every term is
# driven by the Time node (game seconds), so it's frame-rate independent.
# Sine nodes run in cycles (period 1). Whole-tree sway reads the weights
# import_biome_flora.py bakes into UV1 (pipeline/flora_common.sway_uv):
# U = trunk bend, 0 at the base, 1 at the crown of a 15 m tree; V = branch-
# tip extra, 1 at the rim of a 4 m crown. Both are functions of the rest
# position, so bark and the leaf cards on it get the same offset and stay
# attached. Instance scale (trees are yaw-only, so it is the Z of local up
# in world space) makes a 2x tree sway 2x. A mesh without UV1 would read
# garbage here, but every slot has a _real mesh (verify_flora_real_slots).
WIND_BEND_CM = 6.0      # crown bend of a 15 m tree at full gust
WIND_BEND_HZ = 0.22     # trunk sway rate
WIND_TIP_CM = 4.0       # extra branch-tip bob at the rim of a 4 m crown
WIND_TIP_HZ = 0.7
WIND_GUST_HZ = 0.09     # slow gust wave rolling across the forest
WIND_SCALE_MAX = 2.2    # ODForestManager's largest tree instance scale


def _gust(mat, t, wxy):
    """0.3..1.0 gust strength, a slow wave across world XY (period ~4 km)."""
    return _add(mat, _const(mat, 0.65), _mul(mat, _const(mat, 0.35), _sine(mat, _add(
        mat, _mul(mat, t, _const(mat, WIND_GUST_HZ)), _dotp(mat, wxy, _const2(mat, 0.00025, 0.00016))))))


def _tree_sway(mat, t, wxy, pir, gust):
    """World offset of the whole-tree bend + branch-tip bob (~25 ALU ops)."""
    uv1 = _node(mat, unreal.MaterialExpressionTextureCoordinate)
    uv1.set_editor_property("coordinate_index", 1)
    up = _node(mat, unreal.MaterialExpressionTransform)
    up.set_editor_property("transform_source_type", unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_LOCAL)
    up.set_editor_property("transform_type", unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
    _must_connect(_const3(mat, 0.0, 0.0, 1.0), "", up, "")
    scale_gust = _mul(mat, _mask(mat, up, b=True), gust)
    # bend: a downwind lean that breathes, 0.1..1.0 of WIND_BEND_CM x U
    bend = _mul(mat, _mask(mat, uv1, r=True), _add(mat, _const(mat, 0.55), _mul(mat, _const(mat, 0.45), _sine(
        mat, _add(mat, _mul(mat, t, _const(mat, WIND_BEND_HZ)), pir)))))
    bend_v = _mul(mat, _const3(mat, 0.89 * WIND_BEND_CM, 0.46 * WIND_BEND_CM, 0.0), bend)
    # tips: faster bob, phase by world position so neighbouring limbs differ
    tip = _mul(mat, _mask(mat, uv1, gg=True), _sine(mat, _add(mat, _mul(mat, t, _const(mat, WIND_TIP_HZ)), _add(
        mat, pir, _dotp(mat, wxy, _const2(mat, 0.0037, 0.0029))))))
    tip_v = _mul(mat, _const3(mat, 0.5 * WIND_TIP_CM, 0.26 * WIND_TIP_CM, 0.83 * WIND_TIP_CM), tip)
    return _mul(mat, _add(mat, bend_v, tip_v), scale_gust)


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from flora_common import SWAY_U_MAX, SWAY_V_MAX
# Worst-case sway offset (direction vectors are ~unit, sines and gust <= 1),
# for max_world_position_offset_displacement: Nanite pads the bounds by it.
# ODForestManager also stops evaluating WPO past 100 m.
SWAY_BOUND_CM = (WIND_BEND_CM * SWAY_U_MAX + WIND_TIP_CM * SWAY_V_MAX) * WIND_SCALE_MAX * 1.01


# ----------------------------------------------------------- M_Bark_* (opaque)
BARK_MATS = {
    "M_Bark_Conifer": ("T_BarkConifer", 0.80),
    "M_Bark_Aspen":   ("T_BarkAspen", 0.70),
    "M_Bark_Willow":  ("T_BarkWillow", 0.75),
}
if REAL:
    BARK_MATS = {"M_Bark_Conifer_real": ("T_BarkConifer_real", 0.80),
                 "M_Bark_Aspen_real": ("T_BarkAspen_real", 0.70),
                 "M_Bark_Willow_real": ("T_BarkWillow_real", 0.75)}
for mat_name, (tex_name, roughness) in BARK_MATS.items():
    mat = new_material(mat_name)
    ts = MEL.create_material_expression(
        mat, unreal.MaterialExpressionTextureSample, -400, 0)
    ts.set_editor_property("texture", tex[tex_name])
    wet = _wetness(mat)
    wet_bark = _wet_darken(mat, ts, wet, a_out="RGB")
    must_connect_property(wet_bark, "", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = MEL.create_material_expression(
        mat, unreal.MaterialExpressionConstant, -400, 250)
    rough.set_editor_property("r", roughness)
    if REAL:   # rougher in the dark fissures, plus the derived normal map
        rough = _lerp(mat, _const(mat, min(1.0, roughness + 0.12)), _const(mat, roughness - 0.15),
                      _mask(mat, ts, r=True))
        nrm = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -400, -250)
        nrm.set_editor_property("texture", tex[tex_name + "_N"])
        nrm.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        must_connect_property(nrm, "RGB", unreal.MaterialProperty.MP_NORMAL)
        t = _node(mat, unreal.MaterialExpressionTime)
        wxy = _world_xy(mat)
        pir = _node(mat, unreal.MaterialExpressionPerInstanceRandom)
        must_connect_property(_tree_sway(mat, t, wxy, pir, _gust(mat, t, wxy)), "",
                              unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
        mat.set_editor_property("max_world_position_offset_displacement", SWAY_BOUND_CM)
    must_connect_property(_lerp(mat, rough, _const(mat, WET_BARK_ROUGHNESS), wet),
                          "", unreal.MaterialProperty.MP_ROUGHNESS)

    mat.set_editor_property("used_with_nanite", True)
    # ODForestManager places every tree via UHierarchicalInstancedStaticMesh
    # Component (see its BeginPlay()), so every material actually rendered
    # on a tree - bark included, not just M_Foliage - needs this usage flag
    # or it silently falls back to the flat grey engine default at runtime
    # ("Material .../M_Bark_* missing usage flag InstancedStaticMeshes!").
    mat.set_editor_property("used_with_instanced_static_meshes", True)
    mat.set_editor_property("two_sided", False)

    # Deciduous-arc follow-up (2026-09, next steps.md sec 7.17): "twig snow" -
    # M_Bark_Aspen ONLY (this is the trunk+branch material for tree_blob, the
    # one species with real branch geometry - see make_tree_meshes.py's
    # add_deciduous_branches()). AODForestManager applies a SEPARATE dynamic
    # MID off this material to the Blob/tree_blob batch only
    # (DeciduousBarkMID); every other species/batch keeps this material's
    # STATIC baked reference with SnowCover at its 0.0 default, so this is
    # zero-regression for bark_conifer/bark_willow and for tree_scrub_oak
    # (also M_Bark_Aspen, also left static). SnowCover=0 reproduces the
    # plain sampled bark color exactly (Lerp alpha 0 = A unchanged).
    if mat_name == "M_Bark_Aspen" + SFX:
        snow_cover_bark = MEL.create_material_expression(
            mat, unreal.MaterialExpressionScalarParameter, -700, 700)
        snow_cover_bark.set_editor_property("parameter_name", "SnowCover")
        snow_cover_bark.set_editor_property("default_value", 0.0)

        # Chunky snow "bunches" rather than a uniform coat: threshold a
        # value-noise field (branch-scale cells, ~35 cm) against SnowCover so
        # only a growing, patchy subset of the bark surface goes white -
        # reads as clumps of snow sitting on twigs/branch crooks, not a flat
        # tint. Same wrap-lattice-noise technique apply_ground_material.py's
        # polish pass proved out for this project's real-world coordinate
        # scale (see _value_noise()'s docstring above).
        patch = _value_noise(mat, _world_xy(mat), scale_cm=35.0, seed_offset=(3.7, 9.1))
        # SnowCover boosted before the threshold compare so full winter
        # (SnowCover=1) comfortably clears every patch's noise value instead
        # of asymptotically approaching partial coverage.
        boosted = _mul(mat, snow_cover_bark, _const(mat, 1.6))
        snow_amt_bark = _clamp01(mat, _sub(mat, boosted, patch))

        bark_color = wet_bark  # the sampled bark, wet-darkened above
        snow_color_bark = _const3(mat, 0.95, 0.96, 0.98)
        snowed_bark = _lerp(mat, bark_color, snow_color_bark, snow_amt_bark)
        must_connect_property(snowed_bark, "", unreal.MaterialProperty.MP_BASE_COLOR)
        log("  + twig-snow SnowCover wiring (chunky patch threshold, ~35cm cells)")

    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/{mat_name}", only_if_is_dirty=False)
    log(f"built {mat_name} (opaque, roughness {roughness})")

# ----------------------------------------------------- M_Foliage (masked, 2S)
m_fol = new_material("M_Foliage" + SFX)
ts = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionTextureSample, -400, 0)
ts.set_editor_property("texture", tex["T_FoliageAtlas" + SFX])
# blend mode must be Masked BEFORE connecting to the opacity-mask pin, or
# the connection can be rejected/ignored (the property pin is only "live"
# for the matching blend mode).
m_fol.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
must_connect_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
must_connect_property(ts, "A", unreal.MaterialProperty.MP_OPACITY_MASK)

rough = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionConstant, -400, 250)
rough.set_editor_property("r", 0.85)
fol_wet = _wetness(m_fol)
must_connect_property(_lerp(m_fol, rough, _const(m_fol, WET_LEAF_ROUGHNESS), fol_wet),
                      "", unreal.MaterialProperty.MP_ROUGHNESS)

# 2026-09-03 dark-crown fix: plain "Two Sided" only disables backface
# culling, it does NOT flip the backface normal for lighting - half of every
# crossed foliage card (see make_tree_meshes.py) faces away from the sun by
# construction, so those backfaces got N.L<=0 and rendered pure black,
# reading as a near-black/navy crown in-game (ScreenShot00247.png) even
# after brightening the atlas colors in build_flora_textures.py. The
# Two-Sided Foliage shading model is the engine's purpose-built fix for
# exactly this (leaf/needle cards lit from both sides): it adds a wrapped
# subsurface-style transmission term so backfaces read as lit (tinted by
# MP_SUBSURFACE_COLOR) instead of unlit-black.
m_fol.set_editor_property(
    "shading_model", unreal.MaterialShadingModel.MSM_TWO_SIDED_FOLIAGE)
sss = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionConstant3Vector, -400, 400)
sss.set_editor_property("constant", unreal.LinearColor(0.10, 0.28, 0.08, 1.0))
MEL.connect_material_property(sss, "", unreal.MaterialProperty.MP_SUBSURFACE_COLOR)

m_fol.set_editor_property("opacity_mask_clip_value", 0.35)
m_fol.set_editor_property("two_sided", True)
m_fol.set_editor_property("used_with_nanite", True)
m_fol.set_editor_property("used_with_instanced_static_meshes", True)

# ---------------------------------------------------------- seasons: canopy snow
# (2026-09-07, seasons feature part 1) - AODForestManager applies a shared MID
# built from THIS material to its Conifer/evergreen HISM batch only (see
# ODForestManager.cpp's FoliageSeasonMID) and drives SnowCover on it.
# SnowCover=0 is a byte-for-byte no-op (final base color == the plain sampled
# atlas color) - required zero-regression baseline for every OTHER species'
# batch too, since they still use this same base material asset unmodified.
#
# Snow only settles on top-facing card surfaces (VertexNormalWS.Z, clamped to
# [0,1] - a card facing sideways or down gets none) and is desynced per
# HISM instance via MaterialExpressionPerInstanceRandom (free on any
# instanced-static-mesh draw, no SetCustomDataValue plumbing needed - this
# project has none of that anywhere yet) so a whole tile's conifers don't
# all frost over in lockstep.
#
# FallBlend is exposed here (scalar param, unconnected) for a SECOND task's
# deciduous-tree arc (orange leaves / leaf drop / bare twigs) to wire up
# against this same material - not used for any visual effect by this pass,
# per this task's own scope split.
snow_cover_param = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionScalarParameter, -700, 600)
snow_cover_param.set_editor_property("parameter_name", "SnowCover")
snow_cover_param.set_editor_property("default_value", 0.0)

fall_blend_param = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionScalarParameter, -700, 750)
fall_blend_param.set_editor_property("parameter_name", "FallBlend")
fall_blend_param.set_editor_property("default_value", 0.0)
# Deliberately left unconnected - see docstring above; the param still exists
# on the material/MID for the deciduous-tree task to consume.

# NOTE (2026-09-07): this used to be VertexNormalWS.Z clamped to [0,1] - a
# "top facing surface" metric. That's meaningless for this project's crossed
# foliage cards (see docstring above): a vertical X-shaped quad has no true
# upward-facing normal, so VertexNormalWS.Z reads near-zero across the whole
# canopy and crushed snow_amount to ~0 everywhere regardless of SnowCover
# (confirmed by screenshot: winter render had zero visible canopy snow even
# though every graph connection here checked out fine).
#
# Two height-based replacements were tried and both failed for reasons
# specific to this HISM/foliage vertex factory, confirmed via a throwaway
# diagnostic material that visualized each candidate metric directly as a
# bright red overlay and re-rendered at the same vantage:
#   1. MaterialExpressionLocalPosition.Z (normalized against the conifer's
#      ~1650-1814cm bounding-box height) rendered as a flat, uniform zero
#      across every instance's entire canopy - this vertex factory does not
#      populate per-vertex local position usefully here (no readable "origin"
#      enum exists on this node in this UE 5.8 python API to diagnose further).
#   2. WorldPosition().Z minus ObjectPositionWS().Z (meant to give "height
#      above this instance's own pivot") instead produced a patchy, cluster-
#      shaped banding that didn't line up with individual tree crowns at all -
#      ObjectPositionWS evidently resolves to something like the HISM's
#      internal occlusion-cluster origin for this vertex factory, not a true
#      per-instance pivot, so the subtraction is meaningless per-tree.
# The one thing PROVEN to work (same diagnostic technique, forcing
# snow_amount = SnowCover directly) is a UNIFORM per-instance tint: MID
# param push, species targeting (Conifer-only), and the base-color Lerp all
# render correctly end to end. So this pass drops the per-vertex "only the
# top of the crown" ambition and tints the WHOLE canopy per instance instead,
# still desynced by PerInstanceRandom so a tile's conifers don't frost in
# lockstep - a simpler, honest design that matches this game's chunky
# stylized-realistic look (many stylized racers just whiten the whole crown
# at winter) rather than a photoreal top-only accumulation this vertex
# factory can't currently give us cheaply.
per_instance_random = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionPerInstanceRandom, -700, 1050)
desync = MEL.create_material_expression(m_fol, unreal.MaterialExpressionLinearInterpolate, -500, 1050)
desync_lo = MEL.create_material_expression(m_fol, unreal.MaterialExpressionConstant, -700, 1200)
# 2026-09-07 polish pass: raised from 0.55 - the winter QA shot
# (season_winter.png) showed conifers still reading plain green at driving
# distance, only detectable as frosted in a zoomed pixel crop. 0.55 meant the
# laggiest instance only ever reached a 55% blend toward snow_color even at
# full SnowCover=1, which the two-sided-foliage subsurface term's still-green
# MP_SUBSURFACE_COLOR (see snowed_subsurface below, added this same pass)
# fights against at grazing/backlit angles. 0.82 keeps SOME per-instance
# desync (a laggiest tree is still visibly behind the leaders) while making
# every conifer read as clearly frosted, not just tinted, once winter holds.
desync_lo.set_editor_property("r", 0.82)
desync_hi = MEL.create_material_expression(m_fol, unreal.MaterialExpressionConstant, -700, 1300)
desync_hi.set_editor_property("r", 1.0)
if not MEL.connect_material_expressions(desync_lo, "", desync, "A"):
    raise RuntimeError("could not connect desync_lo -> desync Lerp.A")
if not MEL.connect_material_expressions(desync_hi, "", desync, "B"):
    raise RuntimeError("could not connect desync_hi -> desync Lerp.B")
if not MEL.connect_material_expressions(per_instance_random, "", desync, "Alpha"):
    raise RuntimeError("could not connect PerInstanceRandom -> desync Lerp.Alpha")

snow_amount_raw = MEL.create_material_expression(m_fol, unreal.MaterialExpressionMultiply, -200, 1050)
if not MEL.connect_material_expressions(snow_cover_param, "", snow_amount_raw, "A"):
    raise RuntimeError("could not connect SnowCover param -> snow_amount_raw.A")
if not MEL.connect_material_expressions(desync, "", snow_amount_raw, "B"):
    raise RuntimeError("could not connect desync -> snow_amount_raw.B")

# 2026-09-07 polish pass: steepen the response curve so canopies reach a
# strongly-frosted look well before SnowCover hits 1.0 (Crew 2's winter
# conifers are boldly white-topped, not a subtle tint) instead of a slow
# linear crawl. SnowCover=0 is untouched - Multiply/Clamp of 0 is still 0, so
# the byte-for-byte zero-regression baseline holds.
snow_boost_const = MEL.create_material_expression(m_fol, unreal.MaterialExpressionConstant, -200, 1150)
snow_boost_const.set_editor_property("r", 1.45)
snow_amount_boosted = MEL.create_material_expression(m_fol, unreal.MaterialExpressionMultiply, -100, 1100)
if not MEL.connect_material_expressions(snow_amount_raw, "", snow_amount_boosted, "A"):
    raise RuntimeError("could not connect snow_amount_raw -> snow_amount_boosted.A")
if not MEL.connect_material_expressions(snow_boost_const, "", snow_amount_boosted, "B"):
    raise RuntimeError("could not connect snow_boost_const -> snow_amount_boosted.B")
snow_amount = MEL.create_material_expression(m_fol, unreal.MaterialExpressionClamp, 0, 1100)
snow_amount.set_editor_property("min_default", 0.0)
snow_amount.set_editor_property("max_default", 1.0)
if not MEL.connect_material_expressions(snow_amount_boosted, "", snow_amount, ""):
    raise RuntimeError("could not connect snow_amount_boosted -> snow_amount Clamp input")

snow_color = MEL.create_material_expression(m_fol, unreal.MaterialExpressionConstant3Vector, -350, 0)
snow_color.set_editor_property("constant", unreal.LinearColor(0.92, 0.94, 0.97, 1.0))
snowed_base_color = MEL.create_material_expression(m_fol, unreal.MaterialExpressionLinearInterpolate, -100, 0)
if not MEL.connect_material_expressions(ts, "RGB", snowed_base_color, "A"):
    raise RuntimeError("could not connect base atlas color -> canopy-snow Lerp.A")
if not MEL.connect_material_expressions(snow_color, "", snowed_base_color, "B"):
    raise RuntimeError("could not connect snow color -> canopy-snow Lerp.B")
if not MEL.connect_material_expressions(snow_amount, "", snowed_base_color, "Alpha"):
    raise RuntimeError("could not connect snow_amount -> canopy-snow Lerp.Alpha")
# Re-point BaseColor at the snow-blended result (was `ts` "RGB" directly above).
must_connect_property(snowed_base_color, "", unreal.MaterialProperty.MP_BASE_COLOR)

# 2026-09-07 polish pass: MSM_TWO_SIDED_FOLIAGE's wrapped-transmission term
# tints backlit/grazing-angle canopy faces with MP_SUBSURFACE_COLOR
# regardless of BaseColor - `sss` above stays a fixed dark green year-round,
# which was silently fighting the BaseColor snow blend above and reading as
# "still plain green" at driving distance even where BaseColor itself was
# heavily blended toward snow_color. Fade it toward a pale winter subsurface
# tint on the same snow_amount curve so backlit needles read frosted too.
# 2026-10-22: the pale tint (0.75, 0.80, 0.86) was the snow-card glow - a
# backlit snowy card transmitted ~80% of the sun on top of its 0.92 front
# diffuse, so snowed trees and grass tufts lit up like lamps. Snow is opaque:
# fade the transmission to a dim neutral instead (same idea as
# build_ground_cover_real.py's snow subsurface).
snow_subsurface_color = MEL.create_material_expression(m_fol, unreal.MaterialExpressionConstant3Vector, -400, 500)
snow_subsurface_color.set_editor_property("constant", unreal.LinearColor(0.06, 0.06, 0.07, 1.0))
snowed_subsurface = MEL.create_material_expression(m_fol, unreal.MaterialExpressionLinearInterpolate, -200, 450)
if not MEL.connect_material_expressions(sss, "", snowed_subsurface, "A"):
    raise RuntimeError("could not connect base subsurface color -> canopy-snow-subsurface Lerp.A")
if not MEL.connect_material_expressions(snow_subsurface_color, "", snowed_subsurface, "B"):
    raise RuntimeError("could not connect snow subsurface color -> canopy-snow-subsurface Lerp.B")
if not MEL.connect_material_expressions(snow_amount, "", snowed_subsurface, "Alpha"):
    raise RuntimeError("could not connect snow_amount -> canopy-snow-subsurface Lerp.Alpha")
# Re-point MP_SUBSURFACE_COLOR at the snow-blended result (was `sss` directly above).
must_connect_property(snowed_subsurface, "", unreal.MaterialProperty.MP_SUBSURFACE_COLOR)

# ------------------------------------------------ deciduous arc: fall color + leaf drop
# (2026-09 follow-up to the seasons feature - next steps.md sec 7.17). Wires
# up FallBlend (exposed-but-unwired by the first seasons pass) and adds a new
# LeafDrop param. ONLY the deciduous (Blob/tree_blob) species gets a dynamic
# MID off this same base material with non-zero values pushed into these
# (see ODForestManager.cpp's DeciduousFoliageMID) - every other species'
# HISM still references this material's STATIC baked defaults (FallBlend=0,
# LeafDrop=0), and every formula below is an exact no-op at 0/0 (Lerp alpha
# 0 = the plain sampled atlas color unchanged; drop_amount 0 = opacity mask
# unchanged), so this whole section is zero-regression for every non-target
# species/batch and for the summer baseline.
#
# Variation at two levels per the task spec: PER-TREE desync reuses the same
# free PerInstanceRandom input the snow desync above already reads (early/
# late turners), and WITHIN-CANOPY patchiness comes from a value-noise field
# sampled from absolute WorldPosition.XY - the same tileable lattice-noise
# hash (with the same lattice-wrap numerical-stability fix) apply_ground_
# material.py's polish pass proved out at this project's real-world (large,
# UTM-scale) coordinate range; see _value_noise()'s docstring above. Both
# are blended into one per-pixel-per-instance "turn threshold" in [0,1];
# FallBlend/LeafDrop crossing that threshold (over a soft band, not a hard
# cut) is what produces "a few spots turn first, then more and more" instead
# of the whole canopy flipping at once.
leaf_drop_param = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionScalarParameter, -700, 1500)
leaf_drop_param.set_editor_property("parameter_name", "LeafDrop")
leaf_drop_param.set_editor_property("default_value", 0.0)

patch_noise = _value_noise(m_fol, _world_xy(m_fol), scale_cm=180.0, seed_offset=(41.0, 17.0))
turn_threshold = _add(m_fol,
                       _mul(m_fol, per_instance_random, _const(m_fol, 0.5)),
                       _mul(m_fol, patch_noise, _const(m_fol, 0.5)))

TURN_BAND = 0.30  # soft-threshold width - wider = more overlap between "early" and "late" trees/patches
thr_lo = _sub(m_fol, turn_threshold, _const(m_fol, TURN_BAND / 2.0))

fall_amount = _clamp01(m_fol, _div(m_fol, _sub(m_fol, fall_blend_param, thr_lo), _const(m_fol, TURN_BAND)))
drop_amount = _clamp01(m_fol, _div(m_fol, _sub(m_fol, leaf_drop_param, thr_lo), _const(m_fol, TURN_BAND)))

fall_color = _const3(m_fol, 0.86, 0.42, 0.07)   # stylized aspen-gold/orange
if REAL:   # aspen gold to orange by patch, keeping the leaf texture's light/dark
    lum = _dotp(m_fol, ts, _const3(m_fol, 0.3, 0.59, 0.11), a_out="RGB")
    gold = _lerp(m_fol, _const3(m_fol, 0.95, 0.66, 0.14), _const3(m_fol, 0.88, 0.38, 0.08), patch_noise)
    fall_color = _mul(m_fol, gold, _mul(m_fol, lum, _const(m_fol, 2.6)))
base_after_fall = _lerp(m_fol, ts, fall_color, fall_amount, a_out="RGB")
# Re-point the canopy-snow Lerp's "before snow" input at the fall-blended
# color instead of the raw sampled atlas color (was `ts` "RGB" - see the
# snowed_base_color wiring above); harmless for every non-deciduous species
# since fall_amount is 0 there, so base_after_fall == ts exactly.
# Wet weather: darken before the snow lerp, so fresh snow stays white.
if not MEL.connect_material_expressions(_wet_darken(m_fol, base_after_fall, fol_wet), "", snowed_base_color, "A"):
    raise RuntimeError("could not re-point canopy-snow Lerp.A at base_after_fall")

opacity_after_drop = _mul(m_fol, ts, _sub(m_fol, _const(m_fol, 1.0), drop_amount), a_out="A")
# Re-point the opacity mask at the leaf-drop-eroded value (was `ts` "A"
# directly, connected near the top of this script) - the alpha-cutout
# clip (opacity_mask_clip_value=0.35, set above) then discards whatever this
# pushes below that threshold, progressively opening holes in the canopy
# card down to nothing as LeafDrop approaches 1, revealing the bare branch
# geometry make_tree_meshes.py's add_deciduous_branches() gives tree_blob
# underneath (branches share the bark material, always rendered).
must_connect_property(opacity_after_drop, "", unreal.MaterialProperty.MP_OPACITY_MASK)

# ------------------------------------------------ grass-tuft root shade (2026-10-07)
# Tufts don't cast shadows (ODForestManager BuildGrassCell), so darken each
# blade toward its root instead. Keyed off the grass_blade atlas cell's UVs -
# only grass_tuft samples that cell, so every tree/bush is untouched. Height
# metrics are out (see the snow notes above); UV v runs root->tip on a card.
# UE flips OBJ v on import: UE v = 1 - obj v.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from flora_common import cell_uv_rect
GRASS_ROOT_SHADE = 0.5      # brightness at the root
GRASS_SHADE_FRAC = 0.5      # fraction of the card height the gradient spans
gu0, gu1, gv0, gv1 = cell_uv_rect("grass_blade")
g_root_v, g_card_v = 1.0 - gv0, gv1 - gv0
uv0 = MEL.create_material_expression(m_fol, unreal.MaterialExpressionTextureCoordinate, -900, 1700)
uv_u, uv_v = _mask(m_fol, uv0, r=True), _mask(m_fol, uv0, gg=True)
in_cell = _mul(m_fol, _clamp01(m_fol, _mul(m_fol, _sub(m_fol, uv_u, _const(m_fol, gu0 - 0.01)), _const(m_fol, 1000.0))),
               _clamp01(m_fol, _mul(m_fol, _sub(m_fol, uv_v, _const(m_fol, 1.0 - gv1 - 0.01)), _const(m_fol, 1000.0))))
up = _clamp01(m_fol, _div(m_fol, _sub(m_fol, _const(m_fol, g_root_v), uv_v), _const(m_fol, g_card_v * GRASS_SHADE_FRAC)))
shade = _lerp(m_fol, _const(m_fol, 1.0), _lerp(m_fol, _const(m_fol, GRASS_ROOT_SHADE), _const(m_fol, 1.0), up), in_cell)
# Grass distance fade (2026-10-09, Taylor: tufts popped in ~60 m ahead).
# PerInstanceFadeAmount runs 1 -> 0 across each HISM's own cull start..end
# (BuildGrassCell's SetCullDistances), and a tuft drops out once it falls
# below its PerInstanceRandom, so density thins out over the band instead of
# a whole 16 m cell popping. Grass-blade cell only - tree canopies would
# dissolve to bare trunks.
fade = MEL.create_material_expression(m_fol, unreal.MaterialExpressionPerInstanceFadeAmount, -900, 1900)
keep = _clamp01(m_fol, _mul(m_fol, _sub(m_fol, fade, per_instance_random), _const(m_fol, 8.0)))
must_connect_property(_mul(m_fol, opacity_after_drop, _lerp(m_fol, _const(m_fol, 1.0), keep, in_cell)),
                      "", unreal.MaterialProperty.MP_OPACITY_MASK)
must_connect_property(_mul(m_fol, snowed_base_color, shade), "", unreal.MaterialProperty.MP_BASE_COLOR)
must_connect_property(_mul(m_fol, snowed_subsurface, shade), "", unreal.MaterialProperty.MP_SUBSURFACE_COLOR)

if REAL:
    # per-tree tint (no two crowns quite the same green) and leaf-coloured
    # transmission instead of the fixed dark-green subsurface constant
    tint = _lerp(m_fol, _const3(m_fol, 0.84, 0.86, 0.80), _const3(m_fol, 1.10, 1.08, 1.0), per_instance_random)
    must_connect_property(_mul(m_fol, _mul(m_fol, snowed_base_color, shade), tint), "",
                          unreal.MaterialProperty.MP_BASE_COLOR)
    _must_connect(_mul(m_fol, base_after_fall, _const(m_fol, 0.75)), "", snowed_subsurface, "A")

    # Wind: card flutter via world position offset (Track F 4). Driven by
    # the Time node (game seconds), so it's frame-rate independent. Keyed off
    # each card's own UV height - the atlas cells are 0.5 V tall and UE V =
    # 1 - OBJ v, so frac(2V) runs 0.92 at a card's attached base to 0.08 at
    # its tip: bases stay on their branch / in the ground, tips sway. No
    # local-position height (it reads 0 in this HISM vertex factory - see the
    # snow notes above), no new parameter (the interface rule wants
    # M_Foliage's exact scalar list). Phase: PerInstanceRandom plus a slow
    # world-space gust wave rolling across the forest. ~20 vertex-shader ALU
    # ops; same graph sways the trees, bushes and grass. On top of it, the
    # whole-tree bend from UV1 (_tree_sway, shared with the bark).
    WIND_CM = 6.0                                       # tip sway amplitude
    wind_dir = _const3(m_fol, 0.86, 0.45, 0.24)         # downwind, slight lift
    t = _node(m_fol, unreal.MaterialExpressionTime)
    wxy = _world_xy(m_fol)
    tip = _clamp01(m_fol, _div(m_fol, _sub(m_fol, _const(m_fol, 0.92),
                                            _frac(m_fol, _mul(m_fol, uv_v, _const(m_fol, 2.0)))),
                                _const(m_fol, 0.84)))
    phase = _add(m_fol, per_instance_random,
                 _dotp(m_fol, wxy, _const2(m_fol, 0.0011, 0.0007)))        # Sine period 1 = cycles
    gust = _gust(m_fol, t, wxy)
    sway = _add(m_fol, _add(m_fol, _const(m_fol, 0.35),
                            _mul(m_fol, _const(m_fol, 0.7), _sine(m_fol, _add(m_fol, _mul(m_fol, t, _const(m_fol, 0.45)), phase)))),
                _mul(m_fol, _const(m_fol, 0.3), _sine(m_fol, _add(m_fol, _mul(m_fol, t, _const(m_fol, 1.3)),
                                                                   _mul(m_fol, phase, _const(m_fol, 1.7))))))
    amount = _mul(m_fol, _mul(m_fol, tip, gust), _mul(m_fol, sway, _const(m_fol, WIND_CM)))
    # + the whole-tree sway the bark gets (same inputs), so cards ride their branch
    must_connect_property(_add(m_fol, _mul(m_fol, wind_dir, amount),
                               _tree_sway(m_fol, t, wxy, per_instance_random, gust)),
                          "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
    # bounds headroom: card flutter (max ~1.65 x WIND_CM) + tree sway
    m_fol.set_editor_property("max_world_position_offset_displacement", WIND_CM * 1.7 + SWAY_BOUND_CM)

MEL.recompile_material(m_fol)
unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/M_Foliage{SFX}", only_if_is_dirty=False)
log("built M_Foliage (masked, two-sided-foliage shading, alpha-cutout from T_FoliageAtlas, "
    "seasons params SnowCover [wired, boosted+clamped uniform per-instance tint x "
    "PerInstanceRandom desync [0.82,1.0], drives BaseColor AND MP_SUBSURFACE_COLOR] / "
    "FallBlend+LeafDrop [wired 2026-09 deciduous-arc follow-up: per-instance x "
    "per-patch value-noise turn-threshold drives orange BaseColor blend and "
    "opacity-mask leaf-drop dissolve])")

if REAL:
    # interface rule: each _real twin exposes exactly its stylized twin's
    # scalar parameters (C++ MIDs push SnowCover/FallBlend/LeafDrop/Wetness)
    for name in [*BARK_MATS, "M_Foliage_real"]:
        twin = unreal.load_asset(f"{MAT_DIR}/{name[:-5]}")
        mine = unreal.load_asset(f"{MAT_DIR}/{name}")
        want = sorted(str(n) for n in MEL.get_scalar_parameter_names(twin))
        got = sorted(str(n) for n in MEL.get_scalar_parameter_names(mine))
        if want != got:
            raise RuntimeError(f"interface rule: {name} params {got} != {name[:-5]} {want}")
        log(f"interface ok: {name} {got}")
    log("DONE - saved realistic flora materials")   # saved one by one: stylized assets untouched
else:
    unreal.EditorAssetLibrary.save_directory(MAT_DIR, only_if_is_dirty=False)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    log("DONE - saved 3 bark materials + M_Foliage")
