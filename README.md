# OpenDrive

Procedurally generated racing across real-world places at 1:1 scale, built
entirely from open data. Stylized "real places, storybook look" art direction.
UE 5.8, C++ + Python. Target: free public release (itch/GitHub).

**Current stage:** Tail of the Dragon — the real US-129 (Deals Gap → Tabcat
Creek, 18.2 km, 318 curves) on real USGS terrain.

## What works today

- Real terrain + grade-conforming, banked road meshes generated from USGS
  3DEP elevation and OpenStreetMap geometry — the road rides the real ground
  and only deviates (cut/bridge/tunnel) where a per-highway-class grade
  limit forces it to, never terrain-smoothed; roads carved into the
  mountainside with a tight cut-wall blend (canyon-wall proximity
  preserved).
- **Fully custom vehicle physics** (`UODVehicleMovement`, no Chaos vehicles):
  raycast spring/damper suspension with real per-corner loads, slip-curve
  tire model (slip ratio + slip angle on one friction circle — burnouts,
  lockups, tail-out oversteer all live), integrated engine + auto gearbox
  (rev limiter, kickdown, coast-hold, torque-cut shifts, clutch-bite launch
  flare), per-tire surface grip (road vs. off-road via physical materials -
  the player is meant to stay on the road, so grip doesn't simulate specific
  ground textures), aero drag + downforce. Five tuning archetypes — Street / Rally / Drift /
  Drag / Offroad — as data assets, cycled with **T**.
- **Driver assists** (pause-menu toggles): traction control High (never
  breaks loose) / Low (slides allowed, ESC brakes catch excess yaw) / Off;
  ABS on/off (select-low-per-axle slip-release brake modulation — matches
  real production ABS so keyboard/M&K braking doesn't pull — vs. honest
  lockups off).
  Gearbox holds gear through committed throttle ("attack mode") and only
  settles into normal auto-shifting after sustained light throttle.
- Runtime forest: deterministic tile-seeded scatter (zero per-tree storage),
  wall-of-trees density, per-tile dominant species, trunk collision via a
  capsule pool. Works in editor, standalone, and packaged builds (cooked
  mask sidecar).
- Day/night cycle (1 h default) with automatic vehicle headlights at dusk.
- Rainfall-darkened, wet-gloss roads from NASA POWER climate normals.
- Race framework: 62 checkpoint gates, flying start, timer + sector splits
  vs your best, persistent best times, spectral ghost replay of the PB,
  **R** respawns at the last gate. Round, zoomable mini-map (**M** / **[ ]**)
  drawing the real road centerline; full map on the pause screen.
- Procedural audio, all C++ DSP: pulse-train engine synth (per-cylinder
  firing table, displacement-voiced resonance, audible rev-limiter chatter),
  surface-colored tire noise, wind, collision thumps. FFT-verified pitch
  tracking (60 Hz fundamental at 900 RPM idle, exact for 8 cyl).

**Also underway (not yet part of the playable game):** Arc 7's open-world
foundation — real-world terrain baked entirely offline and streamed at
runtime by our own `AODTerrainManager` (no engine plugin, no network
dependency), with a real OSM road network, procedural buildings, runtime
forest, and a continuous collision barrier all machine-verified on a
separate open-world test area (Oahu, HI as of 2026-07-27). See
`status.md` § 7.0–7.10 for status.

**Arc 7 scope, narrowed 2026-07-27:** the target is the **mountain areas** of
North America — Cascades, Sierra Nevada, Rockies, Blue Ridge/Appalachians,
coast ranges, plus all of Hawaii and Alaska — as discrete zones each with its
own local coordinate frame, rather than one continuous continent. The plains,
Midwest, Gulf and most of the desert Southwest are cut. See
[`AUDIT.md`](AUDIT.md) for the implementation audit that drove this and the
ranked work queue that came out of it — items 0–3 (barrier-seed
determinism, adaptive martini terrain, retired-tile cleanup, bridge/tunnel
elevation) are done as of 2026-07-28; `status.md` § 7.10 has the
maintained status, `AUDIT.md` itself is a dated snapshot.

Current per-arc status: [`status.md`](status.md). Open work: [`next steps.md`](next%20steps.md).

## Repo layout

| Path | What it is |
|---|---|
| `pipeline/` | Offline data pipeline (Python): fetch open data, build stage artifacts |
| `unreal/` | Editor automation (Python, run headless via `UnrealEditor-Cmd`) |
| `OpenDriveUE/Source/` | C++ game module (all `OD*` classes) |
| `OpenDriveUE/Config/` | Project settings |
| `data/stages/<stage>/` | Built stage artifacts (git-ignored; deterministic re-runs) |
| `data/road_tiles/<area>_test/` | Open-world pipeline intermediates (road_stations.json, road_mesh.json, git-ignored) — baked terrain tiles themselves land directly in `OpenDriveUE/Content/OpenDrive/Masks/` |
| `status.md` | What's built: per-arc status, standing decisions, process rules |
| `next steps.md` | Open work only — remove items as they land in `status.md` |
| `AUDIT.md` | Implementation audit (2026-07-27): how the world is built + delivered, measured limits, ranked work queue |

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
`apply_vehicle_audio.py` → `apply_tuning_presets.py` →
`apply_light_wet_fix.py` → `apply_pause_menu.py` →
`pipeline/make_car_mesh.py` + `apply_vehicle_pawn.py` (placeholder car +
ODGameMode) → `apply_road_physmat.py` (asphalt physmat on M_Road — the
suspension raycasts read the *render* material's physmat) →
`apply_unpaved_road_materials.py` (M_Road_Gravel + PM_Gravel, M_Road_Dirt +
PM_DirtRoad for unpaved road chunks; km per class per area:
`python pipeline/checks/surface_km.py`) → `apply_lens_drop_material.py` (M_LensDrop for rain/snow
landing on the camera lens) →
`pipeline/export_centerline_bin.py` (real road centerline for the mini-map,
reads the already-built `road_mesh.json` — no network calls).
Verify with `diag_arc23.py`. Landscape transform drifted? `fix_landscape_headless.py`.
(`apply_drivetrain.py`, `apply_transmission_fix.py`, and
`remove_chaos_managers.py` are historical — superseded by the Chaos rip-out;
the vehicle is 100% `UODVehicleMovement` in C++ now.)

One-time bootstrap on a fresh machine: any UE 5.8 C++-capable project named
`OpenDriveUE` works (the old Vehicle-template content is deleted; the car is
pipeline-generated), enable the **Python Editor Script** and **Geometry
Script** plugins, create an empty level `TailOfTheDragon`, and do the single
manual Landscape import (transform values in `stage_meta.json`; the import
dialog treats Location as the landscape *center* — run
`fix_landscape_headless.py` afterwards). Heightmap *updates* after that are
fully scripted.

Arc 7 (open-world terrain streaming) needs no plugin at all — terrain is
baked entirely offline by the pipeline and streamed at runtime by our own
`AODTerrainManager` (plain C++, no engine plugin dependency).

### Rebuilding the Arc 7 test terrain + road network

Restructured 2026-07-20 to a strict load-order — terrain, then road, then
overlay — with elevation sampled exactly once and everything else built
from that. Restructured again for the Arc 7.0 pivot (2026-07-23): the
terrain itself is no longer a live-streamed tileset at all — it's baked
once, offline, into world-space tile files and streamed at runtime by
`AODTerrainManager`, so there is nothing left to reconcile against at
runtime, ever.

```
python pipeline/rebuild_area.py
```

Runs every stage that feeds `OpenDriveUE/Content/OpenDrive/Masks/` for the
active `test_areas.AREA_NAME` — fetch, stations, retaining walls, terrain,
road network + bin export, barrier, buildings, place labels, biome/
settlement classification, map texture, and the terrain-fit gate — in the
one dependency order the pipeline actually requires, and stops immediately
on the first stage that fails so a partial rebake never gets left on disk.
See the script's own docstring for the full stage list and why each stage
sits where it does; see `AUDIT-RELIABILITY.md` §3.3 for why hand-listing a
subset of these steps (the old version of this section) was itself the
mechanism producing mixed-generation bakes.

Roadside furniture (PLAN-REALISM Phase 1) rides in the same run:
`build_road_furniture.py` → `export_road_furniture_bin.py` (ODF1 tiles for
`AODFurnitureManager`). They run after the `experimental_junction_pads.py`
stage, since props sit on the pad-snapped road.
Gate: `python pipeline/verify_furniture_fit.py` (no prop in a lane, bases on
terrain/edge, control poles ≤ 15 m from their OSM node, per-type counts) and
`python pipeline/tests/test_furniture_place.py`. The meshes are a one-off:
`python pipeline/make_furniture_meshes.py`, then headless
`unreal\import_furniture_meshes.py` (→ `/Game/OpenDrive/Furniture`,
`M_Furniture` + `MI_Furn_*`).

Route / lane graph (PLAN-REALISM 3a): `export_road_centerline_bin.py` writes
`routegraph_<area>_test.bin` (ODG1), which nav reads now and traffic will
read later. It reads the pad-patched shipped stations and
`road_furniture.json` `controls`, so `rebuild_area.py` runs it after the
pads and furniture stages. By hand, keep that order: pads, then furniture,
then `python pipeline/export_road_centerline_bin.py`. Layout (the full spec is the script's
docstring):

- `'ODG1'`, `u32 N`, `N × (f32 x_cm, f32 y_cm)`;
- `u32 E`, `E × (u32 a, u32 b, f32 len_cm)`;
- the one-way block: `E × i8`;
- the optional lane block:
  - `N × f32 z_cm`;
  - `N × u8` control: 0 none, 1 signal, 2 stop, 3 give way, 4 priority;
  - `E × u8` class: bits 0–2 class id, 8 ramp, 16 no pavement;
  - `E × u8` lanes per direction;
  - `E × u8` km/h;
  - `E × u8` yield: bit 0 means a→b stops at a, bit 1 means b→a stops at b.

Files ending after the edges or after the one-way block still load. The gate
is `python pipeline/checks/verify_route_graph.py` (it must print ALL CHECKS
PASSED); also run `python pipeline/tests/test_route_graph_lanes.py`.

`verify_terrain_fit.py` is the gate to run before ever loading the level —
it checks a fresh in-memory rebuild of the terrain mesh against
`road_stations.json`, not the pre-bake elevation grid. **It does not read the
`.bin` files actually written to `Content/OpenDrive/Masks/`**
(`AUDIT-RELIABILITY.md` §4.1), so a "PASS" is necessary but not sufficient —
it can't catch a stale/partial bake on disk. Load the level and eyeball it
before trusting a rebake. Far/background tiles print for information only
since the player is never meant to stray far enough from the road to see
them close up. No local tile
server is needed — the baked tiles live directly under
`OpenDriveUE/Content/OpenDrive/Masks/` and `AODTerrainManager` streams them
from local (or packaged) Content, same as the road/forest tile managers.

Then headlessly (editor closed, and delete
`OpenDriveUE/Content/OpenWorldTest.umap` from disk first if it already
exists — see that script's docstring for why):
`UnrealEditor-Cmd.exe <uproject> -run=pythonscript
-script=unreal\build_openworld_test_level.py -nullrhi -unattended -nosplash
-nop4 -stdout` builds `/Game/OpenWorldTest` fresh. Load it with
`UnrealEditor.exe <uproject> /Game/OpenWorldTest -game -windowed -log`
(prefix with `MSYS_NO_PATHCONV=1` in Git Bash or the leading `/Game/...`
gets mangled into a Windows path). Separate from `TailOfTheDragon.umap` —
nothing here touches the playable stage.

**Resolved (2026-07-20, superseded by the Arc 7.0 baked-terrain pivot on
2026-07-23):** the road reliably fits the terrain — verified three
independent ways (the offline mesh-accurate fit gate, a live in-engine
`-odseam` collision-trace check, and a visual screenshot) after two real
bugs were found and fixed: the terrain carve was being applied before
pymartini's own `.T` transpose, stamping every station at its geographic
diagonal-mirror position instead of its real one (not a carve-width issue —
this alone caused the ~60–440 m mismatches seen before); and the near-zoom
simplification tolerance (`MAX_ERROR`) was loose enough to leave a few real
~2.5 m gap/overlap clusters even after that fix, tightened separately for
the finest zoom only. All the runtime ground-snap/wait-for-terrain logic
this used to lean on (`AODRoadManager::SnapChunkToGround`, the
await-terrain queue/timeout) has been deleted — the pipeline now guarantees
the fit before the engine ever sees the data, so there's nothing left to
reconcile at runtime.

Packaged build (proven 2026-07-18; needs `pipeline/make_forest_mask_bin.py`
run first so the forest mask has its cooked sidecar):

```
RunUAT.bat BuildCookRun -project=<uproject> -platform=Win64 ^
    -clientconfig=Development -build -cook -stage -pak -archive ^
    -archivedirectory=<dir> -noP4 -unattended -utf8output
```

Output: `<dir>\Windows\OpenDriveUE.exe`. Smoke-test it headlessly with the
same `-odtrace`/`-odrev` hooks plus `-abslog=<file>`.

## In-game keys & test hooks

| Key / flag | Effect |
|---|---|
| **ESC / P** | Pause menu: tuning, gearbox, engine, exhaust, traction, ABS, quit (stopgap for Arc 4 UI) |
| **T** | Cycle tuning preset (HUD toast shows which) |
| **R** | Respawn at the last checkpoint gate |
| **M** | Toggle the mini-map; **[ / ]** zoom out/in (full map lives on the pause screen) |
| `-odshot=N` | Screenshot N s after boot → `Saved/Screenshots/` (headless visual QA) |
| `-odrev=T` | Full throttle from T s (audio/physics tests; lives on the pawn) |
| `-odlift=T` | Throttle back to zero at T s (lift-off transients) |
| `-odbrake=T` | Full brake from T s (after `-odlift` — braking/ABS tests) |
| `-odsteer=T` | Half steering from T s (lateral/yaw tests) |
| `-odthr=F` | Cap scripted throttle at F (e.g. `"-odthr=0.45"` — **quote it**: PowerShell 5.1 splits bare `-x=0.45` at the decimal point) |
| `-odtc=high\|low\|off` | Pin traction-control mode (assist tests) |
| `-odabs=0\|1` | Force ABS on/off (assist tests) |
| `-odrec=D` | Record D s of audio → `Saved/AudioTest/odrec.wav` (`pipeline/analyze_audio_test.py` verifies pitch; `pipeline/analyze_brap.py` verifies limiter-chatter envelope modulation) |
| `-odtune=N` | Auto-cycle tuning presets every N s |
| `-odtrace` | 10 Hz vehicle trace (rpm/gear/thr/speed/slip/loads/yaw/susp/surface/tc/esc/abs/relax) to the log |
| `-odcrashtrace` | Crash trace (`ODCrashTrace:` lines): per-frame body velocity/spin/wheel contacts around each wall hit or one-frame velocity jump (10 frames before, 2 s after), each hit's normal/penetration/solver vs. response speed, then `SUMMARY ... PASS/FAIL` (peak speed after ≤ impact speed) |
| `-odmenu=T` | Open the pause menu at T s + screenshot (headless menu QA) |
| `-odmaskbin` | Force the cooked forest-mask sidecar path in editor builds (packaging QA) |

Headless game run for verification:
`UnrealEditor.exe <uproject> /Game/TailOfTheDragon -game -windowed -resx=1280 -resy=720 -odshot=25 -log`
then read `OpenDriveUE/Saved/Logs/OpenDriveUE.log` (manager heartbeats:
`ODForestManager:`, `ODVehicleAudio:`, `ODTuningManager:`).

## Data credits (release requirement)

- Road geometry: © OpenStreetMap contributors, ODbL.
- Elevation: USGS 3DEP via AWS Terrain Tiles; imagery: USGS — public domain,
  credit courtesy.
- Climate normals: NASA POWER.
- Traffic volumes: FHWA HPMS (geo.dot.gov), state lookup via the US Census
  geocoder — public domain.
- Planned (Arc 7): ESA WorldCover (CC-BY 4.0), PRISM/WorldClim — credit when
  integrated.
