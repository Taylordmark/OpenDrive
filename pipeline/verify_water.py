"""Offline check of the baked water (PLAN-REALISM Phase 4) - no UE launch.

  * tiles with water, split by feature kind (lake / ribbon / sea)
  * islands: every inner member of a lake multipolygon (osm_water.json
    "holes") must NOT be covered by a baked lake mesh - target 0
  * sea sanity: road stations on dry land (elev > 1 m, not a bridge or
    tunnel) that a sea mesh covers - a flipped land/sea split floods the
    whole island, so this is the coastline classifier's check - target 0

Usage:  python verify_water.py        (exit 1 on a failed check)
"""

import json
import sys
from pathlib import Path

import numpy as np
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

from build_terrain_mesh_tiles import OUT_DIR, WATER_TILE_GLOB, _TILE_XY_RE
from build_water_tiles import KIND_LAKE, KIND_RIBBON, KIND_SEA, RAW_DIR, STATIONS_PATH
from test_areas import AREA_NAME, to_local
from test_water_tiles import parse_tile


def covered(tris_xy, px, py):
    """Bool per point: inside any of the (T,3,2) triangles."""
    if len(tris_xy) == 0 or len(px) == 0:
        return np.zeros(len(px), bool)
    tree = shapely.STRtree(shapely.polygons(np.concatenate([tris_xy, tris_xy[:, :1]], axis=1)))
    hit = tree.query(shapely.points(px, py), predicate="intersects")[0]
    out = np.zeros(len(px), bool)
    out[hit] = True
    return out


def main():
    flags = {}
    fp = OUT_DIR / f"water_{AREA_NAME}_test_ribbon_flags.json"
    if fp.is_file():
        flags = json.loads(fp.read_text())
    tris_by_kind = {KIND_LAKE: [], KIND_RIBBON: [], KIND_SEA: []}
    tiles_by_kind = {k: 0 for k in tris_by_kind}
    paths = sorted(OUT_DIR.glob(WATER_TILE_GLOB))
    for p in paths:
        m = _TILE_XY_RE.search(p.name)
        tf = flags.get(f"{m.group(1)},{m.group(2)}", [])
        kinds = set()
        for i, (verts, _uvs, tris) in enumerate(parse_tile(p.read_bytes())[1]):
            k = int(tf[i]) if i < len(tf) else KIND_LAKE
            if k not in tris_by_kind:
                continue   # waterfall sheets / mist cards: not surfaces (checks/verify_river_flow.py)
            kinds.add(k)
            tris_by_kind[k].append(verts[:, :2].astype(np.float64)[tris.astype(np.int64)])
        for k in kinds:
            tiles_by_kind[k] += 1
    tris_by_kind = {k: (np.concatenate(v) if v else np.zeros((0, 3, 2))) for k, v in tris_by_kind.items()}
    print(f"[TILES] {len(paths)} water tile(s): {tiles_by_kind[KIND_LAKE]} with lakes, "
          f"{tiles_by_kind[KIND_RIBBON]} with river ribbons, {tiles_by_kind[KIND_SEA]} with sea")
    ok = True

    # islands
    holes = []
    wp = RAW_DIR / "osm_water.json"
    for poly in (json.loads(wp.read_text()) if wp.is_file() else []):
        for h in poly.get("holes", []):
            e, n = to_local(np.array([lon for _, lon in h]), np.array([lat for lat, _ in h]))
            if len(e) >= 3:
                rp = shapely.Polygon(np.c_[e, n]).buffer(0).representative_point()
                holes.append((rp.x * 100.0, -rp.y * 100.0))
    holes = np.array(holes).reshape(-1, 2)
    n_cov = int(covered(tris_by_kind[KIND_LAKE], holes[:, 0], holes[:, 1]).sum())
    print(f"[ISLANDS] {len(holes)} lake island(s) in OSM, {n_cov} still covered by lake water")
    ok &= n_cov == 0

    # sea over dry road
    if len(tris_by_kind[KIND_SEA]):
        px, py = [], []
        for seg in json.loads(Path(STATIONS_PATH).read_text())["segments"]:
            struct = np.array(seg.get("is_struct", [False] * len(seg["east_m"])), bool)
            dry = (np.array(seg["elev_m"]) > 1.0) & ~struct
            px.append(np.array(seg["east_m"])[dry] * 100.0)
            py.append(-np.array(seg["north_m"])[dry] * 100.0)
        px, py = np.concatenate(px)[::3], np.concatenate(py)[::3]
        wet = covered(tris_by_kind[KIND_SEA], px, py)
        print(f"[SEA] {wet.sum()} of {len(px)} sampled dry road station(s) under a sea mesh"
              + (f", e.g. ({px[wet][0] / 100:.0f}, {-py[wet][0] / 100:.0f}) m" if wet.any() else ""))
        ok &= not wet.any()

    print("PASS [WATER]" if ok else "FAIL [WATER]")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
