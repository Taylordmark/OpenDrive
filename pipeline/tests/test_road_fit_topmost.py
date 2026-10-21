"""verify_road_fit._sample_topmost (2026-10-08): when two road tiles both
cover a point (a ribbon's skirt in one, the pavement in a neighbour), road
sampling keeps the TOPMOST hit, not whichever tile is tried first.

Run:  python pipeline/tests/test_road_fit_topmost.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import verify_road_fit as vrf


def _quad(z):
    # flat square covering x, y in [0, 100] cm at height z
    pos = np.array([[0, 0, z], [100, 0, z], [100, 100, z], [0, 100, z]], dtype=np.float64)
    return {"positions": pos, "triangles": np.array([[0, 1, 2], [0, 2, 3]])}


def test_topmost_beats_first_hit():
    tiles = {(0, 0): _quad(10.0), (1, 0): _quad(50.0)}   # own tile low, neighbour high
    loader = lambda tx, ty: tiles.get((tx, ty))
    tx = ty = np.zeros(2, dtype=int)
    x, y = np.array([50.0, 500.0]), np.array([50.0, 50.0])   # second point: no tile covers it
    first = vrf._sample_serial(loader, tx, ty, x, y, ring=1)
    top = vrf._sample_serial(loader, tx, ty, x, y, ring=1, topmost=True)
    assert first[0] == 10.0 and top[0] == 50.0, (first, top)
    assert np.isnan(first[1]) and np.isnan(top[1]), (first, top)


if __name__ == "__main__":
    test_topmost_beats_first_hit()
    print("ok: road sampling keeps the topmost tile hit")
