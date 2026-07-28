"""Second drivable car - the "GT" (garage groundwork, 2026-07-24). A lower,
wider, sleeker low-poly silhouette in an orange palette, distinct from the
placeholder coupe (make_car_mesh.py). Deliberately keeps the SAME axle
geometry contract (front axle x=+132, rear x=-128, half-track 84, wheel
radius 34) so UODVehicleMovement's wheel positions need no special-casing -
the GT's faster/grippier character comes from its UODVehicleDef stats
(AWD, higher grip/downforce, taller final drive), not its footprint.

Self-contained (own Mesh class) so it can't perturb the working coupe output.

Outputs: data/cars/gt/{chassis_gt.obj, wheel_gt.obj, T_CarPalette_GT.png}
"""

import math
from pathlib import Path

import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "data" / "cars" / "gt"
OUT.mkdir(parents=True, exist_ok=True)

# same cell roles as the coupe, restyled to an orange supercar
PALETTE = [
    (0.80, 0.28, 0.02),   # 0 body paint (orange)
    (0.05, 0.08, 0.11),   # 1 glass
    (0.04, 0.04, 0.05),   # 2 trim / bumpers / rockers
    (0.03, 0.03, 0.035),  # 3 tire
    (0.55, 0.55, 0.58),   # 4 hub (bright silver)
    (0.95, 0.90, 0.70),   # 5 headlight
    (0.70, 0.06, 0.03),   # 6 taillight
    (0.42, 0.12, 0.01),   # 7 body dark (lower)
]
N_CELL = len(PALETTE)


def cell_uv(i):
    return ((i + 0.5) / N_CELL, 0.5)


class Mesh:
    def __init__(self):
        self.tris = []

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


def write_palette():
    cell = 32
    img = np.zeros((cell, cell * N_CELL, 3), dtype=np.float32)
    for i, c in enumerate(PALETTE):
        img[:, i * cell:(i + 1) * cell] = c
    Image.fromarray((np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)) \
        .save(OUT / "T_CarPalette_GT.png")
    print("wrote T_CarPalette_GT.png")


# ------------------------------------------------------------------ chassis
# lofted profile sections front to back: (x, half_width, z_bottom, z_top) -
# lower and wider than the coupe (roof 90 vs 116), a fastback stance.
SECTIONS = [
    (224.0, 44.0, 2.0, 20.0),     # low pointed nose
    (208.0, 70.0, -4.0, 30.0),    # front splitter/bumper
    (150.0, 80.0, -10.0, 38.0),   # front fender (wide, low)
    (82.0, 80.0, -12.0, 42.0),    # cowl (windshield base)
    (-118.0, 80.0, -12.0, 42.0),  # cabin rear base
    (-188.0, 78.0, -8.0, 36.0),   # rear haunch (wide)
    (-222.0, 58.0, -2.0, 26.0),   # low tail
]
ROOF_Z, ROOF_W = 90.0, 50.0       # low, narrow fastback roof
CAB = [(82.0, 80.0, 42.0), (46.0, ROOF_W, ROOF_Z),
       (-104.0, ROOF_W, ROOF_Z), (-118.0, 80.0, 42.0)]

m = Mesh()
BODY, DARK = 0, 7

for s0, s1 in zip(SECTIONS, SECTIONS[1:]):
    x0, w0, b0, t0 = s0
    x1, w1, b1, t1 = s1
    for side in (1, -1):
        a = (x0, side * w0, b0)
        b = (x1, side * w1, b1)
        c = (x1, side * w1, t1)
        d = (x0, side * w0, t0)
        cell = DARK if max(t0, t1) <= 34 else BODY
        if side > 0:
            m.quad(a, b, c, d, cell)
        else:
            m.quad(d, c, b, a, cell)
    m.quad((x0, -w0, t0), (x1, -w1, t1), (x1, w1, t1), (x0, w0, t0), BODY)
    m.quad((x0, w0, b0), (x1, w1, b1), (x1, -w1, b1), (x0, -w0, b0), DARK)

xN, wN, bN, tN = SECTIONS[0]
m.quad((xN, wN, bN), (xN, wN, tN), (xN, -wN, tN), (xN, -wN, bN), 5)
xT, wT, bT, tT = SECTIONS[-1]
m.quad((xT, -wT, bT), (xT, -wT, tT), (xT, wT, tT), (xT, wT, bT), 6)

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
m.save("chassis_gt")

# ------------------------------------------------------------------- wheel
w = Mesh()
R, HW, SEG = 34.0, 13.0, 12       # same radius as the coupe; slightly wider
HUB_R = 17.0
for k in range(SEG):
    a0 = 2 * math.pi * k / SEG
    a1 = 2 * math.pi * (k + 1) / SEG
    p0 = (R * math.cos(a0), 0, R * math.sin(a0))
    p1 = (R * math.cos(a1), 0, R * math.sin(a1))
    w.quad((p0[0], -HW, p0[2]), (p1[0], -HW, p1[2]),
           (p1[0], HW, p1[2]), (p0[0], HW, p0[2]), 3)
    for side in (1, -1):
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
w.save("wheel_gt")

write_palette()
(OUT / "LICENSE.txt").write_text(
    "Placeholder GT car meshes generated by pipeline/make_car_gt.py.\n"
    "Original procedural content, no third-party assets.\n")
print("wrote LICENSE.txt")
