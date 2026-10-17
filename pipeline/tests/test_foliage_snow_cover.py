"""Foliage snow follows the ground snow at the player's height (2026-10-17).

Mirrors ODSeasonController.cpp's anon-namespace snow-line math
(SnowLineHigh/LowForCover, the identity ramp, SnowCoverAt) and the
FoliageCover rule in PushParamsToMIDs. Keep in sync with that block.
Also checks ODWeatherController's rain wetting integral reaches ~1 in Rain.

    python pipeline/tests/test_foliage_snow_cover.py
"""

BLEND_IN_END, BLEND_OUT_START = 0.15, 0.85


def lerp(a, b, t):
    return a + (b - a) * t


def clamp01(x):
    return min(max(x, 0.0), 1.0)


def smooth(t):
    t = clamp01(t)
    return t * t * (3 - 2 * t)


def high(b, c):
    return lerp(b["sum_hi"], b["win_hi"], c)


def low_identity(b, c, ref):
    return (ref - c * high(b, c)) / max(1 - c, 0.01)


def low_ramp(b, c, ref):
    if c <= BLEND_IN_END:
        return lerp(b["sum_lo"], low_identity(b, BLEND_IN_END, ref), smooth(c / BLEND_IN_END))
    if c >= BLEND_OUT_START:
        w = smooth((c - BLEND_OUT_START) / (1 - BLEND_OUT_START))
        return lerp(low_identity(b, BLEND_OUT_START, ref), b["win_lo"], w)
    return low_identity(b, c, ref)


def low(b, c, ref):
    winter_at_ref = clamp01((ref - b["win_lo"]) / max(b["win_hi"] - b["win_lo"], 1))
    return lerp(lerp(b["sum_lo"], b["win_lo"], c), low_ramp(b, c, ref), winter_at_ref)


def cover_at(lo, hi, z):
    return clamp01((z - lo) / max(hi - lo, 1))


def foliage_cover(b, c, ref, fresh=0.0):
    return max(cover_at(low(b, c, ref), high(b, c), ref), fresh)


# Colorado-like: summer line well above, winter line well below the valley
COLD = dict(sum_lo=-20000.0, sum_hi=-5000.0, win_lo=-120000.0, win_hi=-100000.0)
# Oahu-like: even full winter keeps the lines above all terrain
WARM = dict(sum_lo=500000.0, sum_hi=600000.0, win_lo=300000.0, win_hi=350000.0)
REF = -67030.0

# the bug: a Winter lock over bare ground used to push SnowCover 1 to the trees
assert foliage_cover(WARM, 1.0, 0.0) == 0.0
assert foliage_cover(WARM, 1.0, 0.0, fresh=0.4) == 0.4   # fresh roadside snow still dusts them
# well below the winter low line: bare
assert foliage_cover(COLD, 1.0, -200000.0) == 0.0
# summer: bare at the valley
assert foliage_cover(COLD, 0.0, REF) == 0.0
# full winter inside the winter zone: white
assert foliage_cover(COLD, 1.0, REF) > 0.99
# mid-transition inside the winter zone: trees track the ground (identity ramp)
for c in (0.3, 0.5):   # (later the high line passes REF and cover clamps to 1)
    assert abs(foliage_cover(COLD, c, REF) - c) < 1e-3, c


# rain wetting: Rain targets precip 0.45 over a 90 s smoothstep ramp,
# Wetness += dt * precip / 180 (WetUpSeconds); same answer at any fixed step
def wetness_at(t_end, dt):
    w = ramp = 0.0
    for _ in range(int(round(t_end / dt))):
        ramp = min(ramp + dt / 90.0, 1.0)
        p = 0.45 * smooth(ramp)
        w = min(w + dt * p / 180.0, 1.0) if p > 0.05 else w
    return w


for dt in (1 / 30, 1 / 60, 1 / 144):
    assert wetness_at(400, dt) > 0.85, (dt, wetness_at(400, dt))
    assert wetness_at(460, dt) > 0.999
assert abs(wetness_at(300, 1 / 30) - wetness_at(300, 1 / 144)) < 0.01

print("ok")
