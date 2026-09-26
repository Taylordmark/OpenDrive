"""Follow-up inventory: for named roads of interest (Dillon Dam Road, North
Dillon Dam Road, Swan Mountain Road, US 6, CO 9, I-70/Gerald R. Ford
Memorial Highway), report per-station distance from the CENTERLINE to the
nearest lake polygon boundary (and whether centerline/either edge is
inside), so we can see near-misses, not just hits at the +-4.5m edge
offset. Also identifies the "(unnamed)" chain from the primary inventory.
"""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import numpy as np
from road_mesh_common import ROAD_HALF_WIDTH
from test_areas import AREA_NAME
from build_terrain_mesh_tiles import M_PER_DEG_LAT, load_lake_beds, _point_in_polygon, _dist_to_ring

STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
data = json.loads(STATIONS_PATH.read_text())
lat0, lon0 = data["origin_latlon"]
m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
lakes = load_lake_beds()

TARGETS = ["Dillon Dam Road", "North Dillon Dam Road", "Swan Mountain Road",
           "US 6", "US Highway 6", "Colorado 9", "CO 9", "State Highway 9",
           "Gerald R. Ford Memorial Highway", "I-70"]

def min_dist_to_lakes(cx_cm, cy_cm, cand_lakes):
    n = len(cx_cm)
    best = np.full(n, 1e18)
    for lake in cand_lakes:
        d = _dist_to_ring(cx_cm, cy_cm, lake["ring_x"], lake["ring_y"])
        best = np.minimum(best, d)
    return best / 100.0  # m

for seg in data["segments"]:
    n = len(seg["east_m"])
    if n < 2:
        continue
    road_name = seg.get("road_name", [""] * n)
    names_here = set(nm for nm in road_name if nm)
    if not any(any(t in nm for t in TARGETS) for nm in names_here):
        continue
    east = np.array(seg["east_m"]); north = np.array(seg["north_m"])
    cx_cm = east * 100.0
    cy_cm = -north * 100.0
    seg_bx0, seg_bx1 = cx_cm.min(), cx_cm.max()
    seg_by0, seg_by1 = cy_cm.min(), cy_cm.max()
    pad = 100000.0  # 1 km cm
    cand_lakes = [lk for lk in lakes if not (
        lk["bbox"][1] < seg_bx0 - pad or lk["bbox"][0] > seg_bx1 + pad or
        lk["bbox"][3] < seg_by0 - pad or lk["bbox"][2] > seg_by1 + pad)]
    if not cand_lakes:
        continue
    dmins = min_dist_to_lakes(cx_cm, cy_cm, cand_lakes)
    if dmins.min() > 200:
        continue  # nowhere near any lake, skip
    top_nm = max(names_here, key=lambda nm: sum(1 for x in road_name if x == nm))
    i_min = int(np.argmin(dmins))
    lat = lat0 + north[i_min] / M_PER_DEG_LAT
    lon = lon0 + east[i_min] / m_per_deg_lon
    print(f"{top_nm:35s} seg n={n:4d} min_dist_to_lake={dmins.min():7.1f} m  at ({lat:.5f},{lon:.5f})  "
          f"n_within_10m={int((dmins<=10).sum())} n_within_50m={int((dmins<=50).sum())} "
          f"({len(cand_lakes)} candidate lake(s))")
