"""Offline check (2026-10-21, area picker A3): experimental_junction_pads run
per pass-2 job rect gives the same pads, tiles, sidecar and bank as one
whole-area run. Synthetic network, temp dirs, terrain re-carve stubbed.

    python pipeline/test_junction_pads_cells.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.pop("OD_JOB_RECT_M", None)
import experimental_junction_pads as jp
from build_road_network import write_station_sidecar

CUT = 1000.0   # rects A | B meet at east = 1000 m
RECTS = ("-2000,-2000,1000,2000", "1000,-2000,4000,2000")


def road(name, e0, n0, e1, n1, dz=0.0):
    n = int(np.hypot(e1 - e0, n1 - n0) // 5.0) + 1
    t = np.linspace(0.0, 1.0, n)
    e, nn = e0 + t * (e1 - e0), n0 + t * (n1 - n0)
    s = np.r_[0.0, np.cumsum(np.hypot(np.diff(e), np.diff(nn)))]
    z = 100.0 + 0.02 * e + 0.01 * nn + dz + 0.3 * np.sin(e / 90.0 + nn / 70.0)
    return {"name": name, "east_m": e.tolist(), "north_m": nn.tolist(), "elev_m": z.tolist(),
            "s_m": s.tolist(), "road_name": [name] * n,
            "is_struct": [False] * n, "is_bridge": [False] * n}


SEGMENTS = [
    road("main", -1500.0, 2.0, 3500.0, -3.0),
    road("cut_cross", 993.0, -800.0, 999.0, 800.0, 0.8),     # crosses main 1-7 m from the cut
    road("west_cross", -300.0, -700.0, -250.0, 700.0, -0.6),
    road("east_cross", 2200.0, -600.0, 2260.0, 650.0, 0.5),
    road("cut_tee", 1012.0, 600.0, 1010.0, 9.0, 0.4),        # dangles just short of main
]


def run(root, rect):
    for k, v in PATHS.items():
        setattr(jp, k, root / v)
    jp.btmt.STATIONS_PATH = root / "road_stations.json"
    if rect:
        os.environ["OD_JOB_RECT_M"] = rect
    else:
        os.environ.pop("OD_JOB_RECT_M", None)
    seen = []
    jp.regrade_terrain_at_junctions = lambda junctions, *a, **k: seen.append(
        [dict(j) for j in junctions])
    try:
        jp.main()
    finally:
        os.environ.pop("OD_JOB_RECT_M", None)
    return seen[0]


def setup(root):
    (root / "OpenDrive" / "Masks").mkdir(parents=True)
    (root / "road_stations.json").write_text(json.dumps({"segments": SEGMENTS}))
    e = np.concatenate([s["east_m"] for s in SEGMENTS])
    n = np.concatenate([s["north_m"] for s in SEGMENTS])
    z = jp.road_ue_z_m(np.concatenate([s["elev_m"] for s in SEGMENTS]), jp.ROAD_LIFT,
                       jp.ORIGIN_HEIGHT_M)
    write_station_sidecar(root / "OpenDrive" / "Masks" / jp.SIDECAR_PATH.name,
                          [(e * 100.0, -n * 100.0, z * 100.0, np.full(len(e), 400.0))],
                          jp.pack_generation_id(GEN))


def outputs(root):
    m = root / "OpenDrive"
    tiles = {p.relative_to(m).as_posix(): p.read_bytes()
             for d in ("Masks", "MasksV2") for p in (m / d).glob("road*_tile_*.bin")}
    return tiles, (m / "Masks" / jp.SIDECAR_PATH.name).read_bytes()


def cut_pad(junctions):
    near = [j for j in junctions if abs(j["xy"][0] - CUT) < 40.0 and abs(j["xy"][1]) < 40.0]
    assert len(near) == 1, [j["xy"] for j in near]
    j = near[0]
    return (np.r_[j["xy"], j.get("ellipse", ()), j["radius_m"], j["elev"], j.get("plane", (0, 0))],
            np.asarray(j.get("profile") or [], dtype=float))


GEN = "0123456789abcdef"
PATHS = {k: getattr(jp, k).relative_to(jp.OUT_DIR.parent.parent)   # OpenDrive/...
         for k in ("OUT_DIR", "BACKUP_DIR", "OUT_DIR_V2", "V3_BACKUP_DIR", "TERRAIN_BACKUP_DIR",
                   "SIDECAR_PATH", "SIDECAR_BACKUP_DIR", "BANK_SCALE_PATH")}
jp.compute_generation_id = lambda: GEN
jp.shipped_terrain_heights = lambda xs, ys: np.full(len(np.asarray(xs)), np.nan)
sys.argv = [sys.argv[0]]

with tempfile.TemporaryDirectory() as td:
    whole, cells = Path(td) / "whole", Path(td) / "cells"
    for root in (whole, cells):
        setup(root)
    pads_w = run(whole, None)
    pads_a = run(cells, RECTS[0])
    pads_b = run(cells, RECTS[1])

    # the pad straddling the cut comes out the same from both rects (and whole)
    ref = cut_pad(pads_w)
    for got in (cut_pad(pads_a), cut_pad(pads_b)):
        assert len(got[0]) == len(ref[0]) and np.allclose(got[0], ref[0], atol=1e-6), (got[0], ref[0])
        assert got[1].shape == ref[1].shape and np.allclose(got[1], ref[1], atol=1e-6)

    # the union of the two rects' outputs is the whole-area output
    tiles_w, side_w = outputs(whole)
    tiles_c, side_c = outputs(cells)
    assert tiles_w and sorted(tiles_w) == sorted(tiles_c), (sorted(tiles_w), sorted(tiles_c))
    diff = [k for k in tiles_w if tiles_w[k] != tiles_c[k]]
    assert not diff, f"tiles differ: {diff}"
    assert side_w == side_c, "station sidecar differs"
    bank = [np.load(r / PATHS["BANK_SCALE_PATH"])["scale"] for r in (whole, cells)]
    assert np.array_equal(*bank), "bank scale differs"
    key = lambda d: (round(d["xy"][0], 6), round(d["xy"][1], 6))
    pj_w = sorted(json.loads((whole / "junction_pads_final.json").read_text()), key=key)
    pj_c = sorted(json.loads((cells / "junction_pads_final.json").read_text()), key=key)
    assert pj_w == pj_c, "junction_pads_final.json differs"
    print(f"OK: {len(pads_w)} pad(s), {len(tiles_w)} tile(s) identical whole vs "
          f"{RECTS[0]} + {RECTS[1]}; cut pad identical from both sides")
