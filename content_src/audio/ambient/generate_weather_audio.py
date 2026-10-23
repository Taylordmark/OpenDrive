"""Weather W2 (next steps.md sec 7.40): synthesizes the two weather sounds
AODAmbientSoundManager needs that the freesound ambient set (manifest.json)
doesn't have - a rain loop and a thunder one-shot (plus, 2026-10-23, the
river_rush loop AODWaterManager plays at rapids and waterfalls) - straight into
normalized/, next to normalize_ambient_audio.py's output, for
unreal/import_ambient_audio.py to import (SW_Rain looping, SW_Thunder not).

Generated, not downloaded: filtered noise is what rain and distant thunder
physically are, so a procedural render sounds right and needs no license or
credit entry (nothing third-party is in it). The wind/dust layer reuses the
existing SW_AlpineWind loop, so no third file.

Deterministic (fixed seed), same level targets and loop-seam check as
normalize_ambient_audio.py (imported from it). normalized/*.wav is
gitignored; re-run this to reproduce.

Usage:  python generate_weather_audio.py
"""

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import butter, sosfilt

from normalize_ambient_audio import NORM_DIR, PEAK_CEILING_DBFS, TARGET_LUFS, TARGET_SR, loop_seam_jump_db

SR = TARGET_SR
RAIN_SECONDS = 40.0
RAIN_XFADE_SECONDS = 1.0
THUNDER_SECONDS = 8.0
THUNDER_LUFS = TARGET_LUFS + 5.0   # a strike sits above the ambience beds
RUSH_SECONDS = 30.0
RUSH_BUBBLES_HZ = 60.0


def band(x, lo, hi, order=4):
    return sosfilt(butter(order, [lo, hi], btype="band", fs=SR, output="sos"), x)


def lowpass(x, hi, order=4):
    return sosfilt(butter(order, hi, btype="low", fs=SR, output="sos"), x)


def rain(rng):
    """Hiss bed + many small drop ticks + fewer heavy drops, rendered
    RAIN_XFADE_SECONDS long and crossfaded end-into-start so it loops."""
    n = int(SR * (RAIN_SECONDS + RAIN_XFADE_SECONDS))
    bed = band(rng.standard_normal(n), 500.0, 9000.0) * 0.5

    def drops(rate_hz, lo, hi, decay_ms):
        imp = np.zeros(n)
        k = rng.poisson(rate_hz * n / SR)
        imp[rng.integers(0, n, k)] = rng.uniform(0.2, 1.0, k)
        t = np.arange(int(SR * decay_ms * 5 / 1000.0)) / SR
        kernel = rng.standard_normal(len(t)) * np.exp(-t / (decay_ms / 1000.0))
        return band(np.convolve(imp, kernel)[:n], lo, hi)

    x = bed + drops(900.0, 2000.0, 12000.0, 1.5) * 0.6 + drops(35.0, 600.0, 4000.0, 6.0) * 1.2
    # slow +-1.5 dB swell so the bed isn't a static hiss
    t = np.arange(n) / SR
    x *= 10 ** (1.5 * np.sin(2 * np.pi * t / 13.0) * np.sin(2 * np.pi * t / 7.3) / 20.0)

    m = int(SR * RAIN_XFADE_SECONDS)
    out = x[: n - m].copy()
    f = np.arange(m) / m
    out[:m] = x[:m] * np.sin(f * np.pi / 2) + x[n - m:] * np.cos(f * np.pi / 2)
    return out


def thunder(rng):
    """Short crack, then a rolling low rumble of a few overlapping swells."""
    n = int(SR * THUNDER_SECONDS)
    t = np.arange(n) / SR
    crack = band(rng.standard_normal(n), 300.0, 6000.0) * np.exp(-t / 0.06) * (t < 0.4)
    rumble_src = lowpass(rng.standard_normal(n), 180.0)
    env = np.zeros(n)
    for start, amp in ((0.05, 1.0), (0.6, 0.8), (1.3, 0.6), (2.2, 0.45)):
        s = np.clip(t - start - rng.uniform(0.0, 0.2), 0.0, None)
        env += amp * (1 - np.exp(-s / 0.08)) * np.exp(-s / 1.4) * (t >= start)
    rumble = rumble_src * env
    x = crack * 0.35 + rumble / (np.max(np.abs(rumble)) + 1e-12)
    fade = int(SR * 0.3)
    x[-fade:] *= np.linspace(1.0, 0.0, fade)
    return x


def river_rush(rng):
    """Rapids / waterfall roar (flowing rivers, 2026-10-23) for AODWaterManager's
    point emitters: a broadband low roar, a midband rush, short resonant
    bubble pops (the gurgle that tells running water from wind), and a slow
    surge. Rendered with an end-into-start crossfade like rain() so it loops."""
    n = int(SR * (RUSH_SECONDS + RAIN_XFADE_SECONDS))
    t = np.arange(n) / SR
    roar = lowpass(rng.standard_normal(n), 400.0) * 1.4
    rush = band(rng.standard_normal(n), 600.0, 5000.0) * 0.5
    bubbles = np.zeros(n)
    for _ in range(int(RUSH_BUBBLES_HZ * n / SR)):
        f0, dur = rng.uniform(300.0, 1400.0), rng.uniform(0.015, 0.05)
        k = np.arange(int(SR * dur))
        tk = k / SR
        # rising chirp, the classic Minnaert bubble shape
        pop = np.sin(2 * np.pi * f0 * tk * (1 + 2.0 * tk / dur)) * np.exp(-tk / (dur / 3))
        s0 = rng.integers(0, n - len(k))
        bubbles[s0:s0 + len(k)] += pop * rng.uniform(0.1, 0.5)
    x = roar + rush + bubbles * 0.35
    x *= 10 ** (2.0 * np.sin(2 * np.pi * t / 9.0) * np.sin(2 * np.pi * t / 5.3) / 20.0)
    m = int(SR * RAIN_XFADE_SECONDS)
    out = x[: n - m].copy()
    f = np.arange(m) / m
    out[:m] = x[:m] * np.sin(f * np.pi / 2) + x[n - m:] * np.cos(f * np.pi / 2)
    return out


def write(name, x, lufs, loop):
    meter = pyln.Meter(SR)
    loud = meter.integrated_loudness(x)
    peak = 20 * np.log10(np.max(np.abs(x)) + 1e-12)
    gain_db = min(lufs - loud, PEAK_CEILING_DBFS - peak)
    y = np.clip(x * 10 ** (gain_db / 20.0), -1.0, 1.0)
    NORM_DIR.mkdir(parents=True, exist_ok=True)
    sf.write(str(NORM_DIR / f"{name}.wav"), y, SR, subtype="PCM_16")
    print(f"  {name}: {len(y) / SR:.1f}s, {meter.integrated_loudness(y):.1f} LUFS "
          f"(target {lufs:.0f}), peak {20 * np.log10(np.max(np.abs(y))):.1f} dBFS"
          + (f", loop seam {loop_seam_jump_db(y, SR):+.2f} dB" if loop else ""))
    return y


def main():
    rng = np.random.default_rng(20260925)
    r = write("rain", rain(rng), TARGET_LUFS, loop=True)
    write("thunder", thunder(rng), THUNDER_LUFS, loop=False)
    rr = write("river_rush", river_rush(rng), TARGET_LUFS, loop=True)
    assert abs(loop_seam_jump_db(r, SR)) < 1.5, "rain loop seam too loud"
    assert abs(loop_seam_jump_db(rr, SR)) < 1.5, "river_rush loop seam too loud"
    print("Done.")


if __name__ == "__main__":
    main()
