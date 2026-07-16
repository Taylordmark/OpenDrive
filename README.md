# OpenDrive

Procedurally generated racing across real-world places at 1:1 scale, built
entirely from open data. Stylized "real places, storybook look" art direction.
UE 5.8, C++ + Python. Target: free public release (itch/GitHub).

**Current stage:** Tail of the Dragon — the real US-129 (Deals Gap → Tabcat
Creek, 18.2 km, 318 curves) on real USGS terrain.

## What works today

- Real terrain + grade-smoothed, banked road meshes generated from USGS 3DEP
  elevation and OpenStreetMap geometry; roads carved into the mountainside
  with a tight cut-wall blend (canyon-wall proximity preserved).
- Tuned Chaos vehicle with per-tire surface grip (asphalt/dirt/grass via
  physical materials) and five tuning archetypes — Street / Rally / Drift /
  Drag / Offroad — as data assets, cycled with **T**.
- Runtime forest: deterministic tile-seeded scatter (zero per-tree storage),
  wall-of-trees density, per-tile dominant species, trunk collision via a
  capsule pool. Works in editor, standalone, and (pending cooked mask work)
  packaged builds.
- Day/night cycle (1 h default) with automatic vehicle headlights at dusk.
- Rainfall-darkened, wet-gloss roads from NASA POWER climate normals.
- Race framework: 62 checkpoint gates, flying start, timer + sector splits
  vs your best, persistent best times, spectral ghost replay of the PB,
  **R** respawns at the last gate.
- Procedural audio, all C++ DSP: engine synth that tracks RPM/load/throttle
  (verified by FFT: 60 Hz fundamental at 900 RPM idle, exact for 8 cyl),
  surface-colored tire noise, wind, collision thumps.

Full roadmap and per-arc status: [`next steps.md`](next%20steps.md).

## Repo layout

| Path | What it is |
|---|---|
| `pipeline/` | Offline data pipeline (Python): fetch open data, build stage artifacts |
| `unreal/` | Editor automation (Python, run headless via `UnrealEditor-Cmd`) |
| `OpenDriveUE/Source/` | C++ game module (all `OD*` classes) |
| `OpenDriveUE/Config/` | Project settings |
| `data/stages/<stage>/` | Built stage artifacts (git-ignored; deterministic re-runs) |
| `wishlist.txt` | The original 14-item feature wishlist |
| `next steps.md` | Plan of record: 7 arcs, statuses, decisions |

`OpenDriveUE/Content/` is git-ignored by design: every imported asset is
regenerable from the pipeline + headless scripts.

## Rebuilding a stage from nothing

Data (Python 3.12, `numpy pillow requests`):

```
python pipeline/fetch_data.py        # OSM road + terrain tiles
python pipeline/fetch_imagery.py     # USGS aerial imagery
python pipeline/fetch_climate.py     # NASA POWER precip/temp -> road moisture
python pipeline/build_stage.py       # heightmap (carved), road_mesh.json, stage_meta
python pipeline/make_road_texture.py # moisture-darkened asphalt texture
python pipeline/make_detail_textures.py  # terrain mask + detail textures
python pipeline/make_tree_meshes.py  # low-poly foliage OBJs + palette
python pipeline/export_road_obj.py   # road chunks as OBJ for import
```

Engine (editor **closed** — headless commandlet is the standard workflow):

```
"C:\Program Files\Epic Games\UE_5.8\Engine\Build\BatchFiles\Build.bat" ^
    OpenDriveUEEditor Win64 Development -project=<uproject> -waitmutex

UnrealEditor-Cmd.exe <uproject> -run=pythonscript -script=<unreal\script.py> ^
    -nullrhi -unattended -nosplash -nop4 -stdout
```

Apply scripts in order for a fresh level: `build_level_headless.py` →
`build_visuals_headless.py` → `apply_arc1_headless.py` →
`apply_arc23_headless.py` (**needs `-AllowCommandletRendering` instead of
`-nullrhi`** — the landscape heightmap re-import blits through a render
target) → `apply_forest_manager.py` → `strip_ground_bushes.py` →
`apply_vehicle_audio.py` → `apply_tuning_presets.py`.
Verify with `diag_arc23.py`. Landscape transform drifted? `fix_landscape_headless.py`.

One-time bootstrap on a fresh machine: create a UE 5.8 **Vehicle template**
Blueprint project named `OpenDriveUE`, enable the **Python Editor Script** and
**Geometry Script** plugins, create an empty level `TailOfTheDragon`, and do
the single manual Landscape import (transform values in `stage_meta.json`;
the import dialog treats Location as the landscape *center* — run
`fix_landscape_headless.py` afterwards). Heightmap *updates* after that are
fully scripted.

## In-game keys & test hooks

| Key / flag | Effect |
|---|---|
| **T** | Cycle tuning preset (HUD toast shows which) |
| **R** | Respawn at the last checkpoint gate |
| `-odshot=N` | Screenshot N s after boot → `Saved/Screenshots/` (headless visual QA) |
| `-odrev=T` | Full throttle from T s (audio/physics tests) |
| `-odrec=D` | Record D s of audio → `Saved/AudioTest/odrec.wav` (`pipeline/analyze_audio_test.py` verifies) |
| `-odtune=N` | Auto-cycle tuning presets every N s |

Headless game run for verification:
`UnrealEditor.exe <uproject> /Game/TailOfTheDragon -game -windowed -resx=1280 -resy=720 -odshot=25 -log`
then read `OpenDriveUE/Saved/Logs/OpenDriveUE.log` (manager heartbeats:
`ODForestManager:`, `ODVehicleAudio:`, `ODTuningManager:`).

## Data credits (release requirement)

- Road geometry: © OpenStreetMap contributors, ODbL.
- Elevation: USGS 3DEP via AWS Terrain Tiles; imagery: USGS — public domain,
  credit courtesy.
- Climate normals: NASA POWER.
- Planned (Arc 7): ESA WorldCover (CC-BY 4.0), PRISM/WorldClim — credit when
  integrated.
