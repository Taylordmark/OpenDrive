"""Mirror of AODWeatherController::TickLens parked-drop motion (2026-10-20):
stick-slip slides heading within +-10 deg of straight down, in bursts, with
nothing slid once airflow owns the drops (15 km/h); merges keep area.
Constants copied from ODWeatherController.cpp (LENS_*). Run: python <this>."""
import math
import random

STILL_MS, SLIDE_MIN_R, SLIDE_SPEED, SLIDE_MAX = 4.2, 0.7, 0.08, 0.22
STICK_S, SLIP_S, MAX_ANGLE, TURN_DEG_S = (0.4, 2.8), (0.15, 0.9), 10.0, 15.0
DROP_SIZE = 0.025


def run(radius, speed_ms, seconds=60.0, dt=1 / 60):
    rng = random.Random(1)
    pos = [0.0, 0.0]
    angle = target = slip = 0.0
    stick = rng.uniform(*STICK_S)
    speed_w = min(max(speed_ms / STILL_MS, 0.0), 1.0)
    max_a = math.radians(MAX_ANGLE)
    turn = math.radians(TURN_DEG_S) * dt
    over = radius / DROP_SIZE - SLIDE_MIN_R
    moving_ticks = ticks = 0
    max_seen = 0.0
    for _ in range(int(seconds / dt)):
        ticks += 1
        sliding = False
        if over > 0 and speed_w < 1:
            if slip > 0:
                slip -= dt
                sliding = True
                if slip <= 0:
                    stick = rng.uniform(*STICK_S) / (1 + over)
            else:
                stick -= dt
                if stick <= 0:
                    slip = rng.uniform(*SLIP_S)
                    target = rng.uniform(-max_a, max_a)
        angle = min(max(angle + min(max(target - angle, -turn), turn), -max_a), max_a)
        if sliding:
            v = min(SLIDE_SPEED * over, SLIDE_MAX) * (1 - speed_w)
            pos[0] += math.sin(angle) * v * dt
            pos[1] -= math.cos(angle) * v * dt
            moving_ticks += v > 0
            max_seen = max(max_seen, abs(angle))
    return pos, moving_ticks / ticks, math.degrees(max_seen)


def demo():
    pos, frac, deg = run(DROP_SIZE * 1.3, 0.0)
    assert pos[1] < -0.05, pos                      # it went down the screen
    assert 0.1 < frac < 0.7, frac                   # in bursts, not a steady slide
    assert deg <= MAX_ANGLE + 1e-6, deg             # never past 10 deg
    assert abs(pos[0]) < abs(pos[1]) * math.tan(math.radians(MAX_ANGLE)) + 1e-9
    assert run(DROP_SIZE * 0.6, 0.0)[1] == 0.0      # small beads stay put
    assert run(DROP_SIZE * 1.3, STILL_MS)[1] == 0.0  # airflow owns them at 15 km/h
    r = math.sqrt(0.02 ** 2 + 0.03 ** 2)            # merge keeps area
    assert abs(r * r - (0.02 ** 2 + 0.03 ** 2)) < 1e-12
    print(f"ok: slid {-pos[1]:.3f} half-widths down, moving {frac:.0%} of the time, max {deg:.1f} deg")


if __name__ == "__main__":
    demo()
