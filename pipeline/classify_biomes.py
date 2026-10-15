"""Classify land-cover biome along the road corridor from USGS aerial
imagery + DEM elevation (Arc 7.3 interim classifier, 2026-07-20). The formal
ESA WorldCover swap is a separate, later task (next steps.md sec 7.3) - this
reuses make_detail_textures.py's same color-heuristic classification
(w_forest/w_grass/w_rock/w_water from RGB), fetched fresh for the road's own
bbox instead of a Landscape stage square, and gated to a buffer around the
road corridor (NOT the whole, much larger, terrain BBOX_SNWE) - same
road-gating philosophy build_terrain_tiles.py already established for z15
terrain: the player only experiences biome variety near the road, so only
that imagery needs fetching/classifying. SUPERSEDED 2026-09-25 (V1, next
steps.md sec 7.35): the background hills turned out to be what the player
stares at most, so a coarse GRID_SPACING_M grid over the whole terrain bbox
is now classified too (corridor samples unchanged), plus V2 per-sample
aspect (northness) / valley-bottom moisture from a z12 DEM.

ESA WorldCover 2021 (2026-10-14) now backs up the imagery where colour
fails - tropical pasture/ag and dark lava rock; see fetch_worldcover().

Usage:  python classify_biomes.py     (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Outputs data/road_tiles/<AREA_NAME>_test/road_biomes.json
        {"lon": [...], "lat": [...], "biome": [...EODBiome int...],
         "forest_weight": [...0..1...], "northness": [...-1..1...],
         "moisture": [...0..1...], "elev_m": [...], "n_corridor": N}
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
import zlib
from io import BytesIO
from pathlib import Path

import mercantile
import numpy as np
import requests
from PIL import Image, TiffImagePlugin

from scipy import ndimage
from scipy.spatial import cKDTree

from fetch_cache import cached_bytes
from fetch_climate import power_climatology
from terrain_common import fetch_terrarium_tile, terrarium_to_elevation
from test_areas import (AREA_NAME, BACKGROUND_BBOX_SNWE, BBOX_SNWE, TERRAIN_TILE_SIZE_CM,
                        local_rect, lonlat_bbox, to_lonlat)

IMG_ZOOM = 13        # ~19 m/px at this latitude - regional biome character,
                      # not fine detail, so far coarser than terrain's z15
MARGIN_M = 2000.0     # imagery fetch buffer beyond the road's own bbox
SAMPLE_STRIDE = 10    # classify every 10th station (~50 m spacing)
# V1 (2026-09-25, next steps.md sec 7.35): coarse full-terrain-bbox grid on
# top of the corridor samples. The terrain mesh reaches BACKGROUND_BBOX_SNWE,
# but corridor-only samples left every background-hill vertex with no biome
# at all (ApplyBiomeTint's FarBiomeFallbackCm give-up). 300 m keeps every
# point within ~212 m of a grid sample, i.e. always inside the runtime's
# 300 m blend radius (ODTerrainManager/ODForestManager BiomeBlendRadiusCm).
GRID_SPACING_M = 300.0
# V2: aspect + valley-bottom moisture from a z12 (~30 m/px) Terrarium DEM.
DEM_ZOOM = 12
ASPECT_SMOOTH_M = 150.0    # hillside-scale aspect, not per-gully noise
NORTHNESS_FULL_GRADE = 0.35  # dz/dnorth (m/m, ~19 deg) at which |northness| saturates
VALLEY_RADIUS_M = 1000.0   # "local minimum within ~1 km" window
VALLEY_DRY_RISE_M = 150.0  # rise above that minimum at which moisture hits 0
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
 BIOME_FOOTHILL, BIOME_TROPICAL_COAST, BIOME_LAVA) = range(16)
BIOME_NAMES = ["Deciduous", "Evergreen", "Chaparral", "Desert", "Plains",
               "Wetland", "Alpine", "Arctic", "Farmland", "Savanna",
               "Rainforest", "Coastal", "Canyon", "Foothill", "TropicalCoast", "Lava"]

# ESA WorldCover 2021 v200 (10 m, CC-BY 4.0, (c) ESA WorldCover project 2021 /
# Contains modified Copernicus Sentinel data (2021) processed by ESA
# WorldCover consortium), added 2026-10-14. Used only where imagery colour
# fails: Hawaii's blue-cast USGS imagery reads dim pasture/ag on the central
# Oahu plateau as w_forest leftover (Rainforest), and Mauna Loa basalt reads
# the same as Volcano rainforest (val ~0.26, chroma ~0.03). Read straight from
# the public S3 COGs by HTTP range (no rasterio): PIL parses the TIFF header,
# zlib inflates the 1024 px DEFLATE tiles.
WC_URL = ("https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
          "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif")
WC_HEADER_BYTES = 65536   # all 7 IFDs + tile offset tables sit in the first 64 KB
WC_OVERVIEW = 1           # IFD 1 = 20 m, ~IMG_ZOOM's 19 m/px
WC_TREE, WC_SHRUB, WC_GRASS, WC_CROP, WC_BUILT, WC_BARE, WC_WATER = 10, 20, 30, 40, 50, 60, 80
# Shore (2026-10-15, realism eval T5): Sandy Beach/Waikiki's beach strip
# classified Savanna (1 of 200 shore points was TropicalCoast) - the old
# coast test needed 0.1 < w_water <= 0.4 imagery colour, which bright sand
# never has. Now: within SHORE_M of WorldCover water (or no-data open ocean)
# and below SHORE_MAX_ELEV_M, unless WorldCover says trees or built-up.
SHORE_M = 80.0
SHORE_MAX_ELEV_M = 8.0
# Lava (tropics only) = dark imagery where WorldCover says bare/sparse, or
# grass/shrub with no green and little colour at all: WorldCover maps young
# Hawaiian flows mostly as grass (Mauna Loa 1984: 80% class 30, 13% bare).
# ponytail: tropics-only because dark bare scree reads the same - at any
# latitude summit_county got 631 Lava points. Iceland-style flows need more.
LAVA_MAX_VAL = 0.32
LAVA_MAX_GREEN = 0.0      # g - max(r, b); Oahu pasture median +0.03
LAVA_MAX_CHROMA = 0.075   # grass route only: Waianae's blue-cast shadowed
                          # slopes run 0.08-0.14, flows 0.02-0.07
LAVA_SMOOTH_PX = 41       # ~700 m box at z13 - flows are km-scale; Oahu's dim
                          # bare fields near Waipio drop 0.022 -> 0.003


def _wc_range(session, url, start, end):
    """Bytes [start, end) of url; b"" for a 3 deg tile WorldCover never
    published (open ocean)."""
    for attempt in range(4):
        try:
            r = session.get(url, headers={**HEADERS, "Range": f"bytes={start}-{end - 1}"},
                            timeout=60)
            if r.status_code in (403, 404):
                return b""
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 3:
                raise


def fetch_worldcover(west, south, east, north, ovr=WC_OVERVIEW):
    """WorldCover class grid (uint8, 0 = no data) over the bbox, plate
    carree -> (grid, (w, s, e, n)) snapped to whole pixels."""
    ppd, span = 12000 >> ovr, 3 * (12000 >> ovr)   # px per degree, px per 3 deg file
    c0, c1 = math.floor((west + 180) * ppd), math.ceil((east + 180) * ppd)
    r0, r1 = math.floor((90 - north) * ppd), math.ceil((90 - south) * ppd)
    grid = np.zeros((r1 - r0, c1 - c0), np.uint8)
    session = requests.Session()
    for fr in range(r0 // span, (r1 - 1) // span + 1):
        for fc in range(c0 // span, (c1 - 1) // span + 1):
            lat_s, lon_w = 90 - 3 * (fr + 1), 3 * fc - 180   # files are named by SW corner
            name = (f"{'N' if lat_s >= 0 else 'S'}{abs(lat_s):02d}"
                    f"{'E' if lon_w >= 0 else 'W'}{abs(lon_w):03d}")
            url = WC_URL.format(tile=name)
            hdr = cached_bytes("worldcover", f"{name}/header",
                               lambda: _wc_range(session, url, 0, WC_HEADER_BYTES))
            if not hdr:
                continue
            tif = TiffImagePlugin.TiffImageFile(BytesIO(hdr))   # no bomb check: header only
            tif.seek(ovr)
            tw, size = tif.tag_v2[322], tif.tag_v2[256]
            offs, cnts = tif.tag_v2[324], tif.tag_v2[325]
            ntx = -(-size // tw)
            fr0, fc0 = fr * span, fc * span   # file origin, global px
            for iy in range(max(0, (r0 - fr0) // tw), min(ntx, (r1 - 1 - fr0) // tw + 1)):
                for ix in range(max(0, (c0 - fc0) // tw), min(ntx, (c1 - 1 - fc0) // tw + 1)):
                    k = iy * ntx + ix
                    if not cnts[k]:
                        continue
                    raw = cached_bytes("worldcover", f"{name}/o{ovr}/{iy}_{ix}",
                                       lambda: _wc_range(session, url, offs[k], offs[k] + cnts[k]))
                    tile = np.frombuffer(zlib.decompress(raw), np.uint8).reshape(tw, tw)
                    ty0, tx0 = fr0 + iy * tw, fc0 + ix * tw
                    ya, yb = max(ty0, r0), min(ty0 + tw, r1, fr0 + size)
                    xa, xb = max(tx0, c0), min(tx0 + tw, c1, fc0 + size)
                    grid[ya - r0:yb - r0, xa - c0:xb - c0] = tile[ya - ty0:yb - ty0, xa - tx0:xb - tx0]
    print(f"worldcover {grid.shape[1]}x{grid.shape[0]} px at {10 << ovr} m")
    return grid, (c0 / ppd - 180, 90 - r1 / ppd, c1 / ppd - 180, 90 - r0 / ppd)


def sample_nearest_ll(grid, bounds, lon, lat):
    """Nearest-pixel lookup in a plate-carree grid (classes don't blend)."""
    w, s, e, n = bounds
    h, wd = grid.shape
    x = np.clip(((lon - w) / (e - w) * wd).astype(int), 0, wd - 1)
    y = np.clip(((n - lat) / (n - s) * h).astype(int), 0, h - 1)
    return grid[y, x]


def lava_weight(mosaic, bounds, wc_grid, wc_bounds):
    """Local lava fraction (0..1, LAVA_SMOOTH_PX box) on the imagery mosaic's
    own pixel grid - see LAVA_MAX_VAL."""
    w, s, e, n = bounds
    h, wd = mosaic.shape[:2]
    if abs(s + n) / 2 >= 23.5:
        return np.zeros((h, wd), np.float32)
    lon = w + (np.arange(wd) + 0.5) / wd * (e - w)
    merc = lambda phi: np.log(np.tan(np.pi / 4 + np.radians(phi) / 2))
    y = merc(n) - (np.arange(h) + 0.5) / h * (merc(n) - merc(s))
    lat = np.degrees(2 * np.arctan(np.exp(y))) - 90.0
    wc = sample_nearest_ll(wc_grid, wc_bounds, lon[None, :], lat[:, None])
    f = mosaic.astype(np.float32) / 255.0
    dark = f.max(axis=2) < LAVA_MAX_VAL
    green = f[..., 1] - np.maximum(f[..., 0], f[..., 2])
    chroma = f.max(axis=2) - f.min(axis=2)
    lava = dark & ((wc == WC_BARE) | (np.isin(wc, (WC_SHRUB, WC_GRASS))
                                      & (green < LAVA_MAX_GREEN) & (chroma < LAVA_MAX_CHROMA)))
    return ndimage.uniform_filter(lava.astype(np.float32), LAVA_SMOOTH_PX)


def _fetch_imagery_tile(session, z, x, y):
    url = TILE_URL.format(z=z, y=y, x=x)

    def fetch():
        for attempt in range(4):
            try:
                r = session.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                return r.content
            except requests.RequestException:
                if attempt == 3:
                    raise

    img = cached_bytes("usgs_imagery", f"{z}/{x}/{y}", fetch)
    return np.asarray(Image.open(BytesIO(img)).convert("RGB"))


def _fetch_dem_tile(session, z, x, y):
    return terrarium_to_elevation(fetch_terrarium_tile(z, x, y, session))


def fetch_mosaic(west, south, east, north, zoom=IMG_ZOOM, fetch_tile=_fetch_imagery_tile):
    """Slippy-tile mosaic over the bbox - imagery (uint8 RGB) by default,
    or the Terrarium DEM (float32 m) via fetch_tile=_fetch_dem_tile."""
    tiles = list(mercantile.tiles(west, south, east, north, [zoom]))
    xs, ys = [t.x for t in tiles], [t.y for t in tiles]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    print(f"fetching {cols * rows} tile(s) at z{zoom} ({cols}x{rows}) ...")
    mosaic = None
    session = requests.Session()
    for ty in range(y0, y1 + 1):
        for tx in range(x0, x1 + 1):
            tile = fetch_tile(session, zoom, tx, ty)
            if mosaic is None:
                mosaic = np.zeros((rows * 256, cols * 256) + tile.shape[2:], dtype=tile.dtype)
            r0, c0 = (ty - y0) * 256, (tx - x0) * 256
            mosaic[r0:r0 + 256, c0:c0 + 256] = tile
        print(f"  row {ty - y0 + 1}/{rows}")
    nw = mercantile.bounds(mercantile.Tile(x0, y0, zoom))
    se = mercantile.bounds(mercantile.Tile(x1, y1, zoom))
    return mosaic, (nw.west, se.south, se.east, nw.north)


def dem_derivatives(dem, bounds):
    """V2 per-pixel drivers from the DEM mosaic -> (grade, northness,
    moisture). Pixel size treated as uniform (mean latitude), the same
    linear-in-lat approximation sample_bilinear() already makes.

    northness in [-1,1]: +1 = steep north-facing (ground falls away to the
    north - shaded, snow-holding, denser/darker), -1 = steep south-facing
    (sun-baked, drier/tan/sparser); scaled by steepness so flat ground ~0.
    moisture in [0,1]: valley-bottom proxy, 1 at the lowest point within
    VALLEY_RADIUS_M, 0 once VALLEY_DRY_RISE_M above it."""
    w, s, e, n = bounds
    h, wd = dem.shape
    mid_lat = math.radians((s + n) / 2.0)
    px_m = (e - w) * 111132.0 * math.cos(mid_lat) / wd
    py_m = (n - s) * 111132.0 / h
    gy, gx = np.gradient(dem, py_m, px_m)   # gy = d/d(row) = d/d(south)
    grade = np.hypot(gx, gy)
    smooth = ndimage.gaussian_filter(dem, ASPECT_SMOOTH_M / px_m)
    sgy, _ = np.gradient(smooth, py_m, px_m)
    # north-facing = elevation DROPS going north = rises going south = sgy > 0
    northness = np.clip(sgy / NORTHNESS_FULL_GRADE, -1.0, 1.0)
    win = int(round(2 * VALLEY_RADIUS_M / px_m)) | 1
    rise = smooth - ndimage.minimum_filter(smooth, size=win)
    moisture = np.clip(1.0 - rise / VALLEY_DRY_RISE_M, 0.0, 1.0)
    return grade, northness, moisture


def coarse_grid(rect, corridor_east, corridor_north):
    """V1: GRID_SPACING_M lattice (local east/north frame, test_areas.to_local)
    over rect (east_min, north_min, east_max, north_max), minus any point
    within half a spacing of a corridor sample - near the road the dense
    corridor samples stay authoritative, exactly as before."""
    e0, n0, e1, n1 = rect
    east = np.arange(e0, e1, GRID_SPACING_M)
    north = np.arange(n0, n1, GRID_SPACING_M)
    ge, gn = (a.ravel() for a in np.meshgrid(east, north))
    d, _ = cKDTree(np.column_stack([corridor_east, corridor_north])).query(np.column_stack([ge, gn]))
    keep = d > GRID_SPACING_M / 2.0
    ge, gn = ge[keep], gn[keep]
    g_lon, g_lat = to_lonlat(ge, gn)
    return ge, gn, g_lat, g_lon


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
WETLAND_GRADE_MAX = 0.10  # see pick_biome()'s Wetland branch
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
    # row 0 = north. Web-mercator rows, not linear-in-latitude (2026-09-25):
    # the linear approximation was ~3-4 px (60-80 m at z13) off mid-mosaic
    # and the error moves whenever the mosaic bbox does (V1 grew it).
    merc = lambda phi: np.log(np.tan(np.pi / 4 + np.radians(phi) / 2))
    v = (merc(n) - merc(lat)) / (merc(n) - merc(s)) * h
    x0 = np.clip(u.astype(int), 0, wd - 2)
    y0 = np.clip(v.astype(int), 0, h - 2)
    fx, fy = u - x0, v - y0
    c00, c01 = chan[y0, x0], chan[y0, x0 + 1]
    c10, c11 = chan[y0 + 1, x0], chan[y0 + 1, x0 + 1]
    return c00 * (1 - fx) * (1 - fy) + c01 * fx * (1 - fy) \
        + c10 * (1 - fx) * fy + c11 * fx * fy


# Montane-conifer/high-desert elevation cutoff (m), added 2026-09-03 (Summit
# County/Breckenridge fix - see MONTANE_CUTOFF_M's uses below). Every area
# baked before summit_county tops out well under this (Green Valley's real
# sampled range is 317-1567 m, Skagit's foothills lower still, Oahu 0-1220 m)
# so this is a no-op everywhere except summit_county, where valley floors
# alone (Frisco/Dillon/Breckenridge, ~2600-2900 m real) sit well above it.
MONTANE_CUTOFF_M = 2200.0

# How far (m) a point must rise above ITS OWN AREA's real sampled valley
# floor before "rocky + steep" imagery reads as true alpine/treeline terrain,
# rather than an ordinary rocky roadcut through a forested valley. Replaces
# the old flat `elev_m > 1200.0` absolute cutoff (see ALPINE_ELEV_BASELINE_M
# below).
ALPINE_RISE_ABOVE_VALLEY_M = 900.0

# Aridity gate (2026-10-05): the Desert branch needs the area's annual
# precip (NASA POWER, fetch_climate.power_climatology) under the semi-arid
# ceiling. Without it, urban pavement/roofs - bright, warm, low-green - read
# as w_bare: sf_russian_hill (~580 mm/yr) baked
# 225/268 corridor samples DesertScrub. Green Valley (285-340 mm) passes.
# ponytail: one area-centre value, not a precip raster - fine at area scale.
ARID_MAX_PRECIP_MM = 500.0


def shore_mask(wc_grid):
    """True within SHORE_M of WorldCover water / no data (20 m px at WC_OVERVIEW)."""
    water = (wc_grid == WC_WATER) | (wc_grid == 0)
    return ndimage.distance_transform_edt(~water) * (10 << WC_OVERVIEW) <= SHORE_M


def pick_biome(w_forest, w_grass, w_rock, w_water, w_bare, elev_m, lat_deg,
                grade=1.0, area_elev_min_m=0.0, annual_precip_mm=0.0, wc=0, w_lava=0.0,
                shore=False):
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
    grade) still commits to Canyon/Alpine same as before.

    `area_elev_min_m` (2026-09-03 fix, Breckenridge/Coyne Valley Rd reviewer
    defect): the calling area's own real minimum sampled road elevation (see
    main()) - defaults to 0.0 for any caller without it, which reproduces the
    pre-fix behavior exactly for low-baseline areas. Every absolute-meters
    threshold in this function (1200 m alpine floor, 45 deg latitude for
    Evergreen conifer, no floor at all on the Desert branch) was tuned only
    against Skagit/Green Valley/Oahu, whose real elevation never exceeds
    ~1600 m - the exact same "stale constant carried over from a low-baseline
    area" bug class ODTerrainManager.cpp's ElevLowCm/ElevHighCm hit at the
    C++ layer (fixed fd7ed1f) when ACTIVE_AREA flipped to summit_county
    (valley floors ~2600-2900 m, origin 3435.3 m). Concretely, before this
    fix: (1) any rocky roadcut anywhere in a Colorado valley immediately
    qualified as BIOME_ALPINE (elev_m already > 1200 everywhere in the
    bbox), instead of needing genuine relief above the valley floor; (2)
    dense real conifer canopy could never reach BIOME_EVERGREEN at all since
    Summit County's 39.5N latitude is below the 45.0 Pacific-NW-only gate,
    landing in sparse Chaparral/Deciduous instead; (3) ordinary dry
    ground/gravel/dormant grass at 2900 m (nowhere near a real desert
    biome's actual elevation band) could still commit straight to
    BIOME_DESERT via w_bare with no elevation check at all. All three
    combined to read as "sparse tiny trees, bare gravel hillsides" over what
    should be dense lodgepole/spruce forest."""
    abs_lat = abs(lat_deg)
    arid = annual_precip_mm < ARID_MAX_PRECIP_MM   # see ARID_MAX_PRECIP_MM
    alpine_floor_m = max(1200.0, area_elev_min_m + ALPINE_RISE_ABOVE_VALLEY_M)
    # `wc`/`w_lava` (2026-10-14): ESA WorldCover class at the point (0 = none)
    # and lava_weight(); the defaults leave every branch as before.
    if w_lava > 0.5:
        return BIOME_LAVA
    if shore and elev_m < SHORE_MAX_ELEV_M and wc not in (WC_TREE, WC_BUILT, WC_WATER, 0):
        return BIOME_TROPICAL_COAST if abs_lat < 23.5 else BIOME_COASTAL
    if w_rock > 0.35 and grade > CANYON_GRADE_MIN:
        if elev_m > alpine_floor_m or (elev_m > 500.0 and abs_lat > 35.0
                                        and elev_m - area_elev_min_m > 500.0):
            return BIOME_ALPINE
        return BIOME_CANYON
    if elev_m < 5.0 and 0.1 < w_water <= 0.4:
        return BIOME_TROPICAL_COAST if abs_lat < 23.5 else BIOME_COASTAL
    # grade gate (2026-09-25, V1): the dark+blue-ish w_water signature also
    # matches shadowed north-facing conifer slopes - the first full-bbox grid
    # classified 21% of summit_county's grid as Wetland, mean northness +0.36
    # (scratchpad/terrain_variety/diag_wetland.py). Water/marsh is flat.
    if w_water > 0.4 and grade < WETLAND_GRADE_MAX:
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
    #
    # elev_m < MONTANE_CUTOFF_M added 2026-09-03: real SW high-desert scrub
    # (the biome this branch models) tops out well under 2200 m - bare/dry-
    # looking ground above that, in a genuinely montane region, is far more
    # likely dormant alpine meadow grass, dry scree, or a sunlit forest gap
    # than true desert, and the branches below (forest/grass split, Alpine
    # gate above) already handle those correctly once this stops eating them.
    if arid and w_bare > 0.35 and w_forest < 0.6 and elev_m < MONTANE_CUTOFF_M:
        return BIOME_DESERT
    # Tropics only: dim blue-cast pasture/ag reads as the w_forest leftover.
    # Built-up too - towns (Wahiawa: 936 of the Dole plateau's 1552 samples)
    # are lawns + scattered big trees, not jungle.
    if abs_lat < 23.5 and wc in (WC_GRASS, WC_CROP, WC_BUILT):
        return BIOME_FARMLAND if wc == WC_CROP else BIOME_SAVANNA
    if w_forest >= w_grass:
        if abs_lat < 23.5:
            return BIOME_RAINFOREST
        # elev_m > MONTANE_CUTOFF_M added 2026-09-03: real conifer forest
        # isn't exclusive to high latitude - Rocky Mountain montane/subalpine
        # forest is driven by ELEVATION and was otherwise unreachable at
        # Summit County's 39.5N (< the old 45.0-only gate), forcing real
        # lodgepole/spruce canopy into Chaparral/Deciduous instead.
        if abs_lat > 45.0 or elev_m > MONTANE_CUTOFF_M:
            return BIOME_EVERGREEN   # Pacific NW default / real montane conifer
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
    # east_m/north_m are already in the SAME local frame (test_areas.
    # to_local) every other consumer uses - reuse directly rather than
    # re-deriving the lon/lat projection a second time. UE frame is
    # +X=east, +Y=south (road_mesh_common.py), so Y_cm = -north_m * 100.
    east_m = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north_m = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    # Real minimum sampled elevation for THIS area's own road network (full
    # 5 m station spacing, before SAMPLE_STRIDE below) - see pick_biome()'s
    # area_elev_min_m docstring. Using the actual data instead of another
    # area's hand-tuned constant is what keeps the Alpine/Evergreen/Desert
    # bands correct across areas with wildly different elevation baselines
    # (Skagit near sea level vs. summit_county's 2364-4053 m span) without
    # per-area special-casing.
    area_elev_min_m = float(elev.min())
    s, w, n, e = BBOX_SNWE
    annual_precip_mm = power_climatology((s + n) / 2, (w + e) / 2)["PRECTOTCORR"]["ANN"] * 365.25
    print(f"annual precip {annual_precip_mm:.0f} mm -> Desert "
          f"{'allowed' if annual_precip_mm < ARID_MAX_PRECIP_MM else 'off'}")
    lon, lat, elev = lon[::SAMPLE_STRIDE], lat[::SAMPLE_STRIDE], elev[::SAMPLE_STRIDE]
    grade = grade[::SAMPLE_STRIDE]
    east_m, north_m = east_m[::SAMPLE_STRIDE], north_m[::SAMPLE_STRIDE]
    x_cm, y_cm = east_m * 100.0, -north_m * 100.0
    print(f"{len(lon)} sample point(s) (every {SAMPLE_STRIDE}th station)")

    # V1: grid covers the whole rendered terrain (background bbox when the
    # area has one); the fetch covers corridor+margin AND that grid. Tiles
    # are aligned to the global z13 lattice, so corridor pixels (and thus
    # corridor classifications) are identical to a corridor-only fetch.
    # + one terrain tile of padding: tiles are grid-aligned, so the edge ring
    # of baked terrain tiles overhangs the bbox by up to a tile (the first
    # unpadded run left 24-100% of those edge tiles' verts untinted).
    # Pads are in local metres, fetch bboxes their projected-edge lon/lat
    # cover (2026-10-05) - LCC rotates the local rectangle against lon/lat.
    pad = TERRAIN_TILE_SIZE_CM / 100.0
    ge0, gn0, ge1, gn1 = local_rect(BACKGROUND_BBOX_SNWE or BBOX_SNWE)
    grid_rect = (ge0 - pad, gn0 - pad, ge1 + pad, gn1 + pad)
    gs, gw, gn_, ge_ = lonlat_bbox(*grid_rect)
    cs, cw, cn, ce = lonlat_bbox(east_m.min() - MARGIN_M, north_m.min() - MARGIN_M,
                                 east_m.max() + MARGIN_M, north_m.max() + MARGIN_M)
    west, east, south, north = min(cw, gw), max(ce, ge_), min(cs, gs), max(cn, gn_)

    g_east, g_north, g_lat, g_lon = coarse_grid(grid_rect, east_m, north_m)
    print(f"{len(g_lon)} coarse grid point(s) at {GRID_SPACING_M:.0f} m")

    dem, dem_bounds = fetch_mosaic(west, south, east, north, DEM_ZOOM, _fetch_dem_tile)
    d_grade, d_north, d_moist = dem_derivatives(dem, dem_bounds)
    g_elev = sample_bilinear(dem, dem_bounds, g_lon, g_lat)
    g_grade = sample_bilinear(d_grade, dem_bounds, g_lon, g_lat)

    n_corr = len(lon)
    lon, lat = np.concatenate([lon, g_lon]), np.concatenate([lat, g_lat])
    elev, grade = np.concatenate([elev, g_elev]), np.concatenate([grade, g_grade])
    x_cm = np.concatenate([x_cm, g_east * 100.0])
    y_cm = np.concatenate([y_cm, -g_north * 100.0])
    northness = sample_bilinear(d_north, dem_bounds, lon, lat)
    moisture = sample_bilinear(d_moist, dem_bounds, lon, lat)
    del dem, d_grade, d_north, d_moist

    mosaic, bounds = fetch_mosaic(west, south, east, north)
    w_forest, w_grass, w_rock, w_water, w_bare = classify_rgb(mosaic)
    wc_grid, wc_bounds = fetch_worldcover(west, south, east, north)
    wc = sample_nearest_ll(wc_grid, wc_bounds, lon, lat)
    shore = sample_nearest_ll(shore_mask(wc_grid), wc_bounds, lon, lat)
    sl = sample_bilinear(lava_weight(mosaic, bounds, wc_grid, wc_bounds), bounds, lon, lat)
    del mosaic, wc_grid

    sf = sample_bilinear(w_forest, bounds, lon, lat)
    sg = sample_bilinear(w_grass, bounds, lon, lat)
    sr = sample_bilinear(w_rock, bounds, lon, lat)
    sw = sample_bilinear(w_water, bounds, lon, lat)
    sb = sample_bilinear(w_bare, bounds, lon, lat)

    biomes = [pick_biome(float(sf[i]), float(sg[i]), float(sr[i]), float(sw[i]),
                          float(sb[i]), float(elev[i]), float(lat[i]), float(grade[i]),
                          area_elev_min_m, annual_precip_mm, int(wc[i]), float(sl[i]),
                          bool(shore[i]))
              for i in range(len(lon))]
    forest_weight = np.clip(sf + 0.3 * sg, 0, 1)   # density signal, not just the forest channel

    out = {
        "lon": lon.tolist(), "lat": lat.tolist(),
        "x_cm": x_cm.tolist(), "y_cm": y_cm.tolist(),
        "biome": biomes, "forest_weight": forest_weight.tolist(),
        # V2 (sec 7.35): aspect/moisture drivers + sample elevation (V5
        # species banding); first n_corridor entries are the road samples.
        "northness": np.round(northness, 3).tolist(),
        "moisture": np.round(moisture, 3).tolist(),
        "elev_m": np.round(elev, 1).tolist(),
        "n_corridor": n_corr,
    }
    OUT_PATH.write_text(json.dumps(out))
    print(f"wrote {OUT_PATH} ({len(lon)} points: {n_corr} corridor + {len(lon) - n_corr} grid)")
    for label, sl in (("corridor", slice(0, n_corr)), ("grid", slice(n_corr, None)),
                      ("all", slice(None))):
        counts = np.bincount(np.asarray(biomes[sl]), minlength=len(BIOME_NAMES))
        print(f"biome counts ({label}): " + ", ".join(
            f"{BIOME_NAMES[b]}={n}" for b, n in enumerate(counts) if n))


if __name__ == "__main__":
    main()
