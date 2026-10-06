"""build_road_furniture (2026-10-06): which approaches a control node gets a
pole on, and which curves get chevrons.

Run:  python pipeline/tests/test_furniture_place.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_road_furniture import approach_dirs, chevron_mask, chevron_radius_m, in_pavement, pavement_index, yaw_deg

if __name__ == "__main__":
    xy = np.c_[np.arange(0, 105, 5.0), np.zeros(21)]          # straight road, east, 21 stations
    node = xy[10]
    # junction node mid-segment: both sides approach it, 2 stations upstream each
    assert approach_dirs(xy, 10, 0, node, 2) == [(1, 8), (-1, 12)]
    # oneway only the way it runs
    assert approach_dirs(xy, 10, 1, node, 2) == [(1, 8)]
    assert approach_dirs(xy, 10, -1, node, 2) == [(-1, 12)]
    # segment ending at the node: only the approach arriving at that end
    assert approach_dirs(xy, 20, 0, xy[20], 2) == [(1, 18)]
    assert approach_dirs(xy, 0, 0, xy[0], 2) == [(-1, 2)]
    # stop node short of its junction (at the east end): only toward it
    assert approach_dirs(xy, 17, 0, xy[20], 0) == [(1, 17)]
    # oneway leaving the junction: no approach
    assert approach_dirs(xy, 17, -1, xy[20], 0) == []

    # signs face oncoming traffic: eastbound traffic sees a sign facing west
    assert abs(abs(yaw_deg(-1.0, 0.0)) - 180.0) < 1e-9 and abs(yaw_deg(0.0, 1.0) + 90.0) < 1e-9

    # chevrons: a 40 m radius arc is tight at 45 mph, not at 15 mph; the
    # straight lead-in never is; left turn = positive curvature
    th = np.linspace(0, np.pi / 2, 13)                          # ~5.2 m spacing
    arc = np.c_[40 * np.sin(th), 40 * (1 - np.cos(th))]
    road = np.r_[np.c_[np.arange(-50, 0, 5.0), np.zeros(10)], arc]
    m, k = chevron_mask(road, np.full(len(road), 72.0), np.zeros(len(road), bool))
    assert m[12:20].all() and not m[:6].any() and (k[12:20] > 0).all()
    m, _ = chevron_mask(road, np.full(len(road), 24.0), np.zeros(len(road), bool))
    assert not m.any()
    assert chevron_radius_m(200.0) == 150.0 and 40 < chevron_radius_m(40.0) < 50
    # a blocked (junction / bridge) stretch breaks the run below the minimum
    blocked = np.zeros(len(road), bool)
    blocked[11::3] = True
    assert not chevron_mask(road, np.full(len(road), 72.0), blocked)[0].any()

    # lane test: inside the 4 m half-width, not 5 m out, nor at another level
    idx = pavement_index(xy[:, 0], xy[:, 1], np.zeros(21), np.full(21, 4.0), np.zeros(21, int), np.zeros(21, bool))
    assert in_pavement(idx, np.array([52.0, 52.0, 52.0]), np.array([3.0, -5.0, 0.0]),
                       np.array([0.0, 0.0, 9.0])).tolist() == [True, False, False]
    print("ok")
