"""Fetch climate normals + storm eligibility per stage/test road (Weather W0,
next steps.md "Weather plan (2026-09-25)").

Climate: NASA POWER climatology (free, global, no key) - annual precip/temp,
the derived road-moisture factor (wishlist #1: wetter climate = darker
roads), and 12 monthly temp/precip values for the W1 season clock.
coast_km: nearest Natural Earth 10m coastline vertex (the OSM coastline
via Overpass was tried first - radius queries 429/504 at 150+ km).
Storm weights (0..1) are computed offline here so the runtime just reads
them; hand-edit climate.json to override a stage.

Usage:  python fetch_climate.py [name ...]   (default: every stage + test road)
        python fetch_climate.py --selftest
Reads   data/stages/<name>/stage_meta.json  or
        data/road_tiles/<name>/road_test_meta.json (+ road_biomes.json)
Writes  <same dir>/climate.json
        OpenDriveUE/Content/OpenDrive/Masks/climate_<name>.json (runtime copy
        for AODWeatherController, staged like the .bin sidecars)
"""

import argparse
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
import requests

ROOT = Path(__file__).resolve().parent.parent
# Natural Earth 10m coastline (public domain), downloaded once and kept
COAST_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
             "master/geojson/ne_10m_coastline.geojson")
COAST_PATH = ROOT / "data" / "raw" / "ne_10m_coastline.geojson"
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
HEADERS = {"User-Agent": "OpenDrive-pipeline/0.1"}
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_DAYS = [31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
# EODBiome ints, order MUST match classify_biomes.BIOME_NAMES (not imported:
# classify_biomes pulls in test_areas' ACTIVE_AREA)
PLAINS, FARMLAND = 4, 8
ALPINE, ARCTIC = 6, 7
DESERT, SAVANNA, CANYON = 3, 9, 12

# --- hurricane ---
HURRICANE_MAX_COAST_KM = 150.0          # weight ramps 1 -> 0 over this distance
HURRICANE_ABS_LAT = (8.0, 40.0)
# ponytail: lat/lon boxes, not real basin polygons - fine for picking stages
HURRICANE_BASINS = {                     # (lat_min, lat_max, lon_min, lon_max)
    "N Atlantic": (8, 45, -100, -10),
    "E Pacific": (8, 30, -140, -85),     # stops at Baja: SoCal never qualifies
    "C Pacific": (8, 30, -180, -140),    # Hawaii
    "W Pacific": (8, 45, 100, 180),
    "N Indian": (8, 30, 45, 100),
    "S Indian/Pacific": (-45, -8, 30, 180),
    "S Pacific E": (-45, -8, -180, -120),
}
# --- tornado ---
TORNADO_ABS_LAT = (25.0, 50.0)
TORNADO_MIN_COAST_KM = 150.0             # "continental interior"
TORNADO_FULL_SHARE = 0.4                 # Plains+Farmland share for weight 1
TORNADO_ALPINE_VETO_SHARE = 0.05         # Alpine+Arctic share that zeroes it
TORNADO_SE_US_BOX = (30, 37, -92, -76)   # Dixie Alley weak allowance
TORNADO_SE_US_WEIGHT = 0.25
# --- dust storm ---
DUST_MAX_PRECIP_MM = 350.0
DUST_FULL_SHARE = 0.5                    # Desert+Savanna+Canyon share for weight 1


def clamp01(v):
    return max(0.0, min(1.0, v))


def in_box(lat, lon, box):
    return box[0] <= lat <= box[1] and box[2] <= lon <= box[3]


def storm_weights(lat, lon, coast_km, shares, annual_mm):
    """shares: {biome_int: fraction}, or None when no road_biomes.json."""
    hurricane = 0.0
    if (HURRICANE_ABS_LAT[0] <= abs(lat) <= HURRICANE_ABS_LAT[1]
            and any(in_box(lat, lon, b) for b in HURRICANE_BASINS.values())):
        hurricane = clamp01(1.0 - coast_km / HURRICANE_MAX_COAST_KM)

    s = shares or {}
    tornado = 0.0
    if (TORNADO_ABS_LAT[0] <= abs(lat) <= TORNADO_ABS_LAT[1]
            and coast_km >= TORNADO_MIN_COAST_KM
            and s.get(ALPINE, 0) + s.get(ARCTIC, 0) < TORNADO_ALPINE_VETO_SHARE):
        tornado = clamp01((s.get(PLAINS, 0) + s.get(FARMLAND, 0)) / TORNADO_FULL_SHARE)
        if in_box(lat, lon, TORNADO_SE_US_BOX):
            tornado = max(tornado, TORNADO_SE_US_WEIGHT)

    dust = 0.0
    if annual_mm < DUST_MAX_PRECIP_MM:
        # no biome data -> precipitation alone decides
        dry = 1.0 if shares is None else s.get(DESERT, 0) + s.get(SAVANNA, 0) + s.get(CANYON, 0)
        dust = clamp01(dry / DUST_FULL_SHARE)
    return {"hurricane": round(hurricane, 2), "tornado": round(tornado, 2),
            "dust_storm": round(dust, 2)}


def coast_km(lat, lon):
    if not COAST_PATH.exists():
        r = requests.get(COAST_URL, timeout=300, headers=HEADERS)
        r.raise_for_status()
        COAST_PATH.parent.mkdir(parents=True, exist_ok=True)
        COAST_PATH.write_bytes(r.content)
    feats = json.loads(COAST_PATH.read_text())["features"]
    pts = np.radians(np.concatenate([np.asarray(f["geometry"]["coordinates"])
                                     for f in feats]))  # (lon, lat)
    # ponytail: nearest vertex, not segment - NE 10m vertex spacing is ~1 km,
    # far below the 150 km thresholds
    p1, l1 = math.radians(lat), math.radians(lon)
    a = (np.sin((pts[:, 1] - p1) / 2) ** 2
         + math.cos(p1) * np.cos(pts[:, 1]) * np.sin((pts[:, 0] - l1) / 2) ** 2)
    return round(float(6371.0 * 2 * np.arcsin(np.sqrt(a.min()))), 1)


def find_dir(name):
    for d, meta in ((ROOT / "data" / "stages" / name, "stage_meta.json"),
                    (ROOT / "data" / "road_tiles" / name, "road_test_meta.json")):
        if (d / meta).exists():
            return d, json.loads((d / meta).read_text())
    raise SystemExit(f"no stage_meta.json/road_test_meta.json for {name}")


def all_names():
    return ([p.parent.name for p in sorted((ROOT / "data" / "stages").glob("*/stage_meta.json"))]
            + [p.parent.name for p in sorted((ROOT / "data" / "road_tiles").glob("*/road_test_meta.json"))])


def power_climatology(lat, lon):
    """NASA POWER climatology at a point -> {"PRECTOTCORR": mm/day, "T2M": C},
    each keyed by MONTHS + "ANN". Also classify_biomes.py's aridity gate."""
    url = ("https://power.larc.nasa.gov/api/temporal/climatology/point"
           f"?parameters=PRECTOTCORR,T2M&community=RE"
           f"&longitude={lon:.4f}&latitude={lat:.4f}&format=JSON")
    r = requests.get(url, timeout=60, headers=HEADERS)
    r.raise_for_status()
    return r.json()["properties"]["parameter"]


def run(name):
    out_dir, meta = find_dir(name)
    lat, lon = meta.get("center_latlon") or meta["origin_latlon"]   # area / stage meta

    p = power_climatology(lat, lon)
    annual_mm = p["PRECTOTCORR"]["ANN"] * 365.25   # mm/day climatology -> mm/yr
    t_ann = p["T2M"]["ANN"]

    shares = None
    biomes_path = out_dir / "road_biomes.json"
    if biomes_path.exists():
        b = json.loads(biomes_path.read_text())["biome"]
        shares = {k: v / len(b) for k, v in Counter(b).items()}

    ckm = coast_km(lat, lon)
    moisture = max(0.0, min(1.0, (annual_mm - 400.0) / 1200.0))
    out = {
        "annual_precip_mm": round(annual_mm, 1),
        "annual_temp_c": round(t_ann, 1),
        "road_moisture": round(moisture, 3),
        "monthly_temp_c": [round(p["T2M"][m], 1) for m in MONTHS],
        "monthly_precip_mm": [round(p["PRECTOTCORR"][m] * d, 1)
                              for m, d in zip(MONTHS, MONTH_DAYS)],
        "coast_km": ckm,
        "biome_source": "road_biomes.json" if shares is not None else "none",
        "storms": storm_weights(lat, lon, ckm, shares, annual_mm),
    }
    text = json.dumps(out, indent=2)
    (out_dir / "climate.json").write_text(text)
    MASKS_DIR.mkdir(parents=True, exist_ok=True)
    (MASKS_DIR / f"climate_{name}.json").write_text(text)
    return lat, lon, out


def selftest():
    # Oahu: coastal, central Pacific
    w = storm_weights(21.48, -157.98, 3.0, {10: 1.0}, 1100)
    assert w["hurricane"] > 0.9 and w["tornado"] == 0 and w["dust_storm"] == 0, w
    # Kansas farmland, interior
    w = storm_weights(38.5, -98.0, 900.0, {PLAINS: 0.3, FARMLAND: 0.3}, 700)
    assert w["tornado"] == 1.0 and w["hurricane"] == 0, w
    # Colorado high country: alpine veto
    w = storm_weights(39.5, -106.2, 1100.0, {PLAINS: 0.5, ALPINE: 0.1}, 500)
    assert w["tornado"] == 0, w
    # SE US forest: weak tornado allowance, too far inland for hurricanes
    w = storm_weights(35.5, -84.0, 400.0, {1: 1.0}, 1400)
    assert w["tornado"] == TORNADO_SE_US_WEIGHT and w["hurricane"] == 0, w
    # Mojave: dust, and SoCal is outside every hurricane basin
    w = storm_weights(34.6, -118.1, 60.0, {DESERT: 0.7}, 200)
    assert w["dust_storm"] == 1.0 and w["hurricane"] == 0, w
    assert storm_weights(34.6, -118.1, 60.0, None, 200)["dust_storm"] == 1.0
    assert storm_weights(34.6, -118.1, 60.0, {DESERT: 0.7}, 400)["dust_storm"] == 0
    # Gulf coast
    assert storm_weights(29.3, -94.8, 1.0, {}, 1300)["hurricane"] > 0.9
    print("selftest ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*", help="stage/test-road dir names (default: all)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        raise SystemExit
    print(f"{'name':24} {'lat':>7} {'lon':>9} {'precip':>7} {'temp':>5} "
          f"{'coast':>6} {'biomes':>6} {'hurr':>5} {'torn':>5} {'dust':>5}")
    for name in args.names or all_names():
        lat, lon, o = run(name)
        s = o["storms"]
        print(f"{name:24} {lat:7.2f} {lon:9.2f} {o['annual_precip_mm']:7.0f} "
              f"{o['annual_temp_c']:5.1f} {str(o['coast_km']):>6} "
              f"{'yes' if o['biome_source'] != 'none' else 'no':>6} "
              f"{s['hurricane']:5.2f} {s['tornado']:5.2f} {s['dust_storm']:5.2f}")
