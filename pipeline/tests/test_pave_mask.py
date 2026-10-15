"""build_pave_mask.py: OSM paved-area filter, and rasterized polygons land
north-up (row 0 = north edge = UE y_min, as AODTerrainManager::LoadPaveMask
and the material's PaveTex UV assume) at the right texels.

Run:  python pipeline/tests/test_pave_mask.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_pave_mask import grid_for, is_paved_area, rasterize


def test_filter():
    assert is_paved_area({"amenity": "parking"})
    assert not is_paved_area({"amenity": "parking", "parking": "underground"})
    assert is_paved_area({"highway": "pedestrian", "area": "yes"})
    assert not is_paved_area({"highway": "pedestrian"})          # a linear street
    assert is_paved_area({"place": "square"}) and is_paved_area({"area:highway": "footway"})
    assert not is_paved_area({"landuse": "grass"})


def test_raster_north_up():
    rect = (0.0, 0.0, 100.0, 50.0)            # 100 m east x 50 m north
    texel, w, h = grid_for(rect, 5.0, 8192)
    assert (texel, w, h) == (5.0, 20, 10)
    lot = [(10.0, 40.0), (30.0, 40.0), (30.0, 50.0), (10.0, 50.0)]   # NW corner, 20 x 10 m
    m = rasterize([lot], rect, texel, w, h)
    assert m[0:2, 2:6].min() == 255            # top rows = north
    assert m[3:, :].max() == 0 and m[:, 7:].max() == 0
    assert grid_for((0.0, 0.0, 80000.0, 1000.0), 5.0, 8192)[0] > 9.7   # capped texel


if __name__ == "__main__":
    test_filter()
    test_raster_north_up()
    print("test_pave_mask: OK")
