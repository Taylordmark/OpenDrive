"""Headless: import the five replica-style procedural car meshes
(pipeline/make_car_meshes.py: Muscle/JDM/Super/Pickup/Rally) plus the shared
wheel mesh into /Game/OpenDrive/Cars/, build seven shared per-CATEGORY
materials off the shared palette texture, assign them to each mesh's
matching OBJ-derived material slot, and author five new UODVehicleDef data
assets - same make_def() pattern as unreal/apply_vehicle_defs.py used for
the GT car.

Fully ADDITIVE (unchanged since 2026-08-27): DA_Vehicle_Coupe / DA_Vehicle_GT
and their meshes in /Game/OpenDrive/Car (singular - the older folder) are
untouched. This writes into /Game/OpenDrive/Cars (plural) instead. The
2026-08-27..09-01 Sedan/Coupe S/Sport/Proto set this replaces (2026-09-03
Phase 4a rescope: same asset folder, all-new car identities - see
pipeline/make_car_meshes.py's module docstring) is fully superseded here;
make_def() below deletes-and-recreates (or updates in place) so re-running
this script after re-running the generator is idempotent. The stale
DA_Vehicle_Sedan / DA_Vehicle_CoupeS / DA_Vehicle_Sport / DA_Vehicle_Proto
assets and their SM_Car_Sedan/Coupe/Sport/Proto meshes from the previous
pass are NOT deleted by this script (Python asset deletion of possibly-
still-referenced assets is exactly the flaky operation make_def() already
works around below) - a later UE-side pass should manually remove them
once the new five have been drive-tested and nothing still references them.

2026-09-03 material-slot split: make_car_meshes.py now writes each car OBJ
with real multi-material-slot structure - triangles are grouped into up to
seven `usemtl` groups named body/plastic/glass/chrome/tire/rim/lights (see
that file's CATEGORY_OF/CATEGORY_ORDER). Unreal's OBJ importer creates one
material slot per usemtl name it encounters, in the FIRST-SEEN order in the
file, with material_slot_name set to that usemtl name - so after import we
can look up each mesh's slots BY NAME and point each at the matching one of
seven persistent UE materials (M_CarBody/M_CarGlass/M_CarChrome/
M_CarPlastic/M_CarTire/M_CarRim/M_CarLights), each with its own metallic/
roughness/opacity, all still sampling the ONE shared T_CarPalette2 texture
(no new texture work - only the shading params differ per slot). This
replaces the earlier single-material M_Car2 approach. NOT SMOKE-TESTED IN
UE by this pass (pipeline agent constraint: no UE builds/launches) - flagged
for a later agent to verify get_editor_property("static_materials") /
material_slot_name actually round-trips through the OBJ importer as
expected in UE 5.8 before relying on this; if slot-name lookup comes back
empty, fall back to assigning by usemtl order (CATEGORY_ORDER filtered to
categories present, same order make_car_meshes.py wrote them in).

Run make_car_meshes.py first (writes data/cars/procedural/*), then:

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC = Path(__file__).resolve().parents[1] / "data" / "cars" / "procedural"
CAR_DIR = "/Game/OpenDrive/Cars"
tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"CARMESH {msg}")


# ------------------------------------------------------------------ import
BASE_MESH_NAMES = ("SM_Car_Muscle", "SM_Car_JDM", "SM_Car_Super", "SM_Car_Pickup",
                   "SM_Car_Rally", "SM_Car_Concept", "SM_Car_LMP", "SM_CarWheel")
# Graphics menu "Asset Detail" variants (make_car_meshes.py TIERS). The defs
# below reference the Standard meshes; AODVehiclePawn swaps in <name>_high/
# _ultra at runtime (falling back to Standard if a variant is missing).
TIER_SUFFIXES = ("", "_high", "_ultra")
MESH_NAMES = tuple(n + sfx for sfx in TIER_SUFFIXES for n in BASE_MESH_NAMES)
tasks = []
for name in MESH_NAMES:
    t = unreal.AssetImportTask()
    t.filename = str(SRC / "obj" / f"{name}.obj")
    t.destination_path = CAR_DIR
    t.automated = True
    t.save = False
    t.replace_existing = True
    tasks.append(t)

tex_task = unreal.AssetImportTask()
tex_task.filename = str(SRC / "textures" / "T_CarPalette2.png")
tex_task.destination_path = CAR_DIR
tex_task.automated = True
tex_task.save = False
tex_task.replace_existing = True
tasks.append(tex_task)

tools.import_asset_tasks(tasks)

meshes = {n: unreal.load_asset(f"{CAR_DIR}/{n}") for n in MESH_NAMES}
palette = unreal.load_asset(f"{CAR_DIR}/T_CarPalette2")
missing = [n for n, a in meshes.items() if a is None]
if missing or palette is None:
    raise RuntimeError(
        f"car asset import failed - missing meshes {missing}, "
        f"palette {'ok' if palette else 'MISSING'} - run make_car_meshes.py first")
palette.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
palette.set_editor_property(
    "mip_gen_settings", unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
log(f"imported {len(meshes)} meshes + T_CarPalette2")

# Nanite is auto-enabled on import by default in UE 5.8, but every chassis
# has a "glass" slot that must be genuinely translucent (see M_CarGlass
# below) and Nanite does not support translucent materials on any slot of a
# Nanite mesh - the observed symptom (seen live in a first pass) was
# "Material .../M_CarGlass missing usage flag Nanite! Default Material will
# be used in game", i.e. the whole glass slot silently falls back to the
# flat grey engine default. These are all low-poly procedural meshes (a few
# hundred tris) with zero need for Nanite anyway, so just turn it off on
# all eight rather than juggle a mixed Nanite/non-Nanite material set.
for name, mesh in meshes.items():
    ns = mesh.get_editor_property("nanite_settings")
    ns.set_editor_property("enabled", False)
    mesh.set_editor_property("nanite_settings", ns)
log(f"disabled Nanite on all {len(meshes)} car meshes (required for the translucent glass slot)")

# box collision on all seven chassis (SM_CarWheel has no collision - matches
# WheelVisuals' NoCollision setup in AODVehiclePawn's constructor)
CHASSIS_NAMES = ("SM_Car_Muscle", "SM_Car_JDM", "SM_Car_Super", "SM_Car_Pickup",
                  "SM_Car_Rally", "SM_Car_Concept", "SM_Car_LMP")
for name in (n + sfx for sfx in TIER_SUFFIXES for n in CHASSIS_NAMES):
    mesh = meshes[name]
    unreal.EditorStaticMeshLibrary.remove_collisions(mesh)
    n_col = unreal.EditorStaticMeshLibrary.add_simple_collisions(
        mesh, unreal.ScriptingCollisionShapeType.BOX)
    log(f"{name}: box collision ({n_col} prim)")

# ------------------------------------------------------- seven category materials
# One material per CATEGORY_ORDER entry in make_car_meshes.py, all sampling
# the same shared palette texture (RGB->BaseColor, same trick as before),
# differing only in roughness/metallic/opacity so each material category
# actually shades like the real-world thing it represents. Rebuilt in place
# each run (delete_all_material_expressions then recreate), same pattern
# and same "MEL.get_used_textures reports [] in a commandlet, don't use it
# to verify" caveat as the old single-material script.
#
# 2026-09-03 Crew-2-style quality pass: body pushed to a proper clear-coat
# look via metallic~0.9/roughness~0.15 (the plain ClearCoat shading model
# needs extra Clear Coat / Clear Coat Roughness pins this script can't
# verify bind offline, so this is the documented fallback from next
# steps.md); glass made genuinely translucent (BLEND_TRANSLUCENT + a
# constant opacity, not just a low-roughness opaque tile) - PALETTE's glass
# cell (see make_car_meshes.py) is already a cool blue-grey tint, so no
# extra tint node is needed, just let it show through; chrome tightened to
# roughness 0.05 (was 0.12); tire kept near-black (already true from the
# palette cell) at roughness 0.9.
#
# 2026-09-03 polish fix: opacity 0.35 let the camera see straight through
# the glass into the hollow chassis shell from behind (ScreenShot00245.png -
# "toy" read). Raised to 0.80 (dark privacy-glass look) - PALETTE's glass
# cell is already a fairly dark blue-grey tint (see make_car_meshes.py's
# GLASS palette comment) so no separate tint node was needed, just letting
# less light through it. Paired with make_car_meshes.py's new cabin-blocker
# panel so even the residual ~20% transmission doesn't reveal an empty
# interior at any angle.
CATEGORY_MATERIALS = {
    # category: (asset_name, roughness, metallic, two_sided, opacity_or_None)
    "body":    ("M_CarBody", 0.15, 0.90, True, None),
    "plastic": ("M_CarPlastic", 0.75, 0.0, True, None),
    "glass":   ("M_CarGlass", 0.06, 0.05, True, 0.80),
    "chrome":  ("M_CarChrome", 0.05, 1.0, True, None),
    "tire":    ("M_CarTire", 0.90, 0.0, True, None),
    "rim":     ("M_CarRim", 0.25, 0.85, True, None),
    "lights":  ("M_CarLights", 0.30, 0.0, True, None),   # lamp glow: apply_car_lights_material.py, run after
}

# 2026-09-25 "Asset Detail" High/Ultra set (M_Car*_Hi, on the _high/_ultra
# meshes only - their auto-smoothed normals are what make a mirror finish
# look like paint instead of a disco ball; Standard's faceted meshes keep
# the set above). Body = real clear coat: glossy coat (0.03) over a rougher
# metallic-flake base; chrome/glass/lens near-mirror. Lumen reflections
# (r.ReflectionMethod=1, DefaultEngine.ini) supply what they reflect.
# clear_coat = (ClearCoat, ClearCoatRoughness) -> MSM_CLEAR_COAT, bound to
# CustomData0/1 (NOT verified offline - check M_CarBody_Hi's shading model
# and pins in the editor on first run).
CATEGORY_MATERIALS_HI = {
    # category: (asset_name, roughness, metallic, two_sided, opacity_or_None[, clear_coat])
    "body":    ("M_CarBody_Hi", 0.35, 0.55, True, None, (1.0, 0.03)),
    "plastic": ("M_CarPlastic_Hi", 0.55, 0.0, True, None),
    "glass":   ("M_CarGlass_Hi", 0.02, 0.05, True, 0.80),
    "chrome":  ("M_CarChrome_Hi", 0.02, 1.0, True, None),
    "tire":    ("M_CarTire_Hi", 0.85, 0.0, True, None),
    "rim":     ("M_CarRim_Hi", 0.12, 0.9, True, None),
    "lights":  ("M_CarLights_Hi", 0.08, 0.0, True, None),
}


def build_material(cat, mat_name, roughness, metallic, two_sided, opacity, clear_coat=None):
    mat_path = f"{CAR_DIR}/{mat_name}"
    mat = None
    if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
        mat = unreal.load_asset(mat_path)
        if mat is not None:
            MEL.delete_all_material_expressions(mat)
    if mat is None:
        mat = tools.create_asset(mat_name, CAR_DIR, unreal.Material,
                                  unreal.MaterialFactoryNew())
    if mat is None:
        raise RuntimeError(f"could not create or load {mat_path}")

    ts = MEL.create_material_expression(
        mat, unreal.MaterialExpressionTextureSample, -500, 0)
    ts.set_editor_property("texture", palette)
    MEL.connect_material_property(ts, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)

    rough = MEL.create_material_expression(
        mat, unreal.MaterialExpressionConstant, -500, 250)
    rough.set_editor_property("r", roughness)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

    metal = MEL.create_material_expression(
        mat, unreal.MaterialExpressionConstant, -500, 400)
    metal.set_editor_property("r", metallic)
    MEL.connect_material_property(metal, "", unreal.MaterialProperty.MP_METALLIC)

    spec = MEL.create_material_expression(
        mat, unreal.MaterialExpressionConstant, -500, 550)
    spec.set_editor_property("r", 0.9 if cat in ("body", "glass", "chrome") else 0.5)
    MEL.connect_material_property(spec, "", unreal.MaterialProperty.MP_SPECULAR)

    # UE 5.8 Python exposes no MP_CUSTOM_DATA0/1 (2026-10-09 run): default lit, coat roughness skipped
    if clear_coat is not None and not hasattr(unreal.MaterialProperty, "MP_CUSTOM_DATA0"):
        log(f"{mat_name}: no CustomData pins in this UE Python - default lit, no clear coat")
        clear_coat = None
    if clear_coat is not None:
        mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_CLEAR_COAT)
        for i, (val, prop) in enumerate(zip(clear_coat, (unreal.MaterialProperty.MP_CUSTOM_DATA0,
                                                         unreal.MaterialProperty.MP_CUSTOM_DATA1))):
            c = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -500, 850 + 150 * i)
            c.set_editor_property("r", val)
            MEL.connect_material_property(c, "", prop)

    if opacity is not None:
        # translucent glass: constant opacity, no Nanite (Nanite requires
        # opaque/masked - leaving used_with_nanite off below for this branch).
        op = MEL.create_material_expression(
            mat, unreal.MaterialExpressionConstant, -500, 700)
        op.set_editor_property("r", opacity)
        MEL.connect_material_property(op, "", unreal.MaterialProperty.MP_OPACITY)
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    else:
        mat.set_editor_property("used_with_nanite", True)

    # the procedural OBJ writer's y-negation mirrors the mesh, so roughly
    # half the hull faces end up wound inward - every car material MUST be
    # two-sided or the body renders "not enclosed" (same fix as the old
    # single-material M_Car2/M_Car/M_Car_GT convention).
    mat.set_editor_property("two_sided", two_sided)
    MEL.recompile_material(mat)
    unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
    log(f"built {mat_name} (roughness {roughness}, metallic {metallic}"
        f"{f', opacity {opacity}' if opacity is not None else ''}"
        f"{f', clear coat {clear_coat}' if clear_coat else ''})")
    return mat


category_material_assets = {cat: build_material(cat, *v) for cat, v in CATEGORY_MATERIALS.items()}
category_material_assets_hi = {cat: build_material(cat, *v) for cat, v in CATEGORY_MATERIALS_HI.items()}

# CATEGORY_ORDER mirrors pipeline/make_car_meshes.py's CATEGORY_ORDER - used
# as the fallback slot->category mapping (by usemtl-encounter order) if
# slot-NAME lookup doesn't come back populated for a given mesh (see module
# docstring's not-smoke-tested note).
CATEGORY_ORDER = ["body", "plastic", "glass", "chrome", "tire", "rim", "lights"]

for name, mesh in meshes.items():
    try:
        slots = list(mesh.get_editor_property("static_materials"))
    except Exception as exc:  # pragma: no cover - defensive, see docstring
        log(f"{name}: could not read static_materials ({exc}) - skipping material assignment")
        continue

    slot_names = [str(s.get_editor_property("material_slot_name")) for s in slots]
    hi = name.endswith(("_high", "_ultra"))
    mats = category_material_assets_hi if hi else category_material_assets
    named_ok = all(n in CATEGORY_MATERIALS for n in slot_names) and slot_names
    if named_ok:
        for i, cat in enumerate(slot_names):
            mesh.set_material(i, mats[cat])
        log(f"{name}: assigned {len(slot_names)} slot(s) by name ({slot_names})")
    else:
        # fallback: assign in CATEGORY_ORDER, trimmed to however many slots
        # this mesh actually has (usemtl-encounter order matches
        # CATEGORY_ORDER by construction on the generator side).
        cats = CATEGORY_ORDER[:len(slots)]
        for i, cat in enumerate(cats):
            mesh.set_material(i, mats[cat])
        log(f"{name}: slot names not usable ({slot_names}) - assigned "
            f"{len(cats)} slot(s) by encounter order ({cats}) instead")

unreal.EditorAssetLibrary.save_directory(CAR_DIR, only_if_is_dirty=False)
log(f"assigned category materials to all {len(meshes)} meshes")

# ------------------------------------------------------ the seven vehicle defs
wheel_mesh = meshes["SM_CarWheel"]


def make_def(name, props):
    path = f"{CAR_DIR}/{name}"
    a = None
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        unreal.EditorAssetLibrary.delete_asset(path)
        if unreal.EditorAssetLibrary.does_asset_exist(path):
            # delete_asset can silently fail to remove an asset still
            # considered referenced (seen once in this environment on a
            # same-session re-run) - rather than let the next create_asset
            # return None and crash on set_editor_property, just update the
            # existing asset's properties in place. Re-running this script
            # is then idempotent either way.
            log(f"{name}: delete_asset did not remove it - updating in place instead")
            a = unreal.load_asset(path)
    if a is None:
        factory = unreal.DataAssetFactory()
        factory.set_editor_property("data_asset_class", unreal.ODVehicleDef)
        a = tools.create_asset(name, CAR_DIR, unreal.ODVehicleDef, factory)
    if a is None:
        raise RuntimeError(f"could not create or load {path}")
    a.set_editor_property("wheel_mesh", wheel_mesh)
    for k, v in props.items():
        a.set_editor_property(k, v)
    unreal.EditorAssetLibrary.save_asset(path, only_if_is_dirty=False)
    log(f"wrote {name} ('{props.get('display_name')}')")


# geometry/mass straight from make_car_meshes.py's printed per-car dicts -
# axle geometry + WheelRadiusCm match each mesh's own footprint so
# UODVehicleMovement's wheel table lines up with the visual wheels. On top
# of that each car now has its own chassis/powertrain character: weight
# split (front_weight_frac), yaw_dynamic_index (Iz = DI*m*a*b), COM height,
# drive layout, torque and brakes (total scaled with mass x wheel radius,
# front share ~ split + 0.1; handbrake 2x rear). Validated in
# pipeline/checks/esc_sim.py (Street, High/Low brake-into-turn + snap). Tire and
# suspension stay at UODVehicleDef's defaults; aero too except
# downforce_coeff (N per (m/s)^2) on the aero cars, sized at 150 mph
# (67.06 m/s, the usual real-world quote point): Concept = 1.0x its weight
# (Taylor, 2026-09-28), LMP 1.5x, Super 0.5x - coeff = k*m*9.81/67.06^2.
make_def("DA_Vehicle_Muscle", {
    "display_name": "Muscle",
    "chassis_mesh": meshes["SM_Car_Muscle"],
    "front_axle_x_cm": 140.0, "rear_axle_x_cm": -150.0,
    "half_track_y_cm": 90.0, "wheel_radius_cm": 36.0,
    "mass_kg": 1620.0,
    # front V8 in a long hood: nose-heavy, heavy ends, tallish; big torque
    "front_weight_frac": 0.55, "yaw_dynamic_index": 1.05, "com_local_z": 12.0,
    "drive_layout": unreal.ODDriveLayout.RWD, "peak_torque_nm": 560.0,
    "front_brake_nm": 5550.0, "rear_brake_nm": 3000.0, "handbrake_nm": 6000.0,
})
make_def("DA_Vehicle_JDM", {
    "display_name": "JDM",
    "chassis_mesh": meshes["SM_Car_JDM"],
    "front_axle_x_cm": 124.0, "rear_axle_x_cm": -114.0,
    "half_track_y_cm": 80.0, "wheel_radius_cm": 32.0,
    "mass_kg": 1380.0,
    # front-mid turbo six, low. Short 2.38 m wheelbase: COM 4 + DI 1.05 + 66% front
    # brake share keep Low-assist trail-braking catchable (COM 6 spins it)
    "front_weight_frac": 0.52, "yaw_dynamic_index": 1.05, "com_local_z": 4.0,
    "drive_layout": unreal.ODDriveLayout.RWD, "peak_torque_nm": 450.0,
    "front_brake_nm": 4300.0, "rear_brake_nm": 2200.0, "handbrake_nm": 4400.0,
})
make_def("DA_Vehicle_Super", {
    "display_name": "Super",
    "chassis_mesh": meshes["SM_Car_Super"],
    "front_axle_x_cm": 118.0, "rear_axle_x_cm": -150.0,
    "half_track_y_cm": 94.0, "wheel_radius_cm": 36.0,
    "mass_kg": 1430.0,
    # mid-engine: rear-biased, mass inside the wheelbase (low DI), low
    "front_weight_frac": 0.42, "yaw_dynamic_index": 0.88, "com_local_z": 5.0,
    "drive_layout": unreal.ODDriveLayout.RWD, "peak_torque_nm": 650.0,
    "front_brake_nm": 3950.0, "rear_brake_nm": 3650.0, "handbrake_nm": 7300.0,
    "downforce_coeff": 1.56,
})
make_def("DA_Vehicle_Pickup", {
    "display_name": "Pickup",
    "chassis_mesh": meshes["SM_Car_Pickup"],
    "front_axle_x_cm": 185.0, "rear_axle_x_cm": -160.0,
    "half_track_y_cm": 98.0, "wheel_radius_cm": 42.0,
    "mass_kg": 2380.0,
    # empty-bed 4x4 (off-road crew cab -> AWD): nose-heavy, long overhangs, tall
    "front_weight_frac": 0.57, "yaw_dynamic_index": 1.1, "com_local_z": 20.0,
    "drive_layout": unreal.ODDriveLayout.AWD, "peak_torque_nm": 650.0,
    "front_brake_nm": 9850.0, "rear_brake_nm": 4850.0, "handbrake_nm": 9700.0,
})
make_def("DA_Vehicle_Rally", {
    "display_name": "Rally",
    "chassis_mesh": meshes["SM_Car_Rally"],
    "front_axle_x_cm": 112.0, "rear_axle_x_cm": -118.0,
    "half_track_y_cm": 78.0, "wheel_radius_cm": 32.0,
    "mass_kg": 1310.0,
    # rally-prepped AWD hatch: ballast/fuel moved back (56%), kit at both ends (DI 1.1),
    # sits low; 78% front brakes like a FWD-based hatch; hydraulic handbrake.
    # Still the twitchiest car (2.30 m wheelbase) - see esc_sim.py LOW_EXTRA
    "front_weight_frac": 0.56, "yaw_dynamic_index": 1.1, "com_local_z": 6.0,
    "drive_layout": unreal.ODDriveLayout.AWD, "peak_torque_nm": 440.0,
    "front_brake_nm": 4800.0, "rear_brake_nm": 1350.0, "handbrake_nm": 5000.0,
})
make_def("DA_Vehicle_Concept", {
    "display_name": "Concept",
    "chassis_mesh": meshes["SM_Car_Concept"],
    "front_axle_x_cm": 128.0, "rear_axle_x_cm": -140.0,
    "half_track_y_cm": 92.0, "wheel_radius_cm": 35.0,
    "mass_kg": 1150.0,
    # LMP-style road concept (Furai x GT40): mid-engine RWD, like the Super
    "front_weight_frac": 0.42, "yaw_dynamic_index": 0.88, "com_local_z": 5.0,
    "drive_layout": unreal.ODDriveLayout.RWD, "peak_torque_nm": 500.0,
    "front_brake_nm": 3100.0, "rear_brake_nm": 2850.0, "handbrake_nm": 5700.0,
    "downforce_coeff": 2.51,
})
make_def("DA_Vehicle_LMP", {
    "display_name": "LMP",
    "chassis_mesh": meshes["SM_Car_LMP"],
    "front_axle_x_cm": 150.0, "rear_axle_x_cm": -140.0,
    "half_track_y_cm": 88.0, "wheel_radius_cm": 34.0,
    "mass_kg": 1030.0,
    # Le Mans prototype: mid-engine, aero nose keeps it 45% front, lowest COM
    "front_weight_frac": 0.45, "yaw_dynamic_index": 0.85, "com_local_z": 3.0,
    "drive_layout": unreal.ODDriveLayout.RWD, "peak_torque_nm": 600.0,
    "front_brake_nm": 2850.0, "rear_brake_nm": 2300.0, "handbrake_nm": 4600.0,
    "downforce_coeff": 3.37,
})

unreal.EditorAssetLibrary.save_directory(CAR_DIR, only_if_is_dirty=False)
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved 8 meshes, 7 category materials, and 7 vehicle defs")
