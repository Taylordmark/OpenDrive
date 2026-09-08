"""Buckets build_road_bridges.py's genuine-span runs into per-tile ODB1
binary sidecars AODBridgeManager streams at runtime - same world-space tile
grid and bucket-by-centroid convention as export_road_embankment_bin.py
(which this closely mirrors: points only, no baked mesh - AODBridgeManager
extrudes the deck fascia/underside slab and pier columns at runtime from
these points).

Usage:  python export_road_bridge_bin.py   (needs road_bridges.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_bridges.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/bridge_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODB1 format (per file):
    'O','D','B','1'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py)
    uint32 LE  run_count
    per run:
        uint8      side          (0 = side -1, 1 = side +1)
        uint8      reserved      (0 - unused, same word-alignment/future-flag
                                   reservation as ODE1)
        uint32 LE  station_count
        station_count * (float32 x_cm, float32 y_cm, float32 z_cm,
                          float32 drop_cm)
                                  (x,y,z = pavement-edge seat point, deck top;
                                   drop_cm = road-above-terrain drop at that
                                   station, cm - AODBridgeManager derives the
                                   deck fascia/underside slab from this)
        uint32 LE  pier_count
        pier_count * (float32 x_cm, float32 y_cm, float32 z_top_cm,
                       float32 height_cm)
                                  (x,y,z_top = pier TOP, deck underside point;
                                   height_cm = pier's own vertical extent down
                                   to terrain)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_bridges.json"
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
        out_path = OUT_DIR / f"bridge_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        written_paths.append(out_path)
        with open(out_path, "wb") as f:
            f.write(b"ODB1")
            f.write(gen_id_bytes)
            f.write(struct.pack("<I", len(tile_runs)))
            for r in tile_runs:
                xyzd = r["station_xyzd_cm"]
                scount = len(xyzd) // 4
                side01 = 1 if r["side"] > 0 else 0
                f.write(struct.pack("<BBI", side01, 0, scount))
                f.write(struct.pack(f"<{len(xyzd)}f", *xyzd))
                piers = r["piers_xyzh_cm"]
                pcount = len(piers) // 4
                f.write(struct.pack("<I", pcount))
                if piers:
                    f.write(struct.pack(f"<{len(piers)}f", *piers))

    n_stale = delete_stale_tiles(OUT_DIR, f"bridge_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale bridge tile(s) from a previous run")
    total_stations = sum(len(r["station_xyzd_cm"]) // 4 for r in runs)
    total_piers = sum(len(r["piers_xyzh_cm"]) // 4 for r in runs)
    print(f"{len(runs)} bridge run(s) ({total_stations} stations, {total_piers} piers) "
          f"-> {len(run_buckets)} tile(s) -> {OUT_DIR}")


if __name__ == "__main__":
    main()
