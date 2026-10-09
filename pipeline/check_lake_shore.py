"""Lake shore height check (2026-10-09, Taylor, Summit: a grey water plane
ringed by a thick black band - "part of a lake?"). apply_water_depression's
exterior blend used to aim 2 m BELOW the water plane, so every lake sat in a
dry trench. Runs it on a flat synthetic grid with a round lake and checks the
bank never dips below the water outside the ring, and the bed is under the
water inside it.

Usage:  python pipeline/check_lake_shore.py
"""
import numpy as np

import build_terrain_mesh_tiles as T

R_CM, Z_CM, GROUND_CM = 10000.0, 0.0, 300.0   # 100 m lake, ground 3 m above water


def main():
    a = np.linspace(0, 2 * np.pi, 128, endpoint=False)
    lake = {"ring_x": R_CM * np.cos(a), "ring_y": R_CM * np.sin(a), "z_cm": Z_CM,
            "bbox": (-R_CM, R_CM, -R_CM, R_CM)}
    g = np.arange(-20000.0, 20001.0, 200.0)
    gx, gy = np.meshgrid(g, g)
    z = T.apply_water_depression(gx, gy, np.full(gx.shape, GROUND_CM), [lake])
    r = np.hypot(gx, gy)
    ring_cm = R_CM * np.cos(np.pi / 128)   # polygon inradius
    out = r > R_CM + 1
    inn = r < ring_cm - 1
    print(f"bank min {z[out].min() - Z_CM:+.0f} cm, bed max {z[inn].max() - Z_CM:+.0f} cm vs water")
    assert z[out].min() >= Z_CM, "bank outside the ring must not dip below the water (dry trench)"
    assert z[inn].max() < Z_CM, "bed inside the ring must sit under the water"
    assert z[r > R_CM + T.WATER_SHORE_BLEND_M * 100 + 1].min() == GROUND_CM, "natural ground past the blend"
    print("PASS: lake bank meets the water, no dry trench")


if __name__ == "__main__":
    main()
