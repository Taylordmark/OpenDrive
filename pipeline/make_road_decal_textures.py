"""Road decal atlas for the Realistic style (PLAN-VISUALS Track C), generated
here so it's our own work (decision V5) - no downloads.

Writes content_src/realistic/road/:
  T_RoadDecals_real_C.png   RGB linear albedo (stored sRGB), A opacity
  T_RoadDecals_real_N.png   RG tangent normal xy (0.5 = flat), B roughness
2x2 atlas, 1024 px cells: 0 crack, 1 patch (top row), 2 manhole, 3 tire
marks (bottom row) - the cell index UODRoadDecals writes into DecalColor.R.
In every cell x runs along the road (decal U) and y across it (decal V).
Each cell keeps a transparent border so mips don't bleed between cells.
Deterministic (fixed seed): a re-run rewrites the same bytes.

Usage:  python pipeline/make_road_decal_textures.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "content_src" / "realistic" / "road"
CELL = 1024
BORDER = 24                      # px kept fully transparent around each cell


def noise(rng, cells, size=CELL):
    """Smooth 0..1 value noise: a cells x cells random grid, bicubic-upscaled."""
    g = Image.fromarray((rng.random((cells, cells)) * 255).astype(np.uint8))
    return np.asarray(g.resize((size, size), Image.BICUBIC), np.float32) / 255.0


def grid():
    y, x = np.mgrid[0:CELL, 0:CELL].astype(np.float32) / (CELL - 1)
    return x, y


def smoothstep(e0, e1, v):
    t = np.clip((v - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def crack(rng):
    """Meandering longitudinal crack with a few branches and a dark halo."""
    img = Image.new("L", (CELL, CELL), 0)
    d = ImageDraw.Draw(img)
    xs = np.linspace(0.06, 0.94, 60)
    wob = noise(rng, 6, 64)[32]
    ys = 0.5 + 0.22 * (np.interp(xs, np.linspace(0, 1, 64), wob) - 0.5) + rng.normal(0, 0.006, len(xs))
    pts = [(float(x * CELL), float(y * CELL)) for x, y in zip(xs, ys)]
    d.line(pts, fill=255, width=9, joint="curve")
    for _ in range(4):                                   # branches
        i = int(rng.integers(8, len(pts) - 8))
        x, y = pts[i]
        dx, dy = rng.uniform(40, 140), rng.choice([-1, 1]) * rng.uniform(80, 260)
        mid = (x + dx * 0.5 + rng.normal(0, 15), y + dy * 0.5 + rng.normal(0, 15))
        d.line([(x, y), mid, (x + dx, y + dy)], fill=255, width=5, joint="curve")
    core = np.asarray(img, np.float32) / 255.0
    halo = np.asarray(img.filter(ImageFilter.GaussianBlur(14)), np.float32) / 255.0
    x, _ = grid()
    taper = smoothstep(0.04, 0.16, x) * smoothstep(0.96, 0.84, x)
    alpha = np.maximum(core, np.clip(halo * 2.5, 0, 1) * 0.35) * taper
    color = np.full((CELL, CELL, 3), 0.05, np.float32) * (1 - core[..., None]) + 0.02 * core[..., None]
    return color, alpha, -core * 0.8, 0.85 - 0.25 * core


def patch(rng):
    """Rectangular asphalt repair: rough fresh aggregate, sealed dark edge."""
    x, y = grid()
    edge_n = noise(rng, 40) - 0.5
    dist = np.maximum(np.abs(x - 0.5), np.abs(y - 0.5)) + edge_n * 0.01   # 0.5 = cell edge
    inside = smoothstep(0.445, 0.435, dist)
    seal = smoothstep(0.445, 0.43, dist) * smoothstep(0.405, 0.42, dist)
    speck = rng.random((CELL, CELL)).astype(np.float32)
    agg = noise(rng, 180) * 0.6 + speck * 0.4
    tone = 0.075 + 0.03 * (noise(rng, 5) - 0.5)
    color = np.repeat((tone + 0.035 * (agg - 0.5))[..., None], 3, axis=2)
    color = color * (1 - seal[..., None]) + 0.02 * seal[..., None]
    height = inside * (0.25 + 0.5 * agg) - seal * 0.2
    rough = 0.82 + 0.1 * (agg - 0.5) - seal * 0.5
    return color, inside * 0.92, height, rough


def manhole(rng):
    """Cast-iron cover: raised diamond grid, rim, concrete collar."""
    x, y = grid()
    r = np.hypot(x - 0.5, y - 0.5)
    lid = smoothstep(0.372, 0.365, r)
    rim = smoothstep(0.365, 0.37, r) * smoothstep(0.395, 0.388, r)
    collar = smoothstep(0.388, 0.395, r) * smoothstep(0.47, 0.43, r)
    u, v = (x + y) * 40, (x - y) * 40
    studs = ((np.abs(np.sin(u * np.pi)) > 0.55) & (np.abs(np.sin(v * np.pi)) > 0.55)).astype(np.float32)
    rust = noise(rng, 48) * noise(rng, 9)
    iron = np.stack([0.085 + 0.05 * rust, 0.085 + 0.025 * rust, 0.085 + 0.005 * rust], -1)
    iron = iron * (1 + 0.6 * studs)[..., None]   # worn stud tops are polished lighter
    concrete = np.repeat((0.16 + 0.04 * (noise(rng, 60) - 0.5))[..., None], 3, axis=2)
    color = iron * (lid + rim)[..., None] + concrete * collar[..., None]
    alpha = np.clip(lid + rim + collar * 0.75, 0, 1)
    height = lid * studs * 0.6 + rim * 0.8 - collar * 0.1
    rough = (0.5 - 0.15 * studs) * lid + 0.35 * rim + 0.9 * collar + 0.85 * (1 - alpha)
    return color, alpha, height, rough


def tire_marks(rng):
    """A pair of rubber streaks 1.6 m apart (cell spans 2.1 m across)."""
    x, y = grid()
    tread = np.repeat(noise(rng, 90)[:, :1], CELL, axis=1)   # striations: vary across, constant along
    bow = 0.03 * np.sin(np.pi * x)
    streaks = sum(smoothstep(0.048, 0.03, np.abs(y - (0.5 + s * 0.38) - bow)) for s in (-1, 1))
    env = smoothstep(0.03, 0.18, x) * smoothstep(0.97, 0.6, x)
    smear = 0.55 + 0.45 * noise(rng, 12)
    alpha = streaks * env * smear * (0.6 + 0.4 * tread) * 0.8
    color = np.full((CELL, CELL, 3), 0.02, np.float32)
    return color, alpha, np.zeros_like(alpha), np.full_like(alpha, 0.55)


def normal_from_height(h, strength=6.0):
    gy, gx = np.gradient(h)
    n = np.stack([-gx * strength, -gy * strength, np.ones_like(h)], -1)
    return n / np.linalg.norm(n, axis=-1, keepdims=True)


def build():
    rng = np.random.default_rng(20261009)
    c_img = np.zeros((2 * CELL, 2 * CELL, 4), np.float32)
    n_img = np.zeros((2 * CELL, 2 * CELL, 4), np.float32)
    n_img[..., :2], n_img[..., 2], n_img[..., 3] = 0.5, 0.85, 1.0
    keep = np.zeros((CELL, CELL), np.float32)
    keep[BORDER:-BORDER, BORDER:-BORDER] = 1.0
    for i, fn in enumerate((crack, patch, manhole, tire_marks)):
        color, alpha, height, rough = fn(rng)
        alpha = np.clip(alpha, 0, 1) * keep
        n = normal_from_height(height * keep)
        cy, cx = (i // 2) * CELL, (i % 2) * CELL
        c_img[cy:cy + CELL, cx:cx + CELL, :3] = np.clip(color, 0, 1) ** (1 / 2.2)   # linear -> ~sRGB
        c_img[cy:cy + CELL, cx:cx + CELL, 3] = alpha
        n_img[cy:cy + CELL, cx:cx + CELL, :2] = n[..., :2] * 0.5 + 0.5
        n_img[cy:cy + CELL, cx:cx + CELL, 2] = np.clip(rough, 0.02, 1)
    return [(np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8) for a in (c_img, n_img)]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    c, n = build()
    assert c.shape == (2 * CELL, 2 * CELL, 4)
    for i in range(4):   # every cell has content and a clear border
        cy, cx = (i // 2) * CELL, (i % 2) * CELL
        a = c[cy:cy + CELL, cx:cx + CELL, 3]
        assert a.max() > 128, f"cell {i} empty"
        assert a[:BORDER].max() == 0 and a[:, :BORDER].max() == 0, f"cell {i} bleeds into its border"
    Image.fromarray(c, "RGBA").save(OUT / "T_RoadDecals_real_C.png")
    Image.fromarray(n, "RGBA").save(OUT / "T_RoadDecals_real_N.png")
    print(f"wrote {OUT / 'T_RoadDecals_real_C.png'} and _N.png ({2 * CELL}px, 4 cells)")


if __name__ == "__main__":
    main()
