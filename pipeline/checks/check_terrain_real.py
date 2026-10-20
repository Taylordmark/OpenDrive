"""Offline check for unreal/apply_terrain_real_materials.py (PLAN-VISUALS
Track D). Ports the material math 1:1 (constants parsed from the script):

1. anti-tiling: the grass layer's albedo over a 64 m patch, single sample
   (stylized) vs near+rotated-far mix, up close and at range. The tile-period
   self-correlation (1.0 for the stylized single sample) must drop.
2. height_blend: exact at alpha 0/1, same mean coverage as a lerp.
3. strata: bands span dark..light and don't repeat at one band.
4. road-edge blend (join J, unreal/apply_road_material.py RealEdge*): the
   road's fringe uses this script's shoulder set, tile, tint and dirt-mix
   noise; it always covers the pavement edge/lip, never reaches the lane
   interior or junction pads, rarely eats the edge line, and wanders.

Writes scratchpad/terrain_real_tiling.png (stylized | real near | real far).
Run from repo root:  python pipeline/checks/check_terrain_real.py
"""
import ast
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_noise_common import value_noise

src = (ROOT / "unreal/apply_terrain_real_materials.py").read_text(encoding="utf-8")
C = {}
for node in ast.parse(src).body:   # every top-level literal constant
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        try:
            C[node.targets[0].id] = ast.literal_eval(node.value)
        except ValueError:
            pass


ROAD_SRC = (ROOT / "unreal/apply_road_material.py").read_text(encoding="utf-8")
R = {}
for node in ast.parse(ROAD_SRC).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith("Real"):
        R[node.targets[0].id] = ast.literal_eval(node.value)
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "EdgeLineInsetM":
        R["EdgeLineInsetM"] = ast.literal_eval(node.value)


def road_value_noise2(x, y):
    """apply_road_material.value_noise2 (UE Sine = sin(2 pi x))."""
    def h(v):
        s_ = np.sin(2 * np.pi * v * 127.1) * 43758.5453
        return s_ - np.floor(s_)
    ix, iy = np.floor(x), np.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    hh = lambda dx, dy: h(ix + dx + (iy + dy) * 57.0)
    return (hh(0, 0) + (hh(1, 0) - hh(0, 0)) * fx) * (1 - fy) + (hh(0, 1) + (hh(1, 1) - hh(0, 1)) * fx) * fy


def check_road_edge():
    assert R["RealEdgeSet"][0] == C["LAYERS"]["shoulder"][0] and R["RealEdgeTileCm"] == C["LAYERS"]["shoulder"][1],         "road fringe gravel != terrain shoulder set/tile"
    assert R["RealEdgeDirtSet"][0] == C["LAYERS"]["dirt"][0] and R["RealEdgeDirtTileCm"] == C["LAYERS"]["dirt"][1],         "road fringe dirt != terrain dirt set/tile"
    assert tuple(R["RealEdgeTint"]) == tuple(C["SHOULDER_TINT"]), "road fringe tint != SHOULDER_TINT"
    assert R["RealEdgeDirtSatKeep"] == C["DIRT_SAT_KEEP"], "road fringe dirt saturation != DIRT_SAT_KEEP"
    assert R["RealEdgeSet"][1] == ("/Game/OpenDrive/Realistic/Terrain" if C["LAYERS"]["shoulder"][0] in C["REAL_SETS"]
                                   else "/Game/OpenDrive/Textures/Ground"), "road fringe gravel loads from the wrong dir"
    assert f"g.noise_xy(xy, {R['RealEdgeDirtNoiseCm']}, {tuple(R['RealEdgeDirtSeed'])})" in src,         "road fringe dirt-mix noise != road_edge_blend's fine noise"

    v0 = np.arange(0.0, 5000.0, 0.02) + 12345.0          # 5 km of road, 2 cm steps
    rng = np.random.default_rng(3)
    for half_w in (3.0, 4.5, 6.0):
        for side in (-1.0, 1.0):
            seed = rng.random()
            meter_edge = side * half_w                       # crumble noise sampled at the edge
            long_n = road_value_noise2(np.full_like(v0, meter_edge / R["RealEdgeCellM"]),
                                       v0 / R["RealEdgeCellM"] + seed * 83.0)
            crumble = road_value_noise2(np.full_like(v0, meter_edge / R["RealEdgeCrumbleCellM"]),
                                        v0 / R["RealEdgeCrumbleCellM"])
            w = R["RealEdgeMinM"] + (R["RealEdgeMaxM"] - R["RealEdgeMinM"]) * long_n + (crumble - 0.5) * 2 * R["RealEdgeCrumbleM"]

            def fringe(d_in, soft):
                return np.maximum(np.clip((w - d_in) / soft, 0, 1), np.clip(1 - d_in / R["RealEdgeLipM"], 0, 1))

            for soft in (R["RealEdgeSoftM"], R["RealEdgeFarSoftM"]):
                assert np.all(fringe(0.0, soft) == 1.0), "pavement edge / wrap lip not fully fringed"
            reach = R["RealEdgeMaxM"] + R["RealEdgeCrumbleM"]
            assert np.all(fringe(reach + R["RealEdgeSoftM"], R["RealEdgeSoftM"]) == 0)
            assert reach + R["RealEdgeFarSoftM"] < 2.5, "fringe would reach junction pads (|meterPos| <= 2 m)"
            line_eaten = float(np.mean(fringe(R["EdgeLineInsetM"], R["RealEdgeSoftM"]) > 0.5))
            assert line_eaten < 0.1, f"fringe eats the edge line on {line_eaten:.0%} of the road"
            assert 0.12 < w.mean() < 0.3 and w.std() > 0.04, f"fringe width mean {w.mean():.2f} std {w.std():.2f}"
    print(f"road edge: fringe width {w.mean():.2f} +- {w.std():.2f} m, edge line eaten {line_eaten:.1%}, "
          f"matches terrain shoulder ({R['RealEdgeSet'][0]} @ {R['RealEdgeTileCm']:.0f} cm)")


def tex_root():
    # binaries are gitignored; a worktree reads the main checkout's copy
    for base in (ROOT, Path(subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True).strip()).parent):
        p = base / "content_src/textures/ground"
        if (p / "Grass001").exists():
            return p
    sys.exit("ground textures not downloaded - run content_src/textures/ground/download_ground_textures.py")


def check_layer_means():
    """LAYER_MEAN_LIN (the detail layers' hue divisor) = the sets' measured mean,
    and *_SAT_KEEP land on the survey's saturation targets (median HSV S)."""
    lin = lambda a: np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    for key, keep, target in (("grass", "GRASS_SAT_KEEP", 0.30), ("forest", "FOREST_SAT_KEEP", 0.29),
                              ("dirt", "DIRT_SAT_KEEP", 0.15)):
        aid = C["LAYERS"][key][0]
        d = tex_root() / aid if aid not in C["REAL_SETS"] else tex_root().parents[1] / "realistic/terrain" / aid
        a = lin(np.asarray(Image.open(next(d.glob("*_Color.*"))).convert("RGB"), dtype=np.float64) / 255.0)
        m = a.reshape(-1, 3).mean(0)
        assert np.allclose(m, C["LAYER_MEAN_LIN"][key], rtol=0.02), f"{aid} mean {m} != LAYER_MEAN_LIN[{key}]"
        a = a[::4, ::4]
        grey = (a @ np.array([0.3, 0.59, 0.11]))[..., None]
        k = grey + (a - grey) * C[keep]
        s = np.where(k <= 0.0031308, k * 12.92, 1.055 * np.clip(k, 0, 1) ** (1 / 2.4) - 0.055)
        sat = float(np.median((s.max(-1) - s.min(-1)) / np.maximum(s.max(-1), 1e-6)))
        print(f"{key} ({aid}): mean {m.round(4)}, saturation at {keep} {C[keep]} = {sat:.2f} (target {target})")
        assert abs(sat - target) < 0.02, f"{keep} misses the saturation target"


def contrast(x, k):
    return np.clip((x - 0.5) * k + 0.5, 0, 1)


def height_blend(alpha, ha, hb):
    return np.clip(alpha + (hb - ha) * 4 * C["HEIGHT_BLEND"] * alpha * (1 - alpha), 0, 1)


def sample(img, u, v):
    h, w = img.shape
    return img[(np.floor(np.mod(v, 1) * h)).astype(int) % h, (np.floor(np.mod(u, 1) * w)).astype(int) % w]


def tile_corr(a, period_px):
    x, y = a[:, :-period_px].ravel(), a[:, period_px:].ravel()
    return float(np.corrcoef(x, y)[0, 1])


def main():
    grass = np.asarray(Image.open(tex_root() / "Grass001/Grass001_2K-PNG_Color.png").convert("L"),
                       dtype=np.float32) / 255.0
    tile = C["LAYERS"]["grass"][1]
    px_cm = 10.0
    n = 640                                       # 64 m patch, 10 cm/px
    xs = (np.arange(n) * px_cm) + 1234567.0       # far from origin, like real world coords
    wx, wy = np.meshgrid(xs, xs + 7654321.0)

    single = sample(grass, wx / tile, wy / tile)

    ang = math.radians(C["ROT_DEG"])
    sx, sy = wx / (tile * C["FAR_MUL"]), wy / (tile * C["FAR_MUL"])
    fu = sx * math.cos(ang) - sy * math.sin(ang) + C["FAR_OFFSET"][0]
    fv = sx * math.sin(ang) + sy * math.cos(ang) + C["FAR_OFFSET"][1]
    far = sample(grass, fu, fv)
    var_w = contrast(value_noise(wx, wy, C["VAR_NOISE_CM"], seed_offset=(17.0, 3.0)), C["VAR_CONTRAST"]) * C["VAR_MAX"]

    def real(dist_t):
        w = var_w + (C["FAR_W_AT_DIST"] - var_w) * dist_t
        return single + (far - single) * w

    period = int(round(tile / px_cm))
    near_img, far_img = real(0.0), real(1.0)
    c_single, c_near, c_far = (tile_corr(a, period) for a in (single, near_img, far_img))
    print(f"tile-period ({tile / 100:.0f} m) self-correlation: stylized {c_single:.3f}  "
          f"real near {c_near:.3f}  real far {c_far:.3f}")
    assert c_single > 0.9, "sanity: a single tiled sample must repeat exactly at its tile"
    # up close (< 25 m) a few tiles are all you see, so the bar is lower there;
    # 50-200 m (the drive-check range) is where the far copy has to win
    assert c_near < 0.7 and c_far < 0.3, "anti-tiling mix doesn't break the tile period"

    out = ROOT / "scratchpad"
    out.mkdir(exist_ok=True)
    strip = np.concatenate([single, near_img, far_img], axis=1)
    Image.fromarray((np.clip(strip, 0, 1) * 255).astype(np.uint8)).save(out / "terrain_real_tiling.png")

    rng = np.random.default_rng(1)
    ha, hb = rng.random(100000), rng.random(100000)
    assert np.all(height_blend(0.0, ha, hb) == 0) and np.all(height_blend(1.0, ha, hb) == 1)
    for a in (0.2, 0.5, 0.8):
        mean = height_blend(a, ha, hb).mean()
        assert abs(mean - a) < 0.02, f"height_blend shifts coverage at alpha {a}: {mean:.3f}"

    z = np.arange(0, 3000.0, 1.0) + 50000.0       # a 30 m cut wall, 1 cm steps
    zs = z / C["STRATA_BAND_CM"]
    coarse = value_noise(zs, np.full_like(zs, 5.0), 1.0)
    fine = value_noise(zs * C["STRATA_FINE_MUL"], np.full_like(zs, 9.0), 1.0)
    band = contrast(coarse + (fine - coarse) * C["STRATA_FINE_WEIGHT"], C["STRATA_CONTRAST"])
    lag = int(C["STRATA_BAND_CM"])
    b_corr = float(np.corrcoef(band[:-lag], band[lag:])[0, 1])
    print(f"strata: min {band.min():.2f} max {band.max():.2f} one-band self-correlation {b_corr:.2f}")
    assert band.min() < 0.15 and band.max() > 0.85, "strata bands don't span dark..light"
    assert b_corr < 0.5, "strata repeat at one band"
    check_road_edge()
    check_layer_means()
    print(f"OK - wrote {out / 'terrain_real_tiling.png'}")


if __name__ == "__main__":
    main()
