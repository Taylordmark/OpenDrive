"""One-off verification dump: walk M_Foliage / M_Bark_Aspen / M_Bark_Conifer
/ M_Bark_Willow's material graphs and write a plain-text inventory of every
expression (class, key properties) plus the top-level material-property
connections, to verify_material_wiring.txt. Run via:
UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script=<this file>
    -nullrhi -unattended -nosplash -nop4 -stdout
Writing directly to a file rather than relying on unreal.log()/stdout
capture, which has proven unreliable to grep out of the commandlet's console
output in this environment (Display-level LogPython messages, including
plain print(), do not appear in the captured -stdout stream here; only
LogPython Errors do).
"""
import unreal
from collections import Counter

MEL = unreal.MaterialEditingLibrary
MAT_DIR = "/Game/OpenDrive/Materials"
OUT = r"C:\Users\taylo\AppData\Local\Temp\claude\C--Users-taylo-Coding-OpenDrive\26b7f002-8582-449d-bd00-11b035157f40\scratchpad\verify_material_wiring.txt"

MATS = ["M_Foliage", "M_Bark_Aspen", "M_Bark_Conifer", "M_Bark_Willow"]

lines = []


def dump(name):
    path = f"{MAT_DIR}/{name}"
    m = unreal.load_asset(path)
    lines.append(f"=== {name} ({path}) ===")
    if m is None:
        lines.append("  !! FAILED TO LOAD")
        return
    for prop in ("two_sided", "blend_mode", "shading_model",
                 "opacity_mask_clip_value", "used_with_instanced_static_meshes"):
        try:
            lines.append(f"  {prop}={m.get_editor_property(prop)}")
        except Exception as e:
            lines.append(f"  {prop}=<n/a: {e}>")

    exprs = MEL.get_material_expressions(m)
    lines.append(f"  expression_count={len(exprs)}")

    lines.append("  scalar params (name=default):")
    for pname in MEL.get_scalar_parameter_names(m):
        try:
            v = MEL.get_material_default_scalar_parameter_value(m, pname)
        except Exception as e:
            v = f"<err {e}>"
        lines.append(f"    {pname} = {v}")

    lines.append("  vector params (name=default):")
    for pname in MEL.get_vector_parameter_names(m):
        try:
            v = MEL.get_material_default_vector_parameter_value(m, pname)
        except Exception as e:
            v = f"<err {e}>"
        lines.append(f"    {pname} = {v}")

    lines.append("  texture samples:")
    for e in exprs:
        if isinstance(e, (unreal.MaterialExpressionTextureSampleParameter2D,
                           unreal.MaterialExpressionTextureSample)):
            tex = e.get_editor_property("texture")
            lines.append(f"    {e.get_class().get_name()}: texture={tex.get_name() if tex else None}")

    hist = Counter(e.get_class().get_name() for e in exprs)
    lines.append("  node class histogram:")
    for cls, n in sorted(hist.items()):
        lines.append(f"    {cls}: {n}")

    # material-property input nodes (what feeds BaseColor / Opacity / OpacityMask / SubsurfaceColor)
    lines.append("  material-property inputs:")
    for prop_name, prop_enum in [
        ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
        ("Opacity", unreal.MaterialProperty.MP_OPACITY),
        ("OpacityMask", unreal.MaterialProperty.MP_OPACITY_MASK),
        ("SubsurfaceColor", unreal.MaterialProperty.MP_SUBSURFACE_COLOR),
        ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
    ]:
        try:
            node = MEL.get_material_property_input_node(m, prop_enum)
            out_name = MEL.get_material_property_input_node_output_name(m, prop_enum)
            if node is not None:
                lines.append(f"    {prop_name} <- {node.get_class().get_name()} (output={out_name!r})")
            else:
                lines.append(f"    {prop_name} <- (unconnected)")
        except Exception as e:
            lines.append(f"    {prop_name} <- <err {e}>")

    lines.append("")


for name in MATS:
    dump(name)

with open(OUT, "w") as f:
    f.write("\n".join(lines))

with open(r"C:\Users\taylo\Coding\OpenDrive\scratchpad\probe_out.txt", "w") as f:
    f.write("done, wrote " + OUT)
