"""ABS should help braking, not hurt it (2026-10-09, Taylor: "abs should be
helping braking not hurting it right?"; earlier: "holding S seemed to brake less
and less the slower I got").

Mirrors UODVehicleMovement::TickTires for one braked corner at the fixed 1/120 s
step: tire Fx from the slip ratio, the wheel integrates the tire reaction, then
the brake pass. Old ABS cut brake torque by a memoryless slip-overshoot factor
(min 0.05): at 120 Hz the brake re-locks the wheel within a step, the cut dumps
it, the tire spins it back up - the wheel cycles across the peak, and worse as
speed (wheel omega) falls. New ABS: the brake pass may not pull the wheel's road
speed below (1 - target) * Vx - real ABS holding the tire at its slip target.

Usage:  python pipeline/check_abs.py
"""
MU, PEAK, SLIDE = 1.05, 0.14, 0.72                  # MuPeak, PeakSlipRatio, SlideFrac
TARGET = 0.13                                       # AbsSlipTarget
I, R, M, DT = 1.5, 0.34, 1500.0, 1 / 120            # wheel inertia, m, kg, s
BRAKE_NM = 3000.0                                   # pedal floored: well past lock


def fx_of(slip):
    sc = abs(slip) / PEAK
    shape = 1.5 * sc - 0.5 * sc ** 3 if sc <= 1 else 1 + (SLIDE - 1) * min((sc - 1) / 1.5, 1)
    return (1 if slip > 0 else -1) * MU * M / 4 * 9.81 * shape if sc > 1e-6 else 0.0


def run(mode, v0=25.0, v_end=2.0):
    q = M / 4
    v, w, t, dist, cycles, prev_cut = v0, v0 / R, 0.0, 0.0, 0, False
    while v > v_end:
        ws = w * R
        slip = (ws - v) / max(abs(v), abs(ws), 1.0)
        fx = fx_of(slip)
        w -= fx * R / I * DT                        # tire reaction spins the wheel up
        v += fx / q * DT
        delta = BRAKE_NM / I * DT
        if mode == "old" and slip < -TARGET:
            delta *= min(max(1 - (-slip - TARGET) * 6, 0.05), 1.0)
        cut = mode == "old" and slip < -TARGET
        w_new = max(w - delta, 0.0)
        if mode == "new":                           # hold the slip target
            floor = (1 - TARGET) * v / R
            w_new = max(w_new, min(w, floor))
            cut = w_new > max(w - delta, 0.0)
        cycles += cut and not prev_cut
        prev_cut, w = cut, w_new
        dist += v * DT
        t += DT
    return (v0 - v) / t / 9.81, dist, cycles


def main():
    res = {m: run(m) for m in ("locked", "old", "new")}
    for m, (g, d, c) in res.items():
        print(f"{m:6s}  25 -> 2 m/s: mean decel {g:.2f} g  distance {d:5.1f} m  ABS on-cycles {c}")
    lock_g, old_g, new_g = res["locked"][0], res["old"][0], res["new"][0]
    assert new_g > lock_g and new_g > old_g, "new ABS out-brakes both a locked wheel and the old ABS"
    assert new_g > 0.95 * MU, "holds near peak grip all the way down"
    assert res["new"][2] <= 2, "no on/off cycling"
    print("PASS: ABS holds the slip target and helps braking")


if __name__ == "__main__":
    main()
