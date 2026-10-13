"""bake_world_map.py (2026-10-13): ODL1 town parse -> per-city centroids.

Run:  python pipeline/tests/test_bake_world_map.py"""
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bake_world_map as bw


def rec(x, y, road, city):
    r, c = road.encode(), city.encode()
    return struct.pack("<ffH", x, y, len(r)) + r + struct.pack("<H", len(c)) + c


body = [rec(0, 0, "Main St", "Brisbane"), rec(200, 100, "Oak", "Brisbane"),
        rec(5, 5, "Hwy 1", ""), rec(-50, 30, "Elm", "Alameda")]
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "labels.bin"
    p.write_bytes(b"ODL1" + bytes(16) + struct.pack("<I", len(body)) + b"".join(body))
    towns = bw.read_towns(p)
assert towns == [["Alameda", -50, 30], ["Brisbane", 100, 50]], towns   # sorted, centroid, blank city dropped
print("ok")
