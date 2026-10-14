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

Tunnels: the ribbon runs straight through on the stations' real elev_m;
build_tunnels.py adds the bore around it and opens the terrain at each
portal. Tunnel stations stay out of the ODS2 station sidecar (runtime verge
tint / tree exclusion / player start belong to open-air road only).
"""

import json
import math
import struct
from pathlib import Path

import numpy as np

from generation_id import compute_generation_id, pack_generation_id
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, ROAD_TILE_SIZE_CM
from road_mesh_common import (ROAD_HALF_WIDTH, ROAD_LIFT, STATION_SPACING,
                              build_road_chunks, half_width_for_stations,
                              road_ue_z_m, shipped_terrain_heights,
                              unpaved_stations)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"
STATIONS_PATH = OUT / "road_stations.json"
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def write_station_sidecar(path, segment_geoms, gen_id_bytes):
    """Writes the ODS2 road-station sidecar (Arc 7 runtime-carve slice):
    every road station's final UE-world-frame (cm) position plus its
    pavement half-width, at the exact same 5 m spacing and post-regrade
    elevation build_road_chunks() just used to build road_mesh.json's
    ribbon geometry - so the C++ runtime terrain carve can read station
    elevations far beyond the road-mesh spawn radius (out to the 6 km
    terrain visual radius) without re-deriving anything road_mesh_common.py
    already computed.

    `segment_geoms` is the same list of (cx, cy, cz, hw_cm) UE-frame (cm)
    station-centerline arrays main() already builds per segment, minus
    tunnel stations (see the module docstring). hw_cm is the per-station pavement half-width in cm
    (road_mesh_common.half_width_for_stations - the same values baked into
    the ribbon's uv1.x channel, x100).

    Layout (pinned contract w/ ODBinSidecarUtil.h's
    ParseRoadStationSidecarFile - do not change without updating both
    sides; that reader still accepts the older 3-float ODS1 layout with a
    default half-width):
        'O','D','S','2'                              magic (4 bytes)
        uint32 LE  generation-id string byte length L
        L bytes    UTF-8 generation id (generation_id.py)
        uint32 LE  station count N
        N * (float32 x_cm, float32 y_cm, float32 z_cm, float32 hw_cm)
                   UE world frame, same frame/units as road_mesh.json's
                   exported vertices
    """
    gen_id_utf8 = gen_id_bytes  # already ASCII bytes from pack_generation_id
    with open(path, "wb") as f:
        f.write(b"ODS2")
        f.write(struct.pack("<I", len(gen_id_utf8)))
        f.write(gen_id_utf8)
        total = sum(len(g[0]) for g in segment_geoms)
        f.write(struct.pack("<I", total))
        for cx, cy, cz, hw_cm in segment_geoms:
            n = len(cx)
            interleaved = np.empty(n * 4, dtype=np.float32)
            interleaved[0::4] = cx
            interleaved[1::4] = cy
            interleaved[2::4] = cz
            interleaved[3::4] = hw_cm
            f.write(interleaved.tobytes())
    return total


def main():
    if not STATIONS_PATH.exists():
        raise SystemExit(f"{STATIONS_PATH} not found - run build_road_stations.py first.")
    data = json.loads(STATIONS_PATH.read_text())

    MIN_RUN_STATIONS = 2   # build_road_chunks needs 2 points for its tangent

    segments = []
    segment_geoms = []   # (cx, cy, cz, hw_cm) per built segment - player-start pick + ODS2 sidecar
    total_km = 0.0
    for seg in data["segments"]:
        s_full = np.array(seg["s_m"])
        e_full = np.array(seg["east_m"])
        n_full = np.array(seg["north_m"])
        h_full = np.array(seg["elev_m"])
        # only filters the station sidecar now - the ribbon runs through
        is_tunnel = np.array(seg.get("is_tunnel", [False] * len(s_full)), dtype=bool)
        is_oneway_full = seg.get("oneway")   # absent in older files - defaults to two-way
        highway_full = seg.get("highway_class")   # absent in older files - defaults to unknown class
        lanes_full = seg.get("lanes")   # absent in older files - class-table fallback (2026-09-06)
        surface_full = seg.get("surface")   # absent in older files - all paved (2026-09-30)
        # junction_suppress (2026-09-04, Arc 7.17): absent in older
        # road_stations.json -> zeros (no junction influence, same as the
        # pre-fix behavior). See build_road_stations.py's module docstring
        # and road_mesh_common.build_road_chunks()'s uv1.y doc for the full
        # path this feeds: ODR3 marking-suppression channel + z-bias.
        junction_suppress_full = np.array(
            seg.get("junction_suppress", [0.0] * len(s_full)), dtype=np.float64)
        runs = [(0, len(s_full))]
        for run_idx, (lo, hi) in enumerate(runs):
            if hi - lo < MIN_RUN_STATIONS:
                continue
            s = s_full[lo:hi]
            e = e_full[lo:hi]
            n = n_full[lo:hi]
            h = h_full[lo:hi]
            z = road_ue_z_m(h, ROAD_LIFT, ORIGIN_HEIGHT_M)
            is_oneway = is_oneway_full[lo:hi] if is_oneway_full else None
            highway_classes = highway_full[lo:hi] if highway_full else None
            osm_lanes = lanes_full[lo:hi] if lanes_full else None
            is_unpaved = unpaved_stations(surface_full[lo:hi], highway_classes) if surface_full else None
            # Real per-road width from lanes= tags (class-table fallback for
            # untagged ways) - see road_mesh_common.half_width_for_stations().
            half_width_s = (half_width_for_stations(highway_classes, is_oneway, osm_lanes)
                            if highway_classes else None)
            junction_suppress = junction_suppress_full[lo:hi]
            name = seg["name"] if len(runs) == 1 else f"{seg['name']}_p{run_idx}"
            chunks, cx, cy, cz = build_road_chunks(s, e, n, z, name_prefix=name,
                                                   spacing=STATION_SPACING, is_oneway=is_oneway,
                                                   highway_classes=highway_classes,
                                                   osm_lanes=osm_lanes, half_width_s=half_width_s,
                                                   junction_suppress=junction_suppress,
                                                   is_unpaved=is_unpaved,
                                                   ground_cm=shipped_terrain_heights)
            segments.append({"name": name, "chunks": chunks})
            hw_m = (half_width_s if half_width_s is not None
                    else np.full(len(cx), ROAD_HALF_WIDTH))
            open_air = ~is_tunnel[lo:hi]
            segment_geoms.append((cx[open_air], cy[open_air], cz[open_air], hw_m[open_air] * 100.0))
            length_km = float(s[-1] - s[0]) / 1000.0 if len(s) > 1 else 0.0
            total_km += length_km
            print(f"  {name}: {length_km:.2f} km, {len(chunks)} chunk(s)")

    if not segment_geoms:
        raise SystemExit("No segments in road_stations.json - check build_road_stations.py's output.")

    (OUT / "road_mesh.json").write_text(json.dumps({"segments": segments}))

    MASKS_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    station_sidecar_path = MASKS_DIR / f"roadstations_{AREA_NAME}.bin"
    n_stations = write_station_sidecar(station_sidecar_path, segment_geoms, gen_id_bytes)
    print(f"station sidecar: {n_stations} stations -> {station_sidecar_path}")

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
    # that worst case; 500 m comfortably was, back when ROAD_TILE_SIZE_CM was
    # 200000 (2 km).
    #
    # 2026-08-25: ROAD_TILE_SIZE_CM shrank to 50000 (500 m, see its comment
    # in test_areas.py) to fix the Chaos/UE5.8 collision-registration bug.
    # A true ~375 m safety margin no longer fits - it would need to be under
    # half the tile width (250 m) to leave any valid interior band at all, so
    # this check can no longer GUARANTEE the picked station's chunk buckets
    # to the tile it looks like it's in. That guarantee isn't needed anymore
    # either: AODRoadManager::Tick's ring-load math always resides at least a
    # 3x3-tile neighborhood around the player (see the SpawnRadius/TileSizeCm
    # comments in ODRoadManager.h), so even a seam-adjacent spawn's true
    # chunk tile is still loaded on spawn. This margin is now just a soft
    # preference for picking a station well inside a tile rather than a hard
    # safety requirement - 100 m keeps a real (100 m, 400 m) valid band.
    TILE_MARGIN_CM = 10000.0   # 100 m
    best = None       # (dist2, cx, cy, cz, station_idx) - respects TILE_MARGIN_CM
    best_any = None   # closest overall, ignoring margin - fallback only
    for cx, cy, cz, _hw in segment_geoms:
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
        # bbox centre, for fetch_climate.py's point lookup - NOT a frame
        # origin any more (2026-10-05, the frame is road_stations' frame_lcc)
        "center_latlon": [(BBOX_SNWE[0] + BBOX_SNWE[2]) / 2, (BBOX_SNWE[1] + BBOX_SNWE[3]) / 2],
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
