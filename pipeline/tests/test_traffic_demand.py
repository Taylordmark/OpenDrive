"""build_traffic_demand (PLAN-REALISM 3b, 2026-10-14): an urban interstate at
I-5 volumes jams at the weekday evening peak and runs free at 03:00, the peak
direction flips between morning and evening, and an HPMS interstate section
never hands its volume to a frontage road beside it.

Run:  python pipeline/tests/test_traffic_demand.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_traffic_demand import CLS_RAMP, capacity_vph, demand_vph, match_edges

# I-5 through Santa Clarita: ~230k/day, 4 lanes each way, urban, 55% peak share
URBAN, INBOUND = 1, 4
aadt, dirp = 230000, 55
cap = capacity_vph(0, 4)
fri = 4
pm_out = demand_vph(aadt, dirp, URBAN | INBOUND, False, 17, fri, 8)    # b->a heads out of town, 8% trucks
assert pm_out > cap, (pm_out, cap)
assert demand_vph(aadt, dirp, URBAN | INBOUND, False, 3, fri, 8) < 0.2 * cap
assert demand_vph(1000, 50, 0, True, 12, 0, 50) == 1.5 * demand_vph(1000, 50, 0, True, 12, 0)   # truck = 2 cars
# morning: the inbound (a->b) side carries the peak share, evening the outbound
assert demand_vph(aadt, dirp, URBAN | INBOUND, True, 8, fri) > demand_vph(aadt, dirp, URBAN | INBOUND, False, 8, fri)
assert pm_out > demand_vph(aadt, dirp, URBAN | INBOUND, True, 17, fri)
# a rural two-lane at 6k/day never comes near capacity
assert max(demand_vph(6000, 55, 0, f, h, d) for f in (True, False) for h in range(24) for d in range(7)) \
    < 0.5 * capacity_vph(2, 1)

# matching: HPMS I-70 (f_system 1) runs east-west along y=0; a frontage road
# (tertiary, class 4) 20 m north and the motorway carriageway 40 m south
s_xy = np.stack([np.arange(0, 500, 10.0), np.zeros(50)], axis=1)
s_brg = np.full(50, 90.0)
s_rows = np.tile([44000, 58, 0, 0, 1, 12], (50, 1)).astype(float)
mid = np.array([[250.0, 20.0], [250.0, -40.0], [250.0, -40.0]])
brg = np.array([90.0, 90.0, 90.0])
cls = np.array([4, 0, 0 | CLS_RAMP])
m = match_edges(mid, brg, cls, s_xy, s_brg, s_rows)
assert m[0] == -1, "frontage road took the interstate's volume"
assert m[1] >= 0, "divided carriageway 40 m out missed its HPMS centreline"
assert m[2] == -1, "a ramp matched a mainline section"
print("test_traffic_demand: PASS")
