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
"""

from pathlib import Path

import unreal

STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
TEX_DIR = "/Game/OpenDrive/Textures"
MAT_DIR = "/Game/OpenDrive/Materials"

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"FLORAMAT {msg}")


# ------------------------------------------------------------------ textures
TEXTURES = ("T_BarkConifer.png", "T_BarkAspen.png", "T_BarkWillow.png",
            "T_FoliageAtlas.png")
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
log(f"imported {len(tex)} flora textures")


def new_material(name):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        m = unreal.load_asset(path)
        if m is not None:
            MEL.delete_all_material_expressions(m)
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


# ----------------------------------------------------------- M_Bark_* (opaque)
BARK_MATS = {
    "M_Bark_Conifer": ("T_BarkConifer", 0.80),
    "M_Bark_Aspen":   ("T_BarkAspen", 0.70),
    "M_Bark_Willow":  ("T_BarkWillow", 0.75),
}
for mat_name, (tex_name, roughness) in BARK_MATS.items():
    mat = new_material(mat_name)
    ts = MEL.create_material_expression(
        mat, unreal.MaterialExpressionTextureSample, -400, 0)
    ts.set_editor_property("texture", tex[tex_name])
    MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = MEL.create_material_expression(
        mat, unreal.MaterialExpressionConstant, -400, 250)
    rough.set_editor_property("r", roughness)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    mat.set_editor_property("used_with_nanite", True)
    # ODForestManager places every tree via UHierarchicalInstancedStaticMesh
    # Component (see its BeginPlay()), so every material actually rendered
    # on a tree - bark included, not just M_Foliage - needs this usage flag
    # or it silently falls back to the flat grey engine default at runtime
    # ("Material .../M_Bark_* missing usage flag InstancedStaticMeshes!").
    mat.set_editor_property("used_with_instanced_static_meshes", True)
    mat.set_editor_property("two_sided", False)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/{mat_name}", only_if_is_dirty=False)
    log(f"built {mat_name} (opaque, roughness {roughness})")

# ----------------------------------------------------- M_Foliage (masked, 2S)
m_fol = new_material("M_Foliage")
ts = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionTextureSample, -400, 0)
ts.set_editor_property("texture", tex["T_FoliageAtlas"])
# blend mode must be Masked BEFORE connecting to the opacity-mask pin, or
# the connection can be rejected/ignored (the property pin is only "live"
# for the matching blend mode).
m_fol.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
must_connect_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
must_connect_property(ts, "A", unreal.MaterialProperty.MP_OPACITY_MASK)

rough = MEL.create_material_expression(
    m_fol, unreal.MaterialExpressionConstant, -400, 250)
rough.set_editor_property("r", 0.85)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

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
desync_lo.set_editor_property("r", 0.55)
desync_hi = MEL.create_material_expression(m_fol, unreal.MaterialExpressionConstant, -700, 1300)
desync_hi.set_editor_property("r", 1.0)
if not MEL.connect_material_expressions(desync_lo, "", desync, "A"):
    raise RuntimeError("could not connect desync_lo -> desync Lerp.A")
if not MEL.connect_material_expressions(desync_hi, "", desync, "B"):
    raise RuntimeError("could not connect desync_hi -> desync Lerp.B")
if not MEL.connect_material_expressions(per_instance_random, "", desync, "Alpha"):
    raise RuntimeError("could not connect PerInstanceRandom -> desync Lerp.Alpha")

snow_amount = MEL.create_material_expression(m_fol, unreal.MaterialExpressionMultiply, -200, 1050)
if not MEL.connect_material_expressions(snow_cover_param, "", snow_amount, "A"):
    raise RuntimeError("could not connect SnowCover param -> snow_amount.A")
if not MEL.connect_material_expressions(desync, "", snow_amount, "B"):
    raise RuntimeError("could not connect desync -> snow_amount.B")

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

MEL.recompile_material(m_fol)
unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/M_Foliage", only_if_is_dirty=False)
log("built M_Foliage (masked, two-sided-foliage shading, alpha-cutout from T_FoliageAtlas, "
    "seasons params SnowCover [wired, uniform per-instance tint x PerInstanceRandom desync] / "
    "FallBlend [exposed, unwired])")

unreal.EditorAssetLibrary.save_directory(MAT_DIR, only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved 3 bark materials + M_Foliage")
