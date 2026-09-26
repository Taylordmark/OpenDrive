"""Direct geometric seam-continuity check across the SHIPPED terrain tile
grid (backend-only, no app launch - see memory/feedback_no_run_app_for_testing.md).

verify_terrain_fit.py's check_generation_ids() already catches the "some
tiles are from an older bake than their neighbors" failure mode (a stale
tile left on disk after a partial rebake). What it does NOT catch is a tile
boundary that disagrees in height even though every tile reports the SAME
generation id - e.g. two adjacent tiles whose independent martini
simplification (MAX_ERROR_NEAR_M/MAX_ERROR_FAR_M tolerance, see
build_terrain_mesh_tiles.py) legitimately picked different vertices along
their shared edge, or a near/far boundary where the two sides sample DEM at
different resolutions. build_seam_skirt() hides small edge cracks with a
downward curtain, but that's a visual patch, not a guarantee the visible top
surface actually agrees - and it says nothing about a real bug that produces
a LARGE mismatch.

Method: for every pair of tiles that are on-disk neighbors (right/top, so
each shared edge is checked once), parse both tiles' ACTUAL shipped mesh
(bin_tile_io.parse_terrain_tile_bin, same as verify_terrain_fit.py's shipped
check) and barycentric-sample BOTH sides at points along their shared
boundary line (road_mesh_common.sample_mesh_height, the same production
function). A healthy seam samples near-identically from either side; a
mismatch here is a real, visible crack in the world, not a proxy for one.

Usage:  python verify_terrain_seams.py
"""
import re
import sys
from pathlib import Path

import numpy as np

from bin_tile_io import parse_terrain_tile_bin
from build_terrain_mesh_tiles import TERRAIN_CARVE_DROP_M as CARVE_DROP_M  # noqa: F401 (context only)
from build_terrain_mesh_tiles import MAX_ERROR_NEAR_M
from road_mesh_common import sample_mesh_height
from test_areas import AREA_NAME, TERRAIN_TILE_SIZE_CM as TILE_CM

ROOT = Path(__file__).resolve().parent.parent
MASKS_DIR = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"

N_SAMPLES = 9          # points along each shared edge
# Two independently-simplified NEAR tiles can each miss the true surface by
# up to MAX_ERROR_NEAR_M (see build_seam_skirt's docstring, which measured
# exactly this on bit-identical DEM data) - budget both sides plus a little
# slack for float/barycentric noise.
TOL_M = 2 * MAX_ERROR_NEAR_M + 0.5

NAME_RE = re.compile(rf"^terrain_{re.escape(AREA_NAME)}_test_tile_(-?\d+)_(-?\d+)\.bin$")


def load_tile_index():
    tiles = {}
    for p in MASKS_DIR.glob(f"terrain_{AREA_NAME}_test_tile_*.bin"):
        m = NAME_RE.match(p.name)
        if m:
            tiles[(int(m.group(1)), int(m.group(2)))] = p
    return tiles


def main():
    tile_paths = load_tile_index()
    print(f"{len(tile_paths)} shipped terrain tile(s) found for {AREA_NAME}_test")
    if not tile_paths:
        print("nothing to check")
        sys.exit(1)

    cache = {}

    def get_mesh(key):
        if key not in cache:
            parsed = parse_terrain_tile_bin(tile_paths[key])
            if parsed is None:
                cache[key] = None
            else:
                positions = np.array(parsed["verts"], dtype=np.float64).reshape(-1, 3)
                triangles = np.array(parsed["tris"], dtype=np.int64).reshape(-1, 3)
                cache[key] = (positions, triangles)
        return cache[key]

    results = []  # (tx, ty, axis, max_dev, mean_dev, n_ok, n_miss)
    for (tx, ty) in sorted(tile_paths):
        for axis, nkey in ((("x"), (tx + 1, ty)), (("y"), (tx, ty + 1))):
            if nkey not in tile_paths:
                continue
            mesh_a = get_mesh((tx, ty))
            mesh_b = get_mesh(nkey)
            if mesh_a is None or mesh_b is None:
                results.append((tx, ty, axis, None, None, 0, N_SAMPLES))
                continue
            pos_a, tri_a = mesh_a
            pos_b, tri_b = mesh_b

            if axis == "x":
                edge_x = (tx + 1) * TILE_CM
                edge_ys = np.linspace(ty * TILE_CM, (ty + 1) * TILE_CM, N_SAMPLES)
                pts = [(edge_x, y) for y in edge_ys]
            else:
                edge_y = (ty + 1) * TILE_CM
                edge_xs = np.linspace(tx * TILE_CM, (tx + 1) * TILE_CM, N_SAMPLES)
                pts = [(x, edge_y) for x in edge_xs]

            devs = []
            n_miss = 0
            for (x_cm, y_cm) in pts:
                za = sample_mesh_height(pos_a, tri_a, x_cm, y_cm)
                zb = sample_mesh_height(pos_b, tri_b, x_cm, y_cm)
                if za is None or zb is None:
                    n_miss += 1
                    continue
                devs.append(abs(za - zb) / 100.0)  # cm -> m

            if devs:
                results.append((tx, ty, axis, max(devs), float(np.mean(devs)), len(devs), n_miss))
            else:
                results.append((tx, ty, axis, None, None, 0, n_miss))

    checked = [r for r in results if r[3] is not None]
    unreadable = [r for r in results if r[3] is None and r[6] < N_SAMPLES]
    all_miss = [r for r in results if r[5] == 0]

    print(f"{len(results)} shared edge(s) between on-disk tile neighbors, "
          f"{len(checked)} sampled successfully, {len(all_miss)} totally missed "
          f"(edge floating-point slop or a genuinely unreadable tile)")

    if checked:
        max_devs = np.array([r[3] for r in checked])
        print(f"seam height agreement: mean of per-edge max = {max_devs.mean():.3f}m, "
              f"worst edge = {max_devs.max():.3f}m (tolerance {TOL_M:.2f}m)")

        worst = sorted(checked, key=lambda r: -r[3])[:15]
        print(f"\nworst {len(worst)} edge(s):")
        for tx, ty, axis, max_dev, mean_dev, n_ok, n_miss in worst:
            other = (tx + 1, ty) if axis == "x" else (tx, ty + 1)
            print(f"  tile ({tx},{ty}) vs ({other[0]},{other[1]}) [{axis}-edge]: "
                  f"max={max_dev:.3f}m mean={mean_dev:.3f}m ({n_ok} pt ok, {n_miss} missed)")

        n_over = int((max_devs > TOL_M).sum())
        ok = n_over == 0
    else:
        ok = False
        n_over = 0

    print(f"\n{n_over} edge(s) over {TOL_M:.2f}m")
    if ok:
        print("PASS: every checked shipped tile boundary agrees in height "
              "within the expected simplification budget - no visible cracks.")
    else:
        print("FAIL: at least one shipped tile boundary disagrees by more than "
              "simplification alone should produce - a real seam, not just the "
              "skirt-hidden kind. Check the tiles listed above (likely a "
              "different generation id slipping past check_generation_ids, a "
              "near/far classification edge case, or a genuine carve/DEM bug).")
        sys.exit(1)


if __name__ == "__main__":
    main()
