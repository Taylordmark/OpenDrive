"""Traffic demand per directed lane-graph edge (PLAN-REALISM 3b, 2026-10-14).

Density comes from real volumes and real daily patterns, not a setting (D4):
  - Volumes: FHWA HPMS AADT (public domain) for every federal-aid section in
    the area, from the per-state geo.dot.gov feature services. Each lane-graph
    edge takes the nearest HPMS section within MATCH_RADIUS_M whose bearing
    agrees, its HPMS functional class fits the OSM class (so a frontage road
    beside I-70 doesn't take the interstate's volume) and ramps only match
    ramps.
  - Unmatched edges (locals, most residential): a per-class AADT scaled by the
    building count of the edge's 2 km tile (settlement_tiles.json "counts").
    ponytail: buildings stand in for the Census block-group population plan
    3b names - swap in the raster if local streets read wrong in a drive.
  - Hourly / weekly shape: HOUR_SHARE and DOW_FACTOR below, after the FHWA
    Traffic Monitoring Guide's typical urban-commuter vs rural curves.
    ponytail: one national curve per (urban|rural, weekday|weekend); the TMG
    per-station factor groups are the upgrade if a road's timing reads wrong.

Writes Content/OpenDrive/Masks/traffic_<area>_test.bin, edge order = the
routegraph_<area>_test.bin ODG1 it was built from:
    'ODT1' uint32 E
    4 x 24 float32  HOUR_SHARE  (urban wkday, rural wkday, urban wkend, rural wkend)
    7 x float32     DOW_FACTOR  (Mon..Sun, x AADT)
    E x uint32      aadt        two-way vehicles/day of the road this edge is on
    E x uint8       dir_pct     peak-direction share 50..100 (HPMS dir_factor)
    E x uint8       truck_pct   trucks (HPMS single-unit + combination) % of AADT
    E x uint8       flags       bit 0 urban, bit 1 HPMS-matched,
                                bit 2 a->b heads toward the area's densest
                                town (the morning peak direction)
Hourly one-way flow on an edge = demand_vph() below, in passenger-car
equivalents (a truck = TRUCK_PCE cars, HCM level terrain); 3c's
AODTrafficManager mirrors it.

Usage: OD_AREA=<area> python build_traffic_demand.py   (after
export_road_centerline_bin.py; rebuild_area.py runs it there)
"""
import json
import struct
import sys
from pathlib import Path

import numpy as np
import requests

from bake_job import outline_ll
from fetch_cache import cached_bytes, clip_geojson_lines
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM, lonlat_bbox, to_local

sys.path.insert(0, str(Path(__file__).resolve().parent / "checks"))
from verify_route_graph import load_graph   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
SETTLEMENTS = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "settlement_tiles.json"
HEADERS = {"User-Agent": "OpenDrive-pipeline/1.0 (traffic demand bake)"}
HPMS_YEARS = (2024, 2023, 2022)       # newest first; field names differ by year
# by road class id: freeway carriageways split up to ~80 m around HPMS's one
# centreline (I-70 at Vail Pass); everything else stays tight
MATCH_RADIUS_M = (80.0, 80.0, 30.0, 30.0, 30.0, 30.0, 30.0)
# HPMS f_system (1 interstate .. 7 local) each OSM class may match
F_SYSTEM_OK = ((1, 2, 3), (1, 2, 3, 4), (2, 3, 4, 5), (3, 4, 5, 6), (4, 5, 6, 7), (6, 7), (6, 7))
MATCH_BEARING_DEG = 25.0
SAMPLE_STEP_M = 10.0
CLS_RAMP = 8                          # export_road_centerline_bin class flag
TRUCK_PCE = 2.0                       # HCM passenger-car equivalent, level terrain
FALLBACK_TRUCK_PCT = 5

# Percent of the day's traffic in each hour 0..23 (normalised below).
HOUR_SHARE = np.array([
    # urban weekday: two commute peaks, 7-9 and 16-19
    [0.9, 0.6, 0.5, 0.5, 0.8, 2.0, 5.0, 7.2, 6.6, 5.3, 5.0, 5.3,
     5.6, 5.6, 6.0, 6.9, 7.6, 7.8, 6.3, 4.7, 3.8, 3.2, 2.4, 1.6],
    # rural weekday: one broad afternoon hump
    [1.0, 0.7, 0.6, 0.6, 0.9, 1.8, 3.4, 5.0, 5.4, 5.6, 6.0, 6.3,
     6.5, 6.6, 7.0, 7.4, 7.6, 7.2, 5.8, 4.6, 3.7, 3.0, 2.3, 1.5],
    # urban weekend: late start, midday plateau
    [1.8, 1.3, 1.0, 0.7, 0.6, 0.9, 1.6, 2.5, 3.8, 5.2, 6.4, 7.2,
     7.6, 7.7, 7.6, 7.4, 7.1, 6.6, 5.8, 4.8, 4.0, 3.4, 2.8, 2.2],
    # rural weekend
    [1.5, 1.0, 0.8, 0.6, 0.6, 0.9, 1.7, 2.9, 4.3, 5.7, 6.8, 7.4,
     7.7, 7.7, 7.6, 7.4, 7.0, 6.4, 5.5, 4.5, 3.7, 3.0, 2.4, 1.9],
], dtype=np.float64)
HOUR_SHARE /= HOUR_SHARE.sum(axis=1, keepdims=True)
DOW_FACTOR = np.array([1.02, 1.04, 1.05, 1.06, 1.10, 0.92, 0.81])   # Mon..Sun, mean 1.0

# Fallback AADT by road class id (road_mesh_common.ROAD_CLASS_ID) for a rural
# tile, and the gain toward a dense one (FALLBACK_TOWN_GAIN x at
# FALLBACK_DENSE_BUILDINGS per 2 km tile).
FALLBACK_AADT = (20000, 9000, 5000, 2500, 1200, 300, 80)
FALLBACK_DENSE_BUILDINGS = 2000.0
FALLBACK_TOWN_GAIN = 4.0
FALLBACK_RAMP_SHARE = 0.25          # an unmatched ramp carries this much of its class
URBAN_BUILDINGS = 300                 # a tile at least this built-up is urban

# Capacity per lane, vehicles/hour, by class id: freeway ~1900 (HCM base),
# signalised / at-grade arterials about half that (green time), locals less.
CAPACITY_VPHPL = (1900, 1500, 900, 800, 700, 500, 300)


def demand_vph(aadt, dir_pct, flags, forward, hour, dow, truck_pct=0):
    """One-way hourly flow (passenger-car equivalents) on an edge in its a->b
    (forward=True) or b->a direction at hour 0..23 of day-of-week 0=Mon..6=Sun.
    The peak-direction share goes inbound before noon and outbound after."""
    urban = bool(flags & 1)
    weekend = dow >= 5
    share = HOUR_SHARE[(2 if weekend else 0) + (0 if urban else 1)][int(hour) % 24]
    inbound = bool(flags & 4) == forward
    peak = (hour < 12) == inbound
    d = dir_pct / 100.0
    pce = 1.0 + truck_pct / 100.0 * (TRUCK_PCE - 1.0)
    return aadt * pce * DOW_FACTOR[dow] * share * (d if peak else 1.0 - d)


def capacity_vph(cls, lanes):
    return CAPACITY_VPHPL[min(cls & 7, 6)] * max(int(lanes), 1)


def _get_json(url, params):
    r = requests.get(url, params=params, headers=HEADERS, timeout=120)
    r.raise_for_status()
    return r.content


def state_at(lon, lat):
    raw = cached_bytes("census_state", f"{lon:.3f}_{lat:.3f}.json", lambda: _get_json(
        "https://geocoding.geo.census.gov/geocoder/geographies/coordinates",
        {"x": lon, "y": lat, "benchmark": "Public_AR_Current",
         "vintage": "Current_Current", "layers": "States", "format": "json"}))
    states = json.loads(raw)["result"]["geographies"].get("States", [])
    return states[0]["STUSAB"] if states else None


def hpms_features(state, snwe):
    """Every HPMS section with an AADT intersecting snwe, newest year served."""
    s, w, n, e = snwe
    for year in HPMS_YEARS:
        url = f"https://geo.dot.gov/server/rest/services/Hosted/HPMS_FULL_{state}_{year}/FeatureServer/0/query"
        feats, off = [], 0
        try:
            while True:
                raw = cached_bytes("hpms", f"{state}_{year}_{s:.4f}_{w:.4f}_{n:.4f}_{e:.4f}_{off}.json",
                                   lambda: _get_json(url, {
                                       "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
                                       "inSR": 4326, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
                                       "where": "aadt > 0", "outFields": "*", "returnGeometry": "true",
                                       "resultOffset": off, "resultRecordCount": 2000, "f": "geojson"}))
                d = json.loads(raw)
                if "features" not in d:
                    raise RuntimeError(d.get("error", d))
                feats += d["features"]
                if len(d["features"]) < 2000:
                    # (2026-10-21) a drawn area keeps the sections inside its outline
                    poly = outline_ll()
                    return year, (feats if poly is None else clip_geojson_lines(feats, poly))
                off += 2000
        except (requests.HTTPError, RuntimeError) as err:
            print(f"  HPMS {state} {year}: {err} - trying an older year")
    return None, []


def hpms_samples(features):
    """-> (xy Nx2 m, bearing N deg mod 180, rows of aadt, dir_pct, urban, ramp, f_system, truck_pct) sampled
    every SAMPLE_STEP_M along each section."""
    xy, brg, rows = [], [], []
    for f in features:
        p = f["properties"]
        aadt = int(p.get("aadt") or 0)
        if aadt <= 0 or not f.get("geometry"):
            continue
        g = f["geometry"]
        lines = g["coordinates"] if g["type"] == "MultiLineString" else [g["coordinates"]]
        dirf = int(p.get("dir_factor") or 55)
        urban = int(p.get("urban_id") or 99999) != 99999
        ramp = int(p.get("facility_type") or 0) == 4
        fsys = int(p.get("f_system") or 7)
        trucks = int(p.get("aadt_single_unit") or 0) + int(p.get("aadt_combination") or 0)
        tpct = min(100.0 * trucks / aadt, 60.0) if trucks else FALLBACK_TRUCK_PCT
        for line in lines:
            ll = np.asarray(line, dtype=np.float64)[:, :2]
            if len(ll) < 2:
                continue
            e, n = to_local(ll[:, 0], ll[:, 1])
            pts = np.stack([e, n], axis=1)
            for a, b in zip(pts[:-1], pts[1:]):
                d = b - a
                L = float(np.hypot(*d))
                if L < 1e-3:
                    continue
                k = max(int(L // SAMPLE_STEP_M), 1)
                t = (np.arange(k) + 0.5) / k
                xy.append(a + t[:, None] * d)
                brg += [np.degrees(np.arctan2(d[0], d[1])) % 180.0] * k
                rows += [(aadt, min(max(dirf, 50), 100), urban, ramp, fsys, tpct)] * k
    if not xy:
        return np.zeros((0, 2)), np.zeros(0), np.zeros((0, 6))
    return np.concatenate(xy), np.asarray(brg), np.asarray(rows, dtype=np.float64)


def match_edges(mid, brg, cls, s_xy, s_brg, s_rows):
    """Index into the HPMS samples per edge, -1 = no match. cls = lane-block class byte."""
    from scipy.spatial import cKDTree
    out = np.full(len(mid), -1, dtype=np.int64)
    if not len(s_xy):
        return out
    tree = cKDTree(s_xy)
    radius = np.array(MATCH_RADIUS_M)[np.minimum(cls & 7, 6)]
    dist, idx = tree.query(mid, k=16, distance_upper_bound=max(MATCH_RADIUS_M))
    for i in range(len(mid)):
        c = min(int(cls[i]) & 7, 6)
        for d, j in zip(dist[i], idx[i]):   # nearest first
            if d > radius[i]:
                break
            db = abs(brg[i] - s_brg[j]) % 180.0
            if (min(db, 180.0 - db) <= MATCH_BEARING_DEG and bool(s_rows[j, 3]) == bool(cls[i] & CLS_RAMP)
                    and int(s_rows[j, 4]) in F_SYSTEM_OK[c]):
                out[i] = j
                break
    return out


def edge_values(m, s_rows, fb, bcount):
    """-> (aadt, dir_pct, truck_pct, urban) per edge: the matched HPMS row, else the fallback."""
    hit = m >= 0
    if not len(s_rows):
        # HPMS down (HTTP 500 every year, 2026-10-15 Breckenridge) or no sections: all fallback.
        print("  WARNING: no HPMS samples - every edge uses the fallback AADT")
        s_rows = np.zeros((1, 6))
    r = s_rows[np.maximum(m, 0)]
    return (np.where(hit, r[:, 0], fb), np.where(hit, r[:, 1], 55.0),
            np.where(hit, r[:, 5], FALLBACK_TRUCK_PCT), np.where(hit, r[:, 2] > 0, bcount >= URBAN_BUILDINGS))


def main():
    nodes, edges, _dirs, lane = load_graph(MASKS / f"routegraph_{AREA_NAME}_test.bin")
    assert lane is not None, "routegraph has no lane block - re-run export_road_centerline_bin.py"
    en = np.stack([nodes[:, 0] / 100.0, -nodes[:, 1] / 100.0], axis=1)   # UE cm -> east/north m
    a, b = en[edges["a"]], en[edges["b"]]
    mid, d = (a + b) / 2, b - a
    brg = np.degrees(np.arctan2(d[:, 0], d[:, 1])) % 180.0
    cls = lane["cls"]

    lo, hi = en.min(axis=0) - 100.0, en.max(axis=0) + 100.0
    snwe = lonlat_bbox(lo[0], lo[1], hi[0], hi[1])
    probes = [(snwe[1], snwe[0]), (snwe[3], snwe[0]), (snwe[1], snwe[2]), (snwe[3], snwe[2]),
              ((snwe[1] + snwe[3]) / 2, (snwe[0] + snwe[2]) / 2)]
    states = sorted({s for s in (state_at(lo_, la_) for lo_, la_ in probes) if s})
    feats = []
    for st in states:
        year, f = hpms_features(st, snwe)
        print(f"  HPMS {st} {year}: {len(f)} section(s)")
        feats += f
    s_xy, s_brg, s_rows = hpms_samples(feats)
    m = match_edges(mid, brg, cls, s_xy, s_brg, s_rows)

    # tile building counts: fallback volumes, urban flag, the "town" for the peak direction
    st = json.loads(SETTLEMENTS.read_text()) if SETTLEMENTS.exists() else {}
    counts = {tuple(map(int, k.split(","))): n for k, n in st.get("counts", {}).items()}
    if not counts and st.get("tiles"):
        print("  settlement_tiles.json has no counts - re-run classify_settlements.py")
    tile = lambda xy: (int(xy[0] * 100.0 // TERRAIN_TILE_SIZE_CM), int(-xy[1] * 100.0 // TERRAIN_TILE_SIZE_CM))
    bcount = np.array([counts.get(tile(p), 0) for p in mid], dtype=np.float64)

    hit = m >= 0
    fb = np.array(FALLBACK_AADT, dtype=np.float64)[np.minimum(cls & 7, 6)]
    fb *= 1.0 + (FALLBACK_TOWN_GAIN - 1.0) * np.clip(bcount / FALLBACK_DENSE_BUILDINGS, 0.0, 1.0)
    fb *= np.where(cls & CLS_RAMP, FALLBACK_RAMP_SHARE, 1.0)
    aadt, dir_pct, truck_pct, urban = edge_values(m, s_rows, fb, bcount)

    if counts:
        (tx, ty), _ = max(counts.items(), key=lambda kv: kv[1])
        town = np.array([(tx + 0.5) * TERRAIN_TILE_SIZE_CM / 100.0, -(ty + 0.5) * TERRAIN_TILE_SIZE_CM / 100.0])
    else:
        town = en.mean(axis=0)
    inbound = np.linalg.norm(b - town, axis=1) < np.linalg.norm(a - town, axis=1)
    flags = (urban.astype(np.uint8) | (hit.astype(np.uint8) << 1) | (inbound.astype(np.uint8) << 2))

    out = MASKS / f"traffic_{AREA_NAME}_test.bin"
    with open(out, "wb") as f:
        f.write(b"ODT1")
        f.write(struct.pack("<I", len(edges)))
        f.write(HOUR_SHARE.astype("<f4").tobytes())
        f.write(DOW_FACTOR.astype("<f4").tobytes())
        f.write(np.round(aadt).astype("<u4").tobytes())
        f.write(np.round(dir_pct).astype("u1").tobytes())
        f.write(np.round(truck_pct).astype("u1").tobytes())
        f.write(flags.astype("u1").tobytes())
    km = np.hypot(*d.T) / 1000.0
    print(f"wrote {out} ({len(edges)} edges; HPMS-matched {hit.mean():.1%} of edges, "
          f"{km[hit].sum():.0f} of {km.sum():.0f} km; urban {urban.mean():.0%}; "
          f"top AADT {int(aadt.max())})")


if __name__ == "__main__":
    main()
