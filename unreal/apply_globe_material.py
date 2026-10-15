"""In-engine world map globe (2026-10-14): builds the three materials
AODMenuGlobe (ODMenuGlobe.cpp) puts on its runtime meshes - the Open Drive /
Track Select globe that replaced the globe.gl page.

  M_Globe      opaque unlit. Emissive = "Albedo" (globe-dark.jpg, set at
               runtime) x an emboss from three taps of "Bump" (globe-bump.jpg,
               offset "BumpTexel", gain "BumpStrength") x a limb darkening
               ("Limb" at the edge, 1 facing the camera) x "Brightness" -
               stands in for globe.gl's Phong + bumpMap lit from the camera.
  M_GlobeLine  translucent, two-sided coast/border/state ribbons. Colour and
               alpha are the vertex colour. Each ribbon is built zero-width with
               its side direction as the vertex normal; WPO pushes it out by
               "Width" x camera distance, so lines stay ~1 px at any zoom.
               Opacity also x "Opacity" (2026-10-14: lines fade out near the
               ground). The star field reuses it (zero-size diamonds).
  M_GlobeAtmo  translucent rim on an inside-out shell (only its far half
               draws): opacity = (N.V / "Edge")^"Power" x "Strength", glowing
               full at the globe's limb and fading to 0 at the shell's edge,
               emissive "Color" (globe.gl atmosphereColor #8894a8). Now only a
               faint thin rim (2026-10-14).
  M_GlobeDrape translucent area relief draped on the globe (2026-10-14):
               emissive "Albedo" x "Brightness", opacity "Opacity" (the C++
               fades it in by altitude).

The translucent two are MSM_DEFAULT_LIT with black base colour, not Unlit -
see apply_snow_particle_material.py (Unlit + translucent renders solid black
in this project). Texture params default to WhiteSquareTexture (sRGB Color
samplers), so the C++ marks both runtime textures sRGB.

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import unreal

MAT_DIR = "/Game/OpenDrive/Materials"
MEL = unreal.MaterialEditingLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()
white_tex = unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture")
if white_tex is None:
    raise RuntimeError("/Engine/EngineResources/WhiteSquareTexture missing - pick another texture default")


def log(msg):
    unreal.log_warning(f"GLOBEMAT {msg}")


class G:
    """Tiny node-graph helper (pin-name gotchas: see apply_ground_material.py)."""

    def __init__(self, name):
        self.name = name
        self.path = f"{MAT_DIR}/{name}"
        if unreal.EditorAssetLibrary.does_asset_exist(self.path):
            self.mat = unreal.load_asset(self.path)
            for _e in list(MEL.get_material_expressions(self.mat)):  # not delete_all_material_expressions: UE 5.8 leaves half the nodes
                MEL.delete_material_expression(self.mat, _e)
        else:
            self.mat = tools.create_asset(name, MAT_DIR, unreal.Material, unreal.MaterialFactoryNew())
        self.y = 0

    def req(self, ok, what):
        if not ok:
            raise RuntimeError(f"{self.name}: failed to connect {what}")

    def node(self, cls, **props):
        self.y += 120
        n = MEL.create_material_expression(self.mat, cls, -1200, self.y)
        for k, v in props.items():
            n.set_editor_property(k, v)
        return n

    def link(self, a, a_out, n, pin):
        self.req(MEL.connect_material_expressions(a, a_out, n, pin), f"{n.get_class().get_name()}.{pin}")

    def op(self, cls, a, b, a_out="", b_out=""):
        n = self.node(cls)
        self.link(a, a_out, n, "A")
        self.link(b, b_out, n, "B")
        return n

    def mul(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionMultiply, a, b, a_out, b_out)

    def add(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionAdd, a, b, a_out, b_out)

    def sub(self, a, b, a_out="", b_out=""):
        return self.op(unreal.MaterialExpressionSubtract, a, b, a_out, b_out)

    def sat(self, a, a_out=""):
        n = self.node(unreal.MaterialExpressionClamp)   # 0..1 by default
        self.link(a, a_out, n, "")
        return n

    def const(self, v):
        return self.node(unreal.MaterialExpressionConstant, r=v)

    def scalar(self, name, default):
        return self.node(unreal.MaterialExpressionScalarParameter, parameter_name=name, default_value=default)

    def tex(self, name, uv):
        n = self.node(unreal.MaterialExpressionTextureSampleParameter2D, parameter_name=name, texture=white_tex)
        self.req(any(MEL.connect_material_expressions(uv, "", n, pin) for pin in ("UVs", "Coordinates")), f"{name} UV")
        return n

    def view_dot(self):
        """N.V: vertex normal against the pixel -> camera direction."""
        return self.op(unreal.MaterialExpressionDotProduct, self.node(unreal.MaterialExpressionVertexNormalWS),
                       self.node(unreal.MaterialExpressionCameraVectorWS))

    def out(self, n, prop, n_out=""):
        self.req(MEL.connect_material_property(n, n_out, prop), str(prop))

    def save(self, props, params):
        errors = MEL.recompile_material(self.mat)
        if errors:
            raise RuntimeError(f"{self.name}: compile errors {list(errors)}")
        unreal.EditorAssetLibrary.save_asset(self.path, only_if_is_dirty=False)
        reloaded = unreal.load_asset(self.path)
        for p in props:
            if MEL.get_material_property_input_node(reloaded, p) is None:
                raise RuntimeError(f"{self.name}: {p} unconnected after save")
        names = [str(p) for p in MEL.get_scalar_parameter_names(reloaded)] + \
                [str(p) for p in MEL.get_vector_parameter_names(reloaded)] + \
                [str(p) for p in MEL.get_texture_parameter_names(reloaded)]
        for want in params:
            if want not in names:
                raise RuntimeError(f"{self.name}: {want} param missing after save")
        log(f"built + saved {self.name} (params {names})")


# ---- M_Globe ----
g = G("M_Globe")
g.mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
uv = g.node(unreal.MaterialExpressionTextureCoordinate)
texel = g.scalar("BumpTexel", 1.0 / 2048.0)
zero = g.const(0.0)
uv_e = g.add(uv, g.op(unreal.MaterialExpressionAppendVector, texel, zero))   # east neighbour
uv_s = g.add(uv, g.op(unreal.MaterialExpressionAppendVector, zero, texel))   # south neighbour
h = g.tex("Bump", uv)
emboss = g.add(g.sub(h, g.tex("Bump", uv_e), "R", "R"), g.sub(h, g.tex("Bump", uv_s), "R", "R"))
shade = g.add(g.const(1.0), g.mul(emboss, g.scalar("BumpStrength", 4.0)))
limb = g.node(unreal.MaterialExpressionLinearInterpolate)
g.link(g.scalar("Limb", 0.6), "", limb, "A")
g.link(g.const(1.0), "", limb, "B")
g.link(g.sat(g.view_dot()), "", limb, "Alpha")
albedo = g.tex("Albedo", uv)
# sat(shade): the emboss only darkens (globe.gl's Phong never brightened past the map)
emissive = g.mul(g.mul(g.mul(albedo, g.sat(shade), "RGB"), limb), g.scalar("Brightness", 1.0))
g.out(emissive, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
g.save([unreal.MaterialProperty.MP_EMISSIVE_COLOR], ["Albedo", "Bump", "BumpTexel", "BumpStrength", "Limb", "Brightness"])


def translucent(name):
    t = G(name)
    t.mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    t.mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)
    t.mat.set_editor_property("use_translucency_vertex_fog", False)
    t.out(t.node(unreal.MaterialExpressionConstant3Vector, constant=unreal.LinearColor(0, 0, 0, 1)),
          unreal.MaterialProperty.MP_BASE_COLOR)
    return t


# ---- M_GlobeLine ----
g = translucent("M_GlobeLine")
g.mat.set_editor_property("two_sided", True)
vc = g.node(unreal.MaterialExpressionVertexColor)
g.out(vc, unreal.MaterialProperty.MP_EMISSIVE_COLOR)
g.out(g.mul(vc, g.scalar("Opacity", 1.0), "A"), unreal.MaterialProperty.MP_OPACITY)   # VertexColor's alpha is its own "A" pin (apply_road_material.py)
dist = g.op(unreal.MaterialExpressionDistance, g.node(unreal.MaterialExpressionWorldPosition),
            g.node(unreal.MaterialExpressionCameraPositionWS))
wpo = g.mul(g.node(unreal.MaterialExpressionVertexNormalWS), g.mul(dist, g.scalar("Width", 0.0005)))
g.out(wpo, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
g.save([unreal.MaterialProperty.MP_EMISSIVE_COLOR, unreal.MaterialProperty.MP_OPACITY,
        unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET], ["Width", "Opacity"])

# ---- M_GlobeAtmo ----
g = translucent("M_GlobeAtmo")
color = g.node(unreal.MaterialExpressionVectorParameter, parameter_name="Color",
               default_value=unreal.LinearColor(0.246, 0.296, 0.392, 1.0))   # #8894a8 linear
g.out(g.mul(color, g.scalar("Strength", 1.0), "RGB"), unreal.MaterialProperty.MP_EMISSIVE_COLOR)
rim = g.op(unreal.MaterialExpressionDivide, g.sat(g.view_dot()), g.scalar("Edge", 0.49))
power = g.node(unreal.MaterialExpressionPower)
g.link(g.sat(rim), "", power, "Base")
g.link(g.scalar("Power", 2.0), "", power, "Exp")   # "Exp", not "Exponent" (apply_ground_material.py)
g.out(g.sat(g.mul(power, g.scalar("Strength", 1.0))), unreal.MaterialProperty.MP_OPACITY)
g.save([unreal.MaterialProperty.MP_EMISSIVE_COLOR, unreal.MaterialProperty.MP_OPACITY],
       ["Color", "Edge", "Power", "Strength"])

# ---- M_GlobeDrape (2026-10-14) ----
g = translucent("M_GlobeDrape")
albedo = g.tex("Albedo", g.node(unreal.MaterialExpressionTextureCoordinate))
g.out(g.mul(albedo, g.scalar("Brightness", 1.0), "RGB"), unreal.MaterialProperty.MP_EMISSIVE_COLOR)
g.out(g.scalar("Opacity", 1.0), unreal.MaterialProperty.MP_OPACITY)
g.save([unreal.MaterialProperty.MP_EMISSIVE_COLOR, unreal.MaterialProperty.MP_OPACITY],
       ["Albedo", "Brightness", "Opacity"])
