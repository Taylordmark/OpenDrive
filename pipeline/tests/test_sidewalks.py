"""build_sidewalks: closed outward solids, and a walk that breaks at a crossing road.

Run:  python pipeline/tests/test_sidewalks.py
"""

import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_sidewalks import WALK_W_M, clear_of_roads, prism, runs_of
from build_tunnels import ue_volume


def test_prism_closed_and_outward():
    x = np.arange(0, 50.0, 5.0) * 100.0
    c = np.c_[x, np.zeros_like(x)]
    r = np.tile([0.0, 1.0], (len(x), 1))
    prof = np.array([(0, -25), (0, 15), (180, 15), (180, -25)], float)
    v, uv, t = prism(c, r, np.zeros(len(x)), prof)
    assert ue_volume(v.astype(float), t) > 0
    edges = np.sort(np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]), axis=1)
    _, cnt = np.unique(edges, axis=0, return_counts=True)
    assert (cnt == 2).all()                                   # watertight
    assert abs(ue_volume(v.astype(float), t) - 45 * 180 * 40 * 100) < 1e3   # 45 m x 1.8 m x 0.4 m (cm^3)


def test_walk_breaks_at_a_crossing_road():
    s = np.arange(-100, 101, 5.0)
    east = np.r_[s, np.zeros_like(s)]                         # E-W street + N-S cross street
    north = np.r_[np.zeros_like(s), s]
    hw = np.full(len(east), 4.0)
    tree = cKDTree(np.c_[east, north])
    own = np.arange(len(s))
    o = hw[own] + WALK_W_M                                    # outer edge of the north walk
    ok = clear_of_roads(east[own], north[own] + o, tree, hw, own)
    assert not ok[np.abs(s) <= 4].any() and ok[np.abs(s) >= 10].all()
    assert len(runs_of(ok)) == 2


if __name__ == "__main__":
    test_prism_closed_and_outward()
    test_walk_breaks_at_a_crossing_road()
    print("ok")
