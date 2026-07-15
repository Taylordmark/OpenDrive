"""Generate tree atlas texture + scattered forest baked into chunked OBJs.

Trees are cheap card trees (3 crossed quads + box trunk, ~14 tris) merged into
per-tile static meshes so the import path is the proven OBJ route — no
instancing APIs. Placement: dense within 120 m of the road, sparse to 500 m,
gated by the forest channel of T_TerrainMask and kept off the road corridor.

Outputs:
    textures/T_TreeAtlas.png              RGBA (bark | green canopy | olive canopy)
    obj/foliage_XXX.obj                   per 600 m tile, UE cm, axis map baked
"""

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

STAGE = "tail_of_the_dragon"
ROOT = Path(__file__).resolve().parent.parent
STAGE_DIR = ROOT / "data" / "stages" / STAGE
TEX = STAGE_DIR / "textures"
OBJ_OUT = STAGE_DIR / "obj"

rng = np.random.default_rng(7)

# ------------------------------------------------------------------ atlas
def tileable_noise(size, cell, lo=0.0, hi=1.0):
    n = max(2, -(-size // cell))
    g = rng.random((n, n)).astype(np.float32)
    big = np.kron(g, np.ones((cell, cell), dtype=np.float32))[:size, :size]
    for sh in (1, 2, 4, 8, 16):
        if sh >= cell:
            break
        big = 0.5 * big + 0.25 * (np.roll(big, sh, 0) + np.roll(big, sh, 1))
    big = (big - big.min()) / (big.max() - big.min() + 1e-6)
    return lo + big * (hi - lo)


def make_atlas():
    S = 1024
    img = np.zeros((S, S, 4), dtype=np.float32)
    # bark strip x in [0, 0.25)
    bw = S // 4
    streaks = tileable_noise(S, 6)[:, :bw]
    vert = tileable_noise(S, 48)[:, :bw]
    bark = np.stack([0.16 + 0.10 * streaks * vert,
                     0.11 + 0.07 * streaks * vert,
                     0.07 + 0.04 * streaks], axis=-1)
    img[:, :bw, :3] = bark
    img[:, :bw, 3] = 1.0

    # canopies: blobby leaf clusters with alpha holes
    def canopy(x0, x1, base, tip):
        w = x1 - x0
        yy, xx = np.mgrid[0:S, 0:w].astype(np.float32)
        cx, cy = w / 2, S / 2
        r = np.sqrt(((xx - cx) / (w * 0.48)) ** 2 + ((yy - cy) / (S * 0.48)) ** 2)
        blob = tileable_noise(S, 96)[:, :w]
        fine = tileable_noise(S, 8)[:, :w]
        alpha = np.clip((1.0 - r) * 2.2, 0, 1) * ((blob * 0.7 + fine * 0.5) > 0.42)
        shade = 0.45 + 0.55 * (1 - r) * fine
        col = (np.array(base)[None, None, :] * (1 - shade[:, :, None])
               + np.array(tip)[None, None, :] * shade[:, :, None])
        img[:, x0:x1, :3] = col
        img[:, x0:x1, 3] = (alpha > 0.5).astype(np.float32)

    canopy(bw, bw + (S - bw) // 2, [0.03, 0.09, 0.02], [0.12, 0.26, 0.06])
    canopy(bw + (S - bw) // 2, S, [0.06, 0.09, 0.02], [0.20, 0.24, 0.07])

    rgb = np.clip(img[:, :, :3], 0, 1) ** (1 / 2.2)
    out = np.concatenate([rgb, img[:, :, 3:4]], axis=-1)
    Image.fromarray((out * 255).astype(np.uint8), mode="RGBA").save(
        TEX / "T_TreeAtlas.png")
    print("wrote T_TreeAtlas.png")


# ------------------------------------------------------------------ terrain
meta = json.loads((STAGE_DIR / "stage_meta.json").read_text())
ls = meta["landscape"]
res = ls["resolution"]
side_cm = (res - 1) * ls["scale"][0]
corner = ls["location_cm"]
z_scale = ls["scale"][2]
z_center_cm = ls["location_cm"][2]

hm = np.asarray(Image.open(STAGE_DIR / "heightmap.png"), dtype=np.float32)
mask_img = np.asarray(Image.open(TEX / "T_TerrainMask.png"), dtype=np.float32) / 255.0
forest_w = mask_img[:, :, 0]
mres = forest_w.shape[0]


def terrain_z_cm(x_cm, y_cm):
    """Bilinear height from the carved heightmap (matches Landscape render)."""
    u = (x_cm - corner[0]) / side_cm * (res - 1)
    v = (y_cm - corner[1]) / side_cm * (res - 1)
    u = np.clip(u, 0, res - 1.001)
    v = np.clip(v, 0, res - 1.001)
    x0 = u.astype(int); y0 = v.astype(int)
    fx = u - x0; fy = v - y0
    h = (hm[y0, x0] * (1 - fx) * (1 - fy) + hm[y0, x0 + 1] * fx * (1 - fy)
         + hm[y0 + 1, x0] * (1 - fx) * fy + hm[y0 + 1, x0 + 1] * fx * fy)
    return z_center_cm + (h - 32768.0) / 128.0 * z_scale


def forest_at(x_cm, y_cm):
    u = np.clip((x_cm - corner[0]) / side_cm * (mres - 1), 0, mres - 1).astype(int)
    v = np.clip((y_cm - corner[1]) / side_cm * (mres - 1), 0, mres - 1).astype(int)
    return forest_w[v, u]


# road centerline from road_mesh.json (avg of the two asphalt-edge verts)
road = json.loads((STAGE_DIR / "road_mesh.json").read_text())
centers = []
for ch in road["chunks"]:
    v = np.array(ch["vertices"], dtype=np.float64).reshape(-1, 4, 3)
    centers.append((v[:, 1, :2] + v[:, 2, :2]) / 2)
centerline = np.vstack(centers)  # (N,2) cm


def dist_to_road(pts):
    """Min distance (cm) from pts (M,2) to centerline, chunked to save RAM."""
    out = np.full(len(pts), np.inf)
    for i in range(0, len(centerline), 400):
        seg = centerline[i:i + 400]
        d = np.sqrt(((pts[:, None, :] - seg[None, :, :]) ** 2).sum(-1)).min(1)
        out = np.minimum(out, d)
    return out


# ------------------------------------------------------------------ scatter
def scatter(grid_m, band_lo_m, band_hi_m, keep_prob):
    lo, hi = band_lo_m * 100, band_hi_m * 100
    g = grid_m * 100
    xs = np.arange(corner[0], corner[0] + side_cm, g)
    ys = np.arange(corner[1], corner[1] + side_cm, g)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=-1)
    pts += rng.uniform(-0.45 * g, 0.45 * g, pts.shape)
    # cheap prefilter: bounding box around road, then true distance
    bb = 1.15 * hi
    near_bb = ((pts[:, 0] > centerline[:, 0].min() - bb)
               & (pts[:, 0] < centerline[:, 0].max() + bb)
               & (pts[:, 1] > centerline[:, 1].min() - bb)
               & (pts[:, 1] < centerline[:, 1].max() + bb))
    pts = pts[near_bb]
    d = dist_to_road(pts)
    pts = pts[(d > lo) & (d < hi)]
    w = forest_at(pts[:, 0], pts[:, 1])
    keep = rng.random(len(pts)) < (w * keep_prob)
    return pts[keep & (w > 0.4)]


print("scattering trees ...")
dense = scatter(7.5, 0.10, 120, 0.95)   # 10 m off centerline
sparse = scatter(20, 120, 500, 0.8)
trees = np.vstack([dense, sparse])
print(f"  dense={len(dense)} sparse={len(sparse)} total={len(trees)}")

z = terrain_z_cm(trees[:, 0], trees[:, 1]) - 40.0
scale = rng.uniform(0.75, 1.4, len(trees))
yaw = rng.uniform(0, 2 * math.pi, len(trees))
variant = rng.random(len(trees)) > 0.45  # green vs olive canopy


# ------------------------------------------------------------------ bake objs
# atlas u ranges
U_BARK = (0.02, 0.23)
U_CAN = {False: (0.26, 0.615), True: (0.645, 0.995)}
H = 1800.0  # base tree height cm


def tree_geo(x, y, zc, s, a, var, verts, uvs, tris):
    h = H * s
    cw = h * 0.62
    ct, cb = h, h * 0.28
    base = len(verts)
    # 3 crossed quads
    for k in range(3):
        ang = a + k * math.pi / 3
        dx, dy = math.cos(ang) * cw / 2, math.sin(ang) * cw / 2
        u0, u1 = U_CAN[var]
        verts.extend([(x - dx, y - dy, zc + cb), (x + dx, y + dy, zc + cb),
                      (x - dx, y - dy, zc + ct), (x + dx, y + dy, zc + ct)])
        uvs.extend([(u0, 1), (u1, 1), (u0, 0), (u1, 0)])
        b = base + k * 4
        tris.extend([(b, b + 1, b + 2), (b + 1, b + 3, b + 2)])
    # box trunk
    r = 14.0 * s
    tb = len(verts)
    u0, u1 = U_BARK
    for (ox, oy) in ((-r, -r), (r, -r), (r, r), (-r, r)):
        verts.append((x + ox, y + oy, zc))
        uvs.append((u0 if ox < 0 else u1, 1))
    for (ox, oy) in ((-r, -r), (r, -r), (r, r), (-r, r)):
        verts.append((x + ox, y + oy, zc + cb * 1.15))
        uvs.append((u0 if ox < 0 else u1, 0))
    for k in range(4):
        a0, a1 = tb + k, tb + (k + 1) % 4
        b0, b1 = a0 + 4, a1 + 4
        tris.extend([(a0, a1, b0), (a1, b1, b0)])


TILE = 60000.0  # 600 m
tiles = {}
for i in range(len(trees)):
    key = (int(trees[i, 0] // TILE), int(trees[i, 1] // TILE))
    tiles.setdefault(key, []).append(i)

n_files = 0
for key, idxs in sorted(tiles.items()):
    verts, uvs, tris = [], [], []
    for i in idxs:
        tree_geo(trees[i, 0], trees[i, 1], z[i], scale[i], yaw[i],
                 bool(variant[i]), verts, uvs, tris)
    lines = ["# OpenDrive foliage tile"]
    for (vx, vy, vz) in verts:
        lines.append(f"v {vx:.0f} {-vy:.0f} {vz:.0f}")   # axis map x,-y,z
    for (tu, tv) in uvs:
        lines.append(f"vt {tu:.4f} {tv:.4f}")
    for (a, b, c) in tris:
        lines.append(f"f {a + 1}/{a + 1} {b + 1}/{b + 1} {c + 1}/{c + 1}")
    (OBJ_OUT / f"foliage_{n_files:03d}.obj").write_text("\n".join(lines) + "\n")
    n_files += 1

print(f"wrote {n_files} foliage tiles, {len(trees)} trees, "
      f"~{len(tris) and sum(len(tiles[k]) for k in tiles) * 14} tris total")

make_atlas()
