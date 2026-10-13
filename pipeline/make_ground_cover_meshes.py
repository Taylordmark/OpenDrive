"""Realistic ground cover card meshes (PLAN-VISUALS.md Track K), generated -
no downloads. Writes gc_tall_grass_real.obj and gc_weed_real.obj next to the
flora OBJs; unreal/build_ground_cover_real.py imports them as
/Game/OpenDrive/Realistic/GroundCover/SM_GCTallGrass_real / SM_GCWeed_real.

Same card technique and atlas (T_FoliageAtlas_real, flora_common cells) as
the Track F flora, through make_tree_meshes' helpers, single material slot,
base on the origin (ODGroundCover reads bounds Min.Z). ~20 tris each: these
are drawn by the tens of thousands, so one tier for every Model Detail level
(detail scales their density instead, see ODGroundCover.cpp).

    python pipeline/make_ground_cover_meshes.py
"""

import math

import numpy as np

from make_tree_meshes import Mesh, REAL_TEX, _dir, real_card, real_ground_cards, real_normals


def weed(seed=2121):
    """Broadleaf forb: a low rosette of aspen-leaf sprays lying almost flat,
    plus two or three willow-leaf stalks standing up through it (~40 cm)."""
    cr = np.random.default_rng(seed)
    m = Mesh()
    for i in range(6):
        a = 2 * math.pi * (i + cr.uniform(-0.3, 0.3)) / 6
        up = _dir(a, math.radians(cr.uniform(14, 32)))
        real_card(m, up * 4 + [0, 0, 3], up, cr.uniform(-0.5, 0.5), cr.uniform(18, 24),
                  cr.uniform(20, 28), "aspen_leaf", _dir(a, 0) + [0, 0, 1.5])
    for _ in range(3):
        a = cr.uniform(0, 2 * math.pi)
        up = _dir(a, math.radians(cr.uniform(68, 82)))
        real_card(m, _dir(a, 0) * cr.uniform(0, 6) + up * 5, up, cr.uniform(-math.pi, math.pi),
                  cr.uniform(16, 22), cr.uniform(30, 44), "willow_leaf", _dir(a, 0))
    lift = np.array([0.0, 0.0, max(0.0, -min(p[2] for t in m.tris for p in t[1]))])
    m.tris = [(mt, tuple(p + lift for p in pts), uv, ns) for mt, pts, uv, ns in m.tris]
    real_normals(m, lambda p: p * [1, 1, 0] + [0, 0, 25])
    m.save("gc_weed_real", {"foliage": REAL_TEX["foliage"]})


def main():
    # taller, fuller clump than grass_tuft (35-55 cm): 60-95 cm meadow grass
    real_ground_cards("gc_tall_grass", "", 3131, n=9, r=16, pitch=(68, 86), h=(60, 95),
                      w=(30, 46), cell="grass_blade", crown_z=80)
    weed()


if __name__ == "__main__":
    main()
