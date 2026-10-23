# Fun features: scoping

Nine player-facing features. For each one: what already exists in the code, the
plan, open questions and a size. Nothing is built yet. Paths are relative to
`OpenDriveUE/Source/OpenDriveUE/` unless they start with `pipeline/`.

## Summary

| # | Feature | Already exists | Size | Suggested order |
|---|---|---|---|---|
| 7 | Time / weather scrubber | Setters for all three, slider rows | S | 1 |
| 6 | Road trip mode | Routing, GPS banner, minimap, world-map pick | S–M | 2 |
| 2 | Speed traps / zones | Sub-frame gate timing, road centreline, saves | M | 3 |
| 3 | Shareable ghosts | Recording and playback | S | 4 |
| 1 | Music controls | Input layer, wheel buttons, Canvas HUD | S (v1) / M (v2) | 5 |
| 5 | Landmark hunting | Landmark meshes, a pipeline list | M | 6 |
| 8 | Horn + traffic reactions | Traffic AI, player-as-leader, pass-by voices | M (8a) / M (8b) | 7 |
| 4 | Photo mode | Pause, free-look, PPVs, screenshot call | M | 8 |
| 9 | Instant replay | Fixed-step sim, camera views, ghost lerp | L | 9 |

Small means about a day, M a few days, L a week or more. The order puts cheap,
visible wins first, then features that unlock others: #7 feeds #4, and #3's
file format feeds #9.

Shared plumbing that several features need:
- **New input actions** (#1, #4, #8, #9): add an `EODInputAction` entry before
  `Count` (`ODInputConfig.h:44-62`), plus its `ActionName`, defaults in
  `ODInputConfig.cpp` and a row in `ODRebindMenu`. Bind it from the
  `{Action, Fn, bWhenPaused}` table in `ODRaceHUD.cpp:87-111`. Wheel buttons
  need an `EODWheelButton` entry (`ODWheel.h:38-47`), consumed at
  `ODVehiclePawn.cpp:1282-1313`.
- **Toasts / small HUD panels** (#1, #2, #5, #8): all UI is Canvas in
  `AODRaceHUD::DrawHUD` (`ODRaceHUD.cpp:2592`), styled from `ODHudStyle.h`.
  There's no UMG, which keeps it editor-agnostic.
- **Per-area records** (#2, #5): today `UODRaceSave` is one slot per race
  (`ODRaceManager.h:8-29`). Add one `UODAreaSave` slot per area
  (`OpenDrive_area_<GetAreaKey()>`) holding trap bests and discovered landmarks.

---

## 1. Music controls (Spotify or any player)

**Exists:** nothing media-related. Input, wheel buttons and the HUD are all
ready to host it (see shared plumbing). Build.cs links only
`dinput8/dxguid/dxgi` and doesn't use WinRT.

**Plan: two steps.**
- **v1, controls only, no new dependency.** Add actions `MediaPlayPause`,
  `MediaNext` and `MediaPrev`, plus wheel buttons. Each one calls `SendInput`
  with `VK_MEDIA_PLAY_PAUSE` / `VK_MEDIA_NEXT_TRACK` / `VK_MEDIA_PREV_TRACK`.
  Windows routes those keys to whichever player owns the media session,
  including Spotify, browsers and Apple Music. That's about 20 lines and needs
  only `user32`, which is already linked. Default binds: gamepad D-pad
  left/right and keyboard `[` `]` `\`.
- **v2, now-playing panel.** Read the title, artist, play state and album art
  from `GlobalSystemMediaTransportControlsSessionManager` (SMTC).
  - This needs C++/WinRT: the Windows SDK `cppwinrt` headers, `windowsapp.lib`
    and C++17/20. Check the engine's default `CppStandard` first.
  - Wrap the includes in `THIRD_PARTY_INCLUDES_START` and
    `AllowWindowsPlatformTypes.h`.
  - Poll on the thread pool every ~1 s and also on the session-changed event,
    then marshal back with the existing `Async` → `AsyncTask(GameThread)`
    pattern (`ODBarrierManager.cpp:263-270`).
  - Draw a small bottom-left card that slides in for ~4 s on a track change
    and stays visible while paused. Album art goes in through
    `FImageUtils::ImportBufferAsTexture2D`.

**Not doing:** the Spotify Web API. It needs OAuth, a registered app and
Premium for playback control, and it only works with Spotify. SMTC covers
every player.

**Questions:**
- Should the card be always-on, show only on track change, or follow a setting?
- Should in-game music ducking or volume be part of this? It can't affect the
  external player's volume through SMTC.

**Size:** S for v1, M for v2. Most of v2's risk is getting the WinRT headers
to build inside UBT.

---

## 2. Speed traps and speed zones

**Exists:**
- `AODRaceManager` does sub-frame gate crossing (`ODRaceManager.cpp:360-367`),
  and the same maths gives an exact crossing time and speed at a trap.
- Road centreline and nearest-point lookup: `MapCenterline` and
  `NearestRoadPoint` (`ODRoadManager.h:285, 333`).
- Speed comes from `UODVehicleMovement::GetForwardSpeed()`.
- `maxspeed` per station already exists in pipeline
  `road_stations.json` (`pipeline/build_road_stations.py:84, 508`), but it
  never reaches runtime.

**Plan:**
- **Pipeline:** add `pipeline/build_speed_challenges.py`, which writes
  `Masks/challenges_<area>.json`. Two kinds:
  - **Trap:** a point at the end of the longest straights. Find these from
    station curvature: the lowest curvature over ≥ 400 m, top N per area,
    spaced ≥ 1 km apart.
  - **Zone:** a 1–3 km section with flowing corners, scored by average speed.
    Pick these from curvature variance on the same stations.
  - Name each one from the nearest place label, e.g. "Hwy 9 straight".
- **Runtime:** add a small `AODChallengeManager`, spawned in free roam only.
  - Load the JSON.
  - Each tick, run the existing segment-vs-circle test against nearby traps.
    For zones, test the entry and exit gates and integrate distance over time.
  - Show a toast with the result versus your best, then save to
    `UODAreaSave`.
- **Visuals:** a marker (a tall emissive post or sign, built procedurally) and
  icons on `DrawStageMap` and the world map.
- **Optional:** a speed-camera "flash" (a short white exposure pop through the
  existing grade PPV).

**Questions:**
- Free roam only, or also during races? Traffic is off in races, so traps
  never collide with AI there.
- Should results be scored against the speed limit (fun "fines" in a cozy
  mode) or just record top speed?

**Size:** M (pipeline stage, manager, markers, save).

---

## 3. Shareable ghosts

**Exists:**
- `AODGhost` records position and rotation at 10 Hz, frame-rate-independently
  (`ODRaceManager.cpp:337-344`).
- It's saved only on a personal best, inside the per-race USaveGame
  (`cpp:427-458`).
- Car identity isn't stored. Playback copies the current player's mesh.

**Plan:**
- **File format:** `Saved/Ghosts/<area>_<track>_<time>.odghost` holds a small
  header plus samples:

  ```
  magic ODGH, version, area key, track hash, car id, total time,
  sample interval, then N × (pos, quat)
  ```

  - The track hash is a hash of the gate transforms, not the track name,
    because `custom_<n>` names differ between machines.
  - Store rotations as quaternions, and switch playback from `FRotator` lerp to
    `FQuat::Slerp`. That's a small fix.
- **Export:** write the file every time a new best is saved (alongside the
  save slot), plus an "Open ghosts folder" row in the pause menu.
- **Import:** on race start, scan `Saved/Ghosts/` for files matching the area
  and track hash. A pause-menu row cycles through "Race against: My best /
  Imported (name, time)". To share, drop the file in the folder.
- Spawn the ghost with the recorded car's mesh when that car exists.

**Questions:**
- Show several ghosts at once?
- Should ghost files hold a display name? With no accounts, it would just be
  free text from settings.

**Size:** S.

---

## 4. Photo mode

**Exists:**
- Pause works through `SetGamePaused` (`ODPauseMenu.cpp:182`), and
  `HideForOverlay()` (`ODPauseMenu.h:52`) is a precedent for a full-screen
  paused mode.
- Free-look orbit exists (`ODVehiclePawn.cpp:1393-1451`), and the SpringArm
  ticks while paused.
- The grade PPV has exposure settings (`ODAtmosphereController.cpp:429-501`).
- `RequestScreenshot(false)` captures without UI.
- There's no DOF, no HUD-hide flag and no player-facing capture.

**Plan:**
- **Enter:** add a `PhotoMode` action, available from the pause menu too.
  It keeps the game paused, hides the pause menu and sets a HUD `bPhotoMode`
  flag. While the flag is on, `DrawHUD` draws only a minimal control strip,
  and pressing H hides that too.
- **Camera:** spawn a separate photo camera actor that ticks while paused and
  uses `SetViewTargetWithBlend`. It orbits the car by default, with a free-fly
  toggle clamped to ~50 m from the car and above the terrain. Inputs need
  `bExecuteWhenPaused`.
- **Controls** use Canvas rows in the same pattern as `ODSettingsMenu`
  (`NumRows / GetRow / AdjustSelected`), so slider bars come for free from
  `ParsePercent`:
  - FOV
  - roll
  - exposure bias
  - DOF focus distance (auto-focus on the car) and aperture, through a
    dedicated PPV with `DepthOfFieldFstop` / `FocalDistance`
  - vignette
  - time of day, weather and season, reusing #7 and its paused-tick fix
- **Capture:** `FScreenshotRequest::RequestScreenshot(filename, false, false)`
  to `Saved/Screenshots/OpenDrive_<area>_<stamp>.png`. Optionally offer a 2× or
  4× resolution through `HighResShot`. Show a toast with the path.

**Questions:**
- Free-fly radius limit: does the bigger number expose unfinished distant
  terrain?
- Car paint and grime tweaks inside photo mode, or keep it camera-only?

**Size:** M.

---

## 5. Landmark hunting

**Exists:**
- `pipeline/build_landmarks.py` has a curated list of about 6 per area. It
  writes the mesh tiles (ODL1) and `data/road_tiles/<area>_test/landmarks.json`
  with names and footprints.
- At runtime, `ODBuildingManager.cpp:323-425` only swaps meshes in. It keeps
  no names or positions.

**Plan:**
- **Pipeline:** `build_landmarks.py` also writes
  `Masks/landmarks_<area>.json`, a list of
  `{name, kind, centroid_cm, radius_cm, height_m}`. It's a small sidecar,
  loaded once like the place labels. Don't add a name field to ODL1: the place
  labels share that magic.
- **Optional:** widen the set beyond built meshes. The ranked candidate tables
  in `docs/landmarks/*.md` already list peaks, viewpoints, bridges and lakes.
  Those can be discoverable points with no mesh: "viewpoints".
- **Runtime:** discover a landmark when the car is within radius + 150 m and
  has a line of sight to the centroid, or when a cheap distance check alone
  passes.
  - Show a toast with the landmark name and "3 / 9 discovered".
  - Save to `UODAreaSave`.
  - Add a pause-menu page with a checklist: undiscovered entries show as
    "???" with the distance and direction from the car.
  - Undiscovered landmarks appear on the world map as hollow pins. Clicking
    one calls the existing `SetRouteDestination` (synergy with #6).
- **Optional:** a per-area "100% explored" reward, such as a paint colour.

**Questions:**
- Built meshes only, or also mesh-less viewpoints? The latter gives more to
  find, at no art cost.
- `u_tail_of_the_dragon` has a `landmarks.json` but no docs page. Should the
  docs be regenerated?

**Size:** M.

---

## 6. Road trip mode

**Exists:** most of it.
- A* over the route graph: `FindRoutePath`, `ODRoadManager.cpp:772`.
- `SetRouteDestination` with re-routing and arrival handling.
- `GetNextTurn` and the GPS banner with turn arrows (`ODRaceHUD.cpp:2540-2590`).
- Route drawing on the minimap.
- In-game world-map click-to-route (`ODRaceHUD.cpp:2292-2310`).

**Plan:**
- **Pick A and B in the main menu.**
  - The AreaMap already picks the start town (`StartAt`, `TownPick`). Add a
    second pick, "Destination".
  - That needs a screen-to-area-cm inverse of `AreaCmToScreen`: invert the
    bilinear map with a few Newton steps, or use towns only for v1.
  - Pass the destination as a URL option `?oddest=<x>,<y>` through
    `OpenArea()`. After the graph loads, `ODGameMode` calls
    `SetRouteDestination`.
- **Trip feel:**
  - A start banner like "Road trip: Arlington → Darrington · 42 km".
  - An arrival card with distance, time, average speed, top speed and
    landmarks passed (synergy with #5).
  - A trip log of the last N trips per area.
- **Street names in turns:** sample the nearest `PlaceLabel` a little past
  each turn point, e.g. "Turn left onto SR 530". This is a runtime lookup and
  needs no pipeline change.
- **Optional:** multi-stop trips through the existing `RouteThrough`.

**Gotcha:** the graph is undirected with a wrong-way penalty
(`WrongWayCostMult = 20`), so long trips may still route the wrong way down a
one-way street. That's fine in rural areas, but worth checking on
`sf_russian_hill`.

**Questions:**
- Can the destination be any point, or towns and landmarks only? Towns-only
  skips the inverse transform.

**Size:** S–M. Mostly menu work.

---

## 7. Time / weather / season scrubber

**Exists:** the runtime setters, already used by the garage
(`ODMainMenu.cpp:1906-1937`):
- `ADayNight->TimeOfDay` and `CycleSeconds`
- `Season->SetSeasonLock(name)`
- `Weather->ForceState(name, bInstant)`, `ResumeAuto`, `CanProduce`

Slider bars come free from `"NN%"` row values (`ParsePercent`,
`ODRaceHUD.cpp:547-555`).

**Plan:**
- Add a pause-menu row "Environment" that opens a sub-page of rows:
  - **Time of day:** 0–24 h, left/right in 15-minute steps.
  - **Time speed:** Frozen / Real / 1 h per min / Time-lapse (24 h per min).
    This maps to `CycleSeconds`.
  - **Season:** Auto / Spring / Summer / Autumn / Winter.
  - **Weather:** Auto plus the states this climate can produce, filtered
    through `AODMainMenu::WeatherPossible`. Rename that to a shared static
    helper.
- **Paused-tick fix:** the DayNight, Season and Weather controllers don't
  tick while paused, so scrubbing wouldn't move the sun until resume. Set
  `bTickEvenWhenPaused` on the three controllers, but have them advance their
  clocks only when not paused, so the time doesn't run while paused. The
  alternative is calling their update paths directly.
- Persist the choice per session only. The garage already covers defaults.

**Gotcha:** the QA flags `-odseason=` and `-odhour=` override player choice.
Leave that alone, since they're QA-only.

**Size:** S.

---

## 8. Horn and traffic reactions

**Exists:**
- No horn, indicators or hazards anywhere.
- Traffic (`ODTrafficManager`) is IDM car-following at 20 Hz, with `FAgent`
  structs drawn as instanced meshes.
- The player already counts as a leader.
- There's a `Dodge` bias around a parked player after 2 s, and `HoldUntil`
  after a bump.
- The nearest cars already get pass-by `UODEngineSynth` voices.
- All audio is procedural (no SoundWaves).

**8a. Horn plus reactions:**
- **Horn sound:** a `Horn` action and a small horn synth: two detuned square
  or saw tones around 400/500 Hz through a low-pass filter. That's about
  40 lines in the `ODEngineSynth` style, needs no asset, and is license-clean.
  Each car gets its own pitch and character from its vehicle def.
- **Reactions:** for agents within ~40 m in a forward cone of the player
  (`Step()`, next to the player-corridor block):
  - **Agent stopped ahead of you and not at a signal:** start `Dodge` now
    instead of after 2 s, and nudge it to pull right. Check for oncoming
    traffic before dodging. That check is already a `ponytail:` gap.
  - **Agent being honked at from behind on a multi-lane road:** trigger a
    discretionary MOBIL lane change to the right if it's safe. MOBIL is
    already implemented for lane ends (`.cpp:1554`).
  - **Honk-back:** if you honk repeatedly, or just bumped them (`HoldUntil`),
    the agent honks back. Reuse a pass-by voice slot with the horn synth.
- **AI-initiated honks:** honk when the player sits through a green light
  ahead of them, or cuts in close. These are cheap once the synth exists.

**8b. Indicators and hazards:**
- Player actions: left, right and hazard indicators, with auto-cancel after a
  steering return.
- **Material work:** the lamp material has only Head/Tail/ReverseGlow. It
  needs `IndicatorL/R` parameters and a left/right mask, probably from the
  lamp UVs' X sign. Also check whether every car mesh's `lights` slot covers
  the indicator lamps.
- **AI indicators:** need per-instance custom data on the traffic ISMs.
  Today they only swap whole models for brake lights. Use this to show
  indicators before turns and lane changes.
- **Gotcha:** `UpdateLights` exits early unless the day/night state changes,
  so blinking needs its own path.

**Questions:**
- Should "headlight flash" from AI be included? It needs the same
  per-instance data as 8b.

**Size:** M for 8a, M for 8b. 8b is mostly material and mesh checking, not
code.

---

## 9. Instant replay

**Exists:**
- A fixed 120 Hz sim (`DefaultEngine.ini:37`, `AsyncPhysicsTickComponent`).
- Six camera views (`EODCameraView`).
- Ghost interpolation.
- Per-wheel state in `FODWheel`.
- No ring buffer or replay code.

**Plan:**
- **Record:** a ring buffer of 30 s at 60 Hz, filled on the game thread. To
  stay frame-rate independent, place samples by interpolating between the
  previous and current frame, exactly like the ghost recorder
  (`ODRaceManager.cpp:337-344`). Each frame holds:
  - body transform
  - per wheel: spring length, steer and spin angle
  - rpm, gear, throttle, brake
  - surface per wheel, for audio and dust

  That's about 120 B × 1800 = ~200 KB. Always on, negligible cost.
- **Play:** a `Replay` action pauses the sim by freezing the movement
  component and making the body kinematic, then drives the visuals from the
  buffer.
  - Engine and road audio read the recorded rpm and throttle.
  - Brake lights read the recorded brake.
  - Traffic and weather freeze; recording them isn't worth it.
  - Controls: scrub, ±speed (0.25×–2×), cycle camera, exit. Exit restores the
    exact pre-replay state.
- **Cameras:**
  - The existing views.
  - **Trackside:** pick road points ~80 m apart along the recorded path. The
    camera sits at the roadside, at height, and looks at the car, cutting to
    the next point when the car passes. This uses `NearestRoadPoint` plus an
    offset, and needs a terrain trace for height.
  - **Orbit:** reuse photo mode's camera (#4).
- **Optional:** save a clip as a ghost-format file (#3 header, richer
  frames), and allow photo mode during a replay.

**Questions:**
- Rewind and resume driving (Forza-style rewind), or view-only? Resuming
  needs a full physics-state restore (velocities, wheel omegas, async
  thread). That's much more work and risk, so I'd do view-only for v1.

**Size:** L.

---

## Cross-cutting notes

- Everything is code- and pipeline-only. There are no UMG or editor assets,
  per the editor-agnostic rule. The horn and photo-mode PPV are procedural,
  so there's no licensing exposure.
- Every new feature that runs during a race needs a check against fixed-step
  timing. Traps, landmarks and replay recording all use the
  segment-interpolation pattern, never per-frame `Dt` thresholds.
- Verification for each feature: the pipeline sidecars (#2, #5) get a
  `pipeline/checks/verify_*.py`. The runtime logic (trap crossing,
  ghost file round-trip, replay ring-buffer wrap) gets an `-od<flag>` headless
  self-check that logs pass/fail, so nothing needs a manual play-through to
  prove it.
