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

m_fol.set_editor_property("opacity_mask_clip_value", 0.35)
m_fol.set_editor_property("two_sided", True)
m_fol.set_editor_property("used_with_nanite", True)
m_fol.set_editor_property("used_with_instanced_static_meshes", True)
MEL.recompile_material(m_fol)
unreal.EditorAssetLibrary.save_asset(f"{MAT_DIR}/M_Foliage", only_if_is_dirty=False)
log("built M_Foliage (masked, two-sided, alpha-cutout from T_FoliageAtlas)")

unreal.EditorAssetLibrary.save_directory(MAT_DIR, only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved 3 bark materials + M_Foliage")
