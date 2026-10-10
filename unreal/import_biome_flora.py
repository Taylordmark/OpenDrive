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

Realistic twins (PLAN-VISUALS.md Track F): OD_FLORA_REAL=1 imports the
make_tree_meshes.py --real set (<slot>_real{,_high,_ultra}) beside the
stylized meshes with the M_*_real materials (build_flora_materials.py with
the same variable first), and saves only those assets.
"""

import os
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
BASE_SLOTS = tuple(FLORA)
FLORA = {n + sfx: v for sfx in ("", "_high", "_ultra") for n, v in FLORA.items()}
FOLIAGE_TEXTURES = ("T_FoliageAtlas.png", "T_BarkConifer.png", "T_BarkAspen.png",
                     "T_BarkWillow.png")
REAL = os.environ.get("OD_FLORA_REAL") == "1"
SFX = "_real" if REAL else ""
if REAL:   # every slot's realistic twin, same bark material family as stylized
    FLORA = {f"{n}_real{t}": (bark and bark + "_real", None)
             for t in ("", "_high", "_ultra")
             for n, (bark, _tex) in FLORA.items() if n in BASE_SLOTS}
    FOLIAGE_TEXTURES = ()   # build_flora_materials.py imported the _real textures

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
if tex_tasks:
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

m_foliage = unreal.load_asset(f"{MAT_DIR}/M_Foliage{SFX}")
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

if REAL:
    # Whole-tree wind weights into UV channel 1 (OBJ carries one UV set, so
    # they go in here, after import). U = trunk bend, V = branch-tip extra,
    # both from the vertex rest position (pipeline/flora_common.sway_uv);
    # M_Foliage_real / M_Bark_*_real read them as world position offset.
    # The reimport above starts from a clean mesh, so this is idempotent.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
    from flora_common import SWAY_U_MAX, SWAY_V_MAX, sway_uv

    def _gs(fn):   # GeometryScript library class names move between versions
        for n in dir(unreal):
            if "GeometryScript" in n and hasattr(getattr(unreal, n), fn):
                return getattr(getattr(unreal, n), fn)
        raise RuntimeError(f"no GeometryScript function {fn}")

    OK = unreal.GeometryScriptOutcomePins.SUCCESS
    read_lod = unreal.GeometryScriptMeshReadLOD()
    read_lod.lod_type = unreal.GeometryScriptLODType.SOURCE_MODEL
    to_opts = unreal.GeometryScriptCopyMeshToAssetOptions()
    to_opts.enable_recompute_tangents = True   # OBJ has none; the import computed them too
    to_opts.emit_transaction = False
    to_opts.generate_lightmap_u_vs = next(   # lightmap UVs would land on UV1
        getattr(unreal.GeometryScriptGenerateLightmapUVOptions, n)
        for n in dir(unreal.GeometryScriptGenerateLightmapUVOptions) if n.startswith("DO_NOT"))
    render_lod = unreal.GeometryScriptMeshReadLOD()
    render_lod.lod_type = unreal.GeometryScriptLODType.RENDER_DATA
    for name, mesh in fol.items():
        dm, res = _gs("copy_mesh_from_static_mesh")(
            mesh, unreal.DynamicMesh(), unreal.GeometryScriptCopyMeshFromAssetOptions(), read_lod)
        if res != OK:
            raise RuntimeError(f"{name}: copy_mesh_from_static_mesh failed")
        _, plist, vgaps = _gs("get_all_vertex_positions")(dm, False)
        _, tlist, tgaps = _gs("get_all_triangle_indices")(dm, False)
        if vgaps or tgaps:
            raise RuntimeError(f"{name}: dynamic mesh has id gaps")
        pos = _gs("convert_vector_list_to_array")(plist)
        tris = _gs("convert_triangle_list_to_array")(tlist)
        H = max(p.z for p in pos)
        R = max((p.x * p.x + p.y * p.y) ** 0.5 for p in pos)
        if min(p.z for p in pos) < -30 or H <= 0:   # cards sink ~12% below their attach point
            raise RuntimeError(f"{name}: not z-up from the trunk base (z {min(p.z for p in pos)}..{H})")
        _gs("set_num_uv_sets")(dm, 2)
        add, umax, vmax, elem = _gs("add_uv_element_to_mesh"), 0.0, 0.0, []
        for p in pos:
            u, v = sway_uv(p.x, p.y, p.z, H, R)
            umax, vmax = max(umax, u), max(vmax, v)
            elem.append(add(dm, 1, unreal.Vector2D(u, v), True)[1])
        if umax > SWAY_U_MAX or vmax > SWAY_V_MAX:
            raise RuntimeError(f"{name}: sway U {umax:.2f} / V {vmax:.2f} over the material's WPO bound")
        set_ids = _gs("set_mesh_triangle_uv_element_i_ds")
        for tid, t in enumerate(tris):
            set_ids(dm, 1, tid, unreal.IntVector(elem[t.x], elem[t.y], elem[t.z]), True)
        _, res = _gs("copy_mesh_to_static_mesh")(dm, mesh, to_opts, unreal.GeometryScriptMeshWriteLOD())
        # read the built render mesh back (Nanite fallback): UV1 must hold the weights
        rd, res2 = _gs("copy_mesh_from_static_mesh")(
            mesh, unreal.DynamicMesh(), unreal.GeometryScriptCopyMeshFromAssetOptions(), render_lod)
        box, valid, empty = _gs("get_uv_set_bounding_box")(rd, 1)
        if res != OK or res2 != OK or not valid or empty or box.min.x < -0.01 or box.max.x < 0.5 * umax:
            raise RuntimeError(f"{name}: sway UV1 not in the built mesh ({valid} {empty} {box})")
        unreal.log_warning(f"FLORA {name}: sway UV1 baked, H {H:.0f} R {R:.0f} cm, U max {umax:.2f} "
                           f"V max {vmax:.2f}, render UV1 max ({box.max.x:.2f}, {box.max.y:.2f})")

if REAL:   # only the new twins - never re-save the stylized content
    for name, mesh in fol.items():
        bark = FLORA[name][0]
        if bark is None and mesh.get_num_sections(0) != 1:
            raise RuntimeError(f"{name}: foliage-only mesh has {mesh.get_num_sections(0)} sections")
        if bark and (mesh.get_num_sections(0) != 2 or mesh.get_material(BARK_SLOT) != bark_mats[bark]):
            raise RuntimeError(f"{name}: expected 2 sections, bark at slot {BARK_SLOT}")
        unreal.EditorAssetLibrary.save_asset(f"{TREE_DIR}/{name}", only_if_is_dirty=False)
    log(f"DONE - saved {len(fol)} realistic meshes")
else:
    unreal.EditorAssetLibrary.save_directory("/Game/OpenDrive", only_if_is_dirty=False)
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
    log("DONE - saved")
