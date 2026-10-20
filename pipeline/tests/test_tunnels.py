"""build_tunnels: closed bore/headwall solids, road clearance, and the portal
terrain hole (python tests/test_tunnels.py)."""
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import build_tunnels as bt

# straight 200 m tunnel along +x, pavement at z=0, 10 m bore (w=500 cm)
K = 41
x = np.linspace(0.0, 20000.0, K)
run = {"name": "t", "c": np.c_[x, np.zeros(K)], "t": np.tile([1.0, 0.0], (K, 1)),
       "r": np.tile([0.0, 1.0], (K, 1)), "z": np.zeros(K), "slope": np.zeros(K),
       "w": np.full(K, 500.0)}

# closed, outward-wound solids: every directed edge has its reverse
for v, uv, t in bt.bore_chunks(run) + bt.headwalls(run):
    e = Counter(map(tuple, np.r_[t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]))
    assert all(e[(b, a)] == n for (a, b), n in e.items()), "open mesh"
    assert bt.ue_volume(v, t) > 0 and len(uv) == len(v)

# clearance over the pavement: walls >= WALL_H, crown = WALL_H + RISE
prof = bt.bore_profile(500.0)
assert prof[2:, 1].min() >= bt.WALL_H_CM and np.isclose(prof[2:, 1].max(), bt.WALL_H_CM + bt.ARCH_RISE_CM)
v = bt.bore_chunks(run)[0][0]
inner = v[: len(v) // 2]
assert np.isclose(inner[:, 2].max(), bt.WALL_H_CM + bt.ARCH_RISE_CM)
assert inner[np.abs(inner[:, 1]) < 1, 2].max() >= bt.WALL_H_CM + bt.ARCH_RISE_CM - 1   # crown over the road

# hill: road-level at the portals, 30 m of rock over the middle
gx, gy = np.meshgrid(np.arange(-4000.0, 24001.0, 200.0), np.arange(-2000.0, 2001.0, 200.0))
gz = 3000.0 * np.sin(np.pi * np.clip(gx, 0, 20000) / 20000.0)
verts = np.c_[gx.ravel(), gy.ravel(), gz.ravel()].astype(np.float32)
nx = gx.shape[1]
i = (np.arange(gx.shape[0] - 1)[:, None] * nx + np.arange(nx - 1)).ravel()
tris = np.r_[np.c_[i, i + nx, i + 1], np.c_[i + 1, i + nx, i + nx + 1]]

gone = bt.hole_mask(verts, tris, [run])
cx = verts[tris][:, :, 0].mean(axis=1)
assert gone[cx < 1000].any() and gone[cx > 19000].any(), "portals not opened"
assert not gone[(cx > 5000) & (cx < 15000)].any(), "mountain over the middle removed"
assert not gone[(cx < -1000) | (cx > 21000)].any(), "approach ground removed"

# the cut terrain: nothing pokes into the solid bore (pad 0, 25 cm samples),
# and the hole hugs the shell (split triangles, not whole 2 m cells)
cv, cuv, ct = bt.cut_holes(verts, verts[:, :2].copy(), tris, [run])
assert len(cuv) == len(cv) and ct.max() < len(cv)
for ids, pts in bt.tri_samples(cv, ct, 25.0):
    assert not bt.inside_bore(pts.reshape(-1, 3), [run], pad_cm=0.0).any(), "terrain inside bore"
area = lambda v, t: np.abs(np.cross(v[t[:, 1]] - v[t[:, 0]], v[t[:, 2]] - v[t[:, 0]])[:, 2]).sum() / 2
assert area(cv, ct) > area(verts, tris[~gone]), "split didn't give any ground back"

# lights face down
lv, _, lt = bt.light_strips(run)
assert (np.cross(lv[lt[:, 2]] - lv[lt[:, 0]], lv[lt[:, 1]] - lv[lt[:, 0]])[:, 2] < 0).all()

# culverts: a stream along +x crossing a 2-lane road along y at x=100 m,
# road 3 m over the water; gap 90..110 m spread over two resampled segments
import json, tempfile
from test_areas import ORIGIN_HEIGHT_M
with tempfile.TemporaryDirectory() as td:
    yy = np.arange(-50.0, 51.0, 5.0)
    st = {"segments": [{"east_m": [100.0] * len(yy), "north_m": yy.tolist(),
                        "elev_m": [ORIGIN_HEIGHT_M + 3.0] * len(yy), "highway_class": "residential"}]}
    ch = {"east_m": [0.0, 95.0, 105.0, 200.0], "north_m": [0.0] * 4,
          "water_elev_m": [ORIGIN_HEIGHT_M] * 4, "half_width_m": 1.75,
          "seg_gaps": [[0, [[90 / 95, 1.0]]], [1, [[0.0, 1.0]]], [2, [[0.0, 5 / 95]]]]}
    (Path(td) / "st.json").write_text(json.dumps(st))
    (Path(td) / "ww.json").write_text(json.dumps({"chain_summaries": [ch]}))
    sites, skipped = bt.culvert_sites(Path(td) / "ww.json", Path(td) / "st.json")
assert len(sites) == 2 and skipped == 0, (len(sites), skipped)
(p0, u0, z0, r0), (p1, u1, z1, r1) = sorted(sites, key=lambda s: s[0][0])
assert p0[0] < 9500 and p1[0] > 10500 and u0[0] > 0 and u1[0] < 0   # both face the road
for p_, u_, z_, r_ in sites:
    for v, uv, t in bt.culvert_mouth(p_, u_, z_, r_):
        e = Counter(map(tuple, np.r_[t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]]))
        assert all(e[(b, a)] == n for (a, b), n in e.items()), "open culvert mesh"
        assert bt.ue_volume(v, t) > 0
        assert v[:, 2].max() <= 300.0 + 10.0 - bt.CULVERT_ROAD_COVER_M * 100.0 + 1e-3   # under the road
print("ok")
