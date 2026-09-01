"""Classify land-cover biome along the road corridor from USGS aerial
imagery + DEM elevation (Arc 7.3 interim classifier, 2026-07-20). The formal
ESA WorldCover swap is a separate, later task (next steps.md sec 7.3) - this
reuses make_detail_textures.py's same color-heuristic classification
(w_forest/w_grass/w_rock/w_water from RGB), fetched fresh for the road's own
bbox instead of a Landscape stage square, and gated to a buffer around the
road corridor (NOT the whole, much larger, terrain BBOX_SNWE) - same
road-gating philosophy build_terrain_tiles.py already established for z15
terrain: the player only experiences biome variety near the road, so only
that imagery needs fetching/classifying.

Usage:  python classify_biomes.py     (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Outputs data/road_tiles/<AREA_NAME>_test/road_biomes.json
        {"lon": [...], "lat": [...], "biome": [...EODBiome int...],
         "forest_weight": [...0..1...]}
        one entry per SAMPLED station (every SAMPLE_STRIDE-th, not all -
        biome doesn't need road_stations' 5 m resolution, and this keeps
        the imagery fetch/classification cost down).

EODBiome int values MUST match the enum order in
OpenDriveUE/Source/OpenDriveUE/ODForestManager.h - there is no shared
source of truth between C++ and Python here, so a reorder on one side
without the other silently reclassifies every biome.
"""

import json
import math
from io import BytesIO
from pathlib import Path

import mercantile
import numpy as np
import requests
from PIL import Image

from test_areas import AREA_NAME

IMG_ZOOM = 13        # ~19 m/px at this latitude - regional biome character,
                      # not fine detail, so far coarser than terrain's z15
MARGIN_M = 2000.0     # imagery fetch buffer beyond the road's own bbox
SAMPLE_STRIDE = 10    # classify every 10th station (~50 m spacing)
TILE_URL = ("https://basemap.nationalmap.gov/arcgis/rest/services/"
            "USGSImageryOnly/MapServer/tile/{z}/{y}/{x}")
HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

STATIONS_PATH = (Path(__file__).resolve().parent.parent / "data" / "road_tiles"
                  / f"{AREA_NAME}_test" / "road_stations.json")
OUT_PATH = STATIONS_PATH.parent / "road_biomes.json"

# order MUST match EODBiome in ODForestManager.h
(BIOME_DECIDUOUS, BIOME_EVERGREEN, BIOME_CHAPARRAL, BIOME_DESERT,
 BIOME_PLAINS, BIOME_WETLAND, BIOME_ALPINE, BIOME_ARCTIC, BIOME_FARMLAND,
 BIOME_SAVANNA, BIOME_RAINFOREST, BIOME_COASTAL, BIOME_CANYON,
 BIOME_FOOTHILL) = range(14)
BIOME_NAMES = ["Deciduous", "Evergreen", "Chaparral", "Desert", "Plains",
               "Wetland", "Alpine", "Arctic", "Farmland", "Savanna",
               "Rainforest", "Coastal", "Canyon", "Foothill"]


def fetch_imagery_mosaic(west, south, east, north):
    tiles = list(mercantile.tiles(west, south, east, north, [IMG_ZOOM]))
    xs, ys = [t.x for t in tiles], [t.y for t in tiles]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    print(f"fetching {cols * rows} imagery tile(s) at z{IMG_ZOOM} ({cols}x{rows}) ...")
    mosaic = np.zeros((rows * 256, cols * 256, 3), dtype=np.uint8)
    session = requests.Session()
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            url = TILE_URL.format(z=IMG_ZOOM, y=ty, x=tx)
            r = None
            for attempt in range(4):
                try:
                    r = session.get(url, headers=HEADERS, timeout=30)
                    r.raise_for_status()
                    break
                except requests.RequestException:
                    if attempt == 3:
                        raise
            tile = np.asarray(Image.open(BytesIO(r.content)).convert("RGB"))
            r0, c0 = (ty - y0) * 256, (tx - x0) * 256
            mosaic[r0:r0 + 256, c0:c0 + 256] = tile
        print(f"  row {ty - y0 + 1}/{rows}")
    nw = mercantile.bounds(mercantile.Tile(x0, y0, IMG_ZOOM))
    se = mercantile.bounds(mercantile.Tile(x1, y1, IMG_ZOOM))
    return mosaic, (nw.west, se.south, se.east, nw.north)


def classify_rgb(mosaic):
    """Same heuristic as make_detail_textures.py - kept in sync by eye, not
    import, since that script is stage-square-shaped and this is bbox-shaped.

    w_bare added here (2026-08-27, NOT mirrored back to make_detail_textures.py
    - that script only paints ground texture and has no biome to get wrong)
    to fix a real Green Valley/Palmdale rebake bug: w_forest was previously
    defined purely as the 1-minus-everything-else leftover bucket, so bare
    warm-toned desert dirt/sand (which is neither clearly grass, nor rock
    -  w_rock's green<0.04-AND-val>0.45 rock signature is much stricter than
    typical dry soil - nor water) fell entirely into w_forest and got
    classified as dense Deciduous/Chaparral canopy. w_bare picks out that
    bright, low-saturation, warm (red > blue) signature explicitly so it can
    be routed to BIOME_DESERT in pick_biome() instead of masquerading as
    forest cover."""
    sat = mosaic.astype(np.float32) / 255.0
    r, g, b = sat[..., 0], sat[..., 1], sat[..., 2]
    val = sat.max(axis=2)
    green = g - np.maximum(r, b)
    w_water = np.clip((0.28 - val) * 8, 0, 1) * np.clip((b - r + 0.05) * 8, 0, 1)
    w_grass = np.clip(green * 6, 0, 1) * np.clip((val - 0.32) * 6, 0, 1)
    w_rock = np.clip((0.04 - green) * 8, 0, 1) * np.clip((val - 0.45) * 6, 0, 1)
    # weakly-green (not vegetated, but not rock's near-zero-green extreme
    # either) + warm-toned (red channel above blue - tan/ochre soil, not
    # grey rock or blue-tinted shadow) => bare/desert ground. Brightness
    # floor kept low (0.20, not requiring full sun) so shadowed/dim dry
    # scrub still reads as bare rather than falling to the w_forest leftover
    # bucket by default - real forest canopy stays distinguishable because
    # its green channel dominates (green > 0) even in shadow, which the
    # (0.18 - green) term still excludes.
    w_bare = (np.clip((val - 0.20) * 4, 0, 1)
              * np.clip((0.18 - green) * 5, 0, 1)
              * np.clip((r - b + 0.02) * 8, 0, 1))
    w_forest = np.clip(1.0 - w_grass - w_rock - w_water - w_bare, 0, 1)
    return w_forest, w_grass, w_rock, w_water, w_bare


RUGGED_WINDOW = 21    # stations (5 m spacing) => ~100 m centered window
CANYON_GRADE_MIN = 0.06   # 6% local grade floor - below this, "rock-colored"
                          # ground is flat bare soil/pavement, not badlands


def segment_ruggedness(elev, s_m, window=RUGGED_WINDOW):
    """Local terrain ruggedness proxy: the steepest station-to-station grade
    (|delta elevation| / |delta s|) within a window centered on each point,
    computed at the road's native 5 m station spacing (BEFORE the
    SAMPLE_STRIDE subsampling in main()) so a short steep stretch isn't
    smoothed away by only landing on every 10th point. Computed per-segment
    (not on the globally concatenated arrays) since s_m resets to 0 at each
    segment start and a cross-segment diff would be bogus."""
    elev = np.asarray(elev, dtype=np.float64)
    s_m = np.asarray(s_m, dtype=np.float64)
    if len(elev) < 2:
        return np.zeros_like(elev)
    d_elev = np.diff(elev)
    d_s = np.diff(s_m)
    d_s = np.where(np.abs(d_s) < 1e-6, 1e-6, d_s)
    grade = np.abs(d_elev / d_s)
    grade = np.concatenate([grade, grade[-1:]])   # align back to len(elev)
    pad = window // 2
    padded = np.pad(grade, (pad, pad), mode="edge")
    windows = np.stack([padded[i:i + len(grade)] for i in range(window)])
    return windows.max(axis=0)


def sample_bilinear(chan, bounds, lon, lat):
    w, s, e, n = bounds
    h, wd = chan.shape
    u = (lon - w) / (e - w) * wd
    v = (n - lat) / (n - s) * h   # row 0 = north
    x0 = np.clip(u.astype(int), 0, wd - 2)
    y0 = np.clip(v.astype(int), 0, h - 2)
    fx, fy = u - x0, v - y0
    c00, c01 = chan[y0, x0], chan[y0, x0 + 1]
    c10, c11 = chan[y0 + 1, x0], chan[y0 + 1, x0 + 1]
    return c00 * (1 - fx) * (1 - fy) + c01 * fx * (1 - fy) \
        + c10 * (1 - fx) * fy + c11 * fx * fy


def pick_biome(w_forest, w_grass, w_rock, w_water, w_bare, elev_m, lat_deg, grade=1.0):
    """Elevation/latitude bands tuned for the Skagit/Cascades loop specifically
    (see module docstring) - not a general-purpose elevation->biome model. A
    real per-region model is the ESA WorldCover + climate-raster work (7.3).
    Extended 2026-07-23 (Arc 7.0 pivot) from 7 to 14 archetypes using the same
    coarse RGB+elevation signal plus latitude as a tropics/arctic proxy - this
    stays a placeholder until 7.3's real classifier lands, it just widens the
    output space to match the full archetype list.

    `grade` (2026-08-27 fix): local terrain ruggedness (see
    segment_ruggedness), defaults to 1.0 (always "rugged") for any caller
    that doesn't have station spacing to derive it from. w_rock's
    green<0.04-ish + bright signature is satisfied by BOTH grey eroded rock
    AND bright warm bare desert soil (tan/ochre dirt has red/blue both above
    green, so `green = g - max(r, b)` reads deeply negative same as rock) -
    color alone can't tell flat bright bare valley floor apart from steep
    rocky badlands. Found live: the Antelope Valley floor near Antelope
    Acres (~34.78N -118.35W - flat, bright, warm bare soil/sparse scrub)
    was classifying CanyonBadlands solely off w_rock, despite having
    near-zero road grade. CanyonBadlands is now gated on actual ruggedness;
    flat "rock-colored" ground falls through to the w_bare check below and
    lands in Desert instead, while genuinely steep eroded terrain (high
    grade) still commits to Canyon/Alpine same as before."""
    abs_lat = abs(lat_deg)
    if w_rock > 0.35 and grade > CANYON_GRADE_MIN:
        if elev_m > 1200.0 or (elev_m > 500.0 and abs_lat > 35.0):
            return BIOME_ALPINE
        return BIOME_CANYON
    if elev_m < 5.0 and 0.1 < w_water <= 0.4:
        return BIOME_COASTAL
    if w_water > 0.4:
        return BIOME_WETLAND
    if abs_lat > 60.0 and w_forest < 0.5:
        return BIOME_ARCTIC
    # BIOME_DESERT was completely unreachable before this (2026-08-27 fix):
    # w_forest is a leftover bucket (1 - grass - rock - water), and bare warm
    # desert dirt/sand doesn't trip w_rock's strict near-zero-green+bright
    # signature, so it fell straight into w_forest and lost to the
    # w_forest>=w_grass branch below every time - Green Valley's expanded
    # rebake toward Palmdale/Hwy 14 came back 25712 Deciduous / 10702
    # Chaparral / 6899 Canyon / 0 Desert out of 43313 points, i.e. dense lush
    # forest painted over what should read as sparse high-desert scrub. A
    # strong bare-ground signal (see classify_rgb's w_bare) now commits to
    # Desert before the forest-vs-grass split gets a chance to eat it.
    if w_bare > 0.35 and w_forest < 0.6:
        return BIOME_DESERT
    if w_forest >= w_grass:
        if abs_lat < 23.5:
            return BIOME_RAINFOREST
        if abs_lat > 45.0:
            return BIOME_EVERGREEN   # Pacific NW default for forest cover
        # BIOME_CHAPARRAL/BIOME_DESERT were unreachable here (2026-08-23 fix:
        # every mid-latitude "forest-leaning" pixel fell straight to
        # Deciduous, even w_forest's leftover-bucket definition - 1 minus
        # grass/rock/water - catching dark, patchy dry scrub, not just real
        # closed canopy). Found live: Green Valley CA/Angeles NF (high-desert
        # chaparral) baked as 2399 Deciduous / 545 Canyon out of 2944 points,
        # nothing else - painted "100% green" dense forest
        # (TreeDensityScale=1.0) over what should read as sparse dry brush.
        # A moderate (not overwhelming) forest signal at real elevation now
        # reads as Chaparral (TreeDensityScale=0.5, bush-dominant) instead;
        # only a strongly saturated signal still commits to true Deciduous.
        if w_forest < 0.85 and elev_m > 200.0:
            return BIOME_CHAPARRAL
        return BIOME_DECIDUOUS
    if abs_lat < 23.5:
        return BIOME_SAVANNA
    if w_rock > 0.15:
        return BIOME_FOOTHILL
    if elev_m < 60.0:
        return BIOME_WETLAND if w_water > 0.15 else BIOME_FARMLAND
    return BIOME_PLAINS


def main():
    data = json.loads(STATIONS_PATH.read_text())
    lon = np.concatenate([np.array(seg["lon"]) for seg in data["segments"]])
    lat = np.concatenate([np.array(seg["lat"]) for seg in data["segments"]])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    # grade computed per-segment (s_m resets per segment) at native 5 m
    # spacing, then concatenated in the same order as the other arrays so
    # it survives the shared SAMPLE_STRIDE subsampling below intact.
    grade = np.concatenate([segment_ruggedness(seg["elev_m"], seg["s_m"])
                             for seg in data["segments"]])
    # east_m/north_m are already in the SAME local frame (origin = bbox
    # center) every other consumer uses - reuse directly rather than
    # re-deriving the lon/lat projection a second time. UE frame is
    # +X=east, +Y=south (road_mesh_common.py), so Y_cm = -north_m * 100.
    east_m = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north_m = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    lon, lat, elev = lon[::SAMPLE_STRIDE], lat[::SAMPLE_STRIDE], elev[::SAMPLE_STRIDE]
    grade = grade[::SAMPLE_STRIDE]
    east_m, north_m = east_m[::SAMPLE_STRIDE], north_m[::SAMPLE_STRIDE]
    x_cm, y_cm = east_m * 100.0, -north_m * 100.0
    print(f"{len(lon)} sample point(s) (every {SAMPLE_STRIDE}th station)")

    buf_lat = MARGIN_M / 111132.0
    buf_lon = MARGIN_M / (111132.0 * math.cos(math.radians(float(lat.mean()))))
    west, east = float(lon.min() - buf_lon), float(lon.max() + buf_lon)
    south, north = float(lat.min() - buf_lat), float(lat.max() + buf_lat)

    mosaic, bounds = fetch_imagery_mosaic(west, south, east, north)
    w_forest, w_grass, w_rock, w_water, w_bare = classify_rgb(mosaic)

    sf = sample_bilinear(w_forest, bounds, lon, lat)
    sg = sample_bilinear(w_grass, bounds, lon, lat)
    sr = sample_bilinear(w_rock, bounds, lon, lat)
    sw = sample_bilinear(w_water, bounds, lon, lat)
    sb = sample_bilinear(w_bare, bounds, lon, lat)

    biomes = [pick_biome(float(sf[i]), float(sg[i]), float(sr[i]), float(sw[i]),
                          float(sb[i]), float(elev[i]), float(lat[i]), float(grade[i]))
              for i in range(len(lon))]
    forest_weight = np.clip(sf + 0.3 * sg, 0, 1)   # density signal, not just the forest channel

    out = {
        "lon": lon.tolist(), "lat": lat.tolist(),
        "x_cm": x_cm.tolist(), "y_cm": y_cm.tolist(),
        "biome": biomes, "forest_weight": forest_weight.tolist(),
    }
    OUT_PATH.write_text(json.dumps(out))
    counts = {}
    for b in biomes:
        counts[b] = counts.get(b, 0) + 1
    print(f"wrote {OUT_PATH} ({len(lon)} points)")
    print("biome counts: " + ", ".join(
        f"{BIOME_NAMES[b]}={n}" for b, n in sorted(counts.items())))


if __name__ == "__main__":
    main()
