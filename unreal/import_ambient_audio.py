"""Imports the loudness-matched Arc 5.4 ambient CC0 audio loops (next steps.md
SS7.28) as UE SoundWave assets under /Game/OpenDrive/Audio/Ambient/ -
AODAmbientSoundManager::BeginPlay() LoadObject()s these exact paths as its
default sound assignments (see ODAmbientSoundManager.cpp).

Source is content_src/audio/ambient/normalized/*.wav (48kHz PCM16,
level-matched to a common LUFS target with peak-safe headroom - see the
project's offline verification pass / verify_ambient_audio.png, produced by
loudness-matching each raw content_src/audio/ambient/raw/*.mp3 freesound
preview), NOT the raw MP3s directly - importing the pre-normalized WAVs means
the in-engine relative levels between layers already match without a mix
pass. Run content_src/audio/ambient/download_ambient_audio.py first if
raw/*.mp3 is missing, and the (currently scratchpad-only) loudness-matching
script to produce normalized/*.wav if that directory is missing.

Same AssetImportTask pattern unreal/import_ground_textures.py and
unreal/import_water_textures.py established for this project's other CC0/
generated asset sets - a plain default WAV import already produces a usable
SoundWave, the only explicit override needed here is bLooping (see
SoundWave.h: "If set, when played directly (not through a sound cue) the
wave will be played looping" - AODAmbientSoundManager plays these directly
via SpawnSound2D, not through a SoundCue, so this flag is what actually makes
them loop in-game).

ROLE_TO_ASSET below maps manifest.json's "role" keys to the SW_* asset names
ODAmbientSoundManager.cpp's BeginPlay() LoadObject()s - keep both in sync if
either changes. lake_shore is intentionally imported too (as SW_LakeShore)
even though no default C++ property points at it yet - see manifest.json's
lake_shore notes and the class header comment for why (verified CC0 lake-
specific alternate, not wired to a distinct layer this pass).

UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

from pathlib import Path

import unreal

SRC_DIR = Path(__file__).resolve().parents[1] / "content_src" / "audio" / "ambient" / "normalized"
DEST_DIR = "/Game/OpenDrive/Audio/Ambient"

# manifest.json role -> destination SoundWave asset name (must match the
# SW_* paths ODAmbientSoundManager.cpp's BeginPlay() LoadObject()s).
ROLE_TO_ASSET = {
    "forest_wind": "SW_ForestWind",
    "alpine_wind": "SW_AlpineWind",
    "town_hum": "SW_TownHum",
    "creek_babble": "SW_CreekBabble",
    "lake_shore": "SW_LakeShore",
}


def log(msg):
    unreal.log(f"AMBIENTAUDIO {msg}")


def main():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    imported = []

    for role, asset_name in ROLE_TO_ASSET.items():
        wav_path = SRC_DIR / f"{role}.wav"
        if not wav_path.exists():
            log(f"SKIP {role}: {wav_path} not found (run the loudness-matching "
                f"script against content_src/audio/ambient/raw/ first)")
            continue

        t = unreal.AssetImportTask()
        t.filename = str(wav_path)
        t.destination_path = DEST_DIR
        t.destination_name = asset_name
        t.automated = True
        t.save = False
        t.replace_existing = True
        tools.import_asset_tasks([t])

        wave = unreal.load_asset(f"{DEST_DIR}/{asset_name}")
        if wave is None:
            raise RuntimeError(f"SoundWave import failed: {wav_path} -> {DEST_DIR}/{asset_name}")

        wave.set_editor_property("looping", True)
        unreal.EditorAssetLibrary.save_loaded_asset(wave, only_if_is_dirty=False)
        imported.append(asset_name)
        log(f"imported {DEST_DIR}/{asset_name} (bLooping=True) from {wav_path}")

    log(f"done: {len(imported)}/{len(ROLE_TO_ASSET)} SoundWave asset(s) imported: {imported}")
    if len(imported) < 4:
        log("WARNING: fewer than the 4 assets ODAmbientSoundManager's default "
            "LoadObject() calls expect were imported - forest/alpine/town/creek "
            "are the required 4; lake_shore is an optional extra.")


if __name__ == "__main__":
    main()
