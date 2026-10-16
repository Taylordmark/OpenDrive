# OpenDrive — Next Steps

*This file holds only open work, as of 2026-09-29. What's built, the standing
decisions and the process rules are in [`status.md`](status.md). The old
long-form tracker (root causes, dead ends, measurements) is in
`git show dd050f4:"next steps.md"`. §-numbers refer to sections there and in
`status.md`.*

When something here is finished, delete it from this file and add a line to
`status.md`.

---

Realism work (traffic, roadside furniture, crash response, unpaved surfaces,
water/coast, tunnels, wet weather) is phased in
[`PLAN-REALISM.md`](PLAN-REALISM.md).

## 1. Now: road/terrain fit follow-ups (from §7.43–7.47)

- **Dennison Road hairpin (Taylor to judge in a drive).** Station 184480 at
  (−10734, 1756) m is not a float. The hairpin is about 6 m in radius at 22%,
  so its two pavements sit 8 m apart and 4 m apart vertically, inside one
  7.8 m terrain cell. The terrain under the upper pavement drops 4–6 m.
  - `verify_terrain_fit` accepts it (`HAIRPIN_*`, 2026-10-01). Only 7
    tight-turn stations map-wide are over 2.8 m.
  - If it shows in the drive, the fix is to flatten the grade through tight
    hairpins. That changes every switchback's feel and needs a full rebuild.
    Walls were rejected: they have BlockAll collision next to the lane.
- **Road float after the §7.48 ceiling.** 11,699 near-tile stations float
  more than 0.7 m (3,291 over 2 m). This is the price of 0 buried stations.
  If it shows in drive-tests, the next step is the 513 near grid (3.9 m cells).
  Example: US 6's west edge near Tennis Club Rd shows −0.2 to +1.5 m of
  skirt along its length (worst at (17088, 9005)).
- **Junction pad leftovers** (§7.51):
  - 30 Summit pads read more than 1 m off grade; 29 are stacked
    crossings where the probe hits the lifted deck above, not a pad
    fault. Was 108 before the median flatten and carriageway fix.
  - SF steep-street tabling: 8 station steps of 40-46% where Jones,
    Filbert and Hyde (22-31%) flatten into a level junction box. A longer
    blend can't fix it (peak is ~1.3-1.9x the street grade); tilting the
    box along the steep street would. Judge in a drive first.
  - Summit pad kinks 774 (603 in the lane interior) / steps 1319
    (holes 1). The 400 gate-coverage caps added ~170 kinks.
  - 15 Summit pads are pinned at the 18% tilt cap (`PAD_TILT_MAX_GRADE`,
    was 12% with 79 pinned).
  - SF pad kinks (2026-10-08): 55 total; ~14 are the 0.5-1.5 m edge lip
    every ribbon has. 41 sit in the lane interior (>= 3 m from unpaved),
    nearly all on one crease on Leavenworth between Columbus
    (8 m) and North Point (12.7 m): two flattened junctions 25 m apart
    with 4.5 m between them. The rest: Bay St / Columbus at (136, 203)
    (roads 4.4 m apart at the centre, up to 0.58 m) and a 0.43 m crease at
    Filbert's 31% rim (102, -341). Same family as the steep-street item:
    close or steep junctions need pads shaped along the street, not
    level boxes.
- **Overpass leftovers** (status: Overpasses lifted):
  - Columbine Drive (layer -1) under I-70 is skipped: the upper road
    isn't a bridge in OSM, so nothing is lifted. Cut the lower road, or
    trust the layer tag and build a deck.
  - Lifted decks have no piers; a median pier needs the lower road's
    footprint.
  - 7 station steps of 12-13% where a ramp meets an existing grade.
- **Seam-failure investigation.** Carried over. Not started.
- **Tune the `ROAD_CLASS_GRADE` / `FOLLOW_*` constants** by drive-test. The
  current values are first guesses.

## 2. Taylor's live checks (offline gates pass; nothing below has been seen in-game)

Group these into a few drive sessions. Each line says what to judge.

**Scripted session, 2026-10-06** (Frisco and a Summit gravel road, logs and
window captures only, so no feel or sound):
- Crash runs:
  - 17 mph into a wall: PASS (solver 256 → out 72);
  - 43 mph into a building: stopped dead (solver 708 → out 189), no launch;
  - 88 mph clipped a pole box: glanced off at 2974 cm/s;
  - 11 mph into a pole: stuck, PASS.
- Furniture poles collide.
- Gravel reads PM_Gravel, with 0.4–0.9 wheel slip at 30% throttle.
- Blizzard at 30 cm: white road with tire ruts, roadside snow, snowy trees,
  flakes on the lens. Flakes are sparse and large; judge whether that's right.
- Night rain: lamp heads and windows glow, headlights on, rain streaks and
  lens drops. Lamps light no pool on the road (emissive only).

Fixed in that session:
- spawn trace too short on Summit, car floated at 6 km;
- night "multiple directional lights" warning, sun now has priority 1;
- a scripted `-odsetv` opened a crash window and caused a false FAIL.

Still needs a driver: crash thump/shake, guardrail hits, the grip step and
gravel crunch, and the snow/wet grip feel.

- **Vehicle:**
  - the assist stack plus gearbox/ABS. This gates the stage-3 preset retune;
  - stage-2 tire feel (MuPeak, SlideFrac, PeakSlip*, per-preset grip
    scales);
  - weather grip and wind (§7.41).
- **Roads:**
  - the §7.47 profile on Summit;
  - the Dragon, rebuilt on `follow_ground_profile`;
  - retaining walls up close (§7.12, never driven);
  - junctions reading as flush, with lane markings stopping at the box
    (Blue River Pkwy × 3rd St);
  - embankment material, LOD and the no-collision feel (§7.19);
  - bridge deck and pier look (§7.21);
  - Swan Mtn guardrail posts (§7.16).
- **Terrain look:**
  - V2 palette;
  - V3 macro noise (watch for over-saturated yellow ridges);
  - V4 haze. Try 0.7, then Mie 0.009 → 0.007;
  - V5 aspen and treeline bands. Use a dedicated aspen mesh if the tint
    reads badly.
  - The V3 HLSL has only been built with `-nullrhi`, so it has not had a real
    shader compile yet.
- **Water:**
  - the ribbon shore blend (the lakes' 35 m blend now also applies to narrow
    ribbons);
  - unbridged crossing gaps.
- **Seasons (§7.17):** the deciduous fall look, leaf drop, twig snow and
  pacing.
  - Flags: `-odseason=fall -odseasonphase=0.25/0.75`, `-odseasonrate=20`.
  - Vantage: `-odspawnx=733226 -odspawny=-807394 -odgo=5`.
- **Weather:**
  - cloud and overcast values, rain streaks (§7.40);
  - on-lens rain/snow (2026-10-06): drops land and drift off the edges
    faster with speed, flakes stick then melt to a smear, and snowfall no
    longer twists when the car rolls/pitches. Judge size, count, drift pace
    and the refraction look (`Weather|Lens` knobs, `RefractionIOR` on
    `M_LensDrop`). Pick Rain/HeavyRain/Snow/Blizzard in the garage Weather
    row, or console `od.Weather Snow` (`od.Weather Auto` to release).
  - snow accumulation (2026-10-06): `od.Weather Blizzard`, then watch the
    road stay wet ~1.5 min before it whitens, with darker tire tracks per
    lane and berms rising at both edges (should stop at junctions). Jump
    straight to depth with `-odsnowdepth=30` or `od.Weather Depth 5/15/30`
    (`Auto` releases); `-odsnowrate=10` speeds the curve. Judge the
    slush/white colors, track spacing, the berm shape and lighting (WPO
    keeps the lip's old normal), terrain/foliage whitening, and how grip
    (slush 0.60, packed 0.45) and the deep-snow drag feel.
  - wet weather (2026-10-09): Summit at night, `od.Weather Thunderstorm`,
    then let it clear. Judge the puddles in road dips (they need wetness
    > 0.6 and fade as it dries), the darker ground and trees, and each
    strike's bolt plus the blue flash on the clouds and terrain (near
    strikes are brighter and wider). `od.Weather Auto` releases.
  - drive-feedback fixes (2026-10-09): no trees on the I-70 Wheeler Junction
    pads; grass now reaches 120 m and thins out from 60 m instead of
    popping in; grass, scrub, bushes and the other non-conifer trees go
    white in snow and dark in rain. Deals Gap is in Scene Select next to
    the old Dragon. Wheels should sit still on smooth road at any frame
    rate; Summit lakes should meet their banks with no dark ring; the log
    should have no `BuildTile ... exceeded 8 ms` warnings and driving
    shouldn't rubber-band when Near tiles stream in. Scene menu: Season
    sits above Weather, and the Weather row only cycles that season's
    weather (Summit winter: Clear, Overcast, Snow, Blizzard, Fog).
  - drive feedback round 2 (2026-10-09): Summit trees and boulders should
    stop the car (pebbles under 15 cm still drive over); grass should
    reach further ahead at speed; pause -> Main Menu should land in the
    garage room. Night/Dusk: headlights should be nearly the only light,
    with a faint moonlit floor, a moon disk and stars that hide behind
    clouds and fog. Tune `NightMinEV`, `StarBrightness`, `MoonIntensity`
    on the DayNight controller. Check the log for `M_NightSky` shader
    errors (built with -nullrhi, never compiled for a GPU yet).
  - car lamps (2026-10-09): head and tail lenses glow with the headlights
    (menu vista at Dusk/Night too), brake lights on the brake pedal (the
    throttle in reverse) day or night, white reverse lenses in reverse gear.
    The headlights should now visibly light the road (they were ~7 cd);
    a rear spot washes the road red on braking, white reversing. Tune
    `Headlight/Tail/Brake/ReverseLightLm` and `Head/Tail/BrakelampGlow`.
    High/Ultra cars are imported now, clear coat skipped (UE 5.8 Python
    has no CustomData pins) - check their look before relying on them.
  - fixed 120 Hz physics (2026-10-09): handling, braking, crashes and the
    spawn settle should feel the same at any frame rate. Watch for: a
    one-step (8 ms) visual lag, crash bounces, the parking brake now
    holding at 0 mph, wheels still at a stop and spinning true, the car
    no longer jittering (p.AsyncPhysicsBlockMode=1), and holding S braking
    as hard at 10 mph as at 60 (slip-target ABS). If the car beaches again
    off road, run with -odcrashtrace and read the ODBelly lines. Backup
    lights flickering in the first seconds of a launch (LMP) is still
    open, render side.
  - the tornado funnel, debris and vortex numbers;
  - hurricane gusts (§7.42);
  - the garage season row (§7.38).
- **Audio:**
  - the 5.1/5.1v2 synth character and the lift-off flare fix;
  - exhaust pop character and slider steps (§7.30);
  - ambient town_hum level, lake/creek balance and loop seams
    (§7.28/7.29).
- **UI:**
  - the settings panel layout, credits pace and whether the scalability
    presets differ (§7.31);
  - the 25-row rebind panel, and gamepad capture on hardware (§7.32);
  - world map controls, false turn prompts at interchanges, reroute
    thresholds, fast-travel landing (§7.37);
  - one-way routing: routes on divided highways and ramps should follow
    the right carriageway (e.g. Frisco→Breckenridge via CO 9) (§7.37);
  - lane graph start snap (PLAN-REALISM 3a, never driven): on I-70 or
    CO 9's divided stretch, pick a destination behind you. The route should
    start on your own carriageway and turn at the next exit, with no U-turn
    across the median. Also check Blue River routing (it's joined now) and
    that a pick has no hitch. The log should read `route graph ... (one-way
    yes, lanes yes)`;
  - garage picks (car, engine, drivetrain, tuning, area) survive a restart;
  - `-odspawnx/y` spawns facing along the road.
  - mini-map: roads and the route line up through hairpins; the Travel
    (heading-up) mode reads well; route pick/reroute has no hitch on the
    4x denser graph.
- **Art:**
  - the overall Crew 2 pass verdict;
  - the Super car's rear-wing read;
  - the parking asphalt tone.
- **Crash response** (PLAN-REALISM.md Phase 0, code in, never driven): hit
  a building, a guardrail and a wall at 30/60/100 mph. The car should stop
  or glance off, never launch; judge the thump and shake too. Run with
  `-odcrashtrace` and send one log: every hit should end in an
  `ODCrashTrace: SUMMARY ... PASS`, and the `HIT` lines' `solver=` vs `pre=`
  speeds say whether depenetration was the spike.
- **Unpaved roads** (PLAN-REALISM.md Phase 2, code in, never driven; after
  the combined rebuild): a gravel road in Summit (e.g. Boreas Pass Rd),
  dry and in rain (`od.Weather Rain`). Judge whether the grip step feels
  fair (street: gravel 0.80, dirt 0.70, wet 0.58/0.42; knobs
  `GravelGrip`/`DirtGrip`/`GravelWetLoss`/`DirtWetLoss`), whether the
  crunch sells it (`CrunchRateHz`/`CrunchGain`), and the gravel/dirt look
  wet and under snow. Note: wet dirt grips less than the verge (off-road
  ignores weather by design).
- **Roadside furniture** (PLAN-REALISM.md Phase 1, code in, never driven;
  after the combined rebuild): a town (Frisco Main St, the CO 9 × Main St
  signals, Breck) by day and at night (`-odhour=21`), plus one rural pass
  (Swan Mtn / Boreas). Judge whether the signs, signals and lights read as
  stylized and the density is right; that chevrons point into the curve (a
  mirror bug would show here first); that poles collide and delineators
  don't; and that lamps glow when the headlights come on. The log line
  `ODFurnitureManager: tiles=... props=... lamps=on/off` confirms
  streaming. Speed signs carry no digits yet.
- **Ground detail** (2026-10-07, seen in parked screenshots only): drive Summit rural (Swan Mtn, Boreas) and a town street.
  - Grass ring: tufts should fill out to ~60 m without visible pop-in at the
    edge, stay 3.5 m clear of the pavement, and skip pads, bridges and
    lakes. Judge density per biome (`GrassPerSqM`, `GrassBiomeScale`) and
    frame time (`bEnableGrass` off to compare).
  - Ground patches: dry-straw and bare-dirt patches should break up the
    meadow without looking like camo (`PATCH_*` in
    `apply_ground_material.py`).
  - Shoulder: a gravel band along every road edge, wider and narrower
    along the road, with no seam or jump as the car drives (the texture
    re-centers every 32 m) and no band on cliffs.
- **Road-shaped junction pads** (2026-10-07, Summit; checked in plots
  only): drive through T, Y and 4-way junctions in Frisco/Silverthorne.
  The pad should follow the road arms with rounded 8 m corners, and grass
  should show between the arms. Watch for lips or steps where a pad edge
  now crosses terrain, and for pad patches where the snap moved a road
  (the pad also covers the road's pre-snap line).

## 3. Gameplay and UX

- **Stage 3 vehicle work:**
  - retune the five presets on the new physics;
  - make drift a progressive rear breakaway;
  - add transmission modes (DCT/PDK/manual/upgraded auto) with shift-cut
    feel, stored as preset/garage data. This is the same item as the 5.1v2
    transmission note.
- **Race:**
  - a standing-start countdown;
  - full input recording for ghosts (they store transforms only today).
- **Navigation (§7.37):**
  - one-way leftovers (after PLAN-REALISM 3a): 5 sink / 6 source ends are
    left, all ramps into rest areas or lots that weren't fetched (their OSM
    way has no other fetched way at its end), plus Post Blvd outside the
    bbox;
  - Jefferson is still off the main component. Its piece joins via US 285
    outside the bbox; the fix is a wider fetch. 29.6 km of bbox-leaving
    pieces and a few track-only islands stay off it too;
  - the ~300 dropped short connectors are in the graph without pavement.
    The root fix is a lower `MIN_SEGMENT_STATIONS`, which needs a full
    rebuild;
  - nearest-node lookup is O(N), marked `ponytail:`.
- **World game layer (§7.4):**
  - free-roam ↔ stage entry flow;
  - stage discovery (drive to a famous road to unlock its time trial);
  - a cache-management UI. This one is a release requirement.
- **Night sky:** optional "real-time accurate" sun and moon phases (moon and stars done 2026-10-09).
- **Change notifications (Taylor, 2026-10-13):** a small toast or line of
  text when the **area**, **weather** or **road** changes. Today nothing on
  the driving HUD shows any of them. The only place name is on the
  full-screen map (`PlaceLabelText`, `ODRaceHUD.cpp`), and the weather and
  current road are never shown. Skip any of the three that later gets a
  permanent on-screen readout. Sources: AODGameMode area streaming,
  AODWeatherController state changes, and the nearest named road from
  `labels_<area>`.

## 4. World pipeline

- **California-scale world (agreed order, 2026-10-02):**
  1. ~~World grid: an LCC projection replacing the equirectangular frame,
     cut into 64 km chunks, each with its own local origin.~~ Done
     2026-10-05 (`world_frame.py`). Caveats:
     - the sidecar formats still carry one origin per area; per-chunk
       origins come with the chunked bake (step 3);
     - the vertical datum is still per area (`ORIGIN_HEIGHT_M`).
  2. Runtime chunk streaming (load/unload chunks around the car).
  3. Two-pass chunked bake, so a chunk's edges match its neighbours'.
  4. Horizon culling of distant tiles.
  - Also: coarse terrain tiles sit ~9 m below roads in the corridor
    (`verify_road_fit` coarse poke median −5.9 m on Summit).
  - Far-corner sky still to check in a drive.
- **Multi-zone `test_areas.py` refactor (overdue).** Turn the module
  constants (`AREA_NAME`, `BBOX_SNWE`, `ORIGIN_HEIGHT_M`) into a table keyed
  by zone, and make every script take the zone as an argument. Required
  before a second hot zone exists. No zone priority order has been chosen yet.
- **Alaska and high latitude:**
  - Mercator tile counts grow as 1/cos²(lat): 4.3× at 63°N;
  - the old equirectangular frame had 6.9% edge error over ±2°; the
    per-area LCC (2026-10-05) is conformal, so only its scale varies;
  - so keep Alaskan zones short in latitude.
- **§7.0 step 4, landmarks:** about 1,000 named OSM structures filtered by
  prominence along driving routes. Authored or kitbashed assets placed at
  their real lat/lon, loaded only near the route.
- **§7.0 step 5:** full integration with offline fit/overlap gates, then
  scale out past a single test area.
- **Populated archetypes:** only American Town exists. Still to build:
  - European Village;
  - City (real street grid plus extruded buildings);
  - Rural Hamlet;
  - Industrial/Port;
  - Suburban Sprawl.
- **OSM tag consumers.** `lanes`, `surface` and `layer` ship per station, and
  lanes already drive the width and surface the unpaved tier (2026-10-06).
  Still to build: `layer` → `detect_elevation_conflicts()`.
- **Retaining walls, Phase 2:** cross-chain and different-name conflicts
  (freeway ramps and interchanges) are excluded from the carve and get no
  wall.
- **Bridges and tunnels:**
  - I-70 East Portal is still up to about 30 m off the DEM (two benched
    carriageways on one heightmap);
  - the bridge-adjacent junction jump (up to 34 m if forced), so bridge
    endpoints are excluded today;
  - tunnels stop at the portals, and a real bore mesh is future work;
  - rename the misleading `is_bridge` station field (it is really
    carve-exclusion).
- **Water:**
  - ocean and coastline (`natural=coastline` ring stitching; offshore tiles
    are never baked);
  - a Tier 0 sea plane;
  - lake inner/hole members (islands) are not subtracted;
  - the Blue River doesn't chain into one piece;
  - `offset_polyline()` has no miter clamp;
  - basic confluence handling;
  - no culverts;
  - Oahu has no water tiles, so the hurricane rough water has nowhere to
    show.
- **Biomes:**
  - swap the source to ESA WorldCover 10 m;
  - regional flora recipes;
  - a PRISM/WorldClim moisture raster;
  - Oahu, Skagit, Palmdale and the Dragon have no ODB2 or `road_biomes.json`
    (neutral fallback);
  - re-run `build_ambient_zones.py` to pick up the V1 grid samples;
  - `BlendAt` does a linear 81k-point scan per forest tile (`ponytail:`).
- **Forest:**
  - seasonal material instances for olive, yucca, canyon oak, manzanita and
    scrub oak (no canopy snow today);
  - give `tree_scrub_oak` branches (`build_aspen(..., with_branches=True)`)
    or leave it out of the deciduous arc.
- **Sidewalks, and `highway=service` driveways.** Service ways are excluded
  by `HIGHWAY_CLASS_REGEX` (perf risk).
- **Parking stripes** use a world-axis period instead of each lot's own
  heading.
- **Seasonal ambient audio:** `SetTimeOfDayHint()` and `SetSeasonHint()` are
  no-ops (`ODAmbientSoundManager.h:227-228`).
- **Label gaps:** county labels need admin-boundary polygons (scoped out by
  Taylor); the Dragon has no labels.
- **Dragon stitching**, which also needs the `LoadMask()`
  first-`ALandscapeProxy` assumption fixed.

## 5. Weather and atmosphere

- W2 gaps (puddles, wet ground/trees, Dragon road wetness and the lightning
  bolt + flash light landed 2026-10-09):
  - no separate dust hiss;
  - wetness skips static-material flora (olive, yucca, oaks, shrubs, grass
    cards), rocks, walls and the old Dragon's Landscape (`M_Terrain`);
  - no puddles on the old Dragon (its OBJ road has no sag UV3);
  - `M_Road_OneWay` slots aren't MIDs, so they stay dry;
  - the engine cloud material's own lightning inputs are unused.
- W4/W5 gaps:
  - the funnel doesn't visibly spin (needs a panning-noise material);
  - vortex wind acts sideways only;
  - rain ignores the vortex;
  - no tornado audio, no storm surge (hail dropped, Taylor 2026-10-09);
  - the HUD banner isn't text-measured.

## 6. Performance and stability

- Cold-start streaming latency after a teleport can take up to 20 s. The
  loading screen hides it. Check whether `FinalizeTile` runs unbudgeted on
  the game thread.
- Re-measure streaming frame drops with a frame-time capture on a scripted
  drive.
- The Breckenridge spawn (X=1126119 Y=471996) has no road collision
  underneath. The nudge hides it. The suspected cause is the lake-depression
  terrain work.
- Physmat plumbing: historical traces read `DefaultPhysicalMaterial`.
  Investigate if surface grip feels wrong.
- VSM "Non-Nanite Marking Job Queue overflow" warning: either Nanite-ize the
  heavy HISM sets or lower `r.Shadow.Virtual.ResolutionLodBias*`. Low
  priority.
- A shared tile-streamer base class for the copy-pasted manager Tick/dispatch
  logic (AUDIT §6.3).
- Terrain storage: vertex retention is 22–44%. The fallback is
  `NEAR_GRID_N = 129`.
- DLSS/upscalers: Off/TSR only today. DLSS is deferred.
- Performance budget: parked by Taylor (it was 60 fps on a mid-range GPU at
  1080p).

## 7. Release

- Packaging: an installer or zip, and a test on a fresh machine.
- Check whether the AWS `elevation-tiles-prod` terms need attribution beyond
  USGS public domain.
- Cache-management UI (see §3).

## 8. Larger arcs, not started

- **Arc 6, wheel and sim rig:**
  - enable RawInput;
  - Logitech and Fanatec mappings;
  - wheel axis with a linearity setting;
  - pedals and an H-shifter;
  - per-device profiles in `GameUserSettings.ini`.
  - Acceptance: fully playable on a wheel.
  - Force feedback comes after v1.
  - The deadzone/sensitivity hook from §7.32 is where to start.
- **5.3, simulated engine sample banks:**
  - render an RPM × load sample grid offline per engine config (Engine
    Simulator);
  - granular/crossfade playback behind the synth interface;
  - the 5.1 synth stays as the fallback.
- **Optional:** a scripted whole-town review drive (pure pursuit on the
  §7.37 graph).
