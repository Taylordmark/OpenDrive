"""Packs build_place_labels.py's road_labels.json into the runtime binary
sidecar AODRoadManager loads, same magic-tagged-binary convention as every
other sidecar here (export_biome_bin.py, export_road_centerline_bin.py) -
new wrinkle: variable-length UTF-8 strings instead of fixed-size records,
since road/city names aren't a small fixed enum.

Usage:  python export_place_labels_bin.py    (needs road_labels.json first)

Writes  OpenDriveUE/Content/OpenDrive/Masks/labels_<AREA_NAME>_test.bin
        'ODL1' + 16-byte generation id (ASCII hex, pipeline/generation_id.py,
        2026-08-21 - see that module's docstring) + uint32 N (LE) + N * (
            float32 X_cm, float32 Y_cm,
            uint16 road_len, road_len bytes UTF-8,
            uint16 city_len, city_len bytes UTF-8)
"""

import json
import struct
from pathlib import Path

from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
IN_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_labels.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def pack_string(s):
    b = s.encode("utf-8")
    if len(b) > 65535:
        b = b[:65535]   # never expected for a road/city name, but keep the
                         # uint16 length field honest rather than corrupt
                         # the stream if it somehow happened
    return struct.pack("<H", len(b)) + b


def main():
    data = json.loads(IN_PATH.read_text(encoding="utf-8"))
    x_cm, y_cm = data["x_cm"], data["y_cm"]
    road_label, city_label = data["road_label"], data["city_label"]
    n = len(x_cm)

    gen_id = compute_generation_id()
    print(f"generation id: {gen_id}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"labels_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODL1")
        f.write(pack_generation_id(gen_id))
        f.write(struct.pack("<I", n))
        for i in range(n):
            f.write(struct.pack("<ff", float(x_cm[i]), float(y_cm[i])))
            f.write(pack_string(road_label[i]))
            f.write(pack_string(city_label[i]))
    print(f"wrote {out_path} ({n} points, {out_path.stat().st_size} bytes)")

    # Single fixed-name file (not tile-bucketed like every other format) -
    # this write already replaces the only file that could ever match, so
    # there's nothing else stale to clean up. Still routed through
    # delete_stale_tiles for the same "print 0 on a normal run" convention
    # every other exporter uses, per AUDIT-RELIABILITY.md SS3.2/item 6.
    n_stale = delete_stale_tiles(OUT_DIR, f"labels_{AREA_NAME}_test.bin", [out_path])
    print(f"deleted {n_stale} stale label file(s) from a previous run")


if __name__ == "__main__":
    main()
