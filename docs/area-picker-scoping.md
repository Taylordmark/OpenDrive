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
