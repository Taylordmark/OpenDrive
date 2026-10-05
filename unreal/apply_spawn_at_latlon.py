"""Move the already-built OpenWorldTest level's PlayerStart to an ARBITRARY
real-world lat/lon (2026-07-28) - for controlled visual-comparison testing
(Taylor: spawn at a known spot, screenshot immediately, compare against
real-world Street View/satellite for that exact location - no ambiguity
about where a mid-drive screenshot was actually taken, unlike trying to
back-derive position from an arbitrary screenshot after the fact).

Same "patch an existing level in place" pattern as apply_player_start_fix.py
- no full level rebuild. Samples REAL Terrarium elevation at the target
lat/lon (same DEM source every other pipeline step uses) rather than
guessing a height, so the spawn clearance is realistic regardless of where
on the island the target point is (coastal flat vs. mountain interior).

Edit LAT/LON below, then:
UnrealEditor-Cmd.exe <OpenDriveUE.uproject> -run=pythonscript
    -script="<this file>" -nullrhi -unattended -nosplash -nop4 -stdout
"""

import math
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import unreal

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from test_areas import AREA_NAME, BBOX_SNWE, ORIGIN_HEIGHT_M, to_local

# UE's embedded Python (this commandlet) has no numpy/PIL/requests - only
# stdlib. So unlike every other pipeline script, this one can't import
# terrain_common and must fetch+decode Terrarium tiles itself. URL pattern
# and elevation formula below are copied verbatim from pipeline/terrain_common.py
# - keep them in sync if that file's DEM source ever changes.
TERRAIN_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def fetch_terrarium_tile(z, x, y, retries=3):
    """Fetch one Terrarium PNG tile and decode straight to elevation rows
    (meters), no PIL/numpy. Terrarium tiles are always 8-bit RGB, no
    interlacing - decode is the plain PNG "none/sub/up/avg/paeth" scanline
    unfilter, no palette/alpha handling needed."""
    url = TERRAIN_URL.format(z=z, x=x, y=y)
    req = urllib.request.Request(url, headers={"User-Agent": "OpenDrive-spawn-tool"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _decode_terrarium_png(resp.read())
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def _decode_terrarium_png(data):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    width = height = None
    idat = bytearray()
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        ctype = data[pos + 4:pos + 8]
        cdata = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + type + data + crc
        if ctype == b"IHDR":
            width, height, bit_depth, color_type, _comp, _filt, interlace = \
                struct.unpack(">IIBBBBB", cdata)
            if bit_depth != 8 or color_type != 2 or interlace != 0:
                raise ValueError(f"unsupported Terrarium PNG format "
                                  f"(bitdepth={bit_depth} colortype={color_type} "
                                  f"interlace={interlace}) - expected 8-bit RGB, non-interlaced")
        elif ctype == b"IDAT":
            idat += cdata
        elif ctype == b"IEND":
            break

    raw = zlib.decompress(bytes(idat))
    bpp = 3   # RGB, 8-bit channels
    stride = width * bpp
    prev = bytearray(stride)
    rows = []
    offset = 0
    for _y in range(height):
        filt = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if filt == 0:
                pass
            elif filt == 1:
                line[i] = (line[i] + a) & 0xFF
            elif filt == 2:
                line[i] = (line[i] + b) & 0xFF
            elif filt == 3:
                line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
            else:
                raise ValueError(f"unsupported PNG filter type {filt}")
        rows.append(line)
        prev = line
    return rows


def terrarium_elevation_at(rows, x, y):
    """Same formula as terrain_common.terrarium_to_elevation, one pixel at a time."""
    i = x * 3
    r, g, b = rows[y][i], rows[y][i + 1], rows[y][i + 2]
    return r * 256.0 + g + b / 256.0 - 32768.0


LEVEL = "/Game/OpenWorldTest"
LABEL = "Start_TestArea"

# ---- EDIT THESE for each test spot ----
LAT, LON = 21.57160, -157.87490   # real-world Punaluu, windward Oahu coast
YAW_DEG = 0.0
# ----------------------------------------

DEM_ZOOM = 15   # matches build_road_stations.py's own DEM_ZOOM
SPAWN_CLEARANCE_CM = 2000.0   # 20 m - same streaming catch-up margin every
                               # other spawn point in this project uses


def log(msg):
    unreal.log(f"SPAWNATLATLON {msg}")


def sample_elevation(lat, lon, zoom):
    """Bilinear Terrarium sample at one point - same math as
    build_terrain_mesh_tiles.py's sample_dem(), just for a single query
    point instead of a batch, so this stays a small standalone script."""
    n = 256 * (2 ** zoom)
    lat_r = math.radians(lat)
    px_glob = (lon + 180.0) / 360.0 * n
    py_glob = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    tx, ty = int(px_glob // 256), int(py_glob // 256)
    lx, ly = px_glob - tx * 256, py_glob - ty * 256
    x0, y0 = min(int(lx), 255), min(int(ly), 255)
    fx, fy = lx - x0, ly - y0
    tx1, x1 = (tx + 1, 0) if x0 == 255 else (tx, x0 + 1)
    ty1, y1 = (ty + 1, 0) if y0 == 255 else (ty, y0 + 1)

    cache = {}
    for key in {(tx, ty), (tx1, ty), (tx, ty1), (tx1, ty1)}:
        cache[key] = fetch_terrarium_tile(zoom, key[0], key[1])

    def px(k, x, y):
        return terrarium_elevation_at(cache[k], x, y)

    v00, v01 = px((tx, ty), x0, y0), px((tx1, ty), x1, y0)
    v10, v11 = px((tx, ty1), x0, y1), px((tx1, ty1), x1, y1)
    return (v00 * (1 - fx) * (1 - fy) + v01 * fx * (1 - fy)
            + v10 * (1 - fx) * fy + v11 * fx * fy)


if not (BBOX_SNWE[0] <= LAT <= BBOX_SNWE[2] and BBOX_SNWE[1] <= LON <= BBOX_SNWE[3]):
    raise SystemExit(f"({LAT},{LON}) is outside this area's bbox {BBOX_SNWE} - "
                      f"wrong AREA_NAME ('{AREA_NAME}') or a typo in LAT/LON")

east_m, north_m = to_local(LON, LAT)
elev_m = sample_elevation(LAT, LON, DEM_ZOOM)
z_cm = (elev_m - ORIGIN_HEIGHT_M) * 100.0
log(f"({LAT},{LON}) -> east={east_m:.0f}m north={north_m:.0f}m elev={elev_m:.1f}m")

lls = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
if not lls.load_level(LEVEL):
    raise RuntimeError("could not load level")
actor_sub = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

start = None
for a in actor_sub.get_all_level_actors():
    if a.get_actor_label() == LABEL:
        start = a
        break
if start is None:
    raise RuntimeError(f"{LABEL} not found in {LEVEL} - run "
                        f"build_openworld_test_level.py first")

new_loc = unreal.Vector(east_m * 100.0, -north_m * 100.0, z_cm + SPAWN_CLEARANCE_CM)
new_rot = unreal.Rotator(0, 0, YAW_DEG)
start.set_actor_location(new_loc, False, False)
start.set_actor_rotation(new_rot, False)
log(f"{LABEL} moved to ({new_loc.x:.0f},{new_loc.y:.0f},{new_loc.z:.0f}) yaw={YAW_DEG}")

lls.save_current_level()
unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
log("DONE - saved")
