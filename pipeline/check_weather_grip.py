"""Offline check for the Weather W3 grip multiplier (next steps.md sec 7.41).

Mirrors WeatherGrip()/MudGrip() and SurfaceGrip()'s unpaved tier in
OpenDriveUE/Source/OpenDriveUE/ODVehicleMovement.cpp - same constant names;
change both. Run: python pipeline/check_weather_grip.py
"""

# MIRRORED from ODVehicleMovement.cpp (anonymous namespace)
WET_LOSS = 0.20
SLUSH_LOSS = 0.40
SNOW_LOSS = 0.55
ICE_LOSS = 0.70
TREAD_RELIEF = 0.40
TREAD_OFFROAD_LO = 0.6   # street OffRoadGrip = no relief
TREAD_OFFROAD_HI = 1.0   # offroad OffRoadGrip = full relief
SLUSH_FULL_CM = 2.0      # road snow depth terms (AODRoadManager chunk depth)
SNOW_START_CM = 2.0
SNOW_FULL_CM = 8.0

# garage OffRoadGrip per preset (unreal/apply_tuning_presets.py)
STREET, RALLY, OFFROAD, DRAG = 0.6, 0.9, 1.0, 0.45
# ... and OnRoadGrip
ON_ROAD = {STREET: 1.0, RALLY: 0.95, OFFROAD: 0.90, DRAG: 1.0}

# UODVehicleMovement unpaved-tier knob defaults (PLAN-REALISM Phase 2)
GRAVEL_GRIP, DIRT_GRIP = 0.80, 0.70
GRAVEL_WET_LOSS, DIRT_WET_LOSS = 0.10, 0.25


def clamp01(x):
    return min(max(x, 0.0), 1.0)


def weather_grip(wetness, precip_type, snow_cover, freeze, off_road_grip, depth_cm=0.0):
    w, t = clamp01(wetness), clamp01(precip_type)
    slush = 1.0 - abs(2.0 * t - 1.0)
    snow = clamp01(snow_cover)
    ice = clamp01(freeze) * clamp01(2.0 * (1.0 - t))
    loss = max(w * max(WET_LOSS, SLUSH_LOSS * slush, SNOW_LOSS * snow, ICE_LOSS * ice),
               SLUSH_LOSS * clamp01(depth_cm / SLUSH_FULL_CM),
               SNOW_LOSS * clamp01((depth_cm - SNOW_START_CM) / (SNOW_FULL_CM - SNOW_START_CM)))
    tread = clamp01((off_road_grip - TREAD_OFFROAD_LO) / (TREAD_OFFROAD_HI - TREAD_OFFROAD_LO))
    return 1.0 - loss * (1.0 - TREAD_RELIEF * tread)


def mud_grip(wetness, off_road_grip, wet_loss):
    tread = clamp01((off_road_grip - TREAD_OFFROAD_LO) / (TREAD_OFFROAD_HI - TREAD_OFFROAD_LO))
    return 1.0 - clamp01(wetness) * wet_loss * (1.0 - TREAD_RELIEF * tread)


def surface_grip(surface, off_road_grip, wetness=0.0):
    """SurfaceGrip() for 'asphalt'/'gravel'/'dirt'/'off' in plain rain."""
    wx = weather_grip(wetness, 0.0, 0.0, 0.0, off_road_grip)
    on = ON_ROAD[off_road_grip]
    if surface == "asphalt":
        return on * wx
    if surface == "gravel":
        return max(on * GRAVEL_GRIP, off_road_grip) * wx * mud_grip(wetness, off_road_grip, GRAVEL_WET_LOSS)
    if surface == "dirt":
        return max(on * DIRT_GRIP, off_road_grip) * wx * mud_grip(wetness, off_road_grip, DIRT_WET_LOSS)
    return off_road_grip


CASES = {  # name: (wetness, precip_type, snow_cover, freeze[, road snow cm]), expected street grip
    "dry": ((0.0, 0.0, 0.0, 0.0), 1.00),
    "dry winter": ((0.0, 1.0, 1.0, 1.0), 1.00),
    "wet": ((1.0, 0.0, 0.0, 0.0), 0.80),
    "sleet/slush": ((1.0, 0.5, 0.0, 0.0), 0.60),
    "snow falling, no depth": ((1.0, 1.0, 0.0, 0.0), 0.80),
    "road slush 2 cm": ((1.0, 1.0, 0.0, 0.0, 2.0), 0.60),
    "packed snow 8 cm": ((1.0, 1.0, 0.0, 0.0, 8.0), 0.45),
    "packed snow 30 cm": ((0.0, 1.0, 0.0, 0.0, 30.0), 0.45),
    "wet on snow cover": ((1.0, 0.0, 1.0, 0.0), 0.45),
    "ice (sleet, frozen)": ((1.0, 0.5, 0.0, 1.0), 0.30),
    "ice (freezing rain)": ((1.0, 0.0, 0.0, 1.0), 0.30),
}

if __name__ == "__main__":
    print(f"{'case':22s} {'street':>7s} {'rally':>7s} {'offroad':>7s} {'drag':>7s}")
    for name, (args, want) in CASES.items():
        g = {k: weather_grip(*args[:4], v, *args[4:]) for k, v in
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
    # non-increasing and continuous in road snow depth
    gd = [weather_grip(1.0, 1.0, 0.0, 0.0, STREET, d / 10) for d in range(301)]
    assert all(b <= a + 1e-9 and a - b < 0.03 for a, b in zip(gd, gd[1:]))
    print("weather grip ok")

    # unpaved tier: street tires asphalt > gravel > dirt > off-road when dry;
    # rain costs unpaved more than asphalt (mud), dirt the most
    print(f"\n{'surface grip':22s} {'street':>7s} {'rally':>7s} {'offroad':>7s} {'drag':>7s}")
    for w in (0.0, 1.0):
        for s in ("asphalt", "gravel", "dirt", "off"):
            g = [surface_grip(s, tire, w) for tire in (STREET, RALLY, OFFROAD, DRAG)]
            print(f"{s + (' wet' if w else ' dry'):22s} " + " ".join(f"{x:7.3f}" for x in g))
    assert abs(surface_grip("gravel", STREET) - 0.80) < 1e-6
    assert abs(surface_grip("dirt", STREET) - 0.70) < 1e-6
    for tire in (STREET, RALLY, OFFROAD, DRAG):
        dry = {s: surface_grip(s, tire) for s in ("asphalt", "gravel", "dirt")}
        wet = {s: surface_grip(s, tire, 1.0) for s in ("asphalt", "gravel", "dirt")}
        assert dry["gravel"] >= dry["dirt"] >= tire          # never below the verge, dry
        assert wet["asphalt"] / dry["asphalt"] > wet["gravel"] / dry["gravel"] > wet["dirt"] / dry["dirt"]
    assert surface_grip("asphalt", STREET) > surface_grip("gravel", STREET) > surface_grip("dirt", STREET) > STREET
    print("unpaved grip ok")
