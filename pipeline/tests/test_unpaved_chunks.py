"""road_mesh_common dirt/gravel roads (2026-09-30): short surface runs are
absorbed, chunks split exactly where the surface flips, each chunk carries
one surface class (2026-10-06: paved/gravel/dirt), and the ODR3 flags byte
round-trips.

Run:  python pipeline/tests/test_unpaved_chunks.py"""
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from road_mesh_common import (SURFACE_DIRT, SURFACE_GRAVEL, SURFACE_PAVED, build_road_chunks,
                              chunk_spans, surface_class, unpaved_stations)
from export_road_mesh_bin_v2 import write_tile

# tag -> class (PLAN-REALISM Phase 2)
for tag in ("gravel", "fine_gravel", "compacted", "unpaved"):
    assert surface_class(tag) == SURFACE_GRAVEL, tag
for tag in ("dirt", "ground", "earth", "mud", "sand"):
    assert surface_class(tag) == SURFACE_DIRT, tag
for tag in ("asphalt", "paved", "concrete", "paving_stones", ""):
    assert surface_class(tag) == SURFACE_PAVED, tag
assert surface_class("", "track") == SURFACE_GRAVEL          # untagged track -> gravel
assert surface_class("", "unclassified") == SURFACE_PAVED    # untagged anything else -> paved
assert surface_class("asphalt", "track") == SURFACE_PAVED    # a tag always wins
assert unpaved_stations([""] * 30, ["track"] * 30) == [SURFACE_GRAVEL] * 30
assert unpaved_stations(["gravel"] * 25 + ["dirt"] * 25) == [1] * 25 + [2] * 25

# absorb: a 5-station gravel blip inside asphalt vanishes; a short leading run
# takes the following run's value; a long run survives.
assert unpaved_stations(["asphalt"] * 30 + ["gravel"] * 5 + ["asphalt"] * 30) == [False] * 65
assert unpaved_stations(["dirt"] * 3 + ["asphalt"] * 30) == [False] * 33
assert unpaved_stations(["asphalt"] * 30 + ["dirt"] * 25) == [0] * 30 + [SURFACE_DIRT] * 25
assert unpaved_stations(["", "unpaved"]) == [1, 1]   # all-short: one surface, no flicker

# spans: no surface data = the old fixed chunking, byte for byte
assert chunk_spans(400, 150) == [(0, 150), (150, 300), (300, 399)]
flags = [False] * 200 + [True] * 200
assert chunk_spans(400, 150, flags) == [(0, 150), (150, 200), (200, 350), (350, 399)]

# chunks: straight 400-station road, gravel from station 200 on
n = 400
s = np.arange(n) * 5.0
chunks, *_ = build_road_chunks(s, s.copy(), np.zeros(n), np.zeros(n), is_unpaved=flags)
assert [c["unpaved"] for c in chunks] == [0, 0, 1, 1]
chunks_old, *_ = build_road_chunks(s, s.copy(), np.zeros(n), np.zeros(n))
assert [c["unpaved"] for c in chunks_old] == [False, False, False]

# wire: flags byte = oneway | class << 1
chunks[2]["oneway"] = True
chunks[3]["unpaved"] = SURFACE_DIRT
with tempfile.TemporaryDirectory() as d:
    p = Path(d) / "t.bin"
    write_tile(p, chunks, b"0" * 16)
    data = p.read_bytes()
off, got = 4 + 16 + 4, []
for c in chunks:
    vc, tc = struct.unpack_from("<II", data, off)
    got.append(data[off + 8])
    off += 8 + 3 + vc * (12 + 8 + 8) + tc * 12
assert got == [0, 0, 3, 4], got
print("ok")
