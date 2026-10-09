"""match_sidecar (2026-10-08): two road ends sharing one XY get the two
sidecar entries, not both the same one - the second entry kept its
pre-pad z and verify_road_fit read it as a 3 m poke-through.

Run:  python pipeline/tests/test_match_sidecar.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_junction_pads import match_sidecar

kx = np.array([0.0] + [500.0] * 5 + [900.0])   # five road ends at one node
ky = np.zeros(7)
px = np.array([0.0] + [500.0] * 5 + [900.0, 2000.0])   # last point has no entry
py = np.array([0.0, 0.0, 1.0, 0.0, 2.0, 0.0, 0.0, 0.0])
j = match_sidecar(kx, ky, px, py)
assert j.tolist() == [0, 1, 2, 3, 4, 5, 6, -1], j
print("ok: coincident ends matched one-to-one")
