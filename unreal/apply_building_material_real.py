"""PLAN-VISUALS Track G: M_BuildingFacade_real, the Realistic twin of
M_BuildingFacade (unreal/apply_building_material.py), saved beside it so
ODVisualStyle::Pick (AODBuildingManager::BeginPlay) finds it, and
M_BuildingRoof_real, which has no stylized twin: in Realistic,
AODBuildingManager puts roof triangles (normal Z > ROOF_MASK_THRESHOLD) in a
second mesh section with this material, so walls sample only the 7 wall sets
(21 samples per pixel) and roofs only the roof sets (12), instead of one
material sampling all 24 everywhere. Stylized keeps one section and the
twin's single material.

Same interface as the twin: UV0 (U = perimeter / 4 m, V = height / 4 m from
pipeline/build_building_mesh.py, planar XY / 4 m on the roof cap), vertex
color R = wall set bucket (WallSet encoding, (idx + 0.5) / 7), G = per-
building seed, B = facade archetype in Realistic ((idx + 0.5) / 5, see
FACADE_FLOORS; the stylized twin reads B as flat roof), A = 0 on pitched-roof gable end walls (no
windows; pitched in the pipeline, building_roofs.py). Roof triangles carry
their own colour instead: RGB = the roof's linear albedo, A = roof set
bucket (ROOF_LAYERS, (idx + 0.5) / 4), both from build_building_mesh.py
roof_look. No parameters (the
twin has none), same physical material, two-sidedness and usage flags
(copied from the twin).

What's added over the stylized graph:
  - walls: the same five CC0 sets, but painted siding and plaster get a per-
    building paint colour (PAINTS, keeping the texture's own detail), every
    building a small brightness jitter, a 3 m weathering noise, ground splash
    at the wall foot and rain streaks under each sill.
  - windows: frames, a mullion and transom on upper floors, sills; flat
    normals on glass and trim. Glass is dark and glossy by day (sky
    reflections), some windows show drawn blinds. At night a hash-picked share
    of rooms lights up (LIT_MAX_FRACTION), switching on progressively as the
    sun sets, warm/neutral/cool, LIT_NITS each. Lit windows show the room
    behind them: a parallax interior map (one Custom node, no textures) ray-
    casts a bay x floor x ROOM_DEPTH_M box per window - back wall, side walls,
    ceiling, floor shaded INTERIOR_SHADE_MIN..1, wall/floor tint and depth
    hashed per window, luma renormalised so the exposure check stays exact.
    Unlit windows stay glass.
  - facade archetypes (realism B2), picked per building by the pipeline:
    punched windows, house (garage / entry doors on the ground floor),
    storefront (tall glazed ground floor, sign band, shop doors, lit at
    night), glass curtain wall (mullions, spandrels, tinted glass) and
    balcony tower (slab, railing and shaded recess per bay column), each
    with its own ground / upper floor heights and bay width.
  - roofs (M_BuildingRoof_real, realism B3): the pipeline picks the set
    per building and region (clay tile, asphalt shingle, painted steel,
    flat concrete) and the colour (OSM roof:colour, else a regional
    palette); the texture only adds its detail, albedo = vertex RGB x the
    texture's luma over its mean (ROOF_MEAN_LUMA), clamped ROOF_DETAIL.
Night comes from the sun itself: SkyAtmosphereLightDirection(0) is the sun
(AODAtmosphereController keeps it atmosphere light 0), and the material
applies AODDayNightController::NightFactor() to its elevation - so windows
switch on with the night grade, at any frame rate, with nothing to drive.
Without a sky atmosphere the engine returns straight up (day), so windows
stay dark rather than lit by day. pipeline/check_night_exposure.py parses
the NIGHT_* and LIT_* constants below and checks lit windows against the
night exposure.

New CC0 sets: content_src/realistic/buildings/manifest.json, fetched by
`python content_src/textures/ground/download_ground_textures.py
content_src/realistic/buildings/manifest.json`, imported here to
/Game/OpenDrive/Realistic/Buildings/. Needs import_building_textures.py and
apply_building_material.py to have run (the twin is checked against).

Re-runs clear the graph node by node in place (delete_all_material_
expressions is broken in 5.8), so the asset and its references survive.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
Shader check (nullrhi compiles nothing): cook it and expect no "Failed to
compile Material":
MSYS_NO_PATHCONV=1 UnrealEditor-Cmd.exe <uproject> -run=cook -targetplatform=Windows
    -cooksinglepackagenorefs -package=/Game/OpenDrive/Materials/M_BuildingFacade_real
    -unattended -nosplash -nop4 -stdout
"""

import runpy
from pathlib import Path

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
TWIN_NAME = "M_BuildingFacade"
MAT_NAME = "M_BuildingFacade_real"
ROOF_MAT_NAME = "M_BuildingRoof_real"   # AODBuildingManager loads it by this path
BUILDING_TEX_DIR = "/Game/OpenDrive/Textures/Building"
REAL_TEX_DIR = "/Game/OpenDrive/Realistic/Buildings"
REAL_SRC_DIR = Path(__file__).resolve().parent.parent / "content_src" / "realistic" / "buildings"

# ---- geometry contract (identical to apply_building_material.py) ----------
FACADE_TILE_M = 4.0
WALL_LAYERS = ["Bricks074", "Bricks053", "WoodSiding001", "Plaster001", "Concrete034",
               "stacked_stone_wall", "wood_trunk_wall"]
# albedo gain per layer (realism B4): WoodSiding001 is near-black (0.020) and
# read as soot on every wood house; x6 is weathered grey-brown timber
WALL_GAIN = [1.0, 1.0, 6.0, 1.0, 1.0, 1.5, 1.6]
WALL_MEAN_LUMA = [0.177, 0.500, 0.120, 0.665, 0.490, 0.107, 0.086]   # linear, 2K Color maps x WALL_GAIN
LAYER_TILE_M = {"stacked_stone_wall": 2.0, "wood_trunk_wall": 1.25}   # real-world size of non-4 m sets
PAINTED = (2, 3)   # siding and plaster take a paint colour
ROOF_MASK_THRESHOLD = 0.55

# ---- night (pipeline/check_night_exposure.py parses these) -----------------
NIGHT_ELEV_HI = 2.0      # AODDayNightController::NightFactor: smoothstep((2 - elev) / 8)
NIGHT_ELEV_SPAN = 8.0
LIT_MAX_FRACTION = 0.35  # share of windows lit at full night
LIT_NITS = 4.0           # lit window luminance (cd/m^2) before the per-room 0.8-1.0 jitter
LIT_JITTER_MIN = 0.8
INTERIOR_SHADE_MIN = 0.75   # darkest room face (floor) seen through a lit window, x LIT_NITS

# ---- facade archetypes (realism B2): vertex B in Realistic = (idx + 0.5) / 5,
# baked by build_building_mesh.py pick_archetype; the floor heights are its
# FACADE_FLOORS too (pipeline/tests/test_building_facades.py checks) --------
# punched (generic), house, storefront, glass curtain wall, balcony tower
FACADE_FLOORS = [(3.6, 3.2), (2.9, 2.8), (4.5, 3.3), (5.0, 3.8), (4.0, 3.0)]   # (ground, upper) m
FACADE_BAY_M = [2.5, 3.0, 3.0, 1.5, 3.0]
# window opening (u0, u1, v0, v1) in bay / floor fractions
FACADE_WINDOW_GROUND = [(0.15, 0.85, 0.06, 0.92), (0.20, 0.80, 0.30, 0.80), (0.04, 0.96, 0.09, 0.70),
                        (0.03, 0.97, 0.0, 0.88), (0.10, 0.90, 0.06, 0.85)]
FACADE_WINDOW_UPPER = [(0.15, 0.85, 0.30, 0.82), (0.20, 0.80, 0.30, 0.80), (0.15, 0.85, 0.30, 0.82),
                       (0.03, 0.97, 0.20, 0.985), (0.08, 0.92, 0.07, 0.90)]
GARAGE_SHARE, ENTRY_SHARE = 0.2, 0.18   # house ground-floor bays: garage door, entry door, else window
GARAGE_RECT = (0.06, 0.94, 0.0, 0.76)   # ~2.6 x 2.2 m
ENTRY_RECT = (0.34, 0.66, 0.0, 0.74)    # ~1 x 2.1 m
GARAGE_PANEL_M, GARAGE_GROOVE = 0.53, 0.06
DOOR_COLORS = [(0.60, 0.60, 0.57), (0.45, 0.40, 0.32), (0.06, 0.05, 0.045), (0.28, 0.07, 0.05)]
SHOP_DOOR_SHARE = 0.25   # storefront bays whose glass runs to the floor
SHOP_LIT_FRACTION = 0.7  # shop windows lit at full night (0.8 lifted a shop street off the night exposure floor)
FASCIA_V = (0.75, 0.95)  # sign band over the shopfront, ground-floor fraction
SIGN_COLORS = [(0.03, 0.03, 0.03), (0.25, 0.03, 0.02), (0.02, 0.05, 0.14), (0.03, 0.11, 0.05),
               (0.55, 0.50, 0.40)]
SPANDREL = (0.045, 0.05, 0.06)   # curtain wall's opaque band per floor
CURTAIN_GLASS = [(0.012, 0.020, 0.026), (0.018, 0.018, 0.018), (0.020, 0.016, 0.011)]   # blue, grey, bronze
MULLION, MULLION_ROUGH = (0.12, 0.12, 0.12), 0.35
BALCONY_SHARE = 0.75     # bay columns with balconies on a balcony tower
SLAB_V, RAIL_V, RAIL_TOP_V = 0.06, 0.40, 0.025   # slab, railing top, top rail (floor fraction, ~3 m)
BALUSTER_M, BALUSTER_W = 0.12, 0.25
SLAB_COLOR, RAIL_COLOR = (0.50, 0.50, 0.47), (0.55, 0.55, 0.53)
RECESS_DARK, SLAB_SHADOW = 0.2, 0.3   # balcony recess, and under the slab above

# ---- look ------------------------------------------------------------------
EDGE_SHARPNESS = 120.0   # bay/floor-fraction units: ~2-3 cm soft edge
FRAME_U, FRAME_V = 0.03, 0.025   # frame width, bay / floor fraction (~7.5 cm)
BAR_HALF_U, BAR_HALF_V = 0.012, 0.01
TRANSOM_V = 0.58
SILL_V = 0.04            # sill depth below the window, floor fraction (~13 cm)
SILL_OVERHANG_U = 0.03
STREAK_LEN_V = 0.3       # rain streaks run ~1 m under each sill
# Weather (2026-10-16), AODBuildingManager::SetWeather; both 0 = the dry,
# bare look. Wet masonry soaks up and darkens (glass and trim don't);
# snow lies on roofs by pitch - full on flat roofs, gone past ~50 deg - in
# drift patches, thinning toward the steep side.
WET_WALL_DARKEN = 0.62
WET_WALL_ROUGH = 0.35
WET_ROOF_DARKEN = 0.7
WET_ROOF_ROUGH = 0.25
ROOF_SNOW_COLOR = (0.88, 0.9, 0.94)
ROOF_SNOW_ROUGH = 0.8
ROOF_SNOW_SLIDE_NZ = 0.62   # normal.z below this (~52 deg pitch) sheds snow
ROOF_SNOW_PATCH_CM = 180.0
STREAK_DARK = 0.3
STREAK_COLUMNS = 12.0    # streak columns per bay
SPLASH_M = 0.7           # ground splash height
SPLASH_DARK = 0.35
WEATHER_CELL_M = 3.0
WEATHER_DARK = 0.18
JITTER = (0.85, 1.1)     # per-building wall brightness
GLASS = (0.010, 0.012, 0.015)
GLASS_ROUGH = (0.03, 0.09)
BLINDS = (0.20, 0.17, 0.13)
BLINDS_FRACTION = 0.25
FRAME_COLORS = [((0.60, 0.60, 0.57), 0.45), ((0.05, 0.045, 0.04), 0.4)]   # white, dark bronze
SILL_COLOR, SILL_ROUGH = (0.45, 0.44, 0.42), 0.75
PAINT_FRACTION = 0.7     # painted share of siding/plaster buildings (the rest keep the texture's colour)
PAINTS = [(0.70, 0.70, 0.66), (0.62, 0.55, 0.40), (0.30, 0.36, 0.28), (0.28, 0.33, 0.38), (0.30, 0.07, 0.05)]
LIT_COLORS = [(1.0, 0.66, 0.34), (1.0, 0.86, 0.66), (0.80, 0.88, 1.0)]   # warm, neutral, cool (TV)
LIT_COLOR_SPLIT = (0.7, 0.9)
ROOM_DEPTH_M = (3.0, 6.0)  # interior-mapped room box behind each lit window: bay x floor x depth
# roof sets, indexed by build_building_mesh.py ROOF_TILE/ASPHALT/METAL/FLAT
ROOF_LAYERS = [(BUILDING_TEX_DIR, "RoofingTiles007"), (REAL_TEX_DIR, "RoofingTiles009"),
               (REAL_TEX_DIR, "CorrugatedSteel005"), (BUILDING_TEX_DIR, "Concrete034")]
ROOF_MEAN_LUMA = [0.058, 0.016, 0.329, 0.482]   # linear, 2K Color maps (512 px mean)
ROOF_DETAIL = (0.3, 2.0)


def log(msg):
    unreal.log_warning(f"BUILDINGREAL {msg}")


def luma_normalised(rgb):
    lum = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    return tuple(c / lum for c in rgb)


MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
SHARED_WRAP = unreal.SamplerSourceMode.SSM_WRAP_WORLD_GROUP_SETTINGS   # 24 samples > 16 own samplers


def load_set(base_dir, asset_id):
    out = {k: unreal.load_asset(f"{base_dir}/{asset_id}/T_{asset_id}_{k}") for k in ("A", "N", "R")}
    missing = [k for k, v in out.items() if v is None]
    if missing:
        raise RuntimeError(f"{asset_id} maps {missing} missing under {base_dir}")
    return out


runpy.run_path(str(Path(__file__).resolve().parent / "import_ground_textures.py"),
               init_globals={"SRC_DIR": str(REAL_SRC_DIR), "TEX_DIR": REAL_TEX_DIR})


class G:
    """Node-soup helper; same UE 5.8 pin-name workarounds as
    apply_building_material.py's G (unnamed primary pin on unary nodes, ""
    for VertexColor RGB, If outputs named by caption)."""

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 200
        n = MEL.create_material_expression(self.mat, cls, -2400 + (self._y % 1600) * 2, self._y // 8)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def c(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def c2(self, a, b):
        return self.node(unreal.MaterialExpressionConstant2Vector, r=a, g=b)

    def c3(self, rgb):
        return self.node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(*rgb, 1.0))

    @staticmethod
    def req(ok, what):
        if not ok:
            raise RuntimeError(f"could not connect {what} - pin name mismatch?")

    def k(self, x):
        return self.c(x) if isinstance(x, (int, float)) else x

    def unary(self, cls, a, a_out=""):
        n = self.node(cls)
        self.req(MEL.connect_material_expressions(a, a_out, n, ""), cls.__name__)
        return n

    def floorv(self, a):
        return self.unary(unreal.MaterialExpressionFloor, a)

    def fracv(self, a):
        return self.unary(unreal.MaterialExpressionFrac, a)

    def sat(self, a):
        return self.unary(unreal.MaterialExpressionClamp, a)

    def absv(self, a):
        return self.unary(unreal.MaterialExpressionAbs, a)

    def custom(self, code, out_type, **ins):
        n = self.node(unreal.MaterialExpressionCustom, code=code, output_type=out_type)
        pins = []
        for name in ins:
            i = unreal.CustomInput()
            i.set_editor_property("input_name", name)
            pins.append(i)
        n.set_editor_property("inputs", pins)
        for name, src in ins.items():
            self.req(MEL.connect_material_expressions(self.k(src), "", n, name), f"Custom.{name}")
        return n

    def mask(self, a, r=False, gg=False, b=False, a_out=""):
        n = self.node(unreal.MaterialExpressionComponentMask, r=r, g=gg, b=b, a=False)
        self.req(MEL.connect_material_expressions(a, a_out, n, ""), "ComponentMask")
        return n

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self.req(MEL.connect_material_expressions(self.k(a), a_out, n, "A"), f"{cls.__name__}.A")
        self.req(MEL.connect_material_expressions(self.k(b), b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def div(self, a, b):
        return self.binop(unreal.MaterialExpressionDivide, a, b)

    def dot(self, a, b):
        return self.binop(unreal.MaterialExpressionDotProduct, a, b)

    def append(self, a, b):
        return self.binop(unreal.MaterialExpressionAppendVector, a, b)

    def maxv(self, a, b):
        return self.binop(unreal.MaterialExpressionMax, a, b)

    def minv(self, a, b):
        return self.binop(unreal.MaterialExpressionMin, a, b)

    def lerp(self, a, b, alpha, a_out="", b_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self.req(MEL.connect_material_expressions(self.k(a), a_out, n, "A"), "Lerp.A")
        self.req(MEL.connect_material_expressions(self.k(b), b_out, n, "B"), "Lerp.B")
        self.req(MEL.connect_material_expressions(self.k(alpha), "", n, "Alpha"), "Lerp.Alpha")
        return n

    def step(self, x, edge):
        """~1 where x > edge (hard, for per-building/per-window picks)."""
        return self.sat(self.mul(self.sub(x, edge), 1000.0))

    def frac_hash(self, x, mul, add):
        """Decorrelated [0,1) value from a [0,1) seed."""
        return self.fracv(self.add(self.mul(x, mul), add))

    def hash2(self, v2):
        return self.fracv(self.mul(self.unary(unreal.MaterialExpressionSine, self.dot(v2, self.c2(12.9898, 78.233))),
                                   43758.5453))

    def value_noise(self, v2):
        i = self.floorv(v2)
        f = self.fracv(v2)
        fx, fy = self.mask(f, r=True), self.mask(f, gg=True)
        top = self.lerp(self.hash2(i), self.hash2(self.add(i, self.c2(1.0, 0.0))), fx)
        bot = self.lerp(self.hash2(self.add(i, self.c2(0.0, 1.0))), self.hash2(self.add(i, self.c2(1.0, 1.0))), fx)
        return self.lerp(top, bot, fy)

    def rect(self, x, lo, hi, sharp=EDGE_SHARPNESS):
        a = self.sat(self.mul(self.sub(x, lo), sharp))
        b = self.sat(self.mul(self.sub(hi, x), sharp))
        return self.mul(a, b)

    def select(self, x, values, thresholds, out=""):
        """Hard pick values[i] for the bucket of x among ascending thresholds
        (chained If, not a blend). values: nodes sharing output pin `out`."""
        result, result_out = values[-1], out
        for i in reversed(range(len(thresholds))):
            n = self.node(unreal.MaterialExpressionIf)
            self.req(MEL.connect_material_expressions(x, "", n, "A"), "If.A")
            self.req(MEL.connect_material_expressions(self.c(thresholds[i]), "", n, "B"), "If.B")
            self.req(MEL.connect_material_expressions(values[i], out, n, "A < B"), "If.A<B")
            self.req(MEL.connect_material_expressions(result, result_out, n, "A > B"), "If.A>B")
            self.req(MEL.connect_material_expressions(result, result_out, n, "A == B"), "If.A==B")
            result, result_out = n, ""
        return result

    def sample(self, tex, uv, kind):
        stype = {"A": unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
                 "N": unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL}.get(
            kind, unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex, sampler_type=stype,
                      sampler_source=SHARED_WRAP)
        self.req(any(MEL.connect_material_expressions(uv, "", n, p) for p in ("UVs", "Coordinates")),
                 "TextureSample UV")
        if kind == "R":
            return self.mask(n, r=True, a_out="RGB")
        return self.mask(n, r=True, gg=True, b=True, a_out="RGB")


def sample_set(g, s, uv):
    return {k: g.sample(s[k], uv, k) for k in ("A", "N", "R")}


def inputs(g):
    uv = g.node(unreal.MaterialExpressionTextureCoordinate, coordinate_index=0)
    vc = g.node(unreal.MaterialExpressionVertexColor)
    # snapped to its 8-bit value like the twin (barycentric ulp noise x hash = speckle)
    seed = g.mul(g.floorv(g.add(g.mul(g.mask(vc, gg=True), 255.0), 0.5)), 1.0 / 255.0)
    return uv, vc, seed


def build_facade(mat):
    g = G(mat)
    uv, vc, seed = inputs(g)
    u, v = g.mask(uv, r=True), g.mask(uv, gg=True)
    wall_r = g.mask(vc, r=True)
    s_paint = g.frac_hash(seed, 17.31, 0.71)
    s_frame = g.frac_hash(seed, 3.37, 0.29)
    s_jit = g.frac_hash(seed, 11.13, 0.53)

    # ---- night: AODDayNightController::NightFactor of the sun's elevation
    sun = g.node(unreal.MaterialExpressionSkyAtmosphereLightDirection)
    sun.set_editor_property("light_index", 0)
    sun_z = g.maxv(g.minv(g.mask(sun, b=True), 1.0), -1.0)
    elev = g.mul(g.unary(unreal.MaterialExpressionArcsine, sun_z), 57.29578)
    t = g.sat(g.div(g.sub(NIGHT_ELEV_HI, elev), NIGHT_ELEV_SPAN))
    night = g.mul(g.mul(t, t), g.sub(3.0, g.mul(t, 2.0)))

    # ---- walls (hard per-building pick on R, like the twin)
    sets = [sample_set(g, load_set(BUILDING_TEX_DIR, a),
                       g.mul(uv, FACADE_TILE_M / LAYER_TILE_M[a]) if a in LAYER_TILE_M else uv)
            for a in WALL_LAYERS]
    for s, gain in zip(sets, WALL_GAIN):
        if gain != 1.0:
            s["A"] = g.mul(s["A"], gain)
    thr = [i / len(WALL_LAYERS) for i in range(1, len(WALL_LAYERS))]
    wall_a = g.select(wall_r, [s["A"] for s in sets], thr)
    wall_n = g.select(wall_r, [s["N"] for s in sets], thr)
    wall_rough = g.select(wall_r, [s["R"] for s in sets], thr)

    # paint siding and plaster (buckets PAINTED, R = (i + 0.5) / N), keeping the texture's detail
    n = len(WALL_LAYERS)
    mid = (PAINTED[0] + PAINTED[1] + 1) / 2 / n   # bucket edge between the two
    paintable = g.sat(g.mul(g.sub(1.0 / n, g.absv(g.sub(wall_r, mid))), 20.0 * n))
    mean = g.lerp(WALL_MEAN_LUMA[PAINTED[0]], WALL_MEAN_LUMA[PAINTED[1]], g.step(wall_r, mid))
    detail = g.maxv(g.minv(g.div(g.dot(wall_a, g.c3((0.2126, 0.7152, 0.0722))), mean), 1.6), 0.4)
    paint_pick = g.div(g.sub(s_paint, 1.0 - PAINT_FRACTION), PAINT_FRACTION)   # [0,1) over the painted share
    paint = g.select(paint_pick, [g.c3(p) for p in PAINTS], [i / len(PAINTS) for i in range(1, len(PAINTS))])
    paint_w = g.mul(paintable, g.step(s_paint, 1.0 - PAINT_FRACTION))
    wall_a = g.lerp(wall_a, g.mul(paint, detail), paint_w)
    wall_a = g.mul(wall_a, g.lerp(JITTER[0], JITTER[1], s_jit))
    weather = g.value_noise(g.mul(uv, FACADE_TILE_M / WEATHER_CELL_M))
    wall_a = g.mul(wall_a, g.sub(1.0, g.mul(weather, WEATHER_DARK)))
    splash = g.sat(g.sub(1.0, g.mul(v, FACADE_TILE_M / SPLASH_M)))
    wall_a = g.mul(wall_a, g.sub(1.0, g.mul(splash, SPLASH_DARK)))

    # ---- facade archetype (realism B2): vertex B = (idx + 0.5) / N
    n_arch = len(FACADE_FLOORS)
    arch = g.mask(vc, b=True)
    athr = [i / n_arch for i in range(1, n_arch)]

    def per_arch(values):
        return g.select(arch, [g.c(x) for x in values], athr)

    house, store, curtain, balc = (g.rect(arch, i / n_arch, (i + 1) / n_arch, 1000.0) for i in range(1, 5))
    not_curtain = g.sub(1.0, curtain)

    # ---- window grid: a taller ground floor, then equal upper floors
    ground_h = per_arch([f[0] for f in FACADE_FLOORS])
    upper_h = per_arch([f[1] for f in FACADE_FLOORS])
    h_m = g.mul(v, FACADE_TILE_M)
    above = g.sub(h_m, ground_h)
    upper = g.step(above, 0.0)
    ground = g.sub(1.0, upper)
    up_v = g.div(g.maxv(above, 0.0), upper_h)
    floor_idx = g.mul(upper, g.add(g.floorv(up_v), 1.0))
    floor_f = g.lerp(g.div(h_m, ground_h), g.fracv(up_v), upper)
    bay_w = per_arch(FACADE_BAY_M)
    bay_u = g.div(g.mul(u, FACADE_TILE_M), bay_w)
    bay_idx, bay_f = g.floorv(bay_u), g.fracv(bay_u)
    # vertex alpha 0 = a pitched roof's gable end wall (pipeline
    # building_roofs.py): plain wall, no windows, frames, sills or streaks
    solid = g.mask(vc, r=True, a_out="A")

    # per-window hashes, decorrelated per building (same cell as the twin)
    cell = g.append(g.add(bay_idx, g.mul(seed, 37.0)), g.add(floor_idx, g.mul(seed, 13.0)))
    h_lit = g.hash2(cell)
    h_var = g.frac_hash(h_lit, 13.7, 0.0)
    h_col = g.frac_hash(h_lit, 7.3, 0.0)
    h_blind = g.frac_hash(h_lit, 29.1, 0.37)
    h_mod = g.frac_hash(h_lit, 41.3, 0.17)
    h_column = g.hash2(g.append(g.add(bay_idx, g.mul(seed, 37.0)), g.mul(seed, 13.0)))

    # modules: house garage / entry doors, shop doors, balcony columns
    garage = g.mul(g.mul(house, ground), g.sub(1.0, g.step(h_mod, GARAGE_SHARE)))
    entry = g.mul(g.mul(g.mul(house, ground), g.step(h_mod, GARAGE_SHARE)),
                  g.sub(1.0, g.step(h_mod, GARAGE_SHARE + ENTRY_SHARE)))
    shop_door = g.mul(g.mul(store, ground), g.sub(1.0, g.step(h_mod, SHOP_DOOR_SHARE)))
    bal = g.mul(g.mul(balc, upper), g.sub(1.0, g.step(h_column, BALCONY_SHARE)))
    plain_bal = g.mul(g.mul(balc, upper), g.step(h_column, BALCONY_SHARE))
    no_win = g.sat(g.add(garage, entry))

    def bound(k):
        b = g.lerp(per_arch([w[k] for w in FACADE_WINDOW_UPPER]), per_arch([w[k] for w in FACADE_WINDOW_GROUND]),
                   ground)
        return g.lerp(b, FACADE_WINDOW_UPPER[0][k], plain_bal)   # balcony-tower bays without a balcony

    lo_u, hi_u, lo_v, hi_v = (bound(k) for k in range(4))
    lo_v = g.mul(lo_v, g.sub(1.0, shop_door))

    def rect4(r):
        return g.mul(g.rect(bay_f, r[0], r[1]), g.rect(floor_f, r[2], r[3]))

    door = g.mul(g.sat(g.add(g.mul(garage, rect4(GARAGE_RECT)), g.mul(entry, rect4(ENTRY_RECT)))), solid)
    win = g.mul(solid, g.sub(1.0, no_win))
    opening = g.mul(g.mul(g.rect(bay_f, lo_u, hi_u), g.rect(floor_f, lo_v, hi_v)), win)
    outer = g.mul(g.mul(g.rect(bay_f, g.sub(lo_u, FRAME_U), g.add(hi_u, FRAME_U)),
                        g.rect(floor_f, g.sub(lo_v, FRAME_V), g.add(hi_v, FRAME_V))), win)
    punched = g.mul(g.mul(upper, not_curtain), g.sub(1.0, bal))   # mullion, transom, sill, streaks
    bars = g.mul(g.sat(g.add(g.rect(bay_f, 0.5 - BAR_HALF_U, 0.5 + BAR_HALF_U),
                             g.rect(floor_f, TRANSOM_V - BAR_HALF_V, TRANSOM_V + BAR_HALF_V))), punched)
    glass = g.mul(opening, g.sub(1.0, bars))
    frame = g.sat(g.sub(outer, glass))
    sill = g.mul(g.mul(g.rect(bay_f, g.sub(lo_u, SILL_OVERHANG_U), g.add(hi_u, SILL_OVERHANG_U)),
                       g.rect(floor_f, g.sub(lo_v, FRAME_V + SILL_V), g.sub(lo_v, FRAME_V))), g.mul(punched, solid))
    trim = g.sat(g.add(frame, sill))

    # rain streaks under each sill, in columns
    below = g.sub(g.sub(lo_v, FRAME_V + SILL_V), floor_f)   # > 0 under the sill
    streak_fade = g.mul(g.sat(g.mul(below, 50.0)), g.sat(g.sub(1.0, g.div(below, STREAK_LEN_V))))
    columns = g.value_noise(g.append(g.mul(bay_u, STREAK_COLUMNS), g.mul(floor_idx, 3.1)))
    streak = g.mul(g.mul(g.mul(streak_fade, columns), g.rect(bay_f, lo_u, hi_u, 40.0)), g.mul(punched, solid))
    wall_a = g.mul(wall_a, g.sub(1.0, g.mul(streak, STREAK_DARK)))
    # a curtain wall has no masonry: spandrel panels between the glass
    wall_a = g.lerp(wall_a, g.c3(SPANDREL), curtain)
    wall_rough = g.lerp(wall_rough, MULLION_ROUGH, curtain)
    wall_n = g.lerp(wall_n, g.c3((0.0, 0.0, 1.0)), curtain)

    frame_col = g.select(s_frame, [g.c3(FRAME_COLORS[0][0]), g.c3(FRAME_COLORS[1][0])], [0.5])
    frame_col = g.lerp(frame_col, g.c3(MULLION), curtain)
    frame_rough = g.lerp(FRAME_COLORS[0][1], FRAME_COLORS[1][1], g.step(s_frame, 0.5))
    trim_col = g.lerp(frame_col, g.c3(SILL_COLOR), sill)
    trim_rough = g.lerp(frame_rough, SILL_ROUGH, sill)
    glass_col = g.lerp(g.c3(GLASS), g.c3(BLINDS), g.mul(g.sub(1.0, g.step(h_blind, BLINDS_FRACTION)), not_curtain))
    s_tint = g.frac_hash(seed, 23.7, 0.41)
    tint = g.select(s_tint, [g.c3(c) for c in CURTAIN_GLASS], [i / len(CURTAIN_GLASS) for i in range(1, len(CURTAIN_GLASS))])
    glass_col = g.lerp(glass_col, tint, curtain)
    glass_rough = g.lerp(GLASS_ROUGH[0], GLASS_ROUGH[1], h_var)

    facade_a = g.lerp(g.lerp(wall_a, trim_col, trim), glass_col, glass)
    facade_r = g.lerp(g.lerp(wall_rough, trim_rough, trim), glass_rough, glass)
    flat_w = g.sat(g.add(trim, glass))

    # house doors (garage panels grooved), shop sign band
    s_door = g.frac_hash(seed, 31.9, 0.23)
    door_col = g.select(s_door, [g.c3(c) for c in DOOR_COLORS], [i / len(DOOR_COLORS) for i in range(1, len(DOOR_COLORS))])
    groove = g.mul(g.rect(g.fracv(g.mul(h_m, 1.0 / GARAGE_PANEL_M)), -1.0, GARAGE_GROOVE), garage)
    facade_a = g.lerp(facade_a, g.mul(door_col, g.sub(1.0, g.mul(groove, 0.5))), door)
    fascia = g.mul(g.mul(g.mul(store, ground), g.rect(floor_f, FASCIA_V[0], FASCIA_V[1])), solid)
    sign = g.select(g.frac_hash(seed, 19.3, 0.61), [g.c3(c) for c in SIGN_COLORS],
                    [i / len(SIGN_COLORS) for i in range(1, len(SIGN_COLORS))])
    facade_a = g.lerp(facade_a, sign, fascia)
    facade_r = g.lerp(facade_r, 0.45, g.sat(g.add(door, fascia)))

    # balconies: slab, railing (top rail + balusters) over a shaded recess
    slab = g.mul(bal, g.sub(1.0, g.step(floor_f, SLAB_V)))
    rail_zone = g.rect(floor_f, SLAB_V, RAIL_V)
    balusters = g.mul(rail_zone, g.rect(g.fracv(g.mul(u, FACADE_TILE_M / BALUSTER_M)), -1.0, BALUSTER_W))
    rail = g.mul(bal, g.sat(g.add(balusters, g.rect(floor_f, RAIL_V - RAIL_TOP_V, RAIL_V))))
    shade = g.add(RECESS_DARK, g.mul(g.step(floor_f, 0.85), SLAB_SHADOW))
    facade_a = g.mul(facade_a, g.sub(1.0, g.mul(g.mul(bal, g.sub(1.0, slab)), shade)))
    facade_a = g.lerp(g.lerp(facade_a, g.c3(RAIL_COLOR), rail), g.c3(SLAB_COLOR), slab)
    facade_r = g.lerp(facade_r, 0.6, g.sat(g.add(rail, slab)))
    flat_w = g.sat(g.add(g.add(flat_w, door), g.add(g.add(fascia, rail), slab)))
    facade_n = g.lerp(wall_n, g.c3((0.0, 0.0, 1.0)), flat_w)

    # lit rooms: a window is on once LIT_MAX_FRACTION * night passes its hash,
    # so rooms switch on one by one through dusk; shops stay lit
    lit_frac = g.lerp(LIT_MAX_FRACTION, SHOP_LIT_FRACTION, g.mul(store, ground))
    lit = g.sat(g.mul(g.sub(g.mul(night, lit_frac), h_lit), 50.0))
    lit_col = g.select(h_col, [g.c3(luma_normalised(c)) for c in LIT_COLORS], list(LIT_COLOR_SPLIT))
    nits = g.mul(g.lerp(LIT_NITS * LIT_JITTER_MIN, LIT_NITS, h_var), g.mul(lit, g.mul(glass, g.sub(1.0, rail))))
    # interior mapping: ray from the glass into a bay x floor x depth room box
    # (tangent along +U, sign from screen derivatives so mirrored rings work)
    room = g.custom(INTERIOR_HLSL, unreal.CustomMaterialOutputType.CMOT_FLOAT3,
                    Cell=g.append(bay_f, floor_f),
                    D=g.node(unreal.MaterialExpressionCameraVectorWS),
                    N=g.node(unreal.MaterialExpressionVertexNormalWS),
                    P=g.node(unreal.MaterialExpressionWorldPosition, world_position_shader_offset=
                             unreal.WorldPositionIncludedOffsets.WPT_CAMERA_RELATIVE),
                    U=u, H=g.frac_hash(h_lit, 5.1, 0.11), Col=lit_col,
                    BW=bay_w, FH=g.lerp(upper_h, ground_h, ground))
    emissive = g.mul(room, nits)

    # wet weather: the porous wall darkens, glass/trim/doors stay as they are
    soak = g.mul(weather_param(g, "Wetness"), g.sub(1.0, flat_w))
    facade_a = g.mul(facade_a, g.lerp(1.0, WET_WALL_DARKEN, soak))
    facade_r = g.lerp(facade_r, g.minv(facade_r, g.c(WET_WALL_ROUGH)), soak)
    weather_param(g, "SnowCover")   # in the interface for SetWeather; walls take no snow

    return {
        unreal.MaterialProperty.MP_BASE_COLOR: facade_a,
        unreal.MaterialProperty.MP_NORMAL: facade_n,
        unreal.MaterialProperty.MP_ROUGHNESS: facade_r,
        unreal.MaterialProperty.MP_EMISSIVE_COLOR: emissive,
    }


INTERIOR_HLSL = f"""
float3 T = cross(float3(0, 0, 1), N);
T /= max(length(T), 1e-4);
float s = dot(ddx(P), T) * ddx(U) + dot(ddy(P), T) * ddy(U);
T *= s < 0 ? -1.0 : 1.0;
float3 V = -D;
float depth = lerp({ROOM_DEPTH_M[0]}, {ROOM_DEPTH_M[1]}, frac(H * 7.7));
float3 d = float3(dot(V, T) / BW, V.z / FH, max(-dot(V, N), 0.05) / depth);
d += (abs(d) < 1e-4) * 1e-4;
float3 p = float3(Cell, 0);
float3 t3 = (step(0, d) - p) / d;
float t = min(min(t3.x, t3.y), t3.z);
float3 h = p + d * t;
float3 wall = lerp(float3(0.95, 0.9, 0.8), float3(0.75, 0.85, 0.95), frac(H * 3.3));
float3 c; float sh;
if (t == t3.z) {{ c = wall; sh = 1.0 - 0.15 * (abs(h.x - 0.5) + abs(h.y - 0.5)); }}
else if (t == t3.x) {{ c = wall; sh = 0.95 - 0.2 * h.z; }}
else if (d.y > 0) {{ c = float3(1, 1, 1); sh = 1.0 - 0.1 * h.z; }}
else {{ c = lerp(float3(0.8, 0.55, 0.35), float3(0.55, 0.55, 0.6), frac(H * 5.9)); sh = {INTERIOR_SHADE_MIN}; }}
float3 rgb = Col * c;
return rgb * (clamp(sh, {INTERIOR_SHADE_MIN}, 1.0) / max(dot(rgb, float3(0.2126, 0.7152, 0.0722)), 1e-3));
"""


def build_roof(mat):
    g = G(mat)
    uv, vc, _ = inputs(g)
    pick = g.mask(vc, a_out="A", r=True)   # VertexColor's A pin, as a scalar
    n = len(ROOF_LAYERS)
    sets = [sample_set(g, load_set(d, asset_id), uv) for d, asset_id in ROOF_LAYERS]
    for s, mean in zip(sets, ROOF_MEAN_LUMA):
        luma = g.dot(s["A"], g.c3((0.2126, 0.7152, 0.0722)))
        s["D"] = g.unary(unreal.MaterialExpressionClamp, g.mul(luma, 1.0 / mean))
        s["D"].set_editor_property("min_default", ROOF_DETAIL[0])
        s["D"].set_editor_property("max_default", ROOF_DETAIL[1])
    thr = [(i + 1) / n for i in range(n - 1)]
    out = {k: g.select(pick, [s[k] for s in sets], thr) for k in ("D", "N", "R")}
    albedo = g.mul(g.mask(vc, r=True, gg=True, b=True), out["D"])
    wet = weather_param(g, "Wetness")
    albedo = g.mul(albedo, g.lerp(1.0, WET_ROOF_DARKEN, wet))
    rough = g.lerp(out["R"], g.minv(out["R"], g.c(WET_ROOF_ROUGH)), wet)
    # snow by pitch + drift patches: cover - slope penalty + noise, sharpened
    nz = g.mask(g.node(unreal.MaterialExpressionVertexNormalWS), b=True)
    patch = g.value_noise(g.mul(g.mask(g.node(unreal.MaterialExpressionWorldPosition), r=True, gg=True),
                                1.0 / ROOF_SNOW_PATCH_CM))
    cover = weather_param(g, "SnowCover")
    snow = g.sat(g.mul(g.add(g.sub(g.mul(cover, 1.3),
                                   g.mul(g.sub(1.0, nz), 1.0 / (1.0 - ROOF_SNOW_SLIDE_NZ))),
                             g.mul(g.sub(patch, 0.5), 0.5)), 4.0))
    snow = g.mul(g.mul(snow, g.sat(g.mul(g.sub(nz, ROOF_SNOW_SLIDE_NZ), 20.0))),
                 g.sat(g.mul(cover, 4.0)))   # exactly bare at SnowCover 0
    return {
        unreal.MaterialProperty.MP_BASE_COLOR: g.lerp(albedo, g.c3(ROOF_SNOW_COLOR), snow),
        unreal.MaterialProperty.MP_NORMAL: g.lerp(out["N"], g.c3((0.0, 0.0, 1.0)), snow),
        unreal.MaterialProperty.MP_ROUGHNESS: g.lerp(rough, ROOF_SNOW_ROUGH, snow),
    }


def weather_param(g, name):
    n = g.node(unreal.MaterialExpressionScalarParameter, parameter_name=name)
    n.set_editor_property("default_value", 0.0)
    return n


USAGE_FLAGS = ("used_with_instanced_static_meshes", "used_with_nanite", "used_with_skeletal_mesh",
               "used_with_static_lighting", "used_with_spline_meshes", "used_with_morph_targets")


def param_names(mat):
    names = set()
    for fn in (MEL.get_scalar_parameter_names, MEL.get_vector_parameter_names,
               MEL.get_texture_parameter_names, MEL.get_static_switch_parameter_names):
        names |= {str(n) for n in fn(mat)}
    return names


def channels(mat):
    exprs = MEL.get_material_expressions(mat)
    vc = any(isinstance(e, unreal.MaterialExpressionVertexColor) for e in exprs)
    uvs = {e.get_editor_property("coordinate_index") for e in exprs
           if isinstance(e, unreal.MaterialExpressionTextureCoordinate)}
    return vc, uvs


def check_interface(twin, real, real_path, outputs):
    problems = []
    missing = param_names(twin) - param_names(real)
    if missing:
        problems.append(f"missing params {sorted(missing)}")
    (tvc, tuv), (rvc, ruv) = channels(twin), channels(real)
    if tvc and not rvc:
        problems.append("twin reads vertex color, _real doesn't")
    if not tuv <= ruv:
        problems.append(f"twin reads UV{sorted(tuv - ruv)}, _real doesn't")
    for prop in ("phys_material", "two_sided", "blend_mode") + USAGE_FLAGS:
        if twin.get_editor_property(prop) != real.get_editor_property(prop):
            problems.append(f"{prop} differs")
    for prop in outputs:
        if MEL.get_material_property_input_node(real, prop) is None:
            problems.append(f"{prop} unconnected")
    if problems:
        raise RuntimeError(f"interface rule FAILED for {real_path}: {problems}")
    log(f"interface OK {real_path}: params={sorted(param_names(real))} vertex_color={rvc} uv={sorted(ruv)} "
        f"phys={real.get_editor_property('phys_material')} two_sided={real.get_editor_property('two_sided')}")


def make(twin, name, builder):
    """Build one material on the twin's flags; the roof has no twin of its
    own, so it is checked against the facade's (same mesh, same channels)."""
    path = f"{MAT_DIR}/{name}"
    if EAL.does_asset_exist(path):
        mat = unreal.load_asset(path)
        for e in list(MEL.get_material_expressions(mat)):   # not delete_all_material_expressions (5.8 bug)
            MEL.delete_material_expression(mat, e)
    else:
        mat = tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
    for prop in ("phys_material", "blend_mode", "two_sided") + USAGE_FLAGS:
        mat.set_editor_property(prop, twin.get_editor_property(prop))
    out = builder(mat)
    for prop, node in out.items():
        G.req(MEL.connect_material_property(node, "", prop), str(prop))
    MEL.recompile_material(mat)
    if not EAL.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {path}")
    real = unreal.load_asset(path)
    check_interface(twin, real, path, list(out))
    log(f"saved {path} ({MEL.get_num_material_expressions(real)} nodes)")


def main():
    twin_path = f"{MAT_DIR}/{TWIN_NAME}"
    twin = unreal.load_asset(twin_path)
    if twin is None:
        raise RuntimeError(f"{twin_path} missing - run unreal/apply_building_material.py first")
    make(twin, MAT_NAME, build_facade)
    make(twin, ROOF_MAT_NAME, build_roof)


main()
