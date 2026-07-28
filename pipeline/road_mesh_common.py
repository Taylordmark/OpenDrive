"""Shared road-mesh math: station resampling, grade-smoothed elevation,
superelevation banking, skirt cross-section, and chunked vertex/UV/triangle
mesh assembly.

Extracted from build_stage.py (Arc 7.2 slice 1) so build_road_network.py can
build meshes for an arbitrary OSM road network without duplicating this math.
Behavior is unchanged from the original inline code in build_stage.py.

Coordinate frame (matches the real-world georeferencing convention used
elsewhere in the project): +X = east,
+Y = south, +Z = up, units cm. Callers project lat/lon to a local east/north
meter frame around a shared origin before calling into this module.
"""

import math

import numpy as np

STATION_SPACING = 5.0        # m between road samples
ROAD_HALF_WIDTH = 4.5        # m, asphalt edge
SKIRT_EXTRA = 3.0            # m outward for embankment skirt
SKIRT_DROP = 2.5             # m downward at skirt edge
ROAD_LIFT = 0.10             # m above smoothed ground (wishlist #11: keep the
                             # road close to terrain; build_terrain_tiles.py's
                             # CARVE_DROP_M carves the terrain further below)
MAX_BANK_RAD = 0.09          # ~5.2 deg superelevation cap
BANK_GAIN = 9.55             # bank = min(cap, gain * |curvature 1/m|)
MAX_GRADE = 0.10             # grade clamp after smoothing
CHUNK_STATIONS = 150         # stations per road mesh chunk (~750 m)


def gaussian1d(arr, sigma):
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(arr, r, mode="edge"), k, mode="valid")


def median1d(arr, win):
    r = win // 2
    padded = np.pad(arr, r, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, win), axis=1)


def resample_stations(east, north, spacing=STATION_SPACING):
    """Resample an east/north polyline (m) to even arc-length spacing, then
    Gaussian-smooth laterally (breaks polyline corners, preserves hairpins).
    Returns (s, e, n): station distances (m) + smoothed east/north (m)."""
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
    total_len = d[-1]
    s = np.arange(0, total_len, spacing)
    e = np.interp(s, d, east)
    n = np.interp(s, d, north)
    e = gaussian1d(e, 1.2)
    n = gaussian1d(n, 1.2)
    return s, e, n


def grade_smooth_elevation(ground, spacing=STATION_SPACING, max_grade=MAX_GRADE):
    """Median filter, Gaussian smooth, 4-pass forward/backward grade clamp,
    final Gaussian smooth. `ground` is raw per-station DEM samples (m);
    returns the smoothed ground-height array (m)."""
    h = median1d(ground, 11)
    h = gaussian1d(h, 4.0)
    for _ in range(4):  # grade clamp: lower peaks in both directions
        for i in range(1, len(h)):
            h[i] = min(h[i], h[i - 1] + max_grade * spacing)
        for i in range(len(h) - 2, -1, -1):
            h[i] = min(h[i], h[i + 1] + max_grade * spacing)
    h = gaussian1d(h, 2.0)
    return h


def build_road_chunks(s, e, n, z, name_prefix="road", spacing=STATION_SPACING,
                       chunk_stations=CHUNK_STATIONS,
                       road_half_width=ROAD_HALF_WIDTH, skirt_extra=SKIRT_EXTRA,
                       skirt_drop=SKIRT_DROP, max_bank_rad=MAX_BANK_RAD,
                       bank_gain=BANK_GAIN, is_oneway=None):
    """Build banked, skirted road mesh chunks from station arrays.

    `s` is the per-station arc-length distance (m, from resample_stations).
    `e`, `n`, `z` are smoothed per-station east/north/elevation in meters
    (local equirect frame; `z` already includes ROAD_LIFT). `is_oneway`
    (optional, per-station bool array) picks the chunk's "oneway" output
    flag by majority vote across its stations - a chunk boundary essentially
    never lands mid-carriageway (chains only break at real OSM junctions),
    so this is just a tie-breaker, not a meaningfully lossy reduction.
    Returns (chunks, cx, cy, cz) where chunks matches road_mesh.json's
    {"name","vertices","uvs","triangles","oneway"} schema and cx/cy/cz are
    the UE-frame (cm) station centerline arrays for the caller's own use
    (yaw calc, player start, stats).
    """
    # UE frame: X=east, Y=south(-north), Z=up, cm
    cx, cy, cz = e * 100.0, -n * 100.0, z * 100.0
    tx = np.gradient(cx); ty = np.gradient(cy)
    tl = np.hypot(tx, ty); tx /= tl; ty /= tl
    rx, ry = -ty, tx          # right of travel, horizontal

    # superelevation: raise the OUTER edge through curves (inner edge stays at
    # road height, so the carved terrain can never poke through the low side)
    theta = np.unwrap(np.arctan2(ty, tx))
    curv = np.gradient(theta) / spacing          # 1/m, signed
    bank = np.minimum(max_bank_rad, bank_gain * np.abs(gaussian1d(curv, 3.0)))
    bank_tan = np.tan(gaussian1d(bank, 3.0))
    raise_side = -np.sign(gaussian1d(curv, 3.0))         # +1: raise right side

    # cross-section: (lateral offset m, z offset m), left -> right
    profile = [(-(road_half_width + skirt_extra), -skirt_drop),
               (-road_half_width, 0.0),
               (road_half_width, 0.0),
               (road_half_width + skirt_extra, -skirt_drop)]
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
    for c0 in range(0, n_st - 1, chunk_stations):
        c1 = min(c0 + chunk_stations, n_st - 1)
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
        oneway = bool(np.mean(is_oneway[c0:c1 + 1]) > 0.5) if is_oneway is not None else False
        chunks.append({
            "name": f"{name_prefix}_{c0:04d}",
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "triangles": [i for t in tris for i in t],
            "oneway": oneway,
        })
    return chunks, cx, cy, cz


def road_ue_z_m(elev_m, road_lift_m, origin_height_m):
    """Ground-smoothed absolute elevation (m, DEM frame) -> UE-world Z (m,
    the old georeference actor's CARTOGRAPHIC_ORIGIN frame). One formula,
    shared by build_road_network.py (writing road_mesh.json) and
    verify_terrain_fit.py (checking the built terrain against it) so the two
    can't independently
    drift - the ~800 m elevation-frame bug (2026-07-19) was exactly this
    shift living in one place and being wrong, not two places disagreeing,
    but keeping it in one function removes the *chance* of the latter."""
    return elev_m + road_lift_m - origin_height_m


def carve_heightmap(hm, station_lon, station_lat, station_elev_m,
                     west, south, east, north, inner=10.0, outer=40.0,
                     drop=0.15):
    """Stamp road elevation into an elevation grid within `inner` m of the
    nearest road station, blending to raw terrain at `outer` m - the same
    inverse-distance-stamp + smoothstep blend as build_stage.py's
    carve_road(), generalized from a fixed local-mpp grid to an arbitrary
    lon/lat-bounded grid (a Terrarium/quantized-mesh tile at any zoom).

    `hm` is (rows, cols), row 0 = the `north` edge, col 0 = the `west` edge.
    Callers building a pymartini/quantized-mesh tile MUST pass the array
    AFTER the `.T` pymartini expects for `create_tile`/`rescale_positions`,
    not before - pymartini indexes whatever array it's actually given with
    standard row=y/col=x semantics, and that array is the transposed one.
    Carving before the transpose stamps every station at its diagonal-mirror
    position instead of its real one (found 2026-07-20 via two stations that
    measured 60 m and 444 m off; both dropped under 1 m once the carve moved
    to after the transpose - confirmed with a controlled non-planar 2-D
    gradient test, not assumed). `station_lon/lat/elev_m` are 1-D arrays for
    every road station anywhere near this tile (the caller should prefilter
    by bbox for speed; a station outside `outer` of every pixel is a no-op
    either way, this is a correctness fallback not a requirement).

    `drop` (m) biases the carved surface slightly below the actual road
    surface so it can never poke through (same margin build_stage.py uses).
    No-ops (returns hm unchanged) if no stations are passed.
    """
    if len(station_lon) == 0:
        return hm
    rows, cols = hm.shape
    lat_mid = (south + north) / 2.0
    m_per_deg_lat = 111132.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat_mid))
    mpp_x = (east - west) / (cols - 1) * m_per_deg_lon
    mpp_y = (north - south) / (rows - 1) * m_per_deg_lat
    if mpp_x <= 0 or mpp_y <= 0:
        return hm
    dist = np.full(hm.shape, np.inf, dtype=np.float32)
    roadh = np.zeros_like(hm)
    r = int(math.ceil(outer / max(min(mpp_x, mpp_y), 1e-6))) + 1
    px_all = (station_lon - west) / (east - west) * (cols - 1)
    py_all = (north - station_lat) / (north - south) * (rows - 1)
    for i in range(len(station_lon)):
        px, py = px_all[i], py_all[i]
        x0, x1 = max(0, int(px) - r), min(cols, int(px) + r + 1)
        y0, y1 = max(0, int(py) - r), min(rows, int(py) + r + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d = np.hypot((xx - px) * mpp_x, (yy - py) * mpp_y)
        sub = dist[y0:y1, x0:x1]
        m = d < sub
        sub[m] = d[m]
        roadh[y0:y1, x0:x1][m] = station_elev_m[i]
    w = np.clip((outer - dist) / (outer - inner), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    return w * (roadh - drop) + (1 - w) * hm


def sample_mesh_height(positions, triangles, x_cm, y_cm, eps=1e-4):
    """Barycentric-sample a triangle mesh's height (Z) at an XY point.

    RECONSTRUCTED (2026-07-28): this used to live in pipeline/verify_road_fit.py,
    which a prior cleanup pass deleted as believed-dead - it was actually still
    imported by build_forest_elevation.py and verify_terrain_fit.py (both real,
    live pipeline stages), so that deletion broke them. The original
    implementation could not be recovered (checked git history/stash/fsck - never
    committed; Windows Recycle Bin - git-bash rm bypasses it; VS Code local
    history - this file was never opened there; File History/VSS - not
    configured on this machine). This is a from-scratch reimplementation of the
    standard algorithm the call sites clearly require (find the triangle whose
    XY footprint contains the query point, interpolate Z from its vertices'
    barycentric weights) - tested against real built data
    (data/road_tiles/oahu_test/) to confirm it returns sane heights and a
    near-zero miss rate, but it is NOT guaranteed byte-identical to whatever the
    original did internally. Re-verify verify_terrain_fit.py's output against a
    known-good baseline before trusting a borderline PASS/FAIL from it.

    positions: (N,3) array of vertex (x_cm, y_cm, z_cm).
    triangles: (M,3) int array of vertex indices per triangle.
    Returns the interpolated z_cm, or None if the point falls outside every
    triangle (e.g. genuinely off the tile, or right on a shared-edge seam that
    floating-point rounding pushed just outside both neighbors).
    """
    tri_pts = positions[triangles]   # (M, 3, 3): [tri, vert(0/1/2), xyz]
    ax, ay = tri_pts[:, 0, 0], tri_pts[:, 0, 1]
    bx, by = tri_pts[:, 1, 0], tri_pts[:, 1, 1]
    cx, cy = tri_pts[:, 2, 0], tri_pts[:, 2, 1]

    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    nonzero = denom != 0
    safe_denom = np.where(nonzero, denom, 1.0)   # avoid div-by-zero on degenerate tris

    w_a = ((by - cy) * (x_cm - cx) + (cx - bx) * (y_cm - cy)) / safe_denom
    w_b = ((cy - ay) * (x_cm - cx) + (ax - cx) * (y_cm - cy)) / safe_denom
    w_c = 1.0 - w_a - w_b

    inside = nonzero & (w_a >= -eps) & (w_b >= -eps) & (w_c >= -eps)
    idxs = np.where(inside)[0]
    if len(idxs) == 0:
        return None

    i = idxs[0]
    za, zb, zc = tri_pts[i, 0, 2], tri_pts[i, 1, 2], tri_pts[i, 2, 2]
    return float(w_a[i] * za + w_b[i] * zb + w_c[i] * zc)
