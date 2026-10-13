"""build_track.py (2026-10-13): gate placement, the custom-road spline, and
custom-road end joins / loop closure.

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
    real = [{"type": "node", "id": 77, "lat": 39.50005, "lon": -106.0}]   # ~6 m from a's start
    nodes, ways = bt.custom_road_elements("x", real)
    assert len(ways) == 2, ways                      # c (other area), d (route only) skipped
    a, b = ways
    assert a["nodes"][0] == 77 and a["nodes"][-1] != 77
    assert b["nodes"][0] == b["nodes"][-1] and b["nodes"][0] != 77
    ids = {n["id"] for n in nodes}
    assert all(i in ids for i in a["nodes"][1:] + b["nodes"])
    assert a["tags"]["highway"] == "secondary"
print("ok")
