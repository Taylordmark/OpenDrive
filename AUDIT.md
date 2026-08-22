# OpenDrive — Implementation Method Audit

*Read-only audit, 2026-07-27. Scope: the **methods** by which the world is
built and delivered — terrain + road geometry, textures/materials, procedural
asset generation, runtime streaming architecture — judged against four bars:
scale to North America, robustness/failure modes, comparison to standard
practice, and runtime cost. Central question asked: **could this set of
processes work in a real game deployment? What are the limiting factors, why,
and what are the ways around them?***

No builds were run, no pipeline stages re-executed, no engine launched. Every
number below is measured from files on disk or read out of the source.

> **This is a dated snapshot, not a living document.** The decisions it
> records are restated as plan-of-record in
> [`next steps.md`](next%20steps.md) § 7.9 and the Arc 7 header — **those are
> authoritative and get maintained; this file does not.** What lives here is
> the *analysis and measurement* behind them, which stays true for the state
> of the repo on 2026-07-27 regardless of what changes afterwards.
>
> **Status (2026-07-28):** ranked-queue items 0–3 below (§ 7) are done —
> barrier-seed determinism, adaptive martini terrain, retired-tile cleanup,
> bridge/tunnel elevation. Several numbers in this snapshot are now stale as
> a direct result (e.g. § 3.6's H-3 burial measurements, § 8's disk-usage
> figures for the barrier). See `next steps.md` § 7.10 for the current
> numbers and what changed; this file's own numbers are left as originally
> measured, on purpose, as the historical record.
>
> **Status (2026-07-29):** the road's Z-axis algorithm itself was rewritten,
> not just item 3's call-order bug — see `next steps.md` § 7.11. Two
> passages in this file now describe an algorithm that no longer exists:
> § "Why the pipeline can't just use the road data as-is" point 2 ("Hence
> median → Gaussian → grade clamp") and § 3.6 point 2 ("`grade_smooth_
> elevation` runs afterwards and only ever lowers... a one-way ratchet").
> `grade_smooth_elevation` no longer smooths terrain at all — it's a
> ground-conforming envelope clamp (rides real DEM ground, deviates only
> where a per-highway-class `max_grade` forces a cut/bridge/tunnel) plus an
> optional grade-rate limiter for vertical-curve easing. Left as originally
> written, on purpose, as the historical record of the bug that motivated
> item 3's fix; § 7.11 is authoritative for the current algorithm.

---

## 1. Verdict up front

**The methods are individually sound and unusually well-verified for a project
this young. Under the scope Taylor confirmed during this audit — mountain
"hot zones," not the literal continent — the delivery architecture is
viable, and the work that remains is a handful of bounded engineering tasks
rather than a rethink.**

That scope decision (§ 1.1) is the single most consequential thing established
here. It moves the headline storage number from *fatal* to *comfortable*, and
it reorders everything else.

The three structural limfacs, re-scoped:

| Limfac | Measured today | Under hot-zone scope |
|---|---|---|
| **Baked content size** | 436 MB for Oahu (3,911 km road) = **114 KB/km** | **~25 GB** today; **~1.7 GB** after the § 5 fixes. Not a blocker. |
| **Draw calls / components** | **74,090** buildings on Oahu, one `UDynamicMeshComponent` each; ~6,850 resident average, ~25,000 over downtown Honolulu | Reduced (fewer cities) but **still the top runtime problem** |
| **Coordinate precision** | float32 world-cm vertices in every sidecar | **Resolved by decision** — per-location origins (§ 1.3) keep every zone at 0.5–4 cm. No longer a finding. |

Underlying all three is one structural choice repeated in four places: **the
pipeline bakes and ships the *output* of a cheap deterministic function instead
of its *input*.** That is still worth fixing — it is what turns 25 GB into
1.7 GB, and it is exactly the rule the project already holds for trees
(standing decision 2) but never applied to roads, terrain, barriers, or
buildings. It is now an optimization rather than a survival requirement.

**What moves UP in priority under this scope:** bridges and tunnels (§ 3.6).
Mountain driving roads are disproportionately viaducts, switchbacks and bores —
the Blue Ridge Parkway, Cascades passes, Rockies switchbacks. H-3's failure
mode is no longer an exotic edge case; it is the *representative* case for
every road this game now intends to ship.

### 1.1 Confirmed scope (Taylor, 2026-07-27)

> *"Pretty much only the mountainy areas… we can cut out everything from east
> Colorado to the Blue Ridge… basically just hot zones. We can leave out the
> south west for the most part as well."*

**In scope — essentially every mountain range, plus two full states:**
Cascades, Olympics, Sierra Nevada, California/Oregon/Washington coast ranges,
the US Rockies (CO/WY/MT/ID/UT incl. Wasatch, Sawtooth, Bitterroots, San
Juans), the Canadian Rockies and BC Coast Mountains, the Appalachians/Blue
Ridge/Smokies and the northeastern ranges (Adirondacks, Greens, Whites), the
Ozarks/Ouachitas, the Black Hills, **all of Hawaii**, and **all of Alaska**
(Alaska Range, Brooks, Chugach, Wrangells, Kenai). Cut: the Great Plains, the
Midwest, the Gulf, and most of the desert Southwest. No priority order set.
Oahu stays as the debug testbed — deliberately, since it exercises coastline,
dense urban grid, and dramatic relief in one bake.

Call it ~2.5M km² of the continent's 24.7M km², at rural-mountain road density
(~0.35 km/km²) rather than Oahu's urban-island 2.53 km/km².

| Scenario | Road km | Baked today | After § 5 fixes |
|---|---:|---:|---:|
| Full North America *(the old assumption)* | 8,700,000 | 947 GB | 65 GB |
| Hot zones, every road | 800,000 | 87 GB | 6.0 GB |
| **Hot zones, drivable + scenic classes only** | **230,000** | **25 GB** | **1.7 GB** |
| Hot zones, tight curated corridors | 45,000 | 4.9 GB | 0.3 GB |

Wide error bars — area × density estimates, not a route inventory — but the
*order of magnitude* is what matters, and it lands two to three orders below
the figure the audit was originally written against.

**Alaska is much cheaper than its size suggests, and the reason is worth
knowing.** Alaska is ~1.7M km² but its entire public road network is only
~26,000 km. Because the pipeline is *road-gated* — terrain is only built within
`RENDER_DISTANCE_M` of a route — Alaska costs roughly what its roads cost, not
what its area costs. The road-gating that looks like a limitation elsewhere is
precisely what makes the largest state in scope affordable. Hawaii is similar:
~7,400 km of road across all islands.

**Hot zones are natural download units.** "Download the Cascades pack" is a far
simpler product story than a continental streaming cache, and it makes the
region-download layer (§ 7 item 15) *easier*, not harder.

### 1.2 High latitude: two real costs that only appear in Alaska

Neither has been exercised by any test area so far (Oahu 21°N, Skagit 48°N,
Palmdale 34°N). Both are arithmetic, not opinion.

**Mercator tile counts explode with latitude.** A Web Mercator tile covers
`cos(lat)` as much ground, so the same km² needs `1/cos²` as many tiles:

| Zone | Tiles per km² vs Hawaii |
|---|---:|
| Hawaii (20°N) | 1.00× |
| Blue Ridge (36°N) | 1.35× |
| Cascades (47°N) | 1.90× |
| Alaska interior (63°N) | **4.28×** |
| Brooks Range (68°N) | **6.29×** |

This hits DEM fetch volume and bake time, not disk (the output tiles are
world-space, not Mercator). Combined with Alaska's small road network it is
manageable — but a naive "Alaska is 1.7M km²" estimate of *fetch* cost would be
off by 4–6×.

**The local projection breaks down at high latitude over a tall bbox.**
`build_road_stations.py` uses a single equirectangular frame for the whole
area — `lat0 = (BBOX_SNWE[0] + BBOX_SNWE[2]) / 2`, then
`m_per_deg_lon = 111132 · cos(lat0)`. Edge scale error across a ±2° bbox:

| Zone | Edge scale error |
|---|---:|
| Oahu (21.5°N, ±0.24°) | 0.17% |
| Blue Ridge (36°N, ±2°) | 2.60% |
| Cascades (47°N, ±2°) | 3.80% |
| Alaska (63°N, ±2°) | **6.91%** |
| Brooks Range (68°N, ±2°) | **8.70%** |

**Important mitigating detail: this distortion is self-consistent.** Roads,
terrain, buildings, barriers and labels all derive their frame from the same
`road_stations.json` `origin_latlon`, so nothing misaligns *internally* — the
world is merely stretched east-west relative to reality. For a game that is
mostly acceptable. It becomes a real problem only in two cases: a zone tall
enough that the stretch is visible against known geography, and — critically —
a shared coordinate space (§ 1.3). Practical mitigation is simply to keep
Alaskan zones latitudinally short (±1° ≈ 1.7% error) rather than one tall bbox.

### 1.3 Coordinate space: per-location, decided (Taylor, 2026-07-27)

**Each zone gets its own local coordinate frame and origin. There is no shared
continental coordinate space.** This is what the pipeline already does — one
`origin_latlon` per area, at the bbox center — so it is a decision to *keep*
the current design rather than change anything.

It was worth deciding explicitly, because the alternative carried two blockers
and only one was obvious:

1. **float32 vertex storage.** Across a Hawaii→Alaska→Blue Ridge extent
   (~9,000 km), float32 world-cm gives **64 cm** vertex quantization — visibly
   jagged road surfaces and suspension raycasts reading real steps.
2. **The equirectangular projection cannot span that latitude range at all.**
   `cos(21°) = 0.93` versus `cos(63°) = 0.45`, a factor of two. No single
   `lat0` works for Hawaii and Alaska. Unlike the first, this one is not fixed
   by better storage — it needs a genuinely different projection.

**What the per-location decision buys:**

- **Precision stops being a finding.** A zone a few hundred km across gives
  2–4 cm float32 vertex quantization; a compact one like Oahu gives 0.5 cm.
  Imperceptible on a 5 m station grid. § 7 item 12 (tile-relative storage)
  drops from "prerequisite" to "not needed" — delete it from the plan unless a
  single zone ever grows past ~1,000 km across.
- **The projection distortion stays bounded** (§ 1.2). Keeping each zone's
  bbox latitudinally short is now the only mitigation required, and it is free.
- **No seam reconciliation, ever.** Discrete, geographically separated ranges
  never touch, so the hardest part of a continental world simply does not
  arise.

**The cost, stated honestly:** driving continuously between zones is off the
table. Travel between ranges is a menu/fast-travel action, not a drive. Given
that the zones are separated by exactly the terrain that was deliberately cut
(plains, desert, Midwest), this costs nothing anyone would have wanted.

**One thing to watch:** each zone needs its own `PlayerStart`, its own
`AREA_NAME`-prefixed sidecars, and its own map texture — which the pipeline
already handles per-area. But `test_areas.py` currently holds *one* area as
module-level constants (`AREA_NAME`, `BBOX_SNWE`, `ORIGIN_HEIGHT_M`), swapped
by editing the file. Multiple zones means that has to become a table keyed by
zone name, with every pipeline script taking the zone as an argument. Small
refactor, but it touches every script and is best done before the second zone
exists rather than the fifth.

---

### 1.4 "Happy to burn the GPU" — which constraints that actually relaxes

Taylor's stated approach (2026-07-27): *"I'm happy to burn the GPU I have to
figure out what does and doesn't work and work backwards from a perfect project
rather than struggle getting things right forever because we're trying to meet
resource constraints."*

That is a sound way to run this phase, and most of this document's ceilings do
lift if you spend hardware. But **three of the findings here are not
GPU-bound**, and treating them as though they were will cost real time. Worth
separating explicitly:

**Genuinely GPU-elastic — spend freely, tune down later:**
- Near-terrain grid resolution (§ 3.1) — currently 50 m; raise it
- View/render distance and the far-field backdrop (§ 3.2)
- Tree and foliage density, forest radius (§ 8)
- Texture resolution, Nanite triangle budgets, Lumen quality
- Building triangle counts — 1.72M triangles for all of Oahu is nothing

**Not GPU-bound — a better card changes nothing:**
- **Draw calls (§ 5.2) are a CPU/render-thread cost.** 25,000
  `UDynamicMeshComponent`s over downtown Honolulu is render-thread submission
  and per-component overhead, not shading work. This is the single most
  important correction to make against the "burn the GPU" instinct: the merge
  fix (§ 7 item 5) is *not* optional under this philosophy, because no
  hardware purchase substitutes for it. It is also only hours of work.
- **Bake time and fetch volume** are single-threaded Python plus network I/O
  (§ 6.7 — a 527 MB `road_barrier.json` round-tripped through one
  `json.dumps`). Alaska's 4–6× tile multiplier (§ 1.2) lands here. Fixing this
  is parallelism and format work, not hardware.
- **Coordinate precision (§ 1.3, § 6.2)** is arithmetic. float32 is float32 on
  any GPU.

**Also worth flagging under this philosophy:** collision cost is CPU/physics,
not GPU — which is exactly why corridor-gating building collision (§ 5.2b) is
worth doing even though you are willing to spend on visuals.

The useful reframe: **spend hardware on how the world looks, not on how it is
delivered.** Everything in the first list makes the game better and can be
dialled back later; everything in the second is structural and gets more
expensive to fix the more content is built on top of it.

## 2. What is genuinely strong

Stated plainly, because the rest of this document is critical and the balance
matters:

- **The "carve once offline, verify before the engine loads it" model is
  right.** Arc 7.0's pivot away from reconciling three independently-refining
  async systems at runtime was the correct call, and the project's own history
  (§ 7.2 in `next steps.md`) is an unusually honest record of why. Most
  projects never get to that insight; this one got there and then acted on it.
- **The offline gate pattern is real engineering.** `verify_terrain_fit.py`
  re-requests tiles through the *actual production build function*
  (`btmt.build_tile`), not a reimplementation, and samples the real encoded
  mesh. That is the correct design for a gate, and it has caught real bugs
  (carve radius sized to the wrong grid; untagged elevation conflicts).
- **The binary-sidecar convention is clean and consistent.** Magic + LE header
  + flat payload, manual little-endian parsing on the C++ side (no struct
  casts, no LWC double/float trap), null-prefix-means-feature-off on every
  optional field. Six formats (ODT1/ODR2/ODC1/ODU1/ODW2/ODL1) all follow it.
- **Determinism is taken seriously nearly everywhere.** `FRandomStream(
  HashCombine(GetTypeHash(Key), RandomSeed))` for forest scatter,
  `np.random.default_rng(constant)` for textures/foliage,
  `random.Random(way_id)` for buildings. Exactly one place breaks it (§ 6.1)
  and it stands out precisely because everything else is disciplined.
- **The failure-mode reasoning in comments is exceptional.** The
  pymartini-transpose comment, the near/far DEM-zoom-mismatch comment, the
  triangle-winding comment, the "discrete capsules can never guarantee no gap"
  comment — these are the kind of notes that stop a bug from being
  reintroduced. Keep writing them.

---

## 3. Subsystem: terrain + road geometry

### How it works

`fetch_osm_network.py` → `build_road_stations.py` resamples every OSM chain to
5 m stations and samples the DEM **exactly once**, writing `road_stations.json`
as the single geographic source of truth. `build_terrain_mesh_tiles.py` bakes
2 km world-space grid tiles carved to that profile;
`build_road_network.py` + `export_road_mesh_bin.py` build and bucket the road
ribbon into the same grid. `verify_terrain_fit.py` gates the fit offline.

### Why the pipeline can't just use the road data as-is

Worth stating plainly, because it justifies machinery that otherwise looks like
gratuitous processing. Measured on the actual Oahu fetch
(`data/raw/oahu_test/osm.json`, 18,887 ways / 134,039 nodes):

> **Nodes carrying an `ele` tag: 1 out of 134,039.**

OSM is a 2-D dataset. Every road in it is an ordered list of lat/lon with no
Z whatsoever. There is no "as-is" elevation to use — height must be derived
from a terrain DEM, and three consequences follow that are **not** optional:

1. **Resampling to uniform 5 m stations.** OSM nodes are placed where the
   *shape* changes, not at intervals — a straight motorway run can go 500 m
   between nodes while a hairpin has them every few metres. An evenly-extruded
   ribbon cannot be built from that spacing. This is polyline regularization,
   not interpolation for its own sake.
2. **Smoothing the DEM samples.** The DEM is a ~5 m raster of *terrain*
   sampled along a line: canopy returns, vehicles present at survey time, and
   resampling artifacts make the raw profile corrugated. At racing speeds,
   ±2 m steps every 5 m are not drivable. Hence median → Gaussian → grade
   clamp.
3. **Inventing deck height across bridges.** **Bare-earth DEMs deliberately
   remove bridges** — USGS 3DEP hydro-flattens them out — so the DEM under a
   bridge is the river or valley floor, not the deck. No free elevation
   product at any resolution carries deck height; even 1 m LiDAR would not fix
   this. Something *must* synthesize it. (That the current synthesis picks the
   wrong reference is § 3.6; the need for one is not in question.)

The alternative that would remove all three — a true 3-D road centerline
dataset (HERE HD Live Map, TomTom, state DOT LiDAR centerlines) — is
commercial, non-global, and licence-incompatible with the free-public-release
goal. This is a deliberate price paid for "free and global," and it should be
recorded as such rather than rediscovered later.

**What is *not* justified by any of the above** is discarding the road
attributes OSM *does* carry — see § 3.3.

### Limfacs

**3.1 — Driveable terrain is a 50 m grid.** `NEAR_GRID_N = 41` across a 2 km
tile is 50 m between vertices, with collision. Everything within the ~150 m
carve radius of the road is dominated by the carve, so the road corridor itself
is fine — but that means *the terrain beside the road carries no real shape at
all*. No ditches, no cut banks, no embankment, no roadside relief. A 50 m
triangle cannot express any of it. This is why the near-road world reads flat
regardless of how good the DEM is; the DEM detail is sampled at z15 (~5 m) and
then thrown away by a 50 m tessellation.

*Why it is this way:* the grid size was set by build cost and file size, both
of which are consequences of baking triangles (§ 5.1).

**3.2 — Hard visual horizon at 4–6 km.** `AODTerrainManager::SpawnRadius =
400,000 cm` (4 km) and the pipeline's `RENDER_DISTANCE_M = 6000` mean nothing
beyond ~6 km is built and nothing beyond ~4–6 km is loaded. On Oahu, the
Koʻolau range is visible across the whole island in reality; here it stops
existing. For a game whose entire pitch is "real places at 1:1 scale," the
horizon is the single most legible thing to a player, and it is currently
absent. Standard practice for this is a separate, extremely cheap
low-resolution "far field" mesh (one tile per 20–50 km, no collision, 100 m+
sampling) that is *always* resident — a few MB for an entire region.

**3.3 — The road ignores almost every OSM tag that describes it — and those
tags are already fetched and sitting on disk.** The pipeline consumes `oneway`,
`bridge`/`tunnel`, and `name`/`ref`. Nothing else. Not `lanes`, not `width`,
not `surface`, not `maxspeed`, not even the `highway` class it filtered on.
Consequence: **every road in the world is the identical 9 m ribbon with 3 m
skirts.** Interstate H-1 and a Waikiki residential street are the same mesh
with the same markings.

Measured on `data/raw/oahu_test/osm.json` — this data is already downloaded,
already parsed, and already discarded:

| Tag | Present on | Used today | What it would drive |
|---|---:|---|---|
| `lanes` | 7,667 / 18,887 (41%) | ✗ | carriageway half-width |
| `surface` | 7,629 (40%) | ✗ | material + physmat (paved/gravel/dirt) |
| `maxspeed` | 3,806 (20%) | ✗ | banking gain, AI/traffic later |
| **`layer`** | **814** | ✗ | **vertical stacking order at crossings** |
| `width` | 115 (0.6%) | ✗ | explicit width where surveyed |
| `oneway` | 6,622 | ✓ | one-way texture variant |
| `bridge` / `tunnel` | 804 / 33 | ✓ | carve exclusion + gap fill |

`layer` deserves particular attention: it appears on 814 ways against 837
bridge+tunnel ways, so it covers very nearly the exact set the pipeline is
currently guessing about. It gives no deck *height*, but it states
unambiguously which of two crossing roads is on top — precisely the
disambiguation `detect_elevation_conflicts()` is reverse-engineering from
geometry alone (and, per § 3.6, getting wrong 12% of the time on H-3).

Because nothing needs re-fetching, this is hours of work, not the day
originally estimated.

**3.4 — No junction geometry.** Chains are built independently and banked
independently; where two chains meet, two ribbons interpenetrate. `next
steps.md` flags this honestly as deferred to "slice 3." At Oahu's density this
is no longer a deferrable edge case — a street grid is *mostly* junctions.

**3.5 — The one surviving gate rests on reconstructed math.**
`road_mesh_common.sample_mesh_height` carries this comment:

> *RECONSTRUCTED (2026-07-28) ... The original implementation could not be
> recovered ... it is NOT guaranteed byte-identical to whatever the original
> did internally. Re-verify verify_terrain_fit.py's output against a
> known-good baseline before trusting a borderline PASS/FAIL from it.*

Meanwhile `verify_road_fit.py` — the gate that checked *road vs. terrain*, as
opposed to *terrain vs. stations* — was deleted in the same cleanup and never
replaced, though `verify_terrain_fit.py`'s own docstring still refers to it as
though it exists. So: there is one gate left, it tests half of what used to be
tested, and its core geometric predicate is an unvalidated reimplementation.
Also note `sample_mesh_height` returns `idxs[0]` — the *first* containing
triangle in array order, not the topmost — which is arbitrary wherever geometry
overlaps, exactly the case (bridges, switchbacks) the gate most needs to judge.

**3.6 — Elevated roads and tunnels: the mechanism exists but produces the
wrong shape, and tunnels are catastrophically broken.** Measured directly on
Oahu's H-3 (`I H3`, 12.8 km in the built data), comparing the baked road deck
against the baked terrain beneath it — both read from disk, 651 samples at
~50 m:

| Deck vs. terrain | Share of H-3 |
|---|---:|
| Buried >50 m inside terrain (tunnel) | 4.6% |
| Buried 5–50 m | 7.5% |
| Buried 0.5–5 m | 17.4% |
| **On the surface (±1 m)** | **38.9%** |
| Floating 1–5 m | 20.7% |
| Floating 5–20 m | 10.6% |
| Floating >20 m | 0.3% |

Deepest burial **−429.7 m**; highest float **+23.4 m**.

There *is* a deliberate mechanism for this — `is_structure_way()` flags OSM
`bridge`/`tunnel` ways, `fill_structure_gaps()` replaces their DEM samples,
and those stations are excluded from the terrain carve so the ground is left
where it really is. The intent is right, and per § "Why the pipeline can't
just use the road data as-is," *some* synthesis here is unavoidable: bare-earth
DEMs strip bridges out, so no free elevation source anywhere carries deck
height. The question is only what the synthesis should be anchored to. Three
things defeat the current answer:

1. **The deck is a straight line between the nearest solid-ground stations,
   not a deck profile.** `fill_structure_gaps` does
   `np.interp(idx[~good], idx[good], ground[good])` — it interpolates *ground
   elevation at the abutments*. A long viaduct over a valley that falls away
   mid-span therefore falls away with it. That is why nearly 39% of H-3 sits
   within a metre of the dirt: the model is tracking the ends of the bridge,
   not its deck.
2. **`grade_smooth_elevation` runs afterwards and only ever lowers.** Its
   clamp is `h[i] = min(h[i], h[i-1] + MAX_GRADE * spacing)`, run four times
   in both directions. It is a one-way ratchet toward the ground: it can pull
   a deck down to meet terrain, and can never lift it back.
3. **Tunnels take the identical code path, and for them the interpolation is
   exactly backwards.** A tunnel's portals sit at ground level with a
   *mountain* in between, and the terrain is (correctly) not carved — so the
   road is written straight through solid rock. H-3's Tetsuo Harano Tunnels
   come out **429.7 m inside the Koʻolau range**. Undriveable: the player
   meets the mountain face at the portal.

Two compounding problems. First, **`verify_terrain_fit.py` excludes exactly
these stations by construction** — bridge/tunnel stations are skipped as
"terrain deliberately not carved toward" — so the gate is structurally blind
to the single case most likely to be wrong. Second, even where the deck *does*
float 23 m, it is a bare 9 m asphalt ribbon with a 2.5 m skirt lip hanging in
mid-air: no piers, no deck underside, no railing. H-3's entire visual identity
is its pylons. And `build_road_barrier.py` skips bridges (`open_mask =
bridge_f | in_town`), so a floating viaduct has neither guardrail nor barrier —
you can drive straight off the side.

### Ways around

0. **Elevated roads and tunnels (§ 3.6), in increasing order of effort:**
   - **Minimum-clearance pass.** After `fill_structure_gaps`, for each bridge
     station sample the terrain beneath and lift the deck to
     `max(interpolated, terrain + clearance)`. A few lines; converts "sits on
     the dirt" into "plausibly elevated" for every bridge on the continent.
   - **Exempt structure spans from the grade clamp**, or make the clamp
     two-sided, so it stops ratcheting decks downward.
   - **Fit the deck, not the ground** — a vertical curve matched to the
     approach grades at each abutment, which is what a real bridge profile is.
   - **Read `layer`** (present on 814 Oahu ways, already fetched) to resolve
     which road is on top at a crossing, instead of inferring it geometrically
     in `detect_elevation_conflicts()`.
   - **Treat tunnels as a separate case, not a bridge with a different tag.**
     Cheapest correct option: exclude tunnel spans from the road mesh
     entirely and let the road end at the portal, rather than building
     hundreds of metres of buried pavement. Better later: bore the terrain.
   - **Generate deck geometry** — piers, deck sides, railing — for any span
     whose clearance exceeds a threshold. The station data needed is already
     in `road_stations.json`.
   - **Add a bridge/tunnel-specific gate.** Assert clearance ≥ 0 and burial =
     0 on exactly the stations the main gate skips.

1. **Stop baking triangles; bake the heightfield.** A 16-bit heightmap raster
   at 50 m over Oahu's bbox is ~1,060 × 1,340 samples = **2.8 MB**, versus
   **32.4 MB** of ODT1 triangles — and a raster can be sampled at *any*
   tessellation at runtime, so 3.1 and 3.2 both become tuning knobs rather than
   rebuilds. This is how every open-world terrain system works, including UE's
   own Landscape.
2. **Add an always-resident far field** built from a coarse global DEM (z8–z10),
   one mesh per 20–50 km, no collision. Costs single-digit MB for a region and
   buys back the horizon.
3. **Drive the cross-section from OSM tags.** `lanes` (default by `highway`
   class) → half-width; `surface` → material/physmat; `oneway` already works.
   This is a change to `build_road_chunks`'s `profile` list and one lookup
   table — genuinely small work for the largest visible realism gain available.
4. **Restore a road-vs-terrain gate, and validate `sample_mesh_height` against
   a hand-computed fixture** before trusting any future PASS. A gate you are not
   sure about is worse than no gate, because it converts "unknown" into "green."
5. **Junctions:** the standard cheap approach is to flatten and cap the
   intersection polygon — detect shared OSM nodes, clip both ribbons back, emit
   a flat junction plate at the mean elevation. Not free, but bounded.

---

## 4. Subsystem: textures & materials

### How it works

`make_road_texture.py` generates a 1024² tileable asphalt texture (value noise,
lane polish bands, painted markings) in two variants (two-way / one-way).
`classify_biomes.py` classifies USGS aerial imagery by RGB heuristic into
forest/grass/rock/water weights, mapped to 14 terrain archetypes.
`build_map_texture.py` reuses that classification for the mini-map background.

### Limfacs

**4.1 — One texture for every road on the continent, tinted by the wrong
climate.** `make_road_texture.py` hardcodes `STAGE = "tail_of_the_dragon"` and
reads that stage's `climate.json` for its moisture darkening. The resulting
`T_Road.png` is the material used by `AODRoadManager` for the *entire
open-world network* (confirmed in `ODRoadManager.cpp`). So Oahu's roads are
currently tinted by Deals Gap's rainfall — and every road in North America
would be, too. The wet-road feature (Arc 2.2), which is genuinely nice work, is
structurally unable to vary across the world it was built for.

**4.2 — The biome classifier is a color heuristic on aerial photos, tuned for
one region, applied everywhere.** `pick_biome`'s own docstring says its
elevation/latitude bands are "tuned for the Skagit/Cascades loop specifically —
not a general-purpose elevation→biome model." It is now running on Oahu and is
the intended path for all of NA. The failure modes are inherent to the input,
not the code: aerial imagery varies by season, sun angle, sensor, and tile
vintage, so the same ground classifies differently depending on when USGS
happened to fly it. One such failure has already been found and patched around
(shadowed slopes reading as water, 22% of pixels — fixed in
`build_map_texture.py` only, leaving `classify_biomes.py`'s copy still wrong).

**4.3 — The imagery source is United States only.** `classify_biomes.py` fetches
from `basemap.nationalmap.gov/.../USGSImageryOnly`. Canada and Mexico return
nothing. For an arc literally named "North America," the land-cover input
covers roughly 60% of it.

**4.4 — Two copies of the classifier, "kept in sync by eye."** `classify_rgb`
exists in both `classify_biomes.py` and `make_detail_textures.py`, with a
comment saying they are synchronized manually rather than by import. They have
already diverged once (the water fix).

**4.5 — 14 archetypes over 3 tree meshes and 1 bush.** The recipe table is a
weighting over the existing mesh set. A rainforest and a boreal forest are the
same four meshes at different mix ratios and scales. This is a reasonable
staging decision, but it means "14 biomes" currently describes the data model,
not what a player sees.

### Ways around

1. **Move road texture generation out of the Dragon's stage folder** and key
   moisture off the *area's* climate, not a hardcoded stage. Small change, and
   it unblocks per-region road character.
2. **Adopt ESA WorldCover 10 m as the classification source** — already the
   plan (§ 7.3), and it resolves 4.2 *and* 4.3 at once: it is a global,
   seasonally-normalized, purpose-built land-cover product, not a photo. This
   is the single highest-leverage item in this section and it deletes code
   rather than adding it.
3. **Delete one copy of `classify_rgb`** and import the other. "Kept in sync by
   eye" is a defect waiting for a quiet moment.
4. **Make the C++/Python biome enum a generated file.** Right now
   `classify_biomes.py` and `ODForestManager.h` share an ordering by comment
   only; a reorder on one side silently reclassifies the world. Emit the enum
   from one source.

---

## 5. Subsystem: procedural asset generation

### How it works

Forest: deterministic tile-seeded HISM scatter, zero per-tree storage — the
model the whole project is proud of, and rightly. Buildings: OSM footprints
extruded to archetype height bands, seeded by way ID. Barrier: a continuous
4 m collision ribbon 13 m either side of every road, plus decorative tree/bush
points. Cars, trees, foliage, detail textures: all pipeline-generated.

### Limfacs

**5.1 — The zero-per-instance rule was applied to trees and to nothing else.**
This is the central finding of the audit. Taylor's rule ("nothing per-instance
is ever stored; everything visible is generated deterministically") is enforced
rigorously for the 90,000 trees — and then the pipeline writes 436 MB of
explicit triangles for the road, terrain, barrier, and buildings, all of which
are *equally* derivable from compact inputs:

| Output | Baked size (Oahu) | Derivable from | Est. compact size |
|---|---:|---|---:|
| Road mesh (ODR2) | 114.3 MB | centerline stations + cross-section | ~12 MB |
| Barrier (ODW2) | 213.2 MB | **the same stations**, offset 13 m | **~0 MB** |
| Terrain (ODT1) | 32.4 MB | heightfield raster | ~2.8 MB |
| Buildings (ODU1) | 76.2 MB | footprint polygons + height | ~15 MB |
| **Total** | **436.1 MB** | | **~30 MB** |

The barrier line is the clearest case: it is the **largest single consumer of
disk in the project**, larger than the road and terrain combined, and it
contains *no information at all* that is not already in `road_stations.json`.
It is a 13 m offset and a 4 m extrusion of data the runtime already loads.

**5.2 — One `UDynamicMeshComponent` per building.** 74,090 buildings on Oahu.
`AODBuildingManager::SpawnRadius = 250,000 cm` over 2 km tiles → a 5×5 = 25-tile
resident window. At the island average (274 buildings/tile) that is ~6,850
components; the three densest tiles hold 3,018 / 2,675 / 2,634 each, so a
resident window over downtown Honolulu is on the order of **25,000 separate
components** — each a draw call, each a registered scene component, each with
its own `EnableComplexAsSimpleCollision` physics body, all constructed on the
game thread inside `FinalizeTile`.

The instructive detail: **the triangles are not the problem.** All 74,090
buildings total 1.72M triangles — trivial for any modern GPU. The entire cost
is per-object overhead. This is a pure architecture problem with a pure
architecture fix.

**5.2b — Building collision: corridor-gated (Taylor, 2026-07-27).** Every one
of the 74,090 buildings currently gets `EnableComplexAsSimpleCollision` — a
full per-triangle collision cook, on the game thread, for buildings the player
can never touch. Confirmed direction: **collide only within reach of the road,
mirroring the terrain near/far split that already exists.** A building's
distance to the nearest road station is already computed offline by
`build_building_mesh.py` (it runs the road-exclusion gate with exactly that
lookup), so the classification is free — add a `has_collision` byte to the ODU1
record, the same way ODT1 already carries one per tile.

This composes cleanly with the per-tile merge in § 7 item 5: merge the
no-collision majority into one visual mesh per tile, and keep the small
corridor-adjacent set as individual collidable components. Best of both — one
draw call for the skyline, real collision only where it can matter.

**5.3 — `UDynamicMeshComponent` is the wrong runtime primitive for static
world geometry.** It was chosen for a good reason (build arbitrary geometry at
runtime from data files, no editor step, no plugin) and it is used for terrain,
road, buildings, and barrier walls. But it gets **no Nanite, no LODs, no
instancing, no distance fields, no static lighting path**, and its collision is
cooked at runtime on the game thread. Every one of those is something UE gives
`UStaticMesh` for free. At Tail of the Dragon's scale this cost nothing; at
Oahu's it is the dominant runtime cost.

**5.4 — The barrier is an invisible wall. Confirmed as a stopgap, to be
replaced (Taylor, 2026-07-27).** A 4 m tall invisible collision ribbon 13 m
from the centerline of every road outside a populated tile. It solves the real
problem it was built for (the car escaping into no-collision far terrain,
verified live) and the *continuous mesh vs. discrete capsules* reasoning behind
it is correct and well-documented. But as shipped it is the single most
criticized mechanic in driving games, and on a road *network* — as opposed to
one canyon highway — it walls off every rural street from its own surroundings.

**Direction confirmed:** replace the hard wall with an out-of-bounds reset,
*but keep a light visual boundary so the player can see where the edge is.*
That combination is strictly better than what exists: it removes the
invisible-wall feel, removes 213 MB of baked geometry, and is more honest to
the player than a wall they cannot see. Implementation sketch:

- Keep the deterministic tree/bush points as the visual cue — they already
  exist and already render as HISM instances with no collision. Optionally
  thin them so they read as a boundary hint rather than a hedge.
- Drop the ODW2 wall mesh entirely (no runtime derivation needed either, which
  is *better* than this audit's original "derive it at runtime" suggestion).
- Reuse `AODRaceManager`'s existing respawn-at-last-gate machinery for the
  reset, triggered on sustained `gnd=0` / distance-from-centerline rather than
  on contact.

**5.5 — Building height is a guess wherever OSM is silent.** `AmericanTown` →
1–3 stories, `City` → 8–35 stories, random per way ID. Real `building:levels`
tags win when present. Fine as a proof; at scale, a 35-story random building in
a low-rise district is a visible error, and the `City`/`AmericanTown` split is
made per 2 km tile, which is coarser than real urban form.

### Ways around

1. **Delete the baked barrier entirely and derive it at runtime** from the
   centerline the road manager already has in memory. Immediate: −213 MB
   (−49% of all baked content) for a few dozen lines of C++. This is the single
   cheapest large win in the audit.
2. **Merge per tile.** Buildings: one merged `FDynamicMesh3` per tile (they all
   share `M_BuildingFacade`) → 25 draw calls instead of 25,000. Same for road
   chunks within a tile. This is a change inside `FinalizeTile`, not an
   architecture rewrite, and it can ship before anything else in this document.
3. **Bake to `UStaticMesh` assets in the pipeline** rather than parsing raw
   triangles at runtime. Gets Nanite, LODs, and offline-cooked collision. The
   headless-Python editor automation this project already runs is exactly the
   tool for that step.
4. **Store footprints and stations, generate meshes on load.** The generation
   math already exists in Python; porting the road ribbon and building
   extrusion to C++ is bounded, well-specified work, and it is the change that
   turns ~950 GB into ~85 GB.
5. **Reconsider the wall as scenery.** Options that preserve the guarantee
   without the invisible-wall feel: make the barrier mesh *visible* as a
   guardrail/embankment/hedge; or replace hard collision with a soft
   out-of-bounds reset (the race framework already has respawn-at-last-gate);
   or keep collision but only where the terrain actually drops away.

---

## 6. Subsystem: runtime streaming architecture

### How it works

Five actors — `AODTerrainManager`, `AODRoadManager`, `AODBuildingManager`,
`AODBarrierManager`, `AODForestManager` — each tick, compute the player's tile
key, ring-iterate outward to `SpawnRadius`, dispatch async file loads
(`Async(ThreadPool)` → parse → `AsyncTask(GameThread)` → build components),
and release tiles past a hysteresis margin.

### Limfacs

**6.1 — A real determinism bug, empirically confirmed.**
`pipeline/build_road_barrier.py:168`:

```python
seed = hash((seg["name"], i, side)) & 0xFFFFFFFF
```

Python salts `str` hashes per process unless `PYTHONHASHSEED` is set (it is not
set here). Verified by running it twice:

```
run1 2321597332
run2 2180786600
```

Every barrier tree and bush is placed from a seed that **changes on every
rebuild**. This breaks standing decision 2's determinism promise, means
"regenerable content" does not actually regenerate the same world, and would
break the multiplayer-friendliness the plan cites as the reason for the rule.
It is also the only such defect in the project — everything else seeds
correctly. One-line fix (`zlib.crc32` or `hashlib` over the encoded tuple).

**6.2 — float32 world coordinates cap the world at regional scale.** Every
sidecar stores vertices as `float32` in absolute world cm. Precision by
distance from origin:

| Distance from origin | float32 ULP |
|---:|---:|
| 67 km (Oahu span) | 0.50 cm |
| 500 km | 4 cm |
| 2,000 km | 16 cm |
| 5,000 km (NA span) | **32 cm** |

Below ~84 km from origin, precision is better than 1 cm. Beyond that it decays
linearly. Note this is lost **at bake time**, before UE's own LWC doubles ever
see the data — so no engine setting can recover it. Also confirmed: there are
no `[/Script/Engine...]` world-bounds, origin-rebasing, or LWC entries in
`DefaultEngine.ini` at all. Continental precision is currently unaddressed at
every layer.

**6.3 — The ring-iteration `Tick()` is copy-pasted four times.** Terrain,
road, building, and barrier managers contain near-identical dispatch/despawn
loops with only the tile struct and radii differing. Any fix to the streaming
policy — prioritization, budget, hitch avoidance — must now be made four times
and will eventually be made three times.

**6.4 — Tile construction happens on the game thread and is unbudgeted.**
`MaxNewLoadsPerTick` throttles *dispatch*, but `FinalizeTile` — vertex-by-vertex
`AppendVertex`, triangle-by-triangle `AppendTriangle`, normals, then a
collision cook — runs entirely on the game thread with no cap on how many
completions land in one frame. A tile with 3,018 buildings finalizes as 3,018
component constructions in a single frame. This is almost certainly the
mechanism behind the streaming-latency symptoms recorded in § 7.5.

**6.5 — `MissingTiles` grows without bound.** Every probed-and-absent key is
remembered forever. Bounded per test area; across a continent-crossing drive it
is an ever-growing `TSet` and, more importantly, a permanent record that
prevents a tile from ever being retried after a region download completes.

**6.6 — Everything loads from local `Content/`; there is no fetch path.**
`DirectoriesToAlwaysStageAsUFS=(Path="OpenDrive/Masks")` stages the whole
directory into the package. Today that directory is **728 MB**, of which
roughly **208 MB is dead** — 1,605 stale Skagit terrain tiles, 697 Skagit road
tiles, 163 Skagit barrier tiles, and 18 Palmdale road tiles from retired test
areas, all of which would be cooked and shipped. There is no region-download,
no cache, no eviction. For NA this is the deployment blocker that makes the
storage number in § 1 fatal rather than merely large: 950 GB cannot be a
package, and there is currently no mechanism by which it could be anything else.

**6.7 — Build-machine limits are already being hit.** The Oahu intermediates
are ~978 MB of JSON, including a **527 MB `road_barrier.json`** written with a
single `json.dumps` and read back with a single `json.loads` — multi-GB peak
Python memory for one island. `sample_dem` loops over vertices in pure Python.
`build_road_stations.py` is 560 lines of single-threaded numpy. A full NA bake
on this pipeline is not a long run; it is an impossible one.

### Ways around

1. **Fix the `hash()` seed.** One line. Do it before the next barrier rebuild,
   because every rebuild until then produces a different world.
2. **Store vertices tile-relative.** Tiles already have integer keys; store
   float32 offsets *within* the 2 km tile (precision ~0.0001 cm) and put the
   tile origin in the component's double-precision transform. This is a small
   format bump (ODT2/ODR3) that removes 6.2 completely and makes future
   origin-rebasing nearly free.
3. **Extract one templated tile-streamer base class.** All four managers reduce
   to "parse this payload, build these components." Fixes 6.3, and makes 6.4
   and 6.5 single-site fixes.
4. **Budget `FinalizeTile` by work, not by count** — a per-frame vertex/component
   budget with a continuation queue, prioritized by distance to player.
5. **Prune `Content/OpenDrive/Masks` and stage per-area.** Deleting retired test
   areas is a 208 MB win available today; a per-area staging rule prevents it
   recurring.
6. **Design the region-download layer now, not at 7.4.** Whatever the storage
   number turns out to be after § 5's fixes, a continental game ships a small
   base install plus on-demand regions. That decision shapes the sidecar format,
   the manifest, and the cache — it is much cheaper to decide before another
   format revision than after.
7. **Parallelize and stream the bake.** Per-tile multiprocessing, and stop
   round-tripping through monolithic JSON — write the binary sidecars directly
   or use a chunked/streaming format.

---

## 7. Ranked recommendations

Ranked by (payoff ÷ effort). Items 1–4 are all small and all independently
shippable.

*Re-ranked 2026-07-27 against the confirmed hot-zone scope (§ 1.1), then
re-ordered again per the terrain-detail decisions in § 9.1 — terrain
resolution moves to the front by Taylor's call.*

| # | Action | Effort | Payoff |
|---|---|---|---|
| 0 | ~~Fix `build_road_barrier.py:168`'s `hash()` seed~~ — **done**, was already fixed by the time this audit was written | minutes | Restores determinism |
| 1 | ~~Adaptive martini terrain, base grid 257 (7.81 m)~~ — **done, 2026-07-28** — see `next steps.md` § 7.10 for the measured economics (worse than estimated below) and the seam fix this table didn't anticipate | days | ~25 m-grid storage for 8 m-grid mountains *(not what was measured — actual retention 22–44%, not 10–15%)* |
| 2 | ~~Delete retired-area tiles from `Content/OpenDrive/Masks`~~ — **moot**, no retired-area tiles existed to delete | minutes | −208 MB shipped |
| 3 | ~~Bridge minimum-clearance pass; exempt structure spans from the one-way grade clamp; drop tunnel spans from the road mesh~~ — **done, 2026-07-28**, though the actual root cause was call-order (fill-before-smooth), not just the clamp — see `next steps.md` § 7.10 | hours | Viaducts sitting on the dirt, tunnels 430 m inside mountains (§ 3.6) — the representative case for mountain roads |
| 4 | Replace the barrier with an out-of-bounds reset + light visual boundary; delete ODW2 | hours | **−213 MB**, removes the invisible wall, and the player can finally see the edge (§ 5.4) |
| 5 | Merge buildings per tile + corridor-gate their collision | hours | ~25,000 draw calls → ~25; drops most collision cooks entirely (§ 5.2, § 5.2b). Top *runtime* item — and **CPU-bound, so no GPU upgrade substitutes for it** (§ 1.4) |
| 6 | Drive road width/lanes/surface/`layer` from OSM tags **already on disk** | hours | Largest available realism gain; makes a network look like a network. No re-fetch — 41% of ways already carry `lanes` |
| 7 | Validate `sample_mesh_height` against a fixture; restore a road-vs-terrain gate; add a bridge-clearance gate | 1 day | The gate is trusted more than it has earned — and item 3 needs a gate to confirm it worked |
| 8 | Add an always-resident coarse far-field terrain mesh | 1–2 days | Buys back the horizon. Worth *more* under this scope, not less — mountain backdrops are the entire point of a mountain hot zone |
| 9 | Extract a shared tile-streamer base; budget `FinalizeTile` | 2–3 days | Fixes 6.3/6.4/6.5 at one site; likely resolves the § 7.5 latency mystery |
| 10 | ~~Raise near-terrain resolution~~ — **promoted to item 1** per § 9.1. Remaining here: **D1 generic tiling detail normal** | hours | Cheap close-range surface richness, orthogonal to the geometry path |
| 11 | Swap terrain from baked triangles to a baked heightfield raster | 3–5 days | Makes item 10 a runtime knob rather than a rebuild; −29 MB per region |
| 12 | ~~Tile-relative vertex storage~~ — **dropped**, made unnecessary by the per-location coordinate decision (§ 1.3). Instead: **make `test_areas.py` a multi-zone table** rather than one set of module constants | half day | Prerequisite for having more than one zone at all; cheapest before the second zone exists |
| 13 | ESA WorldCover replaces the imagery colour classifier | 1 week | Season-stable, and fixes Canada (Canadian Rockies are in scope; USGS imagery is not) |
| 14 | Move road/building mesh generation to runtime from compact inputs | 1–2 weeks | **25 GB → 1.7 GB.** Demoted from "decides whether Arc 7 is possible" to "makes the download pleasant" |
| 15 | Region-download layer, keyed on hot zones | multi-session | *Easier* under this scope — "download the Smokies pack" is a natural unit |

---

## 8. Smaller findings

**Documentation drift**

- `README.md` states the open-world test area is Oahu (correct —
  `test_areas.py` has `AREA_NAME = "oahu"`), but `next steps.md` §§ 7.0–7.5 is
  written entirely against Skagit/Cascades. The plan of record lags the code by
  one test area.
- `verify_terrain_fit.py`'s docstring refers to `verify_road_fit.py` as an
  existing companion gate. It was deleted.
- **`next steps.md` references two deleted scripts as though they still
  exist** — `build_terrain_tiles.py` (lines 44, 461, 833, 907, 1169) and
  `verify_road_fit.py` (lines 507, 516, 550, 921, 1176). Both are gone and
  unrecoverable (see the work-loss table below). *`README.md` is correct on
  both counts — it lists `build_terrain_mesh_tiles.py` and
  `verify_terrain_fit.py`. An earlier draft of this audit claimed otherwise;
  that was wrong.*
- Several source comments carry dates of 2026-07-28 (tomorrow) — e.g.
  `build_terrain_mesh_tiles.py`'s `FAR_GRID_N` note and
  `road_mesh_common.sample_mesh_height`'s reconstruction note.

**Stale defaults in C++ (harmless where levels set them explicitly, but a
freshly-spawned actor gets the wrong area):**

- `AODRoadManager::RoadTilePrefix` = `road_palmdale_test` (two areas stale)
- `AODTerrainManager::TerrainTilePrefix` = `terrain_skagit_cascades_test`
- `AODBuildingManager::BuildingTilePrefix` = `buildings_skagit_cascades_test`
- `AODBarrierManager::BarrierTilePrefix` = `barrier_skagit_cascades_test`

**A recurring pattern worth naming: work is being lost, not just misplaced**

Three separate pieces of real pipeline code have been deleted and proved
unrecoverable, all for the same reason — they were never committed:

| Lost | Consequence |
|---|---|
| `pipeline/verify_road_fit.py` | The road-vs-terrain gate no longer exists; only terrain-vs-stations survives (§ 3.5) |
| `road_mesh_common.sample_mesh_height` | Reconstructed from scratch; explicitly not validated against the original, yet every fit number depends on it |
| `pipeline/build_terrain_tiles.py` | The martini implementation Option C was assumed to "already own" (§ 9.1) |

`git log --all` returns nothing for any of them. This is a distinct failure
mode from the well-documented "one-off `apply_*.py` script doesn't survive a
rebuild" theme — that one loses *effects*, this one loses *source*. Given 71
files are currently untracked including 11 new C++ classes (below), the
exposure is live right now, and it is the cheapest problem in this document to
fix: commit early and often, even to a scratch branch.

**Reproducibility**

- `OpenDriveUE.uproject` does not enable `PythonScriptPlugin` or the Geometry
  Script plugin, but the entire headless workflow depends on them. They are
  currently enabled at engine level on this machine only — a fresh clone cannot
  run the documented rebuild without an undocumented manual step. `README.md`
  mentions this in the bootstrap note; it should be in the `.uproject` instead.

**Forest coverage vs. terrain coverage**

- `AODForestManager::SpawnRadius` = 30,000 cm (300 m) against a terrain radius
  of 4 km. Trees exist in a 300 m bubble around the player on otherwise bare
  ground. At 200 mph that bubble is crossed in 3.4 seconds, so pop-in is
  structural rather than incidental.

**Repo state**

- 32 tracked files changed since `f18054a` (+3,443 / −1,203) plus **71
  untracked files**, including 11 new C++ classes. The pre-commit review noted
  in `next steps.md` is overdue and getting more expensive.
- Git reports CRLF/LF normalization warnings across most source files; worth
  settling with a `.gitattributes` before a large commit makes the diff
  unreadable.

---

## 9. Scope boundary, decisions, and open questions

### 9.1 Terrain-detail decisions (Taylor, 2026-07-27)

Three choices, recorded because they change § 7's ordering:

1. **Skip the verification screenshot — commit to raising resolution.** The
   reasoning stands on its own (61-px facets at 1 km; ~100× of the fetched DEM
   currently discarded).
2. **Option C — adaptive martini triangulation**, base grid 257 (7.81 m).
3. **Do it first**, ahead of items 3/5/6.

**Honest risk note, recorded rather than argued:** these three choices each
independently increase risk, and they compound. C is the highest-complexity
option; it is the one option whose output cannot be judged by "does it look
better" alone (adaptive quality is deliberately non-uniform, so it needs a
metric and a seam check); its implementation turned out to be a rewrite rather
than a revival (see Option C above); and skipping the look-first step means
there is no confirmation that 50 m was the actual problem before the work
starts. Doing it first also defers three items that are each *hours* of work
with confirmed defects behind them — most notably viaducts sitting on the dirt
and tunnels built 430 m inside mountains (§ 3.6), which will be visible in the
very mountain terrain this work is meant to improve.

**Two cheap hedges that preserve the decision while cutting most of the risk:**

- **Bake one tile before building the pipeline.** Pick a known-steep Oahu tile,
  run RTIN over it at base 257, and look. Minutes, and it validates both the
  approach and the premise without the look-first step being a separate task.
- **Bundle item 3 (bridges) in.** It is hours, it changes the same mountain
  corridor, and it wants the same verification drive afterward — so it costs
  almost nothing extra to carry along and avoids drive-testing new terrain
  against known-broken viaducts.

**Also decided:** D1 (generic tiling detail normal) is worth doing regardless
and is orthogonal to all of the above. D2 (DEM-derived per-tile normal map) is
dropped — it costs the same as the geometry and delivers less.

### 9.2 Scope boundary and open questions

### What this audit did not cover

Deliberately out of scope (Taylor's subsystem selection): vehicle physics
(`UODVehicleMovement`), audio DSP (`UODEngineSynth`/`UODRoadNoiseSynth`), the
race framework, HUD/menu code, and the Tail of the Dragon stage pipeline.
**Silence here is not a clean bill of health** — those systems were not read.

Also not done, by agreement (read-only audit): no build was compiled, no
pipeline stage re-run, no packaged build produced, no live `-game` session. So
the following remain unverified: that the 71 untracked files including 11 new
C++ classes currently compile; that the packaged build still works against the
present Content; and the actual measured frame cost of the draw-call finding in
§ 5.2, which is argued from component counts rather than a profile capture.

### Questions whose answers would change the recommendations

These are genuine unknowns where I had to assume, and where a different answer
materially reorders § 7.

**Resolved during this audit (Taylor, 2026-07-27):**

1. ~~**Is "drive anywhere in NA" literal?**~~ **No — mountain hot zones.** See
   § 1.1. Reduces the storage limfac by two to three orders of magnitude and
   drives the § 7 re-ranking.
2. ~~**Are buildings scenery or obstacles?**~~ **Collision only near the road**,
   mirroring the terrain near/far split. See § 5.2b.
3. ~~**Is the barrier permanent or a stopgap?**~~ **Stopgap — replace it, but
   keep it slightly visible** so players can see the edge. See § 5.4.

4. ~~**Which hot zones?**~~ **Essentially all mountain ranges, plus all of
   Hawaii and all of Alaska; no priority order.** See § 1.1. Alaska brings the
   high-latitude costs in § 1.2, which no test area has exercised yet.
5. ~~**Shared coordinate space or per-zone origins?**~~ **Per-location, decided.**
   See § 1.3. Keeps the current design, removes coordinate precision from the
   findings entirely, and drops § 7 item 12's original scope. Cost: travel
   between zones is fast-travel, not driving.
6. ~~**What is "a mid GPU at 1080p"?**~~ **Superseded** — Taylor is
   deliberately not optimizing to a hardware target during this phase (§ 1.4).
   The "60 fps on a mid GPU at 1080p" line in **`next steps.md`'s Cross-cutting
   section** (not `README.md`) should be struck or marked deferred so it stops
   reading as an active constraint.

**Still open:**
7. **Is § 7.5's "open-world map reads too dark" bug still live?** Found on the
   Skagit test area, since superseded by Oahu. May be fixed, moot, or silently
   still present. *(The "missing Marysville coastal road" item from the same
   section is closed — Taylor, 2026-07-27: almost certainly a generation-time
   bounding-box error, not a pipeline defect.)*
8. **Nobody has ever measured how the terrain looks *away* from the road.**
   *(Restated — the original phrasing didn't land.)* The terrain is a grid with
   a vertex every 50 m. Within ~150 m of the road, the **carve** forcibly
   stamps the road's own elevation into that grid, so the ground there matches
   the road exactly — it looks right and it measures right. Beyond ~150 m there
   is no carve: it is raw DEM sampled every 50 m, and nothing more.

   The catch is that **every fit number in this project — including the
   `verify_terrain_fit.py` median of 0.018 m — is sampled *at road stations*,
   i.e. precisely where the carve guarantees a good answer.** The gate is
   measuring the one place that cannot be wrong. On Oahu's gentler ground that
   is fine. In the Rockies or the Alaska Range, a cliff face or a ravine 200 m
   off the road is being represented by triangles 50 m across — which will read
   as faceted, origami-looking terrain, and is exactly the thing a mountain
   game cannot afford to get wrong.

   So: item 10 (raise the grid resolution) assumes this is a problem, on
   reasoning rather than evidence. It should be *looked at* — one screenshot
   from a vantage point off the road in steep terrain settles it — before
   anyone spends days on it. Given § 1.4, the fix if needed is cheap on the GPU
   side; the real cost is bake time and fetch volume (§ 1.2).

   **The options, if it does need fixing.** Every row assumes the current
   2 km tile and is measured against today's 41×41 grid. "Hot zones" applies
   the current 8.5 KB per km of road, scaled, over 230,000 km:

   | Grid | Spacing | Tris/tile | Cost | Oahu terrain | Hot zones |
   |---|---:|---:|---:|---:|---:|
   | **41² (today)** | 50 m | 3,200 | 1.0× | 32 MB | 1.9 GB |
   | 81² | 25 m | 12,800 | 4.0× | 128 MB | 7.4 GB |
   | 121² | 16.7 m | 28,800 | 8.9× | 287 MB | 16.5 GB |
   | 201² | 10 m | 80,000 | 24.5× | 795 MB | 45.7 GB |
   | 401² | 5 m | 320,000 | 98× | 3.1 GB | 182 GB |

   Source ceiling: Terrarium z15 is ~4.8 m/px at 21°N and ~3.6 m/px at 47°N, so
   **a 10 m grid is comfortably within the DEM you already fetch.** Going below
   ~5 m needs z16+, which is 4× the tiles and buys detail the source doesn't
   actually contain.

   **Option A — raise the grid uniformly.** Simplest possible change: one
   constant, `NEAR_GRID_N`. Nothing else in the pipeline moves. Costs exactly
   what the table says. At 81² it is nearly free; at 201² it is a real
   commitment.

   **Option B — more distance tiers.** Today there are two (near 50 m with
   collision, far 100 m without). Add a corridor tier at 10 m for the first
   ~100 m either side of the road, keep 50 m out to a few km, coarsen beyond.
   Concentrates detail where the player actually is, for a fraction of Option
   A's cost. **Caveat:** the seam bug documented in `FAR_DEM_ZOOM`'s comment is
   exactly this failure mode — every tier boundary must keep `(N-1)` an integer
   multiple of the coarser tier's, or you get walls at tile edges. More tiers,
   more pairs to keep honest.

   **Option C — adaptive triangulation (martini). ← SELECTED, see § 9.1.**
   Error-driven simplification: dense triangles where relief demands them,
   sparse on flat ground. The *correct* tool for "mountains need detail,
   valleys don't." Best quality per byte by a wide margin.

   **Correction to this option as first written:** it was listed as "the
   project already owns the code." **It does not.**
   `pipeline/build_terrain_tiles.py` was deleted in the Arc 7.0 pivot and
   **was never committed** (`git log --all` returns nothing for it) — the same
   unrecoverable-deletion pattern as `verify_road_fit.py` and
   `sample_mesh_height` (§ 8). What actually survives is `pymartini 0.5.1`,
   still installed, and the *knowledge* preserved in code comments. The
   implementation is a rewrite, not a revival. That does not invalidate the
   choice, but it should be made on accurate information.

   **Partly offsetting good news:** the worst historical martini bug — carving
   before pymartini's `.T` transpose, which stamped every station at its
   diagonal mirror — **largely does not apply here.**
   `build_terrain_mesh_tiles.py`'s own comment explains why: the new tiles are
   plain world-space meter grids the script fully controls, with "no pymartini
   array-order convention to match here at all." Running RTIN over that grid
   rather than round-tripping through quantized-mesh designs the bug class out.

   **Hard constraint discovered: RTIN requires a grid of exactly 2^k + 1.**
   The current `NEAR_GRID_N = 41` is not valid and must change:

   | Grid | Spacing over a 2 km tile |
   |---:|---:|
   | 65 | 31.25 m |
   | 129 | 15.62 m |
   | **257** | **7.81 m** ← recommended base |
   | 513 | 3.91 m (oversamples z15) |

   **257 is the right base grid**: z15 Terrarium is ~4.8 m/px, so 7.81 m
   captures essentially everything the source contains, while 513 would
   oversample data that isn't there.

   **The economics are better than the uniform-grid table suggests, and this is
   the real case for C.** With adaptive triangulation the base grid sets the
   *ceiling*, not the cost — output vertex count is whatever MAX_ERROR keeps.
   At ~1 m error, steep terrain plausibly retains 10–15% of a 257² grid
   (≈6,600–9,900 verts) and flat ground retains 1–2%. That lands near uniform
   81²'s storage while delivering 257²'s detail exactly where relief demands
   it. **Roughly 25 m-grid cost for 8 m-grid mountains** — which is precisely
   why this option was chosen.

   **The main engineering risk is seams, and it should be designed up front
   rather than discovered.** Error-driven vertex selection can pick different
   vertices on either side of a shared tile edge, producing cracks. Standard
   mitigations: force full-resolution tile edges, or constrain edge vertex
   selection to a shared deterministic rule. This is the same failure class as
   the documented `FAR_DEM_ZOOM` seam bug — that one produced walls the width
   of a whole tile edge, so it is worth taking seriously before the first bake,
   not after.

   **Keep Option A as the fallback.** If C stalls on seams or MAX_ERROR tuning,
   `NEAR_GRID_N = 129` remains a one-constant change that captures most of the
   benefit. Nothing about pursuing C forecloses it.

   **Option D — keep geometry coarse, fake it in the material.** This is the
   intuitive answer and it is *partly* right, but it does not solve the problem
   in Q8. Three reasons, and one correction to how it was first listed.

   **D fakes lighting, not shape — the silhouette stays faceted.** A normal map
   changes how a surface responds to light; it cannot change the polygon's
   outline against the sky. Mountains are read primarily by their skyline.
   Facet size in screen pixels, 1080p / 90° FOV:

   | Distance | 50 m facet | 100 m facet |
   |---:|---:|---:|
   | 500 m | **122 px** | 241 px |
   | 1 km | **61 px** | 122 px |
   | 2 km | 31 px | 61 px |
   | 6 km | 10 px | 20 px |
   | 20 km | 3 px | 6 px |

   A 61-px flat facet on a ridgeline 1 km away is a visibly polygonal mountain,
   not a shading subtlety. Facets vanish only past ~20 km — so **D is genuinely
   correct for the far field, and the far field is already fine** (the 100 m far
   tier reads 6 px at 20 km). The problem lives at 0.5–4 km, precisely where D
   cannot help.

   **At equal storage, geometry wins outright.** A DEM-derived normal map is
   unique per tile, not a reusable texture, so it must be baked and shipped
   per tile:

   | | Per 2 km tile |
   |---|---:|
   | 41² geometry (today) | 70 KB |
   | **81² geometry (25 m)** | **278 KB** (+208 KB) |
   | **512² BC5 normal map** (3.9 m/texel) | **256 KB** |

   Same cost. Geometry delivers silhouette *and* shading *and* collision; the
   normal map delivers shading only.

   **The deciding point: the detail is being discarded, not missing.** z15
   Terrarium is ~5 m resolution; it is sampled at 50 m. That throws away
   roughly 100× the information already fetched and paid for, and no normal map
   can restore a ravine that was never sampled. Raising the grid is not "adding
   detail" — it is *keeping* detail that is currently thrown away.

   **Two different things were bundled together here, and they split cleanly:**
   - **D1 — a generic tiling detail normal** (rock/scree/grass micro-surface,
     one asset reused on every tile, not DEM-derived). Costs essentially
     nothing, adds real close-range richness. **Worth doing regardless of the
     geometry choice.** It simply makes no claim about shape.
   - **D2 — a DEM-derived per-tile normal map.** Strictly dominated by Option
     A per the table above. Skip it.

   **Correction:** UE's Virtual Heightfield Mesh was originally filed under
   this option, which was wrong. VHM generates *real geometry* from a
   heightfield at runtime — that is Option A delivered as a runtime system, not
   a faking technique.

   **Option E — Nanite.** Bake terrain tiles to `UStaticMesh` with Nanite
   enabled, then resolution stops being a runtime concern entirely and the cost
   moves wholly to disk and bake time. This is the natural pairing with § 1.4's
   "burn the GPU" stance, and it composes with Option A: crank the grid, let
   Nanite do LOD. Requires the pipeline to emit StaticMesh assets rather than
   raw triangle sidecars — which overlaps § 7 item 14's work, so the two should
   be planned together if both are wanted.

   **Recommendation, in order:** look at it first (one screenshot). If it needs
   fixing, **start with Option A at 81² (25 m)** — one constant, 4× cost,
   128 MB for Oahu, and it will tell you immediately whether resolution was
   the problem. Then decide between C (best quality per byte) and A+E (best
   quality per unit of effort, given you are willing to spend hardware).
   **D1 is worth doing regardless**, independent of whichever geometry path
   wins; D2 is not worth doing at all.

   **The decision decomposes into three independent choices**, which is worth
   noticing because it looks like one big one:

   1. **Look first, or commit now?** One screenshot from a vantage point off
      the road in steep terrain answers whether this is real at all. Costs
      minutes; the alternative is spending days on a problem that may not
      exist at the distances the player actually experiences.
   2. **Which geometry path** — A alone (one constant, no architecture
      change), A+E (crank it and let Nanite absorb the runtime cost), B
      (tiered, cheapest, seam risk), or C (adaptive, best per byte, known
      complexity). This is the only genuinely hard call.
   3. **D1 surface detail** — effectively a yes; it is cheap, orthogonal, and
      improves close range regardless of what 2 decides.

   And one sequencing question outside this section: items 3, 5 and 6 (bridges,
   the per-tile merge, OSM road tags) are all *hours* of work and all ranked
   above this one. Terrain resolution is the most visible item but not the
   most valuable per hour.

## 10. The honest answer to "could this ship?"

**Yes — under the hot-zone scope confirmed in § 1.1, and with less work than
the roadmap implies.**

The audit was originally written against "drive anywhere in North America,"
and against that target the answer was no: ~950 GB of pre-extruded triangles
cannot be built, stored, or shipped. Narrowing to mountain hot zones moves that
to ~25 GB today and ~1.7 GB after the § 5 fixes. **The single largest limfac in
this document was dissolved by a scope decision rather than by engineering** —
which is worth noticing, because it is the cheapest kind of fix available and
it was reachable in one question.

What remains is genuinely tractable:

- **The top item is now correctness, not capacity.** Bridges and tunnels
  (§ 3.6) are broken in a way that matters *more* under this scope than the
  old one, because mountain roads are disproportionately viaducts and bores.
  H-3 is not an exotic case; it is the shape of every road now in scope. That
  is hours of work, and it is the first thing to do.
- **The top runtime item is draw calls** (§ 5.2), fixed by merging per tile
  and corridor-gating collision. Also hours.
- **Everything else is optimization or polish**, and can be sequenced against
  what a drive-test actually reveals rather than against a scaling wall.

The deeper observation still holds, and is still worth acting on eventually:
the pipeline bakes the *output* of a deterministic function instead of its
*input*, in four places, in violation of a rule the project already holds for
trees. Fixing that is what turns 25 GB into 1.7 GB. It is no longer a survival
requirement — but it is the difference between a download people tolerate and
one they don't think about.

**The strongest thing about this project is not any individual system — it is
the discipline around them.** Offline determinism, gates that run the
production code path rather than a reimplementation, and a written record of
why each bug happened. That discipline is why an audit like this can find
concrete, fixable things instead of a fog. Keep it; it is doing more work than
it looks like it is.
