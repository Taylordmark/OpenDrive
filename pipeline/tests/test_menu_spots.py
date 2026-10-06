"""build_menu_spots (2026-10-05): the wedge score prefers the side with the
mountain, and the junction / grade filters drop what they should.

Run:  python pipeline/tests/test_menu_spots.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_menu_spots import JUNCTION_CLEAR_M, candidates, car_heading, score_views, sea_share, ue_yaw

if __name__ == "__main__":
    # flat valley floor at 0 m, a ridge climbing to 250 m from 300 m east of
    # a north-south road along e = 0
    h = lambda e, n: 250.0 * np.clip((np.asarray(e) - 300.0) / 700.0, 0.0, 1.0) + 0.0 * np.asarray(n)
    one = np.zeros(1)
    east, west = (score_views(h, one, one, one, np.array([hd])) for hd in (90.0, 270.0))
    assert east["score"][0] > west["score"][0] + 100.0, (east, west)
    assert not (east["blocked"][0] or east["cliff"][0] or east["void"][0])
    # a 30 m wall 50 m out blocks the view; a 10 m drop behind the camera is a cliff
    wall = lambda e, n: np.where(np.asarray(e) > 50.0, 30.0, 0.0) + 0.0 * np.asarray(n)
    assert score_views(wall, one, one, one, np.array([90.0]))["blocked"][0]
    drop = lambda e, n: np.where(np.asarray(e) < -10.0, -10.0, 0.0) + 0.0 * np.asarray(n)
    assert score_views(drop, one, one, one, np.array([90.0]))["cliff"][0]
    void = lambda e, n: np.where(np.asarray(e) > 300.0, np.nan, 0.0) + 0.0 * np.asarray(n)
    assert score_views(void, one, one, one, np.array([90.0]))["void"][0]

    # seg 0: flat, north from -500 to 500; seg 1: flat, crosses it at the
    # origin; seg 2: 10 % grade, well away from both
    s = np.arange(-500.0, 500.1, 5.0)
    e = np.r_[0.0 * s, s, 2000.0 + 0.0 * s]
    n = np.r_[s, 0.0 * s, s]
    z = np.r_[0.0 * s, 0.0 * s, 0.1 * (s + 500.0)]
    seg = np.repeat([0, 1, 2], len(s))
    off = np.zeros(len(e), bool)
    idx = candidates(e, n, z, seg, off, off)
    assert len(idx) and set(seg[idx]) == {0, 1}, set(seg[idx])
    d_cross = np.hypot(e[idx], n[idx])
    d_end = 500.0 - np.maximum(np.abs(e[idx]), np.abs(n[idx]))
    assert d_cross.min() >= JUNCTION_CLEAR_M and d_end.min() >= JUNCTION_CLEAR_M
    # the crossing stops counting once it's grade-separated (seg 1 bridged 10 m up)
    z_sep = z.copy()
    z_sep[seg == 1] = 10.0
    assert np.hypot(e, n)[candidates(e, n, z_sep, seg, off, off)].min() < JUNCTION_CLEAR_M
    # a bridge flag clears its +-30 m
    br = off.copy()
    br[(seg == 0) & (np.abs(n - 300.0) < 1.0)] = True
    ib = candidates(e, n, z, seg, br, off)
    assert np.abs(n[ib[seg[ib] == 0]] - 300.0).min() > 30.0

    # car three-quarter toward the camera, nose screen right; UE yaw = heading - 90
    assert car_heading(0.0, 90.0) == 180.0 and car_heading(0.0, 270.0) == 0.0
    assert ue_yaw(90.0) == 0.0 and ue_yaw(180.0) == 90.0 and ue_yaw(0.0) == -90.0

    # sea plane: a bay floor (-10 m) north of a 50 m shore counts, high ground or void doesn't
    bay = lambda e, n: np.where(np.asarray(n) > 500.0, -10.0, 50.0) + 0.0 * np.asarray(e)
    assert sea_share(bay, 0.0, 0.0, 0.0) > 0.9 and sea_share(bay, 0.0, 0.0, 180.0) == 0.0
    high = lambda e, n: np.where(np.asarray(n) > 3000.0, np.nan, 3000.0) + 0.0 * np.asarray(e)
    assert sea_share(high, 0.0, 0.0, 0.0) == 0.0
    print("ok")
