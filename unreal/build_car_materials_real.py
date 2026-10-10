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
  chrome / rim / plastic: metal or dielectric constants off the palette.
  tire    tread rough and dusty, sidewall satin (local normal along the axle).

Every twin samples T_CarPalette2 through UV0 exactly as its stylized twin
does (colours stay the palette's) and keeps its parameters; the interface
check at the end fails the run if one is missing, or the blend mode or
physical material differ (PLAN-VISUALS.md interface rule).

Run content_src/realistic/cars/make_car_flake.py first, then:
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

# ---------------------------------------------------------------- flake texture
flake_png = ROOT / "content_src" / "realistic" / "cars" / "T_CarFlake.png"
if not flake_png.exists():
    raise RuntimeError(f"{flake_png} missing - run content_src/realistic/cars/make_car_flake.py")
t = unreal.AssetImportTask()
t.filename, t.destination_path = str(flake_png), REAL_DIR
t.automated, t.save, t.replace_existing = True, False, True
tools.import_asset_tasks([t])
flake = unreal.load_asset(f"{REAL_DIR}/T_CarFlake")
if flake is None:
    raise RuntimeError("T_CarFlake import failed")
flake.set_editor_property("srgb", False)   # directions and noise, not colour
flake.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
save(f"{REAL_DIR}/T_CarFlake")
log("imported T_CarFlake")


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

    def custom(self, code, out_type, **inputs):
        c = self.node(unreal.MaterialExpressionCustom, x=-400, code=code, output_type=out_type)
        ins = []
        for n in inputs:
            i = unreal.CustomInput()
            i.set_editor_property("input_name", n)
            ins.append(i)
        c.set_editor_property("inputs", ins)
        for n, src in inputs.items():
            self.wire(src, c, n)
        return c

    def palette(self):
        return (self.node(unreal.MaterialExpressionTextureSample, texture=palette), "RGB")

    def local_pos(self):
        wp = self.node(unreal.MaterialExpressionWorldPosition)
        lp = self.node(unreal.MaterialExpressionTransformPosition,
                       transform_source_type=unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_WORLD,
                       transform_type=unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_LOCAL)
        self.wire(wp, lp, "")
        return lp

    def local_normal(self):
        n = self.node(unreal.MaterialExpressionVertexNormalWS)
        ln = self.node(unreal.MaterialExpressionTransform,
                       transform_source_type=unreal.MaterialVectorCoordTransformSource.TRANSFORMSOURCE_WORLD,
                       transform_type=unreal.MaterialVectorCoordTransform.TRANSFORM_LOCAL)
        self.wire(n, ln, "")
        return ln

    def legacy(self, base, rough, metal, spec):
        self.pin(base, MP.MP_BASE_COLOR)
        self.pin(rough, MP.MP_ROUGHNESS)
        self.pin(self.const(metal), MP.MP_METALLIC)
        self.pin(self.const(spec), MP.MP_SPECULAR)


F3, F4, F1 = (unreal.CustomMaterialOutputType.CMOT_FLOAT3, unreal.CustomMaterialOutputType.CMOT_FLOAT4,
              unreal.CustomMaterialOutputType.CMOT_FLOAT1)

# local-space triplanar of T_CarFlake: xyz = flake direction (-1..1) at
# FlakeScale, w = low-frequency noise at MacroScale (both per cm)
FLAKE_HLSL = """
float3 w = pow(abs(N), 4.0); w /= dot(w, 1.0);
float4 f = S(P.yz * Scale) * w.x + S(P.xz * Scale) * w.y + S(P.xy * Scale) * w.z;
float m = S(P.yz * Macro).a * w.x + S(P.xz * Macro).a * w.y + S(P.xy * Macro).a * w.z;
return float4(f.rgb * 2.0 - 1.0, m);
""".replace("S(", "Texture2DSample(Tex, TexSampler, ")   # SampleLevel in ray-hit shaders (cook-checked)


def body(g):
    f = g.custom(FLAKE_HLSL, F4, Tex=g.node(unreal.MaterialExpressionTextureObject, texture=flake),
                 P=g.local_pos(), N=g.local_normal(),
                 Scale=g.param("FlakeScale", 0.25), Macro=g.param("MacroScale", 0.004))
    conv = g.node(unreal.MaterialExpressionSubstrateMetalnessToDiffuseAlbedoF0)
    g.wire(g.palette(), conv, "BaseColor")
    g.wire(g.param("BaseMetallic", 0.6), conv, "Metallic")
    g.wire(g.const(0.5), conv, "Specular")
    slab = g.node(unreal.MaterialExpressionSubstrateSimpleClearCoatBSDF, x=-150)
    g.wire((conv, "DiffuseAlbedo"), slab, "Diffuse Albedo")
    # flake speckle: each flake's F0 a little brighter or darker (mips average it out)
    g.wire(g.custom("return F0 * (1.0 + Sparkle * F.z);", F3, F0=(conv, "F0"), F=f,
                    Sparkle=g.param("FlakeSparkle", 0.3)), slab, "F0")
    g.wire(g.param("BaseRoughness", 0.38), slab, "Roughness")
    g.wire(g.const(1.0), slab, "Clear Coat Coverage")
    g.wire(g.custom("return R * lerp(0.7, 1.6, F.w);", F1, R=g.param("CoatRoughness", 0.035), F=f),
           slab, "Clear Coat Roughness")
    # tangent-space tilt under the coat; any tangent basis will do for random flakes
    g.wire(g.custom("return normalize(float3(F.xy * S, 1.0));", F3, F=f,
                    S=g.param("FlakeStrength", 0.35)), slab, "Bottom Normal")
    g.pin(slab, MP.MP_FRONT_MATERIAL)


def glass(g):
    g.legacy(g.custom("return Pal * D;", F3, Pal=g.palette(), D=g.param("GlassDarken", 0.4)),
             g.param("Roughness", 0.02), 0.0, 0.5)
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


def tire(g):
    # wheel axle = local Y (make_car_meshes.py build_wheel): sidewall faces point along it
    t = g.custom("float s = saturate((abs(N.y) - 0.5) * 4.0);"
                 " return float4(Pal * lerp(1.6, 1.0, s), lerp(Tread, Side, s));", F4,
                 Pal=g.palette(), N=g.local_normal(),
                 Tread=g.param("TreadRoughness", 0.85), Side=g.param("SidewallRoughness", 0.62))
    rgb = g.node(unreal.MaterialExpressionComponentMask, r=True, g=True, b=True)
    a = g.node(unreal.MaterialExpressionComponentMask, a=True)
    g.wire(t, rgb, "")
    g.wire(t, a, "")
    g.legacy(rgb, a, 0.0, 0.4)


def simple(rough, metal, spec, tint=1.0):
    def build(g):
        base = g.palette()
        if tint != 1.0:
            base = g.custom("return saturate(Pal * T);", F3, Pal=base, T=g.const(tint))
        g.legacy(base, g.param("Roughness", rough), metal, spec)
    return build


BUILD = {
    "body": body, "glass": glass, "lights": lights, "tire": tire,
    "chrome": simple(0.03, 1.0, 0.5),
    "rim": simple(0.2, 1.0, 0.5, tint=1.5),     # palette 0.5 grey -> machined aluminium
    "plastic": simple(0.55, 0.0, 0.4),
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
        MEL.delete_all_material_expressions(mat)
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
