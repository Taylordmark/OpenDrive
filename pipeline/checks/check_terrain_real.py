"""Offline check for unreal/apply_terrain_real_materials.py (PLAN-VISUALS
Track D). Ports the material math 1:1 (constants parsed from the script):

1. anti-tiling: the grass layer's albedo over a 64 m patch, single sample
   (stylized) vs near+rotated-far mix, up close and at range. The tile-period
   self-correlation (1.0 for the stylized single sample) must drop.
2. height_blend: exact at alpha 0/1, same mean coverage as a lerp.
3. strata: bands span dark..light and don't repeat at one band.

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


def tex_root():
    # binaries are gitignored; a worktree reads the main checkout's copy
    for base in (ROOT, Path(subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True).strip()).parent):
        p = base / "content_src/textures/ground"
        if (p / "Grass001").exists():
            return p
    sys.exit("ground textures not downloaded - run content_src/textures/ground/download_ground_textures.py")


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
    print(f"OK - wrote {out / 'terrain_real_tiling.png'}")


if __name__ == "__main__":
    main()
