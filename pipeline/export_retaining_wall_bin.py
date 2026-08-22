"""Convert build_retaining_walls.py's retaining_walls.json into per-tile
ODS1 binary sidecars AODWallManager streams at runtime - same bucket-by-tile
convention as export_building_mesh_bin.py, just bucketing already-tagged
wall chunks (each already carries its own "tile" key from
build_retaining_walls.py) instead of computing centroids itself.

Usage:  python export_retaining_wall_bin.py   (needs retaining_walls.json first)

Reads   data/road_tiles/<AREA_NAME>_test/retaining_walls.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/walls_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        (one file per non-empty tile)

ODS1 format (per file) - byte-identical structure to ODU1 (buildings):
    'O','D','S','1'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py,
               2026-08-21 - see that module's docstring)
    uint32 LE  wall_count
    per wall:
        uint32 LE  vertex_count
        uint32 LE  tri_count
        vertex_count * (float32 x, float32 y, float32 z)   cm, UE world frame
        vertex_count * (float32 u, float32 v)
        tri_count   * (uint32 i0, uint32 i1, uint32 i2)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "retaining_walls.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def write_tile(path, walls, gen_id_bytes):
    with open(path, "wb") as f:
        f.write(b"ODS1")
        f.write(gen_id_bytes)
        f.write(struct.pack("<I", len(walls)))
        for w in walls:
            verts, uvs, tris = w["vertices"], w["uvs"], w["triangles"]
            vcount, tcount = len(verts) // 3, len(tris) // 3
            assert len(uvs) // 2 == vcount, f"{w['name']}: uv/vertex count mismatch"
            f.write(struct.pack("<II", vcount, tcount))
            f.write(struct.pack(f"<{len(verts)}f", *verts))
            f.write(struct.pack(f"<{len(uvs)}f", *uvs))
            f.write(struct.pack(f"<{len(tris)}I", *tris))


def main():
    data = json.loads(SRC.read_text())
    walls = data["walls"]

    buckets = defaultdict(list)
    for w in walls:
        tx, ty = w["tile"].split(",")
        buckets[(int(tx), int(ty))].append(w)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    total_verts = total_tris = 0
    written_paths = []
    for (tx, ty), tile_walls in buckets.items():
        out_path = OUT_DIR / f"walls_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, tile_walls, gen_id_bytes)
        written_paths.append(out_path)
        v = sum(len(w["vertices"]) // 3 for w in tile_walls)
        t = sum(len(w["triangles"]) // 3 for w in tile_walls)
        total_verts += v
        total_tris += t
        print(f"  tile ({tx},{ty}): {len(tile_walls)} wall chunk(s), {v} verts, {t} tris "
              f"-> {out_path.name}")

    n_stale = delete_stale_tiles(OUT_DIR, f"walls_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale wall tile(s) from a previous run")

    if not buckets:
        print("0 wall chunks - nothing to export")
        return
    xs = [k[0] for k in buckets]
    ys = [k[1] for k in buckets]
    print(f"{len(walls)} wall chunk(s) -> {len(buckets)} tile(s) "
          f"(x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")


if __name__ == "__main__":
    main()
