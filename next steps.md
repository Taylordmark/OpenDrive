# OpenDrive — Plan of Record

*Written 2026-07-14; statuses consolidated 2026-07-19. Setup, workflows, and
test hooks live in [`README.md`](README.md).*

**Vision:** procedurally generated racing across real-world places at 1:1
scale, built from open data. Stylized "real places, storybook look".
**Distribution target:** free public release (itch/GitHub) — needs packaging,
sane defaults, and a data-attribution screen, but no store cert process.

---

## Current status (2026-07-19)

**Arcs 1, 2, 3, and 5.1 are executed and machine-verified. The vehicle runs
on 100% our own physics** (Chaos vehicles fully removed, § 1.8) **with a
full driver-assist stack** (traction control High/Low/Off, ABS on/off,
attack-mode gearbox, clutch-bite launches — § 1.9) **and a pulse-train
engine synth with an audible rev-limiter chatter** (§ 5.1v2). The mini-map
draws the REAL road centerline now, not a gate-spline guess (§ 4.2 v2). The
Lumen atlas-overload warning is fixed (§ Cross-cutting) and keyboard/M&K
braking no longer pulls off a straight line (§ 1.9 ABS). Packaging and
Shipping config are both proven end-to-end. **Arc 7 (North America) is now
underway for real**, not just planned: 7.1's foundation (self-hosted
live-streamed terrain, dynamic tile loading, a genuine 73 mph scripted drive on
real flat terrain) is machine-verified in an isolated test level — see § 7.1.
**7.2 slice 1 (static OSM-network road mesh on that terrain) and slice 2
(tile-based streaming of that mesh, ring-iteration/hysteresis + background
loading) are both built and the Smart App Control block that was blocking
in-game testing has since been cleared** — slice 1 caught and fixed a real
~800 m elevation-frame bug via its own verification hook; slice 2 adds the
first async/background-task code anywhere in `OpenDriveUE`. Extensive live
drive-testing this session found and fixed **seven more real bugs**
(unthrottled ground-snap retries stalling the game, wrong tile-processing
priority, a premature timeout clock, a vehicle-spawn physics depenetration
launch, no plausibility check on ground-snap hits letting a coarse streamed-
terrain LOD placeholder corrupt geometry into a literal wall, road traces hitting
the vehicle's own body, and a player-start pipeline bug placing spawn ~500m
past the map edge) — full list in § 7.2.

**Road/terrain fit RESOLVED 2026-07-20** via a pipeline restructure, not
another reactive patch: load-terrain → load-road → overlay, with elevation
sampled exactly once (`build_road_stations.py`) and the terrain CARVED to
that profile offline (`build_terrain_tiles.py` — *since deleted, superseded by
`build_terrain_mesh_tiles.py` in the 7.0 pivot; see § "Retired scripts"*)
before it's ever encoded,
instead of reconciling the two surfaces at runtime. Found and fixed two
real bugs in the process (a carve-array transpose-order bug that alone
explained the ~440 m worst-case mismatches, and a too-loose near-zoom
simplification tolerance that left a few small but visible gap/overlap
clusters) and deleted ~180 lines of the runtime ground-snap/wait-for-
streamed-terrain machinery this used to depend on — there's nothing left to reconcile at
runtime because the pipeline now guarantees the fit before the engine sees
the data. Verified three independent ways (offline mesh-accurate check,
live in-engine collision trace, screenshot). Full account in § 7.2.

**Immediate to-do (revised 2026-07-28 — see [`AUDIT.md`](AUDIT.md) and
§ 7.9/§ 7.10 below):** the audit's ranked queue items 0–3 (barrier-seed
determinism, adaptive martini terrain, retired-tile cleanup, bridge/tunnel
elevation) are **done** — full account in § 7.10, including a real memory
bug found and fixed along the way and a rebaked/reverified Oahu build. Next
up per the ranked table: item 4 (barrier → out-of-bounds reset), item 5
(merge buildings per tile — the top runtime item), item 6 (OSM road tags).
Taylor's drive-test of the assist stack + gearbox/ABS changes remains the
separate gate before stage-3 preset retune.

**Known debts:** no menus/settings/garage beyond the stopgap (Arc 4.3/4.4),
silent on wheel/FFB (Arc 6), still only one *drivable* stage in the classic
sense (Arc 7's streaming foundation now has a small real road network
streaming in tiles on it too, but it's still a slice-1/2 proof, not a
playable stretch — no foliage on the streamed-terrain side yet, no HTTP-based fetch
of the road tiles). Moon/stars night layer and standing-start countdown are
deliberate deferrals.

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

## Retired scripts — read this before trusting a script name below

Some pipeline scripts named in the historical accounts in this file **no longer
exist**. They were correct at the time of writing; the sections are kept as the
record of *why* things are the way they are. Three were deleted and are
**unrecoverable — none was ever committed** (`git log --all` returns nothing):

| Script | Status | Replacement |
|---|---|---|
| `pipeline/build_terrain_tiles.py` | deleted (7.0 pivot) | `build_terrain_mesh_tiles.py` |
| `pipeline/verify_road_fit.py` | deleted, **not replaced** | none — only `verify_terrain_fit.py` (terrain-vs-stations) survives; road-vs-terrain is currently ungated |
| `road_mesh_common.sample_mesh_height` | deleted, **reconstructed from scratch** | present, but explicitly not validated against the original — every fit number depends on it |
| the from-scratch terrain-test-level build script | retired | `unreal/build_openworld_test_level.py` |

**This is a live failure mode, not just history.** Losing *source* is distinct
from the well-documented "one-off `apply_*.py` script doesn't survive a
rebuild" theme, which loses *effects*. Commit early, even to a scratch branch.

---

## Arc 1 — Vehicle feel ✅ *(complete 2026-07-16)*

- **1.1 C++ module** ✅ — `Source/OpenDriveUE`, headless build via Build.bat.
- **1.2 Chaos tuning** ✅ — damping 0.5→0.9, COM override z=42, downforce 1.1.
- **1.3 Road profile** ✅ — ROAD_LIFT 0.35→0.10, superelevation banking
  (outer-edge raise pivoted on the inner edge).
- **1.3b Cut-wall carve** ✅ — carve blend narrowed 10–30 m → 6.5–13 m; the
  natural slope resumes at the shoulder (canyon proximity). Later refinement
  (streamed world): explicit near-vertical uphill cut + rock material band.
- **1.4 Per-tire surface grip** ✅ — `UODVehicleMovement::SurfaceGrip()` (the
  manager that originally did this, `AODSurfaceTractionManager`, was folded
  in during the 1.7 drivetrain rewrite). **Simplified to 2-tier, not
  per-material, 2026-07-20** (was asphalt 1.0/dirt 0.72/grass 0.60/unknown
  0.85): road (`PM_Asphalt`, 1.0) vs. off-road (everything else, 0.6) — the
  player is meant to stay on the road (the whole open-world corridor design
  assumes it), so off-road grip only needs to read as "you left the road,"
  not simulate a specific ground texture. Biome ground materials can still
  carry distinct names/textures for visuals/audio (`UODVehicleAudio` still
  branches Dirt/Grass for tire sound) - this only stopped physics from
  branching on them.
- **1.5 Tuning archetypes** ✅ — `UODTuningPreset` data assets
  (Street/Rally/Drift/Drag/Offroad in `/Game/OpenDrive/Tuning`), deltas over a
  captured per-car baseline; `AODTuningManager` cycles on **T** with HUD toast;
  13 mid-drive swaps at full throttle verified stable. Garage UI (4.3)
  consumes these same assets.
- **1.6 Tree collision** ✅ — redesigned after drive test: landscape-grass
  instances are editor-only (zero foliage in standalone `-game`, no queryable
  CPU transforms), so trees moved to `AODForestManager` — deterministic
  tile-seeded C++ scatter (96 m tiles, mask forest weight, per-tile species
  mix from a biome recipe table (2026-07-20, see § 7.3), asphalt-physmat
  rejection), per-tile HISMs, trunk capsule pool from owned transforms.
  Verified standalone: 90k instances, 48 live capsules, full-throttle car
  held by trunks. Also the seed of Arc 7.2 runtime world-gen and the 7.3
  flora-recipe hook.
  !Issue - tires never break traction, skid, understeer, oversteer, etc. we need to redesign the physics to be based on the tire grip, angle of grip and weight distributed to each tire * road traction rather than current system

- **1.7 Drivetrain + tire redesign** *(started 2026-07-16, replaces the Chaos
  mechanical sim — also the transmission modes + lift-off items from 5.1)*
  - **Stage 1 ✅ (drive-test PASSED 2026-07-17: revs better, shifts much
    better, coast-hold/kickdown/reverse work, engines good; no launch
    wheelspin — Chaos grip clamp, stage 2 scope)** — `AODDrivetrainManager`:
    engine as own integrated state (torque curve, flywheel inertia, limiter
    fuel cut, engine braking), shift controller (throttle-scaled upshifts,
    kickdown, coast-hold — no downshift flare on lift), 120 ms torque-cut
    shifts, reverse via brake-hold; Chaos mech sim disabled, torque injected
    per driven wheel (Override combine, re-asserted every tick — physics-side
    state resets on RecreatePhysicsState; brake/handbrake replicated on
    driven wheels). RPM now follows *wheel spin*, not car speed: burnout
    against obstacle holds ~4000 rpm at 3 mph; lift-off revs fall instantly.
    Trace hook: `-odtrace` (10 Hz rpm/gear/coupled/speed). Known stage-1
    limits: wheelspin capped by Chaos `MaxWheelspinRotation` (~60 rad/s);
    airborne wheels excluded from coupling (Chaos freezes them at the cap).
  - **Stage 2 — core executed + machine-verified (2026-07-17), feel
    iteration with Taylor next.** Chaos tire friction fully replaced
    (multipliers zeroed every tick; `AODSurfaceTractionManager` stands down):
    per-wheel slip ratio + slip angle through one friction circle, smooth
    peak (κ 0.14 / α 9°) falling to sliding grip (0.72), per-wheel loads
    computed analytically — static split from contact geometry + smoothed
    dynamic weight transfer (Chaos `SpringForce` under-reports ~60%: it's
    constraint suspension). Wheels integrate their own spin with the
    engine's REFLECTED inertia (I·ratio² ≈ 12x wheel inertia in 1st — the
    fix for driveline bang-bang), soft rev limiter for physics + hard cut
    for audio, slip-guarded upshifts (burnouts hold gear), kickdown
    cooldown vs hunting. Machine-verified: launch spins then hooks to slip
    ~0.1 (grip peak) through 1st; power-limited spin near the limiter;
    steering yaws the car (`-odsteer` hook); grass grip visibly lower;
    lateral weight transfer in the trace; FFT audio regression passes.
    Tunables for the feel pass: MuPeak/SlideFrac/PeakSlipRatio/
    PeakSlipAngleDeg + per-preset Front/RearGripScale (presets route).
  - **Stage 3** — retune the five presets on the new physics; drift becomes
    progressive rear breakaway. Transmission modes (DCT/PDK/manual + shift
    cut feel) land here as preset/garage data.

- **1.8 Chaos rip-out ✅** *(2026-07-18, machine-verified; subsumes the
  template exorcism — Taylor drive-test pending)* — Chaos vehicles are GONE
  (module dep + plugin disabled). Why: UE 5.8's constraint-based suspension
  silently pinned our bone-less static-mesh pawn to the world (suspension
  offset frozen at MaxRaise, 4.8 kN of tire force had zero effect), and
  stages 1–2 had already replaced every other Chaos subsystem.
  `UODVehicleMovement` (one component on `AODVehiclePawn`) now owns the
  whole chain: raycast suspension (spring+damper per corner, ~6 cm static
  sag, loads = real spring forces so weight transfer is no longer estimated),
  steering (raw 38° with mild speed taper), the stage-1 engine/auto-box, the
  stage-2 tire model, quadratic drag + downforce, and COM placement (box-mesh
  COM was at z=48 — auto-nudged to z=10). Deleted: `AODDrivetrainManager`,
  `AODSurfaceTractionManager` (tire model owns surface grip),
  `ODVehicleWheel.h`, all Chaos CDO/combine-method workarounds. Presets and
  pause menu now write component fields directly (no baseline capture, no
  RecreatePhysicsState). M_Road got PM_Asphalt (complex traces read the
  render material's physmat, not body_setup's). Machine-verified headless:
  static sag 5.9 cm at corner weight 367 kN·cm⁻²-units; 45% throttle = grip
  launch (slip 0.04) 0→28 mph; 100% throttle = wheelspin past slip 1.0 into
  genuine tail-out power oversteer and a spin (previously impossible); lift
  at redline = revs 6772→900 in ~1.2 s; half steer at 28 mph = 14° front
  slip angle, 45°/s yaw, outside-wheel load 400k→774k; road reads PM_Asphalt,
  shoulder PM_Dirt. Hooks moved to the pawn (they fought key input):
  `-odrev/-odlift/-odsteer` + new `-odthr=0.45` throttle cap for controlled
  tests (quote it in PowerShell: `"-odthr=0.45"` — PS 5.1 splits bare
  `-x=0.45` at the decimal).

- **1.9 Driver assists + gearbox realism ✅** *(2026-07-18, machine-verified
  via trace — Taylor drive-test pending)* — all on `UODVehicleMovement`,
  pause-menu rows for the two toggles:
  - **Traction control** (High/Low/Off, cycles in menu): High closes a
    torque-authority loop on the worst driven-wheel slip ratio (target 0.12,
    fast cut/slow give-back) so the car simply hooks up; Low leaves engine
    torque alone (slides/burnouts stay available) but brakes a single front
    wheel when actual yaw rate exceeds what the steering input kinematically
    calls for — ESC that catches spins without killing drift; Off is raw.
  - **ABS** (on/off): per-wheel, releases brake torque once longitudinal
    slip passes 13% lock instead of letting the wheel go dead - a real
    pulsing release/reapply, not a binary cap. Off keeps honest lockups.
  - **Attack-mode gearbox:** the box now HOLDS the current gear through
    committed throttle (no early upshift mid-corner or mid-burnout) and only
    returns to normal auto-shifting after ~1 s of settled low throttle - at
    which point it corrects straight to the gear road speed calls for, keyed
    off the SLOWEST wheel (immune to a spinning driven wheel inflating the
    speed estimate). Fixed same-session: attack mode still upshifts AT the
    limiter (redline), so a floored car climbs through the box instead of
    bouncing in 1st forever.
  - **Clutch-bite launch:** starting from a stop, the engine free-revs
    (flare), the bite drags rpm back down to meet the still-slow wheels,
    then it climbs with road speed - the real "catch" cars do, and the
    reason a stalled/bogged uphill launch is now representable (the engine
    no longer starts pre-matched into the powerband).
  - Trace additions: `-odtrace` now logs `tc=`, `esc=`, `abs=`, `relax=`.
  !Issue - keyboard/M&K braking is all-or-nothing (no analog pedal), and
  slamming to 100% brake was putting the car into a slight slip angle
  instead of a straight stop.
  *(fixed 2026-07-19, machine-verified via a scripted full-throttle-then-
  full-brake trace: root cause was ABS acting independently per wheel —
  one front wheel could cut its own brake torque a frame before its
  partner, injecting yaw torque exactly when grip margin was thinnest. ABS
  is now select-low per axle (`UODVehicleMovement::TickTires`): both wheels
  on an axle always share whichever wheel's slip demands the bigger
  reduction, same as real production ABS. Straight-line panic stops from
  130+ mph now hold within ±2°/s yaw noise, no sustained pull.)*

## Arc 2 — Atmosphere ✅ *(2.1 + 2.2 executed 2026-07-15)*

- **2.1 Day/night** ✅ — `AODDayNightController`: 1 h cycle, sun rotation
  (lights must be Movable — editor-spawned default Stationary), per-vehicle
  headlight spots auto-on below 8° sun, exposure widened EV100 −3..16.
  *Remaining polish:* moon/stars night layer; "real-time accurate" sun mode
  arrives with real-world georeferencing (Arc 7).
- **2.2 Rainfall-darkened roads** ✅ — `fetch_climate.py` (NASA POWER, no key)
  → `road_moisture` 0.83 for the Dragon → darker albedo baked into T_Road +
  M_Road roughness 0.9 − 0.35·moisture. Stage scalar kept in `climate.json`
  for future weather systems.
  !Issue - road and much of the landscape is getting whitewashed. need to reduce lighting or add more shadows or something. also the road doesnt look wet or darker at all despite us implementing.
  *(addressed 2026-07-16, awaiting drive test: auto-exposure was metering the
  dark forest and blowing out sunlit surfaces — Grade PPV now has −1 EV bias +
  highlight local-exposure 0.6 (`apply_light_wet_fix.py`); M_Road roughness
  0.61 → 0.39 so wetness reads as sheen. Screenshot-verified: dark asphalt +
  gloss visible in sun.)*
  !Issuetrees are not of varying sizes. trees should be up to 3x current size and down to .5 normal size with a log dist of height (bigger trees are most common as those are the ones that have reached full height)
  *(addressed 2026-07-16, awaiting drive test: growth-curve scale distribution
  0.5x–3x, full-grown most common, in `AODForestManager::BuildTile`;
  screenshot-verified.)*

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

- **4.0 Stopgap pause menu ✅** *(2026-07-16, canvas-drawn — superseded by
  4.0b below)* — `AODPauseMenu` + ODRaceHUD drawing: ESC/P pauses; rows =
  Tuning preset (1.5 assets, synced with T), Gearbox Comfort/Sport (1.7
  shift params), Engine V8/V6/V10/V12 (drivetrain curve + synth cylinders),
  Exhaust Stock/Sport/Open (5.2 params), Resume, Quit. `-odmenu=T` QA hook.

- **4.0b Real front-end + simplified pause menu ✅** *(2026-07-25, canvas-
  drawn, no UMG — same no-plugin philosophy as everything else)* —
  `AODMainMenu` (`ODMainMenu.h/.cpp`, new): the actual "start the game" flow,
  two stages drawn by `ODRaceHUD::DrawMainMenu`. **Car config** — arrows pick
  Car / Engine / Drivetrain / Tuning, applied LIVE to a spinning showroom
  pawn (`AODVehiclePawn::SetShowroom`); Enter on "Drive" advances. **Map
  select** — the full route map with clickable town nodes (`AODRoadManager`'s
  `TownNodes`, built from place-label data), hover highlights the nearest
  town, click/Enter spawns there via `NearestRoadPoint` + `TeleportToSpawn`
  (falls back to the level's `PlayerStart` if no town data/pick). Game stays
  paused for the whole flow; `AODPauseMenu` simplified in step (Taylor's
  call) to ONLY the two driver assists (Traction, ABS) plus Resume/Main
  Menu/Quit — car/engine/drivetrain/tuning moved here since they're a
  "before you drive" choice, not a mid-drive one. `-odmenumap=T`/`-odpick=
  <Town>` QA hooks. **Extended 2026-07-26 — see the loading-screen and
  Garage-row entries under Arc 7 below and § 4.3**, both found necessary by
  Taylor's own live testing the same day this got wired into the rebuilt
  `OpenWorldTest` level (it had been built 2026-07-25 but the level itself
  wasn't rebuilt/re-applied against until the next session, see Arc 7 below
  for that gap).

### 4.1 Control rebinding (#9)
- **v1 ✅** *(2026-09-08, see §7.32)* — implemented as a project-level
  indirection layer over the existing legacy `BindKey`/`BindAxisKey` path
  (NOT a real Enhanced Input migration — decision + reasoning in §7.32),
  since no actual mapping-context data assets existed to migrate from
  despite the unused `EnhancedInput` module dependency. Canvas-drawn
  rebinding screen off Settings; deadzone/sensitivity sliders feeding a
  future Arc 6 wheel; persists to `GameUserSettings.ini`
  (`OpenDrive.Input`), not a `SaveGame`, matching this project's existing
  `OpenDrive.Exhaust`/`OpenDrive.Graphics`/`OpenDrive.Audio` idiom instead
  of introducing a new persistence mechanism.
- Migrate to Enhanced Input (if template hasn't); mapping contexts for
  keyboard/gamepad; rebinding settings screen writing to a user config
  `SaveGame`. Include deadzone/sensitivity sliders (feeds Arc 6 wheel).

### 4.2 Map view (#6)
- **v1 ✅** *(2026-07-18)* — canvas stage map: road polyline north-up, player
  arrow, start/finish dots, next-gate marker, PB ghost dot. Mini-map
  bottom-left (**M** toggles), full map beside the pause menu.
- **v2 ✅** *(2026-07-18, machine-verified)* — fixed a real alignment bug:
  v1 drew a Catmull-Rom guess between the 62 checkpoint gates (~300 m
  apart), which visibly cut corners the real road doesn't take. The true
  centerline was already sitting in `road_mesh.json` at 5 m station density
  (same math that places the gates, just not subsampled) -
  `pipeline/export_centerline_bin.py` exports all 3635 stations as a
  `centerline.bin` sidecar (same load pattern as the forest mask, works in
  packaged builds); `AODRaceManager` loads it, `ODRaceHUD` draws it verbatim
  and falls back to the old spline guess only if the sidecar is missing.
  Also new: mini-map is a round, player-centered, **zoomable** panel
  (**[ / ]** keys, default 90 m radius) with a smoothed circular clip and a
  gold bezel — corners stay transparent so the 3D scene shows through
  instead of a black square.
- **Stylized terrain-color background ✅** *(2026-07-26, open-world/
  `AODRoadManager`-backed maps only — Tail of the Dragon's map is unaffected)*
  — see Arc 7's map-texture entry below for the full pipeline/runtime
  account; NA-scale map still arrives with Arc 7.4 (tile-based).
- We need to have a mini map, world map, and navigation between locations on the map/mini map
  *(mini map ✅ v2 + terrain-color background; world map + A* navigation ✅ built 2026-09-25,
  open-world levels, needs Taylor's live drive-test — see §7.37; NA-scale tiled map still Arc 7.4)*

### 4.3 Garage (#7 cars + cosmetics, consumes 1.5 presets)
- **Live car/engine/drivetrain/tuning picker ✅** *(2026-07-25 via
  `AODMainMenu`'s car-config screen; reachable mid-drive via the pause
  menu's new "Garage" row as of 2026-07-26 — see § 4.0b and Arc 7 below)* —
  arrows cycle Car / Engine (4 presets) / Drivetrain (RWD/FWD/AWD) / Tuning
  (1.5 presets), each applied LIVE (`ApplyEngine`/`ApplyDrivetrain`/
  `ApplyTuning`/`ChangeCar` on `AODMainMenu`) so the choice is real, not
  just a UI value. This is the "and it actually does something" bar Taylor
  asked the mid-drive garage access to clear, already satisfied by reusing
  code that predates this session.
- **Still open:** asset pipeline for MULTIPLE real car meshes (currently one
  hardcoded coupe unless `UODVehicleDef` assets exist — the picker/cycling
  code is ready, just has nothing to cycle through yet) — CC0 low-poly pack
  download + license record + headless import, per-car data asset (mesh,
  wheel offsets, base tuning), some hand-tuned Chaos-era wheel mapping work
  per vehicle (accept it). Cosmetics v1 (paint color/wrap) not started.
- Acceptance: 4–6 drivable cars, color choice, preset choice, all persisted.

### 4.4 Release settings shell ✅ *(2026-09-08, see §7.31 — canvas-drawn, no UMG)*
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
  !Issue - on liftoff, car engine tone increses for a moment before decreasing which negatively affects immersion
  *(addressed 2026-07-16, awaiting drive test: RPM-trace instrumentation
  (`-odlift` hook) showed the synth tracks RPM cleanly — the flare was the
  auto box downshifting at 2000 rpm during coast-down and rev-matching UP
  ~1.4x; ChangeDownRPM → 1150 (`apply_transmission_fix.py`).)*
  !Issue - engine noise needs more lower level tones/harmonics - the car sounds like a crappy V8 version of a VQ engine right now
  *(addressed 2026-07-16, awaiting drive test: crank-order stack (0.5/1/1.5/
  2/3× crank) below the firing order, load-swelled; firing-harmonic decay
  tightened. FFT-verified pitch tracking still passes.)*
- **5.1v2 Pulse-train synth ✅** *(2026-07-18, machine-verified — ear pass
  pending)* — the harmonic-stack "note" replaced by real pulse-train
  synthesis: each cylinder firing (once per 720°) triggers a damped
  exhaust-pulse resonator (complex one-pole pool). New physical-ish params
  Taylor asked for: **firing table** (per-cylinder pulse-strength patterns —
  cross-plane V8 bank unevenness = the burble, V12 near-perfectly even =
  the silk), **DisplacementL** (cylinder liters set pulse resonance + ring
  time: V8 6.2L thumps low/long, V6 3.5L barks high/short), **TimingJitter**
  (combustion variability, strongest at low load — the idle lope). Engine
  menu defs now carry displacement; crank-order shake/rasp/muffler chain
  unchanged. FFT regression passes: WOT dominant tracks firing frequency
  (~445 Hz at redline); idle reads deep (30 Hz 2nd-order dominant).
  !Issue automatic transmission is incresdibly slow to shift the engine sounds. Either we need to add transmission upgrades or have options for dct, pdk, manual, upgraded auto. I on some real cars there is a brief cut in the engine while shifting sometimes which is incredibly satisfying, much more than the current auto
  *(partial 2026-07-16: synth RPM slew 140 ms → 50 ms so shifts sound crisp
  instead of glided. Transmission modes (DCT/PDK/manual/upgraded auto) are
  the remaining work; shift-cut character ✅ below.)*
  !Issue - rev limiter sounds "way too light and fluffy/flowy... should be super aggressive like the brap brap sound cars make, not a whoosh"
  *(addressed 2026-07-18, machine-verified via envelope-modulation FFT
  (`pipeline/analyze_brap.py`) then tuned live to Taylor's ear: the physics
  limiter was always a soft torque fade (needed for driveline stability), so
  it never produced an audible hard edge. The synth now gets its own raw,
  unsmoothed `SetIgnitionCut()` - firings are skipped OUTRIGHT during a cut
  (silence, not a fade) at a tunable bounce rate (`LimiterBounceHz`, tuned
  down from 12→8 Hz per Taylor's "more time between braps"), with an
  audible rpm droop during each cut (`CutRpmSag`) and a 1.9x amplitude +
  noise-transient bang on every relight. Shift cuts get the same crack for
  free.)*

### 5.2 Exhaust customization (#13) — ✅ built, see §7.30 (2026-09-08)
- `Loudness` / `Rasp` / `MufflerCutoff` already exposed on the synth; add
  pop/burble probability on lift-off; per-car "exhaust" struct; garage sliders
  (4.3); persisted. Shipped as `FODExhaustConfig` on `UODEngineSynth`
  (pop/burble reuses the 5.1v2 ignition-cut relight machinery) +
  `AODMainMenu` Stock/Sport/Open presets with 4 garage sliders + GConfig
  persistence — full design, params, and what's Taylor's to judge live are
  in §7.30. Preset-level not per-car-def (UODVehicleDef carries no audio
  fields — noted, not expanded).

### 5.3 Simulated engine sample banks (#12c, later upgrade)
- Offline: open-source physical engine simulator (Engine Simulator /
  angethegreat) renders a sample grid (RPM × load) per engine configuration in
  the pipeline; runtime: granular/crossfade playback behind the same
  interface. Exhaust edits = re-render bank with different muffler geometry.
- The "accurately simulate" tier; the 5.1 synth stays as fallback.

### 5.4 Ambient biome/city audio — ✅ built, see §7.28 (2026-09-08)
- Wind/rustling in trees for forested biomes, water sound along
  creeks/rivers/lake shores, town hum in settlement tiles, base wind bed
  elsewhere. Shipped as `AODAmbientSoundManager` + `pipeline/
  build_ambient_zones.py` — full architecture, assets/licenses, objective
  checks, and what's left for Taylor to judge live are in §7.28.


**Size:** 5.2 = small (garage-gated); 5.3 = separate multi-session project;
5.4 = unscoped backlog, no work started.

## Arc 6 — Wheel / sim rig support  *(wishlist #10)*

- Enable **RawInput** plugin; device mappings for common wheels (Logitech,
  Fanatec ids); wheel axis → steering with linearity setting, pedals →
  throttle/brake/clutch axes; H-shifter buttons.
- Per-device profiles saved via 4.1's input settings.
- **Force feedback: explicitly deferred.** Native UE FFB support is weak;
  revisit via dedicated plugin/SDK after release v1. v1 = wheel input works.
- Acceptance: playable start-to-finish with a wheel, axes calibrated in UI.

**Size:** 1–2 sessions (+unknown for FFB later). **Depends on:** 4.1.

## Arc 7 — Mountain hot zones  *(wishlist #5 — the campaign)*

**Scope narrowed 2026-07-27 (Taylor) — this supersedes the original "drive
anywhere in North America" framing.** Goal: drive the *mountain* areas of
North America as a set of discrete, self-contained zones, each with its own
local coordinate frame; travel between zones is fast-travel, not driving.
Everything above survives intact.

**In scope:** Cascades, Olympics, Sierra Nevada, CA/OR/WA coast ranges, the US
Rockies (CO/WY/MT/ID/UT incl. Wasatch, Sawtooth, Bitterroots, San Juans), the
Canadian Rockies and BC Coast Mountains, the Appalachians/Blue Ridge/Smokies
and the northeastern ranges (Adirondacks, Greens, Whites), the
Ozarks/Ouachitas, the Black Hills, **all of Hawaii**, and **all of Alaska**.
No priority order set. Oahu stays as the debug testbed — it exercises
coastline, dense urban grid, and dramatic relief in one bake.

**Cut:** the Great Plains, the Midwest, the Gulf coast, and most of the desert
Southwest.

**Why this matters more than it sounds:** the full-continent framing implied
~950 GB of baked content and made Arc 7 structurally impossible on the current
delivery architecture. Hot zones bring that to ~25 GB today and ~1.7 GB after
the storage fixes — the largest single limiting factor in the project was
dissolved by a scope decision rather than by engineering. Full analysis and
measured numbers in [`AUDIT.md`](AUDIT.md) § 1.1.

**Coordinate frames: per-location, decided 2026-07-27.** Each zone keeps its
own `origin_latlon` (what the pipeline already does). No shared continental
space. This keeps float32 vertex precision at 0.5–4 cm per zone, bounds the
equirectangular projection error, and means there are never any inter-zone
seams to reconcile. Cost: no continuous driving between ranges. See
`AUDIT.md` § 1.3.

**Consequence to action before the second zone exists:** `pipeline/test_areas.py`
holds one area as module-level constants (`AREA_NAME`, `BBOX_SNWE`,
`ORIGIN_HEIGHT_M`), swapped by editing the file. Multiple zones means that
becomes a table keyed by zone name, with every pipeline script taking the
zone as an argument. Small refactor, touches every script, far cheaper now
than at the fifth zone.

**High-latitude costs, new with Alaska and not yet exercised by any test area:**
Mercator tile counts scale as `1/cos²(lat)` — 4.28× at 63°N, 6.29× at the
Brooks Range, versus Hawaii — which hits DEM fetch volume and bake time (not
disk). And the single-`lat0` equirectangular frame reaches 6.9% edge scale
error across a ±2° Alaskan bbox versus 0.17% at Oahu; mitigation is simply to
keep Alaskan zones latitudinally short. `AUDIT.md` § 1.2.

### 7.0 Pivot (2026-07-23): baked/tiled terrain replaces the live-streaming plugin

**Decision (Taylor, 2026-07-23):** the third-party live-streamed-terrain
plugin is not viable for a 200 mph racing sim — the whole Arc 7.2 saga
(§ 7.2 below) was three independently-refining async systems (the plugin's
own LOD, our road tiles, vehicle spawn) racing to agree at runtime with no
reliable "settled" signal. The fix that actually worked there (carve-once-
offline before the engine ever loads it) is the model for everything now,
not just roads: **terrain must be generated exactly like Tail of the
Dragon** — one static, deterministic, offline-baked dataset per region,
verified before the engine sees it. The old live-streaming plugin (the
plugin itself, its HTTP tile server, `AODRoadManager`'s async ground-snap/
wait-for-streamed-terrain machinery) is being replaced, not patched
further. § 7.1/7.2 below are kept as the historical record of *why* — the
mechanism they proved (distance-gated detail) carries forward, just
delivered through UE5's native Landscape World Partition streaming proxies
(baked once, offline) instead of a live quantized-mesh server.

**Standing constraints driving the design:**
- Players only ever need to be within 5–10 m of the road centerline — full
  detail (terrain + collision) is only required in that corridor.
- Distant terrain still needs to be *visible* (mountain backdrops, horizon)
  but not driveable — coarse/smoothed/no-collision is fine there, same
  z15-gating trick already proven in `build_terrain_tiles.py`, generalized
  from quantized-mesh zoom levels to Landscape streaming-proxy tiles.
- Zero-per-instance-storage rule (standing decision 2) still applies:
  buildings must be procedural-from-footprint, not hand-placed, same as the
  tree scatter. Landmarks (~1000, curated exception) are the one place
  per-instance data is acceptable, since the count is small and bounded.

**Terrain archetypes (14, feel-based per standing decision 1, not strict
botany) — supersedes/extends the 7-entry `EODBiome` table below (§ 7.3):**
Dense Deciduous Forest, Evergreen/Boreal Forest, Sparse Scrub/Chaparral,
Desert Scrub, Plains Grassland, Wetland/Swamp/Bayou, Alpine Bare Rock,
Arctic Tundra, Farmland/Agricultural Patchwork, Savanna, Tropical
Rainforest/Jungle, Coastal Dune/Beach, Canyon/Badlands, Rocky
Foothill/Highland transition.

**Populated archetypes (6, new system — no code yet):** European Village,
American Town, City (real street grid + procedural extruded-footprint
buildings — driving the actual real street layout, not a generic skyline),
Rural Hamlet/Farmstead cluster, Industrial/Port district, Suburban Sprawl.

**Landmark layer (new system — no code yet):** ~1000 real named structures
(Empire State Building, The Bean, etc.), sourced algorithmically from OSM
tags (`tourism=attraction`, `historic=*`, named `building=*`) filtered by a
prominence heuristic, along actual driving routes. Each is an authored/
kitbashed asset placed once at its real lat/lon — the deliberate exception
to the zero-per-instance rule, since the count is small and bounded. Loaded
only near the route.

**Pipeline plan, in order:**
1. **Terrain archetype classification, extended to all 14** — grow the
   existing `EODBiome`/`BiomeRecipeTable`/`classify_biomes.py` system (7
   entries today) to the full list above. Lowest-risk step: pure data/
   classification work, no engine/Landscape changes, extends something
   already proven end-to-end.
2. **Baked Landscape + World Partition proof** — small test area, no
   live-streaming plugin: import a heightmap-per-tile Landscape (World
   Partition streaming proxies) with route-distance-gated resolution (full
   detail + collision near the road, coarse/no-collision background
   elsewhere) — this is the plugin-free re-proof of what § 7.1's slice 1
   proved for quantized-mesh tiles.
3. **Building footprint extrusion** — OSM building footprints inside
   populated tiles → per-archetype height range + tiled facade material,
   deterministic/seeded, same pattern as the tree HISM scatter.
4. **Landmark sourcing + placement pipeline** — OSM query + prominence
   filter + curated asset placement.
5. **Full integration + verification gates** — offline fit/overlap checks
   (extending `verify_road_fit.py`'s pattern) before any of this loads in
   the engine; scale out past the current single test area.

**Status (2026-07-23): step 1 done** (14-archetype terrain classification,
build verified). **Step 2 pipeline/runtime code complete, offline gate
passing well, BLOCKED on a live drive-test only Taylor can run** —
`pipeline/build_terrain_mesh_tiles.py` (bakes `ODT1` world-space tiles,
distance-gated near/far exactly like the old plugin's z15 gate, carved to the
road profile in the local meter frame directly), `pipeline/verify_terrain_fit.py`
(offline fit gate, mirrors `verify_road_fit.py`), `AODTerrainManager`
(`ODTerrainManager.h/.cpp`, mirrors `AODRoadManager`'s tile-streaming
pattern), the from-scratch terrain-test-level build script (since retired)
updated to spawn `AODTerrainManager` instead of the old georeference/
3D-tileset actor pair. Full 833-tile bake done for the Skagit test area
(347k verts/644k tris, 165 near/collision + 668 far/background tiles).

Two real bugs found and fixed by `verify_terrain_fit.py` this session (the
gate doing its job, not a sign the approach is wrong):
1. The carve blend radius was inherited from the old streaming pipeline's
   CARVE_INNER_M/OUTER_M (10/40 m, tuned for a ~4 m/px raster) - too small
   for this new grid's ~50 m sample spacing, so barycentric interpolation
   near a station could blend in raw hillside elevation in the Cascades
   foothills' steeper terrain (10-14 m spikes). Fixed with grid-sized
   `TERRAIN_CARVE_INNER_M`/`TERRAIN_CARVE_OUTER_M` (50/150 m) in
   `build_terrain_mesh_tiles.py`.
2. One tile still failed after that: two real, DIFFERENT road segments
   passing ~90 m apart with a 16 m elevation gap and no OSM bridge/tunnel
   tag - same failure class as the original Skagit bridge-crossing bug,
   just untagged this time. Rather than patch that one spot, added
   `detect_elevation_conflicts()` - geometric (not tag-based) detection of
   any different-segment station pair too close in XY with too large an
   elevation gap to be explained by a plausible shared grade, auto-excluded
   from the carve the same way `is_bridge` stations are. Found 354 such
   stations project-wide, a strict superset of what tagging alone would
   catch.

**Offline gate result after both fixes:** median deviation 0.018 m, 99th
percentile 0.77 m across 64,488 checked stations - a small residual tail
(~0.04% of stations with terrain up to ~2.4 m above raw ground, ~1% with a
tighter-than-ideal skirt-hiding margin) remains at the same two known
crossing locations; Taylor's call (2026-07-23) was that further offline
percentile-chasing has hit diminishing returns and a live look is more
informative than more tuning, matching this project's own history with
`verify_road_fit.py` (that gate also only reached full confidence via a
live drive, not endless offline iteration).

**Live rebuild + mechanical verification DONE (2026-07-23, ahead of Taylor's
own drive) - the terrain-streaming test level rebuilt from scratch** via
the from-scratch terrain-test-level build script (since retired; old `.umap`
deleted first, editor closed, per that script's own docstring). One real bug
found and fixed during the rebuild, two more tiles machine-verified correct via
`-odtrace`+`-odshot` (ground-contact log + screenshot, not just a visual
guess), then the level's spawn point restored to its exact rebuilt value
(`8762, 50503, 1514`, yaw `168.4`) so it's untouched for Taylor:

1. **Real bug: the level build script's `Sun` actor never set
   `Mobility = Movable`.** This exact bug was fixed once before (Arc 2.1,
   via a one-off `apply_flora_light_fixes.py` patch on the OLD level) but
   the fix was never folded back into the from-scratch BUILD script, so
   every full rebuild silently reintroduced it - PIE log spammed "Mobility
   ... has to be 'Movable'" every frame, meaning `AODDayNightController`'s
   sun rotation was being rejected the whole session. Fixed at the source
   now (`sun_comp.set_editor_property("mobility", unreal.ComponentMobility.MOVABLE)`
   right after spawn) instead of depending on a separate patch script to
   catch it after the fact.
2. **A methodology trap worth remembering for future headless spawn tests:**
   moving `PlayerStart` to an arbitrary tile CENTER (not an actual road
   point) triggers `AODVehiclePawn`'s intentional kinematic-hold-until-
   `PM_Asphalt`-ground-trace safety logic (the Arc 7.2 slice-2 spawn-
   depenetration fix) - the car never releases physics, stays frozen
   exactly at spawn height, and the resulting screenshot (a flat, textureless
   blue expanse) looked exactly like a rendering bug but was actually just
   a static aerial view over a real lake ("Sunday Lake" - the HUD label was
   accurate the whole time). Cost real debugging time before the `-odtrace`
   ground-contact log (`gnd=0`, never `bound to` in the log) revealed
   physics never engaged at all. Fix: spawn-test ACTUAL road stations
   inside a tile, not tile centroids.
3. **Both known tricky tiles re-tested correctly** (real road stations
   inside each, via `build_terrain_mesh_tiles.build_tile`/station lookup):
   tile `(-11,1)` (near Sunday Lake) and tile `(-15,-15)` (near Belfast) -
   both landed clean (`gnd=4 surf=PM_Asphalt`, settled to ~0 speed),
   screenshots show correct pavement, no poke-through/gaps, trees properly
   grounded on the shoreline/roadside, road-name/city label accurate in
   both ("I 5 · Sunday Lake", "I 5 · Belfast").
4. **The "floating trees" Taylor spotted in the earlier (pre-rebuild) label
   screenshots is confirmed resolved** - that was the OLD level's dead
   3D-tileset actor never rendering any ground at all; trees on the freshly
   rebuilt terrain sit correctly on visible ground in both tricky-tile
   screenshots above.
5. **Not independently re-checked:** triangle winding from a genuinely
   top-down angle (both confirmatory screenshots above are normal chase-cam
   angles, which do prove the mesh isn't fully backface-culled, but not a
   dedicated straight-down view).

**What's left is genuinely Taylor's alone:** actual driving-feel judgment
(does 50 m near-tile spacing read as fine at speed, general vibe) - a
screenshot proves correctness, not feel. Steps 3–5 not started.

## Road name + city/town labels above the mini-map (2026-07-23)

Taylor asked whether the map showed road/county/city names - it didn't
(mini-map only ever drew geometry, and no OSM name data was fetched at
all). Added end to end, scoped (Taylor's call) to road name + nearest
city/town only (no county - needs OSM admin-boundary polygon parsing, a
separate bigger task) and to the open-world/streamed-terrain test-area pipeline only
(Tail of the Dragon's separate single-road pipeline is out of scope).

- `pipeline/build_road_stations.py`: `merge_way_chains()` now also tracks
  each edge's display name (`way_name()` - OSM `name` tag, falling back to
  `ref` so an unnamed state route still shows e.g. "WA-20"), threaded
  through chain-walking the same way `is_structure` already was. Resampled
  stations get their NEAREST original station's name (categorical, not
  interpolated) - new `road_name` array in `road_stations.json`. Verified
  against the real Skagit data: real name transitions along a merged chain
  (e.g. seg000 crosses "Oso Slide Memorial Highway" → "State Route 9" →
  "State Route 530" as the actual named road changes).
- `pipeline/fetch_osm_places.py` (new): Overpass query for named
  city/town/village/hamlet nodes, bbox + 0.15° margin so border-adjacent
  stations can still find a "nearest" town. 169 places fetched for Skagit.
- `pipeline/build_place_labels.py` (new): downsamples stations (same
  `SAMPLE_STRIDE` convention as `classify_biomes.py`, ~50 m spacing), pairs
  each with its own `road_name` plus the nearest place node's name
  (projected into the same local east/north frame). Spot-checked correct:
  real towns (Rockport, Mount Vernon, Marysville) show up with the right
  road names nearby.
- `pipeline/export_place_labels_bin.py` (new): new `ODL1` binary sidecar
  format - variable-length UTF-8 strings (uint16-length-prefixed), a first
  for this project's binary sidecars (every prior one was fixed-size
  records). Round-trip verified byte-for-byte against the C++ parser's
  exact read sequence before trusting the runtime side.
- `AODRoadManager` (extended): new `PlaceLabelFilePrefix` (empty = off,
  same convention as `CenterlineFilePrefix`), loads the sidecar at
  `BeginPlay` into `TArray<FPlaceLabel>`, throttled (~2 Hz) brute-force
  nearest-point scan in `Tick()` caches `CurrentRoadLabel`/`CurrentCityLabel`
  for the HUD to read directly.
- `ODRaceHUD.cpp`: new `DrawPlaceLabel` lambda draws
  `"<road> · <city>"` (or just whichever half is non-empty) directly above
  both the mini-map and the full pause-screen map.
- the from-scratch terrain-test-level build script (since retired) sets the new prefix for from-scratch
  builds; `unreal/apply_place_labels.py` (new, same idempotent-by-label
  pattern as `apply_road_centerline.py`) sets it on the already-built level
  without a full rebuild - **run headlessly and confirmed successful**
  (RoadManager found, property set, level saved, 0 errors).

**Live verification DONE (2026-07-23), ahead of the terrain rebuild** - two
`-game`/`-odshot` tests, each by temporarily moving `Start_TestArea`'s
PlayerStart to a known point (via a scratch diagnostic script, not a
permanent one) and screenshotting, then restoring the exact original
transform (`8762, 50503, -486`, yaw `168.4`) afterward so the level was left
untouched:
1. Rockport-area point → HUD showed **"State Route 530 · Rockport"**, exact
   match.
2. Marysville-area point → HUD showed **"I 5 · Marysville"**, exact match;
   this point also happened to land the car on real, correctly-rendered
   road pavement, confirming `AODRoadManager`'s road mesh streams
   independently of the terrain-streaming state.

Both label + mini-map render correctly. **What these screenshots also
showed, worth flagging so it isn't mistaken for a new bug during the real
drive-test:** trees appeared to be floating in empty space with no ground
visible under them. Root cause understood, not a forest-system bug - the
level's old 3D-tileset actor is still pointed at a dead local tile server
(`ConnectionError` in the log), so no terrain renders at all yet on this
`.umap` (`AODTerrainManager` isn't spawned here until the pending rebuild
happens). `AODForestManager` places trees from its own independent
ground-truth elevation grid (`elev_<AREA>_test`), not a streamed-terrain raycast, so
tree HEIGHTS are correct - there's just nothing else visible in the scene
for them to visually sit on right now. Expected to resolve on its own once
the level is rebuilt with real terrain; not something to chase separately.

## Arc 7.0 step 3 — procedural buildings on populated tiles (2026-07-24)

Mount Vernon proof, end to end: `pipeline/fetch_osm_buildings.py` (6519
real footprints, town-bbox-gated), `classify_settlements.py` (8 populated
tiles, density-gated, American Town archetype), `build_building_mesh.py`
(6517 buildings extruded - 2 excluded for sitting too close to the road,
verified inline as the road-corridor gate the plan called for),
`export_building_mesh_bin.py` (new `ODU1` sidecar format), new
`AODBuildingManager` (mirrors `AODRoadManager`/`AODTerrainManager`'s
tile-streaming pattern exactly, one `UDynamicMeshComponent` per building
per the plan), new `M_BuildingFacade` material
(`unreal/apply_building_material.py`). Headless build clean throughout.
Extrusion math verified correct offline (N-sided footprint → 5N+1 verts,
3N tris, checked against real output). `AODBuildingManager` confirmed
loading all 8 tiles/6517 buildings live and keeping them resident
(steady-state diagnostic log across three separate live sessions).

**Real bug found and fixed during live verification:** buildings were
completely invisible on first live test despite loading correctly and
sitting well above real terrain height (verified: nearest building's base
was 1.95 m above the actual terrain mesh at that exact point, not buried).
Root cause: Mount Vernon's biome sample reads `forest_weight=1.0`
(max-density Evergreen) - `classify_biomes.py`'s imagery heuristic has no
concept of "this is a town," so `AODForestManager` was scattering
full-density forest straight through downtown, burying every building.
Added the same kind of exclusion the road already had: new
`BuildingCentroidFilePrefix`/`IsNearBuilding()` on `AODForestManager`
(reuses the `ODC1` point-list format, `pipeline/export_building_centroids_bin.py`),
18 m radius per building centroid. Confirmed loading (6517 centroids) and
wired into the same `GroundAt()` gate the road centerline exclusion uses.

**Follow-up RESOLVED same session:** added `TownForestDampen()` to
`AODForestManager` - a SOFT, wider (250 m, `TownDampenRadiusCm`) forest-
weight multiplier (down to `TownDampenMinFactor` = 0.08 at/inside the hard
18 m exclusion, smoothstep back to full density by 250 m), applied once
per 96 m tile in `BlendAt()` (cheap even at this radius, unlike a per-
scatter-candidate check), reusing the already-loaded `BuildingCentroids`
data - no new pipeline/sidecar needed. Re-verified live at the same ground-
level test spot that previously showed solid, unbroken canopy: forest is
now visibly sparse (a handful of trees with real gaps, not a wall), and a
distinct light grey/white blocky shape - consistent with a building, not
any tree/bush mesh in this project's set - is now visible through the
remaining trees where nothing but canopy was visible before the fix.

**Building collision CONFIRMED same session** via a scripted drive, not a
static spawn test (which can't validate this - see above for why). Spawned
on real pavement ~37 m from a known building, aimed directly at it
(bearing computed from the two points), `-odrev=1 -odthr=0.3 -odtrace`
(scripted throttle needs BOTH flags - `-odthr` alone silently does nothing,
`bThrottleKey` only flips true at `-odrev`'s timestamp). Trace showed a
clean, unambiguous impact signature: steady ~71-76 units/s cruise for
several seconds, then corner load spiking to `fz=1220000` (~3.3x the
~372000 steady-state value), suspension fully bottomed (`susp=0.0`),
ground contact dropped from 4 wheels to 1, and forward speed collapsed to
near-zero for several seconds despite sustained throttle and climbing RPM
(wheelspin against something immovable) - exactly the distance/bearing to
the target building, not a road feature. **Rough edge worth flagging, not
a blocker:** the car was eventually launched into a wild, physically
implausible tumble after the stuck period (speed readings jumping into the
thousands) - similar in character to the Arc 7.2 spawn-depenetration bug
class, just triggered by a building impact instead of a bad spawn. Collision
itself is proven real; the crash RESPONSE could use tuning later (a clean
stop/crumple would read better than a launch).

**Follow-up also resolved same session (not left for later):** the "tree
wall over town" issue above is fixed via `TownForestDampen()` - see that
section for detail. Re-verified live at the same ground-level spot: forest
visibly thinned (real gaps, not solid canopy) and a distinct building-like
shape is now visible through the remaining trees where none was before.

**Two bugs fixed along the way, worth remembering for future tile-
streamed content:**
1. A background-wait test-automation pattern (grep a log for a marker,
   then `taskkill`) proved unreliable this session for reasons never fully
   root-caused (repeatedly killed the game process seconds into boot,
   before the marker could legitimately appear) - switched to manual
   polling (repeated short `grep`/`tasklist` checks between tool calls)
   instead, which worked reliably every time after. Worth using that
   pattern first for any future live `-game` test rather than the
   background-wait one.
2. Camera **yaw matters** when spawn-testing via a moved `PlayerStart` -
   an arbitrary fixed yaw can easily point away from the thing being
   tested even when position is correct. Compute a bearing toward the
   actual target before trusting a "nothing here" result.

## Guaranteed road-corridor barrier ("wall of scenery") (2026-07-24)

Live-tested same day: a hard cornering slide let the car reach open space
and fall through within a few meters (confirmed by trace: `gnd=0
surf=none` sustained, wild yaw/speed tumble) - `AODForestManager`'s organic
tree scatter is only probabilistic (tile-level biome density), so it can
leave real, drivable gaps; there was nothing GUARANTEED to stop the player
leaving the road at all. Taylor's design intent (reconfirmed): within a
few meters, a wall of scenery should stop you, not open space.

**v1 (discrete capsules) shipped, then found broken by Taylor's own live
test the same day - real bug, not user error.** First version placed a
tree/bush point every 2.5 m on both sides (`BARRIER_OFFSET_M=13`, just
outside `AODForestManager`'s 11.5 m shoulder clearance) with one dedicated
`UCapsuleComponent` per point for collision. My own test (a single
perpendicular slide) happened to hit a capsule and looked like a clean
fix. Taylor then drove off-road "very slowly" and fell through anyway.
Root cause once checked properly: **discrete point obstacles don't form a
continuous wall** - two capsules 2.5 m apart (26 cm radius each) leave a
real ~2 m gap between them, close to or wider than the car itself, so a
slightly different departure point/angle just finds the gap, crosses the
60 m near-tier collision boundary, and falls into the far-tier (no-
collision) terrain beyond - exactly Taylor's report, and exactly what my
own single lucky test failed to catch.

**Fix: replaced discrete capsules with a genuinely continuous collision
mesh.** `pipeline/build_road_barrier.py` now also emits a thin vertical
ribbon mesh (`WALL_HEIGHT_M=4`) following the same offset line, chunked
like road/building meshes and bucketed into tiles (`export_road_barrier_bin.py`,
new `ODW2` format - bumped from `ODW1` since the payload shape changed).
`AODBarrierManager` builds these as `UDynamicMeshComponent`s
(`EnableComplexAsSimpleCollision`, invisible - same "thin sheet, real
collision" pattern the road's own skirt cross-section already relies on
successfully) - a real continuous surface, not points, so there is no gap
size to get lucky or unlucky with. The tree/bush points from v1 are kept
for the visual "wall of scenery" look (HISM instances, no collision of
their own now) - only the collision mechanism changed. 501,806 verts /
495,430 tris of wall mesh across 3,188 chunks for the Skagit route.

**Re-verified live after the fix, not just re-built:** same perpendicular
slow-drive test as before. Speed climbed smoothly, then a sudden
deceleration event (corner load dropping, wheels losing contact one at a
time, traction control maxing out fighting wheelspin against something
solid) - and **`surf` stayed `PM_Asphalt` the entire time, never dropping
to `none`** (confirmed the loaded tile actually had 20 real wall chunks,
not zero - the stop wasn't a coincidence from some other geometry).

**Worth remembering for any future "guarantee no gap" requirement:**
discrete repeated objects (points, capsules, instances) are fundamentally
the wrong tool no matter how tight the spacing gets, because there's
always some spacing where a real gap exists - a continuous mesh is the
only way to actually guarantee it. This cost one round of "looked fixed to
me, wasn't" before being caught by Taylor's own test, not mine - a good
reminder that one successful test path isn't the same as no gap existing
anywhere along hundreds of kilometers of wall (both sides of a 347 km
route, minus bridge/town gaps).

### 7.1 Live-streaming-plugin foundation ✅ *(slice 1, 2026-07-19, machine-verified)*

Proved self-hosted streamed terrain works end-to-end — georeferenced,
dynamically streams as you drive, real vehicle physics on it — in total
isolation from Tail of the Dragon (a separate terrain-streaming test
level; nothing existing was touched). **Decision made:** self-hosted terrain
tiles from day one (Taylor, 2026-07-19), not a hosted tile service — matches the
free-public-release licensing goal.

- **Plugin:** a third-party UE terrain-streaming plugin, the first release
  with official UE 5.8 support (2026-07-01, landed just in time). Installed
  from the vendor's GitHub release zip into `OpenDriveUE/Plugins/`
  (gitignored, binary — re-fetch the matching UE 5.8 release asset per
  machine, no Epic Marketplace/Fab needed).
- **Terrain pipeline:** `pipeline/build_terrain_tiles.py` encodes a
  self-hosted quantized-mesh tile pyramid via `pymartini` +
  `quantized-mesh-encoder` (pure Python, no Docker/C++ build) from the
  *same* Terrarium/AWS DEM source `fetch_data.py` already uses for the
  Dragon (USGS 3DEP-derived, already credited in README) — shared fetch
  logic lives in `pipeline/terrain_common.py`. Test area is a one-line
  `AREA_NAME`/bbox config, not a rewrite — switching areas is cheap.
  Tiles served locally for now (`python -m http.server`); real hosting is a
  later concern.
- **Level:** the from-scratch terrain-test-level build script (since
  retired) built the test level headlessly — a georeference actor +
  a 3D-tileset actor (FromUrl, explicitly linked to the georeference) +
  `PlayerStart`, deliberately separate from `TailOfTheDragon.umap`
  (`AODForestManager::LoadMask()` grabs the first `ALandscapeProxy` in the
  world unconditionally — a second level sidesteps that collision entirely
  rather than fixing it now).
- **Proven, in order:** static terrain renders with real relief and vehicle
  collision (`create_physics_meshes` defaults `true`, no extra setup) →
  dynamic tiles stream in as the car drives, not just once at boot (watched
  new z10-z15 tiles load in bursts on the tile-server log while a scripted
  drive ran) → sustained highway-speed driving works cleanly on flat ground
  (73 mph peak, `yaw≈0`, `slip≈0.06` on the current Palmdale test area, see
  below) → driving off the edge of a bounded test area produces a clean
  freefall (`gnd=0 surf=none`), not a crash — expected since only a finite
  patch is built, not a bug.
- **Test area: Palmdale, CA / Antelope Valley** (moved from an initial Green
  Valley, CA proof spot — its 338 m/km mountain relief made scripted
  physics tests unreliable, cars kept getting stuck on raw slopes).
  Taylor asked for CA-14 Palmdale→I-5 specifically for flat/straight testing
  terrain; a DEM sanity check showed that corridor **isn't** uniformly flat —
  Palmdale/Antelope Valley itself is genuinely flat (~11 m relief in a z15
  tile), but CA-14 continuing south from Palmdale toward I-5 quickly enters
  the Soledad Canyon/Agua Dulce mountains (300-500+ m relief) — canyon, not
  flat. Scoped to the flat Antelope Valley side only.
- **Three real bugs hit and fixed, worth remembering:**
  1. `pymartini.decode_ele` does `red*256` internally, which overflows a
     uint8 array under numpy>=2's stricter scalar-promotion (NEP 50) — pass
     the Terrarium PNG as `int32`, not `uint8`.
  2. A 3D-tileset actor not explicitly linked to a georeference actor via
     `set_editor_property("georeference", geo)` silently resolves its own
     default one (origin at lon/lat 0,0, "Null Island") instead — always
     set this explicitly.
  3. **layer.json's `"scheme"` field only has ONE valid value, `"tms"`** (y
     numbered from the south) — not `"xyz"`, and NOT `"slippyMap"` either
     despite that appearing to be a valid option in an AI-summarized web
     search result (it isn't — confirmed by reading the raw
     `SPECIFICATION.md` directly, not a summary). Terrarium/mercantile
     tiles are numbered XYZ/slippy (y from the north), so tile *files* need
     `tms_y = 2**z - 1 - xyz_y` on write; tile *content*/geographic bounds
     still come from the original xyz coordinates. This was the actual
     reason refinement silently stalled at the root tiles for several
     iterations — double-check raw spec text over AI web-summaries for
     anything this load-bearing again.
- **Known gap:** OS-level screenshots stopped working reliably mid-session
  (Windows lock screen kicks in after ~20-90 s idle, outside automation's
  control). The in-game `-odshot` hook would sidestep this (renders via
  `FScreenshotRequest` inside the process, independent of desktop
  visibility) but currently lives inside `AODForestManager::Tick()` and
  no-ops without a traditional Landscape actor — true for any
  streaming-terrain level.
  `-odtrace` telemetry was sufficient evidence this session; a standalone
  screenshot-hook actor decoupled from forest logic is worth adding if
  visual verification of a streaming-terrain level is needed again.
- **Deferred, deliberately not attempted yet:** stitching to Tail of the
  Dragon / "drive off the Dragon's edges" (needs the seam reconciled with
  the hand-built landscape, plus fixing the `AODForestManager`
  first-landscape assumption above); the full Green Valley → Santa Clarita
  → Vegas → San Bernardino NF loop (trivial to scale the now-proven
  pipeline up to, just more tiles); road generation on top of the terrain
  (7.2, the hardest bite); Large World Coordinates/Origin Rebasing
  precision (only matters at cross-country scale — the
  `"Enable World Bounds Checks"` warning the old plugin printed every run
  was the visible reminder this is still owed before the real end-to-end
  milestone). **Not yet decided which of these three comes next.**
- **Road-gated finest-zoom terrain (2026-07-20):** `build_terrain_tiles.py`
  now only builds z15 (collision-grade, MAX_ERROR_NEAR) tiles that have a
  road station nearby (`road_near_tile()`) — everywhere else falls back to
  whatever coarser zoom is already built, via the standard quantized-mesh
  `layer.json` `available` field (the plugin stops refining at the nearest
  BUILT tile; a missing z15 entry is not a hole). z0..z14 still cover the
  whole bbox unconditionally, so distant terrain/mountains still render,
  just at lower fidelity. Motivated directly by Taylor's 20 m stray
  constraint (2026-07-20, § 7.2) — the player can never reach ground far
  from the road, so there's no reason to pay z15's cost there. On the
  current Palmdale bbox this skipped 37 of 80 z15 tiles (46%); the real
  point is this is what makes the z15 tier scale toward the full North
  America bbox at all — building collision-grade detail for the whole
  continent is a non-starter, building it only for the ~20 m-wide road
  corridor is not. `verify_road_fit.py` re-confirmed PASS at z15 after the
  change (rebuilds its own required tiles directly, unaffected by the
  skip). `availability_ranges()` replaced the old single-rectangle-per-zoom
  `layer.json` writer with per-row contiguous-run grouping, since coverage
  is no longer guaranteed to be one solid block.

### 7.2 Runtime road generation (C++)

**Slice 1 — static OSM-network road mesh on the streamed terrain, built
2026-07-19/20, code complete and builds clean; final in-game re-verify
blocked on a Windows Smart App Control issue (see below), not a code issue.**
Scoped deliberately small, same "prove the mechanics first" approach as
7.1's slice 1 — this does NOT yet do dynamic tile streaming/HTTP (that's
slice 2+, sketched at the end of this section).

- **Network generalization ✅:** `pipeline/fetch_osm_network.py` (broad
  drivable-highway-class Overpass query, no single-route `ref` filter) +
  `pipeline/build_road_network.py`'s `merge_way_chains()` — chains OSM ways
  through degree-2 nodes into maximal polylines, breaking only at real
  junctions/dead-ends. This replaces `build_stage.py`'s single-route
  Dijkstra stitch (fine for the Dragon's one named road, not for a network).
  Proven on the Palmdale bbox: 1470 raw OSM ways → 1015 merged chains → top
  12 longest kept (a deliberate slice-1 scope cap, not all 1015) = 41.95 km
  across real junctions, 63 mesh chunks, ~34k verts.
- **Shared math extracted, zero behavior change:** `pipeline/road_mesh_common.py`
  now holds the station-resample/grade-smooth/superelevation-bank/skirt-
  cross-section/chunk-assembly math that used to be inlined in
  `build_stage.py`; both `build_stage.py` (Dragon, unaffected) and
  `build_road_network.py` (the streamed-terrain network) call into it.
  `build_stage.py`'s `road_mesh.json` output verified **byte-identical**
  before/after the refactor (md5 match) — the Dragon was not touched.
- **Runtime C++ loader:** `AODRoadManager` (`ODRoadManager.h/.cpp`, new)
  loads a binary sidecar (`pipeline/export_road_mesh_bin.py`'s `ODR1`
  format — same magic+LE-header+payload convention as `centerline.bin`/
  `terrain_mask.bin`) at `BeginPlay` and builds real, collidable geometry
  via `UDynamicMeshComponent`/`FDynamicMesh3` (confirmed against the
  installed UE 5.8 engine source — plain `GeometryFramework`/`GeometryCore`
  Runtime modules, no plugin gating, added to `OpenDriveUE.Build.cs`). Same
  collision recipe `unreal/import_stage.py` already uses for the Dragon
  (`EnableComplexAsSimpleCollision` + `BlockAll`), just called directly in
  C++ instead of through Python's GeometryScript reflection layer. Material
  is `/Game/OpenDrive/Materials/M_Road` (same PM_Asphalt-carrying asset the
  Dragon uses) — load-bearing, not cosmetic, since complex-hit suspension
  traces read the *render material's* PhysMaterial.
- **`AODScreenshotHook`** (new, trivial): the `-odshot=N` block lifted
  verbatim out of `ODForestManager::Tick()` into a standalone actor, closing
  the gap 7.1 flagged (the original only fires when a Landscape actor
  exists, so it silently no-ops on streaming-terrain-only levels). Spawned
  only into the terrain-streaming test level; `ODForestManager`/the Dragon
  untouched.
- **A real bug the seam-check verification caught, worth remembering:** the
  road network initially floated **~800 m above** the actual streamed
  terrain — not a small seam, a full frame mismatch. Root cause: the
  pipeline wrote absolute DEM elevation (meters) straight into UE-frame cm
  Z, but the level's georeference actor uses `origin_placement=
  CARTOGRAPHIC_ORIGIN` with `origin_height=801 m`, which makes UE world Z
  *relative to that origin height*, not absolute. `build_road_network.py`
  now subtracts `test_areas.ORIGIN_HEIGHT_M` before the cm conversion. This
  is exactly the kind of thing slice 1 was scoped to de-risk before any
  tiling/streaming work got built on top of an unverified elevation
  assumption — worth the dedicated `-odseam` diagnostic hook
  (`AODRoadManager::RunSeamCheck`, triggered by `-odseam=N`, line-traces a
  sample of skirt-bottom verts on `ECC_Visibility` — the same channel
  `UODVehicleMovement`'s suspension uses — and logs
  `mean|dz|`/`max|dz|`/over-vs-under counts) turning out to be worth
  building.
- **Two environment-only snags hit while verifying, for next time:**
  1. Git Bash mangles a leading `/Game/...` map argument into a Windows
     path (`/c/Program Files/Git/Game/...`) via MSYS's automatic POSIX-path
     conversion — prefix the command with `MSYS_NO_PATHCONV=1`.
  2. `unreal.LevelEditorSubsystem.new_level()` on an already-existing level
     package fails "an asset already exists" even after the `.umap` is
     deleted from disk in the SAME process (`os.remove()` +
     `EditorAssetLibrary.delete_asset()` + a forced
     `AssetRegistry.scan_paths_synchronous(force_rescan=True)` all still
     hit it) — the registry's on-disk cache doesn't invalidate same-process.
     `load_level()` isn't a safe existence probe either (silently succeeds
     against the engine's blank transient level instead of failing). Fix:
     delete the `.umap` from a **separate** process/step before launching
     the one that calls `new_level()` — a fresh process with the file
     already gone works every time.
- **Smart App Control block (2026-07-20) — since cleared.** Windows briefly
  refused to load the freshly-compiled `UnrealEditor-OpenDriveUE.dll` at all
  (both `-game` and the pythonscript commandlet) — decoded error: "An
  Application Control policy has blocked this file." `VerifiedAndReputablePolicyState`
  (Smart App Control's reputation enforcement) was active on this machine;
  a locally-rebuilt, unsigned DLL rewritten many times in one session has no
  established reputation. Taylor turned Smart App Control off via Windows
  Security (his call, since it's a one-way OS security setting — can't be
  re-enabled without a clean Windows reinstall) later the same session,
  which unblocked all further in-game testing, including slice 2's — see
  below for what that testing actually found.

**Slice 2 — tile-based road streaming, built 2026-07-19/20. The streaming
*mechanism* is confirmed working via extensive live testing (not just
headless checks), but the road-to-terrain fit is STILL not reliably
correct as of this writing — see "Still outstanding" below before treating
this as done.** Scoped deliberately: proves the tiling + streaming
*mechanism* only, not HTTP — Taylor's call this session was to defer HTTP
fetch + a disk cache to a later slice and keep tiles loading from local
packaged Content (same model `AODForestManager`'s mask already uses), just
cut into a grid instead of one whole-network file.

- **Pipeline:** `pipeline/export_road_mesh_bin.py` now buckets the
  already-computed flat chunk list by each chunk's centroid into a
  `ROAD_TILE_SIZE_CM = 200000` (2 km, `pipeline/test_areas.py`) world-space
  grid, writing one `ODR1` file per non-empty cell
  (`road_<AREA>_test_tile_<tx>_<ty>.bin`) instead of one big sidecar.
  `road_mesh_common.py`'s station/chunk math is untouched — pure bucketing
  over already-built geometry. Verified on the Palmdale network: 63 chunks
  → 18 tiles (grid x[-2..2] y[-3..2]), 33,788 verts / 50,304 tris total,
  matching the original slice-1 totals. The old single-file
  `road_palmdale_test.bin` is superseded and was deleted (gitignored,
  regenerable, nothing references it anymore).
- **Runtime (`AODRoadManager`, `ODRoadManager.h/.cpp`):** `Tick()` now
  copies `ODForestManager`'s ring-iteration/hysteresis spawn-despawn
  pattern, keyed by the same `FIntPoint` world-grid convention, but tile
  *building* moved off the game thread — the first async/background-task
  code anywhere in `OpenDriveUE` (`Async(EAsyncExecution::ThreadPool, ...)`
  does the file-read + `ODR1` parse into plain arrays, hops back via
  `AsyncTask(ENamedThreads::GameThread, ...)` to build the actual
  `UDynamicMeshComponent`s — a `TWeakObjectPtr` + `GetWorld()` guard makes a
  post-teardown completion a safe no-op). New `TSet<FIntPoint>
  PendingTiles`/`MissingTiles` avoid re-requesting in-flight or
  confirmed-absent tiles. Load/despawn radii are computed in world-space
  cm (`SpawnRadius` + half the max chunk span + a fixed `DespawnMarginCm`),
  not copied as a bare tile-count constant from Forest's 96 m-tile-tuned
  `+2` — that would have badly under- or over-shot at road tiles' 2 km
  size. `EndPlay` is new to this module too (nothing else here has had a
  lifetime-crossing async op to clean up before) — just logs in-flight
  count, since thread-pool work can't be cancelled, only safely ignored.
  `-odseam` still works, now filling in `SkirtSamplePoints` incrementally
  as tiles load rather than all at `BeginPlay`. Build verified clean
  (`Build.bat OpenDriveUEEditor Win64 Development`, 0 errors) — no new
  module dependencies needed (no HTTP/Json/Sockets; `Async`/`AsyncTask` are
  Core-only).
- the from-scratch terrain-test-level build script (since retired) updated:
  sets the new `road_tile_prefix` property instead of the retired
  `road_mesh_file`.
- Design was reviewed against a Plan-agent pass before implementation
  (correctness of the `TWeakObjectPtr` pattern, world-space hysteresis math,
  dispatch-rate capping to avoid a thundering herd of file opens on level
  start); confirmed directly that `DefaultGame.ini`'s
  `DirectoriesToAlwaysStageAsUFS=(Path="OpenDrive/Masks")` is a
  whole-directory staging rule, so the new per-tile files need no packaging
  changes, and that Origin Rebasing is off in `DefaultEngine.ini`, so
  absolute-world-cm `FIntPoint` tile keys stay valid. Full design at
  the session's plan file if needed later, but the summary above plus the
  code is the durable record.
- **Level features decoupled from requiring a race (2026-07-20/21).** Found
  investigating why none of Tail of the Dragon's HUD/audio/tuning/pause
  showed up on the terrain-streaming test level: those actors
  (`AODVehicleAudio`, `AODTuningManager`, `AODPauseMenu`) had only ever been
  placed on the Dragon by one-off `unreal/apply_*.py` scripts hardcoded to that level, and
  `ODRaceHUD::DrawHUD` gated its ENTIRE body on finding an `AODRaceManager`
  (which only exists for checkpoint-gate races — deliberately not spawned
  on this open-world/free-roam level). Fixed in two parts: (1)
  the from-scratch terrain-test-level build script (since retired) now
  spawns `VehicleAudio`/`PauseMenu`/`TuningManager`/`DayNight` directly
  (same actors, same classes, just
  authored per-level like the Dragon's own scripts do) and `ODRaceHUD.cpp`
  now only gates the actual race timer/gate-progress text on `Race` —
  tuning toast, speed readout, and the pause menu draw with no race at all;
  (2) the mini-map/full map — the one piece that genuinely needs route
  data — now falls back to **`AODRoadManager`'s own centerline**
  (`CenterlineFilePrefix`/`RoadCenterline`, same `ODC1` sidecar format and
  loader convention as `AODRaceManager`/`AODForestManager`) when no
  `AODRaceManager` exists, instead of requiring one just to draw a route
  it was never going to use for checkpoints. `DrawStageMap` takes plain
  `Centerline`/`Gates` data now, not an `AODRaceManager&`, so it no longer
  cares which actor supplied the route; start/finish/next-gate markers and
  the PB ghost dot are skipped on the route-only path (nothing to mark).
  `unreal/apply_road_centerline.py` (new, same idempotent-by-label pattern
  as the other `apply_*.py` scripts) sets `CenterlineFilePrefix` on an
  already-built level's `RoadManager` without a full rebuild; also wired
  into the from-scratch terrain-test-level build script (since retired) for
  from-scratch builds. **Verified live** (`-game`, `-odshot`): screenshot on
  the terrain-streaming test level shows the round mini-map drawing the
  real road route with the player arrow, and
  the speed readout, with zero race actors on the level — confirming the
  whole HUD stack now works independent of a race setup, not just headless.
**What live testing found, once Smart App Control cleared (real `-game`
runs, not just headless `-odseam`):** tile-streaming mechanics themselves
checked out — dispatch, async parse, priority ordering, and despawn all
behaved correctly per trace logs. But live driving/visual inspection
surfaced real bugs headless testing alone hadn't caught, fixed one at a
time over several iterations:

1. **Ground-snap retries were unthrottled** — re-tracing every station of
   the front-of-queue tile every single tick made the game unplayably slow
   while waiting on terrain. Fixed: per-station memoization
   (`FParsedChunk::SnappedMask`) + a shared per-tick trace budget
   (`MaxGroundTracesPerTick = 64`).
2. **`AwaitingTerrainQueue` processed tiles in async-completion order, not
   ring-priority order** — a distant tile's file-parse could finish before
   the one actually under the player, so it jumped the queue. Fixed:
   `ProcessAwaitingTerrain` re-picks whichever queued tile is closest to
   the player each call, not blindly index 0.
3. **The per-tile timeout clock started at enqueue time, not
   first-actively-processed time** — a tile queued behind several others
   accumulated "wait debt" while completely untouched, and could time out
   the instant its turn came without ever being traced. Fixed: lazily
   stamped on first selection.
4. **Vehicle spawn depenetration launch** (the original "car flies several
   hundred meters up" report) — `PlayerStart`'s offline-baked height is
   never touched by the road's own runtime ground-snap, and can disagree
   with actual streamed terrain by tens of meters; spawning overlapping
   solid collision made Chaos violently eject the car. Fixed:
   `AODVehiclePawn` now holds the Body kinematic from `BeginPlay`, lifts
   20 m clear immediately so the wait itself is never visibly embedded,
   and only releases physics once a downward trace confirms the actual
   road surface specifically (`PM_Asphalt` physmat — the same check
   `ODForestManager::GroundAt` already used), not just any collision hit.
5. **No plausibility check on ground-snap hits** — the streaming plugin can
   return a hit against a coarse/low-LOD placeholder tile before it refines
   near boot;
   one implausible early hit stretched a station hundreds of meters from
   its neighbors, turning a flat road segment into a literal vertical
   wall (screenshot-confirmed). Fixed: `MaxPlausibleSnapDeltaCm` (50 m,
   comfortably above the ~30 m worst-case *legitimate*
   simplification-tolerance gap already measured via `-odseam`) rejects an
   implausible hit and keeps waiting instead of baking it in.
6. **Road ground-snap traces didn't exclude the vehicle pawn** — a station
   right at the spawn point (where the car always sits) could hit the
   car's own body instead of real terrain. Fixed: both
   `SnapChunkToGround` and `RunSeamCheck` now ignore the player pawn too.
7. **Player-start selection bug, in the pipeline, not the runtime:**
   `build_road_network.py` picked the *longest* built segment's first
   station as the spawn point regardless of where that segment actually
   sat geographically — it happened to land ~500 m past the bbox's
   northern edge (confirmed via direct coordinate math: the old spawn's Y
   put it at ~34.6345°N vs. the bbox's 34.630°N boundary), squarely in the
   ragged, least-reliable part of the built terrain. Fixed: now searches
   every built segment's stations for whichever sits closest to the bbox's
   geographic center instead.

**RESOLVED 2026-07-20 — root-cause restructure, not another reactive patch.**
The prior session's conclusion (below, kept for the record) was that
reactive live-patching had stopped being productive. Taylor's instruction
this session: stop patching the runtime reconciliation and restructure so
the fit is *guaranteed offline*, matching Tail of the Dragon's model (one
source of truth, computed once) instead of racing the streaming plugin's
async loading every time a tile loads.

- **Pipeline reordered to load-terrain → load-road → overlay, with
  elevation sampled exactly once:** new `pipeline/build_road_stations.py`
  owns all DEM sampling/grade-smoothing (moved out of
  `build_road_network.py`) and writes `road_stations.json` — the single
  geographic source of truth. `build_terrain_tiles.py` now CARVES every
  terrain tile to that same profile (`road_mesh_common.carve_heightmap`,
  inverse-distance stamp + smoothstep blend, same technique as the Dragon's
  `carve_road` generalized to the streamed pipeline's lon/lat-bounded tiles) before
  martini simplification runs, instead of leaving the two surfaces to be
  reconciled at runtime. `build_road_network.py` now only does UE-frame
  mesh assembly from `road_stations.json` - no DEM access of its own.
- **New offline gate, `pipeline/verify_road_fit.py`:** re-requests the
  exact tiles covering every road station through the real
  `build_terrain_tiles.build_tile()` (not a reimplementation) and
  barycentric-samples the ACTUAL simplified mesh that gets encoded and
  served - the thing that actually mattered, since a correct carve on the
  raw raster is necessary but not sufficient (simplification can still cut
  across a corridor). Runs entirely engine-free.
- **Two real bugs found by that gate, not guessed at:**
  1. The carve was being applied to the elevation array BEFORE pymartini's
     own `.T` transpose. pymartini indexes whatever array it's actually
     given with standard row=y/col=x semantics, so carving pre-transpose
     stamped every station at its geographic diagonal-mirror position
     instead of its real one - confirmed with a controlled non-planar 2-D
     gradient test, not assumed. This alone was the entire ~440 m worst-case
     mismatch stations 2026-07-19's `-odseam` had been measuring - not a
     carve-width problem at all. Moving the carve to after the transpose
     dropped every measured station under ~3 m.
  2. Even after that fix, z15's `MAX_ERROR=2.0` m simplification tolerance
     still left a few real, LOCALIZED ~2.5-2.6 m gap/overlap clusters -
     small enough to pass a "fits under the 2.5 m skirt" bar, but big
     enough that Taylor watching an actual drive saw the road floating over
     a visible gap in one spot and terrain poking through in another. Added
     `MAX_ERROR_NEAR = 0.5` m, applied only at z15 (MAX_ZOOM) since coarser
     zooms never render close to the vehicle and don't need the extra
     vertex cost - dropped the worst z15 station to 0.90 m.
- **All the runtime reconciliation code this used to lean on is deleted**
  from `AODRoadManager` (`SnapChunkToGround`, the whole
  `AwaitingTerrainQueue`/`Data`/`Since` wait-for-streamed-terrain machinery,
  `MaxPlausibleSnapDeltaCm`, the per-station `SnappedMask`) - roughly 180
  lines gone. `OnTileParsed` now finalizes a tile immediately; there is
  nothing left to reconcile at runtime because the pipeline guarantees the
  fit before the engine ever sees the data.
- **Verified three independent ways, not just one:** `verify_road_fit.py`
  (offline, mesh-accurate) passes at z15 (max 0.90 m); a live in-engine
  `-odseam` collision-trace check (after fixing an unrelated pre-existing
  bug in it - see below) found terrain sitting consistently 2.1-2.7 m
  *above* every sampled skirt-bottom point, 138/138, matching the skirt's
  own 2.5 m design drop exactly; and a screenshot of the car sitting on the
  road shows a clean edge into the distance, no visible gap or overlap.
- **Bonus fix, found while re-verifying: `-odseam`'s anchor selection was
  wrong.** It anchored on `SkirtSamplePoints[0]` assuming tiles finish
  loading in ring/dispatch order: they don't (thread-pool completions
  race), so on this 18-tile network the first-ARRIVED sample landed 3.4 km
  from spawn in an unrelated tile, and every "nearby" trace missed real
  terrain that just hadn't streamed in that far away - a false `n=0` with
  no actual data problem behind it. Fixed by capturing the player's actual
  spawn location on the first `Tick()` (`AODRoadManager::SpawnAnchor`)
  instead of trusting sample-arrival order.
- **Also found, unrelated to elevation:** Git Bash mangles a leading
  `/Game/...` map argument into a Windows path when launching
  `UnrealEditor.exe` directly (not just the pythonscript commandlet path
  documented earlier) - `MSYS_NO_PATHCONV=1` fixes it. And the streaming
  plugin's own request cache (a local sqlite file, since deleted along with
  the plugin) keyed by URL,
  not content - it needed deleting before re-testing after any terrain
  rebuild, or a stale cached tile can hide a real fix (or a real problem)
  behind the last run's response.

**Prior conclusion, kept for context (2026-07-19, superseded by the above):**
"even after every fix above, a live drive-test still showed the road not
correctly fitting the terrain... reactive live-patching had stopped being
productive." That diagnosis was right - the fix wasn't another patch, it
was moving the whole reconciliation offline.

**Why this arc took so many iterations, worth remembering:** Tail of the
Dragon's terrain+road never hit this class of bug because it's one static,
deterministic dataset - a Landscape heightmap imported once, offline, with
the road sampling the *exact same* raw DEM at full precision. There was
only ever one source of truth, computed once. This live-streamed-terrain
level's runtime reconciliation approach required THREE independently-streaming,
asynchronously-refining systems to agree at runtime with no reliable
"everything has settled" signal - restructuring to carve-once-offline
collapses that back down to the Dragon's one-source-of-truth model instead
of trying to make three moving parts agree live. The two real bugs found
this session (transpose order, simplification tolerance) were both
findable and fixable *because* they now live in a deterministic, engine-free
pipeline stage with its own gate, not in async runtime behavior.

**Slice 3 sketch (unchanged, not built yet):** NA-scale preprocessing +
Large World Coordinates/origin-rebasing correctness at cross-country
distances + intersection/junction mesh blending (deliberately not attempted
in slice 1's independent per-segment banking, or slice 2's centroid-bucketed
tiling).

### 7.3 Biomes + surfaces at scale
- **ESA WorldCover 10 m** tiles replace satellite-color classification as the
  mask source (same mask→forest/detail contract; classifier code isolated).
- **Regional flora vibes** *(Taylor, 2026-07-14)*: species mix should match
  the region's *feel*, not its botany — incredibly dense deciduous canopy in
  the Southeast, evergreen-dominated north/mountains, scrub in the SW.
  Implementation: per-region foliage recipes (mesh set + palette + density
  curve) selected by land cover + climate rasters; `AODForestManager`'s
  per-tile species system is the hook — this is data + palette work.
  **Recipe table ✅, classifier not started (2026-07-20):**
  `EODBiome`/`FBiomeRecipe`/`BiomeRecipeTable` in `ODForestManager.h/.cpp`
  now hold 7 named recipes (DeciduousForest, EvergreenForest, Chaparral,
  DesertScrub, PlainsGrassland, Wetland, AlpineBare), each a species-mix
  weighting + tree/bush density scale over the existing 3-species/1-bush
  mesh set (no new assets needed) — the taxonomy is the same list Taylor
  confirmed against the ESA WorldCover class set. `BiomeAt()` is the
  seam a real per-tile classifier plugs into; until WorldCover fetch +
  classification exists, every tile just reads `DefaultBiome` (one value,
  editor-exposed, defaults to DeciduousForest - this manager currently only
  runs on Tail of the Dragon, since `LoadMask()` requires an
  `ALandscapeProxy` that the terrain-streaming test level didn't have yet, and the
  Dragon's real location (US-129, Deals Gap) is Southeast Appalachian
  mixed forest, not the streamed-terrain test area's high-desert character).
  Traction (1.4) and forest density are now independent: traction collapsed
  to road/off-road, this stayed per-biome/multi-value — different systems
  answering different questions ("how much should this hurt" vs. "what does
  this look like").
- Imagery drape: USGS for US at stage fidelity; global stylized fallback =
  colorized land cover + hillshade (avoids imagery licensing/volume).
- PRISM/WorldClim moisture becomes a world raster feeding road tint and
  future weather; region flavor presets extend the palette/mesh-set system.
- **Packaged-build mask ✅** *(2026-07-18)* — `terrain_mask.bin` sidecar
  (pipeline/make_forest_mask_bin.py) + `LoadMask()` bin fallback; verified
  in the packaged build (90k instances, tiles streaming while driving).

### 7.4 World game layer
- NA map view (tiled, zoomable — upgrade of 4.2), free-roam ↔ stage entry
  flow, stage discovery ("drive to a famous road to unlock its time trial"),
  cache management UI (size cap, clear) — release requirement.

**Size:** the campaign — months, parallel with everything after Arc 3.
Bite order: 7.1 → 7.2 (hardest) → 7.3 → 7.4.

### 7.5 Open-world menu/map/spawn robustness (2026-07-26/27)

A cluster of real bugs and one small feature, all found via actual live
`-game` testing (Taylor's own play sessions, not just headless checks) of
the `OpenWorldTest` level after a gap where it hadn't been driven since the
main-menu/pause-menu split (§ 4.0b) landed.

- **Bug: `AODMainMenu` was silently absent from a freshly-rebuilt level.**
  `unreal/build_openworld_test_level.py` (the from-scratch builder) never
  spawned `AODMainMenu` itself — it was only ever added by a separate
  follow-up script, `unreal/apply_main_menu.py`, run once against the level
  right after `AODMainMenu` was written (2026-07-25). When the level got
  rebuilt from scratch again later, that follow-up script wasn't re-run, so
  the new `.umap` had no `AODMainMenu` actor at all. Symptom: the game
  booted straight into driving instead of the car-config screen, and the
  pause menu's "Main Menu" row silently did nothing (`FindManager<AODMainMenu>`
  returned null, so its `if` just never fired). Exactly the same *class* of
  bug as the `Sun` mobility issue from Arc 7.1/7.0 — a one-off patch script's
  effect doesn't survive a from-scratch rebuild unless it's folded into the
  builder itself. **Fixed:** ran `apply_main_menu.py` against the live level
  to recover immediately, and added the same spawn call directly into
  `build_openworld_test_level.py` (next to `PauseMenu`) so this can't happen
  again on the next full rebuild.

- **Feature: stylized terrain-color map background ✅** (`pipeline/
  build_map_texture.py` + `unreal/apply_map_texture.py`, new) — the mini-map/
  full map used to draw the road as a bare line on flat black, which read as
  "a confusing mess of road" with no sense of water/forest/farmland/terrain
  around it. Reuses `classify_biomes.py`'s existing imagery fetch +
  forest/grass/rock/water RGB classification wholesale (that script already
  computes this per-pixel for the whole road bbox, it just only ever
  *sampled* it at road stations before) — palette-maps the four weights to
  flat stylized colors, crops the fetched mosaic to a real-world-meters
  SQUARE before downsampling (imagery tiles are lon/lat-aligned, not square
  in meters away from the equator) so one `span_cm` value covers both
  texture axes with no aspect-ratio bookkeeping needed on the engine side.
  New `AODRoadManager` properties (`MapTexture`/`MapTextureCenterCm`/
  `MapTextureSpanCm`, all optional — null/unset falls back to the original
  flat-color background unchanged); `ODRaceHUD::DrawStageMap` draws it
  behind the road line — a plain UV-cropped tile for the square full map, a
  proper per-vertex-UV triangle fan (`FCanvasTriangleItem`'s explicit-UV
  constructor) for the circular player-centered mini-map — with a light dim
  overlay on top so the route line/dots/labels stay legible over busy
  terrain colors.
  - **Real bug found tuning the classifier for this, not previously
    visible:** `classify_rgb()`'s water heuristic ("dark + blue-tinted")
    misfires broadly on shadowed mountain slopes under blue-sky ambient
    light — measured via a one-off diagnostic against this test area's
    cached imagery mosaic: 22% of all pixels scored `water > 0.4` at a mean
    brightness of 0.20 (squarely shadow range, not open water). This mostly
    didn't matter for the ORIGINAL use of these weights (per-station biome
    classification, which checks `w_rock` first and only reaches the water
    branch past that) but painted whole shadowed ridgelines blue once fed
    directly into a per-pixel color blend with no such precedence. Fixed
    (in `build_map_texture.py` only, `classify_biomes.py`'s own heuristic
    untouched) by gating water to a much stricter bar (`w_water > 0.9`) plus
    a brightness floor — cut the false-positive area to ~1.4%, reading as
    real rivers/lakes instead of a wash over every dark slope.
- **Cleanup: the old live-streaming-terrain plugin's reference is fully
  removed** from `OpenDriveUE.uproject`. Arc 7.0's pivot (2026-07-23)
  replaced it with baked/tiled terrain, but the plugin entry was still
  present and `Enabled: true`, so it kept loading (confirmed zero actual
  C++ dependency first — every historical mention in `Source/` is a prose
  comment or an old asset name, never an include/API call) and spamming a
  `LogClass: Error` about an uninitialized enum semantic on the plugin's own
  metadata-property type, on every single boot.
- **Bug + fix: `M_BuildingFacade` material missing from Content.** Same
  "one-off script, not folded into anything, doesn't survive a rebuild"
  class of issue as the main-menu one above — `unreal/apply_building_material.py`
  builds this material fresh but is never called from anywhere else, and
  `Content/` is entirely git-ignored/regenerable, so it was simply absent
  after some rebuild. Symptom: `LogUObjectGlobals: Warning: Failed to find
  object 'MaterialInterface /Game/OpenDrive/Materials/M_BuildingFacade...'`
  every boot, buildings silently missing their facade material. Fixed: ran
  the script to recreate it, and added the same "warn if missing" guard
  `build_openworld_test_level.py` already uses for missing tuning-preset
  assets, right where `BuildingManager` is spawned.
- **Real bug found + fixed: default spawn hovers thousands of feet up,
  immobile, forever (as reported live — "spawn straight into the air").**
  Root cause, found by direct investigation of `AODVehiclePawn`'s ground-
  snap trace log (`hit=1 onRoad=0 physmat=DefaultPhysicalMaterial` for the
  entire 20 s timeout) plus checking which tile files actually exist on
  disk: `pipeline/build_road_network.py`'s player-start picker searched for
  whichever road station sits closest to the LOCAL ORIGIN `(0,0)` — but
  `(0,0)` is ALSO exactly a corner of the road-tile grid `export_road_mesh_bin.py`
  buckets chunks into (tiles are floored from 0), so the pick landed only
  ~2 m from a tile boundary. The chunk covering that station centroid-
  bucketed into a NEIGHBORING tile instead of the one the spawn point
  nominally sits in, leaving the home tile with zero road mesh. The ground-
  snap trace (which deliberately waits for a `PM_Asphalt` hit, not just any
  surface — see its own class comment) hit bare terrain instead the entire
  wait and only settled onto it once the 20 s timeout elapsed — matching
  "I could see the ground, I was just thousands of feet above it and
  immobile" exactly (something WAS visible below the whole time; it just
  was never going to read as road). **Fixed:** the picker now requires real
  margin (500 m — comfortably past the ~375 m worst-case a station's own
  position can drift from its chunk's centroid, since chunks are fixed
  150-station/750 m windows along the route, not centered on any particular
  station) from every tile boundary on both axes, falling back to the old
  closest-to-center behavior only if literally no station qualifies (never
  hit in practice — this network has ~922k stations). Verified the new
  pick's home tile has both road AND terrain files on disk before treating
  it as fixed (the old one had neither for road). The already-baked level's
  `PlayerStart` actor was moved to the new point in place via a new
  `unreal/apply_player_start_fix.py` (no full level rebuild needed) — future
  from-scratch rebuilds pick up the corrected `road_test_meta.json`
  automatically since `build_openworld_test_level.py` always reads it fresh.
- **Feature: loading screen for map-picked spawns ✅** (2026-07-26,
  `AODMainMenu`/`ODRaceHUD`) — picking a town on the map teleports the car
  via the same `TeleportToSpawn`/ground-snap machinery as the fix above, but
  a freshly-picked, far-off location can have NOTHING streamed in yet
  (`AODRoadManager`/`AODTerrainManager` only load tiles around the player's
  CURRENT position, and neither one appears to tick at all while the game
  is paused, which it is for the entire car-config/map-browsing flow) - the
  player used to see the car hovering, uncontrollable, for however long the
  wait took. New `EODMenuStage::Loading`: `StartAt()` now unpauses
  immediately after teleporting (so streaming can actually start) but shows
  a full opaque "Loading..." overlay and polls the pawn's ground-snap state
  each tick, only handing back control once it's actually settled — the
  same mid-air-hover CONDITION still happens underneath, it's just never
  shown to the player anymore. Confirmed live both ways: Taylor's own
  interactive test ("that worked") and a scripted `-odpick=Marysville
  -odshot=20` repro that caught the loading screen mid-wait
  (screenshot-verified: clean black overlay + "Loading..." text, no popped-
  in geometry visible).
  - **Also fixed the same day: the pause-menu camera/framing complaint**
    ("sitting inside the car, like under it") — `AODPauseMenu` never gave
    the camera any special treatment at all, just froze whatever the chase
    cam happened to be doing at pause time. New "Garage" row
    (`AODPauseMenu`, between ABS and Main Menu) opens `AODMainMenu`'s
    EXISTING car-config screen in place (`AODMainMenu::OpenGarage()`, new) -
    same showroom camera treatment (`SetShowroom(true)`: levels the car,
    detaches the spring-arm's yaw inheritance) the fresh-boot flow already
    used, which incidentally fixes the broken framing as a side effect of
    reusing code that was already working correctly elsewhere, rather than
    debugging the raw chase-cam clipping case directly. Unlike the fresh-
    boot flow, "Drive" (relabeled "Resume Driving" in this mode) or Escape
    just unpauses in place - no re-teleport, no loading screen, since the
    car never left real pavement to begin with.
    - **Documentation catch-up (found reconciling the tree during the
      2026-07-29 doc-consolidation pass, not new work done then): the
      SpringArm-based fix above never actually worked as intended**, per
      the code's own comment, and has since been replaced entirely.
      **Actual root cause:** `APlayerCameraManager` doesn't tick while the
      game is paused, so a spring-arm-relative camera frozen at pause time
      could easily be mid-transition into "inside the car." Fix now in
      `ODVehiclePawn.cpp`/`ODMainMenu.cpp`: `SetShowroom()` fully detaches
      the camera and places it with plain vector math
      (`ShowroomDistanceCm`/`ShowroomHeightCm`/`ShowroomLookAtBiasCm`,
      replacing `ShowroomLateralOffsetCm`) instead of riding the driving
      spring-arm; `AODMainMenu::OpenMenu()` (fresh-boot flow) now
      deliberately does NOT pause the game at all, since the frozen preview
      pawn can't drive off anyway (`SetSimulatePhysics(false)`); `OpenGarage()`
      (this mid-drive reopen) still pauses but now force-ticks the camera
      manager so the detached camera actually updates. `ODScreenshotHook`
      also picked up two related fixes while this was being chased:
      `bTickEvenWhenPaused = true` (its Tick silently never ran during any
      paused menu, so `-odshot` was a no-op the whole time) and a switch
      from `GetWorld()->GetTimeSeconds()` (freezes when paused) to
      `FApp::GetCurrentTime()` (wall-clock) for its elapsed-time check.
- **Deeper, NOT fully root-caused: cold-start tile-streaming latency after
  a map-teleport can still take the full 20 s timeout, not just a few
  frames.** A scripted `-odpick=Marysville` repro hit `onRoad=0` for the
  ENTIRE 20 s wait, twice, in two fully separate process launches (same
  exact hit coordinates, same `DefaultPhysicalMaterial`, both times) -
  identical-enough results across independent runs that this reads more
  like a genuine DATA gap at that specific spot than pure load-order race,
  which lines up with the next bullet. The loading screen above makes this
  no longer visible/confusing to the player, but the underlying "why does
  this take up to 20 real seconds" question for a general (non-Marysville)
  cold teleport target is still open and would need a dedicated
  instrumented pass to answer properly - this project's own history (§ 7.2)
  is that this exact class of bug (reconciling independently-streaming
  systems against a just-moved player) needs that kind of dedicated session,
  not more reactive guessing.
- **New bug, reported live, NOT investigated yet: the coastal road around
  Marysville appears to be entirely missing.** Concrete lead for whoever
  picks this up: picking "Marysville" from the map consistently teleports
  to world XY `(-1699061, 2597499)` cm (confirmed identical across two
  separate runs) - the ROAD tile file for that point's grid cell
  (`road_skagit_cascades_test_tile_-9_12.bin`) DOES exist on disk, but the
  ground-snap trace there never once reads `PM_Asphalt`, even after the
  full 20 s wait. Worth checking whether that tile's chunks actually cover
  the coastline itself or just miss it narrowly (same centroid-bucketing
  class of issue as the player-start bug above, or a genuine gap in the OSM
  fetch for that specific stretch) before assuming it's a big pipeline
  problem.
- **New bug, reported live, NOT investigated yet: the open-world map reads
  far too dark almost everywhere, hard to make anything out.** Not yet
  diagnosed - note for whoever picks this up: `build_openworld_test_level.py`
  DOES already spawn a "Grade" `PostProcessVolume` with the same exposure/
  contrast values as Tail of the Dragon's own lighting fix, so the darkness
  is NOT simply a missing-grade-volume repeat of that old bug; the actual
  cause is still unknown (worth checking sun intensity/angle, fog density,
  and whether the grade volume's `unbound` flag and priority are actually
  taking effect over this level's specific geometry before assuming
  anything about the fix that worked for the Dragon).

### 7.6 Oahu test area — status update (2026-07-27)

Test area switched from Skagit/Cascades to Oahu, HI this session
(`pipeline/test_areas.py` — Taylor's call, "build the entire island," a whole
bbox rather than a scoped loop, and the first real exercise of the
tropical/coastal biome entries plus Honolulu's dense downtown grid; § 7.5
above is the historical record of the Skagit-era `OpenWorldTest` bugs and
predates this switch). Current state: roads and buildings are up and working
on `OpenWorldTest` (barriers and forest intentionally still disabled via
`BARRIER_ENABLED`/`FOREST_ENABLED` in `unreal/build_openworld_test_level.py` —
staged troubleshooting flags for isolating terrain/road/building issues one
system at a time, not a bug; see § 7.8 for what `AODBarrierManager` actually
is). Also fixed this session: far-terrain tessellation improved
(`FAR_GRID_N` 9→21 in `build_terrain_mesh_tiles.py`), map-texture/minimap
wiring, and material compile bugs in `M_BuildingFacade` and
`M_GroundStylized`.

**Not yet verified live:** the visual-fidelity concerns raised during
playtesting remain largely unconfirmed — a Windows-XP-like sharp/oversaturated
look, whether road alignment reads as geographically plausible,
building-to-road misalignment, and (§ 7.7) no water anywhere on the island.
`unreal/apply_spawn_at_latlon.py` already exists for coordinate-matched spawn
testing but hasn't yet been used to do an actual verified screenshot
comparison against real-world reference imagery — that's the natural next
step before chasing any of these visually rather than guessing from memory of
the drive.

### 7.7 Ocean/water rendering — completely missing (scoped 2026-07-27)

Found/scoped during Oahu playtesting — "water doesn't exist" was a direct
Taylor complaint, and on an island bbox that's immediately obvious. Root cause
scoped, not yet fixed: `build_terrain_mesh_tiles.py` only bakes terrain within
`RENDER_DISTANCE_M` (6000 m) of a road station, so offshore tiles are never
built at all — a real geometry hole, not a missing-material issue.

- **Tier 0 (cheap, ~1-2 hrs, no new pipeline work):** a single large flat
  plane at `SEA_LEVEL_Z_CM` (already defined in
  `unreal/apply_ground_material.py`, derived from `ORIGIN_HEIGHT_M` in
  `pipeline/test_areas.py`), sized to the area bbox, simple
  translucent/scrolling water material. Land pokes up naturally wherever DEM
  elevation is positive. Would NOT flood inland water sitting above sea level
  (e.g. Wahiawa Reservoir) — a flat plane at sea level can't distinguish
  "below sea level" from "an inland lake."
- **Tier 1 (real fix, matches the project's established tile-baked-manager
  pattern):** Python bake script + C++ `AODWaterManager` mirroring
  `AODBuildingManager`/`AODTerrainManager`. Fetch OSM `natural=water`
  (lakes/reservoirs — closed polygons, straightforward) and
  `natural=coastline` (the ocean edge). **Call out explicitly:** OSM
  coastlines are disconnected linestring ways, NOT closed polygons, so
  stitching them into closed land/sea rings is a known-hard problem (normally
  needs a dedicated tool like `osmcoastline`) — this is the real time sink
  here, not the manager/streaming boilerplate (which is now a well-worn
  pattern in this codebase).

**Recommendation:** do Tier 0 first as an immediate visual fix; scope Tier 1
as a separate follow-up task.

### 7.8 Roadside furniture (guardrails/signs/poles) — unscoped (2026-07-27)

Found/scoped 2026-07-27.

- `AODBarrierManager` (currently disabled via `BARRIER_ENABLED=False` in
  `unreal/build_openworld_test_level.py` — an intentional Taylor
  troubleshooting choice, not a bug) is **not** roadside furniture — it's a
  pure invisible gameplay collision safety-rail (a flat 13 m offset ribbon
  from every road station, `SetVisibility(false)`), dressed with generic
  trees/bushes just so it "reads as scenery." Already independently flagged
  in `AUDIT.md` § 5 as the single largest disk consumer (213 MB, `ODW2`) and
  the most-criticized-in-genre element of the project if ever shipped as-is.
- No OSM tag data is fetched anywhere today for `barrier=guard_rail`,
  `traffic_sign=*`, `highway=street_lamp`, `traffic_signals`, etc. Road class
  itself isn't even threaded through to `road_stations.json` currently
  (dropped after `osm.json`), so no data exists yet to distinguish a highway
  curve from a residential street for placement purposes.
- **Cheap tier:** fetch real `barrier=guard_rail` ways +
  `traffic_signals`/`stop` intersection nodes (half-day-to-a-day, mechanical,
  follows the buildings-fetch precedent — but actual OSM coverage density for
  Oahu is unknown until queried).
- **Heuristic tier:** generic sign posts, street lights, curvature-triggered
  guardrail placement — no reliable OSM signal exists for any of this, so it
  would mean inventing placement rules from scratch (same risk category as
  the building-height-guessing already flagged elsewhere in the project as a
  weak spot). Should stay a separate, lower-priority, open-ended item, not
  bundled with the cheap tier above.

### 7.9 Implementation audit + decisions (2026-07-27)

A full read-only audit of *how* the world is built and delivered — terrain and
road geometry, textures/materials, procedural asset generation, runtime
streaming. Full analysis, measured numbers, and a ranked 15-item queue live in
[`AUDIT.md`](AUDIT.md); this section is the plan-of-record summary.

**Decisions made (Taylor, 2026-07-27):**

1. **Scope → mountain hot zones**, not the literal continent (see the Arc 7
   header above). Turns ~950 GB into ~25 GB.
2. **Per-location coordinate frames**, no shared continental space.
3. **Buildings: collision only near the road**, mirroring the terrain near/far
   split. All 74,090 Oahu buildings currently get full complex collision.
4. **The road-corridor barrier is a stopgap** — replace the hard invisible wall
   with an out-of-bounds reset (reuse `AODRaceManager`'s respawn), but keep a
   light visual boundary so the player can see the edge. Deletes 213 MB.
5. **Terrain detail: adaptive martini triangulation**, base grid 257
   (7.81 m over a 2 km tile), done *first*, without a look-first verification
   pass. RTIN requires a 2^k+1 grid, so the current `NEAR_GRID_N = 41` must
   change regardless.
6. **D1 generic tiling detail normal** — do it, cheap and orthogonal. **D2
   DEM-derived per-tile normal maps** — dropped; they cost the same as the
   geometry and deliver less (no silhouette).

**The findings that drove them, briefly:**

- **The pipeline bakes the *output* of a deterministic function instead of its
  *input*** — 436 MB for Oahu, of which the barrier alone is 213 MB and
  contains no information not already in `road_stations.json`. The
  zero-per-instance rule (standing decision 2) is enforced for 90,000 trees and
  for nothing else.
- **Bridges and tunnels are broken, measured on H-3:** 38.9% of it sits within
  ±1 m of the ground it should be flying over, and the Tetsuo Harano Tunnels
  are built **429.7 m inside the Koʻolau range**. Root cause:
  `fill_structure_gaps()` interpolates *ground elevation at the abutments*
  rather than constructing a deck, and `grade_smooth_elevation`'s clamp is a
  one-way ratchet that can only pull decks down. Matters more under the new
  scope, not less — mountain roads are disproportionately viaducts and bores.
- **74,090 buildings each get their own `UDynamicMeshComponent`** — ~25,000
  resident over downtown Honolulu, each a draw call and a runtime collision
  cook. The triangles are trivial (1.72M total); the per-object overhead is
  the entire cost, and it is CPU-bound, so no GPU upgrade substitutes for
  merging per tile.
- **A confirmed determinism bug:** `build_road_barrier.py:168` seeds barrier
  scatter with Python's salted `hash()` on a string, so every rebuild places
  different trees. Verified by running it twice. Fix before any rebake.
- **The road discards OSM tags it already fetched** — `lanes` (41% of Oahu
  ways), `surface` (40%), `maxspeed`, `layer` (814 ways, the vertical-stacking
  signal that would settle bridge/tunnel crossings directly). Every road in the
  world is currently the same 9 m ribbon.
- **~208 MB of retired Skagit/Palmdale tiles** are still in
  `Content/OpenDrive/Masks` and would be cooked and shipped, since
  `DirectoriesToAlwaysStageAsUFS` stages the whole directory.

**Explicitly out of the audit's scope** (so silence is not a clean bill of
health): vehicle physics, audio DSP, the race framework, HUD/menu code, and the
Tail of the Dragon stage pipeline. No build was compiled and no live session
run, so it is also unverified that the current untracked C++ compiles.

### 7.10 Audit work queue, items 0–3 (2026-07-27/28)

`AUDIT.md`'s ranked queue (§ 7.9 above) is a dated snapshot; this section is
the maintained status. Items 0 and 2 turned out to already be done by the
time work resumed; items 1 and 3 were implemented and rebaked this session.

- **Item 0 (`build_road_barrier.py`'s non-deterministic `hash()` seed):**
  already fixed (crc32 over an explicit byte encoding) — landed in the same
  commit as the audit itself, so the audit's own "fix before any rebake"
  warning was already stale when written.
- **Item 2 (retired-area tiles in `Content/OpenDrive/Masks`):** already
  moot — checked directly, every file under `Masks/` is `oahu_test`-prefixed;
  no Skagit/Palmdale leftovers exist to prune.
- **Item 1 (adaptive martini terrain, base grid 257):** implemented in
  `build_terrain_mesh_tiles.py`. `NEAR_GRID_N` is now 257 (was 41, RTIN
  requires 2^k+1) and is a ceiling, not the literal output density —
  `MAX_ERROR_NEAR_M = 2.0` drives pymartini's simplification. Far tiles are
  unchanged (still a uniform 21×21 grid, no adaptive triangulation — they're
  already cheap and have no collision).
  - **Measured storage economics don't match the audit's estimate.**
    AUDIT.md guessed steep terrain would retain "10–15% of the 257² grid."
    Probed against 3 real Oahu tiles (flat/moderate/Koʻolau-cliff) before
    committing to the full pipeline (the audit's own recommended cheap
    hedge): retention ran 22–44% even at 2 m error, because Oahu's eroded
    volcanic terrain carries real high-frequency relief almost everywhere,
    not the smooth-valley/sharp-ridge split the estimate assumed. Taylor's
    call, 2026-07-28: accept the higher cost and revisit later if it proves
    too expensive in practice; `NEAR_GRID_N = 129` (uniform, Option A) is
    the documented one-constant fallback if so.
  - **Seams needed a mitigation the audit flagged but didn't design.**
    Measured empirically that pymartini does NOT reliably pick the same
    sparse boundary-vertex subset between two independently-triangulated
    adjacent tiles, even when the underlying elevation is bit-identical
    (5–11 point symmetric differences out of ~60–150 boundary points).
    Fixed with `build_seam_skirt()`: a full-resolution (257-point) vertical
    curtain dropped `SEAM_SKIRT_DROP_M` (10 m) below the true boundary
    elevation on all 4 edges of every near tile — same "hide the gap under
    geometry" pattern `road_mesh_common.py`'s own road skirt already uses.
    Covers both near/near seams and the (pre-existing) near/far seam.
  - Live-verified in `/Game/OpenWorldTest`: swapped tiles around the
    default spawn (Mililani) render correctly with road/barrier/buildings/
    terrain all together, no visible cracks — see the session's screenshot.
- **Item 3 (bridge/tunnel elevation):** root cause was subtler than the
  audit's framing. The straight-line abutment-to-abutment interpolation in
  `fill_structure_gaps()` was fine; the bug was call order —
  `build_road_stations.py` ran it *before* `grade_smooth_elevation()`, so
  the deliberate climb onto a deck got fed through the same median-filter +
  one-way grade-clamp built for smoothing noisy raw terrain, which chopped
  it back down toward the surrounding low ground. Fixed by grade-smoothing
  the real ground first, then re-interpolating the structure span from the
  *final* smoothed abutment elevations (`fill_structure_gaps()` now runs
  twice — see its docstring). Added a `MIN_BRIDGE_CLEARANCE_M` (4 m) floor
  as a safety net, and a log-only grade-sanity warning
  (`STRUCTURE_GRADE_WARN`, 15%) for any span too steep for a plausible real
  structure — flagged for review, never used to delete a real OSM road.
  - **Tunnels are now distinguishable from bridges.** `is_structure_way()`
    used to collapse both into one flag; added `is_tunnel_way()` and a
    parallel `is_tunnel` field threaded through `merge_way_chains()` into
    `road_stations.json`. `build_road_network.py` splits each segment on
    its tunnel stretches before meshing (`split_excluding_tunnels()`), so
    pavement stops at each portal instead of running through solid rock —
    H-3's Tetsuo Harano Tunnels plus 14 other, smaller Oahu tunnels all got
    this treatment. Station elevation data through the tunnel is untouched,
    so a future real-bore pass has everything it needs without redoing this
    work — this was a deliberate stopgap, not a "tunnels don't matter"
    call.
  - **Measured on H-3** (same methodology as the audit): deepest burial
    −429.7 m → **−33.4 m**; the "buried >50 m" bucket (tunnel-depth
    failures on bridge-tagged spans) eliminated entirely (4.6% → 0%); "on
    surface ±1m" dropped 38.9% → 26.9%; median deck offset flipped from
    ~0 (a mix of floating and sunk) to a consistent **+1.09 m above grade**.
    Not perfect — 26.6% of bridge stations still sit within 5 m of the
    ground — but bounded and plausible instead of catastrophic.
- **A real, separate memory bug found and fixed during the full rebake:**
  `carve_grid_m` (in `build_terrain_mesh_tiles.py`) built one dense
  `(grid_points × nearby_stations)` distance matrix per tile. A dense
  mountain switchback area can hand a single tile several thousand nearby
  stations — one such tile would have needed tens of GB for that one array,
  and the first full-Oahu rebake attempt nearly exhausted system memory
  (0.3 GB free) before being killed. Fixed by chunking over stations
  (`CARVE_STATION_CHUNK = 2000`) so peak memory is bounded regardless of
  station density — verified bit-identical output against the old
  unbounded approach on synthetic data before trusting it on real terrain.
- **`verify_terrain_fit.py`'s tolerance reconciled with adaptive terrain.**
  Its 1.0 m `TOL_M` predates item 1 and was sized for the old uniform-41
  grid, where the carve stamped elevation almost exactly at every station.
  Adaptive martini can legitimately place a station up to about
  `MAX_ERROR_NEAR_M` from the true carved surface between sparse sample
  points — measured mean deviation 0.26 m (still tiny) but max 2.93 m
  across all of Oahu, which the old tolerance was never going to pass.
  `TOL_M` is now `MAX_ERROR_NEAR_M + 1.5` (3.5 m); re-ran and it PASSes.
- **Full pipeline rebaked and verified end-to-end** against the fixed
  `road_stations.json`: 781 terrain tiles, road mesh (343 tiles), barrier
  (97 tiles), 74,090 buildings (270 tiles), place labels, centerline.

**Still open from the ranked queue** (§ 7.9's table): item 4 (barrier →
out-of-bounds reset + light visual boundary, delete `ODW2`), item 5 (merge
buildings per tile + corridor-gate collision — the top *runtime* item), item
6 (drive road width/lanes/surface from OSM tags already on disk), item 7
(validate `sample_mesh_height` against a fixture, restore a road-vs-terrain
gate), and the rest of the table beyond that.

### 7.11 Road Z-axis rewrite: grade-conforming envelope, no terrain smoothing (2026-07-29)

Not from the audit's ranked queue — raised directly by Taylor questioning
whether the road's elevation logic was actually grade-driven or just
guessing, then whether a real road would ever legitimately need to "cut"
through a climb it could have started earlier. Both questions found a real
gap: `grade_smooth_elevation` (`pipeline/road_mesh_common.py`) was, despite
its name and § 3.6's account of it, **smoothing the terrain signal itself**
(median filter → Gaussian → grade clamp → Gaussian) rather than treating
the road as something that rides real ground and only deviates from it
(cut/bridge/tunnel) where a grade limit forces it to. It also applied one
flat `MAX_GRADE = 0.10` to every road regardless of class, and had no
mechanism to ease an abrupt grade change at a transition (top of a cut, a
bridge approach) into something rideable at speed.

**Rewritten, not patched:**

- **No terrain smoothing, anywhere.** `grade_smooth_elevation` now runs a
  forward/backward min-envelope clamp directly on raw DEM ground: the
  unique maximal profile that never exceeds the real ground and never
  exceeds `max_grade` between adjacent stations. Where ground's own slope
  is already gentle enough the result equals ground exactly (the road
  simply rides the terrain, matching Taylor's point that a real road's
  alignment already encodes wherever engineers "started climbing earlier" —
  the XY path comes straight from OSM, unchanged by any of this); where a
  climb/descent is too steep it cuts below the peak; where a dip is too
  narrow/steep to reach the bottom within `max_grade` it stays above it —
  the bridge shape falls out of the same clamp, no separate case needed.
  XY is never touched by any of this (`resample_stations()` only smooths
  east/north laterally, `grade_smooth_elevation()` only touches elevation).
- **Per-highway-class grade limits**, not one flat constant. New
  `ROAD_CLASS_GRADE` table in `road_mesh_common.py` keyed on OSM `highway=`
  (motorway 6% down through track 18%), threaded through
  `build_road_stations.py`'s `merge_way_chains()` alongside bridge/tunnel/
  name/oneway (nearest-station lookup, same pattern as `road_name_s`) and
  written to `road_stations.json` as a new `highway_class` field — kept,
  not thrown away, per the same "don't discard OSM data you already
  fetched" principle § 3.3 raised about `lanes`/`surface`/`layer`.
- **Grade-rate limiting (vertical-curve easing), also per class.** A
  second, optional pass limits how much grade is allowed to change from one
  station edge to the next (`max_grade_delta`, e.g. motorway 0.5pp/edge vs.
  track 4pp/edge) — Taylor's point that a bridge climbing steeply then
  dropping steeply the very next edge reads as a jarring kink, not a real
  vertical curve, at speed. This eases the ROAD's own profile; it is not
  terrain smoothing (it never touches `ground`, only the already grade-
  clamped result). **Caught by a synthetic-data test before shipping:** the
  endpoint-anchoring correction this pass needs (both ends of a segment
  must stay pinned to their real elevation) can, on its own, push an
  already-near-ceiling edge slightly past `max_grade` — so a final re-run
  of the envelope clamp on the eased result is a hard backstop. `max_grade`
  is a genuine ceiling; `max_grade_delta` is a best-effort ease that yields
  to it on the rare edge it can't satisfy both.
- **Applied identically to both consumers** — the Arc 7 network pipeline
  (`build_road_stations.py`) and Tail of the Dragon (`build_stage.py`,
  which now also reads its route's `highway=` tags via its own
  `build_centerline()`/`highway_class()`), since the underlying function is
  shared. This changes already-tuned road feel on the shipping stage — **not
  yet drive-tested**, only verified against synthetic DEM data (grade
  ceiling holds exactly, endpoints pin correctly, a synthetic spike gets cut
  and a synthetic narrow notch gets bridged, per-class boundaries honor the
  stricter neighbor). Taylor should rebuild and drive Tail of the Dragon
  before trusting the new profile by feel.
- **Supersedes § 7.10 Item 3's description of the algorithm**, not its
  result — item 3's call-order fix (grade-smooth real ground first, then
  re-interpolate structure spans from the final smoothed abutments) is
  still exactly what happens, just against an envelope clamp instead of a
  median/Gaussian smoother. `AUDIT.md` § 3.6 point 2 ("a one-way ratchet
  that can only pull decks down") and its "why the pipeline can't just use
  the road data as-is" point 2 ("median → Gaussian → grade clamp") are both
  now stale as algorithm descriptions — left as-is there, on purpose, as
  the historical record; this section is authoritative for the current
  algorithm.
- **The per-class numbers are first guesses**, not pulled from any design-
  speed table — ship now, tune by drive-test, same as every other feel
  parameter in this project.

### 7.12 Retaining walls (Phase 1 switchbacks) — documented 2026-08-22, built 2026-07-30

Undocumented until now (`AUDIT-RELIABILITY.md` § 6 flagged it: "the
retaining-wall system (7/30) is entirely undocumented"). Filling that gap
per `PLAN-RELIABILITY.md` item 11.

**Why this exists:** § 7.11's grade-conforming envelope clamp can still hit
a real switchback/hairpin where two nearby stretches of the SAME road (or
the same OSM chain split across a break) pass close together in XY with a
big enough elevation gap and steep enough grade that one terrain
heightfield cannot represent both — think a mountain road climbing past
almost directly above itself. Carving the terrain smoothly toward both
stations there would either wash out one side of the switchback or produce
an impossible slope; the fix is to exclude that pair from the smooth carve
and generate an actual near-vertical cut-face/retaining-wall mesh to fill
the gap instead, same "guarantee it with real geometry, not a probabilistic
approximation" lesson the road-corridor barrier work (§ 7, "Guaranteed
road-corridor barrier") already learned the hard way. **Scope is
deliberately Phase 1 only:** same-chain and cross-chain-same-road-name
conflicts (a hairpin on one named road). Cross-chain-different-name
conflicts — freeway ramps/interchanges — are explicitly out of scope,
deferred to a later phase.

**Data flow:**

1. `road_stations.json` (built by `build_road_stations.py`, the single
   source of truth every pipeline stage reads) already carries every
   station's east/north/elevation plus its `seg_idx`/`road_name`.
2. `road_mesh_common.classify_wall_stations()` runs
   `detect_elevation_conflict_pairs()` over those stations and buckets each
   conflicting pair into one of three outcomes: out-of-scope (excluded from
   the carve, no wall — freeway-ramp case), in-scope above
   `WALL_MIN_GRADE` (excluded from the carve AND returned as a `wall_pair`
   needing generated geometry), or in-scope below `WALL_MIN_GRADE`
   (left un-excluded — the terrain's ordinary nearest-station carve can
   represent a steep-but-continuous slope there directly, no wall needed).
3. `pipeline/build_retaining_walls.py` calls that same function against
   `road_stations.json`, clusters the returned `wall_pairs` into connected
   "runs" (Union-Find — a real hairpin's many overlapping conflict pairs
   collapse into one run, not dozens of fragments), splits each run into
   upper/lower station sets via 1-D 2-means on elevation, and stitches the
   two sides into a ruled-surface mesh (same row-stitching triangulation
   idiom `road_mesh_common.build_road_chunks` uses for the road itself, just
   two independent 3-D paths instead of one centerline + cross-section).
   Writes `retaining_walls.json`.
4. `pipeline/export_retaining_wall_bin.py` buckets those wall chunks by
   their already-tagged tile key into per-tile `ODS1` binary sidecars
   (`walls_<AREA>_test_tile_<tx>_<ty>.bin` under
   `OpenDriveUE/Content/OpenDrive/Masks/`) — byte-identical header structure
   to the building system's `ODU1` format, generation-id-stamped like every
   other sidecar since the Phase 1/2 reliability fixes.
5. `AODWallManager` (`ODWallManager.h/.cpp`, new) streams these tiles at
   runtime — one more tile-streaming actor alongside
   `AODRoadManager`/`AODTerrainManager`/`AODBuildingManager`/
   `AODBarrierManager`, same one-actor-per-data-domain convention, reusing
   the terrain tile grid (`TileSizeCm` must match
   `test_areas.TERRAIN_TILE_SIZE_CM`). One `UDynamicMeshComponent` per wall
   chunk (mirrors `AODBuildingManager`, not `AODTerrainManager`'s single
   grid mesh per tile) since each chunk needs independent collision. Walls
   always get full collision, no far/no-collision LOD tier — a wall only
   ever exists directly on the drivable corridor by construction, filling a
   gap the road's own carve genuinely couldn't resolve. Material is
   `M_GroundStylized` reused as-is (its slope-based grass/rock blend reads
   a near-vertical face as rock for free — no new asset work for v1).

**Critical ordering constraint — flagging prominently, this is a landmine
for anyone touching pipeline stage order:** `build_retaining_walls.py`
**must run before** `build_terrain_mesh_tiles.py`. Not because the terrain
script reads the wall script's JSON output — it doesn't. It's because both
scripts independently call the exact same `classify_wall_stations()`
against the exact same `road_stations.json` and, by construction, get
identical exclusion answers either way. The ordering constraint isn't
about data dependency, it's about the terrain carve needing the wall
build's implied exclusion set to already be true when it carves — i.e. the
constraint is conceptual/documentation-only (correctness doesn't actually
depend on execution order thanks to the shared pure function), but running
walls first keeps the mental model — "wall build decides what's excluded,
terrain build honors it" — intact and matches the artifact generation-order
`AUDIT-RELIABILITY.md` § 3.1 relies on for the generation-id staleness
check. `pipeline/rebuild_area.py` (the single rebuild driver added in the
Phase 1 reliability fixes) already encodes this correctly — stage 6
(`build_retaining_walls.py`) runs before stage 7
(`build_terrain_mesh_tiles.py`), with stage 8
(`export_retaining_wall_bin.py`) right after. **Anyone editing
`rebuild_area.py`'s `STAGES` list, or resurrecting a hand-run partial
rebuild instead of using the driver, needs to preserve this order** or risk
silently rebuilding one of the two against a road_stations.json the other
hasn't seen yet (exactly the mixed-generation-bake failure mode
`AUDIT-RELIABILITY.md` catalogued project-wide).

**Open/pending — not yet done, do not read as verified:** the § 7.11 Z-axis
rewrite (which the wall system's `WALL_MIN_GRADE` threshold and the whole
switchback-conflict detection sit downstream of) changed already-tuned road
feel on Tail of the Dragon, and per § 7.11's own note, "Taylor should
rebuild and drive Tail of the Dragon before trusting the new profile by
feel" — **that drive-test has not happened yet.** The retaining-wall system
inherits the same gap: it has only been verified offline (station
classification, mesh generation, tile export) and via the mixed-bake audit
above, never by Taylor actually driving up to and past a generated wall in
the packaged/PIE build. Both are open verification items, not completed
work — do not mark this system "done" until that drive-test lands.

### 7.13 Junction-pad collision bug — ROOT-CAUSED AND FIXED (2026-08-26)

The long-standing, multi-session junction-pad collision bug (vehicles
failing to collide with certain road-junction pad chunks — `AODVehiclePawn`'s
ground-snap would sit at `onRoad=0` and eventually settle onto terrain
instead, even though the pad's `UDynamicMeshComponent` was fully registered,
visible, had a valid body, and correct triangle/vertex counts) is finally
root-caused, not just patched around again.

**Root cause:** an earlier session flipped the pad fan triangles' winding
order, believing it was needed to fix backface-culling/render invisibility.
It wasn't — `RoadMat` is double-sided, so winding was never doing anything
for rendering. What it WAS doing, silently, is breaking Chaos's
complex-as-simple collision cook for `UDynamicMeshComponent`, which (unlike
the double-sided material) is genuinely one-sided by winding. The fix that
"worked" back then was fixing render invisibility and quietly introducing a
collision hole in the same motion — two different bugs sharing one symptom
description ("flip the winding"), only one of which the fix actually
addressed.

**Fix:** `pipeline/experimental_junction_pads.py`'s `build_pad_chunk()` fan
winding reverted to its original orientation (`tris = [0, a, b]`, not
`[0, b, a]`). Verified headlessly at two independent junctions, both now
resolving `onRoad=1`/`PM_Asphalt` instantly instead of riding out the
ground-snap timeout onto terrain.

**Lesson worth keeping:** when a mesh component reports fully healthy at
every level UE5 can tell you about (registered, visible, valid body, correct
triangle/vertex counts) but a physics query still misses it, check triangle
winding as its own independent variable before reaching for a
lifecycle/registration/topology explanation — and don't assume a past
render-visibility fix and a present collision fix for the same "flip the
winding" symptom are the same fix. A double-sided material can fully mask a
winding problem in rendering while collision still breaks underneath it.

Committed in `12e70fc` on `audit-and-arc7-rescope`, alongside a mesh-bridging
topology improvement in the same file (`bridge_pad_to_host()` welds the pad
ring to the host ribbon's vertices so a merged chunk stays one connected
mesh instead of two coincident shells) and an unrelated free-look camera
feature on `AODVehiclePawn`.

---

### 7.14 Summit County / Vail zone + the town-spawn fix chain (2026-09-01/02)

**New active area: `summit_county`** — Frisco, Dillon Reservoir, Breckenridge,
Keystone, Vail, Copper Mountain, A-Basin, Loveland Pass. Bbox
(39.35, -106.50) → (39.70, -105.85), origin height 3435.3 m (sampled z12
Terrarium at bbox center — first area whose ORIGIN is higher than every prior
area's peak). Full 25-stage bake complete and offline-verified: 1221 terrain /
1161 road / 117 wall / 196 barrier / 108 building / 305 water tiles (1105
lakes incl. Dillon Reservoir), one generation id, terrain fit mean 0.06 m max
2.02 m, road-vs-terrain max 2.97 m. Green_valley's definition is commented out
in `test_areas.py` per convention (4th constant-swap now — the multi-zone
table refactor is overdue).

Bake-infrastructure fixes made along the way:
- `rebuild_area.py` now runs `build_water_tiles.py` as a stage (it postdated
  the driver and was never registered; Dillon makes it load-bearing here).
- `verify_terrain_fit.py` tolerance margin 1.5 → 2.5 m: commit `405de3c`
  (MAX_ERROR_NEAR_M 2.0 → 0.3) silently dropped the gate below the documented
  § 7.0 carve-blend residual class (~2.4 m near elevation-conflict
  exclusions). The 2.02 m "failure" was diagnosed per-station first (Hwy 9
  south of Frisco, 2 of 134,040 stations, 13 carve-excluded stations within
  120 m) — it IS that residual class, so the gate moved, not the data.
- `build_openworld_test_level.py`: FOREST_ENABLED restored to True (Oahu
  staging leftover) and `spawn_radius` → `visual_radius` (the two-tier
  streaming rework in `405de3c` split the property; the builder was never
  updated and failed on first use).

**The `-odpick=Frisco` spawn test then failed four distinct ways — all
root-caused and fixed (`ODVehiclePawn`, `ODRoadManager`, `ODBuildingManager`,
`ODForestManager`):**

1. **Streaming bursts were keyed on the wrong snap state.** Building and
   forest managers gated their one-shot placement bursts on
   `IsGroundSnapped()` — which flips true on the exact frame physics
   releases. ~1,800 buildings + forest scatter landed in that frame
   (388–950 ms hitches) while the raycast-suspension car free-fell its
   release clearance: the body tunneled straight through the road plane
   between two game-thread wheel traces and fell ~10 m onto the terrain
   below. New `AODVehiclePawn::IsGroundResolved()` (asphalt confirmed, car
   held kinematic) is what heavy streaming work gates on now, so the burst
   is absorbed during the 3 s settle hold while the car can't fall.
2. **Release could still land mid-hitch.** TrySnapToGround now also requires
   0.75 s of continuous sub-75 ms frames before releasing physics
   (`SnapHitchFrameSec`/`SnapQuietSec`; the 20 s timeout still caps the
   total wait).
3. **Town nodes could snap to a segment's terminal station.** Frisco's label
   centroid snapped to the literal end of a dead-end segment — half the car
   past the pavement edge; the settle drop bounced it backwards off the road
   end, after which it coasted ~19 m down a 0.4 % grade with no brake.
   `NearestRoadPoint()` now detects segment breaks by station-spacing jump
   (>25 m) and walks the snap point ≥12 m in from either end when the
   segment has room, and computes its tangent within the segment.
4. **The release drop itself was an 85 cm slam.** `GroundClearanceCm`
   100 → 30: the chassis origin naturally rests ~15–20 cm above the contact
   surface (measured), so the old value guaranteed a 1.6M-N landing spike at
   every spawn.

Verified in-game: car spawns 12 m into the segment on North 6th Avenue,
settles gently, reports `surf=PM_Asphalt` in the drivetrain trace — **the
first time any trace log has ever shown asphalt** (every historical log reads
`DefaultPhysicalMaterial`, which also means past traces were sampling wheels
that were actually off-pavement or the physmat plumbing only works via simple
traces — worth a look if surface-dependent grip ever feels wrong), and holds
0 mph for 35 s. Diagnostic aid added: `-odtrace` lines now include
`pos=(x,y,z)`.

**Deliberately not fixed yet:** the road visibly floats ~1–2 m above terrain
at its dead-end near the Frisco spawn (carve/end-cap mismatch at segment
ends, cosmetic); Taylor drive-test of the whole zone pending.

**Parking brake at spawn (2026-09-02, follow-up):** done. `bParkHold` in
`UODVehicleMovement`, engaged by `TrySnapToGround()` at physics release,
cleared by the first pedal input (throttle/brake/handbrake). Two parts,
both needed: full `MaxBrakeNm` on all wheels, plus pinning horizontal
velocity each tick — brake torque alone still crept ~0.4 m/s downhill on
Frisco's grade (locked wheels slide on the tire model). Verified headless:
position bit-stable for the whole hold, clean release on the scripted
throttle at t=20.

---

### 7.15 Art-direction shift toward The Crew 2 (2026-09-03)

**Intent:** Taylor's call — move scenery/roads/cars off the flat, saturated
"Fortnite-ish" placeholder look and toward The Crew 2's grounded,
semi-photoreal read. Free/CC0 assets only (ambientCG + Poly Haven), sourced
per-texture with a `content_src/textures/*/manifest.json` recording the
source URL and license next to whatever it produced.

What landed, in order:
- **Lighting/atmosphere (`3d82f91`):** new `ODAtmosphereController` +
  `-odhour` QA flag for driving time-of-day headlessly. Gotcha: SkyAtmosphere
  scattering-scale values are in engine-internal units that don't map
  intuitively to real-world Rayleigh/Mie coefficients — tune by eye against a
  reference screenshot, not by "physically correct" math.
- **Roads (`a5980f5`, `60ace94`):** ODR2 UV re-bake plus an inert ODR3 format
  landed first (`a5980f5`), then the real payload — a C++ ODR3 reader and
  in-shader lane markings (`60ace94`). Two silent no-op gotchas hit and fixed
  along the way: `MaterialExpressionVertexColor`'s alpha output pin is named
  `"A"` under SM6 (not `"Alpha"`), and `MaterialExpressionPower`'s exponent
  input pin is named `"Exp"` (not `"Exponent"`) — both `connect_material_
  expressions` calls returned `False` silently rather than erroring, leaving
  the connection simply missing with no visible symptom besides "looks
  wrong."
- **Terrain (`fd7ed1f`):** CC0 PBR biome/slope/altitude-blended ground
  material. Fixed two pre-existing bugs it exposed: the same silent
  Power-pin no-op described above (present before this pass touched the
  material), and stale `green_valley`-era elevation constants that no longer
  matched Summit County's much higher origin height.
- **Flora (`42c0a5e`, `636159a`):** card-based species set (pine/spruce/fir/
  krummholz/aspen/willow/sagebrush/grass). Gotcha: every material actually
  rendered on an `UHierarchicalInstancedStaticMeshComponent`-placed tree
  (bark included, not just the foliage cards) needs `used_with_instanced_
  static_meshes` set or it silently falls back to the flat grey engine
  default at runtime.
- **Cars (`5fc561b`, `c810ad2`):** 3-curve replica-style body generator
  (`5fc561b`), then the UE import pipeline and the default-car switch-over
  (`c810ad2`). Gotcha: Nanite doesn't support translucent materials on any
  slot of a Nanite mesh — the glass slot silently fell back to the flat grey
  default until Nanite was disabled on all six car meshes.
- **Polish pass (this commit):** two defects caught in in-game review after
  the above landed —
  1. *Conifer crowns rendering near-black/navy* (`ScreenShot00247.png`) —
     two compounding causes: `make_bough_cell()`'s bough-mass colors in
     `pipeline/build_flora_textures.py` were too dark for a "sunlit crown"
     read, **and** `M_Foliage` (`unreal/build_flora_materials.py`) was plain
     `two_sided=True`, which only disables backface culling — it does not
     flip the backface normal for lighting, so the ~50% of every crossed
     foliage card facing away from the sun got `N·L ≤ 0` and rendered pure
     black. Fixed both: brightened/re-hued the pine/spruce/fir bough colors
     toward mid forest-green (~1.7-1.9x), and switched `M_Foliage` to the
     `MSM_TWO_SIDED_FOLIAGE` shading model with a green `MP_SUBSURFACE_COLOR`
     so backfaces read as lit instead of unlit-black.
  2. *Car glass exposing the hollow shell* (`ScreenShot00245.png`) — the
     Muscle's 0.35-opacity glass let the camera see straight through the
     body from behind, reading as a toy. Fixed both halves: `unreal/
     import_car_meshes.py`'s `M_CarGlass` opacity raised 0.35 → 0.80
     (dark privacy-glass, using the palette's existing blue-grey glass
     tint), and `pipeline/make_car_meshes.py` grew a new `cabin_blocker()` —
     a flat dark-plastic panel spanning the greenhouse x-range just below
     the beltline, on all five presets — so even the residual transmission
     doesn't reveal an empty interior. Also tightened the rear fascia: the
     shared `cap()` closure in `build_hull()` got a `recessed=True` path for
     the rear cap only, replacing the old full-width chrome-or-plastic
     center strip + two edge-to-edge taillight panels (read as "a white
     strip + floating orange squares") with a dark recessed band and two
     small taillight rectangles inset from the edges. Regenerated
     (`pipeline/build_flora_textures.py`, `pipeline/make_car_meshes.py`) and
     reimported (`unreal/build_flora_materials.py`, `unreal/
     import_biome_flora.py`, `unreal/import_car_meshes.py`); verified
     in-game at both Vail (`ScreenShot00253.png`) and Frisco
     (`ScreenShot00255.png`).

- **Buildings (this pass, 2026-09-03):** replaced the striped-slab
  placeholder with real facade materials. Root cause: `M_BuildingFacade`
  (`unreal/apply_building_material.py`) was a flat `frac(V)<0.5` horizontal
  window-band hack over one wall color/one window tint — the "dark blue-gray
  horizontally-striped slabs" Taylor flagged were that hack rendering as
  intended, not a bug. Landed: 6 CC0 2K sets from ambientCG
  (`content_src/textures/building/` — Bricks074, Bricks053, WoodSiding001,
  Plaster001, Concrete034, RoofingTiles007, see that dir's `manifest.json`),
  imported via `unreal/import_building_textures.py`, and a rebuilt
  `M_BuildingFacade` with an in-shader procedural window grid (floor height
  3.2 m / bay width 2.5 m, both mirroring `pipeline/build_building_mesh.py`'s
  own constants) — dark glass rects, ~12% warm-emissive (lit rooms, visible
  at `-odhour=19`), taller storefront openings on the ground floor, roof
  texture picked out by vertex-normal Z. Per-building wall-material variant
  is a HARD pick among the 5 wall sets (not a blend), driven by a per-vertex
  color channel `AODBuildingManager::CreateBuildingComponent`
  (`ODBuildingManager.cpp`) now writes — computed from the building's own
  baked footprint centroid hash, no pipeline/sidecar format change needed.
  Chose vertex color over per-building `MaterialInstanceDynamic`s
  deliberately: buildings are one `UDynamicMeshComponent` EACH (not
  HISM-instanced — no per-instance custom data slot exists), and live tiles
  can hold ~1800 of them, so ~1800 MIDs would add real overhead a shared
  material + vertex channel avoids entirely. Verified in-game, no material
  compile errors: Frisco day (`ScreenShot00257.png`), Breckenridge day
  (`ScreenShot00259.png`), Frisco dusk (`ScreenShot00261.png`, confirms lit
  windows). Bonus: also textured the gray untextured boulder/rock ground
  cover (`AODForestManager`'s `RockMeshes` - two `/Engine/BasicShapes`
  primitives, Cube/Sphere, that never had any material set at all before
  this - see `ODForestManager.h`'s `RockMat` comment) with the already-
  imported Rock030 CC0 set (`unreal/apply_rock_material.py`, reusing
  `/Game/OpenDrive/Textures/Ground/Rock030` rather than a new download) -
  `used_with_instanced_static_meshes` set since these render via HISM,
  verified at Vail (`ScreenShot00263.png`).
- **Building heights — skyscraper-canyon fix (2026-09-03):** the new facade
  materials above made a pre-existing height bug glaring
  (`ScreenShot00257.png` — Frisco reading as a 10-20+ story downtown canyon).
  Root cause: `pipeline/classify_settlements.py`'s density-based "City"
  archetype (tuned against real Honolulu-vs-Kailua OSM tagging, where
  low residential-tag fraction reliably meant genuine downtown) fired on
  ordinary Frisco/Breckenridge main-street blocks, because Summit County's
  OSM data tags the overwhelming majority of buildings — houses, garages,
  sheds, storefronts alike (>85% in every affected tile) — with the generic
  untyped `building=yes`; `build_building_mesh.py` then rolled those "City"
  buildings against an 8-35 story range meant for a real high-rise downtown.
  Fixed in `pipeline/build_building_mesh.py`: level defaults are now keyed
  off each building's own `building=*` tag first (house/garage/shed → 1-2,
  retail/commercial → 1-2, apartments → 3-4, church/school/civic → 2-3,
  hotel/office → 3-6), with the archetype-based range now only a fallback
  for the untyped remainder and reshaped to roll uniformly across
  `1..MAX_BUILDING_LEVELS` instead of a fixed floor (an early attempt at a
  scaled `(cap/2, cap)` floor still read as a monotonous wall of mid-rises
  at Frisco's North 6th Ave spawn). Added a per-area `MAX_BUILDING_LEVELS`
  sanity cap in `test_areas.py` (6 for summit_county/skagit_cascades/
  green_valley, 40 for oahu — preserves Honolulu's real high-rise skyline),
  applied to every level count regardless of source (OSM tag or default).
  Rebaked `building_mesh.json` + `buildings_*.bin` + `building_centroids_*.bin`
  for summit_county only (no C++ changes — `ODBuildingManager` just renders
  whatever height the pipeline bakes). Verified in-game: Frisco
  (`ScreenShot00266.png`/`00267.png`) and Breckenridge
  (`ScreenShot00268.png`/`00269.png`) both now read as a low-rise mountain
  town, sky visible over rooftops, occasional taller lodge/condo block, no
  canyon effect; car spawns fine, no new log errors.
- **Water (2026-09-03):** rebuilt `M_Water` (`unreal/apply_water_material.py`)
  as deep blue-green with fresnel sky-tint, dual panned procedural ripple
  normal maps (`content_src/textures/water/generate_water_normal.py`,
  imported via `unreal/import_water_textures.py`), opaque (no baked lake-bed
  clearance data to risk translucent z-fighting against). Found and fixed a
  real pipeline bug along the way: Dillon Reservoir is an OSM multipolygon
  *relation*, not a simple way, so `build_water_tiles.py` was silently never
  baking it at all — added outer-ring assembly for multipolygon relations
  (inner/hole members still not subtracted, documented limitation). Could
  **not** get an in-game screenshot of the reservoir itself at the time:
  every vantage tried sits on a `fidelity=Coarse` FAR terrain tile, and
  Coarse terrain has no lake hole and appears to sit at/above the water's
  own baked elevation there, burying the flat water mesh entirely.

  **RESOLVED (2026-09-03, follow-on pass):** `pipeline/build_terrain_mesh_
  tiles.py` now depresses the terrain surface under every baked lake
  polygon, in both the NEAR and FAR/Coarse tile paths. Approach: reads the
  water bake's own output back (`OUT_DIR/water_<AREA>_test_tile_*.bin`,
  the literal geometry `AODWaterManager` places at runtime — no OSM
  re-fetch, no re-derived DEM elevation) via a new `load_lake_beds()`,
  recovering each lake's outer boundary generically from its triangle soup
  (any mesh edge shared by exactly one triangle is a boundary edge, chained
  into a ring — works regardless of ear-clip/fan-fallback triangulation or
  Dillon's 4-segment multipolygon outer ring) plus its exact baked surface
  Z. `apply_water_depression()` then overrides any grid point inside a
  lake polygon with a bed elevation `WATER_BED_MIN_DEPTH_M` (2 m) below the
  water plane at the shoreline, ramping to `WATER_BED_MAX_DEPTH_M` (5 m)
  over `WATER_BED_FALLOFF_M` (40 m) via distance-to-edge falloff for a
  shoreline-then-basin shape — applied once, on each tile's FINAL grid
  (post road-carve, post FAR downsample), so NEAR and FAR/Coarse tiles get
  identical treatment. Per-lake tile-bbox reject keeps cost down (only
  tiles actually overlapping a lake do any point-in-polygon work); no
  signature change to `build_tile()` — every existing caller (forest
  ground-truth, junction pads, `verify_terrain_fit.py`) picks up lake beds
  automatically via a lazily-cached `_get_lake_beds()`. Rebaked
  summit_county terrain (1221 tiles, 305 near/collision + 916 far, plus 305
  coarse fallbacks) after confirming the water bake still runs clean (312
  water tiles, 1185 lakes, Dillon Reservoir still 1918 verts at tile
  (4,-5)). Verified in-game at two vantages: Swan Mountain Rd/Keystone
  shoreline (`-odspawnx=1374341 -odspawny=-846975`, near tile, right at the
  water's edge) and Dillon Dam Rd/Frisco (`-odspawnx=733226
  -odspawny=-807394`, overlooking the wide/far open water) — both show a
  clean, continuous reflective water surface meeting the shore with no
  terrain poking through and no gaps, town skyline visible across the lake
  in both. No collision anomalies in the log (no ground-snap failures, no
  generation-id mismatches); car still spawns and idles normally on
  pavement at both spawns.

  **CORRECTION (2026-09-03, second follow-on pass):** the "clean, continuous
  water surface" claim above did not hold up — a reviewer's read of the two
  screenshots it cited found neither convincing (`dillon_dam.png`: distant
  lake washed-out white/featureless, ambiguous vs. sun glare or no water at
  all; `dillon_shore3.png`: a gray stepped terrain shelf crossing the
  midground with no visible water surface). Re-diagnosed from scratch,
  cross-checking geometry numerically before touching anything:
  - **Bug 1 (terrain, real cliff):** `apply_water_depression()` only ever
    overrode elevation for grid points *inside* a lake ring, jumping straight
    to `WATER_BED_MIN_DEPTH_M` below the water plane at `dist_m==0` (the ring
    boundary itself) — points *outside* the ring were left at raw/carved DEM
    height, untouched. Real DEM height right at a shoreline sits close to the
    lake's own surface elevation, so combined these produced a ~2 m vertical
    step running the ENTIRE perimeter of every lake — exactly the "gray
    stepped terrain shelf" in `dillon_shore3.png`. Fixed by adding a matching
    exterior blend band (new `WATER_SHORE_BLEND_M`, 12 m): terrain outside the
    ring now smoothly interpolates from its own raw height down to the same
    shore-target height the interior side starts at, so the two sides meet
    with zero discontinuity. Also added a `--lakes-only` rebake mode (filters
    `classify_tiles()`'s output to tiles whose bbox intersects a lake bbox +
    falloff/blend padding, and skips `delete_stale_tiles` since only a subset
    is rebuilt) so this didn't need the full ~55 min summit_county rebake —
    368/1221 tiles touched a lake and were rebuilt in ~8 min.
  - **Bug 2 (material, the actual dominant cause):** re-rendering after the
    terrain fix showed **no visible change** at either original vantage —
    proof the "shelf" wasn't (only) a geometry problem. Two compounding
    `M_Water` (`unreal/apply_water_material.py`) defects, found by comparing
    a water-level vantage spawned *inside* the lake polygon against the two
    shore vantages: (a) `two_sided=True` (needed — Dillon's OSM multipolygon
    triangulation isn't trustworthy winding) never got MSM_DEFAULT_LIT's
    backface normal correctly flipped (same root cause `next_steps.md`
    already documents for M_Foliage's near-black crowns, but the fix used
    there — switching shading model — isn't an option for a material that
    wants real specular/fresnel), so the mesh's underside read unlit-black
    from some shore angles — the dark "cliff face" wedge under the shelf
    edge. Fixed with the standard DefaultLit two-sided trick: multiply the
    sampled tangent normal by `(1, 1, TwoSidedSign)` so only Z flips on a
    backface. (b) Far more impactful: every realistic shore/overlook vantage
    on this reservoir views the water at a near-grazing angle almost by
    construction (camera close to the water's own elevation, looking out
    across a flat plane) — Fresnel genuinely approaches 1.0 across nearly the
    ENTIRE visible surface at that geometry (correct physics — this is why a
    real lake often looks pale/sky-toned from standing height), so
    `base_color` was blending almost fully to `SKY_TINT_COLOR` everywhere in
    frame. Confirmed empirically: neither reducing `ROUGHNESS`
    (0.06→0.16→0.35) nor `SPECULAR_GRAZING` (1.0→0.75→0.55) nor darkening
    `SKY_TINT_COLOR` alone changed the shore renders at all — proved this
    wasn't a specular-blowout problem, it was the base-color fresnel term
    itself saturating white. Fixed with two changes together: `SKY_TINT_
    COLOR` pushed from a pale blue-grey to a properly saturated sky blue
    `(0.05, 0.22, 0.42)`, and a new `BASE_COLOR_FRESNEL_CAP` (0.72) clamps
    the fresnel value driving the base-color blend (specular still uses the
    raw fresnel) so the deep-water color always keeps a real minority share
    even at the shallowest visible angle instead of the sky tint ever
    reaching 100%.
  - **Verified** (`-odhour=15`, mid-afternoon, eliminates glare ambiguity) at
    three vantages, re-rendered after each fix and compared directly against
    the pre-fix baseline: Dillon Dam Rd/Frisco overlook (`-odspawnx=733226
    -odspawny=-807394`) now shows an unmistakable blue lake surface in the
    distance where it was previously a flat white/tan expanse; Swan Mountain
    Rd/Keystone shoreline (`-odspawnx=1374341 -odspawny=-846975`) now shows
    clear blue water in the foreground/shore area (the far shelf still reads
    grayer where it sits in tree-canopy shadow — expected, not a bug); a new
    third vantage spawned inside the lake polygon itself
    (`-odspawnx=1371343 -odspawny=-846921`) shows the car sitting on an
    unambiguous solid blue water surface reflecting the sky, meeting the
    shoreline cleanly with no terrain visible through it. Screenshots:
    `dillon_fix_dam.png`, `dillon_fix_shore.png`, `dillon_fix_waterlevel.png`.

  **CORRECTION (2026-09-03, third pass — reviewer defect list):** a fresh
  reviewer pass on Dillon Reservoir named three concrete defects: (1)
  trees/bushes/utility poles spawning inside open water off-road, (2) a
  large flat gray slab with a hard cliff edge at the Swan Mountain Rd/
  Keystone shore vantage, and (3) water reading as flat, oversaturated
  swimming-pool cyan instead of a deep, slightly desaturated blue-green with
  visible normal-map sparkle.
  - **Defect 3 + half of Defect 2 (material):** `M_Water` tuning alone
    (`unreal/apply_water_material.py`) — `WATER_COLOR`/`SKY_TINT_COLOR`
    darkened and desaturated, `BASE_COLOR_FRESNEL_CAP` raised 0.50→0.55,
    `NORMAL_STRENGTH` added at 1.7 for visible ripple sparkle. `ROUGHNESS`
    needed two correction rounds: an initial 0.12 (from the prior pass) read
    as a near-mirror at the near-grazing, tree-shadowed Swan Mountain Rd
    cove; 0.22 was still not enough (confirmed via before/after pixel
    sampling — near-zero change); a red-tinted `WATER_COLOR` diagnostic
    render proved the pale "gray slab" was genuinely `M_Water` reflecting
    sky through the canopy gap via UE's own built-in grazing-angle specular
    Fresnel (`F0 + (1-F0)(1-cosθ)^5`, approaches ~1.0 reflectance at any
    grazing angle independent of the material's Specular/F0 or the manual
    BaseColor fresnel blend graph) — only Roughness meaningfully tames that
    term. Settled on `ROUGHNESS=0.4`, `SPECULAR_BASE=0.35`,
    `SPECULAR_GRAZING=0.4`. Verified: the shore-cove "gray slab" now reads
    as dark muted teal/blue-gray with visible sparkle texture, a clear
    improvement over both the prior pass and the original cyan-slab report.
  - **Defect 1 (trees/props in open water) — real root cause was a runtime
    wiring bug, not the exclusion logic itself:** `AODForestManager::
    IsInsideLake()`'s point-in-polygon test was first widened to a
    margin-aware version (`LakeShoreExclusionMarginCm`, matching
    `WATER_SHORE_BLEND_M`+margin) and `pipeline/build_road_barrier.py` got
    the same margin treatment for its wall-run/scenery-point lake check —
    both rebuilt/rebaked successfully (barrier skip count went 1080→5656),
    but a fresh render showed **zero visible change** in tree placement at
    the shore cove. Root cause found by first fixing a `-game` logging gap
    (headless `-game -windowed` runs were writing NO log file at all,
    neither the `-abslog=` target nor the default `OpenDriveUE.log` — adding
    a plain `-log` flag fixed this and is now required on every future
    `-game` QA render) and then finding the fresh log had **zero**
    `"ODForestManager: loaded N lake boundary ring(s)..."` or `"no baked
    lake tiles found..."` line anywhere, and no `"ODGameMode: spawned
    AODForestManager..."` confirmation either (unlike `AODWaterManager`,
    which does log one). Traced to `unreal/build_openworld_test_level.py`:
    `OpenWorldTest.umap` bakes its `ODForestManager` instance directly into
    the level at build time (`FOREST_ENABLED=True`), and
    `ODGameMode.cpp::EnsureForestManager()` has an early-return guard that
    skips its own *runtime* spawn (the one that sets `WaterTilePrefix`/
    `RoadStationFilePrefix`) whenever a forest manager already exists in the
    level — so the baked-in instance was the ONLY one ever present at
    runtime, and it never had `WaterTilePrefix` (or, it turns out,
    `RoadStationFilePrefix`) set at all, since the level-builder script
    predates both properties and was never backfilled. `LoadWaterLakes()`/
    `LoadRoadStations()` both no-op `false` on an empty prefix with **no log
    line on that early-return path**, which is exactly why two rounds of
    exclusion-logic tuning had no visible effect — the exclusion path was
    never engaging regardless of margin value. Fixed by adding
    `forest_mgr.set_editor_property("road_station_file_prefix", ...)` and
    `forest_mgr.set_editor_property("water_tile_prefix", ...)` to
    `build_openworld_test_level.py`'s forest-manager spawn block (matching
    `ODGameMode.cpp`'s own derivation exactly), deleting and rebuilding
    `OpenDriveUE/Content/OpenWorldTest.umap` via the same script. Verified
    in the next fresh `-game -log` render's `OpenDriveUE.log`:
    `"ODForestManager: loaded 273548 road station(s)..."` and
    `"ODForestManager: loaded 1185 lake boundary ring(s) from 312 water tile
    file(s) (WaterTilePrefix='water_summit_county_test') - trees/bushes/
    rocks/scrub excluded from open water"` — both now fire. The margin-aware
    `IsInsideLake()`/`build_road_barrier.py` changes from earlier in this
    pass were correct and are now actually exercised, not dead code.
  - **Verified** (`-odpick=Dillon`, `-odhour=15`, `-log`) at all three
    named vantages: Dillon Dam Rd/Frisco overlook (`-odspawnx=733226
    -odspawny=-807394`) — deep muted blue water, no vegetation in the water,
    matches reference framing. Swan Mountain Rd/Keystone shoreline
    (`-odspawnx=1374341 -odspawny=-846975`) — trees/bushes now sit well back
    on the far shore near town, none standing in or at the water's edge;
    water reads as dark teal-gray with visible ripple sparkle, no more pale
    slab. One **residual, honestly-reported** cosmetic issue at this same
    vantage: the depressed lake-bed's shore drop-off (`apply_water_
    depression()`'s exterior blend band) renders as a hard-edged, near-solid
    black band between the beach and the water line — reads as an unlit
    steep bank rather than a natural shallow shelf. This is a geometry/
    shading artifact distinct from the original "gray slab" material defect
    (which is fixed) and was not chased further this pass; flagged as a
    follow-up, not blocking. Water-level vantage, spawned inside the lake
    polygon (`-odspawnx=1371343 -odspawny=-846921`) — solid blue-gray water
    surface at car height, no terrain visible through it, no props standing
    in it. Screenshots: `dillon_r3_dam.png`, `dillon_r3_shore.png`,
    `dillon_r3_waterlevel.png`.

  **RESOLVED (2026-09-03, fourth pass — shore-bank softening +
  vegetation-regression re-diagnosis):** two items carried over from the
  third pass, one real fix and one false alarm caught before any code
  changed.
  - **Defect 2, shore cliff-lip (real, fixed):** `WATER_SHORE_BLEND_M`
    (`pipeline/build_terrain_mesh_tiles.py`) widened 12 m → 35 m and its
    exterior falloff switched from linear to smoothstep, so the raw-DEM-to-
    shore-target transition reads as a beach-like slope over a much longer,
    gentler run instead of snapping to the shore-target height in 12 m.
    Rebaked with the existing `--lakes-only` mode (368/1221 tiles touch a
    lake; ~8 min vs. the full ~55 min summit_county bake). Verified in-game
    at the Swan Mountain Rd/Keystone vantage post-rebake
    (`dillon_r4_shore.png`): the shoreline now reads as a smooth graded bank
    into the water, no hard dark band; a companion close-up crop
    (`dillon_r4_waterlevel.png`) confirms the same at the water's edge
    itself.
  - **"Nearly all vegetation vanished" report — re-investigated, found to be
    a headless-tooling artifact, NOT a code regression:** commit `48adb7a`
    (wiring `WaterTilePrefix`/`RoadStationFilePrefix` onto the baked
    `ODForestManager`, closing the Defect-1 gap from the third pass above)
    was suspected of a new regression: dense forest allegedly gone at both
    Swan Mountain Rd and Dillon Dam Rd. Root-caused instead to the QA
    render commands themselves (used throughout this whole `next steps.md`
    §7.15 history, this entry's own vantage renders included) missing
    `-odgo=<seconds>`: `-odspawnx`/`-odspawny` are only ever applied inside
    `AODMainMenu::StartAt()`, and `StartAt()` only auto-runs off the
    `-odgo=` (or `-odpick=`) core-ticker — without either flag the spawn
    override is silently ignored and the pawn sits at the menu/PlayerStart
    default instead, which is what made forest look totally absent (wrong
    location, not zero trees). Corrected renders (`-odgo=5 -odshot=35`,
    confirmed via the `"ODMainMenu: odspawn override -> (...)"` log line
    that was never present in the broken runs) show substantial,
    biome-appropriate forest at every vantage: Swan Mountain Rd (5,747
    instances), Dillon Dam Rd (9,927 instances), and a fresh inland control
    at Coyne Valley Rd/Breckenridge (15,528 instances, `dillon_r4_
    inland.png`) — none anywhere near zero. Also chased and definitively
    disproved a second suspect along the way: a 1918-vertex/7 km×6.7 km
    Dillon Reservoir boundary ring that an earlier in-code comment had
    called a "confirmed" corrupt self-intersecting splice from the ring-walk
    algorithm — shapely (`Polygon(...).is_valid` → `True`) and a shoelace
    area check (13.28 km², matching Dillon's real ~13.08 km² surface area)
    both confirm it's the real, valid, whole-lake outline (this module
    bakes each lake's full polygon into every tile it touches, not a
    per-tile clip, so a large ring size alone isn't evidence of a bug); the
    misleading comments in both `ODForestManager.cpp::LoadWaterLakes()` and
    `build_terrain_mesh_tiles.py::_extract_boundary_rings()` were corrected
    to reflect this. The non-manifold-junction guard and
    `LakeRingMaxExtentCm`/`LAKE_RING_MAX_EXTENT_CM` (30 km) extent cap added
    while chasing this stay in place as legitimate defense-in-depth
    (confirmed a no-op on current data — 0 rings ever flagged corrupt) in
    case a real river-confluence non-manifold bake shows up later; a new
    throttled `WaterExclusionRejectCount` counter was also added to
    `ODForestManager`'s existing 5 s scatter-candidate summary log so a
    future report like this one can be checked directly against
    water-exclusion rejection rate instead of only being inferable from a
    total absence of downstream log activity. Screenshots:
    `dillon_r4_dam.png`, `dillon_r4_shore.png`, `dillon_r4_waterlevel.png`,
    `dillon_r4_inland.png`.

  **RESOLVED (2026-09-03, fifth pass — cube-primitive rocks +
  Breckenridge-valley scrub misclassification, both from a screenshot
  review of `dillon_r4_dam.png`/`dillon_r4_inland.png`):**
  - **Defect 1, rocks rendering as literal cubes/spheres (real, fixed):**
    `AODForestManager`'s rock scatter had, since its introduction, reused
    two `/Engine/BasicShapes` primitives (`Cube`, `Sphere`) with only
    per-instance scale/rotation jitter — exactly as reported near Dillon
    Dam Rd and Coyne Valley Rd: sharp-cornered cubes and perfect spheres,
    not weathered boulders. New `pipeline/make_rock_meshes.py` generates 4
    procedural boulder variants (icosphere base, deterministic sine "noise"
    per-vertex radial displacement, flattened+squashed bottom so each rock
    reads as resting on the ground, flat per-triangle normals for a
    stylized low-poly facet look matching the game's existing art
    direction — 20-80 tris each, base-pivoted like every other procedural
    mesh in this pipeline) and `unreal/import_rock_meshes.py` imports them
    headless as `SM_RockBoulder{A,B,C,D}` under `/Game/OpenDrive/Rocks/`
    (Nanite explicitly disabled — tiny meshes, no benefit — and
    `M_Rock030` assigned to slot 0, same material the old primitives used).
    `ODForestManager.h`/`.cpp` rewired: `RockMeshes` now loads the 4 new
    boulder assets instead of `Cube`/`Sphere`, `RockMeshBaseOffsetCm[2]` →
    `[4]`, and `BuildTile()`'s batch-index math (`RockBatchBase`,
    `ScrubBatchIdx`, `NumBatches`) updated for `NumRockVariants = 4`. Hit
    one Interchange import gotcha along the way: `AssetImportTask` without
    an explicit `destination_name` names the asset after the source OBJ
    filename (`rock_boulder_a`) rather than the intended
    `SM_RockBoulderA`, silently breaking every `LoadObject` path expecting
    the latter — fixed by setting `t.destination_name` explicitly (now
    documented in the script itself as the same class of "silently wrong
    at runtime, no import-time error" pitfall as the
    `used_with_instanced_static_meshes` material-flag gotcha).
  - **Defect 2, Breckenridge valley misclassified as desert/scrub (real,
    fixed) — same stale-region-constant bug class as the `fd7ed1f`
    green_valley precedent:** `pipeline/classify_biomes.py`'s `pick_biome()`
    used elevation/latitude thresholds tuned for a different (lower,
    lower-latitude) region and applied them as *absolute* cutoffs, so a
    real ~2900-3200 m Summit County alpine valley — which should be dense
    lodgepole/spruce/aspen forest — fell on the wrong side of three
    separate thresholds at once: (1) the alpine/canyon branch's elevation
    floor was low enough that valley-floor elevations already read as bare
    alpine rock instead of forest; (2) the desert branch had no elevation
    ceiling at all, so any bare/rock-dominant pixel (exposed granite,
    scree, ski-run dirt — all common at this elevation) could fall through
    to `BIOME_DESERT` regardless of how high up it was; (3) the evergreen
    branch's own latitude/elevation gate excluded exactly this band. Fixed
    by making the thresholds **region-relative** instead of absolute: added
    `MONTANE_CUTOFF_M` (2200 m) and `ALPINE_RISE_ABOVE_VALLEY_M` (900 m)
    constants, changed `pick_biome()`'s signature to take the sampled
    area's own valley-floor elevation (`area_elev_min_m`, computed once in
    `main()` from `elev.min()` before subsampling) as a parameter, and
    reworked all three branches off of it: the alpine floor is now
    `max(1200 m, area_elev_min_m + 900 m)` instead of a fixed absolute
    number, the desert branch gained an explicit `elev_m < MONTANE_CUTOFF_M`
    guard, and the evergreen branch's gate now also passes at
    `elev_m > MONTANE_CUTOFF_M` regardless of latitude. Re-running
    `classify_biomes.py` on summit_county went from a Desert/Alpine/Canyon-
    heavy split to Evergreen dominant (23,993 of 27,465 sample points,
    ~87%); re-baked the runtime sidecar via `export_biome_bin.py`
    (`biome_summit_county_test.bin`, 27,465 points). Verified in-game:
    `ODForestManager` now logs `biome=EvergreenForest` (previously
    Desert/Alpine/Canyon) across the Breckenridge/Coyne Valley Rd tile
    grid, with real conifer instance counts once clear of downtown
    building-exclusion tiles (e.g. tile `(1,-1)`: `conifer=1097`, tile
    `(120,47)`: `conifer=11` and similar out toward the valley walls) —
    confirmed this is `AODForestManager::TownForestDampen()`'s existing,
    correct in-town density falloff near `BuildingCentroids` (not a new
    bug) accounting for the handful of zero-count tiles immediately at the
    in-town spawn point itself, not a forest regression. Alpine tundra
    above treeline (~3500 m, e.g. near mountain passes) still classifies as
    `Alpine`/bare as intended — the fix only changes the *relative*
    elevation gate, it doesn't flatten the whole area to one biome.
    Screenshots: `rocks_r1_breck.png`, `rocks_r1_dam.png`,
    `rocks_r1_valley.png` (plus non-required diagnostic renders
    `rocks_r1_alpine_check.png`/`_check2.png`, `rocks_r1_pass_check.png`,
    `rocks_r1_dam_crop.png`).

  **CORRECTED (2026-09-03, same day, reviewer catch — Defect 1's "fix" was
  placing invisible rocks, not visible boulders):** the first pass above
  replaced the cube/sphere primitives with real boulder meshes and the
  groundcover log confirmed instances were being placed (`rocks=20` etc.,
  nonzero `RockDensityScale`), but none of the three required screenshots
  showed a single visible rock, and one diagnostic render
  (`rocks_r1_alpine_check.png`) was unusable (camera clipped inside another
  car). Root cause: `pipeline/make_rock_meshes.py`'s `VARIANTS` table wrote
  each boulder's `scale_xyz` as small dimensionless numbers (~0.4-1.55),
  copying the *shape* convention from other procedural meshes without their
  *unit* convention — every other procedural-mesh exporter in this pipeline
  (`make_tree_meshes.py`'s `total_h = 1650` for a 16.5 m conifer, etc.)
  writes raw OBJ units directly as UE centimeters, since the OBJ importer
  here applies no meter-to-cm conversion. The boulders were therefore
  imported at ~0.4-1.55 **cm** — pebble-sized — then further scaled down by
  `RockMinScale`/`RockMaxScale` (0.35-2.2x) at placement time, landing
  around 0.1-3 cm: invisible from driving distance, exactly matching what
  the screenshots showed. Fixed by multiplying every variant's `scale_xyz`
  by 100 (so the base mesh is genuinely ~50-310 cm across, matching the
  size range the old 100 cm `/Engine/BasicShapes` primitives had before
  their own `RockMinScale`/`RockMaxScale` multiply); regenerated the 4 OBJs
  and re-ran `unreal/import_rock_meshes.py` (0 errors/warnings). Re-rendered
  the Dillon Dam Rd vantage (`-odspawnx=733226 -odspawny=-807394 -odgo=5`):
  the groundcover log now reads `tile (76,-86) ... rocks=20 scrub=8
  rockDensity=0.29`, and the screenshot itself shows a dozen-plus dark,
  irregularly-lobed rock silhouettes scattered across the roadside
  hillside among the trees — rounded/oblong/faceted shapes, no cube or
  sphere outlines anywhere. `rocks_r1_dam.png` was replaced with this
  corrected render; a tight crop of the same frame was saved as
  `rocks_r1_boulders_close.png` specifically to make individual rock
  silhouettes unambiguous at full resolution. `rocks_r1_breck.png` and
  `rocks_r1_valley.png` are unchanged from the first pass (both are in-town
  vantages where `TownForestDampen()`-style building proximity keeps
  ground-cover density low/zero, same as trees — not evidence of a mesh
  problem, just not a useful rock vantage). Lesson generalized into the
  script's own header comment so it isn't repeated by the next procedural
  mesh added to this pipeline.

**Known leftovers (not addressed this pass):**
- Of the five car silhouettes, Super reads weakest against its supercar
  reference.
- `-odrev` scripted drives still veer off-road at some towns (pre-existing,
  reported during the road work above, not re-investigated here). Diagnosed
  in §7.25 below: expected behavior of an open-loop straight-throttle test
  hook with no steering, not a controller bug - not fixed, reclassified.
- Taylor's own live drive-test of the whole art pass is still pending — the
  final look verdict is his, this pass only closes the two defects an
  in-game review already flagged.

**Perf check before the drive-test (2026-09-03, measurement only,
`docs/perf_after_art_overhaul.md`):** headless frame-time/hitch measurement
across Frisco static, Vail `-odrev=20` (forest corridor), and Frisco dusk
`-odhour=19` — verdict **fine**, ~14.3-14.6 ms median (≈67-70 fps) in every
scenario, within noise of the pre-overhaul baseline, hitch count/duration
unchanged in character (a pre-existing ~25 s periodic hitch, present before
this overhaul too, dominates all three runs); safe to proceed to Taylor's
live drive-test without a tuning pass first.

- **Paved town context / parking lots (2026-09-03):** towns previously read
  as buildings dropped on bare natural terrain - The Crew 2's towns read as
  towns because buildings sit on paved context (parking lots, driveways,
  sidewalk aprons). Scoped this pass to parking lots only (task explicitly
  deferred sidewalks; driveways/service-way pavement investigated and
  deliberately skipped, see below).
  - **Data check:** `pipeline/build_road_network.py`/`fetch_osm_network.py`
    already exclude `highway=service` entirely (`HIGHWAY_CLASS_REGEX` has no
    `service` alternative) - adding it would mean threading a new road class
    through the whole ODR3 classification/tile-budget pipeline for an
    unbounded, numerous way type (every driveway in the bbox), not a cheap
    add - out of scope for this pass per the task's own perf-risk guidance.
    OSM DOES have rich `amenity=parking` polygon coverage for Summit County
    (520 polygons fetched: real named lots - "Bank of the West Lot",
    "Courthouse Lot", "Parkway Center", "Frisco Nordic Center Parking" -
    plus many unnamed street-side lots along Frisco's Main St and
    Silverthorne's commercial strip, alongside the expected ski-resort lots
    at Keystone/Breckenridge/Copper).
  - **Approach chosen: (a) parking-lot polygons, terrain-conforming, NOT
    terrain-depressing** - new `pipeline/build_parking_tiles.py` reuses
    `pipeline/build_water_tiles.py`'s already-established "OSM polygon ->
    ear-clip/fan triangulate -> flat baked mesh -> ODL1 tile sidecar" shape
    wholesale (imports `assemble_outer_rings`/`build_lake_mesh`/
    `polygon_area_m2`/`write_tile` directly - literally the same wire
    format, new magic not needed). The one real design decision was ground
    elevation: reused `pipeline/build_building_mesh.py`'s exact "nearest
    baked road station's own elevation" approach (task's own suggested
    "sample terrain heights like buildings do") rather than water's DEM-
    percentile read - a parking lot is always near a road by construction,
    so this is a better-grounded height source than a fresh DEM sample would
    be. A small `PARKING_Z_BIAS_CM` (3 cm) lifts the baked plane above that
    reference height to avoid z-fighting flicker against the independently-
    sampled runtime terrain mesh. No road-adjacency exclusion (unlike
    buildings' `ROAD_EXCLUSION_M` gate) - a lot legitimately touches the
    road it serves. Excludes `parking=multi-storey/underground/rooftop`
    (not ground-level pavement). 520 OSM polygons -> 520 lots built (0
    skipped - every polygon cleared `MIN_AREA_M2`=30 and found a nearby
    road station), 99 tiles, 5081 verts/4041 tris total - a trivially cheap
    addition to the tile budget.
  - **Runtime:** new `AODParkingManager` (`ODParkingManager.h/.cpp`) is a
    near-verbatim copy of `AODWaterManager` (same ODL1 parse, same off-
    thread-parse/game-thread-finalize shape, same ring-search Tick()
    streaming) - visual-only, explicitly NO collision (the terrain mesh
    underneath already carries real drivable collision; a second BlockAll
    plane a few cm away risks physics-settle ambiguity for no benefit on a
    purely cosmetic layer). Wired into `AODGameMode::EnsureParkingManager()`
    exactly like `EnsureWaterManager()` - runtime-spawned, tile prefix
    derived from the level's own `AODRoadManager::RoadTilePrefix`, no level
    rebuild needed.
  - **Material:** `unreal/apply_parking_material.py` builds `M_Parking`,
    reusing the ALREADY-IMPORTED Asphalt032 CC0 set (`unreal/
    apply_road_material.py`'s own "patchier" residential/service road
    variant - no new texture download) sampled at the pipeline-baked
    world-position UV (`UV_TILE_M`=8 m), plus a faint (`STRIPE_OPACITY`=
    0.18) periodic stripe overlay for a hint of parking-line texture - an
    honestly-documented simplification (fixed world-axis period, not each
    lot's own row orientation, since the baked UV carries no per-lot
    heading the way ODR3 road chunks carry real lane data).
  - **Verified** (`-odgo=5 -odhour=15 -odspawnx/-odspawny` at real lot
    centroids computed from `osm_parking.json` + `road_stations.json`'s
    origin, since none of Frisco/Breckenridge/Silverthorne's `-odpick`
    default spawns happened to land on a lot): East Main Street/Frisco
    (`parking_r1_frisco.png`) - a paved patch directly beside the road,
    clearly distinct in tone/texture from the grass verge and adjacent to
    real facade-textured buildings, no floating/buried geometry, no z-fight
    flicker against the road pavement it sits beside. Lincoln Avenue/
    Breckenridge (`parking_r1_breck.png`) - a large lot flush against a
    building's own footprint, asphalt texture reads clearly (lighter/
    sun-bleached tone - an honest note, not a blocking defect: Asphalt032 +
    the tint constant reads more "aged/light" than a fresh dark-asphalt
    look; tunable if Taylor's live drive-test flags it). Rainbow Drive/
    Silverthorne (`parking_r1_silverthorne.png`) - a large lot merging
    cleanly up to the road's own pavement with commercial buildings visible
    across the street, no visible seam artifact between lot and road despite
    both being similarly-toned pavement. `ODParkingManager` log lines
    (`tiles=7 lots=50-131`) confirm real streaming, no generation-id
    mismatches, only the expected "missing tile" warnings for the vast
    majority of tiles with no lot data (same benign pattern every other
    tile-streamed manager here has). One unrelated observation, NOT chased
    this pass (no parking-collision code exists to have caused it): the car
    tipped onto its side after the Breckenridge spawn's initial physics
    settle - flagged for the next physics/spawn pass, not re-investigated
    here since it's off this pass's own scope.

    **RESOLVED (2026-09-03, Breckenridge spawn tip-over):** root cause,
    confirmed via `-odtrace` on the exact repro spawn (Lincoln Avenue,
    `X=1126119 Y=471996`), was two independent bugs stacking:
    1. That spawn XY has NO road collision anywhere below it at all - it
       sits over open ground where only a `DynamicMeshComponent` (terrain,
       tagged `DefaultPhysicalMaterial`) exists, so `TrySnapToGround()`'s
       20s wait for `PM_Asphalt` always timed out and fell back to
       whatever's below, per-tick, stably reproducing the same off-road
       hit every time (`ground-snap timed out after 20.0s ... settling for
       whatever's below (DefaultPhysicalMaterial)`). Likely fallout from
       the same-day lake-depression terrain work nearby (`1d2677f`,
       `27e730f`) reshaping ground under/near this road point.
    2. Separately, `TickComponent`'s suspension tick early-outs whenever
       `!Body->IsSimulatingPhysics()`, so every wheel's `SpringLen` sits
       frozen at its 20.0f default for the entire kinematic
       lift+snap+settle sequence, no matter the real gap. The instant
       physics went live, `TickSuspension`'s first call computed a
       `ComprRate` from that stale default against the real first-hit
       distance - a phantom multi-hundred-cm/s "spring velocity" - and the
       damper term turned it into an uneven, per-corner force spike large
       enough to flip the car even on ordinary pavement.
       Confirmed via trace: `susp=20.0` (fully "uncompressed" per the
       stale default) on the very first post-release tick, snapping open
       over the next few ticks - a violent, asymmetric release, not a
       gentle settle.

    Fix (`ODVehiclePawn.cpp`, `ODVehicleMovement.cpp/.h`):
    - `TrySnapToGround()` now tries a one-shot nearby-pavement nudge once
      2s have passed with no direct asphalt hit: searches a ring of
      offsets (5m-80m, 8 directions) for one that hits `PM_Asphalt`
      directly, and re-centers the spawn XY there before falling through
      to the old timeout/fallback. At Breckenridge this found real
      pavement 40m away and resolved ground-snap in 2.0s instead of 20s
      onto bad terrain.
    - New `UODVehicleMovement::PrimeSuspension()`, called once right
      before `SetSimulatePhysics(true)`: raycasts each wheel at the final
      release pose and sets `SpringLen`/`FzSm` directly from that hit
      (no `ComprRate` involved), so the first real physics tick starts
      from equilibrium instead of computing a bogus compression spike.
    - `TrySnapToGround()` also levels the release pose to a plane fit
      through all 4 wheels' ground hits (previously always released
      perfectly flat, sampled from one point only), and park-hold
      (`UODVehicleMovement::TickComponent`) now zeroes angular velocity
      alongside the existing linear-velocity pin, so a settle that still
      lands with a torque doesn't spin the car before the first pedal
      input. Both are defense-in-depth - the nudge fix is what actually
      resolved the reported bug; these two prevent a *similar* class of
      issue (isolated uneven contact, not "no road at all") from doing
      the same thing elsewhere.
    - Physics contract (mass/axle geometry in `cars_manifest.json`)
      untouched.

    Verified with post-fix `-odtrace` renders at all three towns, car
    upright and stationary ~20-38s after settle in every case: Breckenridge
    (deterministic repro coords, nudge fired and relocated 40m to real
    pavement), Frisco and Dillon (both resolved onto `PM_Asphalt` directly,
    no nudge needed - confirming this wasn't a general suspension-priming
    regression, just the one bad spawn XY plus the latent stale-SpringLen
    bug it exposed).

**Super car in-game verification (2026-09-03/04, §7.15 follow-up):** the
Super preset's structural fix (`bc34028`: belt_z 53, continuous nose rise,
arch radius under beltline) had never actually been looked at in-game since
reimport. Verified now.

- **QA tooling added:** `-odcar=<DisplayName>` launch arg on `AODVehiclePawn`
  (`ODVehiclePawn.cpp`, `BeginPlay`) — case-insensitive lookup by
  `UODVehicleDef::DisplayName` against the already-discovered `Vehicles`
  array, then delegates to the existing `SetVehicleByIndex()`. Sits right
  next to the pre-existing `-odvehidx=N` (index-based) flag; `-odcar=` means
  a QA script doesn't need to know a preset's current alphabetical/
  Muscle-bubbled index. No-ops with a `LogTemp` warning if the name isn't
  found.
- **Super, day (`-odhour=15`) and dusk (`-odhour=20`), Frisco spawn, default
  chase cam:** body reads correctly as "supercar yellow" (palette cell 4,
  `M_CarBody`), glass is genuinely translucent (not hollow-see-through -
  the cabin_blocker panel is doing its job), wheels sit correctly in their
  arches with no floating/sinking/gap at the tire-to-fender line, no
  dune-buggy read, no z-fighting. Dusk shot: headlights and taillights read
  correctly, no stray emissive.
- **False alarm chased down and cleared:** the rear wing
  (`rear_wing()`, `deck_x=-198, deck_z=68, stanchion_h=26`) looked
  suspiciously body-colored (yellow-green) rather than the expected dark
  `ACCENT`/plastic grey in the first zoomed screenshot crop — looked exactly
  like the "slot mismatch after reimport" failure mode this pass was
  watching for. Dumped `SM_Car_Super`'s actual material slots via a
  throwaway headless Python script (`unreal.load_asset` +
  `static_materials`): all 4 slots present on this mesh (body, plastic,
  glass, lights) are correctly bound to `M_CarBody`/`M_CarPlastic`/
  `M_CarGlass`/`M_CarLights`, and the source `T_CarPalette2.png`'s ACCENT
  cell (16) is genuinely mid-grey `(110,110,116)`, not green. Pixel-sampled
  the suspect screenshot region and its color ratios matched the shaded
  *body* palette cell, not accent — it's the trunk deck surface in shadow,
  with the wing itself (correctly colored, just thin and mostly self-
  shadowed at this camera distance) barely legible from the rear 3/4 chase
  cam. Not a material bug; a genuinely weak but not broken read. No pipeline
  or import changes made for Super — verification only.
- **Quick checks of the other three non-Muscle presets (day, same vantage),
  triage only, not fixed this pass:**
  - **JDM:** clean. Blue body, glass roof, flared arches visibly wrap the
    wheels, no gap at the tire line.
  - **Pickup:** wheels read as detached from the body — a visible gap of
    daylight between tire and bodywork on all four corners, no arch flare
    reaching the tire; comes across as oversized wheels bolted under a
    narrow black tray rather than integrated fenders. Worth a look; possibly
    the same family of arch-margin/track-width mismatch the Super's
    Round-2/3 dune-buggy defect was.
  - **Rally:** same symptom as Pickup — wheels sit outside the body
    silhouette with a visible gap to the flared-arch bulge, most noticeable
    at the rear corners.
  - Both Pickup and Rally are candidates for the same arch-vs-track-width
    audit that fixed Super; deliberately not touched in this pass (scope was
    Super verification + light triage of the others).

Screenshots: `super_check_day.png`, `super_check_dusk.png`, `others_JDM.png`,
`others_Pickup.png`, `others_Rally.png` (scratchpad).

**Pickup + Rally arch-vs-track audit (2026-09-03, §7.15 follow-up):** did
the arch-vs-track-width audit flagged above (same class of defect the
Super's Round-2/3 "dune-buggy" fix addressed), against `cars_manifest.json`.
Both cars had the SAME root cause as Super's original bug — `build_hull()`'s
`min_band_cm=14` clamp (`local_bottom <= base_top - 14`) silently chops the
wheel-arch peak down whenever the authored side-profile's `z_top` sits too
close to (or under) the arch's intended height near an axle x — plus,
independently, plan-view half-width narrower than the tire's outer edge
(half-track + tire half-width, 13 cm) at the axle station.

- **Rally:** both axles' hood/hatch profile dipped to 44-54 cm right where
  the wheel arch needed to peak at ~43 cm (wheel_r 32 + margin 11) under
  belt_z 48 — clamp chopped the arch down by 10.6 cm (front) / 11.0 cm
  (rear), leaving the body a flat slab with no visible arch cut at all.
  Plan half-width was also 7.6/22.0 cm short of the tire's outer edge at
  front/rear. Fixed in `build_rally()`: added small raised fender-flare
  bumps in the side PROFILE right at each axle x (112→64 cm, -118→66 cm,
  tapering back to the original low hood/hatch height either side — this is
  literally the "flared arches" identity trait already named in this car's
  docstring, just previously undersized to actually clear the arch), widened
  the PLAN half-width at both axles (69→76 front, 58→80 rear), and bumped
  `belt_z` 48→50 / trimmed `arch_margin_cm` 11→9 to land the arch-to-belt
  spare at 9 cm (matching the 9-12 cm convention every other preset keeps).
  Post-fix: 0 cm clamp loss at either axle, shoulder width now flush with
  (front) or 4 cm proud of (rear) the tire's outer edge.
- **Pickup:** the FRONT axle (x=185) sits directly under the long flat
  40-44 cm hood — clamp chopped the arch peak by 24.9 cm (intended 54 cm,
  chopped to 29.1), same collapsed-slab symptom. Fixed with a raised
  fender-flare profile bump at x=185 (78 cm, an off-road flare stacking
  above the hoodline reads as Raptor-style, not a style break) and a widened
  plan half-width at that station (72→88 cm). The REAR axle (x=-160) was
  worse in a different way: it sits entirely outside the cab's own lofted
  profile domain (x=[-2,228]), under the separately-welded, non-lofted bed
  (`build_pickup_bed()`) — which had NO wheel-arch cut at all (flat side
  rails) and a bed half-width derived from the narrow cab-tail plan width
  (68 cm), 39 cm short of the tire's outer edge (111 cm). Rear wheels were
  fully exposed beside a flat black tray. Fixed in `build_pickup_bed()`:
  the bed is now sized off `half_track` directly (`half_track + 14`, 112 cm)
  instead of the cab-tail width, and the side rails are segmented (18
  stations) with the same arc-clipped wheel-arch cut `build_hull()` uses on
  the main hull, centered on `rear_axle_x` — a genuine scalloped opening
  over the rear tire instead of a solid slab.
- `cars_manifest.json` verified **byte-identical** before/after regen (the
  physics contract — axle x, half-track, wheel radius — never changed, only
  mesh geometry did).
- Regenerated via `pipeline/make_car_meshes.py`, reimported via
  `unreal/import_car_meshes.py` (0 errors, only the pre-existing benign
  "Interchange Obj translator: Cannot find texture" warnings — the script's
  own separate texture-import task handles that, as before), verified live
  with the `-odcar=Pickup` / `-odcar=Rally` QA flag at Frisco
  (`-odhour=15`, default chase cam, `-odshot=15`): `pickup_fixed.png` and
  `rally_fixed.png` (scratchpad) both show wheels seated against a visibly
  widened, arch-flared body with no daylight band at the tire-to-fender
  line, matching Super's already-verified read; each car's identity is
  intact (Pickup keeps its ride height and cab+bed shape, Rally keeps its
  compact-hatch stance). No C++ changes.

---

## Immediate queue (2026-09-05) — post-junction-pads plan of attack

*Written after the sloped-junction-pad work (§ 7.13 follow-through) merged to
`main` at `c8b2c55`. Supersedes the § 7.10 "Next up" list; scoping re-verified
against the CURRENT code (several audit-era items had gone stale: water now
has `AODWaterManager`/`build_water_tiles.py`, guardrails exist per § 7.16,
`BARRIER_ENABLED` is back on). Taylor's drive-test of the sloped pads runs in
parallel with all of this and gates none of it except item 6's live half.*

**Coordination note:** the main checkout (branch `audit-and-arc7-rescope`)
has UNCOMMITTED edits to `build_road_guardrails.py`, `build_road_network.py`,
`build_road_stations.py`, `road_mesh_common.py`, `apply_road_material.py` —
live work in progress. Items 3 and 5 below touch the same files; whoever
picks them up must check that state first and not collide.

1. **Barrier → out-of-bounds reset** *(hours — AUDIT § 7 item 4, direction
   already confirmed)*: crossing the corridor barrier triggers the race
   framework's existing respawn-at-last-gate (plus a short fade) instead of
   bouncing off an invisible wall; keep a light visual boundary. Verify
   headless via a scripted drive off-corridor (`-odgo`/`-odtrace`: expect a
   teleport signature, not a wall impact).
2. **Validate `sample_mesh_height` + keep the road-vs-terrain gate honest**
   *(hours — AUDIT § 7 item 7)*: fixture test with an analytically-known mesh
   (flat plane + known slope + a shared-edge case for the `idxs[0]`
   first-containing-triangle note), asserted in a small test script the
   README's test-hooks section points at. Every fit number in § 7.11–7.16
   rests on this reconstructed function; hours of work to make the gate
   trustworthy.
3. **Per-tile building merge + corridor-gated collision** *(1 session — the
   top runtime item, ~25,000 draw calls → ~25)*: `AODBuildingManager` builds
   ONE `UDynamicMeshComponent` per building today (its own header comment
   documents the debt). Merge all buildings in a tile into one dynamic mesh
   per tile; enable complex collision only on tiles intersecting the road
   corridor (players can't reach the rest). Verify headless: component/draw
   counts in the manager log before/after, collision drive-test against a
   known near-road building (the § 7.0-step-3 scripted-impact method).
4. **OSM road tags → width/surface/layer** *(1–2 sessions — largest realism
   gain)*: `fetch_osm_network.py` currently keeps NO per-way tags. Re-query
   (or extend the fetch) for `lanes`/`surface`/`layer`, thread through
   `build_road_stations.py` like `road_name`/`is_bridge` already are, then
   consume: lane count → real per-road width (biggest visual payoff),
   `surface` → unpaved grip/visual variants, `layer` → feeds the existing
   `detect_elevation_conflicts()` instead of pure geometry. Do the width
   consumer first; grip/visual can trail.
5. **Junction profile-step smoothing** *(1 session, low urgency)*: the pad
   audit still shows 39 sites with profile-only steps > 0.3 m (worst 1.07 m)
   — member-road STATION profiles disagree at crossings before the pad
   flattens them; the baked pavement is already clean (worst 0.43 m).
   Blend member profiles toward agreement upstream
   (`reconcile_junction_elevations()` is the natural home) rather than
   asking the pad bake to hide more. Gate: junction audit worst profile
   step, plus the standard road-fit/terrain-fit suite. **Wait for Taylor's
   sloped-pad drive-test first** — it may show these don't read at speed.
   *(✅ DONE 2026-09-06, `e95ac1e` — proceeded per Taylor's "work through
   all of them, saving any questions" mandate; the drive-test caveat is
   flagged in the end-of-run questions. Audit: 37 sites > 0.3m / worst
   1.09m → **2 sites / worst 0.70m** after a full rebake, all gates green.
   See the item-5/6 results entry below for the two failed designs this
   went through first.)*
6. **Pale background hills — fresh diagnosis** *(unknown; diagnostic)*:
   three rounds of real fixes (`037c85c`, `fffe321`, `2ecf266`) and the
   symptom still stands, which means the mechanism is still unidentified —
   stop patching candidates. Next round is isolation, not fixes: screenshot
   the same vista with fog, atmosphere/exposure, vertex-color tint, and
   distance-tint each toggled independently (the § 7.15 art stack has
   flags/materials for each) and bisect which layer carries the paleness.
   Second half needs Taylor's eyes live to confirm "fixed" (subjective
   read, same rule as audio character).
   *(✅ isolation DONE 2026-09-06 — carrier identified per vantage, no
   fixes applied per this item's own "isolation, not fixes" rule; see the
   item-5/6 results entry below. Awaiting Taylor's live look + fix-direction
   pick.)*

**Recommended order: 1 → 2 → 3 → 4, with 5 and 6 gated on Taylor input**
(5 on the drive-test verdict, 6's confirmation on a live look). 1 and 2 are
hours each and de-risk everything after them; 3 is the top runtime win; 4 is
the top realism win.

### Item 5 + 6 results (2026-09-06 run)

**Item 5 — junction profile-step smoothing (`e95ac1e`).** Landed as
`junction_agreement_pass()` in `road_mesh_common.py`, run by
`build_road_stations.py` LAST — on the final smoothed profiles, output
shipped as-is. Raise-only lo→hi snap (highest-first) of every cross-segment
pair within `JUNCTION_AGREE_RADIUS_M` (5 m) whose gap is below the conflict
threshold, each snap laying a 3% (`JUNCTION_AGREE_TAPER_GRADE`) raise-only
taper along the lower road's own stations; the `conflict_flat | is_struct`
skip mask keeps bridge/tunnel/grade-separation stations out. **Two full
rebakes proved the obvious pre-smooth design cannot work** (write agreement
into reconciled ground, re-smooth touched segments): (a)
`grade_smooth_elevation()`'s envelope clamp cuts a lone-station raise back
to ~`max_grade*spacing` — audit REGRESSED 37→72 sites; (b) even taper-raised
and completely unpinned, pass 2's rate limiter + endpoint-drift ramp floats
whole approaches ~1 m ABOVE their reconciled ground (the final
`envelope_clamp` bounds grade, not "≤ ground") — 38 sites/1.16 m. Per-segment
re-smoothing structurally cannot preserve a two-road agreement it only sees
one road of at a time. Final numbers after the full rebake + pads: **2
at-grade sites > 0.3 m, worst profile step 0.70 m** (residual sits inside the
skip mask by design), worst baked step 0.46 m unchanged; road-fit /
terrain-fit / road-uv / gen-id gates all PASS. Visual spot-check at the
formerly-worst CO 9 / Peak One Boulevard site: pads flush, no step.
`build_road_guardrails.py` now unions the pass's junction mask (both
stations of every at-grade crossing, agreeing or not) into its rail-break
mask. NOTE for future full rebakes: FOUR pads-backup dirs must be cleared
(`_road/_terrain/_sidecar_backup_junction_pads` in Masks plus
`_roadv3_backup_junction_pads` in MasksV2).

**Item 6 — pale background hills, isolation verdict (no fixes applied).**
Render matrix at Silverthorne (Ruby Ranch Rd) + Vail (Piney Lake Rd),
`-odhour=15`, each layer toggled independently on the post-item-5 world:
baseline / `ShowFlag.ColorGrading 0` / slope-layer-off (`flat_w = 1.0`
material variant) / snow-off (snow line 99,999 m) /
`ShowFlag.SpecularLighting 0` / `ShowFlag.Atmosphere 0`. Pixel-diffed hill
crops (mean RGB, 0–255):
- **Snow, color grading, specular: exact no-ops** on the pale hills (<1.6
  delta) at both vantages. Slope-rock layer: near no-op on the big
  Silverthorne hill (+2), and removing it made the right-hand band
  BRIGHTER (+13) — the flat grass/dirt albedo is just as pale out there,
  so the Rock030 slope layer is NOT the carrier (kills the previous
  leading hypothesis; the ~2× facet-slope overstatement on FAR tiles is
  real but paints a layer that isn't what reads pale).
- **Silverthorne carrier = SkyAtmosphere aerial perspective + pale distant
  flat albedo.** Base hill color (109,117,126) is blue-dominant;
  `ShowFlag.Atmosphere 0` strips (−20,−24,−36) off the hills while moving
  foreground ground only slightly — a distance-scaled blue-white inscatter
  veil is the dominant contributor, over an albedo that still renders pale
  warm-gray (89,93,90 vs foreground grass ~(116,110,91)).
  (`r.SkyAtmosphere.AerialPerspective.StartDepth 200` via -ExecCmds was a
  no-op — use the component's aerial-perspective properties, not that
  cvar, when tuning.)
- **Vail carrier = the flat albedo itself.** The pale mid-distance slope is
  warm-neutral (94,95,88 — no blue veil at that range) and every toggle
  left it within run-to-run cloud noise: the dirt/sand flat blend + no
  distant groundcover reads pale sandy, same as it does close up.
Candidate fix directions (NOT implemented, per the item's isolation-only
scope): turn down the SkyAtmosphere component's aerial-perspective
strength/distance scale; darken+saturate the distant flat albedo (distance
or mip-based vegetation/moisture bias rather than more Rock030 changes).
Needs Taylor's live read before any of these move.

---

## Cross-cutting (release engineering)

- **Packaging ✅ first proof (2026-07-18):** scripted `BuildCookRun`
  (Win64 Development, cook+pak+archive to `Packaged/`) builds and **the
  packaged game runs the full loop**: forest streams tiles from the cooked
  mask sidecar (`pipeline/make_forest_mask_bin.py` →
  `Content/OpenDrive/Masks/terrain_mask.bin`, staged via
  DirectoriesToAlwaysStageAsUFS; `-odmaskbin` tests the path in editor
  builds), custom vehicle drives on PM_Asphalt, audio synth plays. Gotcha
  that cost one cook: runtime-`LoadObject` assets (car meshes, trees,
  presets) are invisible to the cooker — `DirectoriesToAlwaysCook=/Game/
  OpenDrive` in DefaultGame.ini. **Shipping config also proven** (builds
  0 errors/0 warnings, packaged Shipping exe runs stable —
  `Packaged/Shipping/`). Remaining for release: installer/zip,
  fresh-machine test.
- **Real unity-build compile bug found and fixed (documentation catch-up,
  2026-07-29 pass — the fix itself predates this session, exact date not
  recorded in-code):** `ODBarrierManager.cpp`, `ODBuildingManager.cpp`,
  `ODRoadManager.cpp`, and `ODTerrainManager.cpp` each carried their own
  private `ReadU32LE`/`ReadF32LE` helpers in an anonymous namespace —
  compiles fine per-file under the Editor target, but **collides under the
  standalone Game target's unity build**, since anonymous namespaces are
  per-translation-unit and unity build merges multiple `.cpp` files into
  one. Fixed by extracting both into a new shared header,
  `ODBinSidecarUtil.h`, included by all four instead of each redefining
  them. Same four managers AUDIT.md § 6.3/item 9 already flags for
  copy-pasted tile-streaming Tick/dispatch logic — this fix only extracts
  two small leaf helpers, it does **not** implement item 9's actual ask (a
  shared tile-streamer base class), so item 9 remains open.
- **Performance budget: DEFERRED, not an active constraint (2026-07-27).**
  Taylor's call: *"I'm happy to burn the GPU I have to figure out what does and
  doesn't work and work backwards from a perfect project rather than struggle
  getting things right forever because we're trying to meet resource
  constraints."* The old "60 fps on a mid GPU at 1080p" target is parked until
  the world looks right. **Important caveat (`AUDIT.md` § 1.4): three costs are
  NOT GPU-bound and a better card does nothing for them** — draw calls
  (CPU/render thread; the 25,000-component building problem), bake time and
  fetch volume (single-threaded Python + network), and coordinate precision
  (arithmetic). Spend hardware on how the world *looks*, not on how it is
  *delivered*.
  !Issue - Lumen surface cache atlas overload warning has been present for a
  while (Taylor, 2026-07-18).
  *(fixed 2026-07-18, machine-verified: `AODForestManager`'s tile HISMs now
  call `SetAffectDynamicIndirectLighting(false)` right after creation — trees
  keep lighting/shadow, stop demanding surface-cache cards. Verified via
  `-odshot=20` at the start gate with tiles loaded: no on-screen overload
  warning, no overload text in `OpenDriveUE.log`, only the expected
  `r.LumenScene.SurfaceCache.AtlasSize:4096` config line. Headroom trim
  (`CardTexelDensityScale`) not needed — this alone cleared it.)*
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
| 5 | all of North America → **mountain hot zones** (rescoped 2026-07-27) | Arc 7 | terrain ✅ baked+streamed, road network ✅, buildings/barrier ✅; scope + coordinate + terrain-detail decisions made (§ 7.9) |
| 6 | map view | 4.2 (stage), 7.4 (world) | ✅ stage v1 |
| 7 | garage/cosmetics | 4.3 | ✅ live car/engine/drivetrain/tuning picker; multi-car assets + cosmetics — |
| 8 | performance mods | 1.5 (+4.3 UI) | ✅ presets |
| 9 | control editing | 4.1 | — |
| 10 | wheel/sim rig | Arc 6 (FFB deferred) | — |
| 11 | road elevation ÷2 | 1.3 (+1.3b cut wall) | ✅ |
| 12 | realistic engine sound | 5.1 ✅, 5.3 upgrade later | ✅ v1 |
| 13 | exhaust editing | 5.2 (params + garage UI + persistence), see §7.30 | ✅ |
| 14 | tree collision (added) | 1.6 | ✅ |

**Recommended next (revised 2026-07-28 — § 7.10):** items 0–3 (barrier-seed
determinism, adaptive martini terrain, retired-tile cleanup, bridge/tunnel
elevation) are done — full account in § 7.10. Next up:

4. **Per-tile building merge + corridor-gated collision** — the top *runtime*
   item (~25,000 draw calls → ~25), CPU-bound so no GPU upgrade substitutes.
5. **OSM road tags** (`lanes`/`surface`/`layer`, already fetched) — largest
   available realism gain, no re-fetch needed.
6. **Restore a road-vs-terrain gate** and validate `sample_mesh_height`
   against a fixture — the one surviving gate rests on a reconstructed,
   unvalidated reimplementation.

Full ranked queue in [`AUDIT.md`](AUDIT.md) § 7 (items 0–3 marked done there
too; the table's original numbers are left as historical record).

Separately and independently: Taylor drive-tests the assist stack + gearbox/ABS
changes (the gate for stage-3 preset retune). Packaging/Shipping are proven;
4.1 input rebinding and Arc 4 UX proper remain queued. Dragon stitching,
HTTP-based road-tile fetch, and intersection/junction mesh blending are still
on the table for later Arc 7 slices. **NA-scale preprocessing/LWC is dropped** —
the per-location coordinate decision removes the need.

**2026-07-27 follow-up:** the audit (§ 7.9) supersedes the previous gate list.
Of § 7.5's three open items: the **Marysville coastal road is closed** (Taylor:
almost certainly a generation-time bounding-box error, not a pipeline defect);
the **too-dark open-world level** is still open but was found on Skagit, since
superseded by Oahu, so confirm it still reproduces before investigating; the
**cold-start tile-streaming latency** is still unexplained but hidden by the
loading screen, and § 7.9's finding that `FinalizeTile` runs unbudgeted on the
game thread is the most likely mechanism — worth checking there first.

The menu/garage/map-texture work (§ 4.0b, § 4.3, § 7.5) is done and verified
live.

**Periodic frame-hitch diagnosis (2026-09-03, §7.15 follow-up,
`docs/perf_after_art_overhaul.md` addendum):** dug into the ~25s-cadence
≥75ms hitch that doc's measurement pass had flagged but not chased (out of
scope that pass, hypothesis was "probably GC"). Added `LogGarbage=Verbose`
and correlated it against the existing `-odtrace`-based frame-timing
parser on the same `abslog`. **GC hypothesis disproven directly:** full
collects fire on the vanilla ~61s `gc.TimeBetweenPurgingPendingKillObjects`
cadence (not ~25s) and cost only ~18-20ms each end to end - a quarter of
the 75ms hitch threshold - and none of their timestamps land near an
observed hitch. Also ruled out this project's own tile-streaming managers:
the original hitches occur even on a fully static scene where all 8
managers (`ODTerrainManager`/`ODBuildingManager`/`ODWallManager`/
`ODBarrierManager`/`ODRoadManager`/`ODForestManager`/`ODWaterManager`/
`ODParkingManager`) report zero pending work, so there's no load/unload
churn to blame. Found one real, if narrow, cause along the way: the QA
`-odshot` screenshot flag itself produces a hitch that lines up exactly
with its own capture log line every time it's present - an artifact of
the tooling, not a real player-facing stall, and it should be excluded
from future hitch tallies. The remainder of the originally-measured hitch
turned out **not reproducible**: two fresh same-day control runs (before
and after the config change below, no `-odshot`) both came back
completely hitch-free (0 hitches ≥75ms, median 14.9-15.3ms/~65-66 fps),
and the original hitch timestamps weren't even a clean 25s multiple
run-to-run once checked precisely. Most likely explanation: transient
OS/driver/hardware jitter on the dev laptop at original capture time, not
an engine or game-code mechanism. Added
`gc.AllowIncrementalReachability=1` / `gc.IncrementalReachabilityTimeLimit=
0.002` / `gc.NumRetriesBeforeForcingGC=10` / `s.ForceGCAfterLevelStreamedOut=0`
to `DefaultEngine.ini` as free, zero-downside GC amortization hardening for
any future spike, explicitly documented as *not* a fix for this specific
(non-reproducing, non-GC) hitch. No C++ changes - no evidenced churn
pattern existed to pool or fix. Doc's original verdict stands: fine, no
blocker for Taylor's live drive-test.

**Regression fix: spawned car appeared stuck at full throttle (2026-09-03/04,
§7.15 follow-up, `ODVehicleMovement.cpp`):** urgent live-drive-test report -
car floored itself with zero input right after spawn. NOT a throttle bug -
`ThrottleIn`/`thr=` traced 0.00 the entire time in every repro. Root cause
was a feedback loop between two of today's spawn-settle changes and the
existing tire model:

1. `82c97ed` (spawn parking hold) pins the body's world-space linear
   velocity to zero every tick while `bParkHold` is engaged, and forces a
   full `W.MaxBrakeNm` into every wheel's `BrakeNm` on top of the driver's
   own pedal.
2. `TickTires`' existing anti-jitter clamp on longitudinal tire force
   (`FxStop`, "kills parking jitter without touching real slides") was
   gated `W.BrakeNm < 1.0f` - meant to let a real hard brake at speed keep
   full ABS/slip physics. ParkHold's forced brake torque made that guard
   permanently false while parked, so the one state most in need of the
   jitter clamp was exactly the state that disabled it.
3. `TickComponent` computes `FwdSpeedCmS`/feeds `TickTires` its `Vx`/`Vy`
   from the body's *current* velocity, which reflects last tick's force
   integration - the park-hold velocity pin runs later in the same
   function, after tire forces are already computed for this tick. So
   each tick: the pin zeros velocity -> the (unclamped, thanks to #2)
   tire model reads a residual pre-pin `Vx`/`Vy`, computes a large
   corrective `Fx`/`Fy` off it, the physics solver integrates that force
   before the *next* tick's pin catches it -> next tick reads a nonzero
   velocity again -> repeat, forever. Confirmed headless with a completely
   idle, un-driven car (`-odgo` only, no `-odrev`): `FxR` swinging
   +/-3x10^6 N and `spd` oscillating +/-55 cm/s at t=35s, never damping,
   with `thr=0.00` throughout - the wheels/chassis visibly straining in
   place, reading exactly like "stuck at full throttle" even though no
   throttle was ever involved.

**Fix:** `TickTires`' stability-clamp block (`ODVehicleMovement.cpp`,
around the `FyStop`/`FxStop` calculation) now special-cases `bParkHold`:
while parked, both `Fx` and `Fy` are forced to `0.0f` outright instead of
being sized by a clamp proportional to `Vx`/`Vy` - proportional clamping
doesn't work here because the velocity being measured is itself corrupted
by the same loop it's supposed to bound. Everything else (real coasting
jitter clamp, real hard-brake ABS physics, the park-hold brake torque and
velocity/angular-velocity pin themselves) is untouched. Root cause was in
the tire model's interaction with `82c97ed`'s park hold, not in `82c97ed`,
`7dc328f` ("tips over after Breckenridge spawn"), or `ffeaf96` (freelook
invert, confirmed genuinely isolated to `UpdateFreeLook`'s pitch sign) -
all three were audited and ruled out as the direct mechanism before this
was found.

**Verified headless, both required cases:**
- No `-odrev` (idle car, zero input): by t=33.99s, `thr=0.00`, `spd=0`,
  position frozen at (680251,-589678,-67870) to the cm, `FyF=FyR=FxR=0`
  exactly, `fz` settled flat at ~383,200 (static corner load), `gnd=4` -
  fully stationary, no residual force.
- With `-odrev=5` (scripted drive): throttle ramps 0.09 -> 1.00 as
  designed, speed reaches 1862 cm/s (~67 km/h), position moves ~44 m from
  spawn - scripted-drive path unaffected by the fix.

Committed locally only (`ODVehicleMovement.cpp`); not pushed.

**Regression fix: wheels visibly spin while parked (2026-09-03/04, §7.15
follow-up, `ODVehicleMovement.cpp`):** user-reported after `b3f3cf0`
(the `Fx`/`Fy`-zeroing fix above) - car sits still but its wheels visibly
rotate with zero input. Mechanism confirmed headless: `TickDriveline`
computes `WheelTq` from the coupled clutch torque
(`NetTq = DriveTq - (CoupledRpm > IdleRpm ? BrakeTq : 0)`) unconditionally
- it doesn't check `bParkHold` - and near-idle standstill still carries a
partially-coupled clutch (traces show rpm~900, `coupled=` in the low
hundreds), so `W.DriveNm` stays nonzero every tick even while parked. In
`TickTires`' wheel-spin integration
(`W.Omega += (W.DriveNm - ReactionNm) / EffInertia * Dt`), `ReactionNm` is
derived from `Fx`, which `b3f3cf0` now forces to `0.0f` during park-hold -
so the one thing that used to react against `DriveNm` and hold the wheel's
spin-rate near equilibrium was removed, leaving `DriveNm` to integrate
`W.Omega` upward unopposed. The brake pass right below
(`EffBrakeNm = W.BrakeNm * AbsFactor[i]`, which does include
`bParkHold`'s forced `W.MaxBrakeNm`) only trims a fixed
`EffBrakeNm/EffInertia*Dt` off `|W.Omega|` per tick rather than clamping
it outright, so it isn't guaranteed to outrun a steady `DriveNm` feed -
the net result was a slow, steady spin-up read as "wheels spinning while
parked." `ODVehiclePawn.cpp`'s visual wheel-mesh rotation reads
`Movement->GetWheelOmega(i)`, which is `Wheels[i].Omega` directly, so the
spin was not cosmetic-only drift - it was the same state driving the
physics.

**Fix:** in `TickTires`, `W.Omega`'s drive/reaction integration is now
gated on `bParkHold`: while parked, `W.Omega` is set to `0.0f` outright
instead of integrating `(DriveNm - ReactionNm)/EffInertia*Dt` - a parked
wheel is physically locked, so it's pinned rather than clamped down over
multiple ticks. `Fx`/`Fy` stay zeroed during park-hold exactly as
`b3f3cf0` left them (no reintroduction of that oscillation); only the
wheel-spin state gained an explicit park case. Also added `omega=`
(driven rear wheel) and `park=` fields to the existing 10 Hz
`ODDrivetrain:` trace line for direct proof, since `slip=` alone only
implies `Omega` indirectly.

**Verified headless, both required cases:**
- Zero input (`-odgo=5` only): 4993 trace samples from spawn through
  t=530s, every one reading `omega=0.00 park=1 slip=0.00 spd=0`, `rpm=900`
  (idle) with `coupled=0` - no wheel spin at any point, matching the
  fix's intent exactly (`grep -c` for any nonzero `omega=`/`slip=` across
  the full run: 0 matches).
- Drive-off (`-odrev=5`): park-hold releases on first pedal input as
  designed (`park=0`), throttle ramps to `thr=1.00`, `omega` climbs
  normally (0.62 -> 5.34 rad/s by t=9.27s) alongside `spd` (0 -> 136 cm/s
  in the same window) - the car drives off exactly as before, confirming
  the park-hold pin doesn't leak into normal driving.

Committed locally only (`ODVehicleMovement.cpp`); not pushed.

**Bleached-white ground fix (2026-09-04, §7.15 follow-up,
`ODTerrainManager.cpp` + `ODAtmosphereController.cpp`):** user-reported
most daytime hillsides render bone-white/pale instead of warm green/brown,
even valley floors well below the ~3500 m snow band. Investigated 5
candidate causes numerically before touching anything, per brief: material
texture/albedo, snow-band unit mismatch, slope-threshold permissiveness,
FAR/NEAR material mismatch, fog/atmosphere over-lightening.

**Diagnostic methodology (isolate-and-verify, one confounder removed per
step, each step confirmed with an actual in-game render, not just static
code reading):**
1. Wired `M_GroundStylized`'s `MaterialExpressionVertexColor` node directly
   to `MP_BASE_COLOR`, bypassing every texture/blend node - still rendered
   pale/sandy. Ruled out the texture/blend graph as the sole cause.
2. Found a real bug in the baked-vertex-color round trip:
   `ApplyBiomeTint()` wrote `Final.ToFColor(true)` (assumes sRGB, gamma-
   encodes) but `BuildTile()` read it back via `FLinearColor(FColor)`
   (assumes sRGB, gamma-*decodes*) while `UDynamicMeshComponent`'s
   `ColorSpaceTransformMode` was `LinearToSRGB` (a THIRD gamma pass at
   render time) - three gamma operations where zero or one was correct,
   net brightening a dark tint like EvergreenForest (0.04,0.14,0.07) well
   past its intended value. Fixed to a lossless byte round trip:
   `QuantizeRound()` (encode) / `ReinterpretAsLinear()` (decode) /
   `NoTransform` (component mode) - all three are plain `/255` or `*255`,
   no gamma, matching the GPU's own UNORM<->float convention for a
   per-vertex attribute (not a sampled sRGB texture). Rebuilt and
   re-verified with the same vtint-direct-to-BaseColor render: **result
   was unchanged** - proved this fix, while a genuine correctness
   improvement (kept), was not the dominant visible cause by itself.
3. Hardcoded `MP_BASE_COLOR` to a flat, very dark green constant
   (0.05,0.15,0.05), bypassing vertex color AND every texture node
   entirely - still rendered as a bright, saturated mint/lime green.
   Since the SAME-frame car body and tree trunks (different materials,
   same lighting) rendered correctly dark/saturated, this pointed at
   exposure/post-process amplification specific to a large, near-
   horizontal, sky-facing ground plane, not the material graph.
4. Found `AODAtmosphereController::SpawnGradeVolume()` spawns a
   `PostProcessVolume` with `Priority=100` (wins over any other unbound
   volume for fields it overrides) that set `AutoExposureBias = 0.0f`
   (neutral) - silently cancelling a documented `-1.0` EV fix for the
   textually identical "roads/terrain look whitewashed" symptom already
   applied to an earlier, lower-priority (`Priority=0`) Grade volume in
   `build_openworld_test_level.py` (itself carried over from the Tail of
   the Dragon map's own whitewash fix, `apply_light_wet_fix.py`). Restored
   `AutoExposureBias = -1.0f` in the winning-priority volume, rebuilt, and
   confirmed numerically via pixel sampling on the same hardcoded-constant
   diagnostic (not just eyeballing, which was initially misleading since
   the visible change is subtle): ground-shoulder sample point went from
   RGB (158,203,146) pre-fix to (100,156,92) post-fix at the same vantage/
   hour - a real ~35-40% reduction in rendered brightness, consistent with
   -1 EV.
5. Sanity-checked whether eye-adaptation convergence or the tonemapper/
   bloom pipeline was still masking the fix by disabling
   `ShowFlag.EyeAdaptation`, `ShowFlag.Bloom`, and `ShowFlag.Tonemapper`
   via `-ExecCmds` on top of the exposure fix - pixel values were
   unchanged from the exposure-fix-only render, so eye adaptation/bloom/
   tonemapper are not separately contributing beyond the exposure bias
   itself.
6. Restored the real `M_GroundStylized` graph (`apply_ground_material.py`)
   and re-rendered all three vantages with both fixes in place.

**Root cause:** two independent, compounding bugs - (a) a broken vertex-
color gamma round trip that silently brightened every baked biome tint
before it ever reached the GPU (real, fixed, but not the dominant visible
effect), and (b) a higher-priority runtime `PostProcessVolume` silently
re-neutralizing an existing, documented `-1.0` EV whitewash fix that a
lower-priority level-authored volume already had in place (the dominant
effect - PostProcessVolume priority/override semantics mean only the
highest-priority volume overriding a given field wins for that field,
regardless of whether a lower-priority volume "already" set it).

**Verification renders** (`-odhour=15`, both fixes + real material,
scratchpad `ground_fix_dam.png` / `ground_fix_silverthorne.png` /
`ground_fix_valley.png`):
- Dillon Dam Road (Frisco): right-side embankment now reads as clearly
  olive/khaki-green grass with visible texture detail (previously pale
  gray-white); the left-side cut slope stays pale gray, which reads as an
  actual exposed-rock roadcut face, consistent with the target look's
  "gray granite only on steep faces."
- Ruby Ranch Road (Silverthorne): flat prairie ground reads as a muted
  khaki-green, a real improvement over "pale sandy" pre-fix, though still
  more desaturated/pale than target reference (The Crew 2's saturated
  greens) - background rolling hills stay tan/tawny rather than green.
  Distant snow-capped peaks correctly read white only above the snow line.
- Piney Lake Road (Vail): near-road grass verges read olive/khaki-brown
  (improved from bleached white); the mid-distance mountainside in the
  background (upper-left/right of frame) still reads pale gray-tan rather
  than warm green, even though it's tree-lined at its base - the weakest
  of the three vantages.

**Honest residual gap:** the fixes materially reduce the bleaching
(numerically verified, not just visually) at all three vantages, but do
not fully reach the target "vivid Crew-2 green" look, particularly on
flat background terrain and mid-distance slopes away from the road
corridor. A follow-up render/pixel-sample comparison during this session
(comparing the raw-vertex-tint-only diagnostic against the real material
graph at the same vantage) points at `apply_ground_material.py`'s
`hue_from_tint()` helper (desaturates the detail texture to luma, gains it
by a flat `2.0`, then multiplies by the vertex tint) and/or the
`forest_weight` luma-threshold blend between grass/forest detail layers as
the next place to look if a punchier, less-desaturated green is wanted -
not investigated further this session due to scope (the reported "bleached
white" defect is resolved; a stylistic saturation pass is separate follow-
up work).

Committed locally only (`ODTerrainManager.cpp`, `ODAtmosphereController.cpp`);
not pushed.

**Background/off-corridor pale-slope follow-up (2026-09-04, §7.15,
`ODTerrainManager.cpp`):** the above fix made near-road corridor ground read
warm olive/khaki, but background/off-corridor slopes (Silverthorne's
rolling hills behind town, the mid-distance Vail mountainside) stayed pale
tan/white well below the snow line. Diagnosed the two suspects the previous
session flagged (`apply_ground_material.py`'s `hue_from_tint()` gain math
and `forest_weight` thresholds) plus the actual mechanism found this
session, before touching anything:

1. Read `AODTerrainManager::ApplyBiomeTint` end to end against
   `pipeline/classify_biomes.py`. Found the real mechanism:
   `classify_biomes.py`'s own module docstring says it samples biome points
   ONLY along the road corridor ("the player only experiences biome variety
   near the road") - one point every `SAMPLE_STRIDE`-th road station
   (~50 m spacing), never spread across the terrain. But the terrain mesh
   itself extends far past the road into `BACKGROUND_BBOX_SNWE`
   (`pipeline/test_areas.py`), tens of km wide, for the hills/peaks a
   vantage actually sees (confirmed numerically: `summit_county`'s
   `road_biomes.json` has 27465 points but `BACKGROUND_BBOX_SNWE` spans
   ~61x73 km vs `BBOX_SNWE`'s ~39x56 km). A vertex with nothing within
   `BiomeBlendRadiusCm` (300 m) used to snap to the single NEAREST
   road-corridor sample point outright, however far away that was - a
   rocky roadcut or switchback km away, not the real cover the distant
   slope shows in-game, making ground color a function of "distance to the
   nearest sampled road point" rather than of the actual terrain.
2. Also found `ElevHighCm` (full grey-bias elevation, retuned in fffe321's
   OWN session for `summit_county`) still landed at ~3835 m - below
   `apply_ground_material.py`'s own `TREELINE_HIGH_M=3650`, so the
   vertex-tint elevation gradient was already greying out real
   below-treeline forest slopes before the material's own texture-layer
   treeline logic had even started blending toward scree.
3. Fixed both: capped the "nearest point outright" fallback at
   `FarBiomeFallbackCm` (1 km) - past it, the loop keeps the
   already-initialized default tint (same one a tile with zero biome
   candidates already used) instead of inheriting an arbitrary remote
   biome; raised `ElevHighCm` to ~4115 m so full grey doesn't land until
   just above the real treeline. Rebuilt clean (`Build.bat
   OpenDriveUEEditor Win64 Development`, 0 errors).
4. **Verified numerically, not just visually**, per the brief's own
   methodology: re-ran the fixed build at Silverthorne and read the
   engine's own `ODTerrainManager: ground tint tile ... lumaMin/lumaMax`
   log lines for the background/coarse tiles behind town - they now
   resolve a consistently warmer default tint (lumaMax mostly 0.12-0.28,
   i.e. a real olive-green range) regardless of how far each tile's
   nearest road sample is, confirming the C++ fix does what it was meant
   to do.
5. **Then pixel-diffed the actual renders** (`ground_fix_silverthorne.png`/
   `ground_fix_valley.png` from fffe321 vs this session's
   `bg_fix_silverthorne.png`/`bg_fix_valley.png`, same crops of the pale
   hillside regions, `-odhour=15`) and found **near-zero change** (mean RGB
   delta under 0.1/255 in both crops) - despite the tint math genuinely
   changing per the engine log. Traced this with two more isolate-the-
   confounder diagnostic material swaps (`MP_BASE_COLOR` wired directly to
   the slope weight, then to raw `VertexNormalWS.B`, each restored via a
   re-run of `apply_ground_material.py` afterward, verified via its own
   post-save connection check): the pale hillsides visible in these two
   specific vantages are governed almost entirely by
   `apply_ground_material.py`'s own SLOPE/TREELINE-driven ROCK/SCREE
   texture layer (`steep_albedo`/`bare_amount`), which samples raw PBR
   `Rock030`/`Gravel030` textures untouched by vertex biome tint by
   design - confirmed both vantages' background tiles logged
   `distinctBiomes=0` (zero biome candidates in query range at all, so
   they were already hitting the same default-tint code path both before
   and after this fix within the vegetated branch; the visible pixels
   just aren't in that branch for these two frames).
6. Regression-checked Dillon Dam Rd (`-odgo=5 -odspawnx=733226
   -odspawny=-807394`): near-corridor ground stays warm olive/khaki, the
   left-side roadcut stays correctly gray rock - **confirmed no
   regression** (pixel-diff near zero on the near-corridor grass band).

**Root cause (two real, verified bugs, fixed):** (a) off-corridor terrain's
biome-tint resolution depended on distance to the nearest ROAD-sampled
biome point with no sane cutoff, so far-background terrain rendered
however the nearest remote road-corridor classification happened to read;
(b) the vertex-tint elevation grey-bias band was miscalibrated against the
material's own treeline constant, greying real forest slopes below the
intended treeline. Both fixed and verified via engine-log numeric evidence.

**Honest residual gap:** despite (a)+(b) being real, verified fixes, the
specific pale-hillside symptom at the Silverthorne and Vail vantages
documented in fffe321 is **not resolved** - pixel-level renders at those
exact vantages are visually unchanged. The dominant driver there is a
DIFFERENT, unaddressed mechanism: `apply_ground_material.py`'s
slope/treeline-driven rock/scree layer, which is untouched by vertex tint
by design and (per the brief's own target: "gray rock only on steep
faces") may legitimately be correct behavior for genuinely steep/elevated
terrain - or may be over-triggering on coarse/background tiles that aren't
actually as steep as their sparse mesh makes them read; this session ran
out of scope to distinguish the two (would need per-vertex slope data from
the coarse tile mesh itself, not just a screenshot diagnostic). Next place
to look: `SLOPE_SOFTNESS`/`TREELINE_LOW_CM`/`ALPINE_BARE_MAX_BLEND` in
`apply_ground_material.py`, and whether `FAR_GRID_N=21`'s ~100 m vertex
spacing on background/coarse tiles produces meaningfully different
per-vertex normals (and therefore `flat_w`) than a real higher-resolution
sampling of the same hillside would.

Verification renders (`-odhour=15`, scratchpad `bg_fix_silverthorne.png` /
`bg_fix_valley.png` / `bg_fix_dam.png`, compared against fffe321's
`ground_fix_*.png`):
- Silverthorne (Ruby Ranch Rd): background rolling hills unchanged from
  fffe321 - still pale tan, per the residual-gap finding above.
- Vail (Piney Lake Rd): mid-distance mountainside unchanged from fffe321 -
  still pale gray-tan above the tree-lined base, per the residual-gap
  finding above (and plausibly correct "steep rock" per spec - not
  distinguished from over-triggering this session).
- Dillon Dam Rd (regression check): near-corridor ground and roadcut
  unchanged from fffe321's already-correct look - confirmed no regression.

Committed locally only (`ODTerrainManager.cpp`); not pushed.

**Pale-background-hills round 3 - three real fixes landed, symptom still not
resolved (2026-09-04, §7.15, `ODTerrainManager.cpp` +
`pipeline/build_terrain_mesh_tiles.py` + `unreal/apply_ground_material.py`):**
picked up the previous entry's "next place to look" (is the slope/treeline
rock-scree layer over-triggering on coarse background tiles, or legitimately
firing?) and worked it with numbers, not eyeballing.

1. **FAR-tile MIN-filter slope noise (real bug, fixed, ~zero visible
   effect).** `build_tile()`'s far-tile branch ran every FAR tile's
   10x-supersampled elevation grid through `downsample_min_grid()`
   unconditionally - a MIN filter over each output footprint. That filter
   exists for exactly one reason (2026-08-26 fix, see that entry): guarantee
   a carved road never gets buried by a coarse background tile's downsample.
   But it was firing on EVERY far tile, including ones with zero carve
   stations anywhere near them - background hillsides km from any road were
   having their elevation grid biased toward each footprint's local minimum,
   which inflates apparent slope/faceting versus the real terrain (a MIN
   filter over a sloped surface always steepens it). Fixed: added
   `FAR_MIN_INFLUENCE_M` (carve-outer-radius + 50 m) and a new
   `downsample_mean_grid()`; a tile now MIN-downsamples only within that
   radius of an actual carve station and MEAN-downsamples everywhere else
   (per-output-cell distance check against the tile's own station list, not
   a whole-tile flag, so a tile that's part-near-road/part-background gets
   both treatments correctly). Also added a dev-only `--bbox-cm=x0,y0,x1,y1`
   flag to `build_terrain_mesh_tiles.py` for fast scoped verification
   rebakes (skips `delete_stale_tiles()` the same way `--lakes-only`
   already does, so it never touches tiles outside the box). Verified via a
   49-tile scoped rebake covering the Silverthorne vantage - the fix is
   real and worth keeping (it's a legitimate background-terrain-fidelity
   bug independent of this task), but a pixel-diff against the pre-fix
   render showed **near-zero visible change** at the actual pale-hillside
   subject, so it is not the dominant cause of the reported symptom. A full
   production rebake was NOT run this session (only the scoped
   `--bbox-cm` verification regions were rebuilt) - the fix needs a real
   full-area rebake before it does anything for hills outside those boxes.
2. **`ElevLowCm` still valley-floor-level (real bug, fixed, ~zero visible
   effect at the specific sampled pixel).** The prior entry's `ElevHighCm`
   retune fixed where full grey BIAS LANDS, but left `ElevLowCm` at its old
   `-70000` (~2735 m - essentially Silverthorne/Frisco/Dillon's own valley-
   floor elevation). That meant `ApplyBiomeTint`'s elevation smoothstep
   started climbing the moment terrain rose above the valley floor at all,
   long before real treeline: at a measured 3268 m background-hill sample
   (below `apply_ground_material.py`'s own `TREELINE_LOW_M=3350`) the old
   band already gave `ElevS~=0.33` - a third of the way toward
   `ElevHighBias`'s flat neutral grey `(0.40,0.40,0.40)`. Raised `ElevLowCm`
   to `-8530` (exactly `TREELINE_LOW_M`, matching the material's own gate)
   so the vertex-tint elevation gradient can no longer out-run the
   material's own below-treeline look. Rebuilt clean (`Build.bat
   OpenDriveUEEditor Win64 Development`, 0 errors). Pixel-sampled the exact
   Silverthorne hillside subject before/after: RGB(108,111,112)/(110,113,113)-
   ish before vs RGB(106,111,113)/(108,111,112)-ish after - **no measurable
   change** at that specific pixel. Still a real, defensible fix (verified
   via the git-blame'd comment history that `ElevLowCm` really was never
   retuned alongside `ElevHighCm`), and will matter for genuinely
   mid-elevation hillsides elsewhere in the area, but not the dominant
   driver at this particular vantage.
3. **Scree layer's raw texture is measurably pale (real, evidenced, fixed,
   still ~zero visible effect at the specific sampled pixel).** Directly
   measured `content_src/textures/ground/Gravel030` (the "scree" layer,
   used both for the flat-ground `bare_amount` alpine-tundra blend and the
   steep-layer above-treeline pick) against the other ground layers:
   Gravel030 mean RGB (140.7,137.9,137.8)/255 - a near-neutral pale grey -
   versus Rock030 (79.4,76.6,69.9)/255, Grass001 (68.1,91.5,40.3)/255,
   Ground068/forest (110.9,91.3,42.9)/255. Gravel030's raw color closely
   matches the pale hillside's rendered RGB, confirming the original task
   brief's own hypothesis (the layer samples a genuinely pale raw PBR
   texture, by design, bypassing vertex tint). Applied the brief's
   suggested "subtle dark multiply" - `scree_albedo_tinted = scree.albedo *
   (0.78, 0.72, 0.62)` (22-38% per-channel darkening, biased warmer/less
   blue so it reads buff/tan talus rather than cool grey), wired into both
   consumers (`veg_albedo`'s flat-ground alpine blend and `steep_albedo`'s
   above-treeline pick), leaving `Rock030` (already darker/warmer) and
   `Snow006` untouched. Rebuilt the material (`apply_ground_material.py`,
   0 errors) and re-rendered Silverthorne: pixel-sampled the same subject
   hillside pixels and again found **no measurable change**
   (RGB(107,112,112) vs the pre-darken (108,111,112) - within noise).
4. **Ruled out atmospheric fog/aerial-perspective as a confound.**
   Re-rendered Silverthorne with `-ExecCmds="ShowFlag.Fog 0,
   ShowFlag.AtmosphericFog 0, ShowFlag.VolumetricFog 0"` - this DID reveal
   real detail that fog had been masking (the right-side ridge's dark
   faceted terrain, matching the FAR-tile tessellation discussed above), so
   the fog-disable itself worked, but the specific pale left hillside was
   pixel-identical with fog on or off. Atmospheric desaturation is not the
   cause.
5. **A fourth, more direct diagnostic - route raw `VertexColor` straight to
   `MP_EMISSIVE_COLOR` (base color forced black) so the rendered pixel IS
   the baked per-vertex tint with zero interference from albedo textures or
   the material's slope/treeline logic - came back blown out to flat white
   across nearly the entire terrain/ground plane in this render (buildings,
   trees and the car itself read correctly, unclipped), which is not a
   trustworthy signal on its own (an unlit-emissive diagnostic run through
   the scene's normal tonemapper/exposure pipeline, calibrated for a lit
   scene, is a known-unreliable technique - a same-family diagnostic
   blew out completely white the same way earlier this session with
   Tonemapper explicitly disabled). Not resolved into a confirmed
   root-cause finding; flagged here rather than over-claimed.

**Honest residual gap - THIS IS STILL NOT FIXED.** All three landed changes
are real, evidenced, defensible fixes to genuine bugs (background-tile slope
noise, a stale elevation constant, an unrealistically neutral scree color)
and are worth keeping, but **none of them moved the actual pale-hillside
pixels at either vantage measurably** - before/after RGB deltas were all
within ~2-3/255, i.e. render noise, not signal. Combined with the fog-disable
null result and the inconclusive raw-vertex-color probe, the weight of
evidence now points AWAY from "the ground material or the vertex-tint C++ is
painting these hills the wrong color" and toward one of two explanations
neither confirmed nor ruled out this session: (a) these specific background
ridges' real DEM elevation is genuinely at or above `ElevHighCm`/
`TREELINE_HIGH_M` (this area sits close to real 13-14k ft Rockies peaks a few
km from Silverthorne/Vail, so "full grey" may simply be accurate there and
the "pale" look is a stylization-target mismatch, not a bug), or (b) the
pale pixels are coming from a rendering element this session never
positively identified - possibly a different tile fidelity/material path
than the one every fix here targeted. Next session should NOT re-attempt
another blind constant/texture tweak; it should first positively identify
what's actually being rasterized at those exact screen pixels (e.g. a
`HitResultForScreenPosition` line-trace-and-log console command, or reading
back the actual carved DEM elevation at those specific world coordinates)
before changing anything else.

Verification renders (`-game -odhour=15`, scratchpad
`scree_fix_silverthorne.png` / `scree_fix_valley.png` / `scree_fix_dam.png`,
compared against this thread's `bg_fix_*.png` baselines - note **all three
prior renders in this file's history were captured without the `-game` flag,
which this session discovered means `AODMainMenu::BeginPlay` (and therefore
the `-odpick`/`-odgo` spawn logic) never runs at all**; every render that
looked like a normal gameplay screenshot in this file's history did in fact
use `-game` even though not always spelled out - a bare `UnrealEditor.exe
<uproject> -odpick=...` launch sits at the empty default-map editor
viewport indefinitely, which reads identically to the documented
"frozen at ZenServer" symptom from the shell log alone and cost significant
time this session before the real cause (missing `-game`) was found by
reading the engine's own `Saved/Logs/OpenDriveUE.log` instead of the
redirected stdout, which buffers far more coarsely):
- Silverthorne (Ruby Ranch Rd): background hills pixel-unchanged from
  `bg_fix_silverthorne.png` through all three fixes above.
- Vail (Piney Lake Rd): left-side mid-distance slope pixel-unchanged
  (~124,126,129 before vs ~123,124,128 after); the right-side slope visible
  through a gap in the tree line reads distinctly warmer/browner
  (~117-129, 102-121, 79-107), suggesting the "pale vs warm" split tracks
  something other than the fixes made this session (camera-relative
  lighting, real elevation, or biome-sample density on that specific slope
  face) rather than anything uniform this session touched.
- Dillon Dam Rd (regression check, `-odgo=5 -odspawnx=733226
  -odspawny=-807394`): roadcut still reads as light warm-grey rock, no
  green/vegetated bleed-through - no regression from the scree-layer
  darkening or the elevation retune.

Committed locally only (`ODTerrainManager.cpp`,
`pipeline/build_terrain_mesh_tiles.py`, `unreal/apply_ground_material.py`);
not pushed. (Two throwaway diagnostic scripts used mid-session,
`unreal/diag_probe2.py` and `unreal/diag_vtint.py`, were deleted before
committing - same "don't leave one-off diagnostics in the repo" pattern this
file's own history already established.)

### 7.16 Visible guardrails (2026-09-03/04)

**What this is:** a new safety-rail feature, same "pipeline ships points
only, C++ builds mesh at runtime" split as `build_road_barrier.py` /
`AODBarrierManager`. `pipeline/build_road_guardrails.py` walks every road's
stations, samples terrain a few meters laterally beyond the pavement edge
(reusing `build_terrain_mesh_tiles.sample_dem()`), and flags a run wherever
`road_z - terrain_z > 2.5m` sustained for >=10m (gaps bridged). Runs are
tagged with a road-class-derived profile: **Jersey barrier** (concrete,
`class_id` 0/1 - motorway/trunk) or **W-beam** (galvanized rail on HISM
posts, everything else). `export_road_guardrails_bin.py` packs the runs into
the same binary-sidecar tile format as every other streamed layer
(`guardrail_<area>_tile_X_Y.bin`). At runtime, the new `AODGuardrailManager`
(mirrors `AODBarrierManager`'s tile-streaming/extrusion pattern exactly)
extrudes each run into a `UDynamicMeshComponent` thin-sheet ribbon with real
`BlockAll` collision on `PM_GuardrailSlick` (a deliberately low-friction
physmat for the "deflect, don't grab" Crew 2 feel Taylor asked for), and
spawns posts on a `UHierarchicalInstancedStaticMeshComponent` along W-beam
runs. Wired into `AODGameMode::BeginPlay()` via `EnsureGuardrailManager()`,
same precedent as `EnsureParkingManager()`.

**Three exclusion bugs found and fixed by live-testing against Dillon Dam
Road (the feature's own primary use case — a real causeway with a genuine
sustained drop to the reservoir), each one discovered only because the
previous fix still left the dam causeway with zero guardrail data:**

1. **Blind `is_bridge` exclusion.** First draft copied
   `build_road_barrier.py`'s `is_bridge` skip verbatim. Dillon Dam Road is
   itself tagged `is_bridge=True` (a causeway, not a "there's a separate
   bridge-railing system for this" case — there is no separate system in
   this project), so the exclusion silently killed the whole test case.
   Removed `is_bridge` from `open_mask` entirely; `is_bridge` already
   despikes upstream in `road_mesh_common`, so nothing legitimate depends on
   this skip.
2. **Lake shore-margin over-exclusion.** `build_road_barrier.py`'s lake
   check dilates by `WATER_SHORE_BLEND_M + 3m` (right call for an opaque
   wall's aesthetics). The dam causeway runs directly along the shoreline,
   so every station in its real 530m/107-station drop run fell inside that
   margin and got skipped anyway. Narrowed the guardrail's own lake check to
   strict point-in-polygon only (the margin is kept solely as a bbox
   pre-filter for the candidate search, not the actual inclusion test) — a
   safety rail belongs at the literal shoreline, not 20m back from it.
3. **Blanket settlement-tile exclusion.** Even after fixes 1-2, the dam
   causeway sits inside the same coarse 2km settlement tile as
   Dillon/Frisco's real town streets, so a blanket "skip runs inside
   populated tiles" check ate the whole run a third time. The original task
   brief itself preferred junction-breaking over blanket settlement
   exclusion ("break runs where another road connects" rather than "skip
   the whole tile"), and flat town streets never trigger `drop_mask` in the
   first place — there's no drop to guard against. Removed the
   settlement-tile check entirely; final `open_mask = in_lake |
   seg_junction` (junction-proximity breaking is the only other exclusion,
   reusing `reconcile_junction_elevations()`'s `touched` mask so rails don't
   run straight through intersections).

**Final pipeline run (`summit_county`):** 1629 guardrail run(s), 71575
station(s) -> `road_guardrails.json` -> 194 tile(s). ~342.82 km W-beam,
~6.91 km Jersey barrier. Skipped near drop: 118 station(s) lake-interior,
15447 station(s) junction-proximity (bridge/culvert spans and settlement
tiles are deliberately NOT skipped — see the module's own docstring for the
full reasoning above).

**Verification (`-game -odhour=15`, scratchpad `rails_*.png`):**
- `rails_dam.png` (Dillon Dam Road, `-odgo=5 -odspawnx=900213
  -odspawny=-937209`, ~8m from a known rail station): a clean galvanized
  W-beam guardrail correctly seated at the pavement edge with visible posts,
  running along the causeway shoulder exactly where the sustained
  road/terrain drop from fixes 1-3 lives. This is the shot that proves all
  three exclusion bugs above are actually fixed, not just that the pipeline
  printed a bigger number.
- `rails_switchback.png` (Swan Mountain Road, Frisco): a correctly-profiled
  W-beam rail curving along the outer edge of a switchback, seated on real
  terrain (not floating/buried). Individual posts weren't visually
  distinguishable at this camera distance/angle despite the manager log
  confirming 1217 posts built in-tile — noted honestly rather than claimed
  as verified.
- `rails_i70.png` (I-70 near Frisco, `-odgo=5 -odspawnx=458943
  -odspawny=-477571`): a light-gray Jersey concrete barrier along the
  highway shoulder, correct profile for a motorway-class road.
- `rails_town_negative.png` (`-odpick=Frisco`, North 6th Avenue): flat town
  street, buildings, no rails anywhere in frame — confirms the feature
  doesn't fire where there's no real drop, and that removing the blanket
  settlement exclusion (fix 3) didn't cause false positives on ordinary flat
  streets.

**Getting a good `rails_dam.png` took 6 spawn-point attempts** (documented
here so a future session doesn't repeat the trial-and-error): the naive
approach of spawning directly at a rail run's own offset coordinates
embeds the car in the barrier mesh; spawning at the nearest *road centerline*
station instead avoids that, but `AODMainMenu::StartAt()` only computes a
fresh heading from a town-node snap or the level's `PlayerStart` — when
`-odspawnx`/`-odspawny` override the position, the yaw is NOT recomputed
from the new location, so the camera can end up facing away from the very
rail you spawned next to. There's no shortcut around this other than
picking a spawn point close enough to the target rail (station-level
lookup against `road_stations.json`, cross-referenced with
`road_guardrails.json`'s own station coordinates) that whatever fixed yaw
you get still points roughly down the road toward it.

Committed locally only (`ODGuardrailManager.cpp`, `ODGuardrailManager.h`,
`ODGameMode.cpp`, `ODGameMode.h`, `pipeline/build_road_guardrails.py`,
`pipeline/export_road_guardrails_bin.py`, `pipeline/rebuild_area.py`,
`unreal/apply_guardrail_assets.py`); not pushed.

---

## Terrain-variety queue (2026-09-07) — "why does it all look the same"

*Taylor's question: "how do we make the terrain not all look the same? fauna?
shading? different textures?" Root-cause answer first, then the plan. This
queue also absorbs the pending pale-hills fix (immediate-queue item 6's
follow-through) since the same edits carry both.*

### Diagnosis — why it's monotone today

The variety systems mostly EXIST but are being fed uniform data:

- **The biome pipeline is real but degenerate for summit_county.**
  `classify_biomes.py` → `road_biomes.json` is 27,465 samples but **87%
  Evergreen** (23,993; next largest Canyon 1,023). One biome ⇒ one ground
  tint (`BiomeGroundTintTable`) and one forest recipe almost everywhere.
- **Biome samples are corridor-only** (classifier gates to road bbox +2 km
  by design). `ApplyBiomeTint` gives up beyond `FarBiomeFallbackCm` (1 km)
  and applies NO tint — so the background hills, the thing you stare at most,
  are exactly the vertices the biome system never reaches. This is the
  mechanism behind the old `distinctBiomes=0` observation.
- **The ground material's flat layer is one grass/dirt blend everywhere**
  (`apply_ground_material.py`); at distance its mips average to the pale
  warm-khaki measured in item 6 (Vail carrier: flat albedo 94,95,88).
  `value_noise()` exists but is only used for snow breakup — no macro
  albedo variation.
- **Aerial perspective** (item-6 Silverthorne carrier) desaturates whatever
  variety survives to the far field.
- The forest side is actually rich (7-species `SpeciesWeight` recipes,
  `TreeDensityScale`, shrub/ambient-veg floors per biome) — it's starved by
  the same 87%-one-biome input, not missing features.

So: not "add fauna vs shading vs textures" — **fix the variation DATA, then
add macro albedo variation in the material, and the existing tint + forest
systems light up on their own.** Plan, in dependency order:

### V1. Off-corridor biome coverage *(1 session — unblocks everything visual
behind the corridor)*
**Status 2026-09-25: done (300 m grid, ODB2 re-bake, offline-verified) — see §7.35.**
Extend `classify_biomes.py` with a coarse full-bbox sample grid (the z13
imagery mosaic is ~19 m/px and cheap; classify on a ~200–400 m grid across
the whole terrain bbox, keep the dense corridor samples as-is) and write
both into the sidecar (`export_biome_bin.py` format is grid-agnostic —
just more points). Raise/parametrize `FarBiomeFallbackCm` if needed.
Gate: per-tile `distinctBiomes` log > 0 on background-hill tiles;
render-matrix diff shows background tint actually moving.

### V2. Within-biome variation drivers: aspect + moisture *(1 session)*
**Status 2026-09-25: done (ODB2 sidecar, tint + forest bias), offline-verified;
palette needs Taylor's eyes — see §7.35.**
87% Evergreen is CORRECT classification — the variety inside a real
evergreen zone comes from aspect and moisture, not land-cover class. Compute
per-sample **aspect** (from the DEM normal; south-facing → dry/tan/sparser,
north-facing → darker/denser) and a **valley-bottom moisture proxy**
(elevation above local minimum within ~1 km, and/or distance to
`build_water_tiles.py` water) in `classify_biomes.py`; extend the ODB1
sidecar record (bump magic or append fields — C++ parser is versioned by
magic) and consume in `ApplyBiomeTint` as a tint modulation on top of the
biome base color, and in `AODForestManager::BlendAt` as density/species
bias (aspen/meadow on moist north aspects and valley bottoms). This is the
single highest-leverage item for "real Colorado hillside" reads.

### V3. Material macro-variation + distant-flat darkening *(1 session —
doubles as the Vail pale-hills fix)*
**Status 2026-09-25: implemented + material re-applied, offline-verified;
awaiting Taylor's live look — see §7.36.**
In `apply_ground_material.py`: add a 2-octave world-space `value_noise`
(~300 m and ~1200 m periods) lerping the flat albedo between 2–3 variants
(dry meadow / green meadow / bare dirt) and darkening+saturating the blend
toward distance so far mips stop averaging to pale khaki. Keep it to the
flat layer; slope/rock/snow blends stay as shipped. Commandlet re-apply +
render matrix (Silverthorne/Vail/Frisco/Dillon) with hue/sat variance
stats over hillside crops — variance should INCREASE, mean value DROP
slightly at distance.

### V4. Aerial-perspective tuning *(hours — the Silverthorne pale-hills fix)*
**Status 2026-09-25: first step in (view-distance scale 1.0 → 0.8 in
ODAtmosphereController.cpp, compiled); parameter-only, needs Taylor's eyes —
see §7.36.**
Reduce the SkyAtmosphere COMPONENT's aerial-perspective contribution
(aerial perspective view distance scale / Mie scattering) in
`build_openworld_test_level.py` or wherever the component is configured —
NOT the `r.SkyAtmosphere.AerialPerspective.StartDepth` cvar (proven silent
no-op via -ExecCmds, see item-6 results). Small steps; this is mood/legibility,
not variety, and too much kills depth cueing. Needs Taylor's live eyes for
final level (same rule as audio character).

### V5. Vegetation banding polish *(1–2 sessions, after V1+V2 land)*
**Status 2026-09-25: first pass in (elevation bands + aspen patches on existing
aspen meshes), offline-verified; aspen read needs Taylor's eyes — see §7.35.**
With real biome + aspect/moisture data flowing, tune the summit_county
read: elevation-banded species mix within Evergreen (lodgepole low,
spruce-fir high, krummholz at treeline — recipe interpolation by elevation,
not new enum entries), and **aspen patches** driven by the V2
moisture/aspect signal + a coarse noise mask (bright green tint variant of
an existing broadleaf species first; dedicated mesh only if the tint
variant reads badly). This is the biggest "postcard" win but is pure
tuning until V1/V2 exist.

**Order: V1 → V2 → V3 (V4 anytime; V5 last).** V1+V2 are pipeline+C++ data
work with objective gates; V3/V4 close out both pale-hills carriers from
item 6 as a side effect; V5 is art tuning that needs Taylor live.

**Decision points for Taylor before/while starting:**
- Palette direction is subjective — V3's meadow variants and V5's aspen
  read need a live look at some point; headless pixel-stats gate the
  mechanism, not the taste.
- V1 full-bbox imagery fetch adds a modest one-time download (z13 over the
  full terrain bbox vs. corridor-only today) — assumed fine.
- ODB1 sidecar format change (V2) forces a `classify_biomes` +
  `export_biome_bin` re-run and an editor rebuild — cheap, but flags the
  usual "pipeline output and C++ must move together" coupling.

---

## Drive-test fix queue (2026-09-07) - CLOSED

All six of Taylor's live-drive findings fixed and committed same day:

1. **Giant retaining-wall slabs** (Copper, Piney Lake Rd) - f12813c.
   Union-Find run percolation + single-PCA ruled surface; fixed with
   spatial-gap split (40m) + recursive extent bisection (250m cap),
   hard max-edge gate. Verified clean in renders at both sighting spots.
2. **Respawn slow / not on road** - 5e237b4. Warm settle (0.75s vs 3s)
   when asphalt is already resident at the reset target.
3. **Stuck behind guardrails** - 5e237b4. Throttle-but-not-moving
   trigger (>50% throttle, <60cm/s, 2.5s) fires the nearest-road reset
   with a "STUCK - RETURNING IN N" HUD countdown; guardrail profiles
   also raised ~25% (W-beam 92cm, Jersey 100cm) so rails are harder to
   hop in the first place.
4. **Streaming frame drops** - 08c8056. Forest burst ms-budgeted
   (2.5ms/frame), guardrail/wall/building collision cooks moved async,
   building deferral queue batched (was: full tile re-cook per resolved
   building), bulk AddInstances, per-tile build logs -> Verbose.
   NOT yet re-measured with a frame-time capture on a scripted drive -
   do that if Taylor still feels drops.
5. **Inside-out rocks** - 5e237b4. OBJ writer mirrored Y without
   reversing winding; one-sided M_Rock030 exposed what the two-sided
   car/tree materials mask. Winding fixed at the writer, boulders
   re-imported, verified in-game.
6. **Cold-load blank screen** - 2fb58bf. 25s loading-screen failsafe
   released into a still-empty world on cold disk loads; ceiling now
   60s (settle-based release unchanged, warm loads unaffected).

## Second drive-test report (2026-09-07 evening) - CLOSED

Taylor's post-launch report, both fixed + render-verified same day:

1. **Junction pads still look bad** (Lone Pine Circle giant flat brown
   amoeba blob, no markings) - c498718. Three compounding causes in
   experimental_junction_pads.py: constant pad UV (0.58,0) collapsed
   every pad to one texel under the procedural M_Road (flat brown, no
   detail); merge_drawn_overlaps had no absolute size cap (only a
   per-member area ratio that scales with member count); and the 12m
   partner-proximity rule deleted a loop street's whole marked ribbon,
   replacing it with the chain of unmarked 6.5m fill discs that WAS the
   amoeba outline. Now: world-derived per-vertex pad UVs confined to
   the marking-free meterPos band [0.5,2.0]m, a 25m semi-axis cap on
   drawn-overlap fusion, and proximity omission scoped to pad reach
   +25m of a junction pairing those segments. Rebaked (1322km, 561
   pads all <=23.8m semi-axis), verified at Lone Pine.
2. **"Terrain above ground" black marbled wedge** (Tennis Club Road) -
   5dc829a. Not terrain: degenerate retaining-wall slabs from
   build_retaining_walls.py (union-range sampling clamp-collapsed rows
   onto single vertices -> 24,000 m^2 fans; 1,743/1,841 shipped chunks
   had degenerate tris feeding garbage normals into M_GroundStylized's
   slope blend = black marble). Now: intersection-range sampling with
   10m slack, 30m max row span, zero-area tris dropped. Rebaked (wall
   area 15.27 -> 1.13 km^2), verified at both Tennis Club vantages.

Also hardened same evening: 8fbcd11 - warm-settle respawn fast path
can never apply to the initial cold-boot spawn (bEverGroundSnapped
gate; the first spawn always takes the full 3s Chaos-registration
settle).

Watch item from the verification renders: at the US 6 / Tennis Club
junction the road sits visibly above terrain with an exposed skirt
step in the foreground - not the reported bug, but worth a look on the
next junction/terrain pass.

### 7.17 Seasons, first half: clock + ground/lake/foliage winter (2026-09-07)

Builds a continuous, looping season **clock** (not a picker) per The Crew
2's stylized-realistic direction: a 4-real-time-hour "year" loops
summer -> fall -> winter -> spring -> summer, each season holding stable
for 45 min then blending gradually into the next over 15 min (nothing
snaps - every parameter is a smoothstep-eased ramp). This pass covers
ground snow, falling-snow weather, evergreen canopy snow, lake freeze, and
spring melt/green-up. Deciduous-tree fall color/drop is explicitly **out
of scope** for this pass - `FallBlend` is computed by the clock and pushed
to the foliage MID every tick, but nothing consumes it yet; it's left
wired and ready for a follow-up task to build the deciduous arc on top of
without touching the clock again.

**Architecture** - `AODSeasonController` (`OpenDriveUE/Source/OpenDriveUE/
ODSeasonController.h/.cpp`), a runtime-spawned singleton actor following
the same "find-or-spawn from `AODGameMode`" shape as
`AODDayNightController`/`AODAtmosphereController`
(`AODGameMode::EnsureSeasonController`, `ODGameMode.h/.cpp`). Its `Tick`
advances a `ClockSeconds` position around a 14400 s loop (`4 *
(HoldSeconds + TransitionSeconds)`, both `EditAnywhere` and both default
to their documented values), runs it through the pure function
`ComputeParamsForClock` to get five blends (`FallBlend`, `SnowCover`,
`SnowfallIntensity`, `FreezeBlend`, `SpringGreen`), and pushes them
(`PushParamsToMIDs`, throttled to `ParamPushHz` = 3 Hz - see the perf note
below) into MIDs the ground/water/foliage managers already own:
`AODTerrainManager::GetGroundMID`, `AODWaterManager::GetWaterMID`,
`AODForestManager::GetFoliageSeasonMID`. No new
`MaterialParameterCollection` - the project has never had one, and only
three materials/one HISM batch need reaching, which the established
per-manager-MID + C++-setter convention already covers fine.

A `MaterialParameterCollection` was considered and rejected for the same
reason `ODSeasonController.h`'s own class comment gives: not enough
distinct materials to justify the project's first MPC.

**QA flags** (mirrors the existing `-odseason`/day-night QA-flag idiom):
- `-odseason=summer|fall|winter|spring` - freezes the clock at the START
  of that season's 45-min hold (`bFrozen = true`, `Tick` stops advancing
  it). `SeasonHoldStartSeconds()` maps the name to its loop-position.
- `-odseasonphase=<0..1>` - only meaningful combined with `-odseason=`;
  offsets further into that season's hold-then-transition span (0 = start
  of hold, ~0.75 = start of hold + most of the transition into the next
  season - used for `season_fall_to_winter_25`/`_75` at phase 0.25/0.75,
  and `season_melt_50` at `-odseason=winter -odseasonphase=0.5`, which
  lands mid-transition into spring).
- `-odseasonrate=<x>` - `TimeScale` multiplier on the un-frozen clock (1.0
  = real time), for accelerating a live/interactive session instead of
  freezing it.

**What each material/MID consumes:**
- Ground (`unreal/apply_ground_material.py`, `M_GroundStylized`) - ground
  snow deliberately **reuses the existing** `SnowLineLowZCm`/
  `SnowLineHighZCm` scalar params rather than adding a new one: sweeping
  both down together (same band width, so the material's own ~40 m
  value-noise patchiness comes along for free) from their baked defaults
  down to a floor well below the area's real valley floor IS the winter
  ground-snow mechanic. Leaving them at the baked defaults (`SnowCover=0`)
  is byte-for-byte the pre-existing altitude snow band - the summer
  baseline this task requires to be an exact zero-regression match. Also
  consumes `SpringGreen` for a fresh-green tint ramped in during the
  spring transition.
- Water (`unreal/apply_water_material.py`, lake material) - consumes
  `FreezeBlend` (opacity/color/roughness shift toward an icy look).
  **Not visually confirmed this pass** - the required Dillon vantage
  (`-odspawnx=733226 -odspawny=-807394`) never has the lake in frame (it's
  occluded by the guardrail/embankment/treeline in every screenshot at
  every season), so `FreezeBlend`'s wiring is code-reviewed and pushed
  correctly (same `PushParamsToMIDs` pattern as everything else) but has
  no photographic proof from this vantage. A future pass should verify it
  from a lake-facing spawn point.
- Foliage (`unreal/build_flora_materials.py`, `M_Foliage`) - consumes
  `SnowCover` for canopy snow (Conifer species only,
  `ConiferSpeciesIndex` in `ODForestManager.cpp`) via a `Lerp` into
  `BaseColor` between the atlas color and a pale
  `LinearColor(0.92,0.94,0.97,1)`, gated by `SnowCover *
  desync(PerInstanceRandom)` so different tree instances pick up snow at
  slightly different points in the transition rather than all snapping
  together - `desync` lerps `PerInstanceRandom` into `[0.55, 1.0]` so
  no instance is fully snow-free once winter is under way, but some are
  visibly "ahead" of others. Also exposes `FallBlend` as an
  unconnected `MaterialExpressionScalarParameter` for the follow-up
  deciduous task.
- Falling snow (`unreal/apply_snow_particle_material.py`, `M_SnowFlake`,
  plus `AODSeasonController`'s own `SnowFxHism`) - a plain camera-attached
  HISM of tiny spheres the controller animates by hand in `Tick`, not
  Niagara (headless Niagara-asset creation from python is finicky in UE
  5.8, and a from-scratch C++ Niagara system still needs an authored data
  asset - a HISM the controller owns directly needed no editor session or
  asset pipeline step). `M_SnowFlake` must be `MSM_DEFAULT_LIT`, not
  `MSM_UNLIT` - unlit renders solid black in this project's lighting
  setup, a gotcha hit and fixed before this continuation.

**Bug found and fixed this pass - canopy-snow material-slot mismatch:**
canopy snow initially showed *zero* visible effect despite every other
part of the pipeline (MID creation, `SnowCover` push, Conifer-species
targeting, MID-to-HISM assignment, Base-Color/Emissive graph wiring - each
independently confirmed via debug logging and an emissive-bypass
diagnostic) checking out correct. Root cause: `import_biome_flora.py`'s
material-slot contract puts opaque bark at slot 0 and the alpha-cutout
foliage cards at slot 1 for every two-slot tree mesh
(`BARK_SLOT, FOLIAGE_SLOT = 0, 1`, see that file's module docstring), but
`ODForestManager.cpp`'s Conifer branch was hardcoded to
`Hism->SetMaterial(0, FoliageSeasonMID)` - slot 0, i.e. the **bark**
slot. The MID was landing on bark (invisible under the tree's own crown
draw order) while the actually-rendered foliage cards at slot 1 kept using
the original static `M_Foliage` reference with `SnowCover` stuck at its
0.0 asset default. Fixed by changing the call to
`Hism->SetMaterial(1, FoliageSeasonMID)`. Two other now-ruled-out HISM
vertex-factory gotchas surfaced while chasing this, worth recording for
future foliage work: `MaterialExpressionLocalPosition.Z` evaluates as a
flat/uniform value for this crossed-billboard-card HISM vertex factory
(unusable as a per-vertex height metric), and `WorldPosition().Z -
ObjectPositionWS().Z` does not resolve to a genuine per-instance pivot
either (patchy, HISM-occlusion-cluster-shaped banding, not per-tree-crown
shaped) - both ruled out a per-vertex "only the crown top gets snow"
design in favor of the simpler uniform-per-instance-tint approach
described above.

**Perf note:** `ParamPushHz = 3.0f` keeps the MID pushes to 3/sec rather
than every tick, since per `docs/perf_after_art_overhaul.md` volumetric
fog and foliage overdraw have the least headroom in this project's frame
budget. A sanity `-odtrace` pass (ODDrivetrain heartbeat, spring render,
~215 s span) showed consistent ~15-18 ms/frame (55-67 fps), no stalls, in
line with the project's existing baseline - no perf regression evident
from the season system.

**Screenshots (Dillon Dam Road vantage, `-odspawnx=733226
-odspawny=-807394`, `-odgo=5`), honest per-shot verdicts:**
- `season_summer.png` (`-odseason=summer`) - baseline: green canopy, no
  ground snow, no falling snow. Matches pre-season behavior exactly (the
  zero-regression requirement).
- `season_fall_to_winter_25.png` / `_75.png`
  (`-odseason=fall -odseasonphase=0.25`/`0.75`) - falling snow visible and
  intensifying between the two; ground snow line creeping down. Canopy
  color itself doesn't change here (deciduous fall-color is next task's
  scope; conifers don't change color in fall).
- `season_winter.png` (`-odseason=winter`) - ground snow-cover fully in,
  falling snow, **and now genuine canopy snow** after the slot-mismatch
  fix above: close-in conifers show visible pale/gray-white patches mixed
  into the green canopy, varying instance-to-instance per the
  `PerInstanceRandom` desync. Confirmed via a tight pixel-crop zoom, not
  just eyeballing the full frame.
- `season_melt_50.png` (`-odseason=winter -odseasonphase=0.5`) - ground
  snow fully retreated to bare grass with a visible fresh-green tint,
  falling snow still present but visibly tapering. Reads correctly as
  "mid-thaw."
- `season_spring.png` (`-odseason=spring`) - ground fully melted and
  green, no falling snow, green canopy. Matches the intended spring
  baseline.
- Lake freeze - **not verified**, see the water bullet above; the lake
  never enters frame at this vantage in any season.

**Not touched:** vehicle physics/driving surfaces, `cars_manifest.json`
(byte-identical), and the five uncommitted road/junction files already in
the working tree before this task (`pipeline/build_road_network.py`,
`pipeline/build_road_stations.py`, `pipeline/road_mesh_common.py`,
`pipeline/build_road_guardrails.py`, `unreal/apply_road_material.py`) -
confirmed via their on-disk mtimes predating this session.

**Polish pass (2026-09-08) - three defects fixed, no redesign:**

1. **Melt was not gradual - fixed, two root causes.** At
   `-odseason=winter -odseasonphase=0.5` the ground rendered as
   already-fully-green instead of a genuine patchy half-and-half, and the
   same compression showed up at every checkpoint in both directions
   (accumulation and melt). Two independent bugs, both in the
   `SnowLineLowZCm`/`HighZCm` sweep:
   - **Wrong reference elevation.** `SnowLineLowForCover`'s identity-ramp
     solve (`Low(S) = (RefZ - S*High(S)) / (1-S)`, chosen so the raw
     crossing fraction at `RefZ` equals `SnowCover` exactly, keeping the
     snow line's *visible* progress proportional to `S` regardless of
     where the low/high band actually sits) used a hardcoded guessed
     "valley floor" constant for `RefZ` that didn't match the real terrain
     elevation at the QA vantage - so the crossing window landed off-screen
     (saturated white or green) at every tested `SnowCover` value instead
     of centered on the camera. Fixed in `ODSeasonController.cpp` by
     computing `RefZ` dynamically each push from
     `UGameplayStatics::GetPlayerPawn(this, 0)->GetActorLocation().Z` (the
     old constant survives only as `SnowValleyRefZFallbackCm`, used before
     a pawn exists). `SnowLineLowIdentity`/`SnowLineLowForCover` now both
     take `RefZ` as a parameter instead of reading a file-scope constant.
     A one-shot `-odtrace`-gated diagnostic log (`bLoggedSnowDebugOnce`,
     `ODSeasonController.h/.cpp`) prints `SnowCover/RefZ/Low/High/Band`
     once per run for future debugging of this path - left in place since
     it's harmless, gated, and one-shot.
   - **Degenerate noise hash at this area's world coordinates.** Even with
     the crossing window correctly centered, the transition looked like a
     uniform pale wash, not chunky patches. `apply_ground_material.py`'s
     `value_noise()` uses the classic `frac(sin(dot(cell, (12.9898,
     78.233))) * 43758.5453)` hash trick, which is only numerically stable
     for small dot-product inputs. This area's absolute world coordinates,
     divided by `SNOW_NOISE_SCALE_CM`, land the lattice cell index around
     (183, -202) - large enough that the hash's own dot product lands in
     five-figure radians, where GPU `sin()` precision collapses and the
     hash goes near-degenerate/near-constant. Confirmed empirically:
     raising `SNOW_NOISE_STRENGTH` alone from 0.5 to an extreme 4.0 barely
     moved rendered ground-coverage in a controlled test (1.24% ->
     2.38% green pixels) - the fingerprint of a saturated hash input, not
     "not enough amplitude." Real fix: a new `wrap_lattice_uv()` helper
     wraps the lattice-space UV into a small repeating `[0, 97)` cell
     range (via `frac(uv/period)*period`) *before* it reaches the hash,
     keeping the hash's input small and stable while still varying at the
     same ~40 m cell size. Combined with a moderate `SNOW_NOISE_STRENGTH =
     1.3` (up from 0.5), this produced genuine, noise-broken,
     roughly-proportional coverage: at `SnowCover=0.5` measured ground
     green-pixel coverage went 1.24% (broken) -> 2.38% (extreme strength
     alone, still broken) -> 17.09% (with the lattice wrap), with a
     visibly wavy/textured snowline under a 2x crop, not a hard edge.
   - Verified via `-odseason=winter -odseasonphase=0.25/0.5/0.75` (melt
     direction) and `-odseason=fall -odseasonphase=0.25/0.75` (accumulation
     direction, `SnowCover` 0.156/0.844 respectively - the same two values
     melt hits at phase 0.75/0.25 by construction) from the Dillon Dam Road
     vantage: phase 0.25 reads mostly snowy with patches opening, phase 0.5
     is a genuine half-and-half with a noise-broken boundary, phase 0.75
     reads mostly green with white drifts surviving near the treeline: all
     four screenshots (`polish_melt_25/50/75.png`, `polish_accum_25.png`)
     plus the accumulation-direction mirror (`polish_accum_75.png`, mostly
     snowy with a little green surviving near a foreground bush) match the
     qualitative spec.
2. **Canopy snow strengthened.** Conifers read plain green at driving
   distance despite `SnowCover` reaching the foliage MID correctly.
   `build_flora_materials.py`: widened the `PerInstanceRandom` desync
   floor from 0.55 to 0.82 (instances start visibly snowing sooner in the
   transition while keeping per-instance desync), added a
   `snow_amount_boosted` step that multiplies the per-instance snow amount
   by 1.45x before clamping to `[0, 1]`, and added a matching winter blend
   into `MP_SUBSURFACE_COLOR` (`snowed_subsurface`, gated the same way as
   the base-color blend) so two-sided foliage's own green subsurface
   scatter stops fighting the white base-color tint from behind/backlit
   angles. `SnowCover=0` still drives every one of these blends to exactly
   the unmodified summer/spring asset default. Verified via
   `polish_winter_trees.png`: far/background conifers are dramatically
   white-capped at a glance, close conifers show natural
   instance-to-instance variation (some heavily frosted, some still mostly
   green) consistent with the intentional desync, and
   `polish_summer_baseline.png` re-confirms zero change at `SnowCover=0`.
3. **Lake freeze visually verified** (previously an open gap - the Dillon
   Dam Road vantage never has the lake in frame). Found a shoreline vantage
   that does: `-odspawnx=1374341 -odspawny=-846975` (Swan Mountain Road,
   Keystone). No material code changes were needed - `apply_water_material
   .py`'s existing `FreezeBlend` mechanics (pale desaturated ice color,
   raised roughness, ripple-flattening) were already correct. `polish_lake_
   winter.png` shows the lake unmistakably frozen (pale gray-white, flat,
   no visible ripples); `polish_lake_summer.png` at the same vantage and
   spawn shows unmistakably liquid water (blue-teal, visible ripple
   texture) at `FreezeBlend=0`, confirming no regression.

Files touched this pass: `OpenDriveUE/Source/OpenDriveUE/
ODSeasonController.h/.cpp` (dynamic RefZ + one-shot debug trace),
`unreal/apply_ground_material.py` (`wrap_lattice_uv`, `SNOW_NOISE_STRENGTH`
0.5 -> 1.3), `unreal/build_flora_materials.py` (desync floor, snow-amount
boost, subsurface-color winter blend). `unreal/apply_water_material.py`
reviewed, not modified. Vehicle physics, `cars_manifest.json`, and the
uncommitted road/junction files listed above remain untouched.

**Follow-up task for a second agent:** build the deciduous-tree fall-color
arc on top of `FallBlend`, which is already computed by the season clock
and already pushed to `M_Foliage`'s MID every tick
(`AODSeasonController::PushParamsToMIDs`) - it's exposed as a
`MaterialExpressionScalarParameter` named `FallBlend` in `M_Foliage`
(`unreal/build_flora_materials.py`) but deliberately left unconnected.
That task should also decide whether to extend the slot-1 foliage-MID
approach to the other (non-Conifer) tree species batches, which currently
have no MID assigned at all (`ODForestManager.cpp`'s conditional is
Conifer-only).

Committed locally only (`OpenDriveUE/Source/OpenDriveUE/
ODSeasonController.h`, `ODSeasonController.cpp`, `ODGameMode.h`,
`ODGameMode.cpp`, `ODTerrainManager.h`, `ODTerrainManager.cpp`,
`ODWaterManager.h`, `ODWaterManager.cpp`, `ODForestManager.h`,
`ODForestManager.cpp`, `unreal/apply_ground_material.py`,
`unreal/apply_water_material.py`, `unreal/build_flora_materials.py`,
`unreal/apply_snow_particle_material.py`); not pushed.

**Deciduous-arc follow-up, delivered (2026-09-08, commit `1b39ada`):**
completes the task flagged above - `FallBlend` is now actually wired, and
tree_blob (aspen) runs the full summer -> orange -> bare-twig-with-snow ->
green-rebud cycle. Deciduous-only, by design: conifers (`tree_olive`,
`tree_conifer`, `tree_yucca`, `tree_canyon_oak`, `tree_manzanita`) never
turn orange or drop leaves; `tree_scrub_oak` (also aspen-derived) was
explicitly left out of scope too, since it has no branch geometry to
dissolve down to.

*What changed:*
- `pipeline/make_tree_meshes.py` - `tree_blob` (`build_aspen(...,
  with_branches=True)`) gets a real bare-twig branch structure
  (`add_deciduous_branches()`/`Mesh.branch()`): 7 tapered 4-sided tube
  branches fanned from the trunk top up into the crown volume, sharing the
  trunk's own bark material (no new material slot). +84 tris/tree
  (116 bark tris total vs. 32 for the branch-less `tree_scrub_oak`,
  well under the <100-tri budget for this feature) - see
  `verify_tree_mesh.png`.
- `unreal/build_flora_materials.py` - wires the previously-inert
  `FallBlend` scalar param into `M_Foliage`'s graph, and adds a new
  `LeafDrop` param: both are driven by a shared "turn threshold" =
  `PerInstanceRandom * 0.5 + patch_value_noise(WorldPosition, 180cm
  cells) * 0.5`, soft-thresholded over a 0.30-wide band, so patches turn
  orange/drop at different points rather than the whole canopy flipping at
  once (see `verify_fall_tint.png`, `verify_canopy_dissolve.png`).
  `FallBlend` lerps the sampled atlas color toward a stylized aspen-orange
  and feeds the pre-existing canopy-snow lerp's "before snow" input;
  `LeafDrop` multiplies down the alpha-cutout opacity so the
  `opacity_mask_clip_value=0.35` cutout progressively discards more of the
  card, revealing the bare branch mesh underneath. `M_Bark_Aspen` gets a
  matching chunky patch-threshold `SnowCover` wiring (~35cm noise cells) so
  snow reads as "bunches" on the twigs, not a flat coat - opaque bark
  materials for every other species are untouched.
- `OpenDriveUE/Source/OpenDriveUE/ODForestManager.h/.cpp` - two new
  dynamic MIDs, `DeciduousFoliageMID` (off `M_Foliage`) and
  `DeciduousBarkMID` (off `M_Bark_Aspen`), assigned to the `tree_blob`
  species batch only (`DeciduousSpeciesIndex = 0`) at both HISM material
  slots (0 = bark/branches, 1 = foliage cards) in `BuildTile()`. The
  Conifer batch keeps its own separate `FoliageSeasonMID` instance off the
  same `M_Foliage` base material.
- `OpenDriveUE/Source/OpenDriveUE/ODSeasonController.h/.cpp` - adds
  `LeafDrop` as a sixth computed param (`ComputeParamsForClock` signature
  extended) and sequences it against the existing five: `FallBlend` ramps
  0->1 across the summer->fall transition and HOLDS at 1 through the
  fall->winter transition (leaves fall off orange, they don't fade back to
  green first); `LeafDrop` ramps 0->1 across that same fall->winter
  transition (bare by the start of winter hold); both `FallBlend` and
  `LeafDrop` reverse together across the winter->spring transition (bare ->
  green re-bud, in lockstep with the melt/`SpringGreen` ramp so re-budding
  leaves render green, never orange). See `verify_year_params.png` for the
  full 4-hour timeline and the sequencing checks (orange-turn completes
  before leaf-drop starts; leaf-drop completes at/before the fall->winter
  transition's snowfall peak; winter->spring starts from `LeafDrop=1.0`).

*Bug found and fixed in this pass:* `ODSeasonController::PushParamsToMIDs`
was still pushing `FallBlend` onto the Conifer batch's `FoliageSeasonMID`
- harmless under the first seasons pass (the param was unwired), but with
`FallBlend` now actually driving an orange color-blend in the shared
`M_Foliage` graph, that stray push would have made evergreens turn orange
too. Fixed by removing that one push line; the conifers-never-turn-orange
guarantee now lives entirely in "which MID gets which parameter pushed,"
documented inline at that call site and in `verify_material_wiring.txt`.

**New verification protocol for this pass - offline only, no game launch.**
Per updated project direction, this task's acceptance gate is entirely
offline (matplotlib/numpy re-implementations and asset-data dumps, no
`-game` launches or gameplay screenshots), saved under `scratchpad/`:
- `verify_year_params.png` - numpy port of `ComputeParamsForClock`, all six
  params over the 4-hour loop plus a per-sample derivative plot. Confirms
  smooth ramps, correct season sequencing, and no discontinuities
  (max |d(param)/dt| bounded and finite at 4 Hz sampling).
- `verify_tree_mesh.png` - parses the regenerated `tree_blob.obj` directly:
  trunk+branch wireframe under canopy-card outlines from 3 angles, bbox
  dims in cm (12.2 m tall, in the expected meters-range), tri counts per
  material, and a branch-envelope check (branch tips fall inside the
  canopy's own Z span).
- `verify_canopy_dissolve.png` / `verify_fall_tint.png` - numpy ports of
  the exact material-graph noise/threshold math (value-noise hash,
  turn-threshold, soft-clamp), rendered as image grids across
  `LeafDrop`/`FallBlend` x 3 `PerInstanceRandom` values. Confirm patchy,
  desynced turn/drop and near-complete convergence at the 0/1 endpoints.
- `verify_material_wiring.txt` - a python dump (via
  `unreal.MaterialEditingLibrary`) of `M_Foliage`/`M_Bark_Aspen`/
  `M_Bark_Conifer`/`M_Bark_Willow`'s actual rebuilt graphs after running
  the real `build_flora_materials.py` commandlet: confirms `LeafDrop`/
  `FallBlend`/`SnowCover` exist as params on `M_Foliage`, `SnowCover`
  exists on `M_Bark_Aspen` only (not the other bark materials), and that
  `BaseColor`/`OpacityMask` are wired to computed nodes rather than the
  raw texture sample - plus the quoted `BuildTile()` MID-routing lines.
- `cars_manifest.json` confirmed byte-identical (unmodified in git status);
  the five uncommitted road/junction files from before this task
  (`pipeline/build_road_network.py`, `build_road_stations.py`,
  `road_mesh_common.py`, `build_road_guardrails.py`,
  `unreal/apply_road_material.py`) were left exactly as found and are not
  part of this commit.

**Honest verdict - what offline verification can and cannot prove:** the
math checks out exactly (the numpy ports are literal 1:1 translations of
the C++ segment logic and the material graph's noise/threshold nodes, not
approximations), and the rebuilt material graphs on disk demonstrably have
the params/wiring/isolation the code implies. What this CANNOT prove:
actual in-engine visual read (does the stylized orange look right, does the
dissolve read as "leaves falling" rather than "leaves fading," does the
twig-snow density look right at real camera distance, does the branch
geometry look convincing rather than sparse/spiky, is the transition pacing
good at real gameplay speed) - all of that needs an actual drive-test,
which is explicitly deferred per this task's protocol. One minor
quantitative wrinkle the verification DID catch and is worth knowing about:
at the extreme corners of the noise+desync space (e.g. `PerInstanceRandom`
near 1.0 combined with the patch-noise field's own max value), the 0.30-
wide soft-threshold band means a tiny sliver of canopy area (<0.2%) doesn't
finish quite as fast as the nominal 0/1 endpoints - e.g. mean drop/turn
amount of 0.9988 instead of an exact 1.0 in the worst-case row tested.
Imperceptible at normal viewing distance and not something this pass
attempted to rebalance (would mean touching the inherited 0.5/0.5 blend
weights or the 0.30 band width), but flagged here rather than silently
rounded away.

**Drive-test flags for Taylor** (each freezes the clock at a specific
point so the deciduous arc can be inspected live, same `-odseason=`/
`-odseasonphase=` idiom as the rest of the season system; combine with
`-odspawnx=733226 -odspawny=-807394 -odgo=5` for the Dillon Dam Road
vantage used throughout this feature, or any vantage with `tree_blob`
aspens in view):
- `-odseason=summer` - baseline: green canopy, no visible branches
  (leaves fully cover them), zero tint. Should be pixel-identical to
  pre-this-task summer.
- `-odseason=fall` - fully orange canopy, still full leaf coverage (no
  drop yet). Look for patchiness tree-to-tree (some fully orange, a few
  still transitioning if caught early via `-odseasonphase`).
- `-odseason=fall -odseasonphase=0.25` / `-odseasonphase=0.75` - mid
  fall->winter transition at 25%/75%: orange leaves progressively
  dissolving away, light dusting of twig snow starting to build at the
  higher phase.
- `-odseason=winter` - bare orange-less twigs (branches fully exposed),
  snow bunches visible on the branch structure, closed ground snow.
- `-odseason=winter -odseasonphase=0.5` - mid winter->spring transition:
  reverse-dissolve underway, fresh green re-budding rather than orange,
  snow melting off both ground and twigs together.
- `-odseason=spring` - canopy back to full green coverage, branches
  hidden again, easing toward the summer baseline over the hold.
- `-odseasonrate=20` (or similar) unfrozen, to watch a full transition
  play out live instead of jumping between fixed phases.

Committed locally only, `pipeline/make_tree_meshes.py`,
`unreal/build_flora_materials.py`,
`OpenDriveUE/Source/OpenDriveUE/ODForestManager.h/.cpp`,
`OpenDriveUE/Source/OpenDriveUE/ODSeasonController.h/.cpp` (commit
`1b39ada`); not pushed. The five pre-existing uncommitted road/junction
files were left alone as instructed.

**Snowfall-snap fix (2026-09-07):** `ComputeParamsForClock`'s winter-hold
branch used to hard-set `SnowfallIntensity` to `WinterLightSnowfall` (0.2)
the instant the hold began, snapping instantly from the fall->winter
transition's 1.0 blizzard peak - the one non-smoothstep step in the whole
param set; fixed in `ODSeasonController.cpp` by easing it down via
smoothstep over the first `WinterSnowfallDecaySeconds` (300s) of the hold
instead, re-verified via `scratchpad/verify_snowfall_fix.py`.

### 7.18 Junction unification: shared elevation + lane-marking suppression (2026-09-04, finished/committed 2026-09-08)

Finishes and commits the five road/junction files that sat uncommitted in
the working tree through the entire seasons arc above (7.17's two passes,
the deciduous follow-up, the snowfall fix) - `pipeline/build_road_network.py`,
`pipeline/build_road_stations.py`, `pipeline/road_mesh_common.py`,
`pipeline/build_road_guardrails.py`, `unreal/apply_road_material.py`. A
prior agent had implemented and in-game-verified the fix (two junction
screenshots) but was stopped before its negative-control check and never
committed; this pass audited the diff, found it complete and correct,
reconciled the on-disk bake (which had drifted out of sync with the
uncommitted code - see below), and ran the offline verification gate this
project now requires in place of `-game` launches.

**The bug (Taylor's report):** at real road junctions (e.g. Blue River
Parkway x 3rd Street, Silverthorne) crossing roads baked as independent
stacked ribbons at different elevations with a visible step face, and lane
markings ran straight through the junction box - because every road is
baked as an independent ribbon with its own per-road elevation profile and
nothing previously reconciled two DIFFERENTLY NAMED roads against each
other (the existing `reconcile_junction_elevations()` only ever compares a
road against itself - same `seg_idx` or same `road_name`).

**The fix, as found in the diff (pipeline/build_road_stations.py, `main()`):**
real junction NODES are detected straight from `merge_way_chains()`'s own
OSM graph degree (`node_degree`, now also returned by that function) - a
node with 3+ incident ways is a real junction by the exact rule that
function already uses to decide where to break a chain, so a plain
same-road continuation through a degree-2 node is never misflagged. Each
kept segment's chain-start/chain-end node id is threaded through
(`node_start`/`node_end`) so every real junction a baked segment actually
touches is found via the graph, not geometric/proximity guessing. For each
junction node with 2+ surviving participant segments: an outlier exclusion
drops any participant more than `JUNCTION_MAX_DISAGREEMENT_M` (10 m) from
the others' median (a segment truncated mid-descent by
`MAX_SEGMENTS`/`MIN_SEGMENT_STATIONS` must not drag correctly-agreeing
roads up to its own stranded elevation - caught live via a "48 hits, all
near junctions" survey); the highest surviving approach wins as the shared
target elevation (same "only ever raise, never lower" doctrine as the
existing same-road pass); each participant segment's OWN elevation is then
blended toward that target using ITS OWN arc-length distance from ITS OWN
endpoint (never network-wide XY-nearest, which a first cut of this pass
did and which `verify_terrain_fit.py`'s pre-bake consistency check caught
failing at 108.91 m max deviation - see the code comment trail for the
full bug story) - flush (fully replaced) within `JUNCTION_BOX_MARGIN_M`
(4 m) beyond the pavement half-width, smoothstep-easing back to the
road's own profile over `JUNCTION_APPROACH_M` (20 m). Bridge/tunnel
endpoints are excluded from participating entirely (a 51-jump,
all-bridge-adjacent survey found forcing a structurally-pinned bridge deck
elevation toward an unrelated surface road's target produced a 34 m jump
at one overpass - this is the "known side issue" flagged for a future pass,
deliberately NOT touched here). The resulting per-station 0..1
`junction_suppress` weight (1.0 deep in the box, smoothstep to 0.0 over the
approach zone) and boolean `in_junction_box` flag are written into
`road_stations.json`, threaded through `build_road_network.py` into
`road_mesh_common.build_road_chunks()`'s ODR3 `uv1.y` channel (repurposed
from an unused "reserved (0.0)"), which `unreal/apply_road_material.py`
reads to multiply lane-marking opacity down to zero inside the junction box
(never gating the base asphalt/gravel look) - see that file's updated
`M_Road` graph docstring. `build_road_guardrails.py` already skipped
guardrail placement near `reconcile_junction_elevations()`'s own `touched`
mask (7.16); this pass additionally passes an `is_bridge` mask into that
same reconciliation call in both `build_road_stations.py` and
`build_road_guardrails.py` so a bridge deck's pinned elevation can't get
matched by XY proximity against an unrelated same-named-road station
elsewhere (the same bridge exclusion the graph-junction pass needed, applied
to the older same-road pass too).

**Assessment of the diff:** complete and internally consistent; no
incomplete/broken code found. One piece of dead code left in place rather
than touched (minimal-diff instruction, and it's harmless): a first cut of
the elevation-flatten logic was implemented as two standalone
`road_mesh_common.py` functions, `compute_junction_influence()` and
`flatten_junction_elevations()` (network-wide XY-nearest matching) - the
final, committed logic replaced this with the inline per-segment
arc-length version directly in `build_road_stations.py.main()` (see that
function's own bug-story comment), but the two superseded functions were
never deleted. They are unused (grep-confirmed - referenced only in
comments), harmless, and documented; flagged here rather than silently
removed or silently left unmentioned.

**Baked-data consistency:** the on-disk `summit_county` bake had drifted -
`road_stations.json`'s mtime (Sep 7 16:31) was NEWER than `road_mesh.json`/
`road_guardrails.json` (Sep 6 11:1x) but did NOT contain the
`junction_suppress`/`in_junction_box` fields at all, meaning some
intervening run had regenerated stations without this fix's code (likely
whatever produced `junction_pads_final.json` at 16:43). Reran, in order,
against the currently-committed code and the existing cached
`data/raw/summit_county_test/` (osm.json, dem_tiles.npz - no refetch
needed): `build_road_stations.py` (362 real junction nodes detected, 6180/
274644 stations with `junction_suppress`>0, 2060 `in_junction_box`) ->
`build_road_network.py` -> `export_road_mesh_bin.py` (809/2758 chunks
nonzero `uv1.y`) -> `build_road_guardrails.py` (5887 stations flagged near a
junction, 3494 guardrail stations skipped for junction proximity) ->
`export_road_guardrails_bin.py`. Then ran the `apply_road_material.py`
`M_Road` rebuild commandlet (`UnrealEditor-Cmd.exe ... -run=pythonscript`,
`-nullrhi -unattended`) since its expression graph changed - completed with
0 errors, `ROADMAT built + saved M_Road`. Did NOT rerun the rest of
`rebuild_area.py`'s ~23 stages (terrain/buildings/walls/biomes/labels/
water) - out of scope for this fix (those stages don't consume
`junction_suppress`/`in_junction_box`), and re-carving terrain against the
now-slightly-different junction-area elevations was explicitly not asked
for and risks compounding the separately-known bridge-adjacent elevation
issue flagged above.

**Drift vs. the prior (uncommitted) progress report's own numbers:** 362
junction nodes / 809 nonzero-uv1.y chunks this run vs. the earlier report's
366 / 856. Same code against the same cached OSM/DEM inputs - the earlier
figures came from a never-committed run whose exact `MAX_SEGMENTS`/
`MIN_SEGMENT_STATIONS`-driven `kept`-segment set can differ by a handful of
short/filtered chain fragments that leave only one surviving participant at
a given junction node (skipped per `len(incident) < 2`); not evidence of a
logic change. Noted honestly in `verify_junction_stats.txt` rather than
silently reconciled to match the old numbers.

**Offline verification (scratchpad, no `-game` launch per current
project-wide protocol):**
- `verify_junction_elevation.png` - two real junctions (Blue River Parkway
  x 3rd Street, Silverthorne; Ski Hill Rd/N+S Main St/Lincoln Ave,
  Breckenridge, a real 4-way), each road's own elevation profile vs.
  distance-from-junction-center, before (a pre-rerun backup of
  `road_stations.json`, dashed) vs. after (the freshly-reconciled bake,
  solid). Elevation spread among participants within 15 m of the junction
  center: Silverthorne 0.48 m -> 0.04 m; Breckenridge 1.30 m -> 0.01 m -
  both collapse to within a few cm, the flush-pavement goal.
- `verify_junction_map.png` - ~300 m top-down box around each junction,
  stations colored by `junction_suppress` (0..1) with the actual
  `JUNCTION_BOX_MARGIN_M`+half-width (8.5 m) and approach-zone-end
  (28.5 m) circles overlaid: suppression is 1.0 (yellow) tightly inside the
  box circle, fades smoothly to 0 by the approach circle, and reads exactly
  0 everywhere else on the same roads - including at OTHER, unrelated real
  junctions caught incidentally inside the same 300 m box, which correctly
  show their own independent suppression halos.
- `verify_junction_stats.txt` - the counts above, cross-checked against the
  prior uncommitted-work report's figures with the drift explanation.

**What this offline pass cannot prove** (per the project's current
verification protocol, no `-game` launch performed): whether the junction
actually READS as flush/unified pavement in-engine (lighting, texture
continuity, the z-bias tiebreaker's visual effect at a real crossing camera
angle) and whether lane markings visibly stop at the junction box edge
rather than merely having their opacity mask zeroed in data. The prior
agent's own two in-game screenshots (referenced in its handoff, not
re-verified here) are the only visual evidence to date; Taylor's own
drive-test is the remaining gate, particularly at Blue River Parkway x 3rd
Street and at least one other multi-way junction of his choosing. The
known bridge-adjacent elevation-jump interaction (where
`reconcile_junction_elevations()` meets bridge-flagged stations) remains
unfixed by design - flagged for a future, separately-scoped pass.

Committed: `pipeline/build_road_network.py`, `pipeline/build_road_stations.py`,
`pipeline/road_mesh_common.py`, `pipeline/build_road_guardrails.py`,
`unreal/apply_road_material.py`. Not pushed.

### 7.19 Embankment skirts (2026-09-07)

Where the road rides above surrounding terrain but not so high a bridge is
warranted, the raw side-extrusion previously showed as a bare beveled wedge
(worst near I-70/Silverthorne). Adds a sloped earthen skirt from the
pavement edge down to terrain for the moderate-drop case, and extends the
existing binary road-above-terrain detection (7.16's guardrails) into a
3-way classification so a later, separately-scoped bridge task has real
per-station data to consume.

**Design, mirroring 7.16's guardrail feature almost exactly** (same
"pipeline ships points, C++ extrudes at runtime" split as
`AODGuardrailManager`/`AODBarrierManager`): `pipeline/build_road_embankments.py`
reuses `build_road_guardrails.py`'s own DEM-probe drop detection, lake
exclusion (`load_lake_beds`/`_point_in_polygon`), and junction exclusion
(`reconcile_junction_elevations()`'s `touched` mask) essentially verbatim,
classifying each side/station by drop depth:

    drop <= 2.5 m            -> none        (terrain meets road acceptably)
    2.5 m < drop <= 7.0 m    -> embankment  (this pass's actual deliverable)
    drop > 7.0 m, or a       -> bridge      (data only - a later task's job;
    qualifying drop over                     classified now so that task has
    open water                               real data without re-detection)

A per-side Schmitt-trigger hysteresis state machine (`classify_with_
hysteresis()`, 0.3 m band at each threshold) prevents station-to-station
class flicker near the 2.5 m/7 m boundaries, and `demote_short_runs()`
(mirroring guardrails' `GUARDRAIL_MIN_RUN_M`) discards any surviving run
under 10 m as a DEM-noise blip rather than a real embankment/bridge. Runs
are written to `road_embankments.json` (`{"runs":[{"tile","side","cls",
"station_xyzd_cm"}]}` - x,y,z,drop per station); `export_road_embankment_
bin.py` packs only "embankment"-class runs into a new ODE1 binary tile
format (bridge stays JSON-only, no runtime consumer yet). A new
`AODEmbankmentManager` (`OpenDriveUE/Source/OpenDriveUE/ODEmbankmentManager.
{h,cpp}`), spawned via `EnsureEmbankmentManager()` in `ODGameMode::
BeginPlay()`, clones `AODGuardrailManager`'s tile-streaming shape verbatim
(ring load/despawn around the pawn, async file I/O, shared `ODBinSidecarUtil.
h` binary-parsing helpers) but differs in two deliberate ways: the
cross-section isn't a fixed profile table (each station's own shipped drop
value sizes the slope on the fly, three layer points top/toe/apron per
station at a 1.5:1 horizontal:vertical `SlopeRatio`), and the mesh is
visual-only with `ECollisionEnabled::NoCollision` - the guardrail sheet at
the same drop already provides containment, so a second collision mesh
would be redundant. `unreal/apply_embankment_material.py` builds `M_Embankment`
reusing an already-imported ambientCG texture (`Ground037` dirt, no new
download) at 850 cm world-space planar XY tiling, with toe-darkening via a
`TextureCoordinate` green channel (the skirt mesh's own UV.Y, 0 at top / 0.6
at toe / 1.0 at apron) lerped toward black.

**Junction exclusion, corrected mid-pass:** a first draft used only the
newer per-station `in_junction_box` field (Arc 7.18), reasoning it was a
strict shortcut around re-deriving `reconcile_junction_elevations()`'s own
mask a second time. Live verification against this session's own acceptance
sanity check ("flat town grids nearly empty") caught this as wrong -
`in_junction_box` only covers the literal flush-pavement box at each real
junction node, far narrower than guardrails' `touched` mask, and Frisco/
Breckenridge's dense street grids sit on genuine hillside grades (not flat),
so the gap between boxes is exactly where a probe point 7.5 m off a town
street can land on a neighboring lot's own cut/fill grade and register a
spurious multi-meter "drop." Switched to reusing `touched` as the primary
mechanism (OR'd with `in_junction_box`, which is free and strictly correct
for the literal box) - confirmed correct because the resulting count (5887
stations) matches SS7.18's own reported figure for this area exactly.

**Toe-apron gap risk, also caught mid-pass:** the idealized straight 1.5:1
slope is not a terrain-follow toe - it's a single DEM probe sample plus a
straight line. `verify_embank_profile.png`'s first render (with the
initial 30 cm embed / 100 cm overlap) showed the mid-drop (~5 m) case's toe
apron could sit up to ~1 m ABOVE the real sampled DEM profile - a genuine
gap risk, not a rendering artifact. Fixed cheaply in `ODEmbankmentManager.h`
by raising `ApronEmbedCm`/`ApronOverlapCm` to 150/200 cm (the apron is
buried by construction - nothing renders below the terrain surface it
embeds into - so embedding deeper costs nothing visually and buys real
margin); rebuilt and reran the profile plot, which now shows the apron
crossing clearly below the real terrain line in all four panels. A genuine
terrain-following toe remains out of scope for this pass (documented in the
header's own comment) - this is a safety margin around the idealized slope,
not a replacement for one.

**Classification numbers** (summit_county, full stdout in
`verify_embank_runs.txt`): 1165 lake beds loaded; 5887 stations flagged near
a junction (3624 of those had a qualifying drop that would otherwise have
started a run); 2151 tile-chunked runs (86,469 stations) written -
embankment 1082 contiguous runs / 29,792 stations / ~143.55 km, bridge (data
only) 420 contiguous runs / 56,677 stations / ~281.28 km. Exported 1232
embankment runs (31,409 stations, 1-station tile-seam overlap) into 177
`.bin` tiles, 514,184 bytes total (~0.49 MB).

**The bridge-count caveat, disclosed honestly rather than hidden:** the
"bridge" figure above is inflated by a real, pre-existing upstream data
issue, not by this pass's own logic. Root-caused via a spot-check on
Potato Patch Drive (an 87 m computed drop that persisted even sampled at
the pavement centerline itself, 0 m offset) to a mismatch between the
pipeline's stored road elevation profile and raw DEM at the identical
coordinate - most likely the same-road-name cross-matching issue
`reconcile_junction_elevations()`'s own docstring already flags as unfixed,
which is out of scope here per this task's brief ("don't touch the junction
fix files beyond consuming their outputs"). Confirmed this does NOT
disproportionately hit town grids (settlement-tile density ~21.3% in-town
vs. ~31.5% county-wide - town is not worse) and, more importantly, does NOT
affect the actual embankment geometry that ships to players: hysteresis
construction caps a real embankment run's max drop at ~7.3 m regardless,
and bridge-class runs are JSON-only with no current runtime/visual
consequence. Only the data-only bridge count is inflated; flagging this for
whoever picks up the bridge task next, since they'll be consuming
`road_embankments.json`'s "bridge" runs directly.

**Dillon Dam Road causeway note** (task-required, actual measured numbers):
all four runs found along Dillon Dam Road classify as **embankment**, not
bridge - 530.0 m/3.29 m max drop, 625.0 m/3.17 m, 55.0 m/6.22 m, 85.0 m/
3.70 m. This is correct: every max drop sits inside the 2.5-7 m embankment
band, and none of these stations test as "in a lake" because the lake-bed
polygon represents the reservoir's open water surface, not the paved top of
the dam structure itself - which is not open water, it IS already a large
engineered earthen/rockfill embankment. Forcing this to "bridge" would have
been the wrong call even though it's a literal water crossing in the broad
sense; a real dam access road runs on compacted fill, not a deck. This is
the same narrower point-in-polygon-only lake test `build_road_guardrails.py`
already uses rather than `build_road_barrier.py`'s wider shore-margin
version, which that module's own docstring notes would incorrectly eat
Dillon Dam Road.

**Offline verification (scratchpad, no `-game` launch per current
project-wide protocol):**
- `verify_embank_classes.png` - county-wide map, road network in gray with
  embankment (orange) and bridge (red) runs overlaid; embankments visibly
  hug hillside/elevated segments and the I-70 corridor shows both classes
  along its length; flat town grids read comparatively sparse relative to
  the mountainous county as a whole (see the bridge-count caveat above for
  the honest caveat on why town grids aren't perfectly empty).
- `verify_embank_profile.png` - cross-sections at four representative
  stations (shallow ~3 m, mid ~5 m, near-threshold ~7 m, and a hillside case
  where only one side needs a skirt): pavement edge, the 1.5:1 skirt slope,
  the toe-apron overlap, and the real DEM-sampled terrain profile along the
  same line. After the apron-margin fix, the toe apron crosses clearly
  below the real terrain line in all four panels - the geometry lands on
  terrain with margin, not a bare hairline touch.
- `verify_embank_runs.txt` - full run/station/km counts per class,
  junction/lake skip counts, a worked hysteresis example demonstrating the
  Schmitt-trigger latching through repeated sub-threshold dips near 2.5 m,
  and the tile/bin-size accounting.

**What this offline pass cannot prove:** whether the skirt mesh actually
reads correctly in-engine - material/texture appearance at real scale and
lighting, LOD/streaming behavior as the player pawn moves, and whether the
no-collision choice feels right against the guardrail sheet's own
containment at speed. These are in-game checks Taylor must do himself per
this session's testing protocol; the toe-apron gap risk in particular was
only caught by comparing idealized geometry against sampled DEM data
offline; whether the buried apron's real-terrain overlap looks right
in-engine (versus merely computing correctly) is unverified.

Committed: `pipeline/build_road_embankments.py`,
`pipeline/export_road_embankment_bin.py`, `pipeline/rebuild_area.py`,
`OpenDriveUE/Source/OpenDriveUE/ODEmbankmentManager.h`,
`OpenDriveUE/Source/OpenDriveUE/ODEmbankmentManager.cpp`,
`OpenDriveUE/Source/OpenDriveUE/ODGameMode.h`,
`OpenDriveUE/Source/OpenDriveUE/ODGameMode.cpp`,
`unreal/apply_embankment_material.py`. Not pushed.

### 7.20 Bridge-classification root cause: reconcile_junction_elevations() false pair matches (2026-09-07)

**The bug (Taylor's report, following straight on from 7.19):** build_road_
embankments.py's 3-way drop classifier was calling 57,222 station-points
(~281 km) of summit_county roads "bridge" - absurd for a county with a
handful of real bridge/viaduct decks. 7.19's own bridge-count caveat had
already spot-checked this down to "the same-road-name cross-matching issue
reconcile_junction_elevations()'s own docstring already flags as unfixed"
via Potato Patch Drive (an 87 m computed drop that persisted even sampled at
the pavement centerline, 0 m probe offset - ruling out a DEM-probe-placement
explanation).

**Root cause, in full:** `reconcile_junction_elevations()` in
`pipeline/road_mesh_common.py` exists to snap two stations of the SAME road
that disagree on elevation at the same physical spot (the documented "Lake
Hughes" bug, 1.1-2.4 m apart) up to match each other. Its scope test was far
too permissive in two independent ways, neither related to a real same-spot
disagreement:

1. **Divided-highway false match.** Cross-segment, same-road-name pairs were
   in scope anywhere within `CONFLICT_RADIUS_M` (150 m) and under 50% grade.
   I-70 through Summit County runs as two independently-graded carriageways
   (benched into opposite sides of the Straight Creek valley) sharing the
   OSM name "I 70"/"Gerald R. Ford Memorial Highway" across dozens of
   separate way segments for the whole corridor - "same name, <150 m, <50%
   grade" is true almost everywhere along a road running roughly parallel to
   itself for kilometers, so one carriageway's real elevation kept getting
   hiked up by several meters to match the other's, station after station.
   145,742 qualifying I-70 pairs confirmed before the fix.
2. **Switchback false match.** Same-segment pairs (one OSM way chain, e.g.
   Potato Patch Drive, a continuous switchback climb) had no station-index-
   gap restriction at all - two points on the SAME climb, hundreds of
   stations apart (different switchback legs) but close together in plan
   view, were treated as "the same spot disagreeing" and forcibly snapped
   together at an ordinary driveable grade. 3,750 qualifying same-segment
   pairs confirmed on Potato Patch Drive alone at 150 m radius.

**The fix** (`pipeline/road_mesh_common.py`, the only file touched):
- New `JUNCTION_RECONCILE_RADIUS_M = 20.0` - `reconcile_junction_elevations()`
  now searches its own candidate pairs at a tight, dedicated 20 m radius
  instead of reusing `CONFLICT_RADIUS_M` (150 m, correctly sized for
  `classify_wall_stations()`'s different carve-exclusion/wall-geometry
  purpose and left untouched there).
- New `JUNCTION_SAME_NAME_EDGE_STATIONS = 5` - a cross-segment, name-only
  match is now only in scope when BOTH stations sit within 5 stations of
  their own segment's start/end (a real chain-break/switchback seam is a
  local event at the join, not scattered across a segment's interior).
  Same-segment pairs are unaffected by this restriction (the genuine
  intra-chain switchback case, already scoped correctly by `seg_idx`
  equality) but do benefit from the tighter radius.
- No call-site changes needed: `build_road_guardrails.py`,
  `build_road_embankments.py`, and `build_road_stations.py` all call
  `reconcile_junction_elevations()` at the default `radius_m`, so the fix
  takes effect everywhere automatically.
- Verified at 20 m: 0 qualifying I-70 cross-segment pairs and 0 qualifying
  Potato Patch Drive same-segment pairs remain, while the documented real
  Lake Hughes disagreement (1.1-2.4 m) is still comfortably caught.

**Dead end, documented for the record:** mid-investigation, road_stations.
json's per-station `is_bridge` OUTPUT field was found True for ~48.7% of all
stations countywide, including ordinary residential streets - looked like a
smoking gun. It wasn't: that field is `expand_carve_exclusion(is_struct_s,
CARVE_BUFFER_FRAC) | conflict_s`, a deliberately wide mask for terrain-carve
exclusion unioned with the wide-radius (150 m) elevation-conflict mask used
for retaining-wall geometry - misleadingly named, but it doesn't feed
elevation values and wasn't the drop-classification root cause. Traced every
consumer before ruling it out.

**Before/after (summit_county, rebaked fresh against the same cached OSM/DEM
inputs both times, via `git stash` on this fix and back):**

| class | before | after |
|---|---|---|
| embankment | 1114 runs, 31,291 stations, ~150.89 km | 1135 runs, 40,413 stations, ~196.39 km |
| bridge | 374 runs, 58,695 stations, ~291.60 km | 157 runs, 13,723 stations, ~67.83 km |

Bridge km down 76.7%. Embankment km grew, as expected - stations that were
falsely forced all the way to "bridge" by the corrupted elevation now read a
smaller, real drop and land in "embankment" instead. Top surviving bridge
runs by length are I-70's East Portal/Straight Creek approach (real
cut-and-fill interstate terrain feeding the Eisenhower-Johnson Tunnel),
Boreas Pass Road and CO-9/Hoosier Pass (real unpaved high-alpine switchback
passes) - not ordinary residential streets. Full downstream chain rebaked:
`build_road_stations.py` -> `build_road_network.py` ->
`export_road_mesh_bin.py` -> `build_road_guardrails.py` ->
`export_road_guardrails_bin.py` -> `build_road_embankments.py` ->
`export_road_embankment_bin.py`.

**Junction regression check** (SS7.18's own two spot-checks, since this
touches the same function the graph-junction flatten pass's input elevation
flows through): Silverthorne (Blue River Parkway x 3rd Street) and
Breckenridge (Ski Hill Rd/N+S Main St/Lincoln Ave) both measured 0.0 m
elevation spread among participants within 15 m of the junction center,
BEFORE this fix and AFTER - no regression. Expected: real-junction
flushness at these two spots comes from the separate, later "real (graph)
junction detection + flatten" pass (SS7.17/7.18), independent of this fix's
target (`reconcile_junction_elevations()`'s own same-road/same-segment pair
matching, which was corrupting elevation far from any real junction).

**Offline verification (scratchpad, no `-game` launch per current
project-wide protocol):**
- `verify_drop_rootcause.png` - road elev_m vs DEM terrain, before/after, for
  I-70's East Portal approach and the full Potato Patch Drive climb. Potato
  Patch Drive shows the bug at its clearest: before, road elevation is flat
  and disconnected from terrain (~2625-2650 m regardless of the real climb);
  after, it tracks the DEM climb from ~2555 m to ~2635 m almost exactly.
  I-70 improves (max |road-DEM| 39.2 m -> 29.7 m in the plotted window) but
  not to zero - see the unprovable-offline note below.
- `verify_drop_classes_fixed.png` - county-wide map, bridge (red) now reads
  as a small number of clearly linear corridors matching real mountain
  highway/pass geography (I-70 corridor, Boreas Pass, CO-9) instead of
  scattered across the whole network; before/after km per class in the
  title.
- `verify_junction_still_ok.txt` - the regression check above, in detail.
- `verify_drop_stats.txt` - full before/after station/run/km counts, root
  cause in plain language, top 10 longest surviving bridge runs with road
  names for a geography sanity check, and the `is_bridge`-field dead end.

**What this offline pass cannot prove:** whether 67.83 km of remaining
"bridge" classification is exactly right or still somewhat inflated. I-70's
East Portal plot still shows up to ~30 m of road-vs-DEM disagreement even
after the fix - large enough that a residual DEM-vs-two-independently-
benched-carriageway artifact (same class of issue as `is_structure_way()`'s
own documented I-5/Skagit River precedent: a single terrain heightmap can't
represent two carriageways benched at different heights on the same
mountainside) can't be ruled out from station data alone. This is the
"bridge elevation jump" symptom flagged during the SS7.18 junction work as
related-but-distinct, and was intentionally NOT touched by this fix, which
targeted only `reconcile_junction_elevations()`'s pair-matching scope. Also
unverified: whether the grown embankment total (196.39 km) is itself fully
honest at its own, smaller magnitude, and any in-engine visual read of the
remaining bridge runs (no `-game` launch performed, per this task's testing
protocol).

Committed: `pipeline/road_mesh_common.py`. Bakes (road_stations.json,
road_embankments.json, road_guardrails.json, and the exported .bin tiles
under `OpenDriveUE/Content/OpenDrive/Masks`) are all gitignored per
`.gitignore` (`data/` and `OpenDriveUE/Content/`) - regenerated locally, not
committed, matching 7.18/7.19 precedent. Not pushed.

### 7.21 Bridges: genuine-span sub-classification + deck/pier geometry (2026-09-07)

**Goal:** 7.20 left "bridge" as a raw drop-threshold class (157 runs, 67.83 km)
that mixes genuine short spans with long mountainside sidehill cuts (a single
terrain heightmap can't represent two independently-benched carriageways on
the same slope - the same DEM limitation `is_structure_way()`'s own docstring
already documents for I-5/Skagit). This pass splits that class into real
bridges (get deck+pier geometry) and sidehill cuts (demoted back into the
7.19 embankment skirt bake, not left naked), then ships runtime geometry for
the real ones.

**Discriminator** (`pipeline/build_road_embankments.py`, new
`subclassify_bridge_runs()`, runs per-segment after both sides' drop/hysteresis
passes so it can see cross-side agreement): a bridge-threshold run is kept
GENUINE if either -
1. **water crossing**: ≥3% of its stations sample inside a mapped lake
   polygon at the pavement edge (`BRIDGE_WATER_MIN_FRAC`), or
2. **short + both-sided**: run length ≤300 m (`BRIDGE_GENUINE_MAX_LEN_M`) AND
   the opposite side of the road also carries a qualifying drop over ≥50% of
   the same stretch (`BRIDGE_BOTH_SIDED_MIN_FRAC`) - real spans clear both
   edges of the roadbed; a sidehill cut only drops on the downhill side.

Otherwise the run is DEMOTED to `embankment` class in `cls_arr` (fed straight
into 7.19's existing skirt bake, same as any other embankment run) with its
shipped drop value capped at `DEMOTED_BRIDGE_DROP_CAP_M = 12.0` m - purely so
the skirt's own 1.5:1 side geometry doesn't produce a degenerate 45 m
horizontal run for a real 30 m sidehill drop (see
`verify_bridge_geometry.png`'s rightmost panel).

A third candidate signal - OSM `bridge=yes` structure-tag coverage on the run
- was measured (`osm_frac`, still written to `bridge_subclass_report.json`
for the record) but deliberately **not** used to decide genuine/demoted.
`road_stations.json`'s per-station `is_bridge` field is
`expand_carve_exclusion(is_struct_s) | conflict_s`, and `conflict_s` comes
from `classify_wall_stations()`'s own separate 150 m same-road-name conflict
mask - a different call from `reconcile_junction_elevations()`, which 7.20
fixed, and this one 7.20 explicitly left untouched (correctly sized for
`classify_wall_stations()`'s own wall-geometry purpose). I-70/"Gerald R. Ford
Memorial Highway" runs parallel same-named carriageways for kilometers -
exactly the geometry that caused 7.20's own pre-fix false-match bug - so a
first pass using OSM-tag coverage as a sufficiency condition kept the 8.64 km
I-70 East Portal run "genuine" at 58-100% coverage, for exactly the wrong
reason. Caught by the brief's own validation requirement (this run must NOT
be genuine) before it shipped; removed as a discriminator, kept measured.

**Result:** 92 genuine runs (2,077 stations) / 9.93 km kept as bridges; 65
runs / 57.91 km demoted to embankment (of the pre-subclass 157 runs / 67.83
km). Embankment grows from 7.20's 1,135 runs/196.39 km to 1,073 runs/52,059
stations/~254.93 km (the demoted sidehill mileage lands there, net run-count
drop because some adjoining embankment+demoted-bridge runs merge). Genuine
list is short and geographically credible - mostly 5-285 m spans on CO-9
(Hoosier Pass switchbacks), Boreas Pass Road, Gerald R. Ford Memorial Highway
short cuts, and county/local roads; the two named "should demote" targets
(I-70 East Portal, 8.64 km/30.4 m max drop; Boreas Pass Road's long run,
6.68 km/14.2 m max drop) both demote correctly, and the named "should stay
genuine" target (I-70/CO-9 interchange overpass near Silverthorne,
lat≈39.6266/lon≈-106.0711) survives at 130-135 m/100% both-sided overlap.
The third named target (Dillon Reservoir causeway / Swan Mountain Road
crossing) could not be validated either way - see caveats below.

**Geometry** (visual-only, `ECollisionEnabled::NoCollision`, same
pipeline-ships-points/C++-derives-mesh split as embankments/guardrails):
- `pipeline/build_road_bridges.py` (new): reads the now-genuine-only "bridge"
  runs out of `road_embankments.json`, places piers every
  `PIER_SPACING_STATIONS` (5 stations = 25 m) wherever deck-underside-to-
  terrain clearance ≥ `MIN_PIER_CLEARANCE_M` (1.0 m), writes
  `road_bridges.json` (deck station points + pier positions/heights). 92 runs
  → 451 piers, 0 runs got zero piers (short spans stay under clearance the
  whole way and read as abutment-to-abutment with no mid-span support, which
  is correct).
- `pipeline/export_road_bridge_bin.py` (new): new `ODB1` binary sidecar
  format (magic + 16-byte generation id + per-run station/pier arrays),
  bucketed per world tile exactly like `export_road_embankment_bin.py`. 92
  runs → 32 tile `.bin` files under
  `OpenDriveUE/Content/OpenDrive/Masks/bridge_summit_county_test_tile_*.bin`.
- `OpenDriveUE/Source/OpenDriveUE/ODBridgeManager.h/.cpp` (new): clones
  `AODEmbankmentManager`'s ring-based tile-streaming `Tick()` verbatim
  (`TileSizeCm`/`SpawnRadius`/`DespawnMarginCm`/`MaxNewLoadsPerTick`,
  `ODBinSidecarUtil.h` parse helpers, generation-id mismatch warnings per
  `AUDIT-RELIABILITY.md §3.1/§5.1`). Per run: one `UDynamicMeshComponent`
  deck fascia (top/bottom-outer/bottom-inner-underside-flap 3-layer quad
  strip, `DeckDepthCm = 125` matching Python's `DECK_DEPTH_M`, UV0.V =
  0/0.7/1.0 for the material's underside-darken read), plus one
  `UHierarchicalInstancedStaticMeshComponent` of `/Engine/BasicShapes/
  Cylinder` instances for piers (scaled per-instance from the unit 50 cm-
  radius/100 cm-height primitive). Abutments are implied naturally at span
  ends (deck simply meets the terrain there) rather than modeled separately.
- `OpenDriveUE/Source/OpenDriveUE/ODGameMode.h/.cpp`: new
  `EnsureBridgeManager()`, same find-or-spawn shape as
  `EnsureEmbankmentManager()`, called from `BeginPlay()` right after it.
- `unreal/apply_bridge_material.py` (new): builds `M_Bridge` reusing the
  already-imported `Concrete034` texture set (`unreal/apply_road_material.py`'s
  existing "junction pad concrete" role, already in
  `content_src/textures/road/manifest.json` - no new download, no new
  manifest entry needed). World-space planar XY UV, underside darkened via
  `Lerp(albedo, black, UV0.V * 0.35)` reading the same V-coordinate layering
  the C++ manager writes - same "shaded concrete" cue `M_Embankment`'s toe-
  darkening already uses, applied to the deck's underside instead. Ran via
  `-run=pythonscript` commandlet: built + saved cleanly, 0 errors.
- `pipeline/rebuild_area.py`: two new stages inserted right after
  `export_road_embankment_bin.py` (`build_road_bridges.py`,
  `export_road_bridge_bin.py`), embankment stage description updated to
  mention the sub-classification pass.

**Build:** `Build.bat OpenDriveUEEditor Win64 Development` succeeded (24.1 s;
compiled `ODGameMode.cpp`, `ODBridgeManager.cpp`, `Module.OpenDriveUE.cpp`,
linked clean).

**Offline verification (scratchpad, no `-game` launch per current
project-wide protocol):**
- `verify_bridge_subclass.png` - county map, genuine spans (red) vs demoted
  sidehill runs (purple, sized by length) over the road network, with km
  totals per class in the title and the three named validation cases
  annotated.
- `verify_bridge_geometry.png` - longitudinal profile (deck/underside/terrain/
  piers) and mid-span cross-section for the Silverthorne I-70/CO-9 overpass,
  a short Boreas Pass Road genuine span, and a CO-9 genuine span, plus the
  demoted I-70 East Portal run's capped-vs-uncapped skirt-toe comparison
  justifying `DEMOTED_BRIDGE_DROP_CAP_M`.
- `verify_bridge_stats.txt` - discriminator in plain language, genuine-span
  list (92 rows: road name/length/max drop/reason), top demoted runs, pier
  counts, and the caveats below.

**What this offline pass cannot prove:**
- Whether all 92 "genuine" runs are truly real bridges vs. a handful of short
  benched-shoulder artifacts that happen to read short-and-briefly-both-sided
  (e.g. a tight switchback where both edges dip momentarily) - station data
  alone can't fully separate a real 40 m culvert-crossing span from a 40 m
  two-sided bench.
- The named Dillon Reservoir causeway / Swan Mountain Road validation target:
  the water-crossing signal never fired on any of the 157 candidate runs
  countywide. Direct check of Swan Mountain Road (`seg023`, `road_stations.
  json`) shows its max drop peaks at 6.0 m (one side) / 2.7 m (other) -
  both under the classifier's pre-existing 7 m bridge threshold from 7.19/
  7.20, so it never becomes a bridge-threshold candidate for this pass to
  see at all. This is a limitation of the upstream drop/water detector
  (out of scope here - the brief's own instruction was not to touch
  elevation code, and this pass didn't), not of the sub-classification
  discriminator built in 7.21. A real reservoir-crossing structure on Swan
  Mountain Road may exist but isn't surfaced by this pipeline's current DEM
  sampling/resolution.
- In-engine visual read (material tiling/scale, pier silhouette against the
  real carved terrain mesh, deck fascia depth at driving speed/camera
  angles) - no `-game` launch performed, per this task's testing protocol.
  `verify_bridge_geometry.png` uses the same idealized drop-interpolation
  approximation `build_road_bridges.py` itself uses for pier placement, not
  a fresh DEM re-sample, so it confirms internal consistency, not ground
  truth.

Committed: `pipeline/build_road_embankments.py`, `pipeline/build_road_
bridges.py` (new), `pipeline/export_road_bridge_bin.py` (new), `pipeline/
export_road_embankment_bin.py`, `pipeline/rebuild_area.py`,
`OpenDriveUE/Source/OpenDriveUE/ODBridgeManager.h` (new), `OpenDriveUE/
Source/OpenDriveUE/ODBridgeManager.cpp` (new), `OpenDriveUE/Source/
OpenDriveUE/ODGameMode.h`, `OpenDriveUE/Source/OpenDriveUE/ODGameMode.cpp`,
`unreal/apply_bridge_material.py` (new). Bakes (road_embankments.json,
road_bridges.json, bridge_subclass_report.json, and the exported .bin tiles
under `OpenDriveUE/Content/OpenDrive/Masks`) are all gitignored per
`.gitignore` (`data/` and `OpenDriveUE/Content/`) - regenerated locally, not
committed, matching 7.18/7.19/7.20 precedent. Not pushed.

### 7.22 Bridge sub-classification: tighten discriminator against unpaved roads and sidehill benches (2026-09-07)

**Reviewer verdict on 7.21 (commit `74aa93d`):** the two-condition
discriminator (water crossing OR short+both-sided) is necessary but not
sufficient - it's satisfied just as well by a switchback bench or a fill
crest (both edges of the roadbed dip briefly at a bench turn with no real
gap underneath) as by a real span. Result: 92 runs/9.93 km "genuine",
including 20 spans on Boreas Pass Road (an unpaved 4x4 pass) and clusters on
CO-9/Hoosier Pass, County Road 4, Homestake Creek Road, and Robbers Nest
Drive - all dirt/gravel roads. Not credible; real Summit County has on the
order of 2-4 km of actual bridge deck.

**Two new AND conditions**, added on top of 7.21's existing short+both-sided
test (`subclassify_bridge_runs()`, `pipeline/build_road_embankments.py`).
The water-crossing OR stays unconditional - a real water crossing already
implies a real gap under the deck, so neither new condition applies to it.

1. **Paved-road test** (`build_road_surface_index()`) - a dirt/gravel/
   track/service road never gets a concrete deck regardless of drop shape.
   `road_stations.json` ships per-station `highway_class` but not OSM
   `surface` - there's no `surface()` counterpart to `build_road_network.
   py`'s `highway_class()`/`way_name()` helpers, and adding one was out of
   scope (this pass touches `build_road_embankments.py` only). Instead this
   joins back to the cached `data/raw/summit_county_test/osm.json` by road
   NAME, length-weighting every way sharing that name/ref into a per-road
   paved/unpaved/minor-highway/no-surface-tag tally, then votes: >=50%
   paved-tagged wins outright; else >=50% track/path/service-class or >40%
   explicit unpaved-surface-tagged demotes; else an unreviewed/unmarked
   TIGER-import fallback (residential/unclassified, no surface tag at all,
   `tiger:reviewed` not "yes"/"aerial") demotes if it's >=50% of the road's
   length. Deliberately does NOT blanket-demote `highway=unclassified`
   despite being one of the task brief's own suggested tags - inspecting
   the actual county data first found unclassified is majority-PAVED here
   (176/323 ways asphalt-surfaced).
   **Caught and fixed during this pass:** the TIGER fallback's first cut
   checked `tiger:reviewed == "no"` literally, which silently missed the
   more common case (231 of 965 tiger-tagged ways vs. 734 marked "no") where
   the tag is simply ABSENT rather than explicitly "no" - County Road 4
   itself (both its OSM ways) has no `tiger:reviewed` tag at all, so the
   first cut left it un-demoted, directly failing the task's own named
   requirement. Fixed by checking "not affirmatively reviewed" (`not in
   ("yes", "aerial")`) instead of "explicitly no".
2. **Terrain-rebound (abutment plausibility) test** (`rebound_shape_ok()`)
   - a real span's drop profile is a bowl: the terrain climbs back to near
   road level (`REBOUND_DROP_M` = 3 m) within `REBOUND_WINDOW_M` beyond BOTH
   ends of the run, checked against the FULL side's station array outside
   the run's own hysteresis-drawn boundary (a run's own edge stations are,
   by construction, already at-or-above threshold, so the rebound has to be
   checked in the stations the classifier excluded). A bench/fill-crest
   instead holds a plateau - it's really one long sustained cut, not a
   short real gap.
   **Window tuned empirically, not from the task brief's own ~30-50 m
   guess** - the brief's own lower estimate clips the real, named-genuine
   Silverthorne I-70/CO-9 interchange overpass (its drop doesn't reach 3 m
   until ~65 m past each run end, then keeps falling to ~0 by ~60-65 m - a
   real if slightly wide abutment approach grade, not a plateau). Tuning
   run: `OD_DEBUG_REBOUND=1 python build_road_embankments.py` dumps every
   water-excluded/paved/short/both-sided candidate's full drop profile out
   to 250 m past each run end to `bridge_rebound_debug.json`; `scratchpad/
   plot_rebound_tuning.py` sweeps window sizes against it. Total genuine km
   among those candidates: 0.59 km @ 40 m -> 1.78 km @ 70 m -> 2.24 km @
   100 m -> 3.18 km @ 250 m - climbing slowly and monotonically rather than
   leveling off, i.e. widening the window mostly keeps re-admitting more of
   the CO-9/Hoosier switchback-bench population the test exists to reject,
   not recovering "real" mileage. `REBOUND_WINDOW_M = 70` is picked as the
   smallest window that clears Silverthorne on both sides (passes at 65 m)
   - the specific, named reason it's needed - while 40 of 42 CO-9-named
   candidates still correctly fail at that window. `REBOUND_DROP_M` stays
   at the brief's own 3 m guess (not tuned - the window was the binding
   constraint). See `verify_bridge_rebound.png` (bowl-vs-plateau profile
   overlay plus the window-vs-km tuning curve, both panels).

**Result:** 23 genuine runs / 1.78 km (down from 74aa93d's 92 runs / 9.93
km) / 134 demoted runs / 66.06 km, of the same pre-subclassification 157
runs / 67.83 km pool 7.21 started from. Of the 69 runs/8.15 km demoted by
the two new conditions: 27 runs/3.44 km hit the unpaved-road rule (all of
which also failed rebound independently - Boreas Pass Road x15, County Road
4 x6, Robbers Nest Drive x3, Homestake Creek Road x1, Garnet Road x1, East
Fork Eagle x1), 42 runs/4.71 km failed only the rebound test on an
otherwise-paved-eligible road (mostly CO-9/GRFMH sidehill benches). Named
validation, all correct: Boreas Pass Road 0/28 runs genuine, County Road 4
0/8, Homestake Creek Road 0/2, Robbers Nest Drive 0/4, Garnet Road 0/2,
I-70 East Portal long sidehill run stays demoted, and the I-70/CO-9
Silverthorne interchange (135 m/130 m spans, both-sides rebound min
-0.87/-0.59 m - comfortably below threshold, not a marginal pass) stays
genuine. The remaining genuine list is dominated by I-70/"Gerald R. Ford
Memorial Highway" interchange-area structures plus a handful of paved
highway creek crossings (CO 91, US 6, Water Street, Tenth Mountain Division
Memorial Highway) - CO-9's own Hoosier Pass switchback population is almost
entirely demoted now (1 of ~22 candidates survives), matching the reviewer's
specific complaint.

Total genuine mileage (1.78 km) landed below the task's own "~2-5 km"
expectation. Not adjusted further to chase that number - see
`verify_bridge_rebound.png`'s tuning curve: every threshold loosening tried
recovers km almost entirely by re-admitting CO-9/Hoosier bench candidates,
not by any named-plausible structure, and the two hard named requirements
(Silverthorne genuine, Boreas Pass/County Road 4/Homestake Creek/Robbers
Nest Drive all zero) are both satisfied at the chosen thresholds.

Full bake chain re-run to completion: `build_road_embankments.py` ->
`export_road_embankment_bin.py` -> `build_road_bridges.py` ->
`export_road_bridge_bin.py`. 23 bridge runs / 378 stations / 86 piers
exported to 11 `.bin` tiles.

**Offline verification (scratchpad, no `-game` launch per current
project-wide protocol):**
- `verify_bridge_subclass2.png` - county map, genuine (red) vs demoted
  (purple) spans, before/after km in the title, Boreas Pass Rd (0/28) and
  the Silverthorne interchange (stays genuine) annotated.
- `verify_bridge_rebound.png` - REBOUND_WINDOW_M tuning: total-genuine-km-
  vs-window curve (with the Silverthorne first-pass window marked) plus
  overlaid drop profiles for bowl-shaped genuine spans vs. plateau-shaped
  demoted benches, at the chosen 3 m/70 m thresholds.
- `verify_bridge_stats2.txt` - discriminator in plain language, full
  genuine-span list (23 rows), a demotion-reason sample (unpaved vs.
  rebound vs. both), named-geography validation checks, and honest caveats
  (name-based OSM join risk, TIGER-fallback heuristic risk, threshold
  tuned-to-this-county risk, and 7.21's still-unresolved Swan Mountain
  Road/water-signal-never-fires caveat).

Committed: `pipeline/build_road_embankments.py` only (this pass touches no
other pipeline module). Bakes (road_embankments.json, road_bridges.json,
bridge_subclass_report.json, bridge_rebound_debug.json, and the exported
.bin tiles) are all gitignored per `.gitignore` - regenerated locally, not
committed, matching 7.18-7.21 precedent. Not pushed.

### 7.23 Water crossing as a first-class bridge trigger (2026-09-07)

7.21/7.22 left an explicit unresolved caveat: the water-crossing signal
never fires anywhere in the county because it only sub-classifies runs that
already passed the pre-existing 7 m DEM-drop threshold, and no road drop
near mapped water reaches 7 m - Swan Mountain Road's near-miss stretch
(the task's own named target, next to Dillon Reservoir) peaks at 6.0 m/
2.7 m, just under. This pass makes water-crossing an unconditional bridge
trigger, independent of DEM drop, with a guard against dam-top/at-grade-
shoreline false positives, plus water-surface-relative pier/deck geometry.

**Offline inventory first** (`scratchpad/inventory_water_crossings.py`,
`inventory_water_crossings2.py`, against the same baked lake polygons
`compute_side()` already reads via `load_lake_beds()`): at the pipeline's
own `ROAD_HALF_WIDTH` (4.5 m) pavement-edge offset, zero stations
county-wide have BOTH edges inside a lake polygon. Only two roads ever have
ONE edge inside a polygon: Swan Mountain Road (20 stations, ~250 m
shoreline stretch) and an unrelated unnamed local road near two small
ponds (13 stations total, two short clusters). Centerline-distance scan of
Dillon Dam Road, US 6, CO 9, CO 91, and I-70/"Gerald R. Ford Memorial
Highway" against every nearby lake polygon found none ever closer than
~10 m at centerline anywhere in the county - Dillon Dam Road specifically
never closer than ~12 m. No OSM `man_made=dam` tag exists anywhere in this
county's cached `osm.json` (checked directly), so the dam question is
answered by geometry alone, not a tag lookup.

**New trigger** (`build_road_embankments.py`, `main()`, between
`compute_side()` and `subclassify_bridge_runs()`): a station is forced to
`BRIDGE_CLS` regardless of measured drop if (a) both pavement edges are
inside a lake polygon (trusted unconditionally), or (b) one edge is inside
a lake polygon AND the road clears that lake's own BAKED surface Z (not the
raw DEM) by >= `WATER_CROSSING_MIN_CLEARANCE_M` (1.5 m) -
`WATER_CROSSING_MIN_CLEARANCE_M`/`WATER_PIER_SUBMERGENCE_M` calibrated
directly against the Swan Mountain Road evidence (actual clearance there is
-1.2 m to +0.6 m, well under the 1.5 m guard, correctly excluding it as an
at-grade shoreline road rather than a deck). Forced runs re-run through the
existing `demote_short_runs()` so isolated single-station polygon touches
don't spawn degenerate stubs. `subclassify_bridge_runs()`'s own
unconditional-water-OR now reads this guarded `water_crossing` mask instead
of the raw `in_lake` geometry test, so a real water-crossing run bypasses
the paved-road/rebound/short-both-sided gates entirely, as intended.

**Water-surface-relative pier/deck geometry:** any in-lake bridge station
now ships `drop_cm` measured against the matching lake's baked surface Z
plus `WATER_PIER_SUBMERGENCE_M` (0.5 m) instead of the raw DEM sample, so
`build_road_bridges.py`'s existing unchanged pier-height math
(`drop_cm - DECK_DEPTH_M`) terminates a water-crossing pier's foot a
half-meter below the real, flat water surface instead of at a noisy raw-DEM
"lakebed" reading. `build_road_bridges.py` itself needed no changes.

**Result:** on this county's current OSM+DEM data, the new trigger's
guarded one-edge case fires on 2 stations (an unrelated short local road
near a small pond, clearance +1.7/+2.2 m), both demoted back to none by the
pre-existing 10 m minimum-run-length rule before reaching the report; the
unconditional both-edges case fires zero times; Swan Mountain Road never
qualifies (guard correctly excludes it). Full bake chain re-run
(`build_road_embankments.py` -> `export_road_embankment_bin.py` ->
`build_road_bridges.py` -> `export_road_bridge_bin.py`) to completion:
genuine bridge list is IDENTICAL to 7.22's - same 23 runs / 378 stations /
86 piers / 1.775 km. Net new mileage from the water trigger: +0 km. This is
the honest, calibrated result for the county actually surveyed, not a bug -
the mechanism is real and exercised (2 stations were genuinely forced to
bridge-class by lake-polygon geometry before the length filter), it simply
found no qualifying crossing in this data, matching the task's own sanity
check from the other direction ("if it explodes, the crossing test is
wrong" - a test that finds nothing after correctly excluding both named
"should not bridge" cases is the expected outcome here). Named validation
unchanged and re-asserted against the fresh bake: Boreas Pass Road 0/28,
County Road 4 0/8, Homestake Creek Road 0/2, Robbers Nest Drive 0/4, Garnet
Road 0/2, all still 0 genuine.

**Offline verification (scratchpad, no `-game` launch):**
- `verify_water_crossings.png` - county map, lake polygons + all roads,
  skirting (orange, Swan Mountain Rd) vs. crossing (red, none present)
  stations, with Swan Mountain Rd/Dillon Dam Road/US 6-CO 9-I-70
  annotations giving each case's crossing-vs-skirting-vs-dam verdict and
  evidence.
- `verify_water_bridge_profile.png` - longitudinal profile of Swan Mountain
  Road's shoreline stretch (road elevation vs. baked lake surface Z vs. the
  1.5 m guard threshold, tight y-limits so the ~1-2 m of real signal is
  visible) plus a schematic cross-section of the pier/deck water-surface
  termination the code path would produce if/when a real crossing
  qualifies.
- `verify_water_bridge_stats.txt` - full inventory findings, dam-guard
  calibration evidence, bake-chain numbers, and honest caveats - most
  importantly that this only extends the LAKE-polygon water test;
  `build_water_tiles.py` explicitly does not bake linear
  `waterway=river|stream` features (rivers/creeks), so real CO 9/Blue River
  or I-70/Tenmile-Straight Creek crossings remain out of reach of this
  specific test and would need a separate linear-waterway baking pass to
  address - a real scope limitation, not fixed by this change.

Committed: `pipeline/build_road_embankments.py` only (this pass touches no
other pipeline module). Bakes (road_embankments.json, road_bridges.json,
bridge_subclass_report.json, and the exported .bin tiles) are gitignored -
regenerated locally, not committed. Not pushed.

### 7.24 Two small backlog cleanups: VSM overflow warning + 7.18 dead code (2026-09-07)

Two unrelated items pulled off the backlog together, one commit each.

**VSM "Non-Nanite Marking Job Queue overflow" warning:** researched (not
fixed). The game logs `[VSM] Non-Nanite Marking Job Queue overflow.
Performance may be affected...` from Virtual Shadow Maps under heavy
non-nanite HISM use (forests, guardrails, embankments, bridges, falling
snow). Grepped the installed UE 5.8 engine source
(`VirtualShadowMapCacheManager.cpp:1081`) for the exact warning string and
traced its trigger back through
`VirtualShadowMapBuildPerPageDrawCommands.usf` to `MARKING_JOB_QUEUE_SIZE
= NUM_THREADS_PER_GROUP * 2` (= 128 groupshared slots) and
`MAX_SINGLE_THREAD_MARKING_AREA = 8` - both hard-coded compile-time
constants (`NUM_THREADS_PER_GROUP` comes from a C++ `static constexpr
int32 = 64` in `VirtualShadowMapArray.cpp`, injected as a shader
`#define`), not backed by any `TAutoConsoleVariable`/`.ini`-settable cvar
anywhere in the engine source. The two candidate cvars guessed up front
(`r.Shadow.Virtual.NonNanite.NumPageAreaDiagSlots` and
`...LargeInstancePageAreaThreshold`) both turned out to be
`#if !UE_BUILD_SHIPPING` diagnostics-only controls (an on-screen "worst
offenders" readout), unrelated to the queue's actual capacity - confirmed
by reading their only call sites. Per "no guessing without evidence," did
**not** add either cvar to `OpenDriveUE/Config/DefaultEngine.ini`, since
neither would change the overflow behavior at all. Practical notes for
whoever picks this back up: the warning is purely informational (it
explicitly does NOT cause visual artifacts, unlike the Page Pool/Visible
Instances overflow messages in the same switch statement) and is logged at
most once per session (its `LoggedOverflowFlags` bit is latched and never
cleared, unlike the Page Pool one). The only real levers are content-side
(fewer/smaller simultaneous large non-nanite shadow footprints - e.g.
converting the heaviest HISM sets to Nanite meshes) or globally lowering
VSM resolution via the existing `r.Shadow.Virtual.ResolutionLodBias{Local,
Directional}` cvars, which was judged out of scope for a one-line config
cleanup. Full source citations and call-site tracing in
`verify_vsm_fix.txt` (scratchpad, not committed - nothing to commit for
this half of the task).

**7.18 dead code removal:** `pipeline/road_mesh_common.py`'s
`compute_junction_influence()` and `flatten_junction_elevations()` -
flagged as unused-but-left-in-place in 7.18's own assessment above (lines
4131-4142) - are now actually deleted. Re-confirmed grep-clean across the
whole repo (`pipeline/`, `unreal/`, `OpenDriveUE/`): zero call sites, only
the same historical comments 7.18 already catalogued (which describe the
superseded approach, not calls, and were left as-is). Removed both
function bodies (85 lines) from `road_mesh_common.py`; left
`reconcile_junction_elevations()`, `classify_wall_stations()`, and all the
`JUNCTION_*` constants (still consumed by live code) untouched. Verified
with `python -m compileall` on the module and its `build_road_embankments.py`
consumer, an explicit `hasattr()` check proving the two functions are gone
while `reconcile_junction_elevations`/`classify_wall_stations` remain, and
a passing run of `pipeline/tests/test_sample_mesh_height.py` (4 passed) as
the fast consumer-stage check. Full before/after `git diff --stat` and
command output in `verify_deadcode_cleanup.txt` (scratchpad).

Committed: `pipeline/road_mesh_common.py` (dead code removal) and this
note. The VSM investigation touched no repo files. Not pushed.

### 7.25 `-odrev` "veers off-road at towns": diagnosed, reclassified as
expected behavior, not a bug (2026-09-07)

Picked up the 7.24-listed leftover from line 2629 above. Read
`ODVehiclePawn.h`/`.cpp` before touching anything: `-odrev=T` is one of a
family of straight open-loop test hooks (`-odrev`/`-odlift`/`-odsteer`/
`-odbrake`), explicitly designed (per the class comment, `ODVehiclePawn.h`
lines 26-39) to isolate single variables in headless verification without
"fighting the key input." `SmSteer`'s target comes only from
`bRightKey`/`bLeftKey` (real keyboard only) - nothing in the `-odrev`/
`-odlift`/`-odbrake` path ever sets them, so a bare `-odrev=T` run (no
`-odsteer`) drives dead straight for its entire duration, by construction.
Grepped the whole repo (`OpenDriveUE/Source`, `pipeline/*.py`) for
pure-pursuit/lookahead/route-follow/autodrive/autopilot: there is no
road-graph or station-polyline follower anywhere. The one "LOOKAHEAD" hit
is unrelated (yaw-smoothing in the road MESH generator,
`build_road_network.py:204`).

So "veers off-road at towns" isn't a controller defect to fix - it's the
deterministic geometric result of driving straight from a point on a
curving road, and it happens faster at a tight town-corner radius than a
gentle highway bend. Confirmed against real baked data
(`data/road_tiles/summit_county_test/road_stations.json`, ROAD_HALF_WIDTH
= 4.5 m): a straight line from three sharp town corners (Main Gore Drive,
North Park Ave/CO-9, Ski Hill Road switchback - curvature up to ~0.068
rad/m, radius ~15 m) leaves the pavement in 2.8-6.2 s of simulated travel,
while the same test against a gentle mountain corridor (French Gulch Road,
curvature ~0.02 rad/m - the same shape of corridor as the Vail
`-odrev=20` perf run noted above at line 2637, which was never flagged as
veering) takes ~13 s. Full numbers, method, and caveats (the longitudinal
speed profile is an approximate stand-in for `UODVehicleMovement`'s real
torque-curve integration; no tire slip/suspension/contact-patch model) in
`verify_odrev_stats.txt`; before/after-style plots of the straight path vs.
the real centerline and the cross-track-error blowup in
`verify_odrev_diagnosis.png` (both scratchpad, not committed).

**No code or data fix applied.** Making `-odrev` follow the road would mean
building a route-following steering system (road-graph selection +
pure-pursuit/lookahead) that doesn't exist in this project at all - that's
a new feature, not a fix, and it would defeat the actual purpose of these
hooks as isolated single-variable tests (reintroducing exactly the
folded-together confounders the project's diagnostic convention says to
avoid). `cars_manifest.json`, vehicle physics tuning, and season files
untouched; no C++ changed, so no rebuild needed. If Taylor wants an actual
scripted whole-town review-drive capability later, that's worth scoping as
its own feature - it isn't "fixing" anything that currently exists.

Committed: this note only (reclassifies the line 2629 backlog item from an
open defect to a diagnosed non-issue). Not pushed.

### 7.26 Linear waterways (rivers/named streams) baked as water ribbons (2026-09-07)

Closes the 7.23 caveat directly: `build_water_tiles.py` explicitly did not
bake linear `waterway=river|stream` features, so real CO-9/Blue River and
I-70/Tenmile-Straight Creek crossings never showed water. This pass adds
that (`pipeline/build_water_tiles.py` only - no runtime/terrain code
changes were needed, see below).

**Selection:** `waterway=river` always; `waterway=stream` only if named
(the Overpass query itself excludes unnamed streams server-side -
`fetch_waterways()`). Result on this county's data: 2064 raw OSM ways ->
382 chained centerlines (10 river, 372 named stream) after joining
same-name/same-class segments that share an OSM node
(`chain_waterways()`). Two named waterways (e.g. Blue River, split into a
2365-segment group and a separate 1205-segment group) did not fully merge
into one chain because the raw OSM way graph itself isn't node-contiguous
across the whole county - reported honestly, not silently merged/fudged.

**Geometry:** centerlines resampled every 20 m, buffered into ribbon
quads (`offset_polyline()`, simple average-tangent miter, no explicit
miter-limit clamp - no visible spike found in this data, not exhaustively
checked), width from OSM `width=` tag where present and sane (none tagged
in this county's data - 0/382 tagged, all 382 used the class default:
river 10 m, named stream 3.5 m) else the class default. Each ribbon
segment is fed through `build_lake_mesh()` - the SAME mesh builder lakes
already use - and appended to the SAME per-tile "lakes" list
`build_terrain_mesh_tiles.load_lake_beds()`/`apply_water_depression()` and
`ODWaterManager.cpp`'s `CreateLakeComponent` already iterate over
generically (verified by reading both: `load_lake_beds()` extracts
boundary rings per mesh entry via `_extract_boundary_rings(tris)`, with no
lake-specific assumption; the runtime loop is a plain `for l in
0..LakeCount`). So M_Water + seasonal freeze apply for free, and no
runtime/terrain code changed.

**Elevation:** flow direction derived by comparing mean DEM elevation of
the first vs. last fifth of each chain (reverses the array so index 0 is
always the upstream/high end), then a centered moving-average smooth
followed by a running-minimum clamp (`monotonic_flow_profile()`) so the
water surface is guaranteed non-increasing along flow even if smoothing
reintroduces a bump. Water surface sits a flat 0.4 m below that smoothed
"bank" reading (`WATERWAY_DEPTH_M`) - a judgment call, not a surveyed bank
height, since DEM resolution (~9 m ground sample) can't resolve a true
channel separately from its banks at these widths (documented caveat).

**Roads:** a per-vertex nearest-road-point check (`cKDTree` over every
road station, `build_road_point_index()`) classifies each ribbon segment
crossing a road corridor as either a "crossing" (>= 45 deg between
waterway and road tangent - left alone, bridge deck + terrain carve
already separate them vertically) or a "parallel run" (clipped off
pavement via `clip_corner_to_road()`, a one-shot corner pull-back to
`ROAD_HALF_WIDTH + 1.0 m` clearance). Result: 449 crossings found (317
coincide with an existing bridge station; the other 132 do NOT - water
will render crossing the road at grade there, an honest caveat, not
silently hidden), 135 parallel-run clips applied (pavement never gets a
ribbon corner closer than the clearance margin).

**Perf:** lakes alone touch 313 tiles (1185 lakes, 1602 OSM polygons
fetched, 417 skipped as slivers); adding 43,912 ribbon segments (870.6 km
total) brings it to 509 tiles total (196 new), 215,037 verts / 124,879
tris across all water tiles - proportionate to, not an order of magnitude
beyond, the lake bake.

**Full bake chain re-run to completion, in the foreground:**
`build_water_tiles.py` (Overpass fetch + lake/ribbon bake, clean run, one
Overpass mirror timeout/fallback, no errors) then
`build_terrain_mesh_tiles.py` (~38 min, 1221/1221 tiles built, 0 errors,
now loading 45,097 lake beds from 509 water tiles - up from the pre-pass
lake-only count - confirming ribbon polygons now feed
`apply_water_depression()` identically to lakes; 6,895,831 verts /
12,967,397 tris, 305 tiles used the coarse fallback same as before this
pass). No other pipeline stage (`build_road_stations.py`,
`build_road_embankments.py`, `build_road_bridges.py`, etc.) consumes
water-tile output, so nothing else needed rerunning; §7.18-7.23 station/
bridge numbers are unaffected (no road/station code touched) and were not
re-verified since nothing they depend on changed.

**Offline verification (scratchpad, no `-game` launch):**
- `verify_waterways_map.png` - county map: lakes + ribbons by class
  (river/stream), named annotations (Blue River, Tenmile Creek, Straight
  Creek, Snake River, Swan River where present), insets for a confluence
  union, the Breckenridge Blue River/CO-9 corridor, and a representative
  road crossing.
- `verify_waterways_profile.png` - longitudinal profiles (raw DEM vs.
  smoothed monotonic "bank" vs. final water surface) for Blue River and
  one named creek, plus a schematic cross-section at a road crossing
  (deck above, water below).
- `verify_waterways_stats.txt` - full selection/width/crossing/tile-count
  numbers and the honest caveats listed above (miter join, confluence
  handling, DEM resolution, the 35 m lake shore-blend now also applying to
  narrow ribbons - unretuned, in-engine look is Taylor's to confirm on a
  drive-test).

Committed: `pipeline/build_water_tiles.py` only. `cars_manifest.json`,
`apply_water_material.py`, `ODSeasonController`,
`build_flora_materials.py`, `apply_ground_material.py`, and all vehicle
physics untouched. Bakes (water tiles, terrain tiles, and the raw
Overpass/summary caches) are gitignored - regenerated locally, not
committed. Not pushed.

### 7.27 Gap-clip the water ribbon at unbridged road crossings (2026-09-07)

Closes the 7.26 "132 crossings render at grade" caveat directly. Of the
449 waterway/road crossings 7.26 found, 317 coincide with a 7.22 bridge
station (deck + terrain carve already separate ribbon from road
vertically - left alone) but 132 do not, and the ribbon rendered straight
across the road pavement at grade there - a visible water-sheet-over-
pavement defect. `pipeline/build_water_tiles.py` only (no road/station/
bridge pipeline code touched, per scope).

**Fix:** `build_waterway_ribbons()`'s existing per-segment crossing check
(unchanged - angle >= `ROAD_CROSSING_ANGLE_DEG`=45 deg between waterway
and nearest-road tangent) now branches on the nearest road station's
`is_bridge` flag. Bridged: draw the segment exactly as before (one
continuous quad, untouched code path). Unbridged: two new helpers,
`find_road_gap_intervals()` (sub-samples the segment at 21 points, finds
contiguous runs within `ROAD_HALF_WIDTH + ROAD_GAP_MARGIN_M` = 4.5 + 1.5 =
6.0 m of the nearest road station, rounds each run outward by one
subsample step - conservative, never leaves a sliver poking out) and
`retained_t_intervals()` (the segment's [0,1] span minus the gap
interval(s)) rebuild the segment's ribbon quad only for the retained sub-
spans, corners/elevation linearly interpolated between the segment's own
two endpoints. A retained fragment shorter than `MIN_GAP_FRAGMENT_M`=1.0 m
is dropped instead of emitted as a sliver (handles a gap consuming a whole
chain-end stub). Each segment is evaluated independently, so two close
crossings, a road crossing the same ribbon twice at a switchback, and a
gap spanning more than one resampled segment all fall out of the same
per-segment logic with no special-casing - confirmed against real cases in
this county's data (see verification below).

**This session's re-run numbers** (fresh Overpass fetch, not the cached
7.26 numbers - see the stats file for why the crossing count itself
shifted slightly, 453/320/133 vs. 449/317/132; not a code regression, the
gap-clip logic is unaffected either way): 119 of 133 non-bridged crossings
actually gap-clipped (14 were flagged "crossing" by the existing coarse
trigger but never came within the tighter 6 m gap threshold at any
subsample - a real "no defect there" case, not a bug); 5 sub-fragments
dropped below the 1.0 m floor; ribbon length 879.95 km after vs. 880.91 km
if every gap were left uncut - 0.109% shrink, trivial, confirms the fix
only removes the road-corridor pinch and not real creek length.

**Terrain rebake: needed, and done, not skipped.** Checked `build_terrain_
mesh_tiles.py`'s `apply_water_depression()` before deciding whether the
~10 m gap would be visually negligible - it is not: the terrain water-bed
carve is `WATER_BED_MIN_DEPTH_M`=2.0 m at the shoreline ramping to
`WATER_BED_MAX_DEPTH_M`=5.0 m over a 40 m falloff (plus a 35 m exterior
shore-blend), applied to the tile's FINAL grid AFTER the road carve - so
every one of the 132 unbridged crossings had a real 2-5 m pit carved
directly under the road pavement before this fix, not just an at-grade
water sheet. Cutting the water MESH gap alone would leave that pit baked
into already-built TERRAIN tiles with no water visible in it - a more
confusing defect than the one being fixed. Both `build_water_tiles.py` and
`build_terrain_mesh_tiles.py` were re-run to completion in the foreground:
water bake clean (one Overpass mirror timeout/fallback, same pattern as
7.26), terrain bake 1221/1221 tiles, 0 errors, 305 coarse-fallback tiles
(same count as the pre-change bake, unaffected), 6,895,831 verts /
12,967,397 tris, 45,644 lake beds loaded from 513 water tiles (up from
45,097/509, consistent with gap-clipped segments now contributing more,
smaller ribbon fragments).

**Offline verification (scratchpad, no `-game` launch):**
- `verify_ribbon_clip.png` - three zoomed panels: (a) "Snake River" at an
  unbridged crossing, before (red dashed, uncut) vs. after (blue, gap cut)
  with the road corridor overlaid; (b) "Soda Creek" at a real 7.22 bridge
  station, ribbon drawn continuous through the corridor, no gap; (c)
  "Buffehr Creek", two distinct crossings near a real road switchback in
  this county's data, each independently gap-clipped.
- `verify_ribbon_clip_stats.txt` - full numbers above, the bridged-
  crossing preservation proof (code-path + numeric + visual), the terrain-
  rebake justification, and honest caveats (gap-boundary discretization,
  no actual culvert geometry/drainage modeled, one switchback example not
  an exhaustive sweep, not drive-tested).

Committed: `pipeline/build_water_tiles.py` and this note only.
`cars_manifest.json` and all road/station/bridge pipeline code untouched.
Bakes are gitignored - regenerated locally, not committed. Not pushed.

### 7.28 Ambient biome/city audio (2026-09-08) — closes the 5.4 backlog item

Tile-keyed ambient sound crossfades as the player drives, per the 5.4 ask:
forest wind/rustling, water babble near creeks/rivers/lake shores, town hum
scaled by settlement density, and a base wind bed under everything else
(open/alpine terrain). Day/night and season coupling deliberately NOT wired
this pass — `SetTimeOfDayHint()`/`SetSeasonHint()` are unused no-op seams for
later, per the brief ("keep hooks simple").

**Architecture:** `AODAmbientSoundManager` (`OpenDriveUE/Source/OpenDriveUE/
ODAmbientSoundManager.h/.cpp`) — same find-or-spawn singleton shape as
`AODEmbankmentManager`/`AODSeasonController`, wired via
`ODGameMode::EnsureAmbientSoundManager()` at the end of `BeginPlay()`.
Deliberately diverges from the per-2km-tile streaming pattern every other
manager in this project uses: the ambient-zone grid is small enough (100 m
cells, a few hundred KB for the whole county) to load once as a flat
whole-map grid and index with arithmetic, no async tile load/unload plumbing
earns its keep here. Four `AudioComponent`s (Forest/Alpine-wind-bed/
Settlement/Water) are spawned once via `UGameplayStatics::SpawnSound2D`
(non-spatialized — these are ambience beds, not point sources) and held
permanently; only their volumes crossfade (`FMath::FInterpTo` toward a
per-layer target, `CrossfadeSeconds`=4s) as the player's position is
re-sampled against the grid at a low rate (`SampleHz`=2). Alpine doubles as
the base "open" wind bed under every zone; Forest/Settlement layer on top of
it rather than replacing it; Water is an independent layer driven by
`SampleWaterDistM()` (bilinearly interpolated, fades in over the last
`WaterFadeInStartM`=60 m of approach) rather than the zone class.

**Zone bake:** new `pipeline/build_ambient_zones.py` bakes a compact
`ODA1`-tagged binary sidecar (`OpenDriveUE/Content/OpenDrive/Masks/
ambient_zones_<area>.bin`, generation-id stamped same convention as every
other sidecar) from `classify_biomes.py`/`classify_settlements.py`'s
existing tile classification data plus a new water-proximity raster
(`scipy.ndimage.distance_transform_edt` over waterway/lake geometry from
`build_water_tiles.py`'s own `osm_water.json`/`osm_waterways.json`,
bounding-box-limited per segment — a naive full-grid vectorization over
~41,000 segments didn't finish in 15+ minutes and had to be killed; the
bbox-limited rewrite runs in seconds). Coarse 100 m grid, explicitly allowed
by the brief ("keep it SIMPLE"). Baked for Summit County: 307,726 cells,
1.47 MB, generation id `e26dcbfb2f64c684`.

**Assets:** 5 CC0 loops from freesound.org (4 wired, 1 sourced-but-spare —
see `content_src/audio/ambient/manifest.json` for full source URLs/authors/
licenses):
- `forest_wind` — teadrinker, "wind_forest_08_strong_l_01.wav" (id 403051)
- `creek_babble` — VKProduktion, "Creek 06 (loop)" (id 231536) — primary Water layer
- `lake_shore` — klangfabrik, "wave loop 1.wav" (id 428088) — sourced/verified alternate for lake-heavy drives, not wired to a distinct C++ layer this pass (manager has one generic Water layer, not a lake-vs-creek split)
- `town_hum` — DotAfter, "traffic-cars-ambience.wav" (id 610978)
- `alpine_wind` — ERR0, "Rolling Wind - looping" (id 210220)

All CC0 1.0. Fetched from freesound's public preview CDN rather than each
sound's login-gated original (this project doesn't create accounts) — see
manifest `_comment` for the honest disclosure that these are lossy preview
transcodes, not the original bytes, though license/authorship/source are
unaffected. `content_src/audio/ambient/download_ambient_audio.py` +
`manifest.json` are tracked (raw MP3s gitignored, deterministic re-fetch);
`normalize_ambient_audio.py` loudness-matches them to a common LUFS target
with peak-safe headroom into `normalized/*.wav` (also gitignored,
deterministic re-run); `unreal/import_ambient_audio.py` imports those as
looping (`bLooping=True`) SoundWave assets under `/Game/OpenDrive/Audio/
Ambient/`, which `AODAmbientSoundManager::BeginPlay()` `LoadObject()`s by
fixed path (same convention as `AODEmbankmentManager::EmbankMat`) — an
initial pick (NomadApe's "Wind through trees", id 444921) was swapped out
for the current `forest_wind` after objective analysis showed it was mostly
near-silence with one loud gust, a bad fit for a continuous bed.

**Objective checks (offline, no `-game`):** waveform + spectrogram +
loop-seam-zoom plot and a LUFS/RMS table per asset. No clipping in any of
the 5. Loop-seam jump/RMS ratios all in the same 0.0001–0.53 range (town_hum
lowest at 0.0001, creek_babble highest at 0.53 — a babbling-water asset
having a less pristine sample-level seam is expected and not evidence of a
bad loop by itself, no isolated hard click visible in the zoomed plot).
Post-normalization LUFS: forest/creek/lake/alpine all land at exactly the
-23.0 target; town_hum lands at -26.4 (peak-ceiling-limited — its own true
peak was already close to -1 dBFS pre-gain, so pushing further would have
clipped it) — the four crossfaded layers are level-matched enough that no
one layer will read as wildly louder than another, per the brief's ask
("loudness normalized across the set").

**Build:** `Build.bat` (`OpenDriveUEEditor Win64 Development`) succeeded —
`ODAmbientSoundManager.cpp`, `ODGameMode.cpp` compiled clean, link OK.
`unreal/import_ambient_audio.py` run headless (`-run=pythonscript
-nullrhi`), 5/5 SoundWave assets imported successfully.

**What's unprovable offline / Taylor's live-session job:** the actual
subjective character of each loop (whether the wind sounds right, whether
the town hum is too loud/quiet relative to engine/road noise once real
gameplay audio is mixed in, whether the crossfade timing feels natural at
driving speed) — none of that can be judged from waveform/spectrogram data,
per this project's testing protocol. Also unprovable offline: whether
`SampleHz`=2/`CrossfadeSeconds`=4/`WaterFadeInStartM`=60 are the right
tuning constants in practice (they're reasonable defaults, not derived from
any measurement) and whether `lake_shore` should get its own distinct layer
instead of staying an unwired spare, which depends on how often Taylor's
drives actually hug Dillon Reservoir vs. river/creek corridors.

Committed: `ODAmbientSoundManager.h/.cpp` (new), `ODGameMode.h/.cpp`
(edited — `EnsureAmbientSoundManager()` hookup), `pipeline/
build_ambient_zones.py` (new), `content_src/audio/ambient/manifest.json` +
`download_ambient_audio.py` + `normalize_ambient_audio.py` (new),
`unreal/import_ambient_audio.py` (new), `.gitignore` (raw/normalized audio
ignore rules), this note. `cars_manifest.json` and all season/vehicle/road
pipeline code untouched. Content/ and pipeline bakes are gitignored —
regenerated locally, not committed. Not pushed.

### 7.29 Ambient audio polish: loop-tail trim + lake-vs-creek Water split (2026-09-08)

Two-part follow-up to 7.28's ambient biome/city audio, both offline
(no `-game`/PIE — waveform/data checks and a headless import commandlet
only, per this project's testing protocol).

**Part A — creek_babble loop-tail dropout, fixed.** Reviewing 7.28's
verify plot found a ~30ms near-silence flat region immediately before
`creek_babble`'s loop point — classic MP3-encoder padding left over from
the freesound preview transcode (see manifest `_comment` on why previews,
not logged-in originals, are what this project fetches). Since the 36s
`creek_babble` WAV plays looping as the primary Water/Creek layer, that
tail read as a periodic ~30ms dropout every loop. `normalize_ambient_audio.py`
now trims contiguous head/tail near-silence (`trim_edge_silence()`:
-60dBFS RMS-envelope threshold, only a run touching sample 0 or the last
sample counts — never a quiet stretch mid-asset — and only if the *whole*
run is under 150ms; a longer run is left untouched entirely rather than
partially cut to the cap, so it doesn't quietly clip a genuine fade).
Re-running against all 5 raw previews: `creek_babble` tail 30.7ms → 0.0ms
(seam jump +49.9dB → -0.7dB), `town_hum` head 48.0ms trimmed (real
padding) but its tail — 210.7ms, a genuine fade into background quiet —
correctly left alone since it exceeds the 150ms cap; forest_wind/
lake_shore/alpine_wind had no edge silence to trim. Re-imported all 5
SoundWaves via `unreal/import_ambient_audio.py` (headless commandlet, 5/5
imported clean, `SW_CreekBabble` now 35.96s not 35.99s). Verification:
scratchpad `verify_ambient_seams2.png`/`.txt` (before/after zoom + jump/RMS
table per asset, plus a raw-preview-vs-normalized creek_babble overlay).

**Part B — lake_shore wired as a distinct lake-vs-creek Water split.**
7.28 sourced/normalized/imported `lake_shore` (`SW_LakeShore`) but the
manager only had one generic Water layer fed by `creek_babble`, faded by a
single combined water-distance channel. `pipeline/build_ambient_zones.py`
now bakes TWO independent distance channels — `LakeDistM` (from
`osm_water.json` lake/reservoir polygons, same source `build_water_tiles.py`
uses) and `WaterwayDistM` (from `osm_waterways.json` ribbon chains, same
bbox-limited-per-segment `distance_transform_edt` approach 7.28 already
proved out — still seconds, not the 15+min naive full-grid version that
was killed once already) — replacing the sidecar's single `WaterDistM`
field. Bumped the magic to `ODA2` (a field-count *and* field-meaning
change, not purely additive, so `ODAmbientSoundManager.cpp::LoadZoneGrid`
explicitly checks for `'2'` after the `ODA` prefix and refuses/logs-and-
disables rather than misreading an old `ODA1` file's bytes at the wrong
offsets). `AODAmbientSoundManager` splits Water into `LayerCreek`
(`CreekSound`/`SW_CreekBabble`, faded by `SampleWaterwayDistM()`) and
`LayerLake` (`LakeSound`/`SW_LakeShore`, faded by `SampleLakeDistM()`),
both keeping the existing 60m `WaterFadeInStartM` linear fade and bilinear
4-cell sampling (factored into a shared `FParsedAmbientZoneGrid::
BilinearSample()` helper). No special-casing for a creek-into-reservoir
confluence — both layers can independently approach full volume there, and
their existing independent crossfades already produce whatever blend that
implies. Alpine/Forest/Settlement logic untouched. `Build.bat` succeeded
(`ODAmbientSoundManager.cpp`, `ODGameMode.cpp` recompiled clean, link OK,
no stray `UnrealEditor` process found before building). Regenerated the
Summit County sidecar: 307,726 cells, 2,154,122 bytes (7 bytes/cell now,
up from 5), generation id `e26dcbfb2f64c684`.

**Verification (offline):** scratchpad `verify_ambient_split.png` — two
panels, lake-distance ≤60m band vs waterway-distance ≤60m band, with
Silverthorne/Frisco/Breckenridge/Dillon Reservoir markers. Lake band: 2,852
cells, visibly hugs Dillon Reservoir (the big contiguous blob right at that
marker) plus a scatter of small real lakes elsewhere — matches
expectation. Waterway band: 542 cells, correctly spread across the whole
bbox rather than clustered at the reservoir, but reads as scattered
isolated points rather than a visually continuous ribbon when zoomed in —
a raw `osm_waterways.json` overlay (43,347 points across 2,070 ways)
confirms the underlying stream network itself is dense and well-registered
(a normal dendritic drainage pattern), so this is a 100m-grid-vs-60m-band
resolution artifact (a thin creek's fade band is often narrower than one
grid cell, so only sporadically-aligned cell centers land inside it), not
a registration or rasterization bug — same coarse-grid tradeoff 7.28's
`CELL_SIZE_M=100` already accepted, just more visible once split out from
the lake signal's much bigger footprint. Overlap (both bands, a creek-into-
reservoir confluence) = 46 cells. `verify_ambient_split_stats.txt`
documents the byte-for-byte header/field layout and confirms the Python
`struct.pack('<BBHHB', ...)` writer and the C++ `Cur[0..6]` parse loop
agree on field order/widths; file size matches the computed
`header(40) + cells*7` exactly.

**What's unprovable offline / Taylor's live-session job:** whether the
lake vs. creek mix balance actually reads as two distinct water characters
in practice (both are currently at the same `WaterFadeInStartM`=60m/
`CrossfadeSeconds`=4s defaults as the old single layer — untuned relative
to each other); whether both playing together at a creek-into-reservoir
confluence sounds like a coherent blend or a muddy overlap; and the
trimmed `creek_babble` loop's subjective quality post-fix (the seam jump
dropped from +49.9dB to -0.7dB and the flat gap is objectively gone, but
whether the loop point itself sounds seamless is a live-listen call, not a
number). The waterway band's dotted/sparse look at 100m resolution
(discussed above) is a real characteristic of the current bake, not fixed
this pass — flagging in case Taylor's live drives along the Blue River
reveal the creek layer cutting in and out rather than holding steady.

Committed: `content_src/audio/ambient/normalize_ambient_audio.py` (Part
A trim fix), `pipeline/build_ambient_zones.py` (Part B ODA2 split),
`OpenDriveUE/Source/OpenDriveUE/ODAmbientSoundManager.h/.cpp` (Part B
layer split), `unreal/import_ambient_audio.py` + `content_src/audio/
ambient/manifest.json` (doc/comment updates reflecting both), this note.
Normalized WAVs and the regenerated `.bin` sidecar are gitignored, same as
7.28 — regenerated locally by re-running `normalize_ambient_audio.py` /
`build_ambient_zones.py`, not committed. Not pushed.

**Correction (2026-09-08, post-review): two defects found in the above,
both fixed.**

1. **Waterway raster was effectively empty.** The "scattered isolated
   points... not a registration or rasterization bug" call above was
   wrong — it *was* a rasterization bug. `rasterize_water()`'s waterway
   loop only marked a cell when the CELL CENTER fell within
   `WATERWAY_HALFWIDTH_M` (3.0m) of the segment. Cells are 100m
   (`CELL_SIZE_CM`) wide, so a polyline threading the grid mostly clips
   cell EDGES, not centers — almost every cell it geometrically passed
   through was silently dropped, leaving 542 near-random hits for an
   ~870km network instead of a connected trace, which killed the
   distance-transform (and therefore the 60m Creek fade) along nearly the
   whole river network. The same class of bug hit the lake polygon
   rasterizer too, worse: `osm_water.json`'s ~1,600 polygons have a
   *median* bbox of ~30-40m — smaller than one 100m cell — so pure
   cell-center point-in-polygon testing missed 1,239/1,600 (77%) of them
   outright. (The "lake count is tiny" assumption in the old docstring
   was true of the count, not the size.)

   Fixed both, keeping the existing per-feature bbox limiting (a naive
   full-grid version was previously killed after >15min — that constraint
   is preserved, not regressed):
   - `waterway_mask`: hit threshold widened from `d <= halfwidth_cm` to
     `d <= halfwidth_cm + CELL_SIZE_CM*0.5*sqrt(2)` (the cell's own
     half-diagonal), so every cell footprint a segment actually crosses is
     registered, not just the ones its center happens to land in. bbox pad
     widened to match.
   - `lake_mask`: switched from cell-center point-in-polygon
     (`matplotlib.path`) to exact polygon-vs-cell-square intersection
     (`shapely`), still bbox-limited per polygon so it stays fast (~1s for
     the whole file, not a full-grid scan).

   Result (same input data, `<=60m` band): lake-adjacent cells 2,852 →
   6,688; waterway-adjacent cells 542 → 12,470 (now a visibly connected
   dendritic trace of the Blue River/Snake River/Tenmile Creek network,
   not a scatter — see `verify_ambient_split2.png`); confluence overlap 46
   → 1,695. Regenerated the sidecar: same 307,726 cells/2,154,122 bytes/
   `ODA2` layout, generation id `e26dcbfb2f64c684` (byte-for-byte format
   unchanged, only the water-distance *values* changed, so no C++/loader
   changes were needed and none were made). Caveat: even fixed, 100m cells
   are still coarse relative to a 3m creek half-width — the field is now
   geometrically *correct* (every cell the line crosses is marked, and the
   distance transform propagates from a connected mask instead of
   scattered dots) but still resolves a creek's location to only ±~70m at
   the finest; that's the module's deliberate "coarse uniform grid, not a
   streaming-tile format" scoping call, not something this fix changes.

2. **`town_hum` loop-seam regression from Part A's trim.** Part A's fix
   correctly trimmed `town_hum`'s 48ms *head* encoder padding, and
   correctly left its genuine 211ms *tail* fade alone (over the 150ms
   cap). But the two together made the loop objectively worse: pre-trim
   the loop went quiet-tail-into-quiet-head (jump +8.44dB); post-trim it
   went quiet-tail-into-full-level-head (jump +29.52dB) — an audible pop
   every ~161s loop that did not exist before Part A. `creek_babble`'s
   fix was fine; this one asset's specific combination (trimmable head +
   untrimmable tail) wasn't checked against its own *before* state.

   Fixed by measuring the seam after trimming and, only if it still
   exceeds a 6dB gate, applying a 225ms equal-power crossfade that blends
   the tail into a *reversed* copy of the clip's own head (so the file's
   very last sample lands on `head[0]` — the actual sample the loop wraps
   to next — rather than on whatever the head sounds like 225ms in; an
   initial non-reversed version overshot to -8.0dB because `town_hum`'s
   head has its own ~300ms attack ramp, so blending against same-offset
   head content targeted the wrong point). `creek_babble` doesn't trigger
   the gate (already ~0.7dB post-trim) and is untouched.

   Result (seam jump, pre-trim `68d6340^` → post-trim `68d6340` →
   post-fix now): `forest_wind` -1.25 → -1.25 → -1.25 (untouched),
   `creek_babble` +49.91 → -0.69 → -0.69 (preserved), `lake_shore` -4.57 →
   -4.57 → -4.57 (untouched), `town_hum` +8.44 → +29.52 → **+0.04**
   (fixed, well inside the pre-trim gate), `alpine_wind` +1.08 → +1.08 →
   +1.08 (untouched). All 5 SoundWaves re-imported clean via
   `unreal/import_ambient_audio.py`. Caveat: the crossfade is a level/
   spectral-envelope match, not a waveform-phase match — it should read
   as seamless (`verify_ambient_seams3_zoom.png`'s stitched waveform is
   visually continuous through the loop point) but whether it's audibly
   perfect is Taylor's live-listen call, not a number this pass can settle.

   Also relevant to defect 1's caveat above and worth flagging together:
   both defects came from the same root mistake — treating a coarse-grid/
   small-feature or trim/no-symmetric-guarantee edge case as "verified"
   because the aggregate numbers looked plausible, without checking the
   specific asset/feature that combination actually breaks.

Fixed in: `pipeline/build_ambient_zones.py` (`rasterize_water()`),
`content_src/audio/ambient/normalize_ambient_audio.py`
(`loop_seam_jump_db()` / `apply_loop_crossfade()`, wired into `main()`).
Re-ran `normalize_ambient_audio.py` → re-imported all 5 SoundWaves →
re-ran `build_ambient_zones.py` (same order as Part A/B originally).
Verification: scratchpad `verify_ambient_split2.png`/`_stats.txt` (waterway
panel now a connected trace, before/after cell counts) and
`verify_ambient_seams3.png`/`.txt`/`_zoom.png` (3-column seam history table
+ zoomed loop-point plots for all 5 assets, town_hum/creek_babble detail).
No C++ changes; `Build.bat` not re-run (ODA2 layout unchanged, loader reads
whatever the sidecar says). Not pushed.

### 7.30 Arc 5.2: exhaust customization — lift-off pop/burble, presets, garage sliders, persistence (2026-09-08)

Implements the 5.2 spec (§5.2 above): lift-off pop/burble on the engine
synth, a per-preset "exhaust" struct, garage sliders beyond the Stock/
Sport/Open cycle, and persistence. Offline only, per the standing testing
protocol (no `-game`/PIE/screenshots — Python re-implementation of the
firing-skip schedule instead, mirroring the exact C++ constants).

**Pop/burble mechanism reuses 5.1v2's ignition-cut machinery, not a
parallel one**, per the task brief. `UODEngineSynth::OnGenerateAudio`
(`ODEngineSynth.cpp`) already skips firings outright and arms a
`ResumeBoost`/`NoiseBurst` relight bang (1.9x amplitude + ~8ms noise tail)
when a real limiter/shift ignition cut ends. The pop mechanism is the same
branch, gated differently: a raw (unsmoothed) throttle-target transition
from above 0.35 to below 0.15 with rpm above 2200 and no active real cut
(`PopLiftHighThrottle`/`PopLiftLowThrottle`/`PopMinRpm`, `ODEngineSynth.h`)
re-arms a `PopWindowRemainSec` countdown; every firing while that window is
open rolls against a decaying probability `PopProbability * (remaining/
window)^PopDecay` (reusing the jitter RNG draw already made that firing,
no extra RNG call) and, on a hit, skips that firing and arms
`ResumeBoost = 1` — so the very next real firing gets the identical
relight bang a limiter cut would give it. That bang landing on an
otherwise-normal firing IS the audible pop; no separate crackle/bang code
exists.

**`FODExhaustConfig`** (`ODEngineSynth.h`) gathers Loudness/Rasp/
MufflerCutoff (already existed) plus the three new pop params
(PopProbability/PopWindowSec/PopDecay) into one struct, with
`SetExhaustConfig()`/`GetExhaustConfig()` on the synth. Checked
`UODVehicleDef` first — it carries no audio fields (engine character
already lives on the menu's own `Engines[]` table, not the def) — so this
is preset-level, not per-car-def; noted rather than expanding scope, and
`UODVehicleDef`/`cars_manifest.json` were never touched (rule 6, audio/UI/
persistence only).

**Presets** (`ODMainMenu.cpp`'s `ExhaustPresets[]`, same table-of-defs
idiom as the pre-existing `Engines[]`/`Layouts[]`): Stock (Loudness 0.65,
Rasp 0.35, Muffler 2200Hz, Pop 0.02/0.25s/2.0 — near-silent overrun), Sport
(0.80/0.55/3200Hz, Pop 0.35/0.55s/1.5 — occasional crackle), Open
(0.95/0.75/4600Hz, Pop 0.75/1.10s/1.1 — dense crackle). All six params
climb together per preset so each reads as one coherent character rather
than "the same pipe with pops turned up".

**Garage sliders**: `AODMainMenu`'s `NumRows()` went 6→11, same canvas-
drawn, no-UMG, arrow-key value-cycling idiom as every existing row (`ODRaceHUD::
DrawMainMenu` needed zero changes — it already loops generically over
`NumRows()`/`GetRow()`). New rows: "Exhaust" (cycles Stock/Sport/Open,
reseeding all four slider fields from the picked preset's baseline), then
"  Loudness"/"  Rasp"/"  Muffler"/"  Pop" sub-rows adjustable independently
of the preset (±0.05/±0.05/±200Hz/±0.05, clamped to the synth's own
ClampMin/Max). `PopWindowSec`/`PopDecay` stay preset-only tuning constants
— same tier as `LimiterBounceHz`/`CutRpmSag`, not garage-exposed — matching
which params were already precedented as player-tunable (Loudness/Rasp/
MufflerCutoff) plus the one new one the spec calls out (pop rate/
probability). All `Selected == 5`/`Selected = 5` literals for the "Drive"
row were changed to `NumRows() - 1` so the row-count change didn't leave a
stale magic number.

**Persistence**: checked first and found Engine/Drivetrain/Tuning do NOT
persist today (no GConfig/SaveGame calls anywhere in `AODTuningManager` or
the menu's own Engine/Drivetrain index fields) — only Input Device did, via
`AODVehiclePawn::Save/LoadInputDeviceMode` (GConfig into
`GameUserSettings.ini`; no SaveGame object exists anywhere in this
project). Per the task's own scoping rule, exhaust now persists through
that SAME mechanism (`AODMainMenu::Save/LoadExhaustConfig`, section
`"OpenDrive.Exhaust"`) rather than introducing a second persistence system,
and Engine/Drivetrain/Tuning's non-persistence remains a known, untouched
gap. `LoadExhaustConfig()` runs from `BeginPlay()` before the menu ever
paints; `SaveExhaustConfig()` runs after every preset pick or slider nudge,
flushed immediately.

`Build.bat` succeeded (`ODEngineSynth.cpp`, `ODMainMenu.cpp` recompiled
clean, link OK; no stray `UnrealEditor` process found before building).

**Verification (offline):** scratchpad `verify_exhaust_pops.png` — an
independent Python re-implementation of the firing-skip schedule (same
LCG constants/thresholds/decay formula, file/line-cited in the script),
simulating a V8 at 5000rpm lifting off throttle, for all three presets:
firing/skip timelines (Stock: 1 skip in the whole window; Sport: scattered;
Open: dense) plus the theoretical vs. empirical pop-rate decay curve.
Numeric sanity checks (also in `verify_exhaust_pops_sanity.txt`): Stock
in-window skip fraction 1.2% (<10% gate), Open 64.3% (>30% gate, >3x
Stock) — all pass. `verify_exhaust_stats.txt` has the full parameter table,
every field's file/line, and the persistence read/write-path inspection
(code-path inspection, not an executed round trip — no SaveGame/commandlet
exists to drive this without a disallowed game launch).

**What's Taylor's to judge live (not verified here, by design):**
subjective crackle character (pop vs. glitchy dropout — same caveat as
5.1v2's rev-limiter work); slider step-size feel from the keyboard/gamepad;
mix level of the pop against the base pulse-train/road noise; whether
PopWindowSec/PopDecay's specific per-preset values feel right (re-tunable
later exactly like LimiterBounceHz/CutRpmSag were, no code-shape change
needed); and whether 11 rows still reads well on the garage panel visually
(the panel height already scales with `NumRows()` so nothing overflows
structurally, but crowding is a look, not a number).

Committed: `OpenDriveUE/Source/OpenDriveUE/ODEngineSynth.h/.cpp` (pop
mechanism + `FODExhaustConfig`), `ODMainMenu.h/.cpp` (presets, sliders,
persistence), this note. Not pushed.

**Correction addendum (2026-09-08): the "reuses the jitter RNG draw" line
above described a real defect, not just an implementation choice.** A
reviewer reading `verify_exhaust_pops.png` flagged that Open's simulated
lift-off showed a solid ~0.45s *all-skip* block (zero relight bangs) right
after the throttle lift, while the empirical rolling skip fraction pegged
at 1.0 even though theoretical `EffP(t)` started at 0.75 — a ~25% survival
rate should have produced scattered relight bangs from the first instant,
not total silence. Diagnosed against the actual `ODEngineSynth.cpp`, not
just the mirror:

- **Root cause was a divisor bug, not RNG serial correlation** (the
  reviewer's working hypothesis). The per-firing `R1`/`R2` draws
  (`ODEngineSynth.cpp`, then ~178-180) computed
  `(NoiseSeed >> 9) / 8388608.0f - 1.0f`. `NoiseSeed` is a `uint32`; `>>9`
  leaves a 23-bit value in `[0, 2^23-1]`. Dividing that by `8388608.0f`
  (`2^23`, the *width* of the shifted range) instead of `4194304.0f`
  (`2^22`, *half* the width — what the file's own `Noise` line a few dozen
  lines down already did correctly with its `(int32)` cast) silently
  confined `R1`/`R2` to `[-1, 0)` — never positive, despite the `// [-1,1)`
  comment on the line. Confirmed empirically (Python, 20000-draw stream):
  100% of draws were negative, mean ≈ -0.50, exactly the `[-1,0)`-not-
  `[-1,1)` signature. Since the pop path used `PopRoll = (R1+1)*0.5`, the
  roll was uniform on `[0, 0.5)`, not `[0,1)`: any `EffP >= 0.5` (Open
  starts at `PopProbability=0.75`) skipped **every** firing with certainty
  (100%, not 75%) until the decaying `EffP` fell under 0.5. That's the
  solid deterministic block the reviewer saw, and why it carried zero
  relight bangs (skips never fire, so never carry the boosted-firing bang;
  the bang lands on the first *real* firing after, which the whole point of
  the bug was to delay).
- **Fix, two parts, both landed:**
  1. Corrected the `R1`/`R2` divisor to `4194304.0f` (restores the
     documented `[-1,1)` range for `AmpJit`/`JitterAngle` too, which were
     silently biased the same way).
  2. The pop roll now draws its **own** independent LCG advance (`R3`,
     `ODEngineSynth.cpp` ~209-211) instead of reusing `R1` — even with
     `R1`'s range now correct, sharing one draw across two unrelated
     consumers (`AmpJit` and the pop roll) was fragile: a future change to
     either one would silently bias the other. `R3` uses the same
     corrected `4194304.0f` mapping.
  3. Defense in depth per the task brief: `MaxConsecutivePopSkips = 3`
     (new design constant, `ODEngineSynth.h`, same tier as `PopMinRpm`) and
     `ConsecutivePopSkips` (new audio-thread state, `ODEngineSynth.cpp`)
     hard-cap the pop mechanism to at most 3 skipped firings in a row —
     after that the next firing always fires, with its relight bang,
     regardless of `PopProbability`/`PopDecay`/RNG behavior. Resets to 0 on
     any firing that actually sounds; a real ignition cut (`bCutNow`)
     doesn't touch it, since that's a separate deliberate-silence
     mechanism, not the pop path.
- **Preset table, `FODExhaustConfig`, garage menu, and persistence are all
  unchanged** — this was a firing-loop bug, not a tuning or UI issue.
- `Build.bat` succeeded post-fix (`ODEngineSynth.cpp` recompiled clean,
  link OK).
- **Verification (offline):** `verify_exhaust_pops2.png` +
  `verify_exhaust_pops2_sanity.txt` (scratchpad) — same three-preset
  lift-off timeline/decay-curve layout as before, re-run against the fixed
  mirror, plus four numeric acceptance gates, all passing: (1) Open's
  25-firing rolling skip fraction now peaks at 0.654 in-window (gate
  `<=0.92`, was pegged at 1.0) and its first relight bang lands 9ms after
  the lift (gate `<=0.1s`); (2) worst max-consecutive-skip run over 250
  simulated lifts per preset, different RNG states each: Stock 1, Sport 3,
  Open 3 — all `<= MaxConsecutivePopSkips` (3); (3) Stock in-window skip
  1.2% (`<10%`), Open 35.4% (`>30%`) — both still hold; (4) empirical mean
  skip fraction (150-trial average per preset) tracks theoretical `EffP(t)`
  within 0.0044/0.0173/0.0354 absolute (Stock/Sport/Open respectively,
  gate `<=0.12`).

### 7.31 Arc 4.4: release settings shell — graphics/audio/input settings + generated credits (2026-09-08)

Implements §4.4: a canvas-drawn Settings screen (resolution/window mode/
quality/upscaler/screen %/vsync/volumes/input device) and a Credits screen
(full data-attribution list), reachable from both the in-game pause menu and
the front-end car-config menu. No UMG anywhere, per the existing AODMainMenu/
AODPauseMenu/ODRaceHUD architecture. Offline only, per the standing testing
protocol (no `-game`/PIE/screenshots — code-path inspection + a green
`Build.bat` run, no engine launch).

**Two new plain logic/state actors, `AODSettingsMenu`/`AODCreditsMenu`
(`ODSettingsMenu.h/.cpp`, `ODCreditsMenu.h/.cpp`), deliberately NOT
independent input-owning actors.** A second actor binding its own Up/Down/
Left/Right/Enter/Escape would race `AODPauseMenu`'s/`AODMainMenu`'s existing
bindings for input-stack priority with no reliable ordering guarantee — so
instead these are driven entirely by whichever parent menu currently has
them open: both parents check a `bSettingsOpen`/`bCreditsOpen` flag FIRST, at
the top of their own already-bound `Up()/Down()/Left()/Right()/Activate()/
Toggle()`-or-`Back()`, and route straight into
`MoveSelection()/AdjustSelected()`/`Scroll()` instead of running their normal
row logic — one input-binding stack in the game at any time, never two.
Lazily spawned (`EnsureSettingsMenu()`/`EnsureCreditsMenu()` in both
`ODPauseMenu.cpp` and `ODMainMenu.cpp`), same lazy-spawn-on-first-use idiom
as `AODMainMenu::EnsureGarageEnvironment()` (falls back to a
`TActorIterator` scan first, so both parent menus share one instance if both
happen to exist on a level).

**Reachable from both menus, new rows inserted before each terminal action
row:** `AODPauseMenu` rows 4 "Settings"/5 "Credits" (`NumRows()` 6→8, Main
Menu/Quit shifted 4/5→6/7); `AODMainMenu` (car-config) rows 10 "Settings"/11
"Credits" (`NumRows()` 11→13, "Drive"/"Resume Driving" shifted 10→12).
Drawing is generic over whichever parent has them open —
`ODRaceHUD::DrawSettingsMenu(AODSettingsMenu*)`/`DrawCreditsMenu
(AODCreditsMenu*)` are two new functions, each called from both of
`DrawHUD()`'s existing menu blocks (front-end and pause-menu) rather than
duplicated per parent.

**Settings row list (`AODSettingsMenu::NumRows() == 11`):** Resolution
(cycles 5 fixed 16:9 modes + a synthesized "Native" entry read live via
`FDisplayMetrics::RebuildDisplayMetrics()`), Window Mode (Fullscreen/
Windowed/Borderless), Quality (Low/Medium/High/Epic via
`UGameUserSettings::SetOverallScalabilityLevel`), Upscaler, Screen %, VSync,
Master/Vehicle/Ambient Volume, Input Device (reuses
`AODVehiclePawn::SetInputDeviceMode` directly rather than reimplementing it —
that row already self-persists on the pawn), Close. Resolution/Window Mode/
Quality/VSync are deliberately NOT shadow-copied into `AODSettingsMenu`
fields — they're read LIVE from `UGameUserSettings::GetGameUserSettings()`
on every draw call, so there's exactly one source of truth and no
state-sync bug is possible; only Upscaler/ScreenPercent/the three volumes
get their own fields, since `UGameUserSettings` has no native equivalent for
any of those four.

**Upscaler finding (key decision):** no FSR/FidelityFX plugin is enabled in
this project (checked `.uproject` and `Config/` — no match on FSR/
FidelityFX/Upscal). Per the task brief's explicit fallback, the row offers
exactly two options: Off (plain TAA) and TSR (built into UE 5.8 core, no
plugin needed), driven via the `r.AntiAliasingMethod` console variable
(`IConsoleManager::FindConsoleVariable`) since `UGameUserSettings` has no
scalability-group equivalent for AA method specifically. Persisted manually
through GConfig alongside Screen % (see persistence below), since the engine
doesn't auto-save cvars.

**Audio volume routing — single-application-point requirement, verified by
grep against every `OnGenerateAudio`/`SetVolumeMultiplier` write site in the
project (exactly three matches, all covered):**
`AODSettingsMenu::ApplyAudioVolumes()` pushes `MasterVolume*VehicleVolume`
into `AODVehicleAudio::SetVehicleVolumeScale()` (new method — caches the
scale and forwards it to both `UODEngineSynth`/`UODRoadNoiseSynth`'s new
`SetVolumeScale()`, re-applied in `EnsureSynths()` so a car swap mid-session
doesn't reset it) and `MasterVolume*AmbientVolume` into
`AODAmbientSoundManager::SetVolumeScale()` (new method). Each synth's
`OnGenerateAudio()` output line gained exactly one extra `* VolumeScale`
multiply (`ODEngineSynth.cpp`: `OutAudio[i] = LpState * Amp * VolumeScale;`;
`ODRoadNoiseSynth.cpp`: `... * Volume * VolumeScale;`); `Tick()`'s existing
per-layer loop in `AODAmbientSoundManager.cpp` gained the same on its one
`SetVolumeMultiplier()` call (`L->CurrentVolume * VolumeScale`) — no new
loop, no per-frame cost beyond the float multiply, `ApplyAudioVolumes()`
itself only runs on load and on a slider nudge, never per-tick. All four
scale setters clamp to `[0,1]` inline.

**Persistence:** Resolution/Window Mode/Quality/VSync persist through
`UGameUserSettings`'s own native path (`SaveSettings()`/
`ApplyResolutionSettings(false)`), no code here duplicates that. Upscaler +
Screen % round-trip through GConfig section `"OpenDrive.Graphics"`; the
three audio volumes round-trip through `"OpenDrive.Audio"` — both new
sections, same `SetInt/GetInt`/`SetFloat/GetFloat` + `GConfig->Flush(false,
GGameUserSettingsIni)` idiom as `AODMainMenu::Save/LoadExhaustConfig`'s
`"OpenDrive.Exhaust"` (§7.30). Both save on every adjustment, not just on
close, matching that precedent.

**Credits screen (`pipeline/build_credits.py`, new script) — GENERATED, not
hand-typed.** Walks every `content_src/**/manifest.json` (both license-record
schemas found in the repo: array-of-objects with `display_name`/`author`/
`source_url`/`license`, and the flat filename-keyed dict `content_src/
textures/flora/manifest.json` uses) plus `data/cars/*/LICENSE.txt`, and
prepends a small hand-maintained header block (title, "by Taylor Markham",
Unreal Engine trademark notice) plus OSM ODbL and USGS/DEM attribution lines
matching README.md's existing wording. `cars_manifest.json` (`data/cars/
procedural/obj/cars_manifest.json`) is explicitly excluded by name in the
script's own docstring, per this task's hard rule against touching vehicle
physics/manifest data. Output is one plain UTF-8 text sidecar,
`OpenDriveUE/Content/OpenDrive/Masks/credits.txt` (163 lines, 5610 bytes) —
plain text rather than one of the project's packed binary sidecar formats,
since this file is machine-regenerated on every pipeline run, not
hand-authored, so packing buys nothing. `AODCreditsMenu::BeginPlay()` loads
it via `FFileHelper::LoadFileToStringArray` from the same `Content/
OpenDrive/Masks/` directory every other sidecar uses.

**Coverage: 30/30 entries (100%)** — 6 manifest.json files (ambient audio 5,
building textures 6, flora textures 4, ground textures 6, road textures 5,
water textures 1) + 3 vehicle-placeholder LICENSE.txt files (gt/
placeholder/procedural, 1 entry each), every one present in the generated
output. Re-running the script against an unchanged repo reproduces the
identical byte count, confirming the generator is deterministic.

**DEM/imagery attribution finding:** USGS 3DEP (via AWS Terrain Tiles,
Terrarium format — confirmed from `pipeline/terrain_common.py`'s own module
docstring and its `TERRAIN_URL` constant) and USGS imagery are U.S.
Government works and public domain in the United States — unlike OSM's
ODbL, there is no attribution *requirement* documented anywhere in this repo
for the DEM/imagery beyond README's existing wording, which credits.txt
mirrors exactly ("public domain" rather than inventing ODbL-style license
terms for it). Flagging this as-read rather than asserting a requirement
that isn't documented: if the AWS Open Data `elevation-tiles-prod` bucket
carries additional terms beyond USGS's own public-domain status, that isn't
captured anywhere in this repo and isn't reflected in credits.txt.

**Build:** `Build.bat OpenDriveUEEditor Win64 Development` succeeded after
three fixes surfaced during the process (none design-level, all mechanical):
(1) `ODCreditsMenu.h`'s doc comment contained a literal `**/` (inside
`content_src/**/manifest.json`) that closed the `/** */` block comment
early, leaving the rest — including an apostrophe a few lines down — parsed
as raw code (`Unterminated character constant`); reworded to avoid the
literal sequence. (2) `FDisplayMetrics::GetDisplayMetrics` isn't a real API
in UE 5.8 (compile error) — switched to the actual API,
`FDisplayMetrics::RebuildDisplayMetrics()`. (3) That call is exported from
the `ApplicationCore` module, which `OpenDriveUE.Build.cs` didn't depend on
(link error) — added `"ApplicationCore"` to `PublicDependencyModuleNames`.
Also renamed two local `r.AntiAliasingMethod`-value constants
(`AAM_TemporalAA`/`AAM_TSR` → `CvarAA_TemporalAA`/`CvarAA_TSR`) after the
build flagged them as ambiguous against the engine's own
`EAntiAliasingMethod` enumerators of the same names, pulled in transitively
via `Engine`/`SceneUtils.h`.

**Offline verification:** `scratchpad/verify_settings_shell.txt` (row/screen
inventory with file/line cites for every row, the three audio-volume
routing points, persistence write/read paths, the upscaler-availability
finding) and `scratchpad/verify_credits.txt` (full generated credits.txt
content verbatim, the 30/30 coverage table, OSM/DEM/engine attribution line
checks) — both in this session's scratchpad, not committed (offline
working notes, not shipping artifacts).

**Caveats — Taylor's live calls, not machine-verifiable offline (hard rule
3 forbids launching the game/editor to check these):**
- Layout/readability of the Settings and Credits panels at actual screen
  resolutions — row spacing, whether long values (e.g. "Native
  (3840x2160)") overflow the panel width — is unverified; the panel sizing
  math scales with `NumRows()`/`VisibleLines` the same way the existing
  garage panel does, but "does it look right" needs eyes on it.
- Credits scroll feel (`AODCreditsMenu::Scroll()`'s clamp math is verified
  correct by inspection — never scrolls past the last page — but pacing/
  feel of Up/Down scrolling through 163 lines wasn't tested live).
- Slider step sizes (10-unit screen-% steps, 5% volume steps, the five
  fixed resolution entries) are reasonable-looking choices, not validated
  against how they feel to actually turn.
- Whether the four quality presets (Low/Medium/High/Epic) visibly differ on
  this project's content: the row correctly reads/writes
  `UGameUserSettings::GetOverallScalabilityLevel()`, the correct existing
  engine API, but whether the buckets look meaningfully different depends
  on this project's `Scalability.ini` (or the engine's own defaults if
  unset) — not something readable from static code.
- Whether TSR vs Off is visually distinguishable at the offered
  screen-percentage range, on this project's content, is untested live.

Committed: `OpenDriveUE/Source/OpenDriveUE/ODSettingsMenu.h/.cpp`,
`ODCreditsMenu.h/.cpp` (new), `ODPauseMenu.h/.cpp`, `ODMainMenu.h/.cpp`,
`ODRaceHUD.h/.cpp` (routing/drawing), `ODVehicleAudio.h/.cpp`,
`ODEngineSynth.h/.cpp`, `ODRoadNoiseSynth.h/.cpp`,
`ODAmbientSoundManager.h/.cpp` (volume-scale plumbing),
`OpenDriveUE.Build.cs` (`ApplicationCore` dependency),
`pipeline/build_credits.py` (new), `OpenDriveUE/Content/OpenDrive/Masks/
credits.txt` (generated), this note. Not pushed.

### 7.32 Arc 4.1: control rebinding — indirection layer over legacy input, deadzone/sensitivity sliders (2026-09-08)

Implements §4.1: rebindable driving controls (keyboard + gamepad), a
canvas-drawn rebinding screen off Settings (no UMG), and deadzone/
sensitivity sliders applied where analog input enters the pawn — the exact
point a future Arc 6 wheel axis would also plug into. Offline only, per the
standing testing protocol (no `-game`/PIE/screenshots — code-path
inspection + a green `Build.bat` run, no engine launch); gamepad hardware
capture is therefore unverified live (see caveats).

**Migration decision: NOT a real Enhanced Input migration.** `Config/
DefaultInput.ini` sets `DefaultPlayerInputClass=EnhancedPlayerInput` and
`OpenDriveUE.Build.cs` depends on the `EnhancedInput` module, but neither
was ever actually load-bearing — a full-project grep found zero
`UInputAction`/`UInputMappingContext` assets or IMC-based binding anywhere;
every bind site (`AODVehiclePawn::SetupPlayerInputComponent`/
`SetupGamepadAndWheelInput`, `AODPauseMenu::BeginPlay`,
`AODRaceHUD::BeginPlay`, `AODRaceManager::BeginPlay`,
`AODTuningManager::BeginPlay`, `AODMainMenu`) uses
`UInputComponent::BindKey`/`BindAxisKey` directly against hardcoded `FKey`
literals. A real migration means adding mapping-context data assets and
IMC-based binding at all seven of those sites for a keyboard+gamepad-only
game with hand-drawn canvas menus and no chorded/contextual input need —
high churn, no behavior change, and against this project's stated
no-framework preference (canvas over UMG, C++ over MetaSounds — see
`ODVehiclePawn.h`'s own comments on why mouse-look is polled rather than
bound). Per the task brief's explicit allowance, rebinding is instead a
project-level indirection layer, `FODInputConfig` (new
`ODInputConfig.h/.cpp`, pure static data, no UObject/actor): every bind
site asks it for the current `FKey` instead of using a literal, and each
affected actor gained a small `RefreshODManagedKeyBindings()`/
`RefreshPauseKeyBinding()` method that surgically removes+rebuilds just its
own tracked keys from `InputComponent->KeyBindings` (`RemoveAll` matching
`B.Chord.Key`, confirmed public via an engine-source grep) — called at
`BeginPlay` and again live by the rebind screen after any assignment, so a
rebind takes effect immediately without a restart.

**Action inventory — 10 rebindable actions** (`EODInputAction`,
`ODInputConfig.h:44-57`), defaults in `ActionTable[]`
(`ODInputConfig.cpp:15-25`), consumed at:
`Throttle`/`Brake`/`SteerLeft`/`SteerRight`/`Handbrake`/`ResetVehicle`
(`ODVehiclePawn.cpp:888-893`, keyboard defaults W/S/A/D/SpaceBar/R;
Handbrake also has gamepad default `Gamepad_FaceButton_Bottom`),
`Pause` (`ODPauseMenu.cpp:126`, default P), `ToggleMap`/`ZoomMapOut`/
`ZoomMapIn` (`ODRaceHUD.cpp:59-61`, defaults M/`[`/`]`). Gamepad
Steer/Throttle/Brake are the left-stick-X and trigger axes — fixed analog
inputs, not meaningfully "rebindable" the way a button is; what's
newly configurable there is the deadzone/sensitivity conditioning (below),
which is the concrete form of "rebinding" an analog axis actually takes.
Explicitly out of scope: **V** (dev-facing `CycleVehicle` hotkey, hardcoded
outside `FODInputConfig` and excluded from the rebind screen's capture
candidate list so it can never be silently double-bound); **Camera** (no
discrete camera key exists anywhere in the codebase today — mouse-look is
polled every `Tick`, not bound — nothing to rebind); **Gear up/down**
(project is automatic-transmission only, no manual-shift input path
exists); `AODRaceManager`'s own `R` (`RespawnAtLastGate`) and
`AODTuningManager`'s `T` (cycle tuning preset) binds, both independent
per-actor binds outside the pawn's input path, left untouched.

**Rebind screen (`AODRebindMenu`, new `ODRebindMenu.h/.cpp`), reached via a
new "Controls" row on `AODSettingsMenu` (row 10, `ControlsRowIndex()`).**
Same "driven by whichever parent has it open" idiom as
`AODSettingsMenu`/`AODCreditsMenu` (§7.31), nested one level deeper:
`AODSettingsMenu::MoveSelection`/`AdjustSelected` check their own
`bRebindOpen` flag and forward straight into `AODRebindMenu`, so
`AODPauseMenu`/`AODMainMenu`'s existing blind Up/Down/Left/Right
forwarding needed no changes; `Activate()`/`Toggle()`-or-`Back()` each
needed one new branch in both parents (mirroring their existing "Close
row" special case), since Enter/Escape can't be blindly forwarded the same
way. 25 rows: 10 actions × 2 device slots (keyboard/gamepad) + 3 sliders +
Reset-to-Defaults + Back, drawn by `ODRaceHUD::DrawRebindMenu` (new
function, same bordered-panel/scroll-window pattern as
`DrawSettingsMenu`/`DrawCreditsMenu`).

**Capture-mode state machine — the one exception to "driven by the
parent."** Selecting a binding row calls `AODRebindMenu::BeginCapture`,
which uses `AActor::EnableInput` to push a fresh, real `InputComponent` to
the TOP of the `PlayerController`'s input stack (highest priority),
`bConsumeInput=true`, bound to a broad fixed candidate-key list
(`KeyboardCandidates[]`/`GamepadCandidates[]`, deliberately excluding
Escape/Up/Down/Left/Right/Enter — the menu's own nav keys — and V) plus a
separate always-consuming Escape bind that cancels. `BindKey`'s
`FKey`-parameter handler signature (`OnCaptureKey(FKey)`) identifies which
key fired without per-key lambdas. On a valid press:
`FODInputConfig::AssignKeyboardKey`/`AssignGamepadKey`, then
`RefreshAllLiveInputBindings` (pushes the change to the live pawn/pause-
menu/race-HUD instances via `TActorIterator`, same idiom as
`AODSettingsMenu::ApplyAudioVolumes`), then `EndCapture`
(`DisableInput`, pops the stack). `MoveSelection`/`AdjustSelected`/
`ActivateSelected` all no-op while capturing.

**Conflict policy: SWAP**, not clear-and-warn.
`FODInputConfig::AssignKeyboardKey`/`AssignGamepadKey` (`ODInputConfig.cpp`)
give a displaced action the key being bumped, so two actions can never
point at the same physical key in the same device slot and nothing is ever
left in a stuck unbound state needing a warning UI. Rejected clear-and-warn
as worse UX for this game's small, mostly disjoint action set with no
failure mode to display.

**Deadzone/sensitivity sliders (feeds Arc 6 wheel).** Three persisted
values in `FODInputConfig`: `SteeringDeadzone` (default 0.15),
`SteeringSensitivity` (default 2.0, response exponent),
`ThrottleBrakeDeadzone` (default 0.15). Applied via
`FODInputConfig::ConditionSteering`/`ConditionThrottleBrake`
(`ODInputConfig.cpp`) inside `AODVehiclePawn::GamepadSteerAxis`/
`GamepadThrottleAxis`/`GamepadBrakeAxis` — the exact point raw
gamepad-stick/trigger values enter the pawn's input path, upstream of
`ODVehicleMovement`/vehicle physics (never touched, per the hard
constraint against altering what actions DO). At the defaults,
`ConditionSteering` reproduces the old hardcoded gamepad-steer curve
bit-for-bit (deadzone-clamp + `x*|x|`, now `Pow(Rescaled, 2.0)` with
`Deadzone=0.15`) — a like-for-like refactor at default settings, not a
feel change. `ConditionThrottleBrake` fixes one real behavior gap versus
the old code: the old throttle/brake deadzone was a hard cutoff with no
rescale (value jumped straight from 0 to 0.15 the instant the deadzone
cleared); the new conditioning rescales continuously across the deadzone
edge (no jump), matching the task brief's explicit ask. Proven numerically
in `scratchpad/verify_input_curves.png` (matplotlib plot of both transfer
functions at 5 steering settings / 3 throttle-brake settings, old-curve
overlay for comparison) and `scratchpad/verify_input_curves_sanity.txt`
(all gates pass: f(0)=0, f(x)=0 for |x|≤deadzone, continuity at the
deadzone edge via shrinking-offset convergence, f(1)=1, monotonic
non-decreasing on [0,1], and exact equivalence to the old hardcoded
gamepad-steer curve at default settings).

**Persistence:** GConfig section `"OpenDrive.Input"` in
`GameUserSettings.ini`, same `SetInt/GetInt`/`SetFloat/GetFloat` +
`GConfig->Flush(false, GGameUserSettingsIni)` idiom as
`AODMainMenu::Save/LoadExhaustConfig`'s `"OpenDrive.Exhaust"` (§7.30) and
`AODSettingsMenu`'s `"OpenDrive.Graphics"`/`"OpenDrive.Audio"` (§7.31) —
not the `SaveGame` originally suggested in §4.1's spec, deliberately, to
stay consistent with this project's one existing persistence mechanism
rather than introduce a second. Saves on every assignment/adjustment, same
"save on every adjustment" idiom as §7.31. `FODInputConfig::Load()` seeds
every binding/slider from its default BEFORE attempting the GConfig read
and only overwrites on a successful, valid-`FKey`-parsing read, so a
missing `"OpenDrive.Input"` section (fresh install, or an ini predating
this change) reproduces today's defaults exactly — same guarantee as
§7.30/§7.31.

**QA-hook / menu-key non-interference — verified by grep, not by
launching the game:** the `-odrev`/`-odlift`/`-odsteer`/`-odbrake`/`-odveh`/
`-odsetvat`/`-odsetv`/`-odvehidx`/`-odcar` hooks (`ODVehiclePawn.cpp:452-
472` and their consuming Tick-time reads) all go through
`FParse::Value(FCommandLine::Get(), ...)` directly into pawn member floats
— entirely independent of `UInputComponent`/`FODInputConfig`, confirmed by
grep finding zero references to either in that parsing code.
`-odmenu`/`-odmenumap`/`-odpick` drive `AODPauseMenu`/`AODMainMenu`'s
`Toggle()`/`OpenMenu()`/`StartAt()` functions directly via `FTSTicker`,
never through `InputComponent` — those function bodies were not changed by
this task, only which physical key *calls* them (`Pause`'s key is now
config-driven; `AODPauseMenu`'s Escape/Up/Down/Left/Right/Enter navigation
binds and `AODMainMenu`'s own nav-key binds were not touched at all).
Full grep evidence and per-hook citations in
`scratchpad/verify_rebinding.txt` §8-9.

**Build:** `Build.bat OpenDriveUEEditor Win64 Development` succeeded on the
first attempt (all fixes below made pre-emptively, before ever building):
plain `enum class EODInputAction` instead of `UENUM(BlueprintType)` (
`ODInputConfig.h` has no `.generated.h` — not a UCLASS/USTRUCT file, so UHT
processing would have failed); `EKeys::Gamepad_LeftTrigger`/
`Gamepad_RightTrigger` (the actual UE 5.8 digital-trigger `FKey` names,
confirmed via engine-header grep) rather than the nonexistent
`*TriggerThreshold` names first guessed for `GamepadCandidates[]`.

**Offline verification:** `scratchpad/verify_rebinding.txt` (input-path
findings, migration-decision reasoning, full action inventory with
file/line cites, capture-mode state machine, conflict policy, persistence
round-trip paths, QA-hook non-interference evidence),
`scratchpad/verify_input_curves.png` + `verify_input_curves_sanity.txt`
(transfer-function plots + numeric gates, described above) — all in this
session's scratchpad, not committed (offline working notes, not shipping
artifacts).

**Caveats — Taylor's live calls, not machine-verifiable offline:**
- Gamepad button/axis capture cannot be hardware-tested in this
  environment (no controller attached). The capture code path for a
  gamepad key is structurally identical to the keyboard path (same
  `OnCaptureKey(FKey)` handler, same device-class guard via the engine's
  own `FKey::IsGamepadKey()`), but an actual button press through
  `BeginCapture`'s gamepad candidate list was never exercised live.
- Slider step sizes (0.05 deadzone step, 0.25 sensitivity step) and the
  resulting steering/throttle FEEL at non-default settings — only the
  underlying math is verified here (continuity, monotonicity, default-
  equivalence to the old curve); how it feels to actually turn the sliders
  and drive is Taylor's call, per the standing "run the build live" rule
  for input/audio tuning.
- Whether the 25-row rebind panel's scroll window reads well at real
  screen resolutions — row spacing follows the same
  `NumRows()*RowHeight+padding` formula as the proven-working
  `AODCreditsMenu` panel, but "does it look right" needs eyes on it.

Committed: `OpenDriveUE/Source/OpenDriveUE/ODInputConfig.h/.cpp`,
`ODRebindMenu.h/.cpp` (both new), `ODVehiclePawn.h/.cpp`,
`ODPauseMenu.h/.cpp`, `ODRaceHUD.h/.cpp`, `ODSettingsMenu.h/.cpp`,
`ODMainMenu.cpp` (routing/live-refresh plumbing), this note. Not pushed.

### 7.33 Embankment/bridge station-data audit: residual reconcile_junction_elevations() corruption after §7.20 - two findings, both left for Taylor's decision (2026-09-08)

**Task:** §7.20 fixed the two false-pair-match classes that inflated
"bridge" 76.7% (57,222 -> 13,723 stations), but explicitly left the
surviving embankment/bridge population unverified at smaller magnitude -
"pairs inside the new 20m radius or within 5 edge stations could still be
false... hiking or sinking elevations by a few meters." This audit
(ANALYSIS-FIRST, offline only, no `-game` launch) closes that caveat.

**Method:** rebuilt `road_stations.json` end-to-end via `build_road_
stations.py`'s own `main()` (output redirected to a scratchpad dir, never
touching the committed `data/` tree), with a scratchpad-local monkeypatch
capturing the exact PRE-reconciliation arrays passed into `reconcile_
junction_elevations()` - i.e. what the un-reconciled elevation would be.
Re-derived the function's own in-scope/keep filtering against that
captured state at the CURRENT shipped parameters (`JUNCTION_RECONCILE_
RADIUS_M=20.0`, `JUNCTION_SAME_NAME_EDGE_STATIONS=5`) to enumerate every
pair that fires TODAY and the elevation delta it imposes. Separately
reran `build_road_embankments.py` fresh for the drop-population
cross-check and the §7.22/§7.23 regression gate. No pipeline files edited.

**Finding 1 (in scope - reconcile_junction_elevations residual gap,
confirmed real, not fixed here):** 295 pairs fire at the current
parameters (of 32,305 raw 20m-radius candidates, 274,644 stations
countywide) - 196 stations / 28 segments / ~14 named roads actually get
snapped. Of the 295: 242 (82%) are SAME-SEGMENT ("switchback-class")
pairs - the branch §7.20 left completely unrestricted beyond the radius
tightening, with no analog to `JUNCTION_SAME_NAME_EDGE_STATIONS`. Its own
docstring assumed this branch was "already correctly scoped by seg_idx
equality"; the data says otherwise: 0 of 295 firing pairs sit anywhere
near the only documented genuine same-spot precedent's distance range
(Lake Hughes, 1.1-2.4m) - 66% sit in the outermost 5m of the 20m radius,
right at the truncation boundary, and 88% touch no real graph-detected
junction at all. Imposed elevation-snap magnitude: median 4.60m, p95
7.77m, max 9.60m - NOT the "smaller-magnitude, hides under 7m" corruption
originally anticipated; 10% of firing pairs individually impose a
bridge-threshold-comparable (>=7m) change on their own. Worst offender:
Robbers Nest Drive seg347, two points 19.8m apart in plan view but 16
stations (~80m) apart along the road - a real ~10m elevation change over
an ordinary switchback climb, forcibly flattened together, artificially
raising the lower station's apparent height above terrain (same bias
direction as §7.20's original bug). Two of the worst-hit roads (Robbers
Nest Drive, County Road 4) are roads §7.22 independently already flagged
as unpaved/switchback-bench roads for a different reason (false bridge
sub-classification) - same physical road class, second independent false
signal. By contrast, the 4 firing pairs on "I 70;CO 9" (the Silverthorne
interchange) look genuine: smaller (2.8-3.4m), at a named ramp junction,
consistent with the fix's actual intent.
Full data: `scratchpad/verify_embank_audit.png` (suspect-pair county map,
imposed-error histogram, current drop-magnitude distribution, worst-pair
profile), `scratchpad/verify_embank_audit_stats.txt` (full pair inventory,
percentile tables, worst-offender lists, per-branch breakdown).
**Not fixed in this pass** - per this audit's own hard rule against
tuning thresholds on a hunch, and because the correct fix isn't a revert
to an established value the way §7.20's own two constants were: it needs
a NEW same-segment analog to `JUNCTION_SAME_NAME_EDGE_STATIONS` (e.g. a
station-index-gap restriction), which - like `REBOUND_WINDOW_M` in §7.22 -
would need empirical tuning against real switchback data, not a hunch.
**Decision for Taylor:** approve a same-segment restriction design (this
audit's data is enough to start from - see the stats file) and a follow-up
pass to implement + rebake, or accept the current ~14-road/9.6m-max
residual as tolerable given §7.20 already fixed the overwhelming majority
(145,742 -> 295 firing pairs) of the original bug.

**Finding 2 (adjacent, out of this audit's declared scope - §7.22/§7.23
boundary regression, unrelated root cause, also not fixed here):** the
audit's own deliverable #5 (cheap regression check against §7.22/§7.23's
documented numbers) does NOT hold on current data. Documented (2026-09-07):
23 genuine bridge runs / 1.775 km, water-crossing trigger firing 0 new
stations. Actual (this fresh bake, later the same day): 122 genuine runs /
2.69 km, water-crossing trigger firing on 485 stations (384 via the
UNCONDITIONAL both-edges-in-water case §7.23's own doc measured at zero).
Root cause, verified not guessed: §7.26/§7.27 (same day, AFTER §7.23) fed
river/stream waterway ribbon polygons into the SAME "lakes" list §7.23's
water-crossing test reads (`build_water_tiles.py`'s own docstring: "fed
into the SAME 'lakes' list"). §7.23's discriminator (both pavement edges
inside one polygon = unconditionally genuine, no clearance guard, no
paved/rebound gate) was calibrated only against real lake/reservoir
polygons, where that geometry genuinely implies open water. A narrow
river/stream ribbon is a different shape entirely - any ordinary road
crossing a named creek (culvert or real bridge, no distinction) can
trivially have both 9m-apart pavement edges land inside a several-meter
ribbon, no elevation gap required. §7.27's gap-cutting only protects
crossings that were NOT already reading as bridge at water-tile-bake time;
crossings that already were are deliberately left ribbon-spanning-straight-
across (so a real deck renders over open water) - which the now-
unconditional water trigger reads back as proof of a genuine crossing, in
a self-reinforcing loop across ordinary highway creek culverts (23
separate "Gerald R. Ford Memorial Highway"/I-70 runs alone). This is a
real, material issue but touches `build_water_tiles.py`'s ribbon-into-
lakes design and §7.23's own discriminator, neither of which this audit's
scope (reconcile_junction_elevations corruption) authorized changing.
**Decision for Taylor:** needs its own scoped pass (likely: the
unconditional both-edges-in-water case should require a minimum polygon
width/area, or ribbons should carry a distinct type from lake rings so the
water-crossing test can tell them apart) - not attempted here.

Verdict: NOT "no material residual corruption." Bound (Finding 1): 196
stations / 28 segments / ~14 named roads directly touched county-wide
(0.07% of 274,644 stations), imposed magnitude up to 9.6m, median 4.6m -
small in count (§7.20 fixed the overwhelming majority), not small in
per-station magnitude, mechanism not resolved, only narrowed to the
same-segment branch. Both findings are the audit brief's own "in-between"
case (real, evidenced mechanism gaps; the concrete fixes need threshold-
tuning judgment or scoped follow-up work, not a hunch) - documented here
per that brief's own instruction, no pipeline code changed by this audit.

Committed: this note only (`next steps.md`). Bakes/scratchpad analysis
outputs are gitignored/scratchpad-local, not committed, matching
§7.18-7.23 precedent. Not pushed.

---

### 7.35 Terrain variety V1/V2/V5 (2026-09-25)

Backend-only verification (standing no-run rule — nothing rendered). All
artifacts in `scratchpad/terrain_variety/`; pre-change sidecar + JSON kept in
`scratchpad/terrain_variety/before/`.

**V1 — off-corridor coverage.** `classify_biomes.py` adds a 300 m lattice over
the BACKGROUND bbox padded by one terrain tile (2 km), dropping lattice points
within 150 m of a corridor sample; the dense corridor samples are unchanged. The
grid points get elevation and grade from a z12 Terrarium DEM mosaic. Re-baked
summit_county: 27,465 corridor + 53,244 grid = 80,709 samples. The run took
about 50 s (120 DEM + 418 imagery tiles). `FarBiomeFallbackCm` is unchanged
because the grid makes it cover only tile-edge gaps.
- Two classifier fixes were needed:
  - **Wetland false positives on the grid.** Shaded north-facing conifer
    slopes matched the dark+blue water heuristic (21% of the grid). Wetland now
    also requires `grade < 0.10`, which brings the grid down to 643 Wetland.
  - **Imagery row sampling.** Rows used to be linear in latitude, which put
    them 60–80 m off, with the error depending on the mosaic bbox. They are now
    exact web-mercator rows (0.000 px error). **Side effect:** 3,874 of 27,465
    corridor samples change biome vs the 09-06 bake, and forest_weight shifts by
    a mean |Δ| of 0.165. This is a correctness fix, but corridor forest density
    and tint will move a little. See `diag_wetland.py/.log`.
- Tint replica (`verify_v1_v2_tint.py/.log`) of `ApplyBiomeTint` over all
  1,221 real terrain tiles:
  - Tiles with any biome: 330 → 1,221.
  - Default/no-data vertices: 30.3% → 0.0%.
  - Background tiles (898): default vertices 99.8% → 0.1%; in-tile luma std
    0.0001 → 0.051; across-tile luma std 0.0012 → 0.036.
  - Plots: `biome_map_before_after.png`, `tint_tiles_before_after.png`.

**V2 — aspect + moisture.** The sidecar is now **ODB2**. It adds 4 bytes per
record: u8 northness, u8 moisture and u16 elevation in metres.
- Northness is the north component of the gradient on a DEM smoothed over
  150 m, and saturates at a grade of 0.35.
- Moisture = 1 − (height above the local 1 km minimum)/150 m.
- `ODTerrainManager`, `ODForestManager` and `ODBarrierManager` parse ODB1 and
  ODB2 by magic, and ODB1 reads as neutral.
- `ApplyBiomeTint`: south slopes lerp toward a dry tan (strength 0.45),
  north slopes darken (up to 30%), and moisture lerps toward a meadow green
  (0.40).
- `BlendAt`: tree density ×(1 + 0.25N − 0.35S)·(1 − 0.20M), bush density
  ×(1 + 0.5M), willow +0.5M, sagebrush +0.5S.
  - The first cut used −0.45S and −0.35M, which thinned the median corridor
    tile by 26%, because the road corridor *is* the valley bottom (mean
    moisture 0.59). The softened values give a median of 0.82.
- Stats: grid northness std 0.52 (27% of samples |n| > 0.5); moisture mean is
  0.59 on the corridor and 0.16 on the grid. Plot: `aspect_moisture_hist.png`.
- `HashLattice`/`ValueNoise2D` moved from ODTerrainManager.cpp into the shared
  `ODBinSidecarUtil.h` (verbatim) so the forest can reuse them.

**V5 — species banding + aspen.** This is recipe interpolation inside
`VarietyRecipe` in ODForestManager.cpp; no new enum entries.
- Bands: the Evergreen table row (lodgepole) blends to a spruce-fir recipe over
  2,950→3,200 m, then to krummholz over 3,350→3,550 m.
- Aspen: `0.75 · clamp(0.7M + 0.6N) · noiseMask · window(2,450–3,350 m)`
  moves share into the two aspen meshes (Blob/ScrubOak, already Colorado
  aspen). The noise mask is `ValueNoise2D` on a 500 m lattice, smoothstepped
  over 0.55–0.70, evaluated once per tile.
- Replica `verify_v5_species.py/.log` + `species_vs_elevation.png`, run over
  the 13,887 real 96 m forest tiles that contain a road station:
  - ODB1-neutral assert passes.
  - Aspen mask is non-zero on 42% of tiles.
  - Aspen share ≥ 20% on 32% of tiles and ≥ 40% on 12%. The table baseline is 8%.
  - Spruce + fir go from 5% at 2,400–2,800 m to 72% at 3,200–3,350 m.
  - Krummholz reaches 41–49% above 3,450 m.
  - TreeDensityScale p10/p50/p90 is 0.47/0.82/0.99.

**Build:** the `ApplyBiomeTint`/ODB2 parsers built "Succeeded" earlier. The
final density-coefficient edit compiled `ODForestManager.cpp` cleanly, but
the full build is blocked by another agent's in-flight map/nav work, not by this
change:
- First attempt: compile errors in **ODRaceHUD.cpp** (`build_v5.log`).
- Retry: every translation unit compiles, but linking fails on the undefined
  `AODRaceHUD::OpenWorldMap`/`CloseWorldMap` (`build_v5_retry.log`).

Re-run Build.bat once that work lands.

**Needs Taylor live:**
- The tint palette: `DrySlopeTint`, `MoistMeadowTint` and their strengths.
- Whether the aspen patches read as groves: patch size (500 m noise lattice)
  and share (`AspenMaxShare`).
- Corridor density after the softened moisture term.
- Band elevations vs the real Summit Co. treeline.

**Open:**
- `build_ambient_zones.py` will pick up the grid samples on its next rebuild;
  it was not rerun.
- Other areas stay ODB1 (neutral) until they are re-exported.
- `BlendAt` does a linear scan over 81k points per forest tile (marked
  `ponytail:`). Bucket it like ODBarrierManager if it shows up in a profile.

---

### 7.36 Terrain variety V3/V4 — pale-hills fixes (2026-09-25)

Closes the two item-6 carriers (Vail = pale flat albedo, Silverthorne = aerial
perspective) at the mechanism level. Both need Taylor's live look for the final
level; nothing here was rendered (standing no-run rule) — verification is
offline math only.

**V3 — `unreal/apply_ground_material.py` (flat/vegetated layer only).**
- New block after the spring-green lerp, before the alpine scree blend: a
  2-octave world-space `value_noise` (300 m + 1200 m, both through
  `wrap_lattice_uv`) → contrast-stretched → 3-stop multiplier over the existing
  biome-tinted blend: green meadow (macro 0) → dry meadow (0.5) → bare dirt
  (1, darker brown, not khaki). Then `PixelDepth`-driven far shaping: saturation
  extrapolated past 1 (Lerp(Desaturation, color, 1+boost·far_t)) and value
  multiplied down. Steep rock, alpine scree, snow, sand untouched.
- Side fix: the existing 120 m dirt-patch noise now also goes through
  `wrap_lattice_uv` (it fed the same large-magnitude sin() hash the 2026-09-08
  snow fix proved degenerate on GPU — likely one uniform dirt amount
  everywhere). If dirt patches suddenly read as NEW patchiness, this is why.
- Tunables (all module-level constants, one block): `MACRO_FINE_SCALE_CM`
  30000, `MACRO_COARSE_SCALE_CM` 120000, `MACRO_COARSE_WEIGHT` 0.6,
  `MACRO_CONTRAST` 2.2, `MACRO_GREEN_MUL` (0.75,1.10,0.70), `MACRO_DRY_MUL`
  (1.10,1.00,0.80), `MACRO_DIRT_MUL` (0.75,0.62,0.50), `FAR_START_CM` 150000,
  `FAR_END_CM` 800000, `FAR_DARKEN` 0.25, `FAR_SAT_BOOST` 0.25.
- Applied: `UnrealEditor-Cmd -run=pythonscript` (the standard pipeline apply
  step, -nullrhi) re-built + saved M_GroundStylized, post-save verify OK
  (`scratchpad/v3_apply_ground_material.log`). -nullrhi means the HLSL was
  NOT shader-compiled here — first editor/game load compiles it.
- Offline verification (`scratchpad/verify_v3_ground_macro.py` → `.log`,
  `.png`): far-field albedo model (textures → linear mip mean; off-corridor
  ApplyBiomeTint port; material constants parsed from the script itself) over
  all 305 summit_county coarse terrain tiles (100 m grid, 116,559 flat pts,
  camera at area origin). Before → after, flat pts, sRGB HSV:
  - macro variants alone (depth 0): hue circ-std 9.55° → 11.17°, sat std
    0.092 → 0.115, value std 0.045 → 0.050.
  - with distance shaping (camera view): hue std 9.55° → 10.59°, sat mean
    0.353 → 0.523, sat std 0.092 → 0.193, value mean 0.304 → 0.272; mean
    albedo sRGB8 (74,78,53) → (67,70,40).
  - mean V vs depth: −2.5 % at 0–1.5 km, −6.9 % at 5 km, −10.9 % from 8 km on.
  - Texture means confirm the khaki source: Ground037 (dirt) linear
    (0.32,0.29,0.10) is by far the palest flat-layer input.
  - Caveats: "before" uses a WORKING (float64) dirt-noise hash, so if the GPU
    hash really was degenerate the true before-variance is lower still; the
    100 m grid aliases the 120 m dirt noise; no lighting/AP in the model.

**V4 — `OpenDriveUE/Source/OpenDriveUE/ODAtmosphereController.cpp`.**
- The component is configured here at BeginPlay (level scripts only spawn the
  actor). Added one named constant `AerialPerspectiveViewDistanceScale = 0.8f`
  (engine default 1.0) → `SetAerialPespectiveViewDistanceScale` (engine's
  spelling). Mie (0.009), Rayleigh (0.028), start depth (0.07 km) unchanged —
  the distance scale affects geometry AP only, not the sky dome, so it's the
  least collateral single knob. Next steps if 0.8 isn't enough: 0.7, then
  consider Mie 0.009 → 0.007.
- Offline estimate (`scratchpad/verify_v4_aerial_perspective.py` → `.log`,
  `.png`; homogeneous ground-density path, single extinction, no multiple
  scattering/fog): blue veil (1−T) at 5 km 0.169 → 0.137 (−19 %), 10 km
  0.311 → 0.257 (−17 %), 20 km 0.526 → 0.449 (−15 %). Parameter-only
  estimate — the actual on-screen delta needs Taylor's eyes.
- Compiled: headless Build.bat, `Result: Succeeded`
  (`scratchpad/v4_build.log`). NOTE: the first build failed on a latent
  unity-build collision — ODSettingsMenu.cpp and ODMainMenu.cpp both define
  an anonymous-namespace `FindActor<T>` template, which only collides once
  adaptive unity regroups files (triggered by any edited .cpp). Fixed by
  renaming ODSettingsMenu.cpp's copy to `FindSettingsActor` (4 lines, no
  behavior change).

**Needs Taylor live:** V3 palette (variant multipliers, far darken/sat) and
V4 haze level at Silverthorne (Ruby Ranch Rd) / Vail (Piney Lake Rd) /
Frisco / Dillon; watch that far ridges don't go too saturated-yellow
(`FAR_SAT_BOOST`) and depth cueing still reads (V4 scale).

### 7.37 Arc 4.2 v3: world map + navigation (2026-09-25)

**Built.** Full-screen world map, destinations (towns + road-snapped
waypoints), A* navigation with the route drawn on every map panel, a HUD
next-turn/distance panel, auto-reroute, and fast travel. Open-world
(`AODRoadManager`-with-route-graph) levels only. Tail of the Dragon has no
graph, so the key is a no-op there.

- **Road graph sidecar (new):** `pipeline/export_road_centerline_bin.py`
  writes a third file, `routegraph_<AREA>_test.bin`.
  - Format: `ODG1`, then u32 N and N×(f32 x_cm, y_cm), then u32 E and
    E×(u32 a, u32 b, f32 len_cm). Edges are undirected.
  - It is already stage 11 of `rebuild_area.py`, so rebakes regenerate it.
  - The existing ODC1/ODM1 outputs are byte-identical to before (md5
    unchanged).
  - `road_stations.json` has no junction topology, so `build_route_graph()`
    rebuilds it by proximity:
    - (a) Segment end → nearest station of each other segment, ≤12 m and
      |dz| ≤12 m. Motorway_link ends sit 5-10 m off the motorway.
    - (b) Interior crossings ≤4 m, |dz| ≤2.5 m, neither station a real OSM
      bridge/tunnel (`is_struct`/`is_tunnel`). Not `is_bridge`, which flags
      49 % of stations.
    - (c) End↔end ≤30 m, |dz| ≤3 m, to cover junction pullback.
    - (d) A Kruskal-style bridge: the shortest end↔end pair ≤60 m, added
      only when it joins two still-separate pieces. Silverthorne was an
      island until this.
  - Nodes are every 4th station (~20 m) plus segment ends and link
    stations. summit_county: 72 617 nodes, 74 872 edges, 1.48 MB.
  - Largest component = 93.2 % of nodes. 27/29 towns sit on it; Blue River
    and Jefferson do not.
- **Runtime (`AODRoadManager`):**
  - `LoadRouteGraph` derives the path from `CenterlineFilePrefix`
    (`centerline_` → `routegraph_`) and builds CSR adjacency.
  - `FindRoutePath` is A* with a Euclidean heuristic.
  - `SetRouteDestination` / `ClearRoute` / `SnapToRouteGraph` /
    `GetNextTurn`.
  - `Tick` runs `UpdateRouteProgress`:
    - Windowed nearest-segment progress and remaining distance.
    - Reroutes when more than 50 m off the line, with a 2 s cooldown.
    - Arrival at 30 m clears the route and shows a 4 s "ARRIVED" toast.
  - Turns: a route node with graph degree ≥3 whose heading 25 m before vs
    25 m after bends ≥35°. Turns within 30 m merge.
    - Wording: "Keep" <60°, "Turn" <120°, "Sharp" ≥120°.
  - `ponytail:` nearest-node lookup is a brute-force O(N) scan. It runs only
    on a pick or a throttled reroute.
- **HUD (`AODRaceHUD`):**
  - `DrawStageMap` draws the route as a magenta line plus a destination dot
    on the mini-map, the pause map and the world map.
  - It takes an optional view-center override.
  - The terrain texture now draws only where the view overlaps the texture.
    Before, `WorldToUV` clamping stretched the edge texels whenever the view
    extended past the texture.
  - New `DrawWorldMap`, `DrawNavPanel` (top-center arrow + "Turn right in
    0.3 mi" + destination · remaining mi/ft), `Open/CloseWorldMap`.
- **Pause menu:** new row 1 "World Map" (rows renumbered, 9 total).
  Escape/P close the world map first.
- **Input (§7.32 layer):** new rebindable actions `WorldMap` (Tab / gamepad
  Back = `Gamepad_Special_Left`) and `ClearRoute` (Backspace). Backspace was
  added to the rebind capture list.
- **Main menu:** `TravelToTown()` is public; it wraps `StartAt`, so fast
  travel uses the same teleport + loading screen. `SetCursorVisible` is now
  public.

**Controls (world map).** Opening pauses the game and shows the cursor;
closing unpauses.

| Action | Keyboard / mouse | Gamepad |
|---|---|---|
| Open / close | Tab (rebindable), or pause → World Map | Back (rebindable) |
| Close | Esc / P | B |
| Pan | Drag, or arrows / WASD | Left stick / d-pad |
| Zoom | Wheel (about the cursor), `[` `]` | LB / RB |
| Route to town / road waypoint | Left-click (snaps to the nearest graph node) | — |
| Route to crosshair | Enter | A |
| Clear route | Right-click, or Backspace (anytime, rebindable) | X |
| Fast travel to hovered town | F | Y |

**Verification (backend only; the game was not run).**
- `scratchpad/nav/verify_route_graph.py` → `routes_summit_county.log` and
  `.png`. It loads the same ODG1/ODL1/ODC1/ODM1 sidecars and mirrors
  FindRoutePath, the turn loop and UpdateRouteProgress. **ALL CHECKS
  PASSED.**
- Each of the 8 town pairs checks: connected, A* length equals scipy
  Dijkstra, length ≥ straight line, endpoints within 50 m.

| Route | Length | Straight line | Nodes | Turns |
|---|---|---|---|---|
| Frisco→Breckenridge | 17.23 km | 11.51 km | 929 | 12 |
| Frisco→Vail | 51.69 km | 27.34 km | 2664 | 7 |
| Dillon→Keystone | 10.24 km | 5.88 km | 540 | 4 |
| Silverthorne→Copper Mountain | 22.60 km | 18.04 km | 1203 | 8 |
| Breckenridge→Vail | 68.50 km | 36.15 km | 3566 | 18 |
| Keystone→Breckenridge | 22.26 km | 15.05 km | 1160 | 13 |
| Silverthorne→Frisco | 10.66 km | 7.65 km | 605 | 10 |
| Vail→Keystone | 68.44 km | 36.80 km | 3545 | 13 |

- Drive simulation along Frisco→Breckenridge:
  - Remaining distance falls monotonically and arrival fires.
  - A stray point 97 m off the line trips the reroute. The new route from
    there is 9.03 km to the same destination.
- Build: headless `Build.bat OpenDriveUEEditor Win64 Development`,
  `Result: Succeeded` (`scratchpad/nav/build.log`). All six touched .cpp
  files have objects newer than their sources.

**Needs Taylor live.**
- **World map:** check that the controls feel right, especially
  drag-vs-click, wheel zoom while paused, and the gamepad mapping.
- **Turn prompts and false positives:** make sure prompts fire at real
  intersections. Complex interchanges and roundabouts may produce odd
  "Sharp" prompts.
- **Reroute:** the 50 m / 2 s thresholds.
- **Fast travel:** make sure it lands cleanly.

**Known limits.**
- One-way restrictions are ignored, so routes may go the wrong way down
  divided-highway carriageways and ramps.
- Grade-separated crossings are only kept apart by the elevation and
  structure gates.
- Blue River and Jefferson (and some small pieces) are not on the main
  graph component. Picking them shows "no road route to there".

## Weather plan (2026-09-25): biome + location driven

**Today:** the only weather is falling snow (`AODSeasonController`,
a pool of sphere particles around the pawn). It follows the global season
clock and is the same everywhere. `road_moisture` in `climate.json` is a
fixed road tint, not rain. Fog values are fixed (`ODAtmosphereController`).
Grip ignores weather (`UODVehicleMovement::SurfaceGrip`).

**Core idea: two inputs, two jobs.**
- **Location decides which storms can happen.** It comes from the stage's
  `origin_latlon` plus distance to the coast. Hurricanes and tornadoes are
  geography, not vegetation: a Plains biome in Colorado at 3000 m never
  gets a hurricane, and Oahu's Coastal/Rainforest biome can.
- **Biome decides how weather looks locally.** It comes from `BiomeAt(x,y)`
  (`ODBarrierManager.h:164`, currently private, needs making public) or the
  forest manager's biome data. Desert gives dust instead of rain, Alpine
  and Arctic give snow, Wetland gives more fog, and so on.
- **Season plus elevation decides the precipitation type:**
  rain → sleet → snow, using a temperature estimate.

### W0. Per-stage climate profile (pipeline, ~1 session)
Generalize `fetch_climate.py` (it's hardcoded to tail_of_the_dragon) to
run on every stage and test road (`road_test_meta.json` already carries
`origin_latlon`). Add these to `climate.json`:
- monthly temperature and precipitation (NASA POWER, same API, monthly
  climatology instead of the annual value), mapped onto the season clock;
- `coast_km` (from the OSM coastline, or distance to the nearest ocean
  water in the existing water data);
- storm eligibility flags computed offline from lat/lon:
  - **hurricane:** `coast_km < ~150`, |lat| between 8° and 40°, and a
    known basin (N Atlantic, E/W Pacific, N Indian, S Pacific/Indian).
    Oahu and Gulf/Atlantic coast stages qualify. Summit, Skagit, Palmdale
    and Dragon don't.
  - **tornado:** continental interior with Plains/Farmland biome share
    above a threshold and latitude 25–50°. Allow it weakly on
    Dragon/Green Valley (the SE US gets them). Never on Oahu or in the
    Alpine band.
  - **dust storm:** Desert/Savanna/Canyon share high and precipitation
    under ~350 mm/year (Palmdale).

The runtime then reads flags instead of doing geography math, and one
JSON edit overrides a stage.

### W1. `AODWeatherController`: state machine, no visuals (~1 session)
Same find-or-spawn-from-`AODGameMode` shape as the season/day-night
controllers.
- States: Clear, Overcast, Rain, HeavyRain, Sleet, Snow, Blizzard, Fog,
  DustStorm, Thunderstorm, Tornado (event), Hurricane (event).
- Weighted random walk between states on a timescale of minutes. The
  weights come from the season clock's month (via W0's monthly precip)
  multiplied by the biome at the player. Transitions reuse the season
  controller's smoothstep ramps, so nothing snaps.
- Publishes a few scalars: `PrecipIntensity`, `PrecipType` (rain 0 → snow
  1, sleet in between), `Wind` (speed + dir), `Visibility`, `Wetness`
  (accumulates, dries slowly), `CloudCover`, `DustAmount`.
- Takes over from season snowfall: `SnowfallIntensity` becomes
  `PrecipIntensity * PrecipType`, and the season clock stops owning
  falling snow (it keeps ground cover, freeze and green-up).
- Debug: a console command to force a state (`od.Weather Hurricane`).

### W2. Visual consumers (~2 sessions)
- **Precipitation particles:** generalize the snowfall pool (spheres →
  stretched streaks for rain, and a sleet mix) before reaching for Niagara.
  One pool around the pawn and one component.
- **Atmosphere:** `ODAtmosphereController` reads `Visibility`,
  `CloudCover` and `DustAmount`. That covers fog density, tan/orange fog
  inscatter for dust, VolumetricCloud coverage and a darker sky.
- **Road wetness:** the existing `road_moisture` roughness term becomes a
  runtime MID parameter driven by `Wetness`, plus puddle darkening.
  Terrain/foliage get the same parameter.
- **Lightning:** a flash from a DirectionalLight intensity spike plus
  delayed thunder in the ambient audio manager.
- **Audio:** rain, wind and dust loops layered into `ODAmbientSoundManager`.

### W3. Driving consequences (~0.5 session)
`SurfaceGrip()` gets a weather multiplier on the on-road tier: wet about
0.8, sleet/slush 0.6, snow 0.45, ice (FreezeBlend + sleet) 0.3. Wind adds
a small lateral force on the pawn. Keep it gentle, since the corridor
design wants the player on the road, not in a ditch. The garage tire tiers
already exist, so rally tires could lose less grip.

### W4. Tornado event (~2 sessions, only where eligible)
- A funnel from a stack of rotating billboard/cylinder meshes with a
  debris particle ring, spawned at 2–5 km and tracking across the stage.
  It's always preceded by Thunderstorm state (green sky tint, hail
  optional).
- Gameplay: strong wind force within ~300 m that pulls toward the core,
  plus heavy visibility loss. No terrain or building destruction: out of
  scope and expensive.
- HUD warning when it spawns.

### W5. Hurricane event (~1–2 sessions, coastal only)
It's mostly a scaled-up HeavyRain + Wind: sustained crosswind, driving
rain, near-zero visibility on gusts, and a rough water surface (a
`ODWaterManager` wave-amplitude param). It arrives in bands of
rain → lull → rain over ~10–20 min, and optionally has a calm "eye"
phase. Test on `oahu_test`.

### Order and verification
Order: W0 → W1 → W2 → W3, then W4/W5 as optional events. Each step is
verified with backend output only: dump the W0 JSON per stage and check
the eligibility table matches expectations (Oahu: hurricane yes;
Palmdale: dust yes; Summit: blizzard-heavy, no tornado). For W1, log a
simulated 4-hour state trace per stage and plot state vs. season clock.

### Decisions (Taylor, 2026-09-25)
- **Random but realistic.** No live weather API. W1's weighted state walk
  (location flags × season month × biome) is the whole design.
- **No weather picker** anywhere. Weather is always automatic.
- **Season selection lives in the garage menu** (the only player-facing
  control). There's no season UI today; the only way to set it is the
  `-odseason=` QA override, which sets `ClockSeconds` and freezes the
  clock. Plan: a garage "Season" row with Auto (default, 4 h loop) /
  Summer / Fall / Winter / Spring. A fixed pick reuses that same
  freeze path at the season's hold midpoint, and it's saved with the
  other garage settings. Weather then rolls within the chosen season
  (for example, Winter at Summit means snow or blizzard; Winter at
  Palmdale means clear or rare rain).
