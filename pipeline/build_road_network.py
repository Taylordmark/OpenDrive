"""Build the UE-ready road-mesh NETWORK (multiple connected segments through
real junctions) for the Arc 7.2 test area, from build_road_stations.py's
already-sampled, already-grade-smoothed geographic profile.

Usage:  python build_road_network.py   (after build_road_stations.py)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Writes  data/road_tiles/<AREA_NAME>_test/
    road_mesh.json      {"segments": [{"name","chunks":[...]}, ...]}
    road_test_meta.json player_start (closest to bbox center), stats.

Restructure (2026-07-20): this script used to fetch the DEM and grade-smooth
elevation itself - now build_road_stations.py does that once, and both this
script and build_terrain_tiles.py (which carves the terrain to match) read
its output, so there's exactly one place the road's elevation profile is
computed. This script's only remaining job is UE-frame mesh assembly: lift
the shared elev_m by ROAD_LIFT, shift into the old georeference actor's
CARTOGRAPHIC_ORIGIN frame (road_mesh_common.road_ue_z_m - see that function's
docstring for why the shift exists), then build banked/skirted geometry via
road_mesh_common.build_road_chunks (unchanged math, shared with the Dragon).
"""

import json
import math
from pathlib import Path

import numpy as np

from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, ROAD_TILE_SIZE_CM
from road_mesh_common import STATION_SPACING, ROAD_LIFT, build_road_chunks, road_ue_z_m

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"
STATIONS_PATH = OUT / "road_stations.json"


def main():
    if not STATIONS_PATH.exists():
        raise SystemExit(f"{STATIONS_PATH} not found - run build_road_stations.py first.")
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]

    segments = []
    segment_geoms = []   # (cx, cy, cz) per built segment, for center-based player-start pick
    total_km = 0.0
    for seg in data["segments"]:
        s = np.array(seg["s_m"])
        e = np.array(seg["east_m"])
        n = np.array(seg["north_m"])
        h = np.array(seg["elev_m"])
        z = road_ue_z_m(h, ROAD_LIFT, ORIGIN_HEIGHT_M)
        is_oneway = seg.get("oneway")   # absent in older road_stations.json - defaults to two-way
        chunks, cx, cy, cz = build_road_chunks(s, e, n, z, name_prefix=seg["name"],
                                               spacing=STATION_SPACING, is_oneway=is_oneway)
        segments.append({"name": seg["name"], "chunks": chunks})
        segment_geoms.append((cx, cy, cz))
        length_km = float(s[-1] - s[0]) / 1000.0 if len(s) > 1 else 0.0
        total_km += length_km
        print(f"  {seg['name']}: {length_km:.2f} km, {len(chunks)} chunk(s)")

    if not segment_geoms:
        raise SystemExit("No segments in road_stations.json - check build_road_stations.py's output.")

    (OUT / "road_mesh.json").write_text(json.dumps({"segments": segments}))

    # Player start near the CENTER of the built area, not just "the longest
    # segment's start" (that put a test spawn ~500 m past the bbox's edge
    # once, 2026-07-19 - edge tiles can have degraded/missing streamed-terrain
    # detail, causing bad ground-snap reads). Search every built segment's
    # stations for whichever sits closest to the local origin (0,0), i.e.
    # the bbox center, leaving margin at both ends for the yaw lookahead.
    #
    # TILE_MARGIN_CM (2026-07-26): the local origin (0,0) this search centers
    # on is ALSO exactly a corner of the road-tile grid export_road_mesh_bin.py
    # buckets chunks into (tiles are ROAD_TILE_SIZE_CM squares floored from 0),
    # so the naive closest-to-origin pick is disproportionately likely to land
    # right on a tile seam - confirmed live: the previous pick sat only ~2 m
    # from a tile boundary, so the chunk covering it centroid-bucketed into the
    # NEIGHBORING tile instead of the one the spawn point nominally sits in,
    # leaving that tile with no road mesh at all. AODVehiclePawn's ground-snap
    # (which waits specifically for a PM_Asphalt hit) then hovered for its
    # full 20 s timeout - hit=1 onRoad=0 the whole time - before settling onto
    # bare terrain instead of pavement (reported as "spawn straight into the
    # air"). Requiring real margin from every tile boundary keeps the pick
    # solidly inside one tile's own coverage.
    LOOKAHEAD = 6
    # chunks are fixed 150-station (750 m) windows along the route (see
    # road_mesh_common.CHUNK_STATIONS), NOT centered on any particular
    # station - a station at one end of its own chunk's window can be up to
    # ~375 m from that chunk's centroid (the value export_road_mesh_bin.py
    # actually buckets by tile). 200 m margin (tried first) still wasn't past
    # that worst case; 500 m comfortably is.
    TILE_MARGIN_CM = 50000.0   # 500 m
    best = None       # (dist2, cx, cy, cz, station_idx) - respects TILE_MARGIN_CM
    best_any = None   # closest overall, ignoring margin - fallback only
    for cx, cy, cz in segment_geoms:
        for i in range(1, len(cx) - LOOKAHEAD):
            dist2 = cx[i] ** 2 + cy[i] ** 2
            if best_any is None or dist2 < best_any[0]:
                best_any = (dist2, cx, cy, cz, i)
            mx = cx[i] % ROAD_TILE_SIZE_CM
            my = cy[i] % ROAD_TILE_SIZE_CM
            if not (TILE_MARGIN_CM < mx < ROAD_TILE_SIZE_CM - TILE_MARGIN_CM
                    and TILE_MARGIN_CM < my < ROAD_TILE_SIZE_CM - TILE_MARGIN_CM):
                continue   # too close to a tile seam - keep searching
            if best is None or dist2 < best[0]:
                best = (dist2, cx, cy, cz, i)
    if best is None:
        print("WARNING: no station found with tile-boundary margin - "
              "falling back to closest-to-center regardless of tile seams")
        best = best_any
    _, lcx, lcy, lcz, i0 = best
    yaw = math.degrees(math.atan2(lcy[i0 + LOOKAHEAD] - lcy[i0], lcx[i0 + LOOKAHEAD] - lcx[i0]))
    road_test_meta = {
        "area": AREA_NAME,
        "origin_latlon": [lat0, lon0],
        "segments": len(segments),
        "total_length_km": round(total_km, 2),
        "player_start": {
            "location_cm": [round(float(lcx[i0]), 1), round(float(lcy[i0]), 1),
                            round(float(lcz[i0]) + 120, 1)],
            "yaw_deg": round(yaw, 1),
        },
    }
    (OUT / "road_test_meta.json").write_text(json.dumps(road_test_meta, indent=2))
    print(json.dumps(road_test_meta, indent=2))
    print(f"{len(segments)} segments, {total_km:.2f} km total -> {OUT}")


if __name__ == "__main__":
    main()
