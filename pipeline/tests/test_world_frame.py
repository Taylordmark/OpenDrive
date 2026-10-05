"""world_frame (2026-10-05): ellipsoidal LCC matches Snyder's worked example,
round-trips across California, and the chunk helpers agree.

Run:  python pipeline/tests/test_world_frame.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from world_frame import WORLDS, CHUNK_SIZE_M, chunk_of, chunk_origin, lcc_forward, lcc_inverse

if __name__ == "__main__":
    # Snyder PP 1395 p. 296: Clarke 1866, phi1=33, phi2=45, phi0=23, lam0=-96
    clarke = dict(a=6378206.4, e2=0.00676866)
    x, y = lcc_forward(-75.0, 35.0, 33.0, 45.0, 23.0, -96.0, **clarke)
    assert abs(x - 1894410.9) < 0.1 and abs(y - 1564649.5) < 0.1, (x, y)
    lon, lat = lcc_inverse(x, y, 33.0, 45.0, 23.0, -96.0, **clarke)
    assert abs(lon + 75.0) < 1e-9 and abs(lat - 35.0) < 1e-9, (lon, lat)

    lon, lat = np.meshgrid(np.linspace(-125.0, -113.5, 60), np.linspace(32.0, 42.5, 60))
    x, y = lcc_forward(lon, lat, *WORLDS["california"])
    lon2, lat2 = lcc_inverse(x, y, *WORLDS["california"])
    x2, y2 = lcc_forward(lon2, lat2, *WORLDS["california"])
    assert np.hypot(x2 - x, y2 - y).max() < 1e-3
    assert np.abs(lon2 - lon).max() < 1e-9 and np.abs(lat2 - lat).max() < 1e-9

    e = np.array([0.0, -1.0, 63999.9, 64000.0, -64000.0, 300000.0])
    i, j = chunk_of(e, -e)
    assert i.tolist() == [0, -1, 0, 1, -1, 4] and j.tolist() == [0, 0, -1, -1, 1, -5]
    ce, cn = chunk_origin(i, j)
    assert (np.abs(ce - e) <= CHUNK_SIZE_M / 2).all() and (np.abs(cn + e) <= CHUNK_SIZE_M / 2).all()
    assert [a.tolist() for a in chunk_of(ce, cn)] == [i.tolist(), j.tolist()]
    print("ok")
