"""Track E (PLAN-VISUALS.md): mirror of UODVehicleFX::Rates and its spawn /
motion loop (OpenDriveUE/Source/OpenDriveUE/ODVehicleFX.cpp). Checks the
slip/surface -> spawn-rate mapping and that the same drive gives the same
puffs at any frame rate. Change the constants here with the .cpp ones.

Run:  python pipeline/tests/test_vehicle_fx_rates.py"""
import math

SMOKE_MAX, SMOKE_SLIP_LO, SMOKE_SLIP_HI = 40.0, 3.0, 12.0
DUST_MAX, DUST_STIR_LO, DUST_STIR_HI, DUST_WET_CUT = 30.0, 2.0, 25.0, 0.85
SPRAY_MAX, SPRAY_WET_LO, SPRAY_WET_HI = 35.0, 0.1, 0.6
SPRAY_SPEED_LO, SPRAY_SPEED_HI = 4.0, 30.0
POWDER_MAX, POWDER_SNOW_LO, POWDER_SNOW_HI, POWDER_STIR_LO, POWDER_STIR_HI = 22.0, 0.05, 0.6, 3.0, 25.0


def clamp01(x):
    return min(max(x, 0.0), 1.0)


def smoothstep(a, b, x):  # FMath::SmoothStep
    if x < a:
        return 0.0
    if x >= b:
        return 1.0
    t = (x - a) / (b - a)
    return t * t * (3.0 - 2.0 * t)


def rates(surface, slip, speed, wet, snow=0.0):
    """(smoke, dust, spray, powder) puffs/s for one wheel. surface: None/'asphalt'/'loose'."""
    wet = clamp01(wet)
    if surface is None:
        return (0.0, 0.0, 0.0, 0.0)
    stir = speed + 2 * slip
    pw = smoothstep(POWDER_SNOW_LO, POWDER_SNOW_HI, snow)
    powder = POWDER_MAX * pw * clamp01((stir - POWDER_STIR_LO) / (POWDER_STIR_HI - POWDER_STIR_LO))
    if surface == "asphalt":
        return (SMOKE_MAX * smoothstep(SMOKE_SLIP_LO, SMOKE_SLIP_HI, slip) * (1 - wet) * (1 - pw), 0.0,
                SPRAY_MAX * smoothstep(SPRAY_WET_LO, SPRAY_WET_HI, wet)
                * clamp01((speed - SPRAY_SPEED_LO) / (SPRAY_SPEED_HI - SPRAY_SPEED_LO)), powder)
    return (0.0, DUST_MAX * clamp01((stir - DUST_STIR_LO) / (DUST_STIR_HI - DUST_STIR_LO))
            * (1 - DUST_WET_CUT * wet) * (1 - pw), 0.0, powder)


def drive(t):
    """A 10 s scripted drive: cruise, burnout, slide, cruise. -> slip, speed (m/s)."""
    slip = 15.0 if 2 < t < 4 else 7.0 if 6 < t < 7.5 else 0.5
    return slip, 10 + 2 * t


def spawn_times(fps, rate_fn, seconds=10.0):
    """The component's debt loop; returns each puff's due time."""
    dt, debt, now, out = 1.0 / fps, 0.0, 0.0, []
    for _ in range(round(seconds * fps)):
        now += dt
        r = rate_fn(now)  # sampled once per frame, like the telemetry
        if r <= 0:
            continue
        debt += r * dt
        while debt >= 1:
            debt -= 1
            out.append(now - debt / r)  # tau = debt / rate
        debt = min(debt, 1.0)
    return out


def integrate(pos, vel, accel, drag, dt):  # UODVehicleFX::Integrate, one axis
    e = math.exp(-drag * dt)
    vinf = accel / drag
    return pos + vinf * dt + (vel - vinf) * (1 - e) / drag, vinf + (vel - vinf) * e


# --- mapping ---
assert rates("asphalt", 0.5, 20, 0) == (0, 0, 0, 0)         # grip driving: nothing
assert rates("asphalt", 15, 5, 0)[0] == SMOKE_MAX            # burnout: full smoke
assert 0 < rates("asphalt", 7, 20, 0)[0] < SMOKE_MAX         # partial slide: some
assert rates("asphalt", 15, 5, 1)[0] == 0                    # soaked road doesn't smoke
assert rates("asphalt", 0, 30, 1)[2] == SPRAY_MAX            # wet + fast: full spray
assert rates("asphalt", 0, 3, 1)[2] == 0                     # wet but crawling: none
assert rates("asphalt", 0, 30, 0.05)[2] == 0                 # damp: none
assert rates("loose", 0, 1, 0) == (0, 0, 0, 0)               # parked on gravel
assert rates("loose", 0, 30, 0)[1] == DUST_MAX               # gravel run
assert rates("loose", 0, 30, 1)[1] < 0.2 * DUST_MAX          # mud, not dust
assert rates(None, 20, 40, 0, 1) == (0, 0, 0, 0)             # grass/air: none
assert rates("asphalt", 0, 30, 0, 1)[3] == POWDER_MAX        # snowy road at speed: full powder
assert rates("asphalt", 0, 30, 0, 0)[3] == 0                 # bare road: none
assert rates("asphalt", 0, 2, 0, 1)[3] == 0                  # crawling: none
assert rates("asphalt", 15, 5, 0, 1)[0] == 0                 # burnout in snow throws powder, not smoke
assert rates("asphalt", 15, 5, 0, 1)[3] > 0
assert rates("loose", 0, 30, 0, 1)[1] == 0                   # snow-covered dirt: no dust
for s in (0, 2, 5, 9, 12, 30):                               # monotonic in slip
    assert rates("asphalt", s, 20, 0)[0] <= rates("asphalt", s + 0.5, 20, 0)[0]

# --- frame-rate independence: count and timing of puffs ---
smoke = lambda t: rates("asphalt", *drive(t), 0.0)[0]
expected = sum(smoke((i + 0.5) / 1000) for i in range(10000)) / 1000  # integral of the rate
counts = {}
for fps in (24, 30, 60, 90, 144, 240):
    times = spawn_times(fps, smoke)
    counts[fps] = len(times)
    assert abs(len(times) - expected) <= 0.02 * expected + 2, (fps, len(times), expected)
    # sub-frame placement: puff due times are evenly spaced inside a burnout
    burn = [t for t in times if 2.2 < t < 3.8]
    gaps = [b - a for a, b in zip(burn, burn[1:])]
    assert max(gaps) - min(gaps) < 1e-6, (fps, min(gaps), max(gaps))
print("smoke puffs over the 10 s drive by fps:", counts, f"(integral {expected:.1f})")

# --- frame-rate independence: motion ---
for accel, drag in ((40, 1.6), (-30, 2.0), (-350, 2.5), (-20, 2.2)):   # smoke, dust, spray, powder
    ref = None
    for fps in (24, 60, 240, 1000):
        p, v = 0.0, 300.0
        for _ in range(round(1.5 * fps)):
            p, v = integrate(p, v, accel, drag, 1.0 / fps)
        ref = ref if ref is not None else (p, v)
        assert abs(p - ref[0]) < 1e-6 * max(1, abs(ref[0])) and abs(v - ref[1]) < 1e-6 * max(1, abs(ref[1])), \
            (accel, drag, fps, p, v, ref)

print("test_vehicle_fx_rates: ok")
