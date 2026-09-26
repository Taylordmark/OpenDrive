"""Offline check for the Weather W3 grip multiplier (next steps.md sec 7.41).

Mirrors WeatherGrip() in OpenDriveUE/Source/OpenDriveUE/ODVehicleMovement.cpp
- same constant names; change both. Run: python pipeline/check_weather_grip.py
"""

# MIRRORED from ODVehicleMovement.cpp (anonymous namespace)
WET_LOSS = 0.20
SLUSH_LOSS = 0.40
SNOW_LOSS = 0.55
ICE_LOSS = 0.70
TREAD_RELIEF = 0.40
TREAD_OFFROAD_LO = 0.6   # street OffRoadGrip = no relief
TREAD_OFFROAD_HI = 1.0   # offroad OffRoadGrip = full relief

# garage OffRoadGrip per preset (unreal/apply_tuning_presets.py)
STREET, RALLY, OFFROAD, DRAG = 0.6, 0.9, 1.0, 0.45


def clamp01(x):
    return min(max(x, 0.0), 1.0)


def weather_grip(wetness, precip_type, snow_cover, freeze, off_road_grip):
    w, t = clamp01(wetness), clamp01(precip_type)
    slush = 1.0 - abs(2.0 * t - 1.0)
    snow = max(t, clamp01(snow_cover))
    ice = clamp01(freeze) * clamp01(2.0 * (1.0 - t))
    loss = w * max(WET_LOSS, SLUSH_LOSS * slush, SNOW_LOSS * snow, ICE_LOSS * ice)
    tread = clamp01((off_road_grip - TREAD_OFFROAD_LO) / (TREAD_OFFROAD_HI - TREAD_OFFROAD_LO))
    return 1.0 - loss * (1.0 - TREAD_RELIEF * tread)


CASES = {  # name: (wetness, precip_type, snow_cover, freeze), expected street grip
    "dry": ((0.0, 0.0, 0.0, 0.0), 1.00),
    "dry winter": ((0.0, 1.0, 1.0, 1.0), 1.00),
    "wet": ((1.0, 0.0, 0.0, 0.0), 0.80),
    "sleet/slush": ((1.0, 0.5, 0.0, 0.0), 0.60),
    "snow": ((1.0, 1.0, 0.0, 0.0), 0.45),
    "wet on snow cover": ((1.0, 0.0, 1.0, 0.0), 0.45),
    "ice (sleet, frozen)": ((1.0, 0.5, 0.0, 1.0), 0.30),
    "ice (freezing rain)": ((1.0, 0.0, 0.0, 1.0), 0.30),
}

if __name__ == "__main__":
    print(f"{'case':22s} {'street':>7s} {'rally':>7s} {'offroad':>7s} {'drag':>7s}")
    for name, (args, want) in CASES.items():
        g = {k: weather_grip(*args, v) for k, v in
             (("street", STREET), ("rally", RALLY), ("offroad", OFFROAD), ("drag", DRAG))}
        print(f"{name:22s} {g['street']:7.3f} {g['rally']:7.3f} {g['offroad']:7.3f} {g['drag']:7.3f}")
        assert abs(g["street"] - want) < 0.01, (name, g["street"], want)
        assert g["drag"] == g["street"]  # below street tread = no relief
        if want < 1.0:  # rally/offroad tires lose proportionally less
            assert g["street"] < g["rally"] < g["offroad"], name
            assert (1 - g["rally"]) / (1 - g["street"]) < 1.0

    # monotonic (non-increasing) in Wetness, and continuous (no snapping)
    steps = [i / 50 for i in range(51)]
    for t in steps[::5]:
        for s in (0.0, 0.5, 1.0):
            for f in (0.0, 0.5, 1.0):
                for tire in (STREET, RALLY):
                    gs = [weather_grip(w, t, s, f, tire) for w in steps]
                    assert all(b <= a + 1e-9 for a, b in zip(gs, gs[1:])), (t, s, f)
                    assert all(a - b < 0.02 for a, b in zip(gs, gs[1:])), (t, s, f)
                pt = [weather_grip(1.0, x, s, f, STREET) for x in steps]
                assert all(abs(a - b) < 0.04 for a, b in zip(pt, pt[1:])), ("type", s, f)
    assert min(weather_grip(w, t, s, f, STREET) for w in steps for t in steps
               for s in (0, 1) for f in (0, 1)) >= 0.3 - 1e-6
    print("weather grip ok")
