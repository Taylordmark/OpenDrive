"""Horizon mesh (build_horizon.py, realism eval T4) on a synthetic DEM: never
above the true ground inside the baked extent (so it can't poke through a
streamed tile), drops for earth curvature outside it, and leaves open sea out.

Run:  OD_AREA=oahu python pipeline/tests/test_horizon.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_horizon as H
from build_terrain_mesh_tiles import road_ue_z_m
from test_areas import ORIGIN_HEIGHT_M


def ground(e, n):   # ridges inland (east < 40 km), sea beyond
    return np.where(e < 40000.0, 300.0 + 200.0 * np.sin(e / 3000.0) * np.cos(n / 5000.0), -50.0)


def test_horizon():
    H.HORIZON_N, H.HORIZON_SUPERSAMPLE, H.HORIZON_MARGIN_M = 129, 4, 30000.0
    ext = (0.0, 0.0, 20000.0, 20000.0)
    tile, st = H.build(ext, ground)
    v = tile["verts"].astype(np.float64) / 100.0
    e, n = v[:, 0], -v[:, 1]
    z_true = road_ue_z_m(ground(e, n), 0.0, ORIGIN_HEIGHT_M)
    inside = H.outside_dist_m(e, n, ext) == 0
    assert inside.any() and (v[inside, 2] <= z_true[inside] - H.HORIZON_SINK_M + 1e-3).all()
    # every triangle's surface stays under the ground inside: check centroids too
    t = tile["tris"].astype(np.int64)
    c = v[t].mean(1); ci = H.outside_dist_m(c[:, 0], -c[:, 1], ext) == 0
    assert (c[ci, 2] < road_ue_z_m(ground(c[ci, 0], -c[ci, 1]), 0.0, ORIGIN_HEIGHT_M)).all()
    # curvature: a vertex 25+ km out sits ~d^2/2R (49+ m) under the ground mean
    far = (H.outside_dist_m(e, n, ext) > 25000) & (e < 30000)
    assert far.any() and (z_true[far] - v[far, 2] > 40.0).all()
    # open sea is dropped: no triangle lies wholly past the coast
    assert (c[:, 0] < 40000.0 + st["cell_m"]).all()


def test_horizon_under_tiles():
    """(2026-10-22) the DEM it samples misses a gorge the tiles have
    (Calderwood: zoom 10 is 40-170 m over the floor): it must sink to the tiles."""
    H.HORIZON_N, H.HORIZON_SUPERSAMPLE, H.HORIZON_MARGIN_M = 129, 4, 30000.0
    ext = (0.0, 0.0, 20000.0, 20000.0)
    e, n = np.meshgrid(np.arange(0.0, 20000.0, 50.0), np.arange(0.0, 20000.0, 50.0))
    gorge = np.abs(e - 10000.0) < 100.0
    z_tile = road_ue_z_m(ground(e, n) - 150.0 * gorge, 0.0, ORIGIN_HEIGHT_M)
    tv = np.c_[e.ravel() * 100, -n.ravel() * 100, z_tile.ravel() * 100].astype(np.float32)
    from matplotlib.tri import LinearTriInterpolator, Triangulation

    def over_tiles(tile):   # horizon surface minus tile Z at every tile vertex (m)
        v = tile["verts"].astype(np.float64) / 100.0
        tri = Triangulation(v[:, 0], v[:, 1], tile["tris"].astype(np.int64))
        return LinearTriInterpolator(tri, v[:, 2])(tv[:, 0] / 100, tv[:, 1] / 100) - tv[:, 2] / 100

    assert over_tiles(H.build(ext, ground)[0]).max() > 0     # DEM alone pokes through
    assert over_tiles(H.build(ext, ground, tv)[0]).max() < 0


if __name__ == "__main__":
    test_horizon()
    test_horizon_under_tiles()
    print("ok")
