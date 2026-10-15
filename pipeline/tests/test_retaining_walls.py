"""build_retaining_walls: per-road-pair clustering and road-sided wall faces.

Run:  python pipeline/tests/test_retaining_walls.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_retaining_walls as brw
from road_mesh_common import road_ue_z_m
from build_retaining_walls import ORIGIN_HEIGHT_M


def _two_roads():
    # road 0 climbs 0 -> 14 m, road 1 sits flat at 6 m, 14 m to the north:
    # road 0 is the upper side only past the middle
    x = np.arange(0.0, 200.0 + 1e-9, 5.0)
    east = np.r_[x, x]
    north = np.r_[np.zeros_like(x), np.full_like(x, 14.0)]
    elev = np.r_[x * 0.07, np.full_like(x, 6.0)]
    seg = np.r_[np.zeros(len(x), int), np.ones(len(x), int)]
    hw = np.full(len(east), 3.0)
    return east, north, elev, seg, hw


def test_cluster_runs_splits_by_road_pair():
    seg = np.array([0, 1, 2, 0, 1, 2])
    pairs = [(0, 1), (1, 2), (3, 4), (4, 5)]    # 0-1 and 1-2 share station 1
    assert sorted(sorted(r) for r in brw.cluster_runs(pairs, 6)) == [[0, 1, 2], [3, 4, 5]]
    runs = brw.cluster_runs(pairs, 6, seg)            # station 1 borders both walls
    assert sorted(sorted(r) for r in runs) == [[0, 1], [1, 2], [3, 4], [4, 5]]


def test_faces_only_where_the_upper_road_is_higher():
    east, north, elev, seg, hw = _two_roads()
    run = list(range(len(east)))
    ref = brw.own_chain_centroid_cm(run, east, north, seg)
    verts, uvs, tris = brw.build_wall_run_mesh(run, east, north, elev, hw, ref, seg)
    assert len(verts) == len(uvs) and len(verts) % 2 == 0
    assert set(np.unique(tris).tolist()) == set(range(len(verts)))   # no orphan rows
    lo, hi = verts[0::2, 2], verts[1::2, 2]
    assert (hi - lo >= 50.0 - 1e-3).all()                            # every kept row >= 0.5 m tall
    # no face where road 0 is still well below road 1 (x < 60 m), one to the end
    assert verts[:, 0].min() > 60.0 * 100.0 and verts[:, 0].max() > 190.0 * 100.0
    assert lo.min() / 100.0 > road_ue_z_m(6.0, 0.0, ORIGIN_HEIGHT_M) - 2.0


if __name__ == "__main__":
    test_cluster_runs_splits_by_road_pair()
    test_faces_only_where_the_upper_road_is_higher()
    print("ok")
