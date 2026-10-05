"""Offline, engine-free junction audit - "do two roads' pavements agree
where they meet?" - without launching the game.

Junctions here = clusters of stations from DIFFERENT segments within
JUNCTION_RADIUS_M of each other in XY. For every such site this measures,
from data already on disk:

  1. PROFILE STEP: the worst cross-segment |elev_m difference| among the
     site's station pairs, from road_stations.json - the disagreement
     reconcile_junction_elevations() exists to remove. This is the "one
     road's pavement bursting through the other" bug class, measured at
     its source.
  2. BAKED STEP: the spread between the drivable surfaces of the ACTUAL
     shipped road chunks (ODR2 road_*.bin, parsed with the same
     bin_tile_io the game mirrors) sampled at the site center - each
     chunk's topmost surface at that XY is its pavement, so
     (max - min) across chunks covering the point is the literal vertical
     step a wheel would feel crossing the junction. Catches bake-level
     divergence the station profiles alone can't.

Sites where any member station is bridge/tunnel-tagged or an auto-detected
elevation conflict (road_mesh_common.classify_wall_stations - overpasses,
grade-separated crossings, wall-covered switchbacks) are reported in a
separate "grade-separated (step expected)" bucket, not mixed into the
flat-junction ranking.

Renders a PNG: map of flat-junction sites colored by profile step, plus a
top-down closeup of the worst 3 sites drawing the real baked road
triangles colored by height - a virtual screenshot from the .bin files.

Usage:
  python diagnose_junctions.py                       whole map
  python diagnose_junctions.py <cx> <cy> [half_m]    bbox only (east/north m)
  ... --pads=<pads.json>   overlay proposed pad footprints (the JSON that
                           experimental_junction_pads.py --dump-pads=... writes)
                           as outlines on the map and closeups - eyeball the
                           footprint-ellipse fit/merge BEFORE baking anything

Writes  data/road_tiles/<AREA>_test/junction_audit.png
"""
import sys
import json
import math
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import build_terrain_mesh_tiles as btmt
from bin_tile_io import parse_road_tile_bin
from road_mesh_common import sample_mesh_height, ROAD_LIFT
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, ROAD_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
OUT_PNG = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "junction_audit.png"

JUNCTION_RADIUS_M = 5.0    # cross-segment stations this close are "the same
                            # physical junction spot" - a bit tighter than
                            # road half-width + margin so parallel dual
                            # carriageways don't all pair up
PROFILE_STEP_WARN_M = 0.30  # ranking threshold for the printed report only -
                            # this script is a diagnostic, not a hard gate
CLOSEUP_HALF_M = 45.0


def find_junction_sites(east, north, seg_of, radius_m):
    """Cross-segment station pairs within radius_m, clustered (connected
    components over the pair graph) into junction sites. Returns a list of
    sorted station-index lists."""
    cell = radius_m
    buckets = defaultdict(list)
    for i in range(len(east)):
        buckets[(int(east[i] // cell), int(north[i] // cell))].append(i)

    parent = {}

    def find(a):
        while parent.get(a, a) != a:
            parent[a] = parent.get(parent[a], parent[a])
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    r2 = radius_m * radius_m
    for (cx, cy), idxs in buckets.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(buckets.get((cx + dx, cy + dy), ()))
        for i in idxs:
            for j in neigh:
                if j <= i or seg_of[i] == seg_of[j]:
                    continue
                de, dn = east[i] - east[j], north[i] - north[j]
                if de * de + dn * dn <= r2:
                    parent.setdefault(i, i)
                    parent.setdefault(j, j)
                    union(i, j)

    comps = defaultdict(list)
    for i in parent:
        comps[find(i)].append(i)
    return [sorted(v) for v in comps.values()]


class RoadTileCache:
    """Lazy ODR2 tile loader with the same own-tile-then-8-neighbors probing
    verify_road_fit.py uses (chunks are bucketed by centroid and can span
    tile borders)."""

    def __init__(self):
        self._tiles = {}

    def _get(self, tx, ty):
        if (tx, ty) not in self._tiles:
            parsed = parse_road_tile_bin(
                MASKS_DIR / f"road_{AREA_NAME}_test_tile_{tx}_{ty}.bin")
            chunks = []
            if parsed is not None:
                for ch in parsed["chunks"]:
                    pos = np.array(ch["verts"], dtype=np.float64).reshape(-1, 3)
                    tris = np.array(ch["tris"], dtype=np.int64).reshape(-1, 3)
                    chunks.append((pos, tris))
            self._tiles[(tx, ty)] = chunks
        return self._tiles[(tx, ty)]

    def pavement_heights(self, x_cm, y_cm):
        """Every distinct road chunk's topmost surface height (m, +origin
        undone) at this XY, across the point's own tile and its 8
        neighbors. One entry per chunk that covers the point. Road tiles are
        500m (ROAD_TILE_SIZE_CM), NOT the 2km terrain tile size; chunks span
        <=750m so their centroid tile is always within +/-1 tile of any
        point they cover."""
        tile_cm = ROAD_TILE_SIZE_CM
        tx, ty = int(x_cm // tile_cm), int(y_cm // tile_cm)
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for pos, tris in self._get(tx + dx, ty + dy):
                    h = sample_mesh_height(pos, tris, x_cm, y_cm)
                    if h is not None:
                        out.append(h / 100.0 + ORIGIN_HEIGHT_M)
        return out

    def triangles_near(self, x_cm, y_cm, half_cm):
        """(pos, tris) per chunk, filtered to triangles with any vertex in
        the closeup box - for the virtual-screenshot rendering."""
        tile_cm = ROAD_TILE_SIZE_CM
        tx, ty = int(x_cm // tile_cm), int(y_cm // tile_cm)
        found = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for pos, tris in self._get(tx + dx, ty + dy):
                    near_v = ((np.abs(pos[:, 0] - x_cm) <= half_cm) &
                              (np.abs(pos[:, 1] - y_cm) <= half_cm))
                    keep = near_v[tris].any(axis=1)
                    if keep.any():
                        found.append((pos, tris[keep]))
        return found


def _pad_outline_xy(pad, sides=64):
    """Boundary polyline of one proposed pad footprint from the
    --dump-pads JSON (drawn-size: fitted ellipse grown by the same
    PAD_OVERLAP_MARGIN_M the bake itself draws with; plain circle of
    radius_m + margin when no ellipse was fitted)."""
    from experimental_junction_pads import PAD_OVERLAP_MARGIN_M, _ellipse_polygon
    if pad.get("ellipse"):
        pts = _ellipse_polygon(tuple(pad["ellipse"]), PAD_OVERLAP_MARGIN_M, sides=sides)
    else:
        cx, cy = pad["xy"]
        r = pad["radius_m"] + PAD_OVERLAP_MARGIN_M
        pts = [(cx + r * math.cos(2 * math.pi * k / sides),
                cy + r * math.sin(2 * math.pi * k / sides)) for k in range(sides)]
    pts.append(pts[0])
    return [p[0] for p in pts], [p[1] for p in pts]


def main():
    pads = None
    argv = []
    for a in sys.argv:
        if a.startswith("--pads="):
            pads = json.loads(Path(a[len("--pads="):]).read_text())
            print(f"--pads: {len(pads)} proposed pad footprint(s) loaded "
                  f"({sum(1 for p in pads if p.get('merged'))} merged)")
        else:
            argv.append(a)
    sys.argv = argv

    cx = cy = half = None
    if len(sys.argv) >= 3:
        cx, cy = float(sys.argv[1]), float(sys.argv[2])
        half = float(sys.argv[3]) if len(sys.argv) > 3 else 1500.0

    data = json.loads(btmt.STATIONS_PATH.read_text())
    east, north, elev, exclude, _clamp_r, _ = btmt.load_stations()
    seg_of = np.concatenate([np.full(len(s["east_m"]), si, dtype=np.int32)
                             for si, s in enumerate(data["segments"])])
    loc_of = np.concatenate([np.arange(len(s["east_m"])) for s in data["segments"]])
    names = [s["road_name"][0] if s["road_name"] else "?" for s in data["segments"]]

    if cx is not None:
        m = ((east > cx - half) & (east < cx + half) &
             (north > cy - half) & (north < cy + half))
        idx_map = np.where(m)[0]
        sites = [[int(idx_map[k]) for k in site] for site in find_junction_sites(
            east[idx_map], north[idx_map], seg_of[idx_map], JUNCTION_RADIUS_M)]
        print(f"bbox +/-{half:.0f}m around ({cx:.0f},{cy:.0f}): "
              f"{len(idx_map)} station(s) in scope")
    else:
        sites = find_junction_sites(east, north, seg_of, JUNCTION_RADIUS_M)

    flat_sites, sep_sites = [], []
    for site in sites:
        (sep_sites if any(exclude[i] for i in site) else flat_sites).append(site)
    print(f"{len(sites)} junction site(s) found "
          f"({len(flat_sites)} at-grade, {len(sep_sites)} grade-separated/"
          f"wall-covered - step expected, not ranked)")

    cache = RoadTileCache()
    rows = []
    for site in flat_sites:
        segs = sorted({int(seg_of[i]) for i in site})
        prof_step = 0.0
        for a in range(len(site)):
            for b in range(a + 1, len(site)):
                i, j = site[a], site[b]
                if seg_of[i] == seg_of[j]:
                    continue
                d = math.hypot(east[i] - east[j], north[i] - north[j])
                if d <= JUNCTION_RADIUS_M:
                    prof_step = max(prof_step, abs(float(elev[i] - elev[j])))
        ce = float(np.mean(east[site])); cn = float(np.mean(north[site]))
        heights = cache.pavement_heights(ce * 100.0, -cn * 100.0)
        baked_step = (max(heights) - min(heights)) if len(heights) >= 2 else 0.0
        rows.append({"site": site, "segs": segs, "e": ce, "n": cn,
                     "prof": prof_step, "baked": baked_step,
                     "n_pave": len(heights)})

    rows.sort(key=lambda r: -max(r["prof"], r["baked"]))
    n_warn = sum(1 for r in rows if max(r["prof"], r["baked"]) > PROFILE_STEP_WARN_M)
    if rows:
        print(f"at-grade junctions: worst profile step "
              f"{max(r['prof'] for r in rows):.2f}m, worst baked pavement step "
              f"{max(r['baked'] for r in rows):.2f}m, "
              f"{n_warn} site(s) over {PROFILE_STEP_WARN_M}m")
    print("worst 12 at-grade junction sites:")
    for r in rows[:12]:
        segn = ", ".join(f"seg{s} '{names[s]}'" for s in r["segs"][:3])
        i0 = r["site"][0]
        print(f"  prof={r['prof']:5.2f}m baked={r['baked']:5.2f}m "
              f"({r['n_pave']} pavement(s)) at ({r['e']:.0f},{r['n']:.0f}) "
              f"[{segn}] e.g. st={i0} loc={loc_of[i0]}")

    # ---- figure: site map + baked-triangle closeups of the worst 3 ----
    fig = plt.figure(figsize=(14, 10))
    ax = fig.add_subplot(2, 1, 1)
    if rows:
        xs = [r["e"] for r in rows]; ys = [r["n"] for r in rows]
        cs = [max(r["prof"], r["baked"]) for r in rows]
        sc = ax.scatter(xs, ys, c=cs, cmap="YlOrRd", vmin=0.0,
                        vmax=max(PROFILE_STEP_WARN_M * 2, max(cs)), s=14)
        fig.colorbar(sc, ax=ax, shrink=0.8, label="worst step (m)")
    for site in sep_sites:
        ax.plot(float(np.mean(east[site])), float(np.mean(north[site])), "x",
                color="0.7", ms=4)
    if pads is not None:
        # context: thin road centerlines when zoomed to a bbox (an ellipse
        # outline means nothing without the roads it's supposed to cap)
        if cx is not None:
            for s in data["segments"]:
                se, sn = np.array(s["east_m"]), np.array(s["north_m"])
                m2 = ((se > cx - half) & (se < cx + half) &
                      (sn > cy - half) & (sn < cy + half))
                if m2.any():
                    ax.plot(se[m2], sn[m2], "-", color="0.75", lw=0.6, zorder=0)
        for pad in pads:
            px, py = pad["xy"]
            if cx is not None and not (abs(px - cx) <= half and abs(py - cy) <= half):
                continue
            ox, oy = _pad_outline_xy(pad)
            ax.plot(ox, oy, "-", lw=1.0, zorder=3,
                    color=("tab:blue" if pad.get("merged") else "tab:green"))
    ax.set_aspect("equal")
    title = (f"at-grade junction steps ({len(flat_sites)} sites; gray x = "
             f"grade-separated, step expected)")
    if pads is not None:
        title += "\nproposed pad footprints: green = single, blue = merged"
    ax.set_title(title)

    for k, r in enumerate(rows[:3]):
        axc = fig.add_subplot(2, 3, 4 + k)
        chunks = cache.triangles_near(r["e"] * 100.0, -r["n"] * 100.0,
                                      CLOSEUP_HALF_M * 100.0)
        zmin = zmax = None
        for pos, tris in chunks:
            z = pos[:, 2] / 100.0 + ORIGIN_HEIGHT_M
            zmin = z.min() if zmin is None else min(zmin, z.min())
            zmax = z.max() if zmax is None else max(zmax, z.max())
        for pos, tris in chunks:
            axc.tripcolor(pos[:, 0] / 100.0, -pos[:, 1] / 100.0, tris,
                          pos[:, 2] / 100.0 + ORIGIN_HEIGHT_M,
                          vmin=zmin, vmax=zmax, cmap="viridis",
                          edgecolors="k", linewidth=0.1)
        if pads is not None:
            for pad in pads:
                px, py = pad["xy"]
                if (abs(px - r["e"]) <= CLOSEUP_HALF_M + pad["radius_m"]
                        and abs(py - r["n"]) <= CLOSEUP_HALF_M + pad["radius_m"]):
                    ox, oy = _pad_outline_xy(pad)
                    axc.plot(ox, oy, "-", lw=1.2,
                             color=("tab:blue" if pad.get("merged") else "lime"))
        axc.plot(r["e"], r["n"], "r+", ms=12, mew=2)
        axc.set_xlim(r["e"] - CLOSEUP_HALF_M, r["e"] + CLOSEUP_HALF_M)
        axc.set_ylim(r["n"] - CLOSEUP_HALF_M, r["n"] + CLOSEUP_HALF_M)
        axc.set_aspect("equal")
        axc.set_title(f"prof={r['prof']:.2f}m baked={r['baked']:.2f}m "
                      f"({r['e']:.0f},{r['n']:.0f})", fontsize=9)
    fig.tight_layout()
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT_PNG, dpi=110)
    print(f"wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
