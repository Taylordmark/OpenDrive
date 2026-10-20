"""build_terrain_mesh_tiles.ceiling_cliff_pairs: two carriageways close enough
that the lower one's pavement-ceiling cells cut the upper one's ground get a
wall pair (Green Valley Valencia Blvd); a small step between them does not.

Run:  python pipeline/tests/test_ceiling_cliff.py"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import build_terrain_mesh_tiles as btmt


def _pairs(dz, gap_m=10.0):
    x = np.arange(10.0, 200.0, 5.0)
    segs = [{"east_m": x.tolist(), "north_m": [-100.0] * len(x), "elev_m": [100.0 + dz] * len(x)},
            {"east_m": x.tolist(), "north_m": [-100.0 - gap_m] * len(x), "elev_m": [100.0] * len(x)}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "road_stations.json"
        path.write_text(json.dumps({"segments": segs}))
        btmt.STATIONS_PATH, btmt._STRUCT_MASK_CACHE, btmt._PAVEMENT_CACHE = path, None, None
        n = 2 * len(x)
        east = np.r_[x, x]
        north = np.r_[np.full(len(x), -100.0), np.full(len(x), -100.0 - gap_m)]
        elev = np.r_[np.full(len(x), 100.0 + dz), np.full(len(x), 100.0)]
        seg = np.r_[np.zeros(len(x), int), np.ones(len(x), int)]
        return btmt.ceiling_cliff_pairs(east, north, elev, np.zeros(n, bool),
                                        np.full(n, 3.0 + btmt.SKIRT_EXTRA), seg), len(x)


def test_cliff_handed_to_wall():
    pairs, k = _pairs(4.1)
    assert pairs and all(i < k <= j for i, j in pairs)     # upper road 0 paired with lower road 1


def test_small_step_left_to_the_carve():
    assert _pairs(1.0)[0] == []
    assert _pairs(4.1, gap_m=30.0)[0] == []                 # out of the cap's one-cell reach


if __name__ == "__main__":
    test_cliff_handed_to_wall()
    test_small_step_left_to_the_carve()
    print("ok")
