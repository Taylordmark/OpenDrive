"""Headless: import the new biome-distinct flora meshes (scrub oak,
manzanita, yucca/Joshua tree, sagebrush, canyon oak - 2026-08-27) into
/Game/OpenDrive/Trees/ and assign the existing M_Foliage material, the same
way build_visuals_headless.py's foliage-import block does for the classic
tree_blob/tree_olive/tree_conifer/grass_tuft/bush set. Split into its own
script rather than folded into build_visuals_headless.py since that script
is Tail-of-the-Dragon-specific (loads the TotD level, builds landscape grass
types) while /Game/OpenDrive/Trees/ content is shared across every stage -
AODForestManager LoadObject()s these paths directly regardless of level (see
ODForestManager.cpp BeginPlay()), so importing them needs no level load at
all.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

STAGE_DIR = Path(__file__).resolve().parents[1] / "data" / "stages" / "tail_of_the_dragon"
TEX_DIR = "/Game/OpenDrive/Textures"
TREE_DIR = "/Game/OpenDrive/Trees"
MAT_PATH = f"/Game/OpenDrive/Materials/M_Foliage"

NEW_FLORA = (
    "tree_scrub_oak", "tree_manzanita", "tree_yucca", "sagebrush",
    "tree_canyon_oak",
)

tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"FLORA {msg}")


# Re-import the shared palette texture too - the new species append columns
# to PALETTE in make_tree_meshes.py, so T_TreePalette.png has grown wider
# and existing meshes' baked UVs (recomputed at generation time, same file)
# must be paired with the matching regenerated texture, not a stale one.
tex_task = unreal.AssetImportTask()
tex_task.filename = str(STAGE_DIR / "textures" / "T_TreePalette.png")
tex_task.destination_path = TEX_DIR
tex_task.automated = True
tex_task.save = False
tex_task.replace_existing = True
tools.import_asset_tasks([tex_task])
tex_palette = unreal.load_asset(f"{TEX_DIR}/T_TreePalette")
if tex_palette is None:
    raise RuntimeError("T_TreePalette re-import failed")
log("re-imported T_TreePalette.png (widened for new species)")

fol_tasks = []
for name in NEW_FLORA:
    ti = unreal.AssetImportTask()
    ti.filename = str(STAGE_DIR / "obj" / f"{name}.obj")
    ti.destination_path = TREE_DIR
    ti.automated = True
    ti.save = False
    ti.replace_existing = True
    fol_tasks.append(ti)
tools.import_asset_tasks(fol_tasks)

fol = {n: unreal.load_asset(f"{TREE_DIR}/{n}") for n in NEW_FLORA}
missing = [n for n, m in fol.items() if m is None]
if missing:
    raise RuntimeError(f"foliage mesh import failed for: {missing}")
log(f"imported {len(fol)} new biome flora meshes")

m_fol = unreal.load_asset(MAT_PATH)
if m_fol is None:
    raise RuntimeError(
        f"{MAT_PATH} not found - run build_visuals_headless.py first "
        "(it builds M_Foliage)")
for name, mesh in fol.items():
    mesh.set_material(0, m_fol)
log(f"assigned M_Foliage to {len(fol)} meshes")

unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
