"""Builds M_Water, the stylized flat-water material for baked lake surfaces
(WATER feature, 2026-08-27) - Green Valley's lakes (Hughes/Elizabeth/etc.,
pipeline/build_water_tiles.py) sit ~700-1000 m ASL, nowhere near a real
coastline, so this is deliberately just a deep desaturated-blue, low-
roughness flat color - no opacity/refraction (opaque is fine and cheaper,
matches the low-poly storybook art direction other placeholder materials
here already commit to - see apply_ground_material.py/apply_building_
material.py's own "cheap flat-shaded" precedent) and NO normal map: a
"glass-flat" stylized lake reads fine with zero ripple detail, and there is
no existing water-appropriate detail-normal texture in Content/OpenDrive/
Textures to reuse (T_DetailGrass_N/T_DetailRock_N would read as a grass/rock
bump pattern on water, not a ripple - worse than no normal at all). Revisit
with a real ripple texture later if the flat look reads wrong live.

Same in-place-rebuild MaterialEditingLibrary approach apply_ground_
material.py established (2026-08-26): load-and-clear via
delete_all_material_expressions() if the asset already exists rather than
delete_asset()+recreate (that pattern turned out unreliable in headless
-run=pythonscript mode - see that script's own comment for the "something
still holds a live reference" failure it fixes), so this script stays
re-runnable.

KNOWN UE 5.8 python-binding pin-name gotchas (apply_ground_material.py's own
hard-won notes, restated here since this script hits some of the same
nodes): MaterialExpressionClamp's primary input pin is UNNAMED ("" not
"Input"); MaterialExpressionVertexColor's RGB output is NOT addressable as
"RGB" (use "" instead). This material doesn't use Clamp/VertexColor, but
DOES use TextureCoordinate/ComponentMask/WorldPosition-style single-output
nodes, connected with "" throughout to sidestep the same class of silent
no-op. Every connection below is verified post-save via
get_inputs_for_material_expression() - see the VERIFY block at the bottom;
a silent pin no-op is a real, recurring failure in this codebase's material
scripts, not a hypothetical.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_Water"

# Deep desaturated blue - stylized, not photoreal (real lake water at this
# elevation/season reads closer to grey-green, but "obviously water" at a
# glance is the storybook-art-direction goal here, same as GRASS_COLOR/
# ROCK_COLOR/SAND_COLOR in apply_ground_material.py being stylized rather
# than sampled).
WATER_COLOR = (0.06, 0.16, 0.26)
ROUGHNESS = 0.1   # near-mirror-flat, stylized "still lake" look
SPECULAR = 0.6    # a bit above the engine 0.5 default - flat water should
                   # read as noticeably more reflective than matte ground


def log(msg):
    # unreal.log() (Display verbosity) doesn't reliably surface in this
    # project's headless -stdout captures (confirmed via a standalone probe
    # script, 2026-08-27: unreal.log_warning() shows up, unreal.log()/print()
    # do not, in this build/config) - log_warning is used here purely to get
    # real evidence into the captured log for verification, not because
    # these are actual warnings.
    unreal.log_warning(f"WATERMAT {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary

mat_path = f"{MAT_DIR}/{MAT_NAME}"

if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    log("--- pre-edit M_Water expression graph ---")
    try:
        for expr in MEL.get_material_expressions(mat):
            log(f"  {expr.get_class().get_name()} @ {MEL.get_material_expression_node_position(expr)}")
    except Exception as exc:   # inspection only, never blocks the rebuild
        log(f"  (inspection failed, proceeding with rebuild anyway: {exc})")
    log("--- end pre-edit graph ---")
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

# Water is a thin flat cap baked from a possibly-untrustworthy ear-clip/fan
# triangulation (pipeline/build_water_tiles.py runtime-fixes winding to face
# up under UE's own normal convention, but two-sided is a cheap, harmless
# safety net for a handful of triangles per lake either way - same reasoning
# apply_building_material.py already documents for its own untrusted-OSM-
# winding two-sided flag).
mat.set_editor_property("two_sided", True)

color = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -300, -100)
color.set_editor_property("constant", unreal.LinearColor(*WATER_COLOR, 1.0))
MEL.connect_material_property(color, "", unreal.MaterialProperty.MP_BASE_COLOR)

rough = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 100)
rough.set_editor_property("r", ROUGHNESS)
MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)

spec = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 250)
spec.set_editor_property("r", SPECULAR)
MEL.connect_material_property(spec, "", unreal.MaterialProperty.MP_SPECULAR)

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

# ---- VERIFY: confirm the saved graph actually wired what the script above
# thinks it wired - apply_ground_material.py's Clamp/VertexColor bugs both
# "succeeded" in headless -nullrhi mode (material created/compiled/saved,
# every log line printed clean) while silently leaving a required pin
# unconnected; the failure only ever surfaced at real (non -nullrhi) compile
# time or live in-game. Reload the just-saved asset fresh (not the in-memory
# `mat` reference) and probe each property input node's own inputs so this
# script can't fool itself the same way.
reloaded = unreal.load_asset(mat_path)
log("--- post-save verification ---")
all_ok = True
for prop_name, prop_enum in (
    ("BaseColor", unreal.MaterialProperty.MP_BASE_COLOR),
    ("Roughness", unreal.MaterialProperty.MP_ROUGHNESS),
    ("Specular", unreal.MaterialProperty.MP_SPECULAR),
):
    node = MEL.get_material_property_input_node(reloaded, prop_enum)
    if node is None:
        all_ok = False
        log(f"  MP_{prop_name}: UNCONNECTED (FAIL)")
        continue
    inputs = MEL.get_inputs_for_material_expression(reloaded, node)
    const_val = "?"
    try:
        const_val = node.get_editor_property("r")
    except Exception:
        try:
            const_val = node.get_editor_property("constant")
        except Exception:
            pass
    log(f"  MP_{prop_name}: driven by {node.get_class().get_name()} "
        f"(constant={const_val}) own_inputs={inputs}")
if not all_ok:
    raise RuntimeError(
        "M_Water: one or more base material property pins failed to connect - "
        "see the post-save verification log above (same silent-pin-no-op class "
        "of bug apply_ground_material.py's Clamp/VertexColor notes document)")

log(f"built + saved {MAT_NAME} (color={WATER_COLOR}, roughness={ROUGHNESS}, "
    f"specular={SPECULAR}, two_sided=True, no normal map - see module docstring)")
