"""Offline round-trip check for the ODT2 terrain tile format (2026-10-21):
bin_tile_io.write_terrain_tile_bin -> parse_terrain_tile_bin keeps positions
and triangles bit for bit and UVs within 1e-4, for the uint16 (V <= 65536)
and uint32 index paths, and a tile whose UVs don't derive still writes (and
reads back) as legacy ODT1. Temp dir only, no network.

Usage:  python pipeline/test_odt2.py
"""
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bin_tile_io import parse_terrain_tile_bin, write_terrain_tile_bin

GEN = b"0123456789abcdef"
SIZE = 100000.0   # 1 km tile, cm


def fake_tile(n, tx=-3, ty=5):
    """n x n grid over tile (tx, ty) the way build_tile() lays one out."""
    x0, y0 = tx * SIZE, ty * SIZE
    gxi, gyi = (a.reshape(-1) for a in np.meshgrid(np.arange(n), np.arange(n)))
    rng = np.random.default_rng(n)
    verts = np.stack([x0 + gxi * SIZE / (n - 1), y0 + gyi * SIZE / (n - 1),
                      rng.uniform(-5e4, 5e4, n * n)], -1).astype(np.float32)
    uvs = np.stack([gxi / (n - 1), gyi / (n - 1)], -1).astype(np.float32)
    q = (gyi[:-n] * n + gxi[:-n])[gxi[:-n] < n - 1]
    tris = np.concatenate([np.stack([q, q + n, q + 1], -1), np.stack([q + 1, q + n, q + n + 1], -1)]).astype(np.uint32)
    return {"has_collision": True, "verts": verts, "uvs": uvs, "tris": tris, "uv_frame": (x0, y0, SIZE)}


def round_trip(tile, path):
    write_terrain_tile_bin(path, tile, GEN)
    t = parse_terrain_tile_bin(path)
    assert t is not None and t["generation_id"] == GEN.decode() and t["has_collision"] is True
    assert np.array_equal(np.asarray(t["verts"], np.float32).view(np.uint32), tile["verts"].reshape(-1).view(np.uint32))
    assert np.array_equal(np.asarray(t["tris"], np.uint32), tile["tris"].reshape(-1))
    assert np.abs(np.asarray(t["uvs"], np.float32) - tile["uvs"].reshape(-1)).max() <= 1e-4
    return path.read_bytes()[:4], path.read_bytes()[21] & 1   # magic, uint16 flag (ODT2)


with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    small, big = fake_tile(17), fake_tile(300)
    assert len(big["verts"]) > 65536
    assert round_trip(small, d / "small.bin") == (b"ODT2", 1)
    assert round_trip(big, d / "big.bin") == (b"ODT2", 0)
    # UVs that don't derive from position -> ODT1, read back exactly
    odd = dict(small, uvs=small["uvs"][::-1].copy())
    assert round_trip(odd, d / "odd.bin")[0] == b"ODT1"
    assert np.array_equal(np.asarray(parse_terrain_tile_bin(d / "odd.bin")["uvs"], np.float32), odd["uvs"].reshape(-1))
    assert round_trip(dict(small, uv_frame=None), d / "noframe.bin")[0] == b"ODT1"
    assert parse_terrain_tile_bin(d / "missing.bin") is None
    print(f"test_odt2: OK (ODT2 {(d / 'big.bin').stat().st_size} B vs ODT1 "
          f"{4 + 16 + 9 + len(big['verts']) * 20 + len(big['tris']) * 12} B for {len(big['verts'])} verts)")
