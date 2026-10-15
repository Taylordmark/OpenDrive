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
building seed, B = flat roof, A = 0 on pitched-roof gable end walls (no
windows; ODBuildingManager PitchRoof, Realistic only). No parameters (the
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
  - roofs (M_BuildingRoof_real): shingle roofs pick RoofingTiles007 (stylized), RoofingTiles009
    (dark asphalt shingle) or CorrugatedSteel005 painted red/green/grey per
    building; flat roofs stay concrete, darker.
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
FLOOR_HEIGHT_M = 3.2
BAY_WIDTH_M = 2.5
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

# ---- look ------------------------------------------------------------------
WINDOW_MARGIN_U = (0.15, 0.85)
WINDOW_MARGIN_V_UPPER = (0.30, 0.82)
WINDOW_MARGIN_V_GROUND = (0.06, 0.92)
EDGE_SHARPNESS = 120.0   # bay/floor-fraction units: ~2-3 cm soft edge
FRAME_U, FRAME_V = 0.03, 0.025   # frame width, bay / floor fraction (~7.5 cm)
BAR_HALF_U, BAR_HALF_V = 0.012, 0.01
TRANSOM_V = 0.58
SILL_V = 0.04            # sill depth below the window, floor fraction (~13 cm)
SILL_OVERHANG_U = 0.03
STREAK_LEN_V = 0.3       # rain streaks run ~1 m under each sill
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
ROOF_SPLIT = (0.4, 0.7)  # RoofingTiles007 | RoofingTiles009 | CorrugatedSteel005
SHINGLE_009_GAIN = 6.0   # RoofingTiles009 is near-black (0.012) -> ~0.07, weathered asphalt
STEEL_MEAN = 0.335
STEEL_PAINTS = [(0.25, 0.05, 0.04), (0.07, 0.15, 0.08), (0.335, 0.335, 0.335)]
FLAT_ROOF_DARK = 0.7


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

    # ---- window grid (same cells as the twin)
    bay_u = g.mul(u, FACADE_TILE_M / BAY_WIDTH_M)
    floor_v = g.mul(v, FACADE_TILE_M / FLOOR_HEIGHT_M)
    bay_idx, bay_f = g.floorv(bay_u), g.fracv(bay_u)
    floor_idx, floor_f = g.floorv(floor_v), g.fracv(floor_v)
    ground = g.sat(g.sub(1.0, g.mul(floor_idx, 10.0)))
    upper = g.sub(1.0, ground)
    lo_v = g.lerp(WINDOW_MARGIN_V_UPPER[0], WINDOW_MARGIN_V_GROUND[0], ground)
    hi_v = g.lerp(WINDOW_MARGIN_V_UPPER[1], WINDOW_MARGIN_V_GROUND[1], ground)
    lo_u, hi_u = WINDOW_MARGIN_U
    # vertex alpha 0 = a pitched roof's gable end wall (ODBuildingManager
    # PitchRoof): plain wall, no windows, frames, sills or streaks
    solid = g.mask(vc, r=True, a_out="A")

    opening = g.mul(g.mul(g.rect(bay_f, lo_u, hi_u), g.rect(floor_f, lo_v, hi_v)), solid)
    outer = g.mul(g.mul(g.rect(bay_f, lo_u - FRAME_U, hi_u + FRAME_U),
                        g.rect(floor_f, g.sub(lo_v, FRAME_V), g.add(hi_v, FRAME_V))), solid)
    bars = g.mul(g.sat(g.add(g.rect(bay_f, 0.5 - BAR_HALF_U, 0.5 + BAR_HALF_U),
                             g.rect(floor_f, TRANSOM_V - BAR_HALF_V, TRANSOM_V + BAR_HALF_V))), upper)
    glass = g.mul(opening, g.sub(1.0, bars))
    frame = g.sat(g.sub(outer, glass))
    sill = g.mul(g.mul(g.rect(bay_f, lo_u - SILL_OVERHANG_U, hi_u + SILL_OVERHANG_U),
                       g.rect(floor_f, g.sub(lo_v, FRAME_V + SILL_V), g.sub(lo_v, FRAME_V))), g.mul(upper, solid))
    trim = g.sat(g.add(frame, sill))

    # rain streaks under each sill, in columns
    below = g.sub(g.sub(lo_v, FRAME_V + SILL_V), floor_f)   # > 0 under the sill
    streak_fade = g.mul(g.sat(g.mul(below, 50.0)), g.sat(g.sub(1.0, g.div(below, STREAK_LEN_V))))
    columns = g.value_noise(g.append(g.mul(bay_u, STREAK_COLUMNS), g.mul(floor_idx, 3.1)))
    streak = g.mul(g.mul(g.mul(streak_fade, columns), g.rect(bay_f, lo_u, hi_u, 40.0)), g.mul(upper, solid))
    wall_a = g.mul(wall_a, g.sub(1.0, g.mul(streak, STREAK_DARK)))

    # per-window hashes, decorrelated per building (same cell as the twin)
    cell = g.append(g.add(bay_idx, g.mul(seed, 37.0)), g.add(floor_idx, g.mul(seed, 13.0)))
    h_lit = g.hash2(cell)
    h_var = g.frac_hash(h_lit, 13.7, 0.0)
    h_col = g.frac_hash(h_lit, 7.3, 0.0)
    h_blind = g.frac_hash(h_lit, 29.1, 0.37)

    frame_col = g.select(s_frame, [g.c3(FRAME_COLORS[0][0]), g.c3(FRAME_COLORS[1][0])], [0.5])
    frame_rough = g.lerp(FRAME_COLORS[0][1], FRAME_COLORS[1][1], g.step(s_frame, 0.5))
    trim_col = g.lerp(frame_col, g.c3(SILL_COLOR), sill)
    trim_rough = g.lerp(frame_rough, SILL_ROUGH, sill)
    glass_col = g.lerp(g.c3(GLASS), g.c3(BLINDS), g.sub(1.0, g.step(h_blind, BLINDS_FRACTION)))
    glass_rough = g.lerp(GLASS_ROUGH[0], GLASS_ROUGH[1], h_var)

    facade_a = g.lerp(g.lerp(wall_a, trim_col, trim), glass_col, glass)
    facade_r = g.lerp(g.lerp(wall_rough, trim_rough, trim), glass_rough, glass)
    facade_n = g.lerp(wall_n, g.c3((0.0, 0.0, 1.0)), g.sat(g.add(trim, glass)))

    # lit rooms: a window is on once LIT_MAX_FRACTION * night passes its hash,
    # so rooms switch on one by one through dusk
    lit = g.sat(g.mul(g.sub(g.mul(night, LIT_MAX_FRACTION), h_lit), 50.0))
    lit_col = g.select(h_col, [g.c3(luma_normalised(c)) for c in LIT_COLORS], list(LIT_COLOR_SPLIT))
    nits = g.mul(g.lerp(LIT_NITS * LIT_JITTER_MIN, LIT_NITS, h_var), g.mul(lit, glass))
    # interior mapping: ray from the glass into a bay x floor x depth room box
    # (tangent along +U, sign from screen derivatives so mirrored rings work)
    room = g.custom(INTERIOR_HLSL, unreal.CustomMaterialOutputType.CMOT_FLOAT3,
                    Cell=g.append(bay_f, floor_f),
                    D=g.node(unreal.MaterialExpressionCameraVectorWS),
                    N=g.node(unreal.MaterialExpressionVertexNormalWS),
                    P=g.node(unreal.MaterialExpressionWorldPosition, world_position_shader_offset=
                             unreal.WorldPositionIncludedOffsets.WPT_CAMERA_RELATIVE),
                    U=u, H=g.frac_hash(h_lit, 5.1, 0.11), Col=lit_col)
    emissive = g.mul(room, nits)

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
float3 d = float3(dot(V, T) / {BAY_WIDTH_M}, V.z / {FLOOR_HEIGHT_M}, max(-dot(V, N), 0.05) / depth);
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
    uv, vc, seed = inputs(g)
    flat_roof = g.mask(vc, b=True)
    s_roof = g.frac_hash(seed, 7.77, 0.13)
    s_paint = g.frac_hash(seed, 17.31, 0.71)   # same per-building hash as the facade paint
    roof_sets = [sample_set(g, load_set(BUILDING_TEX_DIR, "RoofingTiles007"), uv),
                 sample_set(g, load_set(REAL_TEX_DIR, "RoofingTiles009"), uv),
                 sample_set(g, load_set(REAL_TEX_DIR, "CorrugatedSteel005"), uv)]
    roof_sets[1]["A"] = g.mul(roof_sets[1]["A"], SHINGLE_009_GAIN)
    steel_paint = g.select(s_paint, [g.c3(tuple(c / STEEL_MEAN for c in p)) for p in STEEL_PAINTS], [0.33, 0.66])
    roof_sets[2]["A"] = g.mul(roof_sets[2]["A"], steel_paint)
    split = list(ROOF_SPLIT)
    pitched = {k: g.select(s_roof, [s[k] for s in roof_sets], split) for k in ("A", "N", "R")}
    concrete = sample_set(g, load_set(BUILDING_TEX_DIR, "Concrete034"), uv)
    return {
        unreal.MaterialProperty.MP_BASE_COLOR: g.lerp(pitched["A"], g.mul(concrete["A"], FLAT_ROOF_DARK), flat_roof),
        unreal.MaterialProperty.MP_NORMAL: g.lerp(pitched["N"], concrete["N"], flat_roof),
        unreal.MaterialProperty.MP_ROUGHNESS: g.lerp(pitched["R"], concrete["R"], flat_roof),
    }


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
