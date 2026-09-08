"""Embankment skirts: where the road rides ABOVE the surrounding terrain but
not so high a bridge deck is the right answer, fill the gap with a sloped
earthen skirt from the pavement edge down to terrain. Third leg of the same
"pipeline ships points, C++ extrudes at runtime" split as
build_road_guardrails.py/AODGuardrailManager (visible guardrails, Arc 7.16) -
this module reuses that script's own terrain-drop detection shape almost
verbatim (same DEM probe, same lake/junction exclusions) rather than
reinventing it; see that file's docstring for the exclusion reasoning this
one reuses.

Three-way classification by per-station drop (Taylor-approved design, see
"next steps.md" §7.19):
    drop <= EMBANK_LOW_THRESHOLD_M (2.5 m)   -> none        (terrain meets
                                                              road acceptably)
    EMBANK_LOW < drop <= EMBANK_HIGH (7 m)   -> embankment   (this module
                                                              emits geometry)
    drop > EMBANK_HIGH_THRESHOLD_M, OR a      -> bridge       (data only -
    qualifying drop over open water                          the bridge deck/
                                                              pier task, not
                                                              yet built, is
                                                              the consumer -
                                                              see EMBANK_
                                                              MIN_RUN_M note
                                                              below)
All three classes are written to road_embankments.json so the future bridge
task has real per-station drop data to work from without re-running this
detection; export_road_embankment_bin.py only packs "embankment"-class runs
into the runtime tile format, since AODEmbankmentManager is the only runtime
consumer that exists yet.

Threshold stability: a per-side Schmitt-trigger (hysteresis) state machine
walks each side's station sequence so a station sitting exactly on 2.5 m or
7 m doesn't flicker in/out of a class station-to-station - the state only
crosses UP at threshold+EMBANK_HYSTERESIS_M and back DOWN at
threshold-EMBANK_HYSTERESIS_M. On top of that, exactly like guardrails'
GUARDRAIL_MIN_RUN_M, any surviving contiguous run shorter than
EMBANK_MIN_RUN_M is demoted back to "none" - a real embankment/bridge is a
sustained condition, not a momentary DEM blip.

Exclusions, reused from build_road_guardrails.py's own precedent:
  - junction proximity: FIRST DRAFT of this module used only the newer
    per-station `in_junction_box` field (Arc 7.18) directly, reasoning that
    it was a strict superset-free shortcut around re-deriving
    reconcile_junction_elevations()'s own `touched` mask a second time the
    way build_road_guardrails.py does. Live verification caught this as
    WRONG: `in_junction_box` only covers the literal flush-pavement box at
    each real graph junction node, which is far narrower than guardrails'
    `touched` mask - and in dense town street grids (Frisco/Breckenridge are
    built on real hillside grades, not flat), the gaps BETWEEN that narrow
    box and the next one are exactly where noisy DEM-vs-pavement-edge
    sampling produces spurious multi-meter "drops" that have nothing to do
    with a real embankment-worthy slope (a probe point 7.5m off a town
    street can land on a neighboring lot's own cut/fill grade). Switched to
    reusing `reconcile_junction_elevations()`'s `touched` mask exactly the
    way build_road_guardrails.py does (same call, same is_bridge exclusion)
    - empirically the mechanism that actually keeps guardrails' own town
    negative-control (`rails_town_negative.png`, North 6th Avenue Frisco)
    clean, not `in_junction_box` (which postdates that verification and was
    never actually exercised against the town-grid case). `in_junction_box`
    is ALSO applied (the two masks are OR'd) since it's free and strictly
    correct for the literal box itself. A station in either mask is always
    "none" - no skirt should spill across an intersection a driver needs to
    turn into, and the (separately-scoped) bridge task will have its own
    junction handling if a bridge ever lands squarely on one.
  - open water: same point-in-polygon-only lake test as
    build_road_guardrails.py (deliberately narrower than
    build_road_barrier.py's shore-margin version - see that module's
    docstring for why the margin version silently ate Dillon Dam Road).
    Unlike guardrails, a qualifying-drop station over water is NOT skipped
    here - it's classified "bridge" (a water crossing should get a deck,
    never a dirt fill dumped into the reservoir), matching the brief's
    "drop > 7m (or water crossing): bridge" rule.

Dillon Dam Road (the guardrail feature's own primary test case - a real
causeway with a sustained drop to the reservoir) is expected to land as
"bridge" here precisely because it's a water crossing, not because of raw
drop magnitude alone - see verify_embank_runs.txt for the actual measured
numbers instead of assuming.

Usage:  python build_road_embankments.py   (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Outputs data/road_tiles/<AREA_NAME>_test/road_embankments.json
        {"runs": [{"tile","side","cls","station_xyzd_cm"}, ...]}
        (cls: "embankment" or "bridge"; station_xyzd_cm is a flat
        [x,y,z,drop, x,y,z,drop, ...] list, cm, drop_cm = road-above-terrain
        drop at that station in cm)
"""

import json
import math
from pathlib import Path

import numpy as np
import requests

from road_mesh_common import ROAD_HALF_WIDTH, road_ue_z_m, reconcile_junction_elevations
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM
from build_terrain_mesh_tiles import (NEAR_DEM_ZOOM, M_PER_DEG_LAT, sample_dem,
                                       load_lake_beds, _point_in_polygon,
                                       WATER_SHORE_BLEND_M)

LAKE_SHORE_EXCLUSION_MARGIN_M = WATER_SHORE_BLEND_M + 3.0   # bbox pre-filter only, see docstring

EMBANK_LOW_THRESHOLD_M = 2.5     # <= this: no treatment
EMBANK_HIGH_THRESHOLD_M = 7.0    # > this (or water crossing): bridge, not embankment
EMBANK_HYSTERESIS_M = 0.3        # Schmitt-trigger band half-width at each threshold
EMBANK_MIN_RUN_M = 10.0          # sustained-condition requirement, matches guardrails
EMBANK_PROBE_OFFSET_M = ROAD_HALF_WIDTH + 3.0   # same "a few meters beyond the shoulder" probe
EMBANK_EDGE_OFFSET_M = ROAD_HALF_WIDTH          # skirt top seats AT the pavement edge (no gap
                                                  # vs. the road ribbon's own edge vertex)
WALL_CHUNK_STATIONS = 80         # matches build_road_guardrails.py's tile-bucketing granularity

NONE_CLS, EMBANK_CLS, BRIDGE_CLS = 0, 1, 2
CLS_NAMES = {EMBANK_CLS: "embankment", BRIDGE_CLS: "bridge"}

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_PATH = STATIONS_PATH.parent / "road_embankments.json"


def contiguous_runs(mask):
    """Yields (start, end) exclusive index pairs of contiguous True runs."""
    diff = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    return list(zip(starts, ends))


def classify_with_hysteresis(drop_m, in_lake, in_junction):
    """Per-station Schmitt-trigger classification along one side's station
    sequence - see module docstring. Returns an int array of NONE_CLS/
    EMBANK_CLS/BRIDGE_CLS, already zeroed at junction-box stations."""
    n = len(drop_m)
    out = np.zeros(n, dtype=np.int8)
    state = NONE_CLS
    lo, hi, hy = EMBANK_LOW_THRESHOLD_M, EMBANK_HIGH_THRESHOLD_M, EMBANK_HYSTERESIS_M
    for i in range(n):
        d = drop_m[i]
        if in_lake[i]:
            # Open water: never embankment (never dump fill into a lake) -
            # bridge if the drop crosses the LOW threshold at all (a water
            # crossing gets a deck the moment the road is meaningfully above
            # the water, not only once it's past the high threshold too),
            # else none.
            state = BRIDGE_CLS if d >= lo - hy else NONE_CLS
        elif state == NONE_CLS:
            if d >= hi + hy:
                state = BRIDGE_CLS
            elif d >= lo + hy:
                state = EMBANK_CLS
        elif state == EMBANK_CLS:
            if d >= hi + hy:
                state = BRIDGE_CLS
            elif d < lo - hy:
                state = NONE_CLS
        elif state == BRIDGE_CLS:
            if d < lo - hy:
                state = NONE_CLS
            elif d < hi - hy:
                state = EMBANK_CLS
        out[i] = state

    out[in_junction] = NONE_CLS
    return out


def demote_short_runs(cls_arr, s_m):
    """Any contiguous non-NONE run shorter than EMBANK_MIN_RUN_M (arc length)
    is demoted back to NONE_CLS - a momentary DEM blip shouldn't spawn a 3m
    stub of embankment or a one-station bridge flag. Mirrors build_road_
    guardrails.py's GUARDRAIL_MIN_RUN_M filter, generalized to a 3-class
    array (kept per-run rather than per-mask since a run may change class
    mid-run at the hi/lo boundary - it's still one physically-contiguous
    "above threshold" stretch)."""
    out = cls_arr.copy()
    nonzero = cls_arr != NONE_CLS
    for start, end in contiguous_runs(nonzero):
        run_len_m = s_m[end - 1] - s_m[start] if end > start else 0.0
        if run_len_m < EMBANK_MIN_RUN_M:
            out[start:end] = NONE_CLS
    return out


def build_station_runs(ox_cm, oy_cm, z_cm, drop_cm, tile_size_cm):
    """Splits one side's classified station polyline into tile-bucketable
    chunks carrying (x, y, z, drop) per station - mirrors build_road_
    guardrails.py's build_station_runs(), extended with the drop value the
    C++ manager needs to size the 1.5:1 slope."""
    n = len(ox_cm)
    runs = []
    for c0 in range(0, max(1, n - 1), WALL_CHUNK_STATIONS):
        c1 = min(c0 + WALL_CHUNK_STATIONS, n - 1)
        if c1 <= c0 and n > 1:
            continue
        pts = [(ox_cm[i], oy_cm[i], z_cm[i], drop_cm[i]) for i in range(c0, c1 + 1)]
        if len(pts) < 2:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        tile_key = f"{int(cx // tile_size_cm)},{int(cy // tile_size_cm)}"
        runs.append({
            "tile": tile_key,
            "station_xyzd_cm": [round(v, 1) for p in pts for v in p],
        })
    return runs


def main():
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    lakes = load_lake_beds()
    if lakes:
        print(f"{len(lakes)} lake bed(s) loaded - qualifying-drop stations over "
              f"open water are classified 'bridge', never 'embankment'")

    # cross-chain junction proximity (see module docstring's "Exclusions" note
    # for why this reuses build_road_guardrails.py's own `touched`-mask
    # mechanism instead of the narrower in_junction_box field alone).
    all_east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    all_north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    all_elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    all_seg_idx = np.concatenate([
        np.full(len(seg["east_m"]), i, dtype=np.int32) for i, seg in enumerate(data["segments"])])
    all_station_idx = np.concatenate([np.arange(len(seg["east_m"])) for seg in data["segments"]])
    all_road_name = [name for seg in data["segments"] for name in seg["road_name"]]
    all_is_bridge = np.concatenate([np.array(seg["is_bridge"], dtype=bool) for seg in data["segments"]])
    _reconciled, junction_touched, _pinned = reconcile_junction_elevations(
        all_east, all_north, all_elev, all_seg_idx, all_station_idx, all_road_name,
        is_bridge=all_is_bridge)
    offsets = np.concatenate([[0], np.cumsum([len(seg["east_m"]) for seg in data["segments"]])])
    print(f"{int(junction_touched.sum())} station(s) flagged near a road-road junction "
          f"(embankment/bridge classification forces 'none' there)")

    session = requests.Session()
    dem_cache = {}

    runs_out = []
    km_by_cls = {"embankment": 0.0, "bridge": 0.0}
    stations_by_cls = {"embankment": 0, "bridge": 0}
    n_skipped_junction = 0
    n_runs_by_cls = {"embankment": 0, "bridge": 0}
    dam_report = []

    for seg_i, seg in enumerate(data["segments"]):
        east = np.array(seg["east_m"])
        north = np.array(seg["north_m"])
        elev = np.array(seg["elev_m"])
        s = np.array(seg["s_m"])
        n = len(east)
        if n < 3:
            continue
        road_name = seg.get("road_name", [""] * n)
        in_junction_field = np.array(seg.get("in_junction_box", [False] * n), dtype=bool)
        lo, hi = offsets[seg_i], offsets[seg_i + 1]
        in_junction_box = in_junction_field | junction_touched[lo:hi]

        tx = np.gradient(east)
        ty = np.gradient(north)
        tl = np.hypot(tx, ty)
        tl[tl < 1e-6] = 1.0
        tx /= tl
        ty /= tl
        rx, ry = -ty, tx

        road_z_ue_m = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M)
        z_cm_all = road_z_ue_m * 100.0

        for side in (-1.0, 1.0):
            probe_e = east + rx * EMBANK_PROBE_OFFSET_M * side
            probe_n = north + ry * EMBANK_PROBE_OFFSET_M * side
            edge_e = east + rx * EMBANK_EDGE_OFFSET_M * side
            edge_n = north + ry * EMBANK_EDGE_OFFSET_M * side

            probe_lat = lat0 + probe_n / M_PER_DEG_LAT
            probe_lon = lon0 + probe_e / m_per_deg_lon
            terrain_elev = sample_dem(probe_lat, probe_lon, NEAR_DEM_ZOOM, session, dem_cache)

            drop_m = elev - terrain_elev

            edge_cx = edge_e * 100.0
            edge_cy = -edge_n * 100.0

            in_lake = np.zeros(n, dtype=bool)
            bbox_margin_cm = LAKE_SHORE_EXCLUSION_MARGIN_M * 100.0
            for lake in lakes:
                bx0, bx1, by0, by1 = lake["bbox"]
                bb = ((edge_cx >= bx0 - bbox_margin_cm) & (edge_cx <= bx1 + bbox_margin_cm) &
                      (edge_cy >= by0 - bbox_margin_cm) & (edge_cy <= by1 + bbox_margin_cm))
                if not bb.any():
                    continue
                idx = np.where(bb)[0]
                inside = _point_in_polygon(edge_cx[idx], edge_cy[idx], lake["ring_x"], lake["ring_y"])
                in_lake[idx] |= inside

            n_skipped_junction += int((in_junction_box & (drop_m >= EMBANK_LOW_THRESHOLD_M)).sum())

            cls_arr = classify_with_hysteresis(drop_m, in_lake, in_junction_box)
            cls_arr = demote_short_runs(cls_arr, s)

            drop_cm = np.clip(drop_m, 0.0, None) * 100.0

            for target_cls, cls_name in CLS_NAMES.items():
                for run_start, run_end in contiguous_runs(cls_arr == target_cls):
                    run_len_m = s[run_end - 1] - s[run_start]
                    km_by_cls[cls_name] += run_len_m / 1000.0
                    stations_by_cls[cls_name] += (run_end - run_start)
                    n_runs_by_cls[cls_name] += 1

                    station_runs = build_station_runs(
                        edge_cx[run_start:run_end], edge_cy[run_start:run_end],
                        z_cm_all[run_start:run_end], drop_cm[run_start:run_end],
                        TERRAIN_TILE_SIZE_CM)
                    for sr in station_runs:
                        sr["side"] = int(side)
                        sr["cls"] = cls_name
                        runs_out.append(sr)

                    names = set(road_name[run_start:run_end])
                    if any("Dillon Dam" in (nm or "") for nm in names):
                        dam_report.append((cls_name, run_len_m, float(drop_m[run_start:run_end].max())))

    OUT_PATH.write_text(json.dumps({"runs": runs_out}))
    total_embank_stations = stations_by_cls["embankment"]
    total_bridge_stations = stations_by_cls["bridge"]
    print(f"{len(runs_out)} run(s) ({total_embank_stations + total_bridge_stations} station(s)) -> {OUT_PATH}")
    print(f"  embankment: {n_runs_by_cls['embankment']} run(s), {total_embank_stations} station(s), "
          f"~{km_by_cls['embankment']:.2f} km")
    print(f"  bridge:     {n_runs_by_cls['bridge']} run(s), {total_bridge_stations} station(s), "
          f"~{km_by_cls['bridge']:.2f} km")
    print(f"  skipped (junction-box, qualifying drop): {n_skipped_junction} station(s)")
    if dam_report:
        print("  Dillon Dam Road runs found:")
        for cls_name, run_len_m, max_drop in dam_report:
            print(f"    {cls_name}: {run_len_m:.1f} m run, max drop {max_drop:.2f} m")
    else:
        print("  Dillon Dam Road: no qualifying run found on this side/segment pass")


if __name__ == "__main__":
    main()
