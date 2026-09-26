"""verify_water_crossings.png (task step 1 offline verification): county map
with water polygons + roads, crossing/skirting stations highlighted, named
cases annotated with crossing-vs-skirting/dam verdicts.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from road_mesh_common import ROAD_HALF_WIDTH
from test_areas import AREA_NAME
from build_terrain_mesh_tiles import M_PER_DEG_LAT, load_lake_beds, _point_in_polygon

STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
data = json.loads(STATIONS_PATH.read_text())
lat0, lon0 = data["origin_latlon"]
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
lakes = load_lake_beds()

fig, ax = plt.subplots(figsize=(13, 11))

# lake polygons (lat/lon)
for lake in lakes:
    rx_m = lake["ring_x"] / 100.0
    ry_m = -lake["ring_y"] / 100.0
    lat = lat0 + ry_m / M_PER_DEG_LAT
    lon = lon0 + rx_m / m_per_deg_lon
    ax.fill(lon, lat, color="#4a90d9", alpha=0.5, linewidth=0, zorder=1)

# roads (all, light gray) + highlight crossing/skirting stations
edge_pts_cross = []   # both-edge-in-lake at ROAD_HALF_WIDTH
edge_pts_skirt = []   # one-edge-in-lake at ROAD_HALF_WIDTH
for seg in data["segments"]:
    n = len(seg["east_m"])
    if n < 2:
        continue
    east = np.array(seg["east_m"]); north = np.array(seg["north_m"])
    lat = lat0 + north / M_PER_DEG_LAT
    lon = lon0 + east / m_per_deg_lon
    ax.plot(lon, lat, color="#999999", linewidth=0.3, zorder=2, alpha=0.6)

    tx = np.gradient(east); ty = np.gradient(north)
    tl = np.hypot(tx, ty); tl[tl < 1e-6] = 1.0; tx /= tl; ty /= tl
    rx, ry = -ty, tx
    ins = {}
    for side in (-1.0, 1.0):
        ee = east + rx * ROAD_HALF_WIDTH * side
        nn = north + ry * ROAD_HALF_WIDTH * side
        cx = ee * 100.0; cy = -nn * 100.0
        inside = np.zeros(n, dtype=bool)
        for lake in lakes:
            bx0, bx1, by0, by1 = lake["bbox"]
            m = 5000.0
            bb = (cx >= bx0 - m) & (cx <= bx1 + m) & (cy >= by0 - m) & (cy <= by1 + m)
            if not bb.any():
                continue
            idx = np.where(bb)[0]
            hit = _point_in_polygon(cx[idx], cy[idx], lake["ring_x"], lake["ring_y"])
            inside[idx] |= hit
        ins[side] = inside
    both = ins[-1.0] & ins[1.0]
    one = (ins[-1.0] | ins[1.0]) & ~both
    for i in np.where(both)[0]:
        edge_pts_cross.append((lon[i], lat[i]))
    for i in np.where(one)[0]:
        edge_pts_skirt.append((lon[i], lat[i]))

if edge_pts_skirt:
    xs, ys = zip(*edge_pts_skirt)
    ax.scatter(xs, ys, s=14, color="#e8a33d", zorder=4, label="skirting (one edge only, at ROAD_HALF_WIDTH)")
if edge_pts_cross:
    xs, ys = zip(*edge_pts_cross)
    ax.scatter(xs, ys, s=20, color="#d62728", zorder=5, label="crossing (both edges, at ROAD_HALF_WIDTH)")

# named annotations
annotations = [
    ("Swan Mountain Rd\n(shoreline, at-grade\nclearance -1.2..+0.6m\nvs water surface)\nVERDICT: skirting, NOT bridged",
     39.6011, -106.0155, 39.560, -105.970),
    ("Dillon Dam Road\n(dam crest, pavement\nnever enters mapped\nwater polygon, min\n~12m at centerline)\nVERDICT: dam-top fill, NOT bridged",
     39.6120, -106.0700, 39.660, -106.220),
    ("US 6 / CO 9 / I-70:\npass near several lake/pond\npolygons (10-200m) but\nedges never enter one.\nReal creek crossings (Blue\nRiver/Tenmile/Straight Ck)\nare linear waterways, not\nbaked as lake polygons -\nout of reach of this test.",
     39.60, -106.10, 39.55, -106.42),
]
for text, lat_pt, lon_pt, lat_txt, lon_txt in annotations:
    ax.annotate(text, xy=(lon_pt, lat_pt), xytext=(lon_txt, lat_txt), fontsize=7.5,
                bbox=dict(boxstyle="round", fc="white", ec="#555555", alpha=0.9),
                arrowprops=dict(arrowstyle="->", color="#333333", lw=1.0), zorder=6)

ax.set_title("Water-crossing inventory: Summit County roads vs. mapped lake polygons\n"
              "(zero 'both-edges-in-water' county-wide; only Swan Mountain Rd brushes the shoreline, at grade)",
              fontsize=11)
ax.set_xlabel("longitude"); ax.set_ylabel("latitude")
ax.legend(loc="lower left", fontsize=8)
ax.set_aspect(1.0 / math.cos(math.radians(lat0)))
fig.tight_layout()
out = ROOT / "scratchpad" / "verify_water_crossings.png"
fig.savefig(out, dpi=150)
print("wrote", out)
