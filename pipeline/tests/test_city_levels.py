"""build_building_mesh.city_type_level_range: City-tile overrides grow with
the area cap but never drop below the small-town BUILDING_TYPE range."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from build_building_mesh import BUILDING_TYPE_LEVEL_RANGE, city_type_level_range

assert city_type_level_range("apartments", 40) == (3, 20)
assert city_type_level_range("commercial", 40) == (1, 10)
assert city_type_level_range("house", 40) is None
for cap in (3, 4, 6, 8, 12, 40):
    for tag in ("apartments", "dormitory", "office", "hotel", "commercial"):
        lo, hi = city_type_level_range(tag, cap)
        assert (lo, hi) >= BUILDING_TYPE_LEVEL_RANGE[tag] and hi >= BUILDING_TYPE_LEVEL_RANGE[tag][1], (tag, cap)
print("ok")
