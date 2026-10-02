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
     <= 0.0 + eps somewhere (both pavement edges hit), every row's pavement
     columns sit at u=0/1, and the wrap (lip/underside) columns stay clamped
     inside [0, 1] (plain asphalt, no gravel shoulder).
  3. v0 is monotonically non-decreasing along the centerline (station row
     to station row - each row emits ROAD_PROFILE_COLS verts at the same v, so this
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

from road_mesh_common import ROAD_PAVE_COLS, ROAD_PROFILE_COLS
from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
MASKS = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks"
MASKS_V2 = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "MasksV2"

GEN_ID_BYTES = 16
N_PROF = ROAD_PROFILE_COLS   # road_mesh_common.build_road_chunks' fixed cross-section
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




def strip_pad_verts(chunk):
    """experimental_junction_pads.py patches the shipped ODR2 tiles AFTER
    export: pad discs are standalone fan chunks OR get their verts APPENDED
    to an existing ribbon chunk (merge_chunk_into's bridge-triangle merge),
    so a patched chunk's vcount/%4 alignment and per-row column layout no
    longer describe the whole vertex array. Ribbon verts always sit at
    u0 = 0 or 1 (every profile offset is >= the half-width, clamped), pad
    verts strictly inside (_pad_uv's marking-free band; it replaced the old
    constant PAD_UV 2026-09-07); strip those and check only the ribbon part.
    Returns a filtered chunk, or None if nothing but pad verts remain."""
    us = chunk["uvs"][0::2]
    vs = chunk["uvs"][1::2]
    keep = [i for i in range(len(us))
            if not (U_EPS < us[i] < 1.0 - U_EPS)]
    if not keep:
        return None
    if len(keep) == len(us):
        return chunk
    out = dict(chunk)
    out["vcount"] = len(keep)
    out["uvs"] = [c for i in keep for c in (us[i], vs[i])]
    if "uv1" in chunk:
        u1 = chunk["uv1"][0::2]
        v1 = chunk["uv1"][1::2]
        out["uv1"] = [c for i in keep for c in (u1[i], v1[i])]
    return out


def check_uv0(chunk, path, errors):
    vcount = chunk["vcount"]
    uvs = chunk["uvs"]
    us = uvs[0::2]
    vs = uvs[1::2]
    if len(us) != vcount:
        errors.append(f"{path.name}: uv0 count {len(us)} != vcount {vcount}")
        return
    # pavement edges: at least one vertex within U_EPS of 0.0 and one within
    # U_EPS of 1.0 (the ROAD_PAVE_COLS of every station row).
    if not (min(us) <= U_EPS):
        errors.append(f"{path.name}: u0 never reaches left pavement edge (min={min(us):.4f})")
    if not (max(us) >= 1.0 - U_EPS):
        errors.append(f"{path.name}: u0 never reaches right pavement edge (max={max(us):.4f})")
    # pavement columns at u=0/1; wrap columns clamped inside [0,1]
    if vcount % N_PROF != 0:
        errors.append(f"{path.name}: vcount {vcount} not a multiple of N_PROF={N_PROF}")
    else:
        n_rows = vcount // N_PROF
        for row in range(n_rows):
            base = row * N_PROF
            row_u = us[base:base + N_PROF]
            pl, pr = (row_u[c] for c in ROAD_PAVE_COLS)
            if abs(pl) > U_EPS or abs(pr - 1.0) > U_EPS:
                errors.append(f"{path.name} row {row}: pavement u0=({pl:.4f},{pr:.4f}) not (0,1)")
            if min(row_u) < -U_EPS or max(row_u) > 1.0 + U_EPS:
                errors.append(f"{path.name} row {row}: wrap u0 outside [0,1] ({min(row_u):.4f}..{max(row_u):.4f})")
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
    # Per-station width (2026-09-06, lanes= tags): uv1.x may now vary ALONG
    # a chunk, but only smoothly (WIDTH_SMOOTH_SIGMA_M gaussian over 5 m
    # stations bounds any legal row-to-row step well under this), and the
    # verts of one station row must still share one half-width exactly.
    MAX_ROW_STEP_M = 2.0
    if vcount % N_PROF == 0:
        n_rows = vcount // N_PROF
        prev = None
        for row in range(n_rows):
            base = row * N_PROF
            row_ws = half_widths[base:base + N_PROF]
            if max(row_ws) - min(row_ws) > 1e-3:
                errors.append(f"{path.name} row {row}: uv1.x differs within one "
                              f"station row ({min(row_ws):.3f}..{max(row_ws):.3f})")
                break
            if prev is not None and abs(row_ws[0] - prev) > MAX_ROW_STEP_M:
                errors.append(f"{path.name} row {row}: uv1.x jumps {prev:.3f} -> "
                              f"{row_ws[0]:.3f} m between adjacent stations")
                break
            prev = row_ws[0]
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
            chunk = strip_pad_verts(chunk)
            if chunk is None:
                continue   # pure junction-pad fan disc - no ribbon to check
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
            chunk = strip_pad_verts(chunk)
            if chunk is None:
                continue   # pure junction-pad fan disc - no ribbon to check
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

    print("PASS - uv0 pavement-edge/wrap-clamp/monotonic-v checks and "
          "(ODR3) uv1/road_class_id/lane_count sanity checks all passed.")


if __name__ == "__main__":
    main()
