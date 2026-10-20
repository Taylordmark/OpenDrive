"""build_track.py (2026-10-13): gate placement, the custom-road spline,
custom-road end joins / loop closure, and automatic bridges/tunnels.

Run:  python pipeline/tests/test_build_track.py"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_track as bt

# gates: start at 200, every 300, no sliver sector, finish at the end
s = bt.place_gates(1000.0, 300.0, loop=False, start=200.0)
assert np.allclose(s, [200, 500, 800, 1000]), s
s = bt.place_gates(900.0, 300.0, loop=False, start=200.0)
assert np.allclose(s, [200, 500, 900]), s           # 800 dropped: 100 m sliver before the finish
s = bt.place_gates(1000.0, 300.0, loop=True, start=200.0)
assert np.allclose(s, [200, 500, 800, 1200]), s     # finish = start line again
assert np.isclose(s[-1] % 1000.0, s[0])
assert bt.place_gates(400.0, 300.0, loop=False, start=200.0)[0] == 100.0   # short track: start at L/4

# spline passes through every click, roughly `step` apart
p = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 150.0]])
c = bt.catmull_rom(p, 10.0, closed=False)
for q in p:
    assert np.hypot(*(c - q).T).min() < 1e-6, q
gaps = np.hypot(*np.diff(c, axis=0).T)
assert gaps.max() < 12.0 and gaps.min() > 3.0, (gaps.min(), gaps.max())
cc = bt.catmull_rom(p, 10.0, closed=True)
assert np.hypot(*(cc[0] - cc[-1])) < 12.0           # wraps back towards the start

# custom roads: area filter, end joins a fetched node, loop shares its first id
with tempfile.TemporaryDirectory() as d:
    bt.TRACKS = Path(d)
    line = lambda coords, **props: json.dumps({"type": "Feature", "properties": props,
        "geometry": {"type": "LineString", "coordinates": coords}})
    (bt.TRACKS / "a.geojson").write_text(line([[-106.0, 39.5], [-106.0, 39.505]], area="x", road=True))
    (bt.TRACKS / "b.geojson").write_text(line(
        [[-106.01, 39.5], [-106.01, 39.501], [-106.011, 39.501], [-106.01, 39.5]], area="x", road=True))
    (bt.TRACKS / "c.geojson").write_text(line([[-106.0, 39.5], [-106.0, 39.6]], area="y", road=True))
    (bt.TRACKS / "d.geojson").write_text(line([[-106.0, 39.5], [-106.0, 39.6]], area="x"))
    bt.ground_m = lambda ll: np.zeros(len(ll))      # flat: no DEM fetch, no structures
    real = [{"type": "node", "id": 77, "lat": 39.50005, "lon": -106.0}]   # ~6 m from a's start
    nodes, ways = bt.custom_road_elements("x", real)
    assert len(ways) == 2, ways                      # c (other area), d (route only) skipped
    a, b = ways
    assert a["nodes"][0] == 77 and a["nodes"][-1] != 77
    assert b["nodes"][0] == b["nodes"][-1] and b["nodes"][0] != 77
    ids = {n["id"] for n in nodes}
    assert all(i in ids for i in a["nodes"][1:] + b["nodes"])
    assert a["tags"]["highway"] == "secondary"

# structures: span cost is length not height -> one level deck over a gorge,
# a tunnel through a ridge, ground for anything drivable (<= 22%) or noisy
x = np.arange(0, 6000, 10.0)
def metres(g):
    lab, h = bt.structure_labels(g)
    return [int((lab == k).sum()) * 10 for k in (bt.GROUND, bt.BRIDGE, bt.TUNNEL)], lab, h
m, lab, h = metres(np.where(np.abs(x - 3000) < 1000, -500.0, 0.0))   # 2 km wide, 500 m deep
assert 1900 <= m[1] <= 2050 and m[2] == 0, m
assert np.ptp(h[lab == bt.BRIDGE]) < 1.0, np.ptp(h[lab == bt.BRIDGE])   # level, not diving in
m, _, _ = metres(600 * np.exp(-((x - 3000) / 600) ** 2))             # 600 m ridge
assert m[1] == 0 and 1000 <= m[2] <= 2500, m
for g in (np.clip((x - 1500) * 0.18, 0, 180), 20 * np.sin(x / 250),
          np.random.default_rng(0).normal(0, 3, len(x))):            # 18% pitch, hills, DEM noise
    assert metres(g)[0][0] == 6000, metres(g)[0]

# split: one way per run, boundary nodes shared, structure tags on each
lab = np.array([0, 0, 1, 1, 1, 0, 2, 2, 0])
ways = bt.split_by_structure(list(range(9)), lab, {"highway": "secondary"}, 100)
assert [w["nodes"] for w in ways] == [[0, 1], [1, 2, 3, 4, 5], [5, 6, 7, 8]], [w["nodes"] for w in ways]
assert ways[1]["tags"]["bridge"] == "yes" and "bridge" not in ways[0]["tags"] and ways[2]["tags"]["tunnel"] == "yes"
assert [w["id"] for w in ways] == [100, 101, 102]
print("ok")
