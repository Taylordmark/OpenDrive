"""Cooked forest-mask sidecar (packaging prerequisite, 2026-07-18).

AODForestManager reads T_TerrainMask via Texture Source mip data, which is
editor-only - packaged builds had no forest. This writes the R channel
(forest weight) as a raw binary the manager can load anywhere:

    OpenDriveUE/Content/OpenDrive/Masks/terrain_mask.bin
    layout: 'ODM1' magic, uint32 LE resolution, res*res bytes (row-major,
    same orientation as the texture; downsampled to <=1024 like the
    runtime Source path)

Staged into the pak via DirectoriesToAlwaysStageAsUFS (DefaultGame.ini).
Rerun after any pipeline mask rebuild (build_foliage / corridor carve).
"""

import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "stages" / "tail_of_the_dragon" / "textures" / "T_TerrainMask.png"
DST = ROOT / "OpenDriveUE" / "Content" / "OpenDrive" / "Masks" / "terrain_mask.bin"

img = Image.open(SRC)
arr = np.asarray(img)
r = arr[..., 0] if arr.ndim == 3 else arr          # R = forest weight
src_res = r.shape[0]
step = max(1, src_res // 1024)                     # ~9 m/px is plenty
r = r[::step, ::step]
res = r.shape[0]
r = r[:res, :res]                                  # square, defensively

DST.parent.mkdir(parents=True, exist_ok=True)
with open(DST, "wb") as f:
    f.write(b"ODM1")
    f.write(struct.pack("<I", res))
    f.write(np.ascontiguousarray(r, dtype=np.uint8).tobytes())

print(f"wrote {DST} ({res}x{res}, from {src_res})")
sys.exit(0)
