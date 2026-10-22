"""Hand-built real landmarks (2026-10-16): a short curated list per area of
the structures you recognise the place by, each built from its real OSM
footprint plus a recipe (heights, materials, roof, and revolved shapes for
towers, chimneys and tanks) chosen from photos, and swapped in at runtime for
whatever procedural building stands on that footprint.

Usage:  python build_landmarks.py          (needs road_stations.json)
        python build_landmarks.py --list   (rank OSM candidates -> docs/landmarks/<area>.md)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json, OSM via Overpass (cached)
Writes  OpenDriveUE/Content/OpenDrive/Masks/landmarks_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        data/road_tiles/<AREA_NAME>_test/landmarks.json   (summary, checks/verify_landmarks.py)

Geometry is OSM only (ODbL, already credited) plus code: no third-party models.

ODL1 format (per file):
    'O','D','L','1'        magic
    16 bytes               generation id (generation_id.py)
    uint32 LE              footprint_count; per footprint: uint32 n, n * (float32 x, float32 y) cm.
                           AODBuildingManager drops every procedural building whose
                           vertex-mean centroid lies inside one. Each footprint goes into
                           every tile its bbox touches.
    uint32 LE              seed_count (= the embedded building count); one uint8 per
                           building: the material's per-building seed (G vertex colour,
                           picks the paint), 255 = the runtime's usual centroid hash
    ODU4 tile bytes        export_building_mesh_bin.pack_tile: the landmark meshes whose
                           centroid is in this tile
"""

import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

from build_building_mesh import FACADE_TILE_M, STATIONS_PATH, extrude_footprint, load_stations
import building_roofs
from building_roofs import make_roof, obb
from export_building_mesh_bin import pack_tile
from fetch_cache import overpass_json
from fetch_osm_buildings import HEADERS
from bake_job import tile_in_job   # (2026-10-21) a pass-2 job writes only its own tiles
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import road_ue_z_m
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM, to_local

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
SUMMARY_PATH = STATIONS_PATH.parent / "landmarks.json"
DOCS_DIR = ROOT / "docs" / "landmarks"

# apply_building_material_real.py WALL_LAYERS / PAINTS, build_building_mesh FACADE_*, ROOF_*
BRICK, TAN_BRICK, SIDING, PLASTER, CONCRETE, STONE, LOG = range(7)
PUNCHED, HOUSE, STOREFRONT, CURTAIN, BALCONY = range(5)
ROOF_TILE, ROOF_ASPHALT, ROOF_METAL, ROOF_FLAT = range(4)
WHITE, TAN, GREEN, BLUE_GREY, DARK_RED = range(5)
PAINTED_WALLS = (SIDING, PLASTER)
FLAT_GREY = (150, 150, 145)   # flat-roof membrane, linear 0..255


def lin(r, g, b):
    """sRGB 0..1 -> linear 0..255, for roof_rgb."""
    return tuple(round(255 * ((c / 12.92) if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)) for c in (r, g, b))


# Each landmark: way (OSM footprint), the body recipe, optional extra pieces.
#   body "extrude": h m, roof flat/gable/hip, wall, facade, paint (None = unpainted)
#   body "parts":   [(way, h m)] extruded from the ground, all with the landmark's look
#   body None:      pieces only, no windows anywhere
#   pieces: revolved shapes [(z m, r m) ...] from the ground (or "top" = the body's
#   highest point), seg 4 = square aligned to the footprint, at (along, across) m
#   from its centre or "end" = the end of its long axis nearest a road.
# "photo" notes where the look is from memory, not checked against a photo yet.
LANDMARKS = [
    # ---------------- oahu ----------------
    dict(name="Aloha Tower", way=61956192, wall=PLASTER, paint=WHITE, facade=PUNCHED,
         body="extrude", h=39.0, roof="flat", roof_set=ROOF_METAL, roof_rgb=lin(0.33, 0.45, 0.40),
         pieces=[dict(at=(0, 0), z="top", seg=4, profile=[(0, 8.0), (0.6, 8.0), (0.6, 7.2), (6.0, 0.0)])],
         note="1926 clock tower; OSM 56 m includes the flagpole. Pyramid roof colour: photo"),
    dict(name="First Hawaiian Center", way=171281353, wall=CONCRETE, paint=None, facade=CURTAIN,
         body="extrude", h=133.0, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY,
         note="tallest building in Hawaii (133 m); the procedural cap is 40 levels"),
    dict(name="The Ilikai Hotel", way=61720479, wall=PLASTER, paint=WHITE, facade=BALCONY,
         body="extrude", h=76.0, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY,
         note="OSM building:part only, so the procedural stage never fetched it"),
    dict(name="Royal Hawaiian Hotel", way=61759072, wall=PLASTER, paint=TAN, facade=PUNCHED,
         body="extrude", h=20.0, roof="hip", roof_set=ROOF_TILE, roof_rgb=lin(0.72, 0.45, 0.45),
         note="real walls are pink; no pink paint yet, tan stands in"),
    dict(name="Diamond Head Lighthouse", way=1254582682, wall=PLASTER, paint=WHITE, body=None,
         roof_set=ROOF_METAL, roof_rgb=lin(0.12, 0.12, 0.12),
         pieces=[dict(at=(0, 0), z=0, seg=4, profile=[(0, 2.3), (13.6, 2.0), (13.6, 2.5), (14.0, 2.5), (14.0, 1.3)]),
                 dict(at=(0, 0), z=0, seg=12, profile=[(14.0, 1.3), (16.0, 1.3), (16.0, 1.45), (17.0, 0.0)])],
         note="square white tower, round lantern; 17 m"),
    # ---------------- summit_county / breckenridge ----------------
    dict(name="Old Summit County Courthouse", way=225432039, wall=BRICK, paint=None, facade=PUNCHED,
         body="extrude", h=13.0, roof="hip", roof_set=ROOF_ASPHALT, roof_rgb=lin(0.25, 0.24, 0.23),
         pieces=[dict(at=(0, 0), z="top", seg=4, profile=[(-1.0, 2.2), (3.0, 2.2), (3.0, 2.6), (4.5, 0.0)])],
         note="1909 red brick, hipped roof with a centre cupola; cupola size: photo"),
    dict(name="Baker's Tank", way=140529574, wall=SIDING, paint=None, body=None,
         roof_set=ROOF_METAL, roof_rgb=lin(0.30, 0.30, 0.30),
         pieces=[*(dict(at=(a, c), z=0, seg=4, profile=[(0, 0.15), (4.5, 0.15)])
                   for a in (-2.2, 2.2) for c in (-2.2, 2.2)),
                 dict(at=(0, 0), z=0, seg=16, profile=[(4.5, 3.2), (9.5, 3.2), (9.5, 3.35), (11.0, 0.0)])],
         note="1910 narrow-gauge railroad water tank on a timber trestle; leg height: photo"),
    dict(name="Saint Mary Catholic Church", way=225428346, wall=SIDING, paint=WHITE, facade=HOUSE,
         body="extrude", h=6.0, roof="gable", roof_set=ROOF_ASPHALT, roof_rgb=lin(0.22, 0.22, 0.24),
         pieces=[dict(at="end", z=0, seg=4, profile=[(0, 1.6), (11.0, 1.6), (11.0, 1.3), (17.0, 0.0)])],
         note="1881 white clapboard church with a front steeple; steeple height: photo"),
    dict(name="Beaver Run Resort", way=174302434, wall=SIDING, paint=TAN, facade=BALCONY,
         body="parts", parts=[(778665057, 5 * 3.0), (778665056, 9 * 3.0), (778665055, 9 * 3.0)],
         roof="gable", roof_set=ROOF_ASPHALT, roof_rgb=lin(0.28, 0.26, 0.24),
         note="OSM building:part levels 5/9/9 at the foot of Peak 9; siding colour: photo"),
    dict(name="Frisco Historic Park & Museum", way=131082419, wall=LOG, paint=None, facade=HOUSE,
         body="extrude", h=4.5, roof="gable", roof_set=ROOF_METAL, roof_rgb=lin(0.35, 0.33, 0.30),
         note="1880s log schoolhouse museum on Main Street"),
    # ---------------- skagit_cascades ----------------
    dict(name="Mount Vernon Carnation Stack", way=319429117, wall=BRICK, paint=None, body=None,
         roof_set=ROOF_FLAT, roof_rgb=lin(0.2, 0.2, 0.2),
         pieces=[dict(at=(0, 0), z=0, seg=16, profile=[(0, 1.2), (25.5, 0.85), (25.5, 1.0), (27.0, 1.0)])],
         note="27 m brick chimney of the old Carnation condensery"),
    dict(name="Hamilton Lumber Co. Smokestack", way=1106209418, wall=CONCRETE, paint=None, body=None,
         roof_set=ROOF_FLAT, roof_rgb=lin(0.2, 0.2, 0.2),
         pieces=[dict(at=(0, 0), z=0, seg=16, profile=[(0, 1.9), (45.72, 1.3)])],
         note="45.72 m concrete stack"),
    dict(name="Stokley Tower", way=299601920, wall=PLASTER, paint=WHITE, body=None,
         roof_set=ROOF_METAL, roof_rgb=lin(0.55, 0.55, 0.55),
         pieces=[*(dict(at=(a, c), z=0, seg=4, profile=[(0, 0.25), (18.0, 0.25)])
                   for a in (-3.0, 3.0) for c in (-3.0, 3.0)),
                 dict(at=(0, 0), z=0, seg=16, profile=[(18.0, 1.0), (18.0, 4.0), (25.0, 4.0), (25.0, 4.1), (27.0, 0.0)])],
         note="water tower; OSM has no style or height, legged tank is a guess: photo"),
    dict(name="Marysville Opera House", way=436973750, wall=BRICK, paint=None, facade=PUNCHED,
         body="extrude", h=12.2, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY,
         note="1911 brick opera house"),
    dict(name="Lincoln Theatre", way=370122428, wall=TAN_BRICK, paint=None, facade=STOREFRONT,
         body="extrude", h=12.0, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY,
         note="1926 Mount Vernon theatre; height and brick colour: photo"),
    # ---------------- green_valley ----------------
    dict(name="Bridgeport Lighthouse", way=403387321, wall=PLASTER, paint=WHITE, body=None,
         roof_set=ROOF_METAL, roof_rgb=lin(0.12, 0.12, 0.12),
         pieces=[dict(at=(0, 0), z=0, seg=16, profile=[(0, 2.8), (11.0, 2.1), (11.0, 2.6), (11.4, 2.6), (11.4, 1.4)]),
                 dict(at=(0, 0), z=0, seg=12, profile=[(11.4, 1.4), (13.2, 1.4), (13.2, 1.55), (14.2, 0.0)])],
         note="Valencia Bridgeport lake lighthouse; height: photo"),
    dict(name="San Francisquito Power Plant 1", way=472523384, wall=CONCRETE, paint=None, facade=PUNCHED,
         body="extrude", h=17.9, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY,
         note="1917 LADWP hydro plant"),
    dict(name="Saugus Station", way=429624124, wall=SIDING, paint=TAN, facade=HOUSE,
         body="extrude", h=6.0, roof="gable", roof_set=ROOF_ASPHALT, roof_rgb=lin(0.30, 0.22, 0.16),
         note="1887 Southern Pacific depot (OSM 9.4 m is the ridge); real colour is SP yellow"),
    dict(name="Princess Cruises (east)", way=472600550, wall=CONCRETE, paint=None, facade=CURTAIN,
         body="extrude", h=30.5, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY, note="Santa Clarita HQ"),
    dict(name="Princess Cruises (centre)", way=472600557, wall=CONCRETE, paint=None, facade=CURTAIN,
         body="extrude", h=25.8, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY, note="Santa Clarita HQ"),
    dict(name="Princess Cruises (west)", way=472600561, wall=CONCRETE, paint=None, facade=CURTAIN,
         body="extrude", h=19.1, roof="flat", roof_set=ROOF_FLAT, roof_rgb=FLAT_GREY, note="Santa Clarita HQ"),
]

# Recognisable but not built, shown in the --list docs.
NOT_BUILT = {
    "summit_county": ["Dillon Dam (earthen dam: terrain + the Dam Road already carry it)"],
    "oahu": ["Diamond Head crater (terrain)", "Rainbow Tower mural (needs its own texture)",
             "Royal Hawaiian pink walls (needs a free wall tint, not one of the 5 paints)"],
    "skagit_cascades": ["Lower Baker Dam (dam geometry)", "Concrete cement silos (not in OSM)"],
    "green_valley": ["Six Flags coasters (not buildings)", "Castaic Dam (dam geometry)", "Vasquez Rocks (terrain)"],
}


def paint_seed(paint):
    """Seed byte k whose material paint pick (apply_building_material_real.py:
    s = frac(k/255 * 17.31 + 0.71); s < 0.3 unpainted, else
    PAINTS[floor((s - 0.3) / 0.7 * 5)]) lands mid-bucket on `paint`."""
    target = 0.15 if paint is None else 0.3 + (paint + 0.5) * 0.14
    return min(range(255), key=lambda k: abs((k / 255 * 17.31 + 0.71) % 1.0 - target))


def ring_cm(geom):
    pts = [(p["lon"], p["lat"]) for p in geom]
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    e, n = to_local(np.array([p[0] for p in pts]), np.array([p[1] for p in pts]))
    return [(float(x) * 100.0, -float(y) * 100.0) for x, y in zip(e, n)]


def revolve(profile, seg, cx, cy, dx, dy, z0_cm):
    """Revolved surface of a [(z m, r m)] profile, closed at the top. seg 4 is a
    square of half-side r aligned to (dx, dy), flat faces; more segments share
    vertices around each band so it shades round."""
    if profile[-1][1] > 0:
        profile = [*profile, (profile[-1][0], 0.0)]
    a0 = math.atan2(dy, dx) + (math.pi / 4 if seg == 4 else 0.0)
    k_r = math.sqrt(2.0) if seg == 4 else 1.0
    verts, uvs, tris = [], [], []
    v_acc = 0.0
    for (za, ra), (zb, rb) in zip(profile, profile[1:]):
        slant = math.hypot(zb - za, rb - ra)
        if slant == 0:
            continue
        cols = [(j, j + 1) for j in range(seg)] if seg == 4 else [(None, None)]
        for c in cols:
            js = range(c[0], c[1] + 1) if seg == 4 else range(seg + 1)
            base = len(verts)
            for z, r, v in ((za, ra, v_acc), (zb, rb, v_acc + slant)):
                for j in js:
                    a = a0 + 2 * math.pi * j / seg
                    verts.append((cx + 100 * k_r * r * math.cos(a), cy + 100 * k_r * r * math.sin(a), z0_cm + 100 * z))
                    uvs.append((2 * math.pi * j / seg * max(ra, rb) / FACADE_TILE_M, v / FACADE_TILE_M))
            m = len(js)
            for j in range(m - 1):
                b0, b1, t0, t1 = base + j, base + j + 1, base + m + j, base + m + j + 1
                if rb > 0:
                    tris.append((b0, b1, t1))
                if ra > 0:
                    tris.append((b0, t1, t0) if rb > 0 else (b0, b1, t0))
        v_acc += slant
    return verts, uvs, tris


def append(mesh, piece):
    verts, uvs, tris = mesh
    b = len(verts)
    verts.extend(piece[0])
    uvs.extend(piece[1])
    tris.extend((i + b, j + b, k + b) for i, j, k in piece[2])


def extrude(ring, base_z_cm, h_m, roof):
    verts, uvs, tris = extrude_footprint(ring, base_z_cm, h_m * 100.0, 0)
    n_solid, shape = len(verts), "flat"
    if roof != "flat":
        # the procedural size gates keep big blocks flat; a landmark's roof is asked for on purpose
        saved = building_roofs.MAX_AREA_M2, building_roofs.MAX_SPAN_M, building_roofs.MAX_LENGTH_M
        building_roofs.MAX_AREA_M2, building_roofs.MAX_SPAN_M, building_roofs.MAX_LENGTH_M = 1e5, 200.0, 400.0
        try:
            roofed = make_roof(verts, uvs, tris, roof == "hip")
        finally:
            building_roofs.MAX_AREA_M2, building_roofs.MAX_SPAN_M, building_roofs.MAX_LENGTH_M = saved
        if roofed:
            verts, uvs, tris, n_solid = roofed
            shape = roof
    return [verts, uvs, tris], n_solid, shape


def build(lm, rings, ground_z_cm, road_xy_cm):
    ring = rings[lm["way"]]
    cx, cy, dx, dy, hl, _ = obb(ring)
    shape, n_solid = "flat", 0
    if lm["body"] == "extrude":
        mesh, n_solid, shape = extrude(ring, ground_z_cm, lm["h"], lm["roof"])
    elif lm["body"] == "parts":
        mesh = [[], [], []]
        for way, h in lm["parts"]:
            part, _, shape = extrude(rings[way], ground_z_cm, h, lm["roof"])
            append(mesh, part)   # ponytail: end walls of every part get windows, a per-part n_solid split needs ODU5
        n_solid = len(mesh[0])
    else:
        mesh = [[], [], []]
    top_z = max((v[2] for v in mesh[0]), default=ground_z_cm)
    for p in lm.get("pieces", []):
        if p["at"] == "end":   # long-axis end nearest the road, set in a step from the gable
            ends = [(cx + s * (hl - 250) * dx, cy + s * (hl - 250) * dy) for s in (1, -1)]
            px, py = min(ends, key=lambda e: math.dist(e, road_xy_cm))
        else:
            px = cx + 100 * (p["at"][0] * dx - p["at"][1] * dy)
            py = cy + 100 * (p["at"][0] * dy + p["at"][1] * dx)
        z0 = top_z if p["z"] == "top" else ground_z_cm + 100 * p["z"]
        append(mesh, revolve(p["profile"], p["seg"], px, py, dx, dy, z0))
    verts, uvs, tris = mesh
    seed = paint_seed(lm["paint"]) if lm["wall"] in PAINTED_WALLS else 255
    return dict(name=f"landmark_{lm['way']}",
                vertices=[round(c, 1) for v in verts for c in v],
                uvs=[round(c, 4) for uv in uvs for c in uv],
                triangles=[i for t in tris for i in t],
                wall_set=lm["wall"], flat_roof=int(shape == "flat"), roof_set=lm["roof_set"],
                roof_rgb=list(lm["roof_rgb"]), facade=lm.get("facade", PUNCHED), n_solid=n_solid,
                seed=seed, roof=shape)


def in_bbox(geom):
    s, w, n, e = BBOX_SNWE
    lat = sum(p["lat"] for p in geom) / len(geom)
    lon = sum(p["lon"] for p in geom) / len(geom)
    return s <= lat <= n and w <= lon <= e


def fetch_ways(ids):
    q = f"[out:json][timeout:120];way(id:{','.join(map(str, sorted(ids)))});out tags geom;"
    # unclipped: a lookup by id across every area, checked for deleted ways below
    return {w["id"]: w for w in overpass_json(q, HEADERS, timeout=200, clip=False)["elements"]}


def tile_of(x, y):
    return int(x // TERRAIN_TILE_SIZE_CM), int(y // TERRAIN_TILE_SIZE_CM)


def pack_landmark_tile(footprints, buildings, gen_id_bytes):
    out = [b"ODL1", gen_id_bytes, struct.pack("<I", len(footprints))]
    for ring in footprints:
        out.append(struct.pack("<I", len(ring)))
        out.append(struct.pack(f"<{2 * len(ring)}f", *(c for p in ring for c in p)))
    out.append(struct.pack("<I", len(buildings)))
    out.append(bytes(b["seed"] for b in buildings))
    out.append(pack_tile(buildings, gen_id_bytes))
    return b"".join(out)


def main():
    ways = fetch_ways({lm["way"] for lm in LANDMARKS} | {w for lm in LANDMARKS for w, _ in lm.get("parts", [])})
    here = [lm for lm in LANDMARKS if lm["way"] in ways and in_bbox(ways[lm["way"]]["geometry"])]
    missing = [lm["name"] for lm in LANDMARKS if lm["way"] not in ways]
    assert not missing, f"OSM ways gone: {missing}"
    rings = {i: ring_cm(w["geometry"]) for i, w in ways.items()}

    st_e, st_n, st_elev, _ = load_stations()
    st_x, st_y = st_e * 100.0, -st_n * 100.0
    foot_tiles, mesh_tiles, summary = defaultdict(list), defaultdict(list), []
    for lm in here:
        ring = rings[lm["way"]]
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        i = int(np.argmin(np.hypot(st_x - cx, st_y - cy)))
        ground = road_ue_z_m(float(st_elev[i]), 0.0, ORIGIN_HEIGHT_M) * 100.0
        b = build(lm, rings, ground, (float(st_x[i]), float(st_y[i])))
        zs = b["vertices"][2::3]
        xs, ys = b["vertices"][0::3], b["vertices"][1::3]
        tile = tile_of(sum(xs) / len(xs), sum(ys) / len(ys))
        mesh_tiles[tile].append(b)
        tx0, ty0 = tile_of(min(p[0] for p in ring), min(p[1] for p in ring))
        tx1, ty1 = tile_of(max(p[0] for p in ring), max(p[1] for p in ring))
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                foot_tiles[tx, ty].append(ring)
        summary.append(dict(name=lm["name"], way=lm["way"], tile=list(tile), height_m=round((max(zs) - min(zs)) / 100, 2),
                            expected_h_m=round(max([lm.get("h", 0), *(h for _, h in lm.get("parts", [])),
                                                    *(p["profile"][-1][0] + (lm.get("h", 0) if p["z"] == "top" else p["z"])
                                                      for p in lm.get("pieces", []))]), 2),
                            roof=b["roof"], roof_asked=lm.get("roof", "flat"), seed=b["seed"], paint=lm["paint"],
                            verts=len(xs), tris=len(b["triangles"]) // 3, road_m=round(math.dist((cx, cy), (st_x[i], st_y[i])) / 100, 1),
                            footprint=ring, note=lm["note"]))
        print(f"  {lm['name']}: {summary[-1]['height_m']} m, roof {b['roof']}, {len(xs)} verts, tile {tile}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    written = []
    for tile in sorted(t for t in set(foot_tiles) | set(mesh_tiles) if tile_in_job(*t, TERRAIN_TILE_SIZE_CM)):
        path = OUT_DIR / f"landmarks_{AREA_NAME}_test_tile_{tile[0]}_{tile[1]}.bin"
        path.write_bytes(pack_landmark_tile(foot_tiles[tile], mesh_tiles[tile], gen_id_bytes))
        written.append(path)
    n_stale = delete_stale_tiles(OUT_DIR, f"landmarks_{AREA_NAME}_test_tile_*.bin", written)
    SUMMARY_PATH.write_text(json.dumps(dict(area=AREA_NAME, generation_id=gen_id, landmarks=summary), indent=1))
    print(f"{len(here)} landmark(s) -> {len(written)} tile(s), {n_stale} stale deleted, gen {gen_id} -> {OUT_DIR}")


# ------------------------------------------------------------- --list mode
SKIP_HISTORIC = "memorial|marker|wayside_shrine|boundary_stone|milestone|mine_shaft|ruins|archaeological_site|grave|tomb"
TYPE_HEIGHT_M = {"tower": 20, "water_tower": 15, "lighthouse": 15, "chimney": 30, "silo": 20, "bridge": 10, "dam": 10}


def guess_height(t):
    for k in ("height", "building:height"):
        try:
            return float(t[k].split()[0]), "height"
        except (KeyError, ValueError):
            pass
    try:
        return float(t["building:levels"]) * 3.2, "levels"
    except (KeyError, ValueError):
        return TYPE_HEIGHT_M.get(t.get("man_made"), 8.0), "guess"


def list_candidates():
    s, w, n, e = BBOX_SNWE
    b = f"({s},{w},{n},{e})"
    q = f"""[out:json][timeout:300];
(
 nwr["tourism"="attraction"]{b};
 nwr["historic"]["historic"!~"^({SKIP_HISTORIC})$"]{b};
 nwr["man_made"~"^(tower|water_tower|lighthouse|bridge|dam|chimney|silo)$"]{b};
 way["waterway"="dam"]{b};
 nwr["building"]["wikidata"]{b};
 nwr["building:part"]{b};
 way["building"]["height"](if:number(t["height"])>=40){b};
 way["building"]["building:levels"](if:number(t["building:levels"])>=12){b};
);
out tags center;"""
    els = overpass_json(q, HEADERS, timeout=360)["elements"]
    st_e, st_n, _, _ = load_stations()
    from scipy.spatial import cKDTree
    tree = cKDTree(np.c_[st_e, st_n])
    chosen = {lm["way"] for lm in LANDMARKS}
    rows = []
    for el in els:
        t = el.get("tags", {})
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c["lat"] is None or "name" not in t:
            continue
        h, src = guess_height(t)
        d, _ = tree.query(np.array(to_local(c["lon"], c["lat"])).ravel())
        score = h / max(d, 15.0) * (1.5 if "wikidata" in t else 1.0) * (1.3 if "tourism" in t or "historic" in t else 1.0)
        kind = t.get("man_made") or t.get("historic") or t.get("tourism") or t.get("building") or t.get("building:part")
        rows.append((score, el["type"][0] + str(el["id"]), t["name"], kind, h, src, d, el["type"] == "way" and el["id"] in chosen))
    rows.sort(reverse=True)
    seen, lines = set(), []
    for r in rows:
        if r[2] in seen:
            continue
        seen.add(r[2])
        lines.append(f"| {'**built**' if r[7] else ''} | {r[2]} | {r[1]} | {r[3]} | {r[4]:.0f} ({r[5]}) | {r[6]:.0f} | {r[0]:.2f} |")
        if len(lines) == 25:
            break
    ways = fetch_ways(chosen)
    built = [f"- **{lm['name']}** (w{lm['way']}): {lm['note']}" for lm in LANDMARKS
             if lm["way"] in ways and in_bbox(ways[lm["way"]]["geometry"])]
    doc = [f"# Landmarks: {AREA_NAME}", "",
           "Generated by `python pipeline/build_landmarks.py --list` (OD_AREA set). OSM candidates "
           "(tourism=attraction, historic, man_made towers/chimneys/bridges/dams, wikidata or tall buildings), "
           "ranked by height / max(distance to the nearest road station, 15 m), x1.5 with a wikidata link, "
           "x1.3 for tourism/historic. Height source: height tag, levels x 3.2 m, or a guess by type.", "",
           "| | name | OSM | kind | height m | road m | score |", "|---|---|---|---|---|---|---|", *lines, "",
           "## Built (build_landmarks.py LANDMARKS)", "",
           *(built or ["- none"]), "",
           "## Not built", "", *(f"- {x}" for x in NOT_BUILT.get(AREA_NAME, ["-"])), ""]
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    (DOCS_DIR / f"{AREA_NAME}.md").write_text("\n".join(doc), encoding="utf-8")
    print(f"{len(rows)} candidates -> {DOCS_DIR / (AREA_NAME + '.md')}")


if __name__ == "__main__":
    list_candidates() if "--list" in sys.argv else main()
