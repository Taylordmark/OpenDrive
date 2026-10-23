"""Offline check of the baked water tiles (ODL1) the game loads - no UE launch.

Parses every OpenDriveUE/Content/OpenDrive/Masks/water_<AREA>_test_tile_*.bin
with the same bounds rules as AODWaterManager's parser (ODWaterManager.cpp),
then checks the geometry the game would build a mesh from.

Run:  python pipeline/tests/test_water_tiles.py
"""

import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_terrain_mesh_tiles import GENERATION_ID_BYTES, OUT_DIR, WATER_TILE_GLOB


def parse_tile(raw, kinds=None):
    """Mirrors AODWaterManager's ODL1 parser; returns (gen_id, lakes) or raises.
    `kinds`, if a list, gets each lake's ODF1 kind (empty for a pre-trailer tile)."""
    assert raw[:4] == b"ODL1", "bad magic"
    cur = 4
    gen_id = raw[cur:cur + GENERATION_ID_BYTES]; cur += GENERATION_ID_BYTES
    (lake_count,) = struct.unpack_from("<I", raw, cur); cur += 4
    lakes = []
    for _ in range(lake_count):
        vcount, tcount = struct.unpack_from("<II", raw, cur); cur += 8
        need = vcount * 12 + vcount * 8 + tcount * 12
        assert len(raw) - cur >= need, "truncated lake (game would drop the tile)"
        verts = np.frombuffer(raw, "<f4", vcount * 3, cur).reshape(-1, 3); cur += vcount * 12
        uvs = np.frombuffer(raw, "<f4", vcount * 2, cur).reshape(-1, 2); cur += vcount * 8
        tris = np.frombuffer(raw, "<u4", tcount * 3, cur).reshape(-1, 3); cur += tcount * 12
        lakes.append((verts, uvs, tris))
    if raw[cur:cur + 4] == b"ODF1":   # flow trailer: per lake uint8 kind + vcount*(speed, white)
        cur += 4
        for verts, _, _ in lakes:
            if kinds is not None:
                kinds.append(raw[cur])
            cur += 1 + len(verts) * 8
    assert cur == len(raw), f"{len(raw) - cur} trailing bytes"
    return gen_id, lakes


def check_all():
    paths = sorted(OUT_DIR.glob(WATER_TILE_GLOB))
    assert paths, f"no water tiles matching {WATER_TILE_GLOB} in {OUT_DIR} - run build_water_tiles.py"
    gen_ids, n_lakes, n_tris, errors = set(), 0, 0, []
    for p in paths:
        try:
            kinds = []
            gen_id, lakes = parse_tile(p.read_bytes(), kinds)
        except (AssertionError, struct.error) as e:
            errors.append(f"{p.name}: {e}")
            continue
        gen_ids.add(gen_id)
        for i, (verts, uvs, tris) in enumerate(lakes):
            where = f"{p.name} lake {i}"
            if len(tris) == 0:
                errors.append(f"{where}: no triangles"); continue
            if not (np.isfinite(verts).all() and np.isfinite(uvs).all()):
                errors.append(f"{where}: non-finite verts/uvs"); continue
            if tris.max() >= len(verts):
                errors.append(f"{where}: tri index {tris.max()} >= {len(verts)} verts"); continue
            v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
            nz = np.cross(v2 - v0, v1 - v0)[:, 2]  # same winding convention build_lake_mesh() enforces
            if (i >= len(kinds) or kinds[i] < 3) and (nz < 0).any():   # falls/mist (3, 4) stand upright
                errors.append(f"{where}: {(nz < 0).sum()}/{len(tris)} tris face down (invisible from above)")
            n_lakes += 1
            n_tris += len(tris)
    if len(gen_ids) > 1:
        errors.append(f"{len(gen_ids)} different generation ids across tiles (stale tiles mixed in)")
    print(f"{len(paths)} tiles, {n_lakes} water bodies, {n_tris} tris, {len(errors)} problem(s)")
    for e in errors[:50]:
        print("  " + e)
    return errors


def test_water_tiles():
    assert not check_all()


if __name__ == "__main__":
    sys.exit(1 if check_all() else 0)
