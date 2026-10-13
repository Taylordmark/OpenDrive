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


# Whole-tree wind (PLAN-VISUALS.md Track F 4) for the _real flora meshes:
# import_biome_flora.py bakes these into UV channel 1 (the stylized meshes
# have one UV set and their materials never read UV1); build_flora_materials.py
# turns them into world position offset. Both are smooth functions of the
# rest position, so a leaf card and the branch under it get the same offset
# and never come apart.
SWAY_REF_H = 1500.0   # cm: U = 1 at the crown of a 15 m tree
SWAY_REF_R = 400.0    # cm: V = 1 at the outer edge of a crown 4 m in radius
SWAY_U_MAX = 1.5      # the material's WPO bound assumes these (22 m tree, 6 m crown radius)
SWAY_V_MAX = 1.5


def sway_uv(x, y, z, height, reach):
    """(U, V) for a vertex of a mesh `height` cm tall reaching `reach` cm
    from the trunk axis (trunk base at the origin). U: trunk bend, 0 at the
    base, (z/height)^2 scaled by tree height. V: extra for branch tips,
    (r/reach)^2 scaled by crown radius."""
    u = (max(z, 0.0) / height) ** 2 * height / SWAY_REF_H
    v = min(1.0, (x * x + y * y) ** 0.5 / reach) ** 2 * reach / SWAY_REF_R
    return u, v
