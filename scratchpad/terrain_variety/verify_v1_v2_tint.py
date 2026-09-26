"""V1/V2 offline verification (next steps.md sec 7.35) - NO game run.

Reproduces AODTerrainManager::ApplyBiomeTint's biome stage in Python against
the REAL baked terrain tiles (Content/OpenDrive/Masks/terrain_summit_county_
test_tile_*.bin) and the REAL biome sidecars, before (ODB1, 2026-09-06 bake,
saved to before/) vs after (ODB2, this change):
  - candidates = FBiomeSpatialHash 200 m cells overlapping tile +-160 m
  - per vertex: IDW (1/d^2) over candidates within 300 m; else nearest if
    <= FarBiomeFallbackCm (1 km); else the Deciduous default ("no tint")
  - distinctBiomes = set of NEAREST-candidate biomes over the tile's verts
  - GroundTint after V2 aspect/moisture modulation (constants copied from
    ODTerrainManager.cpp). Elevation bias / verge / noise stages are NOT
    reproduced - they're unchanged by this work.
Vertices are subsampled (VERT_STRIDE) for runtime, so distinctBiomes is a
lower bound of the in-game log value.
Background tile = no road station within the tile +-160 m query box.

Writes verify_v1_v2_tint.log + tint_tiles_before_after.png +
biome_map_before_after.png + aspect_moisture_hist.png next to this script.
"""
import glob
import json
import re
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
MASKS = ROOT / "OpenDriveUE/Content/OpenDrive/Masks"
TILE_CM = 200000.0
CELL_CM = 20000.0
MARGIN_CM = 16000.0
RADIUS_CM = 30000.0
FAR_CM = 100000.0
VERT_STRIDE = 13

TINT = np.array([
    (0.06, 0.16, 0.05), (0.04, 0.14, 0.07), (0.42, 0.38, 0.18), (0.55, 0.42, 0.22),
    (0.35, 0.36, 0.14), (0.10, 0.20, 0.10), (0.35, 0.36, 0.35), (0.40, 0.40, 0.38),
    (0.30, 0.28, 0.14), (0.45, 0.40, 0.18), (0.04, 0.15, 0.05), (0.50, 0.46, 0.32),
    (0.48, 0.34, 0.22), (0.28, 0.30, 0.24)], dtype=np.float32)
NAMES = ["Deciduous", "Evergreen", "Chaparral", "Desert", "Plains", "Wetland", "Alpine",
         "Arctic", "Farmland", "Savanna", "Rainforest", "Coastal", "Canyon", "Foothill"]
DRY = np.array((0.30, 0.27, 0.13)); ASPECT_DRY = 0.45; ASPECT_SHADE = 0.30
MEADOW = np.array((0.09, 0.22, 0.06)); MOIST = 0.40


def read_biome_bin(path):
    b = Path(path).read_bytes()
    magic, n = b[:4], struct.unpack_from("<I", b, 4)[0]
    if magic == b"ODB1":
        dt = np.dtype([("x", "<f4"), ("y", "<f4"), ("b", "u1"), ("fw", "u1")])
    else:
        assert magic == b"ODB2", magic
        dt = np.dtype([("x", "<f4"), ("y", "<f4"), ("b", "u1"), ("fw", "u1"),
                       ("n", "u1"), ("m", "u1"), ("e", "<u2")])
    a = np.frombuffer(b, dt, n, 8)
    north = a["n"] / 127.5 - 1.0 if "n" in a.dtype.names else np.zeros(n)
    moist = a["m"] / 255.0 if "m" in a.dtype.names else np.zeros(n)
    return magic.decode(), a["x"].astype(np.float64), a["y"].astype(np.float64), a["b"], north, moist


def read_verts(path):
    b = path.read_bytes()
    vcount = struct.unpack_from("<I", b, 21)[0]
    return np.frombuffer(b, "<f4", vcount * 3, 29).reshape(-1, 3)


def cell_index(x, y):
    cells = {}
    cx, cy = np.floor(x / CELL_CM).astype(int), np.floor(y / CELL_CM).astype(int)
    for i, k in enumerate(zip(cx, cy)):
        cells.setdefault(k, []).append(i)
    return cells


def query(cells, x0, y0, x1, y1):
    out = []
    for cy in range(int(np.floor(y0 / CELL_CM)), int(np.floor(y1 / CELL_CM)) + 1):
        for cx in range(int(np.floor(x0 / CELL_CM)), int(np.floor(x1 / CELL_CM)) + 1):
            out += cells.get((cx, cy), [])
    return np.array(out, dtype=int)


def tint_tile(v, key, sc, cells, v2):
    x, y, b, north, moist = sc
    kx, ky = key
    cand = query(cells, kx * TILE_CM - MARGIN_CM, ky * TILE_CM - MARGIN_CM,
                 (kx + 1) * TILE_CM + MARGIN_CM, (ky + 1) * TILE_CM + MARGIN_CM)
    nv = len(v)
    tint = np.tile(TINT[0], (nv, 1)).astype(np.float64)
    branch = np.full(nv, 2)   # 0 idw, 1 near-fallback, 2 default/no-data
    if len(cand) == 0:
        return tint, branch, set()
    d2 = (v[:, 0:1] - x[cand][None]) ** 2 + (v[:, 1:2] - y[cand][None]) ** 2
    near = np.argmin(d2, axis=1)
    bestd2 = d2[np.arange(nv), near]
    w = np.where(d2 <= RADIUS_CM ** 2, 1.0 / np.maximum(1.0, d2), 0.0)
    ws = w.sum(1)
    idw = ws > 0
    tint[idw] = (w[idw] @ TINT[b[cand]]) / ws[idw, None]
    nrt = np.zeros(nv); moi = np.zeros(nv)
    nrt[idw] = (w[idw] @ north[cand]) / ws[idw]
    moi[idw] = (w[idw] @ moist[cand]) / ws[idw]
    fb = ~idw & (bestd2 <= FAR_CM ** 2)
    tint[fb] = TINT[b[cand][near[fb]]]
    nrt[fb] = north[cand][near[fb]]; moi[fb] = moist[cand][near[fb]]
    branch[idw] = 0; branch[fb] = 1
    if v2:
        south = np.maximum(0, -nrt)[:, None]
        tint = tint + (DRY - tint) * south * ASPECT_DRY
        tint *= 1.0 - np.maximum(0, nrt)[:, None] * ASPECT_SHADE
        tint = tint + (MEADOW - tint) * moi[:, None] * MOIST
    return tint, branch, set(b[cand][near].tolist())


def hsv_stats(t):
    luma = t @ np.array([0.2126, 0.7152, 0.0722])
    mx, mn = t.max(1), t.min(1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    return luma, sat


def main():
    log = []
    p = lambda *a: (print(*a), log.append(" ".join(str(s) for s in a)))
    st = json.load(open(ROOT / "data/road_tiles/summit_county_test/road_stations.json"))
    sx = np.concatenate([np.array(s["east_m"]) for s in st["segments"]]) * 100
    sy = -np.concatenate([np.array(s["north_m"]) for s in st["segments"]]) * 100
    sets = {}
    for label, path in (("before", HERE / "before/biome_summit_county_test.bin"),
                        ("after", MASKS / "biome_summit_county_test.bin")):
        magic, *sc = read_biome_bin(path)
        sets[label] = (magic, sc, cell_index(sc[0], sc[1]))
        p(f"{label}: {path.name} magic={magic} N={len(sc[0])}")
    station_cells = set(zip(np.floor(sx / TILE_CM).astype(int), np.floor(sy / TILE_CM).astype(int)))
    rows = []
    rx = re.compile(r"_tile_(-?\d+)_(-?\d+)\.bin$")
    files = sorted(glob.glob(str(MASKS / "terrain_summit_county_test_tile_*.bin")))
    for f in files:
        kx, ky = map(int, rx.search(f).groups())
        v = read_verts(Path(f))[::VERT_STRIDE].astype(np.float64)
        box = ((sx > kx * TILE_CM - MARGIN_CM) & (sx < (kx + 1) * TILE_CM + MARGIN_CM)
               & (sy > ky * TILE_CM - MARGIN_CM) & (sy < (ky + 1) * TILE_CM + MARGIN_CM))
        row = {"key": (kx, ky), "bg": not box.any(), "nv": len(v)}
        for label, v2 in (("before", False), ("after", True)):
            _, sc, cells = sets[label]
            t, br, seen = tint_tile(v, (kx, ky), sc, cells, v2)
            luma, sat = hsv_stats(t)
            row[label] = dict(distinct=len(seen), idw=(br == 0).mean(), fb=(br == 1).mean(),
                              dflt=(br == 2).mean(), luma_mean=luma.mean(), luma_std=luma.std(),
                              sat_mean=sat.mean(), rgb=t.mean(0))
        rows.append(row)
    for grp, sel in (("ALL", lambda r: True), ("BACKGROUND (no road in query box)", lambda r: r["bg"]),
                     ("CORRIDOR", lambda r: not r["bg"])):
        rs = [r for r in rows if sel(r)]
        p(f"\n== {grp}: {len(rs)} tiles ==")
        for label in ("before", "after"):
            d = np.array([r[label]["distinct"] for r in rs])
            dflt = np.array([r[label]["dflt"] * r["nv"] for r in rs]).sum() / sum(r["nv"] for r in rs)
            fb = np.array([r[label]["fb"] * r["nv"] for r in rs]).sum() / sum(r["nv"] for r in rs)
            ls = np.array([r[label]["luma_std"] for r in rs])
            p(f"  {label:6s}: tiles distinctBiomes>0: {(d > 0).sum()}/{len(rs)} ({(d > 0).mean():.1%}); "
              f"distinct>=2: {(d >= 2).sum()}; mean distinct {d.mean():.2f}; "
              f"verts default/no-data {dflt:.1%}, near-fallback {fb:.1%}; "
              f"mean in-tile luma std {ls.mean():.4f}")
        lm = [np.array([r[l]["luma_mean"] for r in rs]) for l in ("before", "after")]
        p(f"  across-tile std of tile-mean luma: before {lm[0].std():.4f} -> after {lm[1].std():.4f}")
    Path(HERE / "verify_v1_v2_tint.log").write_text("\n".join(log) + "\n")

    # plot: per-tile mean GroundTint before/after
    ks = np.array([r["key"] for r in rows])
    fig, ax = plt.subplots(1, 2, figsize=(14, 7))
    for a, label in zip(ax, ("before", "after")):
        rgb = np.array([r[label]["rgb"] for r in rows])
        disp = np.clip(rgb / 0.45, 0, 1) ** (1 / 2.2)
        a.scatter(ks[:, 0], -ks[:, 1], c=disp, marker="s", s=30)
        bg = np.array([r["bg"] for r in rows])
        a.scatter(ks[bg, 0], -ks[bg, 1], facecolors="none", edgecolors="k", marker="s", s=30, lw=0.2)
        a.set_title(f"{label}: per-tile mean GroundTint (biome stage{' + V2' if label == 'after' else ''})\n"
                    "outlined = background tiles (no road in query box); display-stretched")
        a.set_aspect("equal"); a.set_xlabel("tile X (2 km, +east)"); a.set_ylabel("-tile Y (north up)")
    fig.tight_layout(); fig.savefig(HERE / "tint_tiles_before_after.png", dpi=110)

    # plot: biome sample map before/after
    fig, ax = plt.subplots(1, 2, figsize=(15, 7.5))
    cmap = plt.get_cmap("tab20")
    for a, label in zip(ax, ("before", "after")):
        _, (x, y, b, *_), _ = sets[label]
        for bi in np.unique(b):
            m = b == bi
            a.scatter(x[m] / 1e5, -y[m] / 1e5, s=0.4, color=cmap(bi), label=f"{NAMES[bi]} ({m.sum()})")
        a.set_title(f"{label}: biome samples ({len(x)})"); a.set_aspect("equal")
        a.set_xlabel("east km"); a.set_ylabel("north km"); a.legend(markerscale=12, fontsize=7, loc="lower left")
    fig.tight_layout(); fig.savefig(HERE / "biome_map_before_after.png", dpi=110)

    # plot: aspect / moisture histograms (after)
    _, (x, y, b, north, moist), _ = sets["after"]
    j = json.load(open(ROOT / "data/road_tiles/summit_county_test/road_biomes.json"))
    nc = j["n_corridor"]
    fig, ax = plt.subplots(1, 3, figsize=(15, 4))
    for a, arr, t in ((ax[0], north, "northness (+1 N-facing)"), (ax[1], moist, "moisture (1 = valley bottom)")):
        a.hist([arr[:nc], arr[nc:]], bins=40, label=["corridor", "grid"], histtype="step")
        a.set_title(t); a.legend()
    sc = ax[2].scatter(x[nc:] / 1e5, -y[nc:] / 1e5, c=north[nc:], s=0.5, cmap="coolwarm_r", vmin=-1, vmax=1)
    ax[2].set_aspect("equal"); ax[2].set_title("grid northness (blue=N-facing)"); fig.colorbar(sc, ax=ax[2])
    fig.tight_layout(); fig.savefig(HERE / "aspect_moisture_hist.png", dpi=110)
    p(f"\nnorthness corridor mean {north[:nc].mean():+.3f} std {north[:nc].std():.3f}; "
      f"grid mean {north[nc:].mean():+.3f} std {north[nc:].std():.3f}; |n|>0.5: {(np.abs(north) > 0.5).mean():.1%}")
    p(f"moisture corridor mean {moist[:nc].mean():.3f}; grid mean {moist[nc:].mean():.3f}; "
      f">0.5: corridor {(moist[:nc] > 0.5).mean():.1%} grid {(moist[nc:] > 0.5).mean():.1%}")
    Path(HERE / "verify_v1_v2_tint.log").write_text("\n".join(log) + "\n")


if __name__ == "__main__":
    main()
