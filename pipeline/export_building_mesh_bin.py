"""Convert build_building_mesh.py's building_mesh.json into per-tile ODU4
binary sidecars AODBuildingManager streams at runtime (Arc 7.0 step 3) -
same bucket-by-tile convention as export_road_mesh_bin.py, just bucketing
already-tagged buildings (each already carries its own "tile" key from
build_building_mesh.py) instead of computing centroids itself.

Usage:  python export_building_mesh_bin.py   (needs building_mesh.json first)

Reads   data/road_tiles/<AREA_NAME>_test/building_mesh.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/buildings_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        (one file per non-empty tile)

ODU4 format (per file) - ODU2 (2026-09-29: wall_set, flat_roof) plus the
baked roof's look and the end-wall split (ODU3, realism B3, 2026-10-15; roofs
are pitched in build_building_mesh.py now, not at runtime) and the facade
archetype (ODU4, realism B2):
    'O','D','U','4'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py,
               2026-08-21 - see that module's docstring)
    uint32 LE  building_count
    per building:
        uint32 LE  vertex_count
        uint32 LE  tri_count
        uint8      wall_set    index into apply_building_material*.py WALL_LAYERS
        uint8      flat_roof   1 = flat roof cap, 0 = pitched
        uint8      roof_set    index into apply_building_material_real.py ROOF_LAYERS
        uint8 x3   roof_rgb    roof albedo, linear 0..255
        uint8      facade      build_building_mesh.py FACADE_* archetype
        uint32 LE  n_solid     vertices from here on are roof end walls (no windows)
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
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "building_mesh.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def write_tile(path, buildings, gen_id_bytes):
    with open(path, "wb") as f:
        f.write(b"ODU4")
        f.write(gen_id_bytes)
        f.write(struct.pack("<I", len(buildings)))
        for b in buildings:
            verts, uvs, tris = b["vertices"], b["uvs"], b["triangles"]
            vcount, tcount = len(verts) // 3, len(tris) // 3
            assert len(uvs) // 2 == vcount, f"{b['name']}: uv/vertex count mismatch"
            f.write(struct.pack("<IIBBBBBBBI", vcount, tcount, b["wall_set"], b["flat_roof"], b["roof_set"],
                                *b["roof_rgb"], b["facade"], b["n_solid"]))
            f.write(struct.pack(f"<{len(verts)}f", *verts))
            f.write(struct.pack(f"<{len(uvs)}f", *uvs))
            f.write(struct.pack(f"<{len(tris)}I", *tris))


def main():
    data = json.loads(SRC.read_text())
    buildings = data["buildings"]

    buckets = defaultdict(list)
    for b in buildings:
        tx, ty = b["tile"].split(",")
        buckets[(int(tx), int(ty))].append(b)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    total_verts = total_tris = 0
    written_paths = []
    for (tx, ty), tile_buildings in buckets.items():
        out_path = OUT_DIR / f"buildings_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, tile_buildings, gen_id_bytes)
        written_paths.append(out_path)
        v = sum(len(b["vertices"]) // 3 for b in tile_buildings)
        t = sum(len(b["triangles"]) // 3 for b in tile_buildings)
        total_verts += v
        total_tris += t
        print(f"  tile ({tx},{ty}): {len(tile_buildings)} building(s), {v} verts, {t} tris "
              f"-> {out_path.name}")

    n_stale = delete_stale_tiles(OUT_DIR, f"buildings_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale building tile(s) from a previous run")
    xs = [k[0] for k in buckets]
    ys = [k[1] for k in buckets]
    print(f"{len(buildings)} buildings -> {len(buckets)} tile(s) "
          f"(x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")


if __name__ == "__main__":
    main()
