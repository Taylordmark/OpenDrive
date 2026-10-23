"""Offline check of the drawn-area polygon clip (fetch_cache.clip_osm) and the
pass-2 job rect helpers (bake_job, generation_id.delete_stale_tiles)
(2026-10-21, area picker A2). Synthetic data, no network.

Usage:  python pipeline/test_polygon_clip.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shapely  # noqa: E402

import bake_job  # noqa: E402
from fetch_cache import clip_geojson_lines, clip_osm  # noqa: E402

# --- OSM clip: outline = lon/lat unit square [0, 1] x [0, 1] ---
SQ = shapely.box(0.0, 0.0, 1.0, 1.0)
nodes = {1: (0.2, 0.5), 2: (0.5, 0.5), 3: (1.5, 0.5), 4: (1.6, 0.5), 5: (0.8, 0.6), 6: (0.9, 0.6),
         # house straddling x = 1, centroid inside (0.95)
         10: (0.9, 0.2), 11: (1.0001, 0.2), 12: (1.0001, 0.3), 13: (0.9, 0.3),
         # lake half outside: x 0.5..1.5
         20: (0.5, 0.8), 21: (1.5, 0.8), 22: (1.5, 0.9), 23: (0.5, 0.9),
         30: (0.3, 0.3), 31: (2.0, 2.0)}
els = [{"type": "node", "id": i, "lon": x, "lat": y} for i, (x, y) in nodes.items()]
els[-2]["tags"] = {"place": "town", "name": "In"}
els[-1]["tags"] = {"place": "town", "name": "Out"}
els += [
    # in, out, out, in, in: two runs -> [1, 2] and [5, 6]
    {"type": "way", "id": 100, "nodes": [1, 2, 3, 4, 5, 6], "tags": {"highway": "primary"}},
    {"type": "way", "id": 101, "nodes": [3, 4], "tags": {"highway": "primary"}},
    {"type": "way", "id": 102, "nodes": [10, 11, 12, 13, 10], "tags": {"building": "yes"}},
    {"type": "way", "id": 103, "nodes": [20, 21, 22, 23, 20], "tags": {"natural": "water"}},
    {"type": "way", "id": 104, "center": {"lon": 1.2, "lat": 0.2}, "tags": {"tourism": "museum"}},
    {"type": "way", "id": 105, "center": {"lon": 0.4, "lat": 0.2}, "tags": {"tourism": "museum"}},
]
out = clip_osm({"version": 0.6, "elements": els}, SQ)["elements"]
ways = [e for e in out if e["type"] == "way"]
ids = {e["id"] for e in out if e["type"] == "node"}
roads = [w for w in ways if w.get("tags", {}).get("highway")]
assert [w["nodes"] for w in roads] == [[1, 2], [5, 6]], roads
assert roads[0]["id"] == 100 and roads[1]["id"] >= 2 * 10 ** 15     # split piece gets a new id
assert any(w["id"] == 102 and w["nodes"] == [10, 11, 12, 13, 10] for w in ways)  # house whole
lake = next(w for w in ways if w["id"] == 103)
lake_ll = {e["id"]: (e["lon"], e["lat"]) for e in out if e["type"] == "node"}
ring = shapely.Polygon([lake_ll[n] for n in lake["nodes"]])
assert abs(ring.area - 0.05) < 1e-9 and ring.bounds[2] <= 1.0, ring.wkt      # cut at x = 1
assert [w["id"] for w in ways if "center" in w] == [105]
assert 30 in ids and 31 not in ids and 3 not in ids and 11 in ids            # place in, house node kept
# a relation with any vertex inside keeps its member ways whole
rel = {"type": "relation", "id": 7, "tags": {"type": "multipolygon", "natural": "water"},
       "members": [{"type": "way", "ref": 103, "role": "outer"}]}
out = clip_osm({"elements": els + [rel]}, SQ)["elements"]
assert any(e["type"] == "relation" for e in out)
assert next(w for w in out if w["id"] == 103)["nodes"] == [20, 21, 22, 23, 20]
# HPMS sections cut at the outline
f = clip_geojson_lines([{"properties": {"aadt": 9},
                         "geometry": {"type": "LineString", "coordinates": [[0.5, 0.5], [1.5, 0.5]]}},
                        {"properties": {}, "geometry": {"type": "LineString", "coordinates": [[2, 2], [3, 3]]}}], SQ)
assert len(f) == 1 and max(c[0] for c in f[0]["geometry"]["coordinates"]) == 1.0, f

# --- job rect: east 1000..2000, north -2000..-1000 m (UE y 1000..2000 m) ---
os.environ.pop("OD_JOB_RECT_M", None)
assert bake_job.job_rect() is None and bake_job.tile_in_job(99, 99, 100000.0)
assert bake_job.in_job([0.0], [0.0]).all()
os.environ["OD_JOB_RECT_M"] = "1000,-2000,2000,-1000"
assert bake_job.in_job([150000.0], [150000.0]).all()                         # UE cm, y = -north
assert not bake_job.in_job([150000.0], [-150000.0]).any()
assert bake_job.in_job([90000.0], [150000.0], margin_m=150.0).all()
assert bake_job.tile_in_job(1, 1, 100000.0) and not bake_job.tile_in_job(2, 1, 100000.0)
assert not bake_job.tile_in_job(1, -2, 100000.0)
assert bake_job.tile_in_job(2, 3, 50000.0) and not bake_job.tile_in_job(4, 3, 50000.0)   # 500 m road tiles
assert [k for k, _ in bake_job.job_items({(1, 1): 0, (0, 1): 0, (1, 2): 0}, 100000.0)] == [(1, 1)]

# delete_stale_tiles leaves other jobs' tiles alone (test_areas tile: 2 km
# by default, so the job's 1 km rect touches terrain tile (0, 0) only)
from generation_id import delete_stale_tiles  # noqa: E402
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    for n in ("x_a_test_tile_0_0.bin", "x_a_test_tile_5_5.bin", "road_a_test_tile_2_3.bin",
              "road_a_test_tile_9_9.bin"):
        (d / n).write_bytes(b"")
    assert delete_stale_tiles(d, "x_a_test_tile_*.bin", []) == 1
    assert delete_stale_tiles(d, "road_a_test_tile_*.bin", []) == 1
    assert sorted(p.name for p in d.iterdir()) == ["road_a_test_tile_9_9.bin", "x_a_test_tile_5_5.bin"]
    os.environ.pop("OD_JOB_RECT_M")
    assert delete_stale_tiles(d, "*.bin", []) == 2                        # no job: as before

# --- outline mask sign + in_outline (outline in local metres) ---
bake_job.outline_local = lambda: shapely.box(0.0, 0.0, 100.0, 100.0)
sdf = bake_job.outline_sdf_m([50.0, 10.0, 150.0], [50.0, 50.0, 50.0])
assert abs(sdf[0] - 50) < 1e-9 and abs(sdf[1] - 10) < 1e-9 and abs(sdf[2] + 50) < 1e-9, sdf
assert list(bake_job.in_outline([50.0, 150.0], [50.0, 50.0])) == [True, False]

# --- rebuild_area pass split covers every stage exactly once (both = in each) ---
import rebuild_area  # noqa: E402
p1, p2 = rebuild_area.stages_for("1"), rebuild_area.stages_for("2")
both = [s for s in p1 if s in p2]
assert {s[0].split()[0] for s in both} == rebuild_area.PASS_BOTH
assert len(p1) + len(p2) - len(both) == len(rebuild_area.STAGES)
assert rebuild_area.stages_for(None) == rebuild_area.STAGES
print("test_polygon_clip: all checks passed")
