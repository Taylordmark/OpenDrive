"""Fetch raw geo data for a stage: OSM road geometry + elevation tiles.

Usage:  python fetch_data.py

Outputs to  data/raw/<stage>/ :
    osm.json        raw Overpass response (ways + nodes for the road)
    dem_mosaic.npy  float32 elevation grid (meters), web-mercator pixel space
    dem_meta.json   tile/pixel georeferencing for the mosaic
"""

import json
from pathlib import Path

import numpy as np
import requests
from terrain_common import fetch_terrarium_tile, lonlat_to_tilepx, terrarium_to_elevation

# ---------------------------------------------------------------- config
STAGE = "tail_of_the_dragon"
# bbox: south, west, north, east  (generous around US-129 Deals Gap -> Tabcat Creek)
BBOX = (35.44, -84.03, 35.56, -83.86)
ROAD_REF_REGEX = "US ?129"      # OSM `ref` tag match
DEM_ZOOM = 15                   # terrarium tiles, ~4 m/px at this latitude
DEM_MARGIN_DEG = 0.012          # extra margin around road extent (~1.2 km)

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
HEADERS = {"User-Agent": "OpenDrive-stage-pipeline/0.1 (taylordmark@gmail.com)"}

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / STAGE
RAW_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- overpass
def fetch_osm():
    query = f"""
    [out:json][timeout:90];
    way["highway"]["ref"~"{ROAD_REF_REGEX}"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
    (._;>;);
    out body;
    """
    print("Querying Overpass for US-129 ways ...")
    data = None
    for url in OVERPASS_URLS:
        try:
            r = requests.post(url, data={"data": query}, headers=HEADERS, timeout=120)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            print(f"  {url} failed ({exc}), trying next mirror")
    if data is None:
        raise SystemExit("All Overpass mirrors failed.")
    ways = [e for e in data["elements"] if e["type"] == "way"]
    nodes = [e for e in data["elements"] if e["type"] == "node"]
    if not ways:
        raise SystemExit("No ways matched ref~'US 129' in bbox - need a fallback query.")
    (RAW_DIR / "osm.json").write_text(json.dumps(data))
    print(f"  got {len(ways)} ways, {len(nodes)} nodes")
    return data


def road_extent(data):
    lats, lons = [], []
    for e in data["elements"]:
        if e["type"] == "node":
            lats.append(e["lat"])
            lons.append(e["lon"])
    return min(lats), min(lons), max(lats), max(lons)


# ---------------------------------------------------------------- terrain
def fetch_dem(extent):
    s, w, n_, e = extent
    s -= DEM_MARGIN_DEG; w -= DEM_MARGIN_DEG; n_ += DEM_MARGIN_DEG; e += DEM_MARGIN_DEG
    x0, y1 = lonlat_to_tilepx(w, s, DEM_ZOOM)   # note: y grows southward
    x1, y0 = lonlat_to_tilepx(e, n_, DEM_ZOOM)
    tx0, tx1 = int(x0 // 256), int(x1 // 256)
    ty0, ty1 = int(y0 // 256), int(y1 // 256)
    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    print(f"Fetching {cols * rows} terrain tiles (z{DEM_ZOOM}, {cols}x{rows}) ...")

    mosaic = np.zeros((rows * 256, cols * 256), dtype=np.float32)
    session = requests.Session()
    for ty in range(ty0, ty1 + 1):
        for tx in range(tx0, tx1 + 1):
            img = fetch_terrarium_tile(DEM_ZOOM, tx, ty, session)
            elev = terrarium_to_elevation(img)
            r0, c0 = (ty - ty0) * 256, (tx - tx0) * 256
            mosaic[r0:r0 + 256, c0:c0 + 256] = elev
        print(f"  row {ty - ty0 + 1}/{rows} done")

    np.save(RAW_DIR / "dem_mosaic.npy", mosaic)
    meta = {
        "zoom": DEM_ZOOM,
        "origin_px": [tx0 * 256, ty0 * 256],   # global pixel coords of mosaic[0,0]
        "shape": list(mosaic.shape),
        "extent_snwe": [s, w, n_, e],
    }
    (RAW_DIR / "dem_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"  mosaic {mosaic.shape}, elevation {mosaic.min():.0f}..{mosaic.max():.0f} m")


if __name__ == "__main__":
    osm = fetch_osm()
    ext = road_extent(osm)
    print(f"Road node extent: lat {ext[0]:.4f}..{ext[2]:.4f}, lon {ext[1]:.4f}..{ext[3]:.4f}")
    fetch_dem(ext)
    print("Raw fetch complete ->", RAW_DIR)
