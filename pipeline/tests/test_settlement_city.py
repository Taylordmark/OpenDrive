"""classify_settlements.pick_archetype (realism B6): City needs density plus
EITHER height (low residential share, >= CITY_MIN_TALL multi-storey) OR a
main street (>= CITY_MIN_SHOPS shop footprints, residential share < 50%).
Whole-bbox Skagit's dense tiles of untagged sheds/houses pass neither."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from classify_settlements import CITY_MIN_SHOPS, CITY_MIN_TALL, pick_archetype

# (footprints, residential share, 3+ storey, shop footprints) - real tiles
assert pick_archetype(2295, 0.28, 256, 112) == "City"           # Waikiki: height path
assert pick_archetype(1582, 0.46, 4, 73) == "City"              # downtown Mount Vernon: main street
assert pick_archetype(1109, 0.02, 0, 28) == "City"              # Frisco Main St
assert pick_archetype(408, 0.0, 0, 2) == "AmericanTown"         # Skagit farmland, all "yes"
assert pick_archetype(3107, 0.72, 0, 18) == "AmericanTown"      # Ewa: flat suburb
assert pick_archetype(2000, 0.72, 0, 200) == "AmericanTown"     # strip malls in tagged-house suburb
assert pick_archetype(2000, 0.40, 200, 0) == "AmericanTown"     # tall but residential: height path needs < 35%
assert pick_archetype(100, 0.0, 50, 50) == "AmericanTown"       # too sparse
assert pick_archetype(500, 0.0, CITY_MIN_TALL, 0) == "City" and pick_archetype(500, 0.0, CITY_MIN_TALL - 1, 0) == "AmericanTown"
assert pick_archetype(500, 0.49, 0, CITY_MIN_SHOPS) == "City" and pick_archetype(500, 0.49, 0, CITY_MIN_SHOPS - 1) == "AmericanTown"
print("ok")
