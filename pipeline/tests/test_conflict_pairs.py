"""detect_elevation_conflict_pairs (2026-10-15, array math): same pairs in
the same order as the per-station bucket loop it replaced, and the
classify_wall_stations disk cache returns what it computed.

Run:  python pipeline/tests/test_conflict_pairs.py"""
import math
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import road_mesh_common as rmc


def reference_pairs(east, north, elev, seg, sta, radius=150.0, grade=0.15, gap=1.0, chain=8):
    """The pre-2026-10-15 loop, verbatim in effect."""
    x, y = east * 100.0, -north * 100.0
    cx, cy = np.floor(x / (radius * 100)).astype(int), np.floor(y / (radius * 100)).astype(int)
    buckets = defaultdict(list)
    for i in range(len(x)):
        buckets[(cx[i], cy[i])].append(i)
    out = []
    for i in range(len(x)):
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((cx[i] + dx, cy[i] + dy), ()):
                    if j <= i or (seg[i] == seg[j] and abs(sta[i] - sta[j]) <= chain):
                        continue
                    d = math.hypot(x[j] - x[i], y[j] - y[i]) / 100.0
                    if 0.01 < d <= radius and abs(elev[j] - elev[i]) > grade * d + gap:
                        out.append((i, j))
    return out


rng = np.random.default_rng(7)
n = 3000
east, north = rng.uniform(-600, 600, n), rng.uniform(-600, 600, n)
elev = rng.uniform(0, 60, n)
seg = rng.integers(0, 40, n)
sta = rng.integers(0, 30, n)
got = list(zip(*(a.tolist() for a in rmc.detect_elevation_conflict_pairs(east, north, elev, seg, sta))))
want = reference_pairs(east, north, elev, seg, sta)
assert got == want, (len(got), len(want))

names = [rng.choice(["", "A", "B"]) for _ in range(n)]
plain = rmc._classify_wall_stations(east, north, elev, seg, sta, names)
with tempfile.TemporaryDirectory() as d:
    rmc.WALL_CACHE_DIR, rmc.WALL_CACHE_MIN_STATIONS = Path(d), 0
    for _ in range(2):   # miss, then hit
        ex, wp = rmc.classify_wall_stations(east, north, elev, seg, sta, names)
        assert np.array_equal(ex, plain[0]) and wp == plain[1]
    assert ex.any() and len(list(Path(d).glob("*.npz"))) == 1
print(f"ok: {len(got)} pairs match the loop in order; cache round-trips {ex.sum()} excluded stations")
