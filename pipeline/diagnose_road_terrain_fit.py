"""Fast, game-free observation of road-vs-terrain fit in a small area.

Samples the ACTUAL baked terrain_*.bin meshes (same parse + barycentric
sampling verify_terrain_fit.py's shipped check uses) at every road station in
a bbox, ranks the worst deviations from the expected carve offset
(road elev - TERRAIN_CARVE_DROP_M), flags any baked water ring near each
offender, and renders a PNG:
  - plan view of stations colored by deviation, water rings drawn in cyan
  - road-vs-terrain elevation profiles for the 3 worst segments

Built 2026-09-04 while chasing the Silverthorne 108.91m verify failure
(river polygons baked as flat lakes + water shore blend overriding the road
carve - see build_water_tiles.FLOWING_WATER_TAGS and
build_terrain_mesh_tiles.apply_water_depression's road-carve protection).
The whole iteration loop this enables is:
    python build_terrain_mesh_tiles.py --bbox-cm=x0,y0,x1,y1   (~1 min)
    python diagnose_road_terrain_fit.py <cx> <cy> [half_m]     (~30 s)
instead of a ~70 min full-area rebake + launching the game to look.

Usage:  python diagnose_road_terrain_fit.py <center_east_m> <center_north_m> [half_m=1500]
Writes  data/road_tiles/<AREA>_test/road_terrain_fit.png
"""
import sys
import json
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import build_terrain_mesh_tiles as btmt
from bin_tile_io import parse_terrain_tile_bin
from road_mesh_common import sample_mesh_height
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
OUT_PNG = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_terrain_fit.png"


def main():
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    cx, cy = float(sys.argv[1]), float(sys.argv[2])
    half = float(sys.argv[3]) if len(sys.argv) > 3 else 1500.0

    data = json.loads(btmt.STATIONS_PATH.read_text())
    east, north, elev, exclude, _clamp_r, _ = btmt.load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0
    seg_of = np.concatenate([np.full(len(s["east_m"]), si, dtype=np.int32)
                             for si, s in enumerate(data["segments"])])
    loc_of = np.concatenate([np.arange(len(s["east_m"])) for s in data["segments"]])
    names = [s["road_name"][0] if s["road_name"] else "?" for s in data["segments"]]

    in_box = ((east > cx - half) & (east < cx + half) &
              (north > cy - half) & (north < cy + half))
    box_idx = np.where(in_box)[0]
    print(f"{len(box_idx)} station(s) in bbox +/-{half:.0f}m around ({cx:.0f},{cy:.0f}), "
          f"{int(np.sum(exclude[box_idx]))} excluded (bridge/conflict)")

    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    by_tile = defaultdict(list)
    for i in box_idx:
        by_tile[(int(st_x_cm[i] // tile_cm), int(st_y_cm[i] // tile_cm))].append(i)

    terr_h = np.full(len(east), np.nan)
    for (tx, ty), idxs in by_tile.items():
        parsed = parse_terrain_tile_bin(
            MASKS_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin")
        if parsed is None:
            print(f"  MISSING terrain tile ({tx},{ty})")
            continue
        pos = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
        tris = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
        for i in idxs:
            h = sample_mesh_height(pos, tris, float(st_x_cm[i]), float(st_y_cm[i]))
            if h is not None:
                terr_h[i] = h / 100.0 + ORIGIN_HEIGHT_M

    dev = terr_h - (elev - btmt.TERRAIN_CARVE_DROP_M)   # 0 = perfect carve fit
    chk = box_idx[~exclude[box_idx] & ~np.isnan(terr_h[box_idx])]
    if len(chk) == 0:
        raise SystemExit("no checkable (non-excluded, sampled) stations in this bbox")
    order = chk[np.argsort(-np.abs(dev[chk]))]
    print(f"checked {len(chk)} non-excluded station(s): mean |dev|="
          f"{np.nanmean(np.abs(dev[chk])):.2f}m max |dev|={np.nanmax(np.abs(dev[chk])):.2f}m")

    lakes = btmt._get_lake_beds()
    print("worst 10:")
    for i in order[:10]:
        hit = ""
        px = np.array([st_x_cm[i]]); py = np.array([st_y_cm[i]])
        for li, lake in enumerate(lakes):
            bx0, bx1, by0, by1 = lake["bbox"]
            pad = btmt.WATER_SHORE_BLEND_M * 100.0
            if bx0 - pad <= px[0] <= bx1 + pad and by0 - pad <= py[0] <= by1 + pad:
                inside = btmt._point_in_polygon(px, py, lake["ring_x"], lake["ring_y"])[0]
                d = btmt._dist_to_ring(px, py, lake["ring_x"], lake["ring_y"])[0] / 100.0
                if inside or d <= btmt.WATER_SHORE_BLEND_M:
                    lz = lake["z_cm"] / 100.0 + ORIGIN_HEIGHT_M
                    hit += f" [water ring: inside={inside} d={d:.0f}m z={lz:.1f}m]"
        print(f"  dev={dev[i]:+7.2f}m st={i} seg{seg_of[i]} '{names[seg_of[i]]}' "
              f"loc={loc_of[i]} at ({east[i]:.0f},{north[i]:.0f}) elev={elev[i]:.1f}{hit}")

    worst_segs = []
    for i in order:
        if seg_of[i] not in worst_segs:
            worst_segs.append(seg_of[i])
        if len(worst_segs) == 3:
            break

    fig = plt.figure(figsize=(14, 10))
    ax = fig.add_subplot(2, 1, 1)
    sc_idx = box_idx[~np.isnan(terr_h[box_idx])]
    ex_b = exclude[sc_idx]
    lim = max(3.0, float(np.nanpercentile(np.abs(dev[sc_idx[~ex_b]]), 99.5)))
    sc = ax.scatter(east[sc_idx[~ex_b]], north[sc_idx[~ex_b]], c=dev[sc_idx[~ex_b]],
                    cmap="coolwarm", vmin=-lim, vmax=lim, s=6)
    ax.scatter(east[sc_idx[ex_b]], north[sc_idx[ex_b]], c="0.6", s=4, marker="x",
               label="excluded (bridge/conflict)")
    for lake in lakes:
        lx, ly = lake["ring_x"] / 100.0, -lake["ring_y"] / 100.0
        if (lx.max() < cx - half or lx.min() > cx + half
                or ly.max() < cy - half or ly.min() > cy + half):
            continue
        ax.plot(np.append(lx, lx[0]), np.append(ly, ly[0]), "c-", lw=0.7, alpha=0.7)
    ax.set_xlim(cx - half, cx + half); ax.set_ylim(cy - half, cy + half)
    ax.set_aspect("equal"); ax.legend(loc="upper right", fontsize=8)
    ax.set_title(f"terrain-vs-road deviation (m), +/-{half:.0f}m around ({cx:.0f},{cy:.0f}) "
                 f"- cyan = baked water rings")
    fig.colorbar(sc, ax=ax, shrink=0.8)

    for k, sg in enumerate(worst_segs):
        axp = fig.add_subplot(2, 3, 4 + k)
        m = (seg_of == sg) & in_box
        li = loc_of[m]
        o = np.argsort(li)
        axp.plot(li[o], elev[m][o] - btmt.TERRAIN_CARVE_DROP_M, "k-", lw=1.2,
                 label="road - 0.15m")
        axp.plot(li[o], terr_h[m][o], "r.-", lw=0.8, ms=3, label="terrain mesh")
        exm = exclude[m][o]
        axp.plot(li[o][exm], elev[m][o][exm] - btmt.TERRAIN_CARVE_DROP_M, "b.", ms=4,
                 label="excluded stn")
        axp.set_xlabel("local station idx", fontsize=8)
        axp.set_title(f"seg{sg} '{names[sg]}'", fontsize=9)
        axp.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=110)
    print(f"wrote {OUT_PNG}")


if __name__ == "__main__":
    main()
