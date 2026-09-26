"""§7.34 verification (Finding 2): run the REAL build_water_tiles.py main()
against already-cached raw OSM data (no repeat Overpass queries) and a
preloaded DEM tile cache (data/raw/summit_county_test/dem_tiles.npz, same
z15 as NEAR_DEM_ZOOM - avoids most/all live elevation-tile fetches), so the
current (fixed) code's new ribbon_flags sidecar gets generated for real,
then build_road_embankments.py can be run against genuinely fixed data
instead of a stub. Falls back to a live fetch only for any DEM tile not
already in the preloaded cache.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import numpy as np
import build_water_tiles as bwt

RAW_DIR = bwt.RAW_DIR

_cached_polygons = json.loads((RAW_DIR / "osm_water.json").read_text())
_cached_waterways = json.loads((RAW_DIR / "osm_waterways.json").read_text())


def _fetch_water_polygons_cached():
    print(f"[offline] using cached {RAW_DIR / 'osm_water.json'} "
          f"({len(_cached_polygons)} polygon(s)) instead of an Overpass query")
    return _cached_polygons


def _fetch_waterways_cached():
    # JSON object keys are always strings - the live fetch_waterways() builds
    # `nodes` keyed by int node id (chain_waterways() looks up `nid in nodes`
    # with nid as int, from node_ids), so the round-tripped-through-JSON cache
    # must convert keys back to int or every lookup silently misses and every
    # chain gets dropped for having < 2 resolved latlon points.
    nodes_int_keyed = {int(k): v for k, v in _cached_waterways["nodes"].items()}
    print(f"[offline] using cached {RAW_DIR / 'osm_waterways.json'} "
          f"({len(_cached_waterways['ways'])} way(s)) instead of an Overpass query")
    return _cached_waterways["ways"], nodes_int_keyed


bwt.fetch_water_polygons = _fetch_water_polygons_cached
bwt.fetch_waterways = _fetch_waterways_cached

# Preload the z15 DEM tile cache build_road_stations.py already fetched for
# this same area/zoom (dem_tiles.npz) into build_water_tiles's own dem_cache
# dict (keyed (zoom, tx, ty)) - sample_dem() only fetches live for a
# (zoom, tx, ty) key that's NOT already present.
_dem_npz_path = RAW_DIR / "dem_tiles.npz"
_preload = {}
if _dem_npz_path.exists():
    with np.load(_dem_npz_path) as z:
        for k in z.files:
            if k == "__fingerprint__":
                continue
            tx, ty = (int(v) for v in k.split(","))
            _preload[(bwt.NEAR_DEM_ZOOM, tx, ty)] = z[k]
    print(f"[offline] preloaded {len(_preload)} DEM tile(s) from {_dem_npz_path.name}")

_orig_main = bwt.main


def main_with_preloaded_dem():
    # main() creates its own dem_cache={} local var - reach in by patching
    # sample_dem to seed any cache dict it's given the first time it's called.
    orig_sample_dem = bwt.sample_dem
    seeded = {"done": False}

    def sample_dem_seeded(lat_arr, lon_arr, zoom, session, cache):
        if not seeded["done"]:
            cache.update(_preload)
            seeded["done"] = True
        return orig_sample_dem(lat_arr, lon_arr, zoom, session, cache)

    bwt.sample_dem = sample_dem_seeded
    _orig_main()


main_with_preloaded_dem()
