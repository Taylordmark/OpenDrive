"""build_terrain_mesh_tiles.pavement_ceiling (2026-09-30): a lower road next
to a higher one must never end up under a terrain triangle, and a road on a
grade must not sink its own cap to the cell's lowest point. A banked road
(2026-10-02) stays above every capped triangle across its tilted pavement.

Run:  python pipeline/tests/test_pavement_ceiling.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_terrain_mesh_tiles import (NEAR_GRID_N, PAVEMENT_CEIL_LOW_EXT_M, PAVEMENT_CEIL_MARGIN_NEAR_M,
                                      TERRAIN_TILE_SIZE_CM as T, pavement_ceiling)
from road_mesh_common import MAX_BANK_RAD

N, M = 5, 0.15
X1 = T                                              # 5x5 grid over one tile, cell = T/4
CELL = T / (N - 1)


def bucket(pts):
    """pts: (x cm, y cm, z m, tx, ty, grade m/cm[, bank slope, offset m, half-width m])
    rows -> tile (0,0) bucket; unbanked when the bank fields are left off."""
    a = np.array([tuple(p) + (0.0, 0.0, 4.5)[len(p) - 6:] for p in pts], dtype=np.float64)
    return {0: tuple(a[:, c] for c in range(9))}


def tri_height(cap, x, y, cell=CELL, flip=False):
    """Height of the grid surface at (x, y), split like the far-tile grid
    (flip: the other diagonal - martini uses both)."""
    fi, fj = x / cell, y / cell
    i, j = int(fi), int(fj)
    u, v = fi - i, fj - j
    z00, z10, z01, z11 = cap[j, i], cap[j, i + 1], cap[j + 1, i], cap[j + 1, i + 1]
    if flip:
        z00, z10, z01, z11, u = z10, z00, z11, z01, 1 - u
    if u + v <= 1:                                 # (p, q, p+1)
        return z00 + u * (z10 - z00) + v * (z01 - z00)
    return z11 + (1 - u) * (z01 - z11) + (1 - v) * (z10 - z11)


def test_lower_road_wins_its_cell():
    lo = (1.2 * CELL, 1.5 * CELL, 100.0, 1.0, 0.0, 0.0)
    hi = (1.8 * CELL, 1.5 * CELL, 102.0, 1.0, 0.0, 0.0)   # 2 m up, same cell
    cap = pavement_ceiling(N, 0.0, X1, 0.0, X1, bucket([lo, hi]), 0, 0, M)
    assert tri_height(cap, lo[0], lo[1]) <= 100.0 - M + 1e-9
    assert np.isinf(cap[0, 0]) and np.isinf(cap[4, 4])


def test_grade_line_reproduced_at_probe():
    g = 0.10 / 100.0                                # 10% climb along +x, in m per cm
    x, y = 2.3 * CELL, 2.6 * CELL
    cap = pavement_ceiling(N, 0.0, X1, 0.0, X1, bucket([(x, y, 50.0, 1.0, 0.0, g)]), 0, 0, M)
    assert abs(tri_height(cap, x, y) - (50.0 - M)) < 1e-9     # no extra sink on the grade
    assert abs(cap[2, 3] - cap[2, 2] - g * CELL) < 1e-9


def test_probe_in_neighbor_caps_shared_edge():
    p = (-0.5 * CELL, 1.5 * CELL, 10.0, 0.0, 1.0, 0.0)     # half a cell west of this tile
    cap = pavement_ceiling(N, 0.0, X1, 0.0, X1, {-1_000_003: bucket([p])[0]}, 0, 0, M)
    assert cap[1, 0] == cap[2, 0] == 10.0 - M and np.isinf(cap[1, 1])


def test_banked_road_under_no_triangle():
    """Max bank, road crossing the 7.8 m near grid at assorted angles: no
    capped triangle may rise above the tilted pavement - nor sink more than
    the low-edge extension + margin just past the low edge."""
    n, hw, s = NEAR_GRID_N, 4.5, np.tan(MAX_BANK_RAD)
    cell = T / (n - 1)
    worst, deepest = -np.inf, 0.0
    for ang in np.radians([0, 17, 30, 45, 61, 90, 133]):
        t = np.array([np.cos(ang), np.sin(ang)]); r = np.array([-t[1], t[0]])   # UE right of travel
        c0 = np.array([T / 2, T / 2]) - 60_00 * t
        along = np.arange(0, 120.01, 2.5)
        fan = np.linspace(-hw, hw, 6)
        pts = [(*(c0 + 100 * (a * t + o * r)), 0.0, *t, 0.0, s, o, hw) for a in along for o in fan]
        cap = pavement_ceiling(n, 0.0, T, 0.0, T, bucket(pts), 0, 0, PAVEMENT_CEIL_MARGIN_NEAR_M)
        for a in np.arange(30, 90, 0.7):
            for o in np.arange(-hw, hw + 1e-9, 0.25):
                x, y = c0 + 100 * (a * t + o * r)
                for flip in (False, True):
                    worst = max(worst, tri_height(cap, x, y, cell, flip) - s * o)
            x, y = c0 + 100 * (a * t - (hw + 0.5) * r)          # lip tip, low side
            deepest = min(deepest, tri_height(cap, x, y, cell) - (-s * hw))
    assert worst <= 0.0, worst
    assert deepest >= -(s * (PAVEMENT_CEIL_LOW_EXT_M + 0.5) + PAVEMENT_CEIL_MARGIN_NEAR_M) - 1e-6, deepest
    print(f"banked: worst triangle {worst:+.3f} m vs pavement, deepest at low lip {deepest:+.3f} m vs edge")


if __name__ == "__main__":
    test_banked_road_under_no_triangle()
    test_lower_road_wins_its_cell()
    test_grade_line_reproduced_at_probe()
    test_probe_in_neighbor_caps_shared_edge()
    print("ok")
