"""Offline mirror of AODWeatherController's state walk (Weather W1, next
steps.md sec 7.39). Simulates the 4 h season loop per stage/test road and
prints each state's time share per season, then asserts the plan's sanity
expectations (plus W4/W5, sec 7.42: tornado/hurricane eligibility and the
hurricane band envelope). No game launch needed.

Every constant below is duplicated in ODWeatherController.cpp under the same
name - change both together.

Usage:  python sim_weather.py [--loops N] [--seed S]
Reads   data/{stages,road_tiles}/<name>/climate.json (+ road_biomes.json)
"""

import argparse
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# order == EODWeatherState
STATES = ["Clear", "Overcast", "Rain", "HeavyRain", "Sleet", "Snow", "Blizzard",
          "Fog", "DustStorm", "Thunderstorm", "Tornado", "Hurricane"]
(CLEAR, OVERCAST, RAIN, HEAVY_RAIN, SLEET, SNOW, BLIZZARD,
 FOG, DUST_STORM, THUNDERSTORM, TORNADO, HURRICANE) = range(12)

# ---- season clock (AODSeasonController defaults) ----
HOLD_S, TRANSITION_S = 2700.0, 900.0
LOOP_S = 4 * (HOLD_S + TRANSITION_S)
SEASONS = ["summer", "fall", "winter", "spring"]

# ---- weight table (ODWeatherController.cpp, same names) ----
PRECIP_REF_MM = 50.0       # monthly precip that gives precip propensity 1
PRECIP_CAP = 3.0           # propensity ceiling (Skagit's 300 mm Novembers)
DRY_MM = 30.0              # below this, "dry" ramps 0 -> 1 (Clear/Dust boost)
LAPSE_C_PER_KM = 6.5
SNOW_T_C, RAIN_T_C = -2.0, 2.0   # all snow below, all rain above, sleet between
W_CLEAR, W_CLEAR_DRY = 1.0, 1.5
W_OVERCAST, W_OVERCAST_WET = 0.4, 0.2
W_PRECIP = 1.0
SNOW_BOOST = 2.0           # same water falls as more, lighter snow days
RAIN_SPLIT = (0.6, 0.25, 0.15)   # Rain, HeavyRain, Thunderstorm (x warm)
SNOW_SPLIT = (0.75, 0.25)        # Snow, Blizzard
THUNDER_T_C, THUNDER_RAMP_C = 8.0, 12.0
W_FOG = 0.25
W_DUST = 1.0
W_TORNADO = 0.5            # only rolled out of Thunderstorm
TORNADO_T_C, TORNADO_RAMP_C = 12.0, 10.0
W_HURRICANE = 0.05
HURRICANE_T_C, HURRICANE_RAMP_C = 25.0, 1.5
HOLD_MIN_S, HOLD_MAX_S = 240.0, 720.0
TORNADO_HOLD_S = (180.0, 300.0)
HURRICANE_HOLD_S = (600.0, 1200.0)

# per EODBiome: (precip, fog, dust) multipliers; last row = no biome data
BIOME_MULT = [
    (1.0, 1.0, 0.0),   # DeciduousForest
    (1.1, 1.3, 0.0),   # EvergreenForest
    (0.9, 0.8, 0.5),   # Chaparral
    (0.6, 0.3, 1.5),   # DesertScrub
    (1.0, 0.8, 0.7),   # PlainsGrassland
    (1.1, 2.5, 0.0),   # Wetland
    (1.1, 1.2, 0.0),   # AlpineBare
    (1.0, 1.2, 0.0),   # ArcticTundra
    (1.0, 1.2, 0.5),   # Farmland
    (0.8, 0.5, 1.0),   # Savanna
    (1.3, 1.5, 0.0),   # Rainforest
    (1.0, 1.8, 0.3),   # CoastalDune
    (0.6, 0.3, 1.3),   # CanyonBadlands
    (1.0, 0.8, 0.3),   # RockyFoothill
    (1.2, 1.2, 0.2),   # TropicalCoast
    (0.9, 0.8, 0.6),   # LavaField
]
NEUTRAL_MULT = (1.0, 1.0, 1.0)

# ---- W5 hurricane band envelope (ODWeatherController.cpp, same names, sec 7.42) ----
HURR_BANDS = (0.25, 0.72)   # rain band centers, fraction of the hold
HURR_BAND_HALF = 0.14
HURR_LULL = 0.35            # envelope between bands
HURR_EYE_C, HURR_EYE_HALF, HURR_EYE_LEVEL = 0.485, 0.06, 0.05


def bump(d, h):
    """Raised-cosine bump, 1 at d = 0, 0 beyond +-h."""
    return 0.5 * (1.0 + math.cos(math.pi * d / h)) if abs(d) < h else 0.0


def hurricane_envelope(phase, eye):
    e = HURR_LULL + (1.0 - HURR_LULL) * max(bump(phase - HURR_BANDS[0], HURR_BAND_HALF),
                                            bump(phase - HURR_BANDS[1], HURR_BAND_HALF))
    if eye:
        b = bump(phase - HURR_EYE_C, HURR_EYE_HALF)
        e = e * (1 - b) + HURR_EYE_LEVEL * b
    return e


def check_hurricane_envelope():
    """>= 2 bands (rising through 0.9) with a lull (<= HURR_LULL) between them,
    within 0..1, the eye dips below the lull, and the phase wraps continuously
    (the forced QA hurricane loops)."""
    for eye in (False, True):
        env = [hurricane_envelope(i / 1000.0, eye) for i in range(1001)]
        assert all(0.0 <= e <= 1.0 for e in env), eye
        bands = sum(1 for a, b in zip(env, env[1:]) if a < 0.9 <= b)
        assert bands >= 2, (eye, bands)
        lo, hi = int(HURR_BANDS[0] * 1000), int(HURR_BANDS[1] * 1000)
        assert min(env[lo:hi]) <= HURR_LULL + 1e-6, eye
        assert abs(env[0] - env[-1]) < 1e-6
        if eye:
            assert min(env) <= HURR_EYE_LEVEL + 1e-6
        print(f"hurricane envelope eye={eye}: {bands} bands, lull min {min(env[lo:hi]):.2f}, "
              f"range {min(env):.2f}..{max(env):.2f}")


def clamp01(v):
    return max(0.0, min(1.0, v))


def month_of_clock(clock):
    """[0,12), summer hold midpoint = mid-July (6.5)."""
    return (6.5 + (clock - 0.5 * HOLD_S) / LOOP_S * 12.0) % 12.0


def monthly(vals, m):
    """Linear between month centers (i + 0.5)."""
    f = m - 0.5
    i = math.floor(f)
    t = f - i
    return vals[i % 12] * (1 - t) + vals[(i + 1) % 12] * t


def weights(climate, month, biome, elev_delta_m, current):
    t = monthly(climate["monthly_temp_c"], month) - LAPSE_C_PER_KM * elev_delta_m / 1000.0
    p = monthly(climate["monthly_precip_mm"], month)
    storms = climate["storms"]
    bp, bf, bd = BIOME_MULT[biome] if biome is not None else NEUTRAL_MULT
    pw = min(p / PRECIP_REF_MM, PRECIP_CAP) * bp
    dry = clamp01(1.0 - p / DRY_MM)
    s = clamp01((RAIN_T_C - t) / (RAIN_T_C - SNOW_T_C))
    warm = clamp01((t - THUNDER_T_C) / THUNDER_RAMP_C)
    rain_fam = W_PRECIP * pw * (1 - s) ** 2
    snow_fam = W_PRECIP * pw * s * s * SNOW_BOOST
    w = [0.0] * 12
    w[CLEAR] = W_CLEAR + W_CLEAR_DRY * dry
    w[OVERCAST] = W_OVERCAST + W_OVERCAST_WET * min(pw, 1.0)
    w[RAIN] = rain_fam * RAIN_SPLIT[0]
    w[HEAVY_RAIN] = rain_fam * RAIN_SPLIT[1]
    w[THUNDERSTORM] = rain_fam * RAIN_SPLIT[2] * warm
    w[SLEET] = W_PRECIP * pw * 2 * s * (1 - s)
    w[SNOW] = snow_fam * SNOW_SPLIT[0]
    w[BLIZZARD] = snow_fam * SNOW_SPLIT[1]
    w[FOG] = W_FOG * bf * (0.5 + 0.5 * min(pw, 1.0))
    w[DUST_STORM] = W_DUST * storms["dust_storm"] * dry * bd
    if current == THUNDERSTORM:
        w[TORNADO] = W_TORNADO * storms["tornado"] * clamp01((t - TORNADO_T_C) / TORNADO_RAMP_C)
    if current not in (TORNADO, HURRICANE):
        w[HURRICANE] = W_HURRICANE * storms["hurricane"] * clamp01((t - HURRICANE_T_C) / HURRICANE_RAMP_C)
    return w


def menu_offer(climate, month):
    """AODWeatherController::CanProduce under a garage season lock (2026-10-09):
    the states the Weather row offers in that month (Prev=Thunderstorm opens
    the event gates; a Tornado also needs the month's Thunderstorm)."""
    w = weights(climate, month, None, 0.0, THUNDERSTORM)
    return {i for i in range(12) if w[i] > 0 and (i != TORNADO or w[THUNDERSTORM] > 0)}


def hold_seconds(state, rng):
    if state == TORNADO:
        return rng.uniform(*TORNADO_HOLD_S)
    if state == HURRICANE:
        return rng.uniform(*HURRICANE_HOLD_S)
    return rng.uniform(HOLD_MIN_S, HOLD_MAX_S)


def load_stage(d):
    climate = json.loads((d / "climate.json").read_text())
    samples = [(None, 0.0)]
    bp = d / "road_biomes.json"
    if bp.exists():
        b = json.loads(bp.read_text())
        elev = b.get("elev_m")
        # runtime: nearest ODB2 point's ElevM minus the mean over all points
        # (ODB1 files carry ElevM 0 -> no lapse, same as here)
        mean = sum(elev) / len(elev) if elev else 0.0
        samples = [(bi, (elev[i] - mean) if elev else 0.0) for i, bi in enumerate(b["biome"])]
    return climate, samples


def simulate(climate, samples, loops, rng):
    """Returns {season: [seconds per state]}; season = quarter of the loop
    starting at that season's hold."""
    share = {s: [0.0] * 12 for s in SEASONS}
    state, clock, total = CLEAR, 0.0, loops * LOOP_S
    while clock < total:
        biome, de = rng.choice(samples)   # the car moves between rolls
        w = weights(climate, month_of_clock(clock % LOOP_S), biome, de, state)
        state = rng.choices(range(12), weights=w)[0]
        dur = min(hold_seconds(state, rng), total - clock)
        # split the hold across season boundaries
        c = clock
        while c < clock + dur:
            q = int((c % LOOP_S) // (HOLD_S + TRANSITION_S))
            q_end = (c // (HOLD_S + TRANSITION_S) + 1) * (HOLD_S + TRANSITION_S)
            step = min(q_end, clock + dur) - c
            share[SEASONS[q]][state] += step
            c += step
        clock += dur
    return share


def stage_dirs():
    return (sorted(p.parent for p in (ROOT / "data" / "stages").glob("*/climate.json"))
            + sorted(p.parent for p in (ROOT / "data" / "road_tiles").glob("*/climate.json")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loops", type=int, default=200, help="4 h loops per stage (1 = one trace)")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    shown = [CLEAR, OVERCAST, RAIN, HEAVY_RAIN, THUNDERSTORM, SLEET, SNOW, BLIZZARD,
             FOG, DUST_STORM, TORNADO, HURRICANE]
    abbrev = ["Clr", "Ovc", "Rain", "HvyR", "Tstm", "Slt", "Snow", "Bliz", "Fog", "Dust", "Torn", "Hurr"]
    results = {}
    for d in stage_dirs():
        climate, samples = load_stage(d)
        share = simulate(climate, samples, args.loops, random.Random(args.seed))
        results[d.name] = {}
        print(f"\n{d.name}  ({args.loops} x 4 h, % of season time)")
        print(f"{'season':8}" + "".join(f"{a:>6}" for a in abbrev))
        for s in SEASONS:
            tot = sum(share[s])
            pct = [100.0 * v / tot for v in share[s]]
            results[d.name][s] = pct
            print(f"{s:8}" + "".join(f"{pct[i]:6.1f}" for i in shown))

    def pct(name, season, *states):
        return sum(results[name][season][i] for i in states)

    def all_seasons(name, *states):
        return sum(pct(name, s, *states) for s in SEASONS) / 4

    wet = (RAIN, HEAVY_RAIN, THUNDERSTORM, SLEET)
    # sanity expectations from next steps.md's weather plan
    assert all_seasons("oahu_test", SNOW, BLIZZARD, SLEET) == 0
    assert 0 < all_seasons("oahu_test", HURRICANE) < 5, all_seasons("oahu_test", HURRICANE)
    assert pct("palmdale_test", "summer", DUST_STORM) > 10 * max(pct("palmdale_test", "summer", *wet), 0.1)
    assert pct("summit_county_test", "winter", SNOW, BLIZZARD) > 50
    assert all_seasons("skagit_cascades_test", *wet) > 30
    assert all_seasons("summit_county_test", HURRICANE, TORNADO, DUST_STORM) == 0
    # W4/W5 (sec 7.42): event eligibility
    for name in ("oahu_test", "summit_county_test", "palmdale_test", "green_valley_test",
                 "skagit_cascades_test"):
        assert all_seasons(name, TORNADO) == 0, name
    for name in results:
        assert (all_seasons(name, HURRICANE) > 0) == (name == "oahu_test"), name
    check_hurricane_envelope()
    # garage Season -> Weather row (2026-10-09): season lock months (hold midpoints)
    summit, _ = load_stage(ROOT / "data" / "road_tiles" / "summit_county_test")
    winter, summer = menu_offer(summit, 0.5), menu_offer(summit, 6.5)
    assert SNOW in winter and RAIN not in winter and THUNDERSTORM not in winter, winter
    assert THUNDERSTORM in summer and SNOW not in summer and BLIZZARD not in summer, summer
    print("\nsanity ok")


if __name__ == "__main__":
    main()
