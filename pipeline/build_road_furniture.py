"""Roadside furniture (PLAN-REALISM Phase 1): signal poles, stop / yield /
crossing / railroad signs, speed-limit signs, curve chevrons, street lights,
delineator posts and stop bars, as one flat list of instanced props that
AODFurnitureManager streams per tile (export_road_furniture_bin.py -> ODF1).

Two tiers:
  OSM        highway=traffic_signals|stop|give_way|crossing|street_lamp and
             railway=level_crossing nodes (fetch_osm_network.py). Each control
             node snaps to its road (nearest segment by its ORIGINAL station
             positions; a junction node takes every segment within
             SNAP_RADIUS_M) and gets a pole on the right edge of every
             approach that reaches it - see approach_dirs(). Stop and signal
             approaches also get a flat stop-bar stripe across their lane(s);
             stop bars are props here, not an ODR3 road flag.
  heuristic  speed signs where maxspeed (or the class default) changes and
             near the start of every long segment, chevrons on curves
             tighter than chevron_radius_m() at the posted speed, street
             lights every ~35 m (staggered) where buildings are dense or on
             motorway ramps, delineators every 50 m on rural through roads
             that have no guardrail.

Props sit on the SHIPPED road (experimental_junction_pads' snapped/regraded
stations, via verify_road_fit._shipped_stations, banked with the pad fade
exactly as the road ribbons are) at half_width_for_stations + an offset,
their base on the shipped terrain where that is within 1 m of the banked
pavement edge, else on the edge itself. So re-run this after the manual
junction-pads step. Anything that would land inside any pavement (stop bars
aside) is dropped - see in_pavement().

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json (+ the roadstations
        sidecar / bank fade the junction-pads step patches)
        data/road_tiles/<AREA_NAME>_test/road_guardrails.json (if present)
        data/raw/<AREA_NAME>_test/osm.json, osm_buildings.json
        Content/OpenDrive/Masks/terrain_*.bin
Outputs data/road_tiles/<AREA_NAME>_test/road_furniture.json
        {"props": [[type, x_cm, y_cm, z_cm, yaw_deg, scale_y], ...],
         "controls": [{"id", "kind", "x_cm", "y_cm", "approaches": [[seg, station, dir], ...]}]}
        "controls" is the signal/stop/give_way list Phase 3 (AI traffic) reads.
"""

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

import build_retaining_walls as brw
import build_terrain_mesh_tiles as btmt
import verify_road_fit as vrf
from build_road_stations import junction_node_degree
from export_road_centerline_bin import station_oneway_dirs
from road_mesh_common import STATION_SPACING, gaussian1d
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, to_local

ROOT = Path(__file__).resolve().parent.parent
TILE_DIR = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test"
RAW = ROOT / "data" / "raw" / f"{AREA_NAME}_test"
OUT_PATH = TILE_DIR / "road_furniture.json"

# Type ids: the ODF1 byte and AODFurnitureManager's mesh table index - keep
# both in this order.
TYPES = ["signal", "stop", "yield", "ped_crossing", "rail_crossbuck", "speed",
         "chevron_l", "chevron_r", "street_light", "delineator", "stop_bar"]
T = {name: i for i, name in enumerate(TYPES)}

SNAP_RADIUS_M = 20.0        # control node -> segment snap
JUNCTION_SEARCH_M = 40.0    # stop/signal node -> the junction it guards
JUNCTION_ARM_M = 8.0        # a junction node's own arms pass this close
JUNCTION_CLEAR_M = 1.0      # junction pole: this far back from the crossing arm's edge
LAYER_DZ_M = 4.0            # a segment this far above/below is another level
POLE_OFF_M = 1.0            # sign/signal pole: pavement edge + this
LAMP_OFF_M = 1.5
DELIN_OFF_M = 0.6
CHEVRON_STEP_M = 20.0
CHEVRON_MIN_RUN_M = 20.0
CHEVRON_MAX_R_M = 150.0
CHEVRON_SIDE_FRICTION = 0.27   # R = v^2 / (g f): tighter than this at speed v
LAMP_STEP_M = 35.0
LAMP_OSM_CLEAR_M = 25.0     # no heuristic lamp this close to a mapped one
DELIN_STEP_M = 50.0
DELIN_RAIL_CLEAR_M = 3.0
SPEED_SEG_MIN_M = 300.0
SPEED_START_M = 40.0
SPEED_DEDUPE_M = 150.0
TOWN_RADIUS_M = 100.0
TOWN_MIN_BUILDINGS = 8      # building centroids within TOWN_RADIUS_M = "in town"
PAVEMENT_CLEAR_M = 0.3
PUSH_OUT_M = 1.5
PUSH_STEPS = 4
SIGN_DEDUPE_M = 12.0
BASE_SNAP_M = 1.0           # terrain this close to the banked edge = the base
CONTROL_MAX_M = 15.0        # OSM-tier pole to its node

# Class default speeds (mph -> km/h) where OSM has no maxspeed (US rural/town
# typicals; the area set is all-US today).
DEFAULT_MPH = {"motorway": 65, "trunk": 55, "primary": 45, "secondary": 40,
               "tertiary": 35, "unclassified": 35, "residential": 25,
               "motorway_link": 35, "trunk_link": 35, "primary_link": 35,
               "secondary_link": 35, "tertiary_link": 35, "service": 15}
RURAL_DELIN_CLASSES = {"motorway", "trunk", "primary", "secondary", "tertiary",
                       "unclassified", "motorway_link", "trunk_link", "primary_link",
                       "secondary_link", "tertiary_link"}


def approach_dirs(xy, k, ow, target, back):
    """[(dir, station)] for each travel direction (+1 = increasing station
    order) that reaches `target` (east, north) through station k of a segment
    with stations `xy` (n, 2) and oneway dir `ow` (+1/-1/0): the sign station
    sits `back` stations upstream of k, and from there the next station along
    the travel direction must be closer to the target. That rule covers a
    junction node at k (both sides of a through road, one side at a segment
    end) and a stop node short of its junction (only toward it) alike."""
    out = []
    for d in ((ow,) if ow else (1, -1)):
        j = k - d * back
        if 0 <= j < len(xy) and 0 <= j + d < len(xy) and \
                np.hypot(*(xy[j + d] - target)) < np.hypot(*(xy[j] - target)):
            out.append((d, j))
    return out


def chevron_radius_m(speed_kmh):
    """Curves tighter than this get chevrons: the radius a car at the posted
    speed takes at CHEVRON_SIDE_FRICTION, capped at CHEVRON_MAX_R_M."""
    v = np.asarray(speed_kmh, dtype=float) / 3.6
    return np.minimum(CHEVRON_MAX_R_M, v * v / (9.81 * CHEVRON_SIDE_FRICTION))


def signed_curvature(xy):
    """Per-station signed curvature (1/m, + = turning left in east/north) of a
    station polyline, smoothed over ~2 stations so one noisy vertex is not a
    curve."""
    t = np.gradient(xy, axis=0)
    head = np.unwrap(np.arctan2(t[:, 1], t[:, 0]))
    ds = np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-6)
    return gaussian1d(np.gradient(head) / ds, 2.0) if len(xy) > 4 else np.gradient(head) / ds


def chevron_mask(xy, speed_kmh, blocked):
    """(mask, curvature): stations inside a run of at least CHEVRON_MIN_RUN_M
    whose radius is under chevron_radius_m(speed), not crossing `blocked`."""
    kappa = signed_curvature(xy)
    tight = (np.abs(kappa) * chevron_radius_m(speed_kmh) > 1.0) & ~blocked
    mask = np.zeros(len(xy), dtype=bool)
    i = 0
    while i < len(xy):
        if not tight[i]:
            i += 1
            continue
        j = i
        while j + 1 < len(xy) and tight[j + 1] and np.sign(kappa[j + 1]) == np.sign(kappa[i]):
            j += 1
        if (j - i) * STATION_SPACING >= CHEVRON_MIN_RUN_M:
            mask[i:j + 1] = True
        i = j + 1
    return mask, kappa


def yaw_deg(fe, fn):
    """UE yaw of an east/north facing direction (UE x = east, y = -north)."""
    return math.degrees(math.atan2(-fn, fe))


def pavement_index(east, north, road_z, hw, seg_idx, skip):
    """Stations densified to 1 m (KD-tree, half-width, road z) so a point's
    distance to the nearest sample is within ~3 cm of its true distance to
    the centerline polyline. `skip` stations (tunnels) are left out."""
    pts, w, z = [], [], []
    for s in np.unique(seg_idx):
        m = np.where((seg_idx == s) & ~skip)[0]
        if len(m) == 0:
            continue
        f = np.linspace(0, len(m) - 1, max(1, (len(m) - 1) * 5 + 1))
        i = np.arange(len(m))
        pts.append(np.c_[np.interp(f, i, east[m]), np.interp(f, i, north[m])])
        w.append(np.interp(f, i, hw[m]))
        z.append(np.interp(f, i, road_z[m]))
    return cKDTree(np.concatenate(pts)), np.concatenate(w), np.concatenate(z)


def in_pavement(index, pe, pn, pz, clear=PAVEMENT_CLEAR_M):
    """True where (pe, pn) is within any pavement + `clear` at a road level
    within LAYER_DZ_M of pz (abs m) - a lamp under an overpass is fine."""
    tree, w, z = index
    out = np.zeros(len(pe), dtype=bool)
    for i, near in enumerate(tree.query_ball_point(np.c_[pe, pn], 25.0)):
        if near:
            near = np.asarray(near)
            d = np.hypot(tree.data[near, 0] - pe[i], tree.data[near, 1] - pn[i])
            out[i] = bool(np.any((d < w[near] + clear) & (np.abs(z[near] - pz[i]) < LAYER_DZ_M)))
    return out


def terrain_abs_m(pe, pn):
    """Shipped terrain height (abs m, NaN = no tile) under each point."""
    x_cm, y_cm = pe * 100.0, -pn * 100.0
    h = vrf._sample_with_neighbors(vrf._load_terrain_tile,
                                   np.floor(x_cm / TERRAIN_TILE_SIZE_CM).astype(int),
                                   np.floor(y_cm / TERRAIN_TILE_SIZE_CM).astype(int), x_cm, y_cm)
    return h / 100.0 + ORIGIN_HEIGHT_M


def shipped_stations(e0, n0, elev):
    """(east, north, road z abs m, bank slope) per station as the road
    ships, exactly as verify_road_fit.check_poke_through reads it."""
    from experimental_junction_pads import BANK_SCALE_PATH, SIDECAR_PATH, read_station_sidecar
    scale = None
    if BANK_SCALE_PATH.exists() and SIDECAR_PATH.exists():
        f = np.load(BANK_SCALE_PATH)
        if str(f["gen"]) == read_station_sidecar(SIDECAR_PATH)[0] and len(f["scale"]) == len(e0):
            scale = f["scale"]
    east, north, road_z = vrf._shipped_stations(e0, n0, elev)
    print(f"{int(np.sum((east != e0) | (north != n0)))} station(s) at a junction-pad snapped position; "
          f"bank fade {'applied' if scale is not None else 'not present'}")
    return east, north, road_z, btmt.station_bank(east, north, scale)[:, 0]


def load_town_mask(east, north):
    """Per station: at least TOWN_MIN_BUILDINGS building centroids within
    TOWN_RADIUS_M. settlement_tiles.json's 2 km tiles alone light whole
    valleys."""
    path = RAW / "osm_buildings.json"
    if not path.exists():
        return np.zeros(len(east), dtype=bool)
    ll = np.array([np.mean(b["nodes"], axis=0) for b in json.loads(path.read_text()) if b["nodes"]])
    be, bn = to_local(ll[:, 1], ll[:, 0])
    counts = cKDTree(np.c_[be, bn]).query_ball_point(np.c_[east, north], TOWN_RADIUS_M, return_length=True)
    return counts >= TOWN_MIN_BUILDINGS


def main():
    data = json.loads(brw.STATIONS_PATH.read_text())
    segs = data["segments"]
    e0, n0, elev, seg_idx, st_idx, _names = brw.load_station_arrays()
    hw = brw.load_station_half_widths()
    flat = lambda key, dflt: np.concatenate([np.array(s.get(key, [dflt] * len(s["east_m"]))) for s in segs])
    hclass = flat("highway_class", "")
    maxspeed = flat("maxspeed", 0).astype(float)
    struct = flat("is_bridge", False) | flat("is_tunnel", False) | flat("in_junction_box", False)
    tunnel = flat("is_tunnel", False).astype(bool)
    osm = json.loads((RAW / "osm.json").read_text())
    ow = np.concatenate(station_oneway_dirs(segs, osm))
    offsets = np.concatenate([[0], np.cumsum([len(s["east_m"]) for s in segs])])

    east, north, road_z, bank = shipped_stations(e0, n0, elev)

    # tangent from the ORIGINAL stations: pad-snapped approach stations can
    # bunch up to ~0.5 m apart, and their gradient points anywhere
    te, tn = np.empty_like(east), np.empty_like(east)
    for a, b in zip(offsets[:-1], offsets[1:]):
        if b - a >= 2:
            te[a:b], tn[a:b] = np.gradient(e0[a:b]), np.gradient(n0[a:b])
        else:
            te[a:b], tn[a:b] = 1.0, 0.0
    tl = np.maximum(np.hypot(te, tn), 1e-9)
    te, tn = te / tl, tn / tl          # tangent; right of station order = (tn, -te)
    speed = np.where(maxspeed > 0, maxspeed,
                     np.array([DEFAULT_MPH.get(c, 25) for c in hclass]) * 1.609344)
    town = load_town_mask(east, north)

    # [type, east, north, z_abs, yaw, sy, edge_z, out_e, out_n (away from the road),
    #  node_e, node_n (control poles: their node, shipped frame), free (base on terrain)]
    props = []

    def put(kind, i, d_side, extra, face_d, sy=1.0, along=0.0, yaw=None, node=(np.nan, np.nan), at=None):
        """A prop at station i, `d_side` (+1 right / -1 left of station
        order) at pavement edge + extra, facing travel direction face_d's
        oncoming traffic (+X local = -face_d * tangent). `at` = (east,
        north) overrides the station's shipped position; the edge z is then
        the shipped surface there."""
        x, y = at if at is not None else (east[i], north[i])
        o = d_side * (hw[i] + extra)
        pe = x + tn[i] * o + te[i] * along
        pn = y - te[i] * o + tn[i] * along
        if yaw is None:
            yaw = yaw_deg(-face_d * te[i], -face_d * tn[i])
        edge_z = surface_z(pe, pn) if at is not None else road_z[i] + bank[i] * d_side * hw[i]
        props.append([T[kind], pe, pn, np.nan, yaw, sy, edge_z,
                      d_side * tn[i], -d_side * te[i], *node, 0])

    # ---------------------------------------------------------- OSM tier
    nodes = {e["id"]: e for e in osm["elements"] if e["type"] == "node"}
    ways = [e for e in osm["elements"] if e["type"] == "way"]
    deg = junction_node_degree(ways)
    on_way = {n for w in ways for n in w.get("nodes", [])}
    way_dir = {}        # node -> way direction (east, north) there, for direction=forward|backward
    for w in ways:
        nds = [n for n in w.get("nodes", []) if n in nodes]
        for a, n, b in zip(nds, nds[1:], nds[2:]):
            if nodes[n].get("tags", {}).get("direction") in ("forward", "backward"):
                pa, pb = (np.array(to_local(nodes[x]["lon"], nodes[x]["lat"]), dtype=float) for x in (a, b))
                way_dir[n] = pb - pa
    junc = [nid for nid, dg in deg.items() if dg >= 3 and nid in nodes]
    jll = np.array([(nodes[n]["lon"], nodes[n]["lat"]) for n in junc])
    jxy = np.c_[to_local(jll[:, 0], jll[:, 1])]
    jtree = cKDTree(jxy)
    otree = cKDTree(np.c_[e0, n0])          # matching uses the ORIGINAL positions
    stree = cKDTree(np.c_[east, north])

    def surface_z(px, py):
        """Shipped banked road surface at (px, py), clamped to the pavement
        edge, from the nearest shipped station (verify_furniture_fit's test)."""
        k = stree.query((px, py))[1]
        off = (px - east[k]) * tn[k] - (py - north[k]) * te[k]
        return road_z[k] + bank[k] * min(max(off, -hw[k]), hw[k])

    def kind_of(tags):
        if tags.get("railway") == "level_crossing":
            return "level_crossing"
        return tags.get("highway")

    ctrl = [(nid, e, kind_of(e.get("tags", {}))) for nid, e in nodes.items()]
    ctrl = [c for c in ctrl if c[2] in ("traffic_signals", "stop", "give_way", "crossing", "level_crossing")]
    cxy = {nid: np.array(to_local(e["lon"], e["lat"]), dtype=float) for nid, e, _k in ctrl}
    hard = [cxy[nid] for nid, _e, k in ctrl if k in ("traffic_signals", "stop")]
    hard_tree = cKDTree(np.array(hard)) if hard else None
    controls, osm_skipped, osm_signs = [], Counter(), []
    for nid, e, kind in ctrl:
        tags = e.get("tags", {})
        if kind == "crossing" and (tags.get("crossing") in ("traffic_signals", "no", "unmarked")
                                   or (hard_tree is not None and hard_tree.query(cxy[nid])[0] < 30.0)):
            osm_skipped["crossing (signalised/unmarked/near a signal or stop)"] += 1
            continue
        if kind in ("traffic_signals", "stop", "give_way") and nid not in on_way:
            # mapped beside the road: belongs to a driveway / parking aisle
            # this network does not carry - on the main road it would stop
            # the wrong traffic
            osm_skipped[f"{kind} (not on a fetched road)"] += 1
            continue
        p = cxy[nid]
        near = otree.query_ball_point(p, SNAP_RADIUS_M)
        if not near:
            osm_skipped[f"{kind} (no road within {SNAP_RADIUS_M:.0f} m)"] += 1
            continue
        near = np.asarray(near)
        z_ref = elev[near[np.argmin(np.hypot(e0[near] - p[0], n0[near] - p[1]))]]
        best = {}                               # segment -> nearest station
        for i in near:
            if abs(elev[i] - z_ref) > LAYER_DZ_M:
                continue
            d = math.hypot(e0[i] - p[0], n0[i] - p[1])
            if seg_idx[i] not in best or d < best[seg_idx[i]][0]:
                best[seg_idx[i]] = (d, i)
        is_junction = deg.get(nid, 0) >= 3
        if not is_junction or kind in ("crossing", "level_crossing"):
            best = dict([min(best.items(), key=lambda kv: kv[1][0])])
        else:       # the junction's own arms, not a road passing nearby
            best = {s: v for s, v in best.items() if v[0] < JUNCTION_ARM_M} or best
        back_m = {s: 0.0 for s in best}
        if is_junction:
            # stop short of the widest crossing arm's edge
            target, back = p, None
            for s in best:
                back_m[s] = max([hw[i] for s2, (_d, i) in best.items() if s2 != s] or [0.0]) + JUNCTION_CLEAR_M
        elif kind in ("traffic_signals", "stop", "give_way") and nid in on_way:
            dj, jj = jtree.query(p)
            target, back = (jxy[jj], 0) if dj < JUNCTION_SEARCH_M else (p, 1)
        else:
            target, back = p, 1
        sign = {"traffic_signals": "signal", "stop": "stop", "give_way": "yield",
                "crossing": "ped_crossing", "level_crossing": "rail_crossbuck"}[kind]
        appr = []
        for s, (_d, k) in best.items():
            a = offsets[s]
            xy = np.c_[e0[a:offsets[s + 1]], n0[a:offsets[s + 1]]]
            nb = back if back is not None else int(math.ceil(back_m[s] / STATION_SPACING))
            along = 0.0 if back is not None else nb * STATION_SPACING - back_m[s]   # toward the node
            dirs = int(ow[k])
            if nid in way_dir:      # OSM direction= is along / against the way's node order
                fwd = 1 if te[k] * way_dir[nid][0] + tn[k] * way_dir[nid][1] > 0 else -1
                dirs = fwd if tags["direction"] == "forward" else -fwd
            for d, j in approach_dirs(xy, k - a, dirs, target, nb):
                i = a + j
                # the ORIGINAL station: a pad snap slides approach stations ~10 m
                # along the road, toward the node, so the setback (counted in
                # original stations) lands inside the crossing arm; z from the
                # shipped surface there (surface_z)
                x0, y0 = e0[i], n0[i]
                dz, kz = stree.query((x0, y0))
                appr.append([int(s), int(j), int(d)])
                # one pole per approach: a divided road's two signal nodes
                # (one per carriageway crossing) both reach the same approach
                if any(t == sign and math.hypot(x - x0, y - y0) < SIGN_DEDUPE_M
                       and fx * te[i] * d + fy * tn[i] * d > 0.7 for t, x, y, fx, fy in osm_signs):
                    continue
                osm_signs.append((sign, x0, y0, te[i] * d, tn[i] * d))
                put(sign, i, d, POLE_OFF_M, d, along=d * along, node=p, at=(x0, y0))
                # ponytail: where the pad also slid the road sideways (skewed
                # arms) the pole stands off it - base on terrain; snapping it to
                # the shipped edge is option B (next steps.md)
                props[-1][-1] = int(dz > hw[kz] + 3.0)
                if sign in ("signal", "stop"):
                    # stripe across the travel lane(s), on the road surface
                    w = 2 * hw[i] if ow[i] else hw[i]
                    o = 0.0 if ow[i] else d * hw[i] / 2
                    bx, by = x0 + tn[i] * o + te[i] * d * along, y0 - te[i] * o + tn[i] * d * along
                    props.append([T["stop_bar"], bx, by, surface_z(bx, by) + 0.01, yaw_deg(d * te[i], d * tn[i]), w, np.nan,
                                  0.0, 0.0, np.nan, np.nan, 0])
        if not appr:
            osm_skipped[f"{kind} (no approach reaches it)"] += 1
        if kind in ("traffic_signals", "stop", "give_way") and appr:
            controls.append({"id": nid, "kind": kind, "x_cm": round(p[0] * 100, 1),
                             "y_cm": round(-p[1] * 100, 1), "approaches": appr})

    # mapped street lamps: onto the nearest road's edge if they sit in it
    lamp_xy = []
    for nid, e in nodes.items():
        if e.get("tags", {}).get("highway") != "street_lamp":
            continue
        p = np.array(to_local(e["lon"], e["lat"]), dtype=float)
        d, i = otree.query(p)
        if d > 30.0:
            osm_skipped["street_lamp (no road within 30 m)"] += 1
            continue
        lamp_xy.append(p)
        side = 1.0 if (p[0] - e0[i]) * tn[i] - (p[1] - n0[i]) * te[i] >= 0 else -1.0
        if d < hw[i] + LAMP_OFF_M:
            put("street_light", i, side, LAMP_OFF_M, 0, yaw=yaw_deg(-side * tn[i], side * te[i]))
        else:
            props.append([T["street_light"], p[0], p[1], np.nan,
                          yaw_deg(-side * tn[i], side * te[i]), 1.0, road_z[i] + bank[i] * side * hw[i],
                          side * tn[i], -side * te[i], np.nan, np.nan, 1])
    lamp_tree = cKDTree(np.array(lamp_xy)) if lamp_xy else None

    # ---------------------------------------------------------- heuristic tier
    rails = []
    rail_path = TILE_DIR / "road_guardrails.json"
    if rail_path.exists():
        for r in json.loads(rail_path.read_text())["runs"]:
            xyz = np.array(r["station_xyz_cm"]).reshape(-1, 3)
            rails.append(np.c_[xyz[:, 0] / 100.0, -xyz[:, 1] / 100.0])
    rail_tree = cKDTree(np.concatenate(rails)) if rails else None

    speed_signs = []    # (east, north, face_e, face_n) for the dedupe
    for s, (a, b) in enumerate(zip(offsets[:-1], offsets[1:])):
        n = b - a
        if n < 3:
            continue
        idx = np.arange(a, b)
        dirs = (int(ow[a + n // 2]),) if ow[a + n // 2] else (1, -1)
        mph = np.round(speed[idx] / 1.609344)
        # speed: where the posted speed changes, and near the start of long segments
        cand = []
        for c in np.where(np.diff(mph) != 0)[0] + 1:
            cand += [(c + 2, 1), (c - 3, -1)]
        if n * STATION_SPACING >= SPEED_SEG_MIN_M:
            k0 = int(SPEED_START_M / STATION_SPACING)
            cand += [(k0, 1), (n - 1 - k0, -1)]
        for j, d in cand:
            if d not in dirs or not 0 <= j < n or struct[a + j]:
                continue
            i = a + j
            if any(math.hypot(x - east[i], y - north[i]) < SPEED_DEDUPE_M and fx * -d * te[i] + fy * -d * tn[i] > 0.5
                   for x, y, fx, fy in speed_signs):
                continue
            speed_signs.append((east[i], north[i], -d * te[i], -d * tn[i]))
            put("speed", i, d, POLE_OFF_M, d)
        # chevrons on the outside of tight curves, one board per travel direction
        if hclass[a] in RURAL_DELIN_CLASSES:
            mask, kappa = chevron_mask(np.c_[e0[idx], n0[idx]], speed[idx], struct[idx])
            step = int(CHEVRON_STEP_M / STATION_SPACING)
            for j in np.where(mask & ~town[idx])[0]:
                if j % step:
                    continue
                i = a + j
                side = 1.0 if kappa[j] > 0 else -1.0       # outside of a left turn = right
                for d in dirs:
                    left = kappa[j] * d > 0                 # the traveller turns left
                    put("chevron_l" if left else "chevron_r", i, side, POLE_OFF_M, d, along=-0.06 * d)
        # street lights, staggered by side
        lamp_step = int(LAMP_STEP_M / STATION_SPACING)
        for j in range(n):
            i = a + j
            if struct[i] or not (town[i] or hclass[i] == "motorway_link"):
                continue
            for side, phase in ((1.0, 0), (-1.0, lamp_step // 2)):
                if (j + s) % lamp_step != phase:
                    continue
                if lamp_tree is not None and lamp_tree.query((east[i], north[i]))[0] < LAMP_OSM_CLEAR_M:
                    continue
                put("street_light", i, side, LAMP_OFF_M, 0, yaw=yaw_deg(-side * tn[i], side * te[i]))
        # delineators on rural through roads without a rail
        delin_step = int(DELIN_STEP_M / STATION_SPACING)
        for j in range(0, n, delin_step):
            i = a + j
            if struct[i] or town[i] or hclass[i] not in RURAL_DELIN_CLASSES:
                continue
            for side in (1.0, -1.0):
                o = side * (hw[i] + DELIN_OFF_M)
                q = (east[i] + tn[i] * o, north[i] - te[i] * o)
                if rail_tree is not None and rail_tree.query(q)[0] < DELIN_RAIL_CLEAR_M + 0.3:
                    continue
                put("delineator", i, side, DELIN_OFF_M, side)

    # ---------------------------------------------------------- base z + pavement filter
    P = np.array(props, dtype=float)
    poles = P[:, 0] != T["stop_bar"]
    # a pole that lands in a pavement (a neighbouring road, a pad-snapped
    # approach) steps out from its own road up to PUSH_STEPS times first
    pav = pavement_index(east, north, road_z, hw, seg_idx, tunnel)
    inside = poles & in_pavement(pav, P[:, 1], P[:, 2], P[:, 6])
    for _ in range(PUSH_STEPS):
        k = np.where(inside)[0]
        P[k, 1:3] += PUSH_OUT_M * P[k, 7:9]
        inside[k] = in_pavement(pav, P[k, 1], P[k, 2], P[k, 6])
    # ... and a stop bar the same pad slide put off the road surface
    bars = P[:, 0] == T["stop_bar"]
    inside |= bars & ~in_pavement(pav, P[:, 1], P[:, 2], P[:, 3], clear=0.0)
    print(f"dropped {int(inside.sum())} prop(s) inside a pavement / bar off it: "
          + ", ".join(f"{TYPES[t]} {c}" for t, c in sorted(Counter(P[inside, 0].astype(int)).items())))
    # an OSM-tier pole pushed or set back past CONTROL_MAX_M from its node no
    # longer reads as that node's (verify_furniture_fit's node gate)
    far = np.hypot(P[:, 1] - P[:, 9], P[:, 2] - P[:, 10]) > CONTROL_MAX_M
    print(f"dropped {int(far.sum())} OSM-tier pole(s) over {CONTROL_MAX_M:.0f} m from their node: "
          + ", ".join(f"{TYPES[t]} {c}" for t, c in sorted(Counter(P[far, 0].astype(int)).items())))
    keep = ~inside & ~far
    P, poles = P[keep], poles[keep]
    pe, pn, edge_z = P[:, 1], P[:, 2], P[:, 6]
    terr = np.full(len(P), np.nan)
    terr[poles] = terrain_abs_m(pe[poles], pn[poles])
    use_t = poles & np.isfinite(terr) & ((np.abs(terr - edge_z) <= BASE_SNAP_M) | (P[:, 11] > 0))
    P[:, 3] = np.where(poles, np.where(use_t, terr, edge_z), P[:, 3])
    for k, v in sorted(osm_skipped.items()):
        print(f"  OSM node skipped: {k}: {v}")

    out = [[int(t), round(x * 100, 1), round(-y * 100, 1), round((z - ORIGIN_HEIGHT_M) * 100, 1),
            round(yaw, 2), round(sy, 3)] for t, x, y, z, yaw, sy, *_ in P]
    OUT_PATH.write_text(json.dumps({"types": TYPES, "props": out, "controls": controls}))
    counts = Counter(int(t) for t in P[:, 0])
    print(f"{len(out)} prop(s), {len(controls)} control node(s) -> {OUT_PATH}")
    for t, name in enumerate(TYPES):
        print(f"  {AREA_NAME:>16}  {name:<15} {counts.get(t, 0):6d}")


if __name__ == "__main__":
    main()
