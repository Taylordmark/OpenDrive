"""Post-merge junction verification, part 2 (backend-only plot, no app
launch - see memory/feedback_no_run_app_for_testing.md).

Plots elevation vs. distance-from-crossing for a few named at-grade road
crossings, using the FRESHLY baked road_stations.json, to visually confirm
the two roads' pavement actually meets at the same height post-merge
instead of just checking it numerically (verify_junction_merge_postmerge.py
already did the numeric check: worst residual gap 0.150m).
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

OUT = Path(__file__).resolve().parent.parent / "data" / "road_tiles" / "summit_county_test"
data = json.loads((OUT / "road_stations.json").read_text())
segs = data["segments"]

# (name_a, seg_a, name_b, seg_b, crossing_east, crossing_north) - picked from
# verify_junction_merge_postmerge.py's worst-residual list, including the
# original Blue River Parkway / Silverthorne bug-report site.
SITES = [
    ("Blue River Parkway", 314, "Golden Eagle Road", 1400, 8024, 14507),
    ("CO 91", 254, "CO 91", 257, 3221, -3224),
    ("Falcon Drive", 127, "Red Hawk Road", 663, 7149, 14529),
]

fig, axes = plt.subplots(len(SITES), 1, figsize=(8, 3.2 * len(SITES)))
for ax, (name_a, si_a, name_b, si_b, cx, cy) in zip(axes, SITES):
    for name, si, color in [(name_a, si_a, "tab:blue"), (name_b, si_b, "tab:orange")]:
        seg = segs[si]
        e = np.array(seg["east_m"]); n = np.array(seg["north_m"]); h = np.array(seg["elev_m"])
        d_from_crossing = np.hypot(e - cx, n - cy)
        near = d_from_crossing <= 60.0
        # signed distance along the array so the crossing sits near x=0 on the plot
        idx_near = np.nonzero(near)[0]
        if len(idx_near) == 0:
            continue
        closest = idx_near[np.argmin(d_from_crossing[idx_near])]
        s = np.array(seg["s_m"])
        s_rel = s - s[closest]
        ax.plot(s_rel[near], h[near], "o-", color=color, markersize=3,
                label=f"{name!r} (seg{si})")
    ax.axvline(0, color="gray", linestyle="--", linewidth=1)
    ax.set_title(f"{name_a!r} x {name_b!r} @ ({cx:.0f}E, {cy:.0f}N)")
    ax.set_xlabel("arc-length distance from crossing (m)")
    ax.set_ylabel("elevation (m)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)

fig.tight_layout()
out_path = Path(__file__).resolve().parent / "junction_crossings_postmerge.png"
fig.savefig(out_path, dpi=130)
print(f"wrote {out_path}")
