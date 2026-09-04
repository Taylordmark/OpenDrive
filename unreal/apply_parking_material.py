"""Builds M_Parking, the paved-lot material for baked OSM parking-polygon
ground patches (art-direction §7.15 "paved town context" pass, 2026-09-03) -
see pipeline/build_parking_tiles.py for the mesh bake this renders and
AODParkingManager for the runtime streaming actor. Target: The Crew 2's
towns read as towns because buildings sit on paved context (parking lots,
driveways) instead of bare terrain - this is that pavement's material.

Reuses the SAME Asphalt032 CC0 PBR set unreal/apply_road_material.py already
imports for residential/service roads (content_src/textures/road/, see that
directory's manifest.json) - no new texture download needed, and picking the
already-established "patchier" road variant (not the primary/well-kept set)
reads right for an ordinary town parking lot rather than a fresh highway.
Run unreal/import_road_textures.py first if that hasn't already happened for
this project (apply_road_material.py already requires it).

UV0 is baked directly by build_parking_tiles.py as (world_m / UV_TILE_M) -
same "world-space tiling, not per-lot-normalized" convention build_water_
tiles.py's lake UVs already use, just at pavement scale (UV_TILE_M=8m there
vs. water's 20m) - so this material only needs to sample straight from UV0,
no rescale node needed here.

Faint parking-line markings: a single set of thin white stripes at a fixed
world-space period along the baked U axis, procedural (not baked into the
texture) - same idea as M_Road's lane markings (unreal/apply_road_material.py)
but far simpler, since a parking lot's baked UV carries no real stall
orientation/lane-count data the way ODR3 road chunks do. Because U is plain
world-position (not rotated to each individual lot's own axis), the stripe
direction is a fixed world axis rather than "parallel to this lot's own
rows" - an honest simplification for this pass' scope (flavor texture, not
real striping geometry), acceptable specifically because the stripes are
intentionally faint/low-contrast rather than a bold accurate line layout.

Known UE 5.8 python-binding pin-name gotchas this file works around (see
apply_ground_material.py's G class docstring for the full established list):
Power's exponent pin is "Exp"; Clamp/Abs/Floor/Frac's primary input pin is
unnamed (""); VertexColor's RGB(A) output is unnamed. Every material-property
connection is verified post-save (see the VERIFY block at the bottom) - this
codebase has a real, recurring history of silent pin-connect no-ops.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_Parking"
ROAD_TEX_DIR = "/Game/OpenDrive/Textures/Road"

ASPHALT_ASSET_ID = "Asphalt032"   # same "patchier" residential/service set apply_road_material.py uses

ROUGHNESS_MIN = 0.55   # texture roughness map is scaled into this range -
ROUGHNESS_MAX = 0.95   # worn parking-lot asphalt reads matte, never glossy

BASE_TINT = (0.80, 0.80, 0.82)   # slight cool-grey multiply over the raw albedo -
                                   # reads as slightly weathered/sun-bleached pavement

# Faint stripe markings (see module docstring for the "fixed world axis, not
# per-lot orientation" honest simplification).
STRIPE_PERIOD_UV = 3.0        # in baked UV units (UV_TILE_M=8m per unit -> ~24m world period,
                                # i.e. a handful of parking bays' worth between visible stripe sets)
STRIPE_HALF_WIDTH_UV = 0.02    # thin
STRIPE_SHARPNESS = 300.0
STRIPE_OPACITY = 0.18          # FAINT - a hint of striping texture, not bold accurate lane paint
STRIPE_COLOR = (0.72, 0.72, 0.70)


def log(msg):
    # unreal.log() doesn't reliably surface in this project's headless
    # -stdout captures (apply_water_material.py's 2026-08-27 finding) -
    # log_warning does.
    unreal.log_warning(f"PARKINGMAT {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def load_asphalt(asset_id):
    base = f"{ROAD_TEX_DIR}/{asset_id}"
    albedo = unreal.load_asset(f"{base}/T_{asset_id}_A")
    normal = unreal.load_asset(f"{base}/T_{asset_id}_N")
    rough = unreal.load_asset(f"{base}/T_{asset_id}_R")
    if albedo is None or normal is None or rough is None:
        raise RuntimeError(
            f"road texture set '{asset_id}' missing under {base} - run "
            f"unreal/import_road_textures.py first (after "
            f"content_src/textures/road/download_road_textures.py). M_Parking "
            f"deliberately reuses this set rather than importing its own.")
    return dict(albedo=albedo, normal=normal, rough=rough)


class G:
    """Trimmed node-soup helper, same shape as apply_road_material.py's own
    G class - duplicated rather than imported since these are standalone
    -run=pythonscript commandlets, not a shared package."""

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

    def absv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionAbs, a, a_out)

    def fracv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionFrac, a, a_out)

    def clamp01(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionClamp, a, a_out)

    def mask(self, a, r=False, gg=False, b=False, a_out=""):
        n = self.node(unreal.MaterialExpressionComponentMask, r=r, g=gg, b=b)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), "ComponentMask")
        return n

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), f"{cls.__name__}.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self._req(MEL.connect_material_expressions(alpha, al_out, n, "Alpha"), "Lerp.Alpha")
        return n

    def tex_coord(self, index=0):
        return self.node(unreal.MaterialExpressionTextureCoordinate, coordinate_index=index)

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

    def step(self, edge, x, edge_out="", x_out="", sharpness=1000.0):
        """clamp01((x-edge)*sharpness) - a soft Heaviside step."""
        diff = self.sub(x, edge, x_out, edge_out)
        scaled = self.mul(diff, self.const(sharpness))
        return self.clamp01(scaled)


ASPHALT = load_asphalt(ASPHALT_ASSET_ID)

mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    log("--- pre-edit M_Parking expression graph ---")
    try:
        for expr in MEL.get_material_expressions(mat):
            log(f"  {expr.get_class().get_name()} @ {MEL.get_material_expression_node_position(expr)}")
    except Exception as exc:
        log(f"  (inspection failed, proceeding with rebuild anyway: {exc})")
    log("--- end pre-edit graph ---")
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

# Two-sided safety net for untrusted OSM-derived triangle winding (same
# reasoning apply_water_material.py/apply_building_material.py already
# document for their own bakes) - build_parking_tiles.py reuses build_water_
# tiles.build_lake_mesh, which already runtime-fixes winding to face up, so
# this is defense-in-depth, not a known-necessary fix.
mat.set_editor_property("two_sided", True)
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_OPAQUE)

g = G(mat)

uv0 = g.tex_coord(0)

albedo = g.sample(ASPHALT["albedo"], uv0)
normal = g.sample(ASPHALT["normal"], uv0, is_normal=True)
rough_tex = g.sample(ASPHALT["rough"], uv0)

# ---------------------------------------------------------------- faint stripe overlay
u = g.mask(uv0, r=True)
u_frac = g.fracv(g.mul(u, g.const(1.0 / STRIPE_PERIOD_UV)))
dist_to_line = g.absv(g.sub(u_frac, g.const(0.5)))
# stripe mask: 1 near the periodic line center, 0 elsewhere - inverted step
stripe_edge = g.step(g.const(STRIPE_HALF_WIDTH_UV), dist_to_line, sharpness=STRIPE_SHARPNESS)
stripe_mask = g.sub(g.const(1.0), stripe_edge)
stripe_alpha = g.mul(stripe_mask, g.const(STRIPE_OPACITY))

base_tinted = g.mul(albedo, g.const3(*BASE_TINT), a_out="RGB")
base_color = g.lerp(base_tinted, g.const3(*STRIPE_COLOR), stripe_alpha)

rough_scalar = g.mask(rough_tex, r=True)
roughness = g.lerp(g.const(ROUGHNESS_MIN), g.const(ROUGHNESS_MAX), rough_scalar)

MEL.connect_material_property(base_color, "", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.connect_material_property(normal, "", unreal.MaterialProperty.MP_NORMAL)

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# ---- VERIFY: reload fresh and probe each property input node's own inputs -
# same discipline apply_water_material.py established after this codebase's
# real silent-pin-no-op history (see that file's own VERIFY block comment).
reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
    ("Normal", unreal.MaterialProperty.MP_NORMAL),
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
        "M_Parking: one or more base material property pins failed to connect - "
        "see the post-save verification log above (same silent-pin-no-op class "
        "of bug apply_water_material.py's own notes document)")

log(f"built + saved {MAT_NAME} (asphalt={ASPHALT_ASSET_ID}, tint={BASE_TINT}, "
    f"roughness={ROUGHNESS_MIN}-{ROUGHNESS_MAX}, stripe_period_uv={STRIPE_PERIOD_UV}, "
    f"stripe_opacity={STRIPE_OPACITY}, two_sided=True, blend=Opaque)")
