"""Shaded-relief backdrop for the menu's Area screen map panel (2026-10-14):
multi-directional hillshade tinted by land cover, OSM lakes + ocean deep
blue, darkened so the gold roads / start dots read on top.

Reuses classify_biomes.py's cached slippy mosaics (Terrarium DEM + USGS
imagery -> classify_rgb weights) and build_map_texture.py's local-grid
resample (every pixel through to_lonlat, so LCC meridian convergence lines
up). No imagery -> plain grey hillshade.

Usage:  python pipeline/bake_area_relief.py           (active area, OD_AREA)
        python pipeline/bake_area_relief.py --all     (every menu_spots.json area)

Writes  OpenDriveUE/Content/OpenDrive/Masks/relief_<area>.png  (8-bit RGB,
        long side MAX_PX, row 0 = north, col 0 = west)
        OpenDriveUE/Content/OpenDrive/Masks/relief_<area>.json
            {"min_cm": [x, y], "max_cm": [x, y]} - area-local UE cm, the
            frame of AODMainMenu::AreaMap (centerline_map ODM1: x = east*100,
            y = -north*100), so the top row is min_cm[1].
             "corners_ll": [[lat, lng] x4] NW, NE, SE, SW - the image's outer
             corners (2026-10-14, AODMenuGlobe::SetDrape),
             "proj": "bilinear" - lat/lng of an area-local cm point = bilinear
             blend of corners_ll at u = (x - min_x) / span_x (0 = west),
             v = (y - min_y) / span_y (0 = north); "proj_err_m" is that
             blend's error at the rect centre vs the true LCC inverse
             (sub-metre-ish at these few-km-to-100-km sizes).
Extent = the area bbox's local rect, grown to cover every centerline_map
point (OSM ways run a little past the bbox).
"""

import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

MASKS = Path(__file__).resolve().parent.parent / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
MAX_PX = 1024
SUN_AZ_DEG = (225.0, 270.0, 315.0, 360.0)   # multi-directional, NW-weighted below
SUN_W = (0.15, 0.3, 0.35, 0.2)
SUN_ALT_DEG = 45.0
DARKEN = 0.62                                # whole map, so gold reads on top
# land cover tints (lit, before DARKEN) - order: forest, grass/scrub, rock,
# bare/urban, snow (classify_rgb's weights + a bright-and-grey snow term)
COVER_RGB = np.array([[58, 86, 60], [112, 112, 72], [118, 116, 112],
                      [128, 116, 104], [228, 232, 236]], dtype=np.float32)
PLAIN_RGB = np.array([104, 106, 110], dtype=np.float32)
WATER_RGB = np.array([16, 34, 58], dtype=np.float32)   # not shaded or darkened


def hillshade(dem, px_m, z_factor):
    """dem rows north->south -> [0,1] weighted multi-azimuth shade."""
    gy, gx = np.gradient(dem * z_factor, px_m)
    de, dn = gx, -gy                       # row index grows southward
    norm = np.sqrt(de * de + dn * dn + 1.0)
    alt = np.radians(SUN_ALT_DEG)
    out = np.zeros_like(dem)
    for az, w in zip(np.radians(SUN_AZ_DEG), SUN_W):
        sun = (np.sin(az) * np.cos(alt), np.cos(az) * np.cos(alt), np.sin(alt))
        out += w * np.clip((-de * sun[0] - dn * sun[1] + sun[2]) / norm, 0.0, 1.0)
    return out


def render(dem, px_m, water, cover=None):
    """dem (H,W) m, water (H,W) bool, cover None or (H,W,5) weights -> uint8 RGB."""
    shade = hillshade(dem, px_m, z_factor=2.0 if px_m > 20.0 else 1.3)
    lit = 0.25 + 0.75 * shade / np.sin(np.radians(SUN_ALT_DEG))   # level ground = 1.0
    if cover is None:
        base = np.broadcast_to(PLAIN_RGB, dem.shape + (3,))
    else:
        base = cover @ COVER_RGB / np.clip(cover.sum(-1, keepdims=True), 1e-3, None)
    rgb = base * (lit * DARKEN)[..., None]
    rgb[water] = WATER_RGB
    return np.clip(rgb, 0, 255).astype(np.uint8)


def bounds_cm(e0, n0, e1, n1):
    """local east/north metre rect -> (min_cm, max_cm) in UE x = e*100, y = -n*100."""
    return [e0 * 100.0, -n1 * 100.0], [e1 * 100.0, -n0 * 100.0]


def corners_ll(e0, n0, e1, n1, to_lonlat):
    """local rect -> ([[lat, lng] NW, NE, SE, SW], bilinear error at the centre in m)."""
    lon, lat = to_lonlat(np.array([e0, e1, e1, e0, (e0 + e1) / 2]), np.array([n1, n1, n0, n0, (n0 + n1) / 2]))
    ll = [[round(float(lat[i]), 7), round(float(lon[i]), 7)] for i in range(4)]
    blat, blon = lat[:4].mean(), lon[:4].mean()   # bilinear at u = v = 0.5
    err = float(np.hypot((blat - lat[4]) * 111320.0, (blon - lon[4]) * 111320.0 * np.cos(np.radians(lat[4]))))
    return ll, err


def grid_size(span_e, span_n):
    k = MAX_PX / max(span_e, span_n)
    return max(1, round(span_e * k)), max(1, round(span_n * k))


def map_extent(area):
    """centerline_map_<area>_test.bin (ODM1) point extent in local metres, or None."""
    p = MASKS / f"centerline_map_{area}_test.bin"
    if not p.is_file():
        return None
    b = p.read_bytes()
    n = struct.unpack_from("<I", b, 4)[0]
    xy = np.frombuffer(b, "<f4", n * 2, 8).reshape(-1, 2)
    xy = xy[np.isfinite(xy).all(1)].astype(np.float64)
    return (xy[:, 0].min() / 100, -xy[:, 1].max() / 100, xy[:, 0].max() / 100, -xy[:, 1].min() / 100) if len(xy) else None


def bake():
    from classify_biomes import IMG_ZOOM, _fetch_dem_tile, classify_rgb, fetch_mosaic, sample_bilinear
    from test_areas import AREA_NAME, BBOX_SNWE, local_rect, lonlat_bbox, to_local, to_lonlat

    t0 = time.time()
    e0, n0, e1, n1 = local_rect(BBOX_SNWE)
    if (m := map_extent(AREA_NAME)) is not None:
        e0, n0, e1, n1 = (float(v) for v in (min(e0, m[0]), min(n0, m[1]), max(e1, m[2]), max(n1, m[3])))
    w, h = grid_size(e1 - e0, n1 - n0)
    px_m = (e1 - e0) / w
    ge, gn = np.meshgrid(e0 + (np.arange(w) + 0.5) * px_m, n1 - (np.arange(h) + 0.5) * px_m)
    lon, lat = to_lonlat(ge, gn)
    s_, w_, n_, e_ = lonlat_bbox(e0, n0, e1, n1)

    # z12 (~30 m) where it out-resolves the output, z15 (~4 m) for small areas - both cached
    mosaic, mb = fetch_mosaic(w_, s_, e_, n_, zoom=12 if px_m >= 20.0 else 15, fetch_tile=_fetch_dem_tile)
    dem = sample_bilinear(mosaic, mb, lon, lat)

    water = dem <= 0.0   # ocean/bay: Terrarium carries bathymetry
    lakes = RAW / f"{AREA_NAME}_test" / "osm_water.json"
    if lakes.is_file():
        mask = Image.new("L", (w, h))
        draw = ImageDraw.Draw(mask)
        for poly in json.loads(lakes.read_text()):
            ll = np.asarray(poly["nodes"], dtype=np.float64)   # [lat, lon]
            pe, pn = to_local(ll[:, 1], ll[:, 0])
            # ponytail: outer rings only - a lake island paints as water
            draw.polygon(list(zip((pe - e0) / px_m, (n1 - pn) / px_m)), fill=255)
        water |= np.asarray(mask) > 0

    cover = None
    try:
        img, ib = fetch_mosaic(w_, s_, e_, n_, zoom=IMG_ZOOM)
        f = img.astype(np.float32) / 255.0
        val, sat = f.max(-1), f.max(-1) - f.min(-1)
        snow = np.clip((val - 0.75) * 6, 0, 1) * np.clip((0.12 - sat) * 10, 0, 1)
        wf, wg, wr, _ww, wb = classify_rgb(img)
        wr = wr * (1 - snow)
        cover = np.stack([sample_bilinear(c, ib, lon, lat) for c in (wf, wg, wr, wb, snow)], -1)
        # reefs / tidal flats: Terrarium reads a few m above sea level offshore
        # (Oahu), so blue-dominant imagery near sea level is water too
        blue = sample_bilinear(f[..., 2] - np.maximum(f[..., 0], f[..., 1]), ib, lon, lat)
        water |= (dem < 10.0) & (blue > 0.03)
    except Exception as ex:   # no imagery (offline, uncached) -> plain hillshade
        print(f"  no land cover ({ex!r}) - plain hillshade")

    rgb = render(dem, px_m, water, cover)
    Image.fromarray(rgb, "RGB").save(MASKS / f"relief_{AREA_NAME}.png")
    mn, mx = bounds_cm(e0, n0, e1, n1)
    ll, err = corners_ll(e0, n0, e1, n1, to_lonlat)
    (MASKS / f"relief_{AREA_NAME}.json").write_text(json.dumps(
        {"min_cm": [round(float(v), 1) for v in mn], "max_cm": [round(float(v), 1) for v in mx],
         "corners_ll": ll, "proj": "bilinear", "proj_err_m": round(err, 2)}))
    print(f"wrote relief_{AREA_NAME}.png {w}x{h} ({px_m:.1f} m/px, "
          f"{'land cover' if cover is not None else 'plain'}, water {water.mean():.1%}) "
          f"min_cm={mn} max_cm={mx} bilinear err {err:.2f} m in {time.time() - t0:.1f}s")


def main():
    if "--all" not in sys.argv:
        bake()
        return
    # test_areas binds the frame at import, so one process per area (OD_AREA)
    failed = 0
    for area in json.loads((MASKS / "menu_spots.json").read_text()):
        r = subprocess.run([sys.executable, __file__], env={**os.environ, "OD_AREA": area})
        if r.returncode:
            print(f"relief {area}: FAILED ({r.returncode})")
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
