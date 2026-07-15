"""Generate the road surface texture matching the road mesh UV layout.

UV layout (from build_stage.py cross-section, 4 profile points):
    u 0.000-0.333  left skirt/shoulder (7.5m..4.5m left of center)
    u 0.333-0.667  asphalt (9 m wide)
    u 0.667-1.000  right skirt/shoulder
    v tiles once per 20 m of road.

Output: data/stages/<stage>/textures/T_Road.png (1024x1024, tileable in v).
"""

from pathlib import Path

import numpy as np
from PIL import Image

STAGE = "tail_of_the_dragon"
OUT = Path(__file__).resolve().parent.parent / "data" / "stages" / STAGE / "textures"
OUT.mkdir(parents=True, exist_ok=True)

SIZE = 1024
rng = np.random.default_rng(129)  # US-129 :)


def tileable_noise(size, cell, seed_rng, lo=0.0, hi=1.0):
    """Value noise, tileable both axes."""
    g = seed_rng.random((size // cell, size // cell)).astype(np.float32)
    big = np.kron(g, np.ones((cell, cell), dtype=np.float32))
    # cheap smoothing with wrap so tiling stays seamless
    for sh in (1, 2, 4, 8, 16, 32, 48):
        if sh >= cell:
            break
        big = 0.5 * big + 0.25 * (np.roll(big, sh, 0) + np.roll(big, sh, 1))
    for sh in (1, 2, 3):  # cross-axis pass kills residual block edges
        big = 0.5 * big + 0.25 * (np.roll(big, sh * 7, 0) + np.roll(big, -sh * 5, 1))
    big = (big - big.min()) / (big.max() - big.min() + 1e-6)
    return lo + big * (hi - lo)


u = (np.arange(SIZE) + 0.5) / SIZE          # across road
img = np.zeros((SIZE, SIZE, 3), dtype=np.float32)

# meters from centerline for each u (profile is linear per segment)
m = np.where(u < 1 / 3, -7.5 + (u * 3) * 3.0,
             np.where(u < 2 / 3, -4.5 + (u - 1 / 3) * 3 * 9.0,
                      4.5 + (u - 2 / 3) * 3 * 3.0))

# --- base layers
import json
climate_path = OUT.parent / "climate.json"
MOISTURE = 0.0
if climate_path.exists():
    MOISTURE = json.loads(climate_path.read_text()).get("road_moisture", 0.0)
print(f"road moisture factor: {MOISTURE}")

dirt = np.array([0.24, 0.19, 0.14])
# wetter climate = darker, more weathered asphalt (wishlist #1)
asphalt = np.array([0.075, 0.075, 0.085]) * (1.0 - 0.38 * MOISTURE)
on_road = np.abs(m) <= 4.5

base = np.where(on_road[None, :, None], asphalt[None, None, :],
                dirt[None, None, :])
img[:] = base

# per-pixel speckle + broad blotches (both tileable)
speckle = tileable_noise(SIZE, 2, rng, 0.85, 1.15)[:, :SIZE]
blotch = tileable_noise(SIZE, 64, rng, 0.85, 1.10)
img *= (speckle * blotch)[:, :, None]

# tire-polish: slightly darker wear bands at each lane center (+-1.8 m)
for lane in (-1.8, 1.8):
    wear = np.exp(-((m - lane) / 0.9) ** 2)
    img *= (1.0 - 0.12 * wear)[None, :, None]

# --- markings (painted over asphalt only)
def paint(mask_u, color, wear_amount=0.35):
    wear = tileable_noise(SIZE, 8, rng, 1.0 - wear_amount, 1.0)
    mask = mask_u[None, :] * wear
    img[:] = img * (1 - mask[:, :, None]) + color[None, None, :] * mask[:, :, None]


yellow = np.array([0.90, 0.68, 0.08])
white = np.array([0.88, 0.88, 0.86])
# double yellow: two 10 cm lines, 10 cm apart, centered
for off in (-0.10, 0.10):
    paint(((np.abs(m - off) < 0.05) & on_road).astype(np.float32), yellow)
# white edge lines: 10 cm wide, 30 cm in from asphalt edge
for edge in (-4.2, 4.2):
    paint(((np.abs(m - edge) < 0.05) & on_road).astype(np.float32), white)

out = (np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
Image.fromarray(out).save(OUT / "T_Road.png")
print(f"Wrote {OUT / 'T_Road.png'}")
