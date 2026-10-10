"""Keyboard steer/pedal ramp (2026-10-09, Taylor: "the current inputs make it
difficult to stay on the road"; then fast-tap steering). Mirrors KbSteerStep /
RampKeyAxis + the keyboard block of
AODVehiclePawn::Tick (tuning: "Keyboard driving" in ODVehiclePawn.h) and
asserts: ramp-in / counter times, tap-rhythm settle levels (duty cycle sets
the lock), an identical steer trace at 30/60/144/240 fps (and with jittered
frame times), and the speed cap values (front tyre near its peak slip).

Key events sit on a 1/6 s grid (a whole number of frames at every tested
fps): the game applies a key change from the frame it's read, so off-grid
events differ by up to one frame - input sampling, not the ramp math.

Usage:  python pipeline/check_kbm_steer_ramp.py
"""
import random

RAMP_IN, COUNTER, TAU, EXP = 0.35, 0.12, 0.2, 1.2   # ODVehiclePawn.h
TARGET_G, SLIP_FRAC, WHEELBASE_M = 1.5, 0.8, 2.7
PEAK_SLIP, MAX_STEER = 9.0, 38.0   # UODVehicleMovement defaults
import math
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


def steer_step(r, key, dt):   # KbSteerStep
    if key == 0:
        return r * math.exp(-dt / TAU)
    if r != 0 and sign(r) != sign(key):
        need = abs(r) * COUNTER
        if dt <= need:
            return r - sign(r) * dt / COUNTER
        r, dt = 0.0, dt - need
    return interp_const(r, key, dt, 1 / RAMP_IN)


def taper(mph):   # UODVehicleMovement::SteerTaper
    if mph <= 30: return 1 + (0.85 - 1) * mph / 30
    if mph <= 60: return 0.85 + (0.65 - 0.85) * (mph - 30) / 30
    if mph <= 120: return 0.65 + (0.5 - 0.65) * (mph - 60) / 60
    return 0.5


def cap(speed_cms):
    v = max(speed_cms / 100, 0.1)
    deg = math.degrees(WHEELBASE_M * TARGET_G * 9.81 / (v * v)) + SLIP_FRAC * PEAK_SLIP
    return min(max(deg / max(MAX_STEER * taper(speed_cms / MPH), 1), 0), 1)


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
        r = steer_step(r, k, dt)
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
    # (a) times + tap rhythm
    def t_to(target, r, key, dt=1e-4):
        t = 0.0
        while abs(r - target) > 1e-4:
            r, t = steer_step(r, key, dt), t + dt
        return t
    t_in, t_ctr = t_to(1.0, 0.0, 1), t_to(0.0, 1.0, -1)
    t_rel = t_to(0.05, 1.0, 0)
    print(f"center->full {t_in:.3f}s  full->center (opposite key) {t_ctr:.3f}s  full->5% (released) {t_rel:.3f}s")
    assert abs(t_in - RAMP_IN) < 2e-3 and abs(t_ctr - COUNTER) < 2e-3 and 0.5 < t_rel < 0.7
    levels = {}
    for duty in (0.3, 0.5, 0.7):
        r, per = 0.0, 1 / 8   # 8 Hz taps
        for _ in range(80):
            r = steer_step(r, 1, duty * per)
            r = steer_step(r, 0, (1 - duty) * per)
        levels[duty] = r
        print(f"  8 Hz taps, {duty:.0%} duty -> ramp settles {r:.2f} (end of gap)")
    assert 0.1 < levels[0.3] < levels[0.5] < levels[0.7] and levels[0.5] > 0.3
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

    # (c) speed cap: held key puts the front tyre near its peak slip
    L = WHEELBASE_M
    for mph, lo, hi in ((10, 1.0, 1.0), (20, 1.0, 1.0), (30, 0.55, 0.7), (60, 0.35, 0.5), (80, 0.35, 0.5), (120, 0.35, 0.5)):
        c = cap(mph * MPH)
        deg = c * MAX_STEER * taper(mph)
        v = mph * 0.44704
        kin = math.degrees(L * 9.81 / (v * v))   # angle for 1 g with no slip
        print(f"  {mph:>3} mph: cap {c:.3f}  road wheel {deg:5.2f} deg  (1 g geometry {kin:5.2f} + slip)")
        assert lo - 1e-3 <= c <= hi + 1e-3, (mph, c)
        if mph >= 30:
            assert deg <= kin * TARGET_G + SLIP_FRAC * PEAK_SLIP + 1e-3   # never past target g + tyre peak

if __name__ == "__main__":
    main()
    print("OK")
