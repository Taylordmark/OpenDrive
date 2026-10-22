"""Offline check of challenges_<area>_test.json (pipeline/build_speed_challenges.py)
against the area's road stations. Asserts:
  - >= 1 trap; names non-empty and unique across traps and zones
  - every trap within 2 m of a road station, on a straight of >= 400 m, its
    yaw within 10 deg of the road there (either way), traps >= 1 km apart
  - every zone 1-3 km long by its line (within 5% of length_m), every line
    point within 2 m of a station, zones >= 1 km apart end to end
    (AODChallengeManager uses line[0] / line[-1] as the gates)

Usage:  python pipeline/checks/verify_speed_challenges.py [area] [--masks DIR] [--stations FILE]
Exits non-zero on any failure.
"""
import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from test_areas import AREA_NAME  # noqa: E402

MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("area", nargs="?", default=AREA_NAME)
    ap.add_argument("--masks", type=Path, default=MASKS)
    ap.add_argument("--stations", type=Path)
    a = ap.parse_args()
    ch = json.loads((a.masks / f"challenges_{a.area}_test.json").read_text(encoding="utf-8"))
    segs = json.loads((a.stations or ROOT / "data" / "road_tiles" / f"{a.area}_test" / "road_stations.json")
                      .read_text())["segments"]
    xy = np.concatenate([np.column_stack([np.array(s["east_m"]) * 100, -np.array(s["north_m"]) * 100]) for s in segs])
    fails = []

    def near(x, y):
        d = np.hypot(xy[:, 0] - x, xy[:, 1] - y)
        i = int(np.argmin(d))
        return d[i], i

    names = [t["name"] for t in ch["traps"]] + [z["name"] for z in ch["zones"]]
    if not ch["traps"]:
        fails.append("no traps")
    if any(not n for n in names) or len(set(names)) != len(names):
        fails.append(f"empty or duplicate names: {names}")

    for t in ch["traps"]:
        d, i = near(t["x_cm"], t["y_cm"])
        if d > 200:
            fails.append(f"{t['name']}: {d / 100:.1f} m off the road")
        j = min(i + 4, len(xy) - 1)
        road = math.degrees(math.atan2(xy[j, 1] - xy[i, 1], xy[j, 0] - xy[i, 0]))
        off = abs((t["yaw_deg"] - road + 90) % 180 - 90)
        if off > 10:
            fails.append(f"{t['name']}: yaw {t['yaw_deg']} vs road {road:.1f}")
        if t["straight_m"] < 400:
            fails.append(f"{t['name']}: straight only {t['straight_m']} m")
        print(f"  trap {t['name']}: {t['straight_m']} m straight, {d / 100:.2f} m from road, yaw off {off:.1f} deg")
    for p in range(len(ch["traps"])):
        for q in range(p):
            tp, tq = ch["traps"][p], ch["traps"][q]
            if math.hypot(tp["x_cm"] - tq["x_cm"], tp["y_cm"] - tq["y_cm"]) < 1e5 - 1:
                fails.append(f"{tp['name']} / {tq['name']}: under 1 km apart")

    for z in ch["zones"]:
        line = z["line_cm"]
        length = sum(math.dist(line[k][:2], line[k + 1][:2]) for k in range(len(line) - 1)) / 100
        if not 1000 <= z["length_m"] <= 3000 or abs(length - z["length_m"]) > 0.05 * z["length_m"]:
            fails.append(f"{z['name']}: length_m {z['length_m']}, line {length:.0f} m")
        worst = max(near(p[0], p[1])[0] for p in line)
        if worst > 200:
            fails.append(f"{z['name']}: line {worst / 100:.1f} m off the road")
        print(f"  zone {z['name']}: {z['length_m']} m, line {length:.0f} m, worst {worst / 100:.2f} m off road")
    ends = [(z["name"], p) for z in ch["zones"] for p in (z["line_cm"][0], z["line_cm"][-1])]
    for p in range(len(ends)):
        for q in range(p):
            if ends[p][0] != ends[q][0] and math.dist(ends[p][1][:2], ends[q][1][:2]) < 1e5 - 1:
                fails.append(f"{ends[p][0]} / {ends[q][0]}: gates under 1 km apart")

    print(f"{a.area}: {len(ch['traps'])} trap(s), {len(ch['zones'])} zone(s)")
    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
