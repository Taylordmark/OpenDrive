"""sample_dem's vectorized bilinear (2026-10-01) is bit-identical to the
per-point loop it replaced, including samples on a tile's last pixel
column/row (which read the neighbour tile).

Run:  python pipeline/tests/test_sample_dem.py"""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_terrain_mesh_tiles import sample_dem

zoom, n = 12, 256 * 2 ** 12
rng = np.random.default_rng(0)
cache = {(zoom, tx, ty): rng.uniform(2000, 4000, (256, 256)).astype(np.float32)
         for tx in range(800, 804) for ty in range(1550, 1554)}

def lonlat(px, py):
    lon = px / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * py / n))))
    return lat, lon

pts = [lonlat(rng.uniform(800, 803) * 256, rng.uniform(1550, 1553) * 256) for _ in range(2000)]
pts += [lonlat(801 * 256 + 255.5, 1551 * 256 + 255.7), lonlat(802 * 256 - 0.01, 1552 * 256 - 0.01)]
lat = np.array([p[0] for p in pts])
lon = np.array([p[1] for p in pts])

def reference(lat_arr, lon_arr):   # the pre-2026-10-01 loop
    lat_r = np.radians(lat_arr)
    px_glob = (lon_arr + 180.0) / 360.0 * n
    py_glob = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    tx_arr, ty_arr = (px_glob // 256).astype(int), (py_glob // 256).astype(int)
    px = lambda tx, ty, x, y: float(cache[(zoom, tx, ty)][y, x])
    out = np.empty(len(lat_arr), dtype=np.float32)
    for i in range(len(lat_arr)):
        tx, ty = int(tx_arr[i]), int(ty_arr[i])
        lx, ly = px_glob[i] - tx * 256, py_glob[i] - ty * 256
        x0, y0 = min(int(lx), 255), min(int(ly), 255)
        fx, fy = lx - x0, ly - y0
        tx1, x1 = (tx + 1, 0) if x0 == 255 else (tx, x0 + 1)
        ty1, y1 = (ty + 1, 0) if y0 == 255 else (ty, y0 + 1)
        v00, v01 = px(tx, ty, x0, y0), px(tx1, ty, x1, y0)
        v10, v11 = px(tx, ty1, x0, y1), px(tx1, ty1, x1, y1)
        out[i] = (v00 * (1 - fx) * (1 - fy) + v01 * fx * (1 - fy)
                  + v10 * (1 - fx) * fy + v11 * fx * fy)
    return out

assert np.array_equal(sample_dem(lat, lon, zoom, None, cache), reference(lat, lon))
print("ok")
