"""Bakes OSM parking-lot polygons into flat, terrain-conforming paved ground
patches (Art-direction §7.15 "paved town context" pass, 2026-09-03) - The
Crew 2's towns read as towns because buildings sit on paved context
(parking lots, driveways, sidewalk aprons); Summit County's OSM data
already has amenity=parking polygons for Frisco/Breckenridge/Dillon/
Silverthorne's commercial areas, and nothing currently bakes them - the
ground between buildings and roads renders as bare natural terrain.

Deliberately reuses pipeline/build_water_tiles.py's already-established
"OSM polygon -> flat baked mesh -> ODL1 tile sidecar" shape wholesale
(imports its triangulation/mesh-build/tile-write helpers directly rather
than re-deriving them - same functions, same ODL1 format, see that module's
own docstring for the wire layout) with exactly two differences:

  1. Feature query: amenity=parking (ways + multipolygon relations), not
     natural=water/landuse=reservoir. parking=multi-storey/underground/
     rooftop are excluded (PARKING_TYPE_EXCLUDE below) - those aren't
     ground-level pavement a car would ever see baked flat into the terrain,
     the whole point of this pass.
  2. Ground elevation: NOT a DEM raster sample (a lake has no nearby road to
     reference) - a parking lot is data-poor the same way a building
     footprint is, and Taylor's own task scope calls for "sample terrain
     heights like buildings do" rather than a fresh DEM read. Reuses
     build_building_mesh.py's exact approach: nearest baked ROAD STATION's
     own elevation (road_stations.json), tile-bucketed for the same O(1)-ish
     lookup that module's nearby_station_indices() already established -
     a parking lot is always near a road by construction (it exists to serve
     one), so this is a strictly BETTER-grounded height source than DEM
     would be, not a downgrade. A small PARKING_Z_BIAS_CM lifts the baked
     surface a few cm above that reference height, purely to keep the flat
     pavement plane from z-fighting/flickering against the raw terrain mesh
     sampled independently at runtime (same idea as this project's other
     decal-like flat-cap features, just applied preemptively here since
     there's no baked terrain-depression step for parking the way water
     grew one - out of scope for this pass, see task notes).

No road-adjacency exclusion (unlike buildings' ROAD_EXCLUSION_M gate): a
parking lot legitimately touches/abuts the road it serves at its entrance,
excluding footprints near roads would delete real lots wholesale.

Usage:  python build_parking_tiles.py   (needs road_stations.json first)

Writes  OpenDriveUE/Content/OpenDrive/Masks/parking_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        (ODL1 format, identical wire layout to build_water_tiles.py's
        water_*.bin - see that module's docstring for the byte layout)
"""

import json
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
from fetch_cache import overpass_json

from build_water_tiles import assemble_outer_rings, build_lake_mesh, polygon_area_m2, write_tile
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import road_ue_z_m
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

MIN_AREA_M2 = 30.0            # a handful of real parking spaces (~12.5 m^2
                               # each) - drops slivers/misdigitized nodes
                               # while keeping even a small 2-3 space lot
UV_TILE_M = 8.0                # asphalt tiling period, matches M_Road's own
                               # ~2.6-3.2 m tile scale ballpark for a
                               # believably-sized pavement texture repeat
PARKING_Z_BIAS_CM = 3.0        # lifts the baked flat plane this far above
                               # its reference (nearest-station) elevation -
                               # see module docstring, prevents z-fighting
                               # flicker against the independently-sampled
                               # runtime terrain mesh

# parking=* sub-tag values that are NOT ground-level pavement - excluded so
# this pass never bakes a flat "lot" over what's actually a parking
# structure/garage footprint (those, if built at all, belong with buildings,
# not this ground-context pass).
PARKING_TYPE_EXCLUDE = {"multi-storey", "underground", "rooftop", "sheds"}


def fetch_parking_polygons():
    s, w, n_, e = BBOX_SNWE
    query = f"""
    [out:json][timeout:180];
    (
      way["amenity"="parking"]({s},{w},{n_},{e});
      relation["amenity"="parking"]({s},{w},{n_},{e});
    );
    (._;>>;);
    out body;
    """
    print(f"Querying Overpass for parking-lot polygons/relations in the {AREA_NAME} bbox "
          f"({s:.3f},{w:.3f},{n_:.3f},{e:.3f}) ...")
    data = overpass_json(query, HEADERS)

    nodes = {el["id"]: (el["lat"], el["lon"])
             for el in data["elements"] if el["type"] == "node"}
    ways = [el for el in data["elements"] if el["type"] == "way"]
    relations = [el for el in data["elements"] if el["type"] == "relation"]
    ways_by_id_raw = {w_el["id"]: w_el.get("nodes", []) for w_el in ways}
    parking_relations = [rel for rel in relations
                          if rel.get("tags", {}).get("amenity") == "parking"]
    used_way_ids = {m["ref"] for rel in parking_relations for m in rel.get("members", [])
                     if m.get("type") == "way" and m.get("role") == "outer"}

    polygons = []
    skipped_not_closed = skipped_too_few = skipped_structure = 0
    for w_el in ways:
        if w_el["id"] in used_way_ids:
            continue
        tags = w_el.get("tags", {})
        if tags.get("amenity") != "parking":
            continue   # a member way pulled in only for its node coords, not itself a lot
        if tags.get("parking") in PARKING_TYPE_EXCLUDE:
            skipped_structure += 1
            continue
        nd_ids = w_el.get("nodes", [])
        if len(nd_ids) < 4 or nd_ids[0] != nd_ids[-1]:
            skipped_not_closed += 1
            continue
        ring = [nodes[n] for n in nd_ids[:-1] if n in nodes]
        if len(ring) < 3:
            skipped_too_few += 1
            continue
        polygons.append({"nodes": ring, "way_id": w_el["id"],
                          "name": tags.get("name", ""), "parking_tag": tags.get("parking", "surface")})

    n_relations_built = n_relation_rings = 0
    for rel in parking_relations:
        tags = rel.get("tags", {})
        if tags.get("parking") in PARKING_TYPE_EXCLUDE:
            skipped_structure += 1
            continue
        outer_ids = [m["ref"] for m in rel.get("members", [])
                     if m.get("type") == "way" and m.get("role") == "outer"]
        rings = assemble_outer_rings(outer_ids, ways_by_id_raw)
        for ring_ids in rings:
            ring = [nodes[n] for n in ring_ids if n in nodes]
            if len(ring) < 3:
                continue
            polygons.append({"nodes": ring, "way_id": rel["id"],
                              "name": tags.get("name", ""), "parking_tag": tags.get("parking", "surface")})
            n_relation_rings += 1
        if rings:
            n_relations_built += 1

    (RAW_DIR / "osm_parking.json").write_text(json.dumps(polygons))
    print(f"  got {len(polygons)} parking polygon(s) ({skipped_not_closed} not closed rings, "
          f"{skipped_too_few} too few nodes, {skipped_structure} multi-storey/underground/rooftop, "
          f"skipped; {n_relations_built} multipolygon relation(s) contributed {n_relation_rings} "
          f"outer ring(s)) -> {RAW_DIR / 'osm_parking.json'}")
    return polygons


def load_stations():
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    return lat0, lon0, east, north, elev


def main():
    if not STATIONS_PATH.is_file():
        raise SystemExit(f"{STATIONS_PATH} not found - run build_road_stations.py first "
                          f"(parking needs the shared world-frame origin and station elevations).")
    lat0, lon0, st_east, st_north, st_elev = load_stations()
    st_x_cm, st_y_cm = st_east * 100.0, -st_north * 100.0
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    # Same tile-bucketed nearest-station lookup build_building_mesh.py
    # established, duplicated rather than imported (that module has no
    # importable function boundary around it - it's inline in main()).
    station_tiles = defaultdict(list)
    for i in range(len(st_x_cm)):
        key = (int(st_x_cm[i] // TERRAIN_TILE_SIZE_CM), int(st_y_cm[i] // TERRAIN_TILE_SIZE_CM))
        station_tiles[key].append(i)

    def nearby_station_indices(x_cm, y_cm):
        cx, cy = int(x_cm // TERRAIN_TILE_SIZE_CM), int(y_cm // TERRAIN_TILE_SIZE_CM)
        for ring in (1, 3, 8):
            idx = [i for dx in range(-ring, ring + 1) for dy in range(-ring, ring + 1)
                   for i in station_tiles.get((cx + dx, cy + dy), [])]
            if idx:
                return np.array(idx)
        return None

    polygons = fetch_parking_polygons()
    if not polygons:
        raise SystemExit(f"No parking polygons matched in the {AREA_NAME} bbox.")

    buckets = defaultdict(list)
    n_too_small = n_no_station = n_fan_fallback = 0
    lot_summaries = []

    for poly in polygons:
        ring_ll = poly["nodes"]
        east_m = np.array([(lon - lon0) * m_per_deg_lon for _, lon in ring_ll])
        north_m = np.array([(lat - lat0) * M_PER_DEG_LAT for lat, _ in ring_ll])

        area_m2 = polygon_area_m2(east_m, north_m)
        if area_m2 < MIN_AREA_M2:
            n_too_small += 1
            continue

        ring_xy_cm = list(zip((east_m * 100.0).tolist(), (-north_m * 100.0).tolist()))
        cx_cm = float(np.mean([p[0] for p in ring_xy_cm]))
        cy_cm = float(np.mean([p[1] for p in ring_xy_cm]))

        cand = nearby_station_indices(cx_cm, cy_cm)
        if cand is None:
            n_no_station += 1
            continue
        nearest_i = cand[int(np.argmin(np.hypot(st_x_cm[cand] - cx_cm, st_y_cm[cand] - cy_cm)))]
        ground_z_cm = road_ue_z_m(float(st_elev[nearest_i]), 0.0, ORIGIN_HEIGHT_M) * 100.0 + PARKING_Z_BIAS_CM

        verts, uvs, tris, method = build_lake_mesh(ring_xy_cm, ground_z_cm)
        # NB: build_lake_mesh's UVs are baked at UV_TILE_M=20 (its own
        # module-level constant) - rescale here to this module's own
        # UV_TILE_M so the asphalt material's tiling reads at pavement scale,
        # not lake-water scale.
        uvs = uvs * (20.0 / UV_TILE_M)
        if len(tris) == 0:
            continue
        if method == "fan_fallback":
            n_fan_fallback += 1

        tx = int(cx_cm // TERRAIN_TILE_SIZE_CM)
        ty = int(cy_cm // TERRAIN_TILE_SIZE_CM)
        buckets[(tx, ty)].append({"verts": verts, "uvs": uvs, "tris": tris})

        lot_summaries.append({
            "way_id": poly["way_id"], "name": poly["name"] or "(unnamed)",
            "area_m2": area_m2, "ground_z_cm": ground_z_cm, "tile": (tx, ty),
            "verts": len(verts), "tris": len(tris), "method": method,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    total_verts = total_tris = 0
    written_paths = []
    for (tx, ty), lots in buckets.items():
        out_path = OUT_DIR / f"parking_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, lots, gen_id_bytes)
        written_paths.append(out_path)
        v = sum(len(lk["verts"]) for lk in lots)
        t = sum(len(lk["tris"]) for lk in lots)
        total_verts += v
        total_tris += t
        print(f"  tile ({tx},{ty}): {len(lots)} lot(s), {v} verts, {t} tris -> {out_path.name}")

    n_stale = delete_stale_tiles(OUT_DIR, f"parking_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale parking tile(s) from a previous run")

    print(f"\n{len(polygons)} OSM parking polygon(s) fetched -> {len(lot_summaries)} lot(s) built "
          f"({n_too_small} too small/sliver, {n_no_station} no nearby road station, skipped; "
          f"{n_fan_fallback} used the fan-triangulation fallback) -> {len(buckets)} tile(s), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")

    print("\nPer-lot summary (largest 20):")
    for s in sorted(lot_summaries, key=lambda s: -s["area_m2"])[:20]:
        print(f"  way {s['way_id']:>12} \"{s['name']}\" area={s['area_m2']:.0f} m^2 "
              f"ground_z={s['ground_z_cm']:.1f} cm tile={s['tile']} verts={s['verts']} "
              f"tris={s['tris']} method={s['method']}")


if __name__ == "__main__":
    main()
