"""Stylized low-poly foliage meshes + shared color palette texture.

Solid faceted geometry (no alpha cards): blob trees, a conifer, grass tuft,
bush. Faces carry per-face normals (vn) for the faceted look and UV into a
palette strip. Only these meshes are stored - placement is runtime grass.

Outputs: obj/tree_blob.obj, tree_olive.obj, tree_conifer.obj,
         grass_tuft.obj, bush.obj, textures/T_TreePalette.png
"""

import math
from pathlib import Path

import numpy as np
from PIL import Image

STAGE_DIR = Path(__file__).resolve().parent.parent / "data" / "stages" / \
    "tail_of_the_dragon"
OBJ_OUT = STAGE_DIR / "obj"
TEX = STAGE_DIR / "textures"

# palette columns (linear color, saved sRGB)
PALETTE = [
    (0.28, 0.18, 0.10),   # 0 trunk
    (0.45, 0.62, 0.20),   # 1 blob light
    (0.20, 0.38, 0.11),   # 2 blob dark
    (0.58, 0.55, 0.16),   # 3 olive light
    (0.33, 0.35, 0.09),   # 4 olive dark
    (0.16, 0.40, 0.23),   # 5 conifer light
    (0.07, 0.24, 0.13),   # 6 conifer dark
    (0.36, 0.56, 0.14),   # 7 grass
]
N_CELL = len(PALETTE)


def write_palette():
    cell = 32
    img = np.zeros((cell, cell * N_CELL, 3), dtype=np.float32)
    for i, c in enumerate(PALETTE):
        img[:, i * cell:(i + 1) * cell] = c
    Image.fromarray((np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)) \
        .save(TEX / "T_TreePalette.png")
    print("wrote T_TreePalette.png")


def cell_uv(i):
    return ((i + 0.5) / N_CELL, 0.5)


ICO_T = (1 + 5 ** 0.5) / 2
ICO_V = np.array([(-1, ICO_T, 0), (1, ICO_T, 0), (-1, -ICO_T, 0), (1, -ICO_T, 0),
                  (0, -1, ICO_T), (0, 1, ICO_T), (0, -1, -ICO_T), (0, 1, -ICO_T),
                  (ICO_T, 0, -1), (ICO_T, 0, 1), (-ICO_T, 0, -1), (-ICO_T, 0, 1)])
ICO_V /= np.linalg.norm(ICO_V[0])
ICO_F = [(0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
         (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
         (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
         (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1)]


def icosphere(subdiv=1):
    """Subdivided icosahedron on the unit sphere."""
    verts = [v / np.linalg.norm(v) for v in ICO_V]
    faces = list(ICO_F)
    for _ in range(subdiv):
        cache = {}
        new_faces = []

        def midpoint(i, j):
            key = (min(i, j), max(i, j))
            if key not in cache:
                m = verts[i] + verts[j]
                verts.append(m / np.linalg.norm(m))
                cache[key] = len(verts) - 1
            return cache[key]

        for (i, j, k) in faces:
            a, b, c = midpoint(i, j), midpoint(j, k), midpoint(k, i)
            new_faces += [(i, a, c), (j, b, a), (k, c, b), (a, b, c)]
        faces = new_faces
    return np.array(verts), faces


SPHERE_V, SPHERE_F = icosphere(1)   # 80 faces - round but still low-poly
ICO_N = np.array([v / np.linalg.norm(v) for v in ICO_V])  # 20-face variant


class Mesh:
    def __init__(self):
        self.tris = []  # (v0, v1, v2, cell, n0, n1, n2) - normals may be None

    def tri(self, a, b, c, cell, center=None, normals=None):
        a, b, c = map(np.asarray, (a, b, c))
        n = np.cross(c - a, b - a)  # UE-facing convention (from calibration)
        if center is not None:
            fc = (a + b + c) / 3
            if np.dot(n, fc - center) < 0:
                b, c = c, b
                n = -n
                if normals is not None:
                    normals = (normals[0], normals[2], normals[1])
        if normals is None:
            ln = np.linalg.norm(n)
            n = n / ln if ln > 0 else np.array([0, 0, 1.0])
            normals = (n, n, n)
        self.tris.append((a, b, c, cell, *normals))

    def blob(self, center, r, cell_light, cell_dark, squash=1.0, detail=1):
        """Rounded blob: sphere with smooth normals. detail=0 for small
        ground-cover meshes (20 faces), 1 for tree canopies (80 faces)."""
        sphere_v, sphere_f = (SPHERE_V, SPHERE_F) if detail else (ICO_N, ICO_F)
        center = np.asarray(center, dtype=float)
        stretch = np.array([1, 1, squash])
        top_z = center[2] + r * squash
        for (i, j, k) in sphere_f:
            vs, ns = [], []
            for m in (i, j, k):
                p = sphere_v[m] * r * stretch
                vs.append(center + p)
                # ellipsoid smooth normal
                nrm = sphere_v[m] / stretch
                ns.append(nrm / np.linalg.norm(nrm))
            fc_z = sum(v[2] for v in vs) / 3
            cell = cell_light if fc_z > top_z - r * squash * 0.9 else cell_dark
            self.tri(vs[0], vs[1], vs[2], cell, center=center,
                     normals=tuple(ns))

    def cone(self, base_c, r, h, cell, segs=7):
        base_c = np.asarray(base_c, dtype=float)
        tip = base_c + np.array([0, 0, h])
        for s in range(segs):
            a0 = 2 * math.pi * s / segs
            a1 = 2 * math.pi * (s + 1) / segs
            p0 = base_c + np.array([math.cos(a0) * r, math.sin(a0) * r, 0])
            p1 = base_c + np.array([math.cos(a1) * r, math.sin(a1) * r, 0])
            self.tri(p0, p1, tip, cell, center=base_c + np.array([0, 0, h / 3]))

    def trunk(self, r, h, cell=0):
        pts = [(-r, -r), (r, -r), (r, r), (-r, r)]
        for i in range(4):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % 4]
            a, b = np.array([x0, y0, 0]), np.array([x1, y1, 0])
            c, d = np.array([x1, y1, h]), np.array([x0, y0, h])
            ctr = np.array([0, 0, h / 2])
            self.tri(a, b, c, cell, center=ctr)
            self.tri(a, c, d, cell, center=ctr)

    def save(self, name):
        lines = [f"# OpenDrive stylized foliage: {name}"]
        vi = 1
        f_lines = []
        for (a, b, c, cell, na, nb, nc) in self.tris:
            u, v = cell_uv(cell)
            for p, n in ((a, na), (b, nb), (c, nc)):
                lines.append(f"v {p[0]:.1f} {-p[1]:.1f} {p[2]:.1f}")
                lines.append(f"vt {u:.4f} {v:.4f}")
                lines.append(f"vn {n[0]:.3f} {-n[1]:.3f} {n[2]:.3f}")
            f_lines.append(f"f {vi}/{vi}/{vi} {vi + 1}/{vi + 1}/{vi + 1} "
                           f"{vi + 2}/{vi + 2}/{vi + 2}")
            vi += 3
        (OBJ_OUT / f"{name}.obj").write_text(
            "\n".join(lines + f_lines) + "\n")
        print(f"wrote {name}.obj ({len(self.tris)} tris)")


rng = np.random.default_rng(3)

# blob tree: trunk + rounded canopy cluster
for name, cl, cd in (("tree_blob", 1, 2), ("tree_olive", 3, 4)):
    m = Mesh()
    m.trunk(22, 750)
    m.blob((0, 0, 1150), 430, cl, cd, squash=0.85)
    for k in range(2):
        a = 2 * math.pi * k / 2 + (0.5 if name == "tree_olive" else 0)
        m.blob((math.cos(a) * 290, math.sin(a) * 290, 980 + k * 130),
               290, cl, cd, squash=0.8)
    m.save(name)

# conifer: trunk + 3 stacked cones (sharp on purpose - it's a pine)
m = Mesh()
m.trunk(18, 500)
m.cone((0, 0, 350), 330, 700, 6, segs=8)
m.cone((0, 0, 850), 260, 620, 5, segs=8)
m.cone((0, 0, 1300), 175, 520, 5, segs=8)
m.save("tree_conifer")

# grass tuft: two soft overlapping mounds (low detail - it's 30 cm tall)
m = Mesh()
m.blob((-12, -8, 22), 34, 7, 7, squash=0.6, detail=0)
m.blob((14, 10, 18), 26, 7, 7, squash=0.55, detail=0)
m.save("grass_tuft")

# bush: three overlapping rounded mounds
m = Mesh()
m.blob((-45, -30, 55), 75, 1, 2, squash=0.75, detail=0)
m.blob((50, 20, 50), 65, 1, 2, squash=0.7, detail=0)
m.blob((0, 45, 75), 60, 1, 2, squash=0.75, detail=0)
m.save("bush")

write_palette()
