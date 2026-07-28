"""Mini-map alignment fix (2026-07-18): the map was drawing a Catmull-Rom
guess between the 62 checkpoint gates (~300 m apart), which doesn't match
the real road through corners tighter than that guess assumes. The true
centerline is already sitting in road_mesh.json at full 5 m station density
(same pavement-edge-midpoint math apply_arc23_headless.py uses to place the
gates) - this just exports EVERY station instead of a 1-in-N subset, as a
compact runtime sidecar (same pattern as terrain_mask.bin).

Output: OpenDriveUE/Content/OpenDrive/Masks/centerline.bin
    'ODC1' magic, uint32 LE count, count * (float32 x, float32 y) cm,
    same UE world frame as the checkpoint gates - no transform needed.

Usage:  python export_centerline_bin.py
"""

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ROAD_JSON = ROOT / "data" / "stages" / "tail_of_the_dragon" / "road_mesh.json"
DST = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks" / "centerline.bin"

road = json.loads(ROAD_JSON.read_text())
stations = {}
for chunk in road["chunks"]:
    c0 = int(chunk["name"].split("_")[1])
    v = chunk["vertices"]
    for r in range(len(v) // 12):          # 4 verts * xyz per station row
        b = (r * 4 + 1) * 3                # verts 1,2 = pavement edges
        stations[c0 + r] = ((v[b] + v[b + 3]) / 2.0,
                             (v[b + 1] + v[b + 4]) / 2.0)

pts = [stations[i] for i in sorted(stations)]
DST.parent.mkdir(parents=True, exist_ok=True)
with open(DST, "wb") as f:
    f.write(b"ODC1")
    f.write(struct.pack("<I", len(pts)))
    for x, y in pts:
        f.write(struct.pack("<ff", x, y))

print(f"wrote {DST} ({len(pts)} stations, {len(pts) * 8} bytes)")
sys.exit(0)
