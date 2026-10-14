"""At-grade apron: build_road_chunks widens the edge lip only where the
terrain sits close under the pavement edge (road_mesh_common APRON_*)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from road_mesh_common import (APRON_LIP_OUT_M, ROAD_HALF_WIDTH, WRAP_LIP_OUT_M,
                              build_road_chunks)


def lips(e, n, z, ground_cm):
    """(left, right) lip reach past the pavement edge per station, m."""
    s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(e), np.diff(n)))])
    chunks, *_ = build_road_chunks(s, e, n, z, ground_cm=ground_cm)
    v = np.concatenate([np.array(c["vertices"]).reshape(-1, 7, 3) for c in chunks])
    reach = lambda lip, pave: np.hypot(*(v[:, lip, :2] - v[:, pave, :2]).T) / 100.0
    return reach(1, 2), reach(4, 3)   # lip-L from pave-L, lip-R from pave-R


def test_apron():
    e = np.arange(40) * 5.0
    n = np.zeros(40)
    z = np.full(40, 10.0)                                   # pavement 1000 cm
    flat = lambda x, y: np.full(len(x), 1000.0 - 35.0)       # clamped 35 cm under
    l, r = lips(e, n, z, flat)
    assert np.allclose(l, APRON_LIP_OUT_M, atol=0.01) and np.allclose(r, APRON_LIP_OUT_M, atol=0.01)

    # right of travel (UE +Y = south = -north) falls off a ledge
    ledge = lambda x, y: np.where(y > 0, 1000.0 - 300.0, 1000.0 - 35.0)
    l, r = lips(e, n, z, ledge)
    assert np.allclose(l, APRON_LIP_OUT_M, atol=0.01) and np.allclose(r, WRAP_LIP_OUT_M, atol=0.01)

    l, r = lips(e, n, z, lambda x, y: np.full(len(x), np.nan))   # no terrain tile
    assert np.allclose(l, WRAP_LIP_OUT_M) and np.allclose(r, WRAP_LIP_OUT_M)

    l, r = lips(e, n, z, None)                                    # old callers
    assert np.allclose(l, WRAP_LIP_OUT_M) and np.allclose(r, WRAP_LIP_OUT_M)

    # hairpin, 6 m radius: pavement + apron would fold the inside, keep the plain lip
    a = np.linspace(0.0, np.pi, 8)
    l, r = lips(6.0 * np.cos(a), 6.0 * np.sin(a), np.full(8, 10.0), flat)
    assert ROAD_HALF_WIDTH + 2.0 > 6.0 * 0.8
    assert np.all(l[2:-2] <= WRAP_LIP_OUT_M + 0.01) and np.all(r[2:-2] <= WRAP_LIP_OUT_M + 0.01)


if __name__ == "__main__":
    test_apron()
    print("ok")
