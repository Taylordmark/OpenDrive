"""Track B (PLAN-VISUALS.md): Realistic car paint, glass and lights.

Builds a `_real` twin beside every car material in /Game/OpenDrive/Cars, which
ODVisualStyle::PickSlots swaps onto the chassis and wheel slots when Visual
Style = Realistic. Stylized assets are only read, never touched (V4).

  M_Car<Cat>_Hi_real   materials (High/Ultra meshes, smooth normals)
  M_Car<Cat>_real      instances of those, rougher where faceted Standard
                       meshes would turn a mirror finish into a disco ball

  body    Substrate Simple Clear Coat: glossy coat over a metallic base whose
          bottom normal is tilted per flake (T_CarFlake, triplanar in local
          space so the flakes ride with the car; the palette UVs are one
          point per colour cell and can't tile). Coat roughness varies with
          the flake texture's low-frequency alpha.
  glass   dark tinted translucent, Fresnel opacity (reflective at grazing),
          forward-shaded surface so it gets real specular.
  lights  glossy lens + the exact lamp emissive of apply_car_lights_material.py
          (same LAMP_HLSL, same HeadGlow/TailGlow/ReverseGlow params that
          AODDayNightController drives on the "lights" slot's MID).
  chrome / plastic: metal or dielectric off the palette; plastic adds a
          hex grille mesh on front-facing panels ahead of the front axle.
  rim     machined alloy (lathe rings in the roughness), polished lip,
          dark hub, brake dust.
  tire    world-space normal rebuilt from the wheel's shape (round even on
          the 20-segment Standard wheel), T_TireTread normals on the tread,
          molded lettering (T_TireSidewall) on the sidewalls.

Pass 2 (weather, dirt, detail): per-car state comes in as custom primitive
data written by UODVehicleFX (Realistic only): 0 Dirt (UODVehicleMovement,
per distance and surface), 1 Wetness, 2 SnowCover (the values the season
controller pushes to the ground and foliage), and on the chassis 3..5
FrontAxleX / RearAxleX / WheelRadius. From those and the car-local position:
road grime low on the body and behind each wheel, darkened wheel arches and
underbody, panel-gap lines, rain beads (T_CarRainDrops) on paint and glass,
mud when wet, and snow on the up-facing panels.

Every twin samples T_CarPalette2 through UV0 exactly as its stylized twin
does (colours stay the palette's) and keeps its parameters; the interface
check at the end fails the run if one is missing, or the blend mode or
physical material differ (PLAN-VISUALS.md interface rule).

Run content_src/realistic/cars/make_car_flake.py and make_car_detail.py first, then:
UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout

Shader check (the nullrhi run above compiles nothing): cook the twins and
expect no "Failed to compile Material" in the log, e.g. from Git Bash
MSYS_NO_PATHCONV=1 UnrealEditor-Cmd.exe <uproject> -run=cook -targetplatform=Windows
    -cooksinglepackagenorefs -package=/Game/OpenDrive/Cars/M_CarBody_Hi_real
    (one -package= per twin) -unattended -nosplash -nop4 -stdout
"""
import ast
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
CAR_DIR = "/Game/OpenDrive/Cars"
REAL_DIR = "/Game/OpenDrive/Realistic/Cars"
MEL = unreal.MaterialEditingLibrary
MP = unreal.MaterialProperty
tools = unreal.AssetToolsHelpers.get_asset_tools()
CATS = ("body", "plastic", "glass", "chrome", "tire", "rim", "lights")


def log(msg):
    unreal.log_warning(f"CARREAL {msg}")


def save(path):
    # a failed save (file locked by another process) must not pass as built
    if not unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"could not save {path}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"failed to connect {what}")


# the lamp mask lives in one place: the stylized lamp script
LAMP_HLSL = next(n.value.value for n in ast.parse(
    (ROOT / "unreal" / "apply_car_lights_material.py").read_text(encoding="utf-8")).body
    if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "LAMP_HLSL")

palette = unreal.load_asset(f"{CAR_DIR}/T_CarPalette2")
if palette is None:
    raise RuntimeError("T_CarPalette2 missing - run import_car_meshes.py first")

# ------------------------------------------------------------------- textures
def import_tex(name, maker):
    png = ROOT / "content_src" / "realistic" / "cars" / f"{name}.png"
    if not png.exists():
        raise RuntimeError(f"{png} missing - run content_src/realistic/cars/{maker}")
    t = unreal.AssetImportTask()
    t.filename, t.destination_path = str(png), REAL_DIR
    t.automated, t.save, t.replace_existing = True, False, True
    tools.import_asset_tasks([t])
    tex = unreal.load_asset(f"{REAL_DIR}/{name}")
    if tex is None:
        raise RuntimeError(f"{name} import failed")
    tex.set_editor_property("srgb", False)   # directions, masks and noise, not colour
    tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
    save(f"{REAL_DIR}/{name}")
    log(f"imported {name}")
    return tex


flake = import_tex("T_CarFlake", "make_car_flake.py")
tread_tex = import_tex("T_TireTread", "make_car_detail.py")
side_tex = import_tex("T_TireSidewall", "make_car_detail.py")
drops_tex = import_tex("T_CarRainDrops", "make_car_detail.py")


# --------------------------------------------------------------------- helpers
class G:
    """One material graph being built."""

    def __init__(self, mat):
        self.m, self.y = mat, 0

    def node(self, cls, x=-900, **props):
        self.y += 120
        e = MEL.create_material_expression(self.m, cls, x, self.y)
        for k, v in props.items():
            e.set_editor_property(k, v)
        return e

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def param(self, name, v):
        return self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name, default_value=v)

    def pin(self, src, prop):
        src, out = src if isinstance(src, tuple) else (src, "")
        req(MEL.connect_material_property(src, out, prop), str(prop))

    def wire(self, src, dst, dst_in):
        src, out = src if isinstance(src, tuple) else (src, "")
        req(MEL.connect_material_expressions(src, out, dst, dst_in), f"{dst_in}")

    def custom(self, code, out_type, outputs=(), **inputs):
        """outputs: extra (name, type) pins the code assigns by name."""
        c = self.node(unreal.MaterialExpressionCustom, x=-400, code=code, output_type=out_type)
        if outputs:
            extra = []
            for n, t in outputs:
                o = unreal.CustomOutput()
                o.set_editor_property("output_name", n)
                o.set_editor_property("output_type", t)
                extra.append(o)
            c.set_editor_property("additional_outputs", extra)
        ins = []
        for n in inputs:
            i = unreal.CustomInput()
            i.set_editor_property("input_name", n)
            ins.append(i)
        c.set_editor_property("inputs", ins)
        for n, src in inputs.items():
            self.wire(src, c, n)
        return c

    def cpd(self, name, index):
        """Per-car state from UODVehicleFX (custom primitive data, 0 when unset)."""
        n = self.param(name, 0.0)
        n.set_editor_property("use_custom_primitive_data", True)
        n.set_editor_property("primitive_data_index", index)
        return n

    def tex(self, t):
        return self.node(unreal.MaterialExpressionTextureObject, texture=t)

    def true_local_normal(self):
        """Car-local normal that points out of the surface on both sides:
        the meshes are two-sided with mixed winding, and VertexNormalWS is
        not flipped for back faces (TwoSidedSign is)."""
        n = self.custom("return N * S;", F3, N=self.node(unreal.MaterialExpressionVertexNormalWS),
                        S=self.node(unreal.MaterialExpressionTwoSidedSign))
        ln = self.node(unreal.MaterialExpressionTransform,
                       transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_WORLD,
                       transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_LOCAL)
        self.wire(n, ln, "")
        return ln

    def to_world(self, src):
        t = self.node(unreal.MaterialExpressionTransform,
                      transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_LOCAL,
                      transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_WORLD)
        self.wire(src, t, "")
        return t

    def cam_dist(self):
        """cm from the camera: pixel footprint for line AA and texture mips
        (no ddx/fwidth, so the same code compiles for ray-hit and compute)."""
        return self.custom("return length(W - C);", F1,
                           W=self.node(unreal.MaterialExpressionWorldPosition),
                           C=self.node(unreal.MaterialExpressionCameraPositionWS))

    def car_state(self, wheel=False):
        """CPD inputs: 0 Dirt, 1 Wetness; chassis also 2 SnowCover, 3..5 axle geometry."""
        st = {"Dirt": self.cpd("Dirt", 0), "Wet": self.cpd("Wetness", 1)}
        if not wheel:
            st["Snow"] = self.cpd("SnowCover", 2)
            st.update(Xf=self.cpd("FrontAxleX", 3), Xr=self.cpd("RearAxleX", 4), WR=self.cpd("WheelRadius", 5))
        return st

    def palette(self):
        return (self.node(unreal.MaterialExpressionTextureSample, texture=palette), "RGB")

    def local_pos(self):
        wp = self.node(unreal.MaterialExpressionWorldPosition)
        lp = self.node(unreal.MaterialExpressionTransformPosition,
                       transform_source_type=unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_WORLD,
                       transform_type=unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_LOCAL)
        self.wire(wp, lp, "")
        return lp

    def legacy(self, base, rough, metal, spec):
        self.pin(base, MP.MP_BASE_COLOR)
        self.pin(rough, MP.MP_ROUGHNESS)
        self.pin(self.const(metal), MP.MP_METALLIC)
        self.pin(self.const(spec), MP.MP_SPECULAR)


F3, F4, F1 = (unreal.CustomMaterialOutputType.CMOT_FLOAT3, unreal.CustomMaterialOutputType.CMOT_FLOAT4,
              unreal.CustomMaterialOutputType.CMOT_FLOAT1)

# --------------------------------------------------------------- shared HLSL
# Everything below is projected from the car's LOCAL position (cm, +x
# forward, z = 0 at axle height, so the ground is ~ -WheelRadius): the car
# UVs are one palette point per face and can't carry detail. Texture reads
# use SampleLevel with a mip from camera distance (no ddx), which also keeps
# the atan2 seam on the wheels free of a mip line.
#
# FRAME: triplanar weights, mip, axle geometry (CPD, sane fallbacks if unset)
FRAME = """
float3 w = pow(abs(N), 4.0); w /= dot(w, 1.0);
float px = Dist * 0.0012 + 0.02;                   // cm per pixel, ~1080p 90 deg
float mip = max(0.0, log2(px / 0.04));             // the detail textures are ~0.04 cm/texel
"""
CAR = """
float r = WR > 1.0 ? WR : 34.0;
float xf = Xf > 1.0 ? Xf : 130.0;
float xr = Xr < -1.0 ? Xr : -130.0;
float h = P.z + r;                                 // cm above the ground at rest
float mac = Texture2DSampleLevel(Flake, FlakeSampler, P.yz * 0.004, 0).a * w.x
          + Texture2DSampleLevel(Flake, FlakeSampler, P.xz * 0.004, 0).a * w.y
          + Texture2DSampleLevel(Flake, FlakeSampler, P.xy * 0.004, 0).a * w.z;
// occlusion: low panels, underbody, wheel-arch liners facing the axle
float under = saturate(-N.z * 1.5 - 0.2);
float ao = lerp(0.45, 1.0, saturate(h / 40.0)) * (1.0 - 0.7 * under);
float2 dF = float2(P.x - xf, P.z), dR = float2(P.x - xr, P.z);
float2 d = dot(dF, dF) < dot(dR, dR) ? dF : dR;
float dl = length(d) + 1e-3;
float arch = saturate(dot(-d / dl, N.xz) * 2.0) * (1.0 - smoothstep(r + 6.0, r + 22.0, dl));
ao *= 1.0 - 0.85 * arch;
// grime: low on the body, the spray fan behind each wheel, the tail's dust vortex
float low = 1.0 - smoothstep(10.0, 60.0, h);
float tF = (xf - P.x) / r, tR = (xr - P.x) / r;    // > 0 behind that axle
float fan = max(saturate(1.0 - abs(tF - 1.7) / 1.2), saturate(1.0 - abs(tR - 1.7) / 1.2))
          * (1.0 - smoothstep(15.0, 75.0, h));
float tail = saturate(-N.x * 1.5 - 0.3) * step(P.x, xr) * (1.0 - smoothstep(30.0, 100.0, h));
float m = saturate(max(max(low, fan), 0.8 * tail) + 0.6 * arch + 0.5 * under);
float grime = smoothstep(1.0 - Dirt, 1.25 - Dirt, m * lerp(0.6, 1.0, mac));
float3 dirtCol = float3(0.20, 0.16, 0.12) * lerp(1.0, 0.45, Wet);   // dust, or mud when wet
// snow settles on the up-facing panels
float snow = smoothstep(0.35, 0.75, N.z) * saturate(Snow * 1.5 - (1.0 - mac) * 0.5);
"""
BEAD = """
float4 dr = Texture2DSampleLevel(Drops, DropsSampler, P.yz * 0.05, mip) * w.x
          + Texture2DSampleLevel(Drops, DropsSampler, P.xz * 0.05, mip) * w.y
          + Texture2DSampleLevel(Drops, DropsSampler, P.xy * 0.05, mip) * w.z;
float bead = dr.a * saturate(Wet * 1.5) * saturate(N.z + 0.7);
"""

# local-space triplanar of T_CarFlake: xyz = flake direction (-1..1) at
# FlakeScale, w = low-frequency noise at MacroScale (both per cm)
FLAKE = """
float4 f = Texture2DSample(Flake, FlakeSampler, P.yz * Scale) * w.x
         + Texture2DSample(Flake, FlakeSampler, P.xz * Scale) * w.y
         + Texture2DSample(Flake, FlakeSampler, P.xy * Scale) * w.z;
float fm = Texture2DSampleLevel(Flake, FlakeSampler, P.yz * Macro, 0).a * w.x
         + Texture2DSampleLevel(Flake, FlakeSampler, P.xz * Macro, 0).a * w.y
         + Texture2DSampleLevel(Flake, FlakeSampler, P.xy * Macro, 0).a * w.z;
float3 fd = f.rgb * 2.0 - 1.0;
"""
# panel gaps: door shuts and bumper splits on the flanks, placed from the
# axles (a line ~0.4 cm wide that fades to its coverage when sub-pixel)
GAPS = """
float side = saturate(abs(N.y) * 3.0 - 1.6) * step(12.0, h) * step(h, r + 52.0);
float gx[4] = { xf + r + 20.0, xf - r - 28.0, xr + r + 30.0, xr - r - 20.0 };
float gd = 1e4;
for (int i = 0; i < 4; i++) gd = min(gd, abs(P.x - gx[i]));
float core = saturate((0.2 + 0.5 * px - gd) / (0.5 * px)) * min(1.0, 0.4 / px);
float halo = saturate(1.0 - gd / (1.5 + px)) * min(1.0, 1.5 / px);
float gap = 1.0 - side * (0.85 * core + 0.25 * halo);
"""


def body(g):
    st = g.car_state()
    conv = g.node(unreal.MaterialExpressionSubstrateMetalnessToDiffuseAlbedoF0)
    g.wire(g.palette(), conv, "BaseColor")
    g.wire(g.param("BaseMetallic", 0.6), conv, "Metallic")
    g.wire(g.const(0.5), conv, "Specular")
    code = FRAME + CAR + BEAD + FLAKE + GAPS + """
float k = 1.0 - max(grime, snow);              // bare paint left
float occ = ao * gap;
F0o = lerp(F0 * (1.0 + Sparkle * fd.z) * occ, 0.04, 1.0 - k);
Rough = lerp(BaseR, 0.85, 1.0 - k);
Cov = (1.0 - 0.9 * grime) * (1.0 - snow);
CoatR = lerp(CoatR0 * lerp(0.7, 1.6, fm) + 0.12 * Dirt, CoatR0 * 0.6, Wet);
TopN = normalize(float3((dr.rg * 2.0 - 1.0) * bead * 2.0, 1.0));
// tangent-space tilt under the coat; any tangent basis will do for random flakes
BotN = normalize(float3(fd.xy * Strength, 1.0));
return lerp(lerp(Alb * occ, dirtCol * ao, grime), 0.85, snow);
"""
    O = [("F0o", F3), ("Rough", F1), ("Cov", F1), ("CoatR", F1), ("TopN", F3), ("BotN", F3)]
    c = g.custom(code, F3, outputs=O, P=g.local_pos(), N=g.true_local_normal(), Dist=g.cam_dist(),
                 Flake=g.tex(flake), Drops=g.tex(drops_tex),
                 Alb=(conv, "DiffuseAlbedo"), F0=(conv, "F0"),
                 Scale=g.param("FlakeScale", 0.25), Macro=g.param("MacroScale", 0.004),
                 Sparkle=g.param("FlakeSparkle", 0.3), BaseR=g.param("BaseRoughness", 0.38),
                 CoatR0=g.param("CoatRoughness", 0.035), Strength=g.param("FlakeStrength", 0.35), **st)
    slab = g.node(unreal.MaterialExpressionSubstrateSimpleClearCoatBSDF, x=-150)
    g.wire(c, slab, "Diffuse Albedo")
    for out, pin in (("F0o", "F0"), ("Rough", "Roughness"), ("Cov", "Clear Coat Coverage"),
                     ("CoatR", "Clear Coat Roughness"), ("TopN", "Normal"), ("BotN", "Bottom Normal")):
        g.wire((c, out), slab, pin)
    g.pin(slab, MP.MP_FRONT_MATERIAL)


def trim(rough, metal, spec, grille=False, tint=1.0):
    """Plastic and chrome: palette colour, the shared grime/snow/occlusion,
    and (plastic) a hex grille mesh on front-facing panels ahead of the axle."""
    def build(g):
        st = g.car_state()
        code = FRAME + CAR + """
float3 alb = saturate(Pal * Tint) * ao;
float rgh = R;
float met = M0;
""" + ("""
float front = saturate(N.x * 4.0 - 2.8) * step(xf + 0.3 * r, P.x) * step(8.0, h);
float2 q = P.yz / 2.2, s = float2(1.0, 1.7320508);
float2 a = (frac(q / s) - 0.5) * s, b = (frac((q - 0.5 * s) / s) - 0.5) * s;
float2 c = dot(a, a) < dot(b, b) ? a : b;
float hd = max(dot(abs(c), float2(0.5, 0.8660254)), abs(c.x));   // 0.5 = cell edge
float pq = px / 2.2;
float hole = lerp(saturate((0.40 - hd) / max(pq, 0.02)), 0.6, saturate(pq * 2.0 - 0.3));
alb *= lerp(1.0, 0.08, front * hole);
rgh = lerp(rgh, 0.9, front * hole);
""" if grille else "") + """
float k = max(grime, snow);
Rough = lerp(lerp(rgh, 0.85, grime), 0.75, snow);
MetalO = met * (1.0 - k);
return lerp(lerp(alb, dirtCol * ao, grime), 0.85, snow);
"""
        c = g.custom(code, F3, outputs=[("Rough", F1), ("MetalO", F1)],
                     P=g.local_pos(), N=g.true_local_normal(), Dist=g.cam_dist(), Flake=g.tex(flake),
                     Pal=g.palette(), Tint=g.const(tint), R=g.param("Roughness", rough), M0=g.const(metal), **st)
        g.pin(c, MP.MP_BASE_COLOR)
        g.pin((c, "Rough"), MP.MP_ROUGHNESS)
        g.pin((c, "MetalO"), MP.MP_METALLIC)
        g.pin(g.const(spec), MP.MP_SPECULAR)
    return build


def glass(g):
    c = g.custom(FRAME + BEAD + """
Rough = lerp(R, 0.02, bead);
TopN = normalize(float3((dr.rg * 2.0 - 1.0) * bead * 2.0, 1.0));
return Pal * D;
""", F3, outputs=[("Rough", F1), ("TopN", F3)], P=g.local_pos(), N=g.true_local_normal(),
                 Dist=g.cam_dist(), Drops=g.tex(drops_tex), Pal=g.palette(),
                 D=g.param("GlassDarken", 0.4), R=g.param("Roughness", 0.02), Wet=g.cpd("Wetness", 1))
    g.legacy(c, (c, "Rough"), 0.0, 0.5)
    g.pin((c, "TopN"), MP.MP_NORMAL)
    fr = g.node(unreal.MaterialExpressionFresnel, exponent=4.0, base_reflect_fraction=0.04)
    g.pin(g.custom("return lerp(O, 1.0, Fr);", F1, O=g.param("Opacity", 0.82), Fr=fr), MP.MP_OPACITY)
    g.m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    # "Surface ForwardShading" in the editor: per-pixel specular from sun and sky
    g.m.set_editor_property("translucency_lighting_mode",
                            unreal.TranslucencyLightingMode.TLM_SURFACE_PER_PIXEL_LIGHTING)


def lights(g):
    pal = g.palette()
    g.legacy(pal, g.param("Roughness", 0.05), 0.0, 0.5)
    ins = {"Pal": pal}
    for n in ("Head", "Tail", "Reverse"):
        ins[n] = g.param(f"{n}Glow", 0.0)
    ins["LocalX"] = g.node(unreal.MaterialExpressionComponentMask, r=True)
    g.wire(g.local_pos(), ins["LocalX"], "")
    g.pin(g.custom(LAMP_HLSL, F3, **ins), MP.MP_EMISSIVE_COLOR)


# Wheel, in its local cm before the pawn's per-car scale, axle = local Y.
# The shape is parameters (defaults: today's pipeline/make_car_meshes.py
# build_wheel - rim radius 21.76, spoke gaps (TIRE cells on the dish plane)
# inside r 20.2, tread |y| < 9, rounded shoulder centred on (r 30, |y| 9)),
# so a new wheel mesh only needs new instance values, not new code. The
# tire's normal is rebuilt from that shape, so even the 20-segment Standard
# tire shades round. Nothing here reads the wheel's UVs.
WHEEL_SHAPE = {"RimRadius": 21.76, "ShoulderRadius": 30.0, "TreadHalfWidth": 9.0, "SpokeGapRadius": 20.2,
               "RimLipRadius": 19.5, "HubRadius": 6.5}
TIRE = """
float r = length(P.xz) + 1e-3;
float a = atan2(P.z, P.x);
float s = P.y >= 0.0 ? 1.0 : -1.0;
float ay = abs(P.y);
float3 rad = float3(P.x / r, 0.0, P.z / r);
float3 tng = float3(-rad.z, 0.0, rad.x);           // d/d(angle)
float3 ax = float3(0.0, s, 0.0);
float mip = max(0.0, log2((Dist * 0.0012 + 0.02) / 0.04));
float3 alb = Pal;
float rgh = SideR;
float3 n = ax;
float dust = Dirt * 0.6;
if (r < GapR) {                                    // between the spokes: the dark wheel well behind
    alb = Pal * 0.15; rgh = 0.9; dust = 0.0;
} else if (ay < TreadHW && r > ShoulderR) {                 // tread
    float4 t = Texture2DSampleLevel(Tread, TreadSampler, float2(a * (56.0 / 6.2831853), P.y / (2.0 * TreadHW) + 0.5), mip);
    float3 tn = t.rgb * 2.0 - 1.0;
    n = normalize(tng * tn.x + float3(0.0, 1.0, 0.0) * tn.y + rad * tn.z);
    alb = Pal * lerp(1.15, 0.6, t.a);
    rgh = lerp(TreadR, 0.95, t.a);
    dust = Dirt * 0.8;
} else if (r >= ShoulderR) {                            // shoulder
    n = normalize(rad * (r - ShoulderR) + ax * max(ay - TreadHW, 0.0) + 1e-4);
    rgh = lerp(SideR, TreadR, 0.5);
} else {                                           // sidewall, molded lettering
    float v = saturate((r - RimR) / (ShoulderR - RimR));
    float4 t = Texture2DSampleLevel(Side, SideSampler, float2(s * a * (2.0 / 6.2831853), v), mip);
    float3 tn = t.rgb * 2.0 - 1.0;
    float3 n0 = normalize(ax + rad * (r - 0.5 * (RimR + ShoulderR)) * 0.04);   // the sidewall bulge
    n = normalize(s * tng * tn.x + rad * tn.y + n0 * tn.z);
    alb = Pal * lerp(1.0, 1.6, t.a);
    rgh = lerp(SideR, 0.45, t.a);
}
alb = lerp(alb, float3(0.16, 0.14, 0.11), dust) * lerp(1.0, 0.75, Wet);
Rough = lerp(lerp(rgh, 0.9, dust), rgh * 0.45, Wet);
Nl = n;
return alb;
"""


def tire(g):
    st = g.car_state(wheel=True)
    c = g.custom(TIRE, F3, outputs=[("Rough", F1), ("Nl", F3)], P=g.local_pos(), Dist=g.cam_dist(),
                 Pal=g.palette(), Tread=g.tex(tread_tex), Side=g.tex(side_tex),
                 TreadR=g.param("TreadRoughness", 0.85), SideR=g.param("SidewallRoughness", 0.62),
                 Dirt=st["Dirt"], Wet=st["Wet"], RimR=g.param("RimRadius", WHEEL_SHAPE["RimRadius"]),
                 ShoulderR=g.param("ShoulderRadius", WHEEL_SHAPE["ShoulderRadius"]),
                 TreadHW=g.param("TreadHalfWidth", WHEEL_SHAPE["TreadHalfWidth"]),
                 GapR=g.param("SpokeGapRadius", WHEEL_SHAPE["SpokeGapRadius"]))
    g.legacy(c, (c, "Rough"), 0.0, 0.4)
    g.pin(g.to_world((c, "Nl")), MP.MP_NORMAL)
    # the normal above is world space and already outward: no two-sided flip
    g.m.set_editor_property("tangent_space_normal", False)


# machined alloy face (lathe rings in the roughness), polished lip, dark
# painted hub, brake dust that builds with the car's Dirt
RIM = """
float r = length(P.xz);
float lip = step(LipR, r);
float hub = 1.0 - step(HubR, r);
float ring = frac(sin(floor(r * 6.0) * 12.9898) * 43758.5453) * saturate(1.0 - Dist / 800.0);
float3 alb = saturate(Pal * 1.5) * (0.92 + 0.08 * ring);
float rgh = lerp(R + 0.08 * ring, 0.08, lip);
alb = lerp(alb, Pal * 0.35, hub);
rgh = lerp(rgh, 0.45, hub);
float bd = Dirt * (0.45 + 0.55 * saturate(1.0 - (r - 8.0) / 14.0));
alb = lerp(alb, float3(0.09, 0.075, 0.06), 0.8 * bd);
Rough = lerp(rgh, 0.7, 0.8 * bd);
MetalO = (1.0 - 0.7 * bd) * (1.0 - 0.5 * hub);
return alb;
"""


def rim(g):
    st = g.car_state(wheel=True)
    c = g.custom(RIM, F3, outputs=[("Rough", F1), ("MetalO", F1)], P=g.local_pos(), Dist=g.cam_dist(),
                 Pal=g.palette(), R=g.param("Roughness", 0.2), Dirt=st["Dirt"],
                 LipR=g.param("RimLipRadius", WHEEL_SHAPE["RimLipRadius"]),
                 HubR=g.param("HubRadius", WHEEL_SHAPE["HubRadius"]))
    g.pin(c, MP.MP_BASE_COLOR)
    g.pin((c, "Rough"), MP.MP_ROUGHNESS)
    g.pin((c, "MetalO"), MP.MP_METALLIC)
    g.pin(g.const(0.5), MP.MP_SPECULAR)


BUILD = {
    "body": body, "glass": glass, "lights": lights, "tire": tire, "rim": rim,
    "chrome": trim(0.03, 1.0, 0.5, tint=1.15),
    "plastic": trim(0.55, 0.0, 0.4, grille=True),
}

# Standard-tier instances: faceted normals need a softer highlight
STANDARD_OVERRIDES = {
    "body": {"CoatRoughness": 0.12, "BaseRoughness": 0.45},
    "chrome": {"Roughness": 0.1},
    "rim": {"Roughness": 0.3},
}


def stylized_name(cat, hi):
    return f"M_Car{cat.capitalize()}{'_Hi' if hi else ''}"


def build_material(cat):
    name = stylized_name(cat, True) + "_real"
    path = f"{CAR_DIR}/{name}"
    mat = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if mat is not None:
        for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
            MEL.delete_material_expression(mat, _e)
    else:
        mat = tools.create_asset(name, CAR_DIR, unreal.Material, unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError(f"could not create or load {path}")
    BUILD[cat](G(mat))
    mat.set_editor_property("two_sided", True)    # mirrored OBJ winding (import_car_meshes.py)
    if cat != "glass":
        mat.set_editor_property("used_with_nanite", True)
    MEL.recompile_material(mat)
    save(path)
    log(f"built {name}")
    return mat


def build_instance(cat, parent):
    name = stylized_name(cat, False) + "_real"
    path = f"{CAR_DIR}/{name}"
    mi = unreal.load_asset(path) if unreal.EditorAssetLibrary.does_asset_exist(path) else None
    if mi is None:
        mi = tools.create_asset(name, CAR_DIR, unreal.MaterialInstanceConstant,
                                unreal.MaterialInstanceConstantFactoryNew())
    MEL.set_material_instance_parent(mi, parent)
    MEL.clear_all_material_instance_parameters(mi)
    for k, v in STANDARD_OVERRIDES.get(cat, {}).items():
        MEL.set_material_instance_scalar_parameter_value(mi, k, v)
    save(path)
    log(f"built {name} (instance of {parent.get_name()}, {STANDARD_OVERRIDES.get(cat, {})})")


for cat in CATS:
    build_instance(cat, build_material(cat))


# ------------------------------------------------------------- interface rule
def params(m):
    return {kind: {str(p) for p in fn(m)} for kind, fn in (
        ("scalar", MEL.get_scalar_parameter_names), ("vector", MEL.get_vector_parameter_names),
        ("texture", MEL.get_texture_parameter_names), ("switch", MEL.get_static_switch_parameter_names))}


bad = []
for cat in CATS:
    for hi in (True, False):
        sname = stylized_name(cat, hi)
        s = unreal.load_asset(f"{CAR_DIR}/{sname}")
        r = unreal.load_asset(f"{CAR_DIR}/{sname}_real")
        if s is None or r is None:
            bad.append(f"{sname}: {'stylized' if s is None else '_real'} missing")
            continue
        ps, pr = params(s), params(r)
        miss = {k: sorted(ps[k] - pr[k]) for k in ps if ps[k] - pr[k]}
        if miss:
            bad.append(f"{sname}_real lacks {miss}")
        sb, rb = s.get_base_material(), r.get_base_material()
        if sb.get_editor_property("blend_mode") != rb.get_editor_property("blend_mode"):
            bad.append(f"{sname}_real blend mode differs")
        if sb.get_editor_property("phys_material") != rb.get_editor_property("phys_material"):
            bad.append(f"{sname}_real physical material differs")
        log(f"interface {sname} -> {sname}_real: stylized {ps['scalar'] or '{}'} kept; "
            f"real scalars {sorted(pr['scalar'])}")
if bad:
    raise RuntimeError("interface rule failed: " + "; ".join(bad))
log(f"DONE - {len(CATS) * 2} _real car materials built, interface rule passed")
