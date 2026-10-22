"""World map for the front-end (2026-10-13): Open Drive / Track Select start
on a globe of every area (AODMenuGlobe, in-engine since 2026-10-14; fed
this file by AODMainMenu), picking one zooms into it.

Usage:  python pipeline/bake_world_map.py
Reads   data/raw/ne_10m_coastline.geojson (fetch_climate.py downloads it),
        data/raw/ne_50m_admin_0_boundary_lines_land.geojson,
        data/raw/ne_50m_admin_1_states_provinces_lines.geojson
        (Natural Earth, public domain, github.com/nvkelso/natural-earth-vector),
        test_areas.AREAS
Writes  OpenDriveUE/Content/OpenDrive/Masks/worldmap.json
          {"coast": [[lon0, lat0, lon1, lat1, ...], ...], "borders": [...], "states": [...],
           "areas": {"<area>": {"ll": [lon, lat]}}}
        Lines clipped to North America + Hawaii (the globe's texture carries
        the rest). (An area's Turnouts live in menu_spots.json since 2026-10-14.)
"""
import json
import sys
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


def rank(key):
    """Identical-bbox tie-break: the earlier AREAS entry wins."""
    return list(AREAS).index(key)


def inside(a, b):
    """bbox_snwe a lies within b."""
    return a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]


def main():
    areas = {}
    for key, cfg in AREAS.items():
        s, w, n, e = cfg["bbox_snwe"]
        areas[key] = {"ll": [round((w + e) / 2, 4), round((s + n) / 2, 4)]}
        # (2026-10-21) a drawn area (bake_request.py) is one area: one pin, at its
        # start, shown even inside a hand area's bbox (it bakes its own copy)
        if cfg.get("drawn"):
            areas[key]["ll"] = [round(v, 4) for v in cfg["start_ll"]]
            continue
        # (2026-10-14) a dev/test variant inside another area's bbox gets no globe pin
        if any(o != key and not c.get("drawn") and inside(cfg["bbox_snwe"], c["bbox_snwe"]) and
               (not inside(c["bbox_snwe"], cfg["bbox_snwe"]) or rank(o) < rank(key))
               for o, c in AREAS.items()):
            areas[key]["hidden"] = True
    out = {"coast": lines("ne_10m_coastline"),
           "borders": lines("ne_50m_admin_0_boundary_lines_land"),
           "states": lines("ne_50m_admin_1_states_provinces_lines"),
           "areas": areas}
    dst = MASKS / "worldmap.json"
    dst.write_text(json.dumps(out, separators=(",", ":")))
    pts = sum(len(l) // 2 for k in ("coast", "borders", "states") for l in out[k])
    print(f"wrote {dst}: {pts} line points, {len(areas)} areas, {dst.stat().st_size / 1024:.0f} KB")
    return out


if __name__ == "__main__":
    main()
