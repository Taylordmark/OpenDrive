"""Check the Realistic car meshes (pipeline/make_car_meshes_real.py) against
the contract the pawn relies on, from data/cars/real/real_manifest.json:

- axles / half track / wheel radius equal the physics in
  unreal/import_car_meshes.py's make_def() calls (the mesh is built on them);
- length and height within TOL cm of the stylized OBJ twin (same identity
  and proportions; mirrors, wings and lamp detail may stick out a little);
  width within TOL, or out to the fender flares the generator recorded
  (FenderYCm), which may grow at most FENDER_ALLOW cm a side past it;
- triangle budget: ultra 40-120k, high ~1/2, Standard ~1/3 of ultra (own once-subdivided build);
- wheel authored at radius 34 / half width 13 (the pawn scales by WheelRadius/34),
  every tyre kind (road + the per-setup ones) exported;
- arches, for every garage setup in unreal/apply_tuning_presets.py (its tyre
  size / width, ride height, camber, steering lock): >= 1.5 cm visible gap
  over the tyre at static ride height (Street 1.5-4.5), and the tyre clear of
  the body at static, at the bump stop, at full lock and at bump + lock;
- smoothness: no angle over KINK deg between neighbouring body faces of one
  panel on the ultra hull, away from the crease lines;
- every FBX present, LICENSE.txt present.

python pipeline/checks/check_car_meshes_real.py   (exit 1 on any failure)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
import tuning_setups  # noqa: E402

REAL = ROOT / "data" / "cars" / "real"
OBJ = ROOT / "data" / "cars" / "procedural" / "obj"
TOL = 4.0
FENDER_ALLOW = 18.0    # cm a side the fender flares may stand past the stylized width (Offroad tyres at bump + lock: 16-17)
KINK = 30.0           # deg per ultra edge (today's worst: nose caps / cab back edge, ~27)
SETUPS = list(tuning_setups.setups())
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
    d = max(abs(blo[0] - lo[0]), abs(bhi[0] - hi[0]), abs(blo[2] - lo[2]), abs(bhi[2] - hi[2]))
    check(d <= TOL, f"{car}: length / height within {d:.1f} cm of stylized (tol {TOL})")
    sw, fy = max(-lo[1], hi[1]), max(m["FenderYCm"])
    rw = max(-blo[1], bhi[1])
    check(abs(rw - sw) <= TOL or (sw < rw <= fy + 0.5 and fy - sw <= FENDER_ALLOW),
          f"{car}: half width {rw:.1f} (stylized {sw:.1f}, fender flares {fy:.1f}: +{fy - sw:.1f},"
          f" allow {FENDER_ALLOW})")
    t = m["tris"]
    u, h, s = (t[f"SM_Car_{car}_real{x}"] for x in ("_ultra", "_high", ""))
    check(40000 <= u <= 120000, f"{car}: ultra {u} tris in 40-120k")
    check(0.4 < h / u < 0.6 and 0.25 < s / u < 0.45, f"{car}: tiers {h}/{s} of {u}")
    k = m.get("Kink")
    check(k is not None and k["deg"] <= KINK, f"{car}: worst kink {k and k['deg']} deg (max {KINK}) at {k and k['at']}")
    for x in ("", "_high", "_ultra"):
        check((REAL / "fbx" / f"SM_Car_{car}_real{x}.fbx").exists(), f"SM_Car_{car}_real{x}.fbx exists")
    arches = m.get("arches", {})
    check(set(arches) == {"front", "rear"} and all(list(arches[e]) == SETUPS for e in arches),
          f"{car}: arch clearance measured for {SETUPS}")
    for end, per in arches.items():
        for s, a in per.items():
            hi_gap = 4.5 if s == "Street" else 99.0
            keys = ("static", "bump", "lock", "bump_lock") if end == "front" else ("static", "bump")
            check(a["lip_gap"] is not None and 1.5 <= a["lip_gap"] <= hi_gap and all(a[k] >= 0 for k in keys),
                  f"{car} {end} {s}: lip gap {a['lip_gap']}, clearance "
                  + " ".join(f"{k} {a[k]}" for k in keys))
w =man["wheel"]
check(w["RefRadiusCm"] == 34 and w["HalfWidthCm"] == 13, f"wheel radius {w['RefRadiusCm']} half width {w['HalfWidthCm']}")
for k in w["Kinds"]:      # the road tyre + each garage setup's (SetupTyres)
    for x in ("", "_high", "_ultra"):
        n = "SM_CarWheel_real" + ("" if k == "Road" else f"_{k}") + x
        check((REAL / "fbx" / f"{n}.fbx").exists(), f"{n}.fbx exists")
check(all(k in w["Kinds"] for fr in w["SetupTyres"].values() for k in fr) and set(w["SetupTyres"]) <= set(SETUPS),
      f"setup tyres {w['SetupTyres']} are built kinds of known setups")
check(len(man["cars"]) == 7, f"{len(man['cars'])}/7 cars in manifest")
print(f"\n{len(fails)} failure(s)")
sys.exit(1 if fails else 0)
