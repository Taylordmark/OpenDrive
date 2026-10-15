"""Regional facade palettes (realism B4): brick is rare in Oahu/SoCal, the
Rockies get wood/log/stone, SF gets painted wood + stucco, and every
wall_set index exists in both material scripts and the C++ R encoding."""
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from build_building_mesh import REGION_PALETTE, pick_facade

layers = []
for script in ("apply_building_material.py", "apply_building_material_real.py"):
    m = re.search(r"^WALL_LAYERS = (\[.*?\])", (ROOT / "unreal" / script).read_text(), re.M | re.S)
    layers.append(eval(m.group(1)))
assert layers[0] == layers[1], layers
n_sets = int(re.search(r"NumWallSets = (\d+);",
                       (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODBuildingManager.cpp").read_text()).group(1))
assert n_sets == len(layers[0]), (n_sets, layers[0])
assert all(0 <= w < n_sets for pal in REGION_PALETTE.values() for ws in pal.values() for w in ws)


def share(region, tag, sets, n=4000):
    rng = random.Random(7)
    c = Counter(pick_facade(tag, 2, rng, region)[0] for _ in range(n))
    return sum(c[s] for s in sets) / n


BRICK, WOOD, PLASTER, STONE, LOG = (0, 1), (2,), (3,), (5,), (6,)
for region in ("tropical", "socal"):
    assert share(region, "house", BRICK) < 0.15, region
    assert share(region, "house", PLASTER) > 0.5, region
assert share("sf", "house", WOOD + PLASTER) == 1.0
assert share("rockies", "house", WOOD + LOG + STONE) > 0.8
assert share("rockies", "house", BRICK) == 0.0
assert pick_facade("yes", 12, random.Random(1), "tropical") == (4, 1)     # towers: concrete, flat
assert pick_facade("house", 2, random.Random(1), "sf")[1] == 0            # houses pitched
assert pick_facade("retail", 2, random.Random(1), "socal")[1] == 1        # shops flat
print("ok")
