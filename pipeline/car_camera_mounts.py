"""Per-car camera mounts for UODVehicleDef (cockpit eye, steering wheel, hood,
bumper), straight from the Realistic car geometry - the same numbers
make_car_meshes_real.py's build_interior() places the seats / dash / baked
steering wheel with, so the cockpit cam sits in the driver's seat and the
pawn's turning wheel lands on the baked one.

    python pipeline/car_camera_mounts.py

Runs in plain Python (bpy / bmesh / mathutils stubbed: only the preset and
section math is used). Asserts each mount is inside / on the body where it
should be, then prints the dict pasted into unreal/apply_vehicle_cameras.py.
UE car-local cm: +x forward, +y right (Blender +y is UE -y: the LHD driver
sits at UE -y), z up from axle height.
"""

import sys
import types

for _m in ("bpy", "bmesh", "mathutils", "mathutils.bvhtree"):
    sys.modules[_m] = types.SimpleNamespace(Matrix=None, Vector=None, BVHTree=None)
sys.argv = [sys.argv[0]]
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np  # noqa: E402
import make_car_meshes_real as real  # noqa: E402

EYE_UNDER_ROOF = 22.0      # outer roof to eye: skin + headliner + helmet room + head top
HOOD_CAM_UP = 14.0         # over the hood skin just ahead of the cowl
BUMPER_CAM_AHEAD = 8.0     # ahead of the nose


def mounts(name):
    car = real.Car(name)
    S = real.sections(car, np.array([car.gs - 20.0]))
    belt, ztop = float(S["belt"][0]), float(S["Z"][0, -1])
    xd = car.gs - 8.0
    zdash = min(belt + 3.0, ztop - 5.0)
    xh = car.doors[0][1] + 22.0                         # build_interior's front hip point
    ys = 0.45 * car.width(xh) if not car.center else min(0.45 * car.width(xh), 0.55 * float(
        np.interp(xh, *zip(*sorted((c[0], c[2]) for c in car.center)))))
    if xh - 40.0 < car.rx + car.r_well:
        ys = min(ys, car.ht - car.tyre_in[1] - 24.0)
    top = float(real.sections(car, np.array([xh]))["Z"][0, -1]) - 8.0
    # baked steering wheel centre; build_interior skips it when it would sit
    # in the dash (Rally) - the pawn's wheel then goes just short of the dash
    xw, zw = xh + 58.0 if xh + 58.0 < xd - 4.0 else xd - 12.0, min(belt + 2.0, top - 10.0)

    xe = xh + 6.0
    roof = float(real.sections(car, np.array([xe]))["Z"][0, -1])
    ze = roof - EYE_UNDER_ROOF   # the seat meshes sit on the floor (Pickup too): the roof sets it
    xc = car.gs + 12.0
    zhood = float(real.sections(car, np.array([xc]))["Z"][0, -1])
    nose = max(p[0] for p in car.profile)
    nz = [p for p in car.profile if p[0] == nose][0]
    out = dict(eye=(xe, -ys, ze), wheel=(xw, -ys, zw), hood=(xc, 0.0, zhood + HOOD_CAM_UP),
               bumper=(nose + BUMPER_CAM_AHEAD, 0.0, max(0.5 * (nz[1] + nz[2]), -0.5 * car.wr)))

    # sanity: the eye is in the cabin, sees over the dash, under the roof; the
    # wheel is ahead of and below the eye, short of the dash
    assert car.ge < xe < car.gs, (name, "eye x outside the glass", xe, car.ge, car.gs)
    assert ze > zdash + 5.0, (name, "eye below the dash top", ze, zdash)
    assert 30.0 < xw - xe < 70.0 and zw < ze - 10.0, (name, "wheel not ahead/below the eye", xw - xe, ze - zw)
    assert xw < xd - 4.0, (name, "wheel inside the dash", xw, xd)
    return out


if __name__ == "__main__":
    print("CARS = {")
    for n in real.PRESETS:
        m = mounts(n)
        print(f'    "{n}": dict(' + ", ".join(
            f"{k}=({v[0]:.0f}, {v[1]:.0f}, {v[2]:.0f})" for k, v in m.items()) + "),")
    print("}")
