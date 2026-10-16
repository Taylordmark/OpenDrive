"""Wet / puddle dry-out check (2026-10-16). Mirrors AODWeatherController's
Wetness + Puddles integration (ODWeatherController.cpp Tick, Evaporation())
and asserts: same result at 15/60/240 fps, puddles fill over minutes of rain
and outlast the surface shine, and warm wind dries faster than cold calm.
Constants are parsed from the C++ so the mirror can't drift.

Usage:  python pipeline/checks/check_wet_dryout.py
"""
import math
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "OpenDriveUE/Source/OpenDriveUE"
H = (SRC / "ODWeatherController.h").read_text(encoding="utf-8")
CPP = (SRC / "ODWeatherController.cpp").read_text(encoding="utf-8")


def h_const(name):
    return float(re.search(rf"float {name} = ([\d.]+)f;", H).group(1))


WET_UP, DRY = h_const("WetUpSeconds"), h_const("DrySeconds")
FILL, DRAIN = h_const("PuddleFillSeconds"), h_const("PuddleDrainSeconds")
PER_RAIN = float(re.search(r"PUDDLE_PER_RAIN = ([\d.]+)f;", CPP).group(1))


def evaporation(air_c, wind_ms):
    return min(max((air_c + 5.0) / 20.0, 0.3), 2.0) * (1.0 + wind_ms / 15.0)


def run(schedule, fps, air_c=10.0, wind_ms=3.0):
    """schedule: [(seconds, precip, precip_type)] -> (wetness, puddles) at the end."""
    wet = pud = 0.0
    dt = 1.0 / fps
    evap = evaporation(air_c, wind_ms)
    for secs, precip, ptype in schedule:
        for _ in range(round(secs * fps)):
            wet = min(wet + dt * precip / WET_UP, 1.0) if precip > 0.05 else max(wet - dt * evap / DRY, 0.0)
            target = min(max(precip * (1.0 - ptype) * PER_RAIN, 0.0), 1.0)
            pud = (pud + (target - pud) * (1.0 - math.exp(-dt / max(FILL, 1.0))) if pud < target
                   else max(pud - dt * evap / DRAIN, target))
    return wet, pud


def main():
    shower = [(600, 0.8, 0.0), (900, 0.0, 0.0)]   # 10 min heavy rain, 15 min after
    ref = run(shower, 60)
    for fps in (15, 240):
        w, p = run(shower, fps)
        assert abs(w - ref[0]) < 0.01 and abs(p - ref[1]) < 0.01, f"fps {fps}: {w, p} vs {ref}"
    w, p = run([(600, 0.8, 0.0)], 60)
    assert w == 1.0 and p > 0.8, f"10 min heavy rain: soaked + puddled ({w:.2f}, {p:.2f})"
    w, p = run([(60, 0.8, 0.0)], 60)
    assert p < 0.3, f"a 1 min burst doesn't flood ({p:.2f})"
    w, p = run([(600, 0.8, 1.0)], 60)
    assert p == 0.0, "snow makes no puddles"
    assert ref[1] > ref[0] * 0.9, f"puddles outlast the shine ({ref})"
    cold = run(shower, 60, air_c=-2.0, wind_ms=0.0)
    warm = run(shower, 60, air_c=25.0, wind_ms=12.0)
    assert warm[0] < ref[0] < cold[0] and warm[1] < ref[1] < cold[1], f"dries faster warm/windy: {warm} {ref} {cold}"
    print(f"after 10 min heavy rain + 15 min dry (10 C, 3 m/s): wet {ref[0]:.2f}, puddles {ref[1]:.2f}; "
          f"cold calm {cold[0]:.2f}/{cold[1]:.2f}, warm windy {warm[0]:.2f}/{warm[1]:.2f}")
    print("PASS: wet/puddle dry-out mirror")


if __name__ == "__main__":
    main()
