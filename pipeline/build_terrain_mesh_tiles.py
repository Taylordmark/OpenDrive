"""Arc 7.0 pivot (2026-07-23): bake terrain as offline, world-space-tiled
DynamicMesh geometry that AODTerrainManager streams the same way
AODRoadManager already streams road tiles - replacing the old
live-streaming plugin's quantized-mesh streaming entirely. One static,
deterministic dataset per
region, verified (verify_terrain_fit.py) before the engine ever loads it -
the same "carve once offline" model that fixed Arc 7.2's road/terrain fit
problem, just with no live-streamed terrain to reconcile against at all
this time.

Usage:  python build_terrain_mesh_tiles.py   (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Writes  OpenDriveUE/Content/OpenDrive/Masks/terrain_<AREA_NAME>_test_tile_<tx>_<ty>.bin
        (one ODT1 file per built tile; combines what a separate
        build+export pair does for roads into one script, since - unlike
        the road network - there's no intermediate JSON representation
        worth keeping around here)

Distance-from-route tiling (Taylor, 2026-07-23): players never need to be
more than ~5-10 m from the road, but distant terrain still needs to be
VISIBLE (mountain backdrops etc.), just not driveable. Every tile is
classified against the nearest road station's distance to the tile's own
bounds:
  - NEAR   (<= NEAR_MARGIN_M): full-resolution grid, DEM sampled at
    NEAR_DEM_ZOOM (matches the road's own elevation-sampling zoom), carved
    to the road profile (road_mesh_common's carve, adapted to work in the
    local east/north METER frame directly - road_stations.json already
    carries east_m/north_m per station, so there's no need to round-trip
    through lon/lat and fight the pymartini transpose/row-col gotcha that
    bit the old live-streaming version), collision enabled.
  - FAR    (<= RENDER_DISTANCE_M): coarse grid (FAR_GRID_N, same
    FAR_DEM_ZOOM as near tiles - see the constant's own comment for why a
    mismatched zoom here is a real bug, not a style choice). The sparse
    tessellation alone gives the "average/smooth distant terrain" look;
    no collision - the player can never reach it.
  - Beyond RENDER_DISTANCE_M: not built at all (same "no hole, just a
    missing file the runtime never retries" semantics AODRoadManager
    already relies on for MissingTiles).

ODT1 format (Content/OpenDrive/Masks/terrain_<AREA>_test_tile_<tx>_<ty>.bin):
    'O','D','T','1'                                   magic (4 bytes)
    16 bytes   generation id (ASCII hex, pipeline/generation_id.py,
               2026-08-21 - see that module's docstring)
    uint8      has_collision   (0 or 1)
    uint32 LE  vertex_count
    uint32 LE  tri_count
    vertex_count * (float32 x, float32 y, float32 z)   cm, UE world frame
    vertex_count * (float32 u, float32 v)
    tri_count   * (uint32 i0, uint32 i1, uint32 i2)
"""

import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import requests
from pymartini import Martini

import test_areas
from generation_id import GENERATION_ID_BYTES, compute_generation_id, delete_stale_tiles, pack_generation_id
from road_mesh_common import (ROAD_HALF_WIDTH, SKIRT_EXTRA, road_ue_z_m,
                               classify_wall_stations)
from terrain_common import fetch_terrarium_tile, terrarium_to_elevation
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM

# Optional per-area wider bbox for background-visuals-only far tiles (raw
# DEM, no road carving, no collision) - see test_areas.py's own comment.
# getattr, not a plain import, so areas that don't define it (every area
# except green_valley so far) don't need a stub None assignment of their
# own.
BACKGROUND_BBOX_SNWE = getattr(test_areas, "BACKGROUND_BBOX_SNWE", None)

M_PER_DEG_LAT = 111132.0

NEAR_MARGIN_M = 60.0        # tile-granularity buffer beyond the literal
                             # 5-10 m stray corridor
RENDER_DISTANCE_M = 6000.0  # still-visible-but-not-driveable horizon
NEAR_GRID_N = 257           # AUDIT.md item 1 (2026-07-28): base sampling grid for
# adaptive martini triangulation (RTIN requires exactly 2^k+1; 257 -> 7.81 m
# spacing over a 2 km tile, matches z15 Terrarium's ~4.8 m/px so nothing is
# oversampled). This is a CEILING now, not the literal output density - see
# MAX_ERROR_NEAR_M. Replaces the old uniform-41 grid (50 m spacing, no real
# shape off the road corridor - AUDIT.md 3.1).
MAX_ERROR_NEAR_M = 0.3      # martini simplification tolerance, near tiles only.
# Taylor, 2026-07-28: accept 2.0 despite AUDIT.md's storage estimate not
# holding up under measurement - probed 3 real Oahu tiles (flat/moderate/
# Koolau-cliff) at base 257 before committing to the full pipeline (the
# audit's own recommended cheap hedge): retention ran 22-44% even at this
# tolerance, well above the "10-15% on steep terrain" guess, because Oahu's
# eroded volcanic ground carries real high-frequency relief almost
# everywhere rather than the smooth-valley/sharp-ridge split the estimate
# assumed. Revisit if this proves too expensive in practice; NEAR_GRID_N=129
# (one-constant Option A fallback) is the documented escape hatch.
#
# Tightened 2.0 -> 0.3 (2026-08-22, Taylor: drive-tested Green Valley,
# found many spots where the road sat close to but off the terrain surface
# by under a foot - visually terrain poking through the road, or a sharp
# lip climbing back onto it). 2.0 m of allowed simplification error right
# next to a carved road edge is easily enough to produce a sub-foot gap;
# 0.3 m (~1 ft) forces martini to keep enough vertices near the carve zone
# to track the carved (road-following) surface much more closely. Cost is
# more retained triangles/bigger tiles - not yet measured for Green
# Valley's terrain the way Oahu was above; watch bake time and tile size
# on this rebake and revisit (e.g. NEAR_GRID_N=129 fallback) if it's too
# expensive.
SEAM_SKIRT_DROP_M = 10.0    # see build_seam_skirt()'s docstring - hides the
                             # crack an adaptive mesh can produce at a tile
                             # boundary, the same "gap hidden under geometry"
                             # pattern road_mesh_common.py's road skirt uses.
FAR_GRID_N = 21              # 100 m spacing (2026-07-28, was 9/~250m) - Taylor:
# hillsides visible off to the side of the road read as hard-faceted,
# obviously low-poly ("looks like Windows XP") at the old spacing. Far tiles
# stay on the old uniform-grid path (no adaptive triangulation - they're
# already coarse, no collision, and cost isn't the concern there); the
# NEAR/FAR boundary is now protected by NEAR tiles' own seam skirt (below)
# rather than requiring (NEAR_GRID_N-1) to divide evenly into (FAR_GRID_N-1)
# the way the old uniform-uniform pairing did.
NEAR_DEM_ZOOM = 15          # matches build_road_stations.py's DEM_ZOOM
# FAR_DEM_ZOOM used to be 10 (coarse/pre-generalized Terrarium data, on the
# idea that it'd read as "averaged distant terrain" for free). Real bug,
# found live 2026-07-25: a near tile's grid and its neighboring far tile's
# grid share exact boundary vertex positions (linspace(x0,x1,n) always hits
# both endpoints, and 40 near-segments / 8 far-segments = 5, so every 5th
# near vertex lands exactly on a far vertex) - but at zoom 10 vs 15 those
# shared points sampled DIFFERENT elevation data entirely, so every
# near/far seam carried a real, often large step (worse in the Cascades'
# steep relief) - not a soft LOD crack, a wall/gap the width of the whole
# tile edge. Matching the zoom makes every shared boundary vertex
# numerically identical (same DEM sample + same carve at the same lat/lon);
# FAR_GRID_N's sparse tessellation alone still gives the coarse/averaged
# distant-terrain look, so nothing is lost by dropping the second zoom.
FAR_DEM_ZOOM = NEAR_DEM_ZOOM

# Carve radius, sized to THIS grid's own ~50 m sample spacing (2000 m /
# (NEAR_GRID_N-1)) - NOT reused from build_terrain_tiles.py's
# CARVE_INNER_M/OUTER_M (10/40 m), which were tuned for a ~4 m/px
# live-streamed raster. Found 2026-07-23 via verify_terrain_fit.py: with the
# 40 m live-streamed-era value, many grid vertices near a station sat far
# enough away that the
# carve never fully saturated (w<1) before the next vertex, so barycentric
# interpolation at the station's exact point blended in raw hillside
# elevation - harmless on gentle ground but a real 10-14 m spike in the
# Cascades foothills' steeper terrain, since DEM height can vary a lot over
# just 25-40 m there. INNER (50 m) comfortably covers a grid cell's worst-
# case half-diagonal so the nearest vertex to any station saturates fully;
# OUTER (150 m, 3x spacing) gives a smooth taper back to raw terrain beyond
# that instead of a hard edge.
TERRAIN_CARVE_INNER_M = 50.0
TERRAIN_CARVE_OUTER_M = 150.0
TERRAIN_CARVE_DROP_M = 0.15   # bias the carved surface just below the road so
                              # it can't z-fight/poke through - matches
                              # road_mesh_common.carve_heightmap's proven 0.15
                              # default (Arc 7.1/7.2) and the drop
                              # build_forest_elevation.py's tree ground-truth is
                              # already baked at, so terrain/road/trees all agree.
                              # (Was 1.5 - an unjustified inflation that left the
                              # road+trees floating ~1.35 m above the visible
                              # terrain, fixed 2026-07-24. No larger margin is
                              # needed for banking: road_mesh_common raises only
                              # the OUTER edge through curves, never dropping any
                              # edge below the centerline the carve references.)
                              #
                              # Briefly raised 0.15 -> 0.5 (2026-08-23) to paper
                              # over martini's simplification error (up to
                              # MAX_ERROR_NEAR_M=0.3 m) eating this margin -
                              # worked, but left the road ~0.4-0.6 m above the
                              # shoulder everywhere (Taylor: wanted ~0.15 m,
                              # "only criteria being road always above
                              # environment"). A blanket drop can't satisfy
                              # both - the fix is HARD_CLAMP_MARGIN_M below,
                              # which enforces the invariant only where it's
                              # actually needed (under the physical road
                              # footprint) instead of inflating the drop
                              # everywhere the 50-150 m blend reaches. Reverted
                              # to 0.15 now that the clamp covers the guarantee.

# "Environment reducer after the cut" (Taylor, 2026-08-23): even with a
# correct carve stamp, martini is free to drop any grid vertex whose error is
# within MAX_ERROR_NEAR_M - so a point exactly under the road could still sit
# up to that much above where carve_grid_m placed it, poking through the road
# surface regardless of TERRAIN_CARVE_DROP_M. Clamping every grid point
# within the road's actual paved+skirt footprint (not the full 150 m carve
# blend) to (road_elev - HARD_CLAMP_MARGIN_M) BEFORE martini sees the grid
# guarantees this by construction: martini only ever selects a subset of
# already-clamped points as vertices, and linear interpolation between two
# capped values can't exceed the cap either - so the guarantee survives
# simplification without needing it applied a second time post-triangulation.
# Kept separate from TERRAIN_CARVE_DROP_M/_INNER_M/_OUTER_M, which still
# control the wider cosmetic blend taper back to raw terrain.
HARD_CLAMP_RADIUS_M = ROAD_HALF_WIDTH + SKIRT_EXTRA   # 7.5 m - the literal
                                                       # paved+skirt width
HARD_CLAMP_MARGIN_M = 0.15    # matches TERRAIN_CARVE_DROP_M's own small gap

# Cut/fill asymmetry (2026-08-24, Taylor: switchback screenshots showing the
# road buried under terrain that should have been cut away for it). The
# single 50/150 m taper above was calibrated purely to hide martini/skirt
# seams (see its own comment) - it says nothing about which side of the road
# the ground is on, so a hillside pixel (raw terrain sits ABOVE road
# elevation - a cut) gets the exact same 150 m gentle taper back to raw as a
# drop-off pixel (raw terrain sits BELOW road elevation - a fill). On a
# switchback the inboard/hillside terrain is often close in elevation to the
# OTHER leg of the switchback, not this one, so a wide taper lets raw
# hillside height bleed most of the way back in before it's fully faded -
# reading as "terrain sitting on top of the road" instead of a clean cut
# face. Fill pixels don't have this problem (there's no ground to bleed back
# in from below) and keep the original 50/150 taper so we don't invent fake
# embankments. `best_d <= best_elev-vs-raw sign` decides which taper a given
# pixel uses - see carve_grid_m.
# 2026-08-24 correction: the first cut (12/25 m) was tighter than the grid
# itself can represent - NEAR tiles sample at ~7.8 m spacing (2000 m /
# (257-1)) and martini only keeps points within MAX_ERROR_NEAR_M=0.3 m, so a
# trench that steep and only 1-3 grid cells wide got treated as noise:
# simplification skipped straight over the narrow feature and bridged across
# it, leaving the road plank floating over a V-notch the surviving triangles
# barely touched (Taylor's screenshot). 25/75 m is still meaningfully
# tighter than the 50/150 m fill taper (a real visible cut, not a graded
# slope) while spanning ~3-10 grid cells - enough samples for martini to
# keep the feature under its own error tolerance instead of discarding it.
TERRAIN_CARVE_CUT_INNER_M = 25.0
TERRAIN_CARVE_CUT_OUTER_M = 75.0

# FAR-tile burying bug (2026-08-26, Taylor: Lake Hughes Road screenshot -
# "the road was not vanishing, it was being buried by terrain" on a distant
# hillside, NOT a spawn-radius pop-in gap). Root cause, confirmed numerically
# via a throwaway check against the real build_tile(is_near=False) path
# before this fix: FAR_GRID_N=21 over a 2000 m tile is ~100 m vertex spacing,
# but carve_grid_m's own saturation radius (TERRAIN_CARVE_INNER_M=50 m, or
# TERRAIN_CARVE_CUT_INNER_M=25 m on a cut/hillside pixel) and the actual
# paved+skirt hard-clamp radius (HARD_CLAMP_RADIUS_M=7.5 m) are both far
# narrower than that spacing - direct point-sampling at the sparse 100 m grid
# routinely lands every vertex outside the carve's effective radius of the
# road, so the coarse surface just interpolates raw hillside DEM straight
# through where the road actually is. Measured on green_valley's route
# stations: 78.7% of in-scope stations showed the FAR-resolution mesh sitting
# above the intended (carved) road surface, median +0.90 m, up to +42 m on
# the worst tile - not a rare edge case, the FAR grid's normal behavior on
# sloped/curvy ground.
#
# Fix: sample+carve at a much finer internal resolution first (this many
# fine samples per FAR_GRID_N output cell), then downsample to the FAR_GRID_N
# output grid by taking the MINIMUM elevation in each output vertex's own
# footprint (half a fine-cell towards each neighbor), not by point-sampling.
# martini-style adaptive triangulation isn't used here (see FAR_GRID_N's own
# comment - far tiles intentionally stay a cheap uniform grid), so this is
# the equivalent guarantee for a uniform grid: an output vertex can never end
# up above the finest carve result anywhere in its own neighborhood, the same
# reasoning HARD_CLAMP_MARGIN_M already relies on for near tiles (a min/clamp
# survives naive downsampling/interpolation, a direct sample does not).
# Factor of 10 puts the fine sampling at 2000/((21-1)*10) = 10 m spacing -
# comfortably inside TERRAIN_CARVE_CUT_INNER_M (25 m) so a real cut face gets
# several fine samples across it instead of being averaged/skipped entirely,
# matching the same "several grid cells across the feature" reasoning
# TERRAIN_CARVE_CUT_INNER_M's own comment already used for near tiles.
FAR_SUPERSAMPLE = 10

# Elevation-conflict detection (station pairs close in XY, too far apart in
# Z to be the same piece of ground - e.g. a switchback doubling back on
# itself, or two different chains crossing) used to be reimplemented here
# independently of build_road_stations.py's own copy - the two disagreed by
# construction (different radius, different threshold shape). Both are now
# road_mesh_common.classify_wall_stations() (2026-07-30 consolidation),
# called below against the SAME final station data build_road_stations.py
# already folded its own copy of this decision into `is_bridge` from -
# calling it again here isn't redundant with that, it's what GUARANTEES the
# two agree (same function, same inputs, deterministic), instead of hoping
# two independent implementations happen to.
ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

# Lake-bed depression (2026-09-03, Arc 7.15 follow-on): build_water_tiles.py
# bakes lake surfaces at their exact runtime Z (AODWaterManager places every
# lake vert verbatim, no runtime offset - see ODWaterManager.cpp's
# CreateLakeComponent comment), but nothing ever told the TERRAIN bake a
# lake was there, so the ground kept its raw/carved DEM height straight
# through a reservoir's footprint - at Dillon Reservoir that raw height
# sits at/above the water plane, burying it (no visible water, "dry land
# where the reservoir should be").
#
# Fix: read the water bake's OWN baked .bin tiles back (every
# water_<AREA>_test_tile_*.bin in OUT_DIR, regardless of which terrain tile
# they were bucketed under - build_water_tiles.py buckets a whole lake by
# its centroid, so a big reservoir's mesh can extend well outside its own
# bucket tile's 2 km box) as the source of truth for both the lake polygon
# AND its surface elevation - no re-fetching OSM, no re-running the DEM
# percentile calc (which could drift from the actual baked value), just the
# literal geometry ODWaterManager will place at runtime. A lake mesh's
# OUTER boundary is recovered generically from its triangle soup (any edge
# shared by exactly one triangle is a boundary edge, chained into a ring) -
# this works regardless of ear-clip vs. fan-fallback triangulation and
# naturally ignores the interior island/point vertices build_water_tiles.py
# already documents as NOT subtracted (they're interior to the solid
# triangulated water, so they never appear as boundary edges).
WATER_TILE_GLOB = f"water_{AREA_NAME}_test_tile_*.bin"
WATER_BED_MIN_DEPTH_M = 2.0     # bed depth right at the shoreline
WATER_BED_MAX_DEPTH_M = 5.0     # bed depth WATER_BED_FALLOFF_M+ into the lake
WATER_BED_FALLOFF_M = 40.0      # shoreline->interior depth ramp distance
# 2026-09-03 fix: the INTERIOR falloff above starts at WATER_BED_MIN_DEPTH_M
# immediately at the ring boundary (dist_m==0), but nothing ever touched the
# EXTERIOR side of that same boundary - raw/carved DEM height was left in
# place right up to the shoreline. Real DEM height at the shore is close to
# the lake's own surface elevation, so the two combined into a sudden ~2m
# vertical step running the entire perimeter of every lake (screenshot
# dillon_shore3.png's "gray stepped terrain shelf" - diagnosis (a): rim
# sticks up above the water plane and occludes it). Fix: blend the EXTERIOR
# terrain down to the same shore target height the interior starts at,
# over WATER_SHORE_BLEND_M outward from the ring, so the two meet with zero
# discontinuity and the water plane can be seen lapping the bank instead of
# stopping at a cliff edge.
#
# 2026-09-03 follow-up (reviewer defect: a hard-edged dark band/cliff lip
# still visible right at the shoreline in dillon_r3_shore.png/
# dillon_r3_waterlevel.png): 12 m was too short a run for a 2 m
# (WATER_BED_MIN_DEPTH_M) drop - a ~17% average grade, and worse, the
# original LINEAR blend_t ramp put its full slope right at dist_m==0 (a
# linear ramp's derivative doesn't ease in), so the exterior terrain was
# steepest exactly AT the shoreline, which is precisely where the "cliff"
# read as visible. Two changes below: widen the run so the same 2 m drop
# reads as a gentle beach instead of a bank, and switch the ramp from
# linear to a smoothstep (3t^2-2t^3, see apply_water_depression()) - zero
# slope at BOTH ends means the terrain eases into the drop gently right at
# the shoreline and eases back to natural grade at the outer edge, instead
# of kinking at either seam.
WATER_SHORE_BLEND_M = 35.0      # exterior rim->shore blend distance


def _read_lake_bin_tile(path):
    """Parses one water_<AREA>_test_tile_<tx>_<ty>.bin (ODL1 format, see
    build_water_tiles.py's module docstring) -> list of (verts (N,3)
    float32 cm, tris (T,3) uint32) per lake in the file. Read-only reuse of
    the water bake's own output; this format is intentionally NOT re-derived
    from OSM/DEM here (see this module's lake-bed-depression comment)."""
    raw = path.read_bytes()
    if raw[:4] != b"ODL1":
        return []
    cur = 4 + GENERATION_ID_BYTES
    (lake_count,) = struct.unpack_from("<I", raw, cur); cur += 4
    lakes = []
    for _ in range(lake_count):
        vcount, tcount = struct.unpack_from("<II", raw, cur); cur += 8
        verts = np.frombuffer(raw, dtype="<f4", count=vcount * 3, offset=cur).reshape(-1, 3)
        cur += vcount * 12
        cur += vcount * 8  # uvs - not needed for the depression test, skip over
        tris = np.frombuffer(raw, dtype="<u4", count=tcount * 3, offset=cur).reshape(-1, 3)
        cur += tcount * 12
        lakes.append((verts.copy(), tris.copy()))
    return lakes


def _extract_boundary_rings(tris):
    """Any edge shared by exactly one triangle is a mesh boundary edge -
    chains them (via shared vertex ids) into closed ring(s) of vertex
    indices. Works for any flat triangulated disk regardless of
    ear-clip/fan-fallback method or how many disjoint outer loops a
    multipolygon-relation lake (e.g. Dillon Reservoir's 4 outer segments)
    produced.

    Non-manifold-junction guard (2026-09-03, mirrors AODForestManager::
    LoadWaterLakes()'s identical fix - see its comment for the full
    writeup, including a correction): a stream/river confluence baked into
    the same lake mesh entry puts a vertex on 3+ boundary edges, which the
    greedy "first unvisited neighbor" walk below can't correctly resolve
    into separate loops - it could in principle silently splice two
    unrelated shoreline loops into one huge self-intersecting "ring". A
    1918-vertex ring spanning 7 km x 6.7 km was found in Summit County's
    data and initially suspected to be exactly this, but was re-verified
    with shapely (Polygon(...).is_valid == True) and by shoelace area
    (13.28 km², matching real Dillon Reservoir's ~13.08 km² surface area):
    it is the real, valid, single reservoir outline, baked large because
    this module bakes each lake's FULL unclipped polygon into every 2 km
    tile it touches rather than clipping per-tile - not an artifact of this
    bug. On today's data this guard is confirmed to be a no-op (0 rings
    ever flagged corrupt); it is kept as defense-in-depth against a genuine
    future river-confluence/non-manifold bake. A genuine simple closed loop
    never revisits a vertex before closing back to its own start, so any
    walk that does is discarded as corrupt rather than kept - under-
    excluding a few meters of shoreline near a confluence beats fabricating
    a multi-km phantom lake that (via load_lake_beds()'s downstream
    point-in-polygon test) would carve a bogus depression across a huge
    swath of unrelated terrain."""
    edge_count = defaultdict(int)
    for a, b, c in tris:
        for u, v in ((a, b), (b, c), (c, a)):
            edge_count[(min(u, v), max(u, v))] += 1
    boundary = [e for e, n in edge_count.items() if n == 1]
    adj = defaultdict(list)
    for u, v in boundary:
        adj[u].append(v)
        adj[v].append(u)
    visited = set()
    rings = []
    n_rejected = 0
    for u, v in boundary:
        if (u, v) in visited:
            continue
        ring = [u]
        in_ring = {u}
        corrupt = False
        prev, cur = u, v
        visited.add((u, v)); visited.add((v, u))
        while cur != ring[0]:
            if cur in in_ring:
                corrupt = True
                break
            ring.append(cur)
            in_ring.add(cur)
            nxt = next((n for n in adj[cur] if n != prev and (cur, n) not in visited), None)
            if nxt is None:
                break
            visited.add((cur, nxt)); visited.add((nxt, cur))
            prev, cur = cur, nxt
        if corrupt:
            n_rejected += 1
            continue
        if cur == ring[0] and len(ring) >= 3:
            rings.append(ring)
    if n_rejected:
        print(f"  _extract_boundary_rings: discarded {n_rejected} corrupt ring(s) "
              f"(river-confluence junction artifacts, not real shorelines)")
    return rings


def load_lake_beds():
    """Every baked lake's boundary ring (gx_cm/gy_cm-frame, i.e. the SAME
    x=east_m*100, y=-north_m*100 convention build_tile()'s grid uses) plus
    its baked surface Z (cm) and bounding box, read back from
    OUT_DIR/water_<AREA>_test_tile_*.bin. Returns [] (terrain bakes with no
    lake depression, same "no hole" tolerance every other missing-tile path
    here already has) if no water tiles have been baked yet - run
    build_water_tiles.py first to get lake beds."""
    paths = sorted(OUT_DIR.glob(WATER_TILE_GLOB))
    lakes = []
    for path in paths:
        for verts, tris in _read_lake_bin_tile(path):
            if len(tris) == 0:
                continue
            for ring_idx in _extract_boundary_rings(tris):
                ring_xy = verts[ring_idx][:, :2]
                bbox = (float(ring_xy[:, 0].min()), float(ring_xy[:, 0].max()),
                        float(ring_xy[:, 1].min()), float(ring_xy[:, 1].max()))
                # Defense-in-depth sanity cap, mirrors AODForestManager::
                # LoadWaterLakes()'s LakeRingMaxExtentCm - measured live (2026-09-03):
                # Dillon Reservoir's own real, valid boundary ring spans up to ~7 km
                # on its long axis (each lake's full unclipped polygon is baked into
                # every 2 km tile it touches, not clipped per-tile), so this cap needs
                # real headroom above that, not a tight guess. 30 km is generous for
                # any single lake this project will plausibly bake, while still
                # catching a genuinely pathological artifact.
                LAKE_RING_MAX_EXTENT_CM = 3_000_000.0   # 30 km
                if (bbox[1] - bbox[0] > LAKE_RING_MAX_EXTENT_CM
                        or bbox[3] - bbox[2] > LAKE_RING_MAX_EXTENT_CM):
                    print(f"  load_lake_beds: rejected an oversized lake ring "
                          f"({len(ring_idx)} verts, {(bbox[1]-bbox[0])/100:.0f} x "
                          f"{(bbox[3]-bbox[2])/100:.0f} m bbox) from {path.name} - "
                          f"likely a river-confluence junction artifact")
                    continue
                z_cm = float(verts[ring_idx[0], 2])
                lakes.append({
                    "ring_x": ring_xy[:, 0].astype(np.float64),
                    "ring_y": ring_xy[:, 1].astype(np.float64),
                    "z_cm": z_cm,
                    "bbox": bbox,
                })
    if lakes:
        print(f"  loaded {len(lakes)} lake bed(s) from {len(paths)} water tile(s) for "
              f"terrain depression")
    else:
        print(f"  no baked water tiles found ({WATER_TILE_GLOB}) - terrain will NOT "
              f"depress for lakes; run build_water_tiles.py first for visible water")
    return lakes


def _point_in_polygon(px, py, ring_x, ring_y):
    """Vectorized even-odd ray-casting point-in-polygon test, same algorithm
    as build_water_tiles.point_in_polygon (independent copy - see this
    module's lake-bed-depression comment for why the water bake's baked
    OUTPUT is reused instead of importing that module's fetch/elevation
    code)."""
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


def _dist_to_ring(px, py, ring_x, ring_y):
    """Min distance (cm) from each point to the polygon boundary (point-to-
    segment over every ring edge) - used only for the shoreline->interior
    depth falloff, so approximate/segment-wise is plenty."""
    n = len(ring_x)
    min_d = np.full(len(px), np.inf)
    for i in range(n):
        x1, y1 = ring_x[i], ring_y[i]
        x2, y2 = ring_x[(i + 1) % n], ring_y[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        seg_len2 = dx * dx + dy * dy
        t = 0.0 if seg_len2 < 1e-6 else np.clip(((px - x1) * dx + (py - y1) * dy) / seg_len2, 0.0, 1.0)
        cx, cy = x1 + t * dx, y1 + t * dy
        min_d = np.minimum(min_d, np.hypot(px - cx, py - cy))
    return min_d


def apply_water_depression(gx_cm, gy_cm, z_cm, lakes):
    """For every grid point inside a baked lake polygon, override z_cm with
    a bed elevation comfortably below that lake's own baked water-plane Z
    (WATER_BED_MIN_DEPTH_M at the shore, ramping to WATER_BED_MAX_DEPTH_M
    over WATER_BED_FALLOFF_M) instead of leaving raw/carved DEM height in
    place - see this module's lake-bed-depression comment. Applied to the
    tile's FINAL grid (post road-carve, post FAR downsample), so it covers
    both the NEAR and FAR tile paths identically - build_tile() calls this
    once, after z_cm is computed, before either triangulation branch."""
    if not lakes:
        return z_cm
    tile_x0, tile_x1 = float(gx_cm.min()), float(gx_cm.max())
    tile_y0, tile_y1 = float(gy_cm.min()), float(gy_cm.max())
    blend_cm = WATER_SHORE_BLEND_M * 100.0
    flat_x = gx_cm.reshape(-1)
    flat_y = gy_cm.reshape(-1)
    flat_z = z_cm.reshape(-1).copy()
    touched = False
    for lake in lakes:
        bx0, bx1, by0, by1 = lake["bbox"]
        # bbox reject widened by the exterior blend distance too, so the rim
        # band just outside the ring isn't skipped.
        if (bx1 + blend_cm < tile_x0 or bx0 - blend_cm > tile_x1 or
                by1 + blend_cm < tile_y0 or by0 - blend_cm > tile_y1):
            continue
        inside = _point_in_polygon(flat_x, flat_y, lake["ring_x"], lake["ring_y"])
        shore_z_cm = lake["z_cm"] - WATER_BED_MIN_DEPTH_M * 100.0

        if inside.any():
            touched = True
            idx = np.nonzero(inside)[0]
            dist_m = _dist_to_ring(flat_x[idx], flat_y[idx], lake["ring_x"], lake["ring_y"]) / 100.0
            depth_m = WATER_BED_MIN_DEPTH_M + (WATER_BED_MAX_DEPTH_M - WATER_BED_MIN_DEPTH_M) * \
                np.clip(dist_m / WATER_BED_FALLOFF_M, 0.0, 1.0)
            flat_z[idx] = lake["z_cm"] - depth_m * 100.0

        # Exterior rim band: blend raw terrain height down to shore_z_cm (the
        # SAME height the interior side starts at, at dist_m==0) as points
        # approach the ring from outside, so there is no seam at the
        # boundary - a continuous bowl into the lake instead of a cliff.
        outside = ~inside
        if not outside.any():
            continue
        out_idx = np.nonzero(outside)[0]
        # cheap pre-filter: only bother with points within blend_cm of the
        # lake's (padded) bbox before paying for the exact ring distance.
        near = ((flat_x[out_idx] > bx0 - blend_cm) & (flat_x[out_idx] < bx1 + blend_cm) &
                (flat_y[out_idx] > by0 - blend_cm) & (flat_y[out_idx] < by1 + blend_cm))
        if not near.any():
            continue
        out_idx = out_idx[near]
        dist_m = _dist_to_ring(flat_x[out_idx], flat_y[out_idx], lake["ring_x"], lake["ring_y"]) / 100.0
        lin_t = np.clip(1.0 - dist_m / WATER_SHORE_BLEND_M, 0.0, 1.0)  # 1 at ring, 0 at blend edge
        # Smoothstep, not linear (2026-09-03 shore-cliff fix - see
        # WATER_SHORE_BLEND_M's comment): zero slope at both dist_m==0 (the
        # shoreline) and dist_m==WATER_SHORE_BLEND_M (natural grade) instead
        # of a constant-slope ramp that kinks the terrain normal at both
        # seams and reads steepest exactly at the waterline.
        blend_t = lin_t * lin_t * (3.0 - 2.0 * lin_t)
        rim_idx = out_idx[blend_t > 0.0]
        if len(rim_idx) == 0:
            continue
        touched = True
        t = blend_t[blend_t > 0.0]
        flat_z[rim_idx] = flat_z[rim_idx] * (1.0 - t) + shore_z_cm * t
    return flat_z.reshape(z_cm.shape) if touched else z_cm


_LAKE_BEDS_CACHE = None


def _get_lake_beds():
    """Lazily loads+caches load_lake_beds() once per process - build_tile()
    calls this internally (no signature change) so every existing caller
    (build_forest_elevation.py, experimental_junction_pads.py,
    verify_terrain_fit.py, this module's own main()) picks up lake-bed
    depression automatically and identically, off the same disk read done
    only once."""
    global _LAKE_BEDS_CACHE
    if _LAKE_BEDS_CACHE is None:
        _LAKE_BEDS_CACHE = load_lake_beds()
    return _LAKE_BEDS_CACHE


def load_stations():
    """Returns (lat0, lon0, east, north, elev, exclude, wall_pairs). `exclude`
    is True for any station the terrain carve must skip (real bridge/tunnel,
    OR an elevation conflict - either wall-eligible or out-of-scope, see
    classify_wall_stations()). `wall_pairs` is the (i, j) station-index list
    of in-scope, above-WALL_MIN_GRADE conflicts that need generated wall
    geometry - build_retaining_walls.py consumes this same loader so its
    run-clustering always starts from the identical pair list terrain
    carving used to decide what to exclude."""
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    is_bridge = np.concatenate([
        np.array(seg.get("is_bridge", [False] * len(seg["east_m"])), dtype=bool)
        for seg in data["segments"]])
    seg_ids = np.concatenate([
        np.full(len(seg["east_m"]), si, dtype=np.int32)
        for si, seg in enumerate(data["segments"])])
    station_idx = np.concatenate([
        np.arange(len(seg["east_m"])) for seg in data["segments"]])
    road_name = [name for seg in data["segments"] for name in seg["road_name"]]

    conflict, wall_pairs = classify_wall_stations(
        east, north, elev, seg_ids, station_idx, road_name)
    n_new = int(np.sum(conflict & ~is_bridge))
    if n_new:
        print(f"classify_wall_stations: {n_new} station(s) auto-flagged "
              f"(crossing or same-chain switchback, no bridge/tunnel tag) - "
              f"excluded from the terrain carve the same way is_bridge "
              f"stations are ({len(wall_pairs)} pair(s) wall-eligible)")
    exclude = is_bridge | conflict
    return lat0, lon0, east, north, elev, exclude, wall_pairs


def sample_dem(lat_arr, lon_arr, zoom, session, cache):
    """Bilinear-sample Terrarium elevation at `zoom` for arrays of lat/lon,
    fetching+caching whichever 256x256 tiles are needed into `cache` (dict
    keyed by (zoom, tx, ty) -> elevation array).

    AUDIT-RELIABILITY.md SS5.3 (2026-08-21): a DEM tile that fails to fetch
    (after fetch_terrarium_tile's own retries are exhausted) used to be
    cached as None and read by px() as a silent 0.0 m - a transient network
    error baked a sea-level patch into whatever tile was building at the
    time, uncounted and unreported, and the next (successful) rebake made it
    disappear - "reruns produce different results and I can't reproduce it."
    Fail fast instead, matching this project's existing rebuild_area.py
    convention: an unrecoverable fetch failure aborts the whole bake
    immediately with the offending tile named, rather than being silently
    downgraded to bad data on disk."""
    n = 256 * (2 ** zoom)
    lat_r = np.radians(lat_arr)
    px_glob = (lon_arr + 180.0) / 360.0 * n
    py_glob = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
    tx_arr = (px_glob // 256).astype(int)
    ty_arr = (py_glob // 256).astype(int)

    needed = set(zip(tx_arr.tolist(), ty_arr.tolist()))
    for (tx, ty) in list(needed):
        needed.update({(tx + 1, ty), (tx, ty + 1), (tx + 1, ty + 1)})
    for (tx, ty) in needed:
        key = (zoom, tx, ty)
        if key in cache:
            continue
        try:
            img = fetch_terrarium_tile(zoom, tx, ty, session)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"DEM tile fetch failed after retries: zoom={zoom} tx={tx} "
                f"ty={ty} ({exc}) - aborting bake rather than caching a "
                f"silent sea-level patch (AUDIT-RELIABILITY.md SS5.3). Rerun "
                f"once the network/S3 issue clears."
            ) from exc
        cache[key] = terrarium_to_elevation(img)

    def px(tx, ty, x, y):
        return float(cache[(zoom, tx, ty)][y, x])

    out = np.empty(len(lat_arr), dtype=np.float32)
    for i in range(len(lat_arr)):
        tx, ty = int(tx_arr[i]), int(ty_arr[i])
        lx, ly = px_glob[i] - tx * 256, py_glob[i] - ty * 256
        x0, y0 = min(int(lx), 255), min(int(ly), 255)
        fx, fy = lx - x0, ly - y0
        tx1, x1 = (tx + 1, 0) if x0 == 255 else (tx, x0 + 1)
        ty1, y1 = (ty + 1, 0) if y0 == 255 else (ty, y0 + 1)
        v00, v01 = px(tx, ty, x0, y0), px(tx1, ty, x1, y0)
        v10, v11 = px(tx, ty1, x0, y1), px(tx1, ty1, x1, y1)
        out[i] = (v00 * (1 - fx) * (1 - fy) + v01 * fx * (1 - fy)
                  + v10 * (1 - fx) * fy + v11 * fx * fy)
    return out


def tile_min_dist_m(station_x_cm, station_y_cm, tx, ty, tile_cm):
    """Min distance (m) from ANY station to this tile's bounding box - the
    gating signal for near/far/skip. Clips each station to the box first
    (matches build_terrain_tiles.py's road_near_tile bbox-pad approach, just
    exact-distance instead of a margin-padded bbox overlap test)."""
    x0, x1 = tx * tile_cm, (tx + 1) * tile_cm
    y0, y1 = ty * tile_cm, (ty + 1) * tile_cm
    cx = np.clip(station_x_cm, x0, x1)
    cy = np.clip(station_y_cm, y0, y1)
    d_cm = np.hypot(station_x_cm - cx, station_y_cm - cy)
    return float(d_cm.min()) / 100.0


CARVE_STATION_CHUNK = 2000   # see carve_grid_m's docstring - bounds the
                             # (grid_points x stations) distance matrix

def carve_grid_m(elev_grid, east_grid, north_grid, st_east, st_north, st_elev,
                  inner=TERRAIN_CARVE_INNER_M, outer=TERRAIN_CARVE_OUTER_M,
                  drop=TERRAIN_CARVE_DROP_M,
                  cut_inner=TERRAIN_CARVE_CUT_INNER_M,
                  cut_outer=TERRAIN_CARVE_CUT_OUTER_M):
    """Same inverse-distance-stamp + smoothstep blend as
    road_mesh_common.carve_heightmap, but operating directly in the local
    east/north METER frame instead of a lon/lat-bounded pixel grid - no
    row/col transpose ambiguity to get backwards (the old live-streaming
    version's 2026-07-20 bug) since there's no pymartini array-order convention to
    match here at all, just plain 2-D arrays this script fully controls.

    Cut/fill asymmetric taper (see TERRAIN_CARVE_CUT_INNER_M's comment): a
    pixel whose RAW terrain sits above the nearest station's elevation (a
    hillside/cut pixel) blends back to raw over `cut_inner..cut_outer`
    instead of `inner..outer`, so the hillside reads as a cut face carved
    for the road rather than gently regaining its natural slope over 150 m.
    Fill pixels (raw terrain below road) are unaffected - they keep the
    original wide taper.

    Chunked over stations (2026-07-28) - the naive one-shot version built a
    dense (grid_points x stations) distance matrix (up to 257x257=66,049
    grid points), which is fine for a handful of nearby stations but not
    bounded: a dense mountain switchback area can hand build_tile() several
    thousand "keep" stations for one tile (prefiltered by distance, not
    count), and 66,049 x thousands x 8 bytes is multiple GB for a SINGLE
    tile - found live during the full-Oahu rebake (2026-07-28): process RSS
    swung 5.6->20 GB tile to tile, not a steady climb, which is the
    signature of one-off huge transient allocations on specific dense
    tiles rather than an actual leak. Processing stations in fixed-size
    chunks and keeping a running nearest-distance/elevation bounds peak
    memory to grid_points x CARVE_STATION_CHUNK regardless of how many
    stations a tile has nearby."""
    if len(st_east) == 0:
        return elev_grid
    shape = elev_grid.shape
    gx = east_grid.reshape(-1)
    gy = north_grid.reshape(-1)
    P = gx.shape[0]
    best_d = np.full(P, np.inf, dtype=np.float64)
    best_elev = np.zeros(P, dtype=np.float64)
    for c0 in range(0, len(st_east), CARVE_STATION_CHUNK):
        sx = st_east[c0:c0 + CARVE_STATION_CHUNK].reshape(1, -1)
        sy = st_north[c0:c0 + CARVE_STATION_CHUNK].reshape(1, -1)
        se = st_elev[c0:c0 + CARVE_STATION_CHUNK]
        d = np.hypot(gx.reshape(-1, 1) - sx, gy.reshape(-1, 1) - sy)   # (P, chunk)
        nearest = np.argmin(d, axis=1)
        nearest_d = d[np.arange(P), nearest]
        better = nearest_d < best_d
        best_d = np.where(better, nearest_d, best_d)
        best_elev = np.where(better, se[nearest], best_elev)
    raw = elev_grid.reshape(-1)
    is_cut = raw > best_elev
    px_inner = np.where(is_cut, cut_inner, inner)
    px_outer = np.where(is_cut, cut_outer, outer)
    w = np.clip((px_outer - best_d) / (px_outer - px_inner), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    out = w * (best_elev - drop) + (1 - w) * raw
    # hard clamp under the literal road footprint - see HARD_CLAMP_MARGIN_M's
    # comment. Reuses best_d/best_elev from the blend above rather than a
    # second nearest-station search.
    under_road = best_d <= HARD_CLAMP_RADIUS_M
    out = np.where(under_road, np.minimum(out, best_elev - HARD_CLAMP_MARGIN_M), out)
    return out.reshape(shape)


def downsample_min_grid(fine, n, factor):
    """Downsamples a (sample_n, sample_n) fine grid - sample_n = (n-1)*factor+1
    - to an (n, n) coarse grid by taking the MIN value within each output
    vertex's own footprint (half a fine-cell towards each neighbor, clamped
    at the tile edges). See FAR_SUPERSAMPLE's comment for why min, not a
    direct point-sample or an average, is required here."""
    sample_n = fine.shape[0]
    half = factor // 2
    bounds = []
    for i in range(n):
        c = i * factor
        lo = max(0, c - half)
        hi = min(sample_n, c + half + 1)
        bounds.append((lo, hi))
    out = np.empty((n, n), dtype=fine.dtype)
    for oi, (ylo, yhi) in enumerate(bounds):
        row_block = fine[ylo:yhi, :]
        for oj, (xlo, xhi) in enumerate(bounds):
            out[oi, oj] = row_block[:, xlo:xhi].min()
    return out


_MARTINI_CACHE = {}


def _martini_for(n):
    if n not in _MARTINI_CACHE:
        _MARTINI_CACHE[n] = Martini(n)
    return _MARTINI_CACHE[n]


def fix_up_winding(verts_cm, tris):
    """Flip any triangle whose UE-convention normal
    (normalize(cross(V2-V0, V1-V0))) points down. martini's own winding is
    unpredictable per-triangle (it's not building a regular quad grid), so
    unlike the uniform-grid path below (which gets this right by
    construction via a fixed (p, q, p+1) emission order) each adaptive
    triangle has to be checked individually. See that path's own comment
    for why this matters (2026-07-24 flat-terrain/no-grass bug)."""
    v0 = verts_cm[tris[:, 0]]
    v1 = verts_cm[tris[:, 1]]
    v2 = verts_cm[tris[:, 2]]
    normal_z = np.cross(v2 - v0, v1 - v0)[:, 2]
    flip = normal_z < 0
    out = tris.copy()
    out[flip, 1], out[flip, 2] = tris[flip, 2], tris[flip, 1]
    return out


def build_seam_skirt(gx_cm, gy_cm, z_cm, n, drop_cm=None):
    """Full-resolution (n-point) vertical curtain around all 4 edges of a
    near tile, dropped `drop_cm` below the TRUE boundary elevation (not
    whatever subset of it the adaptive mesh happened to keep).

    Why this exists: adaptive martini triangulation does not reliably pick
    the same sparse boundary vertex subset between two independently-built
    neighboring tiles, even when the underlying elevation data is
    bit-identical - measured 2026-07-28 (see MAX_ERROR_NEAR_M's comment):
    5-11 point symmetric differences out of ~60-150 boundary points, on
    tiles whose shared-edge elevation samples matched to the bit. So the
    straight-line interpolation between one tile's chosen boundary points
    can diverge from its neighbor's by up to MAX_ERROR_NEAR_M on each side -
    a real, if thin, crack. This skirt reproduces the true full-density
    elevation along the boundary and drops it well below that worst case,
    hiding the crack under solid geometry - the same "gap hidden under
    geometry" pattern road_mesh_common.py's own road skirt already uses for
    the road-vs-terrain seam. Also covers the NEAR/FAR tile boundary, which
    has an equivalent (pre-existing, not new) gap between the near tile's
    dense edge and the far tile's coarse one.
    """
    if drop_cm is None:
        drop_cm = SEAM_SKIRT_DROP_M * 100.0

    # One continuous perimeter walk, each corner visited once.
    idx = ([(i, 0) for i in range(n)]
           + [(n - 1, j) for j in range(1, n)]
           + [(i, n - 1) for i in range(n - 2, -1, -1)]
           + [(0, j) for j in range(n - 2, 0, -1)])
    ring_x = np.array([p[0] for p in idx])
    ring_y = np.array([p[1] for p in idx])

    top_x_cm = gx_cm[ring_y, ring_x]
    top_y_cm = gy_cm[ring_y, ring_x]
    top_z_cm = z_cm[ring_y, ring_x]
    bot_z_cm = top_z_cm - drop_cm

    m = len(idx)
    top_verts = np.stack([top_x_cm, top_y_cm, top_z_cm], axis=-1)
    bot_verts = np.stack([top_x_cm, top_y_cm, bot_z_cm], axis=-1)
    verts = np.concatenate([top_verts, bot_verts], axis=0).astype(np.float32)
    ring_uv = np.stack([ring_x / (n - 1), ring_y / (n - 1)], axis=-1)
    uvs = np.concatenate([ring_uv, ring_uv], axis=0).astype(np.float32)

    center_x_cm = float(gx_cm.min() + gx_cm.max()) / 2.0
    center_y_cm = float(gy_cm.min() + gy_cm.max()) / 2.0
    k = np.arange(m)
    k2 = (k + 1) % m
    t0, t1, b0, b1 = k, k2, m + k, m + k2
    quad_tris = np.concatenate([
        np.stack([t0, t1, b1], axis=-1),
        np.stack([t0, b1, b0], axis=-1),
    ], axis=0)

    v0p, v1p, v2p = verts[quad_tris[:, 0]], verts[quad_tris[:, 1]], verts[quad_tris[:, 2]]
    normal = np.cross(v2p - v0p, v1p - v0p)
    mid = (v0p + v1p + v2p) / 3.0
    outward_x = mid[:, 0] - center_x_cm
    outward_y = mid[:, 1] - center_y_cm
    flip = (normal[:, 0] * outward_x + normal[:, 1] * outward_y) < 0
    tris = quad_tris.copy()
    tris[flip, 1], tris[flip, 2] = quad_tris[flip, 2], quad_tris[flip, 1]

    return verts, uvs, tris.astype(np.uint32)


def build_tile(tx, ty, is_near, station_data, session, caches):
    """Builds one tile's regular-grid mesh. Returns a dict with has_collision
    plus (verts Px3 cm, uvs Px2, tris Tx3) - the single production function
    both main() (writes the ODT1 sidecar) and verify_terrain_fit.py (checks
    the actual built mesh, not a reimplementation) call into."""
    lat0, lon0, st_x_cm, st_y_cm, st_elev, st_exclude = station_data
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    tile_cm = TERRAIN_TILE_SIZE_CM
    near_cache, far_cache = caches

    n = NEAR_GRID_N if is_near else FAR_GRID_N
    zoom = NEAR_DEM_ZOOM if is_near else FAR_DEM_ZOOM
    cache = near_cache if is_near else far_cache

    x0_cm, x1_cm = tx * tile_cm, (tx + 1) * tile_cm
    y0_cm, y1_cm = ty * tile_cm, (ty + 1) * tile_cm

    # FAR tiles sample+carve at a much finer internal resolution than their
    # (n x n) output grid, then downsample by MIN - see FAR_SUPERSAMPLE's
    # comment. NEAR tiles are unaffected: sample_n == n, downsample is a
    # no-op reshape below.
    sample_n = n if is_near else (n - 1) * FAR_SUPERSAMPLE + 1
    xs_cm_fine = np.linspace(x0_cm, x1_cm, sample_n)
    ys_cm_fine = np.linspace(y0_cm, y1_cm, sample_n)
    gx_cm_fine, gy_cm_fine = np.meshgrid(xs_cm_fine, ys_cm_fine)   # row j = north, col i = east

    east_m = gx_cm_fine / 100.0
    north_m = -gy_cm_fine / 100.0
    lat = lat0 + north_m / M_PER_DEG_LAT
    lon = lon0 + east_m / m_per_deg_lon

    elev = sample_dem(lat.reshape(-1), lon.reshape(-1), zoom, session, cache).reshape(sample_n, sample_n)

    # prefilter stations to ones that could plausibly matter (cheap - exact
    # carve_grid_m call below is what actually matters for correctness, this
    # is purely a speed optimization for far tiles with thousands of
    # stations elsewhere on the route)
    prefilter_m = TERRAIN_CARVE_OUTER_M + 500.0
    # st_exclude = is_bridge OR an auto-detected elevation conflict (see
    # road_mesh_common.classify_wall_stations) - both mean "don't carve
    # toward this station, the ground here belongs to something else"
    # (either a real structure, or - for wall-eligible conflicts - a
    # generated retaining wall covers the gap instead)
    keep = (~st_exclude) & (
        np.hypot(np.clip(st_x_cm, x0_cm, x1_cm) - st_x_cm,
                 np.clip(st_y_cm, y0_cm, y1_cm) - st_y_cm) / 100.0 <= prefilter_m)
    if keep.any():
        elev = carve_grid_m(elev, east_m, north_m,
                             st_x_cm[keep] / 100.0, -st_y_cm[keep] / 100.0, st_elev[keep])

    if is_near:
        gx_cm, gy_cm = gx_cm_fine, gy_cm_fine
    else:
        gx_cm, gy_cm = np.meshgrid(np.linspace(x0_cm, x1_cm, n), np.linspace(y0_cm, y1_cm, n))
        elev = downsample_min_grid(elev, n, FAR_SUPERSAMPLE)

    z_cm = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M) * 100.0

    # Lake-bed depression - see this module's top-of-file comment next to
    # WATER_BED_MIN_DEPTH_M. Applied here (after road carve/downsample, on
    # the tile's FINAL grid) so it covers NEAR and FAR tiles identically,
    # before either triangulation branch below sees z_cm.
    z_cm = apply_water_depression(gx_cm, gy_cm, z_cm, _get_lake_beds())

    if is_near:
        # AUDIT.md item 1: adaptive martini triangulation instead of a
        # uniform grid - see NEAR_GRID_N/MAX_ERROR_NEAR_M's comments. `elev`
        # (meters, pre-UE-Z-shift) is what martini simplifies against;
        # road_ue_z_m is a pure additive shift (road_mesh_common.py) so an
        # error tolerance in DEM meters is identical to one in UE-Z meters.
        tile = _martini_for(n).create_tile(elev.astype(np.float32))
        grid_verts, martini_tris = tile.get_mesh(MAX_ERROR_NEAR_M)
        grid_verts = grid_verts.reshape(-1, 2).astype(np.int64)  # (x_idx, y_idx)
        gxi, gyi = grid_verts[:, 0], grid_verts[:, 1]

        verts = np.stack([gx_cm[gyi, gxi], gy_cm[gyi, gxi], z_cm[gyi, gxi]],
                          axis=-1).astype(np.float32)
        uvs = np.stack([gxi / (n - 1), gyi / (n - 1)], axis=-1).astype(np.float32)
        tris = fix_up_winding(verts, martini_tris.reshape(-1, 3).astype(np.uint32))

        skirt_verts, skirt_uvs, skirt_tris = build_seam_skirt(gx_cm, gy_cm, z_cm, n)
        base = len(verts)
        verts = np.concatenate([verts, skirt_verts], axis=0)
        uvs = np.concatenate([uvs, skirt_uvs], axis=0)
        tris = np.concatenate([tris, skirt_tris + base], axis=0).astype(np.uint32)
    else:
        # Uniform grid, unchanged - far tiles are already coarse/cheap and
        # have no collision, so adaptive triangulation isn't worth it here.
        verts = np.stack([gx_cm, gy_cm, z_cm], axis=-1).reshape(-1, 3).astype(np.float32)
        ii, jj = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 1, n))
        uvs = np.stack([ii, jj], axis=-1).reshape(-1, 2).astype(np.float32)

        # Winding must put the front face / computed vertex normal UP (+Z). UE's
        # triangle-normal convention (VectorUtil::Normal) is
        # normalize(cross(V2-V0, V1-V0)) - reversed vs. the usual right-handed
        # cross for its left-handed coordinate system - so a grid quad emitted as
        # (p, p+1, q) actually faces DOWN (fixed 2026-07-24: that inverted winding
        # made FMeshNormals::ComputeVertexNormals fill AODTerrainManager's normal
        # overlay pointing down, so the ground material's sun lighting hit the terrain
        # as a back-face and every tile read as flat blue sky-ambient - the "no
        # terrain / no grass" bug). Emitting (p, q, p+1) / (p+1, q, q+1) faces the
        # top up, matching the road pipeline's own convention (road tiles verify
        # 100% up-normals, terrain now does too).
        tris = []
        for j in range(n - 1):
            b = j * n
            for i in range(n - 1):
                p, q = b + i, b + i + n
                tris.append((p, q, p + 1))
                tris.append((p + 1, q, q + 1))
        tris = np.array(tris, dtype=np.uint32)

    return {"has_collision": bool(is_near), "verts": verts, "uvs": uvs, "tris": tris}


def write_tile_bin(path, tile, gen_id_bytes):
    verts, uvs, tris = tile["verts"], tile["uvs"], tile["tris"]
    with open(path, "wb") as f:
        f.write(b"ODT1")
        f.write(gen_id_bytes)
        f.write(struct.pack("<BII", 1 if tile["has_collision"] else 0,
                             len(verts), len(tris)))
        f.write(struct.pack(f"<{verts.size}f", *verts.reshape(-1)))
        f.write(struct.pack(f"<{uvs.size}f", *uvs.reshape(-1)))
        f.write(struct.pack(f"<{tris.size}I", *tris.reshape(-1)))


def background_bbox_tile_range(lat0, lon0):
    """Tile-index bounds (tx0, tx1, ty0, ty1) covering BACKGROUND_BBOX_SNWE,
    in the same local east/north-cm frame (relative to the road network's
    own origin_latlon) build_tile()'s gx_cm/gy_cm grid uses - NOT a fresh
    lat/lon->tile-index derivation of its own, so a background tile and a
    corridor tile with the same (tx, ty) are guaranteed to be the exact
    same physical square. Returns None if BACKGROUND_BBOX_SNWE isn't set
    for this area."""
    if BACKGROUND_BBOX_SNWE is None:
        return None
    s, w, n_, e = BACKGROUND_BBOX_SNWE
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    tile_cm = TERRAIN_TILE_SIZE_CM
    # same north_m/east_m -> gy_cm/gx_cm sign convention as build_tile():
    # gy_cm = -north_m * 100, gx_cm = east_m * 100
    x_cm = [(lon - lon0) * m_per_deg_lon * 100.0 for lon in (w, e)]
    y_cm = [-(lat - lat0) * M_PER_DEG_LAT * 100.0 for lat in (s, n_)]
    tx0, tx1 = int(min(x_cm) // tile_cm), int(max(x_cm) // tile_cm)
    ty0, ty1 = int(min(y_cm) // tile_cm), int(max(y_cm) // tile_cm)
    return tx0, tx1, ty0, ty1


def classify_tiles(station_data):
    """Every tile key within RENDER_DISTANCE_M of the route, tagged
    near/far, PLUS (if BACKGROUND_BBOX_SNWE is set for this area) every
    tile key covering that wider bbox, added as far/no-collision unless
    already claimed as near by the road-distance pass above - a
    background tile never downgrades a driveable one."""
    lat0, lon0, st_x_cm, st_y_cm, _, _ = station_data
    tile_cm = TERRAIN_TILE_SIZE_CM
    pad = RENDER_DISTANCE_M * 100.0
    tx0 = int((st_x_cm.min() - pad) // tile_cm)
    tx1 = int((st_x_cm.max() + pad) // tile_cm)
    ty0 = int((st_y_cm.min() - pad) // tile_cm)
    ty1 = int((st_y_cm.max() + pad) // tile_cm)

    tiles = {}
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            d = tile_min_dist_m(st_x_cm, st_y_cm, tx, ty, tile_cm)
            if d <= NEAR_MARGIN_M:
                tiles[(tx, ty)] = True
            elif d <= RENDER_DISTANCE_M:
                tiles[(tx, ty)] = False

    bg_range = background_bbox_tile_range(lat0, lon0)
    if bg_range is not None:
        bx0, bx1, by0, by1 = bg_range
        n_added = 0
        for ty in range(by0, by1 + 1):
            for tx in range(bx0, bx1 + 1):
                if (tx, ty) not in tiles:
                    tiles[(tx, ty)] = False
                    n_added += 1
        print(f"  +{n_added} background-visuals far tile(s) from BACKGROUND_BBOX_SNWE")
    return tiles


def main():
    # 2026-09-03: --lakes-only restricts the rebake to tiles that actually
    # touch a baked lake bbox (padded by the water-depression falloff/blend
    # distances) - a full summit_county rebake is ~55 min, but the water-
    # depression fix only changes geometry within ~52m of a shoreline, so
    # every other tile is byte-identical and not worth re-baking. Tiles NOT
    # in this filtered set are left untouched on disk (delete_stale_tiles is
    # skipped in this mode - it would otherwise treat every un-rebuilt tile
    # as stale and delete it).
    lakes_only = "--lakes-only" in sys.argv

    lat0, lon0, east, north, elev, exclude, _wall_pairs_unused = load_stations()
    st_x_cm, st_y_cm = east * 100.0, -north * 100.0
    station_data = (lat0, lon0, st_x_cm, st_y_cm, elev, exclude)

    tiles = classify_tiles(station_data)

    if lakes_only:
        lakes = _get_lake_beds()
        pad_cm = (WATER_BED_FALLOFF_M + WATER_SHORE_BLEND_M) * 100.0
        tile_cm = TERRAIN_TILE_SIZE_CM
        keep = {}
        for (tx, ty), is_near in tiles.items():
            tx0, tx1 = tx * tile_cm, (tx + 1) * tile_cm
            ty0, ty1 = ty * tile_cm, (ty + 1) * tile_cm
            for lake in lakes:
                bx0, bx1, by0, by1 = lake["bbox"]
                if not (bx1 + pad_cm < tx0 or bx0 - pad_cm > tx1 or
                        by1 + pad_cm < ty0 or by0 - pad_cm > ty1):
                    keep[(tx, ty)] = is_near
                    break
        print(f"--lakes-only: {len(keep)}/{len(tiles)} tile(s) touch a baked lake bbox "
              f"(+{WATER_BED_FALLOFF_M + WATER_SHORE_BLEND_M:.0f}m pad)")
        tiles = keep

    n_near = sum(1 for v in tiles.values() if v)
    n_far = len(tiles) - n_near
    print(f"{len(tiles)} tile(s) to build ({n_near} near/collision, {n_far} far/no-collision)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gen_id = compute_generation_id()
    gen_id_bytes = pack_generation_id(gen_id)
    print(f"generation id: {gen_id}")

    session = requests.Session()
    caches = ({}, {})   # (near_cache, far_cache), keyed (zoom, tx, ty)
    total_verts = total_tris = 0
    written_paths = []
    written_coarse_paths = []
    n_coarse_fallback = 0
    for i, ((tx, ty), is_near) in enumerate(tiles.items()):
        tile = build_tile(tx, ty, is_near, station_data, session, caches)
        out_path = OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
        write_tile_bin(out_path, tile, gen_id_bytes)
        written_paths.append(out_path)
        total_verts += len(tile["verts"])
        total_tris += len(tile["tris"])

        # Runtime dynamic-LOD streaming (2026-08-25, Arc 7 rescope): near
        # tiles are the only ones with full-res+collision geometry, but
        # AODTerrainManager needs something cheap to show *before* the
        # player is actually close enough to warrant that cost. Bake the
        # same coarse/no-collision variant a plain far tile would get,
        # under a distinct prefix so both can live on disk at once - see
        # AODTerrainManager::RequestTileLoad.
        if is_near:
            coarse_tile = build_tile(tx, ty, False, station_data, session, caches)
            coarse_path = OUT_DIR / f"terrain_coarse_{AREA_NAME}_test_tile_{tx}_{ty}.bin"
            write_tile_bin(coarse_path, coarse_tile, gen_id_bytes)
            written_coarse_paths.append(coarse_path)
            n_coarse_fallback += 1

        if (i + 1) % 10 == 0 or i + 1 == len(tiles):
            print(f"  {i + 1}/{len(tiles)} tile(s) built")

    if lakes_only:
        print(f"--lakes-only: skipping delete_stale_tiles (only a subset of tiles was rebuilt, "
              f"the rest are intentionally left as-is)")
    else:
        n_stale = delete_stale_tiles(OUT_DIR, f"terrain_{AREA_NAME}_test_tile_*.bin", written_paths)
        print(f"deleted {n_stale} stale terrain tile(s) from a previous run")
        n_stale_coarse = delete_stale_tiles(
            OUT_DIR, f"terrain_coarse_{AREA_NAME}_test_tile_*.bin", written_coarse_paths)
        print(f"deleted {n_stale_coarse} stale coarse-fallback tile(s) from a previous run")
    print(f"Built {len(tiles)} terrain tile(s) ({n_coarse_fallback} with a coarse fallback), "
          f"{total_verts} verts, {total_tris} tris -> {OUT_DIR}")


if __name__ == "__main__":
    main()
