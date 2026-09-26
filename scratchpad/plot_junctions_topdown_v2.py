"""Corrected top-down junction plot (backend-only, no app launch).

v1 (plot_junctions_topdown.py) drew its junction-box circle from a cheap
proxy (mean position of one grid cell's own stations) - diagnose_junction_
centers.py showed that proxy was off by 4-12m from the pipeline's own real
in_junction_box ground truth for 2 of the 3 flagged panels, and for the 3rd
(CO 91 "self-crossing") there was no real junction box within 35m at all.

This version centers every circle on an ACTUAL in_junction_box station
cluster (the pipeline's own real-junction ground truth), and adds a wide
view of the CO 91 site to show what it actually is.
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
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

# cluster TRUE in-box stations (pipeline's own real-junction ground truth)
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


cell = 10.0
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
    real_junctions.append((n_segs, cx, cy))
real_junctions.sort(key=lambda t: -t[0])

box_r = 8.5
SITES = [(f"REAL {n_segs}-way junction (corrected center)", cx, cy, 50.0, True)
         for n_segs, cx, cy in real_junctions[:5]]
# CO 91 site, wide view - what IS it, if not a real junction box?
SITES.append(("CO 91 'self-crossing' - wide view", 3221, -3224, 250.0, False))

ncols = 3
nrows = (len(SITES) + ncols - 1) // ncols
fig, axes = plt.subplots(nrows, ncols, figsize=(5 * ncols, 5 * nrows))
axes = np.atleast_1d(axes).ravel()

for ax, (title, cx, cy, pad, draw_circle) in zip(axes, SITES):
    near = (np.abs(east_m - cx) <= pad) & (np.abs(north_m - cy) <= pad)
    for si in sorted(set(seg_idx[near].tolist())):
        m = near & (seg_idx == si)
        name = next((r for r in road_name[m] if r), "") or "(unnamed)"
        in_box_m = in_box[m]
        ax.plot(east_m[m], north_m[m], "-", linewidth=1.2, alpha=0.6, label=f"{name} (seg{si})")
        ax.scatter(east_m[m][~in_box_m], north_m[m][~in_box_m], s=10)
        if in_box_m.any():
            ax.scatter(east_m[m][in_box_m], north_m[m][in_box_m], s=25, marker="s",
                       edgecolors="black", linewidths=0.5, zorder=5)
    if draw_circle:
        ax.add_patch(plt.Circle((cx, cy), box_r, fill=False, linestyle="--", color="red", linewidth=1))
    ax.set_title(title, fontsize=10)
    ax.set_xlim(cx - pad, cx + pad); ax.set_ylim(cy - pad, cy + pad)
    ax.set_aspect("equal")
    ax.set_xlabel("east (m)"); ax.set_ylabel("north (m)")
    ax.legend(fontsize=6, loc="best")
    ax.grid(alpha=0.3)

for ax in axes[len(SITES):]:
    ax.axis("off")

fig.suptitle("Corrected: circles centered on real in_junction_box clusters "
             "(black squares = actual flush-box stations)", fontsize=11)
fig.tight_layout(rect=[0, 0, 1, 0.96])
out_path = Path(__file__).resolve().parent / "junctions_topdown_v2.png"
fig.savefig(out_path, dpi=130)
print(f"wrote {out_path}")
