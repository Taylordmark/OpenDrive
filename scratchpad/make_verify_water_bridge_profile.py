"""verify_water_bridge_profile.png (task step 3 offline verification):
longitudinal profile of Swan Mountain Road's near-miss shoreline stretch
showing road elevation vs. the baked lake surface Z (not raw DEM), plus a
schematic cross-section illustrating how a water-crossing pier (if one had
qualified) terminates at water-surface + WATER_PIER_SUBMERGENCE_M rather
than the noisy raw-DEM "lakebed" reading. No genuine water-triggered bridge
exists in this county's current bake (see verify_water_crossings.png /
verify_water_bridge_stats.txt), so this uses the one location the §7.23
water-crossing pass actually evaluated against real water-surface data
(Swan Mountain Road) to demonstrate the geometry the code path would
produce if/when a real crossing qualifies.
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

from test_areas import AREA_NAME, ORIGIN_HEIGHT_M
from road_mesh_common import road_ue_z_m, ROAD_HALF_WIDTH
from build_terrain_mesh_tiles import M_PER_DEG_LAT, load_lake_beds, _point_in_polygon

STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
data = json.loads(STATIONS_PATH.read_text())
lat0, lon0 = data["origin_latlon"]
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
lakes = load_lake_beds()

WATER_PIER_SUBMERGENCE_M = 0.5
DECK_DEPTH_M = 1.25
WATER_CROSSING_MIN_CLEARANCE_M = 1.5

# Find the Swan Mountain Road segment with the near-miss shoreline stretch
target_seg = None
for seg in data["segments"]:
    names = set(seg.get("road_name", []))
    if "Swan Mountain Road" in names and len(seg["east_m"]) == 1523:
        target_seg = seg
        break
assert target_seg is not None, "Swan Mountain Road segment not found"

east = np.array(target_seg["east_m"]); north = np.array(target_seg["north_m"])
elev = np.array(target_seg["elev_m"])
s = np.array(target_seg["s_m"])
n = len(east)
road_z_m = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M)

tx = np.gradient(east); ty = np.gradient(north)
tl = np.hypot(tx, ty); tl[tl < 1e-6] = 1.0; tx /= tl; ty /= tl
rx, ry = -ty, tx

# lake z + in_lake per side at ROAD_HALF_WIDTH edge
edge_in_lake = {}
edge_lake_z = {}
for side in (-1.0, 1.0):
    ee = east + rx * ROAD_HALF_WIDTH * side
    nn = north + ry * ROAD_HALF_WIDTH * side
    cx = ee * 100.0; cy = -nn * 100.0
    inside = np.zeros(n, dtype=bool)
    lz = np.full(n, np.nan)
    for lake in lakes:
        bx0, bx1, by0, by1 = lake["bbox"]
        m = 5000.0
        bb = (cx >= bx0 - m) & (cx <= bx1 + m) & (cy >= by0 - m) & (cy <= by1 + m)
        if not bb.any():
            continue
        idx = np.where(bb)[0]
        hit = _point_in_polygon(cx[idx], cy[idx], lake["ring_x"], lake["ring_y"])
        inside[idx] |= hit
        lz[idx[hit]] = lake["z_cm"]
    edge_in_lake[side] = inside
    edge_lake_z[side] = lz

lake_side = -1.0  # the shore-side edge established earlier (all which_side==-1.0)
in_lake = edge_in_lake[lake_side]
lake_z_m = edge_lake_z[lake_side] / 100.0

window = slice(80, 220)
s_rel = s[window] - s[110]
road_z_w = road_z_m[window]
lake_z_w = lake_z_m[window]
in_lake_w = in_lake[window]

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1.6, 1]})

ax1.plot(s_rel, road_z_w, color="#333333", linewidth=2, label="road elevation (UE frame)")
lake_z_plot = np.where(np.isnan(lake_z_w), np.nan, lake_z_w)
ax1.plot(s_rel, lake_z_plot, color="#4a90d9", linewidth=2, label="baked lake surface Z (water bake)")
ax1.fill_between(s_rel, lake_z_plot - 3, lake_z_plot, color="#4a90d9", alpha=0.15, step=None)
ax1.axhline(0, color="none")
ax1.scatter(s_rel[in_lake_w], road_z_w[in_lake_w], color="#e8a33d", s=18, zorder=5,
            label="edge sampled inside lake polygon")
clearance_needed = lake_z_w + WATER_CROSSING_MIN_CLEARANCE_M
ax1.plot(s_rel, clearance_needed, color="#d62728", linestyle="--", linewidth=1,
          label=f"water-crossing guard: water+{WATER_CROSSING_MIN_CLEARANCE_M:.1f}m")
ax1.set_xlabel("along-road distance (m, relative)")
ax1.set_ylabel("elevation, UE frame (m)")
ax1.set_title("Swan Mountain Rd shoreline stretch:\nroad sits AT water level, never clears the guard\n(clearance -1.2..+0.6m, guard needs +1.5m)")
ax1.legend(fontsize=8, loc="upper right")
ax1.grid(alpha=0.3)
y_lo = np.nanmin([road_z_w.min(), np.nanmin(lake_z_w)]) - 1.0
y_hi = np.nanmax([road_z_w.max(), np.nanmax(clearance_needed)]) + 0.5
ax1.set_ylim(y_lo, y_hi)

# schematic cross-section for what a QUALIFYING water-crossing pier would
# look like (illustrative - no such run exists in this county's bake)
ax2.set_title("Water-crossing pier termination\n(schematic - the rule that WOULD fire\nif a run qualified)")
deck_top_y = 0.0
deck_bot_y = -DECK_DEPTH_M
water_y = -DECK_DEPTH_M - 2.0
pier_bottom_y = water_y - WATER_PIER_SUBMERGENCE_M
dem_lakebed_y = water_y - 3.5  # illustrative: raw DEM under water reads lower/noisier

ax2.add_patch(plt.Rectangle((-1, deck_bot_y), 6, DECK_DEPTH_M, color="#888888", label="deck"))
ax2.fill_between([-1, 5], water_y, deck_bot_y - 6, color="#4a90d9", alpha=0.35)
ax2.axhline(water_y, color="#1f5fa8", linewidth=1.5)
ax2.text(5.2, water_y + 0.3, "water surface\n(baked lake Z)", fontsize=8, color="#1f5fa8", va="center")
ax2.plot([2, 2], [deck_bot_y, pier_bottom_y], color="#555555", linewidth=10, solid_capstyle="butt")
ax2.axhline(pier_bottom_y, color="#d62728", linewidth=1, linestyle="--")
ax2.text(5.2, pier_bottom_y - 0.3, f"pier foot: water - {WATER_PIER_SUBMERGENCE_M:.1f}m\n(submerged)", fontsize=8, color="#d62728", va="center")
ax2.axhline(dem_lakebed_y, color="#999999", linewidth=1, linestyle=":")
ax2.text(5.2, dem_lakebed_y, "raw DEM 'lakebed'\n(noisy, NOT used)", fontsize=8, color="#999999", va="center")
ax2.set_xlim(-2, 9)
ax2.set_ylim(dem_lakebed_y - 1, deck_top_y + 1)
ax2.set_xticks([])
ax2.set_ylabel("elevation (schematic, m)")

fig.tight_layout()
out = ROOT / "scratchpad" / "verify_water_bridge_profile.png"
fig.savefig(out, dpi=150)
print("wrote", out)
