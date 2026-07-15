"""Fetch climate normals for the stage (NASA POWER, free, global, no key).

Writes data/stages/<stage>/climate.json with annual precipitation and the
derived road-moisture factor (wishlist #1: wetter climate = darker roads).
"""

import json
from pathlib import Path

import requests

STAGE = "tail_of_the_dragon"
STAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "stages" / STAGE

meta = json.loads((STAGE_DIR / "stage_meta.json").read_text())
lat, lon = meta["origin_latlon"]

url = ("https://power.larc.nasa.gov/api/temporal/climatology/point"
       f"?parameters=PRECTOTCORR,T2M&community=RE"
       f"&longitude={lon:.4f}&latitude={lat:.4f}&format=JSON")
r = requests.get(url, timeout=60,
                 headers={"User-Agent": "OpenDrive-pipeline/0.1"})
r.raise_for_status()
p = r.json()["properties"]["parameter"]
annual_mm = p["PRECTOTCORR"]["ANN"] * 365.25   # mm/day climatology -> mm/yr
t_ann = p["T2M"]["ANN"]

moisture = max(0.0, min(1.0, (annual_mm - 400.0) / 1200.0))
out = {
    "annual_precip_mm": round(annual_mm, 1),
    "annual_temp_c": round(t_ann, 1),
    "road_moisture": round(moisture, 3),
}
(STAGE_DIR / "climate.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out))
