"""World manifest (2026-10-05, step 2): rect maths incl. the Y flip, a mirror
of the runtime spawn/destroy rule the C++ streams areas with, and every
manifest area's whole-area sidecars on disk.

Run:  python pipeline/tests/test_world_manifest.py"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_world_manifest import MASKS, area_files, build_manifest, rect_cm
from test_areas import TERRAIN_TILE_SIZE_CM as T

# Runtime rule (C++ mirrors these exact numbers): every 0.5 s, each manifest
# area other than home spawns when the pawn's 2D distance to its rect drops
# under 8 km and is destroyed when it exceeds 16 km. Home is never destroyed.
SPAWN_CM, DESTROY_CM = 800000.0, 1600000.0


def rect_dist(x, y, r):
    return math.hypot(max(r[0] - x, 0.0, x - r[2]), max(r[1] - y, 0.0, y - r[3]))


def tick(x, y, manifest, home, live):
    events = []
    for a in manifest:
        if a["name"] == home:
            continue
        d = rect_dist(x, y, a["rect_cm"])
        if a["name"] not in live and d < SPAWN_CM:
            live.add(a["name"]); events.append(("spawn", a["name"]))
        elif a["name"] in live and d > DESTROY_CM:
            live.remove(a["name"]); events.append(("destroy", a["name"]))
    return events


def drive(path, manifest, home="west"):
    live, events = set(), []
    for x, y in path:
        events += tick(x, y, manifest, home, live)
    return events


def line(x0, x1, y, step=1500.0):   # 30 m/s at 0.5 s ticks
    n = int(abs(x1 - x0) // step)
    return [(x0 + (x1 - x0) * i / n, y) for i in range(n + 1)]


if __name__ == "__main__":
    # (a) rect from keys; keys are floor(X/T), floor(Y/T) with Y = -north
    assert rect_cm([(-3, 5), (2, -1), (0, 0)]) == [-3 * T, -1 * T, 3 * T, 6 * T]
    pts = [(1000.0, 3000.0), (-500.0, -4100.0)]   # (east m, north m)
    keys = [(math.floor(e * 100 / T), math.floor(-n * 100 / T)) for e, n in pts]
    assert keys == [(0, -2), (-1, 2)]
    r = rect_cm(keys)
    assert all(r[0] <= e * 100 <= r[2] and r[1] <= -n * 100 <= r[3] for e, n in pts)

    # (b) two adjacent halves sharing a 1-tile overlap, far_gasquet ~940 km away
    # (north is -Y). Rects in cm, shaped like the real ones.
    m = [{"name": "west", "rect_cm": [0.0, 0.0, 3000000.0, 4000000.0]},
         {"name": "east", "rect_cm": [2800000.0, 0.0, 5800000.0, 4000000.0]},
         {"name": "far_gasquet", "rect_cm": [-46000000.0, -84000000.0, -45400000.0, -83400000.0]}]
    y = 2000000.0
    # deep in west -> east spawns at < 8 km (X > 2.0e6), not before
    out = line(500000.0, 4500000.0, y)
    ev = drive(out, m)
    assert ev == [("spawn", "east")], ev
    first = next(i for i, p in enumerate(out) if rect_dist(*p, m[1]["rect_cm"]) < SPAWN_CM)
    assert out[first][0] > 2000000.0 and drive(out[:first], m) == []
    # ... and back: destroyed only past 16 km (X < 1.2e6)
    ev = drive(out + out[::-1], m)
    assert ev == [("spawn", "east"), ("destroy", "east")], ev
    # lingering at 8 km and 16 km: one event each, no thrash
    wobble = [(x + dx, y) for x in (2000000.0, 1200000.0) for _ in range(200) for dx in (-1000.0, 1000.0)]
    ev = drive(wobble, m)
    assert ev == [("spawn", "east"), ("destroy", "east")], ev
    # teleport to far_gasquet: it spawns, east goes, home west stays
    ev = drive(out + [(-45700000.0, -83700000.0)] * 5 + [(500000.0, y)] * 5, m)
    assert ev == [("spawn", "east"), ("destroy", "east"), ("spawn", "far_gasquet"),
                  ("destroy", "far_gasquet")], ev
    assert all(name != "west" for _, name in ev)
    # home pinned from the other side too: east as home never destroys itself
    ev = drive([(-45700000.0, -83700000.0)], m, home="east")
    assert ev == [("spawn", "far_gasquet")], ev

    # (c) whole-area sidecars each manager loads at BeginPlay
    #     (ODTerrainManager, ODForestManager, ODRoadManager readers)
    manifest = build_manifest()
    for areas in manifest.values():
        for a in (a["name"] for a in areas):
            for f in (f"roadstations_{a}.bin", f"biome_{a}_test.bin", f"elev_{a}_test.bin",
                      f"centerline_{a}_test.bin", f"centerline_map_{a}_test.bin",
                      f"routegraph_{a}_test.bin", f"labels_{a}_test.bin",
                      f"building_centroids_{a}_test.bin"):
                assert (MASKS / f).is_file(), f
            assert any(MASKS.glob(f"road_{a}_test_tile_*.bin")), a
            assert any((MASKS.parent / "MasksV2").glob(f"roadv3_{a}_test_tile_*.bin")), a

    # (e) live streaming: each area's prefetch list holds those sidecars,
    #     its water tiles, nothing per-tile otherwise, nothing of another area
    for areas in manifest.values():
        for a in areas:
            files = a["files"]
            assert files == area_files(a["name"])
            assert f"Masks/roadstations_{a['name']}.bin" in files
            assert f"Masks/biome_{a['name']}_test.bin" in files
            assert {f"Masks/{p.name}" for p in MASKS.glob(f"water_{a['name']}_test_tile_*.bin")} <= set(files)
            assert all("_tile_" not in f or f.startswith(f"Masks/water_{a['name']}_test_tile_") for f in files)
            assert all((MASKS.parent / f).is_file() for f in files)
            others = [o["name"] for o in areas if o["name"] != a["name"]]
            assert not any(f.endswith((f"_{o}.bin", f"_{o}_test.bin")) for f in files for o in others), files

    print("ok")
