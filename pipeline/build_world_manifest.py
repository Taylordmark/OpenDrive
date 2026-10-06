"""World manifest (2026-10-05, California world step 2): writes
Content/OpenDrive/Masks/worlds.json, the list of baked areas per world that
the runtime streams in and out as whole manager sets:

    {"california": [{"name": "gv_west", "rect_cm": [xmin, ymin, xmax, ymax]}, ...]}

An area is listed when it has a `world` in test_areas.AREAS and terrain
tiles on disk. rect_cm is the UE X/Y extent of those tiles: keys are
floor(X/T), floor(Y/T) with X = east cm and Y = -north cm
(build_terrain_mesh_tiles.py), so the rect is
[min_kx*T, min_ky*T, (max_kx+1)*T, (max_ky+1)*T]. Covers every area on
disk, not just the active one; rebuild_area.py runs it last.

Usage:  python pipeline/build_world_manifest.py
"""
import json
import re
from pathlib import Path

from test_areas import AREAS, TERRAIN_TILE_SIZE_CM

MASKS = Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def rect_cm(keys, tile_cm=TERRAIN_TILE_SIZE_CM):
    """[xmin, ymin, xmax, ymax] UE cm covering terrain tile keys (kx, ky)."""
    kx, ky = [k[0] for k in keys], [k[1] for k in keys]
    return [min(kx) * tile_cm, min(ky) * tile_cm, (max(kx) + 1) * tile_cm, (max(ky) + 1) * tile_cm]


def tile_keys(area):
    pat = re.compile(rf"terrain_{re.escape(area)}_test_tile_(-?\d+)_(-?\d+)\.bin")
    return [(int(m[1]), int(m[2])) for p in MASKS.glob(f"terrain_{area}_test_tile_*.bin")
            if (m := pat.fullmatch(p.name))]


def build_manifest():
    worlds = {}
    for name, cfg in AREAS.items():
        keys = cfg.get("world") and tile_keys(name)
        if keys:
            worlds.setdefault(cfg["world"], []).append({"name": name, "rect_cm": rect_cm(keys)})
    return worlds


if __name__ == "__main__":
    worlds = build_manifest()
    (MASKS / "worlds.json").write_text(json.dumps(worlds, indent=1))
    for world, areas in worlds.items():
        for a in areas:
            print(f"  {world}: {a['name']} rect_cm={a['rect_cm']}")
    print(f"wrote {MASKS / 'worlds.json'}")
