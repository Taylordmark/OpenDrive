"""Read-only Python parsers for the ODT1/ODR2 binary sidecar tile formats
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
"""

import struct
from pathlib import Path

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


def parse_terrain_tile_bin(path: Path):
    """Mirrors AODTerrainManager::ParseTerrainTileFile exactly (ODTerrainManager.cpp).

    Returns a dict {generation_id, has_collision, verts (flat list of
    (x_cm,y_cm,z_cm) tuples), uvs, tris (flat list of (i0,i1,i2) tuples)} or
    None if the file is missing, too short, or fails the magic check -
    same "just can't verify" semantics as read_generation_id_header above.
    """
    try:
        data = Path(path).read_bytes()
    except OSError:
        return None

    header_min = MAGIC_BYTES + GENERATION_ID_BYTES + 9   # +1 has_collision, +4 vcount, +4 tcount
    if len(data) < header_min or data[:MAGIC_BYTES] != b"ODT1":
        return None

    off = MAGIC_BYTES
    gen_id = data[off:off + GENERATION_ID_BYTES].decode("ascii", errors="backslashreplace")
    off += GENERATION_ID_BYTES

    has_collision = data[off] != 0
    off += 1
    vcount, tcount = struct.unpack_from("<II", data, off)
    off += 8

    need = vcount * 12 + vcount * 8 + tcount * 12
    if len(data) - off < need:
        return None

    verts = struct.unpack_from(f"<{vcount * 3}f", data, off)
    off += vcount * 12
    uvs = struct.unpack_from(f"<{vcount * 2}f", data, off)
    off += vcount * 8
    tris = struct.unpack_from(f"<{tcount * 3}I", data, off)

    return {
        "generation_id": gen_id,
        "has_collision": has_collision,
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
