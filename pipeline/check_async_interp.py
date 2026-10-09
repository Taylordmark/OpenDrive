"""Car jitter on the fixed 120 Hz physics (2026-10-09, Taylor: "the game feeling
incredibly smooth now but the car was jittering around a lot"). drive_fixed
-odtrace: ODJolt moved/expected clustered at 0.67 and 1.33 at ~25 ms frames,
i.e. the rendered pose advanced 2 or 4 whole physics steps per frame instead
of the interpolated 3.

Mirrors Chaos's async results pull (FChaosResultsChannel::
PullAsyncPhysicsResults_External): the game thread shows the pose at
ResultsTime = now - AsyncDt * p.AsyncInterpolationMultiplier (2), clamped to
the newest finished step. p.AsyncPhysicsBlockMode 0 (default) doesn't wait on
any step dispatched this frame (worst case: none of them finished by the pull);
1 waits for all but the newest step.

Usage:  python pipeline/check_async_interp.py
"""
import math
import random

STEP, MULT = 1 / 120, 2


def run(block_mode, fps, frames=2000, seed=1):
    rng = random.Random(seed)
    t, done_before = 0.0, 0                 # steps dispatched by earlier frames
    prev_pose, bad = None, 0
    for _ in range(frames):
        dt = rng.uniform(0.8, 1.2) / fps    # frame-time jitter
        t += dt
        steps = math.floor(t / STEP + 1e-9)  # dispatched up to now
        done = done_before if block_mode == 0 else steps - 1
        pose = min(t - STEP * MULT, done * STEP)   # alpha clamps at the newest result
        if prev_pose is not None:
            bad += abs((pose - prev_pose) / dt - 1) > 0.2   # ODJolt's threshold
        prev_pose, done_before = pose, steps
    return bad / (frames - 1)


def main():
    for fps in (30, 40, 60, 144):
        m0, m1 = run(0, fps), run(1, fps)
        print(f"{fps:4d} fps   block 0: {m0:5.1%} frames snap   block 1: {m1:5.1%}")
        assert m1 == 0, "block mode 1: the look-behind always has data"
    assert run(0, 40) > 0.3, "mirror reproduces the drive's 40 fps step snapping"
    print("PASS: pose interpolates smoothly at every frame rate with p.AsyncPhysicsBlockMode=1")


if __name__ == "__main__":
    main()
