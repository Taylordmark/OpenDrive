"""Generate tiling ground detail textures + terrain classification mask.

Outputs (data/stages/<stage>/textures/):
    T_DetailForest.png / T_DetailGrass.png / T_DetailRock.png   albedo, tileable
    T_DetailForest_N.png / ..Grass_N / ..Rock_N                 normal maps
    T_TerrainMask.png   RGBA weights from satellite classification
                        R=forest G=grass B=rock/dirt A=water (RGB sums to 1)
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

STAGE = "tail_of_the_dragon"
TEX = Path(__file__).resolve().parent.parent / "data" / "stages" / STAGE / "textures"
SIZE = 1024
rng = np.random.default_rng(42)


def tileable_noise(size, cell, lo=0.0, hi=1.0):
    n = max(2, -(-size // cell))  # ceil so the upscaled grid always covers size
    g = rng.random((n, n)).astype(np.float32)
    big = np.kron(g, np.ones((cell, cell), dtype=np.float32))[:size, :size]
    for sh in (1, 2, 4, 8, 16, 32, 48):
        if sh >= cell:
            break
        big = 0.5 * big + 0.25 * (np.roll(big, sh, 0) + np.roll(big, sh, 1))
    for sh in (1, 2, 3):
        big = 0.5 * big + 0.25 * (np.roll(big, sh * 7, 0) + np.roll(big, -sh * 5, 1))
    big = (big - big.min()) / (big.max() - big.min() + 1e-6)
    return lo + big * (hi - lo)


def fbm(size, cells=(256, 64, 16, 4), weights=(1, 0.5, 0.25, 0.12)):
    acc = np.zeros((size, size), dtype=np.float32)
    for c, w in zip(cells, weights):
        acc += tileable_noise(size, c) * w
    acc = (acc - acc.min()) / (acc.max() - acc.min() + 1e-6)
    return acc


def save_albedo(name, img):
    out = (np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
    Image.fromarray(out).save(TEX / name)
    print("wrote", name)


def save_normal(name, height, strength):
    dx = (np.roll(height, -1, 1) - np.roll(height, 1, 1)) * strength
    dy = (np.roll(height, -1, 0) - np.roll(height, 1, 0)) * strength
    nz = np.ones_like(height)
    ln = np.sqrt(dx * dx + dy * dy + nz * nz)
    n = np.stack([-dx / ln, -dy / ln, nz / ln], axis=-1)
    Image.fromarray(((n * 0.5 + 0.5) * 255).astype(np.uint8)).save(TEX / name)
    print("wrote", name)


def lerp3(a, b, t):
    return a[None, None, :] * (1 - t[:, :, None]) + b[None, None, :] * t[:, :, None]


# ---------------------------------------------------------------- forest floor
# stylized: broad flat patches, warm and bright, minimal grain
h = fbm(SIZE, cells=(256, 64), weights=(1, 0.35))
dark = np.array([0.22, 0.15, 0.08])
mid = np.array([0.36, 0.26, 0.13])
img = lerp3(dark, mid, np.round(h * 3) / 3)   # posterized patches
moss = tileable_noise(SIZE, 128, 0, 1)
img = img * (1 - 0.4 * (moss > 0.62)[:, :, None]) \
    + np.array([0.18, 0.30, 0.10])[None, None, :] * 0.4 * (moss > 0.62)[:, :, None]
save_albedo("T_DetailForest.png", img)
save_normal("T_DetailForest_N.png", h, 0.8)

# ---------------------------------------------------------------- grass
h = fbm(SIZE, cells=(160, 48), weights=(1, 0.4))
g_dark = np.array([0.20, 0.34, 0.09])
g_light = np.array([0.34, 0.50, 0.14])
img = lerp3(g_dark, g_light, np.round(h * 4) / 4)
save_albedo("T_DetailGrass.png", img)
save_normal("T_DetailGrass_N.png", h, 0.5)

# ---------------------------------------------------------------- rock/dirt
h = fbm(SIZE, cells=(256, 96, 24), weights=(1, 0.5, 0.15))
r_dark = np.array([0.30, 0.26, 0.22])
r_light = np.array([0.55, 0.50, 0.43])
img = lerp3(r_dark, r_light, np.round(h * 4) / 4)
save_albedo("T_DetailRock.png", img)
save_normal("T_DetailRock_N.png", h, 2.0)

# ---------------------------------------------------------------- mask
# classify from RAW imagery (keep it; T_Terrain.png becomes the stylized copy)
raw_path = TEX / "T_TerrainRaw.png"
if not raw_path.exists():
    (TEX / "T_Terrain.png").rename(raw_path)
sat = np.asarray(Image.open(raw_path).convert("RGB"),
                 dtype=np.float32) / 255.0
r, g, b = sat[:, :, 0], sat[:, :, 1], sat[:, :, 2]
val = sat.max(axis=2)
green = g - np.maximum(r, b)

w_water = np.clip((0.28 - val) * 8, 0, 1) * np.clip((b - r + 0.05) * 8, 0, 1)
w_grass = np.clip(green * 6, 0, 1) * np.clip((val - 0.32) * 6, 0, 1)
# rock demands *bright* and un-green: khaki leaf-off forest (val ~0.35-0.48,
# greenness ~0) must stay forest or tree cover develops bald gaps
w_rock = np.clip((0.04 - green) * 8, 0, 1) * np.clip((val - 0.45) * 6, 0, 1)
w_forest = np.clip(1.0 - w_grass - w_rock - w_water, 0, 1)


def blur(a, n=3):
    for _ in range(n):
        a = 0.2 * a + 0.2 * (np.roll(a, 1, 0) + np.roll(a, -1, 0)
                             + np.roll(a, 1, 1) + np.roll(a, -1, 1))
    return a


w_forest, w_grass, w_rock, w_water = map(blur, (w_forest, w_grass, w_rock, w_water))

# ---- carve the road corridor out of foliage channels (deterministic:
# uses the actual road centerline, not imagery classification)
meta = json.loads((TEX.parent / "stage_meta.json").read_text())
ls = meta["landscape"]
side_cm = (ls["resolution"] - 1) * ls["scale"][0]
corner = ls["location_cm"]
mres = w_forest.shape[0]
road = json.loads((TEX.parent / "road_mesh.json").read_text())
centers = []
for ch in road["chunks"]:
    v = np.array(ch["vertices"], dtype=np.float64).reshape(-1, 4, 3)
    centers.append((v[:, 1, :2] + v[:, 2, :2]) / 2)
centerline = np.vstack(centers)

mpp = side_cm / 100.0 / mres          # meters per mask pixel
excl = np.ones((mres, mres), dtype=np.float32)
CLEAR_M, FADE_M = 13.0, 28.0
r_px = int(math.ceil(FADE_M / mpp)) + 1
for (cx, cy) in centerline:
    px = (cx - corner[0]) / side_cm * (mres - 1)
    py = (cy - corner[1]) / side_cm * (mres - 1)
    x0, x1 = max(0, int(px) - r_px), min(mres, int(px) + r_px + 1)
    y0, y1 = max(0, int(py) - r_px), min(mres, int(py) + r_px + 1)
    if x0 >= x1 or y0 >= y1:
        continue
    yy, xx = np.mgrid[y0:y1, x0:x1]
    d_m = np.hypot((xx - px) * mpp, (yy - py) * mpp)
    f = np.clip((d_m - CLEAR_M) / (FADE_M - CLEAR_M), 0, 1)
    excl[y0:y1, x0:x1] = np.minimum(excl[y0:y1, x0:x1], f)

w_forest *= excl
w_grass *= excl

tot = w_forest + w_grass + w_rock + 1e-5
mask = np.stack([w_forest / tot, w_grass / tot, w_rock / tot,
                 np.clip(w_water, 0, 1)], axis=-1)
Image.fromarray((mask * 255).astype(np.uint8), mode="RGBA").save(
    TEX / "T_TerrainMask.png")
cov = mask.mean(axis=(0, 1))
print(f"wrote T_TerrainMask.png  coverage forest={cov[0]:.2f} grass={cov[1]:.2f} "
      f"rock={cov[2]:.2f} water={cov[3]:.2f} (road corridor carved)")

# ---------------------------------------------------------------- stylize sat
a = sat.copy()
for _ in range(2):  # soften pixel noise
    a = 0.2 * a + 0.2 * (np.roll(a, 1, 0) + np.roll(a, -1, 0)
                         + np.roll(a, 1, 1) + np.roll(a, -1, 1))
mean = a.mean(axis=2, keepdims=True)
a = np.clip(mean + (a - mean) * 1.55, 0, 1)          # saturate
a = np.clip(a * 1.10 + 0.02, 0, 1)                   # lift
q = Image.fromarray((a * 255).astype(np.uint8)).quantize(
    colors=26, method=Image.MEDIANCUT).convert("RGB")
a = np.asarray(q, dtype=np.float32) / 255.0
for _ in range(1):  # soften banding edges
    a = 0.2 * a + 0.2 * (np.roll(a, 1, 0) + np.roll(a, -1, 0)
                         + np.roll(a, 1, 1) + np.roll(a, -1, 1))
Image.fromarray((a * 255).astype(np.uint8)).save(TEX / "T_Terrain.png")
print("wrote T_Terrain.png (stylized)")
