"""Check the Realistic car-dirt model (OpenDriveUE/Source/OpenDriveUE/ODCarGrime.h,
integrated per fixed physics step in UODVehicleMovement::AsyncPhysicsTickComponent).

Mirrors ODCarGrime::Step / RatePerM with the constants read straight out of
the header, then checks:
  1. accumulation is per distance: 1 km of gravel gives the closed form,
     parking for 10 minutes adds nothing, gravel dirties far faster than
     dry asphalt, wet asphalt faster than dry, never past MaxDirt;
  2. the same drive stepped at 30/60/120/240/500 Hz ends equally dirty;
  3. reset (what AODVehiclePawn::ResetVehicle / TeleportToSpawn /
     ApplyVehicleDef call) returns to 0 and accumulation restarts from 0.

Usage:  python pipeline/checks/check_car_dirt.py   (exit 1 on failure)
"""
import math
import re
from pathlib import Path

HDR = Path(__file__).resolve().parents[2] / "OpenDriveUE" / "Source" / "OpenDriveUE" / "ODCarGrime.h"
src = HDR.read_text(encoding="utf-8")


def const(name):
    m = re.search(rf"constexpr float {name} = ([0-9.]+)f(?: / ([0-9.]+)f)?;", src)
    assert m, f"{name} not found in {HDR.name}"
    return float(m[1]) / (float(m[2]) if m[2] else 1.0)


MAX = const("MaxDirt")
LOOSE, OFF, ASPH, ASPH_WET = (const(n) for n in (
    "LooseRatePerM", "OffRoadRatePerM", "AsphaltRatePerM", "AsphaltWetRatePerM"))


def rate(surface, wet=0.0):
    if surface is None:
        return 0.0
    if "Asphalt" in surface:
        return ASPH + (ASPH_WET - ASPH) * min(max(wet, 0.0), 1.0)
    if "Gravel" in surface or "Dirt" in surface:
        return LOOSE
    return OFF


def step(d, dist_m, r):
    return MAX - (MAX - d) * math.exp(-r * max(dist_m, 0.0))


def surface_at(s_m):
    """A 6 km route: asphalt, gravel, off-road dirt, wet-ish asphalt."""
    if s_m < 2000:
        return "PM_Asphalt", 0.0
    if s_m < 3200:
        return "PM_Gravel", 0.0
    if s_m < 3700:
        return "PM_Dirt", 0.0
    return "PM_Asphalt", 0.7


def drive(hz, speed_ms=25.0, length_m=6000.0, d=0.0):
    dt, s = 1.0 / hz, 0.0
    while s < length_m - 1e-9:
        surf, wet = surface_at(s)
        ds = min(speed_ms * dt, length_m - s)
        d = step(d, ds, sum(rate(surf, wet) for _ in range(4)) / 4)   # four wheels on the same ground
        s += ds
    return d


# 1. per distance
d = 0.0
for _ in range(1000):                      # 1 km of gravel in 1 m steps
    d = step(d, 1.0, rate("PM_Gravel"))
assert abs(d - MAX * (1 - math.exp(-LOOSE * 1000))) < 1e-9, d
parked = d
for _ in range(600 * 120):                 # 10 min parked at 120 Hz: no distance
    d = step(d, 0.0, rate("PM_Gravel"))
assert d == parked, "parking adds dirt"
assert step(0, 1000, rate("PM_Gravel")) > 10 * step(0, 1000, rate("PM_Asphalt")), "gravel >> dry asphalt"
assert step(0, 1000, rate("PM_Asphalt", 1.0)) > 5 * step(0, 1000, rate("PM_Asphalt")), "wet > dry"
assert step(0, 1e7, rate("PM_Dirt")) <= MAX, "capped at MaxDirt"
assert rate(None) == 0.0, "airborne: nothing"
print(f"1 km gravel -> {parked:.3f} of max {MAX}; 1 km dry asphalt -> {step(0, 1000, rate('PM_Asphalt')):.4f}")

# 2. step-rate independence
runs = {hz: drive(hz) for hz in (30, 60, 120, 240, 500)}
spread = max(runs.values()) - min(runs.values())
print("6 km drive:", ", ".join(f"{hz} Hz {v:.5f}" for hz, v in runs.items()), f"(spread {spread:.1e})")
assert spread < 1e-3, f"step rate changes the result by {spread}"
assert 0.2 < runs[60] < MAX, runs[60]

# 3. reset
d = 0.0                                    # Movement->ResetDirt()
assert d == 0.0
assert abs(drive(60, length_m=500.0, d=d) - drive(240, length_m=500.0)) < 1e-6
print("check_car_dirt: OK")
