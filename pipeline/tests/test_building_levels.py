"""build_building_mesh storeys (realism B1): real data first (OSM tag, then
the Microsoft height), else estimate_levels - houses keep their type range,
others copy a neighbour with real data, capped by footprint area."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_building_mesh import estimate_levels, known_levels

assert known_levels({"levels": 3, "ms_height_m": 30.0}) == 3
assert known_levels({"levels": None, "ms_height_m": 8.0}) == 2
assert known_levels({"levels": None, "ms_height_m": 1.0}) == 1
assert known_levels({"height_m": 9.0, "ms_height_m": 30.0}) == 2         # OSM height before Microsoft
assert known_levels({"height_m": 5.5}) == 1                               # one storey + pitched roof
assert known_levels({"height_m": 100.0}) == 31
assert known_levels({"levels": None, "ms_height_m": None}) is None

rng = random.Random(1)
towers = [30] * 12
for _ in range(200):
    assert estimate_levels("house", 900.0, towers, rng) in (1, 2)        # a house stays a house
    assert estimate_levels("yes", 80.0, towers, rng) <= 2                 # small footprint: no tower
    assert estimate_levels("yes", 220.0, [3, 4, 4], rng) in (3, 4)        # SF flats copy the block
    assert estimate_levels("yes", 2000.0, towers, rng) == 30              # big footprint among towers
    assert estimate_levels("yes", 2000.0, [], rng) in (1, 2)              # no data nearby: low
    assert estimate_levels("apartments", 2000.0, [], rng) in (3, 4)       # ... or its type range
print("ok")
