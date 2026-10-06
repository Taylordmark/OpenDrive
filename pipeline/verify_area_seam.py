"""Seam between two world members that meet at an X cut (2026-10-05, step 3
baseline): reads both areas' SHIPPED tiles and measures, along X = cut,

  - terrain: west's last terrain-tile edge vs east's first, sampled every
    ~60 m down every tile row both ship;
  - roads: where each road crosses the cut, the planned road z in each
    bake's road_stations.json (both bake a halo past the cut, so both have
    every crossing; matched within MATCH_CM), and which sides ship a road
    surface there - neither is a gap, both a double-drawn overlap (with
    its z step).

It prints the step sizes; step 3's two-pass bake drives them to ~0. A
measurement, not a gate (exit 0). The cut is west's own_rect_cm xmax, which
must be east's xmin (test_areas.AREAS).

Usage:  python verify_area_seam.py [west east]     (default gv_west gv_east)
"""
import json
import re
import sys
from pathlib import Path

import numpy as np

from bin_tile_io import parse_road_tile_bin, parse_terrain_tile_bin
from road_mesh_common import sample_mesh_heights
from test_areas import AREAS, ROAD_TILE_SIZE_CM as R, TERRAIN_TILE_SIZE_CM as T

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
N_EDGE = 33   # samples per 2 km tile edge
MATCH_CM = 500.0   # same crossing in both bakes' stations


def mesh(parsed, chunks=False):
    """(positions, triangles) of a parsed terrain tile, or every chunk of a
    road tile merged, or None."""
    if parsed is None:
        return None
    parts = parsed["chunks"] if chunks else [parsed]
    pos, tri, base = [], [], 0
    for c in parts:
        v = np.array(c["verts"], dtype=np.float64).reshape(-1, 3)
        pos.append(v)
        tri.append(np.array(c["tris"], dtype=np.int64).reshape(-1, 3) + base)
        base += len(v)
    return (np.concatenate(pos), np.concatenate(tri)) if base else None


def road_z(area, tx, x_cm, y_cm, ring=2):
    """Shipped road surface z (cm) at each (x_cm, y_cm), searching road tile
    column tx and rows around each point's own; NaN = no surface."""
    out = np.full(len(y_cm), np.nan)
    for i, y in enumerate(y_cm):
        ty = int(y // R)
        for dy in range(-ring, ring + 1):
            for dx in range(-ring, ring + 1):
                key = (area, tx + dx, ty + dy)
                if key not in road_z.cache:
                    road_z.cache[key] = mesh(parse_road_tile_bin(
                        MASKS / f"road_{area}_test_tile_{tx + dx}_{ty + dy}.bin"), chunks=True)
                m = road_z.cache[key]
                if m is not None and np.isnan(out[i]):
                    out[i] = sample_mesh_heights(m[0], m[1], np.array([x_cm]), np.array([y]))[0]
    return out


road_z.cache = {}


def crossings(area, cut_cm):
    """(y_cm, elev_m, road name) where each road segment's centerline
    crosses X = cut."""
    data = json.loads((ROOT / "data" / "road_tiles" / f"{area}_test" / "road_stations.json").read_text())
    out = []
    for si, seg in enumerate(data["segments"]):
        x = np.array(seg["east_m"]) * 100.0
        y = -np.array(seg["north_m"]) * 100.0
        z = np.array(seg["elev_m"])
        for i in np.where((x[:-1] - cut_cm) * (x[1:] - cut_cm) < 0)[0]:
            t = (cut_cm - x[i]) / (x[i + 1] - x[i])
            out.append((y[i] + t * (y[i + 1] - y[i]), z[i] + t * (z[i + 1] - z[i]),
                        seg["road_name"][i] or seg["name"]))
    return sorted(out)


def stats(label, d):
    d = np.abs(np.asarray(d))
    if not len(d):
        print(f"{label}: nothing sampled")
        return
    print(f"{label}: n={len(d)}  median {np.median(d):.3f} m  p95 {np.percentile(d, 95):.3f} m  "
          f"max {d.max():.3f} m")


def main(west="gv_west", east="gv_east"):
    cut = AREAS[west]["own_rect_cm"][2]
    assert cut is not None and cut == AREAS[east]["own_rect_cm"][0], "areas don't share an X cut"
    print(f"{west} | {east} at X = {cut:.0f} cm")

    # terrain: west tile column kx-1's east edge vs east column kx's west edge
    kx = int(round(cut / T))
    rows = {}
    for area, col in ((west, kx - 1), (east, kx)):
        pat = re.compile(rf"terrain_{re.escape(area)}_test_tile_{col}_(-?\d+)\.bin")
        rows[area] = {int(m[1]): p for p in MASKS.glob(f"terrain_{area}_test_tile_{col}_*.bin")
                      if (m := pat.fullmatch(p.name))}
    shared = sorted(set(rows[west]) & set(rows[east]))
    print(f"terrain rows: {len(rows[west])} west, {len(rows[east])} east, {len(shared)} shared")
    steps, worst = [], (0.0, None)
    for ty in shared:
        ys = np.linspace(ty * T, (ty + 1) * T, N_EDGE)
        xs = np.full(N_EDGE, float(cut))
        zw, ze = (sample_mesh_heights(*mesh(parse_terrain_tile_bin(rows[a][ty])), xs, ys)
                  for a in (west, east))
        d = (zw - ze)[~np.isnan(zw - ze)] / 100.0
        steps += list(d)
        if len(d) and np.abs(d).max() > worst[0]:
            worst = (float(np.abs(d).max()), ty)
    stats("terrain step (m)", steps)
    if worst[1] is not None:
        print(f"  worst terrain row ty={worst[1]} ({worst[0]:.3f} m)")

    # roads: planned z at each crossing in both bakes, then what ships there
    cw, ce = crossings(west, cut), crossings(east, cut)
    ye = np.array([c[0] for c in ce])
    pairs = []
    for y, z, name in cw:
        j = int(np.argmin(np.abs(ye - y))) if len(ye) else -1
        if j >= 0 and abs(ye[j] - y) < MATCH_CM:
            pairs.append((y, z, ce[j][1], name))
    print(f"road crossings of the cut: {len(cw)} in {west}'s stations, {len(ce)} in {east}'s, "
          f"{len(pairs)} matched within {MATCH_CM / 100:.0f} m")
    stats("road z step, planned (station elev, m)", [zw - ze for _, zw, ze, _ in pairs])
    for y, zw, ze, name in sorted(pairs, key=lambda p: -abs(p[1] - p[2]))[:5]:
        print(f"  {name} at Y={y:.0f} cm: {west} {zw:.2f} m, {east} {ze:.2f} m")
    ys = np.array([p[0] for p in pairs])
    sw = road_z(west, int((cut - 1) // R), cut, ys)
    se = road_z(east, int(cut // R), cut, ys)
    both = ~np.isnan(sw) & ~np.isnan(se)
    print(f"shipped road surface at the cut: both {int(both.sum())}, "
          f"{west} only {int((~np.isnan(sw) & np.isnan(se)).sum())}, "
          f"{east} only {int((np.isnan(sw) & ~np.isnan(se)).sum())}, "
          f"neither (gap) {int((np.isnan(sw) & np.isnan(se)).sum())}")
    stats("road z step, shipped where both (m)", (sw - se)[both] / 100.0)


if __name__ == "__main__":
    main(*sys.argv[1:3])
