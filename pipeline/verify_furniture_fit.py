"""Offline gate on the SHIPPED furniture tiles (furniture_*.bin, ODF1 - see
export_road_furniture_bin.py), read back exactly as AODFurnitureManager
streams them:

  1. no prop except a stop bar inside any lane (shipped pavement, same
     1 m-densified centerline + half-width test build_road_furniture uses,
     with no extra clearance);
  2. every prop base within BASE_TOL_M of the shipped terrain or of the
     banked pavement edge next to it (stop bars: of the road surface);
  3. every signal / stop / yield pole within NODE_TOL_M of an OSM node of
     its kind;
  4. every tile carries the current generation id.

Prints per-type counts for the area. Exits 1 on any failure.

Usage:  python verify_furniture_fit.py
"""

import json
import struct
import sys
from collections import Counter

import numpy as np
from scipy.spatial import cKDTree

import build_retaining_walls as brw
import build_road_furniture as brf
from export_road_furniture_bin import OUT_DIR, RECORD
from generation_id import compute_generation_id
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, to_local

BASE_TOL_M = 0.2
NODE_TOL_M = 15.0
EDGE_SEARCH_M = 15.0
NODE_KIND = {"signal": "traffic_signals", "stop": "stop", "yield": "give_way"}


def load_tiles():
    gen = compute_generation_id()
    rows, stale = [], []
    for p in sorted(OUT_DIR.glob(f"furniture_{AREA_NAME}_test_tile_*.bin")):
        b = p.read_bytes()
        assert b[:4] == b"ODF1", p.name
        if b[4:20].decode("ascii").rstrip("\0") != gen:
            stale.append(p.name)
        n = struct.unpack_from("<I", b, 20)[0]
        rows += list(RECORD.iter_unpack(b[24:24 + n * RECORD.size]))
    return np.array(rows, dtype=float).reshape(-1, 6), stale


def main():
    P, stale = load_tiles()
    T = brf.T
    e0, n0, elev, seg_idx, _si, _rn = brw.load_station_arrays()
    hw = brw.load_station_half_widths()
    tunnel = np.concatenate([np.array(s.get("is_tunnel", [False] * len(s["east_m"])), dtype=bool)
                             for s in json.loads(brw.STATIONS_PATH.read_text())["segments"]])
    east, north, road_z, bank = brf.shipped_stations(e0, n0, elev)
    pe, pn, pz = P[:, 1] / 100.0, -P[:, 2] / 100.0, P[:, 3] / 100.0 + ORIGIN_HEIGHT_M
    bar = P[:, 0] == T["stop_bar"]
    ok = True

    counts = Counter(int(t) for t in P[:, 0])
    print(f"{len(P)} prop(s) in shipped furniture tiles:")
    for t, name in enumerate(brf.TYPES):
        print(f"  {AREA_NAME:>16}  {name:<15} {counts.get(t, 0):6d}")
    if stale:
        print(f"FAIL: {len(stale)} tile(s) with a stale generation id, e.g. {stale[:3]}")
        ok = False

    # 1. lanes
    inside = ~bar & brf.in_pavement(brf.pavement_index(east, north, road_z, hw, seg_idx, tunnel),
                                    pe, pn, pz, clear=0.0)
    print(f"[lanes] {int(inside.sum())} prop(s) inside a lane")
    for i in np.where(inside)[0][:10]:
        print(f"  {brf.TYPES[int(P[i, 0])]} at ({pe[i]:.1f}, {pn[i]:.1f}) m")
    ok = ok and not inside.any()

    # 2. bases: terrain (stop bars: baked road), else the banked edge (stop bars: surface) on the
    # prop's side of any station within EDGE_SEARCH_M - the bin does not say
    # which station placed it
    te, tn = np.empty_like(e0), np.empty_like(e0)
    for s in np.unique(seg_idx):
        m = np.where(seg_idx == s)[0]
        te[m], tn[m] = (np.gradient(e0[m]), np.gradient(n0[m])) if len(m) > 1 else (1.0, 0.0)
    tl = np.maximum(np.hypot(te, tn), 1e-9)
    te, tn = te / tl, tn / tl
    edge_err = np.full(len(P), np.inf)
    for i, near in enumerate(cKDTree(np.c_[east, north]).query_ball_point(np.c_[pe, pn], EDGE_SEARCH_M)):
        j = np.asarray(near, dtype=int)
        off = (pe[i] - east[j]) * tn[j] - (pn[i] - north[j]) * te[j]     # + = right of station order
        z = road_z[j] + bank[j] * np.clip(off, -hw[j], hw[j]) + (0.01 if bar[i] else 0.0)
        edge_err[i] = np.min(np.abs(pz[i] - z), initial=np.inf)
    terr = np.full(len(P), np.nan)
    terr[~bar] = brf.terrain_abs_m(pe[~bar], pn[~bar])
    terr[bar] = brf.road_abs_m(pe[bar], pn[bar]) + 0.01   # bars: the baked road surface, pads included
    err = np.fmin(np.abs(pz - terr), edge_err)
    bad = err > BASE_TOL_M
    print(f"[bases] max {np.nanmax(err):.3f} m off terrain/edge, {int(bad.sum())} over {BASE_TOL_M} m")
    for i in np.where(bad)[0][:10]:
        print(f"  {brf.TYPES[int(P[i, 0])]} at ({pe[i]:.1f}, {pn[i]:.1f}) m: {err[i]:.2f} m")
    ok = ok and not bad.any()

    # 3. control poles near their OSM node - carried along with its nearest
    # station where junction pads XY-snapped the road (up to ~25 m)
    osm = json.loads((brf.RAW / "osm.json").read_text())
    otree = cKDTree(np.c_[e0, n0])
    for name, kind in NODE_KIND.items():
        ll = np.array([(e["lon"], e["lat"]) for e in osm["elements"]
                       if e["type"] == "node" and e.get("tags", {}).get("highway") == kind])
        k = P[:, 0] == T[name]
        if not k.any():
            continue
        xy = np.c_[to_local(ll[:, 0], ll[:, 1])]
        j = otree.query(xy)[1]
        xy = np.r_[xy, xy + np.c_[east[j] - e0[j], north[j] - n0[j]]]
        d = cKDTree(xy).query(np.c_[pe[k], pn[k]])[0]
        far = int((d > NODE_TOL_M).sum())
        print(f"[nodes] {name}: max {d.max():.1f} m from a {kind} node, {far} over {NODE_TOL_M} m")
        ok = ok and far == 0

    print("PASS: furniture fits the shipped road and terrain." if ok else "FAIL: see above.")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
