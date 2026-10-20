"""Offline check of the ODU4 building tiles' per-building facade and roof
bytes (build_building_mesh.py pick_facade / roof_shape / roof_look). Parses
the SAME tiles AODBuildingManager::ParseBuildingTileFile reads (via
building_roofs.read_tile), prints a wall_set x flat_roof histogram per height
band and the facade archetype counts, and asserts:
  - magic is ODU4 in every tile, no trailing/short bytes
  - wall_set < NumWallSets, flat_roof in {0,1}, roof_set < 4,
    facade < NumFacades, n_solid <= verts
  - every flat-roofed building >= 8 stories tall (max z - min z of its
    verts) is concrete (wall_set 4)

Usage:  python pipeline/checks/verify_building_facades.py [area]
        (area defaults to test_areas.AREA_NAME)
Exits non-zero on any failure.
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from building_roofs import read_tile  # noqa: E402
from test_areas import AREA_NAME  # noqa: E402

MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
LEVEL_HEIGHT_CM = 320.0   # build_building_mesh.py LEVEL_HEIGHT_M
HIGH_RISE_LEVELS = 8      # build_building_mesh.py HIGH_RISE_LEVELS
NUM_WALL_SETS, NUM_ROOF_SETS, NUM_FACADES = 7, 4, 5
BANDS = [("1-3 st", 0, 4), ("4-7 st", 4, 8), ("8+ st", 8, 10**6)]


def parse_tile(path):
    """Yields (wall_set, flat_roof, height_cm, building) per building."""
    for b in read_tile(path):
        zs = [v[2] for v in b["verts"]]
        yield b["wall_set"], b["flat_roof"], max(zs) - min(zs), b


def main():
    area = sys.argv[1] if len(sys.argv) > 1 else AREA_NAME
    tiles = sorted(MASKS.glob(f"buildings_{area}_test_tile_*.bin"))
    assert tiles, f"no building tiles for {area} in {MASKS}"

    hist = {name: Counter() for name, _, _ in BANDS}
    facades = Counter()
    failures = []
    n = 0
    for path in tiles:
        for wall_set, flat_roof, h, b in parse_tile(path):
            n += 1
            levels = round(h / LEVEL_HEIGHT_CM)
            band = next(name for name, lo, hi in BANDS if lo <= levels < hi)
            hist[band][(wall_set, flat_roof)] += 1
            facades[b["facade"]] += 1
            if (wall_set >= NUM_WALL_SETS or flat_roof not in (0, 1) or b["roof_set"] >= NUM_ROOF_SETS
                    or b["facade"] >= NUM_FACADES
                    or b["n_solid"] > len(b["verts"])):
                failures.append(f"{path.name}: wall_set={wall_set} flat_roof={flat_roof} "
                                f"roof_set={b['roof_set']} facade={b['facade']} n_solid={b['n_solid']}/{len(b['verts'])}")
            if flat_roof and h >= HIGH_RISE_LEVELS * LEVEL_HEIGHT_CM - 1.0 and wall_set != 4:
                failures.append(f"{path.name}: {h / 100:.1f} m tall but "
                                f"wall_set={wall_set} flat_roof={flat_roof}")

    print(f"{area}: {n} buildings in {len(tiles)} ODU4 tile(s)")
    print("band     " + " ".join(f"w{w}/{'flat' if f else 'ptch'}".rjust(9)
                                  for w in range(NUM_WALL_SETS) for f in (0, 1)) + "    total")
    for name, _, _ in BANDS:
        c = hist[name]
        print(f"{name:8} " + " ".join(str(c[(w, f)]).rjust(9) for w in range(NUM_WALL_SETS) for f in (0, 1))
              + f" {sum(c.values()):8}")
    print("facades (punched, house, storefront, curtain, balcony): "
          + ", ".join(str(facades[i]) for i in range(NUM_FACADES)))

    if failures:
        print(f"FAIL: {len(failures)} problem(s)")
        for f in failures[:20]:
            print("  " + f)
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
