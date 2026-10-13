"""Track B (PLAN-VISUALS.md): T_CarFlake.png, the tiling metallic-flake
texture M_CarBody*_real samples (unreal/build_car_materials_real.py).

RGB: one random direction per flake (jittered-grid Voronoi cells, wrapped so
the tile is seamless), encoded 0.5 + 0.5*v. The material uses .rg as a
tangent-space tilt of the clear coat's bottom normal and .b as a per-flake
F0 speckle. Mips average the flakes out, so they sparkle up close and fade
into the paint at distance, which is what real flake does.
A: smooth periodic noise (FFT low-pass of white noise), 0..1, for the coat's
roughness variation (sampled at a much larger tile).

Procedural, fixed seed, no external input - manifest.json says "generated".

Usage:  python content_src/realistic/cars/make_car_flake.py   (writes + self-checks)
"""
from pathlib import Path

import numpy as np
from PIL import Image

SIZE, CELLS, SEED = 256, 48, 7
OUT = Path(__file__).resolve().parent / "T_CarFlake.png"


def make(size=SIZE, cells=CELLS, seed=SEED):
    rng = np.random.default_rng(seed)
    # jittered grid of flake centres, nearest-centre with wrap-around
    pts = (np.stack(np.meshgrid(np.arange(cells), np.arange(cells), indexing="ij"), -1)
           + rng.uniform(0.15, 0.85, (cells, cells, 2))) * (size / cells)
    dirs = rng.normal(size=(cells, cells, 3))
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)
    yx = np.stack(np.meshgrid(np.arange(size) + 0.5, np.arange(size) + 0.5, indexing="ij"), -1)
    cell = (yx / (size / cells)).astype(int)
    best = np.full((size, size), np.inf)
    rgb = np.zeros((size, size, 3))
    for dy in (-1, 0, 1):          # the nearest centre is always in the 3x3 neighbourhood
        for dx in (-1, 0, 1):
            cy, cx = (cell[..., 0] + dy) % cells, (cell[..., 1] + dx) % cells
            d = (yx - pts[cy, cx] + size / 2) % size - size / 2      # wrapped offset
            d2 = (d ** 2).sum(-1)
            hit = d2 < best
            best[hit] = d2[hit]
            rgb[hit] = dirs[cy, cx][hit]
    # periodic low-pass noise: FFT-filtered white noise is seamless by construction
    f = np.fft.fftfreq(size)
    k2 = f[:, None] ** 2 + f[None, :] ** 2
    a = np.real(np.fft.ifft2(np.fft.fft2(rng.normal(size=(size, size))) * np.exp(-k2 * 600.0)))
    a = (a - a.min()) / (a.max() - a.min())
    return np.concatenate([0.5 + 0.5 * rgb, a[..., None]], -1)


def check(img):
    assert img.shape == (SIZE, SIZE, 4) and img.min() >= 0.0 and img.max() <= 1.0
    v = img[..., :3] * 2 - 1
    assert np.allclose(np.linalg.norm(v, axis=-1), 1.0, atol=1e-6), "flake dirs are unit"
    assert abs(v.mean()) < 0.1, "no net tilt, so mips settle on the true surface normal"
    assert np.unique(v.reshape(-1, 3), axis=0).shape[0] > CELLS * CELLS * 0.9, "one dir per flake"
    a = img[..., 3]
    seam = np.abs(a[0] - a[-1]).mean()          # wrap edge vs a typical neighbouring row
    inner = np.abs(np.diff(a, axis=0)).mean()
    assert seam < 2.0 * inner, f"alpha tiles seamlessly ({seam:.4f} vs {inner:.4f})"
    assert np.array_equal(make(), img), "deterministic"


if __name__ == "__main__":
    img = make()
    check(img)
    Image.fromarray((img * 255 + 0.5).astype(np.uint8), "RGBA").save(OUT)
    print(f"wrote {OUT} ({SIZE}px, {CELLS}x{CELLS} flakes) - checks passed")
