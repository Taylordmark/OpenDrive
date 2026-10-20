"""Disk cache for everything the bake downloads (2026-10-01).

Every rebake used to re-download the same Terrarium DEM tiles (once per
stage that samples heights, and once per terrain worker), the USGS imagery
tiles classify_biomes reads, and every Overpass query. Responses are now kept
under data/cache/<kind>/ (gitignored with the rest of data/) and reused.

To pull fresh data from the source, delete the relevant data/cache/<kind>/
dir - e.g. data/cache/overpass/ after OSM edits you want picked up.
"""
import hashlib
import json
import os
import time
from pathlib import Path

import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
# Waits (s) before each extra pass over every mirror (2026-10-14: both
# original mirrors 504'd for an hour, failing whole rebakes on the spot).
OVERPASS_RETRY_WAITS_S = (30, 120, 300)


def cached_bytes(kind, name, fetch):
    """fetch() -> bytes, or the copy saved under data/cache/<kind>/<name>.
    Safe across the parallel bake workers: written to a per-process temp
    file and renamed into place, so nobody ever reads a half-written file."""
    path = CACHE_DIR / kind / name
    if path.is_file():
        return path.read_bytes()
    data = fetch()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes(data)
    try:
        os.replace(tmp, path)
    except OSError:   # Windows: another worker wrote and is reading it - same bytes
        tmp.unlink(missing_ok=True)
    return data


def overpass_json(query, headers, timeout=200):
    """POST an Overpass QL query (trying each mirror), cached by query text."""
    def fetch():
        for wait in (0, *OVERPASS_RETRY_WAITS_S):
            if wait:
                print(f"  all Overpass mirrors failed, retrying in {wait} s")
                time.sleep(wait)
            for url in OVERPASS_URLS:
                try:
                    r = requests.post(url, data={"data": query}, headers=headers, timeout=timeout)
                    r.raise_for_status()
                    # never cache a non-JSON error page, nor a timed-out query's
                    # partial result (HTTP 200 + "remark": "runtime error: ...")
                    if "error" in r.json().get("remark", ""):
                        raise ValueError(r.json()["remark"])
                    return r.content
                except (requests.RequestException, ValueError) as exc:
                    print(f"  {url} failed ({exc}), trying next mirror")
        raise SystemExit("All Overpass mirrors failed.")
    key = hashlib.sha1(query.encode()).hexdigest()
    return json.loads(cached_bytes("overpass", f"{key}.json", fetch))
