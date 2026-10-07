"""Offline check for M_GroundStylized's 2026-10-07 additions: medium-scale
dry/bare patch coverage, the road-shoulder width vs the grass clear zone,
and RoadDistTex's 8-bit sRGB distance precision near the pavement edge.
Constants are parsed out of unreal/apply_ground_material.py and
ODTerrainManager.h / ODForestManager.h, so drift between them fails here.

Run from repo root:  python pipeline/checks/check_ground_patches.py
"""
import ast
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shader_noise_common import value_noise

src = (ROOT / "unreal/apply_ground_material.py").read_text()
C = {}
for m in re.finditer(r"^([A-Z_]+) = (.+?)(?:\s+#.*)?$", src, re.M):
    try:
        C[m.group(1)] = ast.literal_eval(m.group(2))
    except Exception:
        pass
cpp = (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODTerrainManager.h").read_text()
range_cm = float(re.search(r"RoadDistRangeCm\s*=\s*([\d.]+)", cpp).group(1))
texels = int(re.search(r"RoadDistTexels\s*=\s*(\d+)", cpp).group(1))
texel_cm = float(re.search(r"RoadDistTexelCm\s*=\s*([\d.]+)", cpp).group(1))
clear_cm = float(re.search(r"GrassShoulderClearCm\s*=\s*([\d.]+)",
                           (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODForestManager.h").read_text()).group(1))

# ---- patch coverage over a 4 km square at real area-scale coordinates
xs = np.arange(0, 400000, 150.0) + 1.7e6
wx, wy = np.meshgrid(xs, xs + 2.3e6)
p = (value_noise(wx, wy, C["PATCH_FINE_SCALE_CM"], seed_offset=(23.0, 41.0)) * (1 - C["PATCH_COARSE_WEIGHT"])
     + value_noise(wx, wy, C["PATCH_COARSE_SCALE_CM"], seed_offset=(59.0, 11.0)) * C["PATCH_COARSE_WEIGHT"])
p = (p - 0.5) * C["PATCH_CONTRAST"] + 0.5
dry = np.clip((p - C["PATCH_DRY_LO"]) / (C["PATCH_DRY_HI"] - C["PATCH_DRY_LO"]), 0, 1)
bare = np.clip((p - C["PATCH_BARE_LO"]) / (C["PATCH_BARE_HI"] - C["PATCH_BARE_LO"]), 0, 1)
dry_frac, bare_frac = (dry > 0.5).mean(), (bare > 0.3).mean()
print(f"dry patches {dry_frac:.1%}, bare spots {bare_frac:.1%}")
assert 0.12 <= dry_frac <= 0.45, "dry-straw coverage out of range - retune PATCH_DRY_*"
assert 0.02 <= bare_frac <= 0.15, "bare-dirt coverage out of range - retune PATCH_BARE_*"

# ---- shoulder vs grass clear zone, range constant agreement
assert C["ROAD_DIST_RANGE_M"] * 100 == range_cm, "ROAD_DIST_RANGE_M != RoadDistRangeCm"
assert C["ROAD_DIST_COVERAGE_CM"] == texels * texel_cm, "ROAD_DIST_COVERAGE_CM != RoadDistTexels * RoadDistTexelCm"
assert (C["SHOULDER_MAX_M"] + C["SHOULDER_JITTER_M"]) * 100 <= clear_cm, "grass would grow on the shoulder"

# ---- RoadDistTex precision: C++ stores sRGB(d/range) in 8 bits, sampler decodes
def s2l(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)

def l2s(c):
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)

d = np.linspace(0, 400, 4001)   # cm, the band that matters
dec = s2l(np.round(l2s(d / range_cm) * 255) / 255) * range_cm
err = np.abs(dec - d).max()
print(f"edge-distance error within 4 m: {err:.1f} cm")
assert err < 8.0, "RoadDistTex too coarse near the edge"
print("OK")
