"""Offline check that every shipped building has collidable terrain under it
near its baked height - no UE launch.

AODBuildingManager::ComputeBuildingZOffset re-plants each building on the
lowest ground trace under its footprint corners. Buildings sink when that
trace lands on something other than terrain: the 2026-10-16 SF Russian Hill
bug (minDeltaCm=-9380) was traces falling through not-yet-cooked terrain
collision onto the level's sea-level ocean plane, 94 m under Hyde St. This
replays the placement offline: for each building in the active area's
buildings_*.bin, sample the near (has_collision) terrain tiles under every
base corner and fail if a corner has no collidable terrain (a trace there
falls through) or the offset the game would apply is beyond any real slope
(a datum/offset mismatch between building and terrain data).

Run:  OD_AREA=sf_russian_hill python pipeline/tests/test_building_ground.py
"""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bin_tile_io import parse_terrain_tile_bin
from building_roofs import read_tile
from export_building_mesh_bin import OUT_DIR
from road_mesh_common import sample_mesh_heights
from test_areas import AREA_NAME

# Baked base Z is the nearest road station's elevation, so on a steep street
# the true offset is legitimately tens of metres (Russian Hill: ~23 m).
# The sea-plane bug was -94 m; a datum slip is the whole origin height.
MAX_ABS_OFFSET_CM = 4000.0
EMBED_MARGIN_CM = 5.0          # AODBuildingManager::EmbedMarginCm
CORNER_PIT_MAX_DROP_CM = 5000.0  # AODBuildingManager::CornerPitMaxDropCm


def placement_offsets(masks_dir=OUT_DIR):
    """(offsets_cm, n_off_terrain) as ComputeBuildingZOffset would get them
    if every trace hit collidable terrain."""
    pos, tris, n = [], [], 0
    for p in masks_dir.glob(f"terrain_{AREA_NAME}_test_tile_*.bin"):
        t = parse_terrain_tile_bin(p)
        if t and t["has_collision"]:
            tris.append(np.array(t["tris"]).reshape(-1, 3) + n)
            pos.append(np.array(t["verts"], dtype=np.float64).reshape(-1, 3))
            n += len(pos[-1])
    paths = list(masks_dir.glob(f"buildings_{AREA_NAME}_test_tile_*.bin"))
    assert paths and pos, f"no {AREA_NAME} building/collision-terrain tiles in {masks_dir}"
    pos, tris = np.concatenate(pos), np.concatenate(tris)

    rings = []   # (base_z, base-ring corners) per building, the C++ base-ring rule
    for p in paths:
        for b in read_tile(p):
            v = np.array(b["verts"])
            base = v[:, 2].min()
            rings.append((base, v[np.abs(v[:, 2] - base) <= 2.0]))
    xy = np.concatenate([c[:, :2] for _, c in rings])   # one batched sample, not one per building
    all_ground = np.split(sample_mesh_heights(pos, tris, xy[:, 0], xy[:, 1]),
                          np.cumsum([len(c) for _, c in rings])[:-1])

    offsets, off_terrain = [], 0
    for (base, _), ground in zip(rings, all_ground):
        if np.isnan(ground).any():
            off_terrain += 1
            continue
        ground = ground[ground >= ground.max() - CORNER_PIT_MAX_DROP_CM]
        offsets.append(ground.min() - EMBED_MARGIN_CM - base)
    return np.array(offsets), off_terrain


def test_buildings_sit_on_collidable_terrain():
    offsets, off_terrain = placement_offsets()
    print(f"{len(offsets)} buildings, offset cm min={offsets.min():.1f} max={offsets.max():.1f}, "
          f"{off_terrain} with a corner off collidable terrain")
    assert off_terrain == 0
    assert np.abs(offsets).max() <= MAX_ABS_OFFSET_CM


if __name__ == "__main__":
    test_buildings_sit_on_collidable_terrain()
