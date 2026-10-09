"""Wheel shudder at a near-stop (2026-10-09, Taylor: "why do the wheels move
when stationary"). drive_rev trace, car coasting to rest on a grade in 1st,
no pedals: FxR flipped sign every sample and grew (+/-0.07 -> 0.77 x 1e6) and
the rear wheel's omega stuttered -0.2 / -0.9 rad/s while the car crept 20-40
cm/s.

Mirrors UODVehicleMovement::TickTires for one driven corner: below ~1 m/s the
slip denominator is floored, so tire Fx is far too stiff for the wheel's
inertia and explicit Euler overshoots rolling speed every tick. The fix
(SlipStop) caps coasting Fx at what would close the wheel-vs-road slip in one
tick, given both the corner's mass and the wheel's (reflected) inertia - same
idea and same gate as the existing FxStop, which only bounds the car's side.

Usage:  python pipeline/check_wheel_settle.py
"""
import math

MU, PEAK, SLIDE, RR = 1.05, 0.14, 0.72, 0.015      # MuPeak, PeakSlipRatio, SlideFrac, RollingResistance
I_WHEEL, I_ENG, EFF, RATIO = 1.5, 0.25, 0.9, 3.0 * 3.5   # coupled 1st: reflected engine inertia
R, M, DT = 0.34, 1500.0, 1 / 60                     # m, kg, s
GRADE = 0.07                                        # downhill pull, g


def run(clamp, ticks=120):
    q = M / 4                                       # one corner of the car
    fz = q * 9.81
    inertia = I_WHEEL + I_ENG * RATIO ** 2 * EFF / 2
    slip_mass = 1 / (1 / q + R * R / inertia)       # wheel vs car, at the contact
    v, w = -0.04, 0.0                               # just released, rolling back
    fx_log, w_log = [], []
    for _ in range(ticks):
        ws = w * R
        sx = (ws - v) / max(abs(v), abs(ws), 1.0) / PEAK
        sc = abs(sx)
        shape = 1.5 * sc - 0.5 * sc ** 3 if sc <= 1 else 1 + (SLIDE - 1) * min((sc - 1) / 1.5, 1)
        fx = math.copysign(MU * fz * shape, sx) if sc > 1e-6 else 0.0
        if abs(v) < 1.5:                            # coasting: no brake, no throttle
            stop = q * abs(v) / DT * 0.45           # FxStop
            if clamp:                               # + SlipStop
                stop = min(stop, slip_mass * abs(ws - v) / DT * 0.45)
            fx = max(-stop, min(stop, fx))
        reaction = fx * R + RR * fz * R * math.copysign(1, w) * (w != 0)
        w -= reaction / inertia * DT                # DriveNm = 0 (idle, coasting)
        v += (fx / q - GRADE * 9.81) * DT
        fx_log.append(fx)
        w_log.append(w)
    flips = sum(1 for a, b in zip(fx_log[20:], fx_log[21:]) if a * b < 0)
    jump = max(abs(b - a) for a, b in zip(w_log[20:], w_log[21:]))
    return flips, jump, v


def main():
    for clamp in (False, True):
        flips, jump, v = run(clamp)
        print(f"clamp={clamp!s:5s}  Fx sign flips (100 ticks) {flips:3d}  "
              f"max wheel omega step {jump:.3f} rad/s  speed after 2 s {v * 100:.0f} cm/s")
    flips0, _, _ = run(False)
    flips, jump, _ = run(True)
    assert flips0 > 20, "mirror reproduces the trace's per-tick Fx flip-flop"
    assert flips == 0 and jump < 0.05, "clamped: wheel rolls smoothly, no shudder"
    print("PASS: ground reaction no longer overshoots rolling speed at a near-stop")


if __name__ == "__main__":
    main()
