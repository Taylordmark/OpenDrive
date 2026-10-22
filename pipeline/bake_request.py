"""Drawn-area bake runner (2026-10-21, docs/area-picker-contract.md is binding,
docs/area-picker-scoping.md rounds 6-10 the design). The game writes a request
and launches this detached (cwd = repo root):

    python pipeline/bake_request.py --run data/bake/requests/<key>.json
    python pipeline/bake_request.py --retry <key>     (failed/paused/cancelled cells)

It validates the request (a corridor is buffered into the polygon), registers
the area in data/user_areas.json as a member of its fixed zone world, runs
`rebuild_area.py --pass 1` once (downloads, stations, sidecars), snaps the
start to the nearest road station, names the area after its biggest town,
then bakes pass-2 jobs on the zone's 1 km cell grid: the start cell (or a
teleport target) as a 1 km job first, then the unbaked cell nearest the car,
weighted ahead, as a 2-4 km job. A running job is never killed; car.json,
the cancel file and the quit setting are read between jobs.

Writes   data/bake/<key>/status.json (atomic), bake.log, request.json (for
         --retry); data/user_areas.json; refreshes Masks/coverage.json after
         each job (build_world_manifest.py).
A cell keeps its "baked" state across reruns while its input hash (stations
within the cell + 150 m carve margin, plus generation_id's geometry
constants) is unchanged, so a redrawn outline rebakes only the changed cells.
"""
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE))
import world_frame
from world_frame import WORLDS, lcc_forward, lcc_inverse, pick_zone

ROOT = PIPELINE.parent
BAKE = ROOT / "data" / "bake"
CELL_M = 1000                # contract: 1 km cells, "i_j" = floor(x/1000)_floor(y/1000) in zone metres
MARGIN_M = 150.0             # pass-2 carve margin, also the input-hash reach
MIN_SIDE_M, MAX_SIDE_M = 1000.0, 30000.0
MAX_VERTS = 64
BUILDING_LEVELS = 12         # per-area story cap (test_areas MAX_BUILDING_LEVELS)
CAR_STALE_S = 30.0           # car.json older than this = the game is gone
KEY_RE = re.compile(r"^u_[a-z0-9_]{1,40}$")   # the key names directories: keep it tame
STAGE_RE = re.compile(r"^\[(\d+)/(\d+)\] (\S+?)(?:\.py)?\b")
PLACE_RANK = {"city": 4, "town": 3, "village": 2, "hamlet": 1}


def estimate(km2):
    """Contract's live estimate (the menu shows the same numbers)."""
    first = 300 + 0.5 * km2 + 150
    return {"first_drive_s": first, "done_s": first + 12 * km2, "disk_mb": 50 + 1.0 * km2}


def slug_key(name):
    slug = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:24].strip("_")
    return f"u_{slug}" if slug else None


def default_name(lon, lat):
    return f"Area near {lat:.2f}, {lon:.2f}"


def to_zone(ring_ll, zone):
    return [tuple(float(v) for v in lcc_forward(lon, lat, *WORLDS[zone])) for lon, lat in ring_ll]


def to_ll(ring_m, zone):
    return [[round(float(v), 6) for v in lcc_inverse(x, y, *WORLDS[zone])] for x, y in ring_m]


def buffer_corridor(line_ll, half_width_m):
    """Clicked road line -> polygon ring [[lon, lat], ...] (scoping round 10 #1)."""
    from shapely.geometry import LineString
    lons, lats = [p[0] for p in line_ll], [p[1] for p in line_ll]
    zone = pick_zone((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)
    if zone is None:
        return None
    # quad_segs=2: rounded ends/bends at 8 points per circle, plenty for an outline
    poly = LineString(to_zone(line_ll, zone)).buffer(half_width_m, quad_segs=2)
    return to_ll(list(poly.exterior.coords)[:-1], zone)


def polygon_of(req):
    """The request's polygon ring [[lon, lat], ...] (a corridor buffered)."""
    if req.get("corridor_ll"):
        return buffer_corridor(req["corridor_ll"], float(req.get("corridor_half_width_m") or 1000))
    return req.get("polygon_ll")


def validate(req):
    """List of human-readable problems with a request; empty = OK."""
    from shapely.geometry import Point, Polygon
    errs = []
    if req.get("key") is not None and not KEY_RE.match(str(req["key"])):
        errs.append("key must look like u_<lowercase letters, digits, _>")
    pts = req.get("corridor_ll") if req.get("corridor_ll") else req.get("polygon_ll")
    if bool(req.get("corridor_ll")) == bool(req.get("polygon_ll")):
        return errs + ["set exactly one of polygon_ll and corridor_ll"]
    need = 2 if req.get("corridor_ll") else 3
    if not need <= len(pts) <= MAX_VERTS:
        return errs + [f"needs {need}..{MAX_VERTS} points, got {len(pts)}"]
    if not all(pick_zone(lon, lat) for lon, lat in pts):
        return errs + ["area must be inside the US (USGS imagery and FHWA traffic data are US-only)"]
    ring = polygon_of(req)
    zone = zone_of(ring)
    shape = Polygon(to_zone(ring, zone))
    if not shape.is_valid or shape.area <= 0:
        errs.append("polygon edges cross themselves")
    x0, y0, x1, y1 = shape.bounds
    if not all(MIN_SIDE_M <= side <= MAX_SIDE_M for side in (x1 - x0, y1 - y0)):
        errs.append(f"each side must be {MIN_SIDE_M / 1000:.0f}-{MAX_SIDE_M / 1000:.0f} km "
                    f"(this one is {(x1 - x0) / 1000:.1f} x {(y1 - y0) / 1000:.1f} km)")
    start = req.get("start_ll")
    if not start or not shape.contains(Point(to_zone([start], zone)[0])):
        errs.append("start point must be inside the area")
    return errs


def zone_of(ring_ll):
    lons, lats = [p[0] for p in ring_ll], [p[1] for p in ring_ll]
    return pick_zone((min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2)


def cell_of(x_m, y_m):
    return f"{math.floor(x_m / CELL_M)}_{math.floor(y_m / CELL_M)}"


def ij(cell):
    i, j = cell.split("_")
    return int(i), int(j)


def plan_cells(poly_m):
    """Sorted "i_j" names of the 1 km cells touching the zone-metre polygon."""
    from shapely.geometry import Polygon, box
    shape = Polygon(poly_m)
    x0, y0, x1, y1 = shape.bounds
    return sorted(f"{i}_{j}" for i in range(math.floor(x0 / CELL_M), math.floor(x1 / CELL_M) + 1)
                  for j in range(math.floor(y0 / CELL_M), math.floor(y1 / CELL_M) + 1)
                  if shape.intersects(box(i * CELL_M, j * CELL_M, (i + 1) * CELL_M, (j + 1) * CELL_M)))


def next_job(cells, car_m, heading=None, wait_cell=None, driving=False):
    """(job cells, rect_m x0,y0,x1,y1) for the next pass-2 job, or None.
    cells {"i_j": state}; car_m (x, y) zone metres; heading (east, north) unit
    vector; wait_cell = a cell the player is waiting on (start, teleport
    target): a 1 km job. Otherwise the pending cell nearest the car (one
    behind counts up to double), as the biggest aligned k km block that is
    all pending, k <= 2 while driving within 2 km of it, else k <= 4."""
    pending = {c for c, s in cells.items() if s == "pending"}
    if not pending:
        return None
    if wait_cell in pending:
        pick, k = wait_cell, 1
    else:
        cx, cy = car_m

        def cost(c):
            i, j = ij(c)
            dx, dy = (i + 0.5) * CELL_M - cx, (j + 0.5) * CELL_M - cy
            d = math.hypot(dx, dy)
            ahead = (dx * heading[0] + dy * heading[1]) / d if heading and d else 1.0
            return d * (1.5 - 0.5 * ahead), c
        pick = min(pending, key=cost)
        k = 2 if driving and cost(pick)[0] < 2 * CELL_M else 4
    i, j = ij(pick)
    for k in range(k, 0, -1):
        bi, bj = i // k * k, j // k * k
        block = [f"{a}_{b}" for a in range(bi, bi + k) for b in range(bj, bj + k) if f"{a}_{b}" in cells]
        if all(c in pending for c in block):
            break
    xs, ys = [ij(c)[0] for c in block], [ij(c)[1] for c in block]
    return sorted(block), (min(xs) * CELL_M, min(ys) * CELL_M, (max(xs) + 1) * CELL_M, (max(ys) + 1) * CELL_M)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)   # the game never reads half a file


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def sample_elev_range(polygon, z=10):
    """Real elevation span over the polygon's bbox from Terrarium (cached), padded."""
    import numpy as np
    from terrain_common import fetch_terrarium_tile, lonlat_to_tilepx, terrarium_to_elevation
    lons, lats = [p[0] for p in polygon], [p[1] for p in polygon]
    (x0, y0), (x1, y1) = (lonlat_to_tilepx(min(lons), max(lats), z), lonlat_to_tilepx(max(lons), min(lats), z))
    lo, hi = np.inf, -np.inf
    for tx in range(int(x0 // 256), int(x1 // 256) + 1):
        for ty in range(int(y0 // 256), int(y1 // 256) + 1):
            e = terrarium_to_elevation(fetch_terrarium_tile(z, tx, ty))
            lo, hi = min(lo, float(e.min())), max(hi, float(e.max()))
    # same idea as every hand entry: generous, never clipping a real peak
    return (math.floor(min(lo, 0.0) - 100.0), math.ceil(hi + 600.0))


def register(key, cfg, zone):
    """Merge the area into data/user_areas.json (test_areas/world_frame read it)."""
    data = world_frame.load_user_areas()
    data["worlds"][zone] = list(WORLDS[zone])
    data["areas"][key] = cfg
    write_json(world_frame.USER_AREAS_JSON, data)


def stations(key):
    """road_stations.json segments of the area (pass 1), or []."""
    return (read_json(ROOT / "data" / "road_tiles" / f"{key}_test" / "road_stations.json") or {}).get("segments", [])


def snap_start(segs, start_ll, zone):
    """Nearest road station (lon, lat) to start_ll, or None without stations."""
    sx, sy = to_zone([start_ll], zone)[0]
    best = min(((math.hypot(x - sx, y - sy), (lon, lat)) for s in segs
                for x, y, lon, lat in zip(s["east_m"], s["north_m"], s["lon"], s["lat"])), default=None)
    return [round(best[1][0], 6), round(best[1][1], 6)] if best else None


def biggest_place(key, ring_ll, zone):
    """Name of the biggest pass-1 OSM place inside the polygon (population,
    then place class, then nearest the centre), or None."""
    from shapely.geometry import Point, Polygon
    places = read_json(ROOT / "data" / "raw" / f"{key}_test" / "osm_places.json") or []
    shape = Polygon(to_zone(ring_ll, zone))
    c = shape.centroid
    inside = [(p, Point(to_zone([(p["lon"], p["lat"])], zone)[0])) for p in places]
    inside = [(p, pt) for p, pt in inside if shape.contains(pt)]
    if not inside:
        return None

    def size(item):
        p, pt = item
        pop = str(p.get("population", "")).replace(",", "")
        return (int(pop) if pop.isdigit() else 0, PLACE_RANK.get(p.get("place"), 0), -pt.distance(c))
    return max(inside, key=size)[0]["name"]


def cell_hashes(segs, cells, constants):
    """{cell: hash of the station points within cell + MARGIN_M, plus constants}."""
    pts = [(round(x, 2), round(y, 2), round(z, 2)) for s in segs
           for x, y, z in zip(s["east_m"], s["north_m"], s["elev_m"])]
    out = {}
    for c in cells:
        i, j = ij(c)
        x0, y0 = i * CELL_M - MARGIN_M, j * CELL_M - MARGIN_M
        x1, y1 = x0 + CELL_M + 2 * MARGIN_M, y0 + CELL_M + 2 * MARGIN_M
        mine = [p for p in pts if x0 <= p[0] < x1 and y0 <= p[1] < y1]
        out[c] = hashlib.sha256(repr((constants, mine)).encode()).hexdigest()[:16]
    # ponytail: stations only (roads drive every carve/pad/wall); buildings and
    # other clipped OSM aren't hashed - add the cell's OSM elements if a fresher
    # pull ever needs to rebake buildings by itself
    return out


def geometry_constants(key):
    """generation_id's geometry-constant hash for the area, no stations (a
    child process: test_areas binds the active area at import)."""
    code = ("import generation_id as g; from pathlib import Path; "
            "g.STATIONS_PATH = Path('<none>'); print(g.compute_generation_id())")
    r = subprocess.run([sys.executable, "-c", code], cwd=PIPELINE, capture_output=True, text=True,
                       env={**os.environ, "OD_AREA": key})
    return r.stdout.strip() or "?"


def refresh_coverage():
    subprocess.run([sys.executable, str(PIPELINE / "build_world_manifest.py")], cwd=ROOT,
                   capture_output=True, check=False)


def read_car(key):
    """(car.json or {}, the car if it's in this area else None, game alive,
    driving = low priority). Never written = run by hand: alive, menu."""
    raw = read_json(BAKE / "car.json")
    if raw is None:
        return {}, None, True, False
    alive = time.time() - float(raw.get("t", 0)) <= CAR_STALE_S
    # ponytail: a car in another drawn area doesn't switch the runner to it yet
    # (scoping round 7); the game should only launch one runner at a time
    return raw, (raw if raw.get("area") == key else None), alive, alive and raw.get("mode") == "drive"


def run_job(argv, env_extra, low, log, status, status_path, rebuild_cmd):
    """One rebuild_area.py job; returns (exit code, last output lines)."""
    env = {**os.environ, "PYTHONUTF8": "1", **env_extra}
    kw = {}
    if low:   # driving: stay out of the game's way (Windows children inherit it)
        env["OD_BAKE_WORKERS"] = str(max(1, (os.cpu_count() or 4) // 4))
        if os.name == "nt":
            kw["creationflags"] = subprocess.BELOW_NORMAL_PRIORITY_CLASS
        else:
            kw["preexec_fn"] = lambda: os.nice(10)
    cmd = list(rebuild_cmd) + argv
    log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)} {env_extra} low={low} ===\n")
    # ponytail: priority is set per job; a mode change mid-job applies from the next job
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", cwd=ROOT, env=env, **kw)
    tail = []
    for line in proc.stdout:
        log.write(line)
        log.flush()
        tail = (tail + [line.rstrip()])[-8:]
        m = STAGE_RE.match(line)
        if m:
            status["stage"] = m[3]
            write_status(status_path, status)
    return proc.wait(), tail


def write_status(path, status):
    """Fill in the derived fields (ready_to_drive, ETAs) and write atomically."""
    cells = status["cells"]
    km2 = status["km2"]
    left = sum(s != "baked" for s in cells.values()) / max(1, len(cells))
    est = estimate(km2)
    status["ready_to_drive"] = status["pass1_done"] and cells.get(status["start_cell"]) == "baked"
    status["eta_first_drive_s"] = 0.0 if status["ready_to_drive"] else round(
        max(0.0, est["first_drive_s"] - (time.time() - status["started"])), 1)
    status["eta_done_s"] = round(status["eta_first_drive_s"] + 12 * km2 * left, 1)
    status["updated"] = time.time()
    write_json(path, status)


def run(req, rebuild_cmd=None, elev_range_m=None):
    """Bake a validated request; returns the exit code. rebuild_cmd /
    elev_range_m are swappable for tests (no network, no real bake)."""
    from shapely.geometry import Polygon
    rebuild_cmd = rebuild_cmd or [sys.executable, str(PIPELINE / "rebuild_area.py")]
    ring = polygon_of(req)
    zone = zone_of(ring)
    lons, lats = [p[0] for p in ring], [p[1] for p in ring]
    key = req.get("key") or slug_key(req.get("name")) or slug_key(f"{min(lats):.3f}n_{-min(lons):.3f}w")
    d = BAKE / key
    status_path, cancel = d / "status.json", d / "cancel"
    write_json(d / "request.json", {**req, "key": key})
    poly_m = to_zone(ring, zone)
    cells = plan_cells(poly_m)
    prev = read_json(status_path) or {}
    old_states, old_hash = prev.get("cells", {}), prev.get("cell_hash", {})
    status = {"key": key, "name": req.get("name") or prev.get("name") or default_name(*req["start_ll"]),
              "state": "pass1", "stage": None, "pass1_done": False, "ready_to_drive": False,
              "start_snapped_ll": None, "cell_m": CELL_M,
              # everything not baked is retried: --run on the same key resumes like --retry
              "cells": {c: "baked" if old_states.get(c) == "baked" else "pending" for c in cells},
              "cell_hash": old_hash, "error": None, "zone": zone, "km2": Polygon(poly_m).area / 1e6,
              "started": time.time()}
    status["start_cell"] = cell_of(*to_zone([req["start_ll"]], zone)[0])

    # the area config: bake the cell-snapped rect (tiles touching the polygon bake whole)
    i0, j0 = (min(ij(c)[n] for c in cells) * CELL_M for n in (0, 1))
    i1, j1 = ((max(ij(c)[n] for c in cells) + 1) * CELL_M for n in (0, 1))
    xs, ys = [i0, i1, i1, i0], [j0, j0, j1, j1]
    rect_ll = to_ll(list(zip(xs, ys)), zone)
    elev = elev_range_m or sample_elev_range(ring)
    cfg = dict(bbox_snwe=[min(p[1] for p in rect_ll), min(p[0] for p in rect_ll),
                          max(p[1] for p in rect_ll), max(p[0] for p in rect_ll)],
               background_bbox_snwe=None, world=zone, drawn=True, name=status["name"],
               polygon_ll=ring, start_ll=list(req["start_ll"]), terrain_tile_m=CELL_M,
               elev_plausible_m=list(elev), max_building_levels=BUILDING_LEVELS)
    register(key, cfg, zone)
    write_status(status_path, status)

    with open(d / "bake.log", "a", encoding="utf-8") as log:
        rc, tail = run_job(["--pass", "1"], {"OD_AREA": key, "OD_PASS": "1"}, read_car(key)[3],
                           log, status, status_path, rebuild_cmd)
        if rc:
            status.update(state="failed", error="\n".join(tail))
            write_status(status_path, status)
            return 1
        segs = stations(key)
        snapped = snap_start(segs, req["start_ll"], zone)
        if snapped and cell_of(*to_zone([snapped], zone)[0]) in status["cells"]:
            status["start_snapped_ll"] = snapped
            status["start_cell"] = cell_of(*to_zone([snapped], zone)[0])
            cfg["start_ll"] = snapped
        if not req.get("name"):
            place = biggest_place(key, ring, zone)
            status["name"] = cfg["name"] = f"{place} area" if place else status["name"]
        register(key, cfg, zone)
        hashes = cell_hashes(segs, cells, geometry_constants(key))
        for c, h in hashes.items():
            if status["cells"][c] == "baked" and old_hash.get(c) != h:
                status["cells"][c] = "pending"   # its inputs changed: rebake
        status.update(state="pass2", pass1_done=True, stage=None)
        write_status(status_path, status)

        while True:
            raw, car, alive, low = read_car(key)
            if cancel.exists():
                cancel.unlink(missing_ok=True)
                status["state"] = "cancelled"
                break
            if not alive and raw.get("quit_behaviour", "stop_after_job") == "stop_after_job":
                status["state"] = "paused"
                break
            car_m = heading = target = None
            if car:   # UE cm: X = east, Y = -north; yaw 0 = +X, positive toward +Y (south)
                car_m = (car["x_cm"] / 100.0, -car["y_cm"] / 100.0)
                h = math.radians(car.get("heading_deg", 0.0))
                heading = (math.cos(h), -math.sin(h))
                if car.get("target_cm"):
                    target = cell_of(car["target_cm"][0] / 100.0, -car["target_cm"][1] / 100.0)
            if car_m is None:   # menu with no car here yet: grow outward from the start
                car_m = to_zone([status["start_snapped_ll"] or req["start_ll"]], zone)[0]
            ready = status["cells"].get(status["start_cell"]) == "baked"
            job = next_job(status["cells"], car_m, heading, target or (None if ready else status["start_cell"]),
                           driving=low and car is not None)
            if job is None:
                status["state"] = "failed" if "failed" in status["cells"].values() else "done"
                break
            job_cells, rect = job
            for c in job_cells:
                status["cells"][c] = "running"
            write_status(status_path, status)
            rect_s = ",".join(str(int(v)) for v in rect)
            rc, tail = run_job(["--pass", "2", "--rect", rect_s],
                               {"OD_AREA": key, "OD_PASS": "2", "OD_JOB_RECT_M": rect_s}, low,
                               log, status, status_path, rebuild_cmd)
            for c in job_cells:
                status["cells"][c] = "failed" if rc else "baked"
                if not rc:
                    status["cell_hash"][c] = hashes[c]
            status["error"] = "\n".join(tail) if rc else status["error"]
            status["stage"] = None
            write_status(status_path, status)
            refresh_coverage()
        write_status(status_path, status)
    refresh_coverage()
    return 0 if status["state"] != "failed" else 1


def main(argv):
    if len(argv) == 3 and argv[1] == "--run":
        req = read_json(argv[2]) or {}
    elif len(argv) == 3 and argv[1] == "--retry" and KEY_RE.match(argv[2]):
        req = read_json(BAKE / argv[2] / "request.json") or {}
    else:
        sys.exit(__doc__)
    errs = validate(req)
    if errs:
        key = req.get("key") if KEY_RE.match(str(req.get("key"))) else None
        if key:   # the game shows it on the progress card
            write_json(BAKE / key / "status.json", {"key": key, "name": req.get("name"), "state": "failed",
                                                    "pass1_done": False, "ready_to_drive": False,
                                                    "cells": {}, "error": "; ".join(errs), "updated": time.time()})
        print("\n".join(errs))
        return 2
    return run(req)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
