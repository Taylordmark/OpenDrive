"""Race gate times independent of frame rate (2026-10-09, Taylor: "everything
in the game should be independent of frame rate"). Mirrors the sub-frame
entry time in AODRaceManager::Tick: the first root of |F + t*D| = R on the
straight path from last Tick's position to this one. A car at 30 m/s through
a start gate and a finish gate 1 km apart must time 33.333 s at any fps; the
old frame-stamped time was off by up to a frame.

Usage:  python pipeline/check_gate_timing.py
"""
import math

R, V = 2200.0, 3000.0                       # GateRadius cm, car cm/s
GATES = [(0.0, 0.0), (100000.0, 0.0)]       # start, finish (x, y) cm


def enter(prev, pos, gate, t0, dt):
    dx, dy = pos[0] - prev[0], pos[1] - prev[1]
    fx, fy = prev[0] - gate[0], prev[1] - gate[1]
    a, b, c = dx * dx + dy * dy, 2 * (fx * dx + fy * dy), fx * fx + fy * fy - R * R
    disc = b * b - 4 * a * c
    t = 0.0 if c <= 0 else min(max((-b - math.sqrt(disc)) / (2 * a), 0.0), 1.0) if a > 1e-6 and disc >= 0 else 1.0
    return t0 + t * dt


def lap(fps, interp):
    dt, t, x, times, nxt = 1.0 / fps, 0.0, -50000.0, [], 0
    while nxt < len(GATES):
        prev, t, x = (x, 0.0), t + dt, x + V * dt
        if math.dist((x, 0.0), GATES[nxt]) <= R:
            times.append(enter(prev, (x, 0.0), GATES[nxt], t - dt, dt) if interp else t)
            nxt += 1
    return times[1] - times[0]


def main():
    exact = 100000.0 / V
    for fps in (20, 30, 41, 60, 144):
        old, new = lap(fps, False), lap(fps, True)
        print(f"{fps:4d} fps  frame-stamped {old:.4f} s   interpolated {new:.4f} s")
        assert abs(new - exact) < 1e-6, "interpolated lap time is exact at any fps"
    assert max(abs(lap(f, False) - exact) for f in (20, 30, 41)) > 1e-3, "mirror reproduces the frame snap"
    print(f"PASS: lap {exact:.4f} s at every frame rate")


if __name__ == "__main__":
    main()
