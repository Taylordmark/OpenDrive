"""Offline check for PLAN-VISUALS Track L (pipeline/build_sat_albedo.py ->
sat_<area>_test.png/.json -> AODTerrainManager SatTex -> sat_macro() in
unreal/apply_terrain_real_materials.py). Per baked area (default: the three
the other checks use; pass names to override):

1. extent: the .json rect is the baked terrain tiles' rect, texel count
   matches, the 1-texel border has alpha 0 (clamped sampler = no data).
2. albedo range: land RGB within plausible Sentinel-2 surface reflectance,
   median land luminance in a sane band.
3. seams: across every 2 km terrain-tile border the texel step is no larger
   than inside tiles (one texture, so it should be ~1.0), and no row/column
   anywhere carries a step-change line (MGRS granule / UTM zone edges).
4. material mirror (constants parsed from the material script): weight 0
   (SatAmount 0, alpha 0, i.e. the fallback) is exactly the old albedo, the
   multiplier stays within its clamps, sat_macro runs before wetness/snow,
   and SAT_TINT_LUMA_K / SAT_STEEP_REF_LUMA match what the data says
   (biome-sidecar points' tint luminance vs the imagery under them).

Run:  python pipeline/checks/check_sat_albedo.py [area ...]
"""
import ast
import json
import re
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
MASKS = ROOT / "OpenDriveUE/Content/OpenDrive/Masks"
TILE_CM = 200000.0
LUMA = np.array([0.2126, 0.7152, 0.0722])
AREAS = sys.argv[1:] or ["deals_gap", "far_gasquet", "sf_russian_hill"]

src = (ROOT / "unreal/apply_terrain_real_materials.py").read_text(encoding="utf-8")
C = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        try:
            C[node.targets[0].id] = ast.literal_eval(node.value)
        except ValueError:
            pass
cpp = (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODTerrainManager.cpp").read_text(encoding="utf-8")
table = cpp[cpp.index("BiomeGroundTintTable[] = {"):]
TINTS = np.array([[float(v) for v in m] for m in
                  re.findall(r"FLinearColor\(([\d.]+)f, ([\d.]+)f, ([\d.]+)f\)", table[:table.index("};")])])

fails = []


def check(ok, msg):
    print(("  ok   " if ok else "  FAIL ") + msg)
    if not ok:
        fails.append(msg)


def sat_macro(albedo, sat, alpha, amount, dist_cm, tint, flat_w):
    """numpy port of sat_macro()."""
    sl = np.maximum(sat @ LUMA, 0.005)[..., None]
    tl = np.maximum(tint @ LUMA, 0.005)[..., None]
    ref_c = 1.0 + (tint / tl - 1.0) * flat_w
    ref_l = C["SAT_STEEP_REF_LUMA"] + (tl * C["SAT_TINT_LUMA_K"] - C["SAT_STEEP_REF_LUMA"]) * flat_w
    chroma = np.clip((sat / sl) / ref_c, *C["SAT_CHROMA_CLAMP"])
    value = np.clip((sl / ref_l) ** C["SAT_VALUE_EXP"], *C["SAT_VALUE_CLAMP"])
    ramp = np.clip((dist_cm - C["SAT_NEAR_CM"]) / (C["SAT_FAR_CM"] - C["SAT_NEAR_CM"]), 0, 1)
    w = (amount * C["SAT_STRENGTH"] * alpha * (C["SAT_NEAR_W"] + (1 - C["SAT_NEAR_W"]) * ramp))[..., None]
    return albedo * (1 + (chroma * value - 1) * w)


tint_ratio, land_luma = [], []
for area in AREAS:
    print(area)
    png, js = MASKS / f"sat_{area}_test.png", MASKS / f"sat_{area}_test.json"
    if not png.is_file():
        check(False, f"{png.name} missing - run OD_AREA={area} python -I pipeline/build_sat_albedo.py")
        continue
    meta = json.loads(js.read_text())
    img = np.asarray(Image.open(png).convert("RGBA")).astype(np.float64) / 255.0
    srgb, alpha = img[..., :3], img[..., 3]
    lin = np.where(srgb <= 0.04045, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    H, W = alpha.shape

    # 1. extent
    keys = np.array([tuple(map(int, p.stem.rsplit("_", 2)[1:]))
                     for p in MASKS.glob(f"terrain_{area}_test_tile_*.bin")])
    rect = (keys[:, 0].min() * TILE_CM, keys[:, 1].min() * TILE_CM,
            (keys[:, 0].max() + 1) * TILE_CM - keys[:, 0].min() * TILE_CM,
            (keys[:, 1].max() + 1) * TILE_CM - keys[:, 1].min() * TILE_CM)
    check(np.allclose(rect, (meta["x_min_cm"], meta["y_min_cm"], meta["size_x_cm"], meta["size_y_cm"])),
          f"extent = terrain tiles {rect}")
    check((W, H) == (meta["width"], meta["height"]) and
          np.isclose(meta["size_x_cm"] / W, meta["texel_m"] * 100), f"{W}x{H} texels @ {meta['texel_m']} m")
    border = np.concatenate([alpha[0], alpha[-1], alpha[:, 0], alpha[:, -1]])
    check(border.max() == 0.0, "border alpha 0 (outside = fallback)")
    check("Copernicus Sentinel data" in meta.get("attribution", ""), f"attribution: {meta.get('attribution')}")
    print(f"     {png.stat().st_size / 1e6:.2f} MB png, {meta['scenes']} scenes, years {meta['years']}")

    # 2. albedo range
    luma = lin @ LUMA
    land = (alpha > 0.5) & (luma > 0.012)          # water ~<0.01
    hi = np.percentile(lin[land], 99.9)
    check(0.0 <= lin.min() and hi < 0.6, f"land albedo p99.9 {hi:.3f} < 0.6")
    med = np.median(luma[land])
    check(0.015 < med < 0.15, f"land luminance median {med:.4f}")
    land_luma.append(med)

    # 3. seams: step across tile borders vs everywhere
    tx = np.arange(1, W)[np.isclose((meta["x_min_cm"] + np.arange(1, W) * meta["size_x_cm"] / W) % TILE_CM, 0)]
    ty = np.arange(1, H)[np.isclose((meta["y_min_cm"] + np.arange(1, H) * meta["size_y_cm"] / H) % TILE_CM, 0)]
    dx = np.abs(np.diff(luma, axis=1))            # dx[:, c-1] = step between cols c-1 and c
    dy = np.abs(np.diff(luma, axis=0))
    inner = slice(2, -2)
    col_step, row_step = dx[inner].mean(0), dy[:, inner].mean(1)
    ratio = max(col_step[tx - 1].mean() / col_step.mean(), row_step[ty - 1].mean() / row_step.mean())
    check(ratio < 1.15, f"tile-border step / mean step = {ratio:.3f} ({len(tx)}+{len(ty)} borders)")
    # a granule/zone seam is an isolated spike line; a city grid lifts whole bands
    worst = max((v / ndimage.median_filter(v, 21))[2:-2].max() for v in (col_step, row_step))
    check(worst < 2.0, f"worst row/column step vs its 21-line neighbourhood = {worst:.2f} (no seam line)")

    # 4. tint calibration from the biome sidecar points
    b = (MASKS / f"biome_{area}_test.bin").read_bytes()
    n = struct.unpack("<I", b[4:8])[0]
    rec = 14 if b[3:4] == b"2" else 10
    arr = np.frombuffer(b[8:8 + n * rec], np.uint8).reshape(n, rec)
    px = arr[:, 0:4].copy().view("<f4")[:, 0]
    py = arr[:, 4:8].copy().view("<f4")[:, 0]
    c = ((px - meta["x_min_cm"]) / meta["size_x_cm"] * W).astype(int)
    r = ((py - meta["y_min_cm"]) / meta["size_y_cm"] * H).astype(int)
    ok = (c >= 0) & (c < W) & (r >= 0) & (r < H)
    ok[ok] &= land[r[ok], c[ok]]
    tint_ratio.append(luma[r[ok], c[ok]] / (TINTS[arr[ok, 8]] @ LUMA))

# material mirror
print("material")
rng = np.random.default_rng(1)
alb = rng.uniform(0.02, 0.5, (1000, 3))
sat = rng.uniform(0.0, 0.5, (1000, 3))
tint = TINTS[rng.integers(0, len(TINTS), 1000)]
fw = rng.uniform(0, 1, (1000, 1))
d = rng.uniform(0, 1e5, 1000)
check(np.array_equal(sat_macro(alb, sat, np.ones(1000), 0.0, d, tint, fw), alb), "SatAmount 0 = old albedo exactly")
check(np.array_equal(sat_macro(alb, sat, np.zeros(1000), 1.0, d, tint, fw), alb), "alpha 0 = old albedo exactly")
m = sat_macro(alb, sat, np.ones(1000), 1.0, d, tint, fw) / alb
lo = C["SAT_CHROMA_CLAMP"][0] * C["SAT_VALUE_CLAMP"][0]
hi = C["SAT_CHROMA_CLAMP"][1] * C["SAT_VALUE_CLAMP"][1]
check(m.min() >= 1 + (lo - 1) * C["SAT_STRENGTH"] - 1e-9 and m.max() <= 1 + (hi - 1) * C["SAT_STRENGTH"] + 1e-9,
      f"multiplier within clamps [{m.min():.2f}, {m.max():.2f}]")
near = sat_macro(alb, sat, np.ones(1000), 1.0, np.zeros(1000), tint, fw) / alb
check(np.all(np.abs(near - 1) <= (max(hi, 1 / lo) - 1) * C["SAT_STRENGTH"] * C["SAT_NEAR_W"] + 1e-9),
      f"up close the nudge is at most SAT_NEAR_W={C['SAT_NEAR_W']} of full")
body = src[src.index("def build_ground"):src.index("def sat_macro")]
check(body.index("sat_macro(") < body.index('scalar_param("Wetness"') < body.index('snow_w = '),
      "sat_macro before wetness and snow (snow covers it)")
if tint_ratio:
    k = float(np.median(np.concatenate(tint_ratio)))
    check(abs(k / C["SAT_TINT_LUMA_K"] - 1) < 0.25, f"SAT_TINT_LUMA_K {C['SAT_TINT_LUMA_K']} vs fitted {k:.3f}")
    s = float(np.median(land_luma))
    check(abs(s / C["SAT_STEEP_REF_LUMA"] - 1) < 0.25,
          f"SAT_STEEP_REF_LUMA {C['SAT_STEEP_REF_LUMA']} vs fitted land median {s:.4f}")

print(f"{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
