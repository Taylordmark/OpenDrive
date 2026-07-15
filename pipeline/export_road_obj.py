"""Export road_mesh.json chunks as OBJ files for UE static-mesh import.

Usage:  python export_road_obj.py [--axis-map MAP]

Writes data/stages/<stage>/obj/road_XXXX.obj plus calib.obj (axis probe).

--axis-map controls how UE-space (X,Y,Z cm) coords are written into the OBJ
so that UE's importer lands them back exactly on the source coordinates.
Format: comma triple of source components with sign, e.g. "x,z,-y" means
obj_v = (ue.x, ue.z, -ue.y). Default identity "x,y,z" (used for calibration).
"""

import argparse
import json
from pathlib import Path

STAGE = "tail_of_the_dragon"
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "stages" / STAGE / "road_mesh.json"
OUT = ROOT / "data" / "stages" / STAGE / "obj"
OUT.mkdir(parents=True, exist_ok=True)


def make_mapper(spec):
    comps = spec.split(",")
    assert len(comps) == 3, "axis map needs 3 components"

    def mapper(x, y, z):
        src = {"x": x, "y": y, "z": z}
        out = []
        for c in comps:
            sign = -1.0 if c.startswith("-") else 1.0
            out.append(sign * src[c.lstrip("-")])
        return out
    return mapper


def write_obj(path, vertices, triangles, uvs=None, mapper=None):
    lines = ["# OpenDrive road chunk"]
    for i in range(0, len(vertices), 3):
        v = mapper(vertices[i], vertices[i + 1], vertices[i + 2])
        lines.append(f"v {v[0]:.1f} {v[1]:.1f} {v[2]:.1f}")
    if uvs:
        for i in range(0, len(uvs), 2):
            lines.append(f"vt {uvs[i]:.4f} {uvs[i + 1]:.4f}")
    for i in range(0, len(triangles), 3):
        a, b, c = triangles[i] + 1, triangles[i + 1] + 1, triangles[i + 2] + 1
        if uvs:
            lines.append(f"f {a}/{a} {b}/{b} {c}/{c}")
        else:
            lines.append(f"f {a} {b} {c}")
    path.write_text("\n".join(lines) + "\n")


def write_calib(mapper):
    """Asymmetric probe: axis-aligned spikes with unique lengths
    (+X:1000, +Y:2000, +Z:4000) so the imported bounding box reveals the
    importer's axis mapping and sign flips."""
    verts, tris = [], []

    def spike(dx, dy, dz):
        base = len(verts) // 3
        s = 10.0
        verts.extend([0, 0, 0,  s, 0, 0,  0, s, 0,  dx, dy, dz])
        tris.extend([base, base + 1, base + 3, base, base + 2, base + 3,
                     base + 1, base + 2, base + 3])

    spike(1000, 0, 0)
    spike(0, 2000, 0)
    spike(0, 0, 4000)
    # winding probe: a quad at z=8000 wound exactly like road surface tris
    # (A, A+right, A+forward) which must face UP (+Z) after import
    base = len(verts) // 3
    verts.extend([0, 0, 8000,  0, 1000, 8000,  1000, 0, 8000,  1000, 1000, 8000])
    tris.extend([base, base + 1, base + 2, base + 1, base + 3, base + 2])
    uvs = [0.0, 0.0] * (len(verts) // 3)  # OBJ translator requires UVs on faces
    write_obj(OUT / "calib.obj", verts, tris, uvs=uvs, mapper=mapper)


def main():
    ap = argparse.ArgumentParser()
    # UE Interchange imports OBJ as (x, -y, z) - determined empirically by
    # calib run 2026-07-14 - so pre-flip Y to land on UE coords exactly
    ap.add_argument("--axis-map", default="x,-y,z")
    args = ap.parse_args()
    mapper = make_mapper(args.axis_map)

    write_calib(mapper)
    chunks = json.loads(SRC.read_text())["chunks"]
    for ch in chunks:
        write_obj(OUT / f"{ch['name']}.obj", ch["vertices"], ch["triangles"],
                  ch.get("uvs"), mapper=mapper)
    print(f"Wrote calib.obj + {len(chunks)} chunks -> {OUT} "
          f"(axis map: {args.axis_map})")


if __name__ == "__main__":
    main()
