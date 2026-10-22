"""Rewrites an area's ODT1 terrain tiles (near + coarse) and horizon as
ODT2 in place, no rebake (2026-10-21). Positions and triangles are copied
bit for bit; the UV frame is fitted from each file's own x/uv extremes
(rounded to the cm, exact for tiles) and the writer keeps ODT1 for any file
whose stored UVs don't derive from it. Usage: OD_AREA=<area> python convert_terrain_odt2.py
"""
import os
from pathlib import Path

import numpy as np

from bin_tile_io import parse_terrain_tile_bin, write_terrain_tile_bin

MASKS_DIR = Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def main():
    area = os.environ["OD_AREA"]
    before = after = n = 0
    paths = [*MASKS_DIR.glob(f"terrain*_{area}_test_tile_*.bin"), MASKS_DIR / f"horizon_{area}_test.bin"]
    for p in sorted(paths):
        if not p.exists() or p.read_bytes()[:4] != b"ODT1":
            continue
        t = parse_terrain_tile_bin(p)
        v = np.array(t["verts"], "<f4").reshape(-1, 3)
        uv = np.array(t["uvs"], np.float32).reshape(-1, 2)
        if len(v) == 0:
            continue
        i, j = uv[:, 0].argmin(), uv[:, 0].argmax()
        size = round(float(v[j, 0] - v[i, 0]) / float(uv[j, 0] - uv[i, 0]))
        u0 = round(float(v[i, 0]) - float(uv[i, 0]) * size)
        k = uv[:, 1].argmin()
        v0 = round(float(v[k, 1]) - float(uv[k, 1]) * size)
        tile = {"has_collision": t["has_collision"], "verts": v, "uvs": uv,
                "tris": np.array(t["tris"], np.uint32), "uv_frame": (u0, v0, size)}
        before += p.stat().st_size
        tmp = p.with_suffix(".tmp")
        write_terrain_tile_bin(tmp, tile, t["generation_id"].encode("ascii"))
        u = parse_terrain_tile_bin(tmp)
        assert u["verts"] == t["verts"] and u["tris"] == t["tris"], p.name
        tmp.replace(p)
        after += p.stat().st_size
        n += 1
    print(f"convert_terrain_odt2: {n} tiles, {before / 2**20:.1f} MB -> {after / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
