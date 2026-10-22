"""World manifest (2026-10-05, California world step 2): writes
Content/OpenDrive/Masks/worlds.json, the list of baked areas per world that
the runtime streams in and out as whole manager sets:

    {"california": [{"name": "far_gasquet", "rect_cm": [xmin, ymin, xmax, ymax],
                     "files": ["Masks/biome_far_gasquet_test.bin", ...]}, ...]}

An area is listed when it has a `world` in test_areas.AREAS and terrain
tiles on disk. rect_cm is the UE X/Y extent of those tiles: keys are
floor(X/T), floor(Y/T) with X = east cm and Y = -north cm
(build_terrain_mesh_tiles.py), so the rect is
[min_kx*T, min_ky*T, (max_kx+1)*T, (max_ky+1)*T]. Covers every area on
disk, not just the active one; rebuild_area.py runs it last.

files (2026-10-13, live tile streaming): the area's whole-area sidecars
plus its water tiles (ODForestManager reads all of them up front), as paths
under Content/OpenDrive/. With -ODTileServer the runtime fetches these before
it spawns the area (AODGameMode::PrefetchArea); per-tile files stream on
their own.

(2026-10-21, area picker) A drawn area's entry also carries terrain_tile_m
(1000; hand areas bake 2 km tiles), and the script writes Masks/coverage.json
(docs/area-picker-contract.md) for the map's coverage overlay:

    {"areas": {"<area>": {"name", "drawn", "outline_ll": [[lon, lat], ...],
               "tiles": [{"ll": [[lon, lat] x4], "state": "baked|pending|failed"}]}}}

Baked = a terrain tile on disk (so a deleted or never-baked tile is a hole
whatever a status file says); pending/failed = a drawn area's cells in
data/bake/<area>/status.json that have no tile yet. bake_request.py reruns
this after every job.

Usage:  python pipeline/build_world_manifest.py
"""
import json
import re
from pathlib import Path

from test_areas import AREAS, frame_lcc
from world_frame import lcc_inverse

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
BAKE = ROOT / "data" / "bake"


def tile_cm(cfg):
    return cfg.get("terrain_tile_m", 2000) * 100.0


def rect_cm(keys, tile_cm):
    """[xmin, ymin, xmax, ymax] UE cm covering terrain tile keys (kx, ky)."""
    kx, ky = [k[0] for k in keys], [k[1] for k in keys]
    return [min(kx) * tile_cm, min(ky) * tile_cm, (max(kx) + 1) * tile_cm, (max(ky) + 1) * tile_cm]


def tile_keys(area, masks=None):
    pat = re.compile(rf"terrain_{re.escape(area)}_test_tile_(-?\d+)_(-?\d+)\.bin")
    return [(int(m[1]), int(m[2])) for p in (masks or MASKS).glob(f"terrain_{area}_test_tile_*.bin")
            if (m := pat.fullmatch(p.name))]


def area_files(area):
    """Content/OpenDrive-relative whole-area files for area: names ending in
    _<area>[_test].<ext> (so one area never claims a same-prefix neighbour's), plus water tiles."""
    own = re.compile(rf".*_{re.escape(area)}(_test)?\.(bin|json|png)")
    out = [f"Masks/{p.name}" for p in MASKS.iterdir() if own.fullmatch(p.name)]
    out += [f"Masks/{p.name}" for p in MASKS.glob(f"water_{area}_test_tile_*.bin")]
    return sorted(out)


def build_manifest():
    worlds = {}
    for name, cfg in AREAS.items():
        keys = cfg.get("world") and tile_keys(name)
        if keys:
            entry = {"name": name, "rect_cm": rect_cm(keys, tile_cm(cfg)), "files": area_files(name)}
            if cfg.get("drawn"):
                entry["terrain_tile_m"] = cfg["terrain_tile_m"]
            worlds.setdefault(cfg["world"], []).append(entry)
    return worlds


def build_coverage(areas=AREAS, masks=None, bake=BAKE):
    """coverage.json's dict (module docstring). Tile (kx, ky) spans UE
    X = kx*T..(kx+1)*T, north = -(ky+1)*T..-ky*T; drawn cell "i_j" is the
    1 km tile (i, -j-1)."""
    out = {}
    for name, cfg in areas.items():
        t = tile_cm(cfg) / 100.0
        lcc = frame_lcc(cfg)
        tiles = {k: "baked" for k in tile_keys(name, masks)}
        if cfg.get("drawn"):
            cells = ((json.loads(p.read_text(encoding="utf-8")) if (p := bake / name / "status.json").is_file()
                      else {}).get("cells", {}))
            for c, state in cells.items():
                if state != "baked":   # "baked" with no tile on disk stays a hole
                    i, j = map(int, c.split("_"))
                    tiles.setdefault((i, -j - 1), "failed" if state == "failed" else "pending")
        if not tiles and not cfg.get("drawn"):
            continue
        corners = lambda xs, ns: [[round(float(v), 6) for v in lcc_inverse(x, n, *lcc)] for x, n in zip(xs, ns)]
        if cfg.get("polygon_ll"):
            outline = cfg["polygon_ll"]
        else:
            s, w, n, e = cfg["bbox_snwe"]
            outline = [[w, s], [e, s], [e, n], [w, n]]
        out[name] = {"name": cfg.get("name", name), "drawn": bool(cfg.get("drawn")), "outline_ll": outline,
                     "tiles": [{"ll": corners([kx * t, (kx + 1) * t, (kx + 1) * t, kx * t],
                                              [-(ky + 1) * t, -(ky + 1) * t, -ky * t, -ky * t]), "state": st}
                               for (kx, ky), st in sorted(tiles.items())]}
    return {"areas": out}


if __name__ == "__main__":
    worlds = build_manifest()
    (MASKS / "worlds.json").write_text(json.dumps(worlds, indent=1))
    tmp = MASKS / "coverage.json.tmp"   # atomic: the game may be reading it
    tmp.write_text(json.dumps(build_coverage(), separators=(",", ":")), encoding="utf-8")
    tmp.replace(MASKS / "coverage.json")
    for world, areas in worlds.items():
        for a in areas:
            print(f"  {world}: {a['name']} rect_cm={a['rect_cm']}")
    print(f"wrote {MASKS / 'worlds.json'}")
