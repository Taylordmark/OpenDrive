"""Build a stylized terrain-color background image for the in-game map
(mini-map + full map, ODRaceHUD::DrawStageMap) - water/grass/forest/rock
behind the road line instead of a bare polyline on flat black (2026-07-26).

Reuses classify_biomes.py's imagery fetch + RGB classification wholesale
(same mosaic, same w_forest/w_grass/w_rock/w_water heuristic) rather than
re-deriving anything - that script already computes exactly this per-pixel
signal for the road's bbox, it just only ever SAMPLED it at road stations
instead of rasterizing it into an image.

Usage:  python build_map_texture.py     (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Outputs data/road_tiles/<AREA_NAME>_test/map_texture.png
        data/road_tiles/<AREA_NAME>_test/map_texture_meta.json
            {"center_x_cm": ..., "center_y_cm": ..., "span_cm": ...}
        center/span are in the SAME local east/north-cm frame every other
        consumer (RoadCenterline, PlaceLabels, TownNodes) uses - x_cm = east_m
        * 100, y_cm = -north_m * 100 (see classify_biomes.py's main()) - so
        AODRoadManager's MapTexture can be placed with no extra transform.
"""

import json
from pathlib import Path

import numpy as np
from PIL import Image

import test_areas
from classify_biomes import classify_rgb, fetch_mosaic, sample_bilinear
from test_areas import AREA_NAME, local_rect, lonlat_bbox, to_lonlat

# Optional per-area wider bbox the map background should also cover, even
# though the OSM road network (and this script's own default road-station
# extent below) stays scoped to the tight corridor - see test_areas.py's
# own comment and build_terrain_mesh_tiles.py's matching use of this same
# constant for far/no-collision background terrain tiles. getattr, not a
# plain import, so areas that don't define it don't need a stub None of
# their own.
BACKGROUND_BBOX_SNWE = getattr(test_areas, "BACKGROUND_BBOX_SNWE", None)

MARGIN_M = 2000.0       # same buffer classify_biomes.py fetches beyond the road bbox
OUT_PX = 768             # square output resolution
SUPERSAMPLE = 3          # resample at 3x, then PIL's filtered downscale

# classify_rgb()'s w_water = dark-and-blue-tinted - true for real water, but
# ALSO true for shadowed mountain slopes under blue-sky ambient light, which
# this Cascades-foothills imagery has a lot of. classify_biomes.py itself
# mostly dodges this (its biome picker checks w_rock first and only uses
# w_water at low elevation for most paths), but this script paints straight
# from the raw weights with no elevation signal to fall back on, so w_water's
# default >0.4 bar painted whole shadowed ridgelines blue (confirmed via a
# one-off diagnostic: 22% of pixels scored water>0.4, with a mean brightness
# of 0.20 - squarely in shadow range, not open water). Gating to a much
# stricter bar (measured empirically against this test area's cached mosaic)
# plus a minimum brightness floor cuts that to ~1.4%, which reads as real
# rivers/lakes instead of a wash over every dark slope.
WATER_THRESH = 0.9
WATER_MIN_VAL = 0.12

STATIONS_PATH = (Path(__file__).resolve().parent.parent / "data" / "road_tiles"
                  / f"{AREA_NAME}_test" / "road_stations.json")
OUT_PNG = STATIONS_PATH.parent / "map_texture.png"
OUT_META = STATIONS_PATH.parent / "map_texture_meta.json"

# stylized flat colors (storybook art direction, not photoreal satellite look)
# - order matches classify_rgb()'s (w_forest, w_grass, w_rock, w_water) return
COLOR_FOREST = np.array([46, 92, 58], dtype=np.float32)
COLOR_GRASS = np.array([124, 168, 88], dtype=np.float32)
COLOR_ROCK = np.array([120, 112, 100], dtype=np.float32)
COLOR_WATER = np.array([58, 102, 150], dtype=np.float32)


def main():
    data = json.loads(STATIONS_PATH.read_text())
    east_m = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north_m = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    e0, n0 = float(east_m.min() - MARGIN_M), float(north_m.min() - MARGIN_M)
    e1, n1 = float(east_m.max() + MARGIN_M), float(north_m.max() + MARGIN_M)

    # Widen to also cover BACKGROUND_BBOX_SNWE (if this area defines one) -
    # union, not replace, so a background bbox smaller than the road
    # network's own extent (shouldn't happen, but not assumed) can't
    # shrink the map.
    if BACKGROUND_BBOX_SNWE is not None:
        be0, bn0, be1, bn1 = local_rect(BACKGROUND_BBOX_SNWE)
        e0, n0, e1, n1 = min(e0, be0), min(n0, bn0), max(e1, be1), max(n1, bn1)

    # Square in local metres, padding the narrower axis outward - never
    # cropping the wider one (2026-07-28 Oahu bug: a cropped axis lost real
    # coastline and the road read as "inset" against the map art).
    # (2026-10-05) The square is built in the local frame and every output
    # pixel is sampled through to_lonlat, not cropped out of the lon/lat
    # mosaic: under LCC the mosaic is rotated by meridian convergence (~2.4
    # deg at far_gasquet), so no axis-aligned crop of it lines up with UE.
    span_m = max(e1 - e0, n1 - n0)
    center_east_m, center_north_m = (e0 + e1) / 2.0, (n0 + n1) / 2.0
    e0, n1 = center_east_m - span_m / 2.0, center_north_m + span_m / 2.0

    s_, w, n_, e = lonlat_bbox(e0, n1 - span_m, e0 + span_m, n1)
    mosaic, bounds = fetch_mosaic(w, s_, e, n_)
    # classify_rgb() now also returns w_bare (2026-08-27, classify_biomes.py
    # desert-detection fix) - unused here, this is a map-texture blend, not
    # a biome classification.
    w_forest, w_grass, w_rock, w_water, _w_bare = classify_rgb(mosaic)

    # see WATER_THRESH's comment above - gate out shadow-triggered false
    # water before it ever reaches the blend below.
    sat = mosaic.astype(np.float32) / 255.0
    val = sat.max(axis=2)
    w_water = np.where((w_water > WATER_THRESH) & (val > WATER_MIN_VAL), w_water, 0.0)

    # weighted blend, not a hard argmax - reads softer/more natural at the
    # low resolution this gets downsampled to, same reasoning as the
    # forest_weight blend classify_biomes.py already uses for scatter density
    total = np.clip(w_forest + w_grass + w_rock + w_water, 1e-3, None)
    rgb = (w_forest[..., None] * COLOR_FOREST + w_grass[..., None] * COLOR_GRASS
           + w_rock[..., None] * COLOR_ROCK + w_water[..., None] * COLOR_WATER) / total[..., None]

    # row 0 = north, col 0 = west, in the local frame
    n_px = OUT_PX * SUPERSAMPLE
    t = (np.arange(n_px) + 0.5) / n_px * span_m
    g_lon, g_lat = to_lonlat(*np.meshgrid(e0 + t, n1 - t))
    rgb = np.stack([sample_bilinear(rgb[..., c], bounds, g_lon, g_lat) for c in range(3)], axis=-1)
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")
    img = img.resize((OUT_PX, OUT_PX), Image.BILINEAR)
    img.save(OUT_PNG)

    meta = {
        "center_x_cm": round(center_east_m * 100.0, 2),
        "center_y_cm": round(-center_north_m * 100.0, 2),
        "span_cm": round(span_m * 100.0, 2),
    }
    OUT_META.write_text(json.dumps(meta))
    print(f"wrote {OUT_PNG} ({OUT_PX}x{OUT_PX}) and {OUT_META}: {meta}")


if __name__ == "__main__":
    main()
