"""classify_biomes.py shore rule (realism eval T5): open ground within
SHORE_M of the sea and below SHORE_MAX_ELEV_M is coastal (tropical below
23.5 deg), not Savanna/grass - Waikiki and Sandy Beach used to be Savanna.
Trees, built-up and higher ground keep their normal biome.

Run:  python pipeline/tests/test_biome_shore.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classify_biomes import (BIOME_COASTAL, BIOME_TROPICAL_COAST, SHORE_M, WC_BUILT, WC_GRASS, WC_TREE,  # noqa: E402
                             WC_WATER, pick_biome, shore_mask)


def pick(wc, elev, lat, shore):
    return pick_biome(0.0, 1.0, 0.0, 0.0, 0.0, elev, lat, wc=wc, shore=shore)


def test_shore_mask():
    g = np.full((1, 40), WC_GRASS, np.uint8)
    g[0, :5] = WC_WATER
    m = shore_mask(g)[0]
    px = 10 << 1     # WC_OVERVIEW 1 = 20 m pixels
    k = int(SHORE_M // px)                  # 4 px = 80 m inland
    assert m[:5 + k].all() and not m[5 + k:].any()


def test_pick():
    assert pick(WC_GRASS, 3.0, 21.3, True) == BIOME_TROPICAL_COAST     # Sandy Beach
    assert pick(WC_GRASS, 3.0, 37.8, True) == BIOME_COASTAL            # SF shore
    for wc, elev in ((WC_TREE, 3.0), (WC_BUILT, 3.0), (WC_GRASS, 40.0)):
        assert pick(wc, elev, 21.3, True) not in (BIOME_TROPICAL_COAST, BIOME_COASTAL)
    assert pick(WC_GRASS, 3.0, 21.3, False) != BIOME_TROPICAL_COAST


if __name__ == "__main__":
    test_shore_mask()
    test_pick()
    print("test_biome_shore: OK")
