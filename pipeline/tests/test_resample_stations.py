"""resample_stations (2026-10-08): the first and last stations sit on the
polyline's ends, steps stay ~spacing, a straight road stays straight, and a
closed loop closes - it shipped a 3-9 m unpaved seam before.

Run:  python pipeline/tests/test_resample_stations.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from road_mesh_common import resample_stations

SP = 5.0

for length in (12.0, 101.0, 103.0, 783.4):
    s, e, n = resample_stations(np.array([0.0, length]), np.array([0.0, 0.0]), SP)
    steps = np.diff(s)
    assert s[0] == 0.0 and abs(s[-1] - length) < 1e-9, (length, s[-1])
    assert np.allclose(steps[:-1], SP) and 0.5 * SP <= steps[-1] < 1.5 * SP, (length, steps[-1])
    assert np.allclose(n, 0.0) and abs(e[0]) < 1e-9 and abs(e[-1] - length) < 1e-9, length
    assert np.all(np.diff(e) > 0.4 * SP), length                   # straight, in order

# closed loop: a 100 m-radius circle, first node == last node
t = np.linspace(0.0, 2 * np.pi, 200)
s, e, n = resample_stations(100.0 * np.cos(t), 100.0 * np.sin(t), SP)
seam = np.hypot(e[-1] - e[0], n[-1] - n[0])
assert seam < 1e-6, seam
assert abs(np.hypot(e[0], n[0]) - 100.0) < 1e-6
print(f"ok: ends pinned, loop seam {seam:.1e} m")
