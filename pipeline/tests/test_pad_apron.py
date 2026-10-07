"""Pad apron: every pad ends in a ring PAD_APRON_OUT_M out and PAD_APRON_DROP_M
down (no bare floating edge), the mesh stays manifold, and the host bridge
lands on the apron ring only (2026-10-06).

Run:  python pipeline/tests/test_pad_apron.py"""
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_junction_pads import (PAD_APRON_DROP_M, PAD_APRON_OUT_M, build_pad_chunk_ellipse,
                                        merge_chunk_into)

for kw in ({}, {"z_fn": lambda x, y: 50.0 + 0.05 * x}):      # plain fan, molded rings
    pad = build_pad_chunk_ellipse("p", (0.0, 0.0, 10.0, 10.0, 0.0), 50.0, 0.0, sides=16, **kw)
    V = np.array(pad["vertices"]).reshape(-1, 3) / 100.0
    T = np.array(pad["triangles"]).reshape(-1, 3)
    rs = pad["ring_start"]
    assert len(V) - rs == 16
    r_edge, r_apron = np.hypot(*V[rs - 16:rs, :2].T), np.hypot(*V[rs:, :2].T)
    assert np.allclose(r_apron - r_edge, PAD_APRON_OUT_M, atol=0.01)
    assert np.allclose(V[rs - 16:rs, 2] - V[rs:, 2], PAD_APRON_DROP_M, atol=0.01)
    edges = Counter(tuple(sorted(e)) for t in T for e in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])))
    border = {e for e, c in edges.items() if c == 1}
    assert max(edges.values()) == 2 and all(a >= rs and b >= rs for a, b in border)

    host = {"vertices": [800.0, 0.0, 0.0, 1200.0, 0.0, 0.0, 1200.0, 400.0, 0.0],
            "uvs": [0.0] * 6, "uv1": [0.0] * 6, "triangles": [0, 1, 2]}
    merge_chunk_into(host, pad)
    bridges = np.array(host["triangles"][3 + len(pad["triangles"]):]).reshape(-1, 3)
    assert len(bridges) and (bridges[:, 1:] >= 3 + rs).all()
print("ok")

# conform: a pad 0.3 m over a flat ribbon strip comes down onto it (plus the
# gap) where it covers the strip, fades back to the plane past it, and the
# apron follows its top ring
from experimental_junction_pads import (PAD_CONFORM_FADE_M, PAD_LOWER_M, PAD_ROAD_GAP_M,
                                        conform_pad_to_ribbons,
                                        ribbon_top_triangles)
pad = build_pad_chunk_ellipse("p", (0.0, 0.0, 15.0, 15.0, 0.0), 0.0, 0.0, sides=16,
                              z_fn=lambda x, y: 0.3)
z0 = np.array(pad["vertices"]).reshape(-1, 3)[0, 2]
zr = z0 - 30.0                                       # ribbon 0.3 m under the pad (UE cm)
rib = {"vertices": [-3000.0, -450.0, zr, 3000.0, -450.0, zr, 3000.0, 450.0, zr, -3000.0, 450.0, zr,
                    -3000.0, -450.0, zr - 100, 3000.0, -450.0, zr - 100, 3000.0, 450.0, zr - 100],
       "triangles": [0, 1, 2, 0, 2, 3, 4, 5, 6]}      # pavement + a lower underside
pos, tris = ribbon_top_triangles([rib])
n = conform_pad_to_ribbons(pad, pos, tris)
V = np.array(pad["vertices"]).reshape(-1, 3)
rs = pad["ring_start"]
on = (np.abs(V[:rs, 1]) <= 449) & (np.abs(V[:rs, 0]) < 2999)
assert n == on.sum() > 0 and np.allclose(V[:rs][on, 2], zr + (PAD_ROAD_GAP_M - PAD_LOWER_M) * 100.0, atol=0.2)
far = np.abs(V[:rs, 1]) > 450 + PAD_CONFORM_FADE_M * 100
assert far.any() and np.allclose(V[:rs][far, 2], z0 - PAD_LOWER_M * 100.0)
assert np.allclose(V[rs - 16:rs, 2] - V[rs:, 2], PAD_APRON_DROP_M * 100.0, atol=0.2)
print("ok conform")

# fill discs on two parallel roads 8 m apart and 1 m apart in height draw one
# shared surface: where their footprints overlap they agree, so neither stands
# proud of the other
from scipy.spatial import cKDTree
from experimental_junction_pads import build_fill_disc, conform_disc_to_roads
xs = np.arange(-40.0, 41.0, 5.0)
east = np.r_[xs, xs]; north = np.r_[np.zeros(len(xs)), np.full(len(xs), 8.0)]
z = np.r_[np.full(len(xs), 100.0), np.full(len(xs), 101.0)]
seg = np.r_[np.zeros(len(xs), int), np.ones(len(xs), int)]
tree = cKDTree(np.c_[east, north])
ga, gb = 8, len(xs) + 8                        # both at east=0
da = build_fill_disc("a", (0.0, 0.0), 100.0, 6.5, ga, seg, east, north, z)
db = build_fill_disc("b", (0.0, 8.0), 101.0, 6.5, gb, seg, east, north, z)
for d, g in ((da, ga), (db, gb)):
    conform_disc_to_roads(d, tree, east, north, z, seg, g)
def disc_z(d, pts):                             # top surface (no apron) at pts, UE cm
    V = np.array(d["vertices"]).reshape(-1, 3); T = np.array(d["triangles"]).reshape(-1, 3)
    T = T[(T < d["ring_start"]).all(1)]
    return np.array([sample_mesh_height(V, T, x * 100, -y * 100) or np.nan for x, y in pts])
from road_mesh_common import sample_mesh_height
pts = [(x, y) for x in np.arange(-4.0, 4.1, 1.0) for y in np.arange(2.0, 6.1, 1.0)]
za, zb = disc_z(da, pts), disc_z(db, pts)
both = np.isfinite(za) & np.isfinite(zb)
assert both.sum() > 10 and np.max(np.abs(za[both] - zb[both])) < 3.0, np.nanmax(np.abs(za - zb))   # cm
on_a = disc_z(da, [(0.0, 0.0), (3.0, 0.0)]); on_b = disc_z(db, [(0.0, 8.0), (-3.0, 8.0)])
assert np.ptp(np.r_[on_a, on_b - 100.0]) < 3.0   # each still on its own road at its centerline
print("ok fill discs")
