"""Phase 2a road-visual overhaul (2026-09-03): replaces the old flat/baked-
texture M_Road (see git history on this file's material - it used to be a
single baked-in-Python road texture with the double-yellow centerline drawn
as PIXELS, see pipeline/make_road_texture.py) with a real worn-asphalt PBR
material (content_src/textures/road/, ambientCG CC0, see manifest.json) that
draws lane markings PROCEDURALLY from the ODR3 sidecar data
(OpenDriveUE/Source/OpenDriveUE/ODRoadManager.cpp's CreateChunkMeshComponent)
instead of ever baking them into a texture:

  - UV0 (u0, v0): u0 = 0 at the left pavement edge, 1 at the right pavement
    edge (skirt vertices extrapolate outside [0,1] - see
    pipeline/road_mesh_common.py's build_road_chunks docstring); v0 = arc-
    length METERS along the centerline, continuous (not rescaled by width),
    so tiling by world meters never stretches on curves or between road
    classes.
  - UV1 (u1, "TexCoord[1]"): u1 (r) = road HALF-width in meters at this
    vertex - recovers a real meters offset from u0 (meterPos =
    (u0-0.5)*2*u1) so stripe widths/tiling read as a constant real-world
    size regardless of how wide the pavement is. Zero on any chunk that
    didn't come from a real ODR3 tile (ODRoadManager only writes a second UV
    layer when Chunk.bHasV3Data) - RoadHalfWidthFallbackM below covers that
    case. v1 (g) = junction-suppress weight, 0..1 (Arc 7.17, 2026-09-04 -
    repurposed from the former "reserved (0.0)"; see
    pipeline/road_mesh_common.py's build_road_chunks docstring for how
    build_road_stations.py's real-junction-node flatten pass fills this in).
    1.0 means "inside a real graph junction's flush footprint" - multiplied
    into final_paint_mask below (not_in_junction) so lane markings stop
    being drawn through the junction box, smoothstep-easing back in over the
    approach zone; never gates the base asphalt/gravel look.
  - Vertex color (baked per-chunk in CreateChunkMeshComponent, not read from
    a texture): R = road_class_id/6, G = lane_count/8, B = oneway (0/1),
    A = a per-chunk 0..1 wear-variety seed. A chunk with NO ODR3 data (plain
    ODR2 fallback) gets the sentinel (0,0,0,0) - lane_count=0 is never a
    real value (wire range is 1..8), so G==0 exactly means "no marking data,
    draw the road as physically-real hasData -> that vertex-color state
    resolves to a straight motorway-asphalt tile that is thoroughly
    quantized to 0 in this HasData mask.

Lane-marking layout, all derived analytically from meterPos (signed lateral
offset from centerline, meters) and RoadHalfWidthEffM - no lookup textures:
  - double-yellow CENTER line: two thin yellow stripes straddling meterPos=0,
    only when the road is two-way (vertex color B==0) AND lane_count<=2
    (a genuine 2-lane two-way road - see LaneCountCenterMax).
  - white DASHED lane separators: at every internal lane boundary
    (meterPos = k * laneWidthM for integer k, laneWidthM = pavement width /
    lane_count) - found via a continuous "distance to nearest multiple of
    laneWidthM" formula (frac-based), not a fixed per-lane-count node chain,
    so it works for any lane_count value the vertex color carries. Enabled
    for two-way roads with lane_count>=4, or ANY one-way road with
    lane_count>=2 (a divided highway's own carriageway still has real lane
    dividers between its own lanes).
  - solid white EDGE lines a fixed distance in from each pavement edge.
  - WEAR: a cheap value-noise hash of v0 (offset per chunk by the wear-seed
    in vertex-color A so adjacent chunks don't repeat identically) fades
    marking opacity/brightness down in patches - "recently repainted" is the
    unfaded 1.0 case, this only ever DARKENS/fades toward the unpainted
    asphalt look, never brightens past it.
  - Outside the pavement (u0<0 or u0>1, the skirt), the asphalt blends to a
    gravel-shoulder texture over a short real-world distance
    (ShoulderBlendWidthM) - no markings are drawn there (the marking masks
    below are all built from meterPos distances that only read as "on
    pavement" inside [-halfWidth, halfWidth] anyway).
  - Road CLASS (vertex color R) additionally picks which of three asphalt
    sets to sample, purely for texture variety: motorway/trunk/primary
    (class 0-2) get the primary well-kept set, secondary/tertiary (3-4) the
    variant set, residential/service (5-6) the patchier set - see ASPHALT
    below. Junction pads are ordinary chunks in this same stream (merged
    into their host ribbon chunk by pipeline/experimental_junction_pads.py's
    bridge_pad_to_host - there is no separate pad-material code path) and
    get whatever their own vertex-color class says; pads sit at u0 well
    outside [0,1] on their host chunk's profile in practice (see that
    script), which keeps them out of the lane-marking bands automatically.

Reuses the same G node-soup helper + known UE 5.8 python-binding pin-name
gotchas (Power's "Exp" pin, VertexColor's "" RGB(A) output, Clamp/Abs/Floor/
Frac/Sine's unnamed primary pin) unreal/apply_ground_material.py established
- see that file's own docstring for the full gotcha list this one inherits.

Unpaved variants (2026-10-06, PLAN-REALISM Phase 2):
unreal/apply_unpaved_road_materials.py re-runs this file with SURFACE =
"gravel"/"dirt" injected (runpy init_globals) to build M_Road_Gravel /
M_Road_Dirt - the same graph (Wetness, SnowDepth/SideSnowDepth CPD, tracks,
berm WPO) with an unpaved base texture everywhere and no lane markings.

Run unreal/import_road_textures.py FIRST (imports the PBR sets as Texture2D
assets this script loads by path) - this script raises if any layer's
textures are missing.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
SURFACE = globals().get("SURFACE", "paved")   # see "Unpaved variants" above
MAT_NAME = {"paved": "M_Road", "gravel": "M_Road_Gravel", "dirt": "M_Road_Dirt"}[SURFACE]
ROAD_TEX_DIR = "/Game/OpenDrive/Textures/Road"
# unpaved base: (texture dir, asset_id, tile METERS)
UNPAVED_BASE = {"gravel": (ROAD_TEX_DIR, "Gravel023", 1.4),
                "dirt": ("/Game/OpenDrive/Textures/Ground", "Ground037", 2.0)}

# (asset_id, world-space tile size METERS - v0/meterPos are already in real
# meters, unlike the ground material's cm world-position tiling).
ASPHALT_PRIMARY = ("Asphalt031", 3.2)     # motorway/trunk/primary
ASPHALT_SECONDARY = ("Asphalt033", 3.0)   # secondary/tertiary
ASPHALT_RESIDENTIAL = ("Asphalt032", 2.6) # residential/service - patchier, smaller tile reads rougher
GRAVEL = ("Gravel023", 1.4)               # shoulder past the pavement edge

RoadHalfWidthFallbackM = 4.5   # pipeline/road_mesh_common.py's ROAD_HALF_WIDTH default -
                                # used when UV1 reads 0 (no real ODR3 data on this chunk)
ShoulderBlendWidthM = 0.45     # gravel blend distance past the pavement edge
LaneCountCenterMax = 2.5       # double-yellow center only at <=2 lanes
LaneCountDashMin_TwoWay = 3.5  # white dashed separators at >=4 lanes (two-way)
LaneCountDashMin_OneWay = 1.5  # ... or >=2 lanes on a one-way carriageway
EdgeLineInsetM = 0.35          # distance the solid edge line sits in from the pavement edge
EdgeLineHalfWidthM = 0.06
CenterLineHalfWidthM = 0.055
CenterLineGapHalfM = 0.10      # half the gap between the two yellow stripes
DashHalfWidthM = 0.06
DashPeriodM = 6.0               # paint-then-gap period along v0
DashPaintFraction = 0.5
LineSharpnessPerM = 24.0        # antialiasing sharpness (1/m) for all stripe edges
WearNoiseScaleM = 9.0
WearMaxFade = 0.6               # markings never fade past 40% of full brightness/opacity
# Weather W2 (next steps.md sec 7.40): "Wetness" scalar, pushed at runtime by
# AODSeasonController from AODWeatherController::Wetness into
# AODRoadManager's shared road MID. Default 0 = dry = the exact pre-W2 graph
# output (x * 1 and Lerp(x, y, 0) are identity). Replaces the retired baked
# road_moisture roughness term (sec 2.2) with a live one.
WetDarken = 0.55                # albedo multiplier at Wetness 1 (wet asphalt reads much darker)
WetRoughness = 0.12             # roughness at Wetness 1 (water film = sky reflections)
if SURFACE == "gravel":
    WetDarken, WetRoughness = 0.6, 0.4    # stones darken, little film
elif SURFACE == "dirt":
    WetDarken, WetRoughness = 0.5, 0.25   # mud: dark, satin sheen
# Snow (2026-10-06): per-chunk depth in Custom Primitive Data (AODRoadManager::
# TickSnow) - "SnowDepth" (index 0, road cm) and "SideSnowDepth" (index 1,
# roadside cm). Both 0 = the exact pre-snow graph output, incl. the WPO.
SnowSlushFullCm = 2.0           # wet -> grey slush over the first 2 cm
SnowCoverFullCm = 10.0          # ... -> fully white by 10 cm
SlushColor = (0.42, 0.43, 0.45)
SlushOpacity = 0.7
SnowColor = (0.90, 0.92, 0.95)
SlushRoughness = 0.25
SnowRoughness = 0.8
TrackOffsetM = 0.8              # tire tracks +-0.8 m from each lane's center
TrackHalfWidthM = 0.3
TrackCover = 0.45               # white cover kept inside a track (packed/slushy ruts)
# Berms: WPO moves the wrap-lip verts (UV2 = outward XY unit vector, written
# by ODRoadManager's CreateChunkMeshComponent; zero on every other vert) out
# by BermOutPerCm x depth and up from their 0.6 m drop to the bank top.
BermOutPerCm = 1.5
BermLipDropCm = 60.0            # road_mesh_common.WRAP_LIP_DROP_M


def log(msg):
    # unreal.log() doesn't reliably surface in this project's headless
    # -stdout captures (apply_water_material.py's 2026-08-27 finding) -
    # log_warning does.
    unreal.log_warning(f"ROADMAT {msg}")


MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def load_asphalt(asset_id, tex_dir=ROAD_TEX_DIR):
    base = f"{tex_dir}/{asset_id}"
    albedo = unreal.load_asset(f"{base}/T_{asset_id}_A")
    normal = unreal.load_asset(f"{base}/T_{asset_id}_N")
    rough = unreal.load_asset(f"{base}/T_{asset_id}_R")
    if albedo is None or normal is None or rough is None:
        raise RuntimeError(
            f"road texture set '{asset_id}' missing under {base} - run "
            f"unreal/import_road_textures.py first (after "
            f"content_src/textures/road/download_road_textures.py).")
    return dict(albedo=albedo, normal=normal, rough=rough)


TEXTURES = {
    key: load_asphalt(asset_id)
    for key, (asset_id, _tile) in dict(
        primary=ASPHALT_PRIMARY, secondary=ASPHALT_SECONDARY,
        residential=ASPHALT_RESIDENTIAL, gravel=GRAVEL).items()
}
TILE_M = dict(
    primary=ASPHALT_PRIMARY[1], secondary=ASPHALT_SECONDARY[1],
    residential=ASPHALT_RESIDENTIAL[1], gravel=GRAVEL[1])


class G:
    """Node-soup helper around MaterialEditingLibrary - see module docstring
    for the pin-name gotchas this works around (same set
    unreal/apply_ground_material.py's identical helper documents)."""

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 200
        n = MEL.create_material_expression(
            self.mat, cls, -2400 + (self._y % 1800) * 2, self._y // 8)
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

    def absv(self, a, a_out=""):
        return self.unary(unreal.MaterialExpressionAbs, a, a_out)

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

    def div(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDivide, a, b, a_out, b_out)

    def dotp(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionDotProduct, a, b, a_out, b_out)

    def minv(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMin, a, b, a_out, b_out)

    def maxv(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMax, a, b, a_out, b_out)

    def power(self, base, exp, base_out="", exp_out=""):
        n = self.node(unreal.MaterialExpressionPower)
        self._req(MEL.connect_material_expressions(base, base_out, n, "Base"), "Power.Base")
        # NB: Power's exponent pin is "Exp" in this UE 5.8 build's python
        # bindings, not "Exponent" - see apply_ground_material.py's comment.
        self._req(MEL.connect_material_expressions(exp, exp_out, n, "Exp"), "Power.Exp")
        return n

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self._req(MEL.connect_material_expressions(alpha, al_out, n, "Alpha"), "Lerp.Alpha")
        return n

    def scalar_param(self, name, default):
        n = self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name)
        n.set_editor_property("default_value", default)
        return n

    def vertex_color(self):
        return self.node(unreal.MaterialExpressionVertexColor)

    def tex_coord(self, index):
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
        """clamp01((x-edge)*sharpness) - a soft Heaviside step, ~binary at
        default sharpness. Used for lane-count/oneway threshold LOGIC (where
        the underlying value is already near-integer from roundv), not for
        anti-aliased stripe edges - see band() for those."""
        d = self.sub(x, edge, a_out=x_out, b_out=edge_out)
        return self.clamp01(self.mul(d, self.const(sharpness)))

    def roundv(self, x, x_out=""):
        return self.floorv(self.add(x, self.const(0.5), a_out=x_out))

    def band(self, dist_m, half_width_m):
        """1.0 inside +-half_width_m of a line center, antialiased falloff
        over ~1/LineSharpnessPerM meters, 0.0 outside - dist_m must already
        be a non-negative distance-from-line-center in meters."""
        over = self.sub(dist_m, self.const(half_width_m))
        return self.sub(self.const(1.0), self.clamp01(self.mul(over, self.const(LineSharpnessPerM))))


mat_path = f"{MAT_DIR}/{MAT_NAME}"

if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    log("--- pre-edit M_Road expression graph ---")
    try:
        for expr in MEL.get_material_expressions(mat):
            log(f"  {expr.get_class().get_name()} @ {MEL.get_material_expression_node_position(expr)}")
    except Exception as exc:
        log(f"  (inspection failed, proceeding with rebuild anyway: {exc})")
    log("--- end pre-edit graph ---")
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

g = G(mat)

# ---------------------------------------------------------------- decode ODR3 signals
uv0 = g.tex_coord(0)
u0 = g.mask(uv0, r=True)
v0 = g.mask(uv0, gg=True)   # meters, continuous along centerline

uv1 = g.tex_coord(1)
u1_raw = g.mask(uv1, r=True)   # half-width meters, 0.0 on a chunk with no ODR3 UV1 data
half_width_fallback = g.scalar_param("RoadHalfWidthFallbackM", RoadHalfWidthFallbackM)
# effHalfWidth = u1_raw if u1_raw > ~0 else fallback - "has real data" reads as
# u1_raw > 0.01 (never legitimately near-zero: pipeline's ROAD_HALF_WIDTH is
# several meters), lerp'd rather than branched (no dynamic branch node needed).
has_uv1 = g.step(g.const(0.01), u1_raw)
half_width_eff = g.lerp(half_width_fallback, u1_raw, has_uv1)

# junction_raw (Arc 7.17, 2026-09-04): uv1.y, repurposed from "reserved
# (0.0)" - see road_mesh_common.build_road_chunks()'s docstring. 0..1,
# smoothstep-eased by the pipeline itself (build_road_stations.py's
# junction-flatten pass), so no extra falloff shaping is needed here - just
# invert it into a 1=paint/0=suppress multiplier for the marking masks
# below. Never gates the base asphalt/gravel look (asphalt_albedo etc.
# above), only the painted-line layer - a junction still needs to READ as
# pavement, just without a centerline running through the open box.
junction_raw = g.mask(uv1, gg=True)
not_in_junction = g.sub(g.const(1.0), g.clamp01(junction_raw))

vcol = g.vertex_color()
class_norm = g.mask(vcol, r=True)      # road_class_id/6
lane_norm = g.mask(vcol, gg=True)      # lane_count/8
oneway_raw = g.mask(vcol, b=True)      # 0/1
wear_seed = g.mask(vcol, r=True, a_out="A")   # per-chunk 0..1
# NOTE: VertexColor's default "" output pin type-resolves as a 3-component
# (RGB) value at SM6 compile time in UE 5.8 - a ComponentMask requesting the
# alpha bit off of it fails with "Not enough components ... for component
# mask 0001" (confirmed live: r=True/gg=True/b=True against the default pin
# all compiled fine, only alpha did not). Alpha must instead be read off
# VertexColor's own dedicated named "A" scalar output pin (a_out="A" here),
# then re-masked with r=True since that pin is already a scalar.

class_id = g.roundv(g.mul(class_norm, g.const(6.0)))
lane_count = g.roundv(g.mul(lane_norm, g.const(8.0)))
oneway = g.step(g.const(0.5), oneway_raw)
has_data = g.clamp01(lane_count)   # lane_count==0 sentinel -> 0, any real 1..8 -> saturates to 1

# signed lateral offset from centerline, meters - the coordinate every
# marking band below is measured in.
meter_pos = g.mul(g.sub(u0, g.const(0.5)), g.mul(half_width_eff, g.const(2.0)))

# ---------------------------------------------------------------- base asphalt (class-picked, meters-tiled)
def uv_for(tile_m):
    scaled = g.node(unreal.MaterialExpressionAppendVector)
    g._req(MEL.connect_material_expressions(meter_pos, "", scaled, "A"), "Append.A(meterPos)")
    g._req(MEL.connect_material_expressions(v0, "", scaled, "B"), "Append.B(v0)")
    return g.mul(scaled, g.const(1.0 / tile_m))


uv_primary = uv_for(TILE_M["primary"])
uv_secondary = uv_for(TILE_M["secondary"])
uv_residential = uv_for(TILE_M["residential"])
uv_gravel = uv_for(TILE_M["gravel"])

tex_primary = TEXTURES["primary"]
tex_secondary = TEXTURES["secondary"]
tex_residential = TEXTURES["residential"]
tex_gravel = TEXTURES["gravel"]

alb_primary = g.sample(tex_primary["albedo"], uv_primary)
alb_secondary = g.sample(tex_secondary["albedo"], uv_secondary)
alb_residential = g.sample(tex_residential["albedo"], uv_residential)
nrm_primary = g.sample(tex_primary["normal"], uv_primary, is_normal=True)
nrm_secondary = g.sample(tex_secondary["normal"], uv_secondary, is_normal=True)
nrm_residential = g.sample(tex_residential["normal"], uv_residential, is_normal=True)
rgh_primary = g.mask(g.sample(tex_primary["rough"], uv_primary), r=True)
rgh_secondary = g.mask(g.sample(tex_secondary["rough"], uv_secondary), r=True)
rgh_residential = g.mask(g.sample(tex_residential["rough"], uv_residential), r=True)

# class_id 0-2 -> primary, 3-4 -> secondary, 5-6 -> residential (two soft
# thresholds chained, see G.step's docstring on why sharpness=1000 reads as
# effectively binary against an already-rounded integer input).
t_sec = g.step(g.const(2.5), class_id)
t_res = g.step(g.const(4.5), class_id)
asphalt_albedo = g.lerp(g.lerp(alb_primary, alb_secondary, t_sec, a_out="RGB", b_out="RGB"),
                         alb_residential, t_res, b_out="RGB")
asphalt_normal = g.lerp(g.lerp(nrm_primary, nrm_secondary, t_sec, a_out="RGB", b_out="RGB"),
                         nrm_residential, t_res, b_out="RGB")
asphalt_rough = g.lerp(g.lerp(rgh_primary, rgh_secondary, t_sec), rgh_residential, t_res)

# ---------------------------------------------------------------- shoulder (gravel) blend past the pavement edge
gravel_albedo = g.sample(tex_gravel["albedo"], uv_gravel)
gravel_normal = g.sample(tex_gravel["normal"], uv_gravel, is_normal=True)
gravel_rough = g.mask(g.sample(tex_gravel["rough"], uv_gravel), r=True)

dist_beyond_left = g.maxv(g.const(0.0), g.sub(g.mul(g.const(-1.0), half_width_eff), meter_pos))
dist_beyond_right = g.maxv(g.const(0.0), g.sub(meter_pos, half_width_eff))
dist_beyond = g.maxv(dist_beyond_left, dist_beyond_right)
shoulder_t = g.clamp01(g.mul(dist_beyond, g.const(1.0 / ShoulderBlendWidthM)))

base_albedo = g.lerp(asphalt_albedo, gravel_albedo, shoulder_t, b_out="RGB")
base_normal = g.lerp(asphalt_normal, gravel_normal, shoulder_t, b_out="RGB")
base_rough = g.lerp(asphalt_rough, gravel_rough, shoulder_t)

if SURFACE != "paved":   # unpaved: one base texture across pavement + shoulder
    tex_dir, unpaved_id, unpaved_tile_m = UNPAVED_BASE[SURFACE]
    tex_unpaved = load_asphalt(unpaved_id, tex_dir)
    uv_unpaved = uv_for(unpaved_tile_m)
    base_albedo = g.mask(g.sample(tex_unpaved["albedo"], uv_unpaved), r=True, gg=True, b=True, a_out="RGB")
    base_normal = g.mask(g.sample(tex_unpaved["normal"], uv_unpaved, is_normal=True),
                         r=True, gg=True, b=True, a_out="RGB")
    base_rough = g.mask(g.sample(tex_unpaved["rough"], uv_unpaved), r=True)

# ---------------------------------------------------------------- lane markings
abs_meter_pos = g.absv(meter_pos)

# double-yellow center: two stripes at meterPos = +-CenterLineGapHalfM
d_stripe = g.absv(g.sub(abs_meter_pos, g.const(CenterLineGapHalfM)))
center_line_mask = g.band(d_stripe, CenterLineHalfWidthM)
center_cond = g.mul(g.mul(g.sub(g.const(1.0), oneway), g.step(lane_count, g.const(LaneCountCenterMax))), has_data)
center_mask = g.mul(center_line_mask, center_cond)

# solid white edge lines, inset from each pavement edge
d_edge = g.absv(g.sub(abs_meter_pos, g.sub(half_width_eff, g.const(EdgeLineInsetM))))
edge_mask = g.mul(g.band(d_edge, EdgeLineHalfWidthM), has_data)

# white dashed internal lane separators - continuous "distance to nearest
# multiple of laneWidthM" so it works for any lane_count, not just a fixed
# handful of hardcoded boundary positions (see module docstring).
lane_width = g.div(g.mul(half_width_eff, g.const(2.0)), g.maxv(lane_count, g.const(1.0)))
cell_t = g.div(meter_pos, lane_width)
frac_t = g.fracv(cell_t)
dist_cell = g.minv(frac_t, g.sub(g.const(1.0), frac_t))
dist_boundary_m = g.mul(dist_cell, lane_width)
dash_spatial_mask = g.band(dist_boundary_m, DashHalfWidthM)

dash_v = g.fracv(g.mul(v0, g.const(1.0 / DashPeriodM)))
dash_temporal_mask = g.step(dash_v, g.const(DashPaintFraction), sharpness=200.0)

dash_cond_twoway = g.mul(g.sub(g.const(1.0), oneway), g.step(g.const(LaneCountDashMin_TwoWay), lane_count))
dash_cond_oneway = g.mul(oneway, g.step(g.const(LaneCountDashMin_OneWay), lane_count))
dash_cond = g.mul(g.clamp01(g.add(dash_cond_twoway, dash_cond_oneway)), has_data)
dash_mask = g.mul(g.mul(dash_spatial_mask, dash_temporal_mask), dash_cond)

paint_mask = g.clamp01(g.add(g.add(center_mask, edge_mask), dash_mask))
paint_is_yellow = center_mask   # already 0/1-ish and spatially disjoint from edge/dash

white_color = g.const3(0.88, 0.88, 0.86)
yellow_color = g.const3(0.85, 0.66, 0.06)
paint_color = g.lerp(white_color, yellow_color, paint_is_yellow)

# wear/fade: cheap 1D value noise on v0, offset per-chunk by the wear seed so
# adjacent chunks don't repeat the same fade pattern.
wear_scalar_param = g.scalar_param("WearMaxFade", WearMaxFade)
noise_x = g.add(g.mul(v0, g.const(1.0 / WearNoiseScaleM)), g.mul(wear_seed, g.const(97.0)))
noise_i = g.floorv(noise_x)
noise_f = g.fracv(noise_x)


def hash1(v):
    s = g.sinev(g.mul(v, g.const(127.1)))
    return g.fracv(g.mul(s, g.const(43758.5453)))


wear_noise = g.lerp(hash1(noise_i), hash1(g.add(noise_i, g.const(1.0))), noise_f)
wear_visibility = g.sub(g.const(1.0), g.mul(wear_noise, wear_scalar_param))

final_paint_mask = g.mul(g.mul(paint_mask, wear_visibility), not_in_junction)
if SURFACE != "paved":
    final_paint_mask = g.const(0.0)   # no lane markings on gravel/dirt
paint_rough = g.const(0.35)   # paint reads smoother/glossier than worn asphalt

dry_albedo = g.lerp(base_albedo, paint_color, final_paint_mask)
dry_rough = g.lerp(base_rough, paint_rough, final_paint_mask)

# ---------------------------------------------------------------- weather wetness (W2)
wetness = g.scalar_param("Wetness", 0.0)
wet_albedo = g.mul(dry_albedo, g.lerp(g.const(1.0), g.const(WetDarken), wetness))
wet_rough = g.lerp(dry_rough, g.const(WetRoughness), wetness)


# ---------------------------------------------------------------- snow (per-chunk depth, CPD)
def cpd_param(name, index):
    n = g.scalar_param(name, 0.0)
    n.set_editor_property("use_custom_primitive_data", True)
    n.set_editor_property("primitive_data_index", index)
    return n


snow_d = cpd_param("SnowDepth", 0)
side_d = cpd_param("SideSnowDepth", 1)
slush_t = g.clamp01(g.mul(snow_d, g.const(1.0 / SnowSlushFullCm)))
cover_raw = g.clamp01(g.mul(snow_d, g.const(1.0 / SnowCoverFullCm)))
# tire tracks: |offset from this lane's center| near TrackOffsetM, pavement only
d_lane_center = g.absv(g.mul(g.sub(frac_t, g.const(0.5)), lane_width))
track = g.mul(g.mul(g.band(g.absv(g.sub(d_lane_center, g.const(TrackOffsetM))), TrackHalfWidthM), has_data),
              g.sub(g.const(1.0), shoulder_t))
snow_cover = g.mul(cover_raw, g.sub(g.const(1.0), g.mul(track, g.const(1.0 - TrackCover))))
# berm faces (any vert with a UV2 direction) whiten with the roadside depth
uv2 = g.tex_coord(2)
berm_w = g.mul(g.clamp01(g.mul(g.dotp(uv2, uv2), g.const(64.0))),
               g.clamp01(g.mul(side_d, g.const(1.0 / 5.0))))
snow_white = g.maxv(snow_cover, berm_w)

slushy_albedo = g.lerp(wet_albedo, g.const3(*SlushColor), g.mul(slush_t, g.const(SlushOpacity)))
final_albedo = g.lerp(slushy_albedo, g.const3(*SnowColor), snow_white)
final_rough = g.lerp(g.lerp(wet_rough, g.const(SlushRoughness), slush_t), g.const(SnowRoughness), snow_white)
final_normal = g.lerp(base_normal, g.const3(0.0, 0.0, 1.0), snow_white)

# berm WPO: lip out by depth*BermOutPerCm, up from -BermLipDropCm to +depth
# (eased in over the first 10 cm so a dusting doesn't yank the lip up)
berm_xy = g.mul(uv2, g.mul(side_d, g.const(BermOutPerCm)))
berm_z = g.mul(g.mul(g.clamp01(g.dotp(uv2, uv2)), g.add(side_d, g.const(BermLipDropCm))),
               g.clamp01(g.mul(side_d, g.const(0.1))))
berm_wpo = g.node(unreal.MaterialExpressionAppendVector)
g._req(MEL.connect_material_expressions(berm_xy, "", berm_wpo, "A"), "Append.A(bermXY)")
g._req(MEL.connect_material_expressions(berm_z, "", berm_wpo, "B"), "Append.B(bermZ)")

MEL.connect_material_property(final_albedo, "", unreal.MaterialProperty.MP_BASE_COLOR)
MEL.connect_material_property(final_normal, "", unreal.MaterialProperty.MP_NORMAL)
MEL.connect_material_property(final_rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
MEL.connect_material_property(berm_wpo, "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# ---- VERIFY (2026-08-27+ convention, see apply_ground_material.py's
# identical block) - reload the just-saved asset fresh and confirm every
# base-material-property pin actually connected; a silently-unwired pin
# "succeeds" in headless -nullrhi mode with no error otherwise.
reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Normal", unreal.MaterialProperty.MP_NORMAL),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
    ("WorldPositionOffset", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET),
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
        f"{MAT_NAME}: one or more base material property pins failed to connect - "
        "see the post-save verification log above")

log(f"built + saved {MAT_NAME} - asphalt sets={list(TILE_M.keys())}, "
    f"fallback half-width={RoadHalfWidthFallbackM}m, Wetness param (darken {WetDarken}, "
    f"rough {WetRoughness}), SnowDepth/SideSnowDepth CPD params + berm WPO")
