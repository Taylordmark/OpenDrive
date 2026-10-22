"""Python parsers for the ODT1/ODT2/ODR2 binary sidecar tile formats
that ship to OpenDriveUE/Content/OpenDrive/Masks/ and that AODTerrainManager
/ AODRoadManager parse at runtime (see ODTerrainManager.cpp's
ParseTerrainTileFile and ODRoadManager.cpp's ParseRoadTileFile - this module
mirrors both byte-for-byte, deliberately, so a verify script can check the
ACTUAL bytes any player's game would read rather than an in-memory rebuild
that never touches disk. AUDIT-RELIABILITY.md SS4.1/SS4.2.

Every sidecar format sharing this project's magic+LE-header+payload
convention also shares one property useful on its own: right after the
4-byte magic comes a fixed 16-byte ASCII-hex generation id
(pipeline/generation_id.py). read_generation_id_header() reads just that much
of any tile file (terrain/road/wall/barrier/building alike) without knowing
the rest of that format's payload layout - cheap enough to sweep every file
in Masks/ for the mixed-generation check (AUDIT-RELIABILITY.md SS2) even for
formats this module doesn't otherwise parse.

Kept separate from build_terrain_mesh_tiles.py / export_road_mesh_bin.py on
purpose: those modules build/export FRESH geometry (and pull in
requests/pymartini for terrain); this one only reads bytes that already
exist on disk, so verify_terrain_fit.py and verify_road_fit.py can both
depend on it without any of the bake-side machinery or network calls.

(2026-10-21) The one exception: write_terrain_tile_bin(), the terrain tile
writer itself (ODT2, ODT1 fallback), lives here next to its parser so the
bake, experimental_junction_pads' re-carve and build_horizon all write
through one function and test_odt2.py can round-trip without importing the
bake. build_terrain_mesh_tiles.write_tile_bin is this function.
"""

import struct
import zlib
from pathlib import Path

import numpy as np

from generation_id import GENERATION_ID_BYTES

MAGIC_BYTES = 4


def read_generation_id_header(path: Path):
    """Reads just the magic + generation-id header of any sidecar tile file
    (any of ODT1/ODR2/ODS1/ODW3/ODU2/...). Returns (magic_str, gen_id_str)
    or None if the file is missing/too short/unreadable - callers treat that
    the same as "can't verify this tile", not a hard error, matching the
    runtime's own missing-file tolerance."""
    try:
        with open(path, "rb") as f:
            head = f.read(MAGIC_BYTES + GENERATION_ID_BYTES)
    except OSError:
        return None
    if len(head) < MAGIC_BYTES + GENERATION_ID_BYTES:
        return None
    magic = head[:MAGIC_BYTES].decode("ascii", errors="backslashreplace")
    gen_id = head[MAGIC_BYTES:MAGIC_BYTES + GENERATION_ID_BYTES].decode("ascii", errors="backslashreplace")
    return magic, gen_id


ODT2_UV_TOL = 1e-4   # max |stored uv - derived uv| for ODT2 to be lossless enough
_ODT2_HEAD = "<BBIIdddI"   # has_collision, flags, V, T, u0_cm, v0_cm, uv_size_cm, Z
_odt1_fallback_printed = False


def odt2_uvs(xy, u0_cm, v0_cm, uv_size_cm):
    """ODT2's derived UVs: ((x - u0) / size, (y - v0) / size) in double from
    the float32 x/y, stored as float32 - exactly what the runtime computes."""
    xy = np.asarray(xy, dtype=np.float64)
    return np.stack([(xy[:, 0] - u0_cm) / uv_size_cm,
                     (xy[:, 1] - v0_cm) / uv_size_cm], axis=-1).astype(np.float32)


def write_terrain_tile_bin(path, tile, gen_id_bytes):
    """Writes one terrain tile (format: build_terrain_mesh_tiles.py's module
    docstring). tile = {has_collision, verts Vx3, uvs Vx2, tris Tx3[,
    uv_frame (u0_cm, v0_cm, uv_size_cm)]}. (2026-10-21) ODT2 when uv_frame
    is given and the tile's own UVs are derivable from it within ODT2_UV_TOL,
    else ODT1 as before (printed once per process)."""
    global _odt1_fallback_printed
    verts = np.ascontiguousarray(tile["verts"], dtype="<f4").reshape(-1, 3)
    uvs = np.asarray(tile["uvs"], dtype=np.float32).reshape(-1, 2)
    tris = np.asarray(tile["tris"]).reshape(-1, 3)
    frame = tile.get("uv_frame")
    ok = frame is not None and (len(verts) == 0 or float(
        np.abs(odt2_uvs(verts[:, :2], *frame) - uvs).max()) <= ODT2_UV_TOL)
    with open(path, "wb") as f:
        if ok:
            small = len(verts) <= 65536
            payload = verts.tobytes() + tris.astype("<u2" if small else "<u4").tobytes()
            z = zlib.compress(payload, 6)
            f.write(b"ODT2")
            f.write(gen_id_bytes)
            f.write(struct.pack(_ODT2_HEAD, 1 if tile["has_collision"] else 0, 1 if small else 0,
                                len(verts), len(tris), *map(float, frame), len(z)))
            f.write(z)
            return
        if not _odt1_fallback_printed:
            _odt1_fallback_printed = True
            print(f"write_terrain_tile_bin: {Path(path).name}: UVs not derivable "
                  f"{'(no uv_frame)' if frame is None else 'from its uv_frame'} - wrote ODT1")
        f.write(b"ODT1")
        f.write(gen_id_bytes)
        f.write(struct.pack("<BII", 1 if tile["has_collision"] else 0, len(verts), len(tris)))
        f.write(verts.tobytes())
        f.write(uvs.astype("<f4").tobytes())
        f.write(tris.astype("<u4").tobytes())


def parse_terrain_tile_bin(path: Path):
    """Mirrors AODTerrainManager::ParseTerrainTileFile exactly (ODTerrainManager.cpp).

    Returns a dict {generation_id, has_collision, verts (flat list of
    (x_cm,y_cm,z_cm) tuples), uvs, tris (flat list of (i0,i1,i2) tuples)} or
    None if the file is missing, too short, or fails the magic check -
    same "just can't verify" semantics as read_generation_id_header above.
    (2026-10-21) Accepts ODT2 (zlib payload, derived UVs) and legacy ODT1;
    either comes back in the same shape.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None

    magic = data[:MAGIC_BYTES]
    off = MAGIC_BYTES + GENERATION_ID_BYTES
    if magic == b"ODT2":
        if len(data) < off + struct.calcsize(_ODT2_HEAD):
            return None
        has_c, flags, vcount, tcount, u0, v0, size, zlen = struct.unpack_from(_ODT2_HEAD, data, off)
        off += struct.calcsize(_ODT2_HEAD)
        try:
            body = zlib.decompress(data[off:off + zlen])
        except zlib.error:
            return None
        isz = 2 if flags & 1 else 4
        if len(body) != vcount * 12 + tcount * 3 * isz:
            return None
        verts = struct.unpack_from(f"<{vcount * 3}f", body, 0)
        tris = struct.unpack_from(f"<{tcount * 3}{'H' if isz == 2 else 'I'}", body, vcount * 12)
        xy = np.frombuffer(body, "<f4", vcount * 3).reshape(-1, 3)[:, :2]
        uvs = tuple(odt2_uvs(xy, u0, v0, size).reshape(-1).tolist())
    elif magic == b"ODT1":
        header_min = off + 9   # +1 has_collision, +4 vcount, +4 tcount
        if len(data) < header_min:
            return None
        has_c = data[off]
        vcount, tcount = struct.unpack_from("<II", data, off + 1)
        off += 9

        need = vcount * 12 + vcount * 8 + tcount * 12
        if len(data) - off < need:
            return None

        verts = struct.unpack_from(f"<{vcount * 3}f", data, off)
        off += vcount * 12
        uvs = struct.unpack_from(f"<{vcount * 2}f", data, off)
        off += vcount * 8
        tris = struct.unpack_from(f"<{tcount * 3}I", data, off)
    else:
        return None

    return {
        "generation_id": data[MAGIC_BYTES:MAGIC_BYTES + GENERATION_ID_BYTES].decode("ascii", errors="backslashreplace"),
        "has_collision": has_c != 0,
        "verts": verts,      # flat, 3 per vertex
        "uvs": uvs,           # flat, 2 per vertex
        "tris": tris,         # flat, 3 per triangle
        "vcount": vcount,
        "tcount": tcount,
    }


def parse_road_tile_bin(path: Path):
    """Mirrors AODRoadManager::ParseRoadTileFile exactly (ODRoadManager.cpp).

    Returns a dict {generation_id, chunks: [{oneway, verts, uvs, tris,
    vcount, tcount}, ...]} or None on missing/short/bad-magic file."""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None

    header_min = MAGIC_BYTES + GENERATION_ID_BYTES + 4   # +4 chunk_count
    if len(data) < header_min or data[:MAGIC_BYTES] != b"ODR2":
        return None

    off = MAGIC_BYTES
    gen_id = data[off:off + GENERATION_ID_BYTES].decode("ascii", errors="backslashreplace")
    off += GENERATION_ID_BYTES

    (chunk_count,) = struct.unpack_from("<I", data, off)
    off += 4

    chunks = []
    for _ in range(chunk_count):
        if len(data) - off < 9:
            return None
        vcount, tcount = struct.unpack_from("<II", data, off)
        off += 8
        oneway = data[off] != 0
        off += 1

        need = vcount * 12 + vcount * 8 + tcount * 12
        if len(data) - off < need:
            return None

        verts = struct.unpack_from(f"<{vcount * 3}f", data, off)
        off += vcount * 12
        uvs = struct.unpack_from(f"<{vcount * 2}f", data, off)
        off += vcount * 8
        tris = struct.unpack_from(f"<{tcount * 3}I", data, off)
        off += tcount * 12

        chunks.append({
            "oneway": oneway,
            "verts": verts,
            "uvs": uvs,
            "tris": tris,
            "vcount": vcount,
            "tcount": tcount,
        })

    return {"generation_id": gen_id, "chunks": chunks}
