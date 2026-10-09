"""Headlight beam (2026-10-09, Taylor: "make a transparent beam of light
(that is dimmer than where the light lands) ... it should work in all
scenarios"). Mirrors M_HeadlightBeam's per-pixel march
(unreal/apply_headlight_beam_material.py): along each view ray, inside the
beam sphere and stopped at the scene depth, sum Haze x phase x I x spot / d^2
for both lamps. Renders a chase-camera view of beam-only luminance and the
lit road it lands on, in night mid-grey units, and checks the beam reads but
stays dimmer than the road it hits.

Usage:  python pipeline/check_headlight_beam.py [out.png]
"""
import math
import sys

import numpy as np

# AODDayNightController / UpdateHeadlights
HEAD_LM, COS_IN, COS_OUT = 4500.0, math.cos(math.radians(22)), math.cos(math.radians(38))
CD = HEAD_LM / (2 * math.pi * (1 - COS_OUT))
HAZE, G = 0.015, 0.2              # BeamHaze (1/m), BeamPhaseG
RADIUS = 2200.0                   # beam sphere, cm
MID = 0.5                         # night mid grey, nits (check_night_exposure.py)
N = 32                            # steps, = the HLSL loop
MIN_D = 100.0                     # BeamMinDist, cm: soft core at the lens (the lens glow covers it)

PITCH = math.radians(-6)
DIR = np.array([math.cos(PITCH), 0.0, math.sin(PITCH)])
LAMPS = [np.array([230.0, s * 50.0, 25.0]) for s in (-1, 1)]   # nose, cm (car origin)
GROUND = -35.0                    # road under the wheels, cm
CENTER = (LAMPS[0] + LAMPS[1]) / 2 + DIR * (RADIUS - 300.0)


def beam(c, p_far):
    """HLSL mirror: c camera, p_far the sphere back-face point, scene depth = road."""
    d = p_far - c
    t_far = np.linalg.norm(d)
    d = d / t_far
    t_end = t_far
    if d[2] < 0:                                       # scene depth: ray hits the road
        t_end = min(t_end, (GROUND - c[2]) / d[2])
    oc = c - CENTER
    b = oc @ d
    t_near = max(0.0, -b - math.sqrt(max(b * b - (oc @ oc - RADIUS ** 2), 0.0)))
    if t_end <= t_near:
        return 0.0
    dt = (t_end - t_near) / N
    s = 0.0
    for i in range(N):
        x = c + d * (t_near + (i + 0.5) * dt)
        for lamp in LAMPS:
            v = x - lamp
            dist = max(np.linalg.norm(v), MIN_D)
            l = v / dist
            t = np.clip((l @ DIR - COS_OUT) / (COS_IN - COS_OUT), 0, 1)
            ph = (1 - G * G) / (4 * math.pi * (1 + G * G - 2 * G * (l @ -d)) ** 1.5)
            s += t * t * (3 - 2 * t) * ph / dist ** 2
    return CD * HAZE * s * dt * 100.0


def road(x_cm):
    """Headlit road luminance (albedo 0.1) straight ahead at x_cm."""
    p = np.array([x_cm, 0.0, GROUND])
    e = 0.0
    for lamp in LAMPS:
        v = p - lamp
        dist = np.linalg.norm(v)
        t = np.clip((v / dist @ DIR - COS_OUT) / (COS_IN - COS_OUT), 0, 1)
        e += CD * t * t * (3 - 2 * t) / (dist / 100) ** 2 * (-v[2] / dist)
    return e * 0.1 / math.pi


def main():
    cam = np.array([-700.0, 0.0, 180.0])               # chase cam behind the car
    W, H, fov = 96, 54, math.radians(90)
    img = np.zeros((H, W))
    for j in range(H):
        for i in range(W):
            u = (i + 0.5) / W * 2 - 1
            v = 1 - (j + 0.5) / H * 2
            ray = np.array([1.0, -u * math.tan(fov / 2), v * math.tan(fov / 2) * H / W - 0.12])
            ray /= np.linalg.norm(ray)
            oc = cam - CENTER                          # far side of the sphere
            b = oc @ ray
            disc = b * b - (oc @ oc - RADIUS ** 2)
            if disc > 0:
                img[j, i] = beam(cam, cam + ray * (-b + math.sqrt(disc))) / MID
    pool = max(road(x) for x in range(250, 3000, 10)) / MID    # brightest spot the beams land on
    p50, p99 = np.percentile(img[img > 0.01], [50, 99])
    print(f"beam x mid grey: median {p50:.2f}  p99 {p99:.2f}  peak {img.max():.2f} | road pool peak {pool:.2f}")
    if len(sys.argv) > 1:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.imshow(img, cmap="inferno", vmin=0, vmax=2.0)
        plt.colorbar(label="beam luminance / night mid grey (clipped at 2)")
        plt.title(f"chase cam, beam only (Haze {HAZE}/m, g {G})")
        plt.savefig(sys.argv[1], dpi=80)
    assert p50 > 0.15, "beam reads in clear air (no weather fog)"
    assert p99 < 0.7 * pool, "beam stays dimmer than where it lands"
    print("PASS: visible beam in clear air, dimmer than where it lands")


if __name__ == "__main__":
    main()
