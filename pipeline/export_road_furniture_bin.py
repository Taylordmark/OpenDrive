"""Buckets build_road_furniture.py's props into per-tile ODF1 binary sidecars
AODFurnitureManager streams at runtime - same world-space 2 km tile grid as
export_road_guardrails_bin.py, bucketed by each prop's own position.

Usage:  python export_road_furniture_bin.py   (needs road_furniture.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_furniture.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/furniture_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODF1 format (per file):
    'O','D','F','1'          magic (4 bytes)
    16 bytes                 generation id (ASCII hex, pipeline/generation_id.py)
    uint32 LE  prop_count
    prop_count * (uint8 type,                build_road_furniture.TYPES index
                  float32 x_cm, y_cm, z_cm,  UE world position of the prop's base
                  float32 yaw_deg,           +X local faces this way
                  float32 scale_y)           Y stretch (stop-bar width in m, else 1)
"""

import json
import struct
from collections import Counter, defaultdict
from pathlib import Path

from build_road_furniture import OUT_PATH as SRC, TYPES
from bake_job import job_items   # (2026-10-21) a pass-2 job writes only its own tiles
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
RECORD = struct.Struct("<B5f")


def main():
    props = json.loads(SRC.read_text())["props"]
    buckets = defaultdict(list)
    for p in props:
        buckets[(int(p[1] // TERRAIN_TILE_SIZE_CM), int(p[2] // TERRAIN_TILE_SIZE_CM))].append(p)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    written_paths = []
    for (tx, ty), tile_props in job_items(buckets, TERRAIN_TILE_SIZE_CM):
        out_path = OUT_DIR / f"furniture_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        written_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"ODF1")
            f.write(gen_id_bytes)
            f.write(struct.pack("<I", len(tile_props)))
            for t, x, y, z, yaw, sy in tile_props:
                f.write(RECORD.pack(int(t), x, y, z, yaw, sy))

    n_stale = delete_stale_tiles(OUT_DIR, f"furniture_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale furniture tile(s) from a previous run")
    counts = Counter(int(p[0]) for p in props)
    print(f"{len(props)} prop(s) -> {len(buckets)} tile(s) -> {OUT_DIR}")
    print("  " + ", ".join(f"{name} {counts.get(t, 0)}" for t, name in enumerate(TYPES)))


if __name__ == "__main__":
    main()
