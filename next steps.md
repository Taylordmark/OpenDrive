# OpenDrive — Plan of Record

*Written 2026-07-14; statuses consolidated 2026-07-16. Setup, workflows, and
test hooks live in [`README.md`](README.md).*

**Vision:** procedurally generated racing across real-world places at 1:1
scale, built from open data. Stylized "real places, storybook look".
**Distribution target:** free public release (itch/GitHub) — needs packaging,
sane defaults, and a data-attribution screen, but no store cert process.

---

## Current status (2026-07-16)

**Arcs 1, 2, 3, and 5.1 are executed and machine-verified.** The Dragon is a
complete game loop: tuned surface-aware vehicle with five swappable tuning
archetypes, runtime forest with trunk collision, day/night with headlights,
wet-climate roads, timed runs with sector splits and a PB ghost, procedural
engine/road/impact audio. Awaiting Taylor's drive-test verdict on feel
(suspension, tree-wall density, audio character — all one-line constants to
retune).

**Known debts:** no menus/settings/garage (Arc 4), no packaged-build proof —
forest mask needs a cooked CPU copy (cross-cutting), silent on wheel/FFB
(Arc 6), one hardcoded stage (Arc 7). Moon/stars night layer and standing-
start countdown are deliberate deferrals.

## Standing decisions

1. **Art:** stylized (Fortnite/Marvel Rivals realism level). Procedural assets
   should read as intentional style. No photorealism chases.
2. **Data:** nothing per-instance is ever stored (Taylor's rule). Store compact
   rasters/vectors (elevation, land cover, roads, climate); everything visible
   is generated deterministically at runtime or in a rebuildable cache.
3. **Engine code:** C++ for vehicle physics, world gen, audio DSP; Blueprints
   for UI and game flow; Python for offline pipeline and editor automation.
4. **Multiplayer:** "maybe someday" — no replication work now, but keep choices
   replication-friendly when free (deterministic world gen from seeds, tuning
   as data assets referenced by ID, ghost recordings as portable artifacts).
5. **Cars:** CC0 low-poly packs (Kenney or similar) — license-clean, art-fit,
   downloadable by pipeline without editor sessions.
6. **Licensing/attribution (release requirement):** credits screen must cover
   the full list in README.md § Data credits.

---

## Arc 1 — Vehicle feel ✅ *(complete 2026-07-16)*

- **1.1 C++ module** ✅ — `Source/OpenDriveUE`, headless build via Build.bat.
- **1.2 Chaos tuning** ✅ — damping 0.5→0.9, COM override z=42, downforce 1.1.
- **1.3 Road profile** ✅ — ROAD_LIFT 0.35→0.10, superelevation banking
  (outer-edge raise pivoted on the inner edge).
- **1.3b Cut-wall carve** ✅ — carve blend narrowed 10–30 m → 6.5–13 m; the
  natural slope resumes at the shoulder (canyon proximity). Later refinement
  (streamed world): explicit near-vertical uphill cut + rock material band.
- **1.4 Per-tire surface grip** ✅ — PM_Asphalt/Dirt/Grass physical materials;
  `AODSurfaceTractionManager` scales each wheel's friction by contact physmat
  (asphalt 1.0 / dirt 0.72 / grass 0.60); services any vehicle, no BP edits.
- **1.5 Tuning archetypes** ✅ — `UODTuningPreset` data assets
  (Street/Rally/Drift/Drag/Offroad in `/Game/OpenDrive/Tuning`), deltas over a
  captured per-car baseline; `AODTuningManager` cycles on **T** with HUD toast;
  13 mid-drive swaps at full throttle verified stable. Garage UI (4.3)
  consumes these same assets.
- **1.6 Tree collision** ✅ — redesigned after drive test: landscape-grass
  instances are editor-only (zero foliage in standalone `-game`, no queryable
  CPU transforms), so trees moved to `AODForestManager` — deterministic
  tile-seeded C++ scatter (96 m tiles, mask forest weight, per-tile dominant
  species, asphalt-physmat rejection), per-tile HISMs, trunk capsule pool from
  owned transforms. Verified standalone: 90k instances, 48 live capsules,
  full-throttle car held by trunks. Also the seed of Arc 7.2 runtime world-gen
  and the 7.3 flora-recipe hook.

## Arc 2 — Atmosphere ✅ *(2.1 + 2.2 executed 2026-07-15)*

- **2.1 Day/night** ✅ — `AODDayNightController`: 1 h cycle, sun rotation
  (lights must be Movable — editor-spawned default Stationary), per-vehicle
  headlight spots auto-on below 8° sun, exposure widened EV100 −3..16.
  *Remaining polish:* moon/stars night layer; "real-time accurate" sun mode
  arrives with Cesium georeferencing (Arc 7).
- **2.2 Rainfall-darkened roads** ✅ — `fetch_climate.py` (NASA POWER, no key)
  → `road_moisture` 0.83 for the Dragon → darker albedo baked into T_Road +
  M_Road roughness 0.9 − 0.35·moisture. Stage scalar kept in `climate.json`
  for future weather systems.

## Arc 3 — Race framework ✅ *(executed 2026-07-15)*

`AODRaceManager`: 62 gates every 300 m from the centerline, flying start
(start gate arms at rolling speed only), timer, sector splits vs best
(green/red delta flash), best-time SaveGame, 10 Hz transform recording →
`AODGhost` spectral replay of the PB, **R** = respawn at last gate.
`AODRaceHUD` (canvas): timer, gates, splits, last/best, mph, preset toast.
*Remaining polish:* standing-start countdown → belongs to Arc 4's race-mode
UI; full input recording (currently transforms only).

## Arc 4 — UX  *(wishlist #9, #6, #7 — plus settings for release)*

*UMG/Blueprint-heavy; the weakest area for headless automation. Expect
collaborative editor sessions; widget logic in C++, layouts by hand together.*

### 4.1 Control rebinding (#9)
- Migrate to Enhanced Input (if template hasn't); mapping contexts for
  keyboard/gamepad; rebinding settings screen writing to a user config
  `SaveGame`. Include deadzone/sensitivity sliders (feeds Arc 6 wheel).

### 4.2 Map view (#6)
- Stage map widget: stylized terrain texture + road polyline + live player
  marker (world→map transform is one affine we already know). Toggle key +
  pause-screen full map. NA-scale map arrives with Arc 7 (tile-based).

### 4.3 Garage (#7 cars + cosmetics, consumes 1.5 presets)
- **Assets:** pipeline script downloads + license-records a CC0 low-poly car
  pack (Kenney Car Kit or equivalent); headless import; per-car data asset
  (mesh, wheel offsets, base tuning).
- Chaos setup per car (wheel mapping) — some hand work per vehicle, accept it.
- Cosmetics v1: paint color/wrap params on a shared car material; persisted.
- Garage UI: car select, color picker, tuning preset picker (1.5 assets),
  exhaust sliders (5.2 params — already exposed on `UODEngineSynth`).
- Acceptance: 4–6 drivable cars, color choice, preset choice, all persisted.

### 4.4 Release settings shell
- Graphics (resolution/quality/upscaler), audio volumes, input, credits screen
  with the full data-attribution list. Required for the public release.

**Size:** 3–5 sessions. **Depends on:** Arc 3 (menus wrap the loop).

## Arc 5 — Audio  *(wishlist #12, #13)*

- **5.1 Procedural engine v1** ✅ *(2026-07-15, C++ per standing decision 3
  instead of MetaSounds — also headlessly verifiable)* — `UODEngineSynth`
  (firing-freq harmonic stack, load-shaped tanh rasp, throttle-opened muffler
  LPF), `UODRoadNoiseSynth` (surface-colored tire bed keyed off 1.4 physmats,
  speed² wind, impact thumps from collision impulses), `AODVehicleAudio`
  conductor. FFT-verified: 60 Hz fundamental at 900 RPM idle (exact for
  8 cyl) → ~380 Hz at full throttle. Awaiting Taylor's ears for character
  tuning (three constants).

### 5.2 Exhaust customization (#13)
- `Loudness` / `Rasp` / `MufflerCutoff` already exposed on the synth; add
  pop/burble probability on lift-off; per-car "exhaust" struct; garage sliders
  (4.3); persisted.

### 5.3 Simulated engine sample banks (#12c, later upgrade)
- Offline: open-source physical engine simulator (Engine Simulator /
  angethegreat) renders a sample grid (RPM × load) per engine configuration in
  the pipeline; runtime: granular/crossfade playback behind the same
  interface. Exhaust edits = re-render bank with different muffler geometry.
- The "accurately simulate" tier; the 5.1 synth stays as fallback.

**Size:** 5.2 = small (garage-gated); 5.3 = separate multi-session project.

## Arc 6 — Wheel / sim rig support  *(wishlist #10)*

- Enable **RawInput** plugin; device mappings for common wheels (Logitech,
  Fanatec ids); wheel axis → steering with linearity setting, pedals →
  throttle/brake/clutch axes; H-shifter buttons.
- Per-device profiles saved via 4.1's input settings.
- **Force feedback: explicitly deferred.** Native UE FFB support is weak;
  revisit via dedicated plugin/SDK after release v1. v1 = wheel input works.
- Acceptance: playable start-to-finish with a wheel, axes calibrated in UI.

**Size:** 1–2 sessions (+unknown for FFB later). **Depends on:** 4.1.

## Arc 7 — North America  *(wishlist #5 — the campaign)*

Goal: drive anywhere in North America; curated stages become fast-travel
points inside one continuous streamed world. Everything above survives intact.

### 7.1 Cesium foundation
- Cesium for Unreal plugin; georeference so the existing stage frame (X=east,
  Y=south, Z=up — chosen for this from day one) maps 1:1.
- Terrain source decision with **release licensing** in mind: Cesium ion free
  tier is fine for dev but has terms/quotas for distribution — plan for
  self-hosted terrain tiles from 3DEP/Copernicus (cesium-terrain-builder) or
  direct quantized-mesh from AWS terrain tiles. Decide here.
- Milestone: drive off the Dragon's edges onto streamed whole-earth terrain;
  accurate sun hook-up (2.1 upgrade) lands here too.

### 7.2 Runtime road generation (C++)
- Port `build_stage.py`'s core (OSM ways → stitched polylines → grade-smoothed
  profile → banked road mesh) to a C++ `RoadTileBuilder`: tile request →
  fetch/cache → worker-thread mesh build → spawn/despawn with hysteresis.
  (`AODForestManager` already proves the tile pattern at small scale.)
- Data: preprocessed OSM road tiles (offline pyosmium job converts Geofabrik
  NA extracts into a compact binary tile pyramid; target ≤ a few KB/km²)
  hosted as static files; client caches to disk (hybrid delivery decision).
- Terrain interaction: no landscape carving in streamed world — roads carry
  their own skirts/aprons (already do).
- Physics/precision: Large World Coordinates checks; verify Chaos far from
  origin (test Alaska).
- Milestone: pick a random NA highway by lat/lon, it builds around you as you
  drive at 100 mph with no hitches > 5 ms.

### 7.3 Biomes + surfaces at scale
- **ESA WorldCover 10 m** tiles replace satellite-color classification as the
  mask source (same mask→forest/detail contract; classifier code isolated).
- **Regional flora vibes** *(Taylor, 2026-07-14)*: species mix should match
  the region's *feel*, not its botany — incredibly dense deciduous canopy in
  the Southeast, evergreen-dominated north/mountains, scrub in the SW.
  Implementation: per-region foliage recipes (mesh set + palette + density
  curve) selected by land cover + climate rasters; `AODForestManager`'s
  per-tile species system is the hook — this is data + palette work.
- Imagery drape: USGS for US at stage fidelity; global stylized fallback =
  colorized land cover + hillshade (avoids imagery licensing/volume).
- PRISM/WorldClim moisture becomes a world raster feeding road tint and
  future weather; region flavor presets extend the palette/mesh-set system.
- **Packaged-build mask:** forest reads the terrain mask from editor-only
  texture Source today — needs a cooked CPU copy (small binary asset written
  at import). Required before any packaged release; small, standalone task.

### 7.4 World game layer
- NA map view (tiled, zoomable — upgrade of 4.2), free-roam ↔ stage entry
  flow, stage discovery ("drive to a famous road to unlock its time trial"),
  cache management UI (size cap, clear) — release requirement.

**Size:** the campaign — months, parallel with everything after Arc 3.
Bite order: 7.1 → 7.2 (hardest) → 7.3 → 7.4.

---

## Cross-cutting (release engineering)

- **Packaging:** `BuildCookRun` scripted (headless-friendly) — unblocked now
  that Arc 3 is done; first smoke test should include the 7.3 cooked-mask fix
  so the packaged forest works. Fresh-machine smoke test each arc;
  installer/zip for itch.
- **Performance budget:** 60 fps on a mid GPU at 1080p; watch grass/HISM
  instance counts, Lumen cost at night (headlights), streaming hitches (7.2).
- **Crash discipline:** keep the headless-automation rule; new UE crashes get
  `Saved/Crashes` triage before workarounds.
- **Repo hygiene:** ✅ git initialized 2026-07-15 (`main`; pipeline, unreal,
  Source, Config, docs; Content + data git-ignored as regenerable).

## Wishlist → plan index

| # | Wishlist item | Where | Status |
|---|---|---|---|
| 1 | rainfall-dark roads | 2.2 (stage), 7.3 (world raster) | ✅ stage |
| 2 | day/night | 2.1 (cycle), 7.1 (real-time accurate) | ✅ cycle |
| 3 | floaty car | 1.2 | ✅ |
| 4 | per-tire grip | 1.4 | ✅ |
| 5 | all of North America | Arc 7 | — |
| 6 | map view | 4.2 (stage), 7.4 (world) | — |
| 7 | garage/cosmetics | 4.3 | — |
| 8 | performance mods | 1.5 (+4.3 UI) | ✅ presets |
| 9 | control editing | 4.1 | — |
| 10 | wheel/sim rig | Arc 6 (FFB deferred) | — |
| 11 | road elevation ÷2 | 1.3 (+1.3b cut wall) | ✅ |
| 12 | realistic engine sound | 5.1 ✅, 5.3 upgrade later | ✅ v1 |
| 13 | exhaust editing | 5.2 (params live, UI in 4.3) | params ✅ |
| 14 | tree collision (added) | 1.6 | ✅ |

**Recommended next:** packaging + cooked mask (release-path proof, fully
autonomous), then Arc 4 UX (collaborative sessions).
