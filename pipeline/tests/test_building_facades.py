"""Regional facade palettes (realism B4): brick is rare in Oahu/SoCal, the
Rockies get wood/log/stone, SF gets painted wood + stucco, and every
wall_set index exists in both material scripts and the C++ R encoding.
Roofs (realism B3): OSM roof:shape / roof:material / roof:colour win, houses
pitch and shops stay flat otherwise, regional roof sets, and every roof_set
exists in the roof material and the C++ A encoding."""
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from build_building_mesh import (REGION_PALETTE, REGION_ROOF_SETS, ROOF_ASPHALT, ROOF_FLAT, ROOF_METAL,
                                 ROOF_TILE, parse_colour, pick_facade, roof_look, roof_shape)

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

real = (ROOT / "unreal" / "apply_building_material_real.py").read_text()
roof_layers = eval(re.search(r"^ROOF_LAYERS = (\[.*?\])", real, re.M | re.S).group(1),
                   {"BUILDING_TEX_DIR": "", "REAL_TEX_DIR": ""})
n_roof = int(re.search(r"NumRoofSets = (\d+)",
                       (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODBuildingManager.cpp").read_text()).group(1))
assert len(roof_layers) == n_roof == ROOF_FLAT + 1
assert len(eval(re.search(r"^ROOF_MEAN_LUMA = (\[.*?\])", real, re.M).group(1))) == n_roof

rng = random.Random(3)
assert roof_shape({"roof:shape": "hipped"}, 1, 900, 3, rng) == "hip"
assert roof_shape({"roof:shape": "flat"}, 0, 80, 1, rng) == "flat"
assert roof_shape({}, 1, 2000, 2, rng) == "flat"                         # big shop
assert roof_shape({}, 1, 100, 2, rng) != "flat"                          # small, low: pitched
assert roof_shape({}, 0, 100, 9, rng) == "flat"                          # high-rise
assert {roof_shape({}, 0, 120, 2, rng, "tropical") for _ in range(200)} == {"gable", "hip"}
assert roof_look({}, "flat", rng)[0] == ROOF_FLAT
assert roof_look({"roof:material": "steel"}, "gable", rng)[0] == ROOF_METAL
assert roof_look({"roof:material": "gravel"}, "flat", rng)[0] == ROOF_FLAT
socal = Counter(roof_look({}, "hip", random.Random(i), "socal")[0] for i in range(2000))
assert socal[ROOF_TILE] > 0.5 * 2000 and socal[ROOF_METAL] == 0, socal
assert all(abs(sum(w for _, w in v) - 1) < 1e-9 for v in REGION_ROOF_SETS.values())
brown = parse_colour("#663300")
assert brown[0] > brown[1] > brown[2] and abs(roof_look({"roof:colour": "#663300"}, "gable", rng)[1][0] - brown[0]) < 1e-9
white, black = parse_colour("White"), parse_colour("black")
assert abs(0.2126 * white[0] + 0.7152 * white[1] + 0.0722 * white[2] - 0.6) < 1e-6 and black == (0.03,) * 3
assert parse_colour("#FFFFFCC") is None and parse_colour(None) is None
assert roof_look({}, "gable", random.Random(1), "sf")[0] in (ROOF_ASPHALT, ROOF_TILE)
print("ok")
