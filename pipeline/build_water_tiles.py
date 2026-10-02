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

WATERWAYS (added 2026-09-07, closes the §7.23 caveat that a bridge deck
crosses a dry gully - no water was ever drawn under real creek/river
crossings): waterway=river/named-stream centerlines are buffered into
ribbon polygons and fed into the SAME "lakes" list this module already
writes to each ODL1 tile - build_terrain_mesh_tiles.load_lake_beds()/
apply_water_depression() and ODWaterManager.cpp's CreateLakeComponent both
already treat every polygon in a tile as a generic flat-shaded water
surface with no lake-specific logic (the boundary-ring extractor's own
2026-09-03 comment explicitly anticipated "a stream/river confluence baked
into the same lake mesh entry" - this pass is that), so no runtime/terrain
code changes were needed - see fetch_waterways()/build_waterway_ribbons()
below for the new pipeline stage feeding that same path.

ROAD CROSSING GAPS (added 2026-09-07, §7.27): of the 449 waterway/road
crossings §7.26 found, 317 coincide with a §7.22 bridge station (deck +
terrain carve already separate ribbon from road vertically - left alone)
but 132 do not, and the ribbon used to render straight across the road at
grade there - a visible water-sheet-over-pavement defect. Each such
crossing segment now gets a gap cut spanning the road corridor (+ margin,
see ROAD_GAP_MARGIN_M) via find_road_gap_intervals()/retained_t_intervals(),
so the ribbon terminates at one road edge and resumes at the other (an
implied culvert under the road) instead of rendering through it. Bridged
crossings are untouched by this logic (find_road_gap_intervals is only
called when the crossing's nearest road point is NOT a bridge).

Usage:  python build_water_tiles.py   (needs road_stations.json first, for
                                        the shared world-frame origin)

Writes  OpenDriveUE/Content/OpenDrive/Masks/water_<AREA_NAME>_test_tile_<tx>_<ty>.bin

ODL1 format (per file) - deliberately the same per-feature vertex/uv/tri
layout export_building_mesh_bin.py's ODU1 used (ODU2 since adds two
per-building facade bytes) - one already-established shape for "N
flat-shaded features with a shared header/count", just under a fresh
magic since ODW2/ODW3/ODU2/ODB1/ODT1/ODS1 are all already spoken for by other sidecar families in this project:
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

from fetch_cache import overpass_json
from scipy.spatial import cKDTree

from build_terrain_mesh_tiles import NEAR_DEM_ZOOM, sample_dem
from generation_id import compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import ROAD_HALF_WIDTH, road_ue_z_m
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

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
    data = overpass_json(query, HEADERS)

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


# --------------------------------------------------------------- waterways
# Class defaults (m) used when an OSM `width` tag is absent - see
# build_waterway_ribbons() for the tagged/default split reported in
# verify_waterways_stats.txt. Blue River through Breckenridge (real,
# checked against the DEM valley floor width at a handful of cross-
# sections) runs ~8-12 m bank-to-bank in this stretch; a real named
# mountain creek (Tenmile/Straight/Swan) is a fraction of that.
WATERWAY_CLASS_WIDTH_M = {"river": 10.0, "stream": 3.5}
WATERWAY_MIN_WIDTH_M = 1.5      # sanity floor for a garbage/zero width= tag
WATERWAY_MAX_WIDTH_M = 60.0     # sanity ceiling (a tagged width in the
                                  # hundreds of meters is a digitizing/unit
                                  # error, not a real Summit County creek)
WATERWAY_RESAMPLE_M = 20.0      # centerline resample spacing for elevation
                                  # sampling + ribbon segmentation - matches
                                  # the water bake's own lake DEM raster
                                  # scale (RASTER_STEP_M), fine enough to
                                  # follow real bends without one "lake"
                                  # entry per OSM node.
WATERWAY_SMOOTH_WINDOW = 5      # centered moving-average window (samples)
                                  # applied before the monotonic clamp -
                                  # damps single-pixel DEM noise without
                                  # erasing the real valley grade.
WATERWAY_DEPTH_M = 0.4          # water surface sits this far BELOW the
                                  # smoothed monotonic centerline DEM
                                  # reading (used as a stand-in for "bank"
                                  # elevation - see module docstring caveat
                                  # in verify_waterways_stats.txt: DEM
                                  # resolution can't resolve a real bank
                                  # separately from the channel at these
                                  # narrow widths).
ROAD_WATER_CLEARANCE_M = 1.0    # min gap kept between a ribbon edge and
                                  # the road pavement edge for a
                                  # near-parallel run (Blue River / CO-9).
ROAD_CROSSING_ANGLE_DEG = 45.0  # >= this angle between waterway and
                                  # nearest-road tangent counts as a
                                  # "crossing" (left alone - the bridge
                                  # deck + terrain carve already separates
                                  # them vertically) rather than a
                                  # "parallel run" (clipped off pavement).

# §7.27: at a "crossing" that does NOT coincide with a §7.22 bridge station,
# there is no deck/terrain separation - the ribbon must not render across
# the road at grade (a visible water-sheet-over-pavement defect). Instead a
# gap is cut in the ribbon spanning the road corridor (+ margin) so the
# creek reads as terminating at one road edge and resuming at the other -
# an implied culvert under the road, same visual language a real county
# road/creek crossing without a bridge actually uses.
ROAD_GAP_MARGIN_M = 1.5         # extra clearance added to ROAD_HALF_WIDTH
                                  # on each side of an unbridged-crossing
                                  # gap, so no ribbon triangle pokes out
                                  # from under the implied culvert (same
                                  # spirit as ROAD_WATER_CLEARANCE_M for
                                  # parallel runs, just its own constant
                                  # since a crossing gap and a parallel
                                  # pull-back are different clearances).
GAP_SUBSAMPLES = 21             # sub-samples taken along a crossing
                                  # segment (WATERWAY_RESAMPLE_M ~= 20 m
                                  # long) to locate the gap's entry/exit
                                  # parametric position precisely - ~1 m
                                  # spacing, well inside ROAD_GAP_MARGIN_M's
                                  # buffer against the resulting rounding.
MIN_GAP_FRAGMENT_M = 1.0        # a retained ribbon fragment shorter than
                                  # this after a gap clip (chain-end stub,
                                  # or two closely-spaced gaps leaving a
                                  # sliver between them) is dropped instead
                                  # of emitted as a degenerate quad.


def fetch_waterways():
    """Overpass fetch for waterway=river (always) / waterway=stream (named
    only - an unnamed hillside trickle is noise, not a real feature) in the
    active bbox. Simple `way["waterway"=...]` query only (no relation/
    multipolygon handling - a linear waterway relation, e.g. a very long
    multi-segment river OSM sometimes groups under `type=waterway`, is a
    real but rare shape not seen in this county's data and is out of scope
    for this pass, same spirit as this module's existing lake-relation
    scope note)."""
    s, w, n_, e = BBOX_SNWE
    query = f"""
    [out:json][timeout:180];
    (
      way["waterway"="river"]({s},{w},{n_},{e});
      way["waterway"="stream"]["name"]({s},{w},{n_},{e});
    );
    (._;>;);
    out body;
    """
    print(f"Querying Overpass for waterways in the {AREA_NAME} bbox "
          f"({s:.3f},{w:.3f},{n_:.3f},{e:.3f}) ...")
    data = overpass_json(query, HEADERS)

    nodes = {el["id"]: (el["lat"], el["lon"])
             for el in data["elements"] if el["type"] == "node"}
    ways = [el for el in data["elements"] if el["type"] == "way"]

    n_river = n_stream = n_stream_unnamed_skipped = 0
    # unnamed streams never reach Overpass at all (query already filters
    # them server-side) - n_stream_unnamed_skipped stays informational/0
    # unless a future query relaxation reintroduces them.
    raw_ways = []
    for w_el in ways:
        nd_ids = w_el.get("nodes", [])
        if len(nd_ids) < 2:
            continue
        pts = [nodes[n] for n in nd_ids if n in nodes]
        if len(pts) < 2:
            continue
        tags = w_el.get("tags", {})
        cls = tags.get("waterway")
        if cls not in ("river", "stream"):
            continue
        if cls == "river":
            n_river += 1
        else:
            n_stream += 1
        raw_ways.append({
            "way_id": w_el["id"], "node_ids": [n for n in nd_ids if n in nodes],
            "pts": pts, "name": tags.get("name", ""), "class": cls,
            "width": tags.get("width"),
        })

    (RAW_DIR / "osm_waterways.json").write_text(json.dumps({"ways": raw_ways, "nodes": nodes}))
    print(f"  got {len(raw_ways)} waterway way(s): {n_river} river, {n_stream} named stream "
          f"-> {RAW_DIR / 'osm_waterways.json'}")
    return raw_ways, nodes


def _parse_width_m(width_tag):
    """OSM `width` values are inconsistent free text ("8", "8 m", "8m",
    occasionally other units) - this project's data only ever has plain
    numbers/"<n> m", so a permissive-but-bounded parse (strip non-numeric
    suffix, sanity-clamp) is enough; anything unparseable falls back to the
    class default exactly like a missing tag would."""
    if not width_tag:
        return None
    try:
        num = float("".join(ch for ch in str(width_tag) if ch.isdigit() or ch in ".-"))
    except ValueError:
        return None
    if num <= 0 or not (WATERWAY_MIN_WIDTH_M <= num <= WATERWAY_MAX_WIDTH_M):
        return None
    return num


def chain_waterways(raw_ways, nodes):
    """Chains same-name, same-class OSM way segments that share an endpoint
    node into single logical waterway centerlines (a real OSM river/creek is
    almost always split into many short ways between tributary junctions).
    Chaining is restricted to (class, name) groups so a shared confluence
    node between e.g. Blue River and Tenmile Creek never merges the two into
    one chain - only fragments of the SAME named waterway are joined. Reuses
    build_water_tiles.assemble_outer_rings' segment-walking approach but for
    OPEN chains (no closure requirement). An unnamed river (rare; "river" is
    selected regardless of name) chains within its own empty-name/way_id-
    keyed singleton group instead of merging with other unnamed rivers that
    happen to share a node."""
    groups = defaultdict(list)
    for w_ in raw_ways:
        key = (w_["class"], w_["name"]) if w_["name"] else (w_["class"], f"__unnamed_{w_['way_id']}")
        groups[key].append(w_)

    chains = []
    for (cls, name), members in groups.items():
        segments = [list(m["node_ids"]) for m in members]
        width_tags = [m["width"] for m in members if m["width"]]
        unused = list(range(len(segments)))
        while unused:
            i0 = unused.pop(0)
            chain = list(segments[i0])
            progress = True
            while progress:
                progress = False
                for j in list(unused):
                    seg = segments[j]
                    if seg[0] == chain[-1]:
                        chain.extend(seg[1:]); unused.remove(j); progress = True
                    elif seg[-1] == chain[-1]:
                        chain.extend(list(reversed(seg))[1:]); unused.remove(j); progress = True
                    elif seg[-1] == chain[0]:
                        chain[0:0] = seg[:-1]; unused.remove(j); progress = True
                    elif seg[0] == chain[0]:
                        chain[0:0] = list(reversed(seg))[:-1]; unused.remove(j); progress = True
                    else:
                        continue
                    break
            latlon = [tuple(nodes[nid]) for nid in chain if nid in nodes]
            if len(latlon) < 2:
                continue
            chains.append({
                "class": cls, "name": name or "(unnamed)", "latlon": latlon,
                "width_tag_m": _parse_width_m(width_tags[0]) if width_tags else None,
            })
    return chains


def resample_polyline(east_m, north_m, step_m):
    """Uniform arc-length resample of a polyline (removes OSM's uneven
    node-density noise before DEM sampling / monotonic smoothing)."""
    east_m = np.asarray(east_m, dtype=np.float64)
    north_m = np.asarray(north_m, dtype=np.float64)
    seg = np.hypot(np.diff(east_m), np.diff(north_m))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1]
    if total < 1e-6:
        return east_m[:1], north_m[:1]
    n_samples = max(2, int(round(total / step_m)) + 1)
    s_new = np.linspace(0.0, total, n_samples)
    e_new = np.interp(s_new, s, east_m)
    n_new = np.interp(s_new, s, north_m)
    return e_new, n_new


def monotonic_flow_profile(elev_m):
    """Smooths then forces elev_m non-increasing along its own index order
    (assumes the array has ALREADY been oriented high->low by the caller -
    see build_waterway_ribbons). Moving-average smooth first (damps DEM
    noise), then a running-minimum clamp (guarantees the final profile never
    ticks back uphill even if smoothing reintroduced a local bump)."""
    n = len(elev_m)
    if n < 2:
        return elev_m.copy()
    w = min(WATERWAY_SMOOTH_WINDOW, n if n % 2 else n - 1)
    if w < 3:
        smoothed = elev_m.copy()
    else:
        kernel = np.ones(w) / w
        pad = w // 2
        padded = np.pad(elev_m, pad, mode="edge")
        smoothed = np.convolve(padded, kernel, mode="valid")
    return np.minimum.accumulate(smoothed)


def offset_polyline(east_m, north_m, half_width_m):
    """Per-vertex left/right offset polylines from a centerline, using the
    average of the adjacent segment normals at interior vertices (a simple
    miter join - no explicit miter-length clamp against very sharp bends;
    acceptable for real OSM creek/river node spacing, which is fine-grained
    enough that this hasn't produced a visible spike in this county's data -
    documented limitation, not verified exhaustively). Returns
    (left_e, left_n, right_e, right_n), each same length as the input."""
    e = np.asarray(east_m); n = np.asarray(north_m)
    npts = len(e)
    tangents = np.zeros((npts, 2))
    tangents[1:-1, 0] = e[2:] - e[:-2]
    tangents[1:-1, 1] = n[2:] - n[:-2]
    tangents[0] = [e[1] - e[0], n[1] - n[0]]
    tangents[-1] = [e[-1] - e[-2], n[-1] - n[-2]]
    lens = np.hypot(tangents[:, 0], tangents[:, 1])
    lens[lens < 1e-9] = 1.0
    tangents /= lens[:, None]
    # left normal = tangent rotated +90 deg in (east, north) plane
    normals = np.stack([-tangents[:, 1], tangents[:, 0]], axis=1)
    left_e = e + normals[:, 0] * half_width_m
    left_n = n + normals[:, 1] * half_width_m
    right_e = e - normals[:, 0] * half_width_m
    right_n = n - normals[:, 1] * half_width_m
    return left_e, left_n, right_e, right_n


def build_road_point_index():
    """cKDTree over every road station point (east_m/north_m, meters,
    origin-relative) plus a parallel is_bridge/tangent array, for the
    waterway/road-overlap check (build_waterway_ribbons). Returns None if
    road_stations.json has no segments (shouldn't happen - build_water_tiles
    already requires it for the shared origin - but degrade to "no road
    overlap check" rather than crash)."""
    if not STATIONS_PATH.is_file():
        return None
    station_data = json.loads(STATIONS_PATH.read_text())
    pts_e, pts_n, pts_bridge, pts_tx, pts_ty = [], [], [], [], []
    for seg in station_data.get("segments", []):
        ex = np.asarray(seg["east_m"]); nx = np.asarray(seg["north_m"])
        if len(ex) < 2:
            continue
        tx = np.gradient(ex); ty = np.gradient(nx)
        tl = np.hypot(tx, ty); tl[tl < 1e-9] = 1.0
        pts_e.append(ex); pts_n.append(nx)
        pts_bridge.append(np.asarray(seg.get("is_bridge", [False] * len(ex)), dtype=bool))
        pts_tx.append(tx / tl); pts_ty.append(ty / tl)
    if not pts_e:
        return None
    all_e = np.concatenate(pts_e); all_n = np.concatenate(pts_n)
    all_bridge = np.concatenate(pts_bridge)
    all_tx = np.concatenate(pts_tx); all_ty = np.concatenate(pts_ty)
    tree = cKDTree(np.stack([all_e, all_n], axis=1))
    return {"tree": tree, "bridge": all_bridge, "tx": all_tx, "ty": all_ty,
            "e": all_e, "n": all_n}


def clip_corner_to_road(corner_e, corner_n, center_e, center_n, road_idx):
    """If corner_e/n sits closer to the nearest road point than
    ROAD_HALF_WIDTH+ROAD_WATER_CLEARANCE_M, pulls the corner back toward the
    centerline point just far enough (one-shot approximation using the
    CURRENT nearest-road distance at the corner, not re-queried after the
    pull - good enough at this scale/clearance) to respect that clearance.
    Returns (new_e, new_n, was_clipped)."""
    dist, idx = road_idx["tree"].query([corner_e, corner_n])
    min_allowed = ROAD_HALF_WIDTH + ROAD_WATER_CLEARANCE_M
    if dist >= min_allowed:
        return corner_e, corner_n, False
    dx, dy = corner_e - center_e, corner_n - center_n
    full_len = math.hypot(dx, dy)
    if full_len < 1e-6:
        return corner_e, corner_n, False
    # Approximate target length: shrink by the shortfall between the actual
    # nearest-road distance at the (unclipped) corner and the required
    # clearance - clamped to [0, full_len].
    shortfall = min_allowed - dist
    new_len = max(0.0, full_len - shortfall)
    scale = new_len / full_len
    return center_e + dx * scale, center_n + dy * scale, True


def find_road_gap_intervals(e0, n0, e1, n1, road_idx, gap_half_width_m,
                             n_subsamples=GAP_SUBSAMPLES):
    """§7.27: along the straight segment from (e0,n0) to (e1,n1), finds the
    parametric t-interval(s) in [0,1] where the centerline sits within
    gap_half_width_m of the nearest road station AND that nearest station is
    NOT on a bridge - i.e. where an unbridged road corridor overlaps this
    ribbon segment and must be cut out (implied culvert) rather than drawn
    across the pavement at grade. A bridged crossing never contributes an
    interval here (the deck already separates ribbon from road vertically -
    §7.22/§7.26), so bridged crossings are left fully continuous.

    Each contiguous run of in-gap subsamples is rounded outward by one
    subsample step before being returned - conservative, so the discretized
    boundary never leaves a sliver of ribbon poking out from under the
    implied culvert; ROAD_GAP_MARGIN_M's own clearance covers the resulting
    <= 1 subsample-step (~1 m) of over-clip. Returns a list of merged
    (t_start, t_end) pairs, empty if the whole segment clears the corridor
    or every nearby road point on it is a bridge."""
    ts = np.linspace(0.0, 1.0, n_subsamples)
    pts_e = e0 + (e1 - e0) * ts
    pts_n = n0 + (n1 - n0) * ts
    dists, idxs = road_idx["tree"].query(np.stack([pts_e, pts_n], axis=1))
    in_gap = (dists < gap_half_width_m) & (~road_idx["bridge"][idxs])
    n = len(ts)
    raw_intervals = []
    i = 0
    while i < n:
        if in_gap[i]:
            j = i
            while j + 1 < n and in_gap[j + 1]:
                j += 1
            raw_intervals.append((ts[max(0, i - 1)], ts[min(n - 1, j + 1)]))
            i = j + 1
        else:
            i += 1
    merged = []
    for lo, hi in raw_intervals:
        if merged and lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _lerp(a, b, t):
    return a + (b - a) * t


def retained_t_intervals(gap_intervals):
    """[0,1] minus the union of gap_intervals (already sorted/merged by
    find_road_gap_intervals) -> the list of retained (t0,t1) sub-intervals
    a segment's ribbon quad(s) should actually be built from."""
    if not gap_intervals:
        return [(0.0, 1.0)]
    retained = []
    cursor = 0.0
    for lo, hi in gap_intervals:
        if lo > cursor:
            retained.append((cursor, lo))
        cursor = max(cursor, hi)
    if cursor < 1.0:
        retained.append((cursor, 1.0))
    return retained


def build_waterway_ribbons(chains, lat0, lon0, m_per_deg_lon, session, dem_cache, road_idx):
    """Centerline (lat/lon chain) -> list of per-segment flat ribbon quads
    ("lake" dicts, SAME shape build_lake_mesh's caller already produces) +
    a summary dict for verify_waterways_stats.txt. See module docstring for
    the elevation/geometry/road-overlap approach."""
    ribbons = []
    summaries = []
    n_width_tagged = n_width_default = 0
    n_crossings = n_crossings_bridged = n_parallel_clipped = 0
    n_gap_segments = n_gap_fragments_dropped = 0
    total_ribbon_km = 0.0

    for chain in chains:
        lat = np.array([lat for lat, lon in chain["latlon"]])
        lon = np.array([lon for lat, lon in chain["latlon"]])
        east_m = (lon - lon0) * m_per_deg_lon
        north_m = (lat - lat0) * M_PER_DEG_LAT
        if len(east_m) < 2:
            continue

        rs_e, rs_n = resample_polyline(east_m, north_m, WATERWAY_RESAMPLE_M)
        if len(rs_e) < 2:
            continue
        rs_lat = lat0 + rs_n / M_PER_DEG_LAT
        rs_lon = lon0 + rs_e / m_per_deg_lon
        raw_elev = sample_dem(rs_lat, rs_lon, NEAR_DEM_ZOOM, session, dem_cache)

        # Flow direction: a river flows high -> low. Compare mean elevation
        # of the first vs last fifth of the chain; reverse array order (and
        # remember to reverse geometry back) if the chain was digitized
        # low -> high, so index 0 is always the upstream/high end.
        k = max(1, len(raw_elev) // 5)
        reversed_chain = np.mean(raw_elev[:k]) < np.mean(raw_elev[-k:])
        if reversed_chain:
            rs_e, rs_n = rs_e[::-1].copy(), rs_n[::-1].copy()
            raw_elev = raw_elev[::-1].copy()

        smooth_elev = monotonic_flow_profile(raw_elev)
        water_elev = smooth_elev - WATERWAY_DEPTH_M
        z_cm = (water_elev + 0.0 - ORIGIN_HEIGHT_M) * 100.0  # road_ue_z_m inlined for the array case
        # (road_ue_z_m(elev_m, 0.0, ORIGIN_HEIGHT_M) is scalar-only in its
        # existing call sites; the formula itself - elev + lift - origin -
        # is trivially vectorized here, kept numerically identical.)

        half_width_m = chain["width_tag_m"] or WATERWAY_CLASS_WIDTH_M[chain["class"]]
        if chain["width_tag_m"]:
            n_width_tagged += 1
        else:
            n_width_default += 1
        half_width_m = half_width_m / 2.0

        left_e, left_n, right_e, right_n = offset_polyline(rs_e, rs_n, half_width_m)
        clip_left_e, clip_left_n = left_e.copy(), left_n.copy()
        clip_right_e, clip_right_n = right_e.copy(), right_n.copy()

        gap_half_width_m = ROAD_HALF_WIDTH + ROAD_GAP_MARGIN_M
        seg_gaps = []   # (i, t_start, t_end) in chain order, for the verify plot
        n_seg_crossings = n_seg_parallel_clipped = n_seg_gap_segments = n_seg_fragments_dropped = 0
        for i in range(len(rs_e) - 1):
            l0e, l0n, r0e, r0n = left_e[i], left_n[i], right_e[i], right_n[i]
            l1e, l1n, r1e, r1n = left_e[i + 1], left_n[i + 1], right_e[i + 1], right_n[i + 1]
            gap_intervals = []
            if road_idx is not None:
                mid_e = (rs_e[i] + rs_e[i + 1]) / 2.0
                mid_n = (rs_n[i] + rs_n[i + 1]) / 2.0
                dist, ridx = road_idx["tree"].query([mid_e, mid_n])
                if dist < half_width_m + ROAD_HALF_WIDTH + ROAD_WATER_CLEARANCE_M + 10.0:
                    wtx, wty = rs_e[i + 1] - rs_e[i], rs_n[i + 1] - rs_n[i]
                    wlen = math.hypot(wtx, wty) or 1.0
                    wtx, wty = wtx / wlen, wty / wlen
                    rtx, rty = road_idx["tx"][ridx], road_idx["ty"][ridx]
                    cos_ang = abs(wtx * rtx + wty * rty)
                    angle_deg = math.degrees(math.acos(min(1.0, max(-1.0, cos_ang))))
                    is_crossing = angle_deg >= ROAD_CROSSING_ANGLE_DEG
                    if is_crossing:
                        n_seg_crossings += 1
                        if road_idx["bridge"][ridx]:
                            n_crossings_bridged += 1
                            # §7.22 bridge deck already separates ribbon from
                            # road vertically - no gap, draw straight through.
                        else:
                            # §7.27: no bridge at this crossing - cut a gap
                            # spanning the road corridor (+ margin) instead
                            # of drawing the ribbon across pavement at grade.
                            gap_intervals = find_road_gap_intervals(
                                rs_e[i], rs_n[i], rs_e[i + 1], rs_n[i + 1],
                                road_idx, gap_half_width_m)
                            if gap_intervals:
                                n_seg_gap_segments += 1
                                seg_gaps.append((i, gap_intervals))
                    else:
                        l0e, l0n, c0 = clip_corner_to_road(l0e, l0n, rs_e[i], rs_n[i], road_idx)
                        r0e, r0n, c1 = clip_corner_to_road(r0e, r0n, rs_e[i], rs_n[i], road_idx)
                        l1e, l1n, c2 = clip_corner_to_road(l1e, l1n, rs_e[i + 1], rs_n[i + 1], road_idx)
                        r1e, r1n, c3 = clip_corner_to_road(r1e, r1n, rs_e[i + 1], rs_n[i + 1], road_idx)
                        if c0 or c1 or c2 or c3:
                            n_seg_parallel_clipped += 1
                        if c0: clip_left_e[i], clip_left_n[i] = l0e, l0n
                        if c1: clip_right_e[i], clip_right_n[i] = r0e, r0n
                        if c2: clip_left_e[i + 1], clip_left_n[i + 1] = l1e, l1n
                        if c3: clip_right_e[i + 1], clip_right_n[i + 1] = r1e, r1n

            seg_len_m = math.hypot(rs_e[i + 1] - rs_e[i], rs_n[i + 1] - rs_n[i])

            def _emit_quad(ta, tb):
                # Corners/elevation lerped linearly between vertex i and
                # i+1 at parametric ta/tb - the base smoothed water surface
                # (z_cm) is already monotonic non-increasing along the
                # chain, and a linear interpolation between two points of a
                # monotonic sequence stays monotonic, so splitting a segment
                # here changes nothing about flow monotonicity (§7.26).
                qle0, qln0 = _lerp(l0e, l1e, ta), _lerp(l0n, l1n, ta)
                qre0, qrn0 = _lerp(r0e, r1e, ta), _lerp(r0n, r1n, ta)
                qle1, qln1 = _lerp(l0e, l1e, tb), _lerp(l0n, l1n, tb)
                qre1, qrn1 = _lerp(r0e, r1e, tb), _lerp(r0n, r1n, tb)
                q_xy_cm = [(qle0 * 100.0, -qln0 * 100.0), (qre0 * 100.0, -qrn0 * 100.0),
                           (qre1 * 100.0, -qrn1 * 100.0), (qle1 * 100.0, -qln1 * 100.0)]
                za = _lerp(z_cm[i], z_cm[i + 1], ta)
                zb = _lerp(z_cm[i], z_cm[i + 1], tb)
                q_z_cm = float((za + zb) / 2.0)
                verts, uvs, tris, method = build_lake_mesh(q_xy_cm, q_z_cm)
                if len(tris) == 0:
                    return 0.0
                cx_cm = float(np.mean([p[0] for p in q_xy_cm]))
                cy_cm = float(np.mean([p[1] for p in q_xy_cm]))
                ribbons.append({"verts": verts, "uvs": uvs, "tris": tris,
                                 "centroid_cm": (cx_cm, cy_cm)})
                return seg_len_m * (tb - ta)

            for ta, tb in retained_t_intervals(gap_intervals):
                frag_len_m = seg_len_m * (tb - ta)
                if gap_intervals and frag_len_m < MIN_GAP_FRAGMENT_M:
                    n_seg_fragments_dropped += 1
                    continue
                total_ribbon_km += _emit_quad(ta, tb) / 1000.0

        n_crossings += n_seg_crossings
        n_parallel_clipped += n_seg_parallel_clipped
        n_gap_segments += n_seg_gap_segments
        n_gap_fragments_dropped += n_seg_fragments_dropped
        summaries.append({
            "name": chain["name"], "class": chain["class"],
            "n_segments": len(rs_e) - 1, "half_width_m": half_width_m,
            "elev_hi_m": float(smooth_elev[0]), "elev_lo_m": float(smooth_elev[-1]),
            "reversed": bool(reversed_chain),
            "n_crossings": n_seg_crossings, "n_parallel_clipped": n_seg_parallel_clipped,
            "n_gap_segments": n_seg_gap_segments, "n_fragments_dropped": n_seg_fragments_dropped,
            # Full geometry for offline verification plots (map + longitudinal
            # profile) - small enough per-chain (WATERWAY_RESAMPLE_M spacing)
            # to dump for every chain, not just the named highlights.
            "east_m": rs_e.tolist(), "north_m": rs_n.tolist(),
            "raw_elev_m": raw_elev.tolist(), "smooth_elev_m": smooth_elev.tolist(),
            "water_elev_m": water_elev.tolist(),
            "left_e": clip_left_e.tolist(), "left_n": clip_left_n.tolist(),
            "right_e": clip_right_e.tolist(), "right_n": clip_right_n.tolist(),
            # gap intervals for the verify-script overlay: (segment index,
            # [(t_start, t_end), ...]) - lets the plot draw the exact clipped
            # span against the road corridor rather than re-deriving it.
            "seg_gaps": [(int(idx), [(float(a), float(b)) for a, b in ivls])
                         for idx, ivls in seg_gaps],
        })

    stats = {
        "n_width_tagged": n_width_tagged, "n_width_default": n_width_default,
        "n_crossings": n_crossings, "n_crossings_bridged": n_crossings_bridged,
        "n_parallel_clipped": n_parallel_clipped, "total_ribbon_km": total_ribbon_km,
        "n_gap_segments": n_gap_segments, "n_gap_fragments_dropped": n_gap_fragments_dropped,
    }
    return ribbons, summaries, stats


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
        # a dry area is valid (2026-10-02, sf_russian_hill) - terrain treats
        # missing water tiles as "no lake beds"
        print(f"No water polygons matched in the {AREA_NAME} bbox - no water tiles.")
        return

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
        buckets[(tx, ty)].append({"verts": verts, "uvs": uvs, "tris": tris, "is_ribbon": False})

        lake_summaries.append({
            "way_id": poly["way_id"], "name": poly["name"] or "(unnamed)",
            "area_m2": area_m2, "surface_elev_m": surface_elev_m,
            "surface_z_cm": surface_z_cm, "tile": (tx, ty),
            "n_dem_samples": len(elevs), "verts": len(verts), "tris": len(tris),
            "method": method, "ring_latlon": [list(p) for p in ring_ll],
        })

    # ------------------------------------------------------------ waterways
    n_lake_tiles = len(buckets)
    raw_ways, ww_nodes = fetch_waterways()
    chains = chain_waterways(raw_ways, ww_nodes)
    road_idx = build_road_point_index()
    if road_idx is None:
        print("  WARNING: no road points loaded - waterway/road overlap check skipped")
    ribbons, chain_summaries, ww_stats = build_waterway_ribbons(
        chains, lat0, lon0, m_per_deg_lon, session, dem_cache, road_idx)
    for rb in ribbons:
        cx_cm, cy_cm = rb["centroid_cm"]
        tx = int(cx_cm // TERRAIN_TILE_SIZE_CM)
        ty = int(cy_cm // TERRAIN_TILE_SIZE_CM)
        buckets[(tx, ty)].append({"verts": rb["verts"], "uvs": rb["uvs"], "tris": rb["tris"], "is_ribbon": True})
    n_river_chains = sum(1 for c in chains if c["class"] == "river")
    n_stream_chains = sum(1 for c in chains if c["class"] == "stream")
    print(f"\n{len(raw_ways)} waterway way(s) chained into {len(chains)} waterway centerline(s) "
          f"({n_river_chains} river, {n_stream_chains} named stream), {len(ribbons)} ribbon "
          f"segment(s), {ww_stats['total_ribbon_km']:.2f} km total")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    total_verts = total_tris = 0
    written_paths = []
    ribbon_flags = {}   # §7.34: "tx,ty" -> [is_ribbon per feature, SAME order as
                          # write_tile() wrote them] - a Python-only sidecar, NOT
                          # part of the ODL1 .bin format itself (left byte-for-byte
                          # unchanged - ODWaterManager.cpp's runtime reader and
                          # build_terrain_mesh_tiles.py's lake-bed depression pass
                          # both still treat every feature as a generic flat water
                          # surface, exactly as intended - "ribbons render/freeze
                          # like lakes" stays true). Only load_lake_beds()'s NEW
                          # is_ribbon-aware callers (build_road_embankments.py's
                          # §7.23 water-crossing trigger) read this file; a caller
                          # that doesn't know about it (or a stale/missing sidecar)
                          # sees plain lake dicts exactly as before - see that
                          # function's own backward-compatible-default comment.
    for (tx, ty), lakes in buckets.items():
        out_path = OUT_DIR / f"water_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile(out_path, lakes, gen_id_bytes)
        written_paths.append(out_path)
        v = sum(len(lk["verts"]) for lk in lakes)
        t = sum(len(lk["tris"]) for lk in lakes)
        total_verts += v
        total_tris += t
        ribbon_flags[f"{tx},{ty}"] = [bool(lk.get("is_ribbon", False)) for lk in lakes]
        print(f"  tile ({tx},{ty}): {len(lakes)} feature(s), {v} verts, {t} tris -> {out_path.name}")

    n_stale = delete_stale_tiles(OUT_DIR, f"water_{AREA_NAME}_test_tile_*.bin", written_paths)
    print(f"deleted {n_stale} stale water tile(s) from a previous run")

    ribbon_flags_path = OUT_DIR / f"water_{AREA_NAME}_test_ribbon_flags.json"
    ribbon_flags_path.write_text(json.dumps(ribbon_flags))
    n_ribbon_features = sum(sum(1 for v in flags if v) for flags in ribbon_flags.values())
    print(f"wrote ribbon/lake type sidecar ({n_ribbon_features} ribbon feature(s) "
          f"of {sum(len(v) for v in ribbon_flags.values())} total) -> {ribbon_flags_path.name}")

    print(f"\n{len(polygons)} OSM water polygon(s) fetched -> {len(lake_summaries)} lake(s) built "
          f"({n_flowing} flowing-water [river/stream/canal] polygon(s) skipped - flat-plane "
          f"model doesn't apply, see FLOWING_WATER_TAGS; {n_too_small} too small/sliver, "
          f"skipped; {n_fallback_elev} used the ring-vertex "
          f"elevation fallback [interior raster found nothing]; {n_fan_fallback} used the "
          f"fan-triangulation fallback [ear-clip stalled])")
    print(f"waterway ribbons added {len(ribbons)} more feature(s) across "
          f"{len(buckets) - n_lake_tiles} additional tile(s) (lakes alone used {n_lake_tiles} tile(s))")
    print(f"-> {len(buckets)} tile(s) total, {total_verts} verts, {total_tris} tris -> {OUT_DIR}")

    print("\nPer-lake summary:")
    for s in sorted(lake_summaries, key=lambda s: -s["area_m2"])[:20]:
        print(f"  way {s['way_id']:>12} \"{s['name']}\" area={s['area_m2']:.0f} m^2 "
              f"surface_elev={s['surface_elev_m']:.1f} m surface_z={s['surface_z_cm']:.1f} cm "
              f"tile={s['tile']} dem_samples={s['n_dem_samples']} verts={s['verts']} "
              f"tris={s['tris']} method={s['method']}")

    print("\nPer-waterway summary:")
    for s in sorted(chain_summaries, key=lambda s: -s["n_segments"]):
        print(f"  \"{s['name']}\" class={s['class']} segments={s['n_segments']} "
              f"half_width={s['half_width_m']:.1f}m elev {s['elev_hi_m']:.1f}->{s['elev_lo_m']:.1f}m "
              f"reversed={s['reversed']} crossings={s['n_crossings']} "
              f"parallel_clipped={s['n_parallel_clipped']} gap_segments={s['n_gap_segments']} "
              f"fragments_dropped={s['n_fragments_dropped']}")

    print(f"\nwaterway width sourcing: {ww_stats['n_width_tagged']} tagged, "
          f"{ww_stats['n_width_default']} class default")
    print(f"waterway/road crossings: {ww_stats['n_crossings']} "
          f"({ww_stats['n_crossings_bridged']} coincide with a bridge station)")
    print(f"waterway/road parallel-run clips applied: {ww_stats['n_parallel_clipped']}")
    print(f"§7.27 unbridged-crossing gaps: {ww_stats['n_gap_segments']} segment(s) gap-clipped, "
          f"{ww_stats['n_gap_fragments_dropped']} sub-fragment(s) dropped below "
          f"{MIN_GAP_FRAGMENT_M:.1f} m")

    # Dump a machine-readable record (gitignored data/raw, same as every
    # other *_test raw-fetch cache this module already writes) for offline
    # verification scripts to consume without re-running the Overpass fetch.
    dump = {
        "lake_summaries": [
            {k: (list(v) if isinstance(v, tuple) else v) for k, v in s.items()}
            for s in lake_summaries
        ],
        "chain_summaries": chain_summaries,
        "ww_stats": ww_stats,
        "n_lake_tiles": n_lake_tiles,
        "n_total_tiles": len(buckets),
        "total_verts": total_verts,
        "total_tris": total_tris,
    }
    (RAW_DIR / "waterway_build_summary.json").write_text(json.dumps(dump, indent=2))


if __name__ == "__main__":
    main()
