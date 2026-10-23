"""Tree density vs distance from the road, as the runtime would plant it
(backend-only, no app launch - memory/feedback_no_run_app_for_testing.md).

Replays AODForestManager's two tree sources from the baked sidecars:
  near  BuildTile(): 96 m tiles, TreesPerSqM * TreeDensityScale candidates,
        accept < max(tile forest weight, AmbientVegFloor), road exclusion,
        ForestBandCm upper bound. Forest weight / recipe = inverse-square
        blend of biome points within 300 m (BlendAt), base recipe table only
        (no V2/V5 variety tweaks - those move density by <~35%).
  far   ODFarForest.cpp: the farforest_<area>_test.bin cards; band cards are
        hidden while the near scatter covers them, so they're dropped here.
--mode before = the shipped C++ constants; --mode after = the fix.

Usage:  python analyze_forest_distribution.py <area> [--mode before|after] [--win-km 6]
        (OD_DATA_ROOT = checkout holding data/ and Content/; default: this repo)
Writes  docs/forest_distribution/<area>_<mode>_{map,hist}.png + .json stats
"""

import argparse
import json
import os
import struct
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

REPO = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("OD_DATA_ROOT", REPO))
OUT = REPO / "docs" / "forest_distribution"

TILE_CM = 9600.0
FAR_TILE_M, FAR_CELL_M, FAR_CELLS = 2000.0, 20.0, 100
BLEND_M = 300.0
ROAD_EXCL_M = 11.5            # centerline exclusion (log: "exclusion 11.5 m")
EXTRA_SINK = float(os.environ.get("OD_EXTRA_SINK", 0.25))   # ODFarForest.cpp FarExtraSink
# BiomeRecipeTable: (TreeDensityScale, AmbientVegFloor) per EODBiome
RECIPE = [(1.0, 0), (1.0, 0), (0.5, .15), (.25, .16), (.15, 0), (1.0, 0), (.05, 0), (.02, 0),
          (.10, 0), (.20, 0), (1.2, 0), (.05, 0), (.10, .20), (.5, 0), (.15, .08), (.03, .05)]

MODES = {
    # trees/m^2, band (m), WorldCover gate (replaces the forest-weight roll), cards per far cell, road fade (m)
    "before": dict(trees_per_sqm=0.05, band_m=120.0, wc_gate=False, cards=1, fade=None),
    "after": dict(trees_per_sqm=0.0075, band_m=130.0, wc_gate=True, cards=3, fade=(11.5, 40.0)),
}


def load_far(area):
    """{(tx,ty): (cell idx, x_m, y_m UE, flags)} straight from the ODF1 bin."""
    b = (DATA / "OpenDriveUE/Content/OpenDrive/Masks" / f"farforest_{area}_test.bin").read_bytes()
    assert b[:4] == b"ODF1"
    n_tiles, = struct.unpack_from("<I", b, 4)
    rec = np.dtype([("cell", "<u2"), ("jx", "i1"), ("jy", "i1"), ("z", "<i2"), ("f", "u1")])
    off, xs, ys, fl, cells = 8, [], [], [], {}
    for _ in range(n_tiles):
        tx, ty, n = struct.unpack_from("<iiI", b, off)
        off += 12
        r = np.frombuffer(b, rec, n, off)
        off += n * 7
        ix, iy = r["cell"] % FAR_CELLS, r["cell"] // FAR_CELLS
        xs.append(tx * FAR_TILE_M + (ix + 0.5) * FAR_CELL_M + r["jx"] / 2.0)
        ys.append(ty * FAR_TILE_M + (iy + 0.5) * FAR_CELL_M + r["jy"] / 2.0)
        fl.append(r["f"])
        cells[(tx, ty)] = r["cell"][(r["f"] & 0x40) == 0]
    return np.concatenate(xs), np.concatenate(ys), np.concatenate(fl), cells


def tree_cell(cells, x, y):
    """WorldCover tree at UE (x, y) m, from the far-forest records (tree cells
    within 18 m of a road were dropped by the bake - near trees can't plant
    there anyway)."""
    tx, ty = np.floor(x / FAR_TILE_M).astype(int), np.floor(y / FAR_TILE_M).astype(int)
    c = (np.floor((y - ty * FAR_TILE_M) / FAR_CELL_M).astype(int) * FAR_CELLS
         + np.floor((x - tx * FAR_TILE_M) / FAR_CELL_M).astype(int))
    out = np.zeros(len(x), bool)
    for key in set(zip(tx.tolist(), ty.tolist())):
        m = (tx == key[0]) & (ty == key[1])
        have = cells.get(key)
        if have is not None:
            out[m] = np.isin(c[m], have)
    return out


def tree_cell_3x3(cells, x, y):
    """Runtime gate: the candidate's 20 m cell or any neighbour is a tree cell
    (the neighbour ring bridges the bake's dropped road-strip cells)."""
    out = np.zeros(len(x), bool)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            out |= tree_cell(cells, x + dx * FAR_CELL_M, y + dy * FAR_CELL_M)
    return out


def simulate(area, mode, win_km, seed=1):
    cfg = MODES[mode]
    rt = DATA / "data/road_tiles" / f"{area}_test"
    st = json.loads((rt / "road_stations.json").read_text())
    road = np.concatenate([np.c_[s["east_m"], -np.asarray(s["north_m"])] for s in st["segments"]])  # UE x, y m
    bio = json.loads((rt / "road_biomes.json").read_text())
    bxy = np.c_[bio["x_cm"], bio["y_cm"]] / 100.0
    bfw, bbi = np.asarray(bio["forest_weight"]), np.asarray(bio["biome"])
    fx, fy, ff, cells = load_far(area)

    # window: centred on the densest road stretch (most stations per km^2)
    h, ex, ey = np.histogram2d(road[:, 0], road[:, 1], bins=[np.arange(road[:, 0].min(), road[:, 0].max() + 2000, 2000),
                                                           np.arange(road[:, 1].min(), road[:, 1].max() + 2000, 2000)])
    # ponytail: busiest 2 km cell, prefer forest over town - weight by far-card count there
    fh, _, _ = np.histogram2d(fx, fy, bins=[ex, ey])
    i, j = np.unravel_index(np.argmax(np.minimum(h, 400) * fh), h.shape)
    cx, cy = (ex[i] + ex[i + 1]) / 2, (ey[j] + ey[j + 1]) / 2
    half = win_km * 500.0
    x0, y0 = cx - half, cy - half
    rtree = cKDTree(road)
    btree = cKDTree(bxy)
    rng = np.random.default_rng(seed)

    # near scatter, tile by tile
    tc = TILE_CM / 100.0
    kx = np.arange(np.floor(x0 / tc), np.ceil((x0 + 2 * half) / tc))
    ky = np.arange(np.floor(y0 / tc), np.ceil((y0 + 2 * half) / tc))
    gx, gy = (a.ravel() for a in np.meshgrid(kx, ky))
    ctr = np.c_[(gx + 0.5) * tc, (gy + 0.5) * tc]
    near_tile = rtree.query(ctr, distance_upper_bound=cfg["band_m"] + tc)[0] < np.inf
    near = []
    for (tx, ty), c in zip(np.c_[gx, gy][near_tile], ctr[near_tile]):
        idx = btree.query_ball_point(c, BLEND_M)
        if idx:
            w = 1.0 / np.maximum(1.0, ((bxy[idx] - c) * 100.0) ** 2).sum(1)   # cm^2, as BlendAt
            fw = (bfw[idx] * w).sum() / w.sum()
            tds = (np.array([RECIPE[b][0] for b in bbi[idx]]) * w).sum() / w.sum()
            floor = (np.array([RECIPE[b][1] for b in bbi[idx]]) * w).sum() / w.sum()
        else:
            k = btree.query(c)[1]
            fw, tds, floor = bfw[k], RECIPE[bbi[k]][0], RECIPE[bbi[k]][1]
        # gated: WorldCover says where, cards set the density - biome scale only without it
        n = int(round(tc * tc * cfg["trees_per_sqm"] * (1.0 if cfg["wc_gate"] else tds)))
        p = np.c_[tx * tc + rng.random(n) * tc, ty * tc + rng.random(n) * tc]
        roll = rng.random(n)
        if cfg["wc_gate"]:   # own tree cell, or a neighbour inside the road fade (bake drops cells <= 18 m off a station)
            ring = (rtree.query(p)[0] < cfg["fade"][1]) & tree_cell_3x3(cells, p[:, 0], p[:, 1])
            p = p[tree_cell(cells, p[:, 0], p[:, 1]) | ring | (roll < floor)]
        else:
            p = p[roll < max(fw, floor)]
        near.append(p)
    near = np.concatenate(near) if near else np.zeros((0, 2))
    d = rtree.query(near)[0]
    keep = (d > ROAD_EXCL_M) & (d <= cfg["band_m"])
    if cfg["fade"]:
        lo, hi = cfg["fade"]
        t = np.clip((d - lo) / (hi - lo), 0, 1)
        keep &= rng.random(len(d)) < t * t * (3 - 2 * t)
    near = near[keep]

    # far cards visible next to the player: off-band (band ones hidden), street trees out
    m = ((ff & 0x80) == 0) & ((ff & 0x40) == 0) & (fx >= x0) & (fx < x0 + 2 * half) & (fy >= y0) & (fy < y0 + 2 * half)
    far = np.c_[fx[m], fy[m]]
    if cfg["cards"] > 1:   # extra cards per tree cell, jittered inside it (ODFarForest.cpp)
        k = cfg["cards"]
        extra = np.repeat(far, k - 1, 0) + rng.uniform(-0.45, 0.45, (len(far) * (k - 1), 2)) * FAR_CELL_M
        far = np.r_[far, extra]
    return dict(near=near, far=far, road=road, rtree=rtree, box=(x0, y0, 2 * half), cells=cells)


def stats(sim):
    x0, y0, side = sim["box"]
    # area per distance bin by Monte Carlo over the window
    rng = np.random.default_rng(0)
    mc = np.c_[x0 + rng.random(400000) * side, y0 + rng.random(400000) * side]
    dmc = sim["rtree"].query(mc)[0]
    tree_frac_mc = tree_cell(sim["cells"], mc[:, 0], mc[:, 1])
    edges = np.arange(0, 1001, 10.0)
    area_ha = np.histogram(dmc, edges)[0] / len(mc) * side * side / 1e4
    forest_ha = np.histogram(dmc[tree_frac_mc], edges)[0] / len(mc) * side * side / 1e4
    out = {"edges_m": edges.tolist()}
    for k in ("near", "far"):
        dd = sim["rtree"].query(sim[k])[0]
        out[k] = np.histogram(dd, edges)[0].tolist()
    out["area_ha"] = area_ha.tolist()
    out["forest_ha"] = forest_ha.tolist()
    out["forest_share"] = float(tree_frac_mc.mean())
    tot = np.array(out["near"]) + np.array(out["far"])
    per_forest_ha = tot / np.maximum(forest_ha, 1e-6)
    band = lambda a, b: float(tot[(edges[:-1] >= a) & (edges[1:] <= b)].sum()
                              / max(forest_ha[(edges[:-1] >= a) & (edges[1:] <= b)].sum(), 1e-6))
    out["trees_per_forest_ha"] = {"12-40m": band(10, 40), "40-120m": band(40, 120), "130-300m": band(130, 300),
                                  "300-1000m": band(300, 1000)}
    out["band_over_beyond"] = out["trees_per_forest_ha"]["40-120m"] / max(out["trees_per_forest_ha"]["130-300m"], 1e-6)
    return out, per_forest_ha


def plot(area, mode, sim, st, per_forest_ha):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    x0, y0, side = sim["box"]
    fig, ax = plt.subplots(figsize=(10, 10))
    # WorldCover tree cells as background
    rng = np.random.default_rng(0)
    g = np.arange(0, side, FAR_CELL_M) + FAR_CELL_M / 2
    gx, gy = np.meshgrid(x0 + g, y0 + g)
    tm = tree_cell(sim["cells"], gx.ravel(), gy.ravel()).reshape(gx.shape)
    ax.imshow(tm, extent=(x0, x0 + side, y0 + side, y0), cmap="Greens", vmin=0, vmax=4, origin="upper", interpolation="nearest")
    r = sim["road"]
    rm = (r[:, 0] >= x0) & (r[:, 0] < x0 + side) & (r[:, 1] >= y0) & (r[:, 1] < y0 + side)
    ax.scatter(sim["far"][:, 0], sim["far"][:, 1], s=0.3, c="#2a6f97", lw=0, label=f"far cards ({len(sim['far'])})")
    ax.scatter(sim["near"][:, 0], sim["near"][:, 1], s=0.3, c="#c1121f", lw=0, label=f"near 3D trees ({len(sim['near'])})")
    ax.scatter(r[rm, 0], r[rm, 1], s=0.2, c="k", lw=0, label="road stations")
    ax.set_xlim(x0, x0 + side); ax.set_ylim(y0 + side, y0)
    ax.set_aspect("equal"); ax.set_xlabel("UE x (m)"); ax.set_ylabel("UE y (m)")
    ax.set_title(f"{area} [{mode}] - trees over WorldCover tree cells (light green)")
    ax.legend(loc="upper right", markerscale=20)
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{area}_{mode}_map.png", dpi=110, bbox_inches="tight")
    plt.close(fig)

    e = np.array(st["edges_m"])
    mid = (e[:-1] + e[1:]) / 2
    fig, axs = plt.subplots(2, 1, figsize=(10, 8), sharex=True)
    fha = np.maximum(np.array(st["forest_ha"]), 1e-6)
    axs[0].step(mid, np.array(st["near"]) / fha, where="mid", c="#c1121f", label="near 3D trees")
    axs[0].step(mid, np.array(st["far"]) / fha, where="mid", c="#2a6f97", label="far cards")
    axs[0].step(mid, per_forest_ha, where="mid", c="k", lw=1.5, label="total")
    axs[0].set_ylabel("trees per ha of WorldCover forest")
    axs[0].set_yscale("symlog", linthresh=10)
    axs[0].legend(); axs[0].set_title(f"{area} [{mode}] density vs distance from road "
                                      f"(band/beyond = {st['band_over_beyond']:.1f}x)")
    axs[1].bar(mid, st["near"], width=np.diff(e) * 0.9, color="#c1121f", label="near")
    axs[1].bar(mid, st["far"], width=np.diff(e) * 0.9, bottom=st["near"], color="#2a6f97", label="far")
    axs[1].set_ylabel("tree count per 10 m bin"); axs[1].set_xlabel("distance from road centerline (m)")
    axs[1].set_xscale("symlog", linthresh=100)
    axs[1].legend()
    fig.savefig(OUT / f"{area}_{mode}_hist.png", dpi=110, bbox_inches="tight")
    plt.close(fig)


def zcheck(area, n_tiles=12, k=3, seed=3):
    """ODFarForest.cpp's extra cards: Z = record Z + neighbour-cell slope x
    offset. Sample the shipped terrain mesh there; report |dZ| percentiles
    against the record cards (k=0, baked on the mesh) and a flat Z (no fit)."""
    from bin_tile_io import parse_terrain_tile_bin
    from road_mesh_common import sample_mesh_heights
    b = (DATA / "OpenDriveUE/Content/OpenDrive/Masks" / f"farforest_{area}_test.bin").read_bytes()
    n_all, = struct.unpack_from("<I", b, 4)
    rec = np.dtype([("cell", "<u2"), ("jx", "i1"), ("jy", "i1"), ("z", "<i2"), ("f", "u1")])
    off, tiles = 8, []
    for _ in range(n_all):
        tx, ty, n = struct.unpack_from("<iiI", b, off)
        tiles.append((tx, ty, np.frombuffer(b, rec, n, off + 12)))
        off += 12 + n * 7
    rng = np.random.default_rng(seed)
    tiles = [tiles[i] for i in rng.choice(len(tiles), min(n_tiles, len(tiles)), replace=False)]
    fit_err, flat_err, base_err = [], [], []
    for tx, ty, r in tiles:
        r = r[(r["f"] & 0x40) == 0]
        if len(r) < 10:
            continue
        cell = r["cell"].astype(int)
        ix, iy = cell % FAR_CELLS, cell // FAR_CELLS
        bx = tx * 2000.0 + (ix + 0.5) * 20.0 + r["jx"] / 2.0     # m
        by = ty * 2000.0 + (iy + 0.5) * 20.0 + r["jy"] / 2.0
        bz = r["z"] * 0.1
        grid = np.full((FAR_CELLS * FAR_CELLS, 3), np.nan)
        grid[cell] = np.c_[bx, by, bz]

        def slope(axis):
            step = 1 if axis == 0 else FAR_CELLS
            i = ix if axis == 0 else iy
            lo = np.where(i > 0, cell - step, cell)
            hi = np.where(i < FAR_CELLS - 1, cell + step, cell)
            a, c = grid[lo], grid[hi]
            own = np.c_[bx, by, bz]
            a = np.where(np.isnan(a[:, :1]), own, a)
            c = np.where(np.isnan(c[:, :1]), own, c)
            run = c[:, axis] - a[:, axis]
            return np.where(np.abs(run) > 1.0, (c[:, 2] - a[:, 2]) / np.where(np.abs(run) > 1.0, run, 1), 0.0)
        gx, gy = slope(0), slope(1)
        ex = np.repeat(np.c_[tx * 2000.0 + (ix + 0.5) * 20.0, ty * 2000.0 + (iy + 0.5) * 20.0], k - 1, 0)
        ex += rng.uniform(-0.45, 0.45, ex.shape) * 20.0
        rep = lambda v: np.repeat(v, k - 1)
        zfit = rep(bz) + rep(gx) * (ex[:, 0] - rep(bx)) + rep(gy) * (ex[:, 1] - rep(by))
        # extra cards sink by EXTRA_SINK x slope x offset (C++ FarExtraSink): convex ridges stop floating them
        zfit -= EXTRA_SINK * np.hypot(rep(gx), rep(gy)) * np.hypot(ex[:, 0] - rep(bx), ex[:, 1] - rep(by))
        p = parse_terrain_tile_bin(DATA / "OpenDriveUE/Content/OpenDrive/Masks" / f"terrain_{area}_test_tile_{tx}_{ty}.bin")
        pos = np.array(p["verts"], dtype=np.float64).reshape(-1, 3)
        tri = np.array(p["tris"], dtype=np.int64).reshape(-1, 3)
        zt = sample_mesh_heights(pos, tri, ex[:, 0] * 100.0, ex[:, 1] * 100.0) / 100.0
        zb = sample_mesh_heights(pos, tri, bx * 100.0, by * 100.0) / 100.0
        ok = ~np.isnan(zt)
        fit_err.append(zfit[ok] - zt[ok])
        flat_err.append(rep(bz)[ok] - zt[ok])
        base_err.append((bz - zb)[~np.isnan(zb)])
    pct = lambda e: {f"p{q}": round(float(np.percentile(np.abs(e), q)), 2) for q in (50, 95, 99)}
    out = {"tiles": len(fit_err), "cards": int(sum(len(e) for e in fit_err)),
           "record_cards_m": pct(np.concatenate(base_err)),
           "extra_slope_fit_m": pct(fit_err := np.concatenate(fit_err)),
           "extra_flat_m": pct(np.concatenate(flat_err)),
           "extra_floating_over_1p5m": float(np.mean(fit_err > 1.5))}   # cards sink 1.5 m by design
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{area}_zcheck.json").write_text(json.dumps(out, indent=1))
    print(f"{area} zcheck:", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("area")
    ap.add_argument("--mode", default="before", choices=MODES)
    ap.add_argument("--win-km", type=float, default=6.0)
    ap.add_argument("--zcheck", action="store_true", help="extra far cards' Z vs the shipped terrain")
    a = ap.parse_args()
    if a.zcheck:
        zcheck(a.area)
        return
    sim = simulate(a.area, a.mode, a.win_km)
    st, pfh = stats(sim)
    plot(a.area, a.mode, sim, st, pfh)
    (OUT / f"{a.area}_{a.mode}.json").write_text(json.dumps(st, indent=1))
    print(f"{a.area} [{a.mode}] near={len(sim['near'])} far={len(sim['far'])} forest share={st['forest_share']:.2f}")
    print("  trees per ha of forest:", {k: round(v, 1) for k, v in st["trees_per_forest_ha"].items()},
          f"band/beyond={st['band_over_beyond']:.1f}x")


if __name__ == "__main__":
    main()
