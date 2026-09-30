"""build_terrain_mesh_tiles.pavement_ceiling (2026-09-30): a lower road next
to a higher one must never end up under a terrain triangle, and a road on a
grade must not sink its own cap to the cell's lowest point.

Run:  python pipeline/tests/test_pavement_ceiling.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_terrain_mesh_tiles import TERRAIN_TILE_SIZE_CM as T, pavement_ceiling

N, M = 5, 0.15
X1 = T                                              # 5x5 grid over one tile, cell = T/4
CELL = T / (N - 1)


def bucket(pts):
    """pts: (x cm, y cm, z m, tx, ty, grade m/cm) rows -> tile (0,0) bucket."""
    a = np.array(pts, dtype=np.float64)
    return {0: tuple(a[:, c] for c in range(6))}


def tri_height(cap, x, y):
    """Height of the grid surface at (x, y), split like the far-tile grid."""
    fi, fj = x / CELL, y / CELL
    i, j = int(fi), int(fj)
    u, v = fi - i, fj - j
    z00, z10, z01, z11 = cap[j, i], cap[j, i + 1], cap[j + 1, i], cap[j + 1, i + 1]
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


if __name__ == "__main__":
    test_lower_road_wins_its_cell()
    test_grade_line_reproduced_at_probe()
    test_probe_in_neighbor_caps_shared_edge()
    print("ok")
