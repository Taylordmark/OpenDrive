"""Synthetic fixture tests for the cross-road conflict pass's junction
exemption (2026-09-01, Chaparral Forest Drive / seg 3563: a side road
climbing away from Elizabeth Lake Road at ~10% grade had every station
flagged by CROSS_ROAD_CONFLICT_MAX_GRADE against valley-road stations
130-150m away, flooding the whole segment into is_bridge=True carve
exclusion - see CROSS_ROAD_JUNCTION_RADIUS_M's comment in
road_mesh_common.py).

Covers:
  1. side road branching off a valley road at grade and climbing at 10% -
     the regression case - must produce NO exclusions from the cross pass
  2. same geometry but the chains never touch (side road starts 30m away
     and 5m up) - the original cross-road canyon-tear case shape - must
     still exclude
  3. junction-connected chains whose disagreement exceeds the MAIN
     (CONFLICT_MAX_GRADE) allowance are still excluded - the exemption
     only silences the tight cross-road pass, never the main pass
  4. junction_connected_chain_pairs itself: close-in-XY-far-in-Z (a
     crossing, not a junction) does NOT connect the chains

Run:  python -m pytest pipeline/tests/test_cross_road_junction_exemption.py -v
  (or, no pytest available: python pipeline/tests/test_cross_road_junction_exemption.py)
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from road_mesh_common import (
    classify_wall_stations,
    junction_connected_chain_pairs,
)


def _flatten(chains):
    """chains: list of (east, north, elev, name) per-chain arrays ->
    the flattened parallel arrays classify_wall_stations consumes."""
    east, north, elev, sidx, stidx, names = [], [], [], [], [], []
    for k, (e, n, h, name) in enumerate(chains):
        east += list(e)
        north += list(n)
        elev += list(h)
        sidx += [k] * len(e)
        stidx += list(range(len(e)))
        names += [name] * len(e)
    return (np.array(east), np.array(north), np.array(elev),
            np.array(sidx), np.array(stidx), names)


def _valley_road(n=61, spacing=5.0, z=100.0):
    # east-west along north=0, flat
    e = np.arange(n) * spacing - (n // 2) * spacing
    return e, np.zeros(n), np.full(n, z), "Valley Road"


def _side_road(grade, n=25, spacing=5.0, z0=100.0, north0=2.0):
    # heads due north from just beside the valley road's midpoint,
    # climbing at `grade`
    nn = north0 + np.arange(n) * spacing
    return np.zeros(n), nn, z0 + np.arange(n) * spacing * grade, "Side Road"


def test_junction_side_road_not_excluded():
    # The seg 3563 shape: side road starts 2m from the valley road at the
    # same elevation and climbs at 10%. Station 20 sits ~100m from valley
    # stations up to 150m away with dz 10m+ - over the 0.05 cross-road
    # allowance, under the 0.15 main one. Must NOT be excluded.
    east, north, elev, sidx, stidx, names = _flatten(
        [_valley_road(), _side_road(0.10)])
    exclude, wall_pairs = classify_wall_stations(
        east, north, elev, sidx, stidx, names)
    assert not exclude.any(), np.nonzero(exclude)[0]
    assert not wall_pairs, wall_pairs


def test_disjoint_road_still_excluded():
    # Same climb but starting 30m away and 5m up - never meets the valley
    # road at grade, so the cross-road pass must keep flagging it (this is
    # the shape of its motivating canyon-tear case).
    east, north, elev, sidx, stidx, names = _flatten(
        [_valley_road(), _side_road(0.10, z0=105.0, north0=30.0)])
    assert junction_connected_chain_pairs(east, north, elev, sidx) == set()
    exclude, _ = classify_wall_stations(east, north, elev, sidx, stidx, names)
    assert exclude.any()


def test_main_pass_still_applies_to_junction_chains():
    # Junction-connected, but climbing at 25% - past CONFLICT_MAX_GRADE's
    # allowance well inside the conflict radius, so the MAIN pass (which
    # the exemption must never touch) still excludes. Out-of-scope pairs
    # wall regardless of grade there.
    east, north, elev, sidx, stidx, names = _flatten(
        [_valley_road(), _side_road(0.25)])
    assert (0, 1) in junction_connected_chain_pairs(east, north, elev, sidx)
    exclude, _ = classify_wall_stations(east, north, elev, sidx, stidx, names)
    assert exclude.any()


def test_crossing_is_not_a_junction():
    # Two chains 2m apart in XY but 6m apart in Z at their closest approach:
    # a grade-separated crossing, not a junction - must not connect.
    e1, n1, h1, _ = _valley_road()
    east, north, elev, sidx, stidx, names = _flatten([
        (e1, n1, h1, "Valley Road"),
        (np.zeros(25), 2.0 + np.arange(25) * 5.0, np.full(25, 106.0), "Overpass Road"),
    ])
    assert junction_connected_chain_pairs(east, north, elev, sidx) == set()


if __name__ == "__main__":
    test_junction_side_road_not_excluded()
    test_disjoint_road_still_excluded()
    test_main_pass_still_applies_to_junction_chains()
    test_crossing_is_not_a_junction()
    print("all cross-road junction exemption fixture tests passed")
