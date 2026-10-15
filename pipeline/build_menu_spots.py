"""Garage-menu backdrop spots (2026-10-05): one parked-car spot per baked
area -> Content/OpenDrive/Masks/menu_spots.json, merged per area:

    {"summit_county": {"car_cm": [x, y, z], "car_yaw_deg": .., "view_yaw_deg": ..,
                       "source": "auto" | "override",
                       "starts": {"<town>": {"car_cm": [x, y, z], "car_yaw_deg": ..}}}, ...}
    ("starts", 2026-10-14: town_starts below)

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
     or a heading change >= 10 deg over those 60 m; keep only those within
     5 km of an osm_places name when any are.
  2. each tries 6 view headings (perpendicular to the road both sides plus
     the 4 diagonals). The shipped terrain tiles, sampled on a 25 m grid,
     score a wedge +-35 deg, 150-1000 m out, on terrain visible from the
     eye (1.5 m up, nearer ground occludes along each ray): relief
     (p90 - p10) plus how far its far half (>= 500 m) rises above the eye,
     capped at 270 m. Rejected: >20 % void, terrain above 10 deg within
     100 m or above 20 deg anywhere (a wall, no vista), or the 25 m of
     ground behind the car (camera side) leaving +-4 m of the road. Each
     building centroid within 100 m in the wedge costs 100.
  3. best score wins; the car faces the camera three-quarter, nose toward
     screen right (the menu side).
  4. "sea_z_cm" (sea level in the area's frame, = -ORIGIN_HEIGHT_M*100) is
     added only when >= 10 % of the view wedge's terrain samples, 150 m -
     20 km out (off-tile excluded), sit at or below 2 m - the runtime then
     draws a sea plane. At or below, not within: the DEM carries
     bathymetry, so the bay floor reads -5..-100 m. Inland/high areas never
     get one (a plane far below would show through void as a fake floor).

Usage:  python build_menu_spots.py              (active area, OD_AREA)
        python build_menu_spots.py --all        (every area whose bake is complete)
        python build_menu_spots.py gv_east ...  (those areas)
        add --png DIR for a top-down sanity plot per area,
        --starts to only refresh each area's "starts" (town_starts) and keep its spot.
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
PLACE_RADIUS_M = 5000.0
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
BUILDING_COST = 100.0
REJECT_COST = 1e4           # rejects sink below every clean view, so an all-rejected area still gets its least-bad one
GRID_M = 25.0
# ponytail: below-sea inland basins (Salton Sink, Death Valley) would pass;
# add an OSM coastline check if an area there is ever baked.
SEA_M, SEA_SHARE, SEA_FAR_M = 2.0, 0.10, 20000.0


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


def score_views(h, e, n, z, head_deg, bldg_en=None):
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

    nb = np.zeros(len(e))
    if bldg_en is not None and len(bldg_en):
        from scipy.spatial import cKDTree
        tree = cKDTree(bldg_en)
        for k, near_b in enumerate(tree.query_ball_point(np.c_[e, n], BLOCK_M)):
            if near_b:
                db = bldg_en[near_b] - (e[k], n[k])
                off = np.degrees(np.arctan2(db[:, 0], db[:, 1])) - head_deg[k]
                nb[k] = np.sum(np.abs((off + 180.0) % 360.0 - 180.0) <= WEDGE_HALF_DEG)
    reject = void | blocked | cliff
    score = np.nan_to_num(relief + rise, nan=0.0) - BUILDING_COST * nb - REJECT_COST * reject
    return dict(score=score, relief=relief, rise=rise, buildings=nb,
                void=void, blocked=blocked, cliff=cliff)


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
                no_park=np.isin(cat("highway_class", ""), NO_PARK),
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


def pick(st, h, bldg):
    """-> (station index, car heading, view heading, score dict) for this area."""
    e, n, z = st["e"], st["n"], st["z"]
    # local frame from lon/lat: green_valley's bake predates the LCC frame (~90 m off it)
    le, ln = to_local(st["lon"], st["lat"])
    if AREA_NAME in OVERRIDES:
        lat, lon, car_h, view_h = OVERRIDES[AREA_NAME]
        oe, on = to_local(lon, lat)
        i = int(np.argmin(np.hypot(le - oe, ln - on)))
        s = score_views(h, e[[i]], n[[i]], z[[i]], np.array([float(view_h)]), bldg)
        return i, car_h % 360.0, view_h % 360.0, {k: v[0] for k, v in s.items()}, "override"

    for clear_m, max_grade in ((JUNCTION_CLEAR_M, MAX_GRADE), RELAXED):
        idx = candidates(e, n, z, st["seg"], st["structural"], st["in_box"], clear_m, max_grade)
        idx = idx[owns(e[idx] * 100.0, -n[idx] * 100.0) & ~st["no_park"][idx]]
        if len(idx):
            break
        print(f"no candidates at {clear_m:.0f} m junction clearance / {max_grade:.0%} grade - relaxing")
    places = json.loads((ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm_places.json")
                        .read_text(encoding="utf-8")) if (ROOT / "data" / "raw" / f"{AREA_NAME}_test"
                                                          / "osm_places.json").exists() else []
    if places and len(idx):
        from scipy.spatial import cKDTree
        pe, pn = to_local(np.array([p["lon"] for p in places]), np.array([p["lat"] for p in places]))
        d, _ = cKDTree(np.c_[pe, pn]).query(np.c_[le[idx], ln[idx]])
        if (d < PLACE_RADIUS_M).any():
            idx = idx[d < PLACE_RADIUS_M]
    print(f"{len(idx)} candidate station(s) after filters")
    if not len(idx):
        return None
    w = int(round(WINDOW_M / STATION_SPACING))
    road_h = np.degrees(np.arctan2(e[idx + w] - e[idx - w], n[idx + w] - n[idx - w])) % 360.0
    ci = np.repeat(idx, len(VIEW_OFFSETS_DEG))
    rh = np.repeat(road_h, len(VIEW_OFFSETS_DEG))
    vh = (rh + np.tile(VIEW_OFFSETS_DEG, len(idx))) % 360.0
    parts = [score_views(h, e[c], n[c], z[c], vh[k:k + 4096], bldg)     # chunked: (M, 11, 32) arrays
             for k in range(0, len(ci), 4096) for c in [ci[k:k + 4096]]]
    s = {key: np.concatenate([q[key] for q in parts]) for key in parts[0]}
    print(f"{len(ci)} view(s) scored: {int(s['void'].sum())} void, {int(s['blocked'].sum())} blocked, "
          f"{int(s['cliff'].sum())} cliff behind")
    b = int(np.argmax(s["score"]))
    return int(ci[b]), car_heading(rh[b], vh[b]), float(vh[b]), {k: v[b] for k, v in s.items()}, "auto"


def town_starts(st):
    """(2026-10-14) Area-screen starts: each town of labels_<area>_test.bin
    (bake_world_map.read_towns, the menu's own names and centroids) -> the
    nearest parkable station (step 1's RELAXED rung, owned, not a motorway),
    facing along the road. The menu previews the car there and Drive spawns
    there (AODMainMenu::StartAtTown), so a town centre's junction never is.
    -> {name: {"car_cm": [x, y, z], "car_yaw_deg": ..}}"""
    from scipy.spatial import cKDTree
    from bake_world_map import read_towns
    lab = MASKS / f"labels_{AREA_NAME}_test.bin"
    e, n, z = st["e"], st["n"], st["z"]
    idx = candidates(e, n, z, st["seg"], st["structural"], st["in_box"], *RELAXED)
    idx = idx[owns(e[idx] * 100.0, -n[idx] * 100.0) & ~st["no_park"][idx]]
    if not lab.exists() or not len(idx):
        return {}
    tree = cKDTree(np.c_[e[idx], n[idx]])
    w = int(round(WINDOW_M / STATION_SPACING))   # candidates() keeps +-w inside the segment
    out, far = {}, 0.0
    for name, x, y in read_towns(lab):
        d, k = tree.query((x / 100.0, -y / 100.0))
        i = int(idx[k])
        far = max(far, d)
        out[name] = {"car_cm": [round(float(e[i]) * 100.0, 1), round(-float(n[i]) * 100.0, 1),
                                round((float(z[i]) - ORIGIN_HEIGHT_M) * 100.0, 1)],
                     "car_yaw_deg": ue_yaw(np.degrees(np.arctan2(e[i + w] - e[i - w], n[i + w] - n[i - w])))}
    print(f"{len(out)} town start(s), farthest {far:.0f} m from its label centroid")
    return out


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


def run_area(png_dir=None, starts_only=False):
    if not bake_complete(AREA_NAME):
        print(f"SKIP {AREA_NAME}: bake incomplete (missing stations/sidecar/terrain, or mixed generations)")
        return
    st = load_stations()
    if starts_only:   # --starts: refresh the town starts, keep the picked spot
        spots = json.loads(SPOTS_PATH.read_text()) if SPOTS_PATH.exists() else {}
        if AREA_NAME in spots:
            spots[AREA_NAME]["starts"] = town_starts(st)
            SPOTS_PATH.write_text(json.dumps(spots, indent=1))
        return
    h, H, x0, y0 = height_grid()
    got = pick(st, h, load_buildings())
    if got is None:
        print(f"SKIP {AREA_NAME}: no candidate station survived the filters")
        return
    i, car_h, view_h, s, source = got
    spots = json.loads(SPOTS_PATH.read_text()) if SPOTS_PATH.exists() else {}
    spots[AREA_NAME] = {
        "car_cm": [round(float(st["e"][i]) * 100.0, 1), round(-float(st["n"][i]) * 100.0, 1),
                   round((float(st["z"][i]) - ORIGIN_HEIGHT_M) * 100.0, 1)],
        "car_yaw_deg": ue_yaw(car_h), "view_yaw_deg": ue_yaw(view_h), "source": source}
    sea = sea_share(h, float(st["e"][i]), float(st["n"][i]), view_h)
    print(f"sea share {sea:.0%}")
    if sea >= SEA_SHARE:
        spots[AREA_NAME]["sea_z_cm"] = round(-ORIGIN_HEIGHT_M * 100.0, 1)
    spots[AREA_NAME]["starts"] = town_starts(st)
    SPOTS_PATH.write_text(json.dumps(spots, indent=1))
    flags = [k for k in ("void", "blocked", "cliff") if s[k]] + (["buildings"] if s["buildings"] else [])
    print(f"{AREA_NAME}: {source} {st['name'][i]!r} at {st['lat'][i]:.5f}, {st['lon'][i]:.5f} "
          f"car {car_h:.0f} deg / view {view_h:.0f} deg from north, score {s['score']:.0f} "
          f"(relief {s['relief']:.0f} m, rise {s['rise']:.0f} m{', FLAGS ' + ','.join(flags) if flags else ''})"
          f" -> {SPOTS_PATH.name} {spots[AREA_NAME]}")
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
    starts = "--starts" in args
    args = [a for a in args if a != "--starts"]
    if args and args != ["--one"]:
        # test_areas is import-time OD_AREA-driven: one subprocess per area
        areas = list(AREAS) if args == ["--all"] else args
        for area in areas:
            if args == ["--all"] and not list(MASKS.glob(f"terrain_{area}_test_tile_*.bin")):
                continue
            subprocess.run([sys.executable, __file__, "--one"] + (["--png", png] if png else [])
                           + (["--starts"] if starts else []),
                           env={**os.environ, "OD_AREA": area}, check=True)
    else:
        run_area(png, starts)
