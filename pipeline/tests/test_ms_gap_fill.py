"""fetch_osm_buildings.ms_gap_fill (realism B6): an MS footprint ships only
where no OSM footprint covers it (IoU >= 0.3, or >= 50% of it inside OSM),
it is >= 20 m2, and it touches no water polygon."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fetch_osm_buildings import MS_ID_BASE, ms_gap_fill

D = 1e-4   # ~11 m at lat 21


def sq(x, y, s=D):   # MS ring: [lon, lat], closed
    return [[x, y], [x + s, y], [x + s, y + s], [x, y + s], [x, y]]


def osm(x, y, s=D):  # OSM entry: [lat, lon], open
    return {"nodes": [[lat, lon] for lon, lat in sq(x, y, s)[:-1]]}


X, Y = -157.66, 21.31
buildings = [osm(X, Y), osm(X + 0.01, Y, 10 * D)]
water = [osm(X + 0.02, Y, 5 * D)]
ms = [(sq(X, Y), 6.0),                        # same footprint as OSM: duplicate
      (sq(X + 0.5 * D, Y), -1),               # half-shifted: IoU 1/3, duplicate
      (sq(X + 0.6 * D, Y), -1),               # IoU 0.25: kept (a neighbour)
      (sq(X + 0.01 + 2 * D, Y + 2 * D), -1),  # inside the big OSM mall: duplicate
      (sq(X + 0.005, Y, 0.3 * D), -1),        # ~11 m2 sliver: skipped
      (sq(X + 0.02 + D, Y + D), -1),          # in water: skipped
      (sq(X + 0.005, Y), 7.5),                # alone: kept, with its height
      ([[X, Y + 0.01], [X + D, Y + 0.01], [X, Y + 0.01 + D], [X, Y + 0.01]], -1)]   # triangle: kept
out = ms_gap_fill(buildings, ms, water)
assert [b["way_id"] - MS_ID_BASE for b in out] == [2, 6, 7], out
assert out[1]["ms_height_m"] == 7.5 and out[0]["ms_height_m"] is None
assert all(b["source"] == "ms" and b["building_tag"] == "yes" for b in out)
assert [len(b["nodes"]) for b in out] == [4, 4, 3]   # closing vertex dropped
assert out[1]["nodes"][0] == [Y, X + 0.005]
assert ms_gap_fill([], ms[:1], []) and ms_gap_fill(buildings, [], water) == []
print("ok")
