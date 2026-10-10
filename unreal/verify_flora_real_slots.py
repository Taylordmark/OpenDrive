"""Headless check (PLAN-VISUALS.md Track F): every _real flora mesh keeps its
stylized twin's material slots. For each <slot>_real{,_high,_ultra} in
/Game/OpenDrive/Trees it prints slot names + assigned materials, Nanite
flag, LOD count and LOD0 triangles, and fails unless:
  - slot count and slot names match the stylized <slot>{,_high,_ultra}, and
  - slot i's material is what ODVisualStyle::Pick would give for the
    twin's slot i (the twin's material + "_real" when that exists).
Read-only: saves nothing.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""
import unreal

TREE_DIR = "/Game/OpenDrive/Trees"
SLOTS = ("tree_blob", "tree_olive", "tree_conifer", "tree_scrub_oak", "tree_manzanita",
         "tree_yucca", "tree_canyon_oak", "bush", "sagebrush", "grass_tuft")
EAL = unreal.EditorAssetLibrary


def log(msg):
    unreal.log_warning(f"FLORASLOTS {msg}")


def picked(mat):
    """ODVisualStyle::Pick for a material: its _real twin if imported."""
    if mat is None:
        return None
    p = mat.get_path_name().split(".")[0] + "_real"
    return p if EAL.does_asset_exist(p) else mat.get_path_name().split(".")[0]


def slots(mesh):
    return [(str(s.material_slot_name), s.material_interface) for s in mesh.get_editor_property("static_materials")]


fails, missing = [], []
for slot in SLOTS:
    for tier in ("", "_high", "_ultra"):
        real_p, twin_p = f"{TREE_DIR}/{slot}_real{tier}", f"{TREE_DIR}/{slot}{tier}"
        if not EAL.does_asset_exist(twin_p):   # stylized tiers are optional (Upgrade falls back)
            twin_p = f"{TREE_DIR}/{slot}"
        if not EAL.does_asset_exist(real_p):
            missing.append(real_p)
            continue
        real, twin = unreal.load_asset(real_p), unreal.load_asset(twin_p)
        rs, ts = slots(real), slots(twin)
        nanite = real.get_editor_property("nanite_settings").enabled
        twin_nanite = twin.get_editor_property("nanite_settings").enabled
        lods = real.get_num_lods()
        desc = ", ".join(f"{i}:{n}={m.get_name() if m else None}" for i, (n, m) in enumerate(rs))
        log(f"{slot}_real{tier:7s} nanite={nanite} (twin {twin_nanite}) lods={lods} slots[{desc}]")
        if nanite != twin_nanite:
            fails.append(f"{real_p}: nanite {nanite} != twin {twin_nanite}")
        if [n for n, _ in rs] != [n for n, _ in ts]:
            fails.append(f"{real_p}: slot names {[n for n, _ in rs]} != twin {[n for n, _ in ts]}")
        for i, ((_, rm), (_, tm)) in enumerate(zip(rs, ts)):
            want = picked(tm)
            got = rm.get_path_name().split(".")[0] if rm else None
            if got != want:
                fails.append(f"{real_p}: slot {i} material {got} != Pick(twin) {want}")

fol = unreal.load_asset("/Game/OpenDrive/Materials/M_Foliage_real")
wpo = unreal.MaterialEditingLibrary.get_material_property_input_node(
    fol, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
log(f"M_Foliage_real wind WPO input: {wpo.get_class().get_name() if wpo else None}, "
    f"{unreal.MaterialEditingLibrary.get_num_material_expressions(fol)} nodes")
if wpo is None:
    fails.append("M_Foliage_real: world position offset (wind) not connected")

for m in missing:
    log(f"MISSING {m}")
for f in fails:
    log(f"FAIL {f}")
log(f"RESULT {'FAIL' if fails or missing else 'PASS'} ({len(fails)} mismatches, {len(missing)} missing)")
