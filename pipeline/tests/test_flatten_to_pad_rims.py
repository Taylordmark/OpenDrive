"""flatten_to_pad_rims: stations inside a pad sit on the pad surface, the
approach leaves the rim at pad height and is untouched past
TRANSITION_LEN_M (2026-10-01).

Run:  python pipeline/tests/test_flatten_to_pad_rims.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_junction_pads import (PAD_TILT_MAX_GRADE, TRANSITION_LEN_M, build_pad_chunk_ellipse, fit_pad_tilts,
                                        flatten_to_pad_rims, pad_plane_z, surface_at_snapped)

s = np.arange(0.0, 200.0, 5.0)                 # one road, 10% climb, through a pad at s=100
east, north = s.copy(), np.zeros_like(s)
elev = 1000.0 + 0.1 * s
regraded = elev.copy()
pad = {"xy": (100.0, 0.0), "radius_m": 20.0, "elev": 1010.0, "shape": "circle"}
seg_ids = np.zeros(len(s), dtype=int)
n_in, n_blend, w, _g = flatten_to_pad_rims([pad], s, seg_ids, {0: (0, len(s) - 1)},
                                    np.zeros(len(s), dtype=bool), east, north, regraded)

inside = np.abs(s - 100.0) <= 20.0
assert n_in == inside.sum() and np.allclose(regraded[inside], 1010.0)
# the first station out sits at pad height (was 2.5 m off); from there the
# road climbs at its own 10%, shifted (2026-10-02, SF: no extended-plane
# catch-up)
assert abs(regraded[s == 125.0][0] - 1010.0) < 0.1, regraded[s == 125.0]
assert 0.08 < (regraded[s == 130.0][0] - regraded[s == 125.0][0]) / 5.0 < 0.2
far = np.abs(s - 100.0) - 20.0 > TRANSITION_LEN_M
assert np.array_equal(regraded[far], elev[far])
assert np.all(w[inside] == 1.0) and np.all(w[far] == 0.0)   # the bank fade: none in, full out
# no step: steepest is the smoothstep catch-up (10% grade + 2 m over 50 m)
assert np.max(np.abs(np.diff(regraded))) < 1.2
# a pad tilted onto a straight 10% road leaves that road's grade untouched
# (2026-10-02: flat pads made a shelf at every junction on a hill)
tilted = dict(pad)
assert fit_pad_tilts([tilted], east, north, elev, np.zeros(len(s), dtype=bool)) == 1
assert abs(tilted["plane"][0] - 0.1) < 0.005 and abs(tilted["plane"][1]) < 1e-6
assert abs(tilted["elev"] - 1010.0) < 0.05
assert "mold" not in tilted   # uncapped: the plane already fits (summit: molding noise made bumps)
rg3 = elev.copy()
flatten_to_pad_rims([tilted], s, seg_ids, {0: (0, len(s) - 1)}, np.zeros(len(s), dtype=bool),
                    east, north, rg3, base=elev)
assert np.max(np.abs(rg3 - elev)) < 0.1, np.max(np.abs(rg3 - elev))
# two pads 70 m apart on a 25% road, both capped at 12% so their extended
# planes disagree: the overlapping approaches must not hand over with a step
# (2026-10-02, Hyde St: "strongest pull wins" stepped 3.4 m between stations)
s2 = np.arange(0.0, 300.0, 5.0)
e2 = s2.copy()
el2 = 1000.0 + 0.25 * s2
pads2 = [{"xy": (x, 0.0), "radius_m": 8.0, "elev": 1000.0 + 0.25 * x, "shape": "circle",
          "plane": (0.12, 0.0)} for x in (115.0, 185.0)]
rg4 = el2.copy()
flatten_to_pad_rims(pads2, s2, np.zeros(len(s2), dtype=int), {0: (0, len(s2) - 1)},
                    np.zeros(len(s2), dtype=bool), e2, np.zeros_like(s2), rg4, base=el2)
assert np.max(np.abs(np.diff(rg4))) / 5.0 < 0.40, np.max(np.abs(np.diff(rg4))) / 5.0
# molded pad (2026-10-02): a 25% road through a pad whose tilt caps at 12%
# (PAD_TILT_MAX_GRADE) meets the road at the rim (no crest/sag there) and the junction height in
# the middle, and the drawn mesh carries that curved surface
e3 = np.arange(0.0, 200.0, 5.0)
el3 = 1000.0 + 0.25 * e3
mp = {"xy": (100.0, 0.0), "radius_m": 15.0, "elev": 1025.0, "shape": "circle"}
fit_pad_tilts([mp], e3, np.zeros_like(e3), el3, np.zeros(len(e3), dtype=bool))
assert abs(mp["plane"][0] - PAD_TILT_MAX_GRADE) < 1e-6 and "mold" in mp
assert abs(pad_plane_z(mp, 100.0, 0.0) - 1025.0) < 0.05
assert abs(pad_plane_z(mp, 115.0, 0.0) - el3[e3 == 115.0][0]) < 0.1
# ...linear between stations, like the ribbon (station-IDW stair-stepped)
mid = 0.5 * (pad_plane_z(mp, 110.0, 0.0) + pad_plane_z(mp, 115.0, 0.0))
assert abs(pad_plane_z(mp, 112.5, 0.0) - mid) < 0.05, pad_plane_z(mp, 112.5, 0.0) - mid
# two parallel roads 3 m apart through one capped pad never meet at its
# plane: no mold (summit pad 6, 2026-10-02)
pp = {"xy": (100.0, 0.0), "radius_m": 15.0, "elev": 1025.0, "shape": "circle"}
fit_pad_tilts([pp], np.r_[e3, e3], np.r_[np.full_like(e3, -5.0), np.full_like(e3, 5.0)],
              np.r_[el3 - 1.5, el3 + 1.5], np.zeros(2 * len(e3), dtype=bool))
assert "mold" not in pp and "plane" in pp
rg5 = el3.copy()
flatten_to_pad_rims([mp], e3, np.zeros(len(e3), dtype=int), {0: (0, len(e3) - 1)},
                    np.zeros(len(e3), dtype=bool), e3, np.zeros_like(e3), rg5, base=el3)
out = np.abs(e3 - 100.0) > 15.0
assert np.max(np.abs(rg5[out] - el3[out])) < 0.1, np.max(np.abs(rg5[out] - el3[out]))
ch = build_pad_chunk_ellipse("t", (100.0, 0.0, 15.0, 15.0, 0.0), mp["elev"], 0.0,
                             plane=mp["plane"], z_fn=lambda x, y: pad_plane_z(mp, x, y))
v = np.asarray(ch["vertices"]).reshape(-1, 3)
assert len(v) == 1 + 5 * 28 and len(ch["triangles"]) == 3 * 28 * (1 + 2 * 4)   # 4 rings + apron
assert len(ch["uv1"]) == 2 * len(v) and len(ch["uvs"]) == 2 * len(v)
rim = np.argmax(v[:ch["ring_start"], 0])   # +x rim vertex (not the apron) (UE cm): on the road, not the 12% plane
assert abs(v[rim, 2] - v[0, 2] - (el3[e3 == 115.0][0] - 1025.0) * 100.0) < 10.0
# a station the weld snap slid 2.5 m along a 20% road reads the original
# surface where it now sits, not its old elev (2026-10-02, SF rim 0.5 m off)
e6 = np.arange(0.0, 50.0, 5.0); z6 = 100.0 + 0.2 * e6
q6 = e6.copy(); q6[3] = 17.5
o6 = surface_at_snapped({0: (0, len(e6) - 1)}, e6, np.zeros_like(e6), z6, q6, np.zeros_like(e6))
assert abs(o6[3] - 103.5) < 1e-9 and np.array_equal(np.delete(o6, 3), np.delete(z6, 3))
# twist (2026-10-06, SF): a road running east into a pad tilted 8% up to the
# north leaves the rim with the pad's cross-slope (right of travel = south =
# 8% lower), not level, and is back on its own bank past TRANSITION_LEN_M
from road_mesh_common import bank_slopes
side = dict(pad, plane=(0.0, 0.08))
flat = np.full(len(s), 1000.0)
_n, _b, w5, g5 = flatten_to_pad_rims([side], s, seg_ids, {0: (0, len(s) - 1)},
                                     np.zeros(len(s), dtype=bool), east, north, flat.copy())
slope, _rx, _ry = bank_slopes(east, north, bank_scale=np.c_[1.0 - w5, w5[:, None] * g5])
assert np.allclose(slope[inside], -0.08, atol=1e-6), slope[inside]
assert abs(slope[s == 125.0][0] + 0.08 * w5[s == 125.0][0]) < 1e-6
assert np.allclose(slope[far], 0.0)
print("ok")
