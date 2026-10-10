"""Realistic road materials (PLAN-VISUALS Track C). Builds, beside each
stylized road material (ODVisualStyle::Pick looks there):

  M_Road_real, M_Road_OneWay_real   paved graph, PM_Asphalt
  M_Road_Gravel_real                gravel graph, PM_Gravel
  M_Road_Dirt_real                  dirt graph, PM_DirtRoad

Each is apply_road_material.py re-run with REAL=True, i.e. the stylized
graph plus the Realistic layers (see that file's Real* constants), so the
interface rule holds by construction: same parameter names, UV0-3 and
vertex-color reads, berm WPO. A new twin starts as a duplicate of its
stylized asset, which carries over the physical material (surface grip keys
on its name) and usage flags (Nanite for the Dragon's static road). The run
fails if a twin is missing one of its stylized parameter names or has a
different physical material.

Also imports the generated decal atlas (pipeline/make_road_decal_textures.py
-> content_src/realistic/road/) and builds the deferred-decal material
/Game/OpenDrive/Realistic/Road/M_RoadDecal_real that UODRoadDecals spawns:
DecalColor.R picks the 2x2 atlas cell (crack, patch, manhole, tire marks),
DecalColor.G is the opacity, and "Wetness" darkens/glosses it like the road.

Imports the downloaded CC0 crack set (Asphalt026B, content_src/realistic/
road/manifest.json, fetched by content_src/textures/ground/
download_ground_textures.py <that manifest>) to /Game/OpenDrive/Realistic/
Road/ via import_ground_textures.py; apply_road_material.py's RealCrack*
layer samples it.

Needs import_road_textures.py + import_ground_textures.py to have run, and
apply_terrain_real_materials.py (imports Gravel040, which the RealEdge*
road-edge blend samples).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import runpy
from pathlib import Path

import unreal

HERE = Path(__file__).resolve().parent
ROAD_SCRIPT = str(HERE / "apply_road_material.py")
SRC_DIR = HERE.parent / "content_src" / "realistic" / "road"
MAT_DIR = "/Game/OpenDrive/Materials"
REAL_DIR = "/Game/OpenDrive/Realistic/Road"
DECAL_MAT = "M_RoadDecal_real"
DecalWetDarken = 0.55   # = apply_road_material.WetDarken
DecalWetRough = 0.12    # = apply_road_material.WetRoughness

EAL = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"ROADREAL {msg}")


def save(path):
    if not EAL.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {path}")


runpy.run_path(str(HERE / "import_ground_textures.py"),
               init_globals={"SRC_DIR": str(SRC_DIR), "TEX_DIR": REAL_DIR})


def param_names(mat):
    names = set()
    for fn in (MEL.get_scalar_parameter_names, MEL.get_vector_parameter_names,
               MEL.get_texture_parameter_names, MEL.get_static_switch_parameter_names):
        names |= {str(n) for n in fn(mat)}
    return names


# ------------------------------------------------------------ road twins
ns = None
for base, surface in (("M_Road", "paved"), ("M_Road_OneWay", "paved"),
                      ("M_Road_Gravel", "gravel"), ("M_Road_Dirt", "dirt")):
    base_path, real_path = f"{MAT_DIR}/{base}", f"{MAT_DIR}/{base}_real"
    if not EAL.does_asset_exist(base_path):
        raise RuntimeError(f"{base_path} missing - build the stylized road materials first")
    if not EAL.does_asset_exist(real_path):
        if not EAL.duplicate_asset(base_path, real_path):
            raise RuntimeError(f"could not duplicate {base_path} -> {real_path}")
    ns = runpy.run_path(ROAD_SCRIPT, init_globals={
        "SURFACE": surface, "REAL": True, "MAT_NAME_OVERRIDE": f"{base}_real"})

    twin, real = unreal.load_asset(base_path), unreal.load_asset(real_path)
    missing = param_names(twin) - param_names(real)
    if missing:
        raise RuntimeError(f"{base}_real lacks {base}'s parameters {sorted(missing)} (interface rule)")
    pm_twin, pm_real = twin.get_editor_property("phys_material"), real.get_editor_property("phys_material")
    if pm_twin != pm_real:
        real.set_editor_property("phys_material", pm_twin)
        save(real_path)
    log(f"{base}_real: params {sorted(param_names(real))}, phys {pm_twin.get_name() if pm_twin else None}")

# ------------------------------------------------------------ decal atlas
TEX = {}
for suffix, srgb, comp in (("C", True, unreal.TextureCompressionSettings.TC_DEFAULT),
                           ("N", False, unreal.TextureCompressionSettings.TC_MASKS)):
    name = f"T_RoadDecals_real_{suffix}"
    png = SRC_DIR / f"{name}.png"
    if not png.exists():
        raise RuntimeError(f"{png} missing - run pipeline/make_road_decal_textures.py")
    t = unreal.AssetImportTask()
    t.filename, t.destination_path, t.destination_name = str(png), REAL_DIR, name
    t.automated, t.save, t.replace_existing = True, False, True
    tools.import_asset_tasks([t])
    tex = unreal.load_asset(f"{REAL_DIR}/{name}")
    if tex is None:
        raise RuntimeError(f"import failed: {png}")
    tex.set_editor_property("srgb", srgb)
    tex.set_editor_property("compression_settings", comp)
    tex.set_editor_property("address_x", unreal.TextureAddress.TA_CLAMP)
    tex.set_editor_property("address_y", unreal.TextureAddress.TA_CLAMP)
    if not EAL.save_loaded_asset(tex, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {REAL_DIR}/{name}")
    TEX[suffix] = tex

# ------------------------------------------------------------ decal material
decal_path = f"{REAL_DIR}/{DECAL_MAT}"
if EAL.does_asset_exist(decal_path):
    dm = unreal.load_asset(decal_path)
    MEL.delete_all_material_expressions(dm)
else:
    dm = tools.create_asset(DECAL_MAT, REAL_DIR, unreal.Material, unreal.MaterialFactoryNew())
dm.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
dm.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)

g = ns["G"](dm)
uv = g.tex_coord(0)                          # decal UV: U along the road, V across
dcol = g.node(unreal.MaterialExpressionDecalColor)
cell = g.floorv(g.mul(g.mask(dcol, r=True), g.const(4.0)))   # R = (cell + 0.5) / 4
cell_y = g.floorv(g.mul(cell, g.const(0.5)))
cell_x = g.sub(cell, g.mul(cell_y, g.const(2.0)))
cell_xy = g.node(unreal.MaterialExpressionAppendVector)
g._req(MEL.connect_material_expressions(cell_x, "", cell_xy, "A"), "Append.A(cellX)")
g._req(MEL.connect_material_expressions(cell_y, "", cell_xy, "B"), "Append.B(cellY)")
atlas_uv = g.mul(g.add(uv, cell_xy), g.const(0.5))
col = g.sample(TEX["C"], atlas_uv)
nrm = g.sample(TEX["N"], atlas_uv)
nrm.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)   # linear

wet = g.scalar_param("Wetness", 0.0)
base_color = g.mul(g.mask(col, r=True, gg=True, b=True, a_out="RGB"),
                   g.lerp(g.const(1.0), g.const(DecalWetDarken), wet))
rough = g.lerp(nrm, g.const(DecalWetRough), wet, a_out="B")
nxy = g.sub(g.mul(g.mask(nrm, r=True, gg=True, a_out="RGB"), g.const(2.0)), g.const(1.0))
nz = g.unary(unreal.MaterialExpressionSquareRoot,
             g.clamp01(g.sub(g.const(1.0), g.dotp(nxy, nxy))))
normal = g.node(unreal.MaterialExpressionAppendVector)
g._req(MEL.connect_material_expressions(nxy, "", normal, "A"), "Append.A(nxy)")
g._req(MEL.connect_material_expressions(nz, "", normal, "B"), "Append.B(nz)")
opacity = g.mul(col, g.mask(dcol, gg=True), a_out="A")

MEL.connect_material_property(base_color, "", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(normal, "", unreal.MaterialProperty.MP_NORMAL)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY)
MEL.recompile_material(dm)
save(decal_path)

re = unreal.load_asset(decal_path)
for prop in ("MP_BASE_COLOR", "MP_NORMAL", "MP_ROUGHNESS", "MP_OPACITY"):
    if MEL.get_material_property_input_node(re, getattr(unreal.MaterialProperty, prop)) is None:
        raise RuntimeError(f"{DECAL_MAT}: {prop} unconnected")
log(f"built {decal_path} (domain {re.get_editor_property('material_domain')})")
log("DONE")
