"""Offline gate (2026-10-22): the shipped horizon mesh (horizon_<area>_test.bin,
no collision) stays under every shipped terrain tile, both ways round: no
tile vertex under the horizon surface, no horizon vertex over a tile's
surface. At Deals Gap's Calderwood the zoom-10 horizon stood up to 139 m over
the gorge tiles - a flat slab the car drove through (build_horizon.py).

Usage:  OD_AREA=<area> python pipeline/check_horizon_under_tiles.py [masks_dir]
Exit 1 on any vertex over, listing the worst.
"""
import glob
import sys
from pathlib import Path

import numpy as np
from matplotlib.tri import LinearTriInterpolator, Triangulation

from bin_tile_io import parse_terrain_tile_bin
from test_areas import AREA_NAME

MASKS = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def mesh(path):
    t = parse_terrain_tile_bin(path)
    v = np.asarray(t["verts"], np.float64).reshape(-1, 3) / 100.0   # m
    tri = np.asarray(t["tris"], np.int64).reshape(-1, 3)
    (x0, y0), (x1, y1), (x2, y2) = (v[tri[:, k], :2].T for k in range(3))
    area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    return v, tri[np.abs(area) > 1e-4]   # seam skirts are vertical: no surface in plan


def surface(v, tri):
    return LinearTriInterpolator(Triangulation(v[:, 0], v[:, 1], tri), v[:, 2])


def main():
    hv, htri = mesh(MASKS / f"horizon_{AREA_NAME}_test.bin")
    hz = surface(hv, htri)
    paths = glob.glob(str(MASKS / f"terrain_{AREA_NAME}_test_tile_*.bin")) + \
        glob.glob(str(MASKS / f"terrain_coarse_{AREA_NAME}_test_tile_*.bin"))
    bad, n_tile, n_hor = [], 0, 0
    for p in paths:
        v, tri = mesh(p)
        over = np.ma.filled(hz(v[:, 0], v[:, 1]), -np.inf) - v[:, 2]   # tile verts under the horizon
        lo, hi = v[:, :2].min(0), v[:, :2].max(0)
        h = hv[((hv[:, :2] >= lo) & (hv[:, :2] <= hi)).all(1)]
        over_h = np.ma.filled(surface(v, tri)(h[:, 0], h[:, 1]), np.inf) - h[:, 2]   # horizon verts over the tile
        n_tile, n_hor = n_tile + len(v), n_hor + len(h)
        bad += [(o, x, y, "tile vert") for o, (x, y) in zip(over[over > 0], v[over > 0, :2])]
        bad += [(-o, x, y, "horizon vert") for o, (x, y) in zip(over_h[over_h < 0], h[over_h < 0, :2])]
    print(f"{AREA_NAME}: {len(paths)} tiles, {n_tile} tile verts, {n_hor} horizon verts over tiles: "
          f"{len(bad)} with the horizon on top")
    for o, x, y, kind in sorted(bad, reverse=True)[:10]:
        print(f"  horizon {o:6.1f} m over the tile at UE ({x * 100:.0f}, {y * 100:.0f}) cm ({kind})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
