"""Headless: import the Phase 3b card-based flora meshes (real bark UVs +
alpha-cutout branch/leaf cards - see next steps.md and
pipeline/make_tree_meshes.py's module docstring for the full species list
and mesh-slot mapping) into /Game/OpenDrive/Trees/ and assign the matching
UE materials. Split into its own script rather than folded into
build_visuals_headless.py since that script is Tail-of-the-Dragon-specific
(loads the TotD level, builds landscape grass types) while
/Game/OpenDrive/Trees/ content is shared across every stage - AODForestManager
LoadObject()s these paths directly regardless of level (see
ODForestManager.cpp BeginPlay()), so importing them needs no level load at
all.

Mesh material-slot contract (2026-09-03, replaces the old single-material
palette-strip set): every tree mesh now has TWO material slots - slot 0 is
opaque bark (a per-species texture), slot 1 is alpha-cutout foliage (always
T_FoliageAtlas.png, so ALL species share one M_Foliage material for that
slot). Shrub/ground-cover meshes (bush, sagebrush, grass_tuft) are
foliage-only - ONE slot, no bark. make_tree_meshes.py's Mesh.save() docstring
notes that OBJ/Interchange material-SLOT ORDER (which usemtl ends up at
index 0 vs 1) could not be verified without a UE launch - it writes bark
triangles before foliage triangles in every two-slot mesh, which should put
bark at slot 0, but if the first tree imported here comes in with foliage at
slot 0 instead, flip BARK_SLOT/FOLIAGE_SLOT below (single place to fix) and
rerun.

M_Foliage needs "Two Sided" enabled - crossed foliage cards intentionally
have arbitrary winding relative to camera, so backface culling would punch
holes in every crown from the "wrong" side. This script does not create
M_Foliage/M_Bark_* (out of scope for this offline pass, per next steps.md) -
it expects them to already exist, same as the old M_Foliage dependency did.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
TEX_DIR = "/Game/OpenDrive/Textures"
TREE_DIR = "/Game/OpenDrive/Trees"
MAT_DIR = "/Game/OpenDrive/Materials"

BARK_SLOT, FOLIAGE_SLOT = 0, 1  # see module docstring - flip if a test
                                # import shows these reversed

# name -> (bark UE material name or None if foliage-only, bark texture file)
FLORA = {
    "tree_blob":      ("M_Bark_Aspen", "T_BarkAspen.png"),
    "tree_olive":     ("M_Bark_Conifer", "T_BarkConifer.png"),
    "tree_conifer":   ("M_Bark_Conifer", "T_BarkConifer.png"),
    "tree_scrub_oak": ("M_Bark_Aspen", "T_BarkAspen.png"),
    "tree_manzanita": ("M_Bark_Conifer", "T_BarkConifer.png"),
    "tree_yucca":     ("M_Bark_Conifer", "T_BarkConifer.png"),
    "tree_canyon_oak": ("M_Bark_Conifer", "T_BarkConifer.png"),
    "bush":           ("M_Bark_Willow", "T_BarkWillow.png"),
    "sagebrush":      (None, None),
    "grass_tuft":     (None, None),
}
# Graphics menu "Asset Detail" variants (make_tree_meshes.py TIERS) - same
# materials; the game falls back to the base mesh if these aren't imported.
FLORA = {n + sfx: v for sfx in ("", "_high", "_ultra") for n, v in FLORA.items()}
FOLIAGE_TEXTURES = ("T_FoliageAtlas.png", "T_BarkConifer.png", "T_BarkAspen.png",
                     "T_BarkWillow.png")

tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"FLORA {msg}")


# Re-import all four production textures - T_TreePalette.png is retired by
# this pass (no mesh references it any more; the card-based set uses real
# bark photos + the alpha foliage atlas instead of a flat palette strip).
tex_tasks = []
for fname in FOLIAGE_TEXTURES:
    ti = unreal.AssetImportTask()
    ti.filename = str(STAGE_DIR / "textures" / fname)
    ti.destination_path = TEX_DIR
    ti.automated = True
    ti.save = False
    ti.replace_existing = True
    tex_tasks.append(ti)
tools.import_asset_tasks(tex_tasks)
for fname in FOLIAGE_TEXTURES:
    asset_name = fname.rsplit(".", 1)[0]
    if unreal.load_asset(f"{TEX_DIR}/{asset_name}") is None:
        raise RuntimeError(f"{asset_name} re-import failed")
log(f"imported {len(FOLIAGE_TEXTURES)} flora textures")

fol_tasks = []
for name in FLORA:
    ti = unreal.AssetImportTask()
    ti.filename = str(STAGE_DIR / "obj" / f"{name}.obj")
    ti.destination_path = TREE_DIR
    ti.automated = True
    ti.save = False
    ti.replace_existing = True
    fol_tasks.append(ti)
tools.import_asset_tasks(fol_tasks)

fol = {n: unreal.load_asset(f"{TREE_DIR}/{n}") for n in FLORA}
missing = [n for n, m in fol.items() if m is None]
if missing:
    raise RuntimeError(f"foliage mesh import failed for: {missing}")
log(f"imported {len(fol)} flora meshes")

m_foliage = unreal.load_asset(f"{MAT_DIR}/M_Foliage")
if m_foliage is None:
    raise RuntimeError(
        f"{MAT_DIR}/M_Foliage not found - build it first (M_Foliage needs "
        "'Two Sided' enabled for these crossed alpha cards - see this "
        "script's module docstring) before running this import")

bark_mats = {}
for name, (bark_mat_name, _tex) in FLORA.items():
    if bark_mat_name is None:
        continue
    if bark_mat_name not in bark_mats:
        m = unreal.load_asset(f"{MAT_DIR}/{bark_mat_name}")
        if m is None:
            raise RuntimeError(
                f"{MAT_DIR}/{bark_mat_name} not found - build the per-species "
                f"bark materials (M_Bark_Conifer, M_Bark_Aspen, M_Bark_Willow; "
                f"opaque, textured from T_BarkConifer/T_BarkAspen/T_BarkWillow) "
                f"before running this import")
        bark_mats[bark_mat_name] = m

for name, mesh in fol.items():
    bark_mat_name, _tex = FLORA[name]
    if bark_mat_name is None:
        # foliage-only mesh (sagebrush, grass_tuft) - only slot 0 exists.
        mesh.set_material(0, m_foliage)
    else:
        mesh.set_material(BARK_SLOT, bark_mats[bark_mat_name])
        mesh.set_material(FOLIAGE_SLOT, m_foliage)
log(f"assigned bark/foliage materials to {len(fol)} meshes")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
