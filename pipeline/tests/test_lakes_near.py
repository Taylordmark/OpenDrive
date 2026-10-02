"""lakes_near (2026-10-01) keeps exactly the lakes the old per-lake
`bb.any()` loop kept, in the same order.

Run:  python pipeline/tests/test_lakes_near.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_terrain_mesh_tiles import lakes_near

rng = np.random.default_rng(0)
lakes = []
for _ in range(500):
    x0, y0 = rng.uniform(-1e6, 1e6, 2)
    w, h = rng.uniform(1e2, 5e4, 2)
    lakes.append({"bbox": (x0, x0 + w, y0, y0 + h)})
margin = 1500.0
for _ in range(200):
    cx, cy = rng.uniform(-1e6, 1e6, 2)
    x = cx + np.cumsum(rng.normal(0, 500, 300))
    y = cy + np.cumsum(rng.normal(0, 500, 300))
    brute = [l for l in lakes if ((x >= l["bbox"][0] - margin) & (x <= l["bbox"][1] + margin) &
                                  (y >= l["bbox"][2] - margin) & (y <= l["bbox"][3] + margin)).any()]
    near = lakes_near(lakes, x, y, margin)
    assert all(any(b is n for n in near) for b in brute)   # never drops a hit
    assert [id(l) for l in near] == sorted((id(l) for l in near), key=[id(m) for m in lakes].index)
assert lakes_near(lakes, np.array([]), np.array([]), margin) == []
assert lakes_near([], np.array([0.0]), np.array([0.0]), margin) == []
print("ok")
