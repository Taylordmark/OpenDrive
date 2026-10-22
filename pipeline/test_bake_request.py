"""Offline checks for the area-picker runner (2026-10-21): request validation,
corridor buffer, zone pick, cell grid + job pick, estimate, status files,
coverage from a fake tile dir, and a whole run against a fake rebuild_area.py
(temp dirs only, no network, nothing under data/ or Content/ touched).

Usage:  python pipeline/test_bake_request.py
"""
import json
import sys
import tempfile
import textwrap
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bake_request as br
import build_world_manifest as bwm
import world_frame
from world_frame import WORLDS, lcc_inverse, pick_zone

ZONE = "zone_m3"   # Deals Gap, TN
LL = lambda x, y: [float(v) for v in lcc_inverse(x, y, *WORLDS[ZONE])]
X0, Y0 = 300000.0, -400000.0   # somewhere in zone_m3, metres


def square(w, h, x=X0, y=Y0):
    return [LL(x, y), LL(x + w, y), LL(x + w, y + h), LL(x, y + h)]


def req(**kw):
    return {"key": None, "name": None, "polygon_ll": square(5000, 4000), "corridor_ll": None,
            "corridor_half_width_m": 1000, "start_ll": LL(X0 + 2500, Y0 + 2000), **kw}


# --- zones
assert pick_zone(*LL(X0, Y0)) == ZONE
assert pick_zone(-150.0, 61.0) == "zone_alaska" and pick_zone(-157.9, 21.4) == "zone_hawaii"
assert pick_zone(2.35, 48.85) is None   # Paris

# --- validation
assert br.validate(req()) == [], br.validate(req())
assert br.validate(req(polygon_ll=square(500, 4000), start_ll=LL(X0 + 250, Y0 + 2000)))   # < 1 km
assert br.validate(req(polygon_ll=square(31000, 4000)))                                   # > 30 km
assert br.validate(req(polygon_ll=[[2.3, 48.8], [2.4, 48.8], [2.4, 48.9]], start_ll=[2.35, 48.85]))
bowtie = [LL(X0, Y0), LL(X0 + 5000, Y0 + 4000), LL(X0 + 5000, Y0), LL(X0, Y0 + 4000)]
assert any("cross" in e for e in br.validate(req(polygon_ll=bowtie)))
assert br.validate(req(polygon_ll=[LL(X0 + 10 * k, Y0) for k in range(65)]))
assert br.validate(req(corridor_ll=[LL(X0, Y0), LL(X0 + 9000, Y0)]))                    # both set
assert br.validate(req(key="../evil"))
assert br.validate(req(start_ll=LL(X0 - 500, Y0)))                                        # start outside

# --- corridor: 10 km line, 1 km each side
line = [LL(X0, Y0), LL(X0 + 10000, Y0)]
cor = req(polygon_ll=None, corridor_ll=line, start_ll=LL(X0 + 5000, Y0))
assert br.validate(cor) == [], br.validate(cor)
from shapely.geometry import Polygon
a = Polygon(br.to_zone(br.polygon_of(cor), ZONE)).area
assert 20e6 + 2.5e6 < a < 20e6 + 3.2e6, a   # 2 x 1 km x 10 km + the two (octagonal) end caps

# --- cell grid
cells = br.plan_cells([(0, 0), (2500, 0), (2500, 1500), (0, 1500)])
assert cells == ["0_0", "0_1", "1_0", "1_1", "2_0", "2_1"], cells
assert "2_1" not in br.plan_cells([(0, 0), (2900, 0), (0, 1900)])   # triangle skips the far corner
assert br.cell_of(-0.5, 999.9) == "-1_0"

# --- job pick
grid = {f"{i}_{j}": "pending" for i in range(8) for j in range(8)}
assert br.next_job(grid, (500, 500), None, "3_3") == (["3_3"], (3000, 3000, 4000, 4000))
job, rect = br.next_job(grid, (-20000, -20000))           # far car: a 4 km block
assert len(job) == 16 and rect == (0, 0, 4000, 4000), (job, rect)
job, rect = br.next_job(grid, (4500, 4500))                # car right there: 2 km
assert len(job) == 4 and rect == (4000, 4000, 6000, 6000), (job, rect)
g2 = {**grid, "2_2": "baked"}
job, rect = br.next_job(g2, (-20000, -20000))              # block has a baked cell: shrinks
assert len(job) == 4 and "2_2" not in job and rect[2] - rect[0] == 2000, (job, rect)
# ahead beats behind: car at 4000,4500 between cells 3_4 and 4_4, heading west vs east
w, e = br.next_job(grid, (4000, 4500), (-1.0, 0.0))[0], br.next_job(grid, (4000, 4500), (1.0, 0.0))[0]
assert "3_4" in w and "4_4" not in w and "4_4" in e and "3_4" not in e, (w, e)
assert br.next_job({"0_0": "baked", "1_0": "failed"}, (0, 0)) is None

# --- estimate (contract)
e = br.estimate(100)
assert (e["first_drive_s"], e["done_s"], e["disk_mb"]) == (500, 3500, 60), e

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # --- status write/read
    st = {"key": "u_t", "cells": {"0_0": "baked", "1_0": "pending"}, "km2": 2.0, "start_cell": "0_0",
          "pass1_done": True, "started": time.time()}
    br.write_status(tmp / "s" / "status.json", st)
    back = br.read_json(tmp / "s" / "status.json")
    assert back["ready_to_drive"] and back["eta_first_drive_s"] == 0 and back["eta_done_s"] == 30.0, back
    assert not list((tmp / "s").glob("*.tmp")) and br.read_json(tmp / "nope.json") is None

    # --- coverage from a fake tile dir: 2 baked tiles, a pending and a failed cell,
    # and a cell the status calls baked with no tile (a hole)
    masks, bake = tmp / "masks", tmp / "bake"
    masks.mkdir()
    for kx, ky in ((300, 400), (301, 400)):   # cells 300_-401, 301_-401
        (masks / f"terrain_u_fake_test_tile_{kx}_{ky}.bin").write_bytes(b"")
    br.write_json(bake / "u_fake" / "status.json", {"cells": {
        "300_-401": "baked", "301_-401": "running", "302_-401": "failed", "303_-401": "pending",
        "304_-401": "baked"}})
    cfg = {"bbox_snwe": (35.0, -84.0, 35.1, -83.9), "world": ZONE, "drawn": True, "name": "Fake",
           "polygon_ll": square(5000, 1000), "terrain_tile_m": 1000}
    cov = bwm.build_coverage({"u_fake": cfg}, masks, bake)["areas"]["u_fake"]
    states = [t["state"] for t in cov["tiles"]]
    assert states == ["baked", "baked", "failed", "pending"], states
    assert cov["drawn"] and cov["name"] == "Fake" and cov["outline_ll"] == cfg["polygon_ll"]
    ll = cov["tiles"][0]["ll"]   # tile (300, 400) = east 300..301 km, north -401..-400 km
    assert all(abs(a - b) < 1e-6 for a, b in zip(ll[0], LL(300000, -401000))), ll
    assert all(abs(a - b) < 1e-6 for a, b in zip(ll[2], LL(301000, -400000))), ll

    # --- a whole run against a fake rebuild_area.py
    fake = tmp / "fake_rebuild.py"
    fake.write_text(textwrap.dedent(f"""
        import json, os, sys
        sys.path.insert(0, {str(Path(br.__file__).parent)!r})
        from world_frame import WORLDS, lcc_inverse
        root, key = sys.argv[1], os.environ["OD_AREA"]
        args = sys.argv[2:]
        with open(os.path.join(root, "jobs.txt"), "a") as f:
            f.write(" ".join(args) + "|" + os.environ.get("OD_JOB_RECT_M", "") + "\\n")
        print("[1/2] build_road_stations.py - x")
        if args[:2] == ["--pass", "1"]:
            xs = [{X0} + 100.0 * k for k in range(51)]   # an east-west road 1 km north of the bottom
            ll = [lcc_inverse(x, {Y0} + 1200.0, *WORLDS["{ZONE}"]) for x in xs]
            d = os.path.join(root, "data", "road_tiles", key + "_test"); os.makedirs(d)
            json.dump({{"segments": [{{"east_m": xs, "north_m": [{Y0} + 1200.0] * 51, "elev_m": [500.0] * 51,
                       "lon": [p[0] for p in ll], "lat": [p[1] for p in ll]}}]}}, open(os.path.join(d, "road_stations.json"), "w"))
            d = os.path.join(root, "data", "raw", key + "_test"); os.makedirs(d)
            lon, lat = lcc_inverse({X0} + 1000.0, {Y0} + 1200.0, *WORLDS["{ZONE}"])
            json.dump([{{"name": "Hamletville", "place": "hamlet", "lon": lon, "lat": lat}},
                       {{"name": "Bigtown", "place": "town", "lon": lon + 0.01, "lat": lat}},
                       {{"name": "Outside City", "place": "city", "lon": lon - 1, "lat": lat}}],
                      open(os.path.join(d, "osm_places.json"), "w"))
    """), encoding="utf-8")
    br.ROOT, br.BAKE = tmp, tmp / "data" / "bake"
    world_frame.USER_AREAS_JSON = tmp / "data" / "user_areas.json"
    br.geometry_constants = lambda key: "C"
    br.refresh_coverage = lambda: None
    cmd = [sys.executable, str(fake), str(tmp)]
    # cells 300..302 x -400..-399 (inset 100 m: lon/lat round trips never land on a cell edge)
    r = req(polygon_ll=square(2800, 1800, X0 + 100, Y0 + 100), start_ll=LL(X0 + 2500, Y0 + 1800))   # snaps 600 m south
    assert br.run(r, cmd, (0, 2000)) == 0
    st = br.read_json(next(br.BAKE.glob("u_*")) / "status.json")
    key = st["key"]
    assert st["state"] == "done" and st["ready_to_drive"] and set(st["cells"].values()) == {"baked"}, st
    assert st["name"] == "Bigtown area" and abs(br.to_zone([st["start_snapped_ll"]], ZONE)[0][1] - (Y0 + 1200)) < 1
    jobs = (tmp / "jobs.txt").read_text().splitlines()
    assert jobs[0] == "--pass 1|" and jobs[1].endswith(f"|{int(X0) + 2000},{int(Y0) + 1000},{int(X0) + 3000},{int(Y0) + 2000}"), jobs
    # pass 1, start cell, then (all within 2 km of the start) 1-2 km jobs
    assert len(jobs) == 4 and len(st["cells"]) == 6, jobs
    ua = json.loads(world_frame.USER_AREAS_JSON.read_text())
    a = ua["areas"][key]
    assert a["world"] == ZONE and a["drawn"] and a["terrain_tile_m"] == 1000 and a["name"] == "Bigtown area"
    assert ua["worlds"][ZONE] == list(WORLDS[ZONE])

    # rerun with the same inputs: pass 1 only, every cell reused by hash
    (tmp / "jobs.txt").unlink()
    import shutil
    shutil.rmtree(tmp / "data" / "road_tiles"), shutil.rmtree(tmp / "data" / "raw")
    assert br.run({**r, "key": key}, cmd, (0, 2000)) == 0
    assert len((tmp / "jobs.txt").read_text().splitlines()) == 1

    # cancel: stops before pass 2; a stale car.json + stop_after_job pauses
    for setup, want in ((lambda: (br.BAKE / "u_c").mkdir(parents=True) or (br.BAKE / "u_c" / "cancel").touch(), "cancelled"),
                        (lambda: br.write_json(br.BAKE / "car.json", {"t": time.time() - 60}), "paused")):
        for p in ("road_tiles", "raw"):
            shutil.rmtree(tmp / "data" / p, ignore_errors=True)
        shutil.rmtree(br.BAKE / "u_c", ignore_errors=True)
        setup()
        assert br.run({**r, "key": "u_c"}, cmd, (0, 2000)) == 0
        st = br.read_json(br.BAKE / "u_c" / "status.json")
        assert st["state"] == want and st["pass1_done"] and set(st["cells"].values()) == {"pending"}, st

# delete: only this key's files go, a longer key sharing the prefix stays
masks = tmp / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
masks.mkdir(parents=True)
for n in ("terrain_u_d_test_tile_0_0.bin", "relief_u_d.png", "outline_u_d_tile_1_2.bin", "relief_u_d2.png", "terrain_u_d_x_test_tile_0_0.bin"):
    (masks / n).touch()
(br.BAKE / "u_d").mkdir(parents=True)
ua = br.world_frame.load_user_areas()
ua["areas"]["u_d"] = {}
br.write_json(br.world_frame.USER_AREAS_JSON, ua)
assert br.delete("u_d") == 3 and not (br.BAKE / "u_d").exists()
assert sorted(f.name for f in masks.iterdir()) == ["relief_u_d2.png", "terrain_u_d_x_test_tile_0_0.bin"]
assert "u_d" not in br.world_frame.load_user_areas()["areas"]
assert br.runner_lock()

# pass 1 keeps a same-generation station sidecar (baked jobs' pad patches): header id round-trips
import numpy as np
import build_road_network as brn
from generation_id import pack_generation_id
sc = tmp / "roadstations_x.bin"
brn.write_station_sidecar(sc, [(np.zeros(3), np.zeros(3), np.zeros(3), np.ones(3))], pack_generation_id("0123456789abcdef"))
assert brn.sidecar_gen_id(sc) == "0123456789abcdef" and brn.sidecar_gen_id(tmp / "missing.bin") is None

print("test_bake_request: all checks passed")
