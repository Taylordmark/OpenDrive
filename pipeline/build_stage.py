"""Build UE-ready stage assets from raw fetched data.

Usage:  python build_stage.py

Reads   data/raw/<stage>/          (osm.json, dem_mosaic.npy, dem_meta.json)
Writes  data/stages/<stage>/
    heightmap.png    16-bit grayscale, 2017x2017, for UE Landscape import
    road_mesh.json   chunked vertex/triangle buffers (UE coords, cm) for the road
    stage_meta.json  landscape transform, spawn point, stats
    preview.png      hillshade + road overlay for eyeballing

Coordinate frame (matches Cesium for Unreal georeferencing, so Phase 2 is a
drop-in): +X = east, +Y = south, +Z = up, units cm, origin at stage center.
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

# ---------------------------------------------------------------- config
STAGE = "tail_of_the_dragon"
START_LATLON = (35.46596, -83.91861)   # Deals Gap (US-129 / NC-28 junction)
END_LATLON = (35.5297, -83.9935)       # Tabcat Creek bridge (north end)

STATION_SPACING = 5.0        # m between road samples
ROAD_HALF_WIDTH = 4.5        # m, asphalt edge
SKIRT_EXTRA = 3.0            # m outward for embankment skirt
SKIRT_DROP = 2.5             # m downward at skirt edge
ROAD_LIFT = 0.10             # m above smoothed ground (wishlist #11: keep the
                             # road close to terrain; carve sits at -0.15)
MAX_BANK_RAD = 0.09          # ~5.2 deg superelevation cap
BANK_GAIN = 9.55             # bank = min(cap, gain * |curvature 1/m|)
MAX_GRADE = 0.10             # grade clamp after smoothing
HEIGHTMAP_RES = 2017         # UE-friendly (63 quads, 2 sections, 16x16 comps)
MARGIN_M = 1000.0            # terrain margin around road bbox
CHUNK_STATIONS = 150         # stations per road mesh chunk (~750 m)
INVALID_BELOW = 100.0        # DEM values below this are lake-bathymetry junk

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / STAGE
OUT = ROOT / "data" / "stages" / STAGE
OUT.mkdir(parents=True, exist_ok=True)

M_PER_DEG_LAT = 111132.0


# ---------------------------------------------------------------- helpers
def gaussian1d(arr, sigma):
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(arr, r, mode="edge"), k, mode="valid")


def carve_road(hm, e, n, h, e0, n0, mpp, inner=10.0, outer=30.0):
    """Stamp road elevation into the heightmap within `inner` m of the
    centerline, blending to raw terrain at `outer` m. Guarantees terrain
    never pokes through the (grade-smoothed) road surface."""
    res = hm.shape[0]
    dist = np.full(hm.shape, np.inf, dtype=np.float32)
    roadh = np.zeros_like(hm)
    r = int(math.ceil(outer / mpp)) + 1
    for i in range(len(e)):
        px = (e[i] - e0) / mpp
        py = (n0 - n[i]) / mpp
        x0, x1 = max(0, int(px) - r), min(res, int(px) + r + 1)
        y0, y1 = max(0, int(py) - r), min(res, int(py) + r + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d = np.hypot((xx - px) * mpp, (yy - py) * mpp)
        sub = dist[y0:y1, x0:x1]
        m = d < sub
        sub[m] = d[m]
        roadh[y0:y1, x0:x1][m] = h[i]
    w = np.clip((outer - dist) / (outer - inner), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    return w * (roadh - 0.15) + (1 - w) * hm


def median1d(arr, win):
    r = win // 2
    padded = np.pad(arr, r, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, win), axis=1)


# ---------------------------------------------------------------- DEM
class Dem:
    def __init__(self):
        self.meta = json.loads((RAW / "dem_meta.json").read_text())
        self.grid = np.load(RAW / "dem_mosaic.npy")
        self._fill_invalid()

    def _fill_invalid(self):
        bad = self.grid < INVALID_BELOW
        n_bad = int(bad.sum())
        if not n_bad:
            return
        print(f"DEM: infilling {n_bad} lake-bathymetry pixels")
        # coarse fill: downsample 8x ignoring invalid, iteratively dilate valid
        g = self.grid.copy()
        g[bad] = np.nan
        h, w = g.shape
        coarse = np.nanmean(g[: h // 8 * 8, : w // 8 * 8].reshape(h // 8, 8, w // 8, 8), axis=(1, 3))
        for _ in range(200):
            nan = np.isnan(coarse)
            if not nan.any():
                break
            p = np.pad(coarse, 1, mode="edge")
            stack = np.stack([p[1:-1, :-2], p[1:-1, 2:], p[:-2, 1:-1], p[2:, 1:-1]])
            fill = np.nanmean(stack, axis=0)
            coarse[nan] = fill[nan]
        up = np.repeat(np.repeat(coarse, 8, 0), 8, 1)
        up_full = np.empty_like(self.grid)
        up_full[: up.shape[0], : up.shape[1]] = up
        up_full[up.shape[0]:, :] = up_full[up.shape[0] - 1: up.shape[0], :]
        up_full[:, up.shape[1]:] = up_full[:, up.shape[1] - 1: up.shape[1]]
        self.grid[bad] = up_full[bad]

    def sample(self, lat, lon):
        """Bilinear sample, vectorized. lat/lon arrays -> meters."""
        z = self.meta["zoom"]
        n = 256 * (2 ** z)
        px = (lon + 180.0) / 360.0 * n - self.meta["origin_px"][0]
        lat_r = np.radians(lat)
        py = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n \
            - self.meta["origin_px"][1]
        h, w = self.grid.shape
        px = np.clip(px, 0, w - 1.001)
        py = np.clip(py, 0, h - 1.001)
        x0 = px.astype(int); y0 = py.astype(int)
        fx = px - x0; fy = py - y0
        g = self.grid
        return (g[y0, x0] * (1 - fx) * (1 - fy) + g[y0, x0 + 1] * fx * (1 - fy)
                + g[y0 + 1, x0] * (1 - fx) * fy + g[y0 + 1, x0 + 1] * fx * fy)


# ---------------------------------------------------------------- OSM -> centerline
def build_centerline():
    data = json.loads((RAW / "osm.json").read_text())
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"] if e["type"] == "way"]

    def dist(a, b):
        return math.hypot((a[0] - b[0]) * M_PER_DEG_LAT,
                          (a[1] - b[1]) * M_PER_DEG_LAT * math.cos(math.radians(a[0])))

    # graph: way-endpoint node ids, edges = ways
    edges = []
    adj = {}
    for i, w in enumerate(ways):
        nds = w["nodes"]
        length = sum(dist(nodes[a], nodes[b]) for a, b in zip(nds, nds[1:]))
        edges.append((nds[0], nds[-1], length, nds))
        adj.setdefault(nds[0], []).append(i)
        adj.setdefault(nds[-1], []).append(i)

    def nearest_endpoint(target):
        return min(adj.keys(), key=lambda nid: dist(nodes[nid], target))

    src = nearest_endpoint(START_LATLON)
    dst = nearest_endpoint(END_LATLON)

    # dijkstra over way graph
    import heapq
    best = {src: (0.0, None, None)}  # node -> (cost, prev_node, via_edge)
    pq = [(0.0, src)]
    while pq:
        c, u = heapq.heappop(pq)
        if u == dst:
            break
        if c > best[u][0]:
            continue
        for ei in adj.get(u, []):
            a, b, length, _ = edges[ei]
            v = b if u == a else a
            nc = c + length
            if v not in best or nc < best[v][0]:
                best[v] = (nc, u, ei)
                heapq.heappush(pq, (nc, v))
    if dst not in best:
        raise SystemExit("No path between stage endpoints in OSM data.")

    # walk back and assemble polyline
    chain = []
    node = dst
    while best[node][1] is not None:
        _, prev, ei = best[node]
        nds = edges[ei][3]
        seg = nds if nds[0] == prev else list(reversed(nds))
        chain = seg[1:] + chain if chain else seg
        node = prev
    latlon = np.array([nodes[n] for n in chain])

    # precise trim to nearest vertices of the actual endpoints
    def nearest_idx(target):
        d = np.hypot((latlon[:, 0] - target[0]) * M_PER_DEG_LAT,
                     (latlon[:, 1] - target[1]) * M_PER_DEG_LAT
                     * math.cos(math.radians(target[0])))
        return int(d.argmin())

    i0, i1 = nearest_idx(START_LATLON), nearest_idx(END_LATLON)
    latlon = latlon[min(i0, i1): max(i0, i1) + 1]
    if i0 > i1:
        latlon = latlon[::-1]
    return latlon


# ---------------------------------------------------------------- main build
def main():
    dem = Dem()
    latlon = build_centerline()

    # local frame: origin at road bbox center; equirect projection
    lat0 = (latlon[:, 0].min() + latlon[:, 0].max()) / 2
    lon0 = (latlon[:, 1].min() + latlon[:, 1].max()) / 2
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    east = (latlon[:, 1] - lon0) * m_per_deg_lon
    north = (latlon[:, 0] - lat0) * M_PER_DEG_LAT

    # resample centerline at even spacing
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
    total_len = d[-1]
    s = np.arange(0, total_len, STATION_SPACING)
    e = np.interp(s, d, east)
    n = np.interp(s, d, north)
    e = gaussian1d(e, 1.2)   # break polyline corners, preserve hairpins
    n = gaussian1d(n, 1.2)

    # vertical profile: sample DEM, smooth, clamp grade
    lat_s = lat0 + n / M_PER_DEG_LAT
    lon_s = lon0 + e / m_per_deg_lon
    ground = dem.sample(lat_s, lon_s)
    h = median1d(ground, 11)
    h = gaussian1d(h, 4.0)
    ds = STATION_SPACING
    for _ in range(4):  # grade clamp: lower peaks in both directions
        for i in range(1, len(h)):
            h[i] = min(h[i], h[i - 1] + MAX_GRADE * ds)
        for i in range(len(h) - 2, -1, -1):
            h[i] = min(h[i], h[i + 1] + MAX_GRADE * ds)
    h = gaussian1d(h, 2.0)
    grades = np.abs(np.diff(h)) / ds
    z = h + ROAD_LIFT

    # UE frame: X=east, Y=south(-north), Z=up, cm
    cx, cy, cz = e * 100.0, -n * 100.0, z * 100.0
    tx = np.gradient(cx); ty = np.gradient(cy)
    tl = np.hypot(tx, ty); tx /= tl; ty /= tl
    rx, ry = -ty, tx          # right of travel, horizontal

    # superelevation: raise the OUTER edge through curves (inner edge stays at
    # road height, so the carved terrain can never poke through the low side)
    theta = np.unwrap(np.arctan2(ty, tx))
    curv = np.gradient(theta) / STATION_SPACING          # 1/m, signed
    bank = np.minimum(MAX_BANK_RAD, BANK_GAIN * np.abs(gaussian1d(curv, 3.0)))
    bank_tan = np.tan(gaussian1d(bank, 3.0))
    raise_side = -np.sign(gaussian1d(curv, 3.0))         # +1: raise right side

    # cross-section: (lateral offset m, z offset m), left -> right
    profile = [(-(ROAD_HALF_WIDTH + SKIRT_EXTRA), -SKIRT_DROP),
               (-ROAD_HALF_WIDTH, 0.0),
               (ROAD_HALF_WIDTH, 0.0),
               (ROAD_HALF_WIDTH + SKIRT_EXTRA, -SKIRT_DROP)]
    n_prof = len(profile)

    def station_verts(i):
        out = []
        for off, dz in profile:
            lift = bank_tan[i] * max(0.0, off * raise_side[i])  # outer-edge raise
            out.append((cx[i] + rx[i] * off * 100.0,
                        cy[i] + ry[i] * off * 100.0,
                        cz[i] + (dz + lift) * 100.0))
        return out

    # chunked mesh buffers; winding (A, A+right, A+forward) faces up in UE
    chunks = []
    n_st = len(s)
    for c0 in range(0, n_st - 1, CHUNK_STATIONS):
        c1 = min(c0 + CHUNK_STATIONS, n_st - 1)
        verts, tris, uvs = [], [], []
        for i in range(c0, c1 + 1):
            verts.extend(station_verts(i))
            v = s[i] / 20.0
            uvs.extend([(j / (n_prof - 1), v) for j in range(n_prof)])
        for irow in range(c1 - c0):
            b = irow * n_prof
            for j in range(n_prof - 1):
                p, q = b + j, b + j + n_prof
                tris.append((p, p + 1, q))
                tris.append((p + 1, q + 1, q))
        chunks.append({
            "name": f"road_{c0:04d}",
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "triangles": [i for t in tris for i in t],
        })
    (OUT / "road_mesh.json").write_text(json.dumps({"chunks": chunks}))

    # ---------------- heightmap ----------------
    side = max(east.max() - east.min(), north.max() - north.min()) + 2 * MARGIN_M
    ce_c = (east.max() + east.min()) / 2
    cn_c = (north.max() + north.min()) / 2
    mpp = side / (HEIGHTMAP_RES - 1)
    east_g = ce_c - side / 2 + np.arange(HEIGHTMAP_RES) * mpp
    north_g = cn_c + side / 2 - np.arange(HEIGHTMAP_RES) * mpp   # row 0 = north edge
    lon_g = lon0 + east_g / m_per_deg_lon
    lat_g = lat0 + north_g / M_PER_DEG_LAT
    lon_m, lat_m = np.meshgrid(lon_g, lat_g)
    hm = dem.sample(lat_m, lon_m)
    # narrow blend: canyon walls resume right at the shoulder (cut-wall feel)
    hm = carve_road(hm, e, n, h, east_g[0], north_g[0], mpp,
                    inner=6.5, outer=13.0)
    zmin, zmax = float(hm.min()), float(hm.max())
    hm16 = np.round((hm - zmin) / (zmax - zmin) * 65535).astype(np.uint16)
    Image.fromarray(hm16, mode="I;16").save(OUT / "heightmap.png")
    # RG-split copy for automated in-engine heightmap updates (R=hi, G=lo)
    rg = np.zeros((*hm16.shape, 4), dtype=np.uint8)
    rg[:, :, 0] = hm16 >> 8
    rg[:, :, 1] = hm16 & 0xFF
    rg[:, :, 3] = 255
    Image.fromarray(rg, mode="RGBA").save(OUT / "heightmap_rg.png")

    # UE landscape transform (v=32768 maps to actor Z; 16-bit range = 512m @ scale 100)
    landscape = {
        "location_cm": [round((ce_c - side / 2) * 100, 1),
                        round((-cn_c - side / 2) * 100, 1),
                        round((zmin + zmax) / 2 * 100, 1)],
        "scale": [round(mpp * 100, 4), round(mpp * 100, 4),
                  round((zmax - zmin) / 512 * 100, 4)],
        "resolution": HEIGHTMAP_RES,
    }

    # ---------------- meta + spawn ----------------
    yaw = math.degrees(math.atan2(cy[6] - cy[1], cx[6] - cx[1]))
    meta = {
        "stage": STAGE,
        "origin_latlon": [lat0, lon0],
        "landscape": landscape,
        "road": {
            "length_km": round(total_len / 1000, 2),
            "stations": n_st,
            "chunks": len(chunks),
            "elev_min_max_m": [round(float(z.min()), 1), round(float(z.max()), 1)],
            "max_grade_pct": round(float(grades.max()) * 100, 1),
            "mean_grade_pct": round(float(grades.mean()) * 100, 1),
        },
        "player_start": {
            "location_cm": [round(cx[1], 1), round(cy[1], 1), round(cz[1] + 120, 1)],
            "yaw_deg": round(yaw, 1),
        },
    }
    (OUT / "stage_meta.json").write_text(json.dumps(meta, indent=2))

    # ---------------- preview ----------------
    gy, gx = np.gradient(hm, mpp)
    az, alt = math.radians(315), math.radians(45)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    img = np.clip((shade + 1) / 2 * 255, 0, 255).astype(np.uint8)
    rgb = np.stack([img] * 3, axis=-1)
    px = ((e - (ce_c - side / 2)) / mpp).astype(int)
    py = (((cn_c + side / 2) - n) / mpp).astype(int)
    ok = (px >= 1) & (px < HEIGHTMAP_RES - 1) & (py >= 1) & (py < HEIGHTMAP_RES - 1)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            rgb[py[ok] + dy, px[ok] + dx] = (255, 40, 40)
    rgb[py[1] - 6: py[1] + 6, px[1] - 6: px[1] + 6] = (0, 220, 0)
    rgb[py[-1] - 6: py[-1] + 6, px[-1] - 6: px[-1] + 6] = (40, 90, 255)
    Image.fromarray(rgb).save(OUT / "preview.png")

    print(json.dumps(meta, indent=2))
    print("Stage assets ->", OUT)


if __name__ == "__main__":
    main()
