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
realistic, matched against how the real place looks.

---

## Decisions (Taylor, 2026-10-05)

| # | Question | Decision |
|---|---|---|
| D1 | Crash outcome | **A clean stop for now.** Impact sound and camera shake, no damage visuals. |
| D2 | A third grip level for unpaved roads | **Yes.** The two-tier off-road rule (`SurfaceGrip`, 2026-07-20) stays: unpaved roads are still "on the road". |
| D3 | Traffic in time trials | **Off in a race, on in free roam.** |
| D4 | Traffic density | **Varies by population and time of day, realistically.** Hitting I-5 at rush hour should mean being stuck in traffic. No fixed density level. |
| D5 | Signal/stop compliance | **Strict for now.** |

---

## Phase 0 — Crash response (small, runtime only)

Problem: hitting a building sends the car tumbling at speeds in the
thousands. The body is a simulated rigid body (`ODVehiclePawn.cpp:47`) with
tire forces from `UODVehicleMovement`. Buildings use merged per-tile
trimesh collision.

The code is in (2026-10-06, see `status.md`): the `-odcrashtrace` hook,
CCD and a depenetration cap, the `OnBodyHit` wall response, tire forces zeroed
below two contacts, a synth thump and a camera shake. What remains:

1. **Capture.** Taylor sends one `-odcrashtrace` log. Its `HIT` lines'
   `solver=` vs `pre=` speeds confirm that depenetration was the spike. Every
   `SUMMARY` should read PASS.
2. **Crunch sample (gap).** There's no CC0 crash sample in `content_src/audio`,
   so the road synth's thump (`UODRoadNoiseSynth::TriggerImpact`) stands in.
   Swap one in, normalized like the §5.4 loops, if the thump reads too soft.

**Drive check:** hit a building, a guardrail and a wall at 30/60/100 mph.
The car should stop or glance off, never launch.

---

## Phase 1 — Roadside furniture (medium, pipeline + one manager)

Steps 1–6 built 2026-10-06 (see `status.md`). They need the combined
`rebuild_area.py` run (and a furniture re-run after the manual
`experimental_junction_pads.py` step) before the drive check below.
Phase 3 can read junction control from `road_furniture.json`'s `controls`
list (one entry per signal/stop/give-way node, with its approaches).

Phase 1b (sidewalks + driveway stubs) built 2026-10-15 (`build_sidewalks.py`,
see `status.md`). Sidewalks are concrete ODS1 solids beside the ribbon, not
part of the road mesh, so `M_Road` didn't need regenerating.

**Drive check:** a town (Frisco/Breck) day and night, and one rural pass.
Do the signs and lights read as real, and is the density right?

---

## Phase 2 — Unpaved road surfaces (small)

Built 2026-10-06 (see `status.md`). What's left is the drive check in
`next steps.md` §2, which needs the combined `rebuild_area.py` run first.

---

## Phase 3 — Ambient traffic (large)

### 3a. Directed lane graph (pipeline)
Built 2026-10-06 (see `status.md`): the ODG1 lane block in
`export_road_centerline_bin.py`, read by nav. What's left is the drive check
in `next steps.md` §2. 3c has to merge the 5 m station edges into lane
polylines between junctions; the file has no polyline of its own.

### 3b. Traffic demand (pipeline, D4)
Built 2026-10-14 (see `status.md`): `build_traffic_demand.py` writes
`traffic_<area>_test.bin` (ODT1) per lane-graph edge from FHWA HPMS, with the
hourly/weekly curves and `demand_vph()` that 3c mirrors. What's left, only if
drives show it:
- Census block-group population for unmatched local streets (buildings per
  2 km tile stand in now).
- Recreational profiles (I-70's Sunday ski jam): one national urban/rural
  curve can't produce it; the TMG factor groups can.

### 3c. Traffic runtime (`AODTrafficManager`)
Built 2026-10-14 with 3d and 3e (see `status.md`; compiled, not driven):
`ODTrafficManager.cpp` merges the ODG1 edges into directed links, fills the
range from `demand_vph()` on the fundamental diagram, and runs IDM at a fixed
20 Hz. `AODDayNightController` counts days (Monday on a fresh start,
`-odday=`). Check: `pipeline/tests/test_traffic_runtime.py`. Built
differently from the plan below, or left:
- **One tier, no CTM.** IDM agents out to 800 m (max 1500), drawn as
  instanced meshes. The FD congested speed caps each link's desired speed,
  which holds a jam in place. A far CTM tier (and its handoff test) only if a
  full jam outgrows the agent cap or the 1.5 ms budget. The log prints the
  peak ms every 5 s.
- Mandatory lane change only where a lane ends (MOBIL safety test, else wait
  at the lane end). No lane-up before a split or exit, and no discretionary
  changes.
- Junctions: signals are fixed-cycle by approach axis, stop signs are a full
  stop then gap acceptance, give-way is gap acceptance. Equal-priority
  approaches don't check each other inside the junction box. Signal heads in
  the furniture aren't synced to the cycle.
- Wrecks are physics props. Traffic doesn't brake for them, and they have no
  wheels.
- No seam handoff: the manager swaps to the nearest area's graph and refills.
- No trucks: a truck counts as 2 cars in the density but is drawn as a car.

- **Clock:** `AODDayNightController` has `TimeOfDay` but no day count. Add
  a day counter (it ticks when the hour wraps) so weekday/weekend works.
  Weekdays are the default on a fresh start.
- **Target density per edge** comes from the fundamental diagram: hourly
  flow q = AADT × hour factor × direction split. Below capacity, take the
  free-flow branch of the diagram; above it, the congested branch. A jam
  is a stopped queue near jam density (~130 veh/km/lane) behind the
  bottleneck (lane drop, merge, signal). IDM then produces stop-and-go by
  itself.
- **Two tiers, so a jam fits the budget.** A jammed 4+4-lane I-5 over a
  1.5 km radius is ~3,000 cars:
  - *Near tier (≤ ~300 m, up to ~150 agents):* kinematic agents on their
    lane polylines at an arc-length position, with Z from the polyline.
    - IDM following, with the player car as a possible leader.
    - Lane choice at junctions, plus mandatory lane changes before a split
      or exit (MOBIL rule). Discretionary lane changes are v2.
    - Gap acceptance at stop/give-way.
    - Fixed-cycle signals, obeyed strictly (D5).
    - Per-agent speed factor 0.9–1.1 of the limit.
  - *Far tier:* no agents. Each edge is split into cells with a density and
    speed, stepped at 1–2 Hz (cell transmission model). Cars are drawn as
    HISM instances spaced at the cell density and moving at the cell speed.
    The two tiers swap cars at the 300 m boundary, so the queue you join is
    the one you saw from a distance.
- **Spawn and persistence:** on load, fill every edge in range to its
  target density straight away, so you can start in the middle of a jam.
  After that, cars enter at the edges of the range and leave behind the
  player. A jam ahead stays until the clock moves past the peak.
  Everything is seeded per edge and time bucket (decision 4).
- **Collision:** near-tier agents are kinematic boxes. On contact with the
  player an agent becomes simulated physics and goes through Phase 0's
  crash handling, then despawns once it has settled. This is why Phase 0
  comes first. Far-tier instances have no collision (they're always out of
  reach).
- **Perf budget:** ≤ 1.5 ms/frame at a full I-5 jam (150 near agents +
  ~3,000 far instances). The far tier's cost is the per-cell update and the
  HISM transform writes. Mark it `ponytail:` if the leader search starts
  O(N²).
- **Multi-set:** one manager for the world, reading the nearest area's
  graph like HUD nav does. Cells at area seams hand off to the neighbouring
  area's edge.

### 3d. Look and sound
Built 2026-10-14 apart from paint: M_CarBody samples a palette texture and
has no colour parameter. Random paint needs that material change (shared
with 4.3).
- The five car meshes with a random paint parameter (shared with 4.3
  cosmetics). Headlights at night, and brake lights from IDM deceleration
  (far tier: from the cell speed dropping). A jam at night reads as a river
  of brake lights.
- Pass-by audio: the closest 4 agents get a cheap engine synth instance.
  A jam adds an idle-traffic ambient bed, scaled by nearby density.

### 3e. Modes
Built 2026-10-14: off when any `AODRaceManager` has gates, `-odtraffic=0`,
and a Settings > Game > Traffic On/Off row (D4: no density knob). Snowplows
are still to do.
- Race (D3): the race manager turns traffic off on a stage.
- There's no density setting (D4). The only knob is a hidden
  `-odtraffic=0` QA flag.
- **Snowplows (Taylor, 2026-10-06, for fun, later):** in snow, a few agents
  become plows on main roads. They drive slower and clear the road snow
  depth behind them (throwing it onto the side berms). This needs road snow
  stored per road chunk, not as one global value.

- **Checks:**
  - `tests/test_idm.py`: stop behind a stopped leader with no overlap, and
    reach the speed limit on a free road;
  - `tests/test_ctm.py`: a lane drop above capacity grows a queue
    upstream, and the queue clears once demand falls;
  - a Python mirror of the junction gap rule;
  - a near/far handoff test: the car count is conserved across the 300 m
    boundary.

**Drive check:** Frisco and I-70 at midday and at 17:30. Also a
California member on I-5 near a city at rush hour (needs that area baked).
Look for density, pop-in, the jam feeling stuck, junction deadlocks, and
how traffic reacts to the player.

---

## Phase 4 — Water and coast (medium; needed for the coast ranges and Hawaii in scope)

Built 2026-10-15 (coastline rings, Z=0 sea tiles, lake islands, culvert
mouths, Oahu water, `verify_water.py`; see `status.md`).

**Drive check:** Oahu coast road, and a lake with an island.

---

## Phase 5 — Tunnels and stacked crossings (medium–large)

Built 2026-10-13..15 (bore + dark-interior post-process, synthesized spans
for untagged `layer` stacks, per-road-pair East Portal walls, and the
`verify_road_fit` TUNNEL PAVEMENT / STACKED CROSSINGS gates; see
`status.md`). The East Portal "30 m off the DEM" no longer reproduces: the
worst road-vs-DEM gap near the portal is now a 9 m bridge ramp.

**Drive check:** Eisenhower/Johnson tunnel and one interchange.

---

## Phase 6 — Wet weather (small–medium)

1. A global wetness parameter (already used for the road) also drives the
   terrain and foliage materials (darker, glossier). **Done (2026-10-09):**
   `AODSeasonController::PushParamsToMIDs` pushes `Wetness` to the ground,
   `M_Foliage` and the bark MIDs.
2. Puddles: a deterministic noise mask × the road profile's sags (low
   points between stations), only when wetness > 0.6. **Done (2026-10-09):**
   per-vertex sag in UV3.x (`RoadSagCm`), pooled by `M_Road`'s puddle term.
   `check_puddle_sag.py` also mirrors the mask (2026-10-14): none at
   wetness ≤ 0.6, growing with wetness and sag. On Summit, 8.5% of stations
   reach full sag.
3. Rain streaks follow the car's velocity. **Done (2026-10-14):** the
   streak slant uses the pawn's velocity (fixed 120 Hz physics) instead of
   the per-frame camera delta.
4. Lightning: a bolt mesh/flash plus a cloud flash, timed to the existing
   thunder. **Done (2026-10-09; timing 2026-10-14):** jagged bolt ISM plus
   a flash directional light at atmosphere index 1. The strike keeps its
   countdown overshoot, so the strike and thunder instants don't depend on
   fps. The flash shows the envelope's mean over each frame, so every
   frame rate gets the same light per strike (`check_lightning.py`).
5. The Dragon's road gets wetness (it isn't an `AODRoadManager` mesh, so it
   needs the parameter on its material). **Done (2026-10-09):** its
   `M_Road` slots share one MID (`Wetness`, `PuddleAmount` 0, since its
   OBJ has no UV3).

**Drive check:** a rain → clear cycle on Summit at night. Not driven yet.

---

## Rough size and order

| Phase | Size | Depends on |
|---|---|---|
| 0 Crash | S | — |
| 1 Furniture | M | — |
| 2 Surfaces | S | — |
| 3 Traffic | XL (with the D4 demand model and the far tier) | 0, 1 (junction control), nav one-way via 3a |
| 4 Water/coast | M | — |
| 5 Tunnels/stacks | M–L | — |
| 6 Wet weather | S–M | — |

Recommended order: 0 → 2 → 1 → 3, with 4/5/6 slotted in wherever a
rebuild is already happening. Phases 1, 2, 4 and 5 all need a full
`rebuild_area.py` run, so batch them to share rebuilds.
