"""Procedural low-poly roadside furniture meshes (PLAN-REALISM Phase 1),
one per build_road_furniture.TYPES entry: signal mast, stop / yield /
pedestrian / railroad / speed signs, curve chevrons, street light,
delineator post, stop-bar stripe. Stylized flat-colour blocks (standing
decision 1), no textures, no text.

Conventions (shared with make_rock_meshes.py / make_car_meshes.py):
centimetres in UE axes (X forward, Y right, Z up), written Y-negated with
corner order a,c,b so the faces UE imports point outward. Base pivot at the
origin. A sign's face looks down +X (AODFurnitureManager yaws +X to face
oncoming traffic), so a traveller looks along -X and their left is +Y. The
signal's mast arm and the lamp's arm reach out over the road: +Y for the
signal (pole on the traveller's right), +X for the lamp (yawed toward the
road). Every material group is an OBJ usemtl = one UE material slot, named
after the colour (unreal/import_furniture_meshes.py assigns by name;
"lamp" is the slot that glows at night).

Outputs: data/furniture/obj/SM_Furn_<Type>.obj (+ .mtl)
Usage:   python make_furniture_meshes.py
"""

import math
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "furniture" / "obj"
COLORS = {"gray": (0.45, 0.46, 0.48), "white": (0.9, 0.9, 0.9), "red": (0.7, 0.05, 0.05),
          "yellow": (0.95, 0.75, 0.05), "black": (0.03, 0.03, 0.03), "green": (0.05, 0.6, 0.2),
          "lamp": (1.0, 0.85, 0.6)}


class Mesh:
    def __init__(self):
        self.tris = []      # (a, b, c, material)

    def prism(self, base, offset, mat):
        """Convex polygon `base` (n, 3) swept by `offset`: caps + sides, each
        triangle wound outward from the prism's centroid."""
        base = np.asarray(base, float)
        top = base + np.asarray(offset, float)
        c = np.vstack([base, top]).mean(axis=0)
        n = len(base)
        faces = [(base[0], base[i], base[i + 1]) for i in range(1, n - 1)]
        faces += [(top[0], top[i], top[i + 1]) for i in range(1, n - 1)]
        for i in range(n):
            j = (i + 1) % n
            faces += [(base[i], base[j], top[j]), (base[i], top[j], top[i])]
        for a, b, d in faces:
            if np.dot(np.cross(b - a, d - a), (a + b + d) / 3 - c) < 0:
                b, d = d, b
            self.tris.append((a, b, d, mat))

    def box(self, x0, x1, y0, y1, z0, z1, mat):
        self.prism([(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)], (0, 0, z1 - z0), mat)

    def pole(self, h, r, mat="gray", x=0.0, y=0.0, z0=0.0, sides=8):
        a = [2 * math.pi * (k + 0.5) / sides for k in range(sides)]
        self.prism([(x + r * math.cos(t), y + r * math.sin(t), z0) for t in a], (0, 0, h), mat)

    def plate(self, yz, x0, t, mat):
        """Flat sign plate: convex outline `yz` in the Y-Z plane, from x0 to x0 + t."""
        self.prism([(x0, y, z) for y, z in yz], (t, 0, 0), mat)

    def save(self, name):
        groups = {}
        for a, b, c, mat in self.tris:
            groups.setdefault(mat, []).append((a, b, c))
        lines, f_lines, vi = [f"# OpenDrive procedural furniture: {name}", f"mtllib {name}.mtl"], [], 1
        for mat, tris in groups.items():
            f_lines.append(f"usemtl {mat}")
            for a, b, c in tris:
                n = np.cross(b - a, c - a)
                n = n / max(np.linalg.norm(n), 1e-8)
                for p in (a, c, b):          # see make_rock_meshes.save_obj
                    lines += [f"v {p[0]:.2f} {-p[1]:.2f} {p[2]:.2f}", "vt 0.5 0.5",
                              f"vn {n[0]:.4f} {-n[1]:.4f} {n[2]:.4f}"]
                f_lines.append(f"f {vi}/{vi}/{vi} {vi + 1}/{vi + 1}/{vi + 1} {vi + 2}/{vi + 2}/{vi + 2}")
                vi += 3
        (OUT_DIR / f"{name}.obj").write_text("\n".join(lines + f_lines) + "\n")
        (OUT_DIR / f"{name}.mtl").write_text("".join(
            f"newmtl {m}\nKd {COLORS[m][0]} {COLORS[m][1]} {COLORS[m][2]}\n" for m in groups))
        print(f"wrote {name}.obj ({len(self.tris)} tris, slots {list(groups)})")


def ngon(n, r, cy, cz, rot=0.0):
    return [(cy + r * math.cos(rot + 2 * math.pi * k / n), cz + r * math.sin(rot + 2 * math.pi * k / n))
            for k in range(n)]


def rect(cy, cz, w, h, ang=0.0):
    ca, sa = math.cos(ang), math.sin(ang)
    return [(cy + ca * y - sa * z, cz + sa * y + ca * z)
            for y, z in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]


def sign_post(h=250.0):
    m = Mesh()
    m.pole(h, 3.5)
    return m


def signal():
    m = Mesh()
    m.pole(620, 12)
    m.box(-8, 8, 0, 520, 575, 595, "gray")                 # mast arm over the lane (+Y)
    for y in (260.0, 450.0):                               # two heads
        m.box(-12, 12, y - 18, y + 18, 455, 570, "black")
        for z, mat in ((545, "red"), (512, "yellow"), (479, "green")):
            m.plate(ngon(8, 11, y, z), 12, 2, mat)
    return m


def stop():
    m = sign_post()
    m.plate(ngon(8, 38, 0, 215, math.pi / 8), 4, 2, "red")
    m.plate(rect(0, 215, 52, 12), 6, 1, "white")           # stand-in for the lettering
    return m


def yield_sign():
    m = sign_post()
    m.plate(ngon(3, 50, 0, 215, -math.pi / 2), 4, 2, "red")
    m.plate(ngon(3, 30, 0, 222, -math.pi / 2), 6, 1, "white")
    return m


def ped_crossing():
    m = sign_post()
    m.plate(ngon(4, 42, 0, 215), 4, 2, "yellow")
    m.plate(rect(0, 225, 8, 26), 6, 1, "black")            # stick figure, stylized
    m.plate(rect(0, 203, 22, 6), 6, 1, "black")
    return m


def rail_crossbuck():
    m = sign_post(300)
    for ang, x0 in ((math.pi / 4, 4), (-math.pi / 4, 6)):
        m.plate(rect(0, 260, 120, 22, ang), x0, 2, "white")
    return m


def speed():
    m = sign_post()
    m.plate(rect(0, 210, 60, 76), 4, 2, "white")
    m.plate(rect(0, 236, 44, 8), 6, 1, "black")            # "SPEED LIMIT" band
    m.plate(rect(-10, 200, 14, 34), 6, 1, "black")         # ponytail: digitless "number"
    m.plate(rect(10, 200, 14, 34), 6, 1, "black")          # blocks; real digits = a texture atlas
    return m


def chevron(left):
    """Yellow board, black chevron pointing to the traveller's left (+Y) or right."""
    m = sign_post(180)
    m.plate(rect(0, 150, 46, 60), 4, 2, "yellow")
    s = 1.0 if left else -1.0
    for ang in (math.radians(40), math.radians(-40)):
        # upper / lower arm: from the tip (y = s*12, mid-height) back to the far edge
        m.plate(rect(s * 2, 150 + 11 * math.copysign(1, ang), 30, 8, -s * ang), 6, 1, "black")
    return m


def street_light():
    m = Mesh()
    m.pole(900, 9)
    m.box(0, 230, -5, 5, 880, 892, "gray")                 # arm toward the road (+X)
    m.box(200, 275, -16, 16, 868, 892, "gray")             # head
    m.box(205, 270, -12, 12, 864, 868, "lamp")             # glowing underside
    return m


def delineator():
    m = Mesh()
    m.box(-5, 5, -5, 5, 0, 110, "white")
    m.box(5, 6, -4, 4, 92, 104, "yellow")                  # reflector, both faces
    m.box(-6, -5, -4, 4, 92, 104, "yellow")
    return m


def stop_bar():
    m = Mesh()
    m.box(-20, 20, -50, 50, 0, 2, "white")                 # 1 m wide, scaled in Y per approach
    return m


MESHES = {"Signal": signal, "Stop": stop, "Yield": yield_sign, "PedCrossing": ped_crossing,
          "RailCrossbuck": rail_crossbuck, "Speed": speed, "ChevronL": lambda: chevron(True),
          "ChevronR": lambda: chevron(False), "StreetLight": street_light, "Delineator": delineator,
          "StopBar": stop_bar}

if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, fn in MESHES.items():
        fn().save(f"SM_Furn_{name}")
