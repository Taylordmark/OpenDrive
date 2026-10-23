"""Speed traps and speed zones (2026-10-22, fun-features #2) for
AODChallengeManager, picked from road-station curvature.

  trap  a point on the longest low-curvature straights (radius > STRAIGHT_R_M
        over >= MIN_STRAIGHT_M): the run's end on a one-way road, its middle on
        a two-way one so both directions get a run-up. Top N_TRAPS, >= 1 km apart.
  zone  a ZONE_M stretch of flowing corners (most of it curved, no hairpins),
        scored by average speed at runtime. Top N_ZONES, >= 1 km apart.

Usage:  python build_speed_challenges.py [--root <repo>]   (OD_AREA picks the area)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
        data/road_tiles/<AREA_NAME>_test/road_labels.json  (optional, city names)
Writes  OpenDriveUE/Content/OpenDrive/Masks/challenges_<AREA_NAME>_test.json
    {"area", "traps": [{name, x_cm, y_cm, z_cm, yaw_deg, straight_m, limit_kmh}],
     "zones": [{name, length_m, line_cm: [[x, y, z], ...] every ~50 m, start first}]}
checks/verify_speed_challenges.py checks it against the stations.
"""

import argparse
import json
import math
from pathlib import Path

import numpy as np

from road_mesh_common import road_ue_z_m
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M

ROOT = Path(__file__).resolve().parent.parent
ROADS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified"}
STATION_M = 5.0
HALF_CHORD = 4            # heading from a +/-20 m chord
STRAIGHT_R_M = 1500.0
MIN_STRAIGHT_M = 400.0
N_TRAPS, N_ZONES = 8, 4
SPACING_M = 1000.0
ZONE_M = 2000.0
ZONE_STRIDE = 50          # stations between candidate zone starts (250 m)
FLOW_R_M = (60.0, 600.0)  # a "flowing corner" radius band
HAIRPIN_R_M = 40.0


def curvature(e, n):
    """Signed curvature (1/m) per station from chord headings."""
    k = HALF_CHORD
    i = np.arange(len(e))
    a, b = np.clip(i - k, 0, len(e) - 1), np.clip(i + k, 0, len(e) - 1)
    head = np.arctan2(n[b] - n[a], e[b] - e[a])
    d = (head[b] - head[a] + np.pi) % (2 * np.pi) - np.pi
    return d / np.maximum((b - a) * STATION_M, STATION_M)


def runs(mask):
    """(start, end) inclusive index pairs of True runs."""
    edges = np.flatnonzero(np.diff(np.concatenate([[0], mask.astype(np.int8), [0]])))
    return list(zip(edges[0::2], edges[1::2] - 1))


def spaced(cands, n, pts_of):
    """Greedy best-first pick of n candidates whose points are >= SPACING_M
    from every point already picked."""
    picked, taken = [], []
    for c in cands:
        pts = pts_of(c)
        if all(math.dist(p, q) >= SPACING_M for p in pts for q in taken):
            picked.append(c)
            taken.extend(pts)
            if len(picked) == n:
                break
    return picked


def unique(names, cities):
    """Repeated names get ", <city>" and then a number."""
    dup = {nm for nm in names if names.count(nm) > 1}
    names = [f"{nm}, {c}" if nm in dup and c and c not in nm else nm for nm, c in zip(names, cities)]
    seen, out = {}, []
    for nm in names:
        seen[nm] = seen.get(nm, 0) + 1
        out.append(nm if seen[nm] == 1 else f"{nm} {seen[nm]}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=ROOT, help="repo root holding data/ and OpenDriveUE/Content")
    ap.add_argument("--out", type=Path, help="output dir (default <root>/OpenDriveUE/Content/OpenDrive/Masks)")
    a = ap.parse_args()
    tiles = a.root / "data" / "road_tiles" / f"{AREA_NAME}_test"
    segs = json.loads((tiles / "road_stations.json").read_text())["segments"]
    labels_path = tiles / "road_labels.json"
    lab = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else None
    lab_xy = np.column_stack([lab["x_cm"], lab["y_cm"]]) / 100.0 if lab else None

    def city(e, n):
        if lab_xy is None:
            return ""
        return lab["city_label"][int(np.argmin(np.hypot(lab_xy[:, 0] - e, lab_xy[:, 1] + n)))]

    def place(seg, i):
        return seg["road_name"][i] or city(seg["east_m"][i], seg["north_m"][i]) or "Unnamed road"

    def xyz(seg, i):
        return [round(seg["east_m"][i] * 100.0, 1), round(-seg["north_m"][i] * 100.0, 1),
                round(road_ue_z_m(seg["elev_m"][i], 0.0, ORIGIN_HEIGHT_M) * 100.0, 1)]

    traps, zones = [], []
    for seg in segs:
        e, n = np.array(seg["east_m"]), np.array(seg["north_m"])
        if len(e) < 2 * HALF_CHORD + 2:
            continue
        kappa = curvature(e, n)
        main_road = np.array([c in ROADS and not t for c, t in zip(seg["highway_class"], seg["is_tunnel"])])
        straight = (np.abs(kappa) < 1.0 / STRAIGHT_R_M) & main_road
        for s0, s1 in runs(straight):
            length = (s1 - s0) * STATION_M
            if length >= MIN_STRAIGHT_M:
                i = s1 - 6 if seg["oneway"][s1] else (s0 + s1) // 2   # end: 30 m short of the bend
                traps.append((length, seg, i))
        ak = np.abs(kappa)
        w = int(ZONE_M / STATION_M)
        for s0 in range(0, len(e) - w, ZONE_STRIDE):
            win = ak[s0:s0 + w + 1]
            if not main_road[s0:s0 + w + 1].all() or win.max() > 1.0 / HAIRPIN_R_M:
                continue
            flowing = ((win > 1.0 / FLOW_R_M[1]) & (win < 1.0 / FLOW_R_M[0])).mean()
            if flowing >= 0.5:
                zones.append((flowing * np.minimum(win, 1.0 / FLOW_R_M[0]).mean(), seg, s0, s0 + w))

    traps.sort(key=lambda t: -t[0])
    zones.sort(key=lambda z: -z[0])
    traps = spaced(traps, N_TRAPS, lambda t: [(t[1]["east_m"][t[2]], t[1]["north_m"][t[2]])])
    zones = spaced(zones, N_ZONES, lambda z: [(z[1]["east_m"][j], z[1]["north_m"][j]) for j in (z[2], z[3])])

    out = dict(area=AREA_NAME, traps=[], zones=[])
    for name, (length, seg, i) in zip(unique([f"{place(s, i)} straight" for _, s, i in traps],
                                                       [city(s["east_m"][i], s["north_m"][i]) for _, s, i in traps]), traps):
        a_, b = max(i - HALF_CHORD, 0), min(i + HALF_CHORD, len(seg["east_m"]) - 1)
        yaw = math.degrees(math.atan2(-(seg["north_m"][b] - seg["north_m"][a_]), seg["east_m"][b] - seg["east_m"][a_]))
        x, y, z = xyz(seg, i)
        out["traps"].append(dict(name=name, x_cm=x, y_cm=y, z_cm=z, yaw_deg=round(yaw, 1),
                                 straight_m=round(length), limit_kmh=int(seg["maxspeed"][i])))
    for name, (_, seg, s0, s1) in zip(unique([f"{place(s, (a + b) // 2)} run" for _, s, a, b in zones],
                                                         [city(s["east_m"][a], s["north_m"][a]) for _, s, a, b in zones]), zones):
        idx = list(range(s0, s1, 10)) + [s1]
        out["zones"].append(dict(name=name, length_m=round(seg["s_m"][s1] - seg["s_m"][s0]),
                                 line_cm=[xyz(seg, j) for j in idx]))

    dst = (a.out or a.root / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks") / f"challenges_{AREA_NAME}_test.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {dst}: {len(out['traps'])} trap(s), {len(out['zones'])} zone(s)")
    for t in out["traps"]:
        print(f"  trap {t['name']}: {t['straight_m']} m straight, limit {t['limit_kmh']} km/h")
    for z in out["zones"]:
        print(f"  zone {z['name']}: {z['length_m']} m")


if __name__ == "__main__":
    main()
