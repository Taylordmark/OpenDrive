"""Exports road_stations.json's precise centerline positions as a runtime
sidecar, so AODForestManager can reject tree candidates by geometric
distance-to-centerline instead of a physics raycast against the road
mesh's PM_Asphalt tag (2026-07-20, alongside build_forest_elevation.py -
same motivation: make tree placement fully data-driven, not dependent on
whatever's currently loaded/resident at runtime). Full 5 m station spacing,
not downsampled - the exclusion zone this feeds (road half-width + skirt +
clearance, ~11.5 m) is narrow enough that coarser spacing would introduce
real slop, and the file size is trivial either way (a few hundred KB).

Usage:  python export_road_centerline_bin.py    (needs road_stations.json)

Writes  OpenDriveUE/Content/OpenDrive/Masks/centerline_<AREA_NAME>_test.bin
        'ODC1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm)

ALSO writes a second, map-drawing-only sidecar (2026-07-28, Oahu map lag):
    OpenDriveUE/Content/OpenDrive/Masks/centerline_map_<AREA_NAME>_test.bin
    'ODM1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm)
The ODC1 file above is a single flat concatenation of every segment with NO
break markers - fine for AODForestManager/AODRaceManager's nearest-point
distance queries (adjacency across a segment boundary doesn't matter for a
"how close is the nearest point" check), but wrong for AODRaceHUD::
DrawStageMap, which used to walk it as one continuous polyline and draw a
straight line between the LAST point of one segment and the FIRST point of
the next - two real, unrelated streets, possibly clear across the map,
connected as if they were the same road. On Oahu's 8,848-segment network
this produced thousands of bogus "teleport" lines AND, since it's the full
5 m-station density with no simplification, roughly 780,000 line draws
every frame the map was visible - the actual cause of the reported lag.
ODM1 fixes both by (a) simplifying each segment independently (Douglas-
Peucker, see simplify_rdp()) so nearly-straight runs collapse to a couple of
points instead of one every 5 m, and (b) inserting a NaN sentinel point
between segments that AODRoadManager/DrawStageMap must skip past rather
than draw through - never generated from a flattened array, always from
each segment's own point list, so it can't reintroduce the bug.
"""

import json
import struct
from pathlib import Path

import numpy as np

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

# Map-only simplification tolerance (cm) - this line is a schematic overview,
# not a driving surface, so an aggressive tolerance is fine (matches the
# project's existing stylized/low-poly art direction elsewhere). 30 m.
MAP_SIMPLIFY_TOLERANCE_CM = 3000.0


def simplify_rdp(points, epsilon):
    """Douglas-Peucker simplification of one Nx2 polyline (iterative, not
    recursive - some real segments run thousands of stations long and a
    recursive version can blow Python's call-stack limit on a pathologically
    curvy one). Returns a subset of the input rows, always keeping the first
    and last point."""
    n = len(points)
    if n < 3:
        return points
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        p0, p1 = points[i0], points[i1]
        seg = p1 - p0
        seg_len = np.hypot(seg[0], seg[1])
        chunk = points[i0 + 1:i1]
        if seg_len < 1e-6:
            d = np.hypot(chunk[:, 0] - p0[0], chunk[:, 1] - p0[1])
        else:
            # perpendicular distance from each point to the line p0->p1
            cross = seg[0] * (chunk[:, 1] - p0[1]) - seg[1] * (chunk[:, 0] - p0[0])
            d = np.abs(cross) / seg_len
        rel_idx = int(np.argmax(d))
        if d[rel_idx] > epsilon:
            mid = i0 + 1 + rel_idx
            keep[mid] = True
            stack.append((i0, mid))
            stack.append((mid, i1))
    return points[keep]


def main():
    data = json.loads(STATIONS_PATH.read_text())
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    x_cm = (east * 100.0).astype(np.float32)
    y_cm = (-north * 100.0).astype(np.float32)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"centerline_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODC1")
        f.write(struct.pack("<I", len(x_cm)))
        for i in range(len(x_cm)):
            f.write(struct.pack("<ff", float(x_cm[i]), float(y_cm[i])))
    print(f"wrote {out_path} ({len(x_cm)} points, {8 + len(x_cm) * 8} bytes)")

    # ---- map-only sidecar: per-segment simplify, NaN break between segments
    nan_pair = (float("nan"), float("nan"))
    map_points = []
    total_raw = 0
    for seg in data["segments"]:
        seg_east = np.array(seg["east_m"], dtype=np.float64)
        seg_north = np.array(seg["north_m"], dtype=np.float64)
        total_raw += len(seg_east)
        if len(seg_east) < 2:
            continue
        pts = np.stack([seg_east * 100.0, -seg_north * 100.0], axis=1)
        simplified = simplify_rdp(pts, MAP_SIMPLIFY_TOLERANCE_CM)
        if map_points:
            map_points.append(nan_pair)
        map_points.extend((float(p[0]), float(p[1])) for p in simplified)

    map_out_path = OUT_DIR / f"centerline_map_{AREA_NAME}_test.bin"
    with open(map_out_path, "wb") as f:
        f.write(b"ODM1")
        f.write(struct.pack("<I", len(map_points)))
        for x, y in map_points:
            f.write(struct.pack("<ff", x, y))
    print(f"wrote {map_out_path} ({len(map_points)} points incl. breaks, "
          f"simplified from {total_raw} raw stations across "
          f"{len(data['segments'])} segment(s), {8 + len(map_points) * 8} bytes)")


if __name__ == "__main__":
    main()
