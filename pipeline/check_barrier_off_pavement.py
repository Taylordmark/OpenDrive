"""Offline gate (2026-10-22): no shipped barrier tree/bush point
(barrier_<area>_test_tile_*.bin, AODBarrierManager's "wall of scenery") stands
on shipped road pavement (MasksV2/roadv3_*, ribbons and junction pads). At
Deals Gap's Copp Circle spawn 28 of them stood in the junction: each road's
13 m offset row crossed the roads it met (build_road_barrier.py).

Usage:  OD_AREA=<area> python pipeline/check_barrier_off_pavement.py [content_opendrive_dir]
Exit 1 on any point on pavement, listing the first few.
"""
import glob
import struct
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from export_pad_points_bin import chunks
from test_areas import AREA_NAME

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive"


def barrier_points():
    pts = []
    for p in glob.glob(str(ROOT / "Masks" / f"barrier_{AREA_NAME}_test_tile_*.bin")):
        b = Path(p).read_bytes()
        (n,) = struct.unpack_from("<I", b, 20)
        pts.append(np.frombuffer(b, "<f4", n * 4, 24).reshape(-1, 4)[:, :2])
    return np.concatenate(pts).astype(np.float64) if pts else np.zeros((0, 2))


def on_pavement(pts):
    """Mask of pts (UE cm) inside any road-tile triangle, in plan."""
    tri = np.concatenate([v[t][:, :, :2] for p in sorted((ROOT / "MasksV2").glob(f"roadv3_{AREA_NAME}_test_tile_*.bin"))
                          for v, _, t in chunks(p)]).astype(np.float64)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    det = (b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1]) - (c[:, 0] - a[:, 0]) * (b[:, 1] - a[:, 1])
    keep = np.abs(det) > 1.0
    a, b, c, det = a[keep], b[keep], c[keep], det[keep]
    reach = np.max([np.hypot(*(b - a).T), np.hypot(*(c - a).T)])
    hit = np.zeros(len(pts), bool)
    for i, cand in enumerate(cKDTree(a).query_ball_point(pts, reach)):
        if not cand:
            continue
        k = np.array(cand)
        d = pts[i] - a[k]
        u = (d[:, 0] * (c[k, 1] - a[k, 1]) - d[:, 1] * (c[k, 0] - a[k, 0])) / det[k]
        v = (d[:, 1] * (b[k, 0] - a[k, 0]) - d[:, 0] * (b[k, 1] - a[k, 1])) / det[k]
        hit[i] = ((u >= 0) & (v >= 0) & (u + v <= 1)).any()
    return hit


def main():
    pts = barrier_points()
    hit = on_pavement(pts)
    print(f"{AREA_NAME}: {len(pts)} barrier points, {int(hit.sum())} on road pavement")
    for x, y in pts[hit][:10]:
        print(f"  on pavement at UE ({x:.0f}, {y:.0f}) cm")
    sys.exit(1 if hit.any() else 0)


if __name__ == "__main__":
    main()
