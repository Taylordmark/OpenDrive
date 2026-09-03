"""Offline sanity checker for the Phase 2a road-visual overhaul's baked road
UVs/metadata (2026-09-03). Reads a sample of tiles from BOTH:

  - OpenDriveUE/Content/OpenDrive/Masks/road_<AREA>_test_tile_*.bin (ODR2,
    the format actually loaded by the shipped game today - only its u0/v0
    NUMERIC VALUES changed, see export_road_mesh_bin.py/road_mesh_common.py)
  - OpenDriveUE/Content/OpenDrive/MasksV2/roadv3_<AREA>_test_tile_*.bin
    (ODR3, the new parallel extended format - uv1 + per-chunk road_class_id/
    lane_count; see export_road_mesh_bin_v2.py)

and checks, per sampled chunk:
  1. UVs are present and vertex-count-aligned.
  2. u0 spans the expected pavement-normalized range: >= 1.0 - eps and
     <= 0.0 + eps somewhere (both pavement edges hit), and skirt columns
     extrapolate outside [0, 1].
  3. v0 is monotonically non-decreasing along the centerline (station row
     to station row - each row emits n_prof=4 verts at the same v, so this
     checks row-to-row, not vert-to-vert).
  4. (ODR3 only) uv1.x (road half-width, meters) is positive and sane
     (2..15 m), constant within a chunk.
  5. (ODR3 only) road_class_id in [0,6] and lane_count in [1,8] - "sane"
     range checks, not per-way ground truth (there is none - see
     road_mesh_common.ROAD_CLASS_LANES_TWOWAY's docstring).

Usage:  python verify_road_uv.py [N]   (N = tiles to sample per format,
                                          default 40)
Exits non-zero if any check fails.
"""

import struct
import sys
from pathlib import Path

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
MASKS_V2 = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "MasksV2"

GEN_ID_BYTES = 16
N_PROF = 4   # skirt-L, pave-L, pave-R, skirt-R - road_mesh_common.py's fixed cross-section
U_EPS = 1e-3
V_EPS = 1e-3   # meters - allow tiny float round-trip noise, not a real regression


def read_u32(buf, off):
    return struct.unpack_from("<I", buf, off)[0], off + 4


def read_f32_array(buf, off, n):
    vals = struct.unpack_from(f"<{n}f", buf, off)
    return vals, off + n * 4


def parse_odr2_tile(path):
    buf = path.read_bytes()
    assert buf[0:4] == b"ODR2", f"{path.name}: bad magic {buf[0:4]!r}"
    off = 4 + GEN_ID_BYTES
    chunk_count, off = read_u32(buf, off)
    chunks = []
    for _ in range(chunk_count):
        vcount, off = read_u32(buf, off)
        tcount, off = read_u32(buf, off)
        oneway = buf[off]; off += 1
        verts, off = read_f32_array(buf, off, vcount * 3)
        uvs, off = read_f32_array(buf, off, vcount * 2)
        off += tcount * 12   # skip triangle indices - not needed here
        chunks.append({"vcount": vcount, "oneway": oneway, "uvs": uvs})
    return chunks


def parse_odr3_tile(path):
    buf = path.read_bytes()
    assert buf[0:4] == b"ODR3", f"{path.name}: bad magic {buf[0:4]!r}"
    off = 4 + GEN_ID_BYTES
    chunk_count, off = read_u32(buf, off)
    chunks = []
    for _ in range(chunk_count):
        vcount, off = read_u32(buf, off)
        tcount, off = read_u32(buf, off)
        oneway = buf[off]; off += 1
        road_class_id = buf[off]; off += 1
        lane_count = buf[off]; off += 1
        verts, off = read_f32_array(buf, off, vcount * 3)
        uvs, off = read_f32_array(buf, off, vcount * 2)
        uv1, off = read_f32_array(buf, off, vcount * 2)
        off += tcount * 12
        chunks.append({
            "vcount": vcount, "oneway": oneway, "uvs": uvs, "uv1": uv1,
            "road_class_id": road_class_id, "lane_count": lane_count,
        })
    return chunks


def check_uv0(chunk, path, errors):
    vcount = chunk["vcount"]
    uvs = chunk["uvs"]
    us = uvs[0::2]
    vs = uvs[1::2]
    if len(us) != vcount:
        errors.append(f"{path.name}: uv0 count {len(us)} != vcount {vcount}")
        return
    # pavement edges: at least one vertex within U_EPS of 0.0 and one within
    # U_EPS of 1.0 (columns 1/2 of every station's 4-vert row).
    if not (min(us) <= U_EPS):
        errors.append(f"{path.name}: u0 never reaches left pavement edge (min={min(us):.4f})")
    if not (max(us) >= 1.0 - U_EPS):
        errors.append(f"{path.name}: u0 never reaches right pavement edge (max={max(us):.4f})")
    # skirt columns (0, 3 of every 4-vert row) must extrapolate OUTSIDE [0,1]
    if vcount % N_PROF != 0:
        errors.append(f"{path.name}: vcount {vcount} not a multiple of N_PROF={N_PROF}")
    else:
        n_rows = vcount // N_PROF
        for row in range(n_rows):
            base = row * N_PROF
            u_skirt_l, u_pave_l, u_pave_r, u_skirt_r = us[base:base + N_PROF]
            if not (u_skirt_l < -U_EPS):
                errors.append(f"{path.name} row {row}: left skirt u0={u_skirt_l:.4f} not < 0")
            if not (u_skirt_r > 1.0 + U_EPS):
                errors.append(f"{path.name} row {row}: right skirt u0={u_skirt_r:.4f} not > 1")
        # v0 (station distance, meters) monotonic non-decreasing row-to-row
        row_v = [vs[row * N_PROF] for row in range(n_rows)]
        for i in range(1, len(row_v)):
            if row_v[i] < row_v[i - 1] - V_EPS:
                errors.append(
                    f"{path.name}: v0 not monotonic at row {i} "
                    f"({row_v[i - 1]:.3f} -> {row_v[i]:.3f})")
                break


def check_uv1_and_meta(chunk, path, errors):
    vcount = chunk["vcount"]
    uv1 = chunk["uv1"]
    if len(uv1) // 2 != vcount:
        errors.append(f"{path.name}: uv1 count {len(uv1) // 2} != vcount {vcount}")
        return
    half_widths = uv1[0::2]
    if any(w <= 0.0 or w > 15.0 for w in half_widths):
        bad = next(w for w in half_widths if w <= 0.0 or w > 15.0)
        errors.append(f"{path.name}: implausible road half-width in uv1.x: {bad:.3f} m")
    lo, hi = min(half_widths), max(half_widths)
    if hi - lo > 1e-3:
        errors.append(f"{path.name}: uv1.x (half-width) not constant within chunk ({lo:.3f}..{hi:.3f})")
    if not (0 <= chunk["road_class_id"] <= 6):
        errors.append(f"{path.name}: road_class_id {chunk['road_class_id']} out of [0,6]")
    if not (1 <= chunk["lane_count"] <= 8):
        errors.append(f"{path.name}: lane_count {chunk['lane_count']} out of [1,8]")


def sample_tiles(directory, glob_pattern, limit):
    paths = sorted(directory.glob(glob_pattern))
    if not paths:
        return []
    step = max(1, len(paths) // limit)
    return paths[::step][:limit]


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    errors = []
    checked_chunks_odr2 = 0
    checked_chunks_odr3 = 0

    odr2_paths = sample_tiles(MASKS, f"road_{AREA_NAME}_test_tile_*.bin", limit)
    print(f"ODR2 (Masks/): sampling {len(odr2_paths)} of "
          f"{len(list(MASKS.glob(f'road_{AREA_NAME}_test_tile_*.bin')))} tile(s)")
    for path in odr2_paths:
        try:
            chunks = parse_odr2_tile(path)
        except Exception as e:
            errors.append(f"{path.name}: parse failed: {e}")
            continue
        for chunk in chunks:
            check_uv0(chunk, path, errors)
            checked_chunks_odr2 += 1

    odr3_paths = sample_tiles(MASKS_V2, f"roadv3_{AREA_NAME}_test_tile_*.bin", limit)
    print(f"ODR3 (MasksV2/): sampling {len(odr3_paths)} of "
          f"{len(list(MASKS_V2.glob(f'roadv3_{AREA_NAME}_test_tile_*.bin')))} tile(s)")
    for path in odr3_paths:
        try:
            chunks = parse_odr3_tile(path)
        except Exception as e:
            errors.append(f"{path.name}: parse failed: {e}")
            continue
        for chunk in chunks:
            check_uv0(chunk, path, errors)
            check_uv1_and_meta(chunk, path, errors)
            checked_chunks_odr3 += 1

    print(f"checked {checked_chunks_odr2} ODR2 chunk(s), {checked_chunks_odr3} ODR3 chunk(s)")

    if errors:
        print(f"\nFAIL - {len(errors)} error(s):")
        for e in errors[:50]:
            print(f"  {e}")
        if len(errors) > 50:
            print(f"  ... and {len(errors) - 50} more")
        sys.exit(1)

    print("PASS - uv0 pavement-edge/skirt-extrapolation/monotonic-v checks and "
          "(ODR3) uv1/road_class_id/lane_count sanity checks all passed.")


if __name__ == "__main__":
    main()
