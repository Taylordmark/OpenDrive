"""Deletes this area's tile sidecars outside its owned rect
(test_areas.OWN_RECT_CM) - step 3's "bake with a halo, keep only owned
tiles" (2026-10-05). The bbox bakes past the owned edge so roads and
junctions there have context; only the owned tiles ship, so neighbouring
world members never both ship a tile.

Runs after every tile writer: rebuild_area.py runs it before the gates, and
experimental_junction_pads.py re-runs it after its own re-bake. The gates
only check owned stations. Whole-area files (roadstations, centerline,
routegraph, labels, biome, map texture) keep the halo. The forest grid
(elev_*.bin) is clipped where it's baked (build_forest_elevation.py).

Usage:  python prune_unowned_tiles.py
"""
import re
from pathlib import Path

from test_areas import AREA_NAME, OWN_RECT_CM, ROAD_TILE_SIZE_CM, TERRAIN_TILE_SIZE_CM, owns

ROOT = Path(__file__).resolve().parent.parent
DIRS = (ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks",
        ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "MasksV2")
# every tiled family; road/roadv3 are 500 m tiles, the rest 2 km
TILE_RE = re.compile(r"^(terrain|terrain_coarse|road|roadv3|buildings|walls|barrier|water|parking|"
                     rf"guardrail|embankment|bridge|furniture)_{re.escape(AREA_NAME)}_test_tile_(-?\d+)_(-?\d+)\.bin$")


def main():
    if OWN_RECT_CM is None:
        print(f"{AREA_NAME} owns every tile it bakes - nothing to prune")
        return
    kept = pruned = 0
    for d in DIRS:
        for p in d.glob(f"*_{AREA_NAME}_test_tile_*.bin"):
            m = TILE_RE.match(p.name)
            if not m:
                continue
            size = ROAD_TILE_SIZE_CM if m[1] in ("road", "roadv3") else TERRAIN_TILE_SIZE_CM
            if owns(int(m[2]) * size, int(m[3]) * size):
                kept += 1
            else:
                p.unlink()
                pruned += 1
    print(f"{AREA_NAME}: pruned {pruned} tile(s) outside {OWN_RECT_CM}, kept {kept}")


if __name__ == "__main__":
    main()
