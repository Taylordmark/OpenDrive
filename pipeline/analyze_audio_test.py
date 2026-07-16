"""Verify the Arc 5.1 audio test recording: does the engine pitch track RPM?

Reads Saved/AudioTest/odrec.wav (recorded with -odrev=8 -odrec=12: idle for
~5 s, then full throttle). Passes if:
  1. overall RMS is healthy (not silence, not clipping),
  2. the dominant low-frequency peak RISES substantially after throttle.
"""

import sys
import wave
from pathlib import Path

import numpy as np

WAV = (Path(__file__).resolve().parent.parent
       / "OpenDriveUE" / "Saved" / "AudioTest" / "odrec.wav")

with wave.open(str(WAV), "rb") as w:
    sr = w.getframerate()
    n = w.getnframes()
    ch = w.getnchannels()
    width = w.getsampwidth()
    raw = w.readframes(n)

dtype = {2: np.int16, 4: np.int32}[width]
x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
x = x.reshape(-1, ch).mean(axis=1)
x /= np.abs(x).max() + 1e-9
dur = len(x) / sr
print(f"wav: {dur:.1f}s @ {sr} Hz, {ch} ch, peak-normalized")


def dominant_hz(seg):
    win = np.hanning(len(seg))
    spec = np.abs(np.fft.rfft(seg * win))
    freqs = np.fft.rfftfreq(len(seg), 1 / sr)
    band = (freqs > 20) & (freqs < 800)          # engine fundamental range
    return freqs[band][np.argmax(spec[band])], spec[band].max()


results = []
for t0 in np.arange(0.5, dur - 1.0, 1.0):
    seg = x[int(t0 * sr):int((t0 + 1.0) * sr)]
    rms = float(np.sqrt((seg ** 2).mean()))
    hz, _ = dominant_hz(seg)
    results.append((t0, rms, hz))
    print(f"  t={t0:4.1f}s  rms={rms:.3f}  dominant={hz:6.1f} Hz")

early = [hz for t, r, hz in results if t < 4.0]
late = [hz for t, r, hz in results if t > dur - 4.0]
rms_all = [r for _, r, _ in results]

ok_level = 0.005 < max(rms_all) <= 1.0
ok_rise = np.median(late) > np.median(early) * 1.5
print(f"\nlevel ok: {ok_level} (max rms {max(rms_all):.3f})")
print(f"pitch rise ok: {ok_rise} "
      f"(early median {np.median(early):.0f} Hz -> late {np.median(late):.0f} Hz)")
sys.exit(0 if (ok_level and ok_rise) else 1)
