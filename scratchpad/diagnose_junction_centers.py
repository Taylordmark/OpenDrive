"""Diagnose whether the 3 flagged panels (US6/Dillon Dam 5-way, Blue River
Pkwy x Golden Eagle, CO 91 self-crossing) in junctions_topdown_postmerge.png
are actually mis-centered in the BAKED DATA, or whether the previous plot
script's own centroid heuristic (mean of one grid cell's own stations, not
the real junction) just drew the dashed circle in the wrong spot.

Ground truth this time: road_stations.json's own per-station
`in_junction_box` flag - this is the EXACT set of stations
build_road_stations.py's real (graph-degree) junction pass flattened, so
clustering THOSE gives the pipeline's own real junction locations, not a
proxy.
"""
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parent.parent / "data" / "road_tiles" / "summit_county_test"
data = json.loads((OUT / "road_stations.json").read_text())
segs = data["segments"]

east_m, north_m, seg_idx, road_name, in_box = [], [], [], [], []
for si, seg in enumerate(segs):
    n = len(seg["east_m"])
    east_m.extend(seg["east_m"]); north_m.extend(seg["north_m"])
    seg_idx.extend([si] * n); road_name.extend(seg["road_name"])
    in_box.extend(seg["in_junction_box"])
east_m = np.array(east_m); north_m = np.array(north_m)
seg_idx = np.array(seg_idx); road_name = np.array(road_name, dtype=object)
in_box = np.array(in_box, dtype=bool)
print(f"{int(in_box.sum())} station(s) flagged in_junction_box across the whole area")

# cluster the TRUE in-box stations by simple union-find on proximity (2m -
# well under any real junction spacing) to get the pipeline's own real
# junction locations and participant segments.
idxs = np.nonzero(in_box)[0]
parent = {i: i for i in idxs}


def find(x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[ra] = rb


cell = 10.0  # > STATION_SPACING (~5m) so consecutive in-box stations along
             # the SAME segment's own approach actually link into one cluster
buckets = defaultdict(list)
for i in idxs:
    buckets[(int(east_m[i] // cell), int(north_m[i] // cell))].append(i)
for (bx, by), cell_idxs in buckets.items():
    neigh = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            neigh.extend(buckets.get((bx + dx, by + dy), ()))
    for i in cell_idxs:
        for j in neigh:
            if i != j and (east_m[i] - east_m[j]) ** 2 + (north_m[i] - north_m[j]) ** 2 <= cell * cell:
                union(i, j)

clusters = defaultdict(list)
for i in idxs:
    clusters[find(i)].append(i)

real_junctions = []
for members in clusters.values():
    members = np.array(members)
    cx, cy = float(east_m[members].mean()), float(north_m[members].mean())
    n_segs = len(set(seg_idx[members].tolist()))
    real_junctions.append((n_segs, cx, cy, len(members)))
real_junctions.sort(key=lambda t: -t[0])
print(f"{len(real_junctions)} real junction cluster(s) (from in_junction_box, pipeline's own ground truth)")

# check the 3 flagged sites against the nearest TRUE real-junction cluster
CHECK_SITES = [
    ("US6/Dillon Dam 5-way (panel 2)", 10453, 11506),
    ("Blue River Pkwy x Golden Eagle (panel 5)", 8024, 14507),
    ("CO 91 self-crossing (panel 6)", 3221, -3224),
]
for label, px, py in CHECK_SITES:
    dists = [((cx - px) ** 2 + (cy - py) ** 2) ** 0.5 for _, cx, cy, _ in real_junctions]
    j = int(np.argmin(dists))
    n_segs, cx, cy, n_stations = real_junctions[j]
    print(f"\n{label}: plotted circle @ ({px},{py})")
    print(f"  nearest TRUE real-junction cluster: {n_segs} seg(s), {n_stations} in-box station(s) "
          f"@ ({cx:.1f}E, {cy:.1f}N), {dists[j]:.1f}m away from the plotted circle center")
    if dists[j] > 15.0:
        print(f"  -> NO real graph-junction (in_junction_box) cluster near this site at all - "
              f"the Arc 7.17 pass never flattened this location. The circle was drawn by the "
              f"PREVIOUS SCRIPT's own proxy heuristic, not the pipeline's real junction data.")

print(f"\ntop 10 real junction clusters by segment count:")
for n_segs, cx, cy, n_stations in real_junctions[:10]:
    print(f"  {n_segs} seg(s), {n_stations} station(s) @ ({cx:.1f}E, {cy:.1f}N)")
