# New-area picker: scoping (Phase 1)

Goal: on the in-game map, a player draws or adjusts a boundary, then starts the
offline bake for it, and the new area turns up in the menu when the bake is done.
This doc covers what exists today, the open questions, the options and a recommendation.
Nothing is built yet.

## What exists today

**Areas** live in `pipeline/test_areas.py` `AREAS`, which is hand-edited Python. There are 8:
skagit_cascades, oahu, green_valley, summit_county, breckenridge, sf_russian_hill,
deals_gap and far_gasquet. Each entry holds:
- `bbox_snwe`
- an optional `background_bbox_snwe`, a wider area for far terrain and the map backdrop
- `origin_height_m`, sampled from the z12 Terrarium DEM at the bbox centre, never guessed
- `elev_plausible_m`, from a grid-sampled elevation span plus padding
- `max_building_levels`
- an optional `world` and `own_rect_cm`, used by world members that share a frame

`ACTIVE_AREA` picks the area. The `OD_AREA` env var overrides it, so a bake can
already target any area without editing the file.

The areas range from sf_russian_hill (~0.8 × 1 km) to summit_county (~56 × 39 km,
~2,200 km²).

**Bake**: `python pipeline/rebuild_area.py` runs 58 stages in order and stops at
the first failure. `--from <script>` resumes at a given stage. Inputs come from the
area's bbox plus these downloads:

| Data | Source | Licence | Stages |
|---|---|---|---|
| Roads, places, buildings, water, furniture | OSM via Overpass (overpass-api.de and mirrors) | ODbL | fetch_osm_*, build_landmarks, pave mask |
| Extra building footprints | Microsoft Global ML Building Footprints | ODbL | fetch_osm_buildings |
| Elevation | AWS Terrain Tiles (Terrarium PNG; SRTM/3DEP/etc.) | open, attribution | terrain, water, horizon, relief |
| Aerial imagery (biomes, map) | USGS National Map basemap | US public domain | classify_biomes, build_map_texture |
| Ground albedo | Sentinel-2 L2A via Element84 earth-search | Copernicus open, attribution | build_sat_albedo |
| Land cover | ESA WorldCover | CC-BY 4.0 | pave mask, far forest |
| Climate | NASA POWER | free, attribution | fetch_climate |
| Traffic volumes | FHWA HPMS | US public domain | build_traffic_demand |

All of these are already in use, and `verify_asset_licenses.py` and
`build_credits.py` cover them. The USGS imagery and HPMS are US-only, so **v1
areas must be in the US**.

**Bake time**: the full pipeline hasn't been timed end to end. The terrain stage
alone took 10.6 min for summit_county on 8 workers. The Overpass fetches are the
slowest and least predictable stages, from minutes up to failures during an
Overpass bad patch. Expect a small area (≤10 × 10 km) to take tens of minutes and
a summit-sized area to take over an hour. I'll measure a small area once fetching
is approved.

**After the bake**, an area still needs **an editor step**:
`unreal/build_openworld_test_level.py` runs headless in UnrealEditor-Cmd and:
- creates `/Game/Areas/OpenWorld_<area>.umap`, a level that only sets per-area
  file prefixes on the managers, a PlayerStart and a sea plane
- imports `map_texture.png` as a UTexture2D

`bake_world_map.py` then refreshes `worldmap.json`, which holds the globe pins.

**Menu flow** (`ODMainMenu`): Main → Open Drive → World (a `AODMenuGlobe` with
Blue Marble, coast/state lines and one pin per area cluster) → AreaMap (flies down
and drapes `relief_<area>.png`, pan/zoom clamped) → Start → Drive.
`FindAreaLevels()` lists the areas by scanning `/Game/Areas/OpenWorld_*` in the
AssetRegistry, keeping only levels that have a `menu_spots.json` entry.
`OpenArea()` calls OpenLevel with `?od...` URL options.

## Questions and options

### 1. What the player sees and draws

- **A. Rectangle (recommended).** Every stage is bbox-based: `bbox_snwe`, the
  Overpass bbox and the tile grid. A polygon would only be clipped back to its
  bounding box, so it adds UI without changing the bake. The player drags on the
  World globe, zoomed in. The rectangle has 4 corner handles and a body you can
  drag to move it, and shows a live readout like "12.4 × 8.1 km · 100 km² · est. ~25 min".
  Existing areas show as outlines so the player can see overlaps.
- **B. Polygon.** Nicer for coastlines and valleys, but needs pipeline-wide
  masking. Not worth it for v1.

Size limits: each side 1 km min and 30 km max, so ≤900 km² for v1. Above that,
Overpass gets fragile and the bake runs well past an hour. Out-of-US areas are
refused with a message. The background bbox is derived automatically by padding
~0.1°, which matches the existing areas.

The globe texture is the low-res Blue Marble plus lines. Streaming real map tiles
for precise drawing would be another download source and fall under the OSM tile
policy, so I'd skip it for v1. The coast, state lines and existing area outlines
are enough to aim with, and you can adjust the rectangle afterwards.

### 2. Handing the request to Python

- **A. Request file plus a runner the game launches (recommended).** The game
  writes `Saved/AreaRequests/<key>.json` (name, bbox) and starts
  `python pipeline/bake_request.py <file>` as a detached process
  (`FPlatformProcess::CreateProc`). The runner:
  1. registers the area in a new `data/user_areas.json`, which `test_areas.py`
     merges into `AREAS`, so hand-edited Python is never rewritten
  2. samples `origin_height_m` and the elevation range from the DEM (automating
     the documented manual step)
  3. runs `rebuild_area.py` with `OD_AREA=<key>`
  4. writes `Saved/AreaRequests/<key>.status.json` as it goes

  The bake survives a game restart, and the file contract is easy to test without
  the game.
- B. Only write the file, and have the user start a watcher by hand. Simpler, but
  the button alone doesn't start anything.
- C. A local HTTP service. More moving parts and nothing gained over A.

### 3. Progress and failure

Twice a second the menu reads the status file: `{state: queued|running|failed|done,
stage, stage_i, stage_n, script, started, log}`, where `rebuild_area.py` already
prints `[i/N] script` for each stage.
- The globe shows a "baking" pin, and the World screen shows a card: "Baking
  Mount Hood: stage 17/58, build_road_network (14 min)", with a Cancel button
  that kills the process tree.
- On failure the card shows the stage, the last ~8 log lines, and Retry. Retry
  re-runs from the failed stage with `--from`; fetches are cached in `data/raw`,
  so this is cheap.
- A partial bake never shows up as a drivable area, because it has no
  `menu_spots.json` entry until the end.

### 4. Where the new area appears

This is the real decision. Today an area needs an editor-made `.umap`.

- **A. Template level, no editor step (recommended).**
  1. Add one `OpenWorld_Template.umap`.
  2. The game reads `?odarea=<key>` and sets every manager's file prefix at
     runtime, before they stream. The prefixes are already plain strings, and the
     level builder only sets those.
  3. Load the map texture from PNG at runtime, the same way `relief_<area>.png`
     is already loaded.
  4. `FindAreaLevels` also lists the baked areas from a Masks manifest.

  New areas then appear on the globe the moment the bake finishes, with no editor
  needed and no binaries locked. This works the same in a packaged build. Cost:
  moderate C++ (GameMode plus 1 line per manager) and one template level, built
  once by the existing script. The 8 existing areas keep their own levels.
- B. The runner also runs UnrealEditor-Cmd `build_openworld_test_level.py`. This
  is less new code, but it launches a second UnrealEditor while the game is
  running (the "one editor at a time" rule, plus `.umap`/asset lock risk), adds a
  slow extra stage, and doesn't work outside a dev checkout.

Either way, the runner then re-runs `bake_world_map.py` so the pin appears.

### 5. Downloads and consent

All sources are in the table above and are already approved by being in use. The
Bake button opens a confirm card: "This downloads OpenStreetMap (ODbL), USGS
elevation and imagery, Sentinel-2, ESA WorldCover and NASA POWER data for this
area, about N MB. Bake?" During development, **I won't run a real fetch until you
OK it**. I'll test the runner with a fake rebuild command and no network.

## Revised design (Taylor, 2026-10-20): polygon, start point first, bake-as-you-drive

Taylor asked for three things:
- allow polygons, split into rectangles
- have the player choose a start point before anything bakes
- bake outward from that point and stream terrain from local disk while driving

This runs on machinery that already exists, the **world members** used for the
California world and the gv_west/gv_east split of green_valley:
- Members share one LCC frame and sea-level datum (`world_frame.WORLDS`).
- Each one owns a rect (`own_rect_cm`) on the 2 km terrain grid and bakes ~3–4 km
  past it as a halo. `prune_unowned_tiles.py` cuts the halo back.
- `build_world_manifest.py` lists the members in `worlds.json`.
- `AODGameMode` spawns a member's manager set when the car is under 8 km from its
  rect and drops it past 16 km.

**Flow**
1. **Draw a polygon** on the World globe: click vertices, drag to adjust, with a
   live km² readout.
2. **Pick a start point** inside it. Nothing bakes before this.
3. **Split.** The runner makes a new world, an LCC centred on the polygon. It
   grids the polygon's bbox into member cells, about 4 × 4 km (2 × 2 terrain
   tiles), and keeps every cell that touches the polygon. Each cell becomes a
   member area with its own `own_rect_cm`. At the edges the polygon becomes a
   staircase at cell size. Clipping the bake to the exact outline isn't worth it.
4. **Bake the start cell first.** The player waits for that one only, measured in
   minutes and still unmeasured, with the progress card up. They then spawn at
   the cell's menu spot, the turnout nearest the chosen point.
5. **Bake outward while they drive.** The game writes the car's position and
   heading to a small file about once a second. After each cell the runner picks
   the next not-yet-baked cell closest to the car, weighted toward the direction
   it's heading. Each finished cell re-runs `build_world_manifest.py`. The game
   re-reads `worlds.json` every few seconds (today it reads it once, at
   BeginPlay), and the existing 8 km spawn rule streams the new cell in from
   local disk. No tile server is needed.

**Honest limits**
- **The car can outrun the bake.** Highway speed is about 1.5–2 km per minute,
  and a cell takes minutes. Each cell runs the full 58-stage pipeline, so the
  fixed per-cell cost is maybe 2–3 min of Python start-up plus the Overpass round
  trips, before the real work. Unbaked ground has no terrain, so the edge of the
  baked area needs a soft gate: a "still baking ahead" message and the road
  barrier, or a speed cap. Bigger cells mean fewer seams and less overhead, but a
  longer wait at the start. I'd make cell size one setting and tune it once
  there's a real timing.
- **Seams between members are the known weak spot.** The gv_west/gv_east cut
  measured a terrain step with median 0 and max 15.1 m. At 1 of 60 crossings both
  halves drew the road, and at 4 neither did. Road-fit gates failed within 200 m
  of the cut (status.md, "Streaming, pipeline side"). Status.md says step 3's
  two-pass bake is meant to fix this, and it isn't done. A polygon split into
  many cells has many cuts, so **seam quality has to be solved, or at least
  measured on a real split, before this feels good.**
- **Some things are per member and get duplicated across cells:** centerline and
  map lines, labels, routegraph, roadstations, biome, map texture. Navigation and
  traffic routing across a cell edge haven't been checked. Ambient sound and
  climate are still home-only (status.md).
- **Bakes run one cell at a time.** Terrain already uses 8 worker processes, and
  Overpass allows about 2 concurrent queries per IP.

**Does this replace 4A?** No. It still needs the template level from 4A, run with
`?odarea=<start cell>` as the home member, because the cells have no editor-made
levels.

**Suggested order**
1. **Pipeline only, no game:** polygon → cells → `user_areas.json` world members,
   plus the nearest-cell queue runner and status file. Assert tests cover the
   split and the queue order, run against a fake rebuild command.
2. **Measure, after your fetch OK:** bake one small real polygon of 2–3 cells.
   Time each cell and run `verify_area_seam.py` on the cuts.
3. **Runtime:** the template level and `?odarea`, a `worlds.json` re-read, the
   car-position file and the edge gate.
4. **Menu:** polygon draw, start pick, consent card, progress.

## Round 3 (Taylor, 2026-10-20): edge effect, 1 km tiles, detail only near roads

### Detail near the road: already how it works
`build_terrain_mesh_tiles.py` already splits the terrain by distance from the road:

| Zone | What's baked |
|---|---|
| Within 60 m of a road station (`NEAR_MARGIN_M`) | Near tiles: 7.8 m grid, 0.3 m error, collision |
| Out to 6 km (`RENDER_DISTANCE_M`) | Far tiles: 5 m error, no collision |
| Past 6 km and +80 km around the extent | One zoom-10 horizon mesh (`build_horizon.py`) |

Nothing new is needed here.

### 1 km tiles would make the bake slower, not faster
Two sizes get mixed up here:
- **The streaming tile** is 2 km for terrain and 500 m for roads. Loading from
  local disk isn't the bottleneck. Shrinking it means 4× the files, a change to
  `ODTerrainManager` `TileSizeCm`, and a rebake of every area.
- **The bake cell** is what the player waits for. Each cell bakes a ~3.5 km halo
  past its edges so roads meet their neighbours:
  - A 4 km cell bakes an 11 km square, 7.5× its own area.
  - A 1 km cell would bake an 8 km square, 64× its own area.
  - Each cell also pays the fixed cost of 58 stage processes and its own Overpass
    round trips.

  So 1 km cells mean far more total work and a barely shorter first wait.

**What would actually speed it up** is fetching once for the whole polygon: one
Overpass query per dataset and one DEM pass. Cells then cut their bbox out of that
local copy, with no network per cell. Before choosing a cell size, I should time
each stage on the approved small test bake (`rebuild_area.py` already prints a
time per stage). Then we know whether the fetches, terrain, junction pads or
Python start-up dominate.

### The edge: an Animus-style "unsynced" boundary
This fits well with what's already baked:
1. **Bake the coarse world first.** Right after the start cell, `build_horizon.py`
   runs for the whole polygon (zoom 10, minutes). From the first second, the land
   past the baked edge is real-shaped but low-poly.
2. **Unbaked land looks unsynced.** The horizon mesh past the baked edge gets a
   boundary material:
   - faceted flat shading (its RTIN triangles already look faceted)
   - a faint emissive grid along the triangle edges
   - noise-driven vertex shimmer, so the polygons shift
   - a desaturated, cool tint
3. **A curtain at the edge.** A translucent vertical wall along the outline of the
   baked cells, with an animated glitch/dissolve. A post-process blend ramps up
   blur, chromatic aberration and desaturation over the last ~150 m. The car can't
   go past it: a soft push-back force on the fixed physics step, then an invisible
   blocker as a backstop. That's frame-rate independent, and there's no collision
   on the coarse mesh, so the car never falls into the void.
4. **A cell landing "syncs in."** When the runner finishes a cell, its tiles
   stream in and a dissolve front sweeps outward over ~2 s (time-based). The coarse
   facets resolve into the real terrain, and the curtain moves out.
5. **The polygon outline gets the same curtain.** That makes it the runtime mask:
   cells bake as whole squares (staircase), and anything outside the drawn outline
   stays behind the curtain. **That covers most of the "polygon masking" work at
   no pipeline cost.** Real pipeline clipping of roads, buildings and terrain to
   the outline is only worth doing if you want the land just past the outline
   gone, not merely unreachable.

How it's built: everything in code, following the existing pattern. Materials come
from an `unreal/apply_*_material.py` script, the curtain is a
`UDynamicMeshComponent` built from the cell rects, and nothing is a paid asset or
borrows anything from Ubisoft beyond the idea. It's a deliberately stylised effect
at the edge only; the driven world keeps to the realism bar.

### Revised order
1. Timed test bake of one small real area, ≤10×10 km. Taylor approved this via the
   coordinator. Run it as 2–3 cells to time each stage and measure the seams.
2. Choose by the numbers: cell size, and whether to fetch once per polygon.
3. Pipeline: polygon → cells → world members, the nearest-cell runner, and
   horizon first.
4. Runtime: template level and `?odarea`, a `worlds.json` re-read, the
   car-position file, and the boundary (material, curtain, push-back, sync-in).
5. Menu: polygon drawing, start pick, consent card, progress card.

## Round 4 (Taylor, 2026-10-20): download once for the whole polygon

### What each fetch does today (inventory)
| Source | Called from | Cached by | Shared across cells today? |
|---|---|---|---|
| Terrarium DEM | terrain_common (every height sampler) | z/x/y tile | **yes**, tile-keyed |
| USGS imagery | classify_biomes, map texture | z/x/y tile | **yes** |
| ESA WorldCover | classify_biomes, pave mask, far forest | COG block | **yes** |
| Sentinel-2 | build_sat_albedo | COG byte range; scenes picked per MGRS tile | **yes**: same scenes, so no colour seam between cells |
| MS building footprints | fetch_osm_buildings | quadkey file | **yes** |
| OSM Overpass, 9 query sites | fetch_osm_network, fetch_osm_places, fetch_osm_buildings, build_water_tiles (×2), build_landmarks, build_parking_tiles, build_pave_mask, build_sidewalks | **query text, which contains the cell bbox** | **no**: every cell re-downloads its whole 11 km halo square |
| FHWA HPMS | build_traffic_demand | state + bbox | **no** |
| NASA POWER climate | fetch_climate, build_ground_bands | point (area centre) | no, but one tiny request each |

So "download once" is mostly an **Overpass** and **HPMS** problem. The tile-keyed
sources already download each tile only once. Overlapping halos only re-read them
from `data/cache`.

### Option A: download once (small change)
- `test_areas` gains `FETCH_BBOX_SNWE`. For a drawn world it's the world's bbox
  (polygon plus halo); for every existing area it equals `BBOX_SNWE`.
- The 9 Overpass sites and HPMS put `FETCH_BBOX_SNWE` in their query, so every
  cell sends identical text and hits the cache. They then call
  `overpass_json(..., clip=BBOX_SNWE)`. A new ~30-line clip in `fetch_cache.py`
  keeps what Overpass's own bbox would have returned:
  - nodes inside the bbox
  - ways with any node inside, with all their nodes
  - relations with a kept member
  - for `out center`, elements whose centre is inside
- Climate is fetched at the world centre. Its effect is home-only at runtime anyway.
- **Existing areas don't change** (their fetch bbox and clip bbox are the same).
  It comes with one assert test for the clip.
- A "Downloading map data" phase goes first on the progress card: 9 Overpass
  queries plus HPMS for the whole polygon, then the cells bake offline.

What A does *not* fix: each cell still computes its whole 11 km halo square
(7.5× its own area). Neighbouring cells still build their own road stations over
different extents, and that difference is what produced the gv_west/gv_east seams.

### Option B: compute once too, a two-pass bake
This is the roadmap's own "two-pass chunked bake, so a chunk's edges match its
neighbours'" (next steps.md §4, step 3). Here it would be built for drawn worlds:
- **Pass 1, the whole polygon, once:** every download (A), then the road graph:
  - stations, settlements, retaining walls, junction pads
  - routegraph and traffic
  - labels, biomes, climate and ground bands
  - the horizon (coarse terrain), which is what the boundary effect needs anyway

  Every cell then shares **one** road network, so roads match at the cuts by
  construction.
- **Pass 2, per cell, nearest to the car first:** the tile writers (terrain
  carve, road/ODR3 tiles, barriers, guardrails, embankments, bridges, tunnels,
  sidewalks, buildings, furniture, sat/pave/relief, forest, far forest) write only
  the tiles in that cell. They read pass 1's shared outputs. The halo shrinks from
  3.5 km to the carve reach (~150 m, `TERRAIN_CARVE_OUTER_M`), so a 4 km cell
  computes ~4.3 km, not 11 km.
- **Wins:** pass 2 gets much faster per cell, the seams fix themselves (one road
  network, one set of junction pads), and the per-cell duplicated whole-area files
  (centerline, labels, routegraph, roadstations…) become one set.
- **Costs:**
  - Every one of the 58 stages has to be sorted into pass 1 or pass 2, and each
    tile writer needs an "only tiles in rect R" filter.
  - Pass 1 blocks the first drive. For a 30 km polygon it's roughly a
    summit_county-sized road graph, which hasn't been timed.
  - It's the biggest pipeline change in this doc.

### Recommendation
Do **A** now as part of the pipeline step. It's small, safe for the existing
areas, and exactly what you asked for. Decide **B** from the timed test bake.
`rebuild_area.py` prints a time per stage, so one 2–3-cell run shows:
- how much time goes to the halo (B's speed win)
- how much goes to whole-graph stages (B's up-front wait)
- how bad the seams are (B's quality win)

If the seams come out like gv_west/east, B is worth it regardless of speed.

## Caveat

The bake needs this repo's Python environment and the `pipeline/` folder. It's a
creator tool on a dev machine, not something a player of a shipped build could run
without bundling Python. That's fine for now, but worth knowing.

## Recommendation, and what Phase 2 builds

These are 1A + 2A + 3 + 4A + 5:
1. `test_areas.py` merges in `data/user_areas.json`.
2. `pipeline/bake_request.py` does the request → register → DEM-sample →
   rebuild → status work. It also gets an assert test for the request validation
   (size, US bounds, key) and the status parsing.
3. Template level and runtime prefixes (`?odarea`), runtime map texture, and
   manifest-based area discovery.
4. World screen: a "New area" mode with rectangle draw and adjust, readout and
   limits, a confirm card, launching the runner, a progress card, Cancel and Retry.

Building happens only once no UnrealEditor is running.
