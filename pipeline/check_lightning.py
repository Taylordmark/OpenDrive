"""Lightning bolt check (PLAN-REALISM Phase 6, 2026-10-09). Mirrors
AODAtmosphereController::Strike / AddBoltPath (constants parsed from the C++)
over many random strikes: the main bolt runs cloud base -> ground in one
connected chain, stays within a sane sideways spread, the branch hangs off
it, and the width keeps a far bolt visible.

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
    print("PASS: lightning bolt mirror")


if __name__ == "__main__":
    main()
