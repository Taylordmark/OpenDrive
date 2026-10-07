"""export_road_centerline_bin.build_route_graph directed lane graph
(PLAN-REALISM 3a, 2026-10-06): one-way end stations inherit the run's
direction, a <=3-station one-way blip on an untagged way is dropped, a one-way
end's link edge points out of the sink, and junction control: a stop sign
sets the yield bit on the minor approach's stop-line arc, an uncontrolled
junction gives the lower class the yield bit.

Run:  python pipeline/tests/test_route_graph_lanes.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from export_road_centerline_bin import CONTROL_PRIORITY, build_route_graph


def seg(e, n, hc, oneway=False):
    e, n = np.broadcast_arrays(np.asarray(e, float), np.asarray(n, float))
    k = len(e)
    return {"east_m": e.tolist(), "north_m": n.tolist(),
            "s_m": np.r_[0, np.cumsum(np.hypot(np.diff(e), np.diff(n)))].tolist(),
            "elev_m": [0.0] * k, "is_struct": [False] * k, "is_tunnel": [False] * k,
            "highway_class": [hc] * k, "lanes": [0] * k, "maxspeed": [0] * k, "oneway": [oneway] * k}


main = seg(np.arange(-50, 101, 5), 0, "primary")            # stations 0..30, two-way
side = seg(0, np.arange(-60, -2, 5), "residential")         # 31..42, ends 3 m south of main 10
ramp = seg(35, np.arange(60, 3, -5), "secondary", True)     # 43..54, one-way south into main 17
segs = [main, side, ramp]
dirs = [np.zeros(31, np.int8), np.zeros(12, np.int8), np.r_[np.ones(10), 0, 0].astype(np.int8)]
dirs[0][5:7] = 1                                            # 2-station blip on an untagged way
stop = {"kind": "stop", "approaches": [[1, 10, 1]]}         # stop line at side's station 10
nodes, edges, control = build_route_graph(segs, dirs, controls=[stop])
E = {(a, b): e for a, b, *e in edges}

assert all(E[(i, i + 1)][1] == 0 for i in range(30)), "blip on a two-way way survived"
assert all(E[(i, i + 1)][1] == 1 for i in range(43, 54)), "one-way end stations not inherited"
link = E[(17, 54)]
assert link[1] == -1, "link must run out of the one-way's sink end (54 -> 17)"
assert link[2] & 7 == 3, "link takes the lower class"
assert control[10] == 2 and control[17] == CONTROL_PRIORITY, control[[10, 17]]
assert E[(41, 42)][5] == 1, "stop sign: a->b stop-line arc yields"
assert link[5] == 2, "uncontrolled: the secondary entering the primary yields (b->a)"
assert E[(16, 17)][5] == 0 and E[(17, 18)][5] == 0, "the major road keeps priority"
assert np.allclose(nodes[:, 2], nodes[0, 2]) and E[(0, 1)][3] >= 1 and E[(0, 1)][4] > 0
print("ok")
