"""road_mesh_common.bank_slopes (2026-10-02): stations stacked by junction-pad
snapping must not turn the bank NaN (NaN carve -> holes in the terrain).

Run:  python pipeline/tests/test_bank_slopes.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from road_mesh_common import bank_slopes

if __name__ == "__main__":
    t = np.linspace(0, 1, 40)
    e, n = 100 * np.cos(t), 100 * np.sin(t)
    e[10:13], n[10:13] = e[11], n[11]
    s, rx, ry = bank_slopes(e, n)
    assert np.isfinite(s).all() and np.isfinite(rx).all() and np.isfinite(ry).all()
    assert np.isfinite(bank_slopes(np.zeros(5), np.zeros(5))[0]).all()
    print("ok")
