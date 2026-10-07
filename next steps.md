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
- **Junction-flatten steps.** About 45 steps of 2–3 m sit near segment ends.
  They predate §7.47. Re-measure them after §7.49, which added 1,276
  junctions.
- **Junction pads are a manual step** (§7.51). `experimental_junction_pads.py`
  is not in `rebuild_area.py` STAGES, so any rebuild wipes the pads until it
  is rerun (about 15 min).
- **Junction pad leftovers** (§7.51):
  - 108 pads still sit more than 1 m off their road's grade (worst 4.3 m at
    (24639, 17570)).
  - About 10% of pads are pinned at the 12% tilt cap
    (`PAD_TILT_MAX_GRADE`).
  - 9 road ends are less paved than before the pads, e.g. (-27544, 11341).
  - SF (`OD_AREA=sf_russian_hill`) pads 0 and 5 keep about 20 kinks each:
    the ribbon leaves the molded rim up to 0.68 m high on steep downhill arms.
  - The pad apron/conform/lower pass (status.md "Pad edges at road level")
    has run on SF and Summit (2026-10-06). Other areas still have the old
    lipped pads until the pads script is rerun.
  - SF pad outliers after conform: (136, 201) is ~2.8 m over a lower road
    (a stacked crossing, see bridges below); (16, 274) 0.92 m and
    (133, 217) 0.58 m at the edge; inside-pad jumps up to 0.76 m at
    (65, −133).
  - Poles option B: 18 SF poles stand off the road where a pad slid it
    sideways, and 24 stop bars are dropped. Walk each back toward its node
    until it clears the shipped pavement.
- **Re-check the downstream systems against the new elevations:**
  - `WALL_MIN_GRADE` and the conflict detection (§7.12);
  - the Frisco North 6th Ave dead-end float, 1–2 m (§7.14);
  - the US 6 / Tennis Club Rd skirt step.
- **Bridges for stacked crossings.** Grade-separated crossings that don't
  have a structure yet.
- **Rubber-banding: jitter and pull.** Carried over from the last drive test.
  Not fixed yet.
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

## 3. Gameplay and UX

- **4.3 cosmetics v1:** paint color and wraps. Acceptance: 4–6 cars, color
  choice and preset choice, all persisted.
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
- **Night sky:** moon and stars. Optional "real-time accurate" sun.

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
- **Junction poles vs pads, option B (2026-10-06).** `build_road_furniture`
  now places control poles and stop bars on the ORIGINAL station line
  (option A): pads slide approach stations ~10 m along the road, which put
  them inside the crossing arm (sf_russian_hill: signals 33 → 51, stops
  52 → 84). Where a pad also slid the road sideways (skewed arms, bends)
  18 poles stand 3+ m off the road on terrain and 24 stop bars are dropped
  as off the pavement. Option B: walk back from the node along the approach
  until the spot clears the shipped pavement, so the pole sits at the pad's
  real edge.
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

- W2 gaps:
  - puddle mask, wet terrain and wet foliage;
  - the Dragon's road gets no wetness (it isn't an `AODRoadManager` mesh);
  - no separate dust hiss;
  - lightning has no bolt or cloud flash.
- W4/W5 gaps:
  - the funnel doesn't visibly spin (needs a panning-noise material);
  - vortex wind acts sideways only;
  - rain ignores the vortex;
  - no hail, no tornado audio, no storm surge;
  - the HUD banner isn't text-measured.

## 6. Performance and stability

- A periodic hitch of about 25 s dominates every perf run. Not investigated.
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
