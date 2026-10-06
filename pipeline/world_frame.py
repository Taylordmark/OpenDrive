"""World frame (2026-10-05, California-scale world step 1): one ellipsoidal
Lambert Conformal Conic projection shared by every stage, plus the 64 km
chunk grid the chunked bake will key off.

Replaces the per-stage equirectangular east/north math (each stage scaled
longitude by cos(its own latitude), so stages drifted against each other
and a far-from-origin area stretched). test_areas.to_local/to_lonlat wrap
this for the active area; stages never call it directly.

Formulas: Snyder, "Map Projections - A Working Manual" (USGS PP 1395, 1987),
pp. 107-109. No pyproj on purpose (not installed, and this is ~40 lines).
"""
try:
    import numpy as np
except ImportError:   # the UE editor's embedded Python has no numpy (unreal/
    # scripts import test_areas) - scalar-only stand-ins for what LCC uses
    import math
    from types import SimpleNamespace
    np = SimpleNamespace(pi=math.pi, sin=math.sin, cos=math.cos, tan=math.tan, log=math.log,
                         sqrt=math.sqrt, arctan=math.atan, arctan2=math.atan2, hypot=math.hypot,
                         radians=math.radians, degrees=math.degrees, asarray=float,
                         sign=lambda v: math.copysign(1.0, v))

WGS84_A = 6378137.0
WGS84_E2 = 0.00669437999014

# name -> (standard parallel 1, standard parallel 2, origin lat, central
# meridian), degrees. The world origin is (origin lat, central meridian).
WORLDS = {
    "california": (34.0, 40.5, 37.25, -120.0),
}

# (2026-10-05, step 2) Each world's shared vertical datum: absolute metres at
# UE Z = 0 for every member area (test_areas.ORIGIN_HEIGHT_M), so adjacent
# areas' Z agree where they meet. Sea level.
WORLD_DATUM_M = {
    "california": 0.0,
}

# Chunks are corner-aligned at the frame origin, so the 2 km terrain tiles
# and 500 m road tiles (also origin-aligned) nest inside them exactly.
CHUNK_SIZE_M = 64000.0


def _m(phi, e):
    return np.cos(phi) / np.sqrt(1.0 - (e * np.sin(phi)) ** 2)


def _t(phi, e):
    es = e * np.sin(phi)
    return np.tan(np.pi / 4 - phi / 2) / ((1.0 - es) / (1.0 + es)) ** (e / 2)


def _constants(lat1, lat2, lat0, a, e2):
    e = np.sqrt(e2)
    p1, p2, p0 = np.radians(lat1), np.radians(lat2), np.radians(lat0)
    m1, m2 = _m(p1, e), _m(p2, e)
    t1, t2 = _t(p1, e), _t(p2, e)
    n = np.sin(p1) if abs(p1 - p2) < 1e-12 else (np.log(m1) - np.log(m2)) / (np.log(t1) - np.log(t2))
    aF = a * m1 / (n * t1 ** n)
    return e, n, aF, aF * _t(p0, e) ** n


def lcc_forward(lon, lat, lat1, lat2, lat0, lon0, a=WGS84_A, e2=WGS84_E2):
    """lon/lat degrees -> (x east, y north) metres from (lat0, lon0)."""
    e, n, aF, rho0 = _constants(lat1, lat2, lat0, a, e2)
    rho = aF * _t(np.radians(lat), e) ** n
    theta = n * np.radians(np.asarray(lon) - lon0)
    return rho * np.sin(theta), rho0 - rho * np.cos(theta)


def lcc_inverse(x, y, lat1, lat2, lat0, lon0, a=WGS84_A, e2=WGS84_E2):
    """(x east, y north) metres -> (lon, lat) degrees. Exact inverse of
    lcc_forward (latitude by Snyder's fixed-point iteration, eq. 7-9)."""
    e, n, aF, rho0 = _constants(lat1, lat2, lat0, a, e2)
    s = np.sign(n)
    dy = rho0 - np.asarray(y)
    rho = s * np.hypot(x, dy)
    t = (rho / aF) ** (1.0 / n)
    phi = np.pi / 2 - 2 * np.arctan(t)
    for _ in range(15):   # converges to ~1e-15 rad in <10 at any real latitude
        es = e * np.sin(phi)
        phi = np.pi / 2 - 2 * np.arctan(t * ((1.0 - es) / (1.0 + es)) ** (e / 2))
    lon = lon0 + np.degrees(np.arctan2(s * np.asarray(x), s * dy) / n)
    return lon, np.degrees(phi)


def chunk_of(east, north):
    """Chunk index (i, j) containing a local east/north point (metres)."""
    return (np.floor_divide(east, CHUNK_SIZE_M).astype(int),
            np.floor_divide(north, CHUNK_SIZE_M).astype(int))


def chunk_origin(i, j):
    """Centre (east, north) metres of chunk (i, j)."""
    return (np.asarray(i) + 0.5) * CHUNK_SIZE_M, (np.asarray(j) + 0.5) * CHUNK_SIZE_M
