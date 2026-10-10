"""Planar 4-wheel port of UODVehicleMovement's tire model + the 2026-09-25 brake-only
ESC, slip-target ABS (2026-10-09), tire load sensitivity, speed-tapered steering
and the physical grip model (2026-10-09, Taylor: grip "should vary by car
(downforce, weight) and by tire", not be a direct multiplier):
  Fz   = static axle share + aero share + load transfer   (per wheel, N)
  aero = DownforceCoeff * v^2, split AeroBalanceFront / rear  (DownforceCoeff = rho/2 * ClA)
  mu   = MuPeak(compound) * surface * setup scale * max(1 - k*(Fz/Fz_axle_static - 1), 0.4)
so lateral/brake g come out of each car's compound, weight split, COM height and
aero - no global grip multiplier (the old GripBoost x4 is gone).
Runs at the sim's fixed 120 Hz step. Checks: per-car grip bands, rear load under
full braking, brake+steer stability, frame-rate independence, then the older
garage-setup / ESC checks (CARS, SETUPS). Asserts throughout; prints OK."""
import math

G = 9.8
DT = 1 / 120                   # AsyncFixedTimeStepSize (DefaultEngine.ini)
H_LAT = 0.10                   # Fy applied 35 cm up (LateralForceLiftCm) -> ~10 cm roll lever
PK_SR = 0.14
IW = 1.5
ABS_TARGET = 0.13              # AbsSlipTarget
ESC_ABS_SCALE = 2.5            # EscAbsSlipScale: ESC's braked wheel may slip past ABS's target
ALLOW = {"high": -2.0, "low": 10.0}
RAMP, LOOK = 4.0, 0.2          # EscLookahead (0.12 missed short cars on Offroad)

# Tire compounds: (MuPeak on dry asphalt, TireLoadSensitivity k). Mirrors the
# table in ODVehicleDef.h. Race tires lose less mu per unit of extra load.
COMPOUND = {
    "all-terrain": (0.90, 0.25),
    "street":      (1.00, 0.25),
    "performance": (1.05, 0.25),
    "sport":       (1.15, 0.22),
    "semi-slick":  (1.25, 0.20),
    "slick":       (1.60, 0.15),
}


def make_car(fx, rx, ht, rad, mass, f, di, comz=10.0, fb=4500.0, rb=3000.0,
             tq=420.0, layout="RWD", tyre="performance", rr=1.0, df=0.0, bal=0.45,
             pk=13.0, slide=0.88, steer=38.0, rpm=4600.0):
    """UODVehicleDef fields -> sim car. fx/rx/ht/rad in cm like the def; COM X sits
    at rx + f*(fx - rx) so a = L*(1-f), b = L*f; Iz = DI*m*a*b. Drive proxy: 1st-gear
    torque (4.25 x 2.81) up to the power limit tq*rpm, over the driven wheels;
    lift-off drag the old 250 Nm/wheel x tq/420. pk/slide get ApplyVehicleDef's floors."""
    L = (fx - rx) / 100.0
    a, b = L * (1 - f), L * f
    driven = {"RWD": (2, 3), "FWD": (0, 1), "AWD": (0, 1, 2, 3)}[layout]
    mu, ls = COMPOUND[tyre]
    return dict(M=mass, IZ=di * mass * a * b, A=a, B=b, T=ht / 100.0, R=rad / 100.0,
                H=0.45 + (comz - 10.0) / 100.0, fs=f, BRK=[fb, fb, rb, rb],
                driven=driven, tq1=tq * 4.25 * 2.81, P=tq * rpm * 2 * math.pi / 60,
                drag=[250.0 * tq / 420 * 2 / len(driven) if i in driven else 0.0 for i in range(4)],
                mu=mu, ls=ls, rr=rr, df=df, bal=bal, tyre=tyre,
                pk=max(pk, 13.0), slide=max(slide, 0.88), steer=steer)


# Legacy car the setup checks were tuned on: COM at the axle midpoint-ish
# (a/b 1.32/1.28 = 49.2% front, IZ 2600 = DI 1.03) with the old equal static
# wheel loads (fs=0.5).
BASE = dict(make_car(132, -128, 84, 34, 1500.0, 0.5, 1.0, rr=1.06, df=0.25, bal=0.47), IZ=2600.0, A=1.32, B=1.28)


# Garage setups: the preset script's fields that the physics sees (friction
# scales, surface grips, slip add, slide floor, default assist, Low allowance,
# steer lock, rear brake, torque, COM, ride, Downforce vs Street's 1.1).
def setup(fg=1.0, rg=1.0, on=1.0, off=0.6, add=0.0, slmin=0.0, mode="high",
          allow=10.0, lock=1.0, rbrake=1.0, tq=1.0, com=42.0, ride=0.0, dfs=1.1):
    return dict(fg=fg, rg=rg, on=on, off=off, add=add, slmin=slmin, mode=mode,
                allow=allow, lock=lock, rbrake=rbrake, tq=tq, com=com, ride=ride, dfs=dfs / 1.1)

SETUPS = {
    "Street":  setup(),
    "Rally":   setup(fg=0.95, rg=0.95, on=0.95, off=0.90, add=3, slmin=0.92, mode="low",
                     allow=15, lock=1.1, com=36, ride=4, dfs=0.5),
    "Offroad": setup(fg=0.90, rg=0.90, on=0.90, off=1.00, add=7, tq=1.1, com=38, ride=8 + 4, dfs=0.2),  # +4: 1.12x tire
    "Drift":   setup(fg=1.05, rg=0.80, off=0.5, slmin=0.95, mode="low", allow=25, lock=1.3,
                     rbrake=1.2, tq=1.3, com=40, ride=-1, dfs=0.6),
    "Drag":    setup(rg=1.25, off=0.45, lock=0.85, rbrake=0.8, tq=1.6, com=38, ride=-1, dfs=0.9),
}
STREET = SETUPS["Street"]


def tire(vx, vy, omega, fz, mu, ls, pk_sa, slide, fz0, R):
    sr = (omega * R - vx) / max(abs(vx), abs(omega * R), 1.0)
    sa = math.atan2(-vy, abs(vx) + 0.7)
    sx, sy = sr / PK_SR, sa / pk_sa
    sc = math.hypot(sx, sy)
    if sc < 1e-6:
        return 0.0, 0.0, sr
    shape = 1.5 * sc - 0.5 * sc ** 3 if sc <= 1 else 1 + (slide - 1) * min((sc - 1) / 1.5, 1)
    f = mu * fz * max(1 - ls * (fz / fz0 - 1), 0.4) * shape
    return f * sx / sc, f * sy / sc, sr


def run(mode, v0, steer_fn, brake_fn, thr_fn, r0=0.0, vy0=0.0, t_end=4.0, st=STREET,
        surf="road", slides=None, car=BASE, pin=False, trace=None, frame_dt=None, abs_on=True):
    """pin: hold forward speed at v0 (steady-state grip probes). trace: list that
    gets (t, ax, ay, rear_slip_deg, rear_load_share) per step. frame_dt: drive the
    fixed 120 Hz step from a render-frame accumulator like the async physics tick."""
    M, IZ, A, B, T, R, BRK = (car[k] for k in ("M", "IZ", "A", "B", "T", "R", "BRK"))
    POS = [(A, -T), (A, T), (-B, -T), (-B, T)]      # FL FR RL RR, y right
    pk_sa = math.radians(car["pk"] + st["add"])
    slide_keep = max(car["slide"], st["slmin"])
    surf_g = st["on"] if surf == "road" else st["off"]
    mus = [car["mu"] * surf_g * (st["fg"] if i < 2 else st["rg"] * car["rr"]) for i in range(4)]
    h = car["H"] + (st["com"] - 42.0 + st["ride"]) / 100.0
    df = car["df"] * st["dfs"]
    brk_max = [BRK[i] * (st["rbrake"] if i >= 2 else 1.0) for i in range(4)]
    fst = [M * G * (car["fs"] if i < 2 else 1 - car["fs"]) / 2 for i in range(4)]   # tire nominal load
    x_v, y_v, r = v0, vy0, r0                      # body-frame vel, yaw rate
    om = [v0 / R] * 4
    ax = ay = 0.0
    esc_u, esc_wheel, prev, rate = 0.0, -1, 0.0, 0.0
    tc, trace_slip = 1.0, 0.0                      # TC High (TcScale, TraceSlip)
    max_over = -99.0
    steps = int(round(t_end / DT))
    if frame_dt is None:
        schedule = [steps]
    else:                                          # render frames -> whole fixed steps
        schedule, acc, done = [], 0.0, 0
        while done < steps:
            acc += frame_dt
            n = min(int(acc / DT + 1e-9), steps - done)
            acc -= n * DT
            done += n
            schedule.append(n)
    k = 0
    for n_steps in schedule:
        for _ in range(n_steps):
            t = k * DT                                 # inputs keyed to sim time
            k += 1
            beta = math.degrees(math.atan2(y_v - r * B, max(x_v, 1.0)))   # signed rear slip
            steer, brake = steer_fn(t, beta), brake_fn(t)
            thr = thr_fn(t, beta) if thr_fn.__code__.co_argcount == 2 else thr_fn(t)
            mph = abs(x_v) * 2.23694
            taper = (1 - 0.15 * mph / 30) if mph <= 30 else (0.85 - 0.2 * (mph - 30) / 30) if mph <= 60 else max(0.5, 0.65 - 0.15 * (mph - 60) / 60)
            d = math.radians(car["steer"] * st["lock"] * steer * taper)

            # --- ESC (mirror of TickDrivetrain)
            target = 0.0
            vry = y_v - r * B                          # rear-axle lateral vel (x=-B)
            slip = math.degrees(math.atan2(abs(vry), max(x_v, 1.0)))
            if mode != "off" and x_v > 4.0:
                rate += ((slip - prev) / DT - rate) * min(DT * 15, 1)
                pred = slip + max(rate, 0) * LOOK
                lim = math.degrees(pk_sa) + (ALLOW["high"] if mode == "high" else st["allow"])
                target = min(max((pred - lim) / RAMP, 0), 1)
                if target > 0:
                    esc_wheel = 0 if vry < 0 else 1
                prev = slip
            esc_u += (target - esc_u) * min(DT * 15, 1)
            if x_v > 8.0:
                max_over = max(max_over, slip - math.degrees(pk_sa))
                if slides is not None:
                    slides.append(slip - math.degrees(pk_sa))

            # drive untouched by ESC; only engine-brake drag is cut (MSR)
            f_drive = min(car["tq1"] / R, car["P"] / max(abs(x_v), 1.0)) * st["tq"]
            if mode == "high" and thr > 0.05:          # TC High: cut fast past 0.22 slip, restore 3/s
                tc = min(max(tc + (-(trace_slip - 0.22) * 10 * DT if trace_slip > 0.22 else 3 * DT), 0.35), 1.0)
            else:
                tc = 1.0
            drive = [(thr * f_drive * tc * R / len(car["driven"]) if i in car["driven"] else 0.0)
                     - (0 if thr > 0.05 else car["drag"][i] * (1 - esc_u)) for i in range(4)]
            brk = []
            for i in range(4):
                out = i == esc_wheel
                keep = 1.0 if out else 1 - esc_u
                brk.append(brake * brk_max[i] * keep + (esc_u * BRK[i] * (1 - brake) if out else 0))

            # loads: static + aero (TickAero, at the balance point) + transfer
            # from last step's accel; grip load capped at 2.5x static + aero share
            dfz = df * x_v * x_v
            fzs = []
            for i, (px, py) in enumerate(POS):
                fa = dfz * (car["bal"] if px > 0 else 1 - car["bal"]) / 2
                f = fst[i] + fa - (M * ax * h / (A + B)) / 2 * (1 if px > 0 else -1) \
                    - (M * ay * H_LAT / (2 * T)) / 2 * (1 if py > 0 else -1)
                fzs.append(min(max(f, 0), 2.5 * fst[i] + fa))

            FX = FY = MZ = 0.0
            sa_all, vxr = [], []
            trace_slip = 0.0
            for i, (px, py) in enumerate(POS):
                wd = d if i < 2 else 0.0
                vx_p, vy_p = x_v - r * py, y_v + r * px
                c, s = math.cos(wd), math.sin(wd)
                vxw, vyw = vx_p * c + vy_p * s, -vx_p * s + vy_p * c
                fx, fy, sr = tire(vxw, vyw, om[i], fzs[i], mus[i], car["ls"], pk_sa, slide_keep, fst[i], R)
                vxr.append(vxw)
                if i in car["driven"]:
                    trace_slip = max(trace_slip, abs(sr))
                sa_all.append(math.atan2(-vyw, abs(vxw) + 0.7))
                fbx, fby = fx * c - fy * s, fx * s + fy * c
                FX += fbx; FY += fby; MZ += px * fby - py * fbx
                om[i] += (drive[i] - fx * R) / IW * DT
            # ABS: the brake may not pull road speed below (1 - target) x Vx
            for i in range(4):
                braked = max(abs(om[i]) - brk[i] / IW * DT, 0.0)
                floor = 0.0
                if abs_on and abs(x_v) > 2.0 and brk[i] > 1.0 and om[i] * vxr[i] > 0:
                    tgt = ABS_TARGET * (ESC_ABS_SCALE if i == esc_wheel and esc_u > 0.05
                                        else math.sqrt(max(1 - (sa_all[i] / pk_sa) ** 2, 0.05)))
                    floor = min(abs(om[i]), (1 - tgt) * abs(vxr[i]) / R)
                om[i] = math.copysign(max(braked, floor), om[i])

            ax, ay = FX / M, FY / M
            if trace is not None:
                trace.append((t, ax, ay, slip, (fzs[2] + fzs[3]) / max(sum(fzs), 1.0)))
            if not pin:
                x_v += (ax + r * y_v) * DT
            y_v += (ay - r * x_v) * DT
            r += MZ / IZ * DT
            if x_v < 2:
                return max_over, x_v
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
    "snap": dict(v0=30, r0=0.4, vy0=-10.4, steer_fn=lambda t, b: 0.4, brake_fn=lambda t: 0.0, thr_fn=lambda t: 0.6),
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

def drift_throttle(target_deg, t0=0.0):
    # ...and ride the throttle: more slide than aimed -> ease off (with ~1 g
    # tires a fixed 0.6-0.8 throttle just spins the rears up and around)
    return lambda t, b: 0.0 if t < t0 else max(0.0, min(1.0, 0.4 + 0.08 * (target_deg + b)))

# rear ~15 deg out at 20 m/s with a ~1 g yaw rate (the old r0=1.2 rad/s start
# was a 2.4 g yaw rate only the x4 grip could hold)
POWER = dict(v0=20, r0=0.5, vy0=-4.9, steer_fn=countersteer(25.0),
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
    # Drift's driver rides the throttle; the others floor it (worst case)
    over, v = run(m, st=st, t_end=3.0, slides=sl, **dict(POWER, thr_fn=drift_throttle(25.0))
                  if name == "Drift" else POWER)
    held = sum(sl[-120:]) / 120 if len(sl) >= 120 else -99   # last ~1 s
    print(f"{name:8s} {m:4s} power slide: peak {over:6.1f}, last-1s avg {held:6.1f} deg, end {v:4.1f} m/s")
    if name == "Drift":
        assert 5.0 < held and over < 45.0, ("drift must hold a catchable slide", held, over)
        # (a 30 deg aim was dropped with GripBoost: at ~1 g the scripted P-driver
        # catches or loses it chaotically - a feel-test item, not a check)
        for aim in (20.0,):
            sl = []
            over, v = run(m, st=st, t_end=4.0, slides=sl, v0=31, steer_fn=brake_drift(aim),
                          brake_fn=lambda t: 1.0 if t < 1.0 else 0.0,
                          thr_fn=drift_throttle(aim, 1.0))
            print(f"{name:8s} {m:4s} brake-drift aim {aim:.0f}: peak {over:5.1f}, final {sl[-1]:5.1f} deg, end {v:4.1f} m/s")
            assert over < 45.0 and sl[-1] < 25.0 and v > 5.0, ("brake drift not catchable", aim, over, sl[-1], v)
    elif m == "high":
        # already 2.4 deg past peak and rotating at the start: at ~1 g ESC's
        # outside-front brake moment is ~1/4 of the x4-grip era's, so it
        # carries a few deg further before turning (was <= 3.0)
        assert over <= 7.0, (name, "power slide on High", over)

# off-tarmac grip ordering (tire x surface, rear axle) - the point of the setups
dirt = {n: st["rg"] * st["off"] for n, st in SETUPS.items()}
assert dirt["Offroad"] > dirt["Rally"] > dirt["Street"] > dirt["Drift"], dirt

# ---- per-car defs (unreal/import_car_meshes.py + apply_vehicle_defs.py values):
# axle/track/radius cm, mass, front_weight_frac, yaw_dynamic_index, com_local_z,
# front/rear brake Nm per wheel, peak_torque_nm, layout, then the grip model:
# compound (MuPeak + TireLoadSensitivity), DownforceCoeff = rho/2*ClA (N per
# (m/s)^2) and AeroBalanceFront = front weight frac - 0.03 (a touch of rear
# aero bias keeps high-speed balance stable). Brakes: 7500 Nm total x mass/1500
# x radius/34, front share ~ split + 0.1.
CARS = {
    # generic sporty coupe, summer tires, small lip/spoiler (ClA 0.4)
    "Coupe":   make_car(132, -128, 84, 34, 1500, 0.53, 1.00, 10, 4500, 3000, 420, "RWD",
                        tyre="performance", rr=1.06, df=0.25, bal=0.50),
    # GT3-RS-style road car: Cup-type semi-slicks, big wing (ClA ~2)
    "GT":      make_car(132, -128, 84, 34, 1350, 0.52, 0.95, 6, 5200, 3600, 520, "AWD",
                        tyre="semi-slick", rr=1.08, df=1.2, bal=0.49, pk=8.0, slide=0.80, steer=36.0, rpm=5200),
    # heavy front-V8 on street tires; stock muscle bodies make ~no downforce
    "Muscle":  make_car(140, -150, 90, 36, 1620, 0.55, 1.05, 12, 5550, 3000, 560, "RWD",
                        tyre="street", rr=1.05, df=0.05, bal=0.52),
    # tuner coupe on performance tires, small wing (ClA ~0.3)
    "JDM":     make_car(124, -114, 80, 32, 1380, 0.52, 1.05, 4, 4300, 2200, 450, "RWD",
                        tyre="performance", rr=1.05, df=0.2, bal=0.49),
    # mid-engine supercar on max-performance tires, active aero (0.5x weight @ 150 mph)
    "Super":   make_car(118, -150, 94, 36, 1430, 0.42, 0.88, 5, 3950, 3650, 650, "RWD",
                        tyre="sport", rr=1.1, df=1.56, bal=0.39),
    # 4x4 pickup on all-terrains; bluff body -> no downforce
    "Pickup":  make_car(185, -160, 98, 42, 2380, 0.57, 1.10, 20, 9850, 4850, 650, "AWD",
                        tyre="all-terrain", rr=1.06, df=0.0, bal=0.54),
    # rally hatch on tarmac-spec performance tires, WRC-ish wing (ClA ~0.5)
    "Rally":   make_car(112, -118, 78, 32, 1310, 0.56, 1.10, 6, 4800, 1350, 440, "AWD",
                        tyre="performance", rr=1.05, df=0.3, bal=0.53),
    # LMP-style road concept: semi-slicks, 1.0x weight @ 150 mph (Taylor, 2026-09-28)
    "Concept": make_car(128, -140, 92, 35, 1150, 0.42, 0.88, 5, 3100, 2850, 500, "RWD",
                        tyre="semi-slick", rr=1.1, df=2.51, bal=0.39),
    # Le Mans prototype: slicks, 1.5x weight @ 150 mph (ClA ~5.5)
    "LMP":     make_car(150, -140, 88, 34, 1030, 0.45, 0.85, 3, 2850, 2300, 600, "RWD",
                        tyre="slick", rr=1.06, df=3.37, bal=0.42),
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
                          thr_fn=drift_throttle(20.0, 1.0))
            row.append(f"{sn} catch {over:4.0f}")
            assert over < 45.0 and sl[-1] < 25.0 and v > 5.0, (cn, sn, over, sl[-1], v)
            continue
        cap = 1.0 if m == "high" else st["allow"] + 3.0
        worst = max(run(m, st=st, surf=s_, t_end=3.0, car=car, **SCEN[sc])[0]
                    for sc in ("kb_trail", "pad_trail", "pad_release_turn") for s_ in ("road", "dirt"))
        row.append(f"{sn} {worst:5.1f}/{cap:.0f}")
        assert worst <= cap, (cn, sn, worst)
    print(f"{cn:8s} " + "  ".join(row))

# --- physical grip (no GripBoost): g comes from compound mu, load sensitivity,
# weight and aero. Lateral = peak 0.25 s average of |ay| on a slow steer ramp at
# held speed, ESC off; brake = full pedal at held speed, last 0.4 s.
def lat_g(car, v):
    tr = []
    run("off", v, lambda t, b: min(t / 4.0, 1.0), lambda t: 0.0, lambda t: 0.1,
        t_end=4.0, car=car, pin=True, trace=tr)
    n, ays = int(0.25 / DT), [abs(x[2]) for x in tr]
    return max(sum(ays[i:i + n]) / n for i in range(len(ays) - n)) / G

def brake_g(car, v):
    tr = []
    run("off", v, lambda t, b: 0.0, lambda t: 1.0, lambda t: 0.0, t_end=1.5, car=car, pin=True, trace=tr)
    last = tr[-int(0.4 / DT):]
    return -sum(x[1] for x in last) / len(last) / G, min(x[4] for x in last)

# (low, high) g band at 20 m/s, and the minimum at 60 m/s
BANDS = {"road": ((0.85, 1.12), 0.85), "sports": ((1.10, 1.40), 1.40), "proto": ((1.50, 1.90), 2.60)}
CLASS = {"GT": "sports", "Super": "sports", "Concept": "sports", "LMP": "proto"}
print()
print("car       mass  tyre         mu   ClA  bal |  lat g 20/40/60  |  brake g 20/40/60 | rear share")
for cn, car in CARS.items():
    lat = [lat_g(car, v) for v in (20, 40, 60)]
    bk = [brake_g(car, v) for v in (20, 40, 60)]
    print(f"{cn:8s} {car['M']:5.0f}  {car['tyre']:11s} {car['mu']:.2f} {car['df'] / 0.6125:4.1f} "
          f"{car['bal']:.2f} | {lat[0]:.2f} {lat[1]:.2f} {lat[2]:.2f}  |  {bk[0][0]:.2f} {bk[1][0]:.2f} "
          f"{bk[2][0]:.2f}  | {min(b[1] for b in bk):.2f}")
    (lo, hi), at60 = BANDS[CLASS.get(cn, "road")]
    assert lo <= lat[0] <= hi and lat[2] >= at60 and lat[2] >= lat[0] - 0.02, (cn, "lateral g", lat)
    for (g, share), lg in zip(bk, lat):
        assert 0.9 * lg <= g <= 1.1 * lg, (cn, "brakes not tire-limited", g, lg)
        assert share >= 0.2, (cn, "rear axle unloaded under full braking", share)

# brake + steer at speed (the x4 grip used to hide this): Street High keeps the
# rear under 15 deg; Off is printed for information only
print()
for cn, car in CARS.items():
    row = []
    for v0 in (40, 45):
        for amt in (0.1, 0.35):
            res = {}
            for m in ("off", "high"):
                tr = []
                run(m, v0, lambda t, b, a=amt: a * min(max((t - 0.2) / 0.1, 0.0), 1.0), lambda t: 1.0,
                    lambda t: 0.0, t_end=3.0, car=car, trace=tr)
                res[m] = max(abs(x[3]) for x in tr)
            row.append(f"{v0}/{amt}: {res['high']:4.1f} ({res['off']:4.1f})")
            assert res["high"] < 15.0, (cn, "brake+steer spin on High", v0, amt, res["high"])
    print(f"{cn:8s} brake+steer rear slip High (Off): " + "  ".join(row))

# fixed 120 Hz step: the render rate must not change a single sample
ref = None
for fdt in (None, 1 / 30, 1 / 60, 1 / 144, 1 / 240):
    tr = []
    run("high", 45, lambda t, b: 0.35 * min(max((t - 0.2) / 0.1, 0.0), 1.0), lambda t: 1.0,
        lambda t: 0.0, t_end=3.0, car=CARS["GT"], trace=tr, frame_dt=fdt)
    ref = tr if ref is None else ref
    assert tr == ref, ("frame-rate dependent", fdt)
print("frame-rate independence: 30/60/144/240 fps identical to the 120 Hz reference")
print("OK")
