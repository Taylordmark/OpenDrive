"""Puddle sag check (PLAN-REALISM Phase 6, 2026-10-09). Mirrors
ODRoadManager.cpp's RoadSagCm (UV3.x, read by M_Road's puddle term) and runs
it over the active area's shipped ODR3 road tiles: how much of the ribbon
sits in a sag deep enough to hold a puddle. Constants are parsed from the
C++ and the material builder so the mirror can't drift.

Usage:  OD_AREA=<area> python pipeline/check_puddle_sag.py
"""
import re
import struct
from pathlib import Path

import numpy as np

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
CPP = (ROOT / "OpenDriveUE/Source/OpenDriveUE/ODRoadManager.cpp").read_text(encoding="utf-8")
MAT = (ROOT / "unreal/apply_road_material.py").read_text(encoding="utf-8")
HALF = int(re.search(r"PuddleSagHalfWindow = (\d+);", CPP).group(1))
SAG_FULL = float(re.search(r"^PuddleSagFullCm = ([\d.]+)", MAT, re.M).group(1))


def road_sag_cm(verts, uvs):
    """verts (n, 3) cm, uvs (n, 2) -> per-vertex sag cm (RoadSagCm)."""
    u = uvs[:, 0]
    inner = np.flatnonzero((u > 1e-3) & (u < 1 - 1e-3))
    n_ribbon = inner[0] if len(inner) else len(u)
    nst = n_ribbon // 7
    sag = np.zeros(len(u), np.float32)
    z = 0.5 * (verts[2:nst * 7:7, 2] + verts[3:nst * 7:7, 2])
    for s in range(nst):
        a, b = max(0, s - HALF), min(nst - 1, s + HALF)
        sag[s * 7:s * 7 + 7] = max(0.0, z[a:b + 1].mean() - z[s])
    return sag


def chunks(path):
    buf = path.read_bytes()
    off = 20
    (n,) = struct.unpack_from("<I", buf, off); off += 4
    for _ in range(n):
        vc, tc = struct.unpack_from("<II", buf, off); off += 11
        v = np.frombuffer(buf, "<f4", vc * 3, off).reshape(-1, 3); off += vc * 12
        uv = np.frombuffer(buf, "<f4", vc * 2, off).reshape(-1, 2); off += vc * 8
        off += vc * 8 + tc * 12
        yield v, uv


def self_test():
    x = np.arange(21)
    ramp = np.repeat(x * 0.06 * 500.0, 7)                    # constant 6% grade, cm
    vee = np.repeat(np.abs(x - 10) * 0.04 * 500.0, 7)        # 4% down, 4% up
    uv = np.tile([0, 0, 0, 1, 1, 1, 0], 21)[:, None] * np.array([[1.0, 0.0]])
    for zz, want in ((ramp, 0.0), (vee, None)):
        v = np.c_[np.zeros((147, 2)), zz]
        s = road_sag_cm(v, uv)
        if want is not None:
            assert np.abs(s[4 * 7:17 * 7]).max() < 1e-3, "straight grade must not sag"
        else:
            assert s[70] > 30 and s[0] == 0.0, "vee bottom must sag, ends not"
    pad = np.c_[np.zeros((147 + 5, 2)), np.r_[vee, np.zeros(5)]]
    s = road_sag_cm(pad, np.r_[uv, np.full((5, 2), 0.5)])
    assert (s[147:] == 0).all(), "pad verts get no sag"


def main():
    self_test()
    paths = sorted((ROOT / "OpenDriveUE/Content/OpenDrive/MasksV2").glob(f"roadv3_{AREA_NAME}_test_tile_*.bin"))
    st = []
    for p in paths:
        for v, uv in chunks(p):
            s = road_sag_cm(np.asarray(v), np.asarray(uv))
            st.append(s[::7][: len(s) // 7])
    st = np.concatenate(st) if st else np.zeros(0)
    print(f"{AREA_NAME}: {len(paths)} tiles, {len(st)} ribbon stations; sag cm pct 50/90/99 "
          f"{np.round(np.percentile(st, [50, 90, 99]), 1) if len(st) else '-'}; "
          f">1 cm {np.mean(st > 1):.1%}, >= PuddleSagFullCm ({SAG_FULL:g}) {np.mean(st >= SAG_FULL):.1%}")
    print("PASS: sag mirror self-test")


if __name__ == "__main__":
    main()
