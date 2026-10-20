"""Offline check for pipeline/build_terrain_relief.py + the material's DEM_*
(unreal/apply_terrain_real_materials.py dem_relief_normal, gully/ridge):

1. synthetic DEMs: R/G follow the slope detail with the right sign on both UE
   axes, a plain plane's slope is high-passed away, B reads a valley as gully
   and a crest as ridge, the border is "no data".
2. the material's decode + world-space bend (normal = (-fx, -fy, 1)) of the
   8-bit sRGB texel lands on the true surface normal.
3. every baked dem_<area>_test.* matches its sat_<area>_test extent.
Run from repo root:  python pipeline/checks/check_terrain_relief.py
"""
import ast
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import build_terrain_relief as rb  # noqa: E402

C = {}
for node in ast.parse((ROOT / "unreal/apply_terrain_real_materials.py").read_text(encoding="utf-8")).body:
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.startswith("DEM_"):
        C[node.targets[0].id] = ast.literal_eval(node.value)


def decode(u8):
    s = u8 / 255.0
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)


def ramp(x, lo, hi):
    return np.clip((x - lo) / (hi - lo), 0, 1)


def main():
    assert C["DEM_GRAD_RANGE"] == rb.GRAD_RANGE, "material DEM_GRAD_RANGE != bake GRAD_RANGE"
    t = rb.TEXEL_M
    n = 200
    x, y = np.meshgrid(np.arange(n) * t, np.arange(n) * t)   # x = UE +x (columns), y = UE +y (rows)
    inner = (slice(40, -40), slice(40, -40))
    one = np.ones((n, n))

    # 1a. slope detail, both axes, sign + magnitude
    L = 240.0
    E = 3.0 * np.sin(2 * np.pi * x / L) + 2.0 * np.sin(2 * np.pi * y / L)
    fx, fy = 3.0 * 2 * np.pi / L * np.cos(2 * np.pi * x / L), 2.0 * 2 * np.pi / L * np.cos(2 * np.pi * y / L)
    rgba = rb.relief(E, one, t)
    sx, sy = ((rgba[..., k] - 0.5) * 2 * rb.GRAD_RANGE for k in (0, 1))
    cx, cy = (float(np.corrcoef(a[inner].ravel(), b[inner].ravel())[0, 1]) for a, b in ((sx, fx), (sy, fy)))
    gain = float(np.std(sx[inner]) / np.std(fx[inner]))
    print(f"slope detail: corr x {cx:.3f} y {cy:.3f}, kept {gain:.2f} of a {L:.0f} m ripple")
    assert cx > 0.95 and cy > 0.95, "R/G don't follow dz/dx, dz/dy (sign or axis swapped?)"
    assert 0.6 < gain < 1.2, "high-pass eats (or inflates) the slope detail"

    # 1b. a plain incline is the mesh's job: high-passed to ~0
    plane = rb.relief(0.3 * x + 0.1 * y, one, t)
    assert np.abs(plane[..., :2][inner] - 0.5).max() < 0.01, "a plane leaks into the slope detail"

    # 1c. topographic position: valley = gully (B > 0.5), crest = ridge
    d2 = (x - x.mean()) ** 2
    valley = rb.relief(-10.0 * np.exp(-d2 / (2 * 40.0 ** 2)), one, t)[..., 2]
    crest = rb.relief(10.0 * np.exp(-d2 / (2 * 40.0 ** 2)), one, t)[..., 2]
    mid = (n // 2, n // 2)
    print(f"TPI: valley floor B {valley[mid]:.2f}, crest B {crest[mid]:.2f}")
    assert ramp(valley[mid], *C["DEM_GULLY"]) > 0.5 and ramp(crest[mid], *C["DEM_RIDGE"]) > 0.5
    assert ramp(valley[mid], *C["DEM_RIDGE"]) == 0 and ramp(crest[mid], *C["DEM_GULLY"]) == 0
    assert (rgba[0, :, 3] == 0).all() and (rgba[:, -1, 3] == 0).all(), "border must read as no data"

    # 2. material: decode the 8-bit sRGB texel, bend the (flat) vertex normal
    tex = decode(rb.to_srgb8(rgba).astype(np.float64))
    slope = (tex[..., :2] - 0.5) * 2 * C["DEM_GRAD_RANGE"]
    bent = np.dstack([-slope[..., 0] * C["DEM_NORMAL_STRENGTH"], -slope[..., 1] * C["DEM_NORMAL_STRENGTH"], one])
    true = np.dstack([-fx, -fy, one])
    cos = (bent * true).sum(-1) / np.linalg.norm(bent, axis=-1) / np.linalg.norm(true, axis=-1)
    err = np.degrees(np.arccos(np.clip(cos[inner], -1, 1)))
    print(f"bent normal vs true: mean {err.mean():.2f} deg, max {err.max():.2f} deg")
    assert err.max() < 6.0, "material decode/bend misses the surface normal"

    # 3. baked areas
    for j in sorted(rb.MASKS.glob("dem_*_test.json")):
        area = j.stem[4:-5]
        m, s = json.loads(j.read_text()), json.loads((rb.MASKS / f"sat_{area}_test.json").read_text())
        assert all(m[k] == s[k] for k in ("x_min_cm", "y_min_cm", "size_x_cm", "size_y_cm")), f"{area}: extent != sat"
        a = np.asarray(Image.open(j.with_suffix(".png")))
        assert a.shape == (m["height"], m["width"], 4), f"{area}: png size != json"
        b = decode(a[..., 2][a[..., 3] > 0].astype(np.float64))
        gl, rd = ramp(b, *C["DEM_GULLY"]).mean(), ramp(b, *C["DEM_RIDGE"]).mean()
        print(f"{area}: {m['width']}x{m['height']}, data {(a[..., 3] > 0).mean() * 100:.0f}%, "
              f"mean gully weight {gl:.2f}, ridge {rd:.2f}")
        assert (a[0, :, 3] == 0).all() and 0.02 < gl < 0.4 and 0.02 < rd < 0.4, f"{area}: implausible relief"
    print("check_terrain_relief: OK")


if __name__ == "__main__":
    main()
