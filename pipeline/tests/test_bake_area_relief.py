"""bake_area_relief.py (2026-10-14): render shape, bounds frame, water tint.

Run:  python pipeline/tests/test_bake_area_relief.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bake_area_relief as br

# 3 km x 1.5 km area -> long side MAX_PX, aspect kept
w, h = br.grid_size(3000.0, 1500.0)
assert (w, h) == (br.MAX_PX, br.MAX_PX // 2), (w, h)

# east/north m -> UE cm: x = east*100, y = -north*100, so the top (north) row is min y
mn, mx = br.bounds_cm(-1000.0, 200.0, 2000.0, 1700.0)
assert mn == [-100000.0, -170000.0] and mx == [200000.0, -20000.0], (mn, mx)

# synthetic DEM: a hill in the east half, a lake in the west quarter
yy, xx = np.mgrid[0:h, 0:w]
dem = 500.0 * np.exp(-((xx - 0.7 * w) ** 2 + (yy - h / 2) ** 2) / (2 * (0.1 * w) ** 2))
water = xx < w // 4
px_m = 3000.0 / w
for cover in (None, np.tile([1.0, 0, 0, 0, 0], (h, w, 1))):
    rgb = br.render(dem, px_m, water, cover)
    assert rgb.shape == (h, w, 3) and rgb.dtype == np.uint8
    assert (rgb[water] == br.WATER_RGB).all()                  # flat deep blue
    lake = rgb[h // 2, 10].astype(int)
    assert lake[2] > lake[0] + 30, lake                        # blue-dominant
    # NW-lit: the hill's NW flank is brighter than its SE flank
    c = (h // 2, int(0.7 * w))
    d = int(0.08 * w)
    nw, se = rgb[c[0] - d, c[1] - d].sum(), rgb[c[0] + d, c[1] + d].sum()
    assert nw > se + 30, (nw, se)
# corners NW, NE, SE, SW as [lat, lng]; bilinear centre error of an affine projection is 0
ll, err = br.corners_ll(0.0, 0.0, 2.0, 1.0, lambda e, n: (np.asarray(e) * 0.01, np.asarray(n) * 0.01))
assert ll == [[0.01, 0.0], [0.01, 0.02], [0.0, 0.02], [0.0, 0.0]] and err < 1e-6, (ll, err)
print("ok")
