"""Pass, job rect and drawn outline for the two-pass drawn-area bake
(2026-10-21, area picker Phase 2, docs/area-picker-contract.md).

rebuild_area.py --pass 1 / --pass 2 --rect x0,y0,x1,y1 sets OD_PASS and
OD_JOB_RECT_M for every stage. The rect is area-frame metres, x = east and
y = north (UE cm = (east * 100, -north * 100)). Unset (every hand area, and a
plain rebuild_area.py) = a whole bake: every helper here is then a no-op, so
stages behave exactly as before.

The outline is the area config's polygon_ll ([[lon, lat], ...], drawn areas
only); None for hand areas.
"""
import os
from functools import lru_cache

import numpy as np

# pass-2 decisions that reach past the job rect (terrain carve, road chunks)
# look this far out, reading pass 1's shared outputs (round 6)
CARVE_MARGIN_M = 150.0


def bake_pass():
    """"1", "2" or None (a whole bake)."""
    return os.environ.get("OD_PASS") or None


def job_rect(margin_m=0.0):
    """(x0, y0, x1, y1) east/north metres of this pass-2 job grown by
    margin_m, or None when no job rect is set."""
    v = os.environ.get("OD_JOB_RECT_M")
    if not v:
        return None
    x0, y0, x1, y1 = (float(s) for s in v.split(","))
    return x0 - margin_m, y0 - margin_m, x1 + margin_m, y1 + margin_m


def in_job(x_cm, y_cm, margin_m=0.0):
    """True where UE (x_cm, y_cm) is inside the job rect (+margin_m), half-open
    like test_areas.owns. All True with no job. Vectorised."""
    x, y = np.asarray(x_cm, dtype=float), np.asarray(y_cm, dtype=float)
    r = job_rect(margin_m)
    if r is None:
        return np.ones(np.broadcast(x, y).shape, dtype=bool)
    e, n = x / 100.0, -y / 100.0
    return (e >= r[0]) & (e < r[2]) & (n > r[1]) & (n <= r[3])


def tile_in_job(tx, ty, tile_cm):
    """True when UE tile (tx, ty) of side tile_cm overlaps the job rect (any
    tile with no job). Tile ty spans north (-(ty+1)*T, -ty*T]."""
    r = job_rect()
    if r is None:
        return True
    t = tile_cm / 100.0
    return tx * t < r[2] and (tx + 1) * t > r[0] and -(ty + 1) * t < r[3] and -ty * t > r[1]


def job_items(buckets, tile_cm):
    """The {(tx, ty): v} items a tile exporter writes: the ones in the job."""
    return [(k, v) for k, v in buckets.items() if tile_in_job(k[0], k[1], tile_cm)]


@lru_cache(maxsize=None)
def outline_ll():
    """The active area's drawn outline as a shapely Polygon in (lon, lat), or None."""
    from test_areas import ACTIVE_AREA, AREAS
    pts = AREAS[ACTIVE_AREA].get("polygon_ll")
    if not pts:
        return None
    import shapely
    return shapely.Polygon(pts)   # bake_request.py validates it (simple, <= 64 vertices)


@lru_cache(maxsize=None)
def outline_local():
    """The drawn outline in area-frame (east, north) metres, or None."""
    poly = outline_ll()
    if poly is None:
        return None
    import shapely
    from test_areas import to_local
    # ponytail: straight segments between the projected vertices - an LCC
    # edge bows < 1 m over the 30 km limit
    e, n = to_local(*np.asarray(poly.exterior.coords).T)
    return shapely.Polygon(np.c_[e, n])


def in_outline(east_m, north_m):
    """True where (east_m, north_m) is inside the drawn outline; all True for
    a hand area. Vectorised - the tree and footprint placers' filter."""
    import shapely
    e, n = np.asarray(east_m, dtype=float), np.asarray(north_m, dtype=float)
    poly = outline_local()
    if poly is None:
        return np.ones(np.broadcast(e, n).shape, dtype=bool)
    return shapely.contains_xy(poly, e, n)


def outline_tiles(tile_cm):
    """(tx, ty) of every UE tile of side tile_cm that overlaps the drawn
    outline - the terrain tiles the pass-2 jobs bake (build_terrain_mesh_tiles
    tile_touches_outline) - or None for a hand area. Known in pass 1, before
    any terrain tile exists."""
    poly = outline_local()
    if poly is None:
        return None
    import math
    import shapely
    t = tile_cm / 100.0
    e0, n0, e1, n1 = poly.bounds
    return [(tx, ty) for tx in range(math.floor(e0 / t) - 1, math.floor(e1 / t) + 1)
            for ty in range(math.floor(-n1 / t) - 1, math.floor(-n0 / t) + 1)
            if poly.intersects(shapely.box(tx * t, -(ty + 1) * t, (tx + 1) * t, -ty * t))]


def outline_sdf_m(east_m, north_m):
    """Signed distance (m) to the drawn outline, + inside, - outside."""
    import shapely
    poly = outline_local()
    e, n = np.asarray(east_m, dtype=float), np.asarray(north_m, dtype=float)
    d = shapely.distance(poly.boundary, shapely.points(e, n))
    return np.where(shapely.contains_xy(poly, e, n), d, -d)
