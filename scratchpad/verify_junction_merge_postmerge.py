"""Post-merge junction verification (backend-only, no app launch - see
memory/feedback_no_run_app_for_testing.md).

Re-derives junction_agreement_pass's own at-grade-crossing pair-finder
against the FINAL baked elevations in the freshly regenerated
road_stations.json (produced by this session's `python build_road_stations.py`
run, post audit-and-arc7-rescope merge into main) and reports how many
crossing pairs still disagree by more than the pass's own docstring-cited
audit thresholds (baseline before the fix: 37 sites > 0.3m, worst 1.09m).

If the merge wired the two passes (Arc 7.17 graph-junction flatten +
junction_agreement_pass final catch-all) together correctly, this should
come back near-zero.
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import road_mesh_common as rmc

OUT = Path(__file__).resolve().parent.parent / "data" / "road_tiles" / "summit_county_test"
data = json.loads((OUT / "road_stations.json").read_text())

east_m, north_m, elev_m, seg_idx, road_name, skip_flag = [], [], [], [], [], []
for si, seg in enumerate(data["segments"]):
    n = len(seg["east_m"])
    east_m.extend(seg["east_m"])
    north_m.extend(seg["north_m"])
    elev_m.extend(seg["elev_m"])
    seg_idx.extend([si] * n)
    road_name.extend(seg["road_name"])
    # is_bridge here is the same carve-exclusion flag junction_agreement_pass
    # was called with as junction_skip in build_road_stations.py's main().
    skip_flag.extend(seg["is_bridge"])

east_m = np.array(east_m); north_m = np.array(north_m); elev_m = np.array(elev_m)
seg_idx = np.array(seg_idx); skip_flag = np.array(skip_flag, dtype=bool)
road_name = np.array(road_name, dtype=object)
n_stations = len(east_m)
print(f"loaded {n_stations} station(s) across {len(data['segments'])} segment(s) "
      f"from the freshly-baked road_stations.json")

# Reproduce junction_agreement_pass's own pair-finder exactly (same radius/
# grade/gap constants), but on the FINAL post-merge elevations, to see what
# residual disagreement (if any) remains after both junction passes ran.
radius_m = rmc.JUNCTION_AGREE_RADIUS_M
max_grade = rmc.CONFLICT_MAX_GRADE
min_gap_m = rmc.CONFLICT_MIN_GAP_M
cell = radius_m
buckets = defaultdict(list)
for i in range(n_stations):
    if not skip_flag[i]:
        buckets[(int(east_m[i] // cell), int(north_m[i] // cell))].append(i)

r2 = radius_m * radius_m
pairs = []
for (bx, by), idxs in buckets.items():
    neigh = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            neigh.extend(buckets.get((bx + dx, by + dy), ()))
    for i in idxs:
        for j in neigh:
            if j <= i or seg_idx[i] == seg_idx[j]:
                continue
            de = east_m[i] - east_m[j]; dn = north_m[i] - north_m[j]
            d2 = de * de + dn * dn
            if d2 > r2:
                continue
            d = math.sqrt(d2)
            if abs(elev_m[i] - elev_m[j]) > max_grade * d + min_gap_m:
                continue  # a real conflict-sized gap (grade-separated), not an at-grade crossing
            pairs.append((i, j, d, abs(elev_m[i] - elev_m[j])))

print(f"\n{len(pairs)} at-grade cross-segment station pair(s) found within {radius_m}m "
      f"(same acceptance test junction_agreement_pass uses)")

gaps = np.array([p[3] for p in pairs])
if len(gaps):
    print(f"gap stats (m): min={gaps.min():.4f}  median={np.median(gaps):.4f}  "
          f"max={gaps.max():.4f}  mean={gaps.mean():.4f}")
    for thresh in [0.05, 0.1, 0.2, 0.3, 0.5, 1.0]:
        n_over = int((gaps > thresh).sum())
        print(f"  sites > {thresh:.2f}m: {n_over}")

    worst = sorted(pairs, key=lambda p: -p[3])[:15]
    print(f"\nworst {len(worst)} residual site(s):")
    for i, j, d, gap in worst:
        print(f"  {road_name[i]!r} (seg{int(seg_idx[i])}) vs {road_name[j]!r} "
              f"(seg{int(seg_idx[j])}): {d:.2f}m apart, {gap:.3f}m elevation gap "
              f"@ ({east_m[i]:.0f}E, {north_m[i]:.0f}N)")
else:
    print("no at-grade crossing pairs found at all (unexpected if any junctions exist)")

out_path = Path(__file__).resolve().parent / "junction_merge_verification.json"
out_path.write_text(json.dumps({
    "n_stations": n_stations,
    "n_pairs": len(pairs),
    "gap_stats_m": {
        "min": float(gaps.min()), "median": float(np.median(gaps)),
        "max": float(gaps.max()), "mean": float(gaps.mean())} if len(gaps) else None,
    "sites_over_threshold": {str(t): int((gaps > t).sum()) for t in [0.05, 0.1, 0.2, 0.3, 0.5, 1.0]} if len(gaps) else {},
}, indent=2))
print(f"\nwrote {out_path}")
