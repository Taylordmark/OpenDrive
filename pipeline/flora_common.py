"""Shared layout constants for the card-based flora texture atlas and the
bark photo textures - imported by both build_flora_textures.py (which
renders/downloads the actual PNGs) and make_tree_meshes.py (which needs the
same UV rectangles to lay out branch-card quads). Kept in one place so the
two scripts can never drift out of sync on cell layout.

Atlas grid (T_FoliageAtlas.png, RGBA, alpha-cutout branch/leaf cards):
    col:      0               1                2                 3
    row 0:  pine_needle     spruce_needle    fir_needle      krummholz_needle
    row 1:  aspen_leaf      willow_leaf      sagebrush_leaf  grass_blade

Bark photos are single full-image tiling textures (no atlas grid) - one
material each: T_BarkConifer (pine/spruce/fir/krummholz share the one bark,
same as real Rockies conifers all reading as "dark fissured bark" at
gameplay distance), T_BarkAspen (procedural - no good CC0 pale/birch-style
match found), T_BarkWillow (willow/streamside scrub).
"""

ATLAS_COLS = 4
ATLAS_ROWS = 2
CELL_PX = 512
ATLAS_W = ATLAS_COLS * CELL_PX
ATLAS_H = ATLAS_ROWS * CELL_PX

CELLS = [
    "pine_needle", "spruce_needle", "fir_needle", "krummholz_needle",
    "aspen_leaf", "willow_leaf", "sagebrush_leaf", "grass_blade",
]


def cell_row_col(name):
    i = CELLS.index(name)
    return divmod(i, ATLAS_COLS)  # (row, col)


def cell_uv_rect(name, margin=0.08):
    """Returns (u0, u1, v0, v1) in OBJ/UE UV space (V=0 at image bottom,
    V=1 at image top - standard OBJ convention) with a small inset margin
    so bilinear filtering never bleeds a neighboring cell's alpha in."""
    row, col = cell_row_col(name)
    u0 = (col + margin) / ATLAS_COLS
    u1 = (col + 1 - margin) / ATLAS_COLS
    # row 0 is the TOP row of the image; OBJ v=1 is the top.
    v_top = 1.0 - row / ATLAS_ROWS
    v_bot = 1.0 - (row + 1) / ATLAS_ROWS
    v0 = v_bot + margin / ATLAS_ROWS
    v1 = v_top - margin / ATLAS_ROWS
    return u0, u1, v0, v1


def cell_pixel_box(name):
    """(left, top, right, bottom) pixel box in the PIL image (row 0 = top)."""
    row, col = cell_row_col(name)
    left = col * CELL_PX
    top = row * CELL_PX
    return left, top, left + CELL_PX, top + CELL_PX
