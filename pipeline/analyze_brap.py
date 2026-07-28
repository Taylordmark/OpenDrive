"""Verify the limiter chatter (2026-07-18): during held-at-limiter segments
the engine output should be amplitude-modulated at ~LimiterBounceHz (12 Hz)
- the brap-brap - instead of a steady tone.

Reads Saved/AudioTest/odrec.wav. For each 1 s window prints RMS and the
dominant envelope-modulation frequency in the 5-25 Hz band plus its strength
(peak / median of the band). PASS if any loud window shows a 9-16 Hz
modulation peak with strength > 4.
"""

import sys
import wave
from pathlib import Path

import numpy as np

WAV = (Path(__file__).resolve().parent.parent
       / "OpenDriveUE" / "Saved" / "AudioTest" / "odrec.wav")

with wave.open(str(WAV), "rb") as w:
    sr = w.getframerate()
    raw = w.readframes(w.getnframes())
    ch = w.getnchannels()
    width = w.getsampwidth()

dtype = {2: np.int16, 4: np.int32}[width]
x = np.frombuffer(raw, dtype=dtype).astype(np.float64)
x = x.reshape(-1, ch).mean(axis=1)
x /= np.abs(x).max() + 1e-9
dur = len(x) / sr
print(f"wav: {dur:.1f}s @ {sr} Hz")

# amplitude envelope at 200 Hz sample rate
dec = sr // 200
env = np.abs(x)
env = env[: len(env) // dec * dec].reshape(-1, dec).mean(axis=1)
esr = sr / dec

ok = False
for t0 in np.arange(0.5, dur - 1.0, 1.0):
    seg = x[int(t0 * sr):int((t0 + 1.0) * sr)]
    rms = float(np.sqrt((seg ** 2).mean()))
    e = env[int(t0 * esr):int((t0 + 1.0) * esr)]
    e = e - e.mean()
    spec = np.abs(np.fft.rfft(e * np.hanning(len(e))))
    freqs = np.fft.rfftfreq(len(e), 1 / esr)
    band = (freqs >= 5) & (freqs <= 25)
    peak_hz = freqs[band][np.argmax(spec[band])]
    strength = spec[band].max() / (np.median(spec[band]) + 1e-9)
    mark = ""
    if rms > 0.1 and 9 <= peak_hz <= 16 and strength > 4:
        ok = True
        mark = "  <-- BRAP"
    print(f"  t={t0:4.1f}s rms={rms:.3f} envmod={peak_hz:5.1f} Hz "
          f"strength={strength:4.1f}{mark}")

print(f"\nbrap detected: {ok}")
sys.exit(0 if ok else 1)
