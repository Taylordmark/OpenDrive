"""Keyboard steer/pedal ramp (2026-10-09, Taylor: "the current inputs make it
difficult to stay on the road"). Mirrors RampKeyAxis + the keyboard block of
AODVehiclePawn::Tick (tuning: "Keyboard driving" in ODVehiclePawn.h) and
asserts: ramp-in / return times, an identical steer trace at 30/60/144/240 fps
(and with jittered frame times), and the speed cap values.

Key events sit on a 1/6 s grid (a whole number of frames at every tested
fps): the game applies a key change from the frame it's read, so off-grid
events differ by up to one frame - input sampling, not the ramp math.

Usage:  python pipeline/check_kbm_steer_ramp.py
"""
import random

RAMP_IN, RETURN, EXP, FULL_LOCK_CMS = 0.6, 0.2, 1.5, 715.0   # ODVehiclePawn.h
PEDAL_IN, PEDAL_OUT = 0.25, 0.1
MPH = 44.704   # cm/s


def sign(x):
    return (x > 0) - (x < 0)


def interp_const(cur, tgt, dt, speed):   # FMath::FInterpConstantTo
    d = tgt - cur
    if d * d < 1e-8:
        return tgt
    step = speed * dt
    return cur + max(-step, min(step, d))


def ramp(r, tgt, dt, rin, rout):   # RampKeyAxis
    same = tgt != 0 and sign(tgt) == sign(r)
    if r != 0 and not (same and abs(tgt) >= abs(r)):
        stop = tgt if same else 0.0
        need = abs(r - stop) / rout
        if dt <= need:
            return r + sign(stop - r) * rout * dt
        r, dt = stop, dt - need
    return interp_const(r, tgt, dt, rin)


def cap(speed_cms):
    return (FULL_LOCK_CMS / max(speed_cms, FULL_LOCK_CMS)) ** 2


def steer_out(r, speed_cms):
    return sign(r) * abs(r) ** EXP * cap(speed_cms)


# key script: (start_s, key) on the 1/6 s grid; +1 = D, -1 = A
SCRIPT = [(0, 0), (1 / 6, 1), (4 / 6, 0), (5 / 6, -1), (8 / 6, 1), (10 / 6, 0), (13 / 6, 0)]


def key_at(t):
    k = 0
    for start, key in SCRIPT:
        if t + 1e-9 >= start:
            k = key
    return k


def trace(frame_times, speed_cms=0.0):
    """Steer sampled at each 1/6 s grid point."""
    r, t, out = 0.0, 0.0, {}
    for dt in frame_times:
        k = key_at(t)         # key held through this frame
        r = ramp(r, k, dt, 1 / RAMP_IN, 1 / RETURN)
        t += dt
        g = round(t * 6)
        if abs(t * 6 - g) < 1e-6:
            out[g] = steer_out(r, speed_cms)
    return out


def time_to(target, key_seq_r0, key, rin, rout, dt=1e-4):
    r, t = key_seq_r0, 0.0
    while abs(r - target) > 1e-6:
        r = ramp(r, key, dt, rin, rout)
        t += dt
    return t


def main():
    # (a) times
    t_in = time_to(1.0, 0.0, 1, 1 / RAMP_IN, 1 / RETURN)
    t_out = time_to(0.0, 1.0, 0, 1 / RAMP_IN, 1 / RETURN)
    t_rev = time_to(-1.0, 1.0, -1, 1 / RAMP_IN, 1 / RETURN)
    print(f"center->full lock {t_in:.3f}s  full->center {t_out:.3f}s  full L->full R {t_rev:.3f}s")
    assert abs(t_in - RAMP_IN) < 1e-3 and abs(t_out - RETURN) < 1e-3
    assert abs(t_rev - (RAMP_IN + RETURN)) < 1e-3 and t_out < t_in
    for tap in (0.05, 0.1, 0.2, 0.3):
        print(f"  {tap * 1000:.0f} ms tap -> steer {steer_out(tap / RAMP_IN, 0):.3f} of lock (stopped)")
    assert steer_out(0.1 / RAMP_IN, 0) < 0.08
    p_in = time_to(1.0, 0.0, 1, 1 / PEDAL_IN, 1 / PEDAL_OUT)
    p_out = time_to(0.0, 1.0, 0, 1 / PEDAL_IN, 1 / PEDAL_OUT)
    print(f"pedal 0->full {p_in:.3f}s  full->0 {p_out:.3f}s")
    assert abs(p_in - PEDAL_IN) < 1e-3 and abs(p_out - PEDAL_OUT) < 1e-3

    # (b) same trace at any fps
    dur = 15 / 6
    ref = trace([1 / 1200] * round(dur * 1200))
    for fps in (30, 60, 144, 240):
        tr = trace([1 / fps] * round(dur * fps))
        err = max(abs(tr[g] - ref[g]) for g in ref)
        print(f"{fps:>4} fps: max diff vs 1200 fps on grid = {err:.2e}  "
              f"samples {[round(tr[g], 3) for g in sorted(tr)][:12]}")
        assert set(tr) == set(ref) and err < 1e-5, fps
    # jittered frame times that still land on the grid (variable fps)
    rng = random.Random(1)
    frames = []
    for _ in range(15):
        cuts = sorted(rng.uniform(0, 1 / 6) for _ in range(rng.randint(1, 12)))
        edges = [0.0] + cuts + [1 / 6]
        frames += [b - a for a, b in zip(edges, edges[1:]) if b > a]
    tr = trace(frames)
    err = max(abs(tr[g] - ref[g]) for g in ref)
    print(f"jittered frames: max diff = {err:.2e}")
    assert err < 1e-5

    # (c) speed cap. Road-wheel deg = steer * MaxSteer(38) * movement Taper
    def taper(mph):   # UODVehicleMovement::TickSuspension
        if mph <= 30: return 1 + (0.85 - 1) * mph / 30
        if mph <= 60: return 0.85 + (0.65 - 0.85) * (mph - 30) / 30
        if mph <= 120: return 0.65 + (0.5 - 0.65) * (mph - 60) / 60
        return 0.5
    import math
    L = 2.7   # m, typical wheelbase
    for mph, want in ((10, 1.0), (16, 1.0), (30, 0.284), (60, 0.071), (80, 0.040), (120, 0.0178)):
        c = cap(mph * MPH)
        deg = c * 38 * taper(mph)
        v = mph * 0.44704
        g = v * v * math.tan(math.radians(deg)) / L / 9.81
        print(f"  {mph:>3} mph: cap {c:.3f}  road wheel {deg:5.2f} deg  kinematic {g:.2f} g")
        assert abs(c - want) < 2e-3, (mph, c)
        if mph >= 30:
            assert g < 1.2   # held key never asks for more than ~1 g


if __name__ == "__main__":
    main()
    print("OK")
