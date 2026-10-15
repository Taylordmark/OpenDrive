"""Tile-level populated-area archetype classification (Arc 7.0 step 3,
2026-07-24) - same "coarse interim heuristic, refine later" framing as
classify_biomes.py.

Proof scope (Mount Vernon only, see next steps.md Arc 7.0 step 3): every
populated tile was the SAME real archetype (American Town), since only one
town's data had been fetched. Extended 2026-07-27 (for the Oahu testbed,
whole-bbox building fetch - see fetch_osm_buildings.py) with a real, if
still coarse, density split: a tile with enough footprints to plausibly be
a real downtown (as opposed to an ordinary town block) gets "City" instead,
which `build_building_mesh.py` gives a much taller height range - the
proof's `DEFAULT_LEVEL_RANGE` only ever had one (1-3 story) entry, so
without this split a genuine high-rise downtown (Honolulu) would extrude as
a field of uniform short boxes regardless of how dense it actually is.

Real bug, found live 2026-07-28 (Taylor spawned at Waimanalo Beach: "there
are skyscrapers everywhere"): footprint COUNT alone can't tell a genuine
high-rise downtown apart from a merely DENSE residential suburb - windward
Oahu towns like Kailua pack hundreds of small single-family-home footprints
into a 2 km tile, clearing the same density bar real downtown Honolulu does.
Checked the actual fetched data before guessing a fix: Honolulu's core
tiles are ~0% explicitly residential-tagged (almost entirely OSM's generic
"yes", i.e. untyped - real downtowns apparently don't bother tagging
building type much in this dataset), while the Kailua/Waimanalo-area tiles
are 74-99% explicitly tagged house/detached/residential. That's a much
cleaner, more reliable signal than trying to detect rare "tall building"
tags (commercial/office/apartments are sparse even in confirmed downtown
tiles - e.g. Honolulu's densest tile has exactly 1 "commercial" tag out of
1945 buildings). So: "City" now requires BOTH high density AND a low
residential-tagged fraction - density alone is necessary but not
sufficient. `CITY_DENSITY_PER_TILE`/`RESIDENTIAL_FRACTION_MAX` are tuned
against this real comparison (confirmed downtown tiles at 0% residential-
tagged vs confirmed suburb tiles at 74-99%), not guessed blind. Nearest-
place-class/regularity-based selection for the other 4 archetypes
(Industrial, Suburban, Rural Hamlet, European Village) is still future
work; the seam for it stays `pick_archetype()` below.

Usage:  python classify_settlements.py    (needs osm_buildings.json first)

Reads   data/raw/<AREA_NAME>_test/osm_buildings.json
Outputs data/road_tiles/<AREA_NAME>_test/settlement_tiles.json
        {"tiles": {"<tx>,<ty>": "AmericanTown"|"City", ...}}
"""

import json
from collections import defaultdict
from pathlib import Path

from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM, to_local

MIN_BUILDINGS_PER_TILE = 3   # below this, a tile isn't "populated" - just a
                              # stray footprint or two near town's edge
CITY_DENSITY_PER_TILE = 150  # >= this many footprints in one 2 km tile reads
                              # as a real downtown block, not an ordinary town

# OSM building=* values that mean "this is somebody's house", not a downtown
# high-rise - see module docstring for the real Oahu data this was checked
# against. "yes" (generic/untyped) is deliberately NOT in this set - it's
# the majority tag in confirmed downtown Honolulu tiles, so treating it as
# residential would break the exact signal this is trying to use.
RESIDENTIAL_TAGS = {"house", "detached", "semidetached_house", "residential",
                     "terrace", "bungalow", "farm", "cabin", "static_caravan",
                     "houseboat"}
RESIDENTIAL_FRACTION_MAX = 0.35   # City requires FEWER than this fraction of
                                    # a tile's buildings to be residential-
                                    # tagged - confirmed downtown tiles measured
                                    # ~0%, confirmed suburb tiles measured
                                    # 74-99%, so this has wide margin either way

ROOT = Path(__file__).resolve().parent.parent
BUILDINGS_PATH = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm_buildings.json"
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_PATH = STATIONS_PATH.parent / "settlement_tiles.json"


def footprint_centroid(ring):
    lat = sum(p[0] for p in ring) / len(ring)
    lon = sum(p[1] for p in ring) / len(ring)
    return lat, lon


def pick_archetype(building_count, residential_fraction):
    """Seam for the other 4 archetypes (see module docstring) - for now,
    a density split between an ordinary town block and a real downtown,
    gated on a LOW residential-tagged fraction so a merely dense suburb
    (lots of small houses) can't pass as one just by footprint count."""
    if building_count >= CITY_DENSITY_PER_TILE and residential_fraction < RESIDENTIAL_FRACTION_MAX:
        return "City"
    return "AmericanTown"


def main():
    buildings = json.loads(BUILDINGS_PATH.read_text())

    counts = defaultdict(int)
    residential_counts = defaultdict(int)
    for b in buildings:
        if b.get("source") == "ms":
            continue   # B6 gap-fill is all "yes"-tagged: counting it would flip suburbs to City
        lat, lon = footprint_centroid(b["nodes"])
        east_m, north_m = to_local(lon, lat)
        x_cm, y_cm = east_m * 100.0, -north_m * 100.0
        tx = int(x_cm // TERRAIN_TILE_SIZE_CM)
        ty = int(y_cm // TERRAIN_TILE_SIZE_CM)
        counts[(tx, ty)] += 1
        if b.get("building_tag") in RESIDENTIAL_TAGS:
            residential_counts[(tx, ty)] += 1

    tiles = {}
    for (tx, ty), n in counts.items():
        if n < MIN_BUILDINGS_PER_TILE:
            continue
        frac = residential_counts[(tx, ty)] / n
        tiles[f"{tx},{ty}"] = pick_archetype(n, frac)

    # building count per tile too: build_traffic_demand.py's population proxy
    OUT_PATH.write_text(json.dumps({"tiles": tiles,
                                    "counts": {f"{tx},{ty}": n for (tx, ty), n in counts.items()}}))
    print(f"{len(counts)} tile(s) with any buildings, {len(tiles)} populated "
          f"(>= {MIN_BUILDINGS_PER_TILE} buildings) -> {OUT_PATH}")
    for k, v in tiles.items():
        tx_ty = tuple(int(p) for p in k.split(","))
        n = counts[tx_ty]
        frac = residential_counts[tx_ty] / n
        print(f"  tile ({k}): {v} ({n} buildings, {frac:.0%} residential-tagged)")


if __name__ == "__main__":
    main()
