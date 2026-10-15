"""Far forest file (build_far_forest.py, realism eval T3): records round-trip
through the ODF1 packing AODForestManager::LoadFarForest reads (cell index,
0.5 m jitter, decimetre Z, flags), and the per-cell jitter is deterministic
and keeps every card inside its own 20 m cell.

Run:  python pipeline/tests/test_far_forest.py
"""
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_far_forest import (CELL_M, CELLS, FLAG_BAND, FLAG_STREET, REC, cell_jitter,
                              pack_tile, read_bin)


def test_jitter_deterministic_and_in_cell():
    a, b = cell_jitter(-3, 7), cell_jitter(-3, 7)
    assert np.array_equal(a, b) and not np.array_equal(a, cell_jitter(-3, 8))
    assert a.shape == (2, CELLS * CELLS) and np.abs(a).max() < CELL_M / 2
    assert np.all(a * 2 == np.round(a * 2))   # 0.5 m steps -> exact int8


def test_roundtrip():
    recs = [(0, -7.5, 8.0, -123450.0, 1 | FLAG_BAND), (CELLS * CELLS - 1, 0.5, -0.5, 98760.0, FLAG_STREET | 7)]
    blob = pack_tile(4, -2, *(np.array(c) for c in zip(*recs)))
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "f.bin"
        p.write_bytes(b"ODF1" + struct.pack("<I", 1) + blob)
        got = read_bin(p)[(4, -2)]
    assert got == recs, got
    assert REC.size == 7   # C++ reads 7-byte records


if __name__ == "__main__":
    test_jitter_deterministic_and_in_cell()
    test_roundtrip()
    print("ok")
