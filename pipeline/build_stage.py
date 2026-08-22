"""Build UE-ready stage assets from raw fetched data.

Usage:  python build_stage.py

Reads   data/raw/<stage>/          (osm.json, dem_mosaic.npy, dem_meta.json)
Writes  data/stages/<stage>/
    heightmap.png    16-bit grayscale, 2017x2017, for UE Landscape import
    road_mesh.json   chunked vertex/triangle buffers (UE coords, cm) for the road
    stage_meta.json  landscape transform, spawn point, stats
    preview.png      hillshade + road overlay for eyeballing

Coordinate frame (matches the real-world georeferencing convention used
elsewhere in the project, so later phases are a drop-in): +X = east,
+Y = south, +Z = up, units cm, origin at stage center.
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from road_mesh_common import (
    ROAD_LIFT, STATION_SPACING, CHUNK_STATIONS,
    resample_stations, grade_smooth_elevation, road_class_grade_arrays,
    build_road_chunks,
)

# ---------------------------------------------------------------- config
STAGE = "tail_of_the_dragon"
START_LATLON = (35.46596, -83.91861)   # Deals Gap (US-129 / NC-28 junction)
END_LATLON = (35.5297, -83.9935)       # Tabcat Creek bridge (north end)

HEIGHTMAP_RES = 2017         # UE-friendly (63 quads, 2 sections, 16x16 comps)
MARGIN_M = 1000.0            # terrain margin around road bbox
INVALID_BELOW = 100.0        # DEM values below this are lake-bathymetry junk

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / STAGE
OUT = ROOT / "data" / "stages" / STAGE
OUT.mkdir(parents=True, exist_ok=True)

M_PER_DEG_LAT = 111132.0


# ---------------------------------------------------------------- helpers
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
def highway_class(tags):
    """This way's OSM highway= value - road_mesh_common.ROAD_CLASS_GRADE
    keys off this to pick per-class grade/grade-change limits (2026-07-28).
    Empty string (falls back to DEFAULT_ROAD_CLASS_GRADE) if untagged."""
    return tags.get("highway") or ""


def build_centerline():
    """Returns (latlon, highway_chain) - the resolved node polyline and its
    parallel per-node OSM highway= class (2026-07-28, so main() can build
    per-class grade/grade-change limits the same way build_road_stations.py
    does for the open-world network, instead of one flat constant)."""
    data = json.loads((RAW / "osm.json").read_text())
    nodes = {e["id"]: (e["lat"], e["lon"]) for e in data["elements"] if e["type"] == "node"}
    ways = [e for e in data["elements"] if e["type"] == "way"]

    def dist(a, b):
        return math.hypot((a[0] - b[0]) * M_PER_DEG_LAT,
                          (a[1] - b[1]) * M_PER_DEG_LAT * math.cos(math.radians(a[0])))

    # graph: way-endpoint node ids, edges = ways
    edges = []
    edge_highway = []
    adj = {}
    for i, w in enumerate(ways):
        nds = w["nodes"]
        length = sum(dist(nodes[a], nodes[b]) for a, b in zip(nds, nds[1:]))
        edges.append((nds[0], nds[-1], length, nds))
        edge_highway.append(highway_class(w.get("tags", {})))
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

    # walk back and assemble polyline (+ parallel per-node highway class)
    chain = []
    highway_chain = []
    node = dst
    while best[node][1] is not None:
        _, prev, ei = best[node]
        nds = edges[ei][3]
        seg = nds if nds[0] == prev else list(reversed(nds))
        hwy_seg = [edge_highway[ei]] * len(seg)
        chain = seg[1:] + chain if chain else seg
        highway_chain = hwy_seg[1:] + highway_chain if highway_chain else hwy_seg
        node = prev
    latlon = np.array([nodes[n] for n in chain])
    highway_chain = np.array(highway_chain, dtype=object)

    # precise trim to nearest vertices of the actual endpoints
    def nearest_idx(target):
        d = np.hypot((latlon[:, 0] - target[0]) * M_PER_DEG_LAT,
                     (latlon[:, 1] - target[1]) * M_PER_DEG_LAT
                     * math.cos(math.radians(target[0])))
        return int(d.argmin())

    i0, i1 = nearest_idx(START_LATLON), nearest_idx(END_LATLON)
    latlon = latlon[min(i0, i1): max(i0, i1) + 1]
    highway_chain = highway_chain[min(i0, i1): max(i0, i1) + 1]
    if i0 > i1:
        latlon = latlon[::-1]
        highway_chain = highway_chain[::-1]
    return latlon, highway_chain


# ---------------------------------------------------------------- main build
def main():
    dem = Dem()
    latlon, highway_chain = build_centerline()

    # local frame: origin at road bbox center; equirect projection
    lat0 = (latlon[:, 0].min() + latlon[:, 0].max()) / 2
    lon0 = (latlon[:, 1].min() + latlon[:, 1].max()) / 2
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    east = (latlon[:, 1] - lon0) * m_per_deg_lon
    north = (latlon[:, 0] - lat0) * M_PER_DEG_LAT

    # resample centerline at even spacing
    total_len = float(np.cumsum(np.hypot(np.diff(east), np.diff(north)))[-1])
    s, e, n = resample_stations(east, north, STATION_SPACING)
    n_st = len(s)

    # highway class is categorical (not a smooth 0..1 signal), so nearest-
    # original-station lookup - same pattern build_road_stations.py uses for
    # road_name_s/is_oneway_s - feeds road_class_grade_arrays() below so
    # grade/grade-change limits track the real OSM road class per station
    # instead of one flat constant for the whole route.
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
    right = np.clip(np.searchsorted(d, s), 0, len(d) - 1)
    left = np.clip(right - 1, 0, len(d) - 1)
    nearest_orig = np.where(np.abs(d[right] - s) <= np.abs(d[left] - s), right, left)
    highway_s = [highway_chain[j] for j in nearest_orig]
    max_grade_s, max_grade_delta_s = road_class_grade_arrays(highway_s)

    # vertical profile: sample DEM, grade-conform (ride the ground, deviate
    # only where max_grade forces it - never terrain-smoothed)
    lat_s = lat0 + n / M_PER_DEG_LAT
    lon_s = lon0 + e / m_per_deg_lon
    ground = dem.sample(lat_s, lon_s)
    h = grade_smooth_elevation(ground, STATION_SPACING, max_grade_s, max_grade_delta_s)
    ds = STATION_SPACING
    grades = np.abs(np.diff(h)) / ds
    z = h + ROAD_LIFT

    chunks, cx, cy, cz = build_road_chunks(s, e, n, z, name_prefix="road",
                                           spacing=STATION_SPACING)
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
