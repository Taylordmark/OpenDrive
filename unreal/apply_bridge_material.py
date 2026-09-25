"""Bridge deck material (Arc 7.21, 2026-09-07): a simple plain-concrete
M_Bridge for AODBridgeManager's runtime-extruded deck fascia/underside-flap
ribbon and pier cylinders (pipeline/build_road_embankments.py's §7.21
sub-classification, "bridge" class - now genuine short spans only, see that
module's docstring). Deliberately reuses the ALREADY-IMPORTED Concrete034
texture set (unreal/apply_road_material.py's own optional "junction pad"
role - content_src/textures/road/manifest.json - already downloaded/imported
by unreal/import_road_textures.py) rather than downloading a new one - "plain
concrete look is fine" per the task brief, and Concrete034 is already exactly
that, already on disk, already in the manifest. No new CC0 asset, no new
manifest entry needed.

World-space PLANAR (XY) UV, same reasoning/precedent as
unreal/apply_embankment_material.py's M_Embankment: the deck fascia is a
near-vertical/horizontal ribbon at moderate scale, not the near-vertical
cliff case that justifies M_GroundStylized's triplanar layer - a single
planar XY projection reads fine and is much simpler/cheaper.

Underside darkening: AODBridgeManager::BuildDeckMesh writes UV0.V = 0.0 at
the top (pavement edge) / 0.7 at the fascia's bottom outer edge / 1.0 at the
underside flap's inner edge - this material reads that back and multiplies
the albedo down toward the underside (UNDERSIDE_DARKEN_STRENGTH), the same
"damp/shaded concrete in shadow" cue M_Embankment's toe-darkening uses, just
applied to the deck's own underside instead of an earthen skirt's toe.

Reuses the same G node-soup helper + known UE 5.8 python-binding pin-name
gotchas unreal/apply_ground_material.py established (see that file's
docstring for the full gotcha list).

Run unreal/import_road_textures.py FIRST if Concrete034 isn't already
imported (apply_road_material.py's own prior run already requires this on
this project, so it's normally a no-op check here).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MAT_NAME = "M_Bridge"
ROAD_TEX_DIR = "/Game/OpenDrive/Textures/Road"

CONCRETE_ASSET_ID = "Concrete034"
CONCRETE_TILE_CM = 650.0            # modest planar tile size for a deck-scale surface
UNDERSIDE_DARKEN_STRENGTH = 0.35    # 0 = no darkening, 1 = fully black at the underside flap

MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"BRIDGEMAT {msg}")


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
base = f"{ROAD_TEX_DIR}/{CONCRETE_ASSET_ID}"
albedo_tex = unreal.load_asset(f"{base}/T_{CONCRETE_ASSET_ID}_A")
normal_tex = unreal.load_asset(f"{base}/T_{CONCRETE_ASSET_ID}_N")
rough_tex = unreal.load_asset(f"{base}/T_{CONCRETE_ASSET_ID}_R")
if albedo_tex is None or normal_tex is None or rough_tex is None:
    raise RuntimeError(
        f"bridge concrete texture ({CONCRETE_ASSET_ID}) missing under {base} - run "
        f"unreal/import_road_textures.py first (apply_road_material.py already "
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
uv = g.mul(world_xy, g.const(1.0 / CONCRETE_TILE_CM))

albedo = g.sample(albedo_tex, uv)
normal = g.sample(normal_tex, uv, is_normal=True)
rough = g.sample(rough_tex, uv)
rough_scalar = g.mask(rough, r=True, a_out="RGB")

# Underside darkening - AODBridgeManager::BuildDeckMesh writes UV0.V = 0.0 top
# / 0.7 fascia bottom / 1.0 underside flap; darken_t = V * UNDERSIDE_DARKEN_
# STRENGTH, then Lerp(albedo, black, darken_t). Same mechanism as
# M_Embankment's toe-darkening, applied to the deck's own underside instead.
uv0 = g.tex_coord(0)
v_coord = g.mask(uv0, gg=True)
darken_t = g.mul(v_coord, g.const(UNDERSIDE_DARKEN_STRENGTH))
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

log(f"built + saved {MAT_NAME} (concrete={CONCRETE_ASSET_ID}, tile={CONCRETE_TILE_CM}cm, "
    f"underside_darken={UNDERSIDE_DARKEN_STRENGTH})")
log("DONE")
