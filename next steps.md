# OpenDrive — Next Steps

*Plan of record from current status to full execution. Written 2026-07-14.*

**Vision:** procedurally generated racing across real-world places at 1:1 scale,
built from open data. Stylized "real places, storybook look" art direction.
**Distribution target:** free public release (itch/GitHub) — needs packaging,
sane defaults, and a data-attribution screen, but no store cert process.

---

## Current status (what exists today)

- **Playable stage:** Tail of the Dragon (US-129, 18.17 km) — real USGS 3DEP
  terrain, grade-smoothed road from OSM geometry, runtime forest via Landscape
  Grass, stylized satellite-draped terrain, marked asphalt, post-process grade.
- **Data pipeline** (`pipeline/`): fetch (Overpass + terrain tiles + USGS
  imagery) → build (heightmap w/ carved road corridor, road meshes, masks,
  stylized textures, foliage meshes) → all deterministic and re-runnable.
- **Headless UE automation** (`unreal/*.py` via `UnrealEditor-Cmd -run=pythonscript`):
  imports assets, builds materials/grass types by script, edits and saves the
  level with the editor closed. This is the standard workflow — in-editor
  interactive Python crashes (python311 access violations) and is avoided.
- **Known debts:** floaty car, road sits ~0.5 m proud of terrain, drive-through
  trees, no water surfaces, no game loop (timer/checkpoints), silent, one
  hardcoded stage, no menus.

## Standing decisions

1. **Art:** stylized (Fortnite/Marvel Rivals realism level). Procedural assets
   should read as intentional style. No photorealism chases.
2. **Data:** nothing per-instance is ever stored (Taylor's rule). Store compact
   rasters/vectors (elevation, land cover, roads, climate); everything visible
   is generated deterministically at runtime or in a rebuildable cache.
3. **Engine code:** convert to **C++ project at the start of Arc 1**. C++ for
   vehicle physics, world gen, audio DSP; Blueprints for UI and game flow;
   Python for offline pipeline and editor automation.
4. **Multiplayer:** "maybe someday" — no replication work now, but keep choices
   replication-friendly when free (vehicle state in movement component, tuning
   as replicated-able data assets, deterministic world gen from seeds).
5. **Cars:** CC0 low-poly packs (Kenney or similar) — license-clean, art-fit,
   downloadable by pipeline without editor sessions.
6. **Licensing/attribution (release requirement):** credits screen must cover
   OpenStreetMap (ODbL), USGS 3DEP/imagery (public domain, credit courtesy),
   ESA WorldCover (CC-BY 4.0), PRISM/WorldClim (per their terms), CC0 car pack,
   Cesium ion terms if used (see Arc 7 note).

---

## Arc 1 — Vehicle feel  *(wishlist #3, #11, #4, #8)*

Goal: the car feels planted and surface-aware; game becomes fun to drive.

### 1.1 Convert project to C++
- Add a C++ module to OpenDriveUE (File > New C++ Class once, or generate
  `Source/` tree + `.uproject` module entry by script; VS 2026 already installed).
- Verify `UnrealEditor-Cmd` build path and that headless python still works.
- Acceptance: project compiles, template vehicle still drives.

### 1.2 Chaos tuning pass (#3 floaty car)
- Create `UDataAsset`-based tuning set: mass (~1500 kg), center-of-mass drop,
  spring rate / damping per axle, anti-roll, tire slip curves, downforce coeff,
  gearing. Start from template SUV, iterate against feel notes at 60–100 mph
  through the switchback section (stations ~800–2200).
- Deliverable: `DA_Tuning_Street` + a debug HUD readout (speed, slip, spring
  compression) to make tuning discussable ("floaty" → numbers).
- Acceptance: Taylor signs off on a lap; braking distances and body roll
  subjectively sane.

### 1.3 Road profile regen (#11 road too proud)
- `build_stage.py`: `ROAD_LIFT` 0.35 → ~0.18 m; carve offset −0.15 → −0.08 m;
  optionally tighten carve `inner` 10 → 8 m so verge meets pavement sooner.
- Add **crossfall/banking** while we're in there: bank the road cross-section
  into curves (clamp ~6°), computed from local curvature of the centerline.
- Regen + headless re-apply. Landscape heightmap changes ⇒ one manual landscape
  re-import (Location = 0,0,Z-center in the dialog — it's treated as *center*)
  or scripted `fix_landscape_headless.py` afterwards.
- Acceptance: road edge ≤ ~0.25 m above adjacent terrain on straights; curves
  visibly banked; no terrain poke-through anywhere on a full lap.

### 1.4 Per-tire surface grip (#4)
- Create `PM_Asphalt`, `PM_Dirt`, `PM_Grass` physical materials; assign to road
  meshes (asphalt) and landscape (dirt/grass — landscape supports per-layer
  physmats; we have one layer, so start with dirt overall).
- C++ on the vehicle: per wheel, read contact surface physical material each
  tick; scale that wheel's friction multiplier (asphalt 1.0, dirt ~0.75,
  grass ~0.6, add slip-angle looseness off-road).
- Acceptance: two wheels dropped off the pavement visibly pulls the car; full
  off-road is loose but recoverable.

### 1.5 Tuning archetypes (#8)
- Same `UDataAsset` schema, presets: **Street / Rally / Drift / Drag / Offroad**
  expressed as concrete deltas (rally: +ride height, softer springs, dirt-bias
  grip; drift: rear grip cut, steeper torque; drag: gearing + launch grip; etc.)
  — no explicit archetype labels needed in UI copy, just the effects.
- Runtime-swappable (debug key now; garage UI consumes these in Arc 4).
- Replication note: presets as data assets referenced by ID = MP-friendly later.

### 1.3b Cut-wall carve *(Taylor, 2026-07-14)*
- The wide carve blend (10→30 m) made roadside terrain a gradual ramp and
  killed the "carved out of the mountainside" proximity that makes canyon
  driving exciting. Narrow the blend hard (≈6.5→13 m) so the natural slope —
  cut bank uphill, drop-off downhill — resumes right at the shoulder.
- Later refinement (streamed world): explicitly steepen the uphill face into
  a near-vertical cut with a rock-detail material band.

### 1.6 Tree collision *(wishlist addendum, 2026-07-14)*
*Status 2026-07-15: REDESIGNED after drive test. Landscape-grass instances are
editor-only (zero foliage in standalone -game, no queryable CPU transforms →
collision could never work). Replaced by AODForestManager: deterministic
tile-seeded C++ scatter (96 m tiles, mask forest weight, per-tile dominant
species, asphalt-physmat rejection), per-tile HISMs, trunk capsule pool from
owned transforms. Verified in standalone: 68.7k instances, 48 capsules live.
GT_Forest emptied; GT_Ground keeps tufts only. This is also the seed of the
Arc 7.2 runtime world-gen and the 7.3 flora-recipe hook.*
- Grass-system instances are visual-only by design, so collision is a C++
  **proxy pool**: query the engine-placed grass instance transforms near the
  car each tick, keep ~50 pooled capsule bodies on the trunks within ~60 m,
  recycle capsules as the car moves. Capsule fits the *trunk*, not the canopy
  (clipping foliage at speed should be forgiving; hitting a trunk should not).
- Preserves the zero-per-instance-storage rule — collision derives from what
  the spawner already placed. Costs nothing beyond the near-field pool.
- Pairs with Arc 3's respawn button (getting stuck in a forest needs an out).

**Size:** 2–4 sessions. **Depends on:** nothing. **Unblocks:** Arcs 3, 4, 6.

---

## Arc 2 — Atmosphere  *(wishlist #2, #1)*

*Status 2026-07-15: 2.1 and 2.2 executed (AODDayNightController: 1h cycle,
auto headlights below 8° sun; exposure widened EV100 -3..16; NASA POWER
climate fetch → moisture 0.83 → darker albedo + roughness 0.61 on the
Dragon). Remaining polish: moon/stars night layer, real-time-accurate sun
(Arc 7), weather system consuming the stage moisture scalar.*

### 2.1 Day/night cycle (#2)
- Sun controller (C++ or BP): rotates `Sun` + drives `SkyAtmosphere`;
  moon + stars via night sky layer; auto-exposure range widened for night
  (`CachedLightingPreExposure=8` already set — verify night end of the range).
- **Headlights** on the vehicle (spot + emissive material), auto-on at dusk.
- Cycle length as a setting: 1 h default now; "real-time accurate" mode later
  swaps the rotation driver for a sun-position calculation (lat/long/date —
  comes essentially free with Cesium georeferencing in Arc 7; interim: engine
  "Geographically Accurate Sun Positioning" plugin fed the stage origin lat/lon).
- Acceptance: full cycle drives well at night (headlight throw, exposure), dawn
  and dusk look intentional, no Lumen clipping warnings at any hour.

### 2.2 Rainfall-darkened roads (#1)
- Pipeline: fetch **PRISM 30-yr normal annual precipitation** (US, ~800 m, free;
  WorldClim 2.1 fallback for the rest of NA) → sample along road centerline →
  per-station moisture value 0..1.
- Encode as UV1.y (or vertex color) on the road mesh; `M_Road` darkens albedo,
  drops roughness slightly, and increases crack-noise weight with moisture.
- Stage-level scalar also kept in `stage_meta.json` for future weather systems.
- Acceptance: visibly darker, slightly glossier pavement on the Dragon (wet
  Appalachian climate) vs a future desert stage.

**Size:** 1–2 sessions. **Depends on:** nothing (parallel with Arc 1 fine).

---

## Arc 3 — Race framework  *(not on wishlist; load-bearing for "game")*

*Status 2026-07-15: executed (AODRaceManager: 62 gates every 300 m from the
centerline, timer + best-time SaveGame, 10 Hz transform recording → AODGhost
spectral replay of the PB, R = respawn at last gate; AODRaceHUD canvas HUD
wired into BP_VehicleAdvGameMode). Remaining polish: start countdown, sector
splits.*

- Checkpoint chain generated from centerline stations (every ~300 m) in the
  stage build; start/finish gates at trimmed endpoints.
- Game state: countdown → run → finish; timer HUD; sector splits.
- Persistence: best times + full input/transform recording per run
  (`SaveGame`), which doubles as **ghost** data (MP-friendly artifact).
- Ghost playback actor (spectral material of the player car).
- Reset/respawn-to-road button (needed the moment trees get collision).
- Acceptance: complete timed lap loop with saved PB and rideable ghost.

**Size:** 2–3 sessions. **Depends on:** Arc 1.1 (C++ helps here).

---

## Arc 4 — UX  *(wishlist #9, #6, #7 — plus settings for release)*

*Heads-up: UMG/Blueprint-heavy; the weakest area for headless automation.
Expect collaborative editor sessions; I script what I can (widget logic in
C++, layouts by hand together).*

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
- Garage UI: car select, color picker, tuning preset picker (from 1.5),
  exhaust sliders slot in after Arc 5.
- Acceptance: 4–6 drivable cars, color choice, preset choice, all persisted.

### 4.4 Release settings shell
- Graphics (resolution/quality/upscaler), audio volumes, input, credits screen
  with the full data-attribution list. Required for the public release.

**Size:** 3–5 sessions. **Depends on:** 1.5 (presets), Arc 3 (menus wrap the loop).

---

## Arc 5 — Audio  *(wishlist #12, #13)*

Staged approach ("simulated engine audio" feasibility, decided):

### 5.1 MetaSounds procedural engine v1 (#12a)
- MetaSound graph: per-cylinder pulse oscillators driven by RPM + firing order,
  load-crossfaded intake/exhaust noise layers, muffler = filter chain.
  RPM/load/throttle piped from the Chaos vehicle each tick.
- Also: tire/surface loops keyed off Arc 1.4 physmats (asphalt hiss, gravel),
  wind by speed, collision impacts.
- Acceptance: engine audibly tracks throttle/load/gear; surfaces audible.

### 5.2 Exhaust customization (#13)
- Expose the v1 graph's parameters — master volume (loudness), high-band
  emphasis + distortion drive (rasp), muffler cutoff, pop/burble probability on
  lift-off — as a per-car "exhaust" struct; sliders in the garage; persisted.

### 5.3 Simulated engine sample banks (#12c, later upgrade)
- Offline: open-source physical engine simulator (Engine Simulator /
  angethegreat) renders a sample grid (RPM × load) per engine configuration in
  the pipeline; runtime: granular/crossfade playback in the same MetaSound
  interface. Exhaust edits = re-render bank with different muffler geometry.
- This is the "accurately simulate" tier; keep the v1 graph as fallback.

**Size:** 5.1+5.2 = 2–3 sessions; 5.3 = a separate multi-session project.
**Depends on:** Arc 1 (vehicle telemetry), garage UI for 5.2 sliders.

---

## Arc 6 — Wheel / sim rig support  *(wishlist #10)*

- Enable **RawInput** plugin; device mappings for common wheels (Logitech,
  Fanatec ids); wheel axis → steering with linearity setting, pedals →
  throttle/brake/clutch axes; H-shifter buttons.
- Per-device profiles saved via 4.1's input settings.
- **Force feedback: explicitly deferred.** Native UE FFB support is weak;
  revisit via dedicated plugin/SDK integration after release v1. Set
  expectation: v1 = wheel input works, no FFB.
- Acceptance: playable start-to-finish with a wheel, axes calibrated in UI.

**Size:** 1–2 sessions (+unknown for FFB later). **Depends on:** 4.1.

---

## Arc 7 — North America  *(wishlist #5 — the campaign)*

Goal: drive anywhere in North America; curated stages become fast-travel
points inside one continuous streamed world. Everything above survives intact.

### 7.1 Cesium foundation
- Cesium for Unreal plugin; georeference set so our existing stage frame
  (X=east, Y=south, Z=up — chosen for this from day one) maps 1:1.
- Terrain source decision with **release licensing** in mind: Cesium ion free
  tier is fine for dev but has terms/quotas for distribution — plan for
  **self-hosted terrain tiles** built from 3DEP/Copernicus (cesium-terrain-
  builder) or direct quantized-mesh from AWS terrain tiles. Decide here.
- Milestone: drive off the Dragon's edges onto streamed whole-earth terrain;
  accurate sun hook-up (Arc 2 upgrade) lands here too.

### 7.2 Runtime road generation (C++)
- Port `build_stage.py`'s core (OSM ways → stitched polylines → grade-smoothed
  profile → banked road mesh) to a C++ `RoadTileBuilder`:
  tile request → fetch (or read cache) → build meshes on worker threads →
  spawn/despawn around player with hysteresis.
- Data: preprocessed **OSM road tiles** (offline pyosmium job converts
  Geofabrik NA extracts into a compact binary tile pyramid: polylines + class +
  metadata; target ≤ a few KB/km² average) hosted as static files; client
  caches to disk (the "hybrid delivery" decision).
- Terrain interaction: no landscape carving in streamed world — roads carry
  their own skirts/aprons (already do); flatten via Cesium clipping only if
  needed later.
- Physics/precision: enable Large World Coordinates checks, verify Chaos
  vehicle behavior far from origin (UE5 LWC should handle ±NA distances; test
  Alaska).
- Milestone: pick a random NA highway by lat/lon, it builds around you as you
  drive at 100 mph with no hitches > 5 ms.

### 7.3 Biomes + surfaces at scale
- **ESA WorldCover 10 m** tiles replace satellite-color classification as the
  mask source (same mask→grass/detail contract; classification code already
  isolated).
- **Regional flora vibes** *(Taylor, 2026-07-14)*: tree species mix should
  match the region's feel, not its botany — incredibly dense deciduous canopy
  in the Southeast, evergreen-dominated in the north/mountains, scrub in the
  SW, etc. Implementation: per-region foliage recipes (mesh set + palette +
  density curve) selected by land cover class + climate (temperature/precip
  rasters we already plan to ship). The grass-type system already supports
  swapping variety lists, so this is data + palette work, not architecture. Imagery drape: keep USGS for US at stage fidelity; global
  stylized fallback = colorized land cover + hillshade (avoids imagery
  licensing/volume entirely for free-roam).
- PRISM/WorldClim moisture becomes a world raster feeding road tint (#1) and
  future weather.
- Region flavor presets (SW desert palette/foliage set, boreal conifers, etc.)
  selected by land cover + climate — extends the palette/mesh-set system.

### 7.4 World game layer
- NA map view (tiled, zoomable — upgrade of 4.2), free-roam ↔ stage entry
  flow, stage discovery ("drive to a famous road to unlock its time trial"),
  cache management UI (size cap, clear) — release requirement for a streaming
  game.

**Size:** the campaign — months, in parallel with everything after Arc 3.
Bite order: 7.1 → 7.2 (hardest) → 7.3 → 7.4.

---

## Cross-cutting (release engineering)

- **Packaging:** `BuildCookRun` scripted (headless-friendly) from Arc 3 onward;
  fresh-machine smoke test each arc; installer/zip for itch.
- **Performance budget:** 60 fps on a mid GPU at 1080p; watch masked-foliage
  overdraw, grass instance counts, Lumen cost at night (headlights), streaming
  hitches (7.2).
- **Crash discipline:** keep the headless-automation rule; new UE crashes get
  `Saved/Crashes` triage before workarounds (it found python311 last time).
- **Repo hygiene:** init git for `pipeline/`, `unreal/`, configs, and docs
  (UE `Content/` via LFS or excluded+rebuildable — everything imported is
  regenerable from the pipeline by design). Backup `stage_meta`/wishlist/docs.

## Wishlist → plan index

| Wishlist item | Where |
|---|---|
| 1 rainfall-dark roads | Arc 2.2 (data), 7.3 (world raster) |
| 2 day/night | Arc 2.1 (cycle), 7.1 (real-time accurate) |
| 3 floaty car | Arc 1.2 |
| 4 per-tire grip | Arc 1.4 |
| 5 all of North America | Arc 7 |
| 6 map view | Arc 4.2 (stage), 7.4 (world) |
| 7 garage/cosmetics | Arc 4.3 |
| 8 performance mods | Arc 1.5 (+4.3 UI) |
| 9 control editing | Arc 4.1 |
| 10 wheel/sim rig | Arc 6 (FFB deferred) |
| 11 road elevation ÷2 | Arc 1.3 |
| 12 realistic engine sound | Arc 5.1 now, 5.3 upgrade |
| 13 exhaust editing | Arc 5.2 |
| 14 tree collision (added) | Arc 1.6 |

**Recommended start:** Arc 1 (vehicle feel), with Arc 2 interleaved when
editor-closed windows allow headless runs.
