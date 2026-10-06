"""Km of road per surface class (paved/gravel/dirt) for every area with a
road_stations.json - PLAN-REALISM Phase 2's check. Uses the same
road_mesh_common.unpaved_stations() classing + short-run absorb as the bake.

Run:  python pipeline/checks/surface_km.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "pipeline"))
from road_mesh_common import SURFACE_CLASS_NAMES, unpaved_stations

for path in sorted((ROOT / "data" / "road_tiles").glob("*_test/road_stations.json")):
    km = [0.0] * len(SURFACE_CLASS_NAMES)
    for seg in json.loads(path.read_text())["segments"]:
        s, surf = seg["s_m"], seg.get("surface")
        cls = unpaved_stations(surf, seg.get("highway_class")) if surf else [0] * len(s)
        for i in range(1, len(s)):
            km[cls[i - 1]] += (s[i] - s[i - 1]) / 1000.0
    area = path.parent.name.removesuffix("_test")
    print(f"{area:22s} " + "  ".join(f"{n} {k:7.1f} km" for n, k in zip(SURFACE_CLASS_NAMES, km)))
