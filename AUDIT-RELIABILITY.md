# OpenDrive — Build-Method Reliability Audit

*2026-08-21. Companion to [`AUDIT.md`](AUDIT.md) (2026-07-27, architecture/scale).
Scope here is different: **why do rebuilds and reruns produce inconsistent,
surprising results?** Read-only: no stages re-run, no builds compiled. Every
claim cites a file/line or an on-disk observation.*

---

## 1. Verdict up front

**The individual algorithms are fine. The unreliability is systemic, and it is
over-determined — five independent causes are live at once, and each one is a
mechanism by which "I rebuilt it and now it's different/broken" happens with no
error anywhere.**

1. **The world on disk right now is a mixed-generation bake** — barrier and
   building tiles are from 2026-07-28 (pre-§7.11 Z-rewrite stations), while
   terrain/road/wall tiles are from 2026-07-30. The invisible collision
   barrier is streaming at elevations that no longer match the road it fences.
   (§ 2)
2. **Nothing in the pipeline checks freshness, and nothing ever deletes old
   output.** Any partial rerun silently mixes stale and fresh artifacts. The
   documented README rebuild order itself omits roughly half the export steps,
   so following the docs *produces* partial rebakes. (§ 3)
3. **The one verification gate verifies the wrong thing.** `verify_terrain_fit.py`
   re-builds tiles in memory with fresh DEM fetches — it never reads the `.bin`
   files actually on disk. It can PASS while the shipped data is stale,
   corrupt, or half-missing. (§ 4)
4. **The runtime is designed to fail silently.** Missing file ≡ corrupt file ≡
   mistyped prefix ≡ "no data here, fine" in all five tile managers; four of
   five managers don't log material/mesh load failures; DEM fetch failures
   during a bake become sea-level terrain patches with no message. (§ 5)
5. **The process safety nets are all down simultaneously**: 25 days of work
   uncommitted (including everything §7.10/§7.11 describe as "done"), **no git
   remote at all**, docs end 2026-07-29, the retaining-wall system (7/30) is
   entirely undocumented, and the headless workflow depends on plugins enabled
   only in this machine's editor settings, not the `.uproject`. (§ 6)

Plus a handful of concrete live bugs found along the way (§ 7), including one
hard crash waiting in `build_forest_elevation.py` and a missing physical
material on one-way roads that makes spawn latency and grip genuinely vary by
location today.

---

## 2. The smoking gun: the current bake is internally inconsistent

Observed directly from file dates in `OpenDriveUE/Content/OpenDrive/Masks/`
(all 1,580 tile files are `*_oahu_test_*` — the retired-area cleanup held):

| Artifact | Tiles | Baked | Against which stations |
|---|---:|---|---|
| Terrain (ODT1) | 781 | 2026-07-30 19:17–21:13 | post-§7.11 rewrite |
| Road (ODR2) | 343 | 2026-07-30 23:53 | post-rewrite |
| Walls (ODW-new) | 85 | 2026-07-30 23:53 | post-rewrite |
| **Barrier** | **97** | **2026-07-28 18:18** | **pre-rewrite** |
| **Buildings** | **270** | **2026-07-28 18:25** | **pre-rewrite** |

`road_stations.json` was regenerated 2026-07-30 13:31 by the §7.11 road
Z-axis rewrite — which changed station **elevations**. The barrier is, per
`AUDIT.md` § 5.1, "the same stations, offset 13 m": it is now a set of
invisible 4 m collision ribbons positioned in 3D against a road profile that
no longer exists. Buildings were likewise placed/based against the old carved
terrain.

**This is the strongest single mechanical explanation for currently-perceived
unreliability**: an invisible obstacle in subtly wrong positions produces
exactly the class of symptom — inexplicable collisions, weird car behavior in
specific places, "it feels off and I can't see why" — that cannot be
diagnosed visually, because the thing that's wrong is invisible by design.

**Immediate action:** re-run `build_road_barrier.py` → `export_road_barrier_bin.py`
and `build_building_mesh.py` → `export_building_mesh_bin.py` against the
current `road_stations.json` (or, per `AUDIT.md` item 4, take this as the
moment to delete the barrier in favor of the out-of-bounds reset).

---

## 3. Why partial rebakes keep happening: no freshness, no cleanup, incomplete docs

### 3.1 Zero freshness checking between stages (HIGH)

The chain is ~10 manually ordered scripts. The **only** ordering enforcement
anywhere is `build_road_network.py:65` ("run build_road_stations.py first" if
the file is absent). Nothing compares timestamps, hashes, or embeds a
generation ID in any artifact. Rerunning any single stage against a stale
upstream succeeds silently and produces a plausible but internally
inconsistent world. That's § 2, mechanized.

Aggravators:

- **`dem_tiles.npz` cache is keyed on existence only** (`build_road_stations.py:171-174`).
  Re-fetch OSM, tweak the bbox, or change `DEM_ZOOM` and the cache still
  serves; tiles it doesn't hold return **0.0 m elevation** silently
  (`build_road_stations.py:310-312`) — new roads get sea-level stations.
- **`build_retaining_walls.py` must run before `build_terrain_mesh_tiles.py`**
  and both must see the *same* `road_stations.json` for the
  "identical by construction" carve-exclusion guarantee
  (`build_terrain_mesh_tiles.py:143-152`) to hold. Nothing enforces either.
- The wall classification is additionally weakened by rounding:
  upstream classifies on full-precision coordinates
  (`build_road_stations.py:632-633`) but writes 2-decimal values
  (`:663-666`) that downstream re-classification consumes — threshold-adjacent
  stations can flip between the two computations
  (`road_mesh_common.py:455`).

### 3.2 No script ever deletes old output (HIGH)

Grep confirms zero `unlink`/`glob`-delete/`rmtree` in `pipeline/`. Every
export script (`build_terrain_mesh_tiles.py:534-541`,
`export_road_mesh_bin.py:92-98`, `export_retaining_wall_bin.py:59-63`, the
barrier/building exporters) writes only the tiles the current run produces.
Any tile that existed last run but isn't produced this run **survives and gets
streamed**: ghost roads, terrain from a bake with different
`ORIGIN_HEIGHT_M`/carve settings butting against fresh tiles, walls at
hairpins that no longer classify as walls. Worst for same-area iteration —
i.e. the daily Oahu workflow.

### 3.3 The README rebuild order is the partial-rebake generator (HIGH)

`README.md:151-158` documents the Arc 7 rebuild as: fetch → stations →
terrain → road network → road bin → verify. It **omits** the barrier,
buildings, retaining walls, place labels, biome/settlement classification,
map texture, and centerline exports — most of what actually feeds
`Content/OpenDrive/Masks/`. Following the documented procedure after a
station-changing edit is precisely what produced § 2's mixed bake.

---

## 4. The gate can PASS while the build is broken

### 4.1 It verifies an in-memory rebuild, not the disk artifacts (HIGH)

`verify_terrain_fit.py:165` calls `btmt.build_tile(...)` fresh, with fresh DEM
fetches (`:151-153`). It never opens a single `.bin` from
`Content/OpenDrive/Masks/`. Consequently, all of the following PASS the gate
while the shipped world is wrong:

- bake ran against an older `road_stations.json` than verify uses;
- the bake suffered silent DEM fetch failures (§ 5.3) — sea-level patches on
  disk, but verify's own fetches succeed;
- stale mixed-generation tiles on disk (§ 3.2) — verify only rebuilds
  currently-classified tiles;
- any constant changed between bake and verify.

### 4.2 What it doesn't check at all

FAR tiles (explicitly, `:19-21`), tile-to-tile seams, the road `.bin`s (the
road-vs-terrain gate `verify_road_fit.py` was deleted, never replaced — the
docstring at `:33` still cites it), barrier/building/label bins, tile-set
completeness. Excluded stations (bridges, tunnels, conflicts) are
structurally unverified. `sample_mesh_height` misses just count as "miss" and
don't fail the gate (`:170-172`). The tolerance `TOL_M = 3.5 m` (`:70`) was
ratcheted post-hoc to fit the measured max.

### 4.3 The gate's core predicate is still the unvalidated reconstruction

`road_mesh_common.py:589-597`'s own docstring says the original
`sample_mesh_height` was lost and this version is "NOT guaranteed
byte-identical… re-verify against a known-good baseline." No baseline exists
in the repo. It also returns the **first** containing triangle
(`:623-625`), which is order-arbitrary wherever geometry overlaps (skirts,
walls, bridges) — the exact places a gate matters. Open since July; still the
single point every fit number in the project rests on.

---

## 5. The runtime renders an incomplete world without complaint

### 5.1 Missing ≡ corrupt ≡ mistyped prefix (HIGH)

All five tile parsers return one `bool`; `OnTileParsed` files every failure
into `MissingTiles` with **no log** (`ODTerrainManager.cpp:129-142` and its
four clones) — deliberate, because absent-file is the normal "no data here"
case. So a truncated bake, a version-mismatched sidecar, or a wrong
`*TilePrefix` produces a permanent, silent hole for the session, never
retried, indistinguishable from intent. The only trace is the aggregate
`missing=N` diag counter. The parsers themselves bounds-check well — the code
never crashes on bad data, it *vanishes* it.

Related: every manager's C++ default prefix is hardcoded `*_oahu_test`
(`ODTerrainManager.h:57`, `ODRoadManager.h:97`, `ODBarrierManager.h:84`,
`ODBuildingManager.h:67`, `ODWallManager.h:70`). Correct today; the moment a
second area exists, any hand-placed actor or stale level silently streams the
wrong area or nothing. `test_areas.py:58-66`'s own comment names the failure:
"a mismatch doesn't crash, it just silently loads no terrain."
Also: `ODM1` magic is used by two different formats (map centerline,
`ODRoadManager.cpp:126`; forest mask, `ODForestManager.cpp:175`) — a
prefix mixup between them passes the header check and misparses.

### 5.2 Unlogged asset-load failures (HIGH on fresh checkout)

Road, building, wall, and barrier managers log **nothing** when their
material/mesh loads fail (`ODRoadManager.cpp:64-67`,
`ODBuildingManager.cpp:26-28`, `ODWallManager.cpp:34-36`,
`ODBarrierManager.cpp:37-41`); only terrain logs. Since `Content/` is
git-ignored and populated by ~23 one-off `unreal/apply_*.py` scripts,
a fresh clone that runs only the level builder gets gray roads, default
ground, a 20 s spawn wait, and a black map — with zero errors and a "DONE"
from the builder. The builder warns about only 2 of the ~8 assets it depends
on (`build_openworld_test_level.py:213-215, 442-444`).

### 5.3 A bake network hiccup becomes silent sea-level terrain (HIGH)

`build_terrain_mesh_tiles.sample_dem` (`:215-218`) catches request failures
after retries and caches `None`; `px()` (`:220-222`) reads that as **0.0 m**,
uncounted, unreported. One transient S3 error in a multi-hour bake = a
256×256-sample patch at sea level baked into the `.bin`, with the seam skirt
purpose-built to hide the resulting cliff. The next rebake fetches fine and
the "bug" evaporates — a textbook generator of *"reruns produce different
results and I can't reproduce the problem."* (This is the only real
non-determinism left: all RNG use is properly seeded, and
`build_road_barrier.py:170-173` correctly avoids `hash()` now. But Overpass
and the DEM tiles are live external data, and nothing snapshots or checksums
raw inputs — so cross-rebuild comparisons conflate code changes with data
changes.)

### 5.4 The QA hooks themselves are run-order-dependent (MED, HIGH for trust)

Function-local statics survive across PIE runs in one editor process:
`-odseam` runs once per process (`ODRoadManager.cpp:692-703`), `-odshot`
captures its start time on the first run and never fires again
(`ODScreenshotHook.cpp:31-33`). The `-odpick`/`-odgo` map hooks are one-shot
6 s tickers that silently do nothing if the road manager or town name misses
(`ODMainMenu.cpp:137-163`). **The verification tooling behaves differently on
the second run — so even the instruments feel flaky.**

---

## 6. Process: all three safety nets were down at once

- **25 days since the last commit** (`4a8091a`, 2026-07-27). Uncommitted:
  25 tracked files, +1,480/−416 — the entire §7.10 queue implementation and
  §7.11 Z-rewrite exist **only in the working tree**. Untracked (in zero
  commits ever): `build_retaining_walls.py`, `export_retaining_wall_bin.py`,
  `ODWallManager.cpp/.h`, `ODBinSidecarUtil.h`. This is the identical pattern
  that already permanently destroyed `verify_road_fit.py` and
  `build_terrain_tiles.py`.
- **No git remote exists at all** (`git remote -v` is empty). One disk
  failure loses the repository, not just the working tree. `main` is 4
  commits stale; all Arc 7 work lives on `audit-and-arc7-rescope`.
- **Documentation stops 2026-07-29.** The retaining-wall system (four source
  files, two pipeline scripts, 85 baked tiles, built 7/30) appears in no
  `.md` file anywhere. §7.11 flagged "changed the shipping Tail of the Dragon
  road profile, not yet drive-tested" — no follow-up recorded. Whatever
  happened in August that prompted this audit is undocumented.
- **`.uproject` still doesn't enable `PythonScriptPlugin`/Geometry Script**
  (known since July, still open) — the entire headless workflow depends on
  unversioned editor settings on this one machine.
- ~1.2 GB of retired-area intermediates (skagit 551M+378M+295M, palmdale)
  still sit in `data/` — not shipped, but a live source of
  wrong-file-grabbed confusion.
- Stale `AREA_NAME` copies in apply scripts: `apply_place_labels.py:22` and
  `apply_road_centerline.py:25` still say `skagit_cascades`;
  `build_openworld_test_level.py:64` hardcodes `"oahu"` instead of importing
  `test_areas.AREA_NAME` (which it *does* import at line 57 for the water
  plane).

---

## 7. Concrete live bugs found by this audit

| # | Bug | Where | Effect |
|---|---|---|---|
| 1 | `load_stations()` now returns 7 values; `build_forest_elevation.py:88` unpacks 6 | pipeline | **Hard crash** (`ValueError`) on next forest-elevation rebuild |
| 2 | `M_Road_OneWay` never gets `PM_Asphalt` (`apply_oneway_road_material.py` sets no physmat; `apply_road_physmat.py:24` only patches `M_Road`) | Content | Spawns on one-way roads wait the full 20 s ground-snap timeout (`ODVehiclePawn.cpp:359-360`); trees can plant on one-way pavement (`ODForestManager.cpp:683-685`); grip reads 0.85 fallback, not 1.0 — **location-dependent inconsistency, live today** |
| 3 | `ELEV_PLAUSIBLE_MAX_M = 1300` is Oahu-specific but lives in the shared module and is applied unconditionally (`road_mesh_common.py:99-100`, `build_road_stations.py:557`) | pipeline | Switching to any real mountain zone silently **clips every road above 1300 m flat** |
| 4 | `TeleportToSpawn` trace ceiling is absolute Z = 1 km (`ODVehiclePawn.cpp:274`) | runtime | Any hot zone above ~1 km elevation: downward trace starts *below* the road → 20 s hang, car dropped through world |
| 5 | Showroom-at-Z=3,000,000 doesn't escape streaming — tile keys use XY only (`ODMainMenu.h:74-81` vs `ODTerrainManager.cpp:240-241`) | runtime | Whole menu duration streams and builds tiles around world origin; menu-time hitches that vary with dwell time |
| 6 | `build_forest_elevation` aside, unbounded game-thread finalize: a downtown tile builds thousands of components in one frame (`ODBuildingManager.cpp:144-204`) | runtime | Irregular tile-boundary hitches, run-to-run variable (July finding, still open) |
| 7 | `SkirtSamplePoints` never pruned on tile release (`ODRoadManager.cpp:485, 526-539`) | runtime | Seam-check accuracy depends on drive history; slow memory growth |
| 8 | Duplicated constants with drift risk: `M_PER_DEG_LAT` defined 8+ times; `DEM_ZOOM`/`NEAR_DEM_ZOOM` "match" by comment; `ROAD_TILE_SIZE_CM` vs C++ `TileSizeCm` unchecked cross-language | both | Each is a latent "one file edited, sibling silently sheared" |

---

## 8. Ranked work queue (payoff ÷ effort, reliability only)

| # | Action | Effort | Why |
|---|---|---|---|
| 0 | **Commit everything now; add a remote (private GitHub is fine); merge or fast-forward `main`** | minutes | The July source-loss failure mode is live again, with no off-disk copy at all |
| 1 | **Rebake barrier + buildings against the current `road_stations.json`** (or delete the barrier per `AUDIT.md` item 4, already the plan) | minutes–hours | Removes the invisible stale-collision world (§ 2) — the top candidate for current symptoms |
| 2 | Fix `build_forest_elevation.py:88` (7-tuple unpack) | minutes | Hard crash on next run |
| 3 | Give `M_Road_OneWay` `PM_Asphalt` (extend `apply_road_physmat.py`) | minutes | Kills the 20 s spawn lottery and one-way grip inconsistency |
| 4 | **One driver script** (`pipeline/rebuild_area.py`) that runs every stage in order for the active area — and make README point at it instead of a hand-maintained list | hours | Eliminates the partial-rebake class (§ 3.3) permanently |
| 5 | **Stamp a generation ID** (hash of `road_stations.json` + relevant constants) into every artifact/sidecar header; consumers refuse or loudly warn on mismatch; managers log the first N parse failures per prefix | hours–1 day | Turns every silent § 2/§ 3/§ 5.1 failure into a visible one |
| 6 | Make exporters **delete stale tiles** for the active area that this run didn't write (or write a manifest the runtime checks completeness against) | hours | Ends mixed-generation tilesets (§ 3.2) |
| 7 | Make DEM fetch failures **fatal** (or counted and gating) in `build_terrain_mesh_tiles.py`; invalidate `dem_tiles.npz` on bbox/zoom/OSM change | hours | Ends silent sea-level patches (§ 5.3, § 3.1) |
| 8 | Point `verify_terrain_fit.py` at the **actual `.bin`s on disk**; add the road-vs-terrain gate back; validate `sample_mesh_height` against a hand-computed fixture | 1–2 days | Makes PASS mean something (§ 4) — prerequisite for trusting any future rebake |
| 9 | Enable `PythonScriptPlugin` + Geometry Script in the `.uproject`; import `test_areas.AREA_NAME` everywhere instead of private copies; move `ELEV_PLAUSIBLE_*` into `test_areas.py` | hours | Repro on a fresh machine; kills the stale-copy class |
| 10 | Fix the QA-hook statics (reset per world/PIE run) and the one-shot `-odpick` ticker (log on miss, retry briefly) | hours | Restores trust in the instruments themselves |
| 11 | Document the retaining-wall system in `next steps.md`; record §7.11's pending Tail-of-the-Dragon drive-test as an open gate | hour | Re-arms the institutional memory that made July's audit possible |

Items 0–3 are all minutes each and all independently shippable today.

---

## 9. The one-paragraph diagnosis

The project's *reliability model* is "bake offline, verify offline, stream
dumb files" — which is the right model, but it currently holds only for the
single artifact (`road_stations.json` → terrain carve) it was built around.
Everything else — ordering, freshness, cleanup, completeness, asset presence,
runtime parse health — is enforced by convention, comments, and memory, and
every failure in that space is silent by design. During the 7/28–7/30 sprint,
work outran all three safety nets (commits, docs, full rebakes) at once, and
the world on disk quietly became internally inconsistent while the gate kept
saying PASS. Nothing here is a rethink; it is a short list of loudness and
hygiene fixes, most of them minutes-to-hours, that convert "unknown" from
"green" back into "visible."
