"""Top-down (plan view) look at how baked junctions merge, backend-only (no
app launch - see memory/feedback_no_run_app_for_testing.md).

Uses the freshly-baked road_stations.json (same run verify_junction_merge_
postmerge.py checked numerically). Finds real multi-road junction clusters
by grid-bucketing final stations and counting distinct segments within the
same JUNCTION_BOX_MARGIN_M+ROAD_HALF_WIDTH radius build_road_stations.py's
own Arc 7.17 pass uses, then plots east/north centerlines around the
busiest few (interchanges) plus the 3 sites from the elevation-profile plot,
colored by road name, with the junction "flush box" radius drawn as a
dashed circle.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import road_mesh_common as rmc

OUT = Path(__file__).resolve().parent.parent / "data" / "road_tiles" / "summit_county_test"
data = json.loads((OUT / "road_stations.json").read_text())
segs = data["segments"]

east_m, north_m, seg_idx, road_name = [], [], [], []
for si, seg in enumerate(segs):
    n = len(seg["east_m"])
    east_m.extend(seg["east_m"]); north_m.extend(seg["north_m"])
    seg_idx.extend([si] * n); road_name.extend(seg["road_name"])
east_m = np.array(east_m); north_m = np.array(north_m)
seg_idx = np.array(seg_idx); road_name = np.array(road_name, dtype=object)

box_r = rmc.ROAD_HALF_WIDTH + rmc.JUNCTION_BOX_MARGIN_M
cell = box_r
buckets = defaultdict(list)
for i in range(len(east_m)):
    buckets[(int(east_m[i] // cell), int(north_m[i] // cell))].append(i)

# find cluster centers with the most DISTINCT segments within box_r - proxy
# for "real, busy junction" (interchanges/multi-way intersections) without
# needing the raw OSM node ids main() used internally.
r2 = box_r * box_r
best = []
for (bx, by), idxs in buckets.items():
    neigh = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            neigh.extend(buckets.get((bx + dx, by + dy), ()))
    if len(neigh) < 6:
        continue
    cx, cy = np.mean(east_m[idxs]), np.mean(north_m[idxs])
    near = [k for k in neigh if (east_m[k] - cx) ** 2 + (north_m[k] - cy) ** 2 <= r2]
    n_segs = len(set(seg_idx[k] for k in near))
    if n_segs >= 3:
        best.append((n_segs, float(cx), float(cy)))

# de-dupe nearby cluster centers (same physical junction found from several
# grid cells), keep the highest n_segs per ~box_r*2 neighborhood
best.sort(key=lambda t: -t[0])
kept_centers = []
for n_segs, cx, cy in best:
    if all((cx - kx) ** 2 + (cy - ky) ** 2 > (2 * box_r) ** 2 for _, kx, ky in kept_centers):
        kept_centers.append((n_segs, cx, cy))
print(f"{len(kept_centers)} distinct multi-road junction cluster(s) found "
      f"(3+ distinct segments within {box_r:.1f}m)")
for n_segs, cx, cy in kept_centers[:10]:
    print(f"  {n_segs} segment(s) @ ({cx:.0f}E, {cy:.0f}N)")

# top busiest interchanges by segment count, plus the 3 named sites already
# checked in the elevation-profile plot
SITES = [(f"{n_segs}-way junction", cx, cy) for n_segs, cx, cy in kept_centers[:4]]
SITES += [
    ("Blue River Pkwy x Golden Eagle Rd", 8024, 14507),
    ("CO 91 self-crossing", 3221, -3224),
    ("Falcon Dr x Red Hawk Rd", 7149, 14529),
]

n = len(SITES)
ncols = 3
nrows = (n + ncols - 1) // ncols
fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 5 * nrows))
axes = np.atleast_1d(axes).ravel()

PAD = 50.0
for ax, (title, cx, cy) in zip(axes, SITES):
    near = (np.abs(east_m - cx) <= PAD) & (np.abs(north_m - cy) <= PAD)
    for si in sorted(set(seg_idx[near].tolist())):
        m = near & (seg_idx == si)
        name = next((r for r in road_name[m] if r), "") or "(unnamed)"
        ax.plot(east_m[m], north_m[m], "o-", markersize=3, linewidth=1.5, label=f"{name} (seg{si})")
    circ = plt.Circle((cx, cy), box_r, fill=False, linestyle="--", color="red", linewidth=1)
    ax.add_patch(circ)
    ax.set_title(title, fontsize=10)
    ax.set_xlim(cx - PAD, cx + PAD); ax.set_ylim(cy - PAD, cy + PAD)
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    ax.legend(fontsize=6, loc="best")
    ax.grid(alpha=0.3)

for ax in axes[n:]:
    ax.axis("off")

fig.suptitle("Baked junctions, top-down (plan view) - dashed circle = junction flush-box radius", fontsize=12)
fig.tight_layout(rect=[0, 0, 1, 0.97])
out_path = Path(__file__).resolve().parent / "junctions_topdown_postmerge.png"
fig.savefig(out_path, dpi=130)
print(f"\nwrote {out_path}")
