"""carve_grid_m's nearest station (2026-10-15, cKDTree): same index as the
brute-force argmin it replaced, including exact ties between coincident
stations (lowest index wins).

Run:  python pipeline/tests/test_carve_nearest.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_terrain_mesh_tiles import _nearest_station

rng = np.random.default_rng(3)
se, sn = rng.integers(0, 50, 400).astype(float), rng.integers(0, 50, 400).astype(float)
se[200:], sn[200:] = se[:200], sn[:200]          # every station has a twin
gx, gy = rng.integers(0, 50, 5000).astype(float), rng.integers(0, 50, 5000).astype(float)
d = np.hypot(gx[:, None] - se, gy[:, None] - sn)
idx, dist = _nearest_station(gx, gy, se, sn)
assert np.array_equal(idx, np.argmin(d, axis=1)) and np.array_equal(dist, d.min(axis=1))
print("ok: kd nearest == brute argmin, ties included")
