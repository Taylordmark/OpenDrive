"""§7.34 verification (Finding 1): using the REAL pre-reconciliation station
arrays captured by capture_reconcile_pairs.py, reproduce reconcile_junction_
elevations()'s same-segment candidate-pair set exactly as the PRE-fix code
selected it (in_scope = True unconditionally for same_seg, i.e. only bounded
by the radius_m=20 search + the existing is_bridge/wall_min_grade filters),
then report the pairwise plan-distance distribution to check the claimed
"clean gap" (all 242 false pairs >= 4.67m, nothing between 0 and 4.67m) that
justifies picking JUNCTION_SAME_SEG_RADIUS_M = 3.0.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import road_mesh_common as rmc

data = np.load(Path(__file__).resolve().parent / "reconcile_pre_arrays.npz", allow_pickle=True)
east_m, north_m, elev_m = data["east_m"], data["north_m"], data["elev_m"]
seg_idx, station_idx, road_name = data["seg_idx"], data["station_idx"], data["road_name"]
is_bridge = data["is_bridge"]

pairs_i, pairs_j = rmc.detect_elevation_conflict_pairs(
    east_m, north_m, elev_m, seg_idx, station_idx,
    rmc.JUNCTION_RECONCILE_RADIUS_M, rmc.CONFLICT_MAX_GRADE,
    rmc.CONFLICT_MIN_GAP_M, rmc.CONFLICT_SAME_CHAIN_GAP_STATIONS)
print(f"detect_elevation_conflict_pairs: {len(pairs_i)} candidate pair(s) within "
      f"{rmc.JUNCTION_RECONCILE_RADIUS_M}m search radius")

same_seg_dists = []
same_name_cross_seg = 0
neither = 0
for k in range(len(pairs_i)):
    i, j = int(pairs_i[k]), int(pairs_j[k])
    if is_bridge[i] or is_bridge[j]:
        continue
    same_seg = seg_idx[i] == seg_idx[j]
    same_name = bool(road_name[i]) and road_name[i] == road_name[j]
    dist_m = math.hypot(east_m[j] - east_m[i], north_m[j] - north_m[i])
    grade = abs(elev_m[j] - elev_m[i]) / dist_m if dist_m > 1e-6 else float("inf")
    if grade >= rmc.WALL_MIN_GRADE:
        continue  # excluded by the wall_min_grade filter both before/after this fix
    if same_seg:
        same_seg_dists.append(dist_m)
    elif same_name:
        same_name_cross_seg += 1
    else:
        neither += 1

same_seg_dists = np.array(sorted(same_seg_dists))
print(f"\nsame-segment candidate pairs passing wall_min_grade + is_bridge exclusion: "
      f"{len(same_seg_dists)}")
print(f"same-name cross-segment candidates (unaffected by this fix): {same_name_cross_seg}")
print(f"neither same-seg nor same-name (dropped regardless): {neither}")

if len(same_seg_dists):
    print(f"\nsame-segment pair distance stats (meters):")
    print(f"  min={same_seg_dists.min():.3f}  max={same_seg_dists.max():.3f}  "
          f"median={np.median(same_seg_dists):.3f}")
    for thresh in [1.1, 2.4, 3.0, 4.0, 4.67, 5.0, 10.0, 15.0, 20.0]:
        n_le = int((same_seg_dists <= thresh).sum())
        print(f"  <= {thresh:5.2f}m: {n_le}")
    frac_outer5 = float((same_seg_dists >= 15.0).mean())
    print(f"  fraction in outermost 5m of 20m radius (>=15m): {frac_outer5*100:.1f}%")
    # gap check: largest gap in the sorted distance array
    gaps = np.diff(same_seg_dists)
    if len(gaps):
        gi = int(np.argmax(gaps))
        print(f"  largest gap in sorted distances: {gaps[gi]:.3f}m, between "
              f"{same_seg_dists[gi]:.3f}m and {same_seg_dists[gi+1]:.3f}m")

out_path = Path(__file__).resolve().parent / "verify_same_seg_radius_fix.txt"
with open(out_path, "w") as f:
    f.write(f"same-segment candidate pairs (wall_min_grade-filtered, is_bridge NOT excluded): "
            f"{len(same_seg_dists)}\n")
    f.write(f"distances (sorted): {same_seg_dists.tolist()}\n")
print(f"\nwrote {out_path}")
