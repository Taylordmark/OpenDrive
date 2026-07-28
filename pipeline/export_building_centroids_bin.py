"""Exports building centroid points for AODForestManager's tree-exclusion
check (Arc 7.0 step 3, 2026-07-24) - found necessary after the first live
test: the biome classifier has no concept of "this is a town" and was
scattering max-density forest straight through Mount Vernon, completely
burying every building behind a wall of trees (classify_biomes.py's
imagery heuristic reads a built-up area as green/textured, same as real
canopy, since it was never taught to tell the difference). Same ODC1
point-list sidecar format as export_road_centerline_bin.py - it's just
X/Y points either way, so no new format needed.

Usage:  python export_building_centroids_bin.py  (needs building_mesh.json)

Reads   data/road_tiles/<AREA_NAME>_test/building_mesh.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/building_centroids_<AREA_NAME>_test.bin
        'ODC1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm)
"""

import json
import struct
from pathlib import Path

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "building_mesh.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    data = json.loads(SRC.read_text())
    buildings = data["buildings"]

    centroids = []
    for b in buildings:
        v = b["vertices"]
        n = len(v) // 3
        cx = sum(v[0::3]) / n
        cy = sum(v[1::3]) / n
        centroids.append((cx, cy))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"building_centroids_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODC1")
        f.write(struct.pack("<I", len(centroids)))
        for x, y in centroids:
            f.write(struct.pack("<ff", float(x), float(y)))
    print(f"wrote {out_path} ({len(centroids)} points, {8 + len(centroids) * 8} bytes)")


if __name__ == "__main__":
    main()
