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
import math
from pathlib import Path

import numpy as np
from PIL import Image

from classify_biomes import classify_rgb, fetch_imagery_mosaic
from test_areas import AREA_NAME, BBOX_SNWE

M_PER_DEG_LAT = 111132.0
MARGIN_M = 2000.0       # same buffer classify_biomes.py fetches beyond the road bbox
OUT_PX = 768             # square output resolution

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
    lon = np.concatenate([np.array(seg["lon"]) for seg in data["segments"]])
    lat = np.concatenate([np.array(seg["lat"]) for seg in data["segments"]])

    buf_lat = MARGIN_M / M_PER_DEG_LAT
    buf_lon = MARGIN_M / (M_PER_DEG_LAT * math.cos(math.radians(float(lat.mean()))))
    west, east = float(lon.min() - buf_lon), float(lon.max() + buf_lon)
    south, north = float(lat.min() - buf_lat), float(lat.max() + buf_lat)

    # Real bug found 2026-07-28 (Taylor, live: the road network looked
    # "shrunk toward the center" relative to the map's coastline art): on a
    # non-square bbox (Oahu's road network is ~67 km E-W but only ~53 km
    # N-S), the OLD code below fetched this exact rectangle then cropped the
    # WIDER axis down to match the narrower one, silently discarding the
    # real east/west coastline pixels. DrawStageMap (ODRaceHUD.cpp) fits the
    # road-network panel to the road's own (uncropped, full-width) bounding
    # box, so the panel ends up wider than the texture's actual coverage -
    # WorldToUV clamps out-of-range UVs to the nearest edge texel, which
    # repeats whatever (still-inland) color sat at the crop boundary rather
    # than showing a coastline there. Net effect: the background paints
    # land all the way to the panel edge on the cropped axis while the real,
    # correctly-scaled road polyline stops short at the true coast - reading
    # as "the roads are inset" when actually the BACKGROUND was cropped too
    # small. Fixed by expanding the FETCH bbox to already be square in real
    # meters (before fetching, not after) - the narrower axis (here, N-S)
    # gets padded outward, not the wider one cropped inward, so no real
    # coastline data is ever discarded. mercantile tile-alignment still
    # rounds the actual fetched extent slightly, so the crop-to-square step
    # below is kept as a small residual correction, not the primary mechanism.
    lat0 = float(lat.mean())
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    width_m = (east - west) * m_per_deg_lon
    height_m = (north - south) * M_PER_DEG_LAT
    target_span_m = max(width_m, height_m)
    if height_m < target_span_m:
        pad_deg = (target_span_m - height_m) / 2.0 / M_PER_DEG_LAT
        south, north = south - pad_deg, north + pad_deg
    if width_m < target_span_m:
        pad_deg = (target_span_m - width_m) / 2.0 / m_per_deg_lon
        west, east = west - pad_deg, east + pad_deg

    mosaic, bounds = fetch_imagery_mosaic(west, south, east, north)
    w_forest, w_grass, w_rock, w_water = classify_rgb(mosaic)

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
    img = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8), "RGB")

    # crop the fetched mosaic (tile-aligned, NOT square in meters - longitude
    # degrees are narrower than latitude degrees away from the equator) down
    # to a square in real-world meters, centered on the same bbox, so a
    # single span_cm value describes both axes for the engine side - no
    # aspect-ratio bookkeeping needed in ODRaceHUD's UV math.
    w, s, e, n = bounds
    lat0 = float(lat.mean())
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    width_m = (e - w) * m_per_deg_lon
    height_m = (n - s) * M_PER_DEG_LAT
    span_m = min(width_m, height_m)   # inscribe, don't stretch - crop to a square that's fully covered by data
    crop_frac_x = span_m / width_m
    crop_frac_y = span_m / height_m
    px_w, px_h = img.size
    cx0 = int(px_w * (1 - crop_frac_x) / 2)
    cy0 = int(px_h * (1 - crop_frac_y) / 2)
    img = img.crop((cx0, cy0, px_w - cx0, px_h - cy0))
    img = img.resize((OUT_PX, OUT_PX), Image.BILINEAR)
    img.save(OUT_PNG)

    # center of that square crop, in the same local frame build_road_stations.py
    # anchors everything to (bbox center of test_areas.BBOX_SNWE)
    lat0_area = (BBOX_SNWE[0] + BBOX_SNWE[2]) / 2
    lon0_area = (BBOX_SNWE[1] + BBOX_SNWE[3]) / 2
    center_lon = (w + e) / 2
    center_lat = (s + n) / 2
    center_east_m = (center_lon - lon0_area) * m_per_deg_lon
    center_north_m = (center_lat - lat0_area) * M_PER_DEG_LAT

    meta = {
        "center_x_cm": round(center_east_m * 100.0, 2),
        "center_y_cm": round(-center_north_m * 100.0, 2),
        "span_cm": round(span_m * 100.0, 2),
    }
    OUT_META.write_text(json.dumps(meta))
    print(f"wrote {OUT_PNG} ({OUT_PX}x{OUT_PX}) and {OUT_META}: {meta}")


if __name__ == "__main__":
    main()
