"""Packs classify_biomes.py's road_biomes.json into the runtime binary
sidecar AODForestManager actually loads, mirroring
make_forest_mask_bin.py's terrain_mask.bin convention (a small
magic-tagged binary staged into Content/, not JSON - proven to survive
packaging via DirectoriesToAlwaysStageAsUFS already, so reusing that
convention here instead of introducing a new staged file type).

Usage:  python export_biome_bin.py    (needs road_biomes.json first)

Writes  OpenDriveUE/Content/OpenDrive/Masks/biome_<AREA_NAME>_test.bin
        'ODB1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm,
        uint8 Biome, uint8 ForestWeight255)
"""

import json
import struct
from pathlib import Path

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
IN_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_biomes.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    data = json.loads(IN_PATH.read_text())
    x_cm, y_cm = data["x_cm"], data["y_cm"]
    biome, fw = data["biome"], data["forest_weight"]
    n = len(x_cm)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"biome_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODB1")
        f.write(struct.pack("<I", n))
        for i in range(n):
            f.write(struct.pack("<ffBB", float(x_cm[i]), float(y_cm[i]),
                                 int(biome[i]), int(round(fw[i] * 255))))
    print(f"wrote {out_path} ({n} points, {4 + 4 + n * 10} bytes)")


if __name__ == "__main__":
    main()
