"""Sloped water ribbons (Deals Gap, 2026-10-21: flat 20 m ribbon quads stepped
~9 m apart down Calderwood dam's face read as white strips floating at
different heights, and the bank of each quad was pulled up to that quad's own
level, burying the next one down). Two consecutive quads down a 40% grade:
they must meet at one Z, and the terrain bed must stay under the water
everywhere inside them.

Run:  python pipeline/tests/test_ribbon_slope.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_terrain_mesh_tiles as T
from build_water_tiles import build_lake_mesh, tilt_ribbon_quad

GRADE = 0.4   # 8 m drop per 20 m quad


def quad(x0_m, x1_m):
    """Ribbon quad 10 m wide flowing +x, corners in _emit_quad's order."""
    xy = [(x0_m * 100, -500.0), (x0_m * 100, 500.0), (x1_m * 100, 500.0), (x1_m * 100, -500.0)]
    za, zb = -GRADE * x0_m * 100, -GRADE * x1_m * 100
    verts, _, tris, _ = build_lake_mesh(xy, (za + zb) / 2)
    tilt_ribbon_quad(verts, xy, za, zb)
    return verts, tris


def lake_of(verts):
    ring = verts[:4]
    return {"ring_x": ring[:, 0].astype(float), "ring_y": ring[:, 1].astype(float),
            "ring_z": ring[:, 2].astype(float), "z_cm": float(ring[0, 2]),
            "bbox": (ring[:, 0].min(), ring[:, 0].max(), ring[:, 1].min(), ring[:, 1].max())}


def main():
    (va, _), (vb, _) = quad(0, 20), quad(20, 40)
    end_a = va[np.isclose(va[:, 0], 2000), 2]
    start_b = vb[np.isclose(vb[:, 0], 2000), 2]
    assert np.allclose(end_a, start_b), f"quads step at their joint: {end_a} vs {start_b}"

    gx, gy = np.meshgrid(np.arange(-3000.0, 7001.0, 100.0), np.arange(-2000.0, 2001.0, 100.0))
    ground = -GRADE * gx + 300.0   # valley floor 3 m over the water line
    z = T.apply_water_depression(gx, gy, ground, [lake_of(va), lake_of(vb)])
    inside = (gx > 0) & (gx < 4000) & (np.abs(gy) < 400)
    over = z[inside] - (-GRADE * gx[inside])
    print(f"bed vs water inside the ribbon: max {over.max():+.0f} cm")
    assert over.max() < 0, "terrain covers the sloped ribbon"
    print("PASS: sloped ribbon quads meet, bed under water")


if __name__ == "__main__":
    main()
