"""Visible guardrails: detect road-vs-terrain DROPS (the road riding high
above the surrounding ground - dam causeways, mountain switchback outer
edges) and emit per-side offset station RUNS for AODGuardrailManager to
extrude into real collision geometry at runtime. Mirrors
build_road_barrier.py's split exactly: this script only computes and writes
POINTS (station polylines + per-run road class), never a baked mesh - see
that script's docstring for why (AUDIT.md 5.1/rec. 3, ~5x disk savings, and
the extrusion math is mechanically derivable from the polyline anyway).

Detection (Taylor-approved design): for each road side independently, at a
few meters beyond the pavement edge, compare the ROAD's own graded elevation
against the RAW terrain DEM sampled at that same lateral offset. Where the
road sits GUARDRAIL_DROP_THRESHOLD_M or more above raw terrain, sustained
for at least GUARDRAIL_MIN_RUN_M, that stretch gets a rail. Short sub-
threshold gaps (< GUARDRAIL_GAP_BRIDGE_M) are bridged so a momentary terrain
bump doesn't chop one real drop into a stutter of tiny rail fragments.

Two profiles by road class (ODR3's road_class_id, already baked per road
chunk by road_mesh_common.road_class_and_lanes() - motorway/motorway_link
(id 0, covers US Interstates, which OSM tags highway=motorway) get the
concrete Jersey barrier profile; every other class gets the metal W-beam
profile. AODGuardrailManager picks the actual cross-section at runtime from
each run's class_id - this script only records which class a run's
majority-vote highway= tag implies.

Rail lateral seat position: pavement edge + 30 cm, where "pavement edge" is
the same per-station half-width the road mesh itself is built with
(road_mesh_common.half_width_for_stations - OSM lanes= tag or class-table
guess, 4.5 m for an untagged 2-lane road),
matching build_road_barrier.py's BARRIER_OFFSET_M convention of offsetting
from the same road-frame tangent/right vectors. The terrain-drop PROBE point
is sampled a few meters further out (GUARDRAIL_PROBE_EXTRA_M) - separate
from the rail's own seat offset because the whole point is checking what's
actually beyond the shoulder, not right at it.

Deliberately NOT excluded, unlike build_road_barrier.py: bridge/culvert
spans (is_bridge). This project's OSM is_bridge tag covers everything from
a real elevated causeway (Dillon Dam Road, the primary test case for this
whole feature - the road decking sits well above the reservoir) down to a
10-15 m creek culvert (build_road_stations.py's MIN_BRIDGE_CLEARANCE_M
floor, now despiked - see road_mesh_common.py's post-repair despike
comment). There's no separate bridge-railing system anywhere in this
project to defer to, and "the road is lifted well above the ground it
crosses" is exactly this feature's trigger condition - excluding is_bridge
would silently gut guardrails on their own canonical use case. The run-
length (GUARDRAIL_MIN_RUN_M) and gap-bridging logic already filter out
any short, spurious culvert-scale blips on their own.

Exclusions (reused, not reinvented):
  - NOT populated (settlement) tiles, despite build_road_barrier.py doing
    exactly that - tried it first (mirroring build_road_barrier.py's
    classify_settlements.py-based blanket in_town mask), then live-tested
    and found it silently killed the whole Dillon Dam Road causeway: the
    dam sits inside the same coarse (2 km) settlement tile as
    Dillon/Frisco's actual streets, so the blanket per-TILE mask excluded
    a real, sustained, guardrail-worthy drop that has nothing to do with
    town curb access. The brief's own guidance flagged this risk ("skip
    settlement tiles ONLY if rails visibly conflict with intersections -
    better: break runs where another road connects") - taken literally
    here: junction-breaking (below) is the ONLY town-related exclusion.
    An ordinary flat town street never triggers drop_mask in the first
    place (no drop to detect), so nothing further is needed for the
    common case; a town built on a real slope with a road that DOES drop
    2.5m+ from its shoulder is exactly a case that should still get a rail.
  - open water - reuses build_terrain_mesh_tiles.load_lake_beds()'s point-
    in-polygon test, but DELIBERATELY NARROWER than build_road_barrier.py's
    version: only an offset point actually INSIDE a lake polygon excludes a
    station, not build_road_barrier's extra WATER_SHORE_BLEND_M+3m shore-
    proximity margin. That broader margin is right for opaque retaining
    walls (aesthetic - don't wall off a lake view close to shore) but wrong
    here: Dillon Dam Road runs for ~500 m with its outer edge within a few
    meters of Dillon Reservoir's own shoreline - exactly the kind of real,
    sustained road-above-terrain drop this whole feature exists to rail off
    (found by cross-checking this file's own drop_mask against the margin
    test during verification: the margin version silently ate the primary
    test case end to end). A thin safety rail at the literal water's edge
    is correct, not a bug - only a rail whose OWN seat point would sit
    inside the lake itself (a genuine offset/geometry error, not "close to
    shore") should be suppressed.
  - road-road junctions - reuses road_mesh_common.reconcile_junction_
    elevations()'s own cross-chain proximity detection (the `touched` mask
    it returns) rather than reinventing junction detection: a station that
    pass flagged as needing an elevation snap is, by construction, close to
    where another road connects, and a rail run through that point would
    either clip the connecting road or wall off the very gap a driver needs
    to turn into. Dilated by a small margin so a run breaks BEFORE the
    junction, not exactly at the single flagged station.

Usage:  python build_road_guardrails.py   (needs road_stations.json,
                                             settlement_tiles.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
        data/road_tiles/<AREA_NAME>_test/settlement_tiles.json (if present)
Outputs data/road_tiles/<AREA_NAME>_test/road_guardrails.json
        {"runs": [{"tile","side","class_id","station_xyz_cm"}, ...]}
"""

import json
from pathlib import Path

import numpy as np
import requests

from road_mesh_common import (ROAD_HALF_WIDTH, bank_slopes, half_width_for_stations,
                               junction_agreement_pass, road_class_and_lanes,
                               road_ue_z_m, reconcile_junction_elevations)
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, to_lonlat
from build_terrain_mesh_tiles import (NEAR_DEM_ZOOM, sample_dem,
                                       lakes_near, load_lake_beds, _point_in_polygon,
                                       WATER_SHORE_BLEND_M)

# Only used as a bbox pre-filter margin now (see the "open water" exclusion
# note in the module docstring) - the actual inclusion test is point-in-
# polygon only, no shore-proximity dilation like build_road_barrier.py's.
LAKE_SHORE_EXCLUSION_MARGIN_M = WATER_SHORE_BLEND_M + 3.0

GUARDRAIL_DROP_THRESHOLD_M = 2.5     # road-above-terrain drop that triggers a rail
GUARDRAIL_PROBE_EXTRA_M = 3.0   # probe "a few meters" beyond pavement edge
GUARDRAIL_RAIL_EXTRA_M = 0.3    # rail seat: pavement edge + 30cm
GUARDRAIL_MIN_RUN_M = 10.0           # sustained-drop requirement
GUARDRAIL_GAP_BRIDGE_M = 6.0         # short sub-threshold gaps get bridged
JUNCTION_EXCLUDE_MARGIN_M = 15.0     # break a run this far before/after a junction
WALL_CHUNK_STATIONS = 80             # matches build_road_barrier.py's tile-bucketing granularity

JERSEY_CLASS_IDS = {0}   # motorway/motorway_link (ROAD_CLASS_ID) - US Interstates

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
SETTLEMENT_PATH = STATIONS_PATH.parent / "settlement_tiles.json"
OUT_PATH = STATIONS_PATH.parent / "road_guardrails.json"


def load_settlement_tiles():
    if not SETTLEMENT_PATH.exists():
        return set()
    tiles = json.loads(SETTLEMENT_PATH.read_text())["tiles"]
    return {tuple(int(p) for p in k.split(",")) for k in tiles}


def contiguous_runs(mask):
    """Yields (start, end) exclusive index pairs of contiguous True runs."""
    diff = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    return list(zip(starts, ends))


def bridge_short_gaps(mask, s_m, gap_m):
    """Fills any False run bounded by True on both sides whose span (between
    the surrounding True stations) is <= gap_m - a short sub-threshold dip
    (a single momentary terrain bump/dip in the drop measurement) shouldn't
    chop one real sustained drop into a stutter of separate rail runs."""
    out = mask.copy()
    n = len(mask)
    i = 0
    while i < n:
        if out[i]:
            i += 1
            continue
        j = i
        while j < n and not out[j]:
            j += 1
        if i > 0 and j < n:
            gap_len = s_m[j] - s_m[i - 1]
            if gap_len <= gap_m:
                out[i:j] = True
        i = j
    return out


def build_station_runs(ox_cm, oy_cm, z_cm, tile_size_cm):
    """Splits one side's offset station polyline (already filtered to a
    single contiguous keep-run) into tile-bucketable chunks - station points
    only, mirrors build_road_barrier.py's build_wall_runs()."""
    n = len(ox_cm)
    runs = []
    for c0 in range(0, max(1, n - 1), WALL_CHUNK_STATIONS):
        c1 = min(c0 + WALL_CHUNK_STATIONS, n - 1)
        if c1 <= c0 and n > 1:
            continue
        pts = [(ox_cm[i], oy_cm[i], z_cm[i]) for i in range(c0, c1 + 1)]
        if len(pts) < 2:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        tile_key = f"{int(cx // tile_size_cm)},{int(cy // tile_size_cm)}"
        runs.append({
            "tile": tile_key,
            "station_xyz_cm": [round(v, 1) for p in pts for v in p],
        })
    return runs


def main():
    data = json.loads(STATIONS_PATH.read_text())

    # NOT loading settlement tiles here - see the module docstring's
    # exclusions note for why a blanket town-tile mask was tried and
    # reverted (it ate the Dillon Dam Road test case). load_settlement_
    # tiles() is kept below, unused, in case a future, more targeted
    # "actual street footprint" mask replaces this junction-only approach.
    lakes = load_lake_beds()
    if lakes:
        print(f"{len(lakes)} lake bed(s) loaded - guardrail runs over open water will be skipped")

    # cross-chain junction proximity (2026-09-04): same reconcile_junction_
    # elevations() detection build_road_stations.py already runs, reused
    # here purely for its `touched` mask (see module docstring) - flattened
    # across every segment the same way build_retaining_walls.py does.
    all_east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    all_north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    all_elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    all_seg_idx = np.concatenate([
        np.full(len(seg["east_m"]), i, dtype=np.int32) for i, seg in enumerate(data["segments"])])
    all_station_idx = np.concatenate([np.arange(len(seg["east_m"])) for seg in data["segments"]])
    all_road_name = [name for seg in data["segments"] for name in seg["road_name"]]
    # is_bridge here is road_stations.json's own carve-exclusion flag (bridge/
    # tunnel span, dilated) - same 2026-09-04 exclusion build_road_stations.py
    # applies when it first runs this pass, so a structure station's pinned
    # elevation can't get matched by XY proximity against an unrelated
    # same-named-road station and break a guardrail run on a spurious pop.
    all_is_bridge = np.concatenate([np.array(seg["is_bridge"], dtype=bool) for seg in data["segments"]])
    _reconciled, junction_touched, _pinned = reconcile_junction_elevations(
        all_east, all_north, all_elev, all_seg_idx, all_station_idx, all_road_name,
        is_bridge=all_is_bridge)
    # ... plus every at-grade cross-road crossing junction_agreement_pass()
    # knows about (item 5, 2026-09-06): on shipped stations those pairs now
    # AGREE exactly (the stations build already ran the pass), so only its
    # `touched` junction-location mask matters here - a rail running
    # straight through an at-grade crossing would fence off the cross road.
    _agreed, agree_touched = junction_agreement_pass(
        all_east, all_north, all_elev, all_seg_idx)
    junction_touched = junction_touched | agree_touched
    offsets = np.concatenate([[0], np.cumsum([len(seg["east_m"]) for seg in data["segments"]])])
    print(f"{int(junction_touched.sum())} station(s) flagged near a road-road junction "
          f"(guardrail runs will break there)")

    session = requests.Session()
    dem_cache = {}

    runs_out = []
    km_by_profile = {"jersey": 0.0, "wbeam": 0.0}
    n_skipped_lake = n_skipped_junction = 0

    for seg_i, seg in enumerate(data["segments"]):
        east = np.array(seg["east_m"])
        north = np.array(seg["north_m"])
        elev = np.array(seg["elev_m"])
        s = np.array(seg["s_m"])
        n = len(east)
        if n < 3:
            continue
        is_bridge = np.array(seg.get("is_bridge", [False] * n), dtype=bool)
        highway = seg.get("highway_class", [""] * n)
        lo, hi = offsets[seg_i], offsets[seg_i + 1]
        seg_junction = junction_touched[lo:hi]

        # tangent (east/north METER frame) via central differences on the
        # native ~5m station spacing (STATION_SPACING) - no resampling
        # needed, matches build_retaining_walls.py's "operate on the native
        # station arrays directly" approach.
        tx = np.gradient(east)
        ty = np.gradient(north)
        tl = np.hypot(tx, ty)
        tl[tl < 1e-6] = 1.0
        tx /= tl
        ty /= tl
        rx, ry = -ty, tx   # perpendicular, east/north meter frame

        road_z_ue_m = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M)

        # Per-station pavement half-width, same computation the road mesh
        # itself uses - rails hug the real edge on wide (I-70) and narrow
        # (oneway ramp) roads alike instead of assuming 4.5 m everywhere.
        if highway and any(highway):
            half_w = half_width_for_stations(highway, seg.get("oneway"), seg.get("lanes"))
        else:
            half_w = np.full(n, ROAD_HALF_WIDTH)
        probe_off = half_w + GUARDRAIL_PROBE_EXTRA_M
        bank = bank_slopes(east, north)[0]
        rail_off = half_w + GUARDRAIL_RAIL_EXTRA_M

        for side in (-1.0, 1.0):
            probe_e = east + rx * probe_off * side
            probe_n = north + ry * probe_off * side
            rail_e = east + rx * rail_off * side
            rail_n = north + ry * rail_off * side

            probe_lon, probe_lat = to_lonlat(probe_e, probe_n)
            terrain_elev = sample_dem(probe_lat, probe_lon, NEAR_DEM_ZOOM, session, dem_cache)

            drop_m = elev - terrain_elev
            drop_mask = drop_m >= GUARDRAIL_DROP_THRESHOLD_M

            rail_cx = rail_e * 100.0
            rail_cy = -rail_n * 100.0

            # Narrower than build_road_barrier.py's own lake exclusion -
            # point-in-polygon ONLY, no shore-proximity margin dilation. See
            # the module docstring's "open water" exclusion note for why
            # (Dillon Dam Road's real, sustained shoreline drop was silently
            # eaten by the margin version during verification). bbox_margin_cm
            # below is just a bbox pre-filter for the candidate search, not
            # part of the actual inclusion test.
            in_lake = np.zeros(n, dtype=bool)
            bbox_margin_cm = LAKE_SHORE_EXCLUSION_MARGIN_M * 100.0
            for lake in lakes_near(lakes, rail_cx, rail_cy, bbox_margin_cm):
                bx0, bx1, by0, by1 = lake["bbox"]
                bb = ((rail_cx >= bx0 - bbox_margin_cm) & (rail_cx <= bx1 + bbox_margin_cm) &
                      (rail_cy >= by0 - bbox_margin_cm) & (rail_cy <= by1 + bbox_margin_cm))
                if not bb.any():
                    continue
                idx = np.where(bb)[0]
                inside = _point_in_polygon(rail_cx[idx], rail_cy[idx], lake["ring_x"], lake["ring_y"])
                in_lake[idx] |= inside

            # is_bridge deliberately NOT included here - see the module
            # docstring's "Deliberately NOT excluded" note. Settlement
            # tiles deliberately NOT included either - see the "NOT
            # populated (settlement) tiles" exclusions note.
            open_mask = in_lake | seg_junction
            keep_mask = drop_mask & ~open_mask
            keep_mask = bridge_short_gaps(keep_mask, s, GUARDRAIL_GAP_BRIDGE_M)
            keep_mask &= ~open_mask   # re-apply: bridging must never cross a hard exclusion

            for i in np.where(drop_mask & open_mask)[0]:
                if in_lake[i]:
                    n_skipped_lake += 1
                else:
                    n_skipped_junction += 1

            # seated on the banked edge's height (terrain holds it outward);
            # rx, ry is LEFT of travel, the bank slope per metre to the right
            z_cm = (road_z_ue_m - bank * side * half_w) * 100.0

            for run_start, run_end in contiguous_runs(keep_mask):
                run_len_m = s[run_end - 1] - s[run_start]
                if run_len_m < GUARDRAIL_MIN_RUN_M:
                    continue
                cls_id, cls_name, _lanes = road_class_and_lanes(
                    highway[run_start:run_end])
                is_jersey = cls_id in JERSEY_CLASS_IDS
                profile = "jersey" if is_jersey else "wbeam"
                km_by_profile[profile] += run_len_m / 1000.0

                station_runs = build_station_runs(
                    rail_cx[run_start:run_end], rail_cy[run_start:run_end],
                    z_cm[run_start:run_end], TERRAIN_TILE_SIZE_CM)
                for sr in station_runs:
                    sr["side"] = int(side)
                    sr["class_id"] = int(cls_id)
                    runs_out.append(sr)

    OUT_PATH.write_text(json.dumps({"runs": runs_out}))
    total_stations = sum(len(r["station_xyz_cm"]) // 3 for r in runs_out)
    print(f"{len(runs_out)} guardrail run(s) ({total_stations} station(s)) -> {OUT_PATH}")
    print(f"  ~{km_by_profile['wbeam']:.2f} km W-beam, ~{km_by_profile['jersey']:.2f} km Jersey barrier")
    print(f"  skipped near drop: {n_skipped_lake} lake, {n_skipped_junction} junction "
          f"(bridge/culvert spans and settlement tiles are NOT skipped - see module docstring)")


if __name__ == "__main__":
    main()
