"""Garage-menu backdrop spots (2026-10-05): one parked-car spot per baked
area -> Content/OpenDrive/Masks/menu_spots.json, merged per area:

    {"summit_county": {"car_cm": [x, y, z], "car_yaw_deg": .., "view_yaw_deg": ..,
                       "source": "auto" | "override",
                       "starts": {"<turnout>": {"car_cm": [x, y, z], "car_yaw_deg": ..}}}, ...}
    ("starts", 2026-10-14: the Turnouts, turnouts() below)

UE cm in the area's own frame, as the baked tiles: X = east*100,
Y = -north*100, Z = (road surface - ORIGIN_HEIGHT_M)*100. Yaws are UE yaw
(0 = +X east, 90 = +Y south), i.e. compass heading - 90. The camera stands
behind the car (opposite view_yaw) and looks through it into the vista.

Auto-pick, on the SHIPPED station positions (roadstations_<area>.bin, incl.
the junction-pad XY snap - verify_road_fit._shipped_stations):
  1. candidates every 50 m; drop any within 60 m of a junction (another
     segment's at-grade station within 8 m, a segment end, or
     in_junction_box), on a motorway or a ramp, with a bridge/tunnel
     within +-30 m, a grade >= 8 %
     or a heading change >= 10 deg over those 60 m. (2026-10-16: no longer
     kept near an osm_places name - the spot is a wild viewpoint now.)
  2. each tries 6 view headings (perpendicular to the road both sides plus
     the 4 diagonals). The shipped terrain tiles, sampled on a 25 m grid,
     score a wedge +-35 deg, 150-1000 m out, on terrain visible from the
     eye (1.5 m up, nearer ground occludes along each ray): relief
     (p90 - p10) plus how far its far half (>= 500 m) rises above the eye,
     capped at 270 m. Rejected: >20 % void, terrain above 10 deg within
     100 m or above 20 deg anywhere (a wall, no vista), or the 25 m of
     ground behind the car (camera side) leaving +-4 m of the road.
  3. strict pass (2026-10-16, the menu vista draws no buildings or cars, so
     the view must have none): best score first, over the camera's whole
     90 deg FOV, line of sight over the terrain (strict_view). Rejected: any
     building in sight within 3 km (roof 8 m up), a town on the horizon (25+
     buildings in sight 3-10 km), a busy road (motorway/trunk/primary, a car
     1.5 m up) in sight 150 m - 5 km. Each building in sight 3-10 km below
     that still costs 2 (wild over semi-rural). Highest total wins; when
     every view is rejected, the least-bad one (REJECT_COST per flag).
     The car faces the camera three-quarter, nose toward screen right (the
     menu side). This one spot is the area's menu vista for every turnout.
  4. "sea_z_cm" (sea level in the area's frame, = -ORIGIN_HEIGHT_M*100) is
     added only when >= 10 % of the view wedge's terrain samples, 150 m -
     20 km out (off-tile excluded), sit at or below 2 m - the runtime then
     draws a sea plane. At or below, not within: the DEM carries
     bathymetry, so the bay floor reads -5..-100 m. Inland/high areas never
     get one (a plane far below would show through void as a fake floor).

Usage:  python build_menu_spots.py              (active area, OD_AREA)
        python build_menu_spots.py --all        (every area whose bake is complete)
        python build_menu_spots.py oahu ...     (those areas)
        add --png DIR for a top-down sanity plot per area,
        --starts to only refresh each area's "starts" (turnouts) and keep its spot,
        --dry to print the pick and why without writing menu_spots.json
        (pipeline/check_menu_spots.py runs every area that way).
"""
import json
import os
import struct
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np

from road_mesh_common import STATION_SPACING
from test_areas import AREA_NAME, AREAS, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, owns, to_local

# area -> (lat, lon, car heading, view heading), headings in deg from north.
# Snapped to the nearest road station.
OVERRIDES = {
    # "summit_county": (39.6303, -106.0434, 180.0, 270.0),
    # Hyde St by the cable-car turnaround, Beach..North Point: north over the
    # bay, Alcatraz/Angel Island/Marin left of the car; uphill = car_heading()
    "sf_russian_hill": (37.8062, -122.42047, 171.0, 10.0),
}

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
SPOTS_PATH = MASKS / "menu_spots.json"
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"

CAND_SPACING_M = 50.0
JUNCTION_CLEAR_M = 60.0
JUNCTION_TOUCH_M = 8.0      # another segment's station this close = junction
JUNCTION_DZ_M = 3.0         # ...unless this far apart in z (grade-separated)
WINDOW_M = 30.0             # +- straightness/grade/structure window
MAX_GRADE = 0.08
# ponytail: one fixed fallback rung for street grids too tight/steep for the
# rules above (sf_russian_hill: ~125 m blocks at up to 16 %); add rungs if
# another area comes up empty.
RELAXED = (30.0, 0.20)      # (junction clear m, max grade)
MAX_TURN_DEG = 10.0
VIEW_OFFSETS_DEG = (90.0, -90.0, 45.0, -45.0, 135.0, -135.0)
WEDGE_HALF_DEG = 35.0
WEDGE_NEAR_M, WEDGE_FAR_M, FAR_HALF_M = 150.0, 1000.0, 500.0
EYE_M = 1.5
BLOCK_M, BLOCK_DEG = 100.0, 10.0
WALL_DEG = 20.0             # any wedge terrain this far above the eye fills the frame: no vista
RISE_CAP_M = 270.0          # ~15 deg at 1 km; more is wall, not backdrop
NO_PARK = ("motorway", "motorway_link", "trunk_link", "primary_link", "secondary_link")
BACK_M, BACK_TOL_M = 25.0, 4.0
MAX_VOID = 0.2
# strict pass (step 3)
FOV_HALF_DEG = 45.0         # the vista camera's 90 deg FOV (ODMainMenu.cpp VistaHalfAngleDeg)
SIGHT_STEP_M = 25.0         # = GRID_M
BLDG_CLEAR_M = 3000.0
BLDG_TOP_M = 8.0            # centroids carry no height
TOWN_M, TOWN_MIN = 10000.0, 25
FAR_BLDG_COST = 2.0
BUSY = ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link")
BUSY_M, BUSY_TOP_M = 5000.0, 1.5
BUSY_EVERY = 10             # stations: one busy-road sample per 50 m
MAX_STRICT = 4000           # views checked, best score first
REJECT_COST = 1e4           # rejects sink below every clean view, so an all-rejected area still gets its least-bad one
GRID_M = 25.0
# ponytail: below-sea inland basins (Salton Sink, Death Valley) would pass;
# add an OSM coastline check if an area there is ever baked.
SEA_M, SEA_SHARE, SEA_FAR_M = 2.0, 0.10, 20000.0
# turnouts(): place node -> road
NEAR_ROAD_M = 300.0         # a place farther from any road is no turnout
SNAP_M = 600.0
DEDUP_M = 300.0
MAIN_ROADS = ("trunk", "primary", "secondary", "tertiary")


def candidates(e, n, z, seg, structural, in_box, clear_m=JUNCTION_CLEAR_M, max_grade=MAX_GRADE):
    """Station indices fit to park on (module docstring, step 1). Arrays are
    all segments concatenated in order, seg = segment id per station."""
    from scipy.spatial import cKDTree
    i = np.arange(len(e))
    first = np.r_[True, seg[1:] != seg[:-1]]
    last = np.r_[seg[1:] != seg[:-1], True]
    start = np.maximum.accumulate(np.where(first, i, 0))
    end = np.minimum.accumulate(np.where(last, i, len(e))[::-1])[::-1]
    w = int(round(WINDOW_M / STATION_SPACING))
    ok = ((i - start) % int(round(CAND_SPACING_M / STATION_SPACING)) == 0) \
        & (i - w >= start) & (i + w <= end)
    a, b = np.clip(i - w, 0, len(e) - 1), np.clip(i + w, 0, len(e) - 1)
    grade = np.abs(z[b] - z[a]) / np.maximum(np.hypot(e[b] - e[a], n[b] - n[a]), 1.0)
    turn = np.degrees(np.arctan2(e[b] - e[i], n[b] - n[i]) - np.arctan2(e[i] - e[a], n[i] - n[a]))
    cs = np.r_[0, np.cumsum(structural)]
    ok &= (grade < max_grade) & (np.abs((turn + 180.0) % 360.0 - 180.0) < MAX_TURN_DEG) \
        & (cs[b + 1] - cs[a] == 0)

    p = cKDTree(np.c_[e, n]).query_pairs(JUNCTION_TOUCH_M, output_type="ndarray")
    p = p[(seg[p[:, 0]] != seg[p[:, 1]]) & (np.abs(z[p[:, 0]] - z[p[:, 1]]) < JUNCTION_DZ_M)]
    junc = first | last | in_box
    junc[p.ravel()] = True
    idx = np.where(ok)[0]
    d, _ = cKDTree(np.c_[e[junc], n[junc]]).query(np.c_[e[idx], n[idx]])
    return idx[d >= clear_m]


def score_views(h, e, n, z, head_deg):
    """Score a view wedge per (spot, compass heading) - step 2 above.
    h(e, n) -> ground m, NaN = void. Returns dict of per-view arrays."""
    eye = z + EYE_M
    a = np.radians(head_deg)[:, None] + np.radians(np.linspace(-WEDGE_HALF_DEG, WEDGE_HALF_DEG, 11))

    # one ray set 10-1000 m: the near band only occludes and blocks
    d = np.r_[np.arange(10.0, WEDGE_NEAR_M, 10.0), np.arange(WEDGE_NEAR_M, WEDGE_FAR_M + 1.0, 50.0)]
    gh = h(e[:, None, None] + np.sin(a)[..., None] * d, n[:, None, None] + np.cos(a)[..., None] * d)
    ang = np.degrees(np.arctan2(gh - eye[:, None, None], d))          # (M, rays, dists)
    shadow = np.maximum.accumulate(np.nan_to_num(ang, nan=-90.0), axis=2)
    visible = ang >= np.concatenate([np.full(ang.shape[:2] + (1,), -90.0), shadow[..., :-1]], axis=2)
    far = d >= WEDGE_NEAR_M
    void = np.isnan(gh[..., far]).mean((1, 2)) > MAX_VOID
    vis = np.where(visible[..., far], gh[..., far], np.nan).reshape(len(e), -1)
    vis_far = np.where(visible & (d >= FAR_HALF_M), gh, np.nan).reshape(len(e), -1)
    with warnings.catch_warnings():   # all-NaN wedges are already void
        warnings.simplefilter("ignore", RuntimeWarning)
        relief = np.nanpercentile(vis, 90, 1) - np.nanpercentile(vis, 10, 1)
        rise = np.clip(np.nanpercentile(vis_far, 90, 1) - eye, 0.0, RISE_CAP_M)
    blocked = np.any(ang[..., d <= BLOCK_M] > BLOCK_DEG, (1, 2)) | np.any(ang > WALL_DEG, (1, 2))
    back_d = np.arange(5.0, BACK_M + 1.0, 5.0)
    hr = np.radians(head_deg)[:, None]
    back = h(e[:, None] - np.sin(hr) * back_d, n[:, None] - np.cos(hr) * back_d)
    cliff = ~np.all(np.abs(back - z[:, None]) <= BACK_TOL_M, 1)   # NaN counts as a drop
    reject = void | blocked | cliff
    score = np.nan_to_num(relief + rise, nan=0.0) - REJECT_COST * reject
    return dict(score=score, relief=relief, rise=rise, void=void, blocked=blocked, cliff=cliff)


def sightlines(h, e0, n0, eye, head_deg, far_m):
    """Terrain horizon over the camera FOV from (e0, n0, eye m): rays every
    1 deg, samples every SIGHT_STEP_M to far_m. shadow[ray, k] = highest
    elevation angle (deg) of the ground out to sample k (void = -90)."""
    az = np.radians(head_deg + np.arange(-FOV_HALF_DEG, FOV_HALF_DEG + 0.5, 1.0))[:, None]
    d = np.arange(SIGHT_STEP_M, far_m + 1.0, SIGHT_STEP_M)
    ang = np.degrees(np.arctan2(h(e0 + np.sin(az) * d, n0 + np.cos(az) * d) - eye, d))
    return np.maximum.accumulate(np.nan_to_num(ang, nan=-90.0), axis=1)


def in_sight(shadow, e0, n0, eye, head_deg, pe, pn, ptop):
    """-> (visible bool, distance m) per point whose top is ptop m: inside the
    FOV and its top at or above the ground horizon before it (NaN top = no)."""
    de, dn = pe - e0, pn - n0
    dist = np.hypot(de, dn)
    off = (np.degrees(np.arctan2(de, dn)) - head_deg + 180.0) % 360.0 - 180.0
    k = np.floor(dist / SIGHT_STEP_M).astype(int) - 1                  # last sample short of the point
    inside = (np.abs(off) <= FOV_HALF_DEG) & (k < shadow.shape[1])
    ray = np.clip(np.rint(off + FOV_HALF_DEG).astype(int), 0, shadow.shape[0] - 1)
    hor = np.where(k >= 0, shadow[ray, np.clip(k, 0, shadow.shape[1] - 1)], -90.0)
    with np.errstate(invalid="ignore"):
        vis = inside & (np.degrees(np.arctan2(ptop - eye, np.maximum(dist, 1e-3))) >= hor)
    return vis, dist


def strict_view(h, e0, n0, z0, head_deg, bldg, busy):
    """Step 3: what the camera sees that the vista won't draw. bldg/busy are
    (cKDTree, top m per point) or None. -> dict of counts, flags, penalty."""
    eye = z0 + EYE_M
    shadow = sightlines(h, e0, n0, eye, head_deg, max(TOWN_M, BUSY_M))
    out = dict(near_bldg=0, far_bldg=0, busy=0, nearest_bldg_m=float("nan"))
    if bldg is not None:
        tree, top = bldg
        ids = tree.query_ball_point((e0, n0), TOWN_M)
        if ids:
            vis, dist = in_sight(shadow, e0, n0, eye, head_deg, *tree.data[ids].T, top[ids])
            near = vis & (dist < BLDG_CLEAR_M)
            out.update(near_bldg=int(near.sum()), far_bldg=int((vis & ~near).sum()),
                       nearest_bldg_m=float(dist[vis].min()) if vis.any() else float("nan"))
    if busy is not None:
        tree, top = busy
        ids = tree.query_ball_point((e0, n0), BUSY_M)
        if ids:
            vis, dist = in_sight(shadow, e0, n0, eye, head_deg, *tree.data[ids].T, top[ids])
            out["busy"] = int((vis & (dist > WEDGE_NEAR_M)).sum())
    out["flags"] = [f for f, bad in (("building <3 km", out["near_bldg"]), ("town", out["far_bldg"] >= TOWN_MIN),
                                     ("busy road", out["busy"])) if bad]
    out["penalty"] = REJECT_COST * len(out["flags"]) + FAR_BLDG_COST * out["far_bldg"]
    return out


def sea_share(h, e, n, view_deg):
    """Share of on-tile wedge samples (150 m - SEA_FAR_M) at or below SEA_M - step 4."""
    a = np.radians(view_deg + np.linspace(-WEDGE_HALF_DEG, WEDGE_HALF_DEG, 11))[:, None]
    d = np.arange(WEDGE_NEAR_M, SEA_FAR_M + 1.0, 50.0)
    g = h(e + np.sin(a) * d, n + np.cos(a) * d)
    g = g[~np.isnan(g)]
    return float((g <= SEA_M).mean()) if g.size else 0.0


def car_heading(road_deg, view_deg):
    """Whichever way along the road puts the car three-quarter toward the
    camera, nose to screen right: closest to view + 150."""
    off = lambda hd: abs((hd - view_deg - 150.0 + 180.0) % 360.0 - 180.0)
    return road_deg if off(road_deg) <= off(road_deg + 180.0) else (road_deg + 180.0) % 360.0


def ue_yaw(heading_deg):
    return round(float((heading_deg - 90.0 + 180.0) % 360.0 - 180.0), 2)


# ------------------------------------------------------------------ area I/O

def bake_complete(area):
    """Stations, sidecar and terrain tiles present, and the sidecar's
    generation id among the terrain tiles' (a mid-bake area mixes them)."""
    from bin_tile_io import parse_terrain_tile_bin
    from build_world_manifest import tile_keys
    from experimental_junction_pads import read_station_sidecar
    sidecar = MASKS / f"roadstations_{area}.bin"
    keys = tile_keys(area)
    if not (keys and sidecar.exists()
            and (ROOT / "data" / "road_tiles" / f"{area}_test" / "road_stations.json").exists()):
        return False
    gens = {(parse_terrain_tile_bin(MASKS / f"terrain_{area}_test_tile_{x}_{y}.bin") or {}).get("generation_id")
            for x, y in keys[::max(1, len(keys) // 20)]}
    return read_station_sidecar(sidecar)[0] in gens


def height_grid():
    """Shipped terrain tiles sampled every GRID_M. Returns (h, H, x0_cm, y0_cm):
    h(e, n) -> absolute ground m (bilinear, NaN off the tiles); H[row=Y, col=X]."""
    from scipy.ndimage import map_coordinates
    from bin_tile_io import parse_terrain_tile_bin
    from build_world_manifest import tile_keys
    from road_mesh_common import sample_mesh_heights
    keys = tile_keys(AREA_NAME)
    T, g = TERRAIN_TILE_SIZE_CM, GRID_M * 100.0
    k = int(round(T / g))
    kx0, ky0 = min(x for x, _ in keys), min(y for _, y in keys)
    H = np.full(((max(y for _, y in keys) - ky0 + 1) * k, (max(x for x, _ in keys) - kx0 + 1) * k), np.nan)
    off = (np.arange(k) + 0.5) * g
    for kx, ky in keys:
        t = parse_terrain_tile_bin(MASKS / f"terrain_{AREA_NAME}_test_tile_{kx}_{ky}.bin")
        if t is None:
            continue
        X, Y = np.meshgrid(kx * T + off, ky * T + off)
        H[(ky - ky0) * k:(ky - ky0 + 1) * k, (kx - kx0) * k:(kx - kx0 + 1) * k] = sample_mesh_heights(
            np.array(t["verts"], float).reshape(-1, 3), np.array(t["tris"], np.int64).reshape(-1, 3),
            X.ravel(), Y.ravel()).reshape(k, k)
    H = H / 100.0 + ORIGIN_HEIGHT_M
    x0, y0 = kx0 * T, ky0 * T

    def h(e, n):
        e, n = np.asarray(e, float), np.asarray(n, float)
        c = np.stack([((-n * 100.0 - y0) / g - 0.5).ravel(), ((e * 100.0 - x0) / g - 0.5).ravel()])
        return map_coordinates(H, c, order=1, cval=np.nan).reshape(e.shape)
    return h, H, x0, y0


def load_stations():
    from verify_road_fit import _shipped_stations
    segs = json.loads(STATIONS_PATH.read_text())["segments"]
    cat = lambda key, dflt=False: np.concatenate(
        [np.array(s.get(key, [dflt] * len(s["east_m"]))) for s in segs])
    e, n, z = _shipped_stations(cat("east_m"), cat("north_m"), cat("elev_m"))
    return dict(e=e, n=n, z=z, lon=cat("lon"), lat=cat("lat"),
                seg=np.concatenate([np.full(len(s["east_m"]), k) for k, s in enumerate(segs)]),
                structural=cat("is_bridge").astype(bool) | cat("is_tunnel").astype(bool),
                cls=cat("highway_class", ""), no_park=np.isin(cat("highway_class", ""), NO_PARK),
                in_box=cat("in_junction_box").astype(bool),
                name=[nm for s in segs for nm in s["road_name"]])


def load_buildings():
    path = MASKS / f"building_centroids_{AREA_NAME}_test.bin"
    if not path.exists():
        return None
    raw = path.read_bytes()
    (cnt,) = struct.unpack_from("<I", raw, 4)
    xy = np.frombuffer(raw, "<f4", cnt * 2, 8).reshape(-1, 2).astype(float) / 100.0
    return np.c_[xy[:, 0], -xy[:, 1]]


def strict_inputs(st, h, bldg):
    """(building, busy-road) (cKDTree, top m) pairs for strict_view."""
    from scipy.spatial import cKDTree
    b = None
    if bldg is not None and len(bldg):
        b = (cKDTree(bldg), h(bldg[:, 0], bldg[:, 1]) + BLDG_TOP_M)
    k = np.where(np.isin(st["cls"], BUSY))[0][::BUSY_EVERY]
    r = (cKDTree(np.c_[st["e"][k], st["n"][k]]), st["z"][k] + BUSY_TOP_M) if len(k) else None
    return b, r


def describe(s, sv):
    near = sv["nearest_bldg_m"]
    return (f"relief {s['relief']:.0f} m, rise {s['rise']:.0f} m; in sight: {sv['near_bldg']} building(s) <3 km, "
            f"{sv['far_bldg']} at 3-10 km, {sv['busy']} busy-road sample(s)"
            + (f", nearest building in sight {near:.0f} m" if near == near else ""))


def pick(st, h, bldg):
    """-> (station index, car heading, view heading, score dict, source, why) for this area."""
    e, n, z = st["e"], st["n"], st["z"]
    strict = strict_inputs(st, h, bldg)
    # local frame from lon/lat: green_valley's bake predates the LCC frame (~90 m off it)
    le, ln = to_local(st["lon"], st["lat"])
    if AREA_NAME in OVERRIDES:
        lat, lon, car_h, view_h = OVERRIDES[AREA_NAME]
        oe, on = to_local(lon, lat)
        i = int(np.argmin(np.hypot(le - oe, ln - on)))
        s = {k: v[0] for k, v in score_views(h, e[[i]], n[[i]], z[[i]], np.array([float(view_h)])).items()}
        sv = strict_view(h, e[i], n[i], z[i], float(view_h), *strict)
        return i, car_h % 360.0, view_h % 360.0, s, "override", "hand-picked override; " + describe(s, sv)

    for clear_m, max_grade in ((JUNCTION_CLEAR_M, MAX_GRADE), RELAXED):
        idx = candidates(e, n, z, st["seg"], st["structural"], st["in_box"], clear_m, max_grade)
        idx = idx[owns(e[idx] * 100.0, -n[idx] * 100.0) & ~st["no_park"][idx]]
        if len(idx):
            break
        print(f"no candidates at {clear_m:.0f} m junction clearance / {max_grade:.0%} grade - relaxing")
    print(f"{len(idx)} candidate station(s) after filters")
    if not len(idx):
        return None
    w = int(round(WINDOW_M / STATION_SPACING))
    road_h = np.degrees(np.arctan2(e[idx + w] - e[idx - w], n[idx + w] - n[idx - w])) % 360.0
    ci = np.repeat(idx, len(VIEW_OFFSETS_DEG))
    rh = np.repeat(road_h, len(VIEW_OFFSETS_DEG))
    vh = (rh + np.tile(VIEW_OFFSETS_DEG, len(idx))) % 360.0
    parts = [score_views(h, e[c], n[c], z[c], vh[k:k + 4096])     # chunked: (M, 11, 32) arrays
             for k in range(0, len(ci), 4096) for c in [ci[k:k + 4096]]]
    s = {key: np.concatenate([q[key] for q in parts]) for key in parts[0]}
    print(f"{len(ci)} view(s) scored: {int(s['void'].sum())} void, {int(s['blocked'].sum())} blocked, "
          f"{int(s['cliff'].sum())} cliff behind")

    # step 3, best score first; once a view's score alone can't beat the best total, none below can
    best, best_total, checked = None, -np.inf, []
    for rank, b in enumerate(np.argsort(-s["score"], kind="stable")[:MAX_STRICT]):
        if s["score"][b] <= best_total:
            break
        sv = strict_view(h, e[ci[b]], n[ci[b]], z[ci[b]], vh[b], *strict)
        checked.append(sv["flags"] or ["far buildings"])
        if s["score"][b] - sv["penalty"] > best_total:
            best, best_total, best_sv, best_rank = b, s["score"][b] - sv["penalty"], sv, rank
    print(f"{len(checked)} view(s) checked for sight lines")
    b, sv = best, best_sv
    out = {k: v[b] for k, v in s.items()}
    above = [f for fl in checked[:best_rank] for f in fl]
    rej = ", ".join(f"{above.count(f)}x {f}" for f in dict.fromkeys(above))
    why = (f"score rank {best_rank + 1} of {len(ci)} views (score {s['score'][b]:.0f}, after sight-line costs "
           f"{best_total:.0f})" + (f"; the {best_rank} better-scoring view(s) were rejected for: {rej}" if best_rank else "")
           + (f"; NOTHING CLEAN, least-bad view, still flagged: {', '.join(sv['flags'])}" if sv["flags"] else "")
           + "; " + describe(out, sv))
    return int(ci[b]), car_heading(rh[b], vh[b]), float(vh[b]), out, "auto", why


def turnouts(st):
    """(2026-10-14) Area-screen Turnouts (in-game map fast travel too, via
    AODRoadManager::BuildTownNodes): each osm_places node (city..hamlet) the
    area owns, kept only within NEAR_ROAD_M of a road (hamlets fill the empty
    map), snapped to a parkable station (step 1's RELAXED rung, not a
    motorway) - the nearest on a MAIN_ROADS class within SNAP_M, else the
    nearest any class within SNAP_M - facing along the road. One per
    DEDUP_M: a place whose snap lands near a kept one is dropped.
    The place node itself, not the centroid of the roads labelled with its
    name (that mean sat 1-3 km off, deep in subdivisions; Nast 15 km).
    -> {"<place> · <road>": {"car_cm": [x, y, z], "car_yaw_deg": ..}}"""
    from scipy.spatial import cKDTree
    path = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm_places.json"
    e, n, z = st["e"], st["n"], st["z"]
    idx = candidates(e, n, z, st["seg"], st["structural"], st["in_box"], *RELAXED)
    idx = idx[owns(e[idx] * 100.0, -n[idx] * 100.0) & ~st["no_park"][idx]]
    if not path.exists() or not len(idx):
        return {}
    places = json.loads(path.read_text(encoding="utf-8"))
    le, ln = to_local(st["lon"], st["lat"])   # places' frame (see pick)
    pe, pn = to_local(np.array([p["lon"] for p in places], float), np.array([p["lat"] for p in places], float))
    near_road, _ = cKDTree(np.c_[le, ln]).query(np.c_[pe, pn])
    main = np.isin(st["cls"][idx], MAIN_ROADS)
    trees = [(cKDTree(np.c_[le[idx[m]], ln[idx[m]]]), idx[m]) for m in (main, np.ones_like(main)) if m.any()]
    w = int(round(WINDOW_M / STATION_SPACING))   # candidates() keeps +-w inside the segment
    out, kept = {}, []
    for k in np.argsort(near_road):   # best-placed first wins a dedup
        if near_road[k] > NEAR_ROAD_M or not owns(pe[k] * 100.0, -pn[k] * 100.0):
            continue
        for tree, ids in trees:
            d, j = tree.query((pe[k], pn[k]))
            if d <= SNAP_M:
                break
        else:
            continue
        i = int(ids[j])
        if any(np.hypot(e[i] - a, n[i] - b) < DEDUP_M for a, b in kept):
            continue
        kept.append((e[i], n[i]))
        road = st["name"][i]
        out[f"{places[k]['name']} · {road}" if road else places[k]["name"]] = {
            "car_cm": [round(float(e[i]) * 100.0, 1), round(-float(n[i]) * 100.0, 1),
                       round((float(z[i]) - ORIGIN_HEIGHT_M) * 100.0, 1)],
            "car_yaw_deg": ue_yaw(np.degrees(np.arctan2(e[i + w] - e[i - w], n[i + w] - n[i - w])))}
    print(f"{len(out)} turnout(s) from {len(places)} place(s)")
    return dict(sorted(out.items()))


def plot(path, st, H, x0, y0, i, car_h, view_h):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LightSource
    g = GRID_M
    ext = [x0 / 100.0, x0 / 100.0 + H.shape[1] * g, -y0 / 100.0 - H.shape[0] * g, -y0 / 100.0]  # east/north m
    shade = LightSource(315, 45).hillshade(np.nan_to_num(H, nan=np.nanmin(H)), vert_exag=2, dx=g, dy=g)
    e, n = st["e"], st["n"]
    ce, cn = e[i], n[i]
    fig, axes = plt.subplots(1, 2, figsize=(16, 8))
    for ax, half in zip(axes, (None, 1600.0)):
        ax.imshow(np.where(np.isnan(H), np.nan, shade), cmap="gray", extent=ext, origin="upper")
        ax.imshow(H, cmap="terrain", alpha=0.35, extent=ext, origin="upper")
        step = 1 if half else 4
        ax.plot(e[::step], n[::step], ",", color="k" if half else "red", ms=1)
        a = np.radians(view_h + np.linspace(-WEDGE_HALF_DEG, WEDGE_HALF_DEG, 30))
        ax.fill(np.r_[ce + WEDGE_NEAR_M * np.sin(a), ce + WEDGE_FAR_M * np.sin(a[::-1])],
                np.r_[cn + WEDGE_NEAR_M * np.cos(a), cn + WEDGE_FAR_M * np.cos(a[::-1])],
                color="yellow", alpha=0.35, ec="orange")
        ax.annotate("", (ce + 120 * np.sin(np.radians(car_h)), cn + 120 * np.cos(np.radians(car_h))), (ce, cn),
                    arrowprops=dict(color="blue", width=2, headwidth=8))
        ax.plot(ce, cn, "o", color="magenta", ms=8)
        if half:
            ax.set_xlim(ce - half, ce + half)
            ax.set_ylim(cn - half, cn + half)
        ax.set_aspect("equal")
    axes[0].set_title(f"{AREA_NAME}: spot (magenta), roads (red)")
    axes[1].set_title(f"view {view_h:.0f} deg, car {car_h:.0f} deg (blue), wedge 150-1000 m")
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def run_area(png_dir=None, starts_only=False, dry=False):
    if not bake_complete(AREA_NAME):
        print(f"SKIP {AREA_NAME}: bake incomplete (missing stations/sidecar/terrain, or mixed generations)")
        return
    st = load_stations()
    if starts_only:   # --starts: refresh the turnouts, keep the picked spot
        spots = json.loads(SPOTS_PATH.read_text()) if SPOTS_PATH.exists() else {}
        if AREA_NAME in spots and not dry:
            spots[AREA_NAME]["starts"] = turnouts(st)
            SPOTS_PATH.write_text(json.dumps(spots, indent=1))
        return
    h, H, x0, y0 = height_grid()
    got = pick(st, h, load_buildings())
    if got is None:
        print(f"SKIP {AREA_NAME}: no candidate station survived the filters")
        return
    i, car_h, view_h, s, source, why = got
    spot = {"car_cm": [round(float(st["e"][i]) * 100.0, 1), round(-float(st["n"][i]) * 100.0, 1),
                       round((float(st["z"][i]) - ORIGIN_HEIGHT_M) * 100.0, 1)],
            "car_yaw_deg": ue_yaw(car_h), "view_yaw_deg": ue_yaw(view_h), "source": source}
    sea = sea_share(h, float(st["e"][i]), float(st["n"][i]), view_h)
    if sea >= SEA_SHARE:
        spot["sea_z_cm"] = round(-ORIGIN_HEIGHT_M * 100.0, 1)
    flags = [k for k in ("void", "blocked", "cliff") if s[k]]
    print(f"PICK {AREA_NAME}: {source} {st['name'][i]!r} ({st['cls'][i]}) at {st['lat'][i]:.5f}, {st['lon'][i]:.5f}, "
          f"car {car_h:.0f} deg / view {view_h:.0f} deg from north, sea share {sea:.0%}"
          f"{', FLAGS ' + ','.join(flags) if flags else ''}\n  WHY {why}")
    if not dry:
        spots = json.loads(SPOTS_PATH.read_text()) if SPOTS_PATH.exists() else {}
        spots[AREA_NAME] = {**spot, "starts": turnouts(st)}
        SPOTS_PATH.write_text(json.dumps(spots, indent=1))
        print(f"-> {SPOTS_PATH.name} {spot}")
    if png_dir:
        Path(png_dir).mkdir(parents=True, exist_ok=True)
        plot(Path(png_dir) / f"{AREA_NAME}.png", st, H, x0, y0, i, car_h, view_h)
        print(f"wrote {Path(png_dir) / f'{AREA_NAME}.png'}")


if __name__ == "__main__":
    args = sys.argv[1:]
    png = None
    if "--png" in args:
        k = args.index("--png")
        png, args = args[k + 1], args[:k] + args[k + 2:]
    starts, dry = "--starts" in args, "--dry" in args
    args = [a for a in args if a not in ("--starts", "--dry")]
    if args and args != ["--one"]:
        # test_areas is import-time OD_AREA-driven: one subprocess per area
        areas = list(AREAS) if args == ["--all"] else args
        for area in areas:
            if args == ["--all"] and not list(MASKS.glob(f"terrain_{area}_test_tile_*.bin")):
                continue
            subprocess.run([sys.executable, __file__, "--one"] + (["--png", png] if png else [])
                           + (["--starts"] if starts else []) + (["--dry"] if dry else []),
                           env={**os.environ, "OD_AREA": area}, check=True)
    else:
        run_area(png, starts, dry)
