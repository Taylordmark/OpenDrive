"""Embankment skirt material (Arc 7.19, 2026-09-07): a simple dirt-textured
M_Embankment for AODEmbankmentManager's runtime-extruded sloped skirt ribbon
(pipeline/build_road_embankments.py's classification, "embankment" class -
2.5-7m road-above-terrain drop). Deliberately reuses the ALREADY-IMPORTED
Ground037 dirt texture set (unreal/apply_ground_material.py's LAYERS["dirt"],
imported by import_ground_textures.py) rather than downloading a new one -
"dirt/gravel textured, reading like a built-up road bed" per the task brief,
and Ground037 is already exactly that look, already on disk, already in the
manifest (content_src/textures/ground/manifest.json).

World-space PLANAR (XY) UV, not triplanar - the skirt's own slope is ~33.7
degrees off horizontal (1.5:1 ratio => atan(1/1.5)), nowhere near the
near-vertical case that justifies M_GroundStylized's triplanar rock-cliff
layer (see that script's sample_triplanar docstring); a single planar
projection reads fine at this shallow-ish angle and is much cheaper/simpler,
matching the task brief's "simple triplanar or world-XY UVs" either-or.

Toe darkening: the runtime mesh (ODEmbankmentManager::BuildSkirtMesh) writes
UV0.V = 0.0 at the top (pavement edge) / 0.6 at the toe / 1.0 at the toe
apron - this material reads that back and multiplies the albedo down toward
the toe (TOE_DARKEN_STRENGTH), the classic "damp/shaded dirt at the base of
a slope" cue the task brief asked for ("slight darkening toward the toe
reads well"). No texture blending is keyed off it - same look, just darker.

Reuses the same G node-soup helper + known UE 5.8 python-binding pin-name
gotchas unreal/apply_ground_material.py established (see that file's
docstring for the full gotcha list: Power's "Exp" pin, VertexColor's ""
RGB(A) output, Clamp/Abs/Floor/Frac/Sine's unnamed primary pin,
TextureSample's "UVs"/"Coordinates" pin-name split).

Run unreal/import_ground_textures.py FIRST if Ground037 isn't already
imported (apply_ground_material.py's own prior run already requires this,
so on this project it's a no-op check, not a fresh import).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_Embankment"
GROUND_TEX_DIR = "/Game/OpenDrive/Textures/Ground"

DIRT_ASSET_ID = "Ground037"
DIRT_TILE_CM = 850.0            # matches apply_ground_material.py's LAYERS["dirt"] tile size
TOE_DARKEN_STRENGTH = 0.45      # 0 = no darkening, 1 = fully black at the toe apron

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"EMBANKMAT {msg}")


class G:
    """Minimal node-soup helper, same shape/gotcha-workarounds as
    unreal/apply_ground_material.py's own G class (see that file's docstring
    for the full pin-name gotcha list) - only the subset this material
    needs."""

    def __init__(self, mat):
        self.mat = mat
        self._y = 0

    def node(self, cls, **props):
        self._y += 200
        n = MEL.create_material_expression(
            self.mat, cls, -2200 + (self._y % 1600) * 2, self._y // 8)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def const3(self, r, gg, b):
        return self.node(unreal.MaterialExpressionConstant3Vector,
                          constant=unreal.LinearColor(r, gg, b, 1.0))

    def _req(self, ok, what):
        if not ok:
            raise RuntimeError(f"could not connect {what} - pin name mismatch?")

    def mask(self, a, r=False, gg=False, b=False, alpha=False, a_out=""):
        n = self.node(unreal.MaterialExpressionComponentMask, r=r, g=gg, b=b, a=alpha)
        self._req(MEL.connect_material_expressions(a, a_out, n, ""), "ComponentMask")
        return n

    def binop(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), f"{cls.__name__}.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), f"{cls.__name__}.B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.binop(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def lerp(self, a, b, alpha, a_out="", b_out="", al_out=""):
        n = self.node(unreal.MaterialExpressionLinearInterpolate)
        self._req(MEL.connect_material_expressions(a, a_out, n, "A"), "Lerp.A")
        self._req(MEL.connect_material_expressions(b, b_out, n, "B"), "Lerp.B")
        self._req(MEL.connect_material_expressions(alpha, al_out, n, "Alpha"), "Lerp.Alpha")
        return n

    def world_pos(self):
        return self.node(unreal.MaterialExpressionWorldPosition)

    def tex_coord(self, index=0):
        return self.node(unreal.MaterialExpressionTextureCoordinate, coordinate_index=index)

    def sample(self, tex, uv, is_normal=False, uv_out=""):
        n = self.node(unreal.MaterialExpressionTextureSample, texture=tex)
        if is_normal:
            n.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        ok = False
        for pin in ("UVs", "Coordinates"):
            if MEL.connect_material_expressions(uv, uv_out, n, pin):
                ok = True
                break
        self._req(ok, "TextureSample UV")
        return n


# ------------------------------------------------------------- load textures
base = f"{GROUND_TEX_DIR}/{DIRT_ASSET_ID}"
albedo_tex = unreal.load_asset(f"{base}/T_{DIRT_ASSET_ID}_A")
normal_tex = unreal.load_asset(f"{base}/T_{DIRT_ASSET_ID}_N")
rough_tex = unreal.load_asset(f"{base}/T_{DIRT_ASSET_ID}_R")
if albedo_tex is None or normal_tex is None or rough_tex is None:
    raise RuntimeError(
        f"embankment dirt texture ({DIRT_ASSET_ID}) missing under {base} - run "
        f"unreal/import_ground_textures.py first (apply_ground_material.py already "
        f"depends on the same set)")


# --------------------------------------------------------------- build material
mat_path = f"{MAT_DIR}/{MAT_NAME}"
if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
    mat = unreal.load_asset(mat_path)
    MEL.delete_all_material_expressions(mat)
else:
    mat = tools.create_asset(MAT_NAME, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())

g = G(mat)

world_pos = g.world_pos()
world_xy = g.mask(world_pos, r=True, gg=True)
uv = g.mul(world_xy, g.const(1.0 / DIRT_TILE_CM))

albedo = g.sample(albedo_tex, uv)
normal = g.sample(normal_tex, uv, is_normal=True)
rough = g.sample(rough_tex, uv)
rough_scalar = g.mask(rough, r=True, a_out="RGB")

# Toe darkening (task brief: "slight darkening toward the toe reads well") -
# ODEmbankmentManager::BuildSkirtMesh writes UV0.V = 0.0 top / 0.6 toe / 1.0
# apron; darken_t = V * TOE_DARKEN_STRENGTH, then Lerp(albedo, black, darken_t).
uv0 = g.tex_coord(0)
v_coord = g.mask(uv0, gg=True)
darken_t = g.mul(v_coord, g.const(TOE_DARKEN_STRENGTH))
black = g.const3(0.0, 0.0, 0.0)
final_albedo = g.lerp(albedo, black, darken_t)

ok_bc = MEL.connect_material_property(final_albedo, "", unreal.MaterialProperty.MP_BASE_COLOR)
ok_n = MEL.connect_material_property(normal, "", unreal.MaterialProperty.MP_NORMAL)
ok_r = MEL.connect_material_property(rough_scalar, "", unreal.MaterialProperty.MP_ROUGHNESS)
if not (ok_bc and ok_n and ok_r):
    raise RuntimeError(f"{MAT_NAME}: could not connect BaseColor/Normal/Roughness - pin name mismatch?")

MEL.recompile_material(mat)
unreal.EditorAssetLibrary.save_asset(mat_path, only_if_is_dirty=False)

reloaded = unreal.load_asset(mat_path)
for prop, name in ((unreal.MaterialProperty.MP_BASE_COLOR, "BaseColor"),
                   (unreal.MaterialProperty.MP_NORMAL, "Normal"),
                   (unreal.MaterialProperty.MP_ROUGHNESS, "Roughness")):
    n = MEL.get_material_property_input_node(reloaded, prop)
    if n is None:
        raise RuntimeError(f"{MAT_NAME}: MP_{name} unconnected after save (FAIL)")

log(f"built + saved {MAT_NAME} (dirt={DIRT_ASSET_ID}, tile={DIRT_TILE_CM}cm, "
    f"toe_darken={TOE_DARKEN_STRENGTH})")
log("DONE")
