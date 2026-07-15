"""Fetch USGS aerial imagery aligned to the stage heightmap square.

Uses the public-domain USGSImageryOnly tile service, resamples onto the
exact heightmap extent (same corner/side as the Landscape), and writes
data/stages/<stage>/textures/T_Terrain.png (4096x4096).
Row 0 = north edge, matching heightmap.png and the landscape material UVs.
"""

import json
import math
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image

STAGE = "tail_of_the_dragon"
ZOOM = 16                      # ~2.4 m/px at this latitude, matches 4k output
OUT_RES = 4096
TILE_URL = ("https://basemap.nationalmap.gov/arcgis/rest/services/"
            "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}")
HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}
M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
STAGE_DIR = ROOT / "data" / "stages" / STAGE
OUT = STAGE_DIR / "textures"
OUT.mkdir(parents=True, exist_ok=True)

meta = json.loads((STAGE_DIR / "stage_meta.json").read_text())
lat0, lon0 = meta["origin_latlon"]
ls = meta["landscape"]
side_m = (ls["resolution"] - 1) * ls["scale"][0] / 100.0
east_min = ls["location_cm"][0] / 100.0
north_max = -ls["location_cm"][1] / 100.0
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))


def lonlat_to_px(lon, lat, z):
    n = 256 * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat_r = np.radians(lat)
    y = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


# stage square corners -> lat/lon -> mercator tile range (with margin)
lat_n = lat0 + north_max / M_PER_DEG_LAT
lat_s = lat0 + (north_max - side_m) / M_PER_DEG_LAT
lon_w = lon0 + east_min / m_per_deg_lon
lon_e = lon0 + (east_min + side_m) / m_per_deg_lon

x0, y0 = lonlat_to_px(np.array(lon_w), np.array(lat_n), ZOOM)
x1, y1 = lonlat_to_px(np.array(lon_e), np.array(lat_s), ZOOM)
tx0, tx1 = int(x0 // 256) - 1, int(x1 // 256) + 1
ty0, ty1 = int(y0 // 256) - 1, int(y1 // 256) + 1
cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
print(f"Fetching {cols * rows} imagery tiles (z{ZOOM}, {cols}x{rows}) ...")

mosaic = np.zeros((rows * 256, cols * 256, 3), dtype=np.uint8)
session = requests.Session()
for ty in range(ty0, ty1 + 1):
    for tx in range(tx0, tx1 + 1):
        url = TILE_URL.format(z=ZOOM, y=ty, x=tx)
        for attempt in range(4):
            try:
                r = session.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))
        tile = np.asarray(Image.open(BytesIO(r.content)).convert("RGB"))
        r0, c0 = (ty - ty0) * 256, (tx - tx0) * 256
        mosaic[r0:r0 + 256, c0:c0 + 256] = tile
    print(f"  row {ty - ty0 + 1}/{rows}")

# resample onto stage grid (row 0 = north)
px_grid_e = east_min + (np.arange(OUT_RES) + 0.5) / OUT_RES * side_m
px_grid_n = north_max - (np.arange(OUT_RES) + 0.5) / OUT_RES * side_m
lon_g = lon0 + px_grid_e / m_per_deg_lon
lat_g = lat0 + px_grid_n / M_PER_DEG_LAT
lon_m, lat_m = np.meshgrid(lon_g, lat_g)
mx, my = lonlat_to_px(lon_m, lat_m, ZOOM)
mx -= tx0 * 256
my -= ty0 * 256
x0i = np.clip(mx.astype(int), 0, mosaic.shape[1] - 2)
y0i = np.clip(my.astype(int), 0, mosaic.shape[0] - 2)
fx = (mx - x0i)[..., None]
fy = (my - y0i)[..., None]
m00 = mosaic[y0i, x0i].astype(np.float32)
m01 = mosaic[y0i, x0i + 1].astype(np.float32)
m10 = mosaic[y0i + 1, x0i].astype(np.float32)
m11 = mosaic[y0i + 1, x0i + 1].astype(np.float32)
out = (m00 * (1 - fx) * (1 - fy) + m01 * fx * (1 - fy)
       + m10 * (1 - fx) * fy + m11 * fx * fy)
Image.fromarray(out.astype(np.uint8)).save(OUT / "T_Terrain.png")
print(f"Wrote {OUT / 'T_Terrain.png'} ({OUT_RES}x{OUT_RES})")
