"""Offline check of the ODL1 landmark tiles (pipeline/build_landmarks.py),
parsed the way AODBuildingManager's MergeLandmarkTile reads them, against
the stage's landmarks.json summary. Asserts:
  - magic ODL1, generation id = the summary's, no trailing/short bytes,
    one seed byte per embedded building
  - every embedded mesh: triangle indices < verts, n_solid <= verts, no
    zero-area triangle, wall_set < 7, facade < 5, roof_set < 4
  - every summary landmark: exactly one mesh with its vertex-mean centroid
    inside its footprint, in the tile named by that centroid; its footprint
    ring in that tile; height within 0.1 m of the recipe (pitched roofs add
    up to 6 m); the seed byte picks the recipe's paint
and reports how many procedural buildings each footprint replaces at
runtime (same vertex-mean centroid test, over the buildings_ tiles).

Usage:  python pipeline/checks/verify_landmarks.py [area] [--masks DIR] [--buildings DIR] [--summary FILE]
        (defaults: test_areas.AREA_NAME, this checkout's Masks and landmarks.json)
Exits non-zero on any failure.
"""
import argparse
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from building_roofs import read_tile  # noqa: E402
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM  # noqa: E402

MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def read_landmark_tile(path):
    data = path.read_bytes()
    assert data[:4] == b"ODL1", f"{path.name}: magic {data[:4]!r}"
    gen = data[4:20].decode("ascii")
    off = 20
    (n_rings,) = struct.unpack_from("<I", data, off)
    off += 4
    rings = []
    for _ in range(n_rings):
        (n,) = struct.unpack_from("<I", data, off)
        xy = struct.unpack_from(f"<{2 * n}f", data, off + 4)
        rings.append(list(zip(xy[0::2], xy[1::2])))
        off += 4 + 8 * n
    (n_seeds,) = struct.unpack_from("<I", data, off)
    seeds = data[off + 4:off + 4 + n_seeds]
    blob = data[off + 4 + n_seeds:]
    assert blob[4:20].decode("ascii") == gen, f"{path.name}: embedded ODU4 generation id differs"
    buildings = read_tile(blob)
    assert len(buildings) == n_seeds, f"{path.name}: {n_seeds} seeds for {len(buildings)} buildings"
    return gen, rings, list(seeds), buildings


def inside(ring, x, y):
    c = False
    for (x0, y0), (x1, y1) in zip(ring, ring[-1:] + ring[:-1]):
        if (y0 > y) != (y1 > y) and x < x1 + (y - y1) * (x0 - x1) / (y0 - y1):
            c = not c
    return c


def centroid(verts):
    return sum(v[0] for v in verts) / len(verts), sum(v[1] for v in verts) / len(verts)


def paint_of(seed):
    s = (seed / 255 * 17.31 + 0.71) % 1.0
    return None if s < 0.3 else int((s - 0.3) / 0.7 * 5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("area", nargs="?", default=AREA_NAME)
    ap.add_argument("--masks", type=Path, default=MASKS)
    ap.add_argument("--buildings", type=Path, default=MASKS)
    ap.add_argument("--summary", type=Path)
    a = ap.parse_args()
    summary = json.loads((a.summary or ROOT / "data" / "road_tiles" / f"{a.area}_test" / "landmarks.json").read_text())
    fails = []

    tiles = {}
    for p in sorted(a.masks.glob(f"landmarks_{a.area}_test_tile_*.bin")):
        tx, ty = map(int, p.stem.rsplit("_", 2)[1:])
        gen, rings, seeds, buildings = read_landmark_tile(p)
        if gen != summary["generation_id"]:
            fails.append(f"{p.name}: generation id {gen} != summary {summary['generation_id']}")
        for b in buildings:
            nv = len(b["verts"])
            if b["n_solid"] > nv or b["wall_set"] >= 7 or b["facade"] >= 5 or b["roof_set"] >= 4:
                fails.append(f"{p.name}: bad header bytes {b['wall_set'], b['facade'], b['roof_set'], b['n_solid'], nv}")
            for t in b["tris"]:
                if max(t) >= nv:
                    fails.append(f"{p.name}: triangle index {max(t)} >= {nv} verts")
                    break
                (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (b["verts"][i] for i in t)
                cr = ((by - ay) * (cz - az) - (bz - az) * (cy - ay), (bz - az) * (cx - ax) - (bx - ax) * (cz - az),
                      (bx - ax) * (cy - ay) - (by - ay) * (cx - ax))
                if math.hypot(*cr) < 1e-3:
                    fails.append(f"{p.name}: zero-area triangle {t}")
                    break
        tiles[tx, ty] = (rings, seeds, buildings)
    print(f"{a.area}: {len(tiles)} landmark tile(s), {sum(len(t[2]) for t in tiles.values())} mesh(es)")

    procedural = []   # vertex-mean centroid of every procedural building
    for p in a.buildings.glob(f"buildings_{a.area}_test_tile_*.bin"):
        procedural.extend(centroid(b["verts"]) for b in read_tile(p) if b["verts"])

    for lm in summary["landmarks"]:
        ring = [tuple(p) for p in lm["footprint"]]
        hits = [(k, i, b) for k, (_, _, bs) in tiles.items() for i, b in enumerate(bs) if inside(ring, *centroid(b["verts"]))]
        if len(hits) != 1:
            fails.append(f"{lm['name']}: {len(hits)} meshes inside its footprint (want 1)")
            continue
        key, i, b = hits[0]
        cx, cy = centroid(b["verts"])
        if key != (int(cx // TERRAIN_TILE_SIZE_CM), int(cy // TERRAIN_TILE_SIZE_CM)):
            fails.append(f"{lm['name']}: mesh in tile {key}, centroid says otherwise")
        if not any(len(r) == len(ring) and all(math.dist(p, q) < 1 for p, q in zip(r, ring)) for r in tiles[key][0]):
            fails.append(f"{lm['name']}: footprint ring missing from tile {key}")
        zs = [v[2] for v in b["verts"]]
        h = (max(zs) - min(zs)) / 100
        slack = 6.0 if lm["roof"] != "flat" else 0.1
        if not lm["expected_h_m"] - 0.1 <= h <= lm["expected_h_m"] + slack:
            fails.append(f"{lm['name']}: height {h:.2f} m, recipe {lm['expected_h_m']} m")
        seed = tiles[key][1][i]
        if seed != 255 and paint_of(seed) != lm["paint"]:
            fails.append(f"{lm['name']}: seed {seed} paints {paint_of(seed)}, recipe {lm['paint']}")
        replaced = sum(inside(ring, x, y) for x, y in procedural)
        print(f"  {lm['name']}: {h:.1f} m, roof {lm['roof']}, seed {seed}, tile {key}, "
              f"replaces {replaced} procedural building(s)")

    for f in fails:
        print("FAIL", f)
    print("OK" if not fails else f"{len(fails)} failure(s)")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
