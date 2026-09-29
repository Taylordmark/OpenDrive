"""road_mesh_common.follow_ground_profile (2026-09-28): a sustained climb
steeper than the class cap must be TRACKED, not undercut - the old
grade_smooth_elevation() envelope sank I-70 up to 45 m under Vail Pass.

Run:  python pipeline/tests/test_follow_ground_profile.py"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from road_mesh_common import (STATION_SPACING, follow_ground_profile, grade_smooth_elevation,
                              junction_agreement_pass)


def test_tracks_climb_steeper_than_cap():
    x = np.arange(600) * STATION_SPACING           # 3 km at 8% vs a 6% cap
    ground = 0.08 * x
    old = grade_smooth_elevation(ground, STATION_SPACING, 0.06)
    new, mg = follow_ground_profile(ground, np.full(len(x), 0.06))
    assert (ground - old).max() > 50               # the bug this replaces
    assert np.abs(new - ground)[20:-20].max() < 0.5
    assert mg.max() <= 0.22


def test_bump_is_trimmed_both_ways():
    ground = np.zeros(200)
    ground[100] = 10.0                             # one-station DEM spike
    new, _ = follow_ground_profile(ground, np.full(200, 0.06))
    assert 0.0 < new.max() < 2.0
    assert np.abs(np.diff(new)).max() / STATION_SPACING <= 0.06 + 1e-6


def test_junction_taper_eases_raise_on_steep_approach():
    # seg 0 climbs 10% into an at-grade crossing with seg 1, 1 m higher
    k = np.arange(100)
    east = np.r_[k * STATION_SPACING, np.full(3, 99 * STATION_SPACING)]
    north = np.r_[np.zeros(100), [-3.0, 0.0, 3.0]]
    elev = np.r_[0.10 * STATION_SPACING * k, np.full(3, 0.10 * STATION_SPACING * 99 + 1.0)]
    seg = np.r_[np.zeros(100, int), np.ones(3, int)]
    out, _ = junction_agreement_pass(east, north, elev, seg)
    assert (out - elev).max() <= 1.75 + 1e-6       # conflict-gap bound; was ~34 m
    assert (out - elev)[:60].max() == 0.0


if __name__ == "__main__":
    test_junction_taper_eases_raise_on_steep_approach()
    test_tracks_climb_steeper_than_cap()
    test_bump_is_trimmed_both_ways()
    print("ok")
