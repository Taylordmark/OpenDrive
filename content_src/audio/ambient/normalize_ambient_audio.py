"""Loudness-matches the raw freesound preview MP3s in
content_src/audio/ambient/raw/ (download_ambient_audio.py) into level-matched
48kHz PCM16 WAVs under content_src/audio/ambient/normalized/ - THESE are what
unreal/import_ambient_audio.py actually imports as SoundWave assets, not the
raw MP3s directly, so the 4-5 ambient layers AODAmbientSoundManager
crossfades don't need an in-engine mix pass to sound balanced against each
other.

Deterministic re-run (same convention as every other content_src/<category>/
script in this project): given the same raw/*.mp3 files, this always
produces the same normalized/*.wav bytes, so normalized/ is gitignored same
as raw/ - see repo .gitignore and manifest.json's _comment.

Each file is:
  1. downmixed to mono, resampled to 48kHz
  2. measured for integrated loudness (ITU-R BS.1770 via pyloudnorm)
  3. gained toward TARGET_LUFS, but capped so its true peak never exceeds
     PEAK_CEILING_DBFS (a naturally quiet, wide-dynamic-range clip - e.g. a
     wind bed with one loud gust - would otherwise get pushed into clipping
     trying to hit the same LUFS as a consistently loud one)

This is the non-plotting half of the project's offline audio verification
pass (see verify_ambient_audio.png / ambient_audio_table.txt, produced by a
sibling analysis script) - the LUFS/peak/seam numbers this script's output
gets checked against are reported there, not here; this script only WRITES
the WAVs the game actually imports.

Usage:  python normalize_ambient_audio.py
"""

import json
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly

HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "raw"
NORM_DIR = HERE / "normalized"
MANIFEST = HERE / "manifest.json"

TARGET_SR = 48000
TARGET_LUFS = -23.0
PEAK_CEILING_DBFS = -1.0


def to_mono48k(data, sr):
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != TARGET_SR:
        data = resample_poly(data, TARGET_SR, sr)
    return data.astype(np.float32)


def main():
    NORM_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text())
    meter = pyln.Meter(TARGET_SR)

    for entry in manifest["sounds"]:
        raw_path = HERE.parent.parent.parent / entry["local_file"]
        if not raw_path.exists():
            print(f"  {entry['role']}: SKIP, {raw_path} not found "
                  f"(run download_ambient_audio.py first)")
            continue

        data, sr = sf.read(str(raw_path), always_2d=False)
        mono = to_mono48k(data, sr)

        loudness = meter.integrated_loudness(mono.astype(np.float64))
        peak_dbfs = 20 * np.log10(np.max(np.abs(mono)) + 1e-12)
        gain_db = min(TARGET_LUFS - loudness, PEAK_CEILING_DBFS - peak_dbfs)
        gain = 10 ** (gain_db / 20.0)
        normalized = np.clip(mono * gain, -1.0, 1.0)

        out_wav = NORM_DIR / f"{entry['role']}.wav"
        sf.write(str(out_wav), normalized, TARGET_SR, subtype="PCM_16")
        print(f"  {entry['role']}: {raw_path.name} -> {out_wav.name} "
              f"(orig={loudness:.1f} LUFS, gain={gain_db:+.1f} dB)")

    print("Done.")


if __name__ == "__main__":
    main()
