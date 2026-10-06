"""Offline check for weather snow accumulation (2026-10-06).

Mirrors AODWeatherController::TickSnow in
OpenDriveUE/Source/OpenDriveUE/ODWeatherController.cpp - same knob names and
defaults; change both. Road chunks (AODRoadManager::TickSnow) integrate the
same rates, so the ambient curve here is what an unplowed chunk shows.
Run: python pipeline/check_snow_depth.py
"""

# MIRRORED from ODWeatherController.h "Weather|Snow" defaults
SNOW_TIME_SCALE = 1.0
SNOW_CONTACT_MELT_SECONDS = 90.0
ROAD_SNOW_CM_PER_MIN = 3.5
ROAD_SNOW_CAP_CM = 30.0
SIDE_SNOW_MULT = 2.0
SIDE_SNOW_CAP_CM = 90.0
SNOW_MELT_CM_PER_MIN = 0.5
RAIN_MELT_CM_PER_MIN = 4.0

BLIZZARD, SNOW, RAIN = (1.0, 1.0), (0.5, 1.0), (0.5, 0.0)   # (intensity, type) targets
CLEAR = (0.0, 0.0)


def snow_cold(temp_c, lo):
    return min(max(1.0 - (temp_c + 2.0) / 8.0, lo), 1.5)


class Snow:
    def __init__(self, air_c=-2.0):
        self.air_c, self.chill, self.road, self.side = air_c, 0.0, 0.0, 0.0

    def tick(self, dt, intensity, ptype):
        k = SNOW_TIME_SCALE
        snow, rain = intensity * ptype, intensity * (1.0 - ptype)
        snowing = snow > 0.05
        chill_cold = snow_cold(min(self.air_c, 0.0), 0.75)
        if snowing:
            self.chill = min(self.chill + dt * k * snow * chill_cold / SNOW_CONTACT_MELT_SECONDS, 1.0)
        else:
            self.chill = max(self.chill - dt * k / SNOW_CONTACT_MELT_SECONDS, 0.0)
        build = snow * ROAD_SNOW_CM_PER_MIN
        melt = (0.0 if snowing else SNOW_MELT_CM_PER_MIN / snow_cold(self.air_c, 0.25)) + rain * RAIN_MELT_CM_PER_MIN
        road_rate = k * ((build if self.chill >= 1.0 else 0.0) - melt) / 60.0
        side_rate = k * (build * SIDE_SNOW_MULT - melt) / 60.0
        self.road = min(max(self.road + road_rate * dt, 0.0), ROAD_SNOW_CAP_CM)
        self.side = min(max(self.side + side_rate * dt, 0.0), SIDE_SNOW_CAP_CM)


def run(s, weather, minutes, dt=0.5):
    """Ticks s under weather; returns (first-stick min, cap min, side-cap min) or None each."""
    stick = cap = side_cap = None
    for i in range(int(minutes * 60 / dt)):
        s.tick(dt, *weather)
        t = (i + 1) * dt / 60.0
        if stick is None and s.road > 0.0:
            stick = t
        if cap is None and s.road >= ROAD_SNOW_CAP_CM - 1e-6:
            cap = t
        if side_cap is None and s.side >= SIDE_SNOW_CAP_CM - 1e-6:
            side_cap = t
    return stick, cap, side_cap


if __name__ == "__main__":
    b = Snow()
    b_stick, b_cap, b_side = run(b, BLIZZARD, 30)
    h = Snow()
    h_stick, h_cap, h_side = run(h, SNOW, 40)
    print(f"blizzard: road sticks {b_stick:.2f} min, 30 cm at {b_cap:.2f} min, roadside 90 cm at {b_side:.2f} min")
    print(f"snow    : road sticks {h_stick:.2f} min, 30 cm at {h_cap:.2f} min, roadside 90 cm at {h_side:.2f} min")

    # road melts first (1-2 min in a blizzard) while the roadside already builds
    assert 1.0 <= b_stick <= 2.0, b_stick
    early = Snow()
    run(early, BLIZZARD, 1.0)
    assert early.road == 0.0 and early.side > 0.0, (early.road, early.side)
    # blizzard: 30 cm at ~10 min; heavy snow ~2x as long
    assert 9.0 <= b_cap <= 11.0, b_cap
    assert 1.8 <= h_cap / b_cap <= 2.2, h_cap / b_cap
    # roadside caps at 90 cm, never above
    assert b.side == SIDE_SNOW_CAP_CM and h.side == SIDE_SNOW_CAP_CM
    # colder = sticks sooner, warmer (still snowing) = later, all in 1-2 min
    for air in (-10.0, 0.0, 15.0):
        st = run(Snow(air), BLIZZARD, 5)[0]
        print(f"  blizzard at {air:+.0f} C: sticks {st:.2f} min")
        assert 1.0 <= st <= 2.05, (air, st)   # +1 tick: first positive depth

    # snow stops: depth melts back slowly; rain melts it much faster
    d0 = b.road
    run(b, CLEAR, 10)
    print(f"clear 10 min: road {d0:.1f} -> {b.road:.1f} cm, side -> {b.side:.1f} cm")
    assert 0.0 < b.road < d0 and b.side < SIDE_SNOW_CAP_CM
    r = Snow()
    run(r, BLIZZARD, 30)
    run(r, RAIN, 10)
    print(f"rain 10 min : road 30.0 -> {r.road:.1f} cm")
    assert r.road < b.road
    run(b, CLEAR, 120)
    assert b.road == 0.0, b.road
    print("snow depth ok")
