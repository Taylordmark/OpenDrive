"""Offline check of landmarks_<area>_test.json (build_landmarks.write_sidecar,
read by AODChallengeManager for landmark hunting) against the stage's
landmarks.json summary. Asserts:
  - the same landmarks, in order, names non-empty and unique
  - centroid within 1 cm of the footprint's vertex mean and inside the footprint
  - radius = the farthest footprint vertex (within 1 cm), height_m = the summary's
  - then the summary's discover-only pois, unchanged, kind "poi", radius > 0

Usage:  python pipeline/checks/verify_landmark_sidecar.py [area] [--masks DIR] [--summary FILE]
Exits non-zero on any failure.
"""
import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "pipeline" / "checks"))
from test_areas import AREA_NAME  # noqa: E402
from verify_landmarks import inside  # noqa: E402

MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("area", nargs="?", default=AREA_NAME)
    ap.add_argument("--masks", type=Path, default=MASKS)
    ap.add_argument("--summary", type=Path)
    a = ap.parse_args()
    side = json.loads((a.masks / f"landmarks_{a.area}_test.json").read_text(encoding="utf-8"))["landmarks"]
    d = json.loads((a.summary or ROOT / "data" / "road_tiles" / f"{a.area}_test" / "landmarks.json").read_text())
    summ, pois = d["landmarks"], d.get("pois", [])
    fails = []
    names = [s["name"] for s in side]
    if side[len(summ):] != pois or any(p["kind"] != "poi" or p["radius_cm"] <= 0 for p in pois):
        fails.append(f"pois {side[len(summ):]} != summary {pois}")
    side = side[:len(summ)]
    if [s["name"] for s in side] != [s["name"] for s in summ]:
        fails.append(f"names {names} != summary {[s['name'] for s in summ]}")
    if any(not n for n in names) or len(set(names)) != len(names):
        fails.append(f"empty or duplicate names: {names}")
    for s, m in zip(side, summ):
        ring = [tuple(p) for p in m["footprint"]]
        mx, my = sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)
        cx, cy = s["centroid_cm"]
        if math.dist((cx, cy), (mx, my)) > 1 or not inside(ring, cx, cy):
            fails.append(f"{s['name']}: centroid {cx, cy} vs footprint mean {mx:.1f}, {my:.1f}")
        r = max(math.dist((cx, cy), p) for p in ring)
        if abs(r - s["radius_cm"]) > 1 or s["radius_cm"] <= 0:
            fails.append(f"{s['name']}: radius {s['radius_cm']} vs {r:.1f}")
        if s["height_m"] != m["height_m"] or s["kind"] != "building":
            fails.append(f"{s['name']}: height {s['height_m']} / kind {s['kind']}")
        print(f"  {s['name']}: radius {s['radius_cm'] / 100:.1f} m, {s['height_m']} m tall")
    print(f"{a.area}: {len(side)} built landmark(s), {len(pois)} poi(s)")
    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
