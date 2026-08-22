"""Shared test-area config (Arc 7 open-world work) so the bbox/name used by the
terrain pipeline, OSM road-network fetch, and road-mesh build never drift
apart - they did once before Oahu even existed (Green Valley -> Palmdale
origin-height mixup).
"""

# Skagit Valley / North Cascades foothills, WA (2026-07-20 - 2026-07-26 test
# area; kept here commented out, not deleted, as the known-good reference
# config the whole open-world pipeline was proven against before Oahu).
# AREA_NAME = "skagit_cascades"
# BBOX_SNWE = (48.000, -122.400, 48.550, -121.500)   # south, west, north, east
# ORIGIN_HEIGHT_M = 60.0

# Oahu, HI (2026-07-27) - a real island (Taylor's explicit ask, "build the
# entire island"), coastline on every side rather than the Skagit loop's
# inland rural corridor, Honolulu's actual dense urban grid + real
# high-rise downtown, dramatic Koʻolau/Waiʻanae relief, and the first real
# exercise of the 14-archetype biome table's tropical/coastal entries
# (Rainforest, Savanna, CoastalDune) - everything tested before this was
# temperate Pacific NW. Bbox padded slightly beyond the island's own
# coastline so imagery/terrain fetches don't clip right at the shore.
# Whole island, not a scoped loop - Taylor's call given this was meant to
# surface a broad investigation list, not a clean single feature. Kept
# here (commented out below Green Valley, not deleted) as the known-good
# reference config the reliability-audit rebuild (PLAN-RELIABILITY.md) was
# proven against.
# AREA_NAME = "oahu"
# BBOX_SNWE = (21.24, -158.30, 21.72, -157.65)   # south, west, north, east
# ORIGIN_HEIGHT_M = 306.0

# Green Valley Rd / Angeles National Forest, near Palmdale, CA (2026-08-22)
# - the first non-Oahu, genuinely mountainous testbed: Sierra Pelona ridge
# and the Green Valley / Bouquet Canyon / San Francisquito Canyon backroad
# network northwest of Santa Clarita, LA County. This is the area the
# file's own top-of-file "Green Valley -> Palmdale" origin-height mixup
# warning refers to from before Oahu even existed - re-added carefully
# this time with a real sampled origin and a per-area elevation range
# (ELEV_PLAUSIBLE_M below), not a guess or a copy of another area's
# constant.
#
# TEMPORARILY shrunk to a tight corridor around Green Valley Rd itself
# (2026-08-22) - Overpass API had a sustained bad patch (5 straight
# failures across both mirrors, mix of 504/502/429/500, at different
# pipeline stages each time, over ~45 min with increasing backoff) while
# fetching the originally-intended whole-local-area bbox. Testing whether
# a smaller query succeeds where the larger one didn't before deciding
# whether to expand back. Whole-local-area bbox this replaced:
# BBOX_SNWE = (34.35, -118.62, 34.72, -118.28)
AREA_NAME = "green_valley"
# south, west, north, east - tight corridor around Green Valley Rd/the
# Green Valley community and Lake Hughes, not the full Sierra Pelona area.
BBOX_SNWE = (34.55, -118.50, 34.68, -118.35)

# Background-visuals bbox (2026-08-22): wider than BBOX_SNWE, used ONLY by
# build_terrain_mesh_tiles.py (raw-DEM far tiles, no road carving, no
# collision - same code path that already renders the 6 km horizon ring
# around the corridor, just extended to cover this whole area instead) and
# build_map_texture.py (imagery fetch for the in-game map background) -
# NOT by the OSM road/place/building fetch, which stays scoped to
# BBOX_SNWE above so the corridor's road/building fidelity is unaffected
# and Overpass never sees a query sized for this bbox. This is the
# original whole-local-area bbox BBOX_SNWE was shrunk from (see history
# above) - Sierra Pelona ridge, Bouquet Canyon Reservoir, Elizabeth
# Lake/Lake Hughes. Leave unset (None) for an area that doesn't need a
# wider visual backdrop than its own corridor.
BACKGROUND_BBOX_SNWE = (34.35, -118.62, 34.72, -118.28)

# Fetch_osm_network.py grabs every drivable road in the bbox. Kept at None
# (no ref filter, every drivable road including ramps/cross streets) per
# Taylor's 2026-07-25 realism preference for the Skagit loop - carried
# through Oahu (Honolulu's actual street grid, not just the island's
# highway loop) and Green Valley (every canyon/forest road in the bbox,
# not just Green Valley Rd itself).
ROAD_REF_REGEX = None

# Sampled ground elevation at the origin point - bilinear-read directly from
# a fetched Terrarium tile at the origin lon/lat (z12, one-off interactive
# query), not hand-copied from a map/guess. A prior session's Palmdale
# value being off caused an ~800 m road/terrain offset bug, caught via
# AODRoadManager's -odseam check - exactness matters less than every
# consumer using the SAME value consistently, but starting from a real
# sample instead of a guess avoids that risk entirely.
# The old georeference actor used origin_placement=CARTOGRAPHIC_ORIGIN with
# this as origin_height, which made UE world Z = (absolute_elevation_m -
# ORIGIN_HEIGHT_M) * 100 at the origin lon/lat - any pipeline step that
# writes absolute DEM-sourced elevations into UE-frame cm coordinates for
# this level (currently: build_road_network.py) MUST subtract this first,
# or geometry floats ~ORIGIN_HEIGHT_M metres above where the terrain
# actually renders.
#
# Oahu (2026-07-27): bbox center (21.485, -157.975) sampled 306.0 m -
# lands on the Wahiawa plateau/central saddle between the Koʻolau and
# Waiʻanae ranges (real land, not open ocean, despite the bbox spanning
# coast to coast).
# Green Valley (2026-08-22): tight-corridor bbox center (34.615, -118.425)
# sampled 829.9 m - lands near the Green Valley community itself. (The
# original whole-local-area bbox center (34.535, -118.45) sampled 879.4 m,
# in the Sierra Pelona foothills between Bouquet Canyon and San
# Francisquito Canyon - kept here in case the bbox expands back.)
ORIGIN_HEIGHT_M = 829.9

# Arc 7.2 slice 2: world-space grid size (cm) road-network chunks are
# bucketed into for tile streaming (export_road_mesh_bin.py). MUST match
# AODRoadManager::TileSizeCm's default in ODRoadManager.h - a mismatch here
# doesn't crash anything (the runtime just probes filenames that don't
# exist), it just silently loads no road tiles.
ROAD_TILE_SIZE_CM = 200000.0   # 2 km

# Arc 7.0 pivot (2026-07-23): world-space grid size (cm) baked terrain mesh
# tiles are bucketed into (pipeline/build_terrain_mesh_tiles.py). MUST match
# AODTerrainManager::TileSizeCm's default in ODTerrainManager.h, same
# mismatch caveat as ROAD_TILE_SIZE_CM above. Same size as the road tile
# grid for now (no requirement that they match, just simplest to reason
# about while both systems are new).
TERRAIN_TILE_SIZE_CM = 200000.0   # 2 km

# Absolute plausibility floor/ceiling (m) for raw DEM elevation samples, per
# area. Moved here from pipeline/road_mesh_common.py 2026-08-22
# (PLAN-RELIABILITY.md Phase 3 item 9, HARD DEADLINE before the first
# non-Oahu/non-mountain-zone bake): it used to be one constant shared by
# every area, which silently clipped any real road elevation above 1300 m -
# harmless for Oahu (real span is sea level to Ka'ala's 1220 m) but would
# have corrupted data for any future area with real mountains outside
# Oahu's range.
#
# Original rationale (unchanged): not every Terrarium decode failure is an
# isolated single-pixel spike that despike_ground() can catch by "bouncing
# back" - a corrupt/mismatched source tile can read consistently wrong over
# many consecutive stations, with nothing to bounce back to. clamp_
# plausible_elevation() uses this as a backstop, not a real fix: it clips
# the impossible value to the nearest plausible bound so nothing
# catastrophic (falling through the world) reaches the game, but a clipped
# station is still WRONG, just boundedly wrong - the STRUCTURE_GRADE_WARN
# sanity check downstream is what flags a clipped run for a human to go
# look at the source tile.
#
# area_name -> (min_m, max_m). Add a new area's own range here before
# baking it - do NOT reuse Oahu's range for a mountainous area.
#
# Green Valley's range comes from an actual grid sample across the current
# bbox (2026-08-22, one-off query, not guessed), padded generously since a
# coarse grid sample can miss the true local low point or peak - this is
# exactly the mountainous case the old shared 1300 m ceiling would have
# silently clipped. Tight-corridor bbox (8x8 grid): real span 611..1379 m.
# (Whole-local-area bbox (12x12 grid) sampled 317..1567 m - kept as the
# wider (0.0, 2200.0) range below in case the bbox expands back, since
# that range safely covers the tight-corridor span too.)
ELEV_PLAUSIBLE_M = {
    "oahu": (-50.0, 1300.0),
    "green_valley": (0.0, 2200.0),
}
ELEV_PLAUSIBLE_MIN_M, ELEV_PLAUSIBLE_MAX_M = ELEV_PLAUSIBLE_M[AREA_NAME]
