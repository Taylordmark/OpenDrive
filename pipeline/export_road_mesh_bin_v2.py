"""Convert road_mesh.json into per-tile ODR3 binary sidecars - the EXTENDED
road-mesh format for the Phase 2a road-visual overhaul (worn asphalt + lane
markings, 2026-09-03).

Usage:  python export_road_mesh_bin_v2.py

Reads   data/road_tiles/<AREA_NAME>_test/road_mesh.json
Writes  OpenDriveUE/Content/OpenDrive/MasksV2/roadv3_<AREA_NAME>_test_tile_<tx>_<ty>.bin

IMPORTANT - this is a PARALLEL output, not a replacement for
export_road_mesh_bin.py's ODR2 sidecars in OpenDriveUE/Content/OpenDrive/Masks/:
AODRoadManager::ParseRoadTileFile (ODRoadManager.cpp) only understands the
ODR2 magic/layout today, so an ODR3 file dropped into Masks/ would just fail
its magic check and silently read as "no road in that tile" (see
ParseRoadTileFile's early-out on a magic mismatch) - not a crash, but every
road in the game would vanish. Writing to a separate MasksV2/ directory with
a distinct "roadv3_" filename prefix means the current C++ never even probes
these files, so this script is fully inert until a follow-up agent adds an
ODR3 reader (and a RoadTilePrefix/output-dir switch) to ODRoadManager.cpp.
export_road_mesh_bin.py keeps writing the real, currently-loaded ODR2
sidecars unchanged (same byte LAYOUT as before - only the numeric UV values
baked into them changed, via road_mesh_common.build_road_chunks()'s new u/v
convention; see that function's docstring).

ODR3 format (2026-09-03, Phase 2a road-visual overhaul - extends ODR2 with a
second per-vertex UV channel + two new per-chunk metadata bytes so a future
worn-asphalt/lane-marking material has everything it needs without
re-deriving anything from OSM tags at runtime):
    'O','D','R','3'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py)
    uint32 LE  chunk_count
    per chunk:
        uint32 LE  vertex_count
        uint32 LE  tri_count
        uint8      oneway (1 = one-way carriageway, no center yellow line)
        uint8      road_class_id (0=motorway,1=trunk,2=primary,3=secondary,
                   4=tertiary/unclassified,5=residential/living_street,
                   6=service/track/unknown - see road_mesh_common.
                   ROAD_CLASS_ID/ROAD_CLASS_ID_NAMES)
        uint8      lane_count (1..8, heuristic - see road_mesh_common.
                   ROAD_CLASS_LANES_TWOWAY / road_class_and_lanes())
        vertex_count * (float32 x, float32 y, float32 z)   cm, UE world frame
        vertex_count * (float32 u0, float32 v0)   u0: 0=left pavement edge,
                   1=right pavement edge (skirt vertices extrapolate outside
                   [0,1]); v0: distance along centerline, METERS, continuous
                   - see road_mesh_common.build_road_chunks()'s docstring
        vertex_count * (float32 u1, float32 v1)   u1: road HALF-width in
                   meters at this vertex (full pavement width = 2*u1,
                   currently constant per chunk); v1: reserved, 0.0
        tri_count   * (uint32 i0, uint32 i1, uint32 i2)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME, ROAD_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_mesh.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "MasksV2"


def chunk_centroid(chunk):
    verts = chunk["vertices"]          # flat [x,y,z, x,y,z, ...]
    n = len(verts) // 3
    cx = sum(verts[0::3]) / n
    cy = sum(verts[1::3]) / n
    return cx, cy


def write_tile(path, chunks, gen_id_bytes):
    with open(path, "wb") as f:
        f.write(b"ODR3")
        f.write(gen_id_bytes)
        f.write(struct.pack("<I", len(chunks)))
        for c in chunks:
            verts = c["vertices"]
            uvs = c["uvs"]
            uv1 = c["uv1"]
            tris = c["triangles"]
            vcount = len(verts) // 3
            tcount = len(tris) // 3
            assert len(uvs) // 2 == vcount, f"{c['name']}: uv/vertex count mismatch"
            assert len(uv1) // 2 == vcount, f"{c['name']}: uv1/vertex count mismatch"
            f.write(struct.pack("<II", vcount, tcount))
            f.write(struct.pack("<B", 1 if c.get("oneway") else 0))
            f.write(struct.pack("<B", int(c["road_class_id"])))
            f.write(struct.pack("<B", int(c["lane_count"])))
            f.write(struct.pack(f"<{len(verts)}f", *verts))
            f.write(struct.pack(f"<{len(uvs)}f", *uvs))
            f.write(struct.pack(f"<{len(uv1)}f", *uv1))
            f.write(struct.pack(f"<{len(tris)}I", *tris))


def main():
    data = json.loads(SRC.read_text())
    chunks = [c for seg in data["segments"] for c in seg["chunks"]]
    missing_meta = [c["name"] for c in chunks if "uv1" not in c or "road_class_id" not in c]
    if missing_meta:
        raise SystemExit(
            f"{len(missing_meta)} chunk(s) missing uv1/road_class_id/lane_count "
            f"(e.g. {missing_meta[0]}) - re-run build_road_network.py with the "
            f"current road_mesh_common.build_road_chunks() before exporting ODR3.")

    buckets = defaultdict(list)
    for c in chunks:
        cx, cy = chunk_centroid(c)
        tx = int(cx // ROAD_TILE_SIZE_CM)
        ty = int(cy // ROAD_TILE_SIZE_CM)
        buckets[(tx, ty)].append(c)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    total_verts = total_tris = 0
    xs = [k[0] for k in buckets]
    ys = [k[1] for k in buckets]
    written_paths = []
    for (tx, ty), tile_chunks in buckets.items():
        out_path = OUT_DIR / f"roadv3_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, tile_chunks, gen_id_bytes)
        written_paths.append(out_path)
        v = sum(len(c["vertices"]) // 3 for c in tile_chunks)
        t = sum(len(c["triangles"]) // 3 for c in tile_chunks)
        total_verts += v
        total_tris += t
        print(f"  tile ({tx},{ty}): {len(tile_chunks)} chunk(s), {v} verts, {t} tris "
              f"-> {out_path.name}")

    n_stale = delete_stale_tiles(OUT_DIR, f"roadv3_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale ODR3 road tile(s) from a previous run")
    print(f"{len(chunks)} chunks -> {len(buckets)} tile(s) "
          f"(x[{min(xs)}..{max(xs)}] y[{min(ys)}..{max(ys)}]), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")
    print("NOTE: this is a PARALLEL ODR3 output - AODRoadManager (ODRoadManager.cpp) "
          "does not read MasksV2/ yet. See this file's module docstring.")


if __name__ == "__main__":
    main()
