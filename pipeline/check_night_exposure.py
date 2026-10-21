"""Night exposure sanity (2026-10-09, Taylor: "dusk was not dark enough ...
make the lights of the car be pretty much the only source of light except
for a very faint illumination from the stars").

Mirrors ODSolar.h's sun (real position at the area's lat/lon and date),
AODDayNightController's NightFactor() and the
exposure the night grade allows (auto-exposure floor NightMinEV, plus the
Grade volume's -1 EV bias), then checks what fraction of mid grey the
headlit road, moonlit ground and stars land at for the menu time presets.

Usage:  python pipeline/check_night_exposure.py
"""
import ast
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from make_headlight_ies import road as head_road   # noqa: E402  IES low beam, x mid grey at NIGHT_MIN_EV

NIGHT_MIN_EV = 1.0        # AODDayNightController::NightMinEV
EXPOSURE_BIAS = -1.0      # AODAtmosphereController grade AutoExposureBias
MOON_LUX = 0.15           # AODDayNightController::MoonIntensity
REAR_CONE, TAIL_LM, BRAKE_LM, REVERSE_LM = 70.0, 30.0, 200.0, 400.0   # rear spot
STAR_GAIN = 8.0           # STARS_HLSL "mag * 8.0 * core"
STAR_PEAK = 3.0 * 4.5 * STAR_GAIN   # M_NightSky Brightness x brightest-star magnitude
STAR_MEDIAN = 3.0 * (0.5 + 4.0 * 0.5 ** 6) * STAR_GAIN   # median star (mag = 0.5 + 4 u^6)
STAR_CELLS, STAR_CORE = 150.0, 0.25          # STARS_HLSL cells per unit dir, core radius (cells)
SCREEN_W, SCREEN_PCT, HFOV = 1920, 0.67, 90.0   # worst case: lowest render scale
HEAD_GLOW, TAIL_GLOW, BRAKE_DAY = 300.0, 25.0, 400.0   # AODDayNightController lamp glow, nits
DAY_EV = 12.0             # bright day exposure (Grade volume max)
# M_BuildingFacade_real lit windows (PLAN-VISUALS Track G): constants parsed from its build script
BUILDING_SCRIPT = Path(__file__).resolve().parent.parent / "unreal" / "apply_building_material_real.py"
FACADE_SHARE = 0.5        # worst case: a street of facades fills half the screen
# ODMainMenu TimeOfDayPresets: fixed hour, or (sun deg, +1 rising / -1 setting)
PRESETS = {"Dawn": (3.0, 1), "Golden Hour": (6.0, -1), "Dusk": (-5.0, -1), "Night": 22.0, "Noon": 12.0}
# (lat, lon, clock UTC offset with DST) - worldmap.json "ll", AODDayNightController AreaZones
AREAS = {"far_gasquet": (41.8475, -123.965, -8), "deals_gap": (35.4975, -83.955, -5),
         "summit_county": (39.525, -106.175, -7), "skagit_cascades": (48.275, -121.95, -8),
         "oahu": (21.48, -157.975, -10)}
DAYS = {"summer": 199, "winter": 15}   # AODSeasonController mid-July / mid-January


def sun_elevation(lat, lon, doy, hour, utc):
    """ODSolar::SunPosition (elevation only)."""
    g = 2 * math.pi / 365 * (doy - 1 + (hour - utc - 12) / 24)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    ha = math.radians((hour * 60 + eqt + 4 * lon - 60 * utc) / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))


def preset_hour(preset, lat, lon, doy, utc):
    """ODSolar::HourAtElevation for sun-relative presets (no fallback needed
    at these latitudes)."""
    if not isinstance(preset, tuple):
        return preset
    target, rising = preset[0], preset[1] > 0
    noon = 12 + utc - lon / 15
    lo, hi = (noon - 12, noon) if rising else (noon, noon + 12)
    above = lambda h: sun_elevation(lat, lon, doy, h, utc) > target
    assert above(lo) != above(hi), "sun reaches the preset elevation"
    a_lo = above(lo)
    for _ in range(30):
        mid = (lo + hi) / 2
        if above(mid) == a_lo:
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2) % 24


def preset_elevations():
    """{(area, season): {preset: (hour, sun deg)}} as the menu sets them."""
    out = {}
    for area, (lat, lon, utc) in AREAS.items():
        for season, doy in DAYS.items():
            u = utc + (1 if area != "oahu" and 69 <= doy < 307 else 0)
            out[area, season] = {n: (h, sun_elevation(lat, lon, doy, h, u))
                                 for n, h in ((n, preset_hour(p, lat, lon, doy, u)) for n, p in PRESETS.items())}
    return out


def night_factor(elev):
    t = min(max((2.0 - elev) / 8.0, 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def mid_grey_nits(ev):
    """Luminance auto-exposure maps to mid grey at EV100 ev, with the bias."""
    return 0.125 * 2.0 ** (ev - EXPOSURE_BIAS)


def lambert(lux, albedo):
    return lux * albedo / math.pi


def building_constants():
    out = {}
    for node in ast.parse(BUILDING_SCRIPT.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                pass
    return out


def check_windows(mid, tail, presets):
    """Lit windows by night: visible, below the tail lamps, never enough to
    lift auto-exposure off the night floor; dark by day; switched on by the
    same NightFactor as the grade (the shader re-derives it from the sun)."""
    b = building_constants()
    shader_night = lambda e: (lambda t: t * t * (3 - 2 * t))(
        min(max((b["NIGHT_ELEV_HI"] - e) / b["NIGHT_ELEV_SPAN"], 0.0), 1.0))
    for e in range(-90, 91):
        assert abs(shader_night(e) - night_factor(e)) < 1e-9, "window night = grade night"
    # dimmest = low jitter x the darkest interior-mapped room face (floor)
    lo = b["LIT_NITS"] * b["LIT_JITTER_MIN"] * b.get("INTERIOR_SHADE_MIN", 1.0) / mid
    hi = b["LIT_NITS"] / mid
    u0, u1 = b["WINDOW_MARGIN_U"]
    v0, v1 = b["WINDOW_MARGIN_V_UPPER"]
    glass_share = (u1 - u0) * (v1 - v0)
    lit_share = {n: b["LIT_MAX_FRACTION"] * max(night_factor(e) for _, e in presets) for n, presets in
                 ((n, [p[n] for p in presets.values()]) for n in PRESETS)}
    town = FACADE_SHARE * glass_share * b["LIT_MAX_FRACTION"] * (lo + hi) / 2
    print(f"lit windows x mid grey {lo:.1f}-{hi:.1f} | lit share " +
          "  ".join(f"{n} {v:.2f}" for n, v in lit_share.items()) +
          f" | facade street mean {town:.2f} x mid grey")
    assert lo > 2.0, "dimmest lit window still reads clearly"
    assert hi < 0.5 * tail, "windows stay below the tail lamps (headlights and car lamps first)"
    assert town < 1.0, "a street of lit windows averages under mid grey: exposure stays on the night floor"
    assert lit_share["Noon"] == 0.0 and lit_share["Golden Hour"] == 0.0, "no lit windows by day"
    assert lit_share["Night"] == b["LIT_MAX_FRACTION"]


def main():
    mid = mid_grey_nits(NIGHT_MIN_EV)   # night: the scene is darker than the floor, so it clamps
    road10, road30 = float(head_road(10.0, 0.0)), float(head_road(30.0, 0.0))   # grazing, both lamps
    moonlit = lambert(MOON_LUX, 0.2) / mid
    star = STAR_PEAK / mid
    # a star is smaller than a pixel: what shows is its linear-core energy (pi R^2 / 3)
    # spread over the pixel's solid angle
    pix_sr = (2 * math.tan(math.radians(HFOV / 2)) / (SCREEN_W * SCREEN_PCT)) ** 2
    cover = min(1.0, math.pi * (STAR_CORE / STAR_CELLS) ** 2 / 3 / pix_sr)
    median_star = STAR_MEDIAN * cover / mid
    rear_cd = lambda lm: lm / (2 * math.pi * (1 - math.cos(math.radians(REAR_CONE))))
    behind = {n: lambert(rear_cd(lm) / 3 ** 2, 0.1) / mid
              for n, lm in (("tail", TAIL_LM), ("brake", BRAKE_LM), ("reverse", REVERSE_LM))}
    print("road 3 m behind x mid grey: " + "  ".join(f"{n} {v:.2f}" for n, v in behind.items()))
    tail, brake_night = TAIL_GLOW / mid, 5 * TAIL_GLOW / mid
    brake_day = BRAKE_DAY / mid_grey_nits(DAY_EV)
    print(f"lamps x mid grey: head {HEAD_GLOW / mid:.0f}  tail {tail:.0f}  brake night {brake_night:.0f}  "
          f"brake day {brake_day:.2f}")
    print(f"night mid grey {mid:.2f} nits | x mid grey: road@10m {road10:.2f}  road@30m {road30:.3f}  "
          f"moonlit grass {moonlit:.3f}  brightest star {star:.0f}  median star on screen {median_star:.2f}")
    presets = preset_elevations()
    for (area, season), row in presets.items():
        print(f"{area:16s} {season:6s} " + "  ".join(
            f"{n} {h:5.2f} h {e:+5.1f}" for n, (h, e) in row.items()))
        night = {n: night_factor(e) for n, (_, e) in row.items()}
        assert night["Noon"] == night["Golden Hour"] == night["Dawn"] == 0.0, "day untouched"
        assert night["Dusk"] > 0.95, "menu Dusk is (almost) full night grade"
        assert night["Night"] == 1.0, "Night is night everywhere, every season"
        assert row["Dawn"][0] < 12 < row["Golden Hour"][0] < row["Dusk"][0], "dawn morning, golden hour before dusk"
    assert road10 > 1.0, "headlit road near the car reads at or above mid grey"
    assert road30 > 5 * moonlit, "headlights clearly beat the moon even at 30 m"
    assert 0.005 < moonlit < 0.05, "moonlit ground: very faint, not black, not lit"
    assert star > 10.0, "bright stars read white"
    assert median_star > 4.0, "a typical star reads clearly at low render scale (1x / 0.5 was invisible in game)"
    assert 10.0 < tail < 200.0 and brake_night < 500.0, "tails glow red at night without flooding bloom"
    assert brake_day > 0.1, "brake lights still read in daylight"
    assert behind["tail"] < 0.1 < behind["brake"] < behind["reverse"] < 1.5, "faint tail wash, visible brake and reverse"
    check_windows(mid, tail, presets)
    print("PASS: night is headlights first, a faint moon/star floor, stars visible, lit windows in between")


if __name__ == "__main__":
    main()
