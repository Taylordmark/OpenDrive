"""Realistic W-beam guardrail post (PLAN-VISUALS guardrails track):
a W6x9 steel I-beam post, a blockout spacer between it and the rail, and a
clip-on delineator standing on the rail top. AODGuardrailManager places it
in Realistic only, in place of the stylized /Engine/BasicShapes/Cylinder.

Local frame (cm, Mesh conventions from make_furniture_meshes.py): origin =
the rail seat (the run's station point), +X = outward, away from the road
(the manager yaws +X onto the rail's outward direction), Y along the rail,
Z up. The rail ribbon (AODGuardrailManager::WBeamProfile) occupies x <= 0:
a vertical plate up to z 58 at x 0, bulging to x -14 at z 74, lip at
(-6, 92). The spacer sits against the plate's back, the post behind it.

Slots: "steel" (post + spacer; the manager overrides slot 0 with
M_Guardrail_WBeam_real) and "reflector" (M_GuardrailReflector_real).
UV0: V = height above the seat / POST_V_CM, the same rough "0 at the
ground" gradient the rail's own V gives the shared material's grime.

Outputs: data/guardrails/obj/SM_GuardrailPost_real.obj (+ .mtl)
Usage:   python make_guardrail_post_mesh.py
"""

from pathlib import Path

from make_furniture_meshes import Mesh

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "guardrails" / "obj"

POST_Z = (-75.0, 105.0)       # 1.8 m, ~75 cm of it below the seat
DEPTH, FLANGE_W = 15.0, 10.0  # W6x9: 5.9 in deep, 3.94 in flanges
FLANGE_T, WEB_T = 0.8, 0.6
SPACER_X = (0.0, 15.0)        # blockout: rail back -> post front flange
SPACER_Y, SPACER_Z = 7.5, (40.0, 76.0)
POST_X0 = SPACER_X[1]
REFLECTOR = dict(x=(-9.0, 1.0), y=0.3, z=(90.0, 102.0))   # stands on the rail lip (-6, 92)
POST_V_CM = 175.0


def post():
    m = Mesh()
    x1 = POST_X0 + DEPTH
    hw = FLANGE_W / 2
    m.box(POST_X0, POST_X0 + FLANGE_T, -hw, hw, *POST_Z, "steel")          # road-side flange
    m.box(x1 - FLANGE_T, x1, -hw, hw, *POST_Z, "steel")                     # back flange
    m.box(POST_X0 + FLANGE_T, x1 - FLANGE_T, -WEB_T / 2, WEB_T / 2, *POST_Z, "steel")   # web
    m.box(*SPACER_X, -SPACER_Y, SPACER_Y, *SPACER_Z, "steel")
    r = REFLECTOR
    m.box(*r["x"], -r["y"], r["y"], *r["z"], "reflector")                   # faces +-Y: oncoming traffic
    return m


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mesh = post()
    zs = [p[2] for t in mesh.tris for p in t[:3]]
    assert min(zs) == POST_Z[0] and max(zs) == POST_Z[1], (min(zs), max(zs))
    assert {t[3] for t in mesh.tris} == {"steel", "reflector"}
    assert [t[3] for t in mesh.tris].index("reflector") > 0   # "steel" is slot 0
    mesh.save("SM_GuardrailPost_real", OUT_DIR, uv=lambda p: (0.0, max(p[2], 0.0) / POST_V_CM))
