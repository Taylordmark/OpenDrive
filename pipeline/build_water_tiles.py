"""Bakes OSM lake polygons into flat, stylized water surfaces (WATER
feature, 2026-08-27) - Hughes Lake, Elizabeth Lake, Quail Lake etc. in the
green_valley bbox currently render as bare terrain because nothing ever
placed geometry over them. Mirrors this project's established
fetch+build+export-in-one-script pattern (build_terrain_mesh_tiles.py,
build_building_mesh.py+export_building_mesh_bin.py) end to end:

  1. Fetch OSM water polygons (natural=water, landuse=reservoir - the OSM
     tags real lakes/reservoirs use) via the same Overpass/mirror/retry
     pattern fetch_osm_buildings.py already uses. LAKES ONLY, same as the
     task scope. NB (2026-09-04): "rivers are waterway=*, never queried"
     turned out to be only half true - riverbank AREAS are closed
     natural=water rings with water=river/stream, so they DO come back from
     the natural=water query and must be filtered by tag (see
     FLOWING_WATER_TAGS). Before that filter existed, Blue River's 6 x 15.5
     km riverbank polygon (relation 214368) was baked as one flat "lake" at
     its 10th-percentile elevation (~2548 m - the river falls ~115 m through
     that span, so no single flat plane is ever right for it), and
     build_terrain_mesh_tiles.py's lake-bed depression then stamped that
     bogus plane's bed into the terrain, burying the road near Silverthorne
     ~108 m below grade at the Blue River Parkway / 6th Street junction
     (verify_terrain_fit.py's 2026-09-04 max-dev=108.91m failure).
  2. Project each closed way's ring into the SAME local east/north meter
     frame every other pipeline stage uses (road_stations.json's own
     origin_latlon - keeps water in registration with roads/terrain/
     buildings, which all key off that same origin).
  3. Sample DEM elevation (terrain_common's Terrarium fetch, bilinear via
     build_terrain_mesh_tiles.sample_dem - reused rather than
     reimplemented) at a grid of INTERIOR points (not the shoreline ring -
     shoreline pixels sit on the bank, above the true water surface) and
     take the 10th percentile as the lake's flat surface elevation - a
     lake is flat, so the true water level is close to the LOWEST
     plausible DEM reading across its footprint, not an average that gets
     dragged up by bank/shallow-water noise in the source raster.
  4. Triangulate the (possibly non-convex) ring with a from-scratch ear-
     clipping implementation (no shapely/earcut dependency exists in this
     project - build_building_mesh.py's own roof cap is fan-only, which
     inverts on a concave shore, e.g. Hughes Lake's real fingered
     shoreline) - falls back to the fan approach for the rare
     self-intersecting/degenerate ring ear-clipping can't fully resolve.
     Every real OSM lake shape tested (green_valley bbox) triangulated
     cleanly; see TRIANGULATION FALLBACK log line for any that didn't.
  5. Write one ODL1 tile sidecar per non-empty TERRAIN_TILE_SIZE_CM tile
     (same grid/prefix convention export_building_mesh_bin.py uses),
     bucketed by each lake's own centroid - a lake taller than one 2 km
     tile would get clipped to whichever tile its centroid lands in; no
     real lake in this bbox is anywhere near that large, so this is a
     documented scope limit, not a bug, same as build_building_mesh.py's
     identical single-tile-per-feature assumption.

MULTIPOLYGON RELATIONS (added 2026-09-03, summit_county pass): Dillon
Reservoir - summit_county's whole reason for this pass, see apply_water_
material.py - turned out to be tagged as a `type=multipolygon` OSM
RELATION (natural=water on the relation itself, 4 disconnected "outer" way
segments + 28 "inner" ways for its islands/points), not a single closed way
- the green_valley-era version of this script only ever fetched/used
`way["natural"="water"]`/`way["landuse"="reservoir"]` directly, so Dillon
Reservoir silently never got a tile at all (every real green_valley lake
tested WAS a simple way, which is why this gap went unnoticed until now).
fetch_water_polygons() now also fetches `relation["natural"="water"]`/
`relation["landuse"="reservoir"]`, and assemble_outer_rings() chains a
relation's "outer" member way segments end-to-end (matching shared node
ids, same idea any multipolygon assembler uses) into one or more closed
rings.

STILL NOT handled (documented scope, not silently dropped): the 28 "inner"
member ways (islands/points/exclusion zones) are NOT subtracted as holes -
ear_clip/fan_triangulate below have no hole support, and adding that is a
real triangulation feature, not a quick follow-on. Dillon Reservoir
therefore triangulates as solid water straight through its small islands/
points - a real, visible simplification up close at those specific spots,
accepted for this pass the same way the old single-tile-per-feature/no-
multipolygon-relation limits were accepted for the version before it.

Elevation formula: SAME road_ue_z_m(elev_m, 0.0, ORIGIN_HEIGHT_M) shift
every other elevation-consuming stage in this pipeline uses (road_mesh_
common.py) - a lake at ~700 m ASL sits at the SAME kind of "real absolute
elevation minus this area's local origin" UE-frame Z everything else here
already does; there is deliberately NO global sea-level plane (per task
scope - Green Valley's lakes are ~700 m ASL, nowhere near ocean level).

Usage:  python build_water_tiles.py   (needs road_stations.json first, for
                                        the shared world-frame origin)

Writes  OpenDriveUE/Content/OpenDrive/Masks/water_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODL1 format (per file) - deliberately the same per-feature vertex/uv/tri
layout export_building_mesh_bin.py's ODU1 already uses (one already-
established shape for "N flat-shaded features with a shared header/count"),
just under a fresh magic since ODW2/ODW3/ODU1/ODB1/ODT1/ODS1 are all
already spoken for by other sidecar families in this project:
    'O','D','L','1'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py)
    uint32 LE  lake_count
    per lake:
        uint32 LE  vertex_count
        uint32 LE  tri_count
        vertex_count * (float32 x, float32 y, float32 z)   cm, UE world frame
        vertex_count * (float32 u, float32 v)
        tri_count   * (uint32 i0, uint32 i1, uint32 i2)
"""

import json
import math
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests

from build_terrain_mesh_tiles import NEAR_DEM_ZOOM, sample_dem
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import road_ue_z_m
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

MIN_AREA_M2 = 500.0          # drop degenerate slivers (a handful of pixels'
                              # worth of OSM digitizing noise), keep real
                              # small farm ponds
UV_TILE_M = 20.0             # water UV period, matches this project's
                              # DETAIL_TILE_CM-style world-space tiling idea
                              # (though M_Water may not even sample a texture)

# Elevation sampling: interior points only (see module docstring for why
# the shoreline itself is excluded), a smaller interior grid step than the
# terrain pipeline's own NEAR tiles (lakes are small; a handful of samples
# per lake is enough for a percentile, not a full surface reconstruction).
RASTER_STEP_M = 15.0
MIN_INTERIOR_POINTS = 8      # below this, halve the raster step and retry
MAX_INTERIOR_POINTS = 600    # cap - a big reservoir doesn't need thousands
                              # of DEM samples for one percentile
SURFACE_PERCENTILE = 10.0    # see module docstring - low percentile, not
                              # median/mean, since a flat lake's true level
                              # sits near the DEM's lowest plausible reading
                              # across the footprint

# Flowing-water polygon tags (2026-09-04, Silverthorne terrain-burial fix -
# see module docstring): a closed natural=water ring whose water=* tag says
# the water MOVES has no single surface elevation, so the flat-plane model
# this whole script is built on (one percentile -> one Z for the whole
# polygon) is structurally wrong for it - and worse than cosmetic, because
# build_terrain_mesh_tiles.py reads the baked planes back as lake beds and
# depresses terrain to them. Skip these entirely; proper sloped rivers are
# their own future feature.
FLOWING_WATER_TAGS = {"river", "stream", "canal", "drain", "ditch", "rapids"}


def assemble_outer_rings(way_id_list, ways_by_id):
    """Chains a multipolygon relation's "outer" member way segments
    end-to-end into closed ring(s) of node ids - standard multipolygon-
    assembler logic (match shared endpoint node ids, reversing a segment if
    needed), no external dependency. A relation's outer boundary can be
    split across several disconnected segments (Dillon Reservoir: 4) that
    each individually fail the simple-way "already closed" check this
    script's ways loop uses - this is what actually closes them. Returns a
    list of node-id rings (each with the duplicate closing id already
    dropped); a segment set that never closes (missing/truncated Overpass
    data, or a genuinely broken relation) is dropped with a printed warning
    rather than silently producing a wrong/open shape."""
    segments = [list(ways_by_id[wid]) for wid in way_id_list if wid in ways_by_id]
    segments = [s for s in segments if len(s) >= 2]
    rings = []
    unused = list(range(len(segments)))
    while unused:
        idx = unused.pop(0)
        ring = list(segments[idx])
        progress = True
        while progress and ring[0] != ring[-1]:
            progress = False
            for j in list(unused):
                seg = segments[j]
                if seg[0] == ring[-1]:
                    ring.extend(seg[1:]); unused.remove(j); progress = True; break
                if seg[-1] == ring[-1]:
                    ring.extend(list(reversed(seg))[1:]); unused.remove(j); progress = True; break
                if seg[-1] == ring[0]:
                    ring[0:0] = seg[:-1]; unused.remove(j); progress = True; break
                if seg[0] == ring[0]:
                    ring[0:0] = list(reversed(seg))[:-1]; unused.remove(j); progress = True; break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring[:-1])
        else:
            print(f"    WARNING: outer-ring assembly did not close ({len(ring)} node(s) "
                  f"collected) - dropping this ring")
    return rings


def fetch_water_polygons():
    s, w, n_, e = BBOX_SNWE
    query = f"""
    [out:json][timeout:180];
    (
      way["natural"="water"]({s},{w},{n_},{e});
      way["landuse"="reservoir"]({s},{w},{n_},{e});
      relation["natural"="water"]({s},{w},{n_},{e});
      relation["landuse"="reservoir"]({s},{w},{n_},{e});
    );
    (._;>>;);
    out body;
    """
    # NB: >>  (recurse down, twice-transitive) not the single-level >  the
    # green_valley-era query used - a relation needs TWO hops (relation ->
    # member way -> way's nodes) to pull in the actual node coordinates;
    # plain > only pulls the relation's direct members (the ways), leaving
    # their nodes unfetched. See module docstring (MULTIPOLYGON RELATIONS).
    print(f"Querying Overpass for water polygons/relations in the {AREA_NAME} bbox "
          f"({s:.3f},{w:.3f},{n_:.3f},{e:.3f}) ...")
    data = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=200)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            print(f"  {url} failed ({exc}), trying next mirror")
    if data is None:
        raise SystemExit("All Overpass mirrors failed.")

    nodes = {el["id"]: (el["lat"], el["lon"])
             for el in data["elements"] if el["type"] == "node"}
    ways = [el for el in data["elements"] if el["type"] == "way"]
    relations = [el for el in data["elements"] if el["type"] == "relation"]
    ways_by_id_raw = {w_el["id"]: w_el.get("nodes", []) for w_el in ways}
    water_relations = [rel for rel in relations
                        if rel.get("tags", {}).get("natural") == "water"
                        or rel.get("tags", {}).get("landuse") == "reservoir"]

    # A relation's "outer" member ways are usually untagged open segments
    # that would fail the standalone-way closed-ring check below anyway,
    # but track them so a relation whose outer boundary happens to be one
    # single already-closed, independently-tagged way doesn't get counted
    # twice (once here, once via assemble_outer_rings below).
    used_way_ids = {m["ref"] for rel in water_relations for m in rel.get("members", [])
                     if m.get("type") == "way" and m.get("role") == "outer"}

    polygons = []
    skipped_not_closed = skipped_too_few = 0
    for w_el in ways:
        if w_el["id"] in used_way_ids:
            continue
        nd_ids = w_el.get("nodes", [])
        if len(nd_ids) < 4 or nd_ids[0] != nd_ids[-1]:
            skipped_not_closed += 1
            continue
        ring = [nodes[n] for n in nd_ids[:-1] if n in nodes]
        if len(ring) < 3:
            skipped_too_few += 1
            continue
        tags = w_el.get("tags", {})
        polygons.append({"nodes": ring, "way_id": w_el["id"],
                          "name": tags.get("name", ""),
                          "water_tag": tags.get("water", tags.get("natural", "water"))})

    n_relations_built = n_relation_rings = 0
    for rel in water_relations:
        tags = rel.get("tags", {})
        outer_ids = [m["ref"] for m in rel.get("members", [])
                     if m.get("type") == "way" and m.get("role") == "outer"]
        n_inner = sum(1 for m in rel.get("members", []) if m.get("role") == "inner")
        rings = assemble_outer_rings(outer_ids, ways_by_id_raw)
        for ring_ids in rings:
            ring = [nodes[n] for n in ring_ids if n in nodes]
            if len(ring) < 3:
                continue
            polygons.append({"nodes": ring, "way_id": rel["id"],
                              "name": tags.get("name", ""),
                              "water_tag": tags.get("water", tags.get("natural", "water"))})
            n_relation_rings += 1
        if rings:
            n_relations_built += 1
            if n_inner:
                print(f"  relation {rel['id']} \"{tags.get('name', '(unnamed)')}\": "
                      f"{len(rings)} outer ring(s) from {len(outer_ids)} segment(s), "
                      f"{n_inner} inner member(s) NOT subtracted (see module docstring)")

    (RAW_DIR / "osm_water.json").write_text(json.dumps(polygons))
    print(f"  got {len(polygons)} water polygon(s) ({skipped_not_closed} not closed rings, "
          f"{skipped_too_few} too few nodes, skipped; {n_relations_built} multipolygon "
          f"relation(s) contributed {n_relation_rings} outer ring(s)) -> {RAW_DIR / 'osm_water.json'}")
    return polygons


def polygon_area_m2(ring_e, ring_n):
    """Shoelace formula, absolute value - used only as the MIN_AREA_M2
    degenerate-sliver filter, not for any winding/orientation decision
    (triangulate_polygon below derives its own orientation independently)."""
    x = np.asarray(ring_e); y = np.asarray(ring_n)
    return abs(float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))) / 2.0


def point_in_polygon(px, py, ring_x, ring_y):
    """Vectorized ray-casting point-in-polygon test - px/py are (N,) arrays,
    ring_x/ring_y are the (M,) polygon vertices. Standard even-odd rule."""
    n = len(ring_x)
    inside = np.zeros(len(px), dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi, xj, yj = ring_x[i], ring_y[i], ring_x[j], ring_y[j]
        denom = (yj - yi) if abs(yj - yi) > 1e-9 else 1e-9
        cross = ((yi > py) != (yj > py)) & (px < (xj - xi) * (py - yi) / denom + xi)
        inside ^= cross
        j = i
    return inside


def interior_points_m(ring_e, ring_n, step_m=RASTER_STEP_M,
                       min_points=MIN_INTERIOR_POINTS, max_points=MAX_INTERIOR_POINTS):
    """Interior-only sample points (east_m, north_m arrays) for DEM
    elevation sampling - see module docstring for why shoreline vertices
    themselves are excluded. Shrinks the raster step (down to a 1 m floor)
    if the polygon is small enough that the initial step finds too few
    interior points. Falls back to the ring vertices themselves (a real,
    if biased-high, elevation reading - documented, not silently wrong) if
    even a 1 m raster finds nothing, which only happens for a sliver
    already below MIN_AREA_M2 and excluded upstream."""
    ring_e, ring_n = np.asarray(ring_e), np.asarray(ring_n)
    e0, e1 = ring_e.min(), ring_e.max()
    n0, n1 = ring_n.min(), ring_n.max()
    step = step_m
    sel_e, sel_n = np.array([]), np.array([])
    while step >= 1.0:
        xs = np.arange(e0 + step / 2.0, e1, step)
        ys = np.arange(n0 + step / 2.0, n1, step)
        if len(xs) and len(ys):
            gx, gy = np.meshgrid(xs, ys)
            px, py = gx.reshape(-1), gy.reshape(-1)
            inside = point_in_polygon(px, py, ring_e, ring_n)
            sel_e, sel_n = px[inside], py[inside]
            if len(sel_e) >= min_points:
                break
        step /= 2.0
    if len(sel_e) == 0:
        return ring_e.copy(), ring_n.copy(), True   # fallback flag
    if len(sel_e) > max_points:
        idx = np.linspace(0, len(sel_e) - 1, max_points).astype(int)
        sel_e, sel_n = sel_e[idx], sel_n[idx]
    return sel_e, sel_n, False


def signed_area(ring_xy):
    a = 0.0
    n = len(ring_xy)
    for i in range(n):
        x0, y0 = ring_xy[i]
        x1, y1 = ring_xy[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return a / 2.0


def ear_clip(ring_xy):
    """Standard O(n^2) ear-clipping triangulation of a simple (non-self-
    intersecting) polygon, no external dependency (no shapely/earcut
    installed in this project's environment - see module docstring).
    Returns (triangles, ok): triangles is a list of index triples into
    ring_xy; ok is False if clipping stalled (self-intersecting/degenerate
    ring) with `triangles` covering only what was resolved before it did -
    caller falls back to fan triangulation in that case."""
    n = len(ring_xy)
    if n < 3:
        return [], True
    idx = list(range(n))
    if signed_area(ring_xy) < 0:
        idx.reverse()   # ear-clipping below assumes CCW in standard (x,y) math orientation

    def is_convex(a, b, c):
        ax, ay = ring_xy[a]; bx, by = ring_xy[b]; cx, cy = ring_xy[c]
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) > 1e-9

    def point_in_tri(p, a, b, c):
        px, py = ring_xy[p]
        ax, ay = ring_xy[a]; bx, by = ring_xy[b]; cx, cy = ring_xy[c]
        d1 = (px - bx) * (ay - by) - (ax - bx) * (py - by)
        d2 = (px - cx) * (by - cy) - (bx - cx) * (py - cy)
        d3 = (px - ax) * (cy - ay) - (cx - ax) * (py - ay)
        has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
        has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
        return not (has_neg and has_pos)

    tris = []
    guard = 0
    max_guard = n * n + 8   # generous - a real stall is detected by no-ear-found, not iteration count
    while len(idx) > 3 and guard < max_guard:
        guard += 1
        found = False
        for i in range(len(idx)):
            iprev = idx[i - 1]
            icur = idx[i]
            inext = idx[(i + 1) % len(idx)]
            if not is_convex(iprev, icur, inext):
                continue
            if any(point_in_tri(j, iprev, icur, inext)
                   for j in idx if j not in (iprev, icur, inext)):
                continue
            tris.append((iprev, icur, inext))
            del idx[i]
            found = True
            break
        if not found:
            return tris, False
    if len(idx) == 3:
        tris.append((idx[0], idx[1], idx[2]))
    return tris, True


def fan_triangulate(ring_xy):
    """Centroid-fan fallback (same shape as build_building_mesh.py's roof
    cap) for a ring ear_clip() couldn't fully resolve - adds a centroid
    vertex, so the caller must be prepared for one extra vertex vs. the
    ear-clip path. Consistent winding via the ring's own signed area, same
    convention as build_building_mesh.py's roof."""
    n = len(ring_xy)
    cx = sum(p[0] for p in ring_xy) / n
    cy = sum(p[1] for p in ring_xy) / n
    area = signed_area(ring_xy)
    tris = []
    for i in range(n):
        a, b = i, (i + 1) % n
        if area >= 0:
            tris.append(("C", a, b))
        else:
            tris.append(("C", b, a))
    return cx, cy, tris


def triangulate_polygon(ring_xy):
    """Triangulates ring_xy (list of (x,y), any winding) -> (verts_xy, tris)
    where verts_xy may include one extra centroid vertex appended past the
    original ring (fan fallback only). Winding is NOT guaranteed up/down
    here in UE's Z-up convention - callers building 3-D geometry from this
    must independently confirm/fix the resulting triangle normal, same as
    build_terrain_mesh_tiles.fix_up_winding does for its own adaptive
    triangulation - see build_water_tiles.build_lake_mesh below."""
    tris, ok = ear_clip(ring_xy)
    if ok:
        return ring_xy, tris, "ear_clip"
    # fallback: fan from centroid - always succeeds for a non-self-
    # intersecting ring regardless of convexity
    cx, cy, fan_tris = fan_triangulate(ring_xy)
    verts = list(ring_xy) + [(cx, cy)]
    centroid_idx = len(ring_xy)
    resolved = [(centroid_idx if a == "C" else a, b, c) for a, b, c in fan_tris]
    return verts, resolved, "fan_fallback"


def build_lake_mesh(ring_xy_cm, z_cm):
    """verts_xy_cm (with an optional extra centroid vertex) -> flat 3-D
    mesh (verts, uvs, tris) at a single Z, winding corrected to face up
    under UE's normalize(cross(V2-V0, V1-V0)) convention (same test
    build_terrain_mesh_tiles.fix_up_winding uses) - triangulate_polygon
    above makes no promise about winding, so every triangle is checked
    independently here rather than trusting the ring's own bulk
    orientation the way build_building_mesh.py's simpler convex-friendly
    fan can."""
    verts_xy, tris, method = triangulate_polygon(ring_xy_cm)
    verts = np.array([(x, y, z_cm) for x, y in verts_xy], dtype=np.float32)
    uvs = np.array([(x / 100.0 / UV_TILE_M, y / 100.0 / UV_TILE_M) for x, y in verts_xy],
                   dtype=np.float32)
    tris = np.array(tris, dtype=np.uint32).reshape(-1, 3)
    if len(tris) == 0:
        return verts, uvs, tris, method
    v0 = verts[tris[:, 0]]; v1 = verts[tris[:, 1]]; v2 = verts[tris[:, 2]]
    normal_z = np.cross(v2 - v0, v1 - v0)[:, 2]
    flip = normal_z < 0
    tris[flip, 1], tris[flip, 2] = tris[flip, 2].copy(), tris[flip, 1].copy()
    return verts, uvs, tris, method


def write_tile(path, lakes, gen_id_bytes):
    with open(path, "wb") as f:
        f.write(b"ODL1")
        f.write(gen_id_bytes)
        f.write(struct.pack("<I", len(lakes)))
        for lk in lakes:
            verts, uvs, tris = lk["verts"], lk["uvs"], lk["tris"]
            vcount, tcount = len(verts), len(tris)
            f.write(struct.pack("<II", vcount, tcount))
            f.write(struct.pack(f"<{verts.size}f", *verts.reshape(-1)))
            f.write(struct.pack(f"<{uvs.size}f", *uvs.reshape(-1)))
            f.write(struct.pack(f"<{tris.size}I", *tris.reshape(-1)))


def main():
    if not STATIONS_PATH.is_file():
        raise SystemExit(f"{STATIONS_PATH} not found - run build_road_stations.py first "
                          f"(water needs the shared world-frame origin_latlon it writes).")
    station_data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = station_data["origin_latlon"]
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    polygons = fetch_water_polygons()
    if not polygons:
        raise SystemExit(f"No water polygons matched in the {AREA_NAME} bbox.")

    session = requests.Session()
    dem_cache = {}

    buckets = defaultdict(list)
    n_too_small = n_fallback_elev = n_fan_fallback = n_flowing = 0
    lake_summaries = []

    for poly in polygons:
        if poly.get("water_tag") in FLOWING_WATER_TAGS:
            n_flowing += 1
            continue
        ring_ll = poly["nodes"]
        east_m = np.array([(lon - lon0) * m_per_deg_lon for _, lon in ring_ll])
        north_m = np.array([(lat - lat0) * M_PER_DEG_LAT for lat, _ in ring_ll])

        area_m2 = polygon_area_m2(east_m, north_m)
        if area_m2 < MIN_AREA_M2:
            n_too_small += 1
            continue

        int_e, int_n, used_fallback = interior_points_m(east_m, north_m)
        if used_fallback:
            n_fallback_elev += 1
        int_lat = lat0 + int_n / M_PER_DEG_LAT
        int_lon = lon0 + int_e / m_per_deg_lon
        elevs = sample_dem(int_lat, int_lon, NEAR_DEM_ZOOM, session, dem_cache)
        surface_elev_m = float(np.percentile(elevs, SURFACE_PERCENTILE))
        surface_z_cm = road_ue_z_m(surface_elev_m, 0.0, ORIGIN_HEIGHT_M) * 100.0

        ring_xy_cm = list(zip((east_m * 100.0).tolist(), (-north_m * 100.0).tolist()))
        verts, uvs, tris, method = build_lake_mesh(ring_xy_cm, surface_z_cm)
        if len(tris) == 0:
            continue
        if method == "fan_fallback":
            n_fan_fallback += 1

        cx_cm = float(np.mean([p[0] for p in ring_xy_cm]))
        cy_cm = float(np.mean([p[1] for p in ring_xy_cm]))
        tx = int(cx_cm // TERRAIN_TILE_SIZE_CM)
        ty = int(cy_cm // TERRAIN_TILE_SIZE_CM)
        buckets[(tx, ty)].append({"verts": verts, "uvs": uvs, "tris": tris})

        lake_summaries.append({
            "way_id": poly["way_id"], "name": poly["name"] or "(unnamed)",
            "area_m2": area_m2, "surface_elev_m": surface_elev_m,
            "surface_z_cm": surface_z_cm, "tile": (tx, ty),
            "n_dem_samples": len(elevs), "verts": len(verts), "tris": len(tris),
            "method": method,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    total_verts = total_tris = 0
    written_paths = []
    for (tx, ty), lakes in buckets.items():
        out_path = OUT_DIR / f"water_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, lakes, gen_id_bytes)
        written_paths.append(out_path)
        v = sum(len(lk["verts"]) for lk in lakes)
        t = sum(len(lk["tris"]) for lk in lakes)
        total_verts += v
        total_tris += t
        print(f"  tile ({tx},{ty}): {len(lakes)} lake(s), {v} verts, {t} tris -> {out_path.name}")

    n_stale = delete_stale_tiles(OUT_DIR, f"water_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale water tile(s) from a previous run")

    print(f"\n{len(polygons)} OSM water polygon(s) fetched -> {len(lake_summaries)} lake(s) built "
          f"({n_flowing} flowing-water [river/stream/canal] polygon(s) skipped - flat-plane "
          f"model doesn't apply, see FLOWING_WATER_TAGS; {n_too_small} too small/sliver, "
          f"skipped; {n_fallback_elev} used the ring-vertex "
          f"elevation fallback [interior raster found nothing]; {n_fan_fallback} used the "
          f"fan-triangulation fallback [ear-clip stalled]) -> {len(buckets)} tile(s), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")

    print("\nPer-lake summary:")
    for s in sorted(lake_summaries, key=lambda s: -s["area_m2"])[:20]:
        print(f"  way {s['way_id']:>12} \"{s['name']}\" area={s['area_m2']:.0f} m^2 "
              f"surface_elev={s['surface_elev_m']:.1f} m surface_z={s['surface_z_cm']:.1f} cm "
              f"tile={s['tile']} dem_samples={s['n_dem_samples']} verts={s['verts']} "
              f"tris={s['tris']} method={s['method']}")


if __name__ == "__main__":
    main()
