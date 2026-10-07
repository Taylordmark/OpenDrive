"""Road-shaped pads (pad_profile, 2026-10-07): a T keeps a flat edge on its
open side, a Y pinches between its arms, a cross is a plus - always inside
the old disc, never baring pavement, and point_in_pad / the drawn ring agree.

Run:  python pipeline/tests/test_pad_profile.py"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_junction_pads import (PAD_OVERLAP_MARGIN_M, PAD_ROAD_EDGE_M, _ellipse_polygon,
                                        _profile_r, build_pad_chunk_ellipse, pad_profile, point_in_pad)

R, HW = 18.0, 4.5   # old drawn radius, road half width


def arm(deg, length=40.0, start=0.0):
    t = math.radians(deg)
    return [(s * math.cos(t), s * math.sin(t), HW) for s in np.arange(start, length + 0.1, 5.0)]


def through(deg):
    t = math.radians(deg)
    return [(s * math.cos(t), s * math.sin(t), HW) for s in np.arange(-40.0, 40.1, 5.0)]


old = _ellipse_polygon((0.0, 0.0, R, R, 0.0), 0.0, sides=96)
cases = {"T": [through(0), arm(90)], "Y": [arm(-90), arm(65), arm(115)], "plus": [through(0), through(90)]}
prof = {}
for name, roads in cases.items():
    p = pad_profile((0.0, 0.0), old, roads)
    assert p is not None, name
    prof[name] = p
    j = {"xy": (0.0, 0.0), "radius_m": R - PAD_OVERLAP_MARGIN_M, "profile": p}
    assert max(p) <= R + 1e-6, f"{name}: grew past the old pad"
    # every pavement-edge point inside the old pad stays covered
    for pl in roads:
        for (x0, y0, w), (x1, y1, _) in zip(pl[:-1], pl[1:]):
            L = math.hypot(x1 - x0, y1 - y0)
            nx, ny = -(y1 - y0) / L, (x1 - x0) / L
            for f in np.linspace(0, 1, 6):
                for sgn in (-1, 1):
                    qx, qy = x0 + f * (x1 - x0) + sgn * nx * w, y0 + f * (y1 - y0) + sgn * ny * w
                    if math.hypot(qx, qy) < R - 0.5:
                        assert point_in_pad(qx, qy, j), f"{name}: bared pavement at ({qx:.1f},{qy:.1f})"
    # drawn ring == profile
    pad = build_pad_chunk_ellipse("p", (0.0, 0.0, R, R, 0.0), 0.0, 0.0, profile=p)
    V = np.array(pad["vertices"]).reshape(-1, 3)[1:1 + len(p), :2] / 100.0
    assert np.allclose(np.hypot(V[:, 0], -V[:, 1]), p, atol=0.02)

edge = HW + PAD_ROAD_EDGE_M
# T: open side (270 deg) is flat at the through road's edge, the stem side keeps full reach
assert abs(_profile_r((0, 0), prof["T"], 0.0, -1.0) - edge) < 0.1
assert abs(_profile_r((0, 0), prof["T"], 1.0, -1.0) - edge * math.sqrt(2)) < 0.3   # flat, not round
assert _profile_r((0, 0), prof["T"], 0.0, 1.0) > R - 0.1
# Y: pinched in the 50 deg crotch and on the wide sides, full along each arm
for deg in (65, 115, 270):
    assert _profile_r((0, 0), prof["Y"], math.cos(math.radians(deg)), math.sin(math.radians(deg))) > R - 0.1
for deg in (0, 180):
    assert _profile_r((0, 0), prof["Y"], math.cos(math.radians(deg)), math.sin(math.radians(deg))) < 0.75 * R
# plus: full along arms, pulled in on the diagonals (rounded inner corners)
assert min(_profile_r((0, 0), prof["plus"], math.cos(math.radians(d)), math.sin(math.radians(d)))
           for d in (0, 90, 180, 270)) > R - 0.1
assert max(_profile_r((0, 0), prof["plus"], math.cos(math.radians(d)), math.sin(math.radians(d)))
           for d in (45, 135, 225, 315)) < 0.9 * R
print("ok")
