"""Bakes an elevation reference grid for AODForestManager's tree ground
truth. Ground-truth height comes from the SHIPPED terrain_*.bin tiles on
disk (2026-10-05; was an in-memory build_terrain_mesh_tiles.build_tile()
rebuild, which missed experimental_junction_pads.py's terrain re-carve) - so
a tree's baked height always matches what's actually rendered underfoot.
Must run after every terrain write: rebuild_area.py runs it after the
terrain bake, and experimental_junction_pads.py re-runs it after its own.

Rewritten 2026-07-25 for the Arc 7.0 pivot: the previous version sampled
build_terrain_tiles.py's old live-streaming-era z/x/y quantized-mesh tile
pyramid, which no longer exists - the live-streaming plugin and pipeline
were removed entirely on 2026-07-23 in favor of
build_terrain_mesh_tiles.py's world-space ODT1 tiles. Nobody re-ran this
bake against the new system, so elev_<AREA>_test.bin sat stale for days
(last built 2026-07-20, against terrain that was deleted and replaced
2026-07-24) - every tree's baked height belonged to a terrain surface that
no longer existed, which is why trees were found floating far above the
actual ground (caught live 2026-07-25). Same fan-candidate/tile-grouping
approach as before, just pointed at the new tiling scheme (world-space
tx/ty tiles, near/far classified the same way classify_tiles() does)
instead of geo-referenced z/x/y ones.

Candidates are grouped by tile so each tile file is only read once.

Candidate points are generated as cross-section fans (perpendicular to the
route at intervals along it), not a full grid over the route's bounding
box - see FOREST_RADIUS_M/LATERAL_SPACING_M below.

Usage:  python build_forest_elevation.py
    (needs data/road_tiles/<AREA_NAME>_test/road_stations.json)

Writes  OpenDriveUE/Content/OpenDrive/Masks/elev_<AREA_NAME>_test.bin
        'ODE1' + uint32 N (LE) + N * (float32 X_cm, float32 Y_cm, float32 Z_cm)
"""

import json
import struct
from collections import defaultdict
from pathlib import Path

import numpy as np

import build_terrain_mesh_tiles as btmt
from bin_tile_io import parse_terrain_tile_bin
from road_mesh_common import sample_mesh_heights
from test_areas import AREA_NAME, owns

STATION_STRIDE = 6        # every 6th 5 m station = 30 m along-route spacing
LATERAL_SPACING_M = 30.0
FOREST_RADIUS_M = 550.0   # AODForestManager's SpawnRadius (480 m default)
                           # + margin, so a future SpawnRadius bump doesn't
                           # silently run past this grid's coverage

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"


def fan_points(east, north):
    """Cross-section fan points around one segment's centerline - a point
    every LATERAL_SPACING_M out to +/-FOREST_RADIUS_M, perpendicular to the
    route's local tangent, at every STATION_STRIDE-th station."""
    idx = np.arange(0, len(east), STATION_STRIDE)
    idx = idx[(idx > 0) & (idx < len(east) - 1)]   # need i-1/i+1 for the tangent
    if len(idx) == 0:
        return np.empty(0), np.empty(0)
    tx = east[idx + 1] - east[idx - 1]
    ty = north[idx + 1] - north[idx - 1]
    tlen = np.hypot(tx, ty)
    tlen[tlen < 1e-6] = 1.0
    px, py = -ty / tlen, tx / tlen   # unit perpendicular

    offsets = np.arange(-FOREST_RADIUS_M, FOREST_RADIUS_M + 1e-6, LATERAL_SPACING_M)
    out_e = (east[idx][:, None] + px[:, None] * offsets[None, :]).ravel()
    out_n = (north[idx][:, None] + py[:, None] * offsets[None, :]).ravel()
    return out_e, out_n


def main():
    # fan_points needs each segment's own contiguous east/north arrays for
    # its tangent math (a pre-concatenated array would break the i-1/i+1
    # tangent lookup at segment seams), so segments are read directly.
    data = json.loads(STATIONS_PATH.read_text())
    all_e, all_n = [], []
    for seg in data["segments"]:
        fe, fn = fan_points(np.array(seg["east_m"]), np.array(seg["north_m"]))
        all_e.append(fe)
        all_n.append(fn)
    east = np.concatenate(all_e)
    north = np.concatenate(all_n)
    print(f"{len(east)} candidate ground-truth point(s) (fanned along the corridor)")

    x_cm = east * 100.0
    y_cm = -north * 100.0
    # the grid's extent is the forest's ownership at runtime, so it ends
    # where the area's owned tiles do (2026-10-05, step 3)
    keep = owns(x_cm, y_cm)
    x_cm, y_cm = x_cm[keep], y_cm[keep]

    tile_cm = btmt.TERRAIN_TILE_SIZE_CM
    tx_all = np.floor(x_cm / tile_cm).astype(int)
    ty_all = np.floor(y_cm / tile_cm).astype(int)

    tiles = defaultdict(list)   # (tx,ty) -> [candidate index, ...]
    for i in range(len(x_cm)):
        tiles[(int(tx_all[i]), int(ty_all[i]))].append(i)
    print(f"{len(tiles)} distinct terrain tile(s) touched by the fan")

    # Sample the SHIPPED terrain tiles on disk, not an in-memory build_tile()
    # rebuild (2026-10-05): experimental_junction_pads.py re-carves terrain
    # tiles around its pads after the main bake, and a rebuild from
    # road_stations.json can't see that - trees sat 7-31 m off the ground
    # 37-95 m from pads. Reading what's shipped is also exactly what
    # verify_forest_elevation_fit.py checks against.
    elev_cm = np.full(len(x_cm), np.nan, dtype=np.float64)
    n_miss = 0
    for n_done, ((tx, ty), idxs) in enumerate(tiles.items(), 1):
        idxs = np.array(idxs)
        parsed = parse_terrain_tile_bin(OUT_DIR / f"terrain_{AREA_NAME}_test_tile_{tx}_{ty}.bin")
        if parsed is None:
            n_miss += len(idxs)
            continue
        positions = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
        triangles = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
        hs = sample_mesh_heights(positions, triangles, x_cm[idxs], y_cm[idxs])
        n_miss += int(np.isnan(hs).sum())
        elev_cm[idxs] = hs
        if n_done % 20 == 0:
            print(f"  {n_done}/{len(tiles)} tile(s) sampled")

    if n_miss:
        print(f"WARNING: {n_miss} point(s) had no built tile or fell outside "
              f"their tile's triangles (edge-of-tile floating point slop) - "
              f"filling from the nearest valid candidate; check before "
              f"trusting this file if that count is large")
        valid = np.where(~np.isnan(elev_cm))[0]
        missing = np.where(np.isnan(elev_cm))[0]
        if len(valid) and len(missing):
            for i in missing:
                d2 = (x_cm[valid] - x_cm[i]) ** 2 + (y_cm[valid] - y_cm[i]) ** 2
                elev_cm[i] = elev_cm[valid[np.argmin(d2)]]

    # tile vertex Z is already the final UE-frame Z (road_ue_z_m
    # bakes the origin-height shift in at build time - no separate
    # ORIGIN_HEIGHT_M subtraction needed here, unlike the old live-streaming
    # version where a georeference actor applied that shift at
    # runtime instead).
    x_out = x_cm.astype(np.float32)
    y_out = y_cm.astype(np.float32)
    z_out = elev_cm.astype(np.float32)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"elev_{AREA_NAME}_test.bin"
    with open(out_path, "wb") as f:
        f.write(b"ODE1")
        f.write(struct.pack("<I", len(x_out)))
        for i in range(len(x_out)):
            f.write(struct.pack("<fff", float(x_out[i]), float(y_out[i]), float(z_out[i])))
    print(f"wrote {out_path} ({len(x_out)} points, {8 + len(x_out) * 12} bytes)")


if __name__ == "__main__":
    main()
