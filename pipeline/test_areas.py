"""Shared test-area config (Arc 7 open-world work) so the bbox/name used by the
terrain pipeline, OSM road-network fetch, and road-mesh build never drift
apart - they did once before Oahu even existed (Green Valley -> Palmdale
origin-height mixup).

Refactored 2026-09-02 (after summit_county became the 4th
comment-out-the-old-area constant swap): every area now lives in the AREAS
table below and ACTIVE_AREA is the one switch. The flat module-level names
(AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, ...) are still THE interface -
every pipeline stage imports those, unchanged - they're just derived from
the active entry now instead of hand-edited in place.

To add an area:
  1. Sample its real origin height (z12 Terrarium bilinear read at the bbox
     center - never guess/copy; the Palmdale mixup above was exactly that).
  2. Grid-sample its real elevation span and pad generously for
     elev_plausible_m (do NOT reuse another area's range - the old shared
     1300 m ceiling would have silently clipped any real mountain).
  3. Add the entry, flip ACTIVE_AREA, bake via rebuild_area.py.
"""
import os

from world_frame import WORLD_DATUM_M, WORLDS, lcc_forward, lcc_inverse

# (2026-10-05, step 3) gv_west/gv_east boundary, UE X cm in the california
# frame: the 2 km terrain-tile edge nearest -118.45 at 34.535 N (218 m west
# of it). tests/test_world_manifest.py re-derives it.
_GV_CUT_X_CM = 14200000.0

AREAS = {
    # Skagit Valley / North Cascades foothills, WA (2026-07-20 - 2026-07-26)
    # - the known-good reference config the whole open-world pipeline was
    # proven against before Oahu. Predates per-area elevation ranges and the
    # background bbox; its elev range here is an estimate padded above the
    # foothill peaks near the bbox's east edge - resample before any rebake.
    "skagit_cascades": dict(
        bbox_snwe=(48.000, -122.400, 48.550, -121.500),
        background_bbox_snwe=None,
        origin_height_m=60.0,
        elev_plausible_m=(-50.0, 2500.0),
        max_building_levels=8,
    ),

    # Oahu, HI (2026-07-27) - a real island (Taylor's explicit ask, "build
    # the entire island"): coastline on every side, Honolulu's actual dense
    # urban grid + real high-rise downtown, dramatic Koolau/Waianae relief,
    # and the first real exercise of the biome table's tropical/coastal
    # entries. Bbox padded slightly beyond the coastline so imagery/terrain
    # fetches don't clip right at the shore. The reliability-audit rebuild
    # (PLAN-RELIABILITY.md) was proven against this config. Origin 306.0 m
    # lands on the Wahiawa plateau between the ranges (real land, despite
    # the bbox spanning coast to coast). Real elevation span: sea level to
    # Ka'ala's 1220 m.
    "oahu": dict(
        bbox_snwe=(21.24, -158.30, 21.72, -157.65),
        background_bbox_snwe=None,
        origin_height_m=306.0,
        elev_plausible_m=(-50.0, 1300.0),
        max_building_levels=40,   # real Honolulu high-rise downtown - keep the tall cap
    ),

    # Green Valley Rd / Angeles National Forest, near Palmdale, CA
    # (2026-08-22, expanded back to the full loop 2026-08-26 per Taylor):
    # Sierra Pelona ridge and the Green Valley / Bouquet Canyon /
    # San Francisquito Canyon backroad network northwest of Santa Clarita,
    # plus Hwy 14 down toward Santa Clarita (chaparral biome appears around
    # Hwy 14 per the mid-latitude classification fix). This is the area the
    # module docstring's "Green Valley -> Palmdale" origin-height mixup
    # refers to - re-added with a real sampled origin (879.4 m at the bbox
    # center, Sierra Pelona foothills) and its own elevation range
    # (12x12 grid sampled 317..1567 m, padded). Background bbox padded
    # ~0.1 deg so the horizon ring has backdrop beyond the loop itself.
    # (History: 2026-08-22 it was temporarily shrunk to a tight corridor,
    # bbox (34.505, -118.51, 34.725, -118.34), origin 829.9 m, during a
    # sustained Overpass bad patch; expanded back once that cleared.)
    "green_valley": dict(
        bbox_snwe=(34.35, -118.62, 34.72, -118.28),
        background_bbox_snwe=(34.25, -118.72, 34.82, -118.18),
        origin_height_m=879.4,
        elev_plausible_m=(0.0, 2200.0),
        max_building_levels=8,
    ),

    # Summit County / Vail, CO (2026-09-01) - Taylor's ask: "the area around
    # lake frisco with breck, vail, keystone, etc". First genuinely
    # high-altitude zone (valley floors ~2700 m, passes ~3500 m, Quandary
    # Peak 4348 m inside the bbox), first real US Rockies entry from the
    # Arc 7 hot-zone list, and the first area where build_water_tiles.py
    # covers a major reservoir (Dillon). Spans Hoosier Pass north to
    # Silverthorne/Loveland Pass and west past Vail/Minturn: Frisco, Dillon
    # Reservoir, Breckenridge, Keystone, A-Basin, Loveland Pass (US-6),
    # Copper Mountain, Vail Pass (I-70), Vail, Minturn (US-24).
    # Origin 3435.3 m (bbox center, Tenmile/Gore high country near Copper
    # Mountain) - first area whose ORIGIN is above every prior area's PEAK;
    # nothing downstream should care, but if anything renders ~2.5 km off
    # vs green_valley expectations, check this value's consumers first.
    # Elev range: 12x12 grid read 2364..4053 m; ceiling padded to cover
    # Quandary, floor padded below the Eagle valley's minimum.
    # Background bbox padded ~0.1 deg - the Gore/Tenmile ranges and the
    # Continental Divide ridgeline make the horizon backdrop matter more
    # here than anywhere baked so far.
    "summit_county": dict(
        bbox_snwe=(39.35, -106.50, 39.70, -105.85),
        background_bbox_snwe=(39.25, -106.60, 39.80, -105.75),
        origin_height_m=3435.3,
        elev_plausible_m=(1800.0, 4600.0),
        # 6 stories max (2026-09-03 skyscraper-canyon fix, see
        # build_building_mesh.py) - a real small mountain resort town tops
        # out at a handful of lodge/condo blocks, never a genuine downtown
        # high-rise, no matter what classify_settlements.py's density-based
        # "City" archetype call comes out to for a given tile.
        max_building_levels=6,
    ),

    # Russian Hill, San Francisco (2026-10-02) - Taylor's junction-pad test:
    # steep parallel streets that flatten at the cross streets. 2x2 blocks
    # (Union/Filbert/Greenwich x Hyde/Leavenworth/Jones) plus ~50 m of
    # approach; Filbert drops ~16% over the Hyde->Leavenworth block. Origin
    # 70.4 m (z12 at bbox center); z15 12x12 grid read 22..98 m, padded.
    # Bake with OD_AREA=sf_russian_hill.
    # (2026-10-05) Extended north down Hyde St to Beach St / Aquatic Park
    # (the garage-menu spot, looking out over the bay) and west to keep
    # Hyde's north end inside. Background bbox reaches Alcatraz, Angel
    # Island, the Golden Gate and the Marin Headlands / Sausalito. Origin
    # 55.0 m (z12 at the new bbox center); z15 12x12 grid read -3..98 m, so
    # the range still holds (it only clamps road stations; the background's
    # -110..317 m bay floor / Marin never reaches it).
    "sf_russian_hill": dict(
        bbox_snwe=(37.7988, -122.4225, 37.8080, -122.4154),
        background_bbox_snwe=(37.79, -122.50, 37.87, -122.40),
        origin_height_m=55.0,
        elev_plausible_m=(-20.0, 250.0),
        max_building_levels=12,
    ),

    # Deals Gap, US 129 "Tail of the Dragon" (2026-10-09) - Taylor's
    # side-by-side with the old hand-built /Game/TailOfTheDragon stage
    # (build_stage.py, own equirect frame). Same road, Deals Gap (35.466,
    # -83.919) to the Tabcat Creek bridge (35.530, -83.994), now on the area
    # pipeline. Origin 445.1 m (z12 at bbox centre); z12 16x16 grid read
    # 265..1006 m, padded (Calderwood/Cheoah reservoirs below, Gregory Bald
    # ridge to the east). Bake with OD_AREA=deals_gap.
    "deals_gap": dict(
        bbox_snwe=(35.455, -84.005, 35.540, -83.905),
        background_bbox_snwe=(35.38, -84.10, 35.60, -83.83),
        origin_height_m=445.1,
        elev_plausible_m=(150.0, 1300.0),
        max_building_levels=3,
    ),

    # Gasquet, US 199 along the Smith River (2026-10-02) - the far-corner
    # physics test for a California-wide world. Drive it and compare
    # handling with summit_county. Origin 116.5 m (z12 at bbox centre); z15
    # 12x12 grid read 94..583 m, padded. Bake with OD_AREA=far_gasquet.
    # (2026-10-05) Now a real member of the "california" world: its frame
    # is the state-wide LCC, so it sits ~613 km from the world origin
    # (east -330 km, north +517 km), where it really is. Was a fake origin
    # 7.8 deg east at the same latitude (~646 km) under the old equirect.
    # (2026-10-05, step 2) Its Z datum is the world's (sea level), not the
    # 116.5 m origin - world members carry no origin_height_m.
    "far_gasquet": dict(
        bbox_snwe=(41.835, -123.985, 41.860, -123.945),
        background_bbox_snwe=None,
        world="california",
        elev_plausible_m=(0.0, 900.0),
        max_building_levels=4,
    ),
    # green_valley split at -118.45 into two adjacent "california" members
    # (2026-10-05, step 2: the first streamed pair). The meridian crosses
    # CA-14, Sierra Hwy, Soledad/Bouquet/San Francisquito/Vasquez/Placerita
    # Canyon Rds and Elizabeth Lake Rd. Elev range and building cap are
    # green_valley's. green_valley itself stays as it was.
    # (2026-10-05, step 3) Each half owns the tiles on its side of
    # _GV_CUT_X_CM and bakes a halo 3.0-3.8 km past it (bbox edge -118.415 /
    # -118.489), so roads and junctions at the cut have context;
    # prune_unowned_tiles.py drops the halo's tiles.
    "gv_west": dict(
        bbox_snwe=(34.35, -118.62, 34.72, -118.415),
        own_rect_cm=(None, None, _GV_CUT_X_CM, None),
        background_bbox_snwe=None,
        world="california",
        elev_plausible_m=(0.0, 2200.0),
        max_building_levels=8,
    ),
    "gv_east": dict(
        bbox_snwe=(34.35, -118.489, 34.72, -118.28),
        own_rect_cm=(_GV_CUT_X_CM, None, None, None),
        background_bbox_snwe=None,
        world="california",
        elev_plausible_m=(0.0, 2200.0),
        max_building_levels=8,
    ),

    # far_gasquet's control: same bbox in its own centred frame, so its
    # tiles diff against far_gasquet's to isolate large-coordinate effects
    # (expect an offset plus ~2.4 deg of meridian convergence between them).
    "near_gasquet": dict(
        bbox_snwe=(41.835, -123.985, 41.860, -123.945),
        background_bbox_snwe=None,
        origin_height_m=116.5,
        elev_plausible_m=(0.0, 900.0),
        max_building_levels=4,
    ),
}

# The one switch. Everything below derives from this entry.
# OD_AREA env var overrides it for a one-off bake of another area without
# editing this file (2026-10-02: SF pad test next to summit_county).
ACTIVE_AREA = os.environ.get("OD_AREA", "summit_county")

# ---------------------------------------------------------------------------
# Flat exports - the stable interface every pipeline/unreal stage imports.
# ---------------------------------------------------------------------------

_ACTIVE = AREAS[ACTIVE_AREA]

AREA_NAME = ACTIVE_AREA

# (south, west, north, east)
BBOX_SNWE = _ACTIVE["bbox_snwe"]

# Local east/north frame (2026-10-05, replaces ORIGIN_LATLON + the
# per-stage equirectangular math): every east/north metre in the pipeline
# and every UE cm is an ellipsoidal Lambert Conformal Conic coordinate
# (world_frame.py). An area with world="california" uses that world's LCC
# with the local origin at the world origin; any other area gets its own LCC
# centred on itself (standard parallels at the bbox south/north edges,
# origin at the bbox centre). The old equirect used 111132 m/deg on both
# axes, so it ran ~0.3% short east-west (at Gasquet); this is ellipsoid-true.
# Meridians converge under LCC, so a lon/lat raster can't be mapped onto
# this grid axis-aligned - sample it per point through to_lonlat().
_S, _W, _N, _E = BBOX_SNWE
FRAME_LCC = (WORLDS[_ACTIVE["world"]] if "world" in _ACTIVE
             else (_S, _N, (_S + _N) / 2, (_W + _E) / 2))


def to_local(lon, lat):
    """lon/lat degrees -> (east_m, north_m) in the active frame. Vectorised."""
    return lcc_forward(lon, lat, *FRAME_LCC)


def to_lonlat(east_m, north_m):
    """(east_m, north_m) -> (lon, lat) degrees. Vectorised."""
    return lcc_inverse(east_m, north_m, *FRAME_LCC)


def _edges(x0, y0, x1, y1, k=65):
    import numpy as np   # not at module level: unreal/ scripts import this file
    t = np.linspace(0.0, 1.0, k)
    return (np.concatenate([x0 + (x1 - x0) * t, np.full(k, x1), x1 - (x1 - x0) * t, np.full(k, x0)]),
            np.concatenate([np.full(k, y0), y0 + (y1 - y0) * t, np.full(k, y1), y1 - (y1 - y0) * t]))


def local_rect(snwe):
    """Local (east_min, north_min, east_max, north_max) m covering a lon/lat
    bbox - from its projected edges (parallels map to arcs), not corners."""
    lon, lat = _edges(snwe[1], snwe[0], snwe[3], snwe[2])
    e, n = to_local(lon, lat)
    return float(e.min()), float(n.min()), float(e.max()), float(n.max())


def lonlat_bbox(east_min, north_min, east_max, north_max):
    """(south, west, north, east) covering a local-frame rectangle - from
    its inverse-projected edges, not a linear metres->degrees conversion."""
    lon, lat = to_lonlat(*_edges(east_min, north_min, east_max, north_max))
    return float(lat.min()), float(lon.min()), float(lat.max()), float(lon.max())


# Background-visuals bbox: wider than BBOX_SNWE, used ONLY by
# build_terrain_mesh_tiles.py (raw-DEM far tiles, no road carving, no
# collision - the 6 km horizon-ring code path extended to cover this whole
# area) and build_map_texture.py (imagery for the in-game map background) -
# NOT by the OSM road/place/building fetch, which stays scoped to BBOX_SNWE
# so corridor fidelity is unaffected and Overpass never sees a query sized
# for this. Consumers getattr() it and treat None as "no background ring".
BACKGROUND_BBOX_SNWE = _ACTIVE["background_bbox_snwe"]

# fetch_osm_network.py grabs every drivable road in the bbox. Kept at None
# (no ref filter, ramps/cross streets included) per Taylor's 2026-07-25
# realism preference - carried through every area since.
ROAD_REF_REGEX = None

# Sampled ground elevation at the origin point - bilinear-read from a fetched
# Terrarium tile at the origin lon/lat (z12, one-off query), never guessed or
# hand-copied. UE world Z = (absolute_elevation_m - ORIGIN_HEIGHT_M) * 100 at
# the origin lon/lat - any pipeline step writing absolute DEM-sourced
# elevations into UE-frame cm (currently: build_road_network.py) MUST
# subtract this first, or geometry floats ~ORIGIN_HEIGHT_M metres above the
# rendered terrain (the Palmdale ~800 m offset bug, caught by -odseam).
#
# (2026-10-05) A world member uses its world's shared datum instead
# (world_frame.WORLD_DATUM_M, sea level), so neighbouring areas meet in Z.
ORIGIN_HEIGHT_M = (WORLD_DATUM_M[_ACTIVE["world"]] if "world" in _ACTIVE
                   else _ACTIVE["origin_height_m"])

# (2026-10-05, step 3) The UE-cm rect (xmin, ymin, xmax, ymax) whose tiles
# this area ships, None = open end; edges on the 2 km terrain grid (500 m
# road tiles nest). Its bbox bakes past it (a halo); prune_unowned_tiles.py
# deletes the rest. None = owns everything it bakes.
OWN_RECT_CM = _ACTIVE.get("own_rect_cm")


def owns(x_cm, y_cm):
    """True where UE (x_cm, y_cm) is inside OWN_RECT_CM (half-open). A tile
    is owned iff its min corner is. Vectorised."""
    import numpy as np
    x, y = np.asarray(x_cm, dtype=float), np.asarray(y_cm, dtype=float)
    ok = np.ones(np.broadcast(x, y).shape, dtype=bool)
    if OWN_RECT_CM:
        x0, y0, x1, y1 = OWN_RECT_CM
        for v, lo, hi in ((x, x0, x1), (y, y0, y1)):
            if lo is not None:
                ok &= v >= lo
            if hi is not None:
                ok &= v < hi
    return ok


# Hard per-area sanity cap on building story count (build_building_mesh.py),
# applied to EVERY level count that ends up data-driven - a real OSM
# building:levels/height tag, and the per-building-type/archetype fallback
# defaults alike. Added 2026-09-03: Frisco/Breckenridge (summit_county) were
# extruding as a dense high-rise canyon because classify_settlements.py's
# density-based "City" archetype (tuned against real Honolulu vs Kailua OSM
# tagging patterns) fired on ordinary mountain-town downtown blocks - this
# region's OSM data tags the overwhelming majority of buildings (>85% in
# every affected tile, residential houses/garages/sheds included) with the
# generic untyped `building=yes`, so the archetype's "few residential-tagged
# => real downtown" signal is meaningless here and can't be trusted at all.
# Per-building-type defaulting (see build_building_mesh.py) fixes most of
# it directly; this cap is the backstop for whatever still slips through
# (an untyped "yes" building still falls back to the archetype's range, and
# a bogus/outlier real OSM tag stays possible) - same
# defense-in-depth pattern ELEV_PLAUSIBLE_M below already uses for elevation.
MAX_BUILDING_LEVELS = _ACTIVE["max_building_levels"]

# Arc 7.2 slice 2: world-space grid size (cm) road-network chunks are
# bucketed into for tile streaming (export_road_mesh_bin.py). MUST match
# AODRoadManager::TileSizeCm's default in ODRoadManager.h - a mismatch
# doesn't crash, the runtime just probes filenames that don't exist and
# silently loads no road tiles.
#
# Shrunk from 200000 (2km) 2026-08-25 - the open Chaos/UE5.8 collision-
# registration bug (junction pads unreachable by a ground-snap trace) was
# bisected to a cumulative resident-chunk-count threshold (~111 chunks pass,
# ~144 fail at a real dense junction cluster), and AODRoadManager::Tick's
# ring-load math guarantees AT LEAST a 3x3-tile neighborhood resident no
# matter how small SpawnRadius is - at 2 km tiles that guaranteed minimum
# already exceeded the failure threshold at some real junction clusters. A
# finer grid shrinks the guaranteed-minimum footprint instead - decoupled
# from TERRAIN_TILE_SIZE_CM on purpose (road-chunk-specific bug; terrain
# didn't need rebaking).
ROAD_TILE_SIZE_CM = 50000.0   # 500 m

# Arc 7.0 pivot (2026-07-23): world-space grid size (cm) baked terrain mesh
# tiles are bucketed into (build_terrain_mesh_tiles.py). MUST match
# AODTerrainManager::TileSizeCm's default in ODTerrainManager.h, same
# mismatch caveat as above. Every other tiled stage (barrier/building/biome/
# forest) keys off this one, so it stays at the original size deliberately.
TERRAIN_TILE_SIZE_CM = 200000.0   # 2 km

# Absolute plausibility floor/ceiling (m) for raw DEM elevation samples, per
# area (PLAN-RELIABILITY.md Phase 3 item 9: one shared constant used to
# silently clip any real road elevation above 1300 m - harmless for Oahu,
# data corruption for real mountains). clamp_plausible_elevation() uses this
# as a backstop, not a fix: a clipped station is still WRONG, just boundedly
# wrong - STRUCTURE_GRADE_WARN downstream flags clipped runs for a human.
# Exported as the historical name->range dict for any consumer that wants
# another area's range; the active area's bounds are the _MIN/_MAX pair.
ELEV_PLAUSIBLE_M = {
    name: cfg["elev_plausible_m"] for name, cfg in AREAS.items()
}
ELEV_PLAUSIBLE_MIN_M, ELEV_PLAUSIBLE_MAX_M = ELEV_PLAUSIBLE_M[AREA_NAME]
