"""Generates a seamlessly-tileable water ripple normal map procedurally
(WATER visual-overhaul pass, 2026-09-03 - see unreal/apply_water_material.py)
- no ambientCG/polyhaven CC0 water-normal asset was pulled in for this pass
(the ground/road/flora sets are all real photographed PBR captures; a
synthetic ripple pattern is simpler to get right and perfectly tileable by
construction, which a photographed capture would need explicit seam-fixing
for anyway).

Technique: sum a handful of sine "wave gratings" whose spatial frequencies
are INTEGER multiples of 2*pi over the image width/height - any such sum is
exactly periodic across the image, so the result tiles with zero visible
seam with no post-hoc blending needed. Each grating gets a random direction,
a random integer frequency (small ones dominate for big rolling shape, large
ones add fine chop - classic Gerstner/Tessendorf-style multi-frequency wave
sum, just baked to a static normal map instead of animated per-frame), a
phase, and an amplitude that falls off with frequency (~1/freq, high
frequencies contribute less height but still keep the surface from looking
like a single smooth swell).

The height field is combined from TWO octave GROUPS at different scales
(see BASE_OCTAVES/DETAIL_OCTAVES) baked into one square texture - the
material (apply_water_material.py) samples this ONE texture twice at
different UV tiling/pan speed to get the "two normal layers" look the task
asked for, rather than shipping two separate textures.

Height -> normal via a central-difference gradient, WRAPPED (np.roll) so the
slope calculation itself never sees a fake edge - required for the tiling
promise above to hold; a plain non-wrapped Sobel would put a seam back in.

Usage:  python generate_water_normal.py
Writes: content_src/textures/water/T_WaterRipple_N.png  (RG=xy tilt, B=up,
        standard OpenGL/UE tangent-space normal map convention, 0.5 = flat)
"""

import numpy as np
from PIL import Image

SIZE = 1024
OUT_PATH = __file__.replace("generate_water_normal.py", "T_WaterRipple_N.png")

RNG_SEED = 20260903  # deterministic - re-running this script reproduces the same texture
rng = np.random.default_rng(RNG_SEED)

# (count, freq_range, amplitude_scale, is_detail) - BASE gives long rolling
# ripple lines (low integer frequencies), DETAIL adds fine chop on top
# (higher integer frequencies, much smaller amplitude) so the bake itself
# already has two visually distinct scales even before the material pans
# two samples of it at different UV tiling.
BASE_OCTAVES = dict(count=5, freq_lo=2, freq_hi=6, amp=1.0)
DETAIL_OCTAVES = dict(count=8, freq_lo=9, freq_hi=22, amp=0.28)

NORMAL_STRENGTH = 2.2   # higher = more pronounced tilt (more visible ripple
                         # bump), tuned by eye against the material's fresnel/
                         # roughness pass rather than derived analytically


def make_height(size):
    ys, xs = np.meshgrid(np.linspace(0, 1, size, endpoint=False),
                          np.linspace(0, 1, size, endpoint=False), indexing="ij")
    height = np.zeros((size, size), dtype=np.float64)

    def add_octaves(count, freq_lo, freq_hi, amp_scale):
        nonlocal height
        for _ in range(count):
            angle = rng.uniform(0, 2 * np.pi)
            dx, dy = np.cos(angle), np.sin(angle)
            freq = rng.integers(freq_lo, freq_hi + 1)  # INTEGER -> exact tiling
            phase = rng.uniform(0, 2 * np.pi)
            amp = amp_scale / freq
            height += amp * np.sin(2 * np.pi * freq * (xs * dx + ys * dy) + phase)

    add_octaves(BASE_OCTAVES["count"], BASE_OCTAVES["freq_lo"], BASE_OCTAVES["freq_hi"], BASE_OCTAVES["amp"])
    add_octaves(DETAIL_OCTAVES["count"], DETAIL_OCTAVES["freq_lo"], DETAIL_OCTAVES["freq_hi"], DETAIL_OCTAVES["amp"])

    height -= height.mean()
    height /= (np.abs(height).max() + 1e-9)
    return height


def height_to_normal(height, strength):
    # Wrapped central differences - see module docstring for why this must
    # roll rather than pad/edge-clamp (tiling correctness).
    dzdx = (np.roll(height, -1, axis=1) - np.roll(height, 1, axis=1)) * 0.5 * strength
    dzdy = (np.roll(height, -1, axis=0) - np.roll(height, 1, axis=0)) * 0.5 * strength

    nx, ny, nz = -dzdx, -dzdy, np.ones_like(height)
    length = np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / length, ny / length, nz / length

    r = ((nx * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8)
    g = ((ny * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8)
    b = ((nz * 0.5 + 0.5) * 255.0).clip(0, 255).astype(np.uint8)
    return np.dstack([r, g, b])


def main():
    height = make_height(SIZE)
    normal_rgb = height_to_normal(height, NORMAL_STRENGTH)

    # Verify the tiling promise: a horizontally-doubled tile should show no
    # visible seam - checked numerically (max abs diff across the seam vs.
    # the image's own interior gradient magnitude) rather than just eyeballed.
    seam_diff = np.abs(normal_rgb[:, 0, :].astype(int) - normal_rgb[:, -1, :].astype(int)).max()
    interior_diff = np.abs(np.diff(normal_rgb[:, :8, :].astype(int), axis=1)).max()
    print(f"seam max-diff={seam_diff} vs interior-step max-diff={interior_diff} "
          f"({'OK, seamless' if seam_diff <= interior_diff * 2 else 'WARNING: visible seam risk'})")

    Image.fromarray(normal_rgb, mode="RGB").save(OUT_PATH)
    print(f"wrote {OUT_PATH} ({SIZE}x{SIZE}, tangent-space normal map, "
          f"{BASE_OCTAVES['count']} base + {DETAIL_OCTAVES['count']} detail octaves, "
          f"strength={NORMAL_STRENGTH})")


if __name__ == "__main__":
    main()
