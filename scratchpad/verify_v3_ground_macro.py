"""V3 offline check (next steps.md §7.36): reproduce M_GroundStylized's
FAR-FIELD albedo (textures -> their linear mip-average, which is what far
mips converge to) before/after the V3 macro-variation + distance shaping,
over the real summit_county coarse terrain tiles (100 m grid).

Material constants are parsed straight out of unreal/apply_ground_material.py
(no duplication); vertex tint is a port of AODTerrainManager::ApplyBiomeTint
for OFF-CORRIDOR vertices (default DeciduousForest tint - V1 not landed, so
background hills get no biome sample; road-verge term omitted). Snow is
omitted (summer; item 6 proved it a no-op on the pale hills).
Camera for the map/stats: area origin, 2 m above ground there.

Run from repo root:  python scratchpad/verify_v3_ground_macro.py
"""
import ast
import glob
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import rgb_to_hsv
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
sys.path.insert(0, str(ROOT / "scratchpad"))
from bin_tile_io import parse_terrain_tile_bin
from shader_noise_common import value_noise as wrapped_value_noise
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M

OUT = ROOT / "scratchpad"

# ---- constants from the material script itself
src = (ROOT / "unreal/apply_ground_material.py").read_text()
C = {}
for m in re.finditer(r"^([A-Z_]+) = (.+?)(?:\s+#.*)?$", src, re.M):
    try:
        C[m.group(1)] = ast.literal_eval(m.group(2))
    except Exception:
        pass
C["TREELINE_LOW_CM"] = (C["TREELINE_LOW_M"] - ORIGIN_HEIGHT_M) * 100.0
C["TREELINE_HIGH_CM"] = (C["TREELINE_HIGH_M"] - ORIGIN_HEIGHT_M) * 100.0

# ---- texture linear means (sRGB-imported albedo -> linear, far-mip average)
def s2l(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def l2s(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)

TEX = {}
LAYERS = {"grass": "Grass001", "forest": "Ground068", "dirt": "Ground037", "rock": "Rock030", "scree": "Gravel030"}
for k, aid in LAYERS.items():
    im = np.asarray(Image.open(ROOT / f"content_src/textures/ground/{aid}/{aid}_2K-PNG_Color.png")
                    .convert("RGB").reduce(8), np.float64) / 255.0
    TEX[k] = s2l(im).reshape(-1, 3).mean(0)

UE_DESAT = np.array([0.3, 0.59, 0.11])  # MaterialExpressionDesaturation default LuminanceFactors

# ---- terrain grid from coarse tiles
pts = []
for f in glob.glob(str(ROOT / f"OpenDriveUE/Content/OpenDrive/Masks/terrain_coarse_{AREA_NAME}_test_tile_*.bin")):
    t = parse_terrain_tile_bin(Path(f))
    pts.append(np.array(t["verts"]).reshape(-1, 3))
pts = np.concatenate(pts)
STEP = 10000.0  # 100 m
ix = np.round(pts[:, 0] / STEP).astype(int)
iy = np.round(pts[:, 1] / STEP).astype(int)
ix0, iy0 = ix.min(), iy.min()
H = np.full((iy.max() - iy0 + 1, ix.max() - ix0 + 1), np.nan)
H[iy - iy0, ix - ix0] = pts[:, 2]
valid = ~np.isnan(H)
Hf = np.where(valid, H, np.nanmean(H))
gy, gx = np.gradient(Hf, STEP)
slope_dot = 1.0 / np.sqrt(1 + gx ** 2 + gy ** 2)
X = (np.arange(H.shape[1]) + ix0) * STEP
Y = (np.arange(H.shape[0]) + iy0) * STEP
X, Y = np.meshgrid(X, Y)
Z = Hf

# ---- vertex tint (ApplyBiomeTint, off-corridor path)
def hash_lattice(x, y, seed):
    h = (x.astype(np.uint32) * np.uint32(374761393) + y.astype(np.uint32) * np.uint32(668265263)
         + np.uint32((seed * 2246822519) & 0xFFFFFFFF)).astype(np.uint32)
    h = ((h ^ (h >> np.uint32(13))) * np.uint32(1274126177)).astype(np.uint32)
    h ^= h >> np.uint32(16)
    return (h & np.uint32(0xFFFFFF)).astype(np.float64) / float(0xFFFFFF)

def cpp_value_noise(x, y, scale, seed):
    gx_, gy_ = x / scale, y / scale
    i, j = np.floor(gx_).astype(np.int64), np.floor(gy_).astype(np.int64)
    fx, fy = gx_ - i, gy_ - j
    sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
    i, j = i.astype(np.int32).astype(np.uint32), j.astype(np.int32).astype(np.uint32)
    v00, v10 = hash_lattice(i, j, seed), hash_lattice(i + np.uint32(1), j, seed)
    v01, v11 = hash_lattice(i, j + np.uint32(1), seed), hash_lattice(i + np.uint32(1), j + np.uint32(1), seed)
    return (v00 * (1 - sx) + v10 * sx) * (1 - sy) + (v01 * (1 - sx) + v11 * sx) * sy

def vertex_tint():
    tint = np.array([0.06, 0.16, 0.05])
    et = np.clip((Z - -8530.0) / (68000.0 - -8530.0), 0, 1)[..., None]
    es = et * et * (3 - 2 * et)
    low = tint + (np.array([0.02, 0.10, 0.02]) - tint) * 0.45
    high = tint + (np.array([0.40, 0.40, 0.40]) - tint) * 0.55
    t = low + (high - low) * es
    n = np.clip(0.4 * cpp_value_noise(X, Y, 5000.0, 1) + 0.6 * cpp_value_noise(X, Y, 20000.0, 2), 0, 1)[..., None]
    t = t + (t * np.array([1.25, 1.05, 0.75]) - t) * n * 0.35
    return np.round(np.clip(t, 0, 1) * 255) / 255  # QuantizeRound / ReinterpretAsLinear

VT = vertex_tint()

def lerp(a, b, t):
    return a + (b - a) * t

def albedo(depth_cm, v3):
    luma = VT @ np.array([0.2126, 0.7152, 0.0722])
    fw = np.clip((0.16 - luma) / (0.16 - 0.075), 0, 1)[..., None]
    veg = lerp((TEX["grass"] @ UE_DESAT) * 2 * VT, (TEX["forest"] @ UE_DESAT) * 2 * VT, fw)
    patch = wrapped_value_noise(X, Y, C["MACRO_NOISE_SCALE_CM"])[..., None]
    veg = lerp(veg, TEX["dirt"], patch * C["DIRT_PATCH_STRENGTH"])
    if v3:
        fine = wrapped_value_noise(X, Y, C["MACRO_FINE_SCALE_CM"], seed_offset=(13.0, 57.0))
        coarse = wrapped_value_noise(X, Y, C["MACRO_COARSE_SCALE_CM"], seed_offset=(71.0, 29.0))
        m = np.clip((lerp(fine, coarse, C["MACRO_COARSE_WEIGHT"]) - 0.5) * C["MACRO_CONTRAST"] + 0.5, 0, 1)
        lo, hi = np.clip(m * 2, 0, 1)[..., None], np.clip(m * 2 - 1, 0, 1)[..., None]
        mul = lerp(lerp(np.array(C["MACRO_GREEN_MUL"]), np.array(C["MACRO_DRY_MUL"]), lo),
                   np.array(C["MACRO_DIRT_MUL"]), hi)
        veg = veg * mul
        far = np.clip((depth_cm - C["FAR_START_CM"]) / (C["FAR_END_CM"] - C["FAR_START_CM"]), 0, 1)[..., None]
        grey = (veg @ UE_DESAT)[..., None]
        veg = lerp(grey, veg, 1 + far * C["FAR_SAT_BOOST"]) * (1 - far * C["FAR_DARKEN"])
    tt = np.clip((Z - C["TREELINE_LOW_CM"]) / (C["TREELINE_HIGH_CM"] - C["TREELINE_LOW_CM"]), 0, 1)[..., None]
    scree = TEX["scree"] * np.array([0.78, 0.72, 0.62])
    veg = lerp(veg, scree, tt * C["ALPINE_BARE_MAX_BLEND"])
    steep = lerp(TEX["rock"], scree, tt)
    flat_w = (slope_dot ** C["SLOPE_SOFTNESS"])[..., None]
    return lerp(steep, veg, flat_w), flat_w[..., 0]

def stats(rgb_lin, mask):
    hsv = rgb_to_hsv(l2s(rgb_lin))[mask]
    ang = hsv[:, 0] * 2 * np.pi
    R = np.hypot(np.cos(ang).mean(), np.sin(ang).mean())
    return dict(hue_circ_std_deg=np.degrees(np.sqrt(-2 * np.log(R))), sat_mean=hsv[:, 1].mean(),
                sat_std=hsv[:, 1].std(), val_mean=hsv[:, 2].mean(), val_std=hsv[:, 2].std(),
                min_lin=float(rgb_lin[mask].min()))

cam_z = Hf[np.unravel_index(np.argmin(X ** 2 + Y ** 2), X.shape)] + 200.0
depth = np.sqrt(X ** 2 + Y ** 2 + (Z - cam_z) ** 2)
before, flat_w = albedo(depth, False)
after, _ = albedo(depth, True)
flat = valid & (flat_w > 0.5)
far = flat & (depth > 300000)

lines = [f"area={AREA_NAME} grid={H.shape} 100 m, valid={valid.sum()}, flat(flat_w>0.5)={flat.sum()}, far(>3km)={far.sum()}",
         "texture linear means: " + ", ".join(f"{k}={np.round(v, 4).tolist()}" for k, v in TEX.items()),
         "V3 constants: " + ", ".join(f"{k}={C[k]}" for k in C if k.startswith(("MACRO_", "FAR_")))]
a0, _ = albedo(np.zeros_like(depth), True)
sb0, sa0 = stats(before, flat), stats(a0, flat)
lines.append("[all flat, macro only, depth=0] before: " + " ".join(f"{k}={v:.4f}" for k, v in sb0.items()))
lines.append("[all flat, macro only, depth=0] after : " + " ".join(f"{k}={v:.4f}" for k, v in sa0.items()))
for name, mask in (("all flat", flat), ("far flat >3km", far)):
    sb, sa = stats(before, mask), stats(after, mask)
    lines.append(f"[{name}] before: " + " ".join(f"{k}={v:.4f}" for k, v in sb.items()))
    lines.append(f"[{name}] after : " + " ".join(f"{k}={v:.4f}" for k, v in sa.items()))
    mb, ma = l2s(before[mask].mean(0)) * 255, l2s(after[mask].mean(0)) * 255
    lines.append(f"[{name}] mean albedo sRGB8 before={np.round(mb).tolist()} after={np.round(ma).tolist()}")

# mean value vs distance: every flat point evaluated as if seen at depth d
ds = np.linspace(0, 1500000, 31)
vb = rgb_to_hsv(l2s(before[flat]))[:, 2].mean()
va, sa_ = [], []
for d in ds:
    a, _ = albedo(np.full_like(depth, d), True)
    h = rgb_to_hsv(l2s(a[flat]))
    va.append(h[:, 2].mean()); sa_.append(h[:, 1].mean())
sb_ = rgb_to_hsv(l2s(before[flat]))[:, 1].mean()
lines.append("mean V (sRGB) vs depth, flat pts: before (depth-independent)=%.4f" % vb)
for d, v, s in zip(ds[::5], va[::5], sa_[::5]):
    lines.append(f"  d={d / 100000:5.1f} km  after V={v:.4f} ({(v / vb - 1) * 100:+.1f}%)  S={s:.4f} (before {sb_:.4f})")
log = "\n".join(lines)
print(log)
(OUT / "verify_v3_ground_macro.log").write_text(log + "\n")

fig, ax = plt.subplots(2, 2, figsize=(13, 12))
ext = [X.min() / 1e5, X.max() / 1e5, Y.min() / 1e5, Y.max() / 1e5]
for a_, img, title in ((ax[0, 0], before, "before (shipped) far-field albedo"),
                       (ax[0, 1], after, "after V3 (camera at origin)")):
    a_.imshow(np.where(valid[..., None], l2s(before if img is before else after), 0), origin="lower", extent=ext)
    a_.set_title(title); a_.set_xlabel("x km"); a_.set_ylabel("y km")
ax[1, 0].imshow(np.where(valid[..., None], np.clip(l2s(a0) * 1.8, 0, 1), 0), origin="lower", extent=ext)
ax[1, 0].set_title("after V3, macro variants only (depth=0), x1.8 display gain")
ax[1, 1].plot(ds / 1e5, va, label="after V3: mean V")
ax[1, 1].axhline(vb, color="gray", ls="--", label="before: mean V")
ax[1, 1].plot(ds / 1e5, sa_, label="after V3: mean S")
ax[1, 1].axhline(sb_, color="gray", ls=":", label="before: mean S")
ax[1, 1].set_xlabel("pixel depth km"); ax[1, 1].set_title("flat pts: mean HSV value / saturation vs depth")
ax[1, 1].legend()
plt.tight_layout()
plt.savefig(OUT / "verify_v3_ground_macro.png", dpi=90)
print("wrote", OUT / "verify_v3_ground_macro.png")
