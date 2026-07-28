"""Shared test-area config (Arc 7 open-world work) so the bbox/name used by the
terrain pipeline, OSM road-network fetch, and road-mesh build never drift
apart - they did once already this session (Green Valley -> Palmdale).
"""

# Skagit Valley / North Cascades foothills, WA (2026-07-20 - 2026-07-26 test
# area; kept here commented out, not deleted, as the known-good reference
# config the whole open-world pipeline was proven against before Oahu).
# AREA_NAME = "skagit_cascades"
# BBOX_SNWE = (48.000, -122.400, 48.550, -121.500)   # south, west, north, east
# ORIGIN_HEIGHT_M = 60.0

# Oahu, HI (2026-07-27) - the next testbed: a real island (Taylor's
# explicit ask, "build the entire island"), coastline on every side rather
# than the Skagit loop's inland rural corridor, Honolulu's actual dense
# urban grid + real high-rise downtown, dramatic Koʻolau/Waiʻanae relief,
# and the first real exercise of the 14-archetype biome table's
# tropical/coastal entries (Rainforest, Savanna, CoastalDune) - everything
# tested so far has been temperate Pacific NW. Bbox padded slightly beyond
# the island's own coastline so imagery/terrain fetches don't clip right at
# the shore. Whole island, not a scoped loop - Taylor's call given this is
# meant to surface a broad investigation list, not a clean single feature.
AREA_NAME = "oahu"
# south, west, north, east
BBOX_SNWE = (21.24, -158.30, 21.72, -157.65)

# Fetch_osm_network.py grabs every drivable road in the bbox. Kept at None
# (no ref filter, every drivable road including ramps/cross streets) per
# Taylor's 2026-07-25 realism preference for the Skagit loop - for Oahu this
# also means Honolulu's actual street grid, not just the highway loop
# around the island; expect a much denser network than any prior test area.
ROAD_REF_REGEX = None

# Sampled ground elevation at the origin point - bilinear-read directly from
# a fetched Terrarium tile at the origin lon/lat (z12, one-off interactive
# query, 2026-07-27), not hand-copied from a map/guess. A prior session's
# Palmdale value being off caused an ~800 m road/terrain offset bug, caught
# via AODRoadManager's -odseam check - exactness matters less than every
# consumer using the SAME value consistently, but starting from a real
# sample instead of a guess avoids that risk entirely. Oahu's bbox center
# (21.485, -157.975) lands on the Wahiawa plateau/central saddle between the
# Koʻolau and Waiʻanae ranges (real land, not open ocean, despite the bbox
# spanning coast to coast) - sampled value confirms this.
# The old georeference actor used origin_placement=CARTOGRAPHIC_ORIGIN with
# this as origin_height, which made UE world Z = (absolute_elevation_m -
# ORIGIN_HEIGHT_M) * 100 at the origin lon/lat - any pipeline step that
# writes absolute DEM-sourced elevations into UE-frame cm coordinates for
# this level (currently: build_road_network.py) MUST subtract this first,
# or geometry floats ~ORIGIN_HEIGHT_M metres above where the terrain
# actually renders.
ORIGIN_HEIGHT_M = 306.0

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
