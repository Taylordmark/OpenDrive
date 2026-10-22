# Area picker: interfaces between the pieces (Phase 2)

The design is `docs/area-picker-scoping.md` (rounds 6-10 are current; earlier
rounds are superseded where they disagree). This file fixes the formats each
piece reads and writes so the pieces can be built in parallel. Change a format
here first, then in code.

Paths are relative to the repo root. The game finds the repo root as
`FPaths::ProjectDir() / ".."`. All JSON is UTF-8 and written atomically
(write `*.tmp`, then rename).

## Frames and grid
- Drawn areas join a fixed **zone** world in `world_frame.WORLDS` (named
  `zone_*`, a few CONUS LCC zones plus Alaska and Hawaii), picked by the
  polygon's centre. The area frame is the zone frame: area cm = zone metres ×
  100, so drawn areas in one zone share coordinates.
- **Cell** = 1 km square, index `"i_j"` with `i = floor(x_m / 1000)`,
  `j = floor(y_m / 1000)` in zone metres.
- Drawn areas bake **1 km terrain tiles** (`terrain_tile_m: 1000` in the area
  config and in the area's `worlds.json` entry; hand areas stay 2000). Road
  tiles stay 500 m.

## `data/user_areas.json` (pipeline owns)
```
{"worlds": {"zone_x": [lat1, lat2, lat0, lon0]},
 "areas": {"u_<slug>": {<test_areas fields>, "world": "zone_x", "drawn": true,
            "name": "Gatlinburg area", "polygon_ll": [[lon, lat], ...],
            "start_ll": [lon, lat], "terrain_tile_m": 1000}}}
```

## Request: `data/bake/requests/<key>.json` (game writes, runner reads)
```
{"key": "u_<slug>" | null, "name": str | null,
 "polygon_ll": [[lon, lat], ...] | null,
 "corridor_ll": [[lon, lat], ...] | null, "corridor_half_width_m": 1000,
 "start_ll": [lon, lat]}
```
Exactly one of `polygon_ll` and `corridor_ll` is set; the runner buffers a
corridor into the polygon. The game launches the runner as
`python pipeline/bake_request.py --run <request path>` (detached, cwd = repo root),
or `--retry <key>`. To cancel, the game creates the file `data/bake/<key>/cancel`.
Limits: 1-30 km per side, at most 64 vertices, US only.

## Status: `data/bake/<key>/status.json` (runner writes, game reads)
```
{"key", "name", "state": "pass1" | "pass2" | "done" | "failed" | "cancelled" | "paused",
 "stage": "build_road_stations", "pass1_done": bool, "ready_to_drive": bool,
 "start_snapped_ll": [lon, lat] | null, "cell_m": 1000,
 "cells": {"i_j": "pending" | "running" | "baked" | "failed"},
 "eta_first_drive_s": float, "eta_done_s": float,
 "error": str | null, "updated": unix seconds}
```
`ready_to_drive` = pass 1 done and the start cell baked. The runner snaps the
start to the nearest road station after pass 1 (`start_snapped_ll`).
Log: `data/bake/<key>/bake.log`.

## Car heartbeat: `data/bake/car.json` (game writes about 1 Hz on every screen)
```
{"area": key | null, "mode": "drive" | "menu", "x_cm": f, "y_cm": f,
 "heading_deg": f, "target_cm": [x, y] | null,
 "quit_behaviour": "stop_after_job" | "keep_baking", "t": unix seconds}
```
In `menu` mode the position is the last known one. The runner treats the file
as stale after 30 s, meaning the game is gone. Priority: full with all workers
in `menu`, below-normal with fewer workers in `drive`.

## Pass and job rect for pipeline stages (runner sets, stages read)
- `OD_AREA=<key>` as today.
- `OD_PASS=1|2` (unset = whole bake, as today for hand areas).
- `OD_JOB_RECT_M="x0,y0,x1,y1"`: area-frame metres on the 1 km grid. Pass-2
  stages build only the tiles inside it. Decisions that cross the rect edge use
  the pass-1 sidecars, plus a 150 m carve margin.
- `rebuild_area.py --pass 1` and `rebuild_area.py --pass 2 --rect x0,y0,x1,y1`.

## Coverage: `OpenDriveUE/Content/OpenDrive/Masks/coverage.json` (pipeline writes)
```
{"areas": {"<key>": {"name", "drawn": bool, "outline_ll": [[lon, lat], ...],
           "tiles": [{"ll": [[lon, lat] x4], "state": "baked" | "pending" | "failed"}]}}}
```
This is built from the terrain tiles on disk plus the pending and failed cells in
status.json. `build_world_manifest.py` writes it, and the runner refreshes it
after each job.

## Live estimate (menu and runner use the same formula; the test bake calibrates it)
```
km2 = polygon area
first_drive_s = 300 + 0.5 * km2 + 150
done_s        = first_drive_s + 12 * km2
disk_mb       = 50 + 1.0 * km2
```

## Outline mask: `Masks/outline_<area>_test_tile_<tx>_<ty>.bin` (pipeline writes, terrain reads)
Written by `build_terrain_mesh_tiles.py` next to a drawn area's terrain tile,
only for tiles the drawn outline crosses. A terrain tile with no outline file
is wholly inside the outline (and hand areas never have one).
```
'O','D','O','1'          magic
uint32 L, L bytes        generation id (as the terrain tile)
uint32 N                 samples per side (65)
float32 x0_cm, y0_cm     tile min corner, UE cm (tx * size, ty * size)
float32 size_cm          tile side (terrain_tile_m * 100)
N*N float32              signed distance to the outline in metres, + inside,
                         - outside; row r, column c is at
                         (x0 + c * size / (N - 1), y0 + r * size / (N - 1))
```
Sample bilinearly; the "unsynced" look starts where it goes below 0.
`OD_JOB_RECT_M` is east/north metres: UE cm = (east * 100, -north * 100), so
terrain tile `ty` spans north `(-(ty + 1) * size, -ty * size]`.
