"""Offline inventory (step 1 of the water-crossing-bridge task): intersect
road station polylines (both pavement edges, ROAD_HALF_WIDTH offset - same
convention build_road_embankments.py's compute_side() uses) against the
baked lake polygons, and classify every named road with any qualifying
station into CROSSING (both edges inside a lake polygon at the same
station - water under the whole roadbed) vs SKIRTING (only one edge inside
- shoreline-following, e.g. a dam top or lakeside road) vs none.

Run from repo root: python scratchpad/inventory_water_crossings.py
"""
import json
import math
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import numpy as np
from road_mesh_common import ROAD_HALF_WIDTH
from test_areas import AREA_NAME
from build_terrain_mesh_tiles import M_PER_DEG_LAT, load_lake_beds, _point_in_polygon

STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"

data = json.loads(STATIONS_PATH.read_text())
lat0, lon0 = data["origin_latlon"]
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

lakes = load_lake_beds()
print(f"{len(lakes)} lake polygon(s) loaded")

EDGE_OFFSET_M = ROAD_HALF_WIDTH

# per-road accumulators
road_stats = defaultdict(lambda: {
    "n_stations": 0, "n_both_in": 0, "n_one_in": 0,
    "lats": [], "lons": [], "which_side": [],  # for one-sided cases: -1 or +1 which side is wet
})

for seg in data["segments"]:
    n = len(seg["east_m"])
    if n < 2:
        continue
    east = np.array(seg["east_m"])
    north = np.array(seg["north_m"])
    road_name = seg.get("road_name", [""] * n)

    tx = np.gradient(east)
    ty = np.gradient(north)
    tl = np.hypot(tx, ty)
    tl[tl < 1e-6] = 1.0
    tx /= tl
    ty /= tl
    rx, ry = -ty, tx

    in_edge = {}
    for side in (-1.0, 1.0):
        edge_e = east + rx * EDGE_OFFSET_M * side
        edge_n = north + ry * EDGE_OFFSET_M * side
        edge_cx = edge_e * 100.0
        edge_cy = -edge_n * 100.0
        inside = np.zeros(n, dtype=bool)
        for lake in lakes:
            bx0, bx1, by0, by1 = lake["bbox"]
            margin = 5000.0  # 50 m bbox pre-filter margin, cm
            bb = ((edge_cx >= bx0 - margin) & (edge_cx <= bx1 + margin) &
                  (edge_cy >= by0 - margin) & (edge_cy <= by1 + margin))
            if not bb.any():
                continue
            idx = np.where(bb)[0]
            hit = _point_in_polygon(edge_cx[idx], edge_cy[idx], lake["ring_x"], lake["ring_y"])
            inside[idx] |= hit
        in_edge[side] = inside

    both_in = in_edge[-1.0] & in_edge[1.0]
    one_in = (in_edge[-1.0] | in_edge[1.0]) & ~both_in
    any_in = in_edge[-1.0] | in_edge[1.0]

    if not any_in.any():
        continue

    for i in range(n):
        nm = road_name[i] or "(unnamed)"
        rs = road_stats[nm]
        if both_in[i]:
            rs["n_both_in"] += 1
            lat = lat0 + north[i] / M_PER_DEG_LAT
            lon = lon0 + east[i] / m_per_deg_lon
            rs["lats"].append(lat); rs["lons"].append(lon)
        elif one_in[i]:
            rs["n_one_in"] += 1
            lat = lat0 + north[i] / M_PER_DEG_LAT
            lon = lon0 + east[i] / m_per_deg_lon
            rs["lats"].append(lat); rs["lons"].append(lon)
            rs["which_side"].append(1.0 if in_edge[1.0][i] else -1.0)

print()
print(f"{'road name':35s} {'both-in (crossing)':>20s} {'one-in (skirting)':>20s}  sample lat,lon")
for nm, rs in sorted(road_stats.items(), key=lambda kv: -(kv[1]['n_both_in'] + kv[1]['n_one_in'])):
    if not rs["lats"]:
        continue
    mid = len(rs["lats"]) // 2
    print(f"{nm:35s} {rs['n_both_in']:>20d} {rs['n_one_in']:>20d}  {rs['lats'][mid]:.5f},{rs['lons'][mid]:.5f}")

out = {}
for nm, rs in road_stats.items():
    out[nm] = {"n_both_in": rs["n_both_in"], "n_one_in": rs["n_one_in"],
               "lats": rs["lats"], "lons": rs["lons"], "which_side": rs["which_side"]}
Path(ROOT / "scratchpad" / "water_crossing_inventory.json").write_text(json.dumps(out))
print("\nwritten scratchpad/water_crossing_inventory.json")
