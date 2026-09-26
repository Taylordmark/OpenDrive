"""Packs classify_biomes.py's road_biomes.json into the runtime binary
sidecar AODForestManager actually loads, mirroring
make_forest_mask_bin.py's terrain_mask.bin convention (a small
magic-tagged binary staged into Content/, not JSON - proven to survive
packaging via DirectoriesToAlwaysStageAsUFS already, so reusing that
convention here instead of introducing a new staged file type).

Usage:  python export_biome_bin.py    (needs road_biomes.json first)

Writes  OpenDriveUE/Content/OpenDrive/Masks/biome_<AREA_NAME>_test.bin
        'ODB2' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm,
        uint8 Biome, uint8 ForestWeight255, uint8 Northness255,
        uint8 Moisture255, uint16 ElevM)
        (2026-09-25, next steps.md sec 7.35 V2/V5 - ODB1 was the first 10
        bytes of that record only; the C++ readers accept both magics.
        Northness255 = round((northness + 1) * 127.5), i.e. 128 ~ flat.)
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
    north, moist, elev = data["northness"], data["moisture"], data["elev_m"]
    n = len(x_cm)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"biome_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODB2")
        f.write(struct.pack("<I", n))
        for i in range(n):
            f.write(struct.pack("<ffBBBBH", float(x_cm[i]), float(y_cm[i]),
                                 int(biome[i]), int(round(fw[i] * 255)),
                                 int(round((north[i] + 1.0) * 127.5)),
                                 int(round(moist[i] * 255)),
                                 int(min(max(round(elev[i]), 0), 65535))))
    print(f"wrote {out_path} ({n} points, {4 + 4 + n * 14} bytes)")


if __name__ == "__main__":
    main()
