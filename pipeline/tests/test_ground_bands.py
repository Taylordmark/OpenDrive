"""Per-area ground bands (build_ground_bands.py, realism eval T1): warm areas
get no snow/scree, the Rockies only patchy summer snow above ~3900 m, sea
level sits at -origin, and the material's noisy snow ramp never reaches below
the first-patch elevation.

Run:  python pipeline/tests/test_ground_bands.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_ground_bands import LOW_GREEN_BIAS, SNOW_NOISE_STRENGTH, bands, low_green_bias, snow_ramp


def climate(t_lo, t_hi):
    return {"monthly_temp_c": [t_lo] + [t_hi] * 11}


def max_snow(z_cm, lo, hi):
    """apply_ground_material.py snow_t at the noise's maximum (+strength/2)."""
    return min(1.0, max(0.0, (z_cm - lo) / (hi - lo) + SNOW_NOISE_STRENGTH / 2))


def test_snow_ramp_first_patch():
    lo, hi = snow_ramp(3900.0, 4890.0)
    assert hi == 4890.0
    assert abs(max_snow(3900.0, lo, hi)) < 1e-9 and max_snow(3950.0, lo, hi) > 0


def test_summit_like():
    b = bands(3435.3, climate(-12.4, 13.2), 3234.0)    # summit_county climate + POWER cell
    asl = lambda cm: cm / 100.0 + 3435.3                # noqa: E731
    assert abs(b["sea_z_cm"] + 343530.0) < 1e-6
    assert 3450 < asl(b["treeline_low_cm"]) < 3700
    # Loveland Pass (3655 m) is snow-free in summer even at max noise ...
    assert max_snow((3655 - 3435.3) * 100, b["snow_summer_low_cm"],
                    b["snow_summer_high_cm"]) == 0.0
    # ... patches start ~3900 m, and winter whitens the valley floor (2750 m)
    first = asl(b["snow_summer_low_cm"]) - SNOW_NOISE_STRENGTH / 2 * (b["snow_summer_high_cm"]
                                                                      - b["snow_summer_low_cm"]) / 100
    assert 3850 < first < 3960, first
    assert asl(b["snow_winter_high_cm"]) < 2750


def test_warm_areas_bare():
    for origin, clim, ref, top in ((306.0, climate(22.7, 26.4), 78.0, 1220),     # Oahu
                                   (879.4, climate(8.1, 26.4), 771.0, 1800),     # Green Valley
                                   (55.0, climate(9.3, 17.6), 87.0, 300)):       # SF
        b = bands(origin, clim, ref)
        top_cm = (top - origin) * 100
        assert b["treeline_low_cm"] > top_cm
        assert max_snow(top_cm, b["snow_summer_low_cm"], b["snow_summer_high_cm"]) == 0.0
        assert max_snow(top_cm, b["snow_winter_low_cm"], b["snow_winter_high_cm"]) == 0.0
        assert abs(b["sea_z_cm"] + origin * 100) < 1e-6


def test_low_green_bias():
    gv = {"monthly_temp_c": [8, 9, 11, 14, 18, 23, 26, 26, 23, 18, 12, 8],
          "monthly_precip_mm": [75, 74, 50, 21, 10, 1, 2, 1, 3, 18, 25, 61]}
    wet = dict(gv, monthly_precip_mm=[60] * 12)
    assert low_green_bias(gv) == 0.0                     # summer-dry: no olive pull
    assert low_green_bias(wet) == LOW_GREEN_BIAS
    assert low_green_bias({"monthly_temp_c": [0] * 12}) == LOW_GREEN_BIAS   # old climate.json


if __name__ == "__main__":
    test_snow_ramp_first_patch()
    test_summit_like()
    test_warm_areas_bare()
    test_low_green_bias()
    print("test_ground_bands: OK")
