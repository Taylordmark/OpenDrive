"""find_crossings (2026-10-08): side-by-side carriageways 7 m apart are not
a junction; a T and a Y merge that meets at a node still are.

Run:  python pipeline/tests/test_find_crossings_parallel.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from experimental_junction_pads import find_crossings


def run(*roads):
    e = np.concatenate([r[0] for r in roads]); n = np.concatenate([r[1] for r in roads])
    seg = np.concatenate([np.full(len(r[0]), k) for k, r in enumerate(roads)])
    s = np.concatenate([np.r_[0, np.cumsum(np.hypot(np.diff(r[0]), np.diff(r[1])))] for r in roads])
    return find_crossings(e, n, s, np.zeros(len(e)), seg, np.zeros(len(e), bool))


x = np.arange(0.0, 200.0, 5.0)
main = (x, np.zeros_like(x))
assert run(main, (x, np.full_like(x, 7.0))) == []                    # carriageways
assert len(run(main, (np.full(20, 100.0), np.arange(0.0, 100.0, 5.0)))) == 1   # T
t = np.arange(0.0, 100.0, 5.0)                                         # Y: 10 deg off at x=100
assert len(run(main, (100.0 + t * np.cos(0.17), t * np.sin(0.17)))) >= 1
print("ok: carriageways skipped, T and Y kept")
