# OpenDrive — Phase 1: Tail of the Dragon MVP

Drive the real US-129 (Deals Gap → Tabcat Creek, 18.2 km / 11.3 mi, 318 curves)
on real USGS terrain in UE 5.8.

Everything data-side is already generated in `data/stages/tail_of_the_dragon/`:

| File | What it is |
|---|---|
| `heightmap.png` | 2017×2017 16-bit terrain for Landscape import (road corridor pre-carved) |
| `road_mesh.json` | 25 road mesh chunks, grade-smoothed, with embankment skirts |
| `stage_meta.json` | Landscape transform, spawn point, stats |
| `preview.png` | Top-down sanity check |

To regenerate from scratch: `python pipeline/fetch_data.py` then `python pipeline/build_stage.py`.

## One-time UE editor setup (~10 min of clicking)

### 1. Create the project
Epic Games Launcher → UE 5.8 → **Games → Vehicle** template → Blueprint →
name it `OpenDriveUE`, location `C:\Users\taylo\Coding\OpenDrive\` → Create.

### 2. Enable plugins
Edit → Plugins → search and enable **Python Editor Script Plugin** and
**Geometry Script** → restart the editor when prompted.

### 3. Create the level
File → New Level → **Empty Level** → save as `TailOfTheDragon`.

### 4. Import the Landscape (the one manual import)
1. Click the mode dropdown (top-left, says "Select") → **Landscape**.
2. Choose the **Import from File** tab (called *Import* in some versions).
3. Heightmap File: `data/stages/tail_of_the_dragon/heightmap.png`.
4. It should auto-detect: Section Size **63×63**, Sections Per Component **2×2**,
   Number of Components **16×16** (total resolution 2017×2017). If not, set those.
5. Set the transform **before clicking Import**:
   - Location: X `-440884.2`  Y `-440884.2`  Z `62860.8`
   - Scale:    X `437.3851`   Y `437.3851`   Z `141.9971`
6. Click **Import**, then switch the mode dropdown back to **Select**.

**Gotcha learned the hard way:** the import dialog treats the Location you
type as the landscape *center*, not its corner — so after import, always run
`unreal/fix_landscape.py` (Tools → Execute Python Script) to snap the
Landscape actor to the exact expected transform. Manual alternative: switch
back to Select mode, click Landscape in the Outliner, set Location in Details.

### 5. Run the import script
Tools → **Execute Python Script…** → `unreal/import_stage.py`.
Watch the Output Log: it should report `built 25 road chunks`, spawn a
PlayerStart at Deals Gap, and add sun/sky/fog. Save the level.

### 6. Hook up the vehicle
Window → World Settings → **GameMode Override** → pick the vehicle game mode
that ships with the template (search "vehicle" in the dropdown — named like
`VehicleAdvGameMode` / `GM_Vehicle` depending on version).

### 7. Drive
Press **Play**. You should spawn at Deals Gap facing down the first straight.

## What to evaluate (this is the real test)
- Does 60–100 mph through the switchbacks feel *good*? Grade is clamped at 10 %,
  profile smoothed over ~40 m — too floaty? too bumpy? Tell Claude; those are
  one-line constants in `pipeline/build_stage.py` (`MAX_GRADE`, smoothing sigmas).
- Road width is 9 m (`ROAD_HALF_WIDTH`) — does it read as a real two-lane?
- Any spot where terrain pokes through asphalt, or the road floats visibly.

## Known rough edges (accepted for MVP)
- Gray engine material on everything; no markings, guardrails, or foliage.
- Lake surfaces are DEM-infilled, not water.
- If you fall through the road on Play: that's a triangle-winding/collision
  issue in my generated mesh — report it, one-line fix.

## Data credits
Elevation: USGS 3DEP via AWS Terrain Tiles (Mapzen terrarium).
Road geometry: © OpenStreetMap contributors, ODbL.
