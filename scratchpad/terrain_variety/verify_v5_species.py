"""V2/V5 forest offline verification (next steps.md sec 7.35) - NO game run.

Python replica of ODForestManager.cpp's VarietyRecipe() + BlendAt() (IDW
1/d^2 within 300 m, nearest fallback, per-tile ValueNoise2D aspen mask -
HashLattice/ValueNoise2D ported bit-for-bit incl. uint32 wraparound) run on
the REAL ODB2 sidecar at every 96 m forest tile that contains a road station
(forest scatter is corridor-bound, ForestBandCm=120 m), i.e. the tiles the
player actually sees trees on. Reports species share vs elevation and the
aspect/moisture density bias, and plots them.

TownForestDampen / building / lake exclusion are NOT reproduced (unchanged
by this work, they scale ForestWeight not the recipe).
Writes verify_v5_species.log + species_vs_elevation.png.
"""
import json
import struct
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial import cKDTree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPECIES = ["AspenA(Blob)", "Spruce(Olive)", "LodgepoleA(Conifer)", "AspenB(ScrubOak)",
           "Krummholz(Manzanita)", "SubalpFir(Yucca)", "LodgepoleB(CanyonOak)"]
# BiomeRecipeTable rows actually present in summit_county: species[7], shrub[2], tree, bush
TABLE = {
    0: ([0.78, 0.05, 0.09, 0.03, 0.00, 0.00, 0.05], [0.90, 0.10], 1.0, 0.6),
    1: ([0.05, 0.05, 0.82, 0.03, 0.00, 0.00, 0.05], [0.90, 0.10], 1.0, 0.6),
    2: ([0.02, 0.02, 0.02, 0.55, 0.37, 0.00, 0.02], [0.15, 0.85], 0.5, 1.0),
    3: ([0.01, 0.01, 0.01, 0.05, 0.05, 0.85, 0.02], [0.10, 0.90], 0.25, 0.8),
    4: ([0.28, 0.28, 0.05, 0.14, 0.10, 0.00, 0.15], [0.55, 0.45], 0.15, 1.3),
    5: ([0.50, 0.05, 0.38, 0.02, 0.00, 0.00, 0.05], [0.95, 0.05], 1.0, 1.1),
    6: ([0.15, 0.20, 0.35, 0.10, 0.05, 0.00, 0.15], [0.60, 0.40], 0.05, 0.1),
    12: ([0.02, 0.03, 0.03, 0.35, 0.05, 0.03, 0.49], [0.25, 0.75], 0.10, 0.3),
    13: ([0.10, 0.35, 0.35, 0.10, 0.00, 0.00, 0.10], [0.70, 0.30], 0.5, 0.6),
}
SUBALP = ([0.02, 0.50, 0.08, 0.01, 0.02, 0.35, 0.02], [0.90, 0.10], 1.0, 0.5)
TREEL = ([0.00, 0.15, 0.00, 0.00, 0.70, 0.15, 0.00], [0.60, 0.40], 0.35, 0.4)
SUB_LO, SUB_HI, TL_LO, TL_HI = 2950.0, 3200.0, 3350.0, 3550.0
ASP_LO, ASP_HI, ASP_FADE, ASP_SCALE, ASP_MAX = 2450.0, 3350.0, 150.0, 50000.0, 0.75
TILE = 9600.0
RADIUS = 30000.0


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def hash_lattice(x, y, seed):
    m = 0xFFFFFFFF
    h = ((x & m) * 374761393 + (y & m) * 668265263 + (seed & m) * 2246822519) & m
    h = ((h ^ (h >> 13)) * 1274126177) & m
    h ^= h >> 16
    return (h & 0x00FFFFFF) / float(0x00FFFFFF)


def value_noise(x, y, scale, seed):
    gx, gy = x / scale, y / scale
    ix, iy = int(np.floor(gx)), int(np.floor(gy))
    fx, fy = gx - ix, gy - iy
    v00, v10 = hash_lattice(ix, iy, seed), hash_lattice(ix + 1, iy, seed)
    v01, v11 = hash_lattice(ix, iy + 1, seed), hash_lattice(ix + 1, iy + 1, seed)
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    a, b = v00 + (v10 - v00) * sx, v01 + (v11 - v01) * sx
    return a + (b - a) * sy


def lerp_recipe(a, b, t):
    return (list(np.array(a[0]) + (np.array(b[0]) - np.array(a[0])) * t),
            list(np.array(a[1]) + (np.array(b[1]) - np.array(a[1])) * t),
            a[2] + (b[2] - a[2]) * t, a[3] + (b[3] - a[3]) * t)


def variety_recipe(biome, north, moist, elev, aspen_mask):
    sp, sh, tree, bush = TABLE[biome]
    sp, sh = np.array(sp, float), np.array(sh, float)
    n, s = max(0.0, north), max(0.0, -north)
    aspen = 0.0
    if biome == 1:
        r = lerp_recipe((sp, sh, tree, bush), SUBALP, smoothstep(SUB_LO, SUB_HI, elev))
        r = lerp_recipe(r, TREEL, smoothstep(TL_LO, TL_HI, elev))
        sp, sh, tree, bush = np.array(r[0]), np.array(r[1]), r[2], r[3]
        window = smoothstep(ASP_LO - ASP_FADE, ASP_LO, elev) * (1 - smoothstep(ASP_HI - ASP_FADE, ASP_HI, elev))
        suit = np.clip(0.7 * moist + 0.6 * n, 0, 1)
        aspen = ASP_MAX * suit * aspen_mask * window
        tot = sp.sum()
        sp = sp * (1 - aspen)
        sp[0] += aspen * tot * 0.6
        sp[3] += aspen * tot * 0.4
    tree *= (1 + 0.25 * n - 0.35 * s) * (1 - 0.20 * moist)
    bush *= 1 + 0.5 * moist
    sh = sh + np.array([0.5 * moist, 0.5 * s])
    return sp, sh, tree, bush


def read_odb2(path):
    b = Path(path).read_bytes()
    assert b[:4] == b"ODB2"
    n = struct.unpack_from("<I", b, 4)[0]
    dt = np.dtype([("x", "<f4"), ("y", "<f4"), ("b", "u1"), ("fw", "u1"),
                   ("n", "u1"), ("m", "u1"), ("e", "<u2")])
    a = np.frombuffer(b, dt, n, 8)
    return (a["x"].astype(float), a["y"].astype(float), a["b"].astype(int),
            a["n"] / 127.5 - 1.0, a["m"] / 255.0, a["e"].astype(float))


def main():
    log = []
    p = lambda *a: (print(*a), log.append(" ".join(str(s) for s in a)))
    for bio, (sp0, sh0, tr0, bu0) in TABLE.items():   # ODB1 defaults must be a no-op
        for mk in (0.0, 1.0):
            sp, sh, tr, bu = variety_recipe(bio, 0.0, 0.0, 0.0, mk)
            assert np.allclose(sp, sp0) and np.allclose(sh, sh0) and tr == tr0 and bu == bu0, bio
    p("ODB1 neutrality: VarietyRecipe(N=0,M=0,E=0) == BiomeRecipeTable for all rows, mask 0 and 1: OK")
    x, y, b, north, moist, elev = read_odb2(ROOT / "OpenDriveUE/Content/OpenDrive/Masks/biome_summit_county_test.bin")
    tree_idx = cKDTree(np.column_stack([x, y]))
    st = json.load(open(ROOT / "data/road_tiles/summit_county_test/road_stations.json"))
    lat0, lon0 = st["origin_latlon"]
    lat = np.concatenate([np.array(s["lat"], float) for s in st["segments"]])
    lon = np.concatenate([np.array(s["lon"], float) for s in st["segments"]])
    sx = (lon - lon0) * 111132.0 * np.cos(np.radians(lat0)) * 100   # UE X = east cm
    sy = -(lat - lat0) * 111132.0 * 100                            # UE Y = -north cm
    keys = np.unique(np.column_stack([np.floor(sx / TILE), np.floor(sy / TILE)]).astype(int), axis=0)
    p(f"forest tiles containing a road station: {len(keys)}")
    rows = []
    for kx, ky in keys:
        cx, cy = (kx + 0.5) * TILE, (ky + 0.5) * TILE
        mask = smoothstep(0.55, 0.70, value_noise(cx, cy, ASP_SCALE, 7))
        idx = tree_idx.query_ball_point([cx, cy], RADIUS)
        if not idx:
            _, j = tree_idx.query([cx, cy]); idx = [j]; w = np.array([1.0])
        else:
            d2 = (x[idx] - cx) ** 2 + (y[idx] - cy) ** 2
            w = 1.0 / np.maximum(1.0, d2)
        acc_sp = np.zeros(7); acc_tree = 0.0; acc_bush = 0.0; acc_sh = np.zeros(2); e = 0.0
        for wi, i in zip(w, idx):
            sp, sh, tr, bu = variety_recipe(b[i], north[i], moist[i], elev[i], mask)
            acc_sp += wi * sp; acc_sh += wi * sh; acc_tree += wi * tr; acc_bush += wi * bu; e += wi * elev[i]
        ws = w.sum()
        sp = acc_sp / ws
        rows.append(dict(elev=e / ws, sp=sp / sp.sum(), tree=acc_tree / ws, bush=acc_bush / ws,
                         mask=mask, sh=acc_sh / acc_sh.sum()))
    E = np.array([r["elev"] for r in rows]); SP = np.array([r["sp"] for r in rows])
    T = np.array([r["tree"] for r in rows]); M = np.array([r["mask"] for r in rows])
    aspen = SP[:, 0] + SP[:, 3]
    p(f"aspen noise mask > 0: {(M > 0).mean():.1%} of tiles, == 1: {(M >= 1).mean():.1%}")
    p(f"tiles with aspen share >= 20%: {(aspen >= 0.2).mean():.1%}; >= 40%: {(aspen >= 0.4).mean():.1%}; "
      f"baseline aspen share (Evergreen table row) = 8%")
    p(f"TreeDensityScale across tiles: p10 {np.percentile(T, 10):.2f} p50 {np.percentile(T, 50):.2f} "
      f"p90 {np.percentile(T, 90):.2f} (Evergreen table = 1.0)")
    bins = [2400, 2800, 2950, 3100, 3200, 3350, 3450, 3550, 3700, 4100]
    p("\nelev band      n    " + "  ".join(f"{s[:10]:>10s}" for s in SPECIES) + "   treeDens")
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (E >= lo) & (E < hi)
        if m.sum():
            p(f"{lo}-{hi}  {m.sum():5d}  " + "  ".join(f"{v:10.2f}" for v in SP[m].mean(0))
              + f"   {T[m].mean():.2f}")
    Path(HERE / "verify_v5_species.log").write_text("\n".join(log) + "\n")

    fig, ax = plt.subplots(1, 2, figsize=(15, 5.5))
    es = np.linspace(2400, 3900, 300)
    sw = np.array([variety_recipe(1, 0, 0, e, 0)[0] for e in es])
    ax[0].stackplot(es, (sw / sw.sum(1, keepdims=True)).T, labels=SPECIES)
    ax[0].set_title("Evergreen recipe vs elevation (flat, dry, no aspen mask)")
    ax[0].set_xlabel("elevation m"); ax[0].set_ylabel("species share"); ax[0].legend(fontsize=7, loc="lower left")
    order = np.argsort(E)
    ax[1].stackplot(E[order], SP[order].T, labels=SPECIES)
    ax[1].set_title(f"BlendAt() at {len(E)} real corridor forest tiles (sorted by elevation)")
    ax[1].set_xlabel("tile IDW elevation m"); ax[1].set_ylabel("species share")
    fig.tight_layout(); fig.savefig(HERE / "species_vs_elevation.png", dpi=110)


if __name__ == "__main__":
    main()
