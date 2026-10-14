"""demote_short_runs: a short shallow run is a DEM blip and goes, a short run
dropping past EMBANK_DEEP_SHORT_M (a culvert notch under a fill road) stays,
a lone station always goes (2026-10-14).

Run:  python pipeline/tests/test_embankment_short_runs.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_road_embankments import EMBANK_CLS, EMBANK_DEEP_SHORT_M, demote_short_runs

s = np.arange(0.0, 100.0, 5.0)
cls = np.zeros(len(s), np.int8)
drop = np.zeros(len(s))
cls[2:4], drop[2:4] = EMBANK_CLS, EMBANK_DEEP_SHORT_M - 0.5   # short, shallow
cls[6:8], drop[6:8] = EMBANK_CLS, EMBANK_DEEP_SHORT_M + 1.0   # short, deep
cls[10], drop[10] = EMBANK_CLS, 9.0                           # lone station
cls[13:17], drop[13:17] = EMBANK_CLS, 1.0                     # 15 m: long enough

out = demote_short_runs(cls, s, drop)
assert not out[2:4].any() and not out[10]
assert (out[6:8] == EMBANK_CLS).all() and (out[13:17] == EMBANK_CLS).all()
assert not demote_short_runs(cls, s)[6:8].any()   # no drops given: length rule only
print("ok")
