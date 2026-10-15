"""Sidewalks and driveway stubs (PLAN-REALISM Phase 1b) -> sidewalk_<area>_test
ODS1 tiles: concrete (M_Bridge) with collision, streamed by an AODWallManager
the way the tunnel bores are (ODGameMode spawns it).

Sidewalks: a CURB_H_M curb and a WALK_W_M walk along each pavement edge of
town streets - build_road_furniture's town mask (TOWN_MIN_BUILDINGS building
centroids within TOWN_RADIUS_M, the street-lamp rule), SIDEWALK_CLASSES only,
never on a bridge, in a tunnel or inside a junction box. The walk breaks
wherever it would reach another road's pavement and at every driveway (a
curb cut). It hugs the shipped pavement edge: junction-pad XY snaps and the
pad bank fade are read back exactly as build_road_furniture reads them.

Driveways: OSM highway=service ways with an end node on a road centerline,
the first DRIVEWAY_MAX_M off the pavement edge only (perf): a concrete slab
leaving the road at its edge height and then draped DRIVEWAY_LIFT_M over the
shipped terrain tiles. A stub stops where it would reach another road.

Not part of the road ribbon itself: M_Road has no concrete look and is an
editor-built material this pipeline doesn't regenerate.

Usage:  python build_sidewalks.py   (after the terrain + road tiles and
                                      experimental_junction_pads)
"""

import json
import math
from collections import defaultdict

import numpy as np
from scipy.spatial import cKDTree

import build_retaining_walls as brw
import build_terrain_mesh_tiles as btmt
import verify_road_fit as vrf
from build_road_furniture import load_town_mask, shipped_stations
from build_tunnels import OUT_DIR, ue_volume
from export_retaining_wall_bin import write_tile
from fetch_cache import overpass_json
from fetch_osm_network import HEADERS
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, to_local

SIDEWALK_CLASSES = {"residential", "tertiary", "secondary", "primary", "unclassified",
                    "living_street", "tertiary_link", "secondary_link", "primary_link"}
CURB_H_M = 0.15
WALK_W_M = 1.8
BASE_M = 0.25            # solid reaches this far under the pavement / walk
ROAD_CLEAR_M = 0.3       # walk / stub keeps this far off any other road's pavement
OWN_SKIP_STATIONS = 6    # own-road stations this close in index never block (30 m)
MIN_RUN_STATIONS = 3
CHUNK_STATIONS = 40
CURB_CUT_M = 3.5         # walk stations this close to a driveway mouth are cut
DRIVEWAY_MAX_M = 30.0
DRIVEWAY_STEP_M = 2.5
DRIVEWAY_MIN_M = 5.0
DRIVEWAY_HW_M = 1.6      # service=driveway
SERVICE_HW_M = 2.5       # any other service way
DRIVEWAY_LIFT_M = 0.08
DRIVEWAY_ATTACH_M = 3.0  # end node this close to a road centerline = attached


def prism(c, r, z, profile):
    """Closed solid: the convex (m,2) (lateral cm along r, dz cm) profile swept
    through K frames (c (K,2) cm, unit r (K,2), z (K,) cm), capped at both
    ends. Returns (verts, uvs, tris), UE front faces out."""
    k_n, m = len(c), len(profile)
    off, dz = profile[:, 0], profile[:, 1]
    verts = np.stack([c[:, None, 0] + r[:, None, 0] * off, c[:, None, 1] + r[:, None, 1] * off,
                      z[:, None] + dz], axis=-1).reshape(-1, 3)
    along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
    perim = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(profile, axis=0).T))])
    uvs = np.stack(np.broadcast_arrays(perim[None, :], along[:, None]), axis=-1).reshape(-1, 2) / 400.0
    tris = []
    for k in range(k_n - 1):
        for i in range(m):
            a, b, c2, d = k * m + i, k * m + (i + 1) % m, (k + 1) * m + (i + 1) % m, (k + 1) * m + i
            tris += [(a, b, c2), (a, c2, d)]
    for i in range(1, m - 1):
        tris += [(0, i + 1, i), ((k_n - 1) * m, (k_n - 1) * m + i, (k_n - 1) * m + i + 1)]
    tris = np.array(tris)
    if ue_volume(verts, tris) < 0:
        tris = tris[:, ::-1]
    return verts.astype(np.float32), uvs.astype(np.float32), tris.astype(np.uint32)


def clear_of_roads(px, py, tree, hw, own, skip=OWN_SKIP_STATIONS):
    """Per point (m): no road station other than `own` (+-skip, same global
    order) has the point inside its pavement + ROAD_CLEAR_M."""
    k = min(16, tree.n)
    d, j = tree.query(np.c_[px, py], k=k, distance_upper_bound=hw.max() + ROAD_CLEAR_M)
    hw_pad = np.append(hw, 0.0)[j]                       # j == tree.n where nothing was found
    hit = (d < hw_pad + ROAD_CLEAR_M) & (np.abs(j - np.asarray(own)[:, None]) > skip)
    return ~hit.any(axis=1)


def runs_of(mask, min_len=MIN_RUN_STATIONS):
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return [(a, b) for a, b in zip(np.where(d == 1)[0], np.where(d == -1)[0]) if b - a >= min_len]


def fetch_service_ways():
    s, w, n_, e = BBOX_SNWE
    data = overpass_json(f"""
    [out:json][timeout:180];
    way["highway"="service"]({s},{w},{n_},{e});
    (._;>;);
    out body;
    """, HEADERS)
    nodes = {el["id"]: (el["lat"], el["lon"]) for el in data["elements"] if el["type"] == "node"}
    out = []
    for el in data["elements"]:
        if el["type"] == "way" and all(nd in nodes for nd in el["nodes"]):
            ll = np.array([nodes[nd] for nd in el["nodes"]])
            e_, n2 = to_local(ll[:, 1], ll[:, 0])
            out.append((np.c_[e_, n2], el.get("tags", {}).get("service") == "driveway"))
    return out


def driveway_stubs(ways, tree, east, north, road_z, hw):
    """[(points (K,2) m, z (K,) abs m, half-width m)] - see module docstring."""
    stubs = []
    for pts, is_drive in ways:
        for p in (pts, pts[::-1]):
            if len(p) < 2:
                continue
            d0, j = tree.query(p[0])
            if d0 > DRIVEWAY_ATTACH_M:
                continue
            seg = np.hypot(*np.diff(p, axis=0).T)
            s = np.concatenate([[0.0], np.cumsum(seg)])
            t = np.arange(hw[j], min(s[-1], hw[j] + DRIVEWAY_MAX_M) + 1e-6, DRIVEWAY_STEP_M)
            if len(t) < 2:
                continue
            q = np.c_[np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])]
            ok = clear_of_roads(q[:, 0], q[:, 1], tree, hw, np.full(len(q), j))
            ok[0] = True
            cut = int(np.argmin(ok)) if not ok.all() else len(q)
            q = q[:cut]
            if len(q) < 2 or t[len(q) - 1] - t[0] < DRIVEWAY_MIN_M:
                continue
            stubs.append((q, j, DRIVEWAY_HW_M if is_drive else SERVICE_HW_M))
    # drape on the shipped terrain, all stubs batched
    if not stubs:
        return []
    allq = np.concatenate([q for q, _, _ in stubs])
    x_cm, y_cm = allq[:, 0] * 100.0, -allq[:, 1] * 100.0
    tile = btmt.TERRAIN_TILE_SIZE_CM
    ground = vrf._sample_with_neighbors(vrf._load_terrain_tile, np.floor(x_cm / tile).astype(int),
                                        np.floor(y_cm / tile).astype(int), x_cm, y_cm)
    ground = ground / 100.0 + ORIGIN_HEIGHT_M
    out, off = [], 0
    for q, j, w in stubs:
        g = ground[off:off + len(q)]
        off += len(q)
        z = np.where(np.isnan(g), road_z[j], g + DRIVEWAY_LIFT_M)
        z[0] = road_z[j] - 0.02
        out.append((q, z, w))
    return out


def polyline_frames(q):
    """UE-cm centerline and unit right normal of an (K,2) metre polyline."""
    c = np.c_[q[:, 0], -q[:, 1]] * 100.0
    t = np.gradient(c, axis=0)
    t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
    return c, np.c_[-t[:, 1], t[:, 0]]


def main():
    segs = json.loads(btmt.STATIONS_PATH.read_text())["segments"]
    cat = lambda k: np.concatenate([np.asarray(s[k]) for s in segs])
    e0, n0, elev = cat("east_m").astype(float), cat("north_m").astype(float), cat("elev_m").astype(float)
    east, north, road_z, slope = shipped_stations(e0, n0, elev)
    rxy = btmt.station_bank(east, north)[:, 1:]
    hw = brw.load_station_half_widths()
    hwy = np.array([h for s in segs for h in s["highway_class"]])
    seg_of = np.concatenate([np.full(len(s["east_m"]), i) for i, s in enumerate(segs)])
    tree = cKDTree(np.c_[east, north])

    stubs = driveway_stubs(fetch_service_ways(), tree, east, north, road_z, hw)
    mouths = cKDTree(np.array([q[0] for q, _, _ in stubs]).reshape(-1, 2))

    base = (np.isin(hwy, list(SIDEWALK_CLASSES)) & load_town_mask(east, north)
            & ~cat("is_struct").astype(bool) & ~cat("is_tunnel").astype(bool)
            & ~cat("in_junction_box").astype(bool) & (cat("junction_suppress").astype(float) < 0.05))
    idx = np.arange(len(east))
    tile = TERRAIN_TILE_SIZE_CM
    buckets = defaultdict(list)

    def add(mesh):
        v, uv, t = mesh
        at = v[:, :2].mean(axis=0)
        buckets[(int(math.floor(at[0] / tile)), int(math.floor(at[1] / tile)))].append(
            {"name": "sidewalk", "vertices": v.reshape(-1).tolist(),
             "uvs": uv.reshape(-1).tolist(), "triangles": t.reshape(-1).tolist()})

    walk = np.array([(0.0, -BASE_M), (0.0, CURB_H_M), (WALK_W_M, CURB_H_M), (WALK_W_M, -BASE_M)]) * 100.0
    n_walk_st = 0
    km = 0.0
    for sgn in (-1.0, 1.0):
        ok = base.copy()
        for frac in (0.1, 0.5, 1.0):
            o = hw + WALK_W_M * frac + (ROAD_CLEAR_M if frac == 1.0 else 0.0)
            ok &= clear_of_roads(east + rxy[:, 0] * sgn * o, north - rxy[:, 1] * sgn * o, tree, hw, idx)
        mid = hw + 0.5 * WALK_W_M
        if mouths.n:
            ok &= mouths.query(np.c_[east + rxy[:, 0] * sgn * mid, north - rxy[:, 1] * sgn * mid])[0] > CURB_CUT_M
        for si in np.unique(seg_of[ok]):
            sl = np.where(seg_of == si)[0]
            for a, b in runs_of(ok[sl]):
                st = sl[a:b]
                n_walk_st += len(st)
                c = np.c_[east[st], -north[st]] * 100.0 + rxy[st] * (sgn * hw[st] * 100.0)[:, None]
                z = (road_z[st] - ORIGIN_HEIGHT_M) * 100.0 + slope[st] * sgn * hw[st] * 100.0
                km += float(np.hypot(*np.diff(c, axis=0).T).sum()) / 1e5
                for k in range(0, len(st) - 1, CHUNK_STATIONS):
                    ch = slice(k, min(k + CHUNK_STATIONS, len(st) - 1) + 1)
                    add(prism(c[ch], rxy[st][ch] * sgn, z[ch], walk))

    slab = lambda w: np.array([(-w, -BASE_M), (-w, 0.0), (w, 0.0), (w, -BASE_M)]) * 100.0
    for q, z, w in stubs:
        c, r = polyline_frames(q)
        add(prism(c, r, (z - ORIGIN_HEIGHT_M) * 100.0, slab(w)))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id_bytes = pack_generation_id(compute_generation_id())
    written = []
    for (tx, ty), chunks in buckets.items():
        path = OUT_DIR / f"sidewalk_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(path, chunks, gen_id_bytes)
        written.append(path)
    n_stale = delete_stale_tiles(OUT_DIR, f"sidewalk_{AREA_NAME}_test_tile_*.bin", written)
    print(f"sidewalks: {km:.1f} km of walk over {n_walk_st} station-side(s) "
          f"({int(base.sum())} town-street station(s) eligible); {len(stubs)} driveway stub(s) "
          f"-> {len(written)} tile(s), {n_stale} stale deleted")


if __name__ == "__main__":
    main()
