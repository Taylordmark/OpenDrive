"""PLAN-VISUALS Track L: per-area ground albedo from Copernicus Sentinel-2 L2A,
for the Realistic terrain material's macro colour (M_GroundStylized_real
SatTex, unreal/apply_terrain_real_materials.py SAT_*).

Source: raw Sentinel-2 L2A scenes (Copernicus data licence - free commercial
use, attribution "Contains modified Copernicus Sentinel data <years>"), read
as cloud-optimized GeoTIFFs from Element84 Earth Search on AWS (no login).
NOT the EOX "Sentinel-2 cloudless" mosaics (2018+ editions are CC BY-NC-SA).
content_src/realistic/satellite/manifest.json records it for the licence gate.

Composite: every low-cloud summer scene (Jun - mid Sep, SUMMERS) over the
area's baked terrain extent, B04/B03/B02 read at their 20 m overview (= the
texel size, so no further downsample), masked by SCL to vegetation / bare /
water / unclassified, per-pixel per-band median. Reflectance = DN * scale +
offset from each item's raster:bands (offset skipped where Earth Search
already applied it) - a FIXED scale, no per-scene levels, so
neighbouring areas and tiles agree. Then:
  - road footprints (roadstations_<area>.bin, + ROAD_PAD_M for mixed texels)
    are inpainted from the surrounding ground, so the real road doesn't get a
    dark smear beside it;
  - asphalt-grey texels left (roofs, parking, unmapped roads) lose confidence
    (alpha), so the material falls back to the biome look there;
  - alpha also fades to 0 over EDGE_FADE_PX at the extent's border (a 1-texel
    0 border, so the clamped sampler returns "no data" outside).

Writes Content/OpenDrive/Masks/sat_<area>_test.png (RGBA8: RGB = linear
albedo, sRGB-encoded; A = confidence) + sat_<area>_test.json (extent in UE
cm), read by AODTerrainManager::LoadSatAlbedo (Realistic only). A missing
file = the old look.

Downloads (compressed COG tiles, by URL + tile index) are cached in
data/cache/sentinel2/ (fetch_cache.cached_bytes). Run isolated:
    OD_AREA=deals_gap python -I pipeline/build_sat_albedo.py [--preview DIR]
"""
import json
import math
import struct
import sys
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir
from bake_job import outline_tiles  # noqa: E402
from fetch_cache import cached_bytes  # noqa: E402
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM, to_lonlat  # noqa: E402

STAC = "https://earth-search.aws.element84.com/v1/search"
SUMMERS = (2023, 2024, 2025)
SEASON = ("06-01", "09-15")
MAX_CLOUD = 40.0          # scene-level %, SCL masks the rest per pixel
MAX_SCENES_PER_TILE = 10  # per MGRS tile, lowest cloud first
TEXEL_M = 20.0            # = the bands' first COG overview
SCL_KEEP = (4, 5, 6, 7)   # vegetation, bare, water, unclassified (no cloud/shadow/snow/dark)
ROAD_PAD_M = 12.0         # inpaint radius beyond the pavement half-width
EDGE_FADE_PX = 10
ASPHALT_CHROMA = 0.10     # (max-min)/mean below this ...
ASPHALT_LUMA = (0.03, 0.20)   # ... in this luminance band = grey built surface
ASPHALT_ALPHA = 0.35      # confidence kept on such texels
LUMA = np.array([0.2126, 0.7152, 0.0722])

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
HEADERS = {"User-Agent": "OpenDrive-pipeline/0.1 (sentinel-2 albedo bake)"}
SESSION = requests.Session()


# ---------------------------------------------------------------- UTM
def lonlat_to_utm(lon, lat, zone, south):
    """WGS84 Transverse Mercator, Krueger series to n^3 (mm-level within a
    zone - Karney 2011 eq. 7-9). No pyproj on purpose (not installed)."""
    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    n = f / (2 - f)
    A = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64)
    al = (n / 2 - 2 * n * n / 3 + 5 * n ** 3 / 16, 13 * n * n / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240)
    phi, lam = np.radians(lat), np.radians(lon - (zone * 6 - 183))
    c = 2 * math.sqrt(n) / (1 + n)
    t = np.sinh(np.arctanh(np.sin(phi)) - c * np.arctanh(c * np.sin(phi)))
    xi = np.arctan2(t, np.cos(lam))
    eta = np.arctanh(np.sin(lam) / np.sqrt(1 + t * t))
    e = eta + sum(al[j] * np.cos(2 * (j + 1) * xi) * np.sinh(2 * (j + 1) * eta) for j in range(3))
    nn = xi + sum(al[j] * np.sin(2 * (j + 1) * xi) * np.cosh(2 * (j + 1) * eta) for j in range(3))
    return 500000.0 + k0 * A * e, (10000000.0 if south else 0.0) + k0 * A * nn


# ---------------------------------------------------------------- COG
def _get(url, start, end):
    for attempt in range(5):
        try:
            r = SESSION.get(url, headers=dict(HEADERS, Range=f"bytes={start}-{end}"), timeout=60)
            r.raise_for_status()
            return r.content
        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(2.0 * (attempt + 1))


class Cog:
    """Minimal tiled-GeoTIFF reader: IFD chain, DEFLATE + horizontal
    predictor, uint8/uint16 single band - what Earth Search's S2 COGs use
    (anything else raises)."""

    def __init__(self, url):
        self.url = url
        key = url.split("sentinel-s2-l2a-cogs/")[-1]
        self.key = key
        head = cached_bytes("sentinel2", key + ".head", lambda: _get(url, 0, 262143))
        if head[:2] != b"II":
            raise ValueError(f"{url}: not little-endian TIFF")
        self.ifds = []
        off = struct.unpack("<I", head[4:8])[0]
        size = {3: 2, 4: 4, 16: 8}
        while off:
            n = struct.unpack("<H", head[off:off + 2])[0]
            tags = {}
            for i in range(n):
                tag, typ, cnt, raw = struct.unpack("<HHI4s", head[off + 2 + 12 * i: off + 14 + 12 * i])
                sz = size.get(typ, 1) * cnt
                data = raw if sz <= 4 else head[struct.unpack("<I", raw)[0]:][:sz]
                if len(data) < sz:
                    raise ValueError(f"{url}: header beyond first 256 KB")
                fmt = {3: "H", 4: "I", 16: "Q"}.get(typ, "B")
                tags[tag] = struct.unpack(f"<{cnt}{fmt}", data[:sz])
            self.ifds.append(tags)
            off = struct.unpack("<I", head[off + 2 + 12 * n: off + 6 + 12 * n])[0]
        for t in self.ifds:
            if t[259][0] != 8 or t.get(277, (1,))[0] != 1:
                raise ValueError(f"{url}: unsupported compression/bands")

    def read(self, level, r0, r1, c0, c1):
        """Rows r0:r1, cols c0:c1 of overview `level` (0 = full res)."""
        t = self.ifds[level]
        W, H, tw, th = t[256][0], t[257][0], t[322][0], t[323][0]
        dt = np.uint16 if t[258][0] == 16 else np.uint8
        pred = t.get(317, (1,))[0]
        ntx = (W + tw - 1) // tw
        out = np.zeros((r1 - r0, c1 - c0), dt)
        jobs = [(ty, tx) for ty in range(r0 // th, (r1 - 1) // th + 1) for tx in range(c0 // tw, (c1 - 1) // tw + 1)]

        def fetch(job):
            ty, tx = job
            i = ty * ntx + tx
            o, n = t[324][i], t[325][i]
            if n == 0:
                return job, None
            raw = cached_bytes("sentinel2", f"{self.key}.L{level}.{i}", lambda: _get(self.url, o, o + n - 1))
            a = np.frombuffer(zlib.decompress(raw), dt).reshape(th, tw)
            return job, (np.cumsum(a, axis=1, dtype=dt) if pred == 2 else a)

        with ThreadPoolExecutor(8) as ex:
            for (ty, tx), a in ex.map(fetch, jobs):
                if a is None:
                    continue
                ys, xs = ty * th, tx * tw
                rr0, rr1 = max(r0, ys), min(r1, ys + th)
                cc0, cc1 = max(c0, xs), min(c1, xs + tw)
                out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = a[rr0 - ys:rr1 - ys, cc0 - xs:cc1 - xs]
        return out


# ---------------------------------------------------------------- extent
def terrain_extent_cm():
    """UE-cm rect (x0, y0, x1, y1) of the area's baked near/far terrain tiles
    (a drawn area's planned ones: pass 1 runs before any is baked)."""
    keys = outline_tiles(TERRAIN_TILE_SIZE_CM) or [
        tuple(map(int, p.stem.rsplit("_", 2)[1:])) for p in MASKS.glob(f"terrain_{AREA_NAME}_test_tile_*.bin")]
    if not keys:
        raise SystemExit(f"no terrain tiles for {AREA_NAME} in {MASKS}")
    tx, ty = np.array(keys).T
    s = TERRAIN_TILE_SIZE_CM
    return tx.min() * s, ty.min() * s, (tx.max() + 1) * s, (ty.max() + 1) * s


def stac_items(snwe):
    s, w, n, e = snwe
    items = []
    for year in SUMMERS:
        body = {"collections": ["sentinel-2-l2a"], "bbox": [w, s, e, n], "limit": 200,
                "datetime": f"{year}-{SEASON[0]}T00:00:00Z/{year}-{SEASON[1]}T23:59:59Z",
                "query": {"eo:cloud_cover": {"lt": MAX_CLOUD}}}
        r = SESSION.post(STAC, json=body, headers=HEADERS, timeout=120)
        r.raise_for_status()
        items += r.json()["features"]
    by_tile = {}
    for it in items:
        p = it["properties"]
        by_tile.setdefault((p["mgrs:utm_zone"], p["mgrs:latitude_band"], p["mgrs:grid_square"]), []).append(it)
    picked = []
    for tile, its in sorted(by_tile.items()):
        its.sort(key=lambda it: it["properties"]["eo:cloud_cover"])
        picked += its[:MAX_SCENES_PER_TILE]
    return picked


# ---------------------------------------------------------------- roads
def road_mask(x_cm, y_cm, shape):
    """Texels within half-width + ROAD_PAD_M of a road station (ODS1/ODS2
    sidecar, same layout as experimental_junction_pads.read_station_sidecar)."""
    path = MASKS / f"roadstations_{AREA_NAME}.bin"
    if not path.is_file():
        print(f"  no {path.name} - road footprints not inpainted")
        return np.zeros(shape, bool)
    b = path.read_bytes()
    stride = 4 if b[:4] == b"ODS2" else 3
    gl = struct.unpack("<I", b[4:8])[0]
    n = struct.unpack("<I", b[8 + gl:12 + gl])[0]
    f = np.frombuffer(b[12 + gl:12 + gl + n * stride * 4], "<f4").astype(np.float64)
    sx, sy = f[0::stride], f[1::stride]
    hw = f[3::4] if stride == 4 else np.full(n, 450.0)
    t = TEXEL_M * 100.0
    # densify consecutive stations closer than 3 texels (same road), so a
    # sparse station run still rasterizes as a continuous line
    d = np.hypot(np.diff(sx), np.diff(sy))
    seg = np.nonzero(d < 3 * t)[0]
    k = 4
    fr = (np.arange(1, k) / k)[None, :]
    sx = np.concatenate([sx, (sx[seg, None] + np.diff(sx)[seg, None] * fr).ravel()])
    sy = np.concatenate([sy, (sy[seg, None] + np.diff(sy)[seg, None] * fr).ravel()])
    hw = np.concatenate([hw, np.repeat(hw[seg], k - 1)])
    c = np.floor((sx - x_cm[0]) / t).astype(int)
    r = np.floor((sy - y_cm[0]) / t).astype(int)
    ok = (c >= 0) & (c < shape[1]) & (r >= 0) & (r < shape[0])
    rad = np.zeros(shape)
    np.maximum.at(rad, (r[ok], c[ok]), hw[ok] / 100.0 + ROAD_PAD_M)
    dist, (ir, ic) = ndimage.distance_transform_edt(rad == 0, return_indices=True)
    return dist * TEXEL_M <= rad[ir, ic]


def fill_from(img, valid, sigma):
    """Normalized-convolution fill of ~valid texels from valid ones; returns
    (filled image, support weight)."""
    w = ndimage.gaussian_filter(valid.astype(np.float32), sigma)
    out = img.copy()
    for ch in range(img.shape[2]):
        num = ndimage.gaussian_filter(np.where(valid, img[..., ch], 0.0).astype(np.float32), sigma)
        out[..., ch] = np.where(valid, img[..., ch], num / np.maximum(w, 1e-6))
    return out, w


def main():
    t0 = time.time()
    preview_dir = Path(sys.argv[sys.argv.index("--preview") + 1]) if "--preview" in sys.argv else None
    x0, y0, x1, y1 = terrain_extent_cm()
    t = TEXEL_M * 100.0
    W, H = int(round((x1 - x0) / t)), int(round((y1 - y0) / t))
    xc = x0 + (np.arange(W) + 0.5) * t                 # texel centres, UE cm
    yc = y0 + (np.arange(H) + 0.5) * t
    X, Y = np.meshgrid(xc, yc)
    lon, lat = to_lonlat(X / 100.0, -Y / 100.0)        # UE: x = east, y = -north
    snwe = (lat.min(), lon.min(), lat.max(), lon.max())
    print(f"{AREA_NAME}: {W}x{H} texels @ {TEXEL_M:.0f} m, lon/lat {snwe}")

    items = stac_items(snwe)
    print(f"  {len(items)} scene(s): " + ", ".join(sorted({i['id'][4:9] for i in items})))
    stacks = [[], [], []]
    years = set()
    for k, it in enumerate(items):
        p = it["properties"]
        epsg = p["proj:epsg"]
        zone, south = epsg % 100, epsg // 100 == 327
        E, N = lonlat_to_utm(lon, lat, zone, south)
        tr = it["assets"]["scl"]["proj:transform"]      # GDAL order: [dx, 0, x0, 0, -dy, y0]
        ex0, ny0 = tr[2], tr[5]
        col = np.floor((E - ex0) / TEXEL_M).astype(np.int64)
        row = np.floor((ny0 - N) / TEXEL_M).astype(np.int64)
        size = it["assets"]["scl"]["proj:shape"][0]
        inside = (col >= 0) & (col < size) & (row >= 0) & (row < size)
        if not inside.any():
            continue
        r0, r1 = row[inside].min(), row[inside].max() + 1
        c0, c1 = col[inside].min(), col[inside].max() + 1
        rr, cc = np.clip(row - r0, 0, r1 - r0 - 1), np.clip(col - c0, 0, c1 - c0 - 1)
        scl = Cog(it["assets"]["scl"]["href"]).read(0, r0, r1, c0, c1)[rr, cc]
        good = inside & np.isin(scl, SCL_KEEP)
        if good.mean() < 0.02:
            continue
        years.add(p["datetime"][:4])
        for b, name in enumerate(("red", "green", "blue")):
            a = it["assets"][name]
            rb = a["raster:bands"][0]
            dn = Cog(a["href"]).read(1, r0, r1, c0, c1)[rr, cc].astype(np.float32)
            # Earth Search already removed the baseline-04.00 +1000 DN offset
            # when this flag is set, yet raster:bands still lists -0.1 (forest
            # red DN ~340 = 0.034) - applying it again clipped dark ground to 0
            off = 0.0 if p.get("earthsearch:boa_offset_applied") else rb.get("offset", 0.0)
            refl = dn * rb.get("scale", 1e-4) + off
            stacks[b].append(np.where(good & (dn > 0), refl, np.nan).astype(np.float16))
        print(f"  [{k + 1}/{len(items)}] {it['id']} cloud {p['eo:cloud_cover']:.0f}% "
              f"usable {good.mean() * 100:.0f}%  ({time.time() - t0:.0f} s)")
    if not stacks[0]:
        raise SystemExit("no usable scenes")

    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)   # all-NaN texels -> NaN, handled below
        comp = np.stack([np.nanmedian(np.stack(s), axis=0) for s in stacks], axis=-1).astype(np.float32)
    obs = np.isfinite(comp[..., 0])
    comp = np.clip(np.nan_to_num(comp), 0.0, 1.0)

    roads = road_mask(xc, yc, (H, W))
    keep = obs & ~roads
    comp, support = fill_from(comp, keep, 3.0)
    for sigma in (8.0, 24.0):                          # wide roads / big holes
        hole = support < 0.05
        if hole.any():
            wider, support2 = fill_from(comp, ~hole, sigma)
            comp = np.where(hole[..., None], wider, comp)
            support = np.where(hole, support2, support)
    alpha = np.clip(ndimage.gaussian_filter(obs.astype(np.float32), 2.0) * 1.2, 0, 1)

    luma = comp @ LUMA
    chroma = (comp.max(-1) - comp.min(-1)) / np.maximum(comp.mean(-1), 1e-3)
    asphalt = (chroma < ASPHALT_CHROMA) & (luma > ASPHALT_LUMA[0]) & (luma < ASPHALT_LUMA[1])
    asphalt = ndimage.gaussian_filter(asphalt.astype(np.float32), 1.0)
    alpha *= 1.0 - (1.0 - ASPHALT_ALPHA) * asphalt
    r = np.minimum.reduce(np.meshgrid(np.arange(W), np.arange(H)) +
                          np.meshgrid(W - 1 - np.arange(W), H - 1 - np.arange(H)))
    alpha *= np.clip(r / EDGE_FADE_PX, 0, 1)            # 0 on the 1-texel border

    srgb = np.where(comp <= 0.0031308, comp * 12.92, 1.055 * np.power(comp, 1 / 2.4) - 0.055)
    rgba = np.dstack([srgb, alpha]) * 255.0 + 0.5
    out = MASKS / f"sat_{AREA_NAME}_test.png"
    Image.fromarray(rgba.clip(0, 255).astype(np.uint8), "RGBA").save(out, optimize=True)
    meta = {"x_min_cm": x0, "y_min_cm": y0, "size_x_cm": x1 - x0, "size_y_cm": y1 - y0,
            "texel_m": TEXEL_M, "width": W, "height": H, "scenes": len(stacks[0]),
            "years": sorted(years),
            "attribution": f"Contains modified Copernicus Sentinel data {min(years)}-{max(years)}"}
    (MASKS / f"sat_{AREA_NAME}_test.json").write_text(json.dumps(meta, indent=1))
    land = obs & (luma > 0.01)
    print(f"  wrote {out.name} {out.stat().st_size / 1e6:.2f} MB; observed {obs.mean() * 100:.1f}%, "
          f"roads {roads.mean() * 100:.1f}%, asphalt {asphalt.mean() * 100:.1f}%, "
          f"land luma median {np.median(luma[land]):.4f}, total {time.time() - t0:.0f} s")
    if preview_dir:
        preview_dir.mkdir(parents=True, exist_ok=True)
        disp = np.clip(srgb / np.percentile(srgb[obs], 99.5), 0, 1) * 255   # stretched for viewing only
        overlay = disp.copy()
        overlay[roads] = overlay[roads] * 0.4 + np.array([255, 0, 0]) * 0.6
        a3 = np.repeat(alpha[..., None] * 255, 3, -1)
        Image.fromarray(np.hstack([disp, overlay, a3]).astype(np.uint8)).save(
            preview_dir / f"sat_{AREA_NAME}_preview.png")


if __name__ == "__main__":
    main()
