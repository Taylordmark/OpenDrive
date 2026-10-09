"""Headlight beam (2026-10-09, Taylor: "make a transparent beam of light
(that is dimmer than where the light lands) ... it should work in all
scenarios"; then "kind of hard to see through in third person and doesn't
extend forward quite as far as I would like"). Mirrors M_HeadlightBeam's
per-pixel march (unreal/apply_headlight_beam_material.py): along each view
ray, inside the beam sphere and stopped at the road, sum
Haze x phase x I x spot / (d^2 + D0^2) for both lamps. D0 softens the core
at the lens that a chase camera looks straight down.

Checks, in night mid-grey units: the chase view stays see-through (no
bright veil), the beam still reads, it is dimmer than the road it lands on,
and seen side-on it still shows 60 m out.

Usage:  python pipeline/check_headlight_beam.py [out.png]
"""
import math
import sys

import numpy as np

# AODDayNightController / UpdateHeadlights
HEAD_LM, COS_IN, COS_OUT = 4500.0, math.cos(math.radians(22)), math.cos(math.radians(38))
CD = HEAD_LM / (2 * math.pi * (1 - COS_OUT))
HAZE = 0.01                       # BeamHaze (1/m)
RADIUS = 4500.0                   # BeamRadius, cm: reaches 2R - 3 m ahead, ~ the spots' 90 m
G, D0, N = 0.0, 1000.0, 48         # = MARCH_HLSL: phase g, core softening (cm), steps
MID = 0.5                         # night mid grey, nits (check_night_exposure.py)

PITCH = math.radians(-6)
DIR = np.array([math.cos(PITCH), 0.0, math.sin(PITCH)])
LAMPS = [np.array([230.0, s * 50.0, 25.0]) for s in (-1, 1)]   # nose, cm (car origin)
GROUND = -35.0                    # road under the wheels, cm
CENTER = (LAMPS[0] + LAMPS[1]) / 2 + DIR * (RADIUS - 300.0)


def beam(c, d):
    """HLSL mirror: camera c, unit ray d; scene depth = the road plane."""
    oc = c - CENTER
    b = oc @ d
    h = b * b - (oc @ oc - RADIUS ** 2)
    if h <= 0:
        return 0.0
    t_near, t_end = max(0.0, -b - math.sqrt(h)), -b + math.sqrt(h)
    if d[2] < 0:
        t_end = min(t_end, (GROUND - c[2]) / d[2])
    if t_end <= t_near:
        return 0.0
    dt = (t_end - t_near) / N
    x = c + d * (t_near + (np.arange(N)[:, None] + 0.5) * dt)
    s = 0.0
    for lamp in LAMPS:
        v = x - lamp
        dist = np.linalg.norm(v, axis=1)
        l = v / dist[:, None]
        t = np.clip((l @ DIR - COS_OUT) / (COS_IN - COS_OUT), 0, 1)
        ph = (1 - G * G) / (4 * math.pi * (1 + G * G + 2 * G * (l @ d)) ** 1.5)
        s += np.sum(t * t * (3 - 2 * t) * ph / (dist ** 2 + D0 ** 2))
    return CD * HAZE * s * dt * 100.0 / MID


def road(x_cm):
    """Headlit road luminance (albedo 0.1) straight ahead at x_cm, x mid grey."""
    p = np.array([x_cm, 0.0, GROUND])
    e = 0.0
    for lamp in LAMPS:
        v = p - lamp
        dist = np.linalg.norm(v)
        t = np.clip((v / dist @ DIR - COS_OUT) / (COS_IN - COS_OUT), 0, 1)
        e += CD * t * t * (3 - 2 * t) / (dist / 100) ** 2 * (-v[2] / dist)
    return e * 0.1 / math.pi / MID


def side(x_cm):
    """Beam seen side-on, x_cm ahead of the nose, from 15 m to the side at lamp height."""
    axis = (LAMPS[0] + LAMPS[1]) / 2 + DIR * (x_cm - 230.0) / DIR[0]
    return beam(axis + np.array([0.0, -1500.0, 0.0]), np.array([0.0, 1.0, 0.0]))


def main():
    cam = np.array([-700.0, 0.0, 180.0])               # chase cam behind the car
    W, H, fov = 96, 54, math.radians(90)
    img = np.zeros((H, W))
    for j in range(H):
        for i in range(W):
            u = (i + 0.5) / W * 2 - 1
            v = 1 - (j + 0.5) / H * 2
            ray = np.array([1.0, -u * math.tan(fov / 2), v * math.tan(fov / 2) * H / W - 0.12])
            img[j, i] = beam(cam, ray / np.linalg.norm(ray))
    pool = max(road(x) for x in range(250, 3000, 10))   # brightest spot the beams land on
    p50, p99 = np.percentile(img[img > 0.01], [50, 99])
    reach = {m: side(m * 100.0) for m in (10, 30, 60, 80)}
    print(f"chase view x mid grey: median {p50:.2f}  p99 {p99:.2f} | road pool peak {pool:.1f}")
    print("side-on x mid grey: " + "  ".join(f"{m} m {v:.2f}" for m, v in reach.items()))
    if len(sys.argv) > 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.imshow(img, cmap="inferno", vmin=0, vmax=2.0)
        plt.colorbar(label="beam luminance / night mid grey (clipped at 2)")
        plt.title(f"chase cam, beam only (Haze {HAZE}/m, D0 {D0 / 100:.0f} m, R {RADIUS / 100:.0f} m)")
        plt.savefig(sys.argv[1], dpi=80)
    assert p50 > 0.15, "beam reads in clear air (no weather fog)"
    assert p99 < 1.5, "chase view stays see-through: no bright veil at the lenses"
    assert p99 < 0.1 * pool, "beam stays dimmer than where it lands"
    assert reach[60] > 0.05, "beam still shows 60 m out"
    print("PASS: see-through beam in clear air, dimmer than where it lands, reaching 60 m+")


if __name__ == "__main__":
    main()
