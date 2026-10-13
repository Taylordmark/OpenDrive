"""World map for the front-end (2026-10-13): Open Drive / Track Select start
on a three.js globe of every area (Content/OpenDrive/WorldMap/index.html, fed
this file by AODMainMenu), clicking one zooms into it.

Usage:  python pipeline/bake_world_map.py
Reads   data/raw/ne_10m_coastline.geojson (fetch_climate.py downloads it),
        data/raw/ne_50m_admin_0_boundary_lines_land.geojson,
        data/raw/ne_50m_admin_1_states_provinces_lines.geojson
        (Natural Earth, public domain, github.com/nvkelso/natural-earth-vector),
        test_areas.AREAS, Masks/labels_<area>_test.bin
Writes  OpenDriveUE/Content/OpenDrive/Masks/worldmap.json
          {"coast": [[lon0, lat0, lon1, lat1, ...], ...], "borders": [...], "states": [...],
           "areas": {"<area>": {"ll": [lon, lat], "towns": [[name, x_cm, y_cm], ...]}}}
        Lines clipped to North America + Hawaii (the globe's texture carries
        the rest). Towns: area-local UE cm, the
        AODRoadManager::BuildTownNodes centroids (it snaps them to the road
        at spawn; these are only drawn).
"""
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path

from shapely.geometry import box, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_areas import AREAS

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

CLIP = (-170.0, 15.0, -50.0, 72.0)       # lon/lat box kept (W, S, E, N)
SIMPLIFY_DEG = 0.02                      # ~2 km - about a pixel at full-US zoom


def lines(name):
    clip = box(*CLIP)
    out = []
    for f in json.loads((RAW / f"{name}.geojson").read_text())["features"]:
        g = shape(f["geometry"]).intersection(clip).simplify(SIMPLIFY_DEG)
        for part in getattr(g, "geoms", [g]):
            if part.is_empty or part.geom_type != "LineString" or len(part.coords) < 2:
                continue
            out.append([round(v, 3) for xy in part.coords for v in xy])
    return out


def read_towns(path):
    """labels_<area>_test.bin (ODL1: magic, 16-byte generation id, uint32 N,
    N * (f32 x, y cm, u16-len utf8 road, u16-len utf8 city)) -> one centroid
    per city, sorted by name like BuildTownNodes."""
    b = path.read_bytes()
    assert b[:4] == b"ODL1", path
    n = struct.unpack_from("<I", b, 20)[0]
    p = 24
    acc = defaultdict(lambda: [0.0, 0.0, 0])
    for _ in range(n):
        x, y = struct.unpack_from("<ff", b, p)
        p += 8
        names = []
        for _ in range(2):
            ln = struct.unpack_from("<H", b, p)[0]
            names.append(b[p + 2:p + 2 + ln].decode("utf-8"))
            p += 2 + ln
        if names[1]:
            a = acc[names[1]]
            a[0] += x
            a[1] += y
            a[2] += 1
    return [[k, round(a[0] / a[2]), round(a[1] / a[2])] for k, a in sorted(acc.items())]


def main():
    areas = {}
    for key, cfg in AREAS.items():
        s, w, n, e = cfg["bbox_snwe"]
        lab = MASKS / f"labels_{key}_test.bin"
        areas[key] = {"ll": [round((w + e) / 2, 4), round((s + n) / 2, 4)],
                      "towns": read_towns(lab) if lab.exists() else []}
    out = {"coast": lines("ne_10m_coastline"),
           "borders": lines("ne_50m_admin_0_boundary_lines_land"),
           "states": lines("ne_50m_admin_1_states_provinces_lines"),
           "areas": areas}
    dst = MASKS / "worldmap.json"
    dst.write_text(json.dumps(out, separators=(",", ":")))
    pts = sum(len(l) // 2 for k in ("coast", "borders", "states") for l in out[k])
    print(f"wrote {dst}: {pts} line points, {len(areas)} areas "
          f"({sum(len(a['towns']) for a in areas.values())} towns), {dst.stat().st_size / 1024:.0f} KB")
    return out


if __name__ == "__main__":
    main()
