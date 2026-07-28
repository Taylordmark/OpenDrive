"""Build sparse (road_label, city_label) points for the HUD's road-name/
city label (2026-07-23) - same downsampling/output-shape convention as
classify_biomes.py's road_biomes.json, just carrying strings instead of an
enum index.

Usage:  python build_place_labels.py     (needs road_stations.json and
                                            osm_places.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
        data/raw/<AREA_NAME>_test/osm_places.json
Outputs data/road_tiles/<AREA_NAME>_test/road_labels.json
        {"x_cm": [...], "y_cm": [...],
         "road_label": [...], "city_label": [...]}
        one entry per SAMPLED station (every SAMPLE_STRIDE-th, matching
        classify_biomes.py's reasoning: a road/city label doesn't need 5 m
        precision, and this keeps the sidecar small).
"""

import json
import math
from pathlib import Path

import numpy as np

from test_areas import AREA_NAME

SAMPLE_STRIDE = 10   # every 10th station (~50 m spacing), same as classify_biomes.py

M_PER_DEG_LAT = 111132.0

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
PLACES_PATH = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm_places.json"
OUT_PATH = STATIONS_PATH.parent / "road_labels.json"


def main():
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    places = json.loads(PLACES_PATH.read_text(encoding="utf-8"))

    lon = np.concatenate([np.array(seg["lon"]) for seg in data["segments"]])
    lat = np.concatenate([np.array(seg["lat"]) for seg in data["segments"]])
    east_m = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    north_m = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    road_name = sum([seg["road_name"] for seg in data["segments"]], [])

    lon, lat = lon[::SAMPLE_STRIDE], lat[::SAMPLE_STRIDE]
    east_m, north_m = east_m[::SAMPLE_STRIDE], north_m[::SAMPLE_STRIDE]
    road_name = road_name[::SAMPLE_STRIDE]
    print(f"{len(lon)} sample point(s) (every {SAMPLE_STRIDE}th station)")

    # project places into the SAME local east/north meter frame road
    # stations already use (origin_latlon), so nearest-distance is a plain
    # Euclidean lookup, not a lat/lon haversine one
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    place_names = [p["name"] for p in places]
    place_east = np.array([(p["lon"] - lon0) * m_per_deg_lon for p in places])
    place_north = np.array([(p["lat"] - lat0) * M_PER_DEG_LAT for p in places])

    city_label = []
    for e, n in zip(east_m, north_m):
        d2 = (place_east - e) ** 2 + (place_north - n) ** 2
        city_label.append(place_names[int(np.argmin(d2))] if len(place_names) else "")

    x_cm, y_cm = east_m * 100.0, -north_m * 100.0
    out = {
        "x_cm": x_cm.tolist(), "y_cm": y_cm.tolist(),
        "road_label": road_name, "city_label": city_label,
    }
    OUT_PATH.write_text(json.dumps(out), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({len(x_cm)} points, {len(places)} place(s) available)")


if __name__ == "__main__":
    main()
