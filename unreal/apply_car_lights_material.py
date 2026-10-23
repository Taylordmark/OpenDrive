"""Car lamp glow (2026-10-09, Taylor: "do we even have lights like tail
lights headlights and do they work on braking"): rebuilds M_CarLights and
M_CarLights_Hi (first built by import_car_meshes.py - run this after it)
with an emissive driven by params AODDayNightController sets per car:
"HeadGlow", "TailGlow", "ReverseGlow", "IndicatorL", "IndicatorR" (emissive
nits, 0 = off; traffic indicators come from per-instance custom data 0/1).

All lenses share the one "lights" slot; the palette cell tells them apart
(make_car_meshes.py HEADLIGHT (0.93, 0.85, 0.60), TAILLIGHT (0.62, 0.05,
0.03), REVERSE (0.90, 0.92, 0.95): green splits tail from the rest, blue
splits reverse from head). Rally aux lamps are HEADLIGHT cells.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

CAR_DIR = "/Game/OpenDrive/Cars"
# name: roughness - mirrors import_car_meshes.py CATEGORY_MATERIALS(_HI)["lights"]
LIGHT_MATS = {"M_CarLights": 0.30, "M_CarLights_Hi": 0.08}

# LocalX gates by end of the car (+X = nose): head glow front only, reverse
# rear only, so no blend of palette cells can light the wrong end. Written for
# Taylor's "backup lights are flickering while driving forward" (2026-10-09)
# on a mip-blend theory that doesn't hold - T_CarPalette2 is TF_NEAREST with
# no mips (import_car_meshes.py) - and that flicker is still open.
# pipeline/check_lamp_mask.py mirrors the mask.
# AMBER (1.00, 0.45, 0.02) is the only lights cell with blue < 0.2 and green
# > 0.25 (make_car_meshes.py): it is never head or tail, and blinks by side -
# local +Y is the car's right. IndL/IndR = max("IndicatorL/R" param (player
# car, AODDayNightController), per-instance custom data 0/1 (traffic ISMs)).
LAMP_HLSL = """
float front = step(0.0, LocalX);
float ind = saturate((0.2 - Pal.b) * 10.0) * saturate((Pal.g - 0.25) * 10.0);
float head = saturate((Pal.g - 0.3) * 10.0) * front * (1.0 - ind);
float rev = saturate((Pal.b - 0.75) * 10.0) * (1.0 - front);
float3 e = lerp(float3(1.0, 0.03, 0.02) * Tail * (1.0 - ind), float3(1.0, 0.95, 0.85) * Head, head);
e += float3(1.0, 0.45, 0.02) * (LocalY < 0.0 ? IndL : IndR) * ind;
return lerp(e, float3(1.0, 1.0, 1.0) * Reverse, rev);
"""

MEL = unreal.MaterialEditingLibrary
palette = unreal.load_asset(f"{CAR_DIR}/T_CarPalette2")
if palette is None:
    raise RuntimeError("T_CarPalette2 missing - run import_car_meshes.py first")


def log(msg):
    unreal.log_warning(f"CARLIGHTS {msg}")


def req(ok, what):
    if not ok:
        raise RuntimeError(f"failed to connect {what}")


def const(mat, v, y, prop):
    c = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -500, y)
    c.set_editor_property("r", v)
    req(MEL.connect_material_property(c, "", prop), str(prop))


for name, roughness in LIGHT_MATS.items():
    path = f"{CAR_DIR}/{name}"
    mat = unreal.load_asset(path)
    if mat is None:   # the _Hi set only exists once import_car_meshes.py's Hi pass has run
        log(f"{name} missing - skipped (run import_car_meshes.py, then this)")
        continue
    for _e in list(MEL.get_material_expressions(mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
        MEL.delete_material_expression(mat, _e)

    ts = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -800, 0)
    ts.set_editor_property("texture", palette)
    req(MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR), "BaseColor")
    const(mat, roughness, 250, unreal.MaterialProperty.MP_ROUGHNESS)
    const(mat, 0.0, 400, unreal.MaterialProperty.MP_METALLIC)
    const(mat, 0.5, 550, unreal.MaterialProperty.MP_SPECULAR)

    lamp = MEL.create_material_expression(mat, unreal.MaterialExpressionCustom, -300, 800)
    lamp.set_editor_property("code", LAMP_HLSL)
    lamp.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    ins = []
    for n in ("Pal", "Head", "Tail", "Reverse", "LocalX", "LocalY", "IndL", "IndR"):
        i = unreal.CustomInput()
        i.set_editor_property("input_name", n)
        ins.append(i)
    lamp.set_editor_property("inputs", ins)
    req(MEL.connect_material_expressions(ts, "RGB", lamp, "Pal"), "palette -> lamp")
    wp = MEL.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -1100, 1250)
    to_local = MEL.create_material_expression(mat, unreal.MaterialExpressionTransformPosition, -950, 1250)
    to_local.set_editor_property("transform_source_type", unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_WORLD)
    to_local.set_editor_property("transform_type", unreal.MaterialPositionTransformSource.TRANSFORMPOSSOURCE_LOCAL)
    req(MEL.connect_material_expressions(wp, "", to_local, ""), "world pos -> local")
    lx = MEL.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -800, 1250)
    lx.set_editor_property("r", True)
    req(MEL.connect_material_expressions(to_local, "", lx, ""), "local -> X")
    req(MEL.connect_material_expressions(lx, "", lamp, "LocalX"), "LocalX -> lamp")
    ly = MEL.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -800, 1350)
    ly.set_editor_property("g", True)
    req(MEL.connect_material_expressions(to_local, "", ly, ""), "local -> Y")
    req(MEL.connect_material_expressions(ly, "", lamp, "LocalY"), "LocalY -> lamp")
    for y, side, idx in ((1450, "L", 0), (1600, "R", 1)):
        p = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -950, y)
        p.set_editor_property("parameter_name", f"Indicator{side}")
        p.set_editor_property("default_value", 0.0)
        cd = MEL.create_material_expression(mat, unreal.MaterialExpressionPerInstanceCustomData, -950, y + 70)
        cd.set_editor_property("data_index", idx)
        mx = MEL.create_material_expression(mat, unreal.MaterialExpressionMax, -800, y)
        req(MEL.connect_material_expressions(p, "", mx, "A"), f"Indicator{side} -> max")
        req(MEL.connect_material_expressions(cd, "", mx, "B"), f"custom data {idx} -> max")
        req(MEL.connect_material_expressions(mx, "", lamp, f"Ind{side}"), f"Ind{side} -> lamp")
    for y, n in ((900, "Head"), (1000, "Tail"), (1100, "Reverse")):
        p = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -800, y)
        p.set_editor_property("parameter_name", f"{n}Glow")
        p.set_editor_property("default_value", 0.0)
        req(MEL.connect_material_expressions(p, "", lamp, n), f"{n}Glow -> lamp")
    req(MEL.connect_material_property(lamp, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR), "Emissive")

    mat.set_editor_property("two_sided", True)        # see import_car_meshes.py: mirrored OBJ winding
    mat.set_editor_property("used_with_nanite", True)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)

    names = [str(p) for p in MEL.get_scalar_parameter_names(unreal.load_asset(path))]
    for want in ("HeadGlow", "TailGlow", "ReverseGlow", "IndicatorL", "IndicatorR"):
        if want not in names:
            raise RuntimeError(f"{name}: {want} missing after save")
    log(f"rebuilt {name} with lamp emissive (params {names})")

# which slot the lamps live in, per mesh (the C++ finds it by base material)
for mesh_name in ("SM_Car_Muscle", "SM_Car_Pickup", "SM_Car_Rally_high"):
    mesh = unreal.load_asset(f"{CAR_DIR}/{mesh_name}")
    if mesh:
        slots = [(str(s.get_editor_property("material_slot_name")),
                  s.get_editor_property("material_interface").get_name() if s.get_editor_property("material_interface") else None)
                 for s in mesh.get_editor_property("static_materials")]
        log(f"{mesh_name} slots {slots}")
