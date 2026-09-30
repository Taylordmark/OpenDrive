"""road_mesh_common dirt/gravel roads (2026-09-30): short surface runs are
absorbed, chunks split exactly where the surface flips, each chunk carries
one "unpaved" flag, and the ODR3 flags byte round-trips.

Run:  python pipeline/tests/test_unpaved_chunks.py"""
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from road_mesh_common import build_road_chunks, chunk_spans, unpaved_stations
from export_road_mesh_bin_v2 import write_tile

# absorb: a 5-station gravel blip inside asphalt vanishes; a short leading run
# takes the following run's value; a long run survives.
assert unpaved_stations(["asphalt"] * 30 + ["gravel"] * 5 + ["asphalt"] * 30) == [False] * 65
assert unpaved_stations(["dirt"] * 3 + ["asphalt"] * 30) == [False] * 33
assert unpaved_stations(["asphalt"] * 30 + ["dirt"] * 25) == [False] * 30 + [True] * 25
assert unpaved_stations(["", "unpaved"]) == [True, True]   # all-short: one surface, no flicker

# spans: no surface data = the old fixed chunking, byte for byte
assert chunk_spans(400, 150) == [(0, 150), (150, 300), (300, 399)]
flags = [False] * 200 + [True] * 200
assert chunk_spans(400, 150, flags) == [(0, 150), (150, 200), (200, 350), (350, 399)]

# chunks: straight 400-station road, gravel from station 200 on
n = 400
s = np.arange(n) * 5.0
chunks, *_ = build_road_chunks(s, s.copy(), np.zeros(n), np.zeros(n), is_unpaved=flags)
assert [c["unpaved"] for c in chunks] == [False, False, True, True]
chunks_old, *_ = build_road_chunks(s, s.copy(), np.zeros(n), np.zeros(n))
assert [c["unpaved"] for c in chunks_old] == [False, False, False]

# wire: flags byte = oneway | unpaved << 1
chunks[2]["oneway"] = True
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "t.bin"
    write_tile(p, chunks, b"0" * 16)
    data = p.read_bytes()
off, got = 4 + 16 + 4, []
for c in chunks:
    vc, tc = struct.unpack_from("<II", data, off)
    got.append(data[off + 8])
    off += 8 + 3 + vc * (12 + 8 + 8) + tc * 12
assert got == [0, 0, 3, 2], got
print("ok")
