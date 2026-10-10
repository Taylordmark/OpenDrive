"""Headlight beam pattern as an IES profile (2026-10-09, Taylor after a
night drive: "widen the area covered by the headlights and make the
headlight center area less opaque").

Why a profile: the old head spots (4500 lm, cone 22/38 deg, pitched -6 deg)
put their full ~3400 cd hotspot on the road 4-6 m ahead. A lamp 0.6 m up
lights the road as I*h/d^3, so under the night exposure (mid grey 0.5 nits,
check_night_exposure.py) that pool hit ~80x mid grey (blown white) while the
road past ~16 m and beyond ~+-5 m at 10 m stayed black. No circular cone
fixes that (sweeping cone/pitch/lumens moves the lit area < 20%): a real
low beam is a profile - intense just under the horizon, ~1/alpha^2 dimmer
toward the bumper, wide sideways, cut off above. This writes that as
content_src/lights/headlight.ies (unreal/import_headlight_ies.py imports it;
AODDayNightController puts it on the head spots, Candela units, peak
HEAD_CD).

UE mapping (Engine/Shaders/Private/IESLightProfilesCommon.ush,
IESFile/Private/IESLoader.cpp, SpotLightSceneProxy.cpp): IES vertical angle
= angle from the light's forward axis; horizontal angle H = atan2(w.Z, w.Y)
+ 180 deg for w the light->point direction in light space (Tangent = local
Z, Bitangent = Z x X = Y), so H 90 = down, 270 = up, 0/180 = sideways. The
texture is normalised to its max (bUseIESBrightness off), so values here are
relative and the light's intensity is the peak candela.

Usage:  python pipeline/make_headlight_ies.py     (writes the .ies + self-check)
"""
import math
from pathlib import Path

import numpy as np

HEAD_CD = 40000.0      # AODDayNightController::HeadlightCd - per lamp, a real low beam's hotspot
CUT_UP, CUT_LO = -2.0, 0.5   # deg below horizon: ~2% above CUT_UP, full from CUT_LO (sharp cutoff)
ALPHA1, P = 2.0, 2.0   # full intensity to ALPHA1 below horizon, then (ALPHA1/alpha)^P toward the bumper
PHI0 = 30.0            # sideways: 1/(1+(phi/PHI0)^4) - 0.5 at 30 deg, 0.16 at 45
MID = 0.5              # night mid grey, nits (check_night_exposure.py)
LAMP_X, LAMP_Y, LAMP_H = 2.3, 0.5, 0.6   # m: nose, half lamp spacing, height over the road
OUT = Path(__file__).resolve().parents[1] / "content_src" / "lights" / "headlight.ies"


def profile(alpha, phi):
    """Relative intensity (max 1): alpha deg below horizon, phi deg sideways."""
    alpha, phi = np.asarray(alpha, float), np.asarray(phi, float)
    g = np.where(alpha > ALPHA1, (ALPHA1 / np.maximum(alpha, 1e-6)) ** P, 1.0)
    t = np.clip((alpha - CUT_UP) / (CUT_LO - CUT_UP), 0.0, 1.0)
    g = np.where(alpha < CUT_LO, 0.02 + 0.98 * t * t * (3.0 - 2.0 * t), g)
    return g / (1.0 + (phi / PHI0) ** 4)


def road(x, y, cd=HEAD_CD):
    """Road luminance (albedo 0.1) under both lamps, x mid grey; x ahead of the car origin, y right (m)."""
    e = 0.0
    for s in (-1, 1):
        vx, vy = x - LAMP_X, y - s * LAMP_Y
        d = np.sqrt(vx * vx + vy * vy + LAMP_H ** 2)
        alpha = np.degrees(np.arcsin(LAMP_H / d))
        phi = np.degrees(np.arctan2(np.abs(vy), vx))
        e = e + cd * profile(alpha, phi) / d ** 2 * (LAMP_H / d)
    return e * 0.1 / math.pi / MID


def old_road(x, y):
    """The pre-profile spots: 4500 lm, cone 22/38 deg, pitch -6 (UE spot falloff: t^2)."""
    cd = 4500.0 / (2 * math.pi * (1 - math.cos(math.radians(38))))
    ci, co = math.cos(math.radians(22)), math.cos(math.radians(38))
    dx, dz = math.cos(math.radians(-6)), math.sin(math.radians(-6))
    e = 0.0
    for s in (-1, 1):
        vx, vy = x - LAMP_X, y - s * LAMP_Y
        d = np.sqrt(vx * vx + vy * vy + LAMP_H ** 2)
        t = np.clip(((vx * dx - LAMP_H * dz) / d - co) / (ci - co), 0, 1)
        e = e + cd * t * t / d ** 2 * (LAMP_H / d)
    return e * 0.1 / math.pi / MID


def write_ies(path=OUT):
    v = np.arange(0.0, 90.01, 0.5)          # deg from the forward axis
    h = np.arange(0.0, 360.01, 5.0)         # UE: 90 down, 270 up, 0/180 sideways
    th, tt = np.radians(v)[None, :], np.radians(h - 180.0)[:, None]
    wx, wy, wz = np.cos(th), np.sin(th) * np.cos(tt), np.sin(th) * np.sin(tt)
    cd = profile(np.degrees(np.arcsin(np.clip(-wz, -1, 1))), np.degrees(np.arctan2(np.abs(wy), wx)))
    cd[:, -1] = 0.0                          # nothing sideways/backward of 90 deg
    rows = ["IESNA:LM-63-2002", "[TEST] OpenDrive pipeline/make_headlight_ies.py",
            "[LUMINAIRE] car low beam (relative)", "TILT=NONE",
            f"1 -1 1 {len(v)} {len(h)} 1 2 0 0 0", "1 1 0",
            " ".join(f"{a:g}" for a in v), " ".join(f"{a:g}" for a in h)]
    rows += [" ".join(f"{c:.5f}" for c in row) for row in cd]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n")
    return v, h, cd


def stats(fn, thr=0.1):
    xs, ys = np.arange(3.0, 100.0, 0.25), np.arange(-30.0, 30.01, 0.25)
    X, Y = np.meshgrid(xs, ys)
    L = fn(X, Y)
    axis = L[np.argmin(np.abs(ys))]
    half = lambda x: np.abs(ys)[L[:, np.argmin(np.abs(xs - x))] > thr].max(initial=0.0)
    return dict(peak=L.max(), area=((L > thr) & (L < 16)).sum() / 16, reach=xs[axis > thr].max(initial=0.0),
                spread10=math.degrees(math.atan(half(10) / (10 - LAMP_X))),
                spread20=math.degrees(math.atan(half(20) / (20 - LAMP_X))),
                road10=axis[np.argmin(np.abs(xs - 10))])


def main():
    v, h, cd = write_ies()
    # the IES grid reproduces the profile: straight ahead 3 deg down = H 90, V 3
    assert abs(cd[list(h).index(90.0), list(v).index(3.0)] - float(profile(3.0, 0.0))) < 1e-3
    # sideways (H 180) on the horizon = phi; above the cutoff (H 270) dim
    assert abs(cd[list(h).index(180.0), list(v).index(30.0)] - float(profile(0.0, 30.0))) < 1e-3
    assert cd[list(h).index(270.0), list(v).index(5.0)] < 0.03
    old, new = stats(old_road), stats(road)
    f = lambda s: "  ".join(f"{k} {val:.1f}" for k, val in s.items())
    print(f"old spots   x mid grey / m / m2 / deg: {f(old)}")
    print(f"new profile x mid grey / m / m2 / deg: {f(new)}")
    print(f"wrote {OUT} ({len(v)} V x {len(h)} H)")
    assert new["peak"] < 8.0, "no blown-out pool in front of the bumper (old ~80x mid grey)"
    assert new["spread10"] > 35.0 and new["spread20"] > 30.0, "lights the road +-35 deg wide near the car"
    assert new["reach"] > 1.8 * old["reach"] and new["area"] > 4 * old["area"], "lights much more road"
    assert new["road10"] > 1.0, "road 10 m ahead reads at or above mid grey"
    print("PASS: wide, no blown centre, reaches 2x further")


if __name__ == "__main__":
    main()
