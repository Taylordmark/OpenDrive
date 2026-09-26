"""V4 offline estimate (next steps.md §7.36): how much of the SkyAtmosphere
aerial-perspective veil (1 - transmittance) a distant hill gets, before/after
AerialPespectiveViewDistanceScale 1.0 -> 0.8. Homogeneous horizontal path at
the atmosphere's ground density (PlanetTopAtAbsoluteWorldOrigin: world Z~0 is
atmosphere altitude 0, and the valley sits below it, so density ~ ground max).
Coefficients = ODAtmosphereController.cpp values + engine defaults for the
rest (Rayleigh color (0.175287,0.409607,1), Mie absorption 0.000444).
Single-extinction estimate only: inscatter ~ L_sky*(1-T), surface ~ T.
It ignores multiple scattering, sun phase, and the height fog; the sign and
relative size of the change are what it's good for, not absolute pixels.
Run: python scratchpad/verify_v4_aerial_perspective.py"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from pathlib import Path

OUT = Path(__file__).resolve().parent
RAY = 0.028 * np.array([0.175287, 0.409607, 1.0])   # SetRayleighScatteringScale(0.028)
MIE_EXT = 0.009 + 0.000444                            # SetMieScatteringScale(0.009) + default absorption
START_KM = 0.07                                       # SetAerialPerspectiveStartDepth(0.07)
SIG = RAY + MIE_EXT                                   # per km, per RGB channel

def veil(d_km, scale):
    return 1 - np.exp(-SIG[None, :] * np.maximum(d_km[:, None] * scale - START_KM, 0))

d = np.linspace(0, 30, 301)
b, a = veil(d, 1.0), veil(d, 0.8)
lines = ["V4: AerialPespectiveViewDistanceScale 1.0 -> 0.8 (all other SkyAtmosphere params unchanged: "
         "Rayleigh 0.028, Mie 0.009, MieAnisotropy 0.82, APStartDepth 0.07 km)",
         f"extinction per km RGB = {np.round(SIG, 5).tolist()}"]
for km in (1, 2, 5, 10, 15, 20, 30):
    i = np.argmin(abs(d - km))
    lines.append(f"  d={km:>2} km  veil(1-T) RGB before={np.round(b[i], 3).tolist()} after={np.round(a[i], 3).tolist()}"
                 f"  blue-veil change={100 * (a[i, 2] / b[i, 2] - 1):+.1f}%")
log = "\n".join(lines)
print(log)
(OUT / "verify_v4_aerial_perspective.log").write_text(log + "\n")
for c, col in enumerate("rgb"):
    plt.plot(d, b[:, c], color=col, ls="--", label=f"{col} before (1.0)")
    plt.plot(d, a[:, c], color=col, label=f"{col} after (0.8)")
plt.xlabel("view distance km"); plt.ylabel("aerial-perspective veil fraction (1 - T)")
plt.title("V4 offline estimate: AP veil vs distance"); plt.legend(); plt.grid(alpha=.3)
plt.savefig(OUT / "verify_v4_aerial_perspective.png", dpi=90)
