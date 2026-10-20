"""Realistic car grounding (PLAN-VISUALS Track B note): builds
/Game/OpenDrive/Realistic/CarShadow/M_CarContactShadow, the deferred decal
UODContactShadow (ODContactShadow.cpp) lays under the car body and tyres.

  p       = |UV * 2 - 1|                    decal face, -1..1 from centre
  d       = |max(p - Inner, 0)| / (1 - Inner)   Inner 0 = ellipse, 0.55 = rounded rect
  Opacity = (1 - saturate(d))^2 * Strength
  Inner = DecalColor.R, Strength = DecalColor.A (set per blob by C++, no MID)

BaseColor black, Roughness 1, Specular 0, Metallic 0 under that opacity:
albedo and reflections both darken, so it reads under any light (overcast,
night, headlights), not only the sun. Pure generated graph, no textures.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Realistic/CarShadow"
MAT_NAME = "M_CarContactShadow"

MEL = unreal.MaterialEditingLibrary
EAL = unreal.EditorAssetLibrary


def log(msg):
    unreal.log_warning(f"CARSHADOWMAT {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"{MAT_NAME}: failed to connect {what}")


EAL.make_directory(MAT_DIR)
mat_path = f"{MAT_DIR}/{MAT_NAME}"
if EAL.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
        MEL.delete_material_expression(mat, _e)
else:
    mat = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
mat.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)

_x = [-1600]


def node(cls, **props):
    _x[0] += 150
    n = MEL.create_material_expression(mat, cls, _x[0], 0)
    for k, v in props.items():
        n.set_editor_property(k, v)
    return n


def link(a, b, pin, what):
    req(MEL.connect_material_expressions(a, "", b, pin), what)


def mask(src, chan):
    # Read DecalColor's own named scalar pin ("R"/"A") and mask its R: the
    # default pin resolves as RGB float3 on SM6, so an alpha mask off it fails
    # "Not enough components ... for component mask 0001" (same fix as
    # apply_road_material.py's VertexColor alpha).
    m = node(unreal.MaterialExpressionComponentMask, r=True, g=False, b=False, a=False)
    req(MEL.connect_material_expressions(src, chan.upper(), m, ""), f"mask.{chan}")
    return m


uv = node(unreal.MaterialExpressionTextureCoordinate)
uv2 = node(unreal.MaterialExpressionMultiply, const_b=2.0)
link(uv, uv2, "A", "uv*2")
centred = node(unreal.MaterialExpressionSubtract, const_b=1.0)
link(uv2, centred, "A", "uv*2-1")
p = node(unreal.MaterialExpressionAbs)
link(centred, p, "", "abs")

dcol = node(unreal.MaterialExpressionDecalColor)
inner = mask(dcol, "r")
strength = mask(dcol, "a")

pin = node(unreal.MaterialExpressionSubtract)
link(p, pin, "A", "p-inner.A")
link(inner, pin, "B", "p-inner.B")
q = node(unreal.MaterialExpressionMax, const_b=0.0)
link(pin, q, "A", "max0")
span = node(unreal.MaterialExpressionOneMinus)
link(inner, span, "", "1-inner")
qn = node(unreal.MaterialExpressionDivide)
link(q, qn, "A", "q/span.A")
link(span, qn, "B", "q/span.B")
dd = node(unreal.MaterialExpressionDotProduct)
link(qn, dd, "A", "dot.A")
link(qn, dd, "B", "dot.B")
d = node(unreal.MaterialExpressionSquareRoot)
link(dd, d, "", "sqrt")
sat = node(unreal.MaterialExpressionSaturate)
link(d, sat, "", "saturate")
s = node(unreal.MaterialExpressionOneMinus)
link(sat, s, "", "1-d")
s2 = node(unreal.MaterialExpressionMultiply)
link(s, s2, "A", "s*s.A")
link(s, s2, "B", "s*s.B")
op = node(unreal.MaterialExpressionMultiply)
link(s2, op, "A", "opacity.A")
link(strength, op, "B", "opacity.B")

black = node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0.0, 0.0, 0.0, 1.0))
one = node(unreal.MaterialExpressionConstant, r=1.0)
zero = node(unreal.MaterialExpressionConstant, r=0.0)
req(MEL.connect_material_property(black, "", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
req(MEL.connect_material_property(one, "", unreal.MaterialProperty.MP_ROUGHNESS), "Roughness")
req(MEL.connect_material_property(zero, "", unreal.MaterialProperty.MP_SPECULAR), "Specular")
req(MEL.connect_material_property(zero, "", unreal.MaterialProperty.MP_METALLIC), "Metallic")
req(MEL.connect_material_property(op, "", unreal.MaterialProperty.MP_OPACITY), "Opacity")

MEL.recompile_material(mat)
if not EAL.save_asset(mat_path, only_if_is_dirty=False):
    raise RuntimeError(f"save failed (file in use?): {mat_path}")

re_ = unreal.load_asset(mat_path)
for prop in ("MP_BASE_COLOR", "MP_ROUGHNESS", "MP_SPECULAR", "MP_METALLIC", "MP_OPACITY"):
    if MEL.get_material_property_input_node(re_, getattr(unreal.MaterialProperty, prop)) is None:
        raise RuntimeError(f"{MAT_NAME}: {prop} unconnected")
n_nodes = len(MEL.get_material_expressions(re_))
if n_nodes != 20:
    raise RuntimeError(f"{MAT_NAME}: expected 20 nodes after save, got {n_nodes}")
log(f"built {mat_path} (domain {re_.get_editor_property('material_domain')}, {n_nodes} nodes)")
log("DONE")
