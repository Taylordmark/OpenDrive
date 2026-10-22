"""Drivable tunnels (PLAN-REALISM Phase 5): a closed concrete bore swept
along every OSM tunnel= run, a headwall at each portal, ceiling light strips,
and the terrain-hole test build_terrain_mesh_tiles.build_tile() applies so
the hillside never covers or cuts through a bore.

The road ribbon itself runs through tunnels like any other road
(build_road_network.py); this stage only adds the shell around it. Terrain
is NOT carved for tunnel stations (is_struct), so over the middle of a
tunnel the mountain stays; near a portal the approach carve and the DEM drop
the ground into the bore, and hole_mask() removes exactly those triangles.

Usage:  python build_tunnels.py   (needs road_stations.json)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/tunnel_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        OpenDriveUE/Content/OpenDrive/Masks/tunnellights_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        Both ODS1 (export_retaining_wall_bin.write_tile's mesh-chunk format),
        streamed by two extra AODWallManager instances (M_Bridge concrete,
        and the glowing MI_Furn_lamp) - see AODGameMode::SpawnAreaSet.

Culverts (PLAN-REALISM Phase 4, 2026-10-14) ride in the same concrete
tunnel_* tiles: build_water_tiles.py cuts a waterway ribbon short where it
crosses a road with no bridge (§7.27 gap); each end of that gap gets a pipe
mouth in a square headwall, sized to the stream and kept under the road.
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

from export_retaining_wall_bin import write_tile
from bake_job import job_items   # (2026-10-21) a pass-2 job writes only its own tiles
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import (ROAD_HALF_WIDTH, ROAD_LIFT, bank_slopes,
                              half_width_for_stations, road_ue_z_m)
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
WATERWAY_SUMMARY = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "waterway_build_summary.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

# Bore cross-section (cm, relative to the road centerline, sheared with the
# road's bank): flat floor just under the pavement, vertical walls, an
# elliptical arch. Clearance over the crown = WALL_H + ARCH_RISE.
SHOULDER_CM = 100.0      # wall stands this far outside the pavement edge
FLOOR_DROP_CM = 5.0      # floor under the pavement (the road ribbon sits on it)
WALL_H_CM = 450.0
ARCH_RISE_CM = 150.0
ARCH_SEGS = 6
SHELL_CM = 50.0          # concrete thickness - makes the bore a closed solid
# Portal headwall: a slab around the opening, THICK along the road.
HEAD_WING_CM = 300.0
HEAD_ABOVE_CM = 300.0
HEAD_BELOW_CM = 200.0
HEAD_THICK_CM = 60.0
# Terrain hole: everything within the outer shell plus this pad goes.
HOLE_PAD_CM = 50.0
HOLE_SAMPLE_CM = 100.0   # max spacing of the per-triangle 3D sample points
HOLE_CUT_CM = 100.0      # hit triangles are re-split this fine before the cut
MAX_SAMPLES = 2_000_000  # points per tri_samples batch (~50 MB float64)
DENSE_CM = 50.0          # centerline resampling for the hole test
CHUNK_STATIONS = 40      # stations per bore mesh chunk (~200 m)
LIGHT_W_CM = 30.0
LIGHT_EVERY = 2          # one strip per this many station gaps (~10 m)


def load_tunnels(stations_path=STATIONS_PATH):
    """One dict per contiguous is_tunnel run (>= 2 stations): UE-frame cm
    centerline c (K,2), unit tangent t / right normal r (K,2), pavement
    z (K,) cm, bank slope (K,), inner half-width w (K,) cm (pavement +
    shoulder). Same width and bank the road ribbon is built with."""
    runs = []
    for seg in json.loads(Path(stations_path).read_text())["segments"]:
        tun = np.array(seg.get("is_tunnel", [False] * len(seg["east_m"])), dtype=bool)
        if tun.sum() < 2:
            continue
        e, n = np.array(seg["east_m"]), np.array(seg["north_m"])
        hwy = seg.get("highway_class")
        hw = (half_width_for_stations(hwy, seg.get("oneway"), seg.get("lanes")) if hwy
              else np.full(len(e), ROAD_HALF_WIDTH))
        slope, rx, ry = bank_slopes(e, n)
        z = road_ue_z_m(np.array(seg["elev_m"]), ROAD_LIFT, ORIGIN_HEIGHT_M) * 100.0
        d = np.diff(np.concatenate([[0], tun.astype(int), [0]]))
        for lo, hi in zip(np.where(d == 1)[0], np.where(d == -1)[0]):
            if hi - lo < 2:
                continue
            sl = slice(lo, hi)
            runs.append({"name": seg["name"], "c": np.c_[e[sl], -n[sl]] * 100.0,
                         "t": np.c_[ry[sl], -rx[sl]], "r": np.c_[rx[sl], ry[sl]],
                         "z": z[sl], "slope": slope[sl], "w": hw[sl] * 100.0 + SHOULDER_CM})
    return runs


def bore_profile(w, outer=False):
    """Closed (off, dz) loop in cm: floor L, floor R, then the arch from the
    right wall top over to the left wall top. The outer loop has the same
    point count, offset by SHELL_CM, so the two pair up index by index."""
    s = SHELL_CM if outer else 0.0
    a = np.linspace(0.0, math.pi, ARCH_SEGS + 1)
    floor = [(-(w + s), -FLOOR_DROP_CM - s), (w + s, -FLOOR_DROP_CM - s)]
    return np.array(floor + list(zip((w + s) * np.cos(a), WALL_H_CM + (ARCH_RISE_CM + s) * np.sin(a))))


def headwall_profile(w):
    """Outer loop of the portal slab, index-paired with bore_profile(w):
    floor corners drop HEAD_BELOW, arch points push straight up to the top
    edge, so the slab is the band between the opening and this rectangle."""
    wh, top = w + HEAD_WING_CM, WALL_H_CM + ARCH_RISE_CM + HEAD_ABOVE_CM
    a = np.linspace(0.0, math.pi, ARCH_SEGS + 1)
    return np.array([(-wh, -HEAD_BELOW_CM), (wh, -HEAD_BELOW_CM)]
                    + [(wh * np.cos(x), top) for x in a])


def ue_volume(verts, tris):
    """Signed volume, positive when every UE front face (normal
    cross(v2-v0, v1-v0), terrain's fix_up_winding convention) points out."""
    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    return float(np.einsum("ij,ij->i", v0, np.cross(v2, v1)).sum() / 6.0)


def sweep(c, r, z, slope, inner, outer):
    """Closed solid between two index-paired loops swept through K frames:
    inner surface, outer surface and an annular cap at each end. inner/outer
    are (K,m,2) (off, dz) cm. Returns (verts (V,3), uvs (V,2), tris (T,3)),
    wound so UE front faces point out of the solid (into the bore inside)."""
    k_n, m = inner.shape[:2]

    def place(loop):
        off, dz = loop[..., 0], loop[..., 1]
        return np.stack([c[:, None, 0] + r[:, None, 0] * off, c[:, None, 1] + r[:, None, 1] * off,
                         z[:, None] + dz + slope[:, None] * off], axis=-1).reshape(-1, 3)

    verts = np.concatenate([place(inner), place(outer)])
    along = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(c, axis=0).T))])
    perim = np.concatenate([np.zeros((k_n, 1)), np.cumsum(np.hypot(
        *np.diff(inner, axis=1).transpose(2, 0, 1)), axis=1)], axis=1)
    uv = np.stack([perim, np.repeat(along[:, None], m, 1)], axis=-1).reshape(-1, 2) / 400.0
    uvs = np.concatenate([uv, uv])

    def I(k, i): return k * m + i % m
    def O(k, i): return k_n * m + k * m + i % m
    quads = []
    for i in range(m):
        for k in range(k_n - 1):
            quads.append((I(k, i), I(k, i + 1), I(k + 1, i + 1), I(k + 1, i)))
            quads.append((O(k, i), O(k + 1, i), O(k + 1, i + 1), O(k, i + 1)))
        last = k_n - 1
        quads.append((I(0, i), O(0, i), O(0, i + 1), I(0, i + 1)))
        quads.append((I(last, i), I(last, i + 1), O(last, i + 1), O(last, i)))
    q = np.array(quads)
    tris = np.concatenate([q[:, [0, 1, 2]], q[:, [0, 2, 3]]])
    if ue_volume(verts, tris) < 0:
        tris = tris[:, ::-1]
    return verts.astype(np.float32), uvs.astype(np.float32), tris.astype(np.uint32)


def bore_chunks(run):
    """The bore as closed CHUNK_STATIONS-long solids (consecutive chunks share
    their boundary frame; the coincident caps sit inside the shell)."""
    K = len(run["z"])
    out = []
    for a in range(0, K - 1, CHUNK_STATIONS):
        sl = slice(a, min(a + CHUNK_STATIONS, K - 1) + 1)
        w = run["w"][sl]
        out.append(sweep(run["c"][sl], run["r"][sl], run["z"][sl], run["slope"][sl],
                         np.stack([bore_profile(x) for x in w]),
                         np.stack([bore_profile(x, outer=True) for x in w])))
    return out


def headwalls(run):
    """One slab per portal, from the portal station outward along the road."""
    out = []
    for k, sign in ((0, -1.0), (len(run["z"]) - 1, 1.0)):
        c0 = run["c"][k]
        c = np.stack([c0, c0 + sign * run["t"][k] * HEAD_THICK_CM])
        two = lambda a: np.repeat(np.asarray(a)[None], 2, axis=0)
        w = run["w"][k]
        out.append(sweep(c, two(run["r"][k]), two(run["z"][k]), two(run["slope"][k]),
                         two(bore_profile(w)), two(headwall_profile(w))))
    return out


def light_strips(run):
    """Down-facing quads hung just under the crown, every LIGHT_EVERY station
    gaps, half a gap long each."""
    verts, tris = [], []
    dz = WALL_H_CM + ARCH_RISE_CM - 5.0
    for k in range(0, len(run["z"]) - 1, LIGHT_EVERY):
        p0, p1 = run["c"][k], (run["c"][k] + run["c"][k + 1]) / 2
        z0, z1 = run["z"][k] + dz, (run["z"][k] + run["z"][k + 1]) / 2 + dz
        h = run["r"][k] * LIGHT_W_CM / 2
        b = len(verts)
        verts += [(*(p0 - h), z0), (*(p0 + h), z0), (*(p1 + h), z1), (*(p1 - h), z1)]
        tris += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    verts, tris = np.array(verts, np.float32), np.array(tris, np.uint32)
    v0, v1, v2 = verts[tris[:, 0]], verts[tris[:, 1]], verts[tris[:, 2]]
    up = np.cross(v2 - v0, v1 - v0)[:, 2] > 0
    tris[up] = tris[up][:, ::-1]   # front face (UE normal) must point down
    uvs = np.zeros((len(verts), 2), np.float32)
    return verts, uvs, tris


# ------------------------------------------------------------------ culverts
CULVERT_SEGS = 16
CULVERT_R_MIN_M, CULVERT_R_MAX_M = 0.3, 1.2
CULVERT_WALL_M = 0.12      # pipe wall
CULVERT_HEAD_PAD_M = 0.6   # headwall square half-side = pipe outer radius + this
CULVERT_HEAD_THICK_M = 0.3
CULVERT_PIPE_IN_M = 1.5    # pipe runs this far into the embankment
CULVERT_PIPE_OUT_M = 0.2   # and sticks out of the headwall this much
CULVERT_ROAD_COVER_M = 0.3  # headwall top stays this far under the road
CULVERT_PAVE_CLEAR_M = 1.0  # headwall stays this far off the pavement edge


def circle(r_cm):
    a = np.linspace(0.0, 2.0 * math.pi, CULVERT_SEGS, endpoint=False)
    return np.c_[r_cm * np.cos(a), r_cm * np.sin(a)]


def square_like(r_cm, half_cm):
    """circle(r_cm)'s angles pushed out to a square of half-side half_cm,
    index-paired with it (the band between is the headwall face)."""
    c = circle(1.0)
    return c * half_cm / np.abs(c).max(axis=1, keepdims=True)


def culvert_mouth(p_cm, u, z_cm, r_m):
    """Pipe + headwall at mouth p_cm (UE cm, 2), u = unit UE direction into
    the embankment, z_cm = pipe centre. Two closed solids."""
    r = np.array([u[1], -u[0]])
    two = lambda a: np.repeat(np.asarray(a, float)[None], 2, axis=0)
    rc, wc = r_m * 100.0, (r_m + CULVERT_WALL_M) * 100.0
    pipe_c = np.stack([p_cm - u * CULVERT_PIPE_OUT_M * 100.0, p_cm + u * CULVERT_PIPE_IN_M * 100.0])
    head_c = np.stack([p_cm, p_cm + u * CULVERT_HEAD_THICK_M * 100.0])
    zz, sl = two(z_cm), np.zeros(2)
    return [sweep(pipe_c, two(r), zz, sl, two(circle(rc)), two(circle(wc))),
            sweep(head_c, two(r), zz, sl, two(circle(wc)),
                  two(square_like(wc, wc + CULVERT_HEAD_PAD_M * 100.0)))]


def culvert_sites(summary_path=WATERWAY_SUMMARY, stations_path=STATIONS_PATH):
    """One (p_cm, u, z_cm, r_m) per end of every §7.27 ribbon gap, with the
    gaps a crossing spreads over several resampled segments merged first.
    Returns (sites, n_skipped) - skipped where the road sits too low over the
    stream for even the smallest pipe."""
    if not Path(summary_path).is_file():
        return [], 0
    pts, zs, hws = [], [], []
    for seg in json.loads(Path(stations_path).read_text())["segments"]:
        e, n = np.array(seg["east_m"]), np.array(seg["north_m"])
        hwy = seg.get("highway_class")
        hw = (half_width_for_stations(hwy, seg.get("oneway"), seg.get("lanes")) if hwy
              else np.full(len(e), ROAD_HALF_WIDTH))
        pts.append(np.c_[e, n]); zs.append(road_ue_z_m(np.array(seg["elev_m"]), ROAD_LIFT, ORIGIN_HEIGHT_M))
        hws.append(np.asarray(hw, float))
    pts, zs, hws = np.vstack(pts), np.concatenate(zs), np.concatenate(hws)
    tree = cKDTree(pts)
    sites, n_skipped = [], 0
    for ch in json.loads(Path(summary_path).read_text()).get("chain_summaries", []):
        if not ch.get("seg_gaps"):
            continue
        e, n = np.array(ch["east_m"]), np.array(ch["north_m"])
        w = road_ue_z_m(np.array(ch["water_elev_m"]), 0.0, ORIGIN_HEIGHT_M)
        s = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(e), np.diff(n)))])
        iv = sorted((s[i] + a * (s[i + 1] - s[i]), s[i] + b * (s[i + 1] - s[i]))
                    for i, ivls in ch["seg_gaps"] for a, b in ivls)
        merged = []
        for lo, hi in iv:
            if merged and lo <= merged[-1][1] + 0.1:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            for at, sign in ((lo, 1.0), (hi, -1.0)):
                if at <= 0.0 or at >= s[-1]:
                    continue   # the stream starts/ends under the road
                k = min(int(np.searchsorted(s, at, side="right")) - 1, len(s) - 2)
                d = np.array([e[k + 1] - e[k], n[k + 1] - n[k]])
                d = d / (np.linalg.norm(d) or 1.0) * sign      # into the gap
                t = (at - s[k]) / ((s[k + 1] - s[k]) or 1.0)
                p = np.array([e[k], n[k]]) + (np.array([e[k + 1], n[k + 1]]) - [e[k], n[k]]) * t
                wz = w[k] + (w[k + 1] - w[k]) * t
                r_m = float(np.clip(0.6 * ch["half_width_m"], CULVERT_R_MIN_M, CULVERT_R_MAX_M))
                for _ in range(20):   # back off the pavement, 0.5 m a step
                    dist, j = tree.query(p)
                    pad = r_m + CULVERT_WALL_M + CULVERT_HEAD_PAD_M
                    if dist >= hws[j] + CULVERT_PAVE_CLEAR_M + pad:
                        break
                    p = p - d * 0.5
                road_z = zs[tree.query(p, k=min(8, len(zs)))[1]].min()
                # centre 0.3 r over the water; top = centre + r + wall + pad
                r_fit = (road_z - CULVERT_ROAD_COVER_M - wz - CULVERT_WALL_M - CULVERT_HEAD_PAD_M) / 1.3
                r_m = min(r_m, r_fit)
                if r_m < CULVERT_R_MIN_M:
                    n_skipped += 1
                    continue
                sites.append((np.array([p[0], -p[1]]) * 100.0, np.array([d[0], -d[1]]),
                              (wz + 0.3 * r_m) * 100.0, r_m))
    return sites, n_skipped


# ------------------------------------------------------------- terrain holes
def _dense(runs):
    """Every run resampled to DENSE_CM along: (KD-tree, points (N,2), attrs
    (N,6) = t x/y, z, slope, w, run id) for the nearest-centerline lookup."""
    pts, att = [], []
    for ri, run in enumerate(runs):
        s = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(run["c"], axis=0).T))])
        q = np.linspace(0.0, s[-1], max(2, int(s[-1] / DENSE_CM) + 1))
        lerp = lambda a: np.interp(q, s, a)
        t = np.c_[lerp(run["t"][:, 0]), lerp(run["t"][:, 1])]
        t /= np.maximum(np.hypot(t[:, 0], t[:, 1]), 1e-9)[:, None]
        pts.append(np.c_[lerp(run["c"][:, 0]), lerp(run["c"][:, 1])])
        att.append(np.c_[t, lerp(run["z"]), lerp(run["slope"]), lerp(run["w"]), np.full(len(q), ri)])
    pts = np.concatenate(pts)
    return cKDTree(pts), pts, np.concatenate(att)


def inside_bore(p, runs, pad_cm=HOLE_PAD_CM, index=None):
    """(N,3) cm points -> bool: inside the bore's outer shell grown by pad_cm
    (pad 0 = the solid bore volume). Measured against the nearest centerline
    point, cut flat at each portal plane."""
    if not runs or len(p) == 0:
        return np.zeros(len(p), bool)
    tree, pts, att = index if index is not None else _dense(runs)
    _, j = tree.query(p[:, :2])
    d = p[:, :2] - pts[j]
    t, z, slope, w = att[j, :2], att[j, 2], att[j, 3], att[j, 4]
    along = d[:, 0] * t[:, 0] + d[:, 1] * t[:, 1]
    lat = d[:, 0] * -t[:, 1] + d[:, 1] * t[:, 0]   # right of travel, UE frame
    wo = w + SHELL_CM
    dz = p[:, 2] - z - slope * lat
    top = WALL_H_CM + (ARCH_RISE_CM + SHELL_CM) * np.sqrt(np.clip(1.0 - (lat / wo) ** 2, 0.0, 1.0))
    return ((np.abs(along) <= DENSE_CM) & (np.abs(lat) <= wo + pad_cm)
            & (dz >= -FLOOR_DROP_CM - SHELL_CM - pad_cm) & (dz <= top + pad_cm))


def tri_samples(verts, tris, step_cm=HOLE_SAMPLE_CM):
    """Yields (triangle ids, (n_tri, S, 3) points) - a barycentric grid over
    each triangle in 3D (so vertical seam skirts count too), spaced <= step."""
    v = verts[tris].astype(np.float64)
    edge = np.max(np.linalg.norm(v - np.roll(v, 1, axis=1), axis=2), axis=1)
    levels = np.maximum(1, np.ceil(edge / step_cm)).astype(int)
    for n in np.unique(levels):
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
        ok = i + j <= n
        b = np.c_[i[ok], j[ok], n - i[ok] - j[ok]] / n
        all_ids = np.where(levels == n)[0]
        per = max(1, MAX_SAMPLES // len(b))   # bound memory: ~MAX_SAMPLES points per yield
        for k in range(0, len(all_ids), per):
            ids = all_ids[k:k + per]
            yield ids, np.einsum("sk,tkd->tsd", b, v[ids])


def hole_mask(verts, tris, runs, pad_cm=HOLE_PAD_CM):
    """bool per triangle: any of its sample points lies inside a bore (grown
    by pad_cm). Triangles nowhere near a tunnel are rejected by bbox first."""
    out = np.zeros(len(tris), bool)
    if not runs or len(tris) == 0:
        return out
    v = verts[tris]
    lo, hi = v.min(axis=1), v.max(axis=1)
    reach = WALL_H_CM + ARCH_RISE_CM + SHELL_CM + pad_cm + HEAD_WING_CM
    near = []
    for ri, run in enumerate(runs):
        rlo = np.r_[run["c"].min(0) - run["w"].max() - reach, run["z"].min() - reach]
        rhi = np.r_[run["c"].max(0) + run["w"].max() + reach, run["z"].max() + reach]
        if np.all((hi >= rlo) & (lo <= rhi), axis=1).any():
            near.append(ri)
    if not near:
        return out
    runs = [runs[i] for i in near]
    index = _dense(runs)
    # per-run boxes, not one union box: two far-apart tunnels on a tile would
    # otherwise put the whole tile in play (548M samples, OOM on oahu)
    keep = np.zeros(len(tris), bool)
    for r in runs:
        rlo = np.r_[r["c"].min(0) - r["w"].max(), r["z"].min()] - reach - 2000.0
        rhi = np.r_[r["c"].max(0) + r["w"].max(), r["z"].max()] + reach + 2000.0
        keep |= np.all((hi >= rlo) & (lo <= rhi), axis=1)
    cand = np.where(keep)[0]
    for ids, pts in tri_samples(verts, tris[cand]):
        hit = inside_bore(pts.reshape(-1, 3), runs, pad_cm, index).reshape(pts.shape[:2]).any(axis=1)
        out[cand[ids[hit]]] = True
    return out


def cut_holes(verts, uvs, tris, runs, pad_cm=HOLE_PAD_CM, step_cm=HOLE_CUT_CM):
    """Terrain mesh minus the bore: every triangle hole_mask() hits is
    re-split into a <= step_cm barycentric grid and only the sub-triangles
    that still hit are dropped, so the hole hugs the shell instead of
    following an 8 m adaptive-mesh triangle. ponytail: split triangles are
    not welded to their neighbours (T-junctions on exact edge points, no
    geometric crack)."""
    hit = hole_mask(verts, tris, runs, pad_cm)
    if not hit.any():
        return verts, uvs, tris
    par_all = tris[hit]
    sv, su, st = [verts], [uvs], []
    base = len(verts)
    for ids, pv in tri_samples(verts, par_all, step_cm):
        par = par_all[ids]
        n = round((math.sqrt(1 + 8 * pv.shape[1]) - 3) / 2)   # S = (n+1)(n+2)/2
        # same barycentric grid order as tri_samples (meshgrid i,j; i+j <= n)
        i, j = np.meshgrid(np.arange(n + 1), np.arange(n + 1))
        ok = i + j <= n
        idx = -np.ones((n + 1, n + 1), int)
        idx[ok] = np.arange(ok.sum())          # idx[j, i]
        b = np.c_[i[ok], j[ok], n - i[ok] - j[ok]] / n
        loc = []
        for jj in range(n):
            for ii in range(n - jj):
                loc.append((idx[jj, ii], idx[jj, ii + 1], idx[jj + 1, ii]))
                if ii + jj <= n - 2:
                    loc.append((idx[jj, ii + 1], idx[jj + 1, ii + 1], idx[jj + 1, ii]))
        loc = np.array(loc)
        pu = np.einsum("sk,tkd->tsd", b, uvs[par].astype(np.float64))
        sub = base + np.arange(len(par))[:, None, None] * len(b) + loc[None]   # (t, L, 3)
        # each sub-triangle keeps its parent's winding (UE normal direction)
        pv64 = pv.astype(np.float64)
        p0, p1, p2 = (pv64[:, loc[:, q]] for q in range(3))
        sn = np.cross(p2 - p0, p1 - p0)
        v = verts[par].astype(np.float64)
        pn = np.cross(v[:, 2] - v[:, 0], v[:, 1] - v[:, 0])
        flip = np.einsum("tld,td->tl", sn, pn) < 0
        sub[flip] = sub[flip][:, ::-1]
        sv.append(pv.reshape(-1, 3).astype(np.float32))
        su.append(pu.reshape(-1, 2).astype(np.float32))
        st.append(sub.reshape(-1, 3))
        base += pv.shape[0] * pv.shape[1]
    verts, uvs, sub = np.concatenate(sv), np.concatenate(su), np.concatenate(st)
    sub = sub[~hole_mask(verts, sub, runs, pad_cm)]
    return verts, uvs, np.concatenate([tris[~hit], sub]).astype(np.uint32)


def main():
    runs = load_tunnels()
    tile = TERRAIN_TILE_SIZE_CM
    buckets = {"tunnel": defaultdict(list), "tunnellights": defaultdict(list)}

    def add(kind, mesh, at):
        v, uv, t = mesh
        key = (int(math.floor(at[0] / tile)), int(math.floor(at[1] / tile)))
        buckets[kind][key].append({"name": kind, "vertices": v.reshape(-1).tolist(),
                                   "uvs": uv.reshape(-1).tolist(), "triangles": t.reshape(-1).tolist()})

    km = 0.0
    for run in runs:
        km += float(np.hypot(*np.diff(run["c"], axis=0).T).sum()) / 1e5
        for mesh in bore_chunks(run) + headwalls(run):
            add("tunnel", mesh, mesh[0][:, :2].mean(axis=0))
        add("tunnellights", light_strips(run), run["c"][len(run["c"]) // 2])
    sites, n_culvert_skipped = culvert_sites()
    for p_cm, u, z_cm, r_m in sites:
        for mesh in culvert_mouth(p_cm, u, z_cm, r_m):
            add("tunnel", mesh, p_cm)
    print(f"culverts: {len(sites)} mouth(s), {n_culvert_skipped} skipped (road too low "
          f"over the stream for a {CULVERT_R_MIN_M:.1f} m pipe)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id_bytes = pack_generation_id(compute_generation_id())
    for kind, tiles in buckets.items():
        written = []
        for (tx, ty), chunks in job_items(tiles, TERRAIN_TILE_SIZE_CM):
            path = OUT_DIR / f"{kind}_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
            write_tile(path, chunks, gen_id_bytes)
            written.append(path)
        n_stale = delete_stale_tiles(OUT_DIR, f"{kind}_{AREA_NAME}_test_tile_*.bin", written)
        print(f"{kind}: {sum(len(c) for c in tiles.values())} chunk(s) -> {len(tiles)} tile(s), "
              f"{n_stale} stale deleted")
    print(f"{len(runs)} tunnel run(s), {km:.2f} km of bore, {2 * len(runs)} portal(s) -> {OUT_DIR}")


if __name__ == "__main__":
    main()
