"""Buckets build_road_barrier.py's points + wall station runs into per-tile
ODW3 binary sidecars AODBarrierManager streams at runtime - same
world-space tile grid and bucket-by-centroid convention as
export_road_mesh_bin.py/export_building_mesh_bin.py.

Format bumped to ODW3 (from the fully-triangulated-mesh ODW2, 2026-07-28,
AUDIT.md finding 5.1/recommendation 3): only the offset STATION POINTS are
shipped now, not a pre-triangulated mesh - AODBarrierManager extrudes each
run into the actual vertical-ribbon collision mesh at runtime (2 verts + 2
tris per station step, the same math this script used to bake). Cuts baked
barrier size by ~5x for identical resulting geometry (no per-vertex UV or
triangle-index arrays on disk, no duplicated top/bottom vertex data).

Usage:  python export_road_barrier_bin.py   (needs road_barrier.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_barrier.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/barrier_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODW3 format (per file):
    'O','D','W','3'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py,
               2026-08-21 - see that module's docstring)
    uint32 LE  point_count
    point_count * (float32 x_cm, float32 y_cm, float32 z_cm, uint32 seed)
    uint32 LE  run_count
    per run:
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
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_barrier.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    data = json.loads(SRC.read_text())
    points, wall_runs = data["points"], data["wall_runs"]

    point_buckets = defaultdict(list)
    for p in points:
        tx = int(p["x_cm"] // TERRAIN_TILE_SIZE_CM)
        ty = int(p["y_cm"] // TERRAIN_TILE_SIZE_CM)
        point_buckets[(tx, ty)].append(p)

    run_buckets = defaultdict(list)
    for r in wall_runs:
        tx, ty = r["tile"].split(",")
        run_buckets[(int(tx), int(ty))].append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    all_tiles = set(point_buckets) | set(run_buckets)
    written_paths = []
    for tx, ty in all_tiles:
        tile_points = point_buckets.get((tx, ty), [])
        tile_runs = run_buckets.get((tx, ty), [])
        out_path = OUT_DIR / f"barrier_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        written_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"ODW3")
            f.write(gen_id_bytes)
            f.write(struct.pack("<I", len(tile_points)))
            for p in tile_points:
                f.write(struct.pack("<fffI", p["x_cm"], p["y_cm"], p["z_cm"],
                                     p["seed"] & 0xFFFFFFFF))
            f.write(struct.pack("<I", len(tile_runs)))
            for r in tile_runs:
                xyz = r["station_xyz_cm"]
                scount = len(xyz) // 3
                f.write(struct.pack("<I", scount))
                f.write(struct.pack(f"<{len(xyz)}f", *xyz))

    n_stale = delete_stale_tiles(OUT_DIR, f"barrier_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale barrier tile(s) from a previous run")
    total_stations = sum(len(r["station_xyz_cm"]) // 3 for r in wall_runs)
    print(f"{len(points)} points, {len(wall_runs)} wall run(s) ({total_stations} stations) "
          f"-> {len(all_tiles)} tile(s) -> {OUT_DIR}")


if __name__ == "__main__":
    main()
