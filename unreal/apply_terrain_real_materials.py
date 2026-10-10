"""PLAN-VISUALS Track D: the Realistic twins of the terrain-side materials,
saved BESIDE the stylized ones so ODVisualStyle::Pick finds them:

  /Game/OpenDrive/Materials/M_GroundStylized_real  terrain (ODTerrainManager)
                                                    AND road-cut / retaining
                                                    walls (ODWallManager)
  /Game/OpenDrive/Materials/M_Embankment_real       embankment skirts
  /Game/OpenDrive/Materials/M_Rock030_real          boulder scatter (HISM)

Mostly the CC0 ambientCG sets the stylized versions already import
(content_src/textures/ground/manifest.json), plus four Realistic-only CC0
ambientCG sets (content_src/realistic/terrain/manifest.json, imported here to
/Game/OpenDrive/Realistic/Terrain/): Rock051 bedded cliff rock for steep
ground and cut walls, Ground076 leaf litter for the forest floor, Moss002 for
boulder moss, Gravel040 crushed stone for the road shoulder and embankment
gravel. The rest is material math:
  - anti-tiling: every planar layer is sampled twice, at its own tile and at
    a FAR_MUL-times larger, ROT_DEG-rotated, offset copy; a 17 m noise mixes
    the two up close and the far copy takes over with distance (DIST_*), so
    no single texture period is left to read as a grid at 50-200 m.
  - height-aware blends: AO maps double as height, so dirt shows in the
    grass's low spots first, rock pokes through on slope transitions, and
    snow fills crevices before it covers ridges.
  - the biome vertex tint still leads the hue (interface rule), but keeps
    TINT_KEEP of each texture's own chroma instead of the stylized full
    desaturate-and-retint.
  - rock strata: world-Z bands (warped by XY noise so they dip) tint the
    triplanar rock on steep terrain, cut walls and boulders.
  - AO goes to the AO pin; boulders get moss on up-facing sides.
  - Nanite displacement (plan item D2) on the boulders only: M_Rock030_real
    is tessellation-enabled with Rock030's height map sampled world
    triplanar (same projection and tile as its albedo), so it lands on the
    Nanite SM_RockBoulder*_real_high/_ultra twins
    (unreal/import_rock_meshes_real.py; run it after this). Non-Nanite
    meshes ignore displacement. Terrain, walls and skirts are
    UDynamicMeshComponents and can't be Nanite. UE 5.8 needs no project
    setting (r.Nanite.Tessellation defaults to 1); `r.Nanite.Tessellation 0`
    turns it off.

Interface rule: each _real material must expose every parameter its twin
has, read the twin's vertex-color and data-UV channels, and copy its physical
material and usage flags - check_interface() fails the run otherwise.
Road-edge blend (Join step J) is done on the road side: M_Road*_real's
fringe samples road_edge_blend()'s shoulder (set, tile, tint, dirt noise)
in world XY, so keep the two in step (check_terrain_real.py asserts it).

pipeline/checks/check_terrain_real.py mirrors the anti-tiling and strata math
offline (it parses the constants out of this file).

Run AFTER unreal/import_ground_textures.py and the stylized
apply_ground_material.py / apply_embankment_material.py /
apply_rock_material.py (the twins must exist to be checked against):
UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import math
import runpy
import sys
from pathlib import Path

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from test_areas import ORIGIN_HEIGHT_M

MAT_DIR = "/Game/OpenDrive/Materials"
GROUND_TEX_DIR = "/Game/OpenDrive/Textures/Ground"
REAL_TEX_DIR = "/Game/OpenDrive/Realistic/Terrain"
REAL_SRC_DIR = Path(__file__).resolve().parent.parent / "content_src" / "realistic" / "terrain"
REAL_SETS = ("Rock051", "Ground076", "Moss002", "Gravel040")   # = REAL_SRC_DIR/manifest.json
# (that manifest also fetches Rock030's height map alone -> T_Rock030_H there)

# ---- tunables (pipeline/checks/check_terrain_real.py parses these) --------
# (asset_id, near tile cm) - M_GroundStylized's sets and tiles, except the
# REAL_SETS ones
LAYERS = {
    "grass": ("Grass001", 700.0),
    "forest": ("Ground076", 400.0),
    "dirt": ("Ground037", 850.0),
    "rock": ("Rock051", 1400.0),
    "scree": ("Gravel030", 1000.0),
    "snow": ("Snow006", 1800.0),
    "boulder": ("Rock030", 260.0),
    "moss": ("Moss002", 300.0),
    "shoulder": ("Gravel040", 300.0),
}
ROCK_TINT = (0.68, 0.67, 0.72)   # Rock051 -> Rock030's mean albedo (0.31), a touch cooler
FAR_MUL = 3.7            # far copy's tile = near tile * this (incommensurate on purpose)
ROT_DEG = 37.0           # far copy's rotation
FAR_OFFSET = (0.31, 0.67)
VAR_NOISE_CM = 1700.0    # near/far mix noise cell
VAR_CONTRAST = 2.0
VAR_MAX = 0.7            # max share of the far copy up close
DIST_NEAR_CM = 2500.0    # far copy starts taking over at 25 m ...
DIST_FAR_CM = 15000.0    # ... and holds FAR_W_AT_DIST from 150 m
FAR_W_AT_DIST = 0.8      # never 1: two periods stay mixed at range too
DIST_NORMAL_FLATTEN = 0.5

TINT_KEEP = 0.35         # share of the texture's own chroma kept under the biome tint
TINT_GAIN = 2.0          # same regain as the stylized hue_from_tint
HEIGHT_BLEND = 0.6       # how far AO-as-height bends a blend's edge (0 = plain lerp)

SLOPE_SOFTNESS = 4.0
TRIPLANAR_SHARPNESS = 4.0
ROCK_FAR_MUL = 4.3       # albedo-only far triplanar copy of the cliff rock

STRATA_BAND_CM = 140.0          # main band thickness
STRATA_FINE_MUL = 3.7           # laminations inside each band
STRATA_FINE_WEIGHT = 0.35
STRATA_CONTRAST = 1.8
STRATA_WARP_CM = 6000.0         # bands dip/undulate over ~60 m ...
STRATA_WARP_AMP_CM = 400.0      # ... by up to +-2 m
STRATA_DARK = (0.72, 0.68, 0.64)
STRATA_LIGHT = (1.18, 1.10, 0.98)

# elevation bands - identical to apply_ground_material.py
TREELINE_LOW_CM = (3350.0 - ORIGIN_HEIGHT_M) * 100.0
TREELINE_HIGH_CM = (3650.0 - ORIGIN_HEIGHT_M) * 100.0
ALPINE_BARE_MAX_BLEND = 0.55
SNOW_LOW_CM = (3500.0 - ORIGIN_HEIGHT_M) * 100.0
SNOW_HIGH_CM = (3950.0 - ORIGIN_HEIGHT_M) * 100.0
SNOW_NOISE_SCALE_CM = 4000.0
SNOW_NOISE_STRENGTH = 1.3
SNOW_STEEP_REDUCTION = 0.65
FOREST_LUMA_LOW = 0.075
FOREST_LUMA_HIGH = 0.16
SPRING_GREEN_COLOR = (0.30, 0.48, 0.15)
SPRING_GREEN_MAX_BLEND = 0.3
MACRO_DIRT_SCALE_CM = 12000.0
DIRT_PATCH_STRENGTH = 0.35
PATCH_FINE_SCALE_CM = 700.0
PATCH_COARSE_SCALE_CM = 2300.0
PATCH_DRY_MUL = (1.12, 1.02, 0.78)
MACRO_FINE_SCALE_CM = 30000.0
MACRO_COARSE_SCALE_CM = 120000.0
MACRO_GREEN_MUL = (0.82, 1.06, 0.78)
MACRO_DRY_MUL = (1.06, 1.00, 0.84)
MACRO_DIRT_MUL = (0.82, 0.72, 0.60)
FAR_START_CM = 150000.0
FAR_END_CM = 800000.0
FAR_DARKEN = 0.2
FAR_SAT_BOOST = 0.2
SCREE_TINT = (0.78, 0.72, 0.62)
SAND_COLOR = (0.76, 0.69, 0.52)
SAND_ROUGHNESS = 0.55
BEACH_BAND_CM = 3.0 * 0.3048 * 100.0
SEA_LEVEL_Z_CM = -ORIGIN_HEIGHT_M * 100.0
WET_DARKEN = 0.6
WET_ROUGHNESS_FLAT = 0.5
WET_ROUGHNESS_STEEP = 0.25
# road shoulder - identical to apply_ground_material.py (C++ texture contract)
ROAD_DIST_RANGE_M = 16.0
ROAD_DIST_COVERAGE_CM = 51200.0
SHOULDER_MIN_M = 1.5
SHOULDER_MAX_M = 3.0
SHOULDER_JITTER_M = 0.5
SHOULDER_SOFT_M = 0.35
SHOULDER_TINT = (1.05, 0.98, 0.9)   # Gravel040 (mean 0.39) - slightly warm grey
SHOULDER_EDGE_FADE_CM = 8000.0

# boulders (M_Rock030_real) - world-space triplanar, so the spherical UV0
# seam of make_rock_meshes.py's boulders disappears
BOULDER_STRATA_BAND_CM = 35.0
MOSS_TINT = (0.9, 0.88, 0.9)
MOSS_UP_LO = 0.45        # normal.z where moss starts ...
MOSS_UP_HI = 0.8         # ... and where it's full
MOSS_NOISE_CM = 250.0
MOSS_MAX = 0.85
# Nanite displacement: Rock030 has no published real-world size; at the
# 260 cm boulder tile 8 cm full range (p5-p95 of the height map ~ 5.6 cm) reads as
# weathered relief without swallowing the 20-50 cm small boulders. Centre =
# the height map's mean (0.67), so displacement doesn't swell the rock.
BOULDER_DISP_CM = 8.0
BOULDER_DISP_CENTER = 0.67
BOULDER_DISP_FADE_PX = (4.0, 1.0)   # fade out once 8 cm covers < 4 px, gone at 1 px

# embankment skirt (M_Embankment_real)
EMB_TOE_DARKEN = 0.35    # UV0.V (0 top, 0.6 toe, 1 apron) -> darker toward the toe
EMB_GRAVEL_NOISE_CM = 600.0
EMB_GRAVEL_MAX = 0.55
EMB_GRASS_TOE_V = 0.55   # grass creeps up from the toe apron
EMB_GRASS_SOFT_V = 0.25
EMB_GRASS_NOISE_CM = 400.0


def log(msg):
    unreal.log_warning(f"TERRAINREAL {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
SHARED_WRAP = unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SETTINGS


def load_set(asset_id):
    base = f"{REAL_TEX_DIR if asset_id in REAL_SETS else GROUND_TEX_DIR}/{asset_id}"
    out = {k: unreal.load_asset(f"{base}/T_{asset_id}_{k}") for k in ("A", "N", "R", "AO")}
    missing = [k for k, v in out.items() if v is None]
    if missing:
        raise RuntimeError(f"{asset_id} maps {missing} missing under {base} - run "
                           f"unreal/import_ground_textures.py first")
    return out


# the Realistic-only sets, same importer as the stylized ground sets
runpy.run_path(str(Path(__file__).resolve().parent / "import_ground_textures.py"),
               init_globals={"SRC_DIR": str(REAL_SRC_DIR), "TEX_DIR": REAL_TEX_DIR})
SETS = {key: dict(load_set(aid), tile=tile) for key, (aid, tile) in LAYERS.items()}
SETS["boulder"]["H"] = unreal.load_asset(f"{REAL_TEX_DIR}/Rock030/T_Rock030_H")
if SETS["boulder"]["H"] is None:
    raise RuntimeError("T_Rock030_H missing - run download_ground_textures.py on "
                       f"{REAL_SRC_DIR / 'manifest.json'} first")


class G:
    """Node-soup helper; same UE 5.8 pin-name workarounds as
    apply_ground_material.py's G (unnamed primary pin on unary nodes, ""
    for VertexColor RGB, Power's "Exp", TextureSample "UVs"/"Coordinates")."""

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 200
        n = MEL.create_material_expression(self.mat, cls, -2200 + (self._y % 1600) * 2, self._y // 8)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def c(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def c2(self, a, b):
        return self.node(unreal.MaterialExpressionConstant2Vector, r=a, g=b)

    def c3(self, r, gg, b):
        return self.node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(r, gg, b, 1.0))

    @staticmethod
    def req(ok, what):
        if not ok:
            raise RuntimeError(f"could not connect {what} - pin name mismatch?")

    def unary(self, cls, a, a_out=""):
        n = self.node(cls)
        self.req(MEL.connect_material_expressions(a, a_out, n, ""), cls.__name__)
        return n

    def absv(self, a):
        return self.unary(unreal.MaterialExpressionAbs, a)

    def floorv(self, a):
        return self.unary(unreal.MaterialExpressionFloor, a)

    def fracv(self, a):
        return self.unary(unreal.MaterialExpressionFrac, a)

    def sinev(self, a):
        return self.unary(unreal.MaterialExpressionSine, a)

    def sat(self, a):
        return self.unary(unreal.MaterialExpressionClamp, a)

    def desat(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionDesaturation, a, a_out)

    def mask(self, a, r=False, gg=False, b=False, a_out=""):
        n = self.node(unreal.MaterialExpressionComponentMask, r=r, g=gg, b=b, a=False)
        self.req(MEL.connect_material_expressions(a, a_out, n, ""), "ComponentMask")
        return n

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self.req(MEL.connect_material_expressions(a, a_out, n, "A"), f"{cls.__name__}.A")
        self.req(MEL.connect_material_expressions(b, b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def div(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDivide, a, b, a_out, b_out)

    def dot(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDotProduct, a, b, a_out, b_out)

    def append(self, a, b):
        return self.binop(unreal.MaterialExpressionAppendVector, a, b)

    def maxv(self, a, b):
        return self.binop(unreal.MaterialExpressionMax, a, b)

    def minv(self, a, b):
        return self.binop(unreal.MaterialExpressionMin, a, b)

    def power(self, base, exp):
        n = self.node(unreal.MaterialExpressionPower)
        self.req(MEL.connect_material_expressions(base, "", n, "Base"), "Power.Base")
        self.req(MEL.connect_material_expressions(exp, "", n, "Exp"), "Power.Exp")
        return n

    def lerp(self, a, b, alpha, a_out="", b_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self.req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self.req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self.req(MEL.connect_material_expressions(alpha, "", n, "Alpha"), "Lerp.Alpha")
        return n

    def ramp(self, x, lo, hi):
        """saturate((x - lo) / (hi - lo)) - a linear step."""
        return self.sat(self.div(self.sub(x, self.c(lo)), self.c(hi - lo)))

    def contrast(self, x, k):
        return self.sat(self.add(self.mul(self.sub(x, self.c(0.5)), self.c(k)), self.c(0.5)))

    def scalar_param(self, name, default):
        n = self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name)
        n.set_editor_property("default_value", default)
        return n

    def sample(self, tex, uv, kind):
        """kind: A (sRGB colour), N (normal), R/AO (linear masks)."""
        stype = {"A": unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
                 "N": unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL,
                 "H": unreal.MaterialSamplerType.SAMPLERTYPE_LINEAR_GRAYSCALE}.get(
            kind, unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex, sampler_type=stype,
                      sampler_source=SHARED_WRAP)
        self.req(any(MEL.connect_material_expressions(uv, "", n, p) for p in ("UVs", "Coordinates")),
                 "TextureSample UV")
        return n

    # ---- procedural helpers --------------------------------------------
    def wrap(self, uv2, period=97.0):
        """Keep the sin-hash input small (see apply_ground_material.py's
        wrap_lattice_uv for the GPU precision story)."""
        return self.mul(self.fracv(self.mul(uv2, self.c(1.0 / period))), self.c(period))

    def value_noise(self, uv2, seed=(0.0, 0.0)):
        """Same lattice value noise as apply_ground_material.py; ~[0,1]."""
        if seed != (0.0, 0.0):
            uv2 = self.add(uv2, self.c2(*seed))
        i = self.floorv(uv2)
        f = self.fracv(uv2)

        def h(v):
            return self.fracv(self.mul(self.sinev(self.dot(v, self.c2(12.9898, 78.233))), self.c(43758.5453)))

        top = self.lerp(h(i), h(self.add(i, self.c2(1.0, 0.0))), self.mask(f, r=True))
        bot = self.lerp(h(self.add(i, self.c2(0.0, 1.0))), h(self.add(i, self.c2(1.0, 1.0))), self.mask(f, r=True))
        return self.lerp(top, bot, self.mask(f, gg=True))

    def noise_xy(self, xy, cell_cm, seed):
        return self.value_noise(self.wrap(self.mul(xy, self.c(1.0 / cell_cm))), seed)

    def height_blend(self, alpha, h_a, h_b):
        """Weight of B: alpha + (hB - hA) * HEIGHT_BLEND * 4 alpha (1 - alpha),
        saturated. Exact at alpha 0 and 1, same average coverage as a lerp,
        but the edge follows whichever surface sits higher."""
        bend = self.mul(self.mul(self.sub(h_b, h_a), self.c(4.0 * HEIGHT_BLEND)),
                        self.mul(alpha, self.sub(self.c(1.0), alpha)))
        return self.sat(self.add(alpha, bend))


def blend_layers(g, a, b, t):
    """Lerp every channel of two layer dicts by scalar t."""
    return {k: g.lerp(a[k], b[k], t) for k in ("albedo", "normal", "rough", "ao")}


def mul_albedo(g, layer, tint):
    return dict(layer, albedo=g.mul(layer["albedo"], tint))


def strata(g, world_pos, world_xy, band_cm, seed):
    """Albedo multiplier (vec3) + band value: world-Z rock layers, warped by
    XY noise so they dip and undulate instead of reading as contour lines."""
    warp = g.mul(g.sub(g.noise_xy(world_xy, STRATA_WARP_CM, seed), g.c(0.5)), g.c(STRATA_WARP_AMP_CM))
    zs = g.div(g.add(g.mask(world_pos, b=True), warp), g.c(band_cm))
    coarse = g.value_noise(g.wrap(g.append(zs, g.c(seed[0]))), (0.0, 0.0))
    fine = g.value_noise(g.wrap(g.append(g.mul(zs, g.c(STRATA_FINE_MUL)), g.c(seed[1]))), (0.0, 0.0))
    band = g.contrast(g.lerp(coarse, fine, g.c(STRATA_FINE_WEIGHT)), STRATA_CONTRAST)
    return g.lerp(g.c3(*STRATA_DARK), g.c3(*STRATA_LIGHT), band), band


def triplanar(g, s, world_pos, normal, tile_cm, dist_t=None, far_mul=None):
    """World triplanar sample of a set (axis blend, same simplification as
    the stylized cliff). far_mul adds an albedo-only far copy mixed by
    distance (DIST_*), which breaks the cliff's 14 m repeat at range."""
    abs_n = g.absv(normal)
    pow_n = g.power(abs_n, g.c(TRIPLANAR_SHARPNESS))
    sum_n = g.dot(pow_n, g.c3(1.0, 1.0, 1.0))
    w = [g.div(g.mask(pow_n, r=i == 0, gg=i == 1, b=i == 2), sum_n) for i in range(3)]

    def proj(scale):
        k = g.c(1.0 / scale)
        return [g.mul(g.mask(world_pos, gg=True, b=True), k),    # X-facing: YZ
                g.mul(g.mask(world_pos, r=True, b=True), k),     # Y-facing: XZ
                g.mul(g.mask(world_pos, r=True, gg=True), k)]    # Z-facing: XY

    def blend3(nodes, out=""):
        return g.add(g.add(g.mul(nodes[0], w[0], a_out=out), g.mul(nodes[1], w[1], a_out=out)),
                     g.mul(nodes[2], w[2], a_out=out))

    uvs = proj(tile_cm)
    out = dict(
        albedo=blend3([g.sample(s["A"], uv, "A") for uv in uvs], "RGB"),
        normal=blend3([g.sample(s["N"], uv, "N") for uv in uvs], "RGB"),
        rough=blend3([g.mask(g.sample(s["R"], uv, "R"), r=True, a_out="RGB") for uv in uvs]),
        ao=blend3([g.mask(g.sample(s["AO"], uv, "AO"), r=True, a_out="RGB") for uv in uvs]),
    )
    if "H" in s:
        out["h"] = blend3([g.mask(g.sample(s["H"], uv, "H"), r=True, a_out="RGB") for uv in uvs])
    if far_mul:
        far = blend3([g.sample(s["A"], uv, "A") for uv in proj(tile_cm * far_mul)], "RGB")
        out["albedo"] = g.lerp(out["albedo"], far, g.mul(dist_t, g.c(FAR_W_AT_DIST)))
    return out


def planar_layer(g, s, world_xy, var_w, dist_t, tile_cm=None):
    """Anti-tiled planar sample: near copy + rotated/offset far copy, mixed by
    var_w (noise up close) toward FAR_W_AT_DIST with distance. The far
    normal is rotated back by -ROT_DEG so its lighting direction stays right."""
    tile = tile_cm or s["tile"]
    ang = math.radians(ROT_DEG)
    ca, sa = math.cos(ang), math.sin(ang)
    uv_n = g.mul(world_xy, g.c(1.0 / tile))
    scaled = g.mul(world_xy, g.c(1.0 / (tile * FAR_MUL)))
    uv_f = g.add(g.append(g.dot(scaled, g.c2(ca, -sa)), g.dot(scaled, g.c2(sa, ca))), g.c2(*FAR_OFFSET))
    w = g.lerp(var_w, g.c(FAR_W_AT_DIST), dist_t)

    def pair(kind, out):
        near = g.sample(s[kind], uv_n, kind)
        far = g.sample(s[kind], uv_f, kind)
        if kind in ("R", "AO"):
            near, far = g.mask(near, r=True, a_out="RGB"), g.mask(far, r=True, a_out="RGB")
            out = ""
        return near, far, out

    a_n, a_f, o = pair("A", "RGB")
    albedo = g.lerp(a_n, a_f, w, a_out=o, b_out=o)
    n_n, n_f, o = pair("N", "RGB")
    nf_xy = g.mask(n_f, r=True, gg=True, a_out="RGB")
    n_f_rot = g.append(g.append(g.dot(nf_xy, g.c2(ca, sa)), g.dot(nf_xy, g.c2(-sa, ca))),
                       g.mask(n_f, b=True, a_out="RGB"))
    normal = g.lerp(n_n, n_f_rot, w, a_out="RGB")
    normal = g.lerp(normal, g.c3(0.0, 0.0, 1.0), g.mul(dist_t, g.c(DIST_NORMAL_FLATTEN)))
    r_n, r_f, _ = pair("R", "")
    ao_n, ao_f, _ = pair("AO", "")
    return dict(albedo=albedo, normal=normal, rough=g.lerp(r_n, r_f, w), ao=g.lerp(ao_n, ao_f, w))


def shared_signals(g):
    wp = g.node(unreal.MaterialExpressionWorldPosition)
    xy = g.mask(wp, r=True, gg=True)
    nrm = g.node(unreal.MaterialExpressionVertexNormalWS)
    depth = g.node(unreal.MaterialExpressionPixelDepth)
    dist_t = g.ramp(depth, DIST_NEAR_CM, DIST_FAR_CM)
    var_w = g.mul(g.contrast(g.noise_xy(xy, VAR_NOISE_CM, (17.0, 3.0)), VAR_CONTRAST), g.c(VAR_MAX))
    return wp, xy, nrm, depth, dist_t, var_w


def new_material(name):
    """Recreated from scratch each run: delete_all_material_expressions left
    ~50% stale nodes behind on a rebuild (1163 -> 1744 nodes). Only _real
    assets come through here, never a stylized one."""
    assert name.endswith("_real")
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
    return tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew()), path


USAGE_FLAGS = ("used_with_instanced_static_meshes", "used_with_nanite", "used_with_skeletal_mesh",
               "used_with_static_lighting", "used_with_spline_meshes", "used_with_morph_targets")


def copy_twin_settings(twin, mat):
    """Physical material, shading basics and usage flags come from the twin."""
    for prop in ("phys_material", "blend_mode", "two_sided") + USAGE_FLAGS:
        mat.set_editor_property(prop, twin.get_editor_property(prop))


def param_names(mat):
    names = set()
    for fn in (MEL.get_scalar_parameter_names, MEL.get_vector_parameter_names,
               MEL.get_texture_parameter_names, MEL.get_static_switch_parameter_names):
        names |= {str(n) for n in fn(mat)}
    for e in MEL.get_material_expressions(mat):   # double-vector params aren't listed above
        if isinstance(e, unreal.MaterialExpressionDoubleVectorParameter):
            names.add(str(e.get_editor_property("parameter_name")))
    return names


def channels(mat):
    """(reads vertex color, set of UV indices read)."""
    exprs = MEL.get_material_expressions(mat)
    vc = any(isinstance(e, unreal.MaterialExpressionVertexColor) for e in exprs)
    uvs = {e.get_editor_property("coordinate_index") for e in exprs
           if isinstance(e, unreal.MaterialExpressionTextureCoordinate)}
    return vc, uvs


def check_interface(twin_path, real_path, data_uvs):
    twin, real = unreal.load_asset(twin_path), unreal.load_asset(real_path)
    problems = []
    missing = param_names(twin) - param_names(real)
    if missing:
        problems.append(f"missing params {sorted(missing)}")
    (tvc, tuv), (rvc, ruv) = channels(twin), channels(real)
    if tvc and not rvc:
        problems.append("twin reads vertex color, _real doesn't")
    if not (tuv & data_uvs) <= ruv:
        problems.append(f"twin reads data UV{sorted((tuv & data_uvs) - ruv)}, _real doesn't")
    if twin.get_editor_property("phys_material") != real.get_editor_property("phys_material"):
        problems.append("phys_material differs")
    for prop in USAGE_FLAGS:   # _real may add a usage (Nanite boulders), never drop one
        if twin.get_editor_property(prop) and not real.get_editor_property(prop):
            problems.append(f"{prop} missing")
    for name in sorted(set(OUT_PINS.values()) - connected_outputs(real)):
        problems.append(f"{name} unconnected")
    if problems:
        raise RuntimeError(f"interface rule FAILED for {real_path}: {problems}")
    log(f"interface OK {real_path}: params={sorted(param_names(real))} vertex_color={rvc} uv={sorted(ruv)} "
        f"phys={real.get_editor_property('phys_material')}")


OUT_PINS = {"albedo": "BaseColor", "normal": "Normal", "rough": "Roughness", "ao": "AmbientOcclusion"}
_MP = unreal.MaterialProperty
PIN_PROPS = {"BaseColor": _MP.MP_BASE_COLOR, "Normal": _MP.MP_NORMAL, "Roughness": _MP.MP_ROUGHNESS,
             "AmbientOcclusion": _MP.MP_AMBIENT_OCCLUSION}


def connected_outputs(mat):
    """Names of the connected main outputs (OUT_PINS values, + Displacement),
    whether wired directly or through a MakeMaterialAttributes node."""
    if mat.get_editor_property("use_material_attributes"):
        mma = MEL.get_material_property_input_node(mat, unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES)
        if mma is None:
            return set()
        return {str(n) for n, e in zip(MEL.get_material_expression_input_names(mma),
                                       MEL.get_inputs_for_material_expression(mat, mma)) if e}
    return {n for n, p in PIN_PROPS.items() if MEL.get_material_property_input_node(mat, p)}


def finish(g, path, twin_path, out, data_uvs=frozenset(), props=None):
    """data_uvs: UV channels the C++ writes data into (embankment UV0.V);
    plain geometry UVs (boulder UV0) may be dropped for world projection.
    props: material properties set after the twin's (e.g. tessellation).
    out["h"] (displacement) goes through MakeMaterialAttributes: UE 5.8's
    Python hides MP_Displacement from the MaterialProperty enum."""
    if "h" in out:
        g.mat.set_editor_property("use_material_attributes", True)
        mma = g.node(unreal.MaterialExpressionMakeMaterialAttributes)
        for key, pin in dict(OUT_PINS, h="Displacement").items():
            G.req(MEL.connect_material_expressions(out[key], "", mma, pin), f"{path} {pin}")
        G.req(MEL.connect_material_property(mma, "", unreal.MaterialProperty.MP_MATERIAL_ATTRIBUTES),
              f"{path} attributes")
    else:
        for key, pin in OUT_PINS.items():
            G.req(MEL.connect_material_property(out[key], "", PIN_PROPS[pin]), f"{path} {key}")
    copy_twin_settings(unreal.load_asset(twin_path), g.mat)
    for k, v in (props or {}).items():
        g.mat.set_editor_property(k, v)
    MEL.recompile_material(g.mat)
    if not unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {path}")
    check_interface(twin_path, path, data_uvs)
    if "h" in out and "Displacement" not in connected_outputs(g.mat):
        raise RuntimeError(f"{path}: Displacement unconnected")
    log(f"saved {path} ({len(MEL.get_material_expressions(g.mat))} nodes)")


# =========================================================== M_GroundStylized_real
def build_ground():
    mat, path = new_material("M_GroundStylized_real")
    g = G(mat)
    wp, xy, nrm, depth, dist_t, var_w = shared_signals(g)
    z = g.mask(wp, b=True)
    one = g.c(1.0)
    flat_w = g.power(g.sat(g.dot(nrm, g.c3(0.0, 0.0, 1.0))), g.c(SLOPE_SOFTNESS))
    steep_w = g.sub(one, flat_w)

    vtint = g.node(unreal.MaterialExpressionVertexColor)
    luma = g.dot(vtint, g.c3(0.2126, 0.7152, 0.0722))

    L = {k: planar_layer(g, SETS[k], xy, var_w, dist_t) for k in ("grass", "forest", "dirt", "scree", "snow")}

    def biome_tint(albedo):
        """Vertex tint leads the hue (stylized desaturate-and-retint), then
        TINT_KEEP of the texture's per-pixel chroma ratio is put back so
        blades and soil keep their own colour variation."""
        grey = g.desat(albedo)
        retinted = g.mul(g.mul(grey, g.c(TINT_GAIN)), vtint)
        chroma = g.div(albedo, g.maxv(grey, g.c(0.02)))
        return g.mul(retinted, g.lerp(g.c3(1.0, 1.0, 1.0), chroma, g.c(TINT_KEEP)))

    grass = L["grass"] | {"albedo": biome_tint(L["grass"]["albedo"])}
    forest = L["forest"] | {"albedo": biome_tint(L["forest"]["albedo"])}
    scree = mul_albedo(g, L["scree"], g.c3(*SCREE_TINT))

    forest_w = g.sat(g.div(g.sub(g.c(FOREST_LUMA_HIGH), luma), g.c(FOREST_LUMA_HIGH - FOREST_LUMA_LOW)))
    veg = blend_layers(g, grass, forest, forest_w)

    # dirt: 120 m trails + 7/23 m patches, height-blended into the grass's low spots
    patch = g.contrast(g.lerp(g.noise_xy(xy, PATCH_FINE_SCALE_CM, (23.0, 41.0)),
                              g.noise_xy(xy, PATCH_COARSE_SCALE_CM, (59.0, 11.0)), g.c(0.6)), 1.8)
    dirt_alpha = g.sat(g.add(g.mul(g.noise_xy(xy, MACRO_DIRT_SCALE_CM, (0.0, 0.0)), g.c(DIRT_PATCH_STRENGTH)),
                             g.mul(g.ramp(patch, 0.78, 0.95), g.c(0.75))))
    veg = blend_layers(g, veg, L["dirt"], g.height_blend(dirt_alpha, veg["ao"], L["dirt"]["ao"]))
    veg["albedo"] = g.lerp(veg["albedo"], g.mul(veg["albedo"], g.c3(*PATCH_DRY_MUL)), g.ramp(patch, 0.5, 0.72))

    spring = g.mul(g.scalar_param("SpringGreen", 0.0), g.c(SPRING_GREEN_MAX_BLEND))
    veg["albedo"] = g.lerp(veg["albedo"], g.c3(*SPRING_GREEN_COLOR), spring)

    # 300 m / 1200 m macro variants + distant saturation/darkening (V3, as stylized)
    macro = g.contrast(g.lerp(g.noise_xy(xy, MACRO_FINE_SCALE_CM, (13.0, 57.0)),
                              g.noise_xy(xy, MACRO_COARSE_SCALE_CM, (71.0, 29.0)), g.c(0.6)), 2.2)
    variant = g.lerp(g.lerp(g.c3(*MACRO_GREEN_MUL), g.c3(*MACRO_DRY_MUL), g.sat(g.mul(macro, g.c(2.0)))),
                     g.c3(*MACRO_DIRT_MUL), g.sat(g.sub(g.mul(macro, g.c(2.0)), one)))
    veg["albedo"] = g.mul(veg["albedo"], variant)
    far_t = g.ramp(depth, FAR_START_CM, FAR_END_CM)
    veg["albedo"] = g.lerp(g.desat(veg["albedo"]), veg["albedo"], g.add(one, g.mul(far_t, g.c(FAR_SAT_BOOST))))
    veg["albedo"] = g.mul(veg["albedo"], g.sub(one, g.mul(far_t, g.c(FAR_DARKEN))))

    treeline_t = g.ramp(z, TREELINE_LOW_CM, TREELINE_HIGH_CM)
    veg = blend_layers(g, veg, scree, g.height_blend(g.mul(treeline_t, g.c(ALPINE_BARE_MAX_BLEND)),
                                                     veg["ao"], scree["ao"]))

    # steep: triplanar cliff rock with strata, scree above treeline
    rock = triplanar(g, SETS["rock"], wp, nrm, SETS["rock"]["tile"], dist_t=dist_t, far_mul=ROCK_FAR_MUL)
    strata_mul, band = strata(g, wp, xy, STRATA_BAND_CM, (5.0, 9.0))
    rock["albedo"] = g.mul(g.mul(rock["albedo"], g.c3(*ROCK_TINT)), strata_mul)
    rock["rough"] = g.sat(g.add(rock["rough"], g.mul(g.sub(band, g.c(0.5)), g.c(-0.12))))
    steep = blend_layers(g, rock, scree, treeline_t)

    base = blend_layers(g, steep, veg, g.height_blend(flat_w, steep["ao"], veg["ao"]))
    base = road_edge_blend(g, base, xy, flat_w, L["dirt"])

    # sand / beach (stylized parity; no-op inland)
    sand = g.mul(g.sat(g.div(g.sub(g.c(BEACH_BAND_CM), g.sub(z, g.scalar_param("SeaLevelZCm", SEA_LEVEL_Z_CM))),
                             g.c(BEACH_BAND_CM))), flat_w)
    base["albedo"] = g.lerp(base["albedo"], g.c3(*SAND_COLOR), sand)
    base["rough"] = g.lerp(base["rough"], g.c(SAND_ROUGHNESS), sand)

    # wetness: soil darkens and soaks it up (more in crevices), rock gets slick
    wet = g.scalar_param("Wetness", 0.0)
    base["albedo"] = g.mul(base["albedo"], g.lerp(one, g.c(WET_DARKEN), wet))
    wet_rough = g.lerp(g.c(WET_ROUGHNESS_FLAT), g.c(WET_ROUGHNESS_STEEP), steep_w)
    base["rough"] = g.lerp(base["rough"], g.minv(base["rough"], wet_rough), wet)

    # snow: same altitude params, noise patches, fills crevices first
    lo, hi = g.scalar_param("SnowLineLowZCm", SNOW_LOW_CM), g.scalar_param("SnowLineHighZCm", SNOW_HIGH_CM)
    snow_t = g.sat(g.add(g.sat(g.div(g.sub(z, lo), g.sub(hi, lo))),
                         g.mul(g.sub(g.noise_xy(xy, SNOW_NOISE_SCALE_CM, (91.0, 37.0)), g.c(0.5)),
                               g.c(SNOW_NOISE_STRENGTH))))
    snow_amt = g.mul(snow_t, g.sub(one, g.mul(steep_w, g.c(SNOW_STEEP_REDUCTION))))
    out = blend_layers(g, base, L["snow"], g.height_blend(snow_amt, base["ao"], g.sub(g.c(1.1), base["ao"])))
    out["albedo"] = g.mul(out["albedo"], g.lerp(g.c(0.85), one, out["ao"]))   # cavity darkening
    finish(g, path, f"{MAT_DIR}/M_GroundStylized", out)


def road_edge_blend(g, base, xy, flat_w, dirt):
    """Gravel/dirt shoulder from the C++ RoadDistTex field (contract identical
    to M_GroundStylized). apply_road_material.py's RealEdge* fringe mirrors it."""
    white = unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture")
    origin = g.node(unreal.MaterialExpressionDoubleVectorParameter, parameter_name="RoadDistOrigin")
    cov = g.c(ROAD_DIST_COVERAGE_CM)
    rel = g.div(g.sub(xy, g.mask(origin, r=True, gg=True)), cov)
    tex = g.node(unreal.MaterialExpressionTextureSampleParameter2D, parameter_name="RoadDistTex", texture=white)
    G.req(any(MEL.connect_material_expressions(g.add(rel, g.c2(0.5, 0.5)), "", tex, p)
              for p in ("UVs", "Coordinates")), "RoadDistTex UV")
    road_d = g.mul(g.mask(tex, r=True, a_out="RGB"), g.c(ROAD_DIST_RANGE_M))
    ab = g.absv(rel)
    edge = g.sat(g.mul(g.sub(g.c(0.5), g.maxv(g.mask(ab, r=True), g.mask(ab, gg=True))),
                       g.div(cov, g.c(SHOULDER_EDGE_FADE_CM))))
    wide = g.noise_xy(xy, 4000.0, (37.0, 83.0))
    fine = g.noise_xy(xy, 150.0, (5.0, 67.0))
    width = g.add(g.lerp(g.c(SHOULDER_MIN_M), g.c(SHOULDER_MAX_M), wide),
                  g.mul(g.sub(fine, g.c(0.5)), g.c(2.0 * SHOULDER_JITTER_M)))
    m = g.sat(g.div(g.sub(g.add(width, g.c(SHOULDER_SOFT_M)), road_d), g.c(2.0 * SHOULDER_SOFT_M)))
    m = g.mul(g.mul(m, edge), g.sat(g.mul(flat_w, g.c(2.0))))
    s = SETS["shoulder"]
    uv = g.mul(xy, g.c(1.0 / s["tile"]))
    gravel = g.sample(s["A"], uv, "A")
    sh = dict(
        albedo=g.lerp(g.mul(gravel, g.c3(*SHOULDER_TINT), a_out="RGB"), g.mul(dirt["albedo"], g.c(0.9)),
                      g.sat(g.mul(g.sub(fine, g.c(0.3)), g.c(2.5)))),
        normal=g.sample(s["N"], uv, "N"),
        rough=g.c(0.9),
        ao=g.mask(g.sample(s["AO"], uv, "AO"), r=True, a_out="RGB"),
    )
    return {k: g.lerp(base[k], sh[k], m, b_out="RGB" if k == "normal" else "") for k in base}


# =========================================================== M_Embankment_real
def build_embankment():
    mat, path = new_material("M_Embankment_real")
    g = G(mat)
    wp, xy, nrm, depth, dist_t, var_w = shared_signals(g)
    dirt = planar_layer(g, SETS["dirt"], xy, var_w, dist_t)
    gravel = mul_albedo(g, planar_layer(g, SETS["shoulder"], xy, var_w, dist_t, tile_cm=450.0), g.c3(*SHOULDER_TINT))
    grass = planar_layer(g, SETS["grass"], xy, var_w, dist_t)
    v = g.mask(g.node(unreal.MaterialExpressionTextureCoordinate, coordinate_index=0), gg=True)

    out = blend_layers(g, dirt, gravel, g.height_blend(
        g.mul(g.noise_xy(xy, EMB_GRAVEL_NOISE_CM, (7.0, 19.0)), g.c(EMB_GRAVEL_MAX)), dirt["ao"], gravel["ao"]))
    grass_alpha = g.sat(g.add(g.ramp(v, EMB_GRASS_TOE_V - EMB_GRASS_SOFT_V, EMB_GRASS_TOE_V + EMB_GRASS_SOFT_V),
                              g.mul(g.sub(g.noise_xy(xy, EMB_GRASS_NOISE_CM, (29.0, 2.0)), g.c(0.5)), g.c(0.6))))
    out = blend_layers(g, out, grass, g.height_blend(grass_alpha, out["ao"], grass["ao"]))
    out["albedo"] = g.lerp(out["albedo"], g.c3(0.0, 0.0, 0.0), g.mul(v, g.c(EMB_TOE_DARKEN)))
    out["albedo"] = g.mul(out["albedo"], g.lerp(g.c(0.85), g.c(1.0), out["ao"]))
    finish(g, path, f"{MAT_DIR}/M_Embankment", out, data_uvs={0})


# =========================================================== M_Rock030_real
def build_rock():
    mat, path = new_material("M_Rock030_real")
    g = G(mat)
    wp, xy, nrm, depth, dist_t, var_w = shared_signals(g)
    rock = triplanar(g, SETS["boulder"], wp, nrm, SETS["boulder"]["tile"])
    strata_mul, band = strata(g, wp, xy, BOULDER_STRATA_BAND_CM, (3.0, 13.0))
    rock["albedo"] = g.mul(rock["albedo"], strata_mul)
    s = SETS["moss"]
    moss_uv = g.mul(xy, g.c(1.0 / s["tile"]))
    moss = dict(albedo=g.mul(g.sample(s["A"], moss_uv, "A"), g.c3(*MOSS_TINT), a_out="RGB"),
                normal=g.sample(s["N"], moss_uv, "N"),
                rough=g.mask(g.sample(s["R"], moss_uv, "R"), r=True, a_out="RGB"),
                ao=g.mask(g.sample(s["AO"], moss_uv, "AO"), r=True, a_out="RGB"))
    moss["normal"] = g.mask(moss["normal"], r=True, gg=True, b=True, a_out="RGB")
    up = g.ramp(g.mask(nrm, b=True), MOSS_UP_LO, MOSS_UP_HI)
    moss_alpha = g.mul(g.mul(up, g.contrast(g.noise_xy(xy, MOSS_NOISE_CM, (43.0, 31.0)), 1.6)), g.c(MOSS_MAX))
    out = blend_layers(g, rock, moss, g.height_blend(moss_alpha, rock["ao"], g.sub(g.c(1.0), rock["ao"])))
    out["albedo"] = g.mul(out["albedo"], g.lerp(g.c(0.8), g.c(1.0), out["ao"]))
    out["h"] = rock["h"]
    disp = unreal.DisplacementScaling()
    disp.set_editor_property("magnitude", BOULDER_DISP_CM)
    disp.set_editor_property("center", BOULDER_DISP_CENTER)
    fade = unreal.DisplacementFadeRange()
    fade.set_editor_property("start_size_pixels", BOULDER_DISP_FADE_PX[0])
    fade.set_editor_property("end_size_pixels", BOULDER_DISP_FADE_PX[1])
    finish(g, path, f"{MAT_DIR}/M_Rock030", out, props=dict(
        used_with_nanite=True, enable_tessellation=True, displacement_scaling=disp,
        enable_displacement_fade=True, displacement_fade_range=fade))


build_ground()
build_embankment()
build_rock()
log("DONE")
