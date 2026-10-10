"""PLAN-VISUALS guardrails track: the Realistic guardrail assets.

  M_Guardrail_WBeam_real    twin of M_Guardrail_WBeam (apply_guardrail_
                            assets.py): galvanized steel - Metal038 scratches,
                            zinc spangle, roughness breakup, road grime toward
                            the bottom edge, light rust streaks, scrapes on
                            the bulge where cars hit it
  M_Guardrail_Jersey_real   twin of M_Guardrail_Jersey: weathered Concrete035,
                            dirt and tire scuffs on the lower slope, run-off
                            stains from the top
  M_GuardrailReflector_real the clip-on delineator: white, and at night it
                            lights up toward the camera (the headlights sit
                            next to it), a cheap retroreflection
  SM_GuardrailPost_real     pipeline/make_guardrail_post_mesh.py's W6x9 post +
                            blockout + delineator; AODGuardrailManager uses it
                            instead of the cylinder in Realistic only

Both twins are found by ODVisualStyle::Pick (AODGuardrailManager::BeginPlay);
the post mesh and reflector live in /Game/OpenDrive/Realistic/Guardrails/.

Mapping: the rail ribbon's UV0.U is station index * 20 (not distance) and
its V is the profile layer / (layers - 1), so textures and noise are
projected world-space triplanar (wrapped to WRAP_CM first, for float
precision far from the origin). UV0.V is still read, as a height-above-
ground gradient for the grime/dirt/scuff/stain bands: WBeamProfile layers
sit at z 0/58/74/92 (V 0/.33/.67/1), JerseyProfile at z 0/0/62/100/100
(V 0/.25/.5/.75/1, the road-facing slope is V .25-.75). The post mesh writes
V = z / 175 for the same grime. Normals are output world-space (the ribbon
has no meaningful tangents) via a whiteout triplanar blend.

Same physical material, blend mode, two-sidedness and usage flags as each
twin (copied); the twins have no parameters, and the _real ones add none.
Re-runs clear the graph node by node in place (delete_all_material_
expressions is broken in 5.8).

Needs: python content_src/textures/ground/download_ground_textures.py
       content_src/realistic/guardrails/manifest.json
       python pipeline/make_guardrail_post_mesh.py
       apply_guardrail_assets.py (the twins)
UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
Shader check (nullrhi compiles nothing): cook the three materials and expect
no "Failed to compile Material" (see apply_building_material_real.py).
"""

import runpy
from pathlib import Path

import unreal

ROOT = Path(__file__).resolve().parents[1]
MAT_DIR = "/Game/OpenDrive/Materials"
REAL_DIR = "/Game/OpenDrive/Realistic/Guardrails"
POST_OBJ = ROOT / "data" / "guardrails" / "obj" / "SM_GuardrailPost_real.obj"

WRAP_CM = 20000.0            # world position period before the float math (texture tiles divide it)
TRIPLANAR_SHARPNESS = 4.0

# ---- W-beam ------------------------------------------------------------------
STEEL_TILE_CM = 100.0
STEEL_TEX_LUMA = 0.12        # Metal038 Color mean linear luma (measured off the 2K map)
ZINC = (0.60, 0.61, 0.63)    # weathered galvanizing F0
SPANGLE_CM = 1.6             # zinc crystal size
SPANGLE_TINT, SPANGLE_ROUGH = 0.10, 0.12
STEEL_ROUGH_ADD = 0.12       # Metal038 roughness mean 0.37 -> ~0.49
GRIME_V = 0.30               # grime fades out by ~V .3 (~50 cm up the plate)
GRIME = (0.10, 0.085, 0.065)
GRIME_MAX = 0.75
RUST = (0.24, 0.09, 0.03)
RUST_MAX = 0.45
SCRAPE_V = (0.40, 0.50, 0.85, 0.95)   # bulge band (z ~45-88 cm), where bumpers land

# ---- Jersey ------------------------------------------------------------------
CONC_TILE_CM = 200.0
CONC_TEX_LUMA = 0.232        # Concrete035 Color mean linear luma
CONCRETE = (0.40, 0.39, 0.36)
CONC_ROUGH_ADD = 0.35        # Concrete035 roughness mean 0.48 -> ~0.83
DIRT = (0.13, 0.11, 0.08)
DIRT_V = (0.25, 0.42)        # base of the slope -> ~35 cm up
SCUFF_V = (0.26, 0.29, 0.38, 0.44)    # tire contact band, ~5-40 cm up
STAIN_V = (0.30, 0.75)       # run-off stains strongest near the top

# ---- reflector ---------------------------------------------------------------
REFLECT = (1.0, 0.95, 0.85)
REFLECT_NITS = 6.0
REFLECT_FACING_POW = 8.0
REFLECT_FADE_CM = (6000.0, 15000.0)   # past the low beams' reach
NIGHT_ELEV_HI, NIGHT_ELEV_SPAN = 2.0, 8.0   # AODDayNightController::NightFactor

USAGE_FLAGS = ("used_with_instanced_static_meshes", "used_with_nanite", "used_with_skeletal_mesh",
               "used_with_static_lighting", "used_with_spline_meshes", "used_with_morph_targets")
MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary
MP = unreal.MaterialProperty
F1, F3 = unreal.CustomMaterialOutputType.CMOT_FLOAT1, unreal.CustomMaterialOutputType.CMOT_FLOAT3
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"GUARDRAILREAL {msg}")


def f3(v):
    return "float3(%r, %r, %r)" % tuple(v)


# Shared HLSL: hash/value noise (struct trick - Custom nodes can't declare
# free functions) and the triplanar blend of the nine samples.
PRELUDE = """
struct GRN {
    float h3(float3 p) { p = frac(p * 0.3183099 + 0.1); p *= 17.0; return frac(p.x * p.y * p.z * (p.x + p.y + p.z)); }
    float n3(float3 x) {
        float3 i = floor(x); float3 f = frac(x); f = f * f * (3.0 - 2.0 * f);
        return lerp(lerp(lerp(h3(i), h3(i + float3(1,0,0)), f.x), lerp(h3(i + float3(0,1,0)), h3(i + float3(1,1,0)), f.x), f.y),
                    lerp(lerp(h3(i + float3(0,0,1)), h3(i + float3(1,0,1)), f.x), lerp(h3(i + float3(0,1,1)), h3(i + float3(1,1,1)), f.x), f.y), f.z);
    }
};
GRN g;
float3 w = pow(abs(N), %(sharp)r); w /= dot(w, 1.0);
float3 alb = Ax * w.x + Ay * w.y + Az * w.z;
float rough = Rx * w.x + Ry * w.y + Rz * w.z;
float3 nX = float3(Nx.xy + N.yz, abs(Nx.z) * N.x);
float3 nY = float3(Ny.xy + N.xz, abs(Ny.z) * N.y);
float3 nZ = float3(Nz.xy + N.xy, abs(Nz.z) * N.z);
float3 nrm = normalize(nX.zxy * w.x + nY.xzy * w.y + nZ.xyz * w.z);
""" % {"sharp": TRIPLANAR_SHARPNESS}

WBEAM_HLSL = PRELUDE + """
float3 col = %(zinc)s * alb / %(luma)r;
float metal = 1.0;
float fw = length(fwidth(P));
float sp = (g.h3(floor(P / %(sp)r) + 0.37) - 0.5) * saturate(1.0 - fw / %(sp)r);   // fades before it aliases
col *= 1.0 + %(sp_t)r * sp;
rough += %(rough_add)r + %(sp_r)r * sp;
float patch = g.n3(P / 90.0);
col *= lerp(0.88, 1.05, patch);
rough += (patch - 0.5) * 0.15;
float scrape = smoothstep(0.72, 0.86, g.n3(P * float3(1.0 / 150.0, 1.0 / 150.0, 1.0 / 1.2) + 5.1))
             * smoothstep(0.5, 0.7, g.n3(P / 500.0 + 3.7))
             * smoothstep(%(sv0)r, %(sv1)r, V) * (1.0 - smoothstep(%(sv2)r, %(sv3)r, V));
col = lerp(col, min(col * 1.4, 0.85), scrape);
rough = lerp(rough, 0.18, scrape);
float rust = smoothstep(0.62, 0.82, g.n3(P * float3(1.0 / 3.0, 1.0 / 3.0, 1.0 / 45.0)))
           * smoothstep(0.5, 0.75, g.n3(P / 200.0 + 17.3)) * %(rust_max)r;
col = lerp(col, %(rust)s, rust); rough = lerp(rough, 0.85, rust); metal = lerp(metal, 0.2, rust);
float grime = (1.0 - smoothstep(0.0, %(gv)r, V)) * lerp(0.5, 1.0, g.n3(P / 25.0 + 1.9)) * %(g_max)r;
col = lerp(col, %(grime)s, grime); rough = lerp(rough, 0.9, grime); metal = lerp(metal, 0.1, grime);
""" % dict(zinc=f3(ZINC), luma=STEEL_TEX_LUMA, sp=SPANGLE_CM, sp_t=SPANGLE_TINT, sp_r=SPANGLE_ROUGH,
           rough_add=STEEL_ROUGH_ADD, sv0=SCRAPE_V[0], sv1=SCRAPE_V[1], sv2=SCRAPE_V[2], sv3=SCRAPE_V[3],
           rust_max=RUST_MAX, rust=f3(RUST), gv=GRIME_V, g_max=GRIME_MAX, grime=f3(GRIME))

JERSEY_HLSL = PRELUDE + """
float3 col = %(conc)s * alb / %(luma)r;
float metal = 0.0;
rough += %(rough_add)r;
col *= lerp(0.85, 1.1, g.n3(P / 80.0));
float stain = smoothstep(0.55, 0.8, g.n3(P * float3(1.0 / 10.0, 1.0 / 10.0, 1.0 / 90.0) + 9.2))
            * smoothstep(%(st0)r, %(st1)r, V) * 0.6;
col *= 1.0 - 0.35 * stain;
rough += 0.05 * stain;
float dirt = (1.0 - smoothstep(%(dv0)r, %(dv1)r, V)) * lerp(0.55, 1.0, g.n3(P / 20.0 + 4.4)) * 0.75;
col = lerp(col, %(dirt)s, dirt); rough = lerp(rough, 0.95, dirt);
float scuff = smoothstep(0.70, 0.85, g.n3(P * float3(1.0 / 250.0, 1.0 / 250.0, 1.0 / 3.0) + 2.3))
            * smoothstep(0.45, 0.65, g.n3(P / 700.0 + 8.8))
            * smoothstep(%(sc0)r, %(sc1)r, V) * (1.0 - smoothstep(%(sc2)r, %(sc3)r, V));
col = lerp(col, 0.03, scuff * 0.85); rough = lerp(rough, 0.6, scuff);
""" % dict(conc=f3(CONCRETE), luma=CONC_TEX_LUMA, rough_add=CONC_ROUGH_ADD, st0=STAIN_V[0], st1=STAIN_V[1],
           dv0=DIRT_V[0], dv1=DIRT_V[1], dirt=f3(DIRT), sc0=SCUFF_V[0], sc1=SCUFF_V[1], sc2=SCUFF_V[2],
           sc3=SCUFF_V[3])

OUTPUTS = [(MP.MP_BASE_COLOR, F3, "return saturate(col);"), (MP.MP_METALLIC, F1, "return saturate(metal);"),
           (MP.MP_ROUGHNESS, F1, "return saturate(rough);"), (MP.MP_NORMAL, F3, "return nrm;")]

REFLECT_HLSL = """
float t = saturate((%(hi)r - asin(clamp(S.z, -1.0, 1.0)) * 57.29578) / %(span)r);
float night = t * t * (3.0 - 2.0 * t);
float facing = pow(saturate(abs(dot(normalize(N), normalize(C)))), %(pw)r);
float fade = 1.0 - smoothstep(%(f0)r, %(f1)r, D);
return %(col)s * (%(nits)r * night * facing * fade);
""" % dict(hi=NIGHT_ELEV_HI, span=NIGHT_ELEV_SPAN, pw=REFLECT_FACING_POW, f0=REFLECT_FADE_CM[0],
           f1=REFLECT_FADE_CM[1], col=f3(REFLECT), nits=REFLECT_NITS)


class G:
    def __init__(self, mat):
        self.m, self.y = mat, -1200

    def node(self, cls, x=-900, **props):
        self.y += 120
        e = MEL.create_material_expression(self.m, cls, x, self.y)
        for k, v in props.items():
            e.set_editor_property(k, v)
        return e

    @staticmethod
    def req(ok, what):
        if not ok:
            raise RuntimeError(f"could not connect {what} - pin name mismatch?")

    def wire(self, src, dst, dst_in):
        src, out = src if isinstance(src, tuple) else (src, "")
        self.req(MEL.connect_material_expressions(src, out, dst, dst_in), dst_in or type(dst).__name__)

    def pin(self, src, prop):
        self.req(MEL.connect_material_property(src, "", prop), str(prop))

    def binop(self, cls, a, b):
        n = self.node(cls)
        self.wire(a, n, "A")
        self.wire(b, n, "B")
        return n

    def frac(self, src):
        n = self.node(unreal.MaterialExpressionFrac)
        self.wire(src, n, "")
        return n

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def mask(self, src, **chans):
        n = self.node(unreal.MaterialExpressionComponentMask, **{k: chans.get(k, False) for k in "rgba"})
        self.wire(src, n, "")
        return n

    def custom(self, code, out_type, **inputs):
        c = self.node(unreal.MaterialExpressionCustom, x=-300, code=code, output_type=out_type)
        ins = []
        for name in inputs:
            i = unreal.CustomInput()
            i.set_editor_property("input_name", name)
            ins.append(i)
        c.set_editor_property("inputs", ins)
        for name, src in inputs.items():
            self.wire(src, c, name)
        return c


def open_material(path):
    if EAL.does_asset_exist(path):
        mat = unreal.load_asset(path)
        for e in list(MEL.get_material_expressions(mat)):
            MEL.delete_material_expression(mat, e)
        return mat
    d, name = path.rsplit("/", 1)
    return tools.create_asset(name, d, unreal.Material, unreal.MaterialFactoryNew())


def save(mat, path):
    MEL.recompile_material(mat)
    if not EAL.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {path}")


def surface_material(name, tex_id, tile_cm, hlsl):
    """World triplanar twin of MAT_DIR/name, all shading in HLSL."""
    twin_path, path = f"{MAT_DIR}/{name}", f"{MAT_DIR}/{name}_real"
    twin = unreal.load_asset(twin_path)
    if twin is None:
        raise RuntimeError(f"{twin_path} missing - run apply_guardrail_assets.py first")
    tex = {k: unreal.load_asset(f"{REAL_DIR}/{tex_id}/T_{tex_id}_{k}") for k in "ANR"}
    if None in tex.values():
        raise RuntimeError(f"{tex_id} textures missing under {REAL_DIR}")

    mat = open_material(path)
    g = G(mat)
    wp = g.node(unreal.MaterialExpressionWorldPosition)
    p = g.binop(unreal.MaterialExpressionMultiply,
                g.frac(g.binop(unreal.MaterialExpressionDivide, wp, g.const(WRAP_CM))), g.const(WRAP_CM))
    uvs = [g.binop(unreal.MaterialExpressionDivide, g.mask(p, **{a: True, b: True}), g.const(tile_cm))
           for a, b in (("g", "b"), ("r", "b"), ("r", "g"))]   # X-facing YZ, Y-facing XZ, Z-facing XY
    stype = {"A": unreal.MaterialSamplerType.SAMPLERTYPE_COLOR,
             "N": unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL,
             "R": unreal.MaterialSamplerType.SAMPLERTYPE_MASKS}
    ins = dict(P=p, N=g.node(unreal.MaterialExpressionVertexNormalWS),
               V=g.mask(g.node(unreal.MaterialExpressionTextureCoordinate), g=True))
    for k in "ANR":
        for axis, uv in zip("xyz", uvs):
            s = g.node(unreal.MaterialExpressionTextureSample, texture=tex[k], sampler_type=stype[k])
            g.req(any(MEL.connect_material_expressions(uv, "", s, pin) for pin in ("UVs", "Coordinates")),
                  "TextureSample UV")
            ins[f"{k}{axis}"] = (s, "R") if k == "R" else (s, "RGB")
    for prop, out_type, ret in OUTPUTS:
        g.pin(g.custom(hlsl + ret, out_type, **ins), prop)

    for prop in ("phys_material", "blend_mode", "shading_model", "two_sided") + USAGE_FLAGS:
        mat.set_editor_property(prop, twin.get_editor_property(prop))
    mat.set_editor_property("tangent_space_normal", False)
    save(mat, path)
    check_twin(twin_path, path)
    return unreal.load_asset(path)


def param_names(mat):
    names = set()
    for fn in (MEL.get_scalar_parameter_names, MEL.get_vector_parameter_names,
               MEL.get_texture_parameter_names, MEL.get_static_switch_parameter_names):
        names |= {str(n) for n in fn(mat)}
    return names


def check_twin(twin_path, real_path):
    twin, real = unreal.load_asset(twin_path), unreal.load_asset(real_path)
    problems = []
    if param_names(twin) - param_names(real):
        problems.append(f"missing params {sorted(param_names(twin) - param_names(real))}")
    for prop in ("phys_material", "blend_mode", "two_sided"):
        if twin.get_editor_property(prop) != real.get_editor_property(prop):
            problems.append(f"{prop} differs")
    for prop in USAGE_FLAGS:
        if twin.get_editor_property(prop) and not real.get_editor_property(prop):
            problems.append(f"{prop} missing")
    for prop in (MP.MP_BASE_COLOR, MP.MP_NORMAL, MP.MP_ROUGHNESS, MP.MP_METALLIC):
        if MEL.get_material_property_input_node(real, prop) is None:
            problems.append(f"{prop} unconnected")
    if problems:
        raise RuntimeError(f"{real_path} interface check FAILED: {problems}")
    log(f"{real_path}: interface OK (params {sorted(param_names(real))}, "
        f"ism={real.get_editor_property('used_with_instanced_static_meshes')}, "
        f"two_sided={real.get_editor_property('two_sided')}, phys={real.get_editor_property('phys_material')}, "
        f"{len(MEL.get_material_expressions(real))} nodes)")


def reflector_material():
    path = f"{REAL_DIR}/M_GuardrailReflector_real"
    mat = open_material(path)
    g = G(mat)
    sun = g.node(unreal.MaterialExpressionSkyAtmosphereLightDirection)
    sun.set_editor_property("light_index", 0)
    g.pin(g.node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0.8, 0.8, 0.78, 1.0)),
          MP.MP_BASE_COLOR)
    g.pin(g.const(0.3), MP.MP_ROUGHNESS)
    g.pin(g.custom(REFLECT_HLSL, F3, N=g.node(unreal.MaterialExpressionVertexNormalWS),
                   C=g.node(unreal.MaterialExpressionCameraVectorWS),
                   D=g.node(unreal.MaterialExpressionPixelDepth), S=sun), MP.MP_EMISSIVE_COLOR)
    mat.set_editor_property("two_sided", True)
    mat.set_editor_property("used_with_instanced_static_meshes", True)
    save(mat, path)
    if not unreal.load_asset(path).get_editor_property("used_with_instanced_static_meshes"):
        raise RuntimeError(f"{path}: used_with_instanced_static_meshes FALSE after save")
    log(f"{path}: built")
    return unreal.load_asset(path)


def post_mesh(slots):
    if not POST_OBJ.exists():
        raise RuntimeError(f"{POST_OBJ} missing - run pipeline/make_guardrail_post_mesh.py first")
    t = unreal.AssetImportTask()
    t.filename = str(POST_OBJ)
    t.destination_path = REAL_DIR
    t.destination_name = POST_OBJ.stem
    t.automated = True
    t.save = False
    t.replace_existing = True
    tools.import_asset_tasks([t])
    path = f"{REAL_DIR}/{POST_OBJ.stem}"
    mesh = unreal.load_asset(path)
    if mesh is None:
        raise RuntimeError(f"import failed: {path}")
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", False)
    mesh.set_editor_property("nanite_settings", ns)
    order = [l.split()[1] for l in POST_OBJ.read_text().splitlines() if l.startswith("usemtl ")]
    names = [str(s.get_editor_property("material_slot_name")) for s in mesh.get_editor_property("static_materials")]
    names = names if names and all(n in slots for n in names) else order[:len(names)]
    if names != ["steel", "reflector"]:
        raise RuntimeError(f"{path}: slots {names}, expected ['steel', 'reflector'] (C++ overrides slot 0)")
    for i, n in enumerate(names):
        mesh.set_material(i, slots[n])
    if not EAL.save_asset(path, only_if_is_dirty=False):
        raise RuntimeError(f"save failed (file in use?): {path}")
    b = mesh.get_bounding_box()
    log(f"{path}: tris={mesh.get_num_triangles(0)} slots={names} nanite=off "
        f"bounds min=({b.min.x:.1f},{b.min.y:.1f},{b.min.z:.1f}) max=({b.max.x:.1f},{b.max.y:.1f},{b.max.z:.1f})")


runpy.run_path(str(Path(__file__).resolve().parent / "import_ground_textures.py"),
               init_globals={"SRC_DIR": str(ROOT / "content_src" / "realistic" / "guardrails"),
                             "TEX_DIR": REAL_DIR})
wbeam = surface_material("M_Guardrail_WBeam", "Metal038", STEEL_TILE_CM, WBEAM_HLSL)
surface_material("M_Guardrail_Jersey", "Concrete035", CONC_TILE_CM, JERSEY_HLSL)
post_mesh({"steel": wbeam, "reflector": reflector_material()})
log("DONE")
