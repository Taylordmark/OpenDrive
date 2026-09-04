"""Procedural boulder meshes (2026-09-03, reviewer defect fix - see next
steps.md sec 7.15): AODForestManager's ground-cover rock scatter has always
reused two /Engine/BasicShapes primitives (Cube, Sphere - see
ODForestManager.h's RockMeshes comment) with only per-instance scale/rotation
jitter for variety. That reads exactly as reported: sharp-cornered CUBES and
perfect spheres littering hillsides near Dillon Dam Rd/Coyne Valley Rd, not
weathered granite boulders. This script generates real (if deliberately
low-poly, matching the project's stylized art direction) irregular boulder
geometry instead: an icosphere base, per-vertex radial noise displacement for
an irregular weathered silhouette, and a flattened bottom so each rock reads
as resting on the ground rather than floating as a pure geometric primitive.

No external mesh/noise libraries used - just numpy + a hand-rolled
deterministic multi-term sine "noise" (doesn't need to be seamless/tileable,
only irregular-looking on a closed sphere).

Four size/shape variants (2-4 per the task spec):
    rock_boulder_a  - small, angular (icosahedron, no subdivision - facets
                       read as fresh-fractured granite)
    rock_boulder_b  - medium, rounded weathered boulder (1x subdivided)
    rock_boulder_c  - large, oblong weathered boulder (1x subdivided,
                       stretched, stronger noise)
    rock_boulder_d  - flat angular slab (no subdivision, squashed + widened)

Every mesh is exported BASE-PIVOTED (min Z shifted to 0), matching
make_tree_meshes.py's convention - AODForestManager's per-mesh
RockMeshBaseOffsetCm correction (computed generically from each mesh's own
bounding box, see ODForestManager.h) still works unchanged either way, this
just makes the offset ~0 instead of ~half the mesh height like the old
center-pivoted BasicShapes needed.

UVs are a simple spherical projection of each vertex's PRE-displacement unit
-sphere direction - stable (no seam artifacts from the noise/flatten steps)
and good enough for M_Rock030's tiled PBR set (unreal/apply_rock_material.py
already handles the UV0 tiling factor).

Faces use FLAT (per-triangle, not smoothed) normals on purpose - facets read
as low-poly stylized rock, matching the game's existing art direction
(explicitly not a smooth/organic high-poly sculpt).

Outputs: data/flora/obj/rock_boulder_{a,b,c,d}.obj (+ matching .mtl, unused
for texturing - unreal/import_rock_meshes.py assigns M_Rock030 directly the
same way apply_rock_material.py already did for the old BasicShapes).

Usage: python make_rock_meshes.py
"""

import math
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "flora" / "obj"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------- icosphere

def _icosahedron():
    t = (1.0 + math.sqrt(5.0)) / 2.0
    verts = [
        (-1, t, 0), (1, t, 0), (-1, -t, 0), (1, -t, 0),
        (0, -1, t), (0, 1, t), (0, -1, -t), (0, 1, -t),
        (t, 0, -1), (t, 0, 1), (-t, 0, -1), (-t, 0, 1),
    ]
    verts = [tuple(c / np.linalg.norm(v) for c in v) for v in
             (np.array(v, dtype=float) for v in verts)]
    faces = [
        (0, 11, 5), (0, 5, 1), (0, 1, 7), (0, 7, 10), (0, 10, 11),
        (1, 5, 9), (5, 11, 4), (11, 10, 2), (10, 7, 6), (7, 1, 8),
        (3, 9, 4), (3, 4, 2), (3, 2, 6), (3, 6, 8), (3, 8, 9),
        (4, 9, 5), (2, 4, 11), (6, 2, 10), (8, 6, 7), (9, 8, 1),
    ]
    return [np.array(v) for v in verts], faces


def _subdivide(verts, faces):
    """One round of midpoint subdivision, each new midpoint re-normalized
    onto the unit sphere. Not vertex-deduplicated (matches this project's
    existing non-indexed OBJ-writer convention in make_tree_meshes.py) -
    boulder meshes are small enough (tens to ~300 tris) that this costs
    nothing."""
    def mid(a, b):
        m = (a + b) / 2.0
        return m / np.linalg.norm(m)

    new_faces = []
    new_verts = list(verts)
    cache = {}

    def get_mid_index(i, j):
        key = (min(i, j), max(i, j))
        if key not in cache:
            cache[key] = len(new_verts)
            new_verts.append(mid(verts[i], verts[j]))
        return cache[key]

    for a, b, c in faces:
        ab, bc, ca = get_mid_index(a, b), get_mid_index(b, c), get_mid_index(c, a)
        new_faces += [(a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)]
    return new_verts, new_faces


def _noise(dir_vec, seed):
    """Deterministic multi-term sine "noise" over a unit direction vector -
    irregular-looking on a closed sphere without needing a real Perlin/
    Simplex implementation or an external dependency. Not seamless/tileable
    (doesn't need to be - every boulder is a single closed mesh, not a
    repeating texture)."""
    x, y, z = dir_vec
    n = 0.0
    n += 0.50 * math.sin(3.1 * x + 1.7 * seed) * math.cos(2.3 * y - 0.9 * seed)
    n += 0.30 * math.sin(5.3 * y + 2.9 * seed) * math.cos(4.1 * z + 1.3 * seed)
    n += 0.20 * math.sin(6.7 * z - 2.1 * seed) * math.cos(5.9 * x + 0.5 * seed)
    return n


def make_boulder(subdiv, noise_amp, seed, scale_xyz, flatten_z=-0.35,
                  flatten_squash=0.18):
    """Returns (verts, uvs, faces) - verts/uvs are lists of np.array/tuple,
    faces are index triples into both (shared indices - no separate UV
    seam splitting needed since the spherical projection has no hard seam
    within a single closed mesh's usable range)."""
    verts, faces = _icosahedron()
    for _ in range(subdiv):
        verts, faces = _subdivide(verts, faces)

    # UVs from the PRE-displacement unit-sphere direction (stable, no
    # seam distortion from the noise/flatten steps below).
    uvs = []
    for v in verts:
        x, y, z = v
        u = 0.5 + math.atan2(y, x) / (2.0 * math.pi)
        vv = 0.5 - math.asin(max(-1.0, min(1.0, z))) / math.pi
        uvs.append((u, vv))

    disp = []
    for i, v in enumerate(verts):
        r = 1.0 + noise_amp * _noise(v, seed)
        p = v * r
        disp.append(p)

    # Flatten the bottom: below flatten_z (in the original unit-sphere
    # frame, before per-axis scale), squash the remaining depth toward that
    # plane instead of hard-clipping - keeps a slight taper so no face goes
    # degenerate, while still reading as "resting flat on the ground" rather
    # than a rock floating on a point.
    flat = []
    for p in disp:
        x, y, z = p
        if z < flatten_z:
            z = flatten_z + (z - flatten_z) * flatten_squash
        flat.append(np.array([x, y, z]))

    sx, sy, sz = scale_xyz
    scaled = [np.array([p[0] * sx, p[1] * sy, p[2] * sz]) for p in flat]

    min_z = min(p[2] for p in scaled)
    based = [np.array([p[0], p[1], p[2] - min_z]) for p in scaled]

    return based, uvs, faces


def save_obj(name, verts, uvs, faces):
    """Same non-indexed v/vt/vn-per-face-corner convention and Y-negation
    (mirrors the mesh for UE's winding, matching every other OBJ writer in
    this pipeline - see make_tree_meshes.py's Mesh.save()) as the rest of
    this project's procedural mesh exporters. FLAT per-triangle normals on
    purpose (see module docstring) - each face gets its own cross-product
    normal instead of an averaged vertex normal, so the facets read as
    faceted low-poly rock instead of a smoothed blob."""
    lines = [f"# OpenDrive procedural boulder: {name}", f"mtllib {name}.mtl",
             "usemtl rock"]
    f_lines = []
    vi = 1
    for a, b, c in faces:
        pa, pb, pc = verts[a], verts[b], verts[c]
        n = np.cross(pb - pa, pc - pa)
        ln = np.linalg.norm(n)
        n = n / ln if ln > 1e-8 else np.array([0.0, 0.0, 1.0])
        for idx in (a, b, c):
            p = verts[idx]
            uv = uvs[idx]
            lines.append(f"v {p[0]:.4f} {-p[1]:.4f} {p[2]:.4f}")
            lines.append(f"vt {uv[0]:.4f} {uv[1]:.4f}")
            lines.append(f"vn {n[0]:.4f} {-n[1]:.4f} {n[2]:.4f}")
        f_lines.append(f"f {vi}/{vi}/{vi} {vi+1}/{vi+1}/{vi+1} {vi+2}/{vi+2}/{vi+2}")
        vi += 3
    (OUT_DIR / f"{name}.obj").write_text("\n".join(lines + f_lines) + "\n")
    (OUT_DIR / f"{name}.mtl").write_text(
        "newmtl rock\nKd 0.55 0.55 0.55\n")
    print(f"wrote {name}.obj ({len(faces)} tris)")


VARIANTS = [
    # name, subdiv, noise_amp, seed, scale_xyz, flatten_z, flatten_squash
    ("rock_boulder_a", 0, 0.10, 3.1, (0.55, 0.50, 0.42), -0.25, 0.22),
    ("rock_boulder_b", 1, 0.14, 7.7, (1.00, 0.92, 0.80), -0.35, 0.18),
    ("rock_boulder_c", 1, 0.20, 12.3, (1.55, 1.15, 0.95), -0.30, 0.16),
    ("rock_boulder_d", 0, 0.08, 19.9, (1.35, 1.05, 0.42), -0.05, 0.30),
]


def main():
    for name, subdiv, noise_amp, seed, scale_xyz, fz, fs in VARIANTS:
        verts, uvs, faces = make_boulder(subdiv, noise_amp, seed, scale_xyz, fz, fs)
        save_obj(name, verts, uvs, faces)


if __name__ == "__main__":
    main()
