"""Fetch OSM place nodes (city/town/village/hamlet) for the road-name/
city-label feature (2026-07-23) - same Overpass session/retry/mirror
pattern as fetch_osm_network.py, just querying named place nodes instead of
drivable ways.

Usage:  python fetch_osm_places.py

Outputs to  data/raw/<AREA_NAME>_test/ :
    osm_places.json    [{"name": ..., "lat": ..., "lon": ...}, ...]
"""

import json
from pathlib import Path

import requests

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

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / f"{AREA_NAME}_test"
RAW_DIR.mkdir(parents=True, exist_ok=True)


def fetch_osm_places():
    s, w, n_, e = BBOX_SNWE
    s, w, n_, e = s - MARGIN_DEG, w - MARGIN_DEG, n_ + MARGIN_DEG, e + MARGIN_DEG
    query = f"""
    [out:json][timeout:180];
    node["place"~"{PLACE_CLASS_REGEX}"]["name"]({s},{w},{n_},{e});
    out body;
    """
    print(f"Querying Overpass for named places in {AREA_NAME} bbox (+{MARGIN_DEG} deg margin) ...")
    data = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=200)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            print(f"  {url} failed ({exc}), trying next mirror")
    if data is None:
        raise SystemExit("All Overpass mirrors failed.")

    places = [{"name": el["tags"]["name"], "lat": el["lat"], "lon": el["lon"]}
              for el in data["elements"] if el["type"] == "node"]
    if not places:
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
