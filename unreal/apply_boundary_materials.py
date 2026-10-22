"""Area picker B (2026-10-21): the two materials of a drawn area's edge.

M_ODUnsynced - masked, default-lit ground the terrain manager puts on
material slot 1: triangles outside the drawn outline, horizon triangles
outside it or over cells the runner hasn't baked, and the self-destroying
"sync-in" copy of a tile that was Pending. Cool dark ground with a glowing
25 m world grid. CustomPrimitiveData[0] = SyncStart (game seconds): <= 0 means
always shown; otherwise the surface dissolves away in 3 m blocks over 2 s
(AODTerrainManager::SpawnSyncIn sets it).

M_ODCurtain - translucent, two-sided, default-lit (NOT Unlit - see
apply_snow_particle_material.py's docstring) ribbon AODBoundaryEffect stands
on the outline: slow bands sliding along it, fading out upward.

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
mat, path = fresh("M_ODCurtain")
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
mat.set_editor_property("two_sided", True)

uv = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -900, 0)
t = MEL.create_material_expression(mat, unreal.MaterialExpressionTime, -900, 150)
# UV.x = 1 per 100 m along the outline, UV.y = 0 at the foot (50 m under the
# ground) to 1 at the top (150 m up) - fades out upward, gone at the foot's tip
opacity = custom(mat,
                 "float s = 0.5 + 0.5 * sin(UV.x * 30.0 - T * 1.5 + UV.y * 8.0);\n"
                 "return saturate(1.0 - UV.y) * (0.15 + 0.3 * s);",
                 ["UV", "T"], -600, 0)
req(MEL.connect_material_expressions(uv, "", opacity, "UV"), "curtain UV")
req(MEL.connect_material_expressions(t, "", opacity, "T"), "curtain T")

req(MEL.connect_material_property(const3(mat, (0.4, 0.6, 0.8), -400, -200), "", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(const3(mat, (0.15, 0.4, 0.7), -400, -100), "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")
req(MEL.connect_material_property(opacity, "", unreal.MaterialProperty.MP_OPACITY), "Opacity")
finish(mat, path, (unreal.MaterialProperty.MP_BASE_COLOR, unreal.MaterialProperty.MP_EMISSIVE_COLOR,
                   unreal.MaterialProperty.MP_OPACITY))
