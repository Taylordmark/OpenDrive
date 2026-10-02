"""fetch_cache.cached_bytes (2026-10-01): the first call downloads and saves,
every later call reads the saved copy without touching the network.

Run:  python pipeline/tests/test_fetch_cache.py"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetch_cache

calls = []
def fetch():
    calls.append(1)
    return b"tile bytes"

with tempfile.TemporaryDirectory() as d:
    fetch_cache.CACHE_DIR = Path(d)
    assert fetch_cache.cached_bytes("t", "12/3/4.png", fetch) == b"tile bytes"
    assert fetch_cache.cached_bytes("t", "12/3/4.png", fetch) == b"tile bytes"
    assert len(calls) == 1, calls
    assert (Path(d) / "t" / "12" / "3" / "4.png").read_bytes() == b"tile bytes"
    assert not list(Path(d).rglob("*.tmp"))
print("ok")
