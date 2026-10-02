"""build_road_stations.junction_node_degree (2026-10-01): a side street that
ends on a through way's INTERIOR node is a 3-arm junction, not a degree-1
dead end (what merge_way_chains' ends-only count said).

Run:  python pipeline/tests/test_junction_node_degree.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_road_stations import junction_node_degree

deg = junction_node_degree([
    {"nodes": [1, 2, 3]},        # through way, 2 is interior
    {"nodes": [2, 9]},           # side street T-ing into it at 2
    {"nodes": [5, 6, 7, 5]},     # closed loop
    {"nodes": [4]},              # degenerate, ignored
])
assert deg[2] == 3, deg          # T-junction
assert deg[1] == deg[3] == deg[9] == 1
assert deg[5] == 2 and deg[6] == 2   # loop closure is a pass-through, not a junction
assert 4 not in deg
print("ok")
