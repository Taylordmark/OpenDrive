"""Builds the guardrail feature's three data-only assets: M_Guardrail_WBeam
(galvanized-gray metal, simple constant-color PBR - no texture set exists
for this yet, same "ship a flat color first" approach apply_arc1_headless.py
took for the road material before art direction caught up), M_Guardrail_
Jersey (concrete gray), and PM_GuardrailSlick (a LOW-FRICTION physical
material - Taylor's design call: a car that clips a guardrail should
scrape/slide along it, "Crew 2 deflect" feel, not catch and stop dead - see
AODGuardrailManager::FinalizeTile's SetPhysMaterialOverride call).

CRITICAL gotcha (art-direction §7.15, already hit on this project's tree-
bark and rock materials): M_Guardrail_WBeam is used on AODGuardrailManager's
post HISM as well as the rail ribbon mesh - a material used on an HISM
without used_with_instanced_static_meshes=True silently falls back to the
flat grey engine default at runtime, with no error anywhere. Set below.

The rail ribbon is a runtime-extruded thin cross-section seen from both
sides as the car passes it at speed - two_sided=True on both materials so a
back-face view (e.g. looking back at a rail just passed) doesn't read as
invisible/black.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
PHYS_DIR = "/Game/OpenDrive/Physics"

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"GUARDRAIL {msg}")


def make_flat_color_material(name, color_rgb, roughness, metallic, two_sided=True,
                              used_with_hism=False):
    mat_path = f"{MAT_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
        mat = unreal.load_asset(mat_path)
        MEL.delete_all_material_expressions(mat)
    else:
        mat = tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

    mat.set_editor_property("two_sided", two_sided)
    if used_with_hism:
        mat.set_editor_property("used_with_instanced_static_meshes", True)

    color_n = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -400, -150)
    color_n.set_editor_property("constant", unreal.LinearColor(*color_rgb, 1.0))
    rough_n = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -400, 50)
    rough_n.set_editor_property("r", roughness)
    metal_n = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -400, 150)
    metal_n.set_editor_property("r", metallic)

    ok_bc = MEL.connect_material_property(color_n, "", unreal.MaterialProperty.MP_BASE_COLOR)
    ok_r = MEL.connect_material_property(rough_n, "", unreal.MaterialProperty.MP_ROUGHNESS)
    ok_m = MEL.connect_material_property(metal_n, "", unreal.MaterialProperty.MP_METALLIC)
    if not (ok_bc and ok_r and ok_m):
        raise RuntimeError(f"{name}: could not connect BaseColor/Roughness/Metallic - pin name mismatch?")

    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

    reloaded = unreal.load_asset(mat_path)
    if used_with_hism and not reloaded.get_editor_property("used_with_instanced_static_meshes"):
        raise RuntimeError(f"{name}: used_with_instanced_static_meshes FALSE after save (FAIL) - "
                            f"would silently render grey on the post HISM")
    n = MEL.get_material_property_input_node(reloaded, unreal.MaterialProperty.MP_BASE_COLOR)
    if n is None:
        raise RuntimeError(f"{name}: MP_BASE_COLOR unconnected after save (FAIL)")
    log(f"built + saved {name} (color={color_rgb}, roughness={roughness}, metallic={metallic}, "
        f"two_sided={two_sided}, used_with_hism={used_with_hism})")
    return mat


# Galvanized-gray metal W-beam - moderately metallic, semi-rough (weathered
# galvanizing, not a mirror finish). Used on the post HISM too.
make_flat_color_material("M_Guardrail_WBeam", (0.52, 0.53, 0.55), 0.45, 0.75, used_with_hism=True)

# Concrete gray Jersey barrier - fully dielectric, rough.
make_flat_color_material("M_Guardrail_Jersey", (0.62, 0.60, 0.57), 0.85, 0.0, used_with_hism=False)


# ------------------------------------------------------------- physics material
pm_path = f"{PHYS_DIR}/PM_GuardrailSlick"
if unreal.EditorAssetLibrary.does_asset_exist(pm_path):
    pm = unreal.load_asset(pm_path)
else:
    pm = tools.create_asset("PM_GuardrailSlick", PHYS_DIR, unreal.PhysicalMaterial,
                             unreal.PhysicalMaterialFactoryNew())
# Low friction (vs PM_Asphalt=0.9, PM_Dirt=0.7, PM_Grass=0.55) - low enough
# that a car sliding along the rail keeps most of its forward speed instead
# of catching and yawing/stopping. Not zero - some drag still reads as a
# real scrape, not a frictionless ice rail.
pm.set_editor_property("friction", 0.12)
try:
    # MIN so the LOWER of the rail's/car's own friction always wins the
    # contact (a slick rail should stay slick regardless of what the car's
    # own tire/body physmat says) - best-effort, not load-bearing: if the
    # enum name doesn't match this UE version, the plain low friction value
    # above still does most of the "scrape, don't catch" work on its own.
    pm.set_editor_property("friction_combine_mode", unreal.FrictionCombineMode.MIN)
except Exception as exc:
    log(f"WARNING: could not set friction_combine_mode ({exc}) - low friction alone still applies")
unreal.EditorAssetLibrary.save_loaded_asset(pm)
log(f"built + saved PM_GuardrailSlick (friction=0.12)")

log("DONE")
