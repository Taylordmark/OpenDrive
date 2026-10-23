"""Very-far horizon mesh (realism eval T4, 2026-10-15).

Terrain tiles are baked only within RENDER_DISTANCE_M (6 km) of a road and
streamed within AODTerrainManager::VisualRadius, so past ~6 km the world
just ended. This writes ONE low-res ODT2 mesh, horizon_<area>_test.bin,
covering the baked tile extent plus HORIZON_MARGIN_M (80 km) on every side:
a 513x513 grid (~0.3-0.5 km) of zoom-10 Terrarium, RTIN-simplified, that
AODTerrainManager::LoadHorizon draws once (no collision, no shadow) under
the streamed tiles.

It must never poke through a loaded tile, so inside the baked extent each
vertex takes the MIN over its footprint - of the DEM and of every baked
tile vertex within 1.5 cells, since zoom 10 misses narrow valley floors by
up to 170 m (Calderwood, 2026-10-22) - and the whole mesh sits
HORIZON_SINK_M (> the RTIN error) below that; outside it (never covered by
tiles) the footprint MEAN keeps ridgelines honest. Earth curvature drops it
d^2/2R past the baked extent (500 m at 80 km), and open sea (all three
corners at or below sea level) is left out, as there is no sea plane there.

Usage:  OD_AREA=<area> python build_horizon.py
"""
import glob
import math
import re

import numpy as np
import requests

import build_terrain_mesh_tiles as B
from bake_job import outline_local
from bin_tile_io import parse_terrain_tile_bin
from generation_id import compute_generation_id, pack_generation_id
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, to_lonlat

HORIZON_MARGIN_M = 80000.0
HORIZON_N = 513            # martini grid (2^k + 1)
HORIZON_SUPERSAMPLE = 4    # fine samples per output cell (footprint min/mean)
HORIZON_ZOOM = 10          # ~120-150 m Terrarium pixels
HORIZON_MAX_ERROR_M = 10.0
HORIZON_SINK_M = 15.0      # > HORIZON_MAX_ERROR_M: stays under every streamed tile
EARTH_R_M = 6371000.0
OUT_PATH = B.OUT_DIR / f"horizon_{AREA_NAME}_test.bin"
def baked_extent_m():
    """(e0, n0, e1, n1) of every terrain tile on disk for this area (metres)."""
    # (2026-10-21) a drawn area: its outline's extent - pass 1 builds the
    # horizon before any terrain tile exists (scoping round 6)
    if outline_local() is not None:
        return outline_local().bounds
    keys = [tuple(map(int, re.search(r"_tile_(-?\d+)_(-?\d+)\.bin$", p).groups()))
            for p in glob.glob(str(B.OUT_DIR / f"terrain_{AREA_NAME}_test_tile_*.bin"))]
    if not keys:
        raise SystemExit(f"no terrain_{AREA_NAME}_test tiles - bake terrain first")
    t = np.array(keys, dtype=np.float64) * TERRAIN_TILE_SIZE_CM / 100.0
    # tile (tx, ty) spans x [tx, tx+1) and UE y [ty, ty+1) -> north = -y
    return t[:, 0].min(), -(t[:, 1].max() + TERRAIN_TILE_SIZE_CM / 100.0), \
        t[:, 0].max() + TERRAIN_TILE_SIZE_CM / 100.0, -t[:, 1].min()


def outside_dist_m(e, n, ext):
    e0, n0, e1, n1 = ext
    return np.hypot(np.maximum(0, np.maximum(e0 - e, e - e1)), np.maximum(0, np.maximum(n0 - n, n - n1)))


def tile_verts_cm():
    """(N, 3) UE cm of every baked terrain tile vertex (near and coarse twins,
    skirts included), or None before any tile exists (a drawn area's pass 1)."""
    paths = glob.glob(str(B.OUT_DIR / f"terrain_{AREA_NAME}_test_tile_*.bin")) +         glob.glob(str(B.OUT_DIR / f"terrain_coarse_{AREA_NAME}_test_tile_*.bin"))
    vs = [np.asarray(t["verts"], np.float32).reshape(-1, 3) for t in map(parse_terrain_tile_bin, paths) if t]
    return np.concatenate(vs) if vs else None


def build(ext, elev_fn, tile_verts=None):
    """elev_fn(east_m, north_m) -> metres MSL; tile_verts: tile_verts_cm().
    Returns the terrain tile dict and stats."""
    e0, n0, e1, n1 = ext
    ce, cn = (e0 + e1) / 2, (n0 + n1) / 2
    half = max(e1 - e0, n1 - n0) / 2 + HORIZON_MARGIN_M
    n = HORIZON_N
    fine_n = (n - 1) * HORIZON_SUPERSAMPLE + 1
    fe, fn_ = np.meshgrid(np.linspace(ce - half, ce + half, fine_n), np.linspace(cn + half, cn - half, fine_n))
    fine = elev_fn(fe, fn_)
    mn = B.downsample_min_grid(fine, n, HORIZON_SUPERSAMPLE)
    mean = B.downsample_mean_grid(fine, n, HORIZON_SUPERSAMPLE)
    ge, gn = np.meshgrid(np.linspace(ce - half, ce + half, n), np.linspace(cn + half, cn - half, n))
    d = outside_dist_m(ge, gn, ext)
    # MIN where a streamed tile can be (plus one cell so a footprint never straddles)
    cell = 2 * half / (n - 1)
    elev = np.where(d <= cell, mn, mean)
    sea = elev <= 0.0
    z_m = B.road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M)
    if tile_verts is not None:
        # (2026-10-22) zoom-10 Terrarium stands 40-170 m over the Calderwood
        # gorge floor, so its footprint MIN poked a slab through the tiles.
        # Each vertex also takes the lowest tile vertex within 1.5 cells
        # (nearest-vertex bins, then 3x3), covering its whole one-ring.
        col = np.clip(np.rint((tile_verts[:, 0] / 100.0 - (ce - half)) / cell).astype(np.int64), 0, n - 1)
        row = np.clip(np.rint(((cn + half) + tile_verts[:, 1] / 100.0) / cell).astype(np.int64), 0, n - 1)
        low = np.full((n + 2, n + 2), np.inf)
        np.minimum.at(low, (row + 1, col + 1), tile_verts[:, 2] / 100.0)
        z_m = np.minimum(z_m, np.min([low[a:a + n, b:b + n] for a in range(3) for b in range(3)], axis=0))
    z_cm = (z_m - HORIZON_SINK_M - d * d / (2 * EARTH_R_M)) * 100.0

    tile = B._martini_for(n).create_tile((z_cm / 100.0).astype(np.float32))
    gv, tris = tile.get_mesh(HORIZON_MAX_ERROR_M)
    gv = gv.reshape(-1, 2).astype(np.int64)
    gxi, gyi = gv[:, 0], gv[:, 1]
    verts = np.stack([ge[gyi, gxi] * 100.0, -gn[gyi, gxi] * 100.0, z_cm[gyi, gxi]], -1).astype(np.float32)
    tris = tris.reshape(-1, 3).astype(np.int64)
    tris = tris[~sea[gyi[tris], gxi[tris]].all(1)]   # open sea: no sea plane out here
    tris = B.fix_up_winding(verts, tris.astype(np.uint32))
    used = np.unique(tris)
    remap = np.full(len(verts), -1, np.int64); remap[used] = np.arange(len(used))
    verts = verts[used]
    uvs = np.stack([gxi[used] / (n - 1), gyi[used] / (n - 1)], -1).astype(np.float32)
    tris = remap[tris].astype(np.uint32)
    # (2026-10-21) the grid spans x [ce-half, ce+half], UE y [-(cn+half), -(cn-half)],
    # so the UVs above derive from position and the mesh writes as ODT2
    uv_frame = ((ce - half) * 100.0, -(cn + half) * 100.0, 2 * half * 100.0)
    return ({"has_collision": False, "verts": verts, "uvs": uvs, "tris": tris, "uv_frame": uv_frame},
            dict(half_km=half / 1000, cell_m=cell, verts=len(verts), tris=len(tris)))


def main():
    ext = baked_extent_m()
    sess, cache = requests.Session(), {}

    def elev_fn(e, n):
        lon, lat = to_lonlat(e, n)
        return B.sample_dem(lat.reshape(-1), lon.reshape(-1), HORIZON_ZOOM, sess, cache).reshape(e.shape).astype(np.float64)

    tile, st = build(ext, elev_fn, tile_verts_cm())
    B.write_tile_bin(OUT_PATH, tile, pack_generation_id(compute_generation_id()))
    print(f"horizon: baked extent {(ext[2] - ext[0]) / 1000:.0f} x {(ext[3] - ext[1]) / 1000:.0f} km, "
          f"mesh +-{st['half_km']:.0f} km at {st['cell_m']:.0f} m, {st['verts']} verts, {st['tris']} tris, "
          f"{OUT_PATH.stat().st_size / 1e6:.2f} MB -> {OUT_PATH}")


if __name__ == "__main__":
    main()
