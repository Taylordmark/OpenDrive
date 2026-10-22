"""Buckets build_road_embankments.py's "embankment"-class runs into per-tile
ODE1 binary sidecars AODEmbankmentManager streams at runtime - same world-
space tile grid and bucket-by-centroid convention as
export_road_guardrails_bin.py (which this closely mirrors: points only, no
baked mesh - AODEmbankmentManager extrudes the sloped skirt cross-section at
runtime from these points, sized per-station by the shipped drop value).

"bridge"-class runs in road_embankments.json are deliberately NOT packed
here - by §7.21, that class only contains genuine short spans (build_road_
embankments.py's own sub-classification pass already demoted every sidehill-
cut run back to "embankment"), and build_road_bridges.py/export_road_bridge_
bin.py is their own separate consumer (adds pier placement, packs ODB1 -
AODBridgeManager, not AODEmbankmentManager, streams those).

Usage:  python export_road_embankment_bin.py   (needs road_embankments.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_embankments.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/embankment_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODE1 format (per file):
    'O','D','E','1'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py)
    uint32 LE  run_count
    per run:
        uint8      side          (0 = side -1, 1 = side +1)
        uint8      reserved      (0 - unused, keeps the header word-aligned
                                   and leaves room for a future per-run
                                   variant flag without a format bump)
        uint32 LE  station_count
        station_count * (float32 x_cm, float32 y_cm, float32 z_cm,
                          float32 drop_cm)
                                  (x,y,z = pavement-edge seat point; drop_cm
                                   = road-above-terrain drop at that station,
                                   cm - AODEmbankmentManager derives the toe/
                                   apron points from this at runtime, same
                                   "pipeline ships points, C++ derives
                                   geometry" split as every other streamed
                                   layer in this project)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

from bake_job import job_items   # (2026-10-21) a pass-2 job writes only its own tiles
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_embankments.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    data = json.loads(SRC.read_text())
    runs = [r for r in data["runs"] if r["cls"] == "embankment"]
    n_bridge_skipped = sum(1 for r in data["runs"] if r["cls"] == "bridge")

    run_buckets = defaultdict(list)
    for r in runs:
        tx, ty = r["tile"].split(",")
        run_buckets[(int(tx), int(ty))].append(r)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    written_paths = []
    for (tx, ty), tile_runs in job_items(run_buckets, TERRAIN_TILE_SIZE_CM):
        out_path = OUT_DIR / f"embankment_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        written_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"ODE1")
            f.write(gen_id_bytes)
            f.write(struct.pack("<I", len(tile_runs)))
            for r in tile_runs:
                xyzd = r["station_xyzd_cm"]
                scount = len(xyzd) // 4
                side01 = 1 if r["side"] > 0 else 0
                f.write(struct.pack("<BBI", side01, 0, scount))
                f.write(struct.pack(f"<{len(xyzd)}f", *xyzd))

    n_stale = delete_stale_tiles(OUT_DIR, f"embankment_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale embankment tile(s) from a previous run")
    total_stations = sum(len(r["station_xyzd_cm"]) // 4 for r in runs)
    print(f"{len(runs)} embankment run(s) ({total_stations} stations) -> {len(run_buckets)} tile(s) -> {OUT_DIR}")
    print(f"  ({n_bridge_skipped} genuine bridge run(s) in road_embankments.json left unpacked here - "
          f"see build_road_bridges.py, their own consumer)")


if __name__ == "__main__":
    main()
