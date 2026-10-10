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

### Track C — Roads (medium)

1. **`M_Road_real`:** the higher-res ambientCG asphalt sets
   (`content_src/textures/road`), macro variation, and a wet-puddle mask
   that reads the existing wetness input.
2. **Decals**, deterministic from the road seed (decision 2): cracks,
   patches, tar seams, worn lane paint and tire marks in corners. They're
   spawned only in Realistic. Textures from ambientCG or generated.

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
