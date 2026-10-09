"""Junction-pad pavement points for the forest's road exclusion (2026-10-09,
Taylor: trees standing on I-70 at Wheeler Junction). AODForestManager keeps
trees off roads by distance to road stations / centerline points, but a
junction pad's pavement reaches up to ~28 m from any station (Summit: 98% of
the pavement points outside that exclusion were pad verts). This samples
every pad triangle of the shipped ODR3 road tiles about every SAMPLE_M and
writes them as padpts_<area>_test.bin (ODC1, same point list as the
centerline file), which the forest appends to its centerline exclusion.

Run after experimental_junction_pads.py (it reads the patched tiles).

Usage:  OD_AREA=<area> python pipeline/export_pad_points_bin.py
"""
import struct
from pathlib import Path

import numpy as np

from test_areas import AREA_NAME

ROOT = Path(__file__).resolve().parent.parent
TILES = ROOT / "OpenDriveUE/Content/OpenDrive/MasksV2"
OUT = ROOT / "OpenDriveUE/Content/OpenDrive/Masks" / f"padpts_{AREA_NAME}_test.bin"
SAMPLE_M = 4.0   # well under the forest's 11.5 m PavementExclusionCm


def chunks(path):
    """ODR3 tile -> (verts (n,3) cm, uvs (n,2), tris (t,3))."""
    buf = path.read_bytes()
    off = 20
    (n,) = struct.unpack_from("<I", buf, off); off += 4
    for _ in range(n):
        vc, tc = struct.unpack_from("<II", buf, off); off += 11
        v = np.frombuffer(buf, "<f4", vc * 3, off).reshape(-1, 3); off += vc * 12
        uv = np.frombuffer(buf, "<f4", vc * 2, off).reshape(-1, 2); off += vc * 8
        off += vc * 8
        t = np.frombuffer(buf, "<u4", tc * 3, off).reshape(-1, 3); off += tc * 12
        yield v, uv, t


def pad_points(verts, uvs, tris):
    """Points every <= SAMPLE_M over the triangles made only of pad verts
    (pad verts follow the ribbon and have u0 strictly inside (0,1))."""
    u = uvs[:, 0]
    is_pad = np.zeros(len(u), bool)
    inner = np.flatnonzero((u > 1e-3) & (u < 1 - 1e-3))
    if len(inner):
        is_pad[inner[0]:] = True
    tris = tris[is_pad[tris].all(axis=1)]
    out = [verts[is_pad, :2]]
    for a, b, c in verts[tris][:, :, :2]:
        k = int(np.ceil(max(np.hypot(*(b - a)), np.hypot(*(c - b)), np.hypot(*(a - c))) / (SAMPLE_M * 100)))
        if k < 2:
            continue
        i, j = np.mgrid[0:k + 1, 0:k + 1]
        m = i + j <= k
        wa, wb = i[m] / k, j[m] / k
        out.append(a + np.outer(wa, b - a) + np.outer(wb, c - a))
    return np.concatenate(out)


def self_test():
    # 3-vert ribbon stub (u 0/1) + a 40 m pad triangle
    v = np.array([[0, 0, 0], [0, 0, 0], [0, 0, 0], [0, 0, 0], [4000, 0, 0], [0, 4000, 0]], np.float32)
    uv = np.array([[0, 0], [1, 0], [0, 0], [.5, 0], [.5, 0], [.5, 0]], np.float32)
    p = pad_points(v, uv, np.array([[0, 1, 2], [3, 4, 5]]))
    g = np.mgrid[0:4000:200, 0:4000:200].reshape(2, -1).T
    g = g[g.sum(1) <= 4000]
    d = np.hypot(*(g[:, None, :] - p[None]).transpose(2, 0, 1)).min(1)
    assert d.max() <= SAMPLE_M * 100, "every point of the pad is near a sample"


def main():
    self_test()
    pts = [pad_points(*c) for p in sorted(TILES.glob(f"roadv3_{AREA_NAME}_test_tile_*.bin")) for c in chunks(p)]
    pts = np.concatenate(pts) if pts else np.zeros((0, 2))
    # one point per 4 m cell (still well inside the 11.5 m exclusion)
    pts = pts[np.unique(np.floor(pts / 400.0).astype(np.int64), axis=0, return_index=True)[1]]
    with open(OUT, "wb") as f:
        f.write(b"ODC1")
        f.write(struct.pack("<I", len(pts)))
        f.write(pts.astype("<f4").tobytes())
    print(f"{AREA_NAME}: {len(pts)} pad point(s) -> {OUT.name}")


if __name__ == "__main__":
    main()
