"""Buckets build_road_guardrails.py's per-side offset station runs into
per-tile ODG1 binary sidecars AODGuardrailManager streams at runtime - same
world-space tile grid and bucket-by-centroid convention as
export_road_barrier_bin.py (which this closely mirrors: points only, no
baked mesh - AODGuardrailManager extrudes the W-beam/Jersey cross-section
ribbon at runtime from these points).

Usage:  python export_road_guardrails_bin.py   (needs road_guardrails.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_guardrails.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/guardrail_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODG1 format (per file):
    'O','D','G','1'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py)
    uint32 LE  run_count
    per run:
        uint8      side        (0 = side -1, 1 = side +1 - sign not needed
                                 at runtime, geometry is built purely from
                                 the station polyline + per-station tangent)
        uint8      class_id     (road_mesh_common.ROAD_CLASS_ID - AODGuardrail
                                 Manager maps this to a W-beam/Jersey profile)
        uint32 LE  station_count
        station_count * (float32 x_cm, float32 y_cm, float32 z_cm)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_guardrails.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    data = json.loads(SRC.read_text())
    runs = data["runs"]

    run_buckets = defaultdict(list)
    for r in runs:
        tx, ty = r["tile"].split(",")
        run_buckets[(int(tx), int(ty))].append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    written_paths = []
    for (tx, ty), tile_runs in run_buckets.items():
        out_path = OUT_DIR / f"guardrail_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        written_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"ODG1")
            f.write(gen_id_bytes)
            f.write(struct.pack("<I", len(tile_runs)))
            for r in tile_runs:
                xyz = r["station_xyz_cm"]
                scount = len(xyz) // 3
                side01 = 1 if r["side"] > 0 else 0
                f.write(struct.pack("<BBI", side01, r["class_id"] & 0xFF, scount))
                f.write(struct.pack(f"<{len(xyz)}f", *xyz))

    n_stale = delete_stale_tiles(OUT_DIR, f"guardrail_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale guardrail tile(s) from a previous run")
    total_stations = sum(len(r["station_xyz_cm"]) // 3 for r in runs)
    print(f"{len(runs)} run(s) ({total_stations} stations) -> {len(run_buckets)} tile(s) -> {OUT_DIR}")


if __name__ == "__main__":
    main()
