"""Bakes a coarse ambient-audio zone grid for AODAmbientSoundManager (Arc 5.4,
next steps.md SS7.28 - "ambient biome/city audio").

Design choice, per Taylor's brief: reuse the SAME tile-level biome/settlement
classification data classify_biomes.py/classify_settlements.py already
produce for AODForestManager/AODBuildingManager, rather than inventing a
separate from-scratch spatial system - and keep the bake itself SIMPLE, a
coarse uniform grid, not a new streaming-tile format. Unlike the guardrail/
embankment/bridge managers this deliberately does NOT stream per-2km-tile
files: the payload here is a flat per-cell zone/density/water-distance
lookup table for the WHOLE map, small enough (a few hundred KB at 100 m
cells) to load once in BeginPlay and index with simple math - no async
load/unload plumbing earns its keep for data this size.

Three independent signals are combined per grid cell:
  - dominant BIOME (classify_biomes.py's 14-archetype road_biomes.json,
    nearest-sample-point lookup) -> Forest / Alpine / Open
  - SETTLEMENT (classify_settlements.py's 2km-tile settlement_tiles.json,
    directly reused, same tile grid) -> overrides biome with a Settlement
    zone + a density byte (City=255, AmericanTown=140) driving the distant-
    traffic-hum mix level
  - WATER DISTANCE (data/raw/<AREA>_test/osm_water.json lake polygons +
    osm_waterways.json raw waterway ways) -> a per-cell distance-to-nearest-
    water-feature in meters, capped at WATER_DIST_CAP_M, computed with a
    boolean-raster + scipy distance_transform_edt (fast and exact enough at
    this cell size). Each waterway WAY's own already-resolved lat/lon
    points are rasterized directly (segment-by-segment point-to-line
    distance) rather than first stitching same-name fragments into one
    logical river the way build_water_tiles.py's chain_waterways() does for
    its ribbon MESH - a pure distance mask doesn't need the stitched
    identity, only "is there water near this cell", so per-way segments are
    sufficient and simpler here. This is a SEPARATE signal from the zone
    class (not "dominant zone"), because the water ambience is meant to
    fade in near a creek/lake edge independent of which biome/settlement
    zone that edge sits inside - see AODAmbientSoundManager's water-layer
    crossfade.

Usage:  python build_ambient_zones.py   (needs road_biomes.json,
        settlement_tiles.json, osm_water.json; osm_waterways.json optional)

Writes  OpenDriveUE/Content/OpenDrive/Masks/ambient_zones_<AREA_NAME>_test.bin

ODA2 format (next steps.md SS7.29 - bumped from ODA1, single file, whole
map, no tile suffix): ODA1 stored ONE water-distance channel, combining lake
and waterway (creek/river) proximity into a single "distance to nearest
water feature" number - fine for a single generic Water ambience layer, but
AODAmbientSoundManager now splits Water into two independently-crossfaded
layers (CreekSound vs LakeSound, see that class's header comment), which
need two INDEPENDENT distance fields - a cell can be near a creek and far
from any lake, or vice versa (e.g. Dillon Reservoir's shoreline is far from
the nearest free-flowing creek). ODA2 replaces the single WaterDistM field
with LakeDistM (distance-transform_edt over the same osm_water.json lake/
reservoir polygons build_water_tiles.py rasterizes) and WaterwayDistM
(same osm_waterways.json ribbon-chain rasterization as ODA1's WaterDistM
used) side by side. The magic bump (not a version field) is deliberate -
this is a field-count AND field-meaning change, not a purely additive one,
so the C++ loader must refuse to misread an old ODA1 file as ODA2 (a
straight reinterpret would misalign every field after the old WaterDistM),
not silently degrade.

    'O','D','A','2'                                    magic (4 bytes)
    16 bytes    generation id (ASCII hex, pipeline/generation_id.py)
    int32 LE    GridOriginXCm, GridOriginYCm            (world cm, cell [0,0]'s
                                                          min corner)
    int32 LE    GridCols, GridRows
    float32 LE  CellSizeCm
    GridCols*GridRows cells, row-major (y outer, x inner), each:
        uint8   ZoneClass       (0=Open, 1=Forest, 2=Alpine, 3=Settlement)
        uint8   Density255      (0 unless ZoneClass==Settlement; town-hum mix
                                  scale, City=255 AmericanTown=140)
        uint16 LE LakeDistM     (0..WATER_DIST_CAP_M, meters, capped -
                                  distance to nearest osm_water.json lake/
                                  reservoir polygon)
        uint16 LE WaterwayDistM (0..WATER_DIST_CAP_M, meters, capped -
                                  distance to nearest osm_waterways.json
                                  creek/river ribbon)
        uint8   Reserved        (0 - unused, keeps the record word-ish sized
                                  and leaves room for a future flag)
"""

import json
import math
import struct
from pathlib import Path

import numpy as np
from scipy.ndimage import distance_transform_edt

from generation_id import compute_generation_id, pack_generation_id
from test_areas import AREA_NAME

M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
ROAD_DIR = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"
RAW_DIR = ROOT / "data" / "raw" / f"{AREA_NAME}_test"
META_PATH = ROAD_DIR / "road_test_meta.json"
BIOMES_PATH = ROAD_DIR / "road_biomes.json"
SETTLEMENTS_PATH = ROAD_DIR / "settlement_tiles.json"
WATER_PATH = RAW_DIR / "osm_water.json"
WATERWAYS_PATH = RAW_DIR / "osm_waterways.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

TERRAIN_TILE_SIZE_CM = 200000.0   # same 2 km grid settlement_tiles.json keys off

CELL_SIZE_M = 100.0               # coarse grid, per Taylor's brief - fine
                                   # enough for a ~60 m water-proximity fade,
                                   # far coarser than any per-tile mesh format
CELL_SIZE_CM = CELL_SIZE_M * 100.0
MARGIN_M = 500.0                  # pad the grid past the outermost road sample

WATER_DIST_CAP_M = 500            # nothing downstream needs more than this -
                                   # the runtime fade-in window is ~60 m and
                                   # this is just a lookup table, not a
                                   # physically meaningful "distance to water"
                                   # past a couple hundred meters
WATERWAY_HALFWIDTH_M = 3.0        # default creek half-width when OSM has no
                                   # width tag - matches a modest mountain
                                   # stream, not a claim about any specific
                                   # real creek's actual width

ZONE_OPEN, ZONE_FOREST, ZONE_ALPINE, ZONE_SETTLEMENT = 0, 1, 2, 3

# classify_biomes.py's 14-archetype ids (BIOME_NAMES order) bucketed into
# the 3 ambient wind-bed classes this manager actually plays audio for.
# Deliberately coarse - see next steps.md SS7.28 for the "4 ambient classes
# is plenty" scoping call.
FOREST_BIOME_IDS = {0, 1, 2, 10}         # Deciduous, Evergreen, Chaparral, Rainforest
ALPINE_BIOME_IDS = {6, 7}                # Alpine, Arctic
# everything else (Desert, Plains, Wetland, Farmland, Savanna, Coastal,
# Canyon, Foothill) reads as ZONE_OPEN - a plain wind bed, which is a
# reasonable default for open/rocky/dry terrain that isn't specifically
# forested or above-treeline alpine.

SETTLEMENT_DENSITY = {"City": 255, "AmericanTown": 140}


def project_latlon(lat, lon, lat0, lon0, m_per_deg_lon):
    east_m = (lon - lon0) * m_per_deg_lon
    north_m = (lat - lat0) * M_PER_DEG_LAT
    return east_m * 100.0, -north_m * 100.0   # x_cm, y_cm (UE +X=east, +Y=south)


def load_biome_grid_lookup():
    data = json.loads(BIOMES_PATH.read_text())
    x_cm = np.asarray(data["x_cm"], dtype=np.float64)
    y_cm = np.asarray(data["y_cm"], dtype=np.float64)
    biome = np.asarray(data["biome"], dtype=np.int32)
    return x_cm, y_cm, biome


def rasterize_water(cols, rows, ox_cm, oy_cm, lat0, lon0, m_per_deg_lon):
    """Two independent boolean cell masks (next steps.md SS7.29 lake-vs-
    creek split, replaces ODA1's single combined `water` mask):
      - lake_mask:     True where a grid cell's 100 m FOOTPRINT (not just
                        its center - see below) intersects an osm_water.json
                        lake/reservoir/pond polygon.
      - waterway_mask: True where a grid cell center is within
                        WATERWAY_HALFWIDTH_M of a chained osm_waterways.json
                        centerline, widened by the cell's own half-diagonal
                        so the CELL FOOTPRINT (not just its center) actually
                        crossed by the line gets marked - see the
                        hit_threshold_cm comment below (next steps.md SS7.29
                        correction note).
    osm_water.json for this area is ~1600 polygons and the median one is
    ~30-40 m across - SMALLER than one 100 m cell, not "tiny" the way the
    original comment here assumed. A pure cell-CENTER point-in-polygon test
    (the original version of this function) missed 1239/1600 of them
    entirely: a pond has to happen to straddle a cell center to register at
    all. Fixed by testing actual polygon-vs-cell-square intersection with
    shapely, bbox-limited to each polygon's own padded extent the same way
    the waterway loop below is bbox-limited to each segment's extent (lake
    count is no longer "tiny" but per-polygon cell counts are small, so this
    stays fast - ~1s for the whole file, not the >15 min full-grid approach
    the module docstring warns off). Kept as one function (not split in
    two) since both masks share the same cell grid setup."""
    from shapely.geometry import Polygon as ShapelyPolygon, box as shapely_box
    from shapely import prepare

    cell_cx = ox_cm + (np.arange(cols) + 0.5) * CELL_SIZE_CM
    cell_cy = oy_cm + (np.arange(rows) + 0.5) * CELL_SIZE_CM

    lake_mask = np.zeros((rows, cols), dtype=bool)
    waterway_mask = np.zeros((rows, cols), dtype=bool)

    n_lakes = 0
    if WATER_PATH.is_file():
        polys = json.loads(WATER_PATH.read_text())
        for poly in polys:
            ring = poly.get("nodes")
            if not ring or len(ring) < 3:
                continue
            latlon = np.asarray(ring, dtype=np.float64)
            x_cm, y_cm = project_latlon(latlon[:, 0], latlon[:, 1], lat0, lon0, m_per_deg_lon)
            verts = np.column_stack([x_cm, y_cm])
            try:
                shp = ShapelyPolygon(verts)
                if not shp.is_valid:
                    shp = shp.buffer(0)
                if shp.is_empty:
                    continue
            except Exception:
                continue
            n_lakes += 1

            # Padded bbox of this one polygon, in grid cell index space -
            # pad by a full cell so a cell whose square merely clips the
            # polygon boundary right at the bbox edge isn't dropped.
            minx, miny, maxx, maxy = shp.bounds
            c0 = max(0, int((minx - CELL_SIZE_CM - ox_cm) // CELL_SIZE_CM))
            c1 = min(cols, int((maxx + CELL_SIZE_CM - ox_cm) // CELL_SIZE_CM) + 1)
            r0 = max(0, int((miny - CELL_SIZE_CM - oy_cm) // CELL_SIZE_CM))
            r1 = min(rows, int((maxy + CELL_SIZE_CM - oy_cm) // CELL_SIZE_CM) + 1)
            if c1 <= c0 or r1 <= r0:
                continue

            prepare(shp)
            for rr in range(r0, r1):
                cell_y0 = oy_cm + rr * CELL_SIZE_CM
                for cc in range(c0, c1):
                    if lake_mask[rr, cc]:
                        continue
                    cell_x0 = ox_cm + cc * CELL_SIZE_CM
                    cell_box = shapely_box(cell_x0, cell_y0, cell_x0 + CELL_SIZE_CM, cell_y0 + CELL_SIZE_CM)
                    if shp.intersects(cell_box):
                        lake_mask[rr, cc] = True

    n_waterway_segs = 0
    halfwidth_cm = WATERWAY_HALFWIDTH_M * 100.0
    # Cells are CELL_SIZE_CM (100 m) wide, so a cell CENTER being within
    # halfwidth_cm of the centerline is only true for the handful of cells
    # the line happens to pass almost dead through - a polyline threading a
    # 100 m grid mostly clips cell EDGES, leaving centers many meters off
    # even though the segment plainly crosses the cell. Widen the hit test
    # by the cell's own half-diagonal (worst-case center-to-corner reach)
    # so every cell footprint the segment actually passes through is
    # registered - this is what turned a ~542-cell scatter of near-misses
    # into a connected ~8-9k cell trace of the waterway network (see
    # next steps.md SS7.29 correction note).
    hit_threshold_cm = halfwidth_cm + CELL_SIZE_CM * 0.5 * math.sqrt(2.0)
    if WATERWAYS_PATH.is_file():
        raw = json.loads(WATERWAYS_PATH.read_text())
        # Each way already carries resolved [lat,lon] "pts" (see
        # fetch_waterways() in build_water_tiles.py) - use those directly,
        # per-way, rather than re-resolving through the node-id map (see
        # module docstring for why chaining into named rivers isn't needed
        # for a pure distance mask).
        for way in raw["ways"]:
            pts_ll = way.get("pts")
            if not pts_ll or len(pts_ll) < 2:
                continue
            latlon = np.asarray(pts_ll, dtype=np.float64)
            x_cm, y_cm = project_latlon(latlon[:, 0], latlon[:, 1], lat0, lon0, m_per_deg_lon)
            seg_pts = np.column_stack([x_cm, y_cm])
            for i in range(len(seg_pts) - 1):
                a, b = seg_pts[i], seg_pts[i + 1]
                ab = b - a
                ab_len2 = float(ab[0] ** 2 + ab[1] ** 2)
                if ab_len2 < 1e-6:
                    continue

                # Padded bounding box of this one segment, in grid cell
                # index space - the ONLY cells this segment can possibly
                # mark, since halfwidth_cm is a tight creek half-width. Pad
                # by the same hit_threshold_cm used below (plus a hair of
                # slack) so the bbox can't clip a cell the widened center
                # test would otherwise mark.
                pad = hit_threshold_cm + CELL_SIZE_CM * 0.01
                c0 = max(0, int((min(a[0], b[0]) - pad - ox_cm) // CELL_SIZE_CM))
                c1 = min(cols, int((max(a[0], b[0]) + pad - ox_cm) // CELL_SIZE_CM) + 1)
                r0 = max(0, int((min(a[1], b[1]) - pad - oy_cm) // CELL_SIZE_CM))
                r1 = min(rows, int((max(a[1], b[1]) + pad - oy_cm) // CELL_SIZE_CM) + 1)
                if c1 <= c0 or r1 <= r0:
                    continue

                sub_gx, sub_gy = np.meshgrid(cell_cx[c0:c1], cell_cy[r0:r1])
                sub_pts = np.column_stack([sub_gx.ravel(), sub_gy.ravel()])
                t = ((sub_pts[:, 0] - a[0]) * ab[0] + (sub_pts[:, 1] - a[1]) * ab[1]) / ab_len2
                t = np.clip(t, 0.0, 1.0)
                proj = a + t[:, None] * ab
                d = np.hypot(sub_pts[:, 0] - proj[:, 0], sub_pts[:, 1] - proj[:, 1])
                hit = (d <= hit_threshold_cm).reshape(r1 - r0, c1 - c0)
                waterway_mask[r0:r1, c0:c1] |= hit
            n_waterway_segs += len(seg_pts) - 1

    print(f"  water raster: {n_lakes} lake polygon(s) -> {int(lake_mask.sum())}/{lake_mask.size} lake cell(s), "
          f"{n_waterway_segs} waterway segment(s) -> {int(waterway_mask.sum())}/{waterway_mask.size} waterway cell(s)")
    return lake_mask, waterway_mask


def main():
    meta = json.loads(META_PATH.read_text())
    lat0, lon0 = meta["origin_latlon"]
    import math
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    bx, by, biome = load_biome_grid_lookup()
    settlements = json.loads(SETTLEMENTS_PATH.read_text())["tiles"] if SETTLEMENTS_PATH.is_file() else {}

    pad_cm = MARGIN_M * 100.0
    min_x, max_x = float(bx.min() - pad_cm), float(bx.max() + pad_cm)
    min_y, max_y = float(by.min() - pad_cm), float(by.max() + pad_cm)

    ox_cm = math.floor(min_x / CELL_SIZE_CM) * CELL_SIZE_CM
    oy_cm = math.floor(min_y / CELL_SIZE_CM) * CELL_SIZE_CM
    cols = int(math.ceil((max_x - ox_cm) / CELL_SIZE_CM))
    rows = int(math.ceil((max_y - oy_cm) / CELL_SIZE_CM))
    print(f"grid: {cols} x {rows} cells @ {CELL_SIZE_M:.0f} m, origin ({ox_cm:.0f},{oy_cm:.0f}) cm")

    # --- dominant biome per cell: nearest road_biomes.json sample point ---
    # cKDTree nearest-neighbour - road_biomes.json is road-corridor-sampled
    # (not a full raster), so "nearest sample" is exactly the right lookup:
    # it reads as "whatever biome the nearest bit of actually-sampled road
    # corridor was classified as", same spatial resolution the terrain/
    # forest bake itself already commits to.
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([bx, by]))
    cx = ox_cm + (np.arange(cols) + 0.5) * CELL_SIZE_CM
    cy = oy_cm + (np.arange(rows) + 0.5) * CELL_SIZE_CM
    gx, gy = np.meshgrid(cx, cy)
    _, nearest_idx = tree.query(np.column_stack([gx.ravel(), gy.ravel()]), k=1)
    nearest_biome = biome[nearest_idx].reshape(rows, cols)

    zone = np.full((rows, cols), ZONE_OPEN, dtype=np.uint8)
    zone[np.isin(nearest_biome, list(FOREST_BIOME_IDS))] = ZONE_FOREST
    zone[np.isin(nearest_biome, list(ALPINE_BIOME_IDS))] = ZONE_ALPINE

    density = np.zeros((rows, cols), dtype=np.uint8)
    n_settlement_cells = 0
    for key, archetype in settlements.items():
        tx, ty = (int(v) for v in key.split(","))
        tile_min_x, tile_min_y = tx * TERRAIN_TILE_SIZE_CM, ty * TERRAIN_TILE_SIZE_CM
        tile_max_x, tile_max_y = tile_min_x + TERRAIN_TILE_SIZE_CM, tile_min_y + TERRAIN_TILE_SIZE_CM
        c0 = max(0, int((tile_min_x - ox_cm) // CELL_SIZE_CM))
        c1 = min(cols, int(math.ceil((tile_max_x - ox_cm) / CELL_SIZE_CM)))
        r0 = max(0, int((tile_min_y - oy_cm) // CELL_SIZE_CM))
        r1 = min(rows, int(math.ceil((tile_max_y - oy_cm) / CELL_SIZE_CM)))
        if c1 <= c0 or r1 <= r0:
            continue
        zone[r0:r1, c0:c1] = ZONE_SETTLEMENT
        density[r0:r1, c0:c1] = SETTLEMENT_DENSITY.get(archetype, 140)
        n_settlement_cells += (r1 - r0) * (c1 - c0)

    # --- lake and waterway distance per cell (independent channels, ODA2) ---
    lake_mask, waterway_mask = rasterize_water(cols, rows, ox_cm, oy_cm, lat0, lon0, m_per_deg_lon)

    def dist_from_mask(mask):
        if mask.any():
            d = distance_transform_edt(~mask) * CELL_SIZE_M
        else:
            d = np.full((rows, cols), float(WATER_DIST_CAP_M))
        return np.clip(d, 0, WATER_DIST_CAP_M)

    lake_dist_m = dist_from_mask(lake_mask)
    waterway_dist_m = dist_from_mask(waterway_mask)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    out_path = OUT_DIR / f"ambient_zones_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODA2")
        f.write(pack_generation_id(gen_id))
        f.write(struct.pack("<iiiif", int(ox_cm), int(oy_cm), cols, rows, CELL_SIZE_CM))
        for r in range(rows):
            for c in range(cols):
                f.write(struct.pack("<BBHHB", int(zone[r, c]), int(density[r, c]),
                                     int(round(lake_dist_m[r, c])),
                                     int(round(waterway_dist_m[r, c])), 0))

    zone_counts = {int(k): int(v) for k, v in zip(*np.unique(zone, return_counts=True))}
    file_size = 4 + 16 + 20 + cols * rows * 7
    print(f"generation id: {gen_id}")
    print(f"wrote {out_path} ({cols * rows} cells, {file_size} bytes, ODA2)")
    print(f"zone counts: Open={zone_counts.get(0,0)} Forest={zone_counts.get(1,0)} "
          f"Alpine={zone_counts.get(2,0)} Settlement={zone_counts.get(3,0)} "
          f"({n_settlement_cells} cells from {len(settlements)} settlement tile(s))")
    print(f"lake-adjacent cells (<= 60 m): {int((lake_dist_m <= 60).sum())}")
    print(f"waterway-adjacent cells (<= 60 m): {int((waterway_dist_m <= 60).sum())}")
    print(f"both-adjacent (<= 60 m, confluence overlap): "
          f"{int(((lake_dist_m <= 60) & (waterway_dist_m <= 60)).sum())}")


if __name__ == "__main__":
    main()
