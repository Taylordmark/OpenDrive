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
  2. head/tail encoder-padding silence trimmed (Part A fix, next steps.md
     SS7.29 - see trim_edge_silence() below; catches creek_babble's ~30ms
     near-silence loop-tail dropout, a lossy-transcode artifact, without
     touching genuine quiet passages mid-asset)
  3. measured for integrated loudness (ITU-R BS.1770 via pyloudnorm)
  4. gained toward TARGET_LUFS, but capped so its true peak never exceeds
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

# Part A (next steps.md SS7.29): the freesound preview MP3s are a lossy
# transcode of the CC0 originals, and at least one (creek_babble) carries a
# ~30ms near-silence tail immediately before the loop point - classic
# encoder padding from the transcode, not a real fade in the source
# recording. Since these WAVs are played back looping (bLooping=True, see
# unreal/import_ambient_audio.py), that tail reads as a periodic dropout
# every loop. Trim it here, generically, for all 5 assets - conservative
# enough not to touch a genuine quiet passage:
#   - only a CONTIGUOUS run touching sample 0 (head) or the last sample
#     (tail) counts, never a quiet stretch in the middle (forest_wind's
#     lulls are untouched)
#   - the run must be entirely below EDGE_SILENCE_DBFS relative to full
#     scale, measured on an EDGE_SILENCE_HOP-sample RMS envelope (not
#     per-sample, so a single near-zero-crossing sample doesn't trip it)
#   - capped at EDGE_SILENCE_MAX_MS: if the near-silence run is longer than
#     that, it's treated as a real quiet passage/fade (e.g. town_hum's
#     ~211ms tail into background quiet) and left alone
EDGE_SILENCE_DBFS = -60.0
EDGE_SILENCE_MAX_MS = 150.0
EDGE_SILENCE_HOP = 64  # samples per envelope bin, ~1.33ms @ 48kHz


def trim_edge_silence(mono, sr):
    """Returns (trimmed_mono, head_trimmed_ms, tail_trimmed_ms)."""
    hop = EDGE_SILENCE_HOP
    n_hops = len(mono) // hop
    if n_hops == 0:
        return mono, 0.0, 0.0

    frames = mono[: n_hops * hop].reshape(n_hops, hop).astype(np.float64)
    env_dbfs = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-12)
    below = env_dbfs < EDGE_SILENCE_DBFS
    max_hops = int(EDGE_SILENCE_MAX_MS * sr / 1000.0 / hop)

    # Measure the FULL contiguous run first (uncapped) - only trim it at all
    # if the whole run is short. A run longer than EDGE_SILENCE_MAX_MS reads
    # as a genuine quiet passage/fade (town_hum's ~211ms tail into
    # background quiet), not lossy-transcode encoder padding, and per the
    # brief is left untouched entirely rather than partially trimmed down
    # to the cap.
    head_hops_full = 0
    for v in below:
        if not v:
            break
        head_hops_full += 1
    head_hops = head_hops_full if head_hops_full < max_hops else 0

    tail_hops_full = 0
    for v in below[::-1]:
        if not v:
            break
        tail_hops_full += 1
    tail_hops = tail_hops_full if tail_hops_full < max_hops else 0

    start = head_hops * hop
    end = len(mono) - tail_hops * hop
    trimmed = mono[start:end]
    head_ms = head_hops * hop / sr * 1000.0
    tail_ms = tail_hops * hop / sr * 1000.0
    return trimmed, head_ms, tail_ms


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

        mono, head_trim_ms, tail_trim_ms = trim_edge_silence(mono, TARGET_SR)
        if head_trim_ms or tail_trim_ms:
            print(f"  {entry['role']}: trimmed head={head_trim_ms:.1f}ms "
                  f"tail={tail_trim_ms:.1f}ms encoder-padding silence")

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
