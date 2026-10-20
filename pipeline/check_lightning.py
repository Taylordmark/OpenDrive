"""Lightning bolt check (PLAN-REALISM Phase 6, 2026-10-09). Mirrors
AODAtmosphereController::Strike / AddBoltPath (constants parsed from the C++)
over many random strikes: the main bolt runs cloud base -> ground in one
connected chain, stays within a sane sideways spread, the branch hangs off
it, and the width keeps a far bolt visible. Also the flash timing
(2026-10-14): the frame-mean envelope (FlashIntegral) gives the same light per
strike at any frame rate and never skips the first stroke, and strikes carry
their countdown overshoot so strike/thunder instants don't depend on fps.

Usage:  python pipeline/check_lightning.py
"""
import re
from pathlib import Path

import numpy as np

CPP = (Path(__file__).resolve().parent.parent / "OpenDriveUE/Source/OpenDriveUE/ODAtmosphereController.cpp").read_text(encoding="utf-8")


def const(name):
    return float(re.search(rf"constexpr (?:float|int32) {name} = ([\d.]+)f?;", CPP).group(1))


H_M, SEGS, BRANCH, JITTER = const("BoltCloudBaseM"), int(const("BoltSegments")), int(const("BoltBranchSegments")), const("BoltJitter")
W_PER_KM, W_MIN = const("BoltWidthPerKm"), const("BoltMinWidthM")
FLASH_S, STROKE_S, DIP_END_S = const("FlashSeconds"), const("FlashStrokeS"), const("FlashDipEndS")
DIP, TAIL = const("FlashDipLevel"), const("FlashTailLevel")


def envelope(t):
    if t < 0 or t > FLASH_S:
        return 0.0
    if t < STROKE_S:
        return 1.0
    return DIP if t < DIP_END_S else TAIL * (1 - (t - DIP_END_S) / (FLASH_S - DIP_END_S))


def flash_integral(t):
    """FlashIntegral mirror."""
    a = min(max(t, 0.0), STROKE_S)
    b = min(max(t, STROKE_S), DIP_END_S) - STROKE_S
    c = min(max(t, DIP_END_S), FLASH_S) - DIP_END_S
    return a + DIP * b + TAIL * (c - 0.5 * c * c / (FLASH_S - DIP_END_S))


def run_storm(rng, dts, intervals):
    """UpdateLightning's countdown + flash mirror over a frame-time sequence:
    strike instants and the light (sum of Flash x dt) per strike."""
    t, countdown, age, strikes, light = 0.0, 5.0, -1.0, [], []
    it = iter(intervals)
    for dt in dts:
        t += dt
        if age >= 0:
            age += dt
        countdown -= dt
        if countdown <= 0:
            late = -countdown
            countdown += next(it)
            age = late
            strikes.append(t - late)
            light.append(0.0)
        if age < 0 or age - dt >= FLASH_S:
            age = -1.0
            continue
        light[-1] += flash_integral(age) - flash_integral(age - dt)   # Flash x dt
    return strikes, light


def check_flash_timing():
    ts = np.linspace(-0.1, FLASH_S + 0.1, 20001)
    num = np.concatenate([[0], np.cumsum([(envelope(a) + envelope(b)) / 2 * (b - a) for a, b in zip(ts[:-1], ts[1:])])])
    assert np.abs(num - [flash_integral(x) for x in ts]).max() < 1e-3, "FlashIntegral = integral of the envelope"
    rng = np.random.default_rng(2)
    intervals = rng.uniform(8, 30, 200)
    total = flash_integral(FLASH_S)
    ref = None
    for fps in (8, 15, 30, 60, 144, 240):
        dts = np.full(int(600 * fps), 1.0 / fps) * rng.uniform(0.7, 1.3, int(600 * fps))   # uneven frames
        strikes, light = run_storm(rng, dts, intervals)
        assert np.allclose(light[:-1], total, atol=1e-5), f"{fps} fps: same light per strike"
        n = len(strikes) - 1
        ref = strikes[:n] if ref is None else ref
        m = min(n, len(ref))
        assert np.allclose(strikes[:m], ref[:m], atol=1e-6), f"{fps} fps: strike instants independent of fps"
    # first frame window at 8 fps still sees the stroke (point-sampling at
    # age 0.125 would read the dip)
    assert (flash_integral(0.125) - flash_integral(0.0)) / 0.125 > 0.5
    print(f"flash: light per strike {total:.3f} peak-s at 8..240 fps (uneven frames), strike times match")


def path(rng, a, d, n, step):
    pts = [a]
    for _ in range(n):
        a = a + d * step + np.r_[rng.uniform(-1, 1, 2), 0.0] * step * JITTER
        pts.append(a)
    return np.array(pts)


def strike(rng, dist_m):
    h = H_M * 100.0
    ground = np.r_[dist_m * 100.0, 0.0, 0.0]
    step = h / SEGS
    main = path(rng, ground + np.r_[0, 0, h], np.r_[0, 0, -1.0], SEGS, step)
    out = np.r_[rng.uniform(-1, 1, 2), 0.0]
    out /= np.linalg.norm(out)
    d = np.r_[0, 0, -1.0] + out * 0.8
    branch = path(rng, main[rng.integers(2, SEGS // 2 + 1)], d / np.linalg.norm(d), BRANCH, step * 0.8)
    return main, branch, max(W_MIN, W_PER_KM * dist_m / 1000.0)


def main():
    rng = np.random.default_rng(1)
    spreads = []
    for _ in range(2000):
        dist = rng.uniform(500, 8000)
        m, b, w = strike(rng, dist)
        assert abs(m[-1, 2]) < 1e-6 and abs(m[0, 2] - H_M * 100) < 1e-6, "bolt spans cloud base -> ground"
        assert (np.diff(m[:, 2]) < 0).all(), "main bolt always descends"
        assert any(np.allclose(b[0], p) for p in m), "branch starts on the main bolt"
        assert b[-1, 2] > 0, "branch ends in the air"
        spreads.append(np.abs(m[:, :2] - m[0, :2]).max() / 100.0)
        # pixels at 1080p / 60 deg vfov: w / dist * 1080 / tan(30 deg) / 2
        assert w / dist * 1080 / (2 * np.tan(np.radians(30))) > 1.2, "bolt stays > 1 px wide"
    s = np.array(spreads)
    print(f"bolt: {H_M:.0f} m tall, {SEGS}+{BRANCH} segments; sideways spread m pct 50/99/max "
          f"{np.round(np.percentile(s, [50, 99, 100]), 0)}")
    assert s.max() < H_M * JITTER, "spread bounded by the jitter"
    check_flash_timing()
    print("PASS: lightning bolt mirror + flash timing")


if __name__ == "__main__":
    main()
