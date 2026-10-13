"""Car contact-shadow check (PLAN-VISUALS Track B grounding). Mirrors
UODContactShadow::Strength / AlongOnPlane (OpenDriveUE/Source/OpenDriveUE/
ODContactShadow.cpp; constants parsed from it so the mirror can't drift)
and asserts:
  - parked / compressed: full opacity; rest-height jitter (< fade start): no flicker
  - fades monotonically with the gap, gone at fade end (airborne)
  - fades out as the car tips over, full on any drivable slope (<= 35 deg)
  - frame-rate independent: a jump sampled at 30 and 144 fps gives the same
    strength at the same physics time (pure function of the state)
  - decal basis: X = into the ground (-N), Z = car forward on the ground
    plane, orthonormal on slopes and for a nose-down car

Usage:  python pipeline/checks/check_contact_shadow.py
"""
import math
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CPP = (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODContactShadow.cpp").read_text(encoding="utf-8")


def _c(name):
    return float(re.search(rf"constexpr float {name} = ([\d.]+)f;", CPP).group(1))


TILT_FULL, TILT_ZERO = _c("TiltFullUpZ"), _c("TiltZeroUpZ")
BODY = (_c("BodyOpacity"), _c("BodyFadeStartCm"), _c("BodyFadeEndCm"))
WHEEL = (_c("WheelOpacity"), _c("WheelFadeStartCm"), _c("WheelFadeEndCm"))


def smoothstep(a, b, x):   # FMath::SmoothStep
    if x < a:
        return 0.0
    if x >= b:
        return 1.0
    t = (x - a) / (b - a)
    return t * t * (3 - 2 * t)


def strength(gap, start, end, up_z):
    h = 1.0 - smoothstep(start, end, gap)
    t = min(max((up_z - TILT_ZERO) / (TILT_FULL - TILT_ZERO), 0.0), 1.0)
    return h * t


def along_on_plane(fwd, n):
    a = fwd - n * np.dot(fwd, n)
    if np.dot(a, a) > 1e-6:
        return a / np.linalg.norm(a)
    y = np.cross(n, [0, 0, 1] if abs(n[2]) < 0.9 else [1, 0, 0])
    return y / np.linalg.norm(y)


for opacity, start, end in (BODY, WHEEL):
    assert strength(-20.0, start, end, 1.0) == 1.0, "compressed suspension must be full"
    assert strength(start * 0.9, start, end, 1.0) == 1.0, "rest jitter must not flicker"
    assert strength(end, start, end, 1.0) == 0.0, "airborne past fade end must be gone"
    gaps = np.linspace(-10, end + 10, 400)
    s = [strength(g, start, end, 1.0) for g in gaps]
    assert all(b <= a + 1e-9 for a, b in zip(s, s[1:])), "must fade monotonically"
    assert 0 < opacity <= 1

# tilt: full on a 35 deg slope / body roll, gone on the side
assert strength(0, *BODY[1:], math.cos(math.radians(35))) == 1.0
assert strength(0, *BODY[1:], math.cos(math.radians(70))) == 0.0

# frame-rate independence: a ballistic hop (up 4 m/s, g) sampled at two rates
def gap_at(t):
    return max(0.0, 400.0 * t - 0.5 * 981.0 * t * t)


runs = {fps: [strength(gap_at(k / fps), *WHEEL[1:], 1.0) for k in range(int(0.82 * fps))]
        for fps in (30, 144)}
for k in range(4):   # t = k/6 s is a frame at both rates
    assert runs[30][k * 5] == runs[144][k * 24]
peak = max(gap_at(t) for t in np.linspace(0, 0.82, 1000))
assert strength(peak, *WHEEL[1:], 1.0) < 0.05, f"wheel blob should be ~gone at a {peak:.0f} cm hop"

# decal basis on slopes / nose-down
for slope_deg in (0, 12, 30):
    r = math.radians(slope_deg)
    n = np.array([-math.sin(r), 0.0, math.cos(r)])
    for fwd in (np.array([1.0, 0, 0]), np.array([0.7, 0.7, 0]) / math.sqrt(0.98), n.copy(), -n):
        z = along_on_plane(fwd, n)
        x = -n
        assert abs(np.dot(x, z)) < 1e-6 and abs(np.linalg.norm(z) - 1) < 1e-6
        if abs(np.dot(fwd, n)) < 0.99:
            assert np.dot(z, fwd) > 0, "blob must point along the car"

print(f"check_contact_shadow: OK (body {BODY}, wheel {WHEEL}, tilt {TILT_ZERO}..{TILT_FULL})")
