"""Stylized ground pass for the open-world baked terrain (2026-07-21,
extended 2026-07-27 with a sand/beach blend for the Oahu testbed): full
biome-driven land-cover texturing (Arc 7.3's imagery-drape item) is a
separate, bigger pipeline job - this is the cheap placeholder version,
matching the game's low-poly storybook art direction:
  - slope-blended grass (flat) <-> rock (steep), unchanged from the
    original pass;
  - NEW: blended toward a sand color at low, FLAT elevation near
    SeaLevelZCm - a material SCALAR PARAMETER (so it CAN still be overridden
    per-instance), but its default is now derived from test_areas'
    ORIGIN_HEIGHT_M at build time instead of a hardcoded 0.0 - world Z=0 is
    wherever each area's ORIGIN_HEIGHT_M sampling point happened to land,
    NOT true sea level (elevation=0 m), so a hardcoded 0.0 default silently
    read as "sand below the origin's own elevation" instead of "sand near
    the real coastline". Harmless on Skagit/Palmdale (origin near 0 m,
    inland test loops with no real coastline to expose it) but a real bug
    on Oahu (ORIGIN_HEIGHT_M=306 m, a real island): SAND_DEFAULT_Z_CM below
    made the ENTIRE map below the plateau's elevation (the large majority
    of the island) read as full sand - found live 2026-07-28, Taylor: "beach
    should literally only appear for the last 50 feet or so before the
    water". BEACH_BAND_CM is now exactly that 50 ft, converted to cm.
    Multiplied by the same flatness factor the grass/rock blend already
    uses, so a low but STEEP sea cliff still reads as rock, not sand.
Same MaterialEditingLibrary node-graph approach build_visuals_headless.py/
apply_light_wet_fix.py already use for M_Road.

Textured pass (2026-08-24, Taylor live drive-test: "Flat green terrain, no
texture/variation" - the material above only ever fed MP_BASE_COLOR flat
CONSTANT colors, so even with the slope blend working correctly there was
zero per-pixel detail anywhere on the ground, just a solid painted color).
Content/OpenDrive/Textures/T_DetailGrass(_N)/T_DetailRock(_N) already exist
in the project (pipeline/make_detail_textures.py) but were generated for
the older Tail-of-the-Dragon stage and never wired into this, the Arc-7
open-world ground material - same stylized low-poly art direction, so they
are reused as-is rather than generating a second, redundant texture set.
Sampled with a world-space tiling UV (terrain tiles hand every tile the
SAME 0..1 UV range regardless of the tile's real 2000 m size, so sampling
mesh UV directly would show one texture repeat per tile, not real tiling)
and tinted by the existing GRASS_COLOR/ROCK_COLOR constants so the color
tuning history above (esp. the 2026-07-28 Hawaii-greenery darkening) still
holds - the texture supplies per-pixel variation, the constants still set
the overall hue. Sand stays a flat color: no beach detail texture exists
yet and the beach band is narrow enough (~1 m) that tiling artifacts would
rarely be visible anyway.

AODTerrainManager loads this by path directly (LoadObject, BeginPlay) - no
level-actor material assignment needed, unlike the old streamed-terrain
tileset actor this script used to also wire up (removed 2026-07-27; that
actor no longer exists post-Arc-7.0-pivot - AODTerrainManager's baked
DynamicMesh tiles replaced it).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import sys
from pathlib import Path

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_GroundStylized"

GRASS_COLOR = (0.07, 0.20, 0.07)   # stylized grass green - darkened 2026-07-28
                                    # (Taylor: real Hawaii greenery reads much
                                    # darker/denser than the game's color, not
                                    # brighter - a same-day first attempt at
                                    # (0.24, 0.48, 0.16) went the wrong
                                    # direction and was reverted). Darker AND
                                    # more saturated than the original
                                    # (0.16, 0.34, 0.12) inherited from the
                                    # Dragon's temperate-forest tuning - real
                                    # dense tropical canopy/undergrowth reads
                                    # closer to this.
ROCK_COLOR = (0.42, 0.40, 0.37)    # stylized rock grey
SAND_COLOR = (0.76, 0.69, 0.52)    # stylized beach sand
SLOPE_SOFTNESS = 4.0               # higher = sharper grass/rock transition
# Taylor, 2026-07-28: "beach should literally only appear for the last 50
# feet or so before the water" - but that's LATERAL distance from the
# shoreline, and this material only has a HEIGHT signal to work with, not
# a true distance-to-coast field. Clarified same session: "I mean first
# lateral 50 feet ... in reality this is like the first 3 feet above sea
# level" - i.e. a real beach is nearly flat (~3 ft of rise over its ~50 ft
# width), so a 3 ft VERTICAL band is what actually produces a ~50 ft-wide
# sand strip at realistic beach grade. Do not re-widen this back toward 50
# ft of height thinking it's the same knob - that would paint sand up
# every gentle slope on the whole map, the original "everything is beach"
# bug (SEA_LEVEL_Z_CM mismatch) just with a different-sized version of the
# same failure mode.
BEACH_BAND_CM = 3.0 * 0.3048 * 100.0
# world Z (cm) of true sea level (elevation=0 m) for THIS area - see
# test_areas.ORIGIN_HEIGHT_M's own comment: world Z = (elevation_m -
# ORIGIN_HEIGHT_M) * 100 at the origin, so elevation=0 sits at
# -ORIGIN_HEIGHT_M*100, not 0. Rebuilt fresh per-area every time this
# script runs (same as every other AREA_NAME-driven pipeline step).
SEA_LEVEL_Z_CM = -ORIGIN_HEIGHT_M * 100.0
ROUGHNESS = 0.9
SAND_ROUGHNESS = 0.55              # damp-looking sand reads better than matte
# world-space texel size (cm) for the detail textures - small enough to
# read as real ground detail up close, large enough that the tiling isn't
# blatant from the road. Tune here, not per-tile UVs (see module docstring).
DETAIL_TILE_CM = 800.0


def log(msg):
    unreal.log(f"GROUNDMAT {msg}")


tools = unreal.AssetToolsHelpers.get_asset_tools()
MEL = unreal.MaterialEditingLibrary

mat_path = f"{MAT_DIR}/{MAT_NAME}"

# Vertex-color ground tint (2026-08-26, terrain biome tint task): this script
# used to fully DELETE and re-create M_GroundStylized every run. That turned
# out to be unreliable in headless -run=pythonscript mode: EditorAssetLibrary
# .delete_asset() would intermittently/repeatably fail ("something still
# holds a live reference to it") on a material that had just been through
# MEL.recompile_material() + save_asset() in an earlier run, even with zero
# Python references left and zero asset-registry referencers - almost
# certainly the async shader-compile/material-resource machinery keeping the
# UMaterial alive a little longer than a plain load+delete would. Deleting
# nothing sidesteps the whole problem: if the asset already exists, load it,
# log its current graph for a real before/after record, then wipe its
# expressions with delete_all_material_expressions() (an in-place clear, not
# an asset delete) and rebuild onto the SAME UMaterial object. This is what
# makes the script actually re-runnable, which it needs to be as a repeatable
# pipeline step.
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    log("--- pre-edit M_GroundStylized expression graph ---")
    try:
        old_exprs = MEL.get_material_expressions(mat)
        for expr in old_exprs:
            pos = MEL.get_material_expression_node_position(expr)
            log(f"  {expr.get_class().get_name()} @ {pos}")
        # the exact node this change taps - confirms what's really driving
        # MP_BASE_COLOR before the graph gets cleared and rebuilt below.
        base_color_node = MEL.get_material_property_input_node(
            mat, unreal.MaterialProperty.MP_BASE_COLOR)
        log(f"  MP_BASE_COLOR currently driven by: "
            f"{base_color_node.get_class().get_name() if base_color_node else None}")
    except Exception as exc:  # inspection is best-effort/diagnostic only - never block the rebuild
        log(f"  (inspection failed, proceeding with rebuild anyway: {exc})")
    log("--- end pre-edit graph ---")
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

# ---- slope = dot(vertex normal, world up) - 1.0 on flat ground, ~0 on cliffs
normal = MEL.create_material_expression(
    mat, unreal.MaterialExpressionVertexNormalWS, -900, -200)
up = MEL.create_material_expression(
    mat, unreal.MaterialExpressionConstant3Vector, -900, 0)
up.set_editor_property("constant", unreal.LinearColor(0.0, 0.0, 1.0, 1.0))
dot = MEL.create_material_expression(
    mat, unreal.MaterialExpressionDotProduct, -650, -100)
MEL.connect_material_expressions(normal, "", dot, "A")
MEL.connect_material_expressions(up, "", dot, "B")

# sharpen the flat/steep transition (raw dot is too gradual - most terrain
# reads as "slightly rocky" even on gentle slopes)
sharpen = MEL.create_material_expression(
    mat, unreal.MaterialExpressionPower, -450, -100)
MEL.connect_material_expressions(dot, "", sharpen, "Base")
exp = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -450, 50)
exp.set_editor_property("r", SLOPE_SOFTNESS)
MEL.connect_material_expressions(exp, "", sharpen, "Exponent")

grass = MEL.create_material_expression(
    mat, unreal.MaterialExpressionConstant3Vector, -450, -300)
grass.set_editor_property("constant", unreal.LinearColor(*GRASS_COLOR, 1.0))
rock = MEL.create_material_expression(
    mat, unreal.MaterialExpressionConstant3Vector, -450, 200)
rock.set_editor_property("constant", unreal.LinearColor(*ROCK_COLOR, 1.0))

# ---- detail textures (2026-08-24) - world-space tiling UV, shared by the
# grass and rock samples so both stay in registration with each other.
# world_pos is also reused below by the sand/beach blend (its Z channel).
world_pos = MEL.create_material_expression(
    mat, unreal.MaterialExpressionWorldPosition, -900, 500)
detail_uv_xy = MEL.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -900, -400)
detail_uv_xy.set_editor_property("r", True)
detail_uv_xy.set_editor_property("g", True)
detail_uv_xy.set_editor_property("b", False)
detail_uv_xy.set_editor_property("a", False)
MEL.connect_material_expressions(world_pos, "", detail_uv_xy, "")
detail_tile = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -900, -550)
detail_tile.set_editor_property("r", 1.0 / DETAIL_TILE_CM)
detail_uv = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -750, -400)
MEL.connect_material_expressions(detail_uv_xy, "", detail_uv, "A")
MEL.connect_material_expressions(detail_tile, "", detail_uv, "B")

tex_grass = unreal.load_asset("/Game/OpenDrive/Textures/T_DetailGrass")
tex_grass_n = unreal.load_asset("/Game/OpenDrive/Textures/T_DetailGrass_N")
tex_rock = unreal.load_asset("/Game/OpenDrive/Textures/T_DetailRock")
tex_rock_n = unreal.load_asset("/Game/OpenDrive/Textures/T_DetailRock_N")


def connect_uv(uv_node, tex_sample_node):
    # UE 5.8's python binding reports MaterialExpressionTextureSample's UV
    # input pin under different names depending on context ("UVs" is the
    # documented/common one, "Coordinates" seen elsewhere in this repo -
    # build_visuals_headless.py's Graph.sample() has the same fallback,
    # same class of gotcha as this file's Clamp/If pin-name notes above).
    for pin in ("UVs", "Coordinates"):
        if MEL.connect_material_expressions(uv_node, "", tex_sample_node, pin):
            return
    raise RuntimeError("could not connect detail-texture UV input - pin name mismatch?")


grass_tex = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, -450)
grass_tex.set_editor_property("texture", tex_grass)
connect_uv(detail_uv, grass_tex)
grass_n_tex = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, -600)
grass_n_tex.set_editor_property("texture", tex_grass_n)
grass_n_tex.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
connect_uv(detail_uv, grass_n_tex)

rock_tex = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, 100)
rock_tex.set_editor_property("texture", tex_rock)
connect_uv(detail_uv, rock_tex)
rock_n_tex = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSample, -600, 250)
rock_n_tex.set_editor_property("texture", tex_rock_n)
rock_n_tex.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
connect_uv(detail_uv, rock_n_tex)

# GRASS branch (2026-08-27 rework): hue now comes from the per-vertex biome
# tint, NOT from GRASS_COLOR. The original design multiplied the biome tint
# over an already-green textured base at the very end of the graph - but
# multiply can only darken channels, so a tan desert tint could never
# overcome the green base (and the near-identical dark greens would have
# rendered forests almost black). Discovered 2026-08-27 when Antelope
# Valley rendered lush green over correctly-baked Canyon-tan vertex colors
# ... which it turned out were never wired into the shipped asset at all.
# Now: desaturate the grass detail texture to pure brightness variation,
# regain it toward ~1.0, and let the vertex color BE the ground hue.
# AODTerrainManager bakes absolute ground colors (BiomeGroundTintTable's
# Deciduous entry ~= the old GRASS_COLOR), and fills a default green when a
# tile has no biome data, so flat ground everywhere stays sensibly colored.
# Rock cliffs keep ROCK_COLOR via the slope lerp below - the biome tint
# only owns the flat/"grass" branch. GRASS_COLOR itself is now unused by
# the graph (kept above for the tuning-history record).
vertex_tint = MEL.create_material_expression(mat, unreal.MaterialExpressionVertexColor, -600, -300)
grass_desat = MEL.create_material_expression(mat, unreal.MaterialExpressionDesaturation, -500, -450)
MEL.connect_material_expressions(grass_tex, "RGB", grass_desat, "")
# detail texture averages ~0.5 luma; x2 recenters variation around 1.0 so
# the vertex tint's own brightness carries through unchanged on average
grass_var_gain = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -500, -250)
grass_var_gain.set_editor_property("r", 2.0)
grass_var = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -450, -400)
MEL.connect_material_expressions(grass_desat, "", grass_var, "A")
MEL.connect_material_expressions(grass_var_gain, "", grass_var, "B")
grass_tinted = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -400, -350)
MEL.connect_material_expressions(grass_var, "", grass_tinted, "A")
# VertexColor's RGB output pin is NOT addressable as "RGB" in UE 5.8's
# python binding - connecting with "RGB" silently no-ops (returns false,
# nothing raises), leaving Multiply.B unconnected so it falls back to its
# ConstB=1.0 default and the tint vanishes. Same gotcha family as the
# Clamp note below; caught 2026-08-27 by the saved-asset graph probe
# (scratchpad verify_groundmat_graph.py: inputs=['Multiply', 'None']).
# "" selects the first (RGB) output, same as every other node here.
for pin in ("", "RGB"):
    if MEL.connect_material_expressions(vertex_tint, pin, grass_tinted, "B"):
        break
else:
    raise RuntimeError("could not connect VertexColor -> grass tint multiply")
rock_tinted = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -400, 150)
MEL.connect_material_expressions(rock_tex, "RGB", rock_tinted, "A")
MEL.connect_material_expressions(rock, "", rock_tinted, "B")

lerp = MEL.create_material_expression(
    mat, unreal.MaterialExpressionLinearInterpolate, -200, 0)
MEL.connect_material_expressions(rock_tinted, "", lerp, "A")
MEL.connect_material_expressions(grass_tinted, "", lerp, "B")
MEL.connect_material_expressions(sharpen, "", lerp, "Alpha")

normal_lerp = MEL.create_material_expression(
    mat, unreal.MaterialExpressionLinearInterpolate, -200, -700)
MEL.connect_material_expressions(rock_n_tex, "", normal_lerp, "A")
MEL.connect_material_expressions(grass_n_tex, "", normal_lerp, "B")
MEL.connect_material_expressions(sharpen, "", normal_lerp, "Alpha")
MEL.connect_material_property(normal_lerp, "", unreal.MaterialProperty.MP_NORMAL)

# ---- sand/beach blend (2026-07-27): flat AND near sea level ----------------
world_z = MEL.create_material_expression(
    mat, unreal.MaterialExpressionComponentMask, -700, 500)
world_z.set_editor_property("r", False)
world_z.set_editor_property("g", False)
world_z.set_editor_property("b", True)
world_z.set_editor_property("a", False)
MEL.connect_material_expressions(world_pos, "", world_z, "")

# SeaLevelZCm: a parameter, not a constant - see module docstring for why
sea_level = MEL.create_material_expression(
    mat, unreal.MaterialExpressionScalarParameter, -700, 650)
sea_level.set_editor_property("parameter_name", "SeaLevelZCm")
sea_level.set_editor_property("default_value", SEA_LEVEL_Z_CM)

neg_one = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -700, 800)
neg_one.set_editor_property("r", -1.0)
neg_sea_level = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -550, 750)
MEL.connect_material_expressions(sea_level, "", neg_sea_level, "A")
MEL.connect_material_expressions(neg_one, "", neg_sea_level, "B")

height_above_sea = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -450, 600)
MEL.connect_material_expressions(world_z, "", height_above_sea, "A")
MEL.connect_material_expressions(neg_sea_level, "", height_above_sea, "B")

# beachiness = saturate((BEACH_BAND_CM - heightAboveSea) / BEACH_BAND_CM) -
# 1 at/below sea level, fading to 0 by BEACH_BAND_CM above it
band = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -450, 750)
band.set_editor_property("r", BEACH_BAND_CM)
neg_height = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -300, 700)
MEL.connect_material_expressions(height_above_sea, "", neg_height, "A")
MEL.connect_material_expressions(neg_one, "", neg_height, "B")
band_minus_height = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -150, 700)
MEL.connect_material_expressions(band, "", band_minus_height, "A")
MEL.connect_material_expressions(neg_height, "", band_minus_height, "B")
beach_raw = MEL.create_material_expression(mat, unreal.MaterialExpressionDivide, 0, 700)
MEL.connect_material_expressions(band_minus_height, "", beach_raw, "A")
MEL.connect_material_expressions(band, "", beach_raw, "B")
beachiness = MEL.create_material_expression(mat, unreal.MaterialExpressionClamp, 150, 700)
# Clamp's primary pin is UNNAMED in UE 5.8's python binding
# (get_material_expression_input_names() reports it as "" / None, not
# "Input" - the FExpressionInput member name from the C++ header does NOT
# match the pin name the reflection API expects). Connecting with "Input"
# silently no-ops (connect_material_expressions returns false but nothing
# raises), leaving the node fully unconnected - confirmed 2026-07-28 via
# get_inputs_for_material_expression() returning [None, None, None] on the
# saved asset. This is why the map has been flat engine-default GRAY since
# Oahu's sand-blend was added (2026-07-27): the material fails to compile
# for the game's shader platform ("(Node Clamp) Missing Clamp input",
# LogMaterial) and Unreal falls back to Default Material - but this only
# surfaces at REAL (non -nullrhi) compile time, so the headless build/save
# here reports success either way. Use "" like every other single-output
# connection in this script.
MEL.connect_material_expressions(beach_raw, "", beachiness, "")

# only flat AND low reads as sand - a low, steep sea cliff stays rock
sand_factor = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, 300, 500)
MEL.connect_material_expressions(beachiness, "", sand_factor, "A")
MEL.connect_material_expressions(sharpen, "", sand_factor, "B")

sand = MEL.create_material_expression(
    mat, unreal.MaterialExpressionConstant3Vector, 300, 0)
sand.set_editor_property("constant", unreal.LinearColor(*SAND_COLOR, 1.0))

final_lerp = MEL.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, 500, 200)
MEL.connect_material_expressions(lerp, "", final_lerp, "A")
MEL.connect_material_expressions(sand, "", final_lerp, "B")
MEL.connect_material_expressions(sand_factor, "", final_lerp, "Alpha")

# biome tint is applied in the GRASS branch above (vertex color IS the hue),
# not multiplied over the finished blend here - see the 2026-08-27 comment on
# the grass branch for why the old end-of-graph multiply could never work.
MEL.connect_material_property(final_lerp, "", unreal.MaterialProperty.MP_BASE_COLOR)

# roughness: lower over sand (damp-looking) - lerp the same way as color
rough_land = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -200, 950)
rough_land.set_editor_property("r", ROUGHNESS)
rough_sand = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -200, 1050)
rough_sand.set_editor_property("r", SAND_ROUGHNESS)
rough_lerp = MEL.create_material_expression(mat, unreal.MaterialExpressionLinearInterpolate, 500, 1000)
MEL.connect_material_expressions(rough_land, "", rough_lerp, "A")
MEL.connect_material_expressions(rough_sand, "", rough_lerp, "B")
MEL.connect_material_expressions(sand_factor, "", rough_lerp, "Alpha")
MEL.connect_material_property(rough_lerp, "", unreal.MaterialProperty.MP_ROUGHNESS)

MEL.recompile_material(mat)

# real bug, found 2026-07-27: removing the old streamed-terrain-tileset-
# assignment block (dead code post-Arc-7.0-pivot) also removed the ONLY thing that had
# been saving this package to disk (save_dirty_packages, originally called
# right before saving the level) - without an explicit save the material
# is created/compiled/logged successfully but stays transient in-memory
# only, vanishing the instant this commandlet process exits. Confirmed live:
# AODTerrainManager logged "TerrainMat load FAILED" on the very next level
# build even though this script had "succeeded" moments earlier.
# apply_building_material.py had the identical gap from its original
# authorship, fixed the same way, same day.
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)
log(f"built + saved {MAT_NAME} for area '{AREA_NAME}' (rock={ROCK_COLOR}, "
    f"sand={SAND_COLOR}, beach_band={BEACH_BAND_CM:.1f}cm, "
    f"SeaLevelZCm param default={SEA_LEVEL_Z_CM:.1f} (ORIGIN_HEIGHT_M={ORIGIN_HEIGHT_M}), "
    f"flat-ground hue = biome vertex color x desaturated grass detail "
    f"(AODTerrainManager::ApplyBiomeTint, 2026-08-27 rework))")
