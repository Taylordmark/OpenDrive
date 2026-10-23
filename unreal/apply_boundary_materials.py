"""Area picker B (2026-10-21): the two materials of a drawn area's edge.

M_ODUnsynced - masked, default-lit ground the terrain manager puts on
material slot 1: triangles outside the drawn outline, horizon triangles
outside it or over cells the runner hasn't baked, and the self-destroying
"sync-in" copy of a tile that was Pending. Cool dark ground with a glowing
25 m world grid. CustomPrimitiveData[0] = SyncStart (game seconds): <= 0 means
always shown; otherwise the surface dissolves away in 3 m blocks over 2 s
(AODTerrainManager::SpawnSyncIn sets it).

M_ODCurtain - translucent, two-sided, default-lit (NOT Unlit - see
apply_snow_particle_material.py's docstring) clear glass for AODBoundaryEffect's
pyramid wall: refracts the terrain, white hint from fresnel + edge glow, shown
only within 8..22 m of the car, tips breathe (WPO) while "Breath" > 0.

Stylised on purpose, and only at the edge: everything inside the outline keeps
the realism bar. Same build shape as apply_tornado_material.py.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()

GRID_CM = 2500.0             # grid line every 25 m
DISSOLVE_BLOCK_CM = 300.0    # sync-in noise cell
DISSOLVE_SEC = 2.0           # AODTerrainManager destroys the copy at 2.2 s


def log(msg):
    unreal.log_warning(f"BOUNDARYMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"boundary materials: failed to connect {what}")


def fresh(name):
    path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mat = unreal.load_asset(path)
        for e in list(MEL.get_material_expressions(mat)):   # not delete_all_material_expressions (UE 5.8)
            MEL.delete_material_expression(mat, e)
        return mat, path
    return tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew()), path


def const3(mat, rgb, x, y):
    e = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, x, y)
    e.set_editor_property("constant", unreal.LinearColor(*rgb, 1.0))
    return e


def custom(mat, code, inputs, x, y, out=unreal.CustomMaterialOutputType.CMOT_FLOAT1):
    e = MEL.create_material_expression(mat, unreal.MaterialExpressionCustom, x, y)
    e.set_editor_property("code", code)
    e.set_editor_property("output_type", out)
    ins = []
    for name in inputs:
        ci = unreal.CustomInput()
        ci.set_editor_property("input_name", name)
        ins.append(ci)
    e.set_editor_property("inputs", ins)
    return e


def finish(mat, path, props):
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
    reloaded = unreal.load_asset(path)
    for prop in props:
        if MEL.get_material_property_input_node(reloaded, prop) is None:
            raise RuntimeError(f"{path}: {prop} unconnected after save")
    log(f"built + saved {path}")


# ---- M_ODUnsynced ----------------------------------------------------------
mat, path = fresh("M_ODUnsynced")
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)

wp = MEL.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -900, 0)
t = MEL.create_material_expression(mat, unreal.MaterialExpressionTime, -900, 250)
start = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -900, 400)
start.set_editor_property("parameter_name", "SyncStart")
start.set_editor_property("default_value", 0.0)
start.set_editor_property("use_custom_primitive_data", True)
start.set_editor_property("primitive_data_index", 0)

grid = custom(mat, f"float2 g = abs(frac(WP.xy / {GRID_CM}) - 0.5); return step(0.485, max(g.x, g.y));",
              ["WP"], -600, 0)
req(MEL.connect_material_expressions(wp, "", grid, "WP"), "grid WP")

base = MEL.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, -300, -150)
req(MEL.connect_material_expressions(const3(mat, (0.06, 0.07, 0.09), -600, -250), "", base, "A"), "base A")
req(MEL.connect_material_expressions(const3(mat, (0.25, 0.55, 0.75), -600, -150), "", base, "B"), "base B")
req(MEL.connect_material_expressions(grid, "", base, "Alpha"), "base Alpha")

glow = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -300, 50)
req(MEL.connect_material_expressions(grid, "", glow, "A"), "glow A")
req(MEL.connect_material_expressions(const3(mat, (0.3, 0.9, 1.6), -600, 100), "", glow, "B"), "glow B")

rough = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 150)
rough.set_editor_property("r", 0.9)

dissolve = custom(mat,
                  "if (Start <= 0.0) return 1.0;\n"
                  f"float n = frac(sin(dot(floor(WP.xy / {DISSOLVE_BLOCK_CM}), float2(12.9898, 78.233))) * 43758.5453);\n"
                  f"return step((T - Start) / {DISSOLVE_SEC}, n);",
                  ["WP", "T", "Start"], -600, 300)
req(MEL.connect_material_expressions(wp, "", dissolve, "WP"), "dissolve WP")
req(MEL.connect_material_expressions(t, "", dissolve, "T"), "dissolve T")
req(MEL.connect_material_expressions(start, "", dissolve, "Start"), "dissolve Start")

req(MEL.connect_material_property(base, "", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(glow, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")
req(MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS), "Roughness")
req(MEL.connect_material_property(dissolve, "", unreal.MaterialProperty.MP_OPACITY_MASK), "OpacityMask")
finish(mat, path, (unreal.MaterialProperty.MP_BASE_COLOR, unreal.MaterialProperty.MP_EMISSIVE_COLOR,
                   unreal.MaterialProperty.MP_OPACITY_MASK))

# ---- M_ODCurtain -----------------------------------------------------------
# AODBoundaryEffect's glass pyramids. Mesh data (see Rebuild): UV0 barycentric
# per facet (edge glow), UV1 = (Rise, Phase), vertex colour rgb = inward dir
# (*0.5+0.5), tip alpha = breath amplitude / 100 cm (0 on base verts).
# "Center" = the car in the wall's local space; "Breath" 0..1 (slow = 1).
mat, path = fresh("M_ODCurtain")
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
mat.set_editor_property("two_sided", True)
for prop, val in (("tangent_space_normal", False),
                  ("translucency_lighting_mode", "TLM_SURFACE"),
                  ("refraction_method", "RM_INDEX_OF_REFRACTION")):
    try:
        if isinstance(val, str):
            enum = unreal.TranslucencyLightingMode if prop.startswith("translucency") else unreal.RefractionMode
            val = getattr(enum, val)
        mat.set_editor_property(prop, val)
    except Exception as ex:
        log(f"M_ODCurtain: {prop} not set ({ex})")

lp = MEL.create_material_expression(mat, unreal.MaterialExpressionLocalPosition, -1200, 0)
lp_wpo = MEL.create_material_expression(mat, unreal.MaterialExpressionLocalPosition, -1200, 600)
try:
    lp_wpo.set_editor_property("included_offsets", unreal.PositionIncludedOffsets.EXCLUDE_OFFSETS)
except Exception as ex:
    log(f"LocalPosition offsets not set ({ex})")
cam = MEL.create_material_expression(mat, unreal.MaterialExpressionCameraVectorWS, -1200, 100)
uv0 = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -1200, 200)
uv1 = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -1200, 300)
uv1.set_editor_property("coordinate_index", 1)
vc = MEL.create_material_expression(mat, unreal.MaterialExpressionVertexColor, -1200, 700)
t = MEL.create_material_expression(mat, unreal.MaterialExpressionTime, -1200, 800)
center = MEL.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -1200, 400)
center.set_editor_property("parameter_name", "Center")
breath = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -1200, 900)
breath.set_editor_property("parameter_name", "Breath")
breath.set_editor_property("default_value", 1.0)

NEAR = "float d = length(P.xy - C.xy); float n = saturate(1.0 - (d - 800.0) / 1400.0); "
near = custom(mat, NEAR + "return n * n;", ["P", "C"], -900, 0)
req(MEL.connect_material_expressions(lp, "", near, "P"), "near P")
req(MEL.connect_material_expressions(center, "", near, "C"), "near C")

nrm = custom(mat, "float3 n = normalize(cross(ddy(P), ddx(P))); return dot(n, V) < 0 ? -n : n;",
             ["P", "V"], -900, 150, unreal.CustomMaterialOutputType.CMOT_FLOAT3)
req(MEL.connect_material_expressions(lp, "", nrm, "P"), "normal P")
req(MEL.connect_material_expressions(cam, "", nrm, "V"), "normal V")

EDGE = "float e = min(min(B.x, B.y), 1.0 - B.x - B.y); float edge = 1.0 - smoothstep(0.0, 0.05, e); "
opacity = custom(mat, EDGE + "float fres = pow(1.0 - saturate(abs(dot(N, V))), 3.0);\n"
                 "return Near * I.x * saturate(0.05 + 0.35 * fres + 0.3 * edge);",
                 ["B", "N", "V", "Near", "I"], -600, 0)
for src, pin in ((uv0, "B"), (nrm, "N"), (cam, "V"), (near, "Near"), (uv1, "I")):
    req(MEL.connect_material_expressions(src, "", opacity, pin), f"opacity {pin}")

emis = custom(mat, EDGE + "return (Near * I.x * (0.04 + 0.25 * edge)).xxx;",
              ["B", "Near", "I"], -600, 200, unreal.CustomMaterialOutputType.CMOT_FLOAT3)
for src, pin in ((uv0, "B"), (near, "Near"), (uv1, "I")):
    req(MEL.connect_material_expressions(src, "", emis, pin), f"emissive {pin}")

refr = custom(mat, "return lerp(1.0, 1.2, Near * I.x);", ["Near", "I"], -600, 350)
req(MEL.connect_material_expressions(near, "", refr, "Near"), "refraction Near")
req(MEL.connect_material_expressions(uv1, "", refr, "I"), "refraction I")

wpo = custom(mat, NEAR + "n *= n;\n"
             "float pulse = 0.5 + 0.5 * sin(6.2831853 * (T * 0.5 + I.y));\n"
             "return (VC.rgb * 2.0 - 1.0) * (VC.a * 100.0) * (pulse - 0.5) * Br * n;",
             ["P", "C", "VC", "T", "I", "Br"], -600, 600, unreal.CustomMaterialOutputType.CMOT_FLOAT3)
for src, pin in ((lp_wpo, "P"), (center, "C"), (vc, "VC"), (t, "T"), (uv1, "I"), (breath, "Br")):
    req(MEL.connect_material_expressions(src, "", wpo, pin), f"wpo {pin}")

rough = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, -50)
rough.set_editor_property("r", 0.05)
spec = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 0)
spec.set_editor_property("r", 0.5)

P = unreal.MaterialProperty
req(MEL.connect_material_property(const3(mat, (0.9, 0.95, 1.0), -300, -150), "", P.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(rough, "", P.MP_ROUGHNESS), "Roughness")
req(MEL.connect_material_property(spec, "", P.MP_SPECULAR), "Specular")
req(MEL.connect_material_property(nrm, "", P.MP_NORMAL), "Normal")
req(MEL.connect_material_property(emis, "", P.MP_EMISSIVE_COLOR), "Emissive")
req(MEL.connect_material_property(opacity, "", P.MP_OPACITY), "Opacity")
req(MEL.connect_material_property(refr, "", P.MP_REFRACTION), "Refraction")
req(MEL.connect_material_property(wpo, "", P.MP_WORLD_POSITION_OFFSET), "WPO")
finish(mat, path, (P.MP_BASE_COLOR, P.MP_OPACITY, P.MP_WORLD_POSITION_OFFSET))
