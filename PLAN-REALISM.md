# OpenDrive — Realism Plan

*2026-10-05. Covers the six realism gaps found in a 2026-10-05 review: the
world is empty (no traffic, almost no roadside furniture), crash response,
unpaved road surfaces, water and coast, tunnels and stacked crossings, and wet
weather. When a step is done, delete it here and add a line to
[`status.md`](status.md), as for [`next steps.md`](next%20steps.md).*

Each phase is safe to stop after. Phases 0–2 come first because Phase 3
(traffic) uses their output. Phases 4–6 don't depend on anything and can run
in any order, alongside the others.

Every phase follows the process rules in `status.md`. Pipeline work is
verified offline (scripts, plots, `verify_*` gates, plus one small runnable
check per new piece of logic). Runtime C++ is verified by a headless
`Build.bat` compile. In-game judgement is Taylor's: each phase ends with a
**Drive check** line to add to `next steps.md` §2.

All new world content follows standing decision 2. It is generated
deterministically from OSM vectors and rasters into the rebuildable cache,
and nothing per-instance is hand-placed. The art follows decision 1:
stylized, low-poly, intentional.

---

## Decisions needed from Taylor (before the phase that needs them)

| # | Question | Needed by | Default if unanswered |
|---|---|---|---|
| D1 | Crash outcome: a clean stop only, or also stylized cosmetic damage (crumpled panels or a tint, no deformation sim)? | Phase 0 | Clean stop + impact sound, no damage visuals |
| D2 | Unpaved roads get a third grip tier. This is still "on the road", so the two-tier off-road rule (`SurfaceGrip`, 2026-07-20) stands. OK? | Phase 2 | Yes, gravel 0.80 / dirt 0.70 of on-road grip |
| D3 | Traffic in time trials: off, or the same as free roam? | Phase 3 | Off in a race, on in free roam |
| D4 | Traffic density default, and whether the settings get a slider | Phase 3 | Medium, with an Off/Low/Medium/High row in settings |
| D5 | Do traffic cars obey signals/stops strictly, or "mostly" (some run ambers)? | Phase 3 | Strictly. Simpler, and replication-friendly |

---

## Phase 0 — Crash response (small, runtime only)

Problem: hitting a building sends the car tumbling at speeds in the
thousands. The body is a simulated rigid body (`ODVehiclePawn.cpp:47`) with
tire forces from `UODVehicleMovement`. Buildings use merged per-tile
trimesh collision.

1. **Triage first.** Log `BodyInstance` linear and angular velocity for the
   frames around a building hit, behind a `-odcrashtrace` flag (same pattern
   as `-odtrace`). Taylor provides one capture. Then confirm whether the
   spike comes from depenetration (car deep inside a thin trimesh at speed)
   or from the tire model still pushing while the car is airborne or on its
   side. Fix whichever it is. Don't just clamp the symptom.
2. **Likely fixes (pick per the triage):**
   - Set `MaxDepenetrationVelocity` on the car body (a few m/s) and enable
     CCD on the body, so fast hits don't tunnel into the trimesh.
   - In an `OnComponentHit` handler: remove most of the velocity into the
     hit normal (restitution about 0.1), scrub the tangential velocity with a
     friction factor, and cap the angular velocity that the impact causes.
   - Zero the tire forces when fewer than two wheels have contact (Park hold
     already does this).
3. **Feedback:** an impact "crunch" one-shot scaled by the normal speed
   (CC0, normalized like the §5.4 loops) and a small camera shake. Add
   damage visuals only if D1 says so.
4. **Check:** a small C++-free mirror isn't worth it. Instead, extend the
   trace hook so one scripted `-odrev` run into a known building logs the
   peak speed after impact. Pass if it's ≤ the impact speed. (That run is
   Taylor's, per the no-launch rule.)

**Drive check:** hit a building, a guardrail and a wall at 30/60/100 mph.
The car should stop or glance off, never launch.

---

## Phase 1 — Roadside furniture (medium, pipeline + one manager)

Most of the "empty world" feel goes away without any AI. Phase 3 also uses
the signal and stop data to control junctions.

1. **Fetch.** Add a node query to `fetch_osm_network.py` (same Overpass
   call, cached per §7.50): `highway=traffic_signals|stop|give_way|crossing|street_lamp`
   and `railway=level_crossing`. Also keep `maxspeed` on ways in the station
   metadata (it rides next to `lanes`/`surface`/`layer` in `way_tag_meta`).
2. **Place (`build_road_furniture.py`).**
   - *OSM tier:* snap each node to its nearest station. A signal/stop gets a
     pole on the right edge facing traffic on every approach (each approach
     direction comes from the station heading and `oneway`). A stop also
     gets a stop bar (a lane-marking flag in ODR3, like `junction_suppress`).
   - *Heuristic tier:*
     - speed-limit signs where `maxspeed` changes, else at segment starts
       from the class default;
     - curve chevrons where the station curvature crosses a threshold
       (radius < 150 m at the class speed);
     - street lights every ~35 m on both sides inside settlements
       (`classify_settlements.py`), and on highway ramps;
     - delineator posts on rural roads where there's no guardrail.
   - Offsets use `half_width_for_stations` + the bank (as guardrails do), so
     props sit on the banked edge, not in the lane.
3. **Export** `ODF1` per tile: type id + transform, with a generation_id
   header like the other sidecars.
4. **Runtime** `AODFurnitureManager`: one HISM per prop type, tiled
   streaming copied from `AODGuardrailManager`. Simple box collision on
   poles. Street lights turn emissive with the headlights (sun < 8°). Join
   the multi-set streaming (`SpawnAreaSet`).
5. **Meshes:** `make_furniture_meshes.py` makes procedural low-poly signs,
   poles, lamps and chevrons, like `make_rock_meshes.py`.
6. **Checks:** a `verify_furniture_fit.py` gate checks no prop is inside a
   lane, every prop base is within 0.2 m of terrain/edge, and signals sit at
   ≤ 15 m from their node. Plus `tests/test_furniture_place.py` for the
   approach-direction and curvature rules.
7. **After that (Phase 1b):** sidewalks in settlements (a curb strip from the
   road edge, part of the road mesh) and `highway=service` driveway stubs,
   limited to the first 30 m off the road for perf.

**Drive check:** a town (Frisco/Breck) day and night, and one rural pass.
Do the signs and lights read as stylized, and is the density right?

---

## Phase 2 — Unpaved road surfaces (small)

`surface` already ships per station (`build_road_stations.py:79`) and nothing
uses it.

1. Map `surface` to three classes: paved / gravel (`gravel`, `fine_gravel`,
   `compacted`, `unpaved`) / dirt (`dirt`, `ground`, `earth`, `mud`, `sand`).
   Untagged `track`-like classes default to gravel, everything else to paved.
2. `build_road_network.py`: split chunks on a class change, like the
   `oneway` chunk flag, and write the class in the chunk header.
   `AODRoadManager` picks `M_Road_Gravel` / `M_Road_Dirt`, with no lane
   markings and their own physmats `PM_Gravel` / `PM_Dirt`.
3. `SurfaceGrip`: add the third tier per D2. Weather wetness lowers unpaved
   grip more than asphalt (mud).
4. `UODRoadNoiseSynth`: a gravel crunch layer keyed on the same physmat.
5. **Check:** print a per-area count of km per class, plus a test of the tag
   → class mapping.

**Drive check:** a gravel road in Summit dry and in rain. Does the grip step
feel fair, and does the sound sell it?

---

## Phase 3 — Ambient traffic (large)

### 3a. Directed lane graph (pipeline)
- Extend `build_route_graph` in `export_road_centerline_bin.py`, or add a
  new `ODN1` sidecar, with directed edges: `oneway`, lane count, speed
  (`maxspeed` or class default), and a polyline with Z from the shipped
  road surface.
- The same directed data fixes the nav one-way bug (`next steps.md` §3).
  Point A* at it so the two can't drift apart.
- Junction control per node: signal / stop / give-way from Phase 1, else
  priority by highway class.
- **Check:** extend `pipeline/checks/verify_route_graph.py` to check that
  every directed edge has a successor (or is a real dead end), there are no
  wrong-way edges on ramps, and the main component covers ≥ 98% of the km
  (this also covers the Blue River/Jefferson split).

### 3b. Traffic runtime (`AODTrafficManager`)
- **Agents are kinematic, not simulated:** each follows its lane polyline
  at an arc-length position, with Z from the polyline. That's cheap, has no
  physics jitter, and stays deterministic.
- **Behaviour:**
  - IDM car-following, with the player car as a possible leader;
  - lane choice only at junctions (no lane changes in v1);
  - gap acceptance at stop/give-way;
  - fixed-cycle signal phases;
  - a speed limit plus a per-agent factor (0.9–1.1).
- **Spawning:**
  - a ring 400–1500 m from the player, out of view, and despawn behind;
  - density by highway class × settlement × hour (fewer at night);
  - seeded per edge and time bucket (decision 4).
- **Collision:** a kinematic box with a hit on contact with the player. On
  contact the agent becomes simulated physics and goes through Phase 0's
  crash handling, then despawns after it settles. This is why Phase 0 comes
  first.
- **Perf budget:** ≤ 1 ms/frame for 60 agents. Agents past 600 m tick at
  2 Hz. Mark it `ponytail:` if it starts as an O(N²) leader search.
- **Multi-set:** one manager for the world that reads the nearest area's
  graph, like HUD nav.

### 3c. Look and sound
- The five car meshes with a random paint parameter (shared with 4.3
  cosmetics), headlights at night and brake lights from IDM deceleration.
- Pass-by audio: the closest 4 agents get a cheap engine synth instance,
  and the rest are silent.

### 3d. Settings and modes
- Per D3/D4: a traffic row in settings (saved under `OpenDrive.Graphics`),
  and the race manager suppresses spawns on a stage.

- **Check:** `tests/test_idm.py` (stop behind a stopped leader with no
  overlap, reach the speed limit on a free road) and a Python mirror of the
  junction gap rule.

**Drive check:** free roam through Frisco and on I-70. Look for density,
pop-in, junction deadlocks and how traffic reacts to the player.

---

## Phase 4 — Water and coast (medium; needed for the coast ranges and Hawaii in scope)

1. **Coastline:** stitch `natural=coastline` ways into closed land rings,
   clipped to the area bbox.
2. **Sea plane:** world members share datum 0 (sea level, 2026-10-05), so a
   Tier 0 sea plane is a flat Z=0 mesh outside the land rings, with the
   existing water material. Bake the offshore tiles that are skipped today.
3. **Lake islands:** subtract the `inner` members of lake multipolygons.
4. **Culverts:** at unbridged ribbon crossings (gap-clipped today), keep the
   ribbon and add a culvert mouth mesh on each side of the embankment.
5. **Oahu water tiles,** so hurricane rough water can show there.
6. **Check:** a test for ring stitching on a synthetic broken coastline,
   plus a `verify_*` count of tiles with water and of any lake polygon that
   still covers an island.

**Drive check:** Oahu coast road, and a lake with an island.

---

## Phase 5 — Tunnels and stacked crossings (medium–large)

1. **Tunnel bore:** pavement through the tunnel, along the real `elev_m`
   the stations already keep (`build_road_network.py:26`). Add an extruded
   stylized arch with full collision, dark interior lighting and portal
   faces. The terrain needs no hole, because the bore runs below it and the
   portal faces cover the seam. Check the carve stop at the portal still
   leaves an opening, and lower the terrain there if it doesn't.
2. **Stacked crossings:** use `layer` in the elevation-conflict detection
   (`next steps.md` §4, OSM tag consumers). Grade-separated crossings with
   no structure get a bridge from the existing ODB1 path.
3. **I-70 East Portal:** split the two benched carriageways into separate
   carve corridors.
4. **Check:** `verify_road_fit` covers tunnel stations (pavement continuous
   portal to portal), plus a count of stacked crossings without a structure
   (target 0).

**Drive check:** Eisenhower/Johnson tunnel and one interchange.

---

## Phase 6 — Wet weather (small–medium)

1. A global wetness parameter (already used for the road) also drives the
   terrain and foliage materials (darker, glossier).
2. Puddles: a deterministic noise mask × the road profile's sags (low
   points between stations), only when wetness > 0.6.
3. Rain streaks follow the car's velocity.
4. Lightning: a bolt mesh/flash plus a cloud flash, timed to the existing
   thunder.
5. The Dragon's road gets wetness (it isn't an `AODRoadManager` mesh, so it
   needs the parameter on its material).

**Drive check:** a rain → clear cycle on Summit at night.

---

## Rough size and order

| Phase | Size | Depends on |
|---|---|---|
| 0 Crash | S | — |
| 1 Furniture | M | — |
| 2 Surfaces | S | — |
| 3 Traffic | L | 0, 1 (junction control), nav one-way via 3a |
| 4 Water/coast | M | — |
| 5 Tunnels/stacks | M–L | — |
| 6 Wet weather | S–M | — |

Recommended order: 0 → 2 → 1 → 3, with 4/5/6 slotted in wherever a
rebuild is already happening. Phases 1, 2, 4 and 5 all need a full
`rebuild_area.py` run, so batch them to share rebuilds.
