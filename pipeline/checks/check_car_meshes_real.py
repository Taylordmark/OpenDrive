"""Check the Realistic car meshes (pipeline/make_car_meshes_real.py) against
the contract the pawn relies on, from data/cars/real/real_manifest.json:

- axles / half track / wheel radius equal the physics in
  unreal/import_car_meshes.py's make_def() calls (the mesh is built on them);
- bounding box within TOL cm of the stylized OBJ twin (same identity and
  proportions; mirrors, wings and lamp detail may stick out a little);
- triangle budget: ultra 40-120k, high ~1/2, Standard ~0.22 of ultra;
- wheel authored at radius 34 / half width 13 (the pawn scales by WheelRadius/34);
- every FBX present, LICENSE.txt present.

python pipeline/checks/check_car_meshes_real.py   (exit 1 on any failure)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / "data" / "cars" / "real"
OBJ = ROOT / "data" / "cars" / "procedural" / "obj"
TOL = 4.0
fails = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        fails.append(msg)


def physics():
    src = (ROOT / "unreal" / "import_car_meshes.py").read_text()
    out = {}
    for name, body in re.findall(r'make_def\("DA_Vehicle_(\w+)", \{(.*?)\n\}\)', src, re.S):
        vals = dict(re.findall(r'"(\w+_cm)": (-?[\d.]+)', body))
        out[name] = {k: float(v) for k, v in vals.items()}
    return out


def obj_bbox(path):
    vs = [tuple(map(float, l.split()[1:4])) for l in path.read_text().splitlines() if l.startswith("v ")]
    return [min(v[i] for v in vs) for i in range(3)], [max(v[i] for v in vs) for i in range(3)]


man = json.loads((REAL / "real_manifest.json").read_text())
phys = physics()
check((REAL / "LICENSE.txt").exists(), "data/cars/real/LICENSE.txt present")
for car, m in man["cars"].items():
    p = phys.get(car)
    check(p is not None, f"{car}: make_def found")
    if p:
        for key, mk in (("front_axle_x_cm", "FrontAxleXCm"), ("rear_axle_x_cm", "RearAxleXCm"),
                        ("half_track_y_cm", "HalfTrackYCm"), ("wheel_radius_cm", "WheelRadiusCm")):
            check(abs(p[key] - m[mk]) < 0.01, f"{car}: {mk} {m[mk]} == physics {p[key]}")
    lo, hi = obj_bbox(OBJ / f"SM_Car_{car}.obj")
    blo, bhi = m["bbox"]
    # OBJ y is negated (symmetric, so only the extents matter); z is the same frame
    d = max(max(abs(a - b) for a, b in zip(blo, (lo[0], -hi[1], lo[2]))),
            max(abs(a - b) for a, b in zip(bhi, (hi[0], -lo[1], hi[2]))))
    check(d <= TOL, f"{car}: bbox within {d:.1f} cm of stylized (tol {TOL})")
    t = m["tris"]
    u, h, s = (t[f"SM_Car_{car}_real{x}"] for x in ("_ultra", "_high", ""))
    check(40000 <= u <= 120000, f"{car}: ultra {u} tris in 40-120k")
    check(0.4 < h / u < 0.6 and 0.15 < s / u < 0.3, f"{car}: tiers {h}/{s} of {u}")
    for x in ("", "_high", "_ultra"):
        check((REAL / "fbx" / f"SM_Car_{car}_real{x}.fbx").exists(), f"SM_Car_{car}_real{x}.fbx exists")
w = man["wheel"]
check(w["RefRadiusCm"] == 34 and w["HalfWidthCm"] == 13, f"wheel radius {w['RefRadiusCm']} half width {w['HalfWidthCm']}")
for x in ("", "_high", "_ultra"):
    check((REAL / "fbx" / f"SM_CarWheel_real{x}.fbx").exists(), f"SM_CarWheel_real{x}.fbx exists")
check(len(man["cars"]) == 7, f"{len(man['cars'])}/7 cars in manifest")
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
