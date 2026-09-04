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
  *(mini map ✅ v2 + terrain-color background; world map + navigation = Arc 7.4)*

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

### 5.4 Ambient biome/city audio (backlog — Taylor asked 2026-07-27, nothing
built yet)
- Wind/rustling in trees for forested biomes, water sound along
  beaches/coastline, general traffic/city ambience in dense urban tiles
  (Honolulu downtown and similar). Just logging the ask here — no pipeline or
  runtime work started.
- Likely hook: the same tile-level biome/settlement classification data
  `pipeline/classify_biomes.py` (14-archetype terrain biome per tile) and
  `pipeline/classify_settlements.py` (populated-tile archetype — American
  Town/City/etc.) already produce for `AODForestManager`/`AODBuildingManager`.
  Ambient sound zones should follow the same tile grid the rest of world-gen
  already keys off, rather than inventing a separate from-scratch spatial
  system.


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
  reported during the road work above, not re-investigated here).
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
| 13 | exhaust editing | 5.2 (params live, UI in 4.3) | params ✅ |
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
