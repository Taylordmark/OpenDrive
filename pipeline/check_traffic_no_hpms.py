"""Traffic demand with no HPMS rows (2026-10-15, Breckenridge bake: geo.dot.gov
returned HTTP 500 for every year, s_rows came back empty and edge_values hit
IndexError). Checks every edge then takes the fallback values.

Usage:  python pipeline/check_traffic_no_hpms.py
"""
import numpy as np

import build_traffic_demand as T


def main():
    m = np.full(4, -1, dtype=np.int64)
    fb = np.array([500.0, 1200.0, 3000.0, 8000.0])
    bcount = np.array([0.0, 5.0, 50.0, 500.0])
    s_xy, s_brg, s_rows = T.hpms_samples([])
    assert T.match_edges(np.zeros((4, 2)), np.zeros(4), np.zeros(4, dtype=np.uint8), s_xy, s_brg, s_rows).tolist() == m.tolist()
    aadt, dir_pct, truck_pct, urban = T.edge_values(m, s_rows, fb, bcount)
    assert (aadt == fb).all(), aadt
    assert (dir_pct == 55.0).all() and (truck_pct == T.FALLBACK_TRUCK_PCT).all()
    assert (urban == (bcount >= T.URBAN_BUILDINGS)).all()
    print("ok: empty HPMS -> all fallback")


if __name__ == "__main__":
    main()
