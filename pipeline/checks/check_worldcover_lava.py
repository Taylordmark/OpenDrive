"""Offline check of classify_biomes' ESA WorldCover lava rule (2026-10-14):
classifies a ~100 m grid over two real Big Island flows and asserts most of
it reads BIOME_LAVA, and that rainforest and Oahu grassland don't. Fetches
(cached) USGS imagery + WorldCover, so needs the network on a cold cache.

    python pipeline/checks/check_worldcover_lava.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from classify_biomes import (BIOME_LAVA, classify_rgb, fetch_mosaic, fetch_worldcover,  # noqa: E402
                             lava_weight, pick_biome, sample_bilinear, sample_nearest_ll)

# (name, south, north, west, east, min lava fraction, max lava fraction)
CASES = [
    ("Kilauea", 19.33, 19.37, -155.16, -155.11, 0.5, 1.0),
    ("Mauna Loa 1984", 19.66, 19.70, -155.46, -155.41, 0.45, 1.0),
    ("Volcano rainforest", 19.42, 19.45, -155.25, -155.21, 0.0, 0.05),
    ("Oahu Waianae grass", 21.40, 21.50, -158.20, -158.10, 0.0, 0.02),
    ("Oahu Dole plateau", 21.49, 21.53, -158.06, -158.01, 0.0, 0.02),
    ("Oahu Waipio dim fields", 21.40, 21.50, -158.10, -158.00, 0.0, 0.01),
]


def lava_fraction(s, n, w, e):
    lon, lat = np.meshgrid(np.linspace(w, e, 50), np.linspace(s, n, 45))
    lon, lat = lon.ravel(), lat.ravel()
    img, ib = fetch_mosaic(w, s, e, n)
    chans = [sample_bilinear(c, ib, lon, lat) for c in classify_rgb(img)]
    wc_grid, wb = fetch_worldcover(w, s, e, n)
    wc = sample_nearest_ll(wc_grid, wb, lon, lat)
    wl = sample_bilinear(lava_weight(img, ib, wc_grid, wb), ib, lon, lat)
    biomes = np.array([pick_biome(*(float(c[i]) for c in chans), 1000.0, float(lat[i]), 0.0,
                                  0.0, 2000.0, int(wc[i]), float(wl[i]))
                       for i in range(len(lon))])
    return float(np.mean(biomes == BIOME_LAVA))


if __name__ == "__main__":
    for name, s, n, w, e, lo, hi in CASES:
        f = lava_fraction(s, n, w, e)
        print(f"{name}: lava fraction {f:.2f} (want {lo}-{hi})")
        assert lo <= f <= hi, name
    print("ok")
