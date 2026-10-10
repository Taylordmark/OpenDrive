"""Night exposure sanity (2026-10-09, Taylor: "dusk was not dark enough ...
make the lights of the car be pretty much the only source of light except
for a very faint illumination from the stars").

Mirrors AODDayNightController's sun elevation and NightFactor() and the
exposure the night grade allows (auto-exposure floor NightMinEV, plus the
Grade volume's -1 EV bias), then checks what fraction of mid grey the
headlit road, moonlit ground and stars land at for the menu time presets.

Usage:  python pipeline/check_night_exposure.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_headlight_ies import road as head_road   # noqa: E402  IES low beam, x mid grey at NIGHT_MIN_EV

MAX_ELEV = 68.0           # AODDayNightController::MaxElevation
NIGHT_MIN_EV = 1.0        # AODDayNightController::NightMinEV
EXPOSURE_BIAS = -1.0      # AODAtmosphereController grade AutoExposureBias
MOON_LUX = 0.15           # AODDayNightController::MoonIntensity
REAR_CONE, TAIL_LM, BRAKE_LM, REVERSE_LM = 70.0, 30.0, 200.0, 400.0   # rear spot
STAR_PEAK = 3.0 * 4.15    # M_NightSky Brightness x brightest-star magnitude
HEAD_GLOW, TAIL_GLOW, BRAKE_DAY = 300.0, 25.0, 400.0   # AODDayNightController lamp glow, nits
DAY_EV = 12.0             # bright day exposure (Grade volume max)
PRESETS = {"Golden Hour": 17.5, "Dusk": 18.3, "Night": 22.0, "Noon": 12.0}


def elevation(hour):
    return math.sin((hour - 6.0) / 12.0 * math.pi) * MAX_ELEV


def night_factor(elev):
    t = min(max((2.0 - elev) / 8.0, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def mid_grey_nits(ev):
    """Luminance auto-exposure maps to mid grey at EV100 ev, with the bias."""
    return 0.125 * 2.0 ** (ev - EXPOSURE_BIAS)


def lambert(lux, albedo):
    return lux * albedo / math.pi


def main():
    mid = mid_grey_nits(NIGHT_MIN_EV)   # night: the scene is darker than the floor, so it clamps
    road10, road30 = float(head_road(10.0, 0.0)), float(head_road(30.0, 0.0))   # grazing, both lamps
    moonlit = lambert(MOON_LUX, 0.2) / mid
    star = STAR_PEAK / mid
    rear_cd = lambda lm: lm / (2 * math.pi * (1 - math.cos(math.radians(REAR_CONE))))
    behind = {n: lambert(rear_cd(lm) / 3 ** 2, 0.1) / mid
              for n, lm in (("tail", TAIL_LM), ("brake", BRAKE_LM), ("reverse", REVERSE_LM))}
    print("road 3 m behind x mid grey: " + "  ".join(f"{n} {v:.2f}" for n, v in behind.items()))
    tail, brake_night = TAIL_GLOW / mid, 5 * TAIL_GLOW / mid
    brake_day = BRAKE_DAY / mid_grey_nits(DAY_EV)
    print(f"lamps x mid grey: head {HEAD_GLOW / mid:.0f}  tail {tail:.0f}  brake night {brake_night:.0f}  "
          f"brake day {brake_day:.2f}")
    print(f"night mid grey {mid:.2f} nits | x mid grey: road@10m {road10:.2f}  road@30m {road30:.3f}  "
          f"moonlit grass {moonlit:.3f}  brightest star {star:.0f}")
    for name, hour in PRESETS.items():
        e = elevation(hour)
        print(f"{name:12s} {hour:5.1f} h  sun {e:+6.1f} deg  night {night_factor(e):.2f}")

    assert night_factor(elevation(12.0)) == 0.0 and night_factor(elevation(17.5)) == 0.0, "day untouched"
    assert night_factor(elevation(18.3)) > 0.95, "menu Dusk is (almost) full night grade"
    assert night_factor(elevation(22.0)) == 1.0
    assert road10 > 1.0, "headlit road near the car reads at or above mid grey"
    assert road30 > 5 * moonlit, "headlights clearly beat the moon even at 30 m"
    assert 0.005 < moonlit < 0.05, "moonlit ground: very faint, not black, not lit"
    assert star > 10.0, "bright stars read white"
    assert 10.0 < tail < 200.0 and brake_night < 500.0, "tails glow red at night without flooding bloom"
    assert brake_day > 0.1, "brake lights still read in daylight"
    assert behind["tail"] < 0.1 < behind["brake"] < behind["reverse"] < 1.5, "faint tail wash, visible brake and reverse"
    print("PASS: night is headlights first, a faint moon/star floor, stars visible")


if __name__ == "__main__":
    main()
