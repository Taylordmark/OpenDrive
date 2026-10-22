"""Fetch OSM place nodes (city/town/village/hamlet) for the road-name/
city-label feature (2026-07-23) - same Overpass session/retry/mirror
pattern as fetch_osm_network.py, just querying named place nodes instead of
drivable ways.

Usage:  python fetch_osm_places.py

Outputs to  data/raw/<AREA_NAME>_test/ :
    osm_places.json    [{"name": ..., "lat": ..., "lon": ...}, ...]
"""

import json
import re
from pathlib import Path

from bake_job import outline_ll
from fetch_cache import overpass_json

from test_areas import AREA_NAME, BBOX_SNWE

# city/town/village/hamlet - the granularity a driving-game label should
# show ("Mount Vernon", not "Skagit County" or a single farmhouse's
# isolated=yes tag); suburb/neighbourhood/locality are deliberately excluded
# as too fine-grained for a HUD label at highway speed.
PLACE_CLASS_REGEX = "^(city|town|village|hamlet)$"

# fetch a bit beyond the terrain bbox so a town just outside the edge can
# still be "nearest" for road stations near the border, instead of every
# border-area station picking a farther-away in-bbox town by default
MARGIN_DEG = 0.15

HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def population(tags):
    """OSM population= as an int ("1,234", "approx 500" -> 1234, 500), else None."""
    m = re.search(r"\d[\d,]*", tags.get("population", ""))
    return int(m[0].replace(",", "")) if m else None


def fetch_osm_places():
    s, w, n_, e = BBOX_SNWE
    s, w, n_, e = s - MARGIN_DEG, w - MARGIN_DEG, n_ + MARGIN_DEG, e + MARGIN_DEG
    query = f"""
    [out:json][timeout:180];
    node["place"~"{PLACE_CLASS_REGEX}"]["name"]({s},{w},{n_},{e});
    out body;
    """
    print(f"Querying Overpass for named places in {AREA_NAME} bbox (+{MARGIN_DEG} deg margin) ...")
    data = overpass_json(query, HEADERS)

    # place + population (2026-10-21): the area picker names a drawn area
    # after its biggest town
    places = [{"name": el["tags"]["name"], "lat": el["lat"], "lon": el["lon"],
               "place": el["tags"].get("place"), "population": population(el["tags"])}
              for el in data["elements"] if el["type"] == "node"]
    # a drawn area's outline (fetch_cache.clip_osm) may hold no named place
    if not places and outline_ll() is None:
        raise SystemExit(f"No named places matched in the {AREA_NAME} bbox+margin.")
    (RAW_DIR / "osm_places.json").write_text(json.dumps(places), encoding="utf-8")
    print(f"  got {len(places)} place(s) -> {RAW_DIR / 'osm_places.json'}")
    for p in places:
        # Windows console default codepage (cp1252) can't encode every real
        # place name (accented characters etc.) - this is diagnostic output
        # only, the file itself is written as UTF-8 above regardless.
        try:
            print(f"    {p['name']} ({p['lat']:.4f}, {p['lon']:.4f})")
        except UnicodeEncodeError:
            print(f"    {p['name'].encode('ascii', 'replace').decode()} "
                  f"({p['lat']:.4f}, {p['lon']:.4f})")
    return places


if __name__ == "__main__":
    fetch_osm_places()
