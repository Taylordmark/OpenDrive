# OpenDrive — Realistic Visuals Plan

*2026-10-09. A trial of a realistic, The Crew 2–style look, built behind a
**Visual Style** setting (Stylized / Realistic) on its own branch, so the
current look is never touched and the trial can be dropped at no cost. When
a step is done, delete it here and add a line to [`status.md`](status.md),
as for [`PLAN-REALISM.md`](PLAN-REALISM.md).*

The renderer isn't the gap. `DefaultEngine.ini` already has Lumen GI and
reflections, hardware ray tracing, Virtual Shadow Maps and Substrate, which
is more than The Crew 2 shipped with. The gap is content and grading, so
every track here is materials, assets, decals, effects and post-process
values. There's no engine work.

**Shape of the work:** Wave 0 builds the switch. After that, seven tracks
(A–G) run in parallel, each owning its own files, then one join step (J)
needs two of them done. Everything only Taylor can do is collected in
[Taylor's list](#taylors-list) at the end.

Process rules from `status.md` apply: pipeline work is verified offline,
runtime C++ by a headless `Build.bat` compile, and the look is judged by
Taylor in a **drive check** per track, A/B against Stylized.

---

## Decisions (Taylor, 2026-10-09)

| # | Question | Decision |
|---|---|---|
| V1 | Standing decision 1 (stylized art) | **Suspended on the branch while the trial runs.** Main keeps decision 1 until the branch is merged. |
| V2 | Car models | **Keep the current CC0 low-poly cars.** Only their materials change (Track B). Bought or commissioned cars are out of scope. |
| V3 | How to switch | **A separate "Visual Style" setting**, not a fourth Asset Detail tier. Detail is polygon count, style is the look. They're independent. |
| V4 | Reverting | **Additive only, on branch `visuals-realistic`.** Stylized assets are never edited. Bad result → set the style back to Stylized. Abandon → delete the branch. |
| V5 | Asset licensing | **Free, open, and safe to sell.** Every third-party asset must allow commercial use with no fee or royalty, and be downloadable by the pipeline without an account. When nothing fits, **we make it ourselves** (procedural generator or Blender) rather than buy or license it. |

### V5 in practice

| License | Use? |
|---|---|
| CC0 / public domain (ambientCG, Poly Haven, Kenney, Quaternius) | **Yes**, the default. |
| CC-BY 4.0 | Yes, with a credit line via `build_credits.py`. Prefer CC0 when both exist. |
| CC-BY-SA | No. Share-alike on game assets is murky for a commercial build. |
| Anything NC, ND, "personal use", "editorial", "Standard/UE-only license" | **No.** That rules out Fab, Megascans, Sketchfab store, Unity/Unreal marketplace, TurboSquid. |
| Paid, subscription or per-seat | **No.** |

Each new third-party file gets an entry in its folder's `manifest.json`
(`source`, `license`, `author`), as `content_src/textures/*` already do.
Binaries stay out of git and are re-fetched by the folder's
`download_*.py`.

### The interface rule (applies to every track)

A `_real` material must expose the **same parameter names and read the
same UV and vertex-color channels** as its stylized twin. C++ drives them
at runtime: M_Road's UV3 crest data, wear seed and SnowDepth/SideSnowDepth;
the seasonal foliage MID and FallBlend; M_BuildingFacade's WallSet
encoding; wetness from the NASA POWER rainfall. A `_real` material that
drops one of those breaks snow, seasons or rain only in Realistic. Each
`_real` material's build script checks the parameter list against its
twin and fails if one is missing.

---

## Dependency map

```
Wave 0:   0a Visual Style switch ──┐      0b License gate (independent)
                                   │
Wave 1:   ┌──────┬──────┬──────┬───┴──┬──────┬──────┐
          A      B      C      D      E      F      G      (all parallel)
       Light  Car    Roads  Terrain Effects Veg   Buildings
                      │      │
Join:                 └──J───┘   road-edge blend (needs C and D)
```

### File ownership (so parallel tracks don't collide)

Each track writes only its own files. A `_real` twin is saved **beside
its stylized asset** (same folder; `ODVisualStyle::Pick` looks there),
e.g. `/Game/OpenDrive/Materials/M_Road_real`. New Realistic-only helpers
(textures, functions) go in `Content/OpenDrive/Realistic/<Track>/`.
Sources go in `content_src/realistic/<track>/`. `Content/` isn't in git (it's rebuilt by
the pipeline), so tracks collide only through source files and the import
scripts.

**Shared files:** `ODSettingsMenu.h/.cpp` belong to the "Menu & mouse"
session. Ask there for any new settings row.

| Track | Owns | Reads only |
|---|---|---|
| 0a | `ODVisualStyle.h`, the material-load lines in every manager | — |
| 0b | `pipeline/verify_asset_licenses.py` | all `manifest.json`, `build_credits.py` |
| A | `ODAtmosphereController.cpp`, `Realistic/Atmosphere/` | weather, day/night state |
| B | `Realistic/Cars/`, car material build script | `T_CarPalette2` |
| C | `Realistic/Road/`, new `ODRoadDecals.*`, road texture downloader | road centerline, wetness |
| D | `Realistic/Terrain/`, ground/rock/embankment material scripts | — |
| E | `Realistic/FX/`, new `ODVehicleFX.*` | `UODVehicleMovement` slip and surface |
| F | `make_tree_meshes.py` (new mode), `build_flora_textures.py`, `Realistic/Flora/` | `ODForestManager` (already wrapped in 0a) |
| G | `Realistic/Buildings/`, facade material script | building WallSet encoding |
| J | `M_Road_real` and terrain `_real` edge inputs, the RVT volume | C and D output |

Two things still serialize: headless `Build.bat` compiles and Unreal asset
imports share one project, so run them one at a time even when tracks
run in parallel worktrees.

---

## Wave 0 — Foundation

### 0a. The Visual Style switch (small, runtime C++) — built 2026-10-09

Built on `visuals-realistic` (worktree `.claude/worktrees/visuals-realistic`)
and compiled clean. What's left is the 0a drive check (T-list). The menu
row came in through the Menu & mouse session's settings rewrite on `main`
(`RowVisualStyle`), so it shows up here after `main` is merged in. Until
then, set `VisualStyle=1` under `[OpenDrive.Graphics]` in
`GameUserSettings.ini` by hand.

As built:

No visual change. This is the mechanism every track plugs into.

1. **`ODVisualStyle.h`**, modelled on `ODAssetDetail.h`: config section
   `OpenDrive.Graphics`, key `VisualStyle`, values 0 Stylized / 1
   Realistic, read from `GameUserSettings.ini`, applied on the next level
   load. One templated `Pick<T>(T* Base)` that loads `<Base>_real` from
   the same package path and falls back to `Base` when it isn't imported.
   It covers meshes, materials and data assets.
2. **Settings menu row:** done on `main` by the Menu & mouse session (see
   above).
3. **Wrap every runtime material load** in `ODVisualStyle::Pick`. Each one
   is a single `LoadObject<UMaterialInterface>` today:
   `ODRoadManager` (M_Road, OneWay, Gravel, Dirt), `ODBuildingManager`
   (M_BuildingFacade), `ODForestManager` (M_Foliage, bark, M_Rock030),
   `ODBridgeManager`, `ODEmbankmentManager`, `ODGuardrailManager`,
   `ODParkingManager`, `ODFurnitureManager`, and the terrain and car
   materials, plus `ODWallManager`, `ODWaterManager` and the static-road
   wetness MID in `ODSeasonController`. Car materials come from the mesh
   asset, so `ODVehiclePawn` calls `PickSlots` after each mesh swap.
   Meshes go through `Upgrade(Pick(...))`, so a `_real` mesh gets its own
   `_real_high` / `_real_ultra` tiers from the same generators.
   `Pick` caches hits and misses (tile builds call it per slot).
4. **A style flag** `ODVisualStyle::IsRealistic()` for tracks that spawn
   Realistic-only things (C decals, E effects).

**Check:** compile (passed). `Pick` is a cached `LoadObject` with a
fallback. There's no C++ test harness in the project, so the 0a drive check
is the runtime proof: Realistic with no `_real` assets must look identical
to Stylized.

### 0b. License gate (small, pipeline) — built 2026-10-09

`pipeline/verify_asset_licenses.py` walks every
`content_src/**/manifest.json` and fails on any entry whose `license`
isn't CC0, public domain, CC-BY 4.0 or `generated`, or that has no
`source`. It reuses `build_credits.walk_manifest`, so it reads manifests the
same way the credits screen does. Every manifest entry gets printed in the
credits, so a CC-BY entry is credited automatically and needs no extra
check. Every Wave 1 track runs it before it's called done.

**Check:** `pipeline/tests/test_verify_asset_licenses.py` passes, and the
gate passes on all current manifests (30 records in the main checkout,
27 in a fresh worktree that has no `data/cars`).

---

## Wave 1 — Parallel tracks

Ordered by visual gain per hour, which is also the suggested order if
fewer than seven run at once.

**Status (2026-10-09, from the branch log):**

| Track | State | Commits |
|---|---|---|
| A | first pass (see below) | 7f66ba7 |
| B | first pass: `_real` car paint, glass, lamps | 69e33be |
| C | first pass: `M_Road*_real` + deterministic decals; cars don't receive decals; tire marks cut to braking zones | b6ed029, 940da35, e8872d0 |
| D | first pass: `_real` ground, embankment, boulders | 0079ca4 |
| C/D | CC0 ambientCG cracks, rock, litter, moss, gravel | d85478b |
| E | first pass: tire smoke, dust, spray | de561e9 |
| F | trees for 6 of 7 slots + flora materials | d321c3c |
| G | first pass: `M_BuildingFacade_real` (paint, trim, grime, glass, night windows, 3 roof types) | see Track G |
| H | first pass: `_real` W-beam and Jersey, W6x9 posts, delineators | see Track H |
| J | shader shoulder blend, no RVT; junction pad rims + aprons fringe too (UV3.y edge code from ODRoadManager, Realistic only; `check_pad_edge.py`) | 5cbf1ee, this commit |
| K | first pass: tall grass, weeds, low shrubs, pebbles on the grass cells | see Track K |
| L | first pass: Sentinel-2 macro ground colour (deals_gap, near_gasquet, sf_russian_hill) | see Track L |

`main` merged in with the menu work (cbbce1f). Known gaps:
- The old static Dragon level gets no decals.
- F has wind (Track F item 4) but no LODs yet.
- D's Nanite displacement is on the boulders only. There was no global-setting
  blocker: UE 5.8 has no r.Nanite.AllowTessellation, and r.Nanite.Tessellation
  already defaults to 1. Kill switch: `r.Nanite.Tessellation 0`. Terrain, cut
  walls and skirts are dynamic meshes, so they can't be Nanite (not done).

### Track A — Lighting, sky and post-process (small, biggest gain)

`AODAtmosphereController` already spawns the sky atmosphere, volumetric
clouds, height fog and the "Grade" post-process volume (bloom 0.5,
exposure −4…12, bias −1). Realistic gets its own values for these, chosen
by style at spawn time, and nothing new is spawned.

1. **Grade:** a color-grading LUT (warm highlights, slightly crushed
   shadows, the high-contrast "car ad" look), stronger bloom with a lower
   threshold, a light vignette, film grain off or very low. The target
   comes from Taylor's reference board (T1) if it exists by then.
2. **Camera:** per-object motion blur on the car and world at speed, TSR
   sharpness tuned for thin geometry (guardrails, wires).
3. **Atmosphere:** denser aerial perspective and height fog for depth in
   the mountains, a cloud material with more detail than
   `m_SimpleVolumetricCloud_Inst`. Weather coverage must still drive it.
4. **Day/night:** the grade at dawn, noon, dusk and night with
   headlights. One LUT may need per-time-of-day blending.

**Check:** compile; render values dumped to a log at four times of day.

**Status (2026-10-09):** first pass is in `ODAtmosphereController.cpp` and compiles.
- Grade: done with color-grading fields, not a LUT, so there's no asset.
  Highlight gain 1.04/1.0/0.94, cool shadow gamma, contrast 1.12, FilmToe
  0.6, bloom 0.7 at threshold 0.8, vignette 0.3, grain 0.
- Haze: aerial perspective 0.9 (Stylized 0.8), fog density 0.0013.
- The spawn log line prints style, bloom and vignette.
- Motion blur is already the engine default (on) in both styles, so it's
  unchanged.
- Not done:
  - TSR sharpness: it's a global cvar, so it belongs with the settings
    session.
  - Cloud material: it needs a new asset.
  - Per-time-of-day grade: the night volume only lifts exposure, so wait
    for T5 drive feedback.

### Track B — Car paint, glass and lights (small)

Only the materials change, never the models (V2). Build on the existing
`M_Car*_Hi` set.

1. **Paint:** a Substrate slab with a clearcoat layer, metallic flake via a
   tiling normal, and roughness variation. The colors come from the current
   palette (`T_CarPalette2`).
2. **Glass:** thin translucent glass with a reflection Fresnel and a
   darkened interior, so the low-poly cabin doesn't show through.
3. **Lamps, chrome, rims, tires:** emissive lamps that bloom at night, real
   chrome, tire rubber with sidewall roughness.

**Check:** the material build script runs headless and the interface rule
passes for each `_real` car material.

**Grounding (2026-10-10, Realistic only, no model changes):**
- `UODContactShadow` (new `ODContactShadow.*`, on the pawn) puts 5 deferred
  decals on the ground: a rounded-rect blob under the body and an ellipse
  under each tyre. The material is `Realistic/CarShadow/M_CarContactShadow`,
  built by `unreal/build_car_contact_shadow_material.py`. It darkens albedo
  and removes specular, so it works in overcast and at night.
- Each blob traces down from its anchor every post-physics tick and sits on
  the hit normal. It fades by gap and by tilt, as a pure function of the
  state. The mirror is `pipeline/checks/check_contact_shadow.py`.
- The car doesn't receive decals, so the blobs never draw on it.
- Cost: 5 line traces and 5 small decals for the player car.
- Car meshes cast two-sided shadows, against light leaks through open
  shells and wheel wells.
- The sun gets 30 cm world-space contact shadows (`ODDayNightController`).
- Not done: capsule shadows (skeletal meshes only); reflections. Lumen
  reflections plus the real-time sky capture already cover the paint at
  High/Epic. At Medium, SSR is capped by the scalability tier, so a PP
  override can't help.

**Pass 2 (2026-10-10): tires, rims, grime, weather, detail. Realistic only, no model changes:**
- **Projection:** all detail comes from car-local position and slot category. The car UV0 is only a palette lookup, so this survives the incoming Blender meshes. Wheel proportions (rim, shoulder, tread, spoke-gap and hub radii) are scalar params, not hard-coded.
- **Tires:** `M_CarTire*_real` gets an analytic round normal (tread, shoulder, sidewall bulge) plus a tiling tread normal and raised sidewall lettering. Roughness differs between tread and sidewall.
- **Rims:** `M_CarRim*_real` adds lathe rings, a polished lip, a dark hub, and brake dust that follows the car's grime.
- **Chrome:** stays near-mirror (roughness 0.1).
- **Textures:** `content_src/realistic/cars/make_car_detail.py` generates CC0 `T_TireTread`, `T_TireSidewall` and `T_CarRainDrops`, and self-checks them.
- **Grime:** `ODCarGrime.h` defines the model. `UODVehicleMovement` integrates it per fixed physics step from distance rolled on each surface:
  - gravel and dirt fastest, then off-road, then wet asphalt, with dry asphalt nearly clean;
  - an exact exponential step, so the result does not depend on step rate;
  - capped at `MaxDirt`;
  - reset on respawn, reset and car change.
- **Weather and state to the materials:** `UODVehicleFX::PushCarState` writes custom primitive data, but only when a value changes:
  - Dirt, Wetness and SnowCover to every car mesh;
  - the axles and wheel radius to the chassis.
- **What the materials do with it:**
  - Grime collects low on the body and behind the wheel arches.
  - Wet weather darkens the grime to mud and adds rain beads on paint and glass.
  - Snow settles on up-facing panels.
  - Clear-coat roughness stays at 0.05–0.15. Dirt raises it locally, and wet lowers it.
- **Detail:** panel gaps from axle positions, a hex grille on the front-facing plastic, darkened wheel arches and underbody, and AO from local height.
- **Check:** `pipeline/checks/check_car_dirt.py` covers:
  - accumulation per distance;
  - nothing added while parked;
  - the same result from 30 to 500 Hz;
  - the cap and the reset.
- **Cost (Hi pixel-shader instructions, `_real` before → after):**

  | Material | Before | After |
  |---|---|---|
  | Body | 361 | 497 |
  | Plastic | 201 | 334 |
  | Glass | 1842 | 1907 |
  | Chrome | 201 | 309 |
  | Tire | 206 | 298 |
  | Rim | 203 | 253 |
  | Lights | 228 | 228 |

  Vertex shaders are unchanged.
- **Not verified, because no game run:**
  - the rendered look;
  - door-gap placement on every car (it is heuristic);
  - the new meshes' wheel proportions.

### Track C — Roads (medium)

1. **`M_Road_real`:** the higher-res ambientCG asphalt sets
   (`content_src/textures/road`), macro variation, and a wet-puddle mask
   that reads the existing wetness input.
2. **Decals**, deterministic from the road seed (decision 2): cracks,
   patches, tar seams, worn lane paint and tire marks at corner entries
   and junctions. They're spawned only in Realistic. Textures from
   ambientCG or generated.

**Check:** a plot of decal density along the Tail of the Dragon centerline
in `scratchpad/`, and the same seed gives the same decals twice.

### Track D — Terrain, rock and cut walls (medium)

1. **Terrain:** `_real` versions of the ground materials with layered PBR
   (ambientCG grass, gravel, ground, rock, snow at 2K), macro and micro
   variation and distance blending to kill tiling. Keep the slope and
   vertex-color logic of `M_GroundStylized`.
2. **Rock and cut walls:** Nanite displacement on M_Rock030 and the
   embankment and cut-wall material, so the canyon walls on the Dragon get
   real relief.
   *Done for boulders:* `M_Rock030_real` displaces (8 cm, world triplanar
   Rock030 height) on Nanite twins `SM_RockBoulder*_real_high/_ultra`
   (`unreal/import_rock_meshes_real.py`); the Standard-tier twin stays
   non-Nanite, so Model Detail Standard has no displacement. Cut walls
   would need Nanite static meshes instead of dynamic meshes (not done).

**Check:** interface rule passes; compile.

### Track E — Effects (medium)

Niagara, Realistic only, in a new `ODVehicleFX` component:

1. Tire smoke for burnouts and drifts, driven by `UODVehicleMovement` slip.
2. Dust off unpaved roads and off-road, spray on wet roads, both by surface.
3. Rain beading on the camera in chase view.

Spawn rates read slip and surface from the physics state, so the same
drive gives the same smoke at any fps.

**Check:** compile; a small runnable check of the slip/surface → spawn
rate mapping.

**Status (2026-10-09):** first pass in de561e9, compiles.
- `UODVehicleFX` (one line in `AODVehiclePawn`'s constructor). Not Niagara:
  a 512-sphere instanced pool animated in C++, like the snowfall, drawn
  with `Realistic/FX/M_FXPuff` (`unreal/build_vehicle_fx_material.py`).
- Smoke from contact-patch slide speed on asphalt, dust on gravel/dirt
  roads and the landscape, spray on wet asphalt. Item 3 (rain on the
  lens) already exists in both styles (`AODWeatherController::TickLens`).
- `pipeline/tests/test_vehicle_fx_rates.py` checks the mapping, and that
  puff count, spacing and motion match from 24 to 240 fps.

### Track F — Vegetation (large, we build the trees)

The heaviest track for performance. The forest is dense deterministic
scatter, so every realistic tree is multiplied by thousands.

CC0 sources for realistic trees are thin (Poly Haven has a handful of
plants and bark), so per V5 we make them. The starting point is
`pipeline/make_tree_meshes.py`, which already generates every species
procedurally.

1. **Realistic generator mode** in `make_tree_meshes.py`, writing `_real`
   meshes with matching species names so `ODForestManager` picks them up
   through `Pick`:
   - Branching from a space-colonization or L-system pass instead of the
     stylized silhouette, several variants per species.
   - Leaf cards on the branch tips, with alpha-tested leaf textures.
   - Bark from the CC0 Poly Haven sets already in
     `content_src/textures/flora`.
2. **Leaf and needle textures** generated by `build_flora_textures.py`
   (marked `generated`), or CC0 scans where they exist. Taylor's photos
   (T4) replace them if they turn out better.
3. Keep the seasonal MID interface (FallBlend) on the leaf material.
4. Wind via world-position offset that stays cheap at distance.
   *Done:* whole-tree sway on the `_real` meshes. A UV1 sway weight is baked at
   import (U = height^2 bend, V = radial tip bob; `flora_common.sway_uv`).
   `M_Foliage_real` and the `_real` bark use the same Time-only WPO, so leaves
   stay on their branches: ~6 cm crown bend for a 15 m tree, scaled by
   instance height. Bound 33 cm bark, 44 cm foliage. WPO is off past 100 m
   (`WpoDisableCm`). Collision unaffected (HISMs are NoCollision).
5. **Perf:** frame time at the densest Dragon tile, Realistic vs Stylized,
   at each Asset Detail level, from a CSV profile capture. Taylor runs the
   capture (T2); the numbers are read from the CSV, not by eye.

**Check:** per-species triangle and card counts plotted against the
stylized meshes; a render of each species turntable from the generator.

### Track G — Buildings (medium, optional)

1. `M_BuildingFacade_real`: trim sheets and interior-mapped windows
   (parallax rooms, lit at night), keeping the WallSet encoding.
2. Roof detail from the existing roof tiles set.

**Check:** interface rule passes; compile.

**Status (2026-10-09):** first pass, no C++ (`ODBuildingManager` already
`Pick`s the facade). `unreal/apply_building_material_real.py` builds
`M_BuildingFacade_real` on the twin's UV0 + vertex-color RGB:
- per-building paint on siding/plaster, brightness jitter, weathering,
  ground splash, rain streaks under sills;
- window frames, mullion/transom, sills, flat normals; dark glossy glass by
  day, some drawn blinds;
- night windows: the shader takes NightFactor() from the sun
  (SkyAtmosphereLightDirection 0), so up to 35% of rooms switch on through
  dusk at 3.2-4 nits; `pipeline/check_night_exposure.py` checks them;
- shingle roofs split between RoofingTiles007, CC0 RoofingTiles009 and
  painted CorrugatedSteel005 (`content_src/realistic/buildings/manifest.json`).

**Follow-ups (2026-10-09):**
- wall/roof slot split: Realistic puts roof triangles in a second section
  with `M_BuildingRoof_real`; facade PS 502 -> 430 instr, 24 -> 15 samples.
- pitched roofs (Realistic only): `ODBuildingManager` `PitchRoof` gives small
  near-rectangular footprints (<= 400 m^2, OBB fill >= 0.85, 3-16 m span) a
  gable or hip roof with windowless gable end walls (vertex A = 0); the rest
  get flat concrete. Built off-thread per tile (median 0.7 ms, worst 79 ms);
  42% of buildings, +30% tris. The OSM data has no roof:shape/roof:levels,
  so the shape is hashed. Mirror + tests: `pipeline/building_roofs.py`,
  `pipeline/tests/test_building_roofs.py` (watertight, no self-intersection,
  all 534 tiles).
- interior-mapped rooms in lit windows: one Custom HLSL node ray-casts a
  bay x floor x 3-6 m room per window (hashed tint/depth); PS 432 -> 488
  instr, no extra samples. Unlit windows stay glass.

### Track H — Guardrails (small)

1. `M_Guardrail_WBeam_real` / `M_Guardrail_Jersey_real`, picked by
   `AODGuardrailManager` like any twin.
2. A real W-beam post in Realistic only.

**Status (2026-10-09):** first pass. `unreal/apply_guardrail_assets_real.py`
builds both twins, a reflector material and the post mesh:
- the ribbon's UV0.U is station index, so all texturing is world triplanar
  (CC0 ambientCG Metal038 and Concrete035,
  `content_src/realistic/guardrails/manifest.json`); UV0.V (profile layer)
  only drives the height bands;
- W-beam: galvanized tint, zinc spangle that fades before it aliases,
  roughness breakup, grime along the bottom, light rust streaks, scrapes on
  the bulge;
- Jersey: weathered concrete, dirt and tire scuffs on the lower slope,
  run-off stains;
- `SM_GuardrailPost_real` (`pipeline/make_guardrail_post_mesh.py`): W6x9
  I-beam 1.8 m, a blockout and a clip-on white delineator that glows toward
  the camera at night. `AODGuardrailManager` puts it behind the rail, yawed
  and unscaled, and gives the rail real normals; both only in Realistic.
  Stylized keeps the cylinder.
Not done: amber delineators (the run data has no one-way flag to say which
side is the median), end treatments.

**Drive check:** a W-beam road and a motorway, day and night.

### Track K — Ground cover (small)

Taylor: the landscape "looked pretty crappy and bland". Track D textured the
ground, but nothing grew on it beyond the short grass carpet.

**Status (2026-10-09):** first pass, Realistic only (Stylized never loads the
assets, so its scatter is unchanged).
- `ODGroundCover.h/.cpp`: a pure, cell-seeded `Place()` on
  `AODForestManager`'s 16 m grass cells. It reuses their traced 2 m lattice,
  so pavement, pads, bridges, buildings, parking, cut walls and lakes are
  already bare, plus the shoulder strip (shrubs 1.5 m further off). Full
  density to 30 m from the pavement edge, fading to a 20% fill at 60 m and
  beyond. Per-biome grass, weed, shrub and pebble scales and dryness (lush
  temperate, straw and sage in desert, sparse on alpine and tundra). Cards
  and shrubs stop at about 31° of slope; pebbles hold to about 56°.
- The hook in `ODForestManager` is about 40 lines: load, build with the grass
  cell, evict with it, copy SnowCover and Wetness into the card MID. It is
  streamed with the grass (2 cells per tick).
- Model Detail scales density (×0.5 / 1 / 1.6), the per-cell cap and the
  cull distance (72 / 96 / 120 m for cards and shrubs; pebbles at 40% of
  that). No collision, no shadows, WPO off past 100 m.
- Meshes: `SM_GCTallGrass_real` (60–95 cm) and `SM_GCWeed_real` (a broadleaf
  rosette with stalks), each 18 tris, generated by
  `pipeline/make_ground_cover_meshes.py` from Track F's card helpers and
  atlas. Shrubs reuse `bush_real` and `sagebrush_real` at 0.3–0.6 scale.
  Pebbles reuse `SM_RockBoulderA_real` (the non-Nanite Standard twin) at
  3–15 cm, with `M_Rock030_real`. Nothing is downloaded.
- `unreal/build_ground_cover_real.py` builds `Realistic/GroundCover/
  M_GroundCover_real`. Per-instance custom data sets dryness (lush to
  straw), with root shade, a per-instance fade over the cull band, tip
  flutter driven by Time, and SnowCover and Wetness.
- `pipeline/checks/check_ground_cover.py` mirrors `Place()`. It checks
  determinism, shoulder clearance, bare squares, caps, the road band,
  dryness and the slope cutoff, and prints counts per cell and per ring.
Not done: flowers (the atlas has no flower cell), and a cheap far fill
beyond the 120 m grass ring.

**Drive check:** a meadow road (Summit County) and a desert road, at each
Model Detail level. Watch the shoulder edge and the 60–120 m fade.

### Track L — Satellite ground colour (small)

Taylor: the landscape is bland. At a distance a hillside is one biome tint
plus noise. Real imagery makes it read as what is there: fields, bare
patches, burn scars, dry versus irrigated land, scree.

**Status (2026-10-09):** first pass, Realistic only.
- Source: raw Copernicus Sentinel-2 L2A from Element84 Earth Search on AWS
  (no login). Copernicus terms allow free commercial use with attribution:
  "Contains modified Copernicus Sentinel data 2023-2025". It is recorded in
  `content_src/realistic/satellite/manifest.json` and the credits. Not the
  EOX "cloudless" mosaics: their 2018+ editions are non-commercial.
- `pipeline/build_sat_albedo.py` (run with `python -I`, `OD_AREA=<area>`).
  It covers the baked terrain tiles' rect with up to 10 lowest-cloud summer
  scenes per MGRS tile (Jun to mid-Sep, 2023 to 2025). It reads B04/B03/B02
  at their 20 m COG overview through a small built-in COG reader (no
  rasterio or pyproj), masks with SCL (keeps vegetation, bare, water and
  unclassified) and takes a per-band median. Reflectance uses a fixed scale,
  so tiles and areas match. Road footprints from `roadstations_<area>.bin`
  plus 12 m are inpainted from the ground around them. Asphalt-grey texels
  and the extent border lose alpha (confidence). Downloads are cached in
  `data/cache/sentinel2/`.
- Output: `Masks/sat_<area>_test.png` (RGBA8: sRGB-encoded linear albedo
  and confidence) plus `.json` (extent in UE cm). Sizes: deals_gap 3.1 MB,
  near_gasquet 1.9 MB, sf_russian_hill 1.2 MB.
- Runtime: one texture per area on the shared `TerrainMID`
  (`AODTerrainManager::LoadSatAlbedo`, only when `IsRealistic()`). This is
  seamless across tiles by construction, with no per-tile MIDs and no mesh
  data. `SatAmount` stays 0 when the files are missing, which gives the old
  look. Walls and other users of the material also stay at 0.
- Material (`sat_macro()` in `apply_terrain_real_materials.py`): the albedo
  is multiplied by the imagery's chroma relative to the biome tint's (neutral
  on steep rock), times its luminance relative to `SAT_TINT_LUMA_K` × the
  tint's, both clamped. The weight is 25% up close, rising to full by 200 m,
  × `SAT_STRENGTH` 0.85 × alpha. It applies before wetness and snow, so both
  still cover it.
- `pipeline/checks/check_sat_albedo.py` checks the extent, the 0 border,
  the albedo range, tile-border and granule seams, a port of the material
  math (fallback is exact) and the order relative to snow. It also refits
  the two luminance references from the biome sidecars.

Not done: other areas (run the script per area, about 1 to 2 min uncached);
terrain-shadow removal (the imagery carries some hill shading; the value
exponent and clamp soften it); roads outside the OSM bbox (background ring)
aren't inpainted, only down-weighted when they're asphalt-grey; no mips on
the runtime texture (fine at 20 m texels out to the horizon ring).

**Drive check:** the Dragon from a high pull-off, and near_gasquet's
serpentine slopes. Do fields, clearings and bare ground read at 200 m+
without a halo along the road? Then check SF.

---

## Join — after C and D

### J. Road-edge blend (small)

A runtime virtual texture so the road edge feathers into the terrain
instead of a hard seam. It needs both `M_Road_real` (C) and the terrain
`_real` materials (D) to write and read the RVT, so it waits for both.

**Check:** compile; interface rule still passes on both materials.

---

## Taylor's list

Things only Taylor can do. Nothing here blocks Wave 0. Each Wave 1 track
can be built without its drive check, but isn't called done until its
check is in.

### Before or early (optional, improves results)

- **T1. Reference board.** 5–10 images of the target look: Crew 2
  screenshots and real photos of the Dragon or similar mountain roads, at
  noon and dusk. Kept locally as reference only, never shipped. Feeds
  Track A's grade.

### Drive checks (one per track, each A/B against Stylized)

| Track | Drive check |
|---|---|
| 0a | Flip the style row, restart. Nothing should change yet, and nothing should break. |
| A | Tail of the Dragon at noon and dusk. Does Realistic read as a better image, or just a darker one? |
| B | Garage spin and a sunset drive. Does the paint carry the low-poly body, or make it look worse? |
| C | The Dragon, dry and wet. Do the decals read as real wear, or as a repeating pattern? |
| D | The Dragon's cut walls and one Summit County meadow. Any visible tiling at 50–200 m? |
| E | A burnout, a gravel run and a wet run. |
| F | The densest forest stretch of the Dragon, autumn and summer. Does it hold frame rate at High? |
| G | Frisco/Breck at day and night. |
| J | Slow drive along a road edge. Is the seam gone? |
| K | A meadow road and a desert road. Does the roadside read as grown-in, without pop-in at the fade? |
| L | The Dragon from a high pull-off. Do real fields, clearings and bare slopes read at 200 m+, with no dark smear along the road and no snow-line regression? |

- **T2. Perf capture for Track F:** one CSV profile run on the densest
  Dragon tile, per Asset Detail level, in each style.

### Making assets (the learning part, as wanted)

- **T3. Blender trees** for any species the generator can't get right:
  model it in Blender (free) with its built-in Sapling Tree Gen add-on or
  Geometry Nodes, export with the `_real` naming, and save the `.blend`
  in `content_src/realistic/flora/`. Claude can write the export and
  import scripts and review the result.
- **T4. Leaf, needle and bark photos (optional):** flat, evenly lit shots
  of real leaves and bark, if the generated textures fall short. Your own
  photos are yours to ship, so they're always V5-clean.

### Decisions

- **T5. Winner call**, within about 4 weeks of Track A landing. Carrying
  two art pipelines past that is the main cost of this approach.
- **T6. Keep Stylized as the low-end look, or delete it?** Two styles
  means every future world feature needs assets for both.
- **T7. Minimum spec for Realistic** (roughly an RTX 3060 at 1080p60 once
  Track F lands).

---

## Merge or drop

After T5–T7:

- **Realistic wins:** merge `visuals-realistic`. Update standing decision 1
  in `status.md`, and either keep the style row (Stylized as the low-end
  look) or delete the stylized assets and the setting.
- **Stylized wins:** delete the branch. Main is untouched.
- **Mixed:** cherry-pick tracks that look good in both (likely A's grading
  and E's effects) into Stylized, then delete the branch.
