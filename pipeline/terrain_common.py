"""Shared Terrarium DEM tile fetching (AWS Open Data, USGS 3DEP-derived).

Used by fetch_data.py (per-stage DEM mosaic for landscape heightmap import)
and build_terrain_mesh_tiles.py (self-hosted baked terrain tiles; formerly
also build_terrain_tiles.py's self-hosted quantized-mesh tile pyramid for
the old live-streaming pipeline, since removed). Same self-hosted,
license-clean source for both - see README Data credits.
"""

import time
from io import BytesIO

import numpy as np
import requests
from PIL import Image

TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def lonlat_to_tilepx(lon, lat, z):
    """Web-mercator global pixel coords (256px tiles) at zoom z."""
    import math
    n = 256 * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return x, y


def fetch_terrarium_tile(z, x, y, session=None, retries=3):
    """Fetch one Terrarium PNG tile (slippy/XYZ numbering). Returns a PIL Image."""
    session = session or requests
    url = TERRAIN_URL.format(z=z, x=x, y=y)
    for attempt in range(retries):
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            return Image.open(BytesIO(resp.content)).convert("RGB")
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def terrarium_to_elevation(img):
    """Decode a Terrarium-encoded PIL Image to a float32 elevation array (meters)."""
    arr = np.asarray(img, dtype=np.float32)
    return arr[:, :, 0] * 256.0 + arr[:, :, 1] + arr[:, :, 2] / 256.0 - 32768.0
