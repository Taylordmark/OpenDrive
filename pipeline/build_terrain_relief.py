"""Terrain realism gap 4A: per-area DEM relief texture for the Realistic
terrain material (M_GroundStylized_real DemTex, unreal/apply_terrain_real_materials.py DEM_*).

The far terrain tiles are RTIN-simplified to 5 m error (build_terrain_mesh_tiles
MAX_ERROR_FAR_M), which flattens every gully shallower than that, and the
material had no idea where gullies and ridges are. This bakes, on the same
extent and 20 m texel as SatTex (build_sat_albedo.py):
  R, G = high-passed slope dz/dx, dz/dy (UE axes: x = east, y = south), as
         0.5 + slope / (2 GRAD_RANGE) - the relief the far mesh lost; the
         material bends the vertex normal by it with distance.
  B    = topographic position, 0.5 + 0.5 tanh(TPI / TPI_RANGE_M), TPI = the
         neighbourhood's mean height (gaussian TPI_SIGMA_M) minus the point's:
         > 0.5 gully / hollow, < 0.5 ridge / spur. Drives dirt and rock into
         gullies and grass onto ridges.
  A    = confidence: 1 from z15 tiles, Z_FALLBACK_CONF from the z12 fill, 0 on
         the 1-texel border (the clamped sampler then reads "no data").
All four stored as linear values, sRGB-encoded (BindAreaTexture imports SRGB).

Source: Terrarium tiles (AWS Open Data, Mapzen; USGS 3DEP in the US - public
domain), read ONLY from the local cache data/cache/terrarium (no downloads;
the terrain bake already fetched z15 over the tiles). z15 pixels (~3.5 m) are
box-averaged into the 20 m texels; texels no z15 tile reached take z12.

Writes Content/OpenDrive/Masks/dem_<area>_test.png + .json (BindAreaTexture
contract: x_min_cm, y_min_cm, size_x_cm, size_y_cm, attribution).
    OD_AREA=green_valley python -I pipeline/build_terrain_relief.py [--preview DIR]
"""
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parent))   # -I drops the script dir
import build_sat_albedo as sat  # noqa: E402
from bake_job import bake_pass, job_rect  # noqa: E402
from terrain_common import terrarium_to_elevation  # noqa: E402
from test_areas import AREA_NAME, to_local, to_lonlat  # noqa: E402

Z_FINE, Z_FALLBACK = 15, 12
Z_FALLBACK_CONF = 0.5
HP_SIGMA_M = 60.0       # slope detail = slope - slope blurred at this scale
GRAD_RANGE = 0.5        # |slope| encoded before clipping (0.5 = 27 deg of detail)
TPI_SIGMA_M = 60.0      # neighbourhood for the topographic position
TPI_RANGE_M = 8.0       # TPI (m) that reads as ~0.88 / 0.12 in B (4 m saturated hill country)
ROOT = Path(__file__).resolve().parent.parent


def main_or_here(rel):
    """ROOT/rel, or the main checkout's (a worktree has no cache or Masks)."""
    for base in (ROOT, Path(subprocess.check_output(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=ROOT, text=True).strip()).parent):
        if (base / rel).exists():
            return base / rel
    raise SystemExit(f"no {rel} here or in the main checkout")


TEXEL_M = sat.TEXEL_M
MASKS = sat.MASKS = main_or_here("OpenDriveUE/Content/OpenDrive/Masks")


def tile_px_lonlat(z, tx, ty):
    """lon/lat of a 256x256 tile's pixel centres (web mercator)."""
    n = 256 * 2 ** z
    px = (tx * 256 + np.arange(256) + 0.5) / n
    py = (ty * 256 + np.arange(256) + 0.5) / n
    lon = px * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * py))))
    return np.meshgrid(lon, lat)


def lonlat_tile_range(lon, lat, z):
    n = 2 ** z
    x = (np.asarray(lon) + 180.0) / 360.0 * n
    lr = np.radians(np.asarray(lat))
    y = (1.0 - np.log(np.tan(lr) + 1.0 / np.cos(lr)) / math.pi) / 2.0 * n
    return int(x.min()), int(x.max()), int(y.min()), int(y.max())


def bin_tiles(z, root, x0, y0, t, W, H, lon, lat):
    """Box-average every cached zoom-z tile pixel into the texel grid.
    Returns (sum, count) arrays (H, W)."""
    acc, cnt = np.zeros(H * W), np.zeros(H * W)
    xa, xb, ya, yb = lonlat_tile_range(lon, lat, z)
    used = 0
    for tx in range(xa, xb + 1):
        for ty in range(ya, yb + 1):
            p = root / str(z) / str(tx) / f"{ty}.png"
            if not p.exists():
                continue
            lo, la = tile_px_lonlat(z, tx, ty)
            e, nn = to_local(lo, la)
            c = np.floor((e * 100.0 - x0) / t).astype(np.int64)
            r = np.floor((-nn * 100.0 - y0) / t).astype(np.int64)   # UE y = -north
            ok = (c >= 0) & (c < W) & (r >= 0) & (r < H)
            if not ok.any():
                continue
            elev = terrarium_to_elevation(Image.open(p).convert("RGB"))
            idx = r[ok] * W + c[ok]
            acc += np.bincount(idx, elev[ok], H * W)
            cnt += np.bincount(idx, minlength=H * W)
            used += 1
    return acc.reshape(H, W), cnt.reshape(H, W), used


def relief(E, conf, texel_m):
    """(H, W) elevation (m) -> RGBA float (linear) per the module docstring,
    minus the border zeroing (that's the whole texture's edge, see main)."""
    gy, gx = np.gradient(E, texel_m)            # axis 0 = UE +y (south), axis 1 = UE +x (east)
    s = HP_SIGMA_M / texel_m
    gx_hp, gy_hp = gx - ndimage.gaussian_filter(gx, s), gy - ndimage.gaussian_filter(gy, s)
    tpi = ndimage.gaussian_filter(E, TPI_SIGMA_M / texel_m) - E
    enc = lambda g: np.clip(0.5 + g / (2.0 * GRAD_RANGE), 0.0, 1.0)
    return np.dstack([enc(gx_hp), enc(gy_hp), 0.5 + 0.5 * np.tanh(tpi / TPI_RANGE_M), conf.astype(np.float64)])


def to_srgb8(lin):
    s = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return (s * 255.0 + 0.5).clip(0, 255).astype(np.uint8)


def bake(root, x0, y0, W, H):
    """RGBA float relief of the W x H texel window whose corner is UE (x0, y0) cm."""
    t = TEXEL_M * 100.0
    X, Y = np.meshgrid(x0 + (np.arange(W) + 0.5) * t, y0 + (np.arange(H) + 0.5) * t)
    lon, lat = to_lonlat(X / 100.0, -Y / 100.0)
    acc, cnt, n15 = bin_tiles(Z_FINE, root, x0, y0, t, W, H, lon, lat)
    fine = cnt > 0
    E = np.where(fine, acc / np.maximum(cnt, 1), np.nan)
    conf = fine.astype(np.float64)
    if not fine.all():
        acc, cnt, n12 = bin_tiles(Z_FALLBACK, root, x0, y0, t, W, H, lon, lat)
        print(f"  z15: {n15} tiles, {fine.mean() * 100:.1f}% of texels; z12 fill: {n12} tiles")
        # z12 pixels (~28 m) leave holes at 20 m: fill both from the nearest sample
        have = fine | (cnt > 0)
        E = np.where(fine, E, np.where(cnt > 0, acc / np.maximum(cnt, 1), np.nan))
        conf = np.where(fine, 1.0, np.where(cnt > 0, Z_FALLBACK_CONF, 0.0))
        if not have.any():
            raise SystemExit("no cached Terrarium tiles over this area")
        _, (ri, ci) = ndimage.distance_transform_edt(~have, return_indices=True)
        far = ndimage.distance_transform_edt(~have) > 2   # beyond 2 texels = no real data
        E, conf = E[ri, ci], np.where(far, 0.0, np.where(have, conf, Z_FALLBACK_CONF))
    else:
        print(f"  z15: {n15} tiles, 100% of texels")
    return relief(E, conf, TEXEL_M)


# Per-job bake (pass 2): a texel reads the DEM up to 4 gaussian sigmas (+1
# for the gradient) away, so a job recomputes its rect grown by JOB_PAD
# texels from a window grown by 2 x JOB_PAD, and the last job near a texel
# leaves it exactly as a whole-area bake would.
JOB_PAD = math.ceil(4 * max(HP_SIGMA_M, TPI_SIGMA_M) / TEXEL_M) + 1


def main():
    t0 = time.time()
    x0, y0, x1, y1 = sat.terrain_extent_cm()
    t = TEXEL_M * 100.0
    W, H = int(round((x1 - x0) / t)), int(round((y1 - y0) / t))
    root = main_or_here("data/cache/terrarium")
    out = MASKS / f"dem_{AREA_NAME}_test.png"
    meta_path = MASKS / f"dem_{AREA_NAME}_test.json"
    meta = {"x_min_cm": x0, "y_min_cm": y0, "size_x_cm": x1 - x0, "size_y_cm": y1 - y0,
            "texel_m": TEXEL_M, "width": W, "height": H,
            "grad_range": GRAD_RANGE, "tpi_range_m": TPI_RANGE_M,
            "attribution": "Terrain: Mapzen Terrarium (AWS Open Data), incl. USGS 3DEP (public domain)"}
    print(f"{AREA_NAME}: {W}x{H} texels @ {TEXEL_M:.0f} m from {root}")

    # Drawn-area bake (2026-10-22): pass 1 lays out the planned extent with
    # "no data" (A = 0) and keeps whatever an earlier bake left on the same
    # grid (a redrawn outline keeps its baked cells); each pass-2 job then
    # fills its own rect. No pass = the whole area at once, as before.
    if bake_pass() is None:
        rgba = bake(root, x0, y0, W, H)
        img = to_srgb8(rgba)
        b = rgba[1:-1, 1:-1, 2]
        print(f"  gully (B>0.6) {(b > 0.6).mean() * 100:.1f}%, ridge (B<0.4) {(b < 0.4).mean() * 100:.1f}%")
    else:
        img = np.zeros((H, W, 4), np.uint8)
        img[..., :3] = to_srgb8(np.float64(0.5))
        if out.exists() and meta_path.exists():
            old = json.loads(meta_path.read_text())
            ox, oy = round((old["x_min_cm"] - x0) / t), round((old["y_min_cm"] - y0) / t)
            prev = np.asarray(Image.open(out).convert("RGBA"))
            r0, c0 = max(oy, 0), max(ox, 0)
            r1, c1 = min(oy + prev.shape[0], H), min(ox + prev.shape[1], W)
            if r1 > r0 and c1 > c0:
                img[r0:r1, c0:c1] = prev[r0 - oy:r1 - oy, c0 - ox:c1 - ox]
        rect = job_rect()
        if rect is not None:
            # job rect (east/north m) -> texel rows/cols, grown and clipped
            ca, cb = (np.array([rect[0], rect[2]]) * 100.0 - x0) / t
            ra, rb = (np.array([-rect[3], -rect[1]]) * 100.0 - y0) / t
            ca, ra, cb, rb = math.floor(ca), math.floor(ra), math.ceil(cb), math.ceil(rb)
            box = lambda pad: (max(ra - pad, 0), max(ca - pad, 0), min(rb + pad, H), min(cb + pad, W))
            wr0, wc0, wr1, wc1 = box(JOB_PAD)
            br0, bc0, br1, bc1 = box(2 * JOB_PAD)
            if wr1 > wr0 and wc1 > wc0:
                win = bake(root, x0 + bc0 * t, y0 + br0 * t, bc1 - bc0, br1 - br0)
                img[wr0:wr1, wc0:wc1] = to_srgb8(win[wr0 - br0:wr1 - br0, wc0 - bc0:wc1 - bc0])
            print(f"  job rect {rect}: texels rows {wr0}-{wr1}, cols {wc0}-{wc1}")
    img[0, :, 3] = img[-1, :, 3] = img[:, 0, 3] = img[:, -1, 3] = 0   # clamped sampler reads "no data"

    Image.fromarray(img, "RGBA").save(out, optimize=True)
    meta_path.write_text(json.dumps(meta, indent=1))
    print(f"  wrote {out.name} {out.stat().st_size / 1e6:.2f} MB; total {time.time() - t0:.0f} s")
    if "--preview" in sys.argv:
        d = Path(sys.argv[sys.argv.index("--preview") + 1])
        d.mkdir(parents=True, exist_ok=True)
        Image.fromarray(img[..., 2]).save(d / f"dem_{AREA_NAME}_tpi.png")   # sRGB-coded B


if __name__ == "__main__":
    main()
