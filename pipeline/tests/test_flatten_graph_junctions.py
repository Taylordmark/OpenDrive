"""flatten_graph_junctions (2026-10-08): a junction pull adds at most
JUNCTION_PULL_MAX_GRADE to a road, and two close junctions at different
heights blend instead of jumping (seg377: 4.3 m between two stations).

Run:  python pipeline/tests/test_flatten_graph_junctions.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from road_mesh_common import JUNCTION_PULL_MAX_GRADE, flatten_graph_junctions

BOX = 8.5
s = np.arange(0.0, 400.0, 5.0)
n = len(s)
z = np.full(2 * n, 100.0)                     # two roads; road 1 only joins the nodes
OFF, SS = [0, n, 2 * n], [s, s]

# one junction pulling a flat road up 8 m: box on target, added grade capped
f, w = flatten_graph_junctions(z, OFF, SS, {"a": [(0, 200.0), (1, 200.0)]}, {"a": 108.0}, BOX)
f, w = f[:n], w[:n]
assert np.allclose(f[np.abs(s - 200.0) <= BOX], 108.0)
assert np.abs(np.diff(f) / 5.0).max() <= JUNCTION_PULL_MAX_GRADE + 1e-9, np.abs(np.diff(f)).max()
assert w[np.abs(s - 200.0) <= BOX].max() == 0.0 and w[np.abs(s - 200.0) > 28.5].min() == 1.0

# two junctions 30 m apart, targets 4.6 m apart: no one-station jump
inc = {"a": [(0, 185.0), (1, 185.0)], "b": [(0, 215.0), (1, 215.0)]}
f = flatten_graph_junctions(z, OFF, SS, inc, {"a": 102.0, "b": 106.6}, BOX)[0][:n]
step = np.abs(np.diff(f)).max()
assert step < 1.5, step                       # was 4.6 m in one 5 m step
print(f"ok: added grade capped, close junctions max step {step:.2f} m")
