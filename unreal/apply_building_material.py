"""Rebuilds M_BuildingFacade (Arc 7.0 step 3 building overhaul, 2026-09-03) -
replaces the old placeholder "frac(V)<0.5 horizontal window-band" hack (see
git history on this file, and next steps.md §7.15 for the "dark blue-gray
horizontally-striped slabs" bug report this fixes) with real CC0 PBR wall/
roof texture sets (content_src/textures/building/, see manifest.json) plus
an in-shader procedural window grid, so buildings read as textured mountain-
town blocks (brick/siding/stucco/concrete walls, dark window rectangles with
a warm-lit fraction, a shingle roof cap) instead of a striped placeholder.

WHY vertex color, not per-building Material Instances: AODBuildingManager
gives every building its OWN UDynamicMeshComponent (FBuildingTile - not
HISM-instanced, see that struct's header comment), and a live drive can have
~1800 of them streamed in at once. There is therefore no per-instance custom
data slot to write per-building variation into (that's the HISM-only
mechanism), and creating ~1800 MIDs would add real per-instance material
overhead on top of an already-heavy per-building draw-call count for no
visual benefit a vertex channel doesn't already give for free. So this
material is ONE shared asset, and AODBuildingManager::CreateBuildingComponent
(ODBuildingManager.cpp) writes a per-building selector into vertex color
instead - see BuildingVertexColorForFacade()'s comment there for the exact
R/G/B payload this material reads back below.

Vertex color channels: R = wall texture set, baked per building from OSM
type + height (pipeline/build_building_mesh.py pick_facade(), carried in the
ODU2 tile's WallSet byte, encoded mid-bucket as (idx+0.5)/5); G = lit-window
seed hash; B = roof type (1 = flat roof using the Concrete034 wall set, 0 =
shingle ROOF_LAYER).

Wall-material variant pick is a HARD per-building selection (floor(R*N) via
a chained MaterialExpressionIf, not a blend) - one building always reads as
one material, matching the Crew-2-reference "each building is one facade
material" look rather than a smeared multi-texture blend.

Window grid is fully procedural (no window texture/atlas): built from the
wall UV baked by pipeline/build_building_mesh.py's extrude_footprint() -
U = perimeter distance / FACADE_TILE_M, V = height / FACADE_TILE_M (both
constants mirrored below as *_M) - converted to bay/floor cell coordinates,
then a soft-edged rectangle mask per cell. Ground floor (floor index 0) gets
a taller opening (storefront read); a small hashed fraction of upper-floor
windows are warm-emissive (lit rooms), decorrelated per building via the G
vertex-color channel so neighboring buildings don't share an identical lit
pattern. Roof faces are picked out by vertex-normal Z (see
AODBuildingManager::CreateBuildingComponent's per-vertex-normal comment for
why the wall/roof edge stays crisp rather than smoothing) and get the roof
texture set (shingle, or concrete when B says flat roof), overriding the
wall+window result entirely.

Run unreal/import_building_textures.py FIRST (imports the PBR sets as
Texture2D assets this script loads by path) - this script raises if any
layer's textures are missing.

Known UE 5.8 python-binding pin-name gotchas this file's own G helper class
works around (same ones apply_ground_material.py's docstring documents):
Clamp/ComponentMask/Abs/Floor/Frac/Sine/Power's primary input pin is
unnamed ("" not "Input", except Power's exponent pin which IS named, but
"Exp" not "Exponent"); VertexColor's RGB output is not addressable as "RGB"
(use "" instead); TextureSample's UV input pin is "UVs" in some contexts,
"Coordinates" in others; MaterialExpressionIf's branch-output pins are the
human-readable captions "A > B"/"A == B"/"A < B", not C++ property names -
get_material_expression_input_names() confirmed these are the same pins the
old placeholder script (git history) needed and initially got wrong.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_BuildingFacade"
BUILDING_TEX_DIR = "/Game/OpenDrive/Textures/Building"

# Mirrors pipeline/build_building_mesh.py's FACADE_TILE_M (wall UV period,
# meters) - MUST match or the window-grid math below reads the wrong scale
# off the baked UV.
FACADE_TILE_M = 4.0
# Mirrors pipeline/build_building_mesh.py's LEVEL_HEIGHT_M.
FLOOR_HEIGHT_M = 3.2
BAY_WIDTH_M = 2.5

# (asset_id,) - order matters: this IS the floor(R*5) bucket order the
# per-building vertex-color R channel selects between. Two brick variants
# first (most common "read as a real street" material), then siding/
# stucco/concrete for the rest of the mix.
WALL_LAYERS = ["Bricks074", "Bricks053", "WoodSiding001", "Plaster001", "Concrete034"]
ROOF_LAYER = "RoofingTiles007"

# Window grid tuning (by feel, Crew-2-reference bar: dark glass rectangles,
# occasional warm-lit ones, taller storefront openings at street level).
WINDOW_MARGIN_U = (0.15, 0.85)          # bay-fraction window extent (upper floors)
WINDOW_MARGIN_V_UPPER = (0.30, 0.82)    # floor-fraction window extent (upper floors)
WINDOW_MARGIN_V_GROUND = (0.06, 0.92)   # taller storefront opening, ground floor
WINDOW_EDGE_SHARPNESS = 40.0            # higher = crisper rectangle edge
LIT_FRACTION = 0.12                     # fraction of upper-floor windows warm-emissive
LIT_COLOR = (1.0, 0.72, 0.32)
LIT_EMISSIVE_INTENSITY = 6.0
GLASS_COLOR = (0.035, 0.04, 0.05)
GLASS_ROUGHNESS = 0.18
ROOF_MASK_THRESHOLD = 0.55              # vertex-normal.Z above this = roof


def log(msg):
    # unreal.log() doesn't reliably surface in this project's headless
    # -stdout captures (apply_water_material.py's 2026-08-27 finding) -
    # log_warning does.
    unreal.log_warning(f"BUILDINGMAT {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def load_set(asset_id):
    base = f"{BUILDING_TEX_DIR}/{asset_id}"
    albedo = unreal.load_asset(f"{base}/T_{asset_id}_A")
    normal = unreal.load_asset(f"{base}/T_{asset_id}_N")
    rough = unreal.load_asset(f"{base}/T_{asset_id}_R")
    if albedo is None or normal is None or rough is None:
        raise RuntimeError(
            f"building texture set '{asset_id}' missing under {base} - run "
            f"unreal/import_building_textures.py first (after "
            f"content_src/textures/building/download_building_textures.py)")
    return dict(albedo=albedo, normal=normal, rough=rough)


WALL_TEXTURES = [load_set(a) for a in WALL_LAYERS]
ROOF_TEXTURES = load_set(ROOF_LAYER)


class G:
    """Node-soup helper around MaterialEditingLibrary - same shape as
    apply_ground_material.py's own G class (see that file's module
    docstring for the full list of pin-name gotchas this works around)."""

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 200
        n = MEL.create_material_expression(
            self.mat, cls, -2200 + (self._y % 1600) * 2, self._y // 8)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def const2(self, a, b):
        return self.node(unreal.MaterialExpressionConstant2Vector, r=a, g=b)

    def const3(self, r, gg, b):
        return self.node(unreal.MaterialExpressionConstant3Vector,
                          constant=unreal.LinearColor(r, gg, b, 1.0))

    def _req(self, ok, what):
        if not ok:
            raise RuntimeError(f"could not connect {what} - pin name mismatch?")

    def unary(self, cls, a, a_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), cls.__name__)
        return n

    def floorv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionFloor, a, a_out)

    def fracv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionFrac, a, a_out)

    def sinev(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionSine, a, a_out)

    def clamp01(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionClamp, a, a_out)

    def mask(self, a, r=False, gg=False, b=False, alpha=False, a_out=""):
        n = self.node(unreal.MaterialExpressionComponentMask, r=r, g=gg, b=b, a=alpha)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), "ComponentMask")
        return n

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), f"{cls.__name__}.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def dotp(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDotProduct, a, b, a_out, b_out)

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self._req(MEL.connect_material_expressions(alpha, al_out, n, "Alpha"), "Lerp.Alpha")
        return n

    def append(self, a, b, a_out="", b_out=""):
        n = self.node(unreal.MaterialExpressionAppendVector)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Append.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Append.B")
        return n

    def vertex_color(self):
        return self.node(unreal.MaterialExpressionVertexColor)

    def world_normal(self):
        return self.node(unreal.MaterialExpressionVertexNormalWS)

    def tex_coord(self):
        return self.node(unreal.MaterialExpressionTextureCoordinate)

    def sample(self, tex, uv, is_normal=False, uv_out=""):
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex)
        if is_normal:
            n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        ok = False
        for pin in ("UVs", "Coordinates"):
            if MEL.connect_material_expressions(uv, uv_out, n, pin):
                ok = True
                break
        self._req(ok, "TextureSample UV")
        return n

    def rough_scalar(self, rough_tex_node):
        return self.mask(rough_tex_node, r=True, a_out="RGB")

    def if_lt(self, x, thresh_val, true_branch, false_branch, x_out=""):
        """(node,out) selecting true_branch if x < thresh_val else
        false_branch - MaterialExpressionIf's branch-output pins are named
        by caption, see module docstring."""
        n = self.node(unreal.MaterialExpressionIf)
        self._req(MEL.connect_material_expressions(x, x_out, n, "A"), "If.A")
        t = self.const(thresh_val)
        self._req(MEL.connect_material_expressions(t, "", n, "B"), "If.B")
        tb, tb_out = true_branch
        fb, fb_out = false_branch
        self._req(MEL.connect_material_expressions(tb, tb_out, n, "A < B"), "If.A<B")
        self._req(MEL.connect_material_expressions(fb, fb_out, n, "A > B"), "If.A>B")
        self._req(MEL.connect_material_expressions(fb, fb_out, n, "A == B"), "If.A==B")
        return (n, "")

    def select_n(self, x, branches, thresholds):
        """Hard-select one of len(branches) values by chained if_lt against
        ascending `thresholds` (len(branches) - 1 of them) - NOT a blend,
        exactly one branch's value passes through for any given x."""
        assert len(branches) == len(thresholds) + 1
        result = branches[-1]
        for i in reversed(range(len(thresholds))):
            result = self.if_lt(x, thresholds[i], branches[i], result)
        return result

    def rect_mask(self, coord, lo, hi, sharpness, coord_out=""):
        """Soft-edged 1D rectangle: ~1 inside [lo,hi], ~0 outside, edge
        width ~= 1/sharpness."""
        sharp = self.const(sharpness)
        a = self.clamp01(self.mul(self.sub(coord, lo, a_out=coord_out), sharp))
        b = self.clamp01(self.mul(self.sub(hi, coord, b_out=coord_out), sharp))
        return self.mul(a, b)

    def hash2(self, vec2):
        """Cheap position hash -> ~[0,1] scalar, same sin/frac hash shape
        apply_ground_material.py's value_noise() uses."""
        d = self.dotp(vec2, self.const2(12.9898, 78.233))
        s = self.sinev(d)
        m = self.mul(s, self.const(43758.5453))
        return self.fracv(m)


mat_path = f"{MAT_DIR}/{MAT_NAME}"

if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

# OSM building-footprint rings have no guaranteed winding direction (see the
# old placeholder script's identical comment, still true) - two-sided
# sidesteps needing to normalize/guess winding from untrusted source data.
mat.set_editor_property("two_sided", True)

g = G(mat)

# ---------------------------------------------------------------- shared signals
uv = g.tex_coord()
u = g.mask(uv, r=True)
v = g.mask(uv, gg=True)

vtint = g.vertex_color()   # "" is the RGB output on this UE version
variant_r = g.mask(vtint, r=True)
seed_g = g.mask(vtint, gg=True)
flat_roof_b = g.mask(vtint, b=True)   # 0 or 1 per building, see docstring

normal_ws = g.world_normal()
up = g.const3(0.0, 0.0, 1.0)
ndotup = g.dotp(normal_ws, up)
roof_mask = g.clamp01(g.mul(g.sub(ndotup, g.const(ROOF_MASK_THRESHOLD)), g.const(20.0)))

# ---------------------------------------------------------------- wall variant select (hard pick, not a blend)
wall_samples = [
    dict(
        albedo=g.sample(t["albedo"], uv),
        normal=g.sample(t["normal"], uv, is_normal=True),
        rough=g.rough_scalar(g.sample(t["rough"], uv)),
    )
    for t in WALL_TEXTURES
]
thresholds = [i / len(WALL_LAYERS) for i in range(1, len(WALL_LAYERS))]  # e.g. 5 layers -> .2/.4/.6/.8

wall_albedo_node, wall_albedo_out = g.select_n(
    variant_r, [(s["albedo"], "RGB") for s in wall_samples], thresholds)
wall_normal_node, wall_normal_out = g.select_n(
    variant_r, [(s["normal"], "RGB") for s in wall_samples], thresholds)
wall_rough_node, wall_rough_out = g.select_n(
    variant_r, [(s["rough"], "") for s in wall_samples], thresholds)

# ---------------------------------------------------------------- window grid
bay_u = g.mul(u, g.const(FACADE_TILE_M / BAY_WIDTH_M))
floor_v = g.mul(v, g.const(FACADE_TILE_M / FLOOR_HEIGHT_M))
bay_idx = g.floorv(bay_u)
bay_frac = g.fracv(bay_u)
floor_idx = g.floorv(floor_v)
floor_frac = g.fracv(floor_v)

# 1 on the ground floor (floor_idx == 0), 0 on every floor above.
ground_mask = g.clamp01(g.sub(g.const(1.0), g.mul(floor_idx, g.const(10.0))))

lo_v = g.lerp(g.const(WINDOW_MARGIN_V_UPPER[0]), g.const(WINDOW_MARGIN_V_GROUND[0]), ground_mask)
hi_v = g.lerp(g.const(WINDOW_MARGIN_V_UPPER[1]), g.const(WINDOW_MARGIN_V_GROUND[1]), ground_mask)

mask_u = g.rect_mask(bay_frac, g.const(WINDOW_MARGIN_U[0]), g.const(WINDOW_MARGIN_U[1]), WINDOW_EDGE_SHARPNESS)
mask_v = g.rect_mask(floor_frac, lo_v, hi_v, WINDOW_EDGE_SHARPNESS)
window_mask = g.mul(mask_u, mask_v)

# per-window lit hash - offset by the per-building seed channel so two
# buildings never show an identical lit-window pattern even at the same
# facade UV phase.
cell_u = g.add(bay_idx, g.mul(seed_g, g.const(37.0)))
cell_v = g.add(floor_idx, g.mul(seed_g, g.const(13.0)))
cell = g.append(cell_u, cell_v)
lit_hash = g.hash2(cell)
lit_mask = g.clamp01(g.mul(g.sub(g.const(LIT_FRACTION), lit_hash), g.const(50.0)))
# never light the storefront band itself as a "lit window" (it's glazing,
# not a lamp-lit interior the same way) - only floors above ground.
above_ground = g.sub(g.const(1.0), ground_mask)
lit_mask = g.mul(lit_mask, above_ground)

glass_albedo = g.const3(*GLASS_COLOR)
glass_rough = g.const(GLASS_ROUGHNESS)
lit_color = g.const3(*LIT_COLOR)

wall_or_window_albedo = g.lerp(wall_albedo_node, glass_albedo, window_mask, a_out=wall_albedo_out)
wall_or_window_rough = g.lerp(wall_rough_node, glass_rough, window_mask, a_out=wall_rough_out)
emissive = g.mul(g.mul(lit_color, lit_mask), g.mul(window_mask, g.const(LIT_EMISSIVE_INTENSITY)))

# ---------------------------------------------------------------- roof composite
roof_albedo = g.sample(ROOF_TEXTURES["albedo"], uv)
roof_normal = g.sample(ROOF_TEXTURES["normal"], uv, is_normal=True)
roof_rough = g.rough_scalar(g.sample(ROOF_TEXTURES["rough"], uv))

# flat roofs reuse the already-sampled concrete wall set
concrete = wall_samples[WALL_LAYERS.index("Concrete034")]
roof_albedo = g.lerp(roof_albedo, concrete["albedo"], flat_roof_b, a_out="RGB", b_out="RGB")
roof_normal = g.lerp(roof_normal, concrete["normal"], flat_roof_b, a_out="RGB", b_out="RGB")
roof_rough = g.lerp(roof_rough, concrete["rough"], flat_roof_b)

final_albedo = g.lerp(wall_or_window_albedo, roof_albedo, roof_mask)
final_normal = g.lerp(wall_normal_node, roof_normal, roof_mask, a_out=wall_normal_out)
final_rough = g.lerp(wall_or_window_rough, roof_rough, roof_mask)
zero3 = g.const3(0.0, 0.0, 0.0)
final_emissive = g.lerp(emissive, zero3, roof_mask)

MEL.connect_material_property(final_albedo, "", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(final_normal, "", unreal.MaterialProperty.MP_NORMAL)
MEL.connect_material_property(final_rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.connect_material_property(final_emissive, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

MEL.recompile_material(mat)

# real bug the old placeholder script hit (still true, see its own comment,
# kept here verbatim): without an explicit save the material is created/
# compiled/logged successfully but stays transient in-memory only, vanishing
# the instant this commandlet process exits.
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# ---- VERIFY: confirm the saved graph actually wired what this script
# thinks it wired - same reload-and-check convention apply_ground_material.py/
# apply_water_material.py already established, adopted here for the same
# "headless -nullrhi mode succeeds even with a silently-unconnected required
# pin" failure class.
reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Normal", unreal.MaterialProperty.MP_NORMAL),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
    ("EmissiveColor", unreal.MaterialProperty.MP_EMISSIVE_COLOR),
):
    node = MEL.get_material_property_input_node(reloaded, prop_enum)
    if node is None:
        all_ok = False
        log(f"  MP_{prop_name}: UNCONNECTED (FAIL)")
        continue
    inputs = MEL.get_inputs_for_material_expression(reloaded, node)
    log(f"  MP_{prop_name}: driven by {node.get_class().get_name()} own_inputs={inputs}")
if not all_ok:
    raise RuntimeError(
        "M_BuildingFacade: one or more base material property pins failed to "
        "connect - see the post-save verification log above")

log(f"built + saved {MAT_NAME} - wall_layers={WALL_LAYERS}, roof_layer={ROOF_LAYER}, "
    f"facade_tile_m={FACADE_TILE_M}, floor_height_m={FLOOR_HEIGHT_M}, bay_width_m={BAY_WIDTH_M}, "
    f"lit_fraction={LIT_FRACTION}")
