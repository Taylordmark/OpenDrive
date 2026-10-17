"""Junction approach blend vs a bridge/tunnel inside the transition
(2026-10-15, Oahu (16152,-20771)): the blend must taper to 0 at the
structure, never lift the approach right up to a portal that keeps its grade.

Run:  python pipeline/tests/test_blend_structure.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_junction_pads import STATION_SPACING, TRANSITION_LEN_M, _blend_approaches_to_junction


def blend(struct_at=None):
    n = int(2 * TRANSITION_LEN_M / STATION_SPACING) + 1
    s = np.arange(n) * STATION_SPACING
    elev = np.zeros(n)
    east, north = s.copy(), np.zeros(n)
    structural = np.zeros(n, dtype=bool)
    if struct_at is not None:
        structural[struct_at:] = True
    edge = n // 2
    rg, re, rn = elev.copy(), east.copy(), north.copy()
    _blend_approaches_to_junction({0: {"edge": edge, "s": s[edge]}}, -1, 3.0, (s[edge], 0.0),
                                  {0: (0, n - 1)}, structural, s, elev, east, north, rg, re, rn)
    return edge, rg


def test_no_structure_unchanged():
    edge, rg = blend()
    assert abs(rg[edge] - 3.0) < 1e-9 and rg[0] == 0.0 and rg[-1] == 0.0


def test_tapers_to_structure():
    edge, rg = blend()
    portal = edge + 3
    _, rs = blend(struct_at=portal)
    assert abs(rs[edge] - 3.0) < 1e-9
    assert rg[portal - 1] > 2.0                   # old blend: ~2.6 m beside the portal
    assert rs[portal - 1] < rg[portal - 1] * 0.5  # new: tapered toward the portal's 0
    assert np.all(rs[portal:] == 0.0)
    assert np.all(rs[: edge] == rg[: edge])       # the open side is untouched


if __name__ == "__main__":
    test_no_structure_unchanged()
    test_tapers_to_structure()
    print("ok")
