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
}

# The one switch. Everything below derives from this entry.
ACTIVE_AREA = "summit_county"

# ---------------------------------------------------------------------------
# Flat exports - the stable interface every pipeline/unreal stage imports.
# ---------------------------------------------------------------------------

_ACTIVE = AREAS[ACTIVE_AREA]

AREA_NAME = ACTIVE_AREA

# (south, west, north, east)
BBOX_SNWE = _ACTIVE["bbox_snwe"]

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
ORIGIN_HEIGHT_M = _ACTIVE["origin_height_m"]

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
