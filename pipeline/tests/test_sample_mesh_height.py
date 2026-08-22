"""Hand-computed fixture tests for road_mesh_common.sample_mesh_height
(AUDIT-RELIABILITY.md SS4.3/SS4.4, added 2026-08-21).

sample_mesh_height has no baseline anywhere in the repo to check it against
(its own docstring: "NOT guaranteed byte-identical" to the lost original) -
these are small, hand-built synthetic tiles (not real baked data) with
manually computed expected heights, specifically covering:
  1. a flat patch (sanity: any two triangles covering the same height agree)
  2. a sloped ramp (barycentric interpolation is actually happening, not
     just returning a vertex's raw Z)
  3. overlapping/duplicate geometry at two different heights - the exact
     case AUDIT-RELIABILITY.md SS4.3 flagged: the function used to return
     the FIRST triangle numpy's np.where() happened to return (order-
     arbitrary), not the topmost. Fixed 2026-08-21 to return the topmost
     containing triangle - test 3 below pins that behavior down so it can't
     silently regress.
  4. a query point outside every triangle returns None (miss case)

Run:  python -m pytest pipeline/tests/test_sample_mesh_height.py -v
  (or, no pytest available: python pipeline/tests/test_sample_mesh_height.py)
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from road_mesh_common import sample_mesh_height


def test_flat_patch():
    # A single flat quad (two triangles), Z=100 everywhere, spanning
    # (0,0)-(100,100). Sampling anywhere inside must read exactly 100,
    # regardless of which of the two triangles the point falls in.
    positions = np.array([
        [0.0, 0.0, 100.0],
        [100.0, 0.0, 100.0],
        [100.0, 100.0, 100.0],
        [0.0, 100.0, 100.0],
    ])
    triangles = np.array([
        [0, 1, 2],
        [0, 2, 3],
    ])

    # centroid, well inside triangle 0 (0,1,2)
    h = sample_mesh_height(positions, triangles, 60.0, 20.0)
    assert h is not None and abs(h - 100.0) < 1e-6, h

    # well inside triangle 1 (0,2,3)
    h = sample_mesh_height(positions, triangles, 20.0, 60.0)
    assert h is not None and abs(h - 100.0) < 1e-6, h


def test_sloped_ramp():
    # Single triangle ramp: A=(0,0,z=0), B=(100,0,z=0), C=(0,100,z=200).
    # Sampling at the centroid ((A+B+C)/3 in xy) has hand-computable
    # barycentric weights of exactly 1/3 each (query point == mesh centroid
    # for a single triangle), so expected z = (0 + 0 + 200) / 3.
    positions = np.array([
        [0.0, 0.0, 0.0],
        [100.0, 0.0, 0.0],
        [0.0, 100.0, 200.0],
    ])
    triangles = np.array([[0, 1, 2]])

    cx = (0.0 + 100.0 + 0.0) / 3.0
    cy = (0.0 + 0.0 + 100.0) / 3.0
    expected_z = (0.0 + 0.0 + 200.0) / 3.0

    h = sample_mesh_height(positions, triangles, cx, cy)
    assert h is not None and abs(h - expected_z) < 1e-4, (h, expected_z)

    # A second hand-computable point: the midpoint of edge B-C. Barycentric
    # weights there are (0, 0.5, 0.5) by construction (on the B-C edge,
    # zero weight on A), so expected z = 0.5*0 + 0.5*200 = 100.
    mx = (100.0 + 0.0) / 2.0
    my = (0.0 + 100.0) / 2.0
    h_mid = sample_mesh_height(positions, triangles, mx, my)
    assert h_mid is not None and abs(h_mid - 100.0) < 1e-3, h_mid


def test_overlapping_geometry_returns_topmost():
    # Two flat triangle-pairs whose XY footprints overlap entirely:
    # a "lower" pair at Z=50 covering (0,0)-(100,100), and an "upper" pair
    # at Z=150 covering the same square exactly - mimics the real case this
    # was written for (a skirt/wall/bridge triangle sitting in plan-view
    # under or over the surface that actually matters). A query point in
    # the overlap must return the TOPMOST (150), not whichever triangle
    # happens to come first in the array (the pre-fix bug).
    lower = np.array([
        [0.0, 0.0, 50.0],
        [100.0, 0.0, 50.0],
        [100.0, 100.0, 50.0],
        [0.0, 100.0, 50.0],
    ])
    upper = np.array([
        [0.0, 0.0, 150.0],
        [100.0, 0.0, 150.0],
        [100.0, 100.0, 150.0],
        [0.0, 100.0, 150.0],
    ])

    # Lower triangles listed FIRST in the array - under the old "return the
    # first containing triangle" behavior this would incorrectly return 50.
    positions = np.concatenate([lower, upper], axis=0)
    triangles = np.array([
        [0, 1, 2], [0, 2, 3],       # lower pair, indices 0-3
        [4, 5, 6], [4, 6, 7],       # upper pair, indices 4-7
    ])

    h = sample_mesh_height(positions, triangles, 50.0, 50.0)
    assert h is not None and abs(h - 150.0) < 1e-6, (
        f"expected topmost triangle's height 150.0, got {h} - "
        "sample_mesh_height regressed to first-hit behavior"
    )

    # Reversed array order (upper triangles listed first) must give the
    # SAME answer - proves the result depends on geometry, not array order.
    positions_rev = np.concatenate([upper, lower], axis=0)
    triangles_rev = np.array([
        [0, 1, 2], [0, 2, 3],       # upper pair now first, indices 0-3
        [4, 5, 6], [4, 6, 7],       # lower pair, indices 4-7
    ])
    h_rev = sample_mesh_height(positions_rev, triangles_rev, 50.0, 50.0)
    assert h_rev is not None and abs(h_rev - 150.0) < 1e-6, h_rev


def test_outside_every_triangle_returns_none():
    positions = np.array([
        [0.0, 0.0, 100.0],
        [100.0, 0.0, 100.0],
        [100.0, 100.0, 100.0],
        [0.0, 100.0, 100.0],
    ])
    triangles = np.array([
        [0, 1, 2],
        [0, 2, 3],
    ])
    h = sample_mesh_height(positions, triangles, 500.0, 500.0)
    assert h is None, h


if __name__ == "__main__":
    test_flat_patch()
    test_sloped_ramp()
    test_overlapping_geometry_returns_topmost()
    test_outside_every_triangle_returns_none()
    print("all sample_mesh_height fixture tests passed")
