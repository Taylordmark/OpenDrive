"""Placeholder car meshes for the OD vehicle pawn (template exorcism,
2026-07-17): stylized low-poly coupe + wheel, same faceted-solid + palette
conventions as make_tree_meshes.py. The Kenney CC0 pack import (4.3) will
replace these; until then the whole car is pipeline-generated.

Geometry contract with AODVehiclePawn (cm, pivot at wheelbase center at
WHEEL AXLE height z=0):
    front axle x=+132, rear axle x=-128, half-track 84
    wheel radius 34, width 24 (wheel.obj pivot centered, spins around Y)

Outputs: data/cars/placeholder/{chassis.obj, wheel.obj, T_CarPalette.png}
"""

import math
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "data" / "cars" / "placeholder"
OUT.mkdir(parents=True, exist_ok=True)

# palette columns (linear color, saved sRGB)
PALETTE = [
    (0.55, 0.09, 0.07),   # 0 body paint
    (0.06, 0.10, 0.14),   # 1 glass
    (0.05, 0.05, 0.06),   # 2 trim / bumpers / rockers
    (0.035, 0.035, 0.04), # 3 tire
    (0.45, 0.45, 0.48),   # 4 hub
    (0.92, 0.83, 0.55),   # 5 headlight
    (0.55, 0.04, 0.03),   # 6 taillight
    (0.30, 0.05, 0.04),   # 7 body dark (lower)
]
N_CELL = len(PALETTE)


def write_palette():
    cell = 32
    img = np.zeros((cell, cell * N_CELL, 3), dtype=np.float32)
    for i, c in enumerate(PALETTE):
        img[:, i * cell:(i + 1) * cell] = c
    Image.fromarray((np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)) \
        .save(OUT / "T_CarPalette.png")
    print("wrote T_CarPalette.png")


def cell_uv(i):
    return ((i + 0.5) / N_CELL, 0.5)


class Mesh:
    def __init__(self):
        self.tris = []   # (a, b, c, cell) - per-face normal computed on save

    def tri(self, a, b, c, cell):
        self.tris.append((np.array(a, float), np.array(b, float),
                          np.array(c, float), cell))

    def quad(self, a, b, c, d, cell):
        self.tri(a, b, c, cell)
        self.tri(a, c, d, cell)

    def save(self, name):
        lines, f_lines, vi = [], [], 1
        for (a, b, c, cell) in self.tris:
            n = np.cross(b - a, c - a)
            ln = np.linalg.norm(n)
            n = n / ln if ln > 1e-6 else np.array([0.0, 0.0, 1.0])
            u, v = cell_uv(cell)
            for p in (a, b, c):
                lines.append(f"v {p[0]:.1f} {-p[1]:.1f} {p[2]:.1f}")
                lines.append(f"vt {u:.4f} {v:.4f}")
                lines.append(f"vn {n[0]:.3f} {-n[1]:.3f} {n[2]:.3f}")
            f_lines.append(f"f {vi}/{vi}/{vi} {vi + 1}/{vi + 1}/{vi + 1} "
                           f"{vi + 2}/{vi + 2}/{vi + 2}")
            vi += 3
        (OUT / f"{name}.obj").write_text("\n".join(lines + f_lines) + "\n")
        print(f"wrote {name}.obj ({len(self.tris)} tris)")


# ------------------------------------------------------------------ chassis
# lofted profile sections front to back: (x, half_width, z_bottom, z_top)
SECTIONS = [
    (218.0, 52.0, 6.0, 30.0),     # nose tip
    (200.0, 70.0, 0.0, 42.0),     # bumper
    (140.0, 76.0, -6.0, 50.0),    # front arch area
    (75.0, 78.0, -10.0, 54.0),    # cowl (windshield base)
    (-125.0, 78.0, -10.0, 54.0),  # cabin rear base
    (-185.0, 74.0, -6.0, 48.0),   # rear arch
    (-215.0, 60.0, 2.0, 40.0),    # tail
]
# cabin (glass sides, body roof): base x 75 -> -125, roof x 35 -> -95
ROOF_Z, ROOF_W = 116.0, 60.0
CAB = [(75.0, 78.0, 54.0), (35.0, ROOF_W, ROOF_Z),
       (-95.0, ROOF_W, ROOF_Z), (-125.0, 78.0, 54.0)]

m = Mesh()
BODY, DARK = 0, 7

# hull sides / top / bottom between sections
for s0, s1 in zip(SECTIONS, SECTIONS[1:]):
    x0, w0, b0, t0 = s0
    x1, w1, b1, t1 = s1
    for side in (1, -1):
        a = (x0, side * w0, b0)
        b = (x1, side * w1, b1)
        c = (x1, side * w1, t1)
        d = (x0, side * w0, t0)
        cell = DARK if max(t0, t1) <= 44 else BODY
        if side > 0:
            m.quad(a, b, c, d, cell)
        else:
            m.quad(d, c, b, a, cell)
    m.quad((x0, -w0, t0), (x1, -w1, t1), (x1, w1, t1), (x0, w0, t0), BODY)
    m.quad((x0, w0, b0), (x1, w1, b1), (x1, -w1, b1), (x0, -w0, b0), DARK)

# nose / tail caps (lights + trim)
xN, wN, bN, tN = SECTIONS[0]
m.quad((xN, wN, bN), (xN, wN, tN), (xN, -wN, tN), (xN, -wN, bN), 5)
xT, wT, bT, tT = SECTIONS[-1]
m.quad((xT, -wT, bT), (xT, -wT, tT), (xT, wT, tT), (xT, wT, bT), 6)

# cabin: glass front/back/sides, painted roof
(cx0, cw0, cz0), (rx0, rw0, rz0), (rx1, rw1, rz1), (cx1, cw1, cz1) = CAB
m.quad((cx0, -cw0, cz0), (cx0, cw0, cz0), (rx0, rw0, rz0),
       (rx0, -rw0, rz0), 1)                                   # windshield
m.quad((cx1, cw1, cz1), (cx1, -cw1, cz1), (rx1, -rw1, rz1),
       (rx1, rw1, rz1), 1)                                    # rear glass
for side in (1, -1):
    a = (cx0, side * cw0, cz0)
    b = (rx0, side * rw0, rz0)
    c = (rx1, side * rw1, rz1)
    d = (cx1, side * cw1, cz1)
    if side > 0:
        m.quad(a, b, c, d, 1)
    else:
        m.quad(d, c, b, a, 1)
m.quad((rx0, -rw0, rz0), (rx0, rw0, rz0), (rx1, rw1, rz1),
       (rx1, -rw1, rz1), BODY)                                # roof
m.save("chassis")

# ------------------------------------------------------------------- wheel
w = Mesh()
R, HW, SEG = 34.0, 12.0, 12
HUB_R = 15.0
for k in range(SEG):
    a0 = 2 * math.pi * k / SEG
    a1 = 2 * math.pi * (k + 1) / SEG
    p0 = (R * math.cos(a0), 0, R * math.sin(a0))
    p1 = (R * math.cos(a1), 0, R * math.sin(a1))
    # tread
    w.quad((p0[0], -HW, p0[2]), (p1[0], -HW, p1[2]),
           (p1[0], HW, p1[2]), (p0[0], HW, p0[2]), 3)
    for side in (1, -1):
        # tire ring face
        q0 = (HUB_R * math.cos(a0), side * HW, HUB_R * math.sin(a0))
        q1 = (HUB_R * math.cos(a1), side * HW, HUB_R * math.sin(a1))
        f0 = (p0[0], side * HW, p0[2])
        f1 = (p1[0], side * HW, p1[2])
        hub0 = (0, side * HW, 0)
        if side > 0:
            w.quad(f0, f1, q1, q0, 3)
            w.tri(q0, q1, hub0, 4)
        else:
            w.quad(q0, q1, f1, f0, 3)
            w.tri(hub0, q1, q0, 4)
w.save("wheel")

write_palette()
(OUT / "LICENSE.txt").write_text(
    "Placeholder car meshes generated by pipeline/make_car_mesh.py.\n"
    "Original procedural content, no third-party assets.\n")
print("wrote LICENSE.txt")
