"""Coastline stitching on a synthetic broken coastline (PLAN-REALISM Phase 4).

A 10 x 10 km bbox: a mainland coast running west->east along y = 3 km (land
on the left = north), split into scrambled ways, plus a 2 x 2 km island
drawn counter-clockwise whose ring is missing one way (a 1 km data gap).

Run:  python pipeline/tests/test_coastline.py
"""

import sys
from pathlib import Path

import numpy as np
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_water_tiles import close_coast_gaps, land_polygon, mesh_from_polygon, stitch_coastline


def test_broken_coastline():
    xy = {}
    # mainland: nodes 1..6 west -> east (starts/ends outside the bbox)
    for i, x in enumerate([-1000, 2000, 4000, 6000, 8000, 11000], start=1):
        xy[i] = (x, 3000.0)
    # island at sea, ccw: 11 (6,.5) 12 (8,.5) 13 (8,2.5) 14 (6,2.5) 15 (6,1.5);
    # the way 15->11 is missing
    xy.update({11: (6000, 500), 12: (8000, 500), 13: (8000, 2500), 14: (6000, 2500),
               15: (6000, 1500)})
    ways = [[3, 4, 5], [1, 2, 3], [5, 6], [11, 12, 13], [13, 14, 15]]
    chains = stitch_coastline(ways)
    assert sorted(len(c) for c in chains) == [5, 6], chains   # two open chains
    box = shapely.box(0, 0, 10000, 10000)
    chains_xy, n_dropped = close_coast_gaps([np.array([xy[n] for n in c], float) for c in chains], box)
    assert n_dropped == 0
    assert sum(np.allclose(c[0], c[-1]) for c in chains_xy) == 1   # island closed over the gap
    land = land_polygon(chains_xy, box)
    # mainland north of y = 3 km (70 km^2) plus the 4 km^2 island
    assert abs(land.area - 74e6) < 1e3, land.area
    assert land.contains(shapely.Point(7000, 1500)) and not land.contains(shapely.Point(3000, 1500))


def test_island_in_sea_and_lake_hole():
    # coast running EAST->WEST along y = 7 km: land on the left = south.
    # An island at sea north of it, and the sea mesh must leave it a hole.
    ways = [[1, 2], [2, 3]]
    xy = {1: (11000, 7000), 2: (5000, 7000), 3: (-1000, 7000),
          11: (4000, 8000), 12: (6000, 8000), 13: (6000, 9000), 14: (4000, 9000)}
    ways.append([11, 12, 13, 14, 11])
    box = shapely.box(0, 0, 10000, 10000)
    chains_xy, _ = close_coast_gaps(
        [np.array([xy[n] for n in c], float) for c in stitch_coastline(ways)], box)
    land = land_polygon(chains_xy, box)
    assert abs(land.area - (70e6 + 2e6)) < 1e3, land.area
    sea = box.difference(land)
    verts, uvs, tris = mesh_from_polygon(sea, 0.0)
    v = verts.astype(float)
    area = 0.5 * np.abs(np.cross(v[tris[:, 1]] - v[tris[:, 0]], v[tris[:, 2]] - v[tris[:, 0]])[:, 2]).sum()
    assert abs(area / 1e4 - 28e6) < 1e3, area / 1e4      # cm^2 -> m^2
    # faces up under UE's winding rule
    assert (np.cross(v[tris[:, 2]] - v[tris[:, 0]], v[tris[:, 1]] - v[tris[:, 0]])[:, 2] > 0).all()
    # the island centre (5 km, 8.5 km) -> UE cm (500000, -850000) is not covered
    p = np.array([500000.0, -850000.0])
    a, b, c = v[tris[:, 0], :2], v[tris[:, 1], :2], v[tris[:, 2], :2]
    def s(p1, p2): return (p[0] - p2[:, 0]) * (p1[:, 1] - p2[:, 1]) - (p1[:, 0] - p2[:, 0]) * (p[1] - p2[:, 1])
    d1, d2, d3 = s(a, b), s(b, c), s(c, a)
    covered = ~(((d1 < 0) | (d2 < 0) | (d3 < 0)) & ((d1 > 0) | (d2 > 0) | (d3 > 0)))
    assert not covered.any()


if __name__ == "__main__":
    test_broken_coastline()
    test_island_in_sea_and_lake_hole()
    print("ok")
