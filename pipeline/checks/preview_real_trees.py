"""Track F check (PLAN-VISUALS.md): offline turntable sheet of the realistic
tree meshes next to their stylized twins - a tiny numpy z-buffer rasterizer
(orthographic, alpha-tested atlas, lambert from the OBJ normals, sun from
the upper left). Not the UE look; it shows silhouette, density, card
orientation and normals. Also prints a tri/card table vs the stylized mesh.

    python pipeline/checks/preview_real_trees.py [out.png]
Needs make_tree_meshes.py (both modes) and build_flora_textures.py (both).
"""
import math
from functools import lru_cache
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "data" / "stages" / "tail_of_the_dragon"
SLOTS = ["tree_conifer", "tree_canyon_oak", "tree_olive", "tree_yucca", "tree_blob", "tree_scrub_oak"]
SUN = np.array([-0.5, -0.4, 0.75]) / np.linalg.norm([-0.5, -0.4, 0.75])


def load_obj(path):
    v, vt, vn, tris = [], [], [], []
    mat = None
    for line in path.read_text().splitlines():
        p = line.split()
        if not p:
            continue
        if p[0] == "v":
            v.append([float(x) for x in p[1:4]])
        elif p[0] == "vt":
            vt.append([float(x) for x in p[1:3]])
        elif p[0] == "vn":
            vn.append([float(x) for x in p[1:4]])
        elif p[0] == "usemtl":
            mat = p[1]
        elif p[0] == "f":
            idx = [int(c.split("/")[0]) - 1 for c in p[1:4]]
            tris.append((mat, idx))
    return np.array(v), np.array(vt), np.array(vn), tris


@lru_cache(None)
def tex(name):
    return np.asarray(Image.open(STAGE / "textures" / name).convert("RGBA"), dtype=np.float32) / 255


def render(path, yaw, size=360, extent=None):
    v, vt, vn, tris = load_obj(path)
    real = "_real" in path.name
    atlas = tex("T_FoliageAtlas_real.png" if real else "T_FoliageAtlas.png")
    bark = tex(("T_BarkAspen" if "blob" in path.name or "scrub" in path.name else "T_BarkConifer")
               + ("_real.png" if real else ".png"))
    c, s = math.cos(yaw), math.sin(yaw)
    x = v[:, 0] * c - v[:, 1] * s                      # screen x
    d = v[:, 0] * s + v[:, 1] * c                      # depth (bigger = nearer)
    z = v[:, 2]
    ext = extent or max(z.max(), 2 * np.abs(x).max()) * 1.05
    px = (x / ext + 0.5) * size
    py = (1 - z / ext) * size
    img = np.full((size, size, 3), [0.78, 0.84, 0.92], np.float32)
    zb = np.full((size, size), -1e9, np.float32)
    for mat, (i0, i1, i2) in tris:
        t = atlas if mat == "foliage" else bark
        P = np.array([[px[i], py[i]] for i in (i0, i1, i2)])
        x0, y0 = np.floor(P.min(0)).astype(int)
        x1, y1 = np.ceil(P.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, size - 1), min(y1, size - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        (ax, ay), (bx, by), (cx, cy) = P
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-9:
            continue
        w0 = ((by - cy) * (gx - cx) + (cx - bx) * (gy - cy)) / den
        w1 = ((cy - ay) * (gx - cx) + (ax - cx) * (gy - cy)) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        W = np.stack([w0, w1, w2], -1)[inside]
        depth = W @ d[[i0, i1, i2]]
        uv = W @ vt[[i0, i1, i2]]
        h, w = t.shape[:2]
        tx = (np.mod(uv[:, 0], 1) * (w - 1)).astype(int)
        ty = ((1 - np.mod(uv[:, 1], 1)) * (h - 1)).astype(int)
        col = t[ty, tx]
        n = W @ vn[[i0, i1, i2]]
        n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-9
        n[:, 1] *= -1                                 # OBJ writes -y
        view = np.array([s, c, 0.0])                  # toward the camera
        if mat == "foliage":                          # two-sided: flip back-facing normals
            n = np.where((n @ view)[:, None] < 0, -n, n)
            lit = 0.45 + 0.75 * np.clip(n @ SUN, 0, 1)
        else:
            lit = 0.35 + 0.85 * np.clip(n @ SUN, 0, 1)
        keep = (col[:, 3] > 0.35) & (depth > zb[gy[inside].astype(int), gx[inside].astype(int)])
        yy, xx = gy[inside][keep].astype(int), gx[inside][keep].astype(int)
        zb[yy, xx] = depth[keep]
        img[yy, xx] = col[keep, :3] * lit[keep, None]
    return np.clip(img, 0, 1), ext


def count(path):
    _, _, _, tris = load_obj(path)
    fol = sum(1 for m, _ in tris if m == "foliage")
    return len(tris), fol // 2


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "scratch_preview_real_trees.png"
    rows = []
    print(f"{'slot':16s} {'stylized tris/cards':>20s} {'real tris/cards (std/high/ultra)':>40s}")
    for slot in SLOTS:
        o = STAGE / "obj"
        st = count(o / f"{slot}.obj")
        re = [count(o / f"{slot}_real{t}.obj") for t in ("", "_high", "_ultra")]
        print(f"{slot:16s} {st[0]:>10d}/{st[1]:<9d} " + "  ".join(f"{a}/{b}" for a, b in re))
        ext = max(render(o / f"{n}.obj", 0, 32)[1] for n in (slot, slot + "_real"))
        tiles = [render(o / f"{slot}.obj", 0.0, extent=ext)[0]]
        tiles += [render(o / f"{slot}_real.obj", y, extent=ext)[0] for y in (0.0, math.pi / 2)]
        tiles += [render(o / f"{slot}_real_ultra.obj", 0.0, extent=ext)[0]]
        rows.append(np.concatenate(tiles, 1))
    Image.fromarray(np.uint8(np.concatenate(rows, 0) * 255)).save(out)
    print(f"wrote {out}  (columns: stylized | real 0deg | real 90deg | real ultra)")


if __name__ == "__main__":
    main()
