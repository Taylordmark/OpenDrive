"""Planar 4-wheel port of UODVehicleMovement's tire model + the 2026-09-25 brake-only
ESC, cornering ABS, tire load sensitivity and gamepad speed-scaled steering,
plus the garage setups (unreal/apply_tuning_presets.py SETUP values).
Checks the rear-axle slide stays bounded per assist mode and per setup's
default assist, and that Drift holds a catchable slide, then repeats the
brake-into-turn + snap checks for every UODVehicleDef car (CARS). Asserts at bottom."""
import math

H_LAT = 0.10                   # Fy applied 35 cm up (LateralForceLiftCm) -> ~10 cm roll lever
MU = 1.05 * 4.0                # MuPeak x GripBoost
PK_SR, PK_SA, SLIDE = 0.14, math.radians(13.0), 0.88
IW = 1.5
ALLOW = {"high": -2.0, "low": 10.0}
RAMP, LOOK = 4.0, 0.2          # EscLookahead (0.12 missed short cars on Offroad)
LOAD_SENS = 0.25               # TireLoadSensitivity


def make_car(fx, rx, ht, rad, mass, f, di, comz=10.0, fb=4500.0, rb=3000.0,
             tq=420.0, layout="RWD", mu=1.05, pk=13.0, slide=0.88, steer=38.0):
    """UODVehicleDef fields -> sim car. fx/rx/ht/rad in cm like the def; COM X sits
    at rx + f*(fx - rx) so a = L*(1-f), b = L*f; Iz = DI*m*a*b. Drive proxy: the
    old 3000 Nm/rear wheel (and 250 Nm/wheel lift-off drag) scaled by
    peak_torque/420, split evenly over the driven wheels."""
    L = (fx - rx) / 100.0
    a, b = L * (1 - f), L * f
    driven = {"RWD": (2, 3), "FWD": (0, 1), "AWD": (0, 1, 2, 3)}[layout]
    k = tq / 420.0 * 2 / len(driven)
    return dict(M=mass, IZ=di * mass * a * b, A=a, B=b, T=ht / 100.0, R=rad / 100.0,
                H=0.45 + (comz - 10.0) / 100.0, fs=f, BRK=[fb, fb, rb, rb],
                drive=[3000.0 * k if i in driven else 0.0 for i in range(4)],
                drag=[250.0 * k if i in driven else 0.0 for i in range(4)],
                mu=mu, pk=pk, slide=slide, steer=steer)


# Legacy car the setup checks were tuned on: COM at the axle midpoint-ish
# (a/b 1.32/1.28 = 49.2% front, IZ 2600 = DI 1.03) with the old equal static
# wheel loads (fs=0.5). Kept verbatim so those results don't move.
BASE = dict(make_car(132, -128, 84, 34, 1500.0, 0.5, 1.0), IZ=2600.0, A=1.32, B=1.28)


# Garage setups: tuple order matches the preset script's SETUP columns that
# the physics sees (friction scales, surface grips, slip add, slide floor,
# default assist, Low allowance, steer lock, rear brake, torque, COM, ride).
def setup(fg=1.0, rg=1.0, on=1.0, off=0.6, add=0.0, slmin=0.0, mode="high",
          allow=10.0, lock=1.0, rbrake=1.0, tq=1.0, com=42.0, ride=0.0):
    return dict(fg=fg, rg=rg, on=on, off=off, add=add, slmin=slmin, mode=mode,
                allow=allow, lock=lock, rbrake=rbrake, tq=tq, com=com, ride=ride)

SETUPS = {
    "Street":  setup(),
    "Rally":   setup(fg=0.95, rg=0.95, on=0.95, off=0.90, add=3, slmin=0.92, mode="low",
                     allow=15, lock=1.1, com=36, ride=4),
    "Offroad": setup(fg=0.90, rg=0.90, on=0.90, off=1.00, add=7, tq=1.1, com=38, ride=8 + 4),  # +4: 1.12x tire
    "Drift":   setup(fg=1.05, rg=0.72, off=0.5, slmin=0.95, mode="low", allow=25, lock=1.3,
                     rbrake=1.2, tq=1.3, com=40, ride=-1),
    "Drag":    setup(rg=1.25, off=0.45, lock=0.85, rbrake=0.8, tq=1.6, com=38, ride=-1),
}
STREET = SETUPS["Street"]


def tire(vx, vy, omega, fz, mu, pk_sa, slide, fz0, R):
    sr = (omega * R - vx) / max(abs(vx), abs(omega * R), 1.0)
    sa = math.atan2(-vy, abs(vx) + 0.7)
    sx, sy = sr / PK_SR, sa / pk_sa
    sc = math.hypot(sx, sy)
    if sc < 1e-6:
        return 0.0, 0.0, sr
    shape = 1.5 * sc - 0.5 * sc ** 3 if sc <= 1 else 1 + (slide - 1) * min((sc - 1) / 1.5, 1)
    f = mu * fz * max(1 - LOAD_SENS * (fz / fz0 - 1), 0.4) * shape
    return f * sx / sc, f * sy / sc, sr


def run(mode, v0, steer_fn, brake_fn, thr_fn, r0=0.0, vy0=0.0, t_end=4.0, dt=1 / 240,
        st=STREET, surf="road", slides=None, car=BASE):
    M, IZ, A, B, T, R, BRK = (car[k] for k in ("M", "IZ", "A", "B", "T", "R", "BRK"))
    POS = [(A, -T), (A, T), (-B, -T), (-B, T)]      # FL FR RL RR, y right
    pk_sa = math.radians(car["pk"] + st["add"])
    slide_keep = max(car["slide"], st["slmin"])
    surf_g = st["on"] if surf == "road" else st["off"]
    mus = [MU / 1.05 * car["mu"] * surf_g * (st["fg"] if i < 2 else st["rg"]) for i in range(4)]
    h = car["H"] + (st["com"] - 42.0 + st["ride"]) / 100.0
    brk_max = [BRK[i] * (st["rbrake"] if i >= 2 else 1.0) for i in range(4)]
    x_v, y_v, r = v0, vy0, r0                      # body-frame vel, yaw rate
    om = [v0 / R] * 4
    ax = ay = 0.0
    esc_u, esc_wheel, prev, rate = 0.0, -1, 0.0, 0.0
    max_over, t = -99.0, 0.0
    while t < t_end:
        beta = math.degrees(math.atan2(y_v - r * B, max(x_v, 1.0)))   # signed rear slip
        steer, brake, thr = steer_fn(t, beta), brake_fn(t), thr_fn(t)
        mph = abs(x_v) * 2.23694
        taper = (1 - 0.15 * mph / 30) if mph <= 30 else (0.85 - 0.2 * (mph - 30) / 30) if mph <= 60 else max(0.5, 0.65 - 0.15 * (mph - 60) / 60)
        d = math.radians(car["steer"] * st["lock"] * steer * taper)

        # --- ESC (mirror of TickDrivetrain)
        target = 0.0
        vry = y_v - r * B                          # rear-axle lateral vel (x=-B)
        slip = math.degrees(math.atan2(abs(vry), max(x_v, 1.0)))
        if mode != "off" and x_v > 4.0:
            rate += ((slip - prev) / dt - rate) * min(dt * 15, 1)
            pred = slip + max(rate, 0) * LOOK
            lim = math.degrees(pk_sa) + (ALLOW["high"] if mode == "high" else st["allow"])
            target = min(max((pred - lim) / RAMP, 0), 1)
            if target > 0:
                esc_wheel = 0 if vry < 0 else 1
            prev = slip
        esc_u += (target - esc_u) * min(dt * 15, 1)
        if x_v > 8.0:
            max_over = max(max_over, slip - math.degrees(pk_sa))
            if slides is not None:
                slides.append(slip - math.degrees(pk_sa))

        # drive untouched by ESC; only engine-brake drag is cut (MSR)
        drive = [thr * car["drive"][i] * st["tq"] - (0 if thr > 0.05 else car["drag"][i] * (1 - esc_u))
                 for i in range(4)]                                 # per wheel, Nm
        brk = []
        for i in range(4):
            out = i == esc_wheel
            keep = 1.0 if out else 1 - esc_u
            brk.append(brake * brk_max[i] * keep + (esc_u * BRK[i] * (1 - brake) if out else 0))

        # loads: static + transfer from last tick's accel, capped 2.5x
        fz0 = M * 9.8 / 4
        fzs = []
        for i, (px, py) in enumerate(POS):
            fst = M * 9.8 * (car["fs"] if px > 0 else 1 - car["fs"]) / 2
            f = fst - (M * ax * h / (A + B)) / 2 * (1 if px > 0 else -1) - (M * ay * H_LAT / (2 * T)) / 2 * (1 if py > 0 else -1)
            fzs.append(min(max(f, 0), 2.5 * fz0))

        FX = FY = MZ = 0.0
        sr_all, sa_all = [], []
        for i, (px, py) in enumerate(POS):
            wd = d if i < 2 else 0.0
            vx_p, vy_p = x_v - r * py, y_v + r * px
            c, s = math.cos(wd), math.sin(wd)
            vxw, vyw = vx_p * c + vy_p * s, -vx_p * s + vy_p * c
            fx, fy, sr = tire(vxw, vyw, om[i], fzs[i], mus[i], pk_sa, slide_keep, fz0, R)
            sr_all.append(sr)
            sa_all.append(math.atan2(-vyw, abs(vxw) + 0.7))
            fbx, fby = fx * c - fy * s, fx * s + fy * c
            FX += fbx; FY += fby; MZ += px * fby - py * fbx
            om[i] += (drive[i] - fx * R) / IW * dt
        # ABS select-low per axle; cornering ABS shrinks the target with slip angle
        for a in (0, 2):
            fac = 1.0
            for i in (a, a + 1):
                tgt = 0.13 * math.sqrt(max(1 - (sa_all[i] / pk_sa) ** 2, 0.05))
                if brk[i] > 1 and sr_all[i] < -tgt:
                    fac = min(fac, max(1 - (-sr_all[i] - tgt) * 6, 0.05))
            for i in (a, a + 1):
                dw = brk[i] * fac / IW * dt
                om[i] = math.copysign(max(abs(om[i]) - dw, 0), om[i])

        ax, ay = FX / M, FY / M
        x_v += (ax + r * y_v) * dt
        y_v += (ay - r * x_v) * dt
        r += MZ / IZ * dt
        t += dt
        if x_v < 2:
            break
    return max_over, x_v


# Scenarios mirror the pawn's input paths (ODVehiclePawn.cpp ~1293): the keyboard
# caps steer at ~0.4 and ramps 1.0/s at 70 mph; the gamepad stick now gets the same
# lock cap (0.4 at 70 mph), so a 0.7 flick lands at 0.28.
def kb_steer(t0):
    return lambda t, b: 0.0 if t < t0 else min(0.4, (t - t0) * 1.0)
def pad_steer(t0, amt):
    return lambda t, b: 0.0 if t < t0 else min(amt, (t - t0) / 0.1 * amt)

SCEN = {
    # brake hard, turn in while braking, release mid-corner (the reported case)
    "kb_trail":  dict(v0=31, steer_fn=kb_steer(0.3), brake_fn=lambda t: 1.0 if t < 1.0 else 0.0, thr_fn=lambda t: 0.0),
    "pad_trail": dict(v0=31, steer_fn=pad_steer(0.3, 0.7 * 0.4), brake_fn=lambda t: 1.0 if t < 1.0 else 0.0, thr_fn=lambda t: 0.0),
    # brake, release, THEN turn (lift-off: engine drag on the rears)
    "pad_release_turn": dict(v0=31, steer_fn=pad_steer(0.9, 0.7 * 0.4), brake_fn=lambda t: 1.0 if t < 0.8 else 0.0, thr_fn=lambda t: 0.0),
    # rear already 20 deg out, driver keeps steer + throttle
    "snap": dict(v0=30, r0=1.6, vy0=-9.0, steer_fn=lambda t, b: 0.4, brake_fn=lambda t: 0.0, thr_fn=lambda t: 0.6),
}
res = {}
for name, sc in SCEN.items():
    for mode in ("off", "low", "high"):
        over, v = run(mode, t_end=3.0, **sc)
        res[(name, mode)] = over
        print(f"{name:17s} {mode:4s} max rear slide past tire peak {over:6.1f} deg   end {v:5.1f} m/s")

for name in SCEN:
    if name == "snap":
        continue
    assert res[(name, "high")] <= 1.0, (name, res[(name, "high")])
    assert res[(name, "low")] <= ALLOW["low"] + 3.0, (name, res[(name, "low")])
# already-sliding start: assists must not make it worse than a few deg over Off
assert res[("snap", "high")] <= res[("snap", "off")] + 2.0, res

# ---- garage setups: each on its own default assist
# (a) the reported brake-into-turn case, on tarmac and on dirt
# (b) a power slide held by a countersteering driver (Drift must hold it)
def countersteer(target_deg):
    # the player: countersteer against the slide, aim for target slip angle
    return lambda t, b: max(-1.0, min(1.0, 0.4 - 0.08 * (-b - target_deg)))

def brake_drift(target):
    # NFS brake-to-drift: turn in on the brakes, catch it once the rear is 10 deg out
    cs, turn, st = countersteer(target), kb_steer(0.3), {"caught": False}
    def f(t, b):
        st["caught"] |= -b > 10.0
        return cs(t, b) if st["caught"] else turn(t, b)
    return f

POWER = dict(v0=20, r0=1.2, vy0=-4.0, steer_fn=countersteer(25.0),
             brake_fn=lambda t: 0.0, thr_fn=lambda t: 0.8)
print()
for name, st in SETUPS.items():
    m = st["mode"]
    cap = {"high": 1.0, "low": st["allow"] + 3.0, "off": 45.0}[m]
    for sc in ("kb_trail", "pad_trail", "pad_release_turn"):
        for surf in ("road", "dirt"):
            over, _ = run(m, st=st, surf=surf, t_end=3.0, **SCEN[sc])
            print(f"{name:8s} {m:4s} {sc:16s} {surf}: slide {over:6.1f} deg (cap {cap:.0f})")
            # Drift is loose on purpose: hands-off brake-into-turn spins it
            # (that's how its drifts start) - the catch test below covers it
            assert name == "Drift" or over <= cap, (name, sc, surf, over)
    sl = []
    over, v = run(m, st=st, t_end=3.0, slides=sl, **POWER)
    held = sum(sl[-240:]) / 240 if len(sl) >= 240 else -99   # last ~1 s
    print(f"{name:8s} {m:4s} power slide: peak {over:6.1f}, last-1s avg {held:6.1f} deg, end {v:4.1f} m/s")
    if name == "Drift":
        assert 5.0 < held and over < 45.0, ("drift must hold a catchable slide", held, over)
        for aim in (20.0, 30.0):
            sl = []
            over, v = run(m, st=st, t_end=4.0, slides=sl, v0=31, steer_fn=brake_drift(aim),
                          brake_fn=lambda t: 1.0 if t < 1.0 else 0.0,
                          thr_fn=lambda t: 0.0 if t < 1.0 else 0.6)
            print(f"{name:8s} {m:4s} brake-drift aim {aim:.0f}: peak {over:5.1f}, final {sl[-1]:5.1f} deg, end {v:4.1f} m/s")
            assert over < 45.0 and sl[-1] < 25.0 and v > 5.0, ("brake drift not catchable", aim, over, sl[-1], v)
    elif m == "high":
        assert over <= 3.0, (name, "power slide on High", over)

# off-tarmac grip ordering (tire x surface, rear axle) - the point of the setups
dirt = {n: st["rg"] * st["off"] for n, st in SETUPS.items()}
assert dirt["Offroad"] > dirt["Rally"] > dirt["Street"] > dirt["Drift"], dirt

# ---- per-car defs (unreal/import_car_meshes.py + apply_vehicle_defs.py values):
# axle/track/radius cm, mass, front_weight_frac, yaw_dynamic_index, com_local_z,
# front/rear brake Nm per wheel, peak_torque_nm, layout (+ GT's tuned tire/lock).
# Brakes: 7500 Nm total x mass/1500 x radius/34, front share ~ split + 0.1.
# What the brake-into-turn cases punish is COM-to-front-axle distance over COM
# height (the rear unloads under the ~3 g GripBoost braking) and low Iz, so the
# short cars need low COM / more front bias / DI >= 1.05 to stay catchable.
CARS = {
    "Coupe":   make_car(132, -128, 84, 34, 1500, 0.53, 1.00, 10, 4500, 3000, 420, "RWD"),
    "GT":      make_car(132, -128, 84, 34, 1350, 0.52, 0.95, 6, 5200, 3600, 520, "AWD",
                        mu=1.32, pk=8.0, slide=0.80, steer=36.0),
    "Muscle":  make_car(140, -150, 90, 36, 1620, 0.55, 1.05, 12, 5550, 3000, 560, "RWD"),
    "JDM":     make_car(124, -114, 80, 32, 1380, 0.52, 1.05, 4, 4300, 2200, 450, "RWD"),
    "Super":   make_car(118, -150, 94, 36, 1430, 0.42, 0.88, 5, 3950, 3650, 650, "RWD"),
    "Pickup":  make_car(185, -160, 98, 42, 2380, 0.57, 1.10, 20, 9850, 4850, 650, "AWD"),
    "Rally":   make_car(112, -118, 78, 32, 1310, 0.56, 1.10, 6, 4800, 1350, 440, "AWD"),
    "Concept": make_car(128, -140, 92, 35, 1150, 0.42, 0.88, 5, 3100, 2850, 500, "RWD"),
    "LMP":     make_car(150, -140, 88, 34, 1030, 0.45, 0.85, 3, 2850, 2300, 600, "RWD"),
}
# (Rally needed +3 deg on Street/Low at the old 0.12 s lookahead; 0.2 s fixed it)
LOW_EXTRA = {}
print()
for cn, car in CARS.items():
    r = {}
    for sc in ("kb_trail", "pad_trail", "pad_release_turn", "snap"):
        for mode in ("off", "low", "high"):
            r[(sc, mode)] = run(mode, t_end=3.0, car=car, **SCEN[sc])[0]
    trail = [sc for sc in SCEN if sc != "snap"]
    hi, lo = max(r[(s_, "high")] for s_ in trail), max(r[(s_, "low")] for s_ in trail)
    print(f"{cn:8s} Iz {car['IZ']:5.0f}  a/b {car['A']:.2f}/{car['B']:.2f}  street brake-into-turn peak: "
          f"High {hi:5.1f}  Low {lo:5.1f}  snap off/low/high {r[('snap', 'off')]:5.1f}/"
          f"{r[('snap', 'low')]:5.1f}/{r[('snap', 'high')]:5.1f}")
    assert hi <= 1.0 and lo <= ALLOW["low"] + 3.0 + LOW_EXTRA.get(cn, 0.0), (cn, hi, lo)
    assert max(r[("snap", "high")], r[("snap", "low")]) <= r[("snap", "off")] + 2.0, (cn, r)
rs = SETUPS["Rally"]
for sc in ("kb_trail", "pad_trail", "pad_release_turn"):
    for surf in ("road", "dirt"):
        over, _ = run(rs["mode"], st=rs, surf=surf, t_end=3.0, car=CARS["Rally"], **SCEN[sc])
        print(f"Rally car on Rally setup {sc:16s} {surf}: slide {over:6.1f} deg (cap {rs['allow'] + 3:.0f})")
        assert over <= rs["allow"] + 3.0, ("Rally car", sc, surf, over)

# every car x every garage setup, on the setup's default assist
print()
for cn, car in CARS.items():
    row = []
    for sn, st in SETUPS.items():
        m = st["mode"]
        if sn == "Drift":
            sl = []
            over, v = run(m, st=st, t_end=4.0, slides=sl, car=car, v0=31, steer_fn=brake_drift(20.0),
                          brake_fn=lambda t: 1.0 if t < 1.0 else 0.0,
                          thr_fn=lambda t: 0.0 if t < 1.0 else 0.6)
            row.append(f"{sn} catch {over:4.0f}")
            assert over < 45.0 and sl[-1] < 25.0 and v > 5.0, (cn, sn, over, sl[-1], v)
            continue
        cap = 1.0 if m == "high" else st["allow"] + 3.0
        worst = max(run(m, st=st, surf=s_, t_end=3.0, car=car, **SCEN[sc])[0]
                    for sc in ("kb_trail", "pad_trail", "pad_release_turn") for s_ in ("road", "dirt"))
        row.append(f"{sn} {worst:5.1f}/{cap:.0f}")
        assert worst <= cap, (cn, sn, worst)
    print(f"{cn:8s} " + "  ".join(row))
print("OK")
