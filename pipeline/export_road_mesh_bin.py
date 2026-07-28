"""Convert a road_mesh.json (build_road_network.py's {"segments":[...]}
output) into per-tile ODR2 binary sidecars AODRoadManager streams at
runtime (Arc 7.2 slice 2).

Usage:  python export_road_mesh_bin.py

Reads   data/road_tiles/<AREA_NAME>_test/road_mesh.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/road_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        (one file per non-empty world-space grid cell)

Extends this project's existing magic+LE-header+payload sidecar convention
(see export_centerline_bin.py / make_forest_mask_bin.py). Segments are
flattened into one flat chunk list first (each chunk's "name" is already
unique - seg000_0000, seg001_0000, ...), same as slice 1; the only new step
is bucketing that flat list by each chunk's centroid into a fixed-size
world-space grid (test_areas.ROAD_TILE_SIZE_CM) instead of writing one file
for the whole network. This is a pure bucketing pass - road_mesh_common.py's
station/chunk math is untouched, and a chunk (<=750 m span) straddling a
tile boundary is an accepted slice-2 simplification handled on the runtime
load-radius side (AODRoadManager pads its load reach by half a chunk span),
not by re-cutting geometry here.

No manifest file is written - AODRoadManager probes for a tile's file by
name and treats a missing file as "no road in that tile" (DefaultGame.ini
stages OpenDrive/Masks as a whole directory, so this needs no packaging
changes).

ODR2 format (2026-07-25: was ODR1, bumped for the new per-chunk oneway byte
below - reader/writer are always regenerated together in this pipeline, so
no back-compat with old ODR1 files is needed; the magic bump just makes a
stale/mismatched pair fail loudly instead of silently misreading a byte):
    'O','D','R','2'                                   magic (4 bytes)
    uint32 LE  chunk_count
    per chunk:
        uint32 LE  vertex_count
        uint32 LE  tri_count
        uint8      oneway (1 = one-way carriageway, no center yellow line)
        vertex_count * (float32 x, float32 y, float32 z)   cm, UE world frame
        vertex_count * (float32 u, float32 v)
        tri_count   * (uint32 i0, uint32 i1, uint32 i2)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

from test_areas import AREA_NAME, ROAD_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_mesh.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def chunk_centroid(chunk):
    verts = chunk["vertices"]          # flat [x,y,z, x,y,z, ...]
    n = len(verts) // 3
    cx = sum(verts[0::3]) / n
    cy = sum(verts[1::3]) / n
    return cx, cy


def write_tile(path, chunks):
    with open(path, "wb") as f:
        f.write(b"ODR2")
        f.write(struct.pack("<I", len(chunks)))
        for c in chunks:
            verts = c["vertices"]
            uvs = c["uvs"]
            tris = c["triangles"]
            vcount = len(verts) // 3
            tcount = len(tris) // 3
            assert len(uvs) // 2 == vcount, f"{c['name']}: uv/vertex count mismatch"
            f.write(struct.pack("<II", vcount, tcount))
            f.write(struct.pack("<B", 1 if c.get("oneway") else 0))
            f.write(struct.pack(f"<{len(verts)}f", *verts))
            f.write(struct.pack(f"<{len(uvs)}f", *uvs))
            f.write(struct.pack(f"<{len(tris)}I", *tris))


def main():
    data = json.loads(SRC.read_text())
    chunks = [c for seg in data["segments"] for c in seg["chunks"]]

    buckets = defaultdict(list)
    for c in chunks:
        cx, cy = chunk_centroid(c)
        tx = int(cx // ROAD_TILE_SIZE_CM)
        ty = int(cy // ROAD_TILE_SIZE_CM)
        buckets[(tx, ty)].append(c)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total_verts = total_tris = 0
    xs = [k[0] for k in buckets]
    ys = [k[1] for k in buckets]
    for (tx, ty), tile_chunks in buckets.items():
        out_path = OUT_DIR / f"road_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, tile_chunks)
        v = sum(len(c["vertices"]) // 3 for c in tile_chunks)
        t = sum(len(c["triangles"]) // 3 for c in tile_chunks)
        total_verts += v
        total_tris += t
        print(f"  tile ({tx},{ty}): {len(tile_chunks)} chunk(s), {v} verts, {t} tris "
              f"-> {out_path.name}")

    print(f"{len(chunks)} chunks -> {len(buckets)} tile(s) "
          f"(x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")


if __name__ == "__main__":
    main()
