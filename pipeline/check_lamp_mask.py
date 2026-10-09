"""Lamp lens masks (2026-10-09, Taylor: "the backup lights are flickering
while driving forward"). M_CarLights tells lenses apart by palette colour
(unreal/apply_car_lights_material.py LAMP_HLSL). A blend of palette
cells could read a reverse+tail mix as a HEADLIGHT cell, so the mask also
gates on the car's local X: head glow front only, reverse rear only. (The
palette is nearest-filtered with no mips, so this was not the flicker's
cause - that is still open.) Mirrors LAMP_HLSL over blends of the lamp cells and checks no
rear pixel ever gets head glow and no front pixel reverse glow.

Usage:  python pipeline/check_lamp_mask.py
"""
import numpy as np

HEAD, TAIL, REV = np.array([0.93, 0.85, 0.60]), np.array([0.62, 0.05, 0.03]), np.array([0.90, 0.92, 0.95])
HEAD_GLOW, TAIL_GLOW, REV_GLOW = 300.0, 25.0, 0.0     # night, driving forward (reverse off)


def lamp(pal, local_x, gated):
    sat = lambda v: np.clip(v, 0.0, 1.0)
    front = 1.0 if local_x >= 0 else 0.0
    head = sat((pal[1] - 0.3) * 10.0) * (front if gated else 1.0)
    rev = sat((pal[2] - 0.75) * 10.0) * ((1.0 - front) if gated else 1.0)
    e = (1 - head) * np.array([1.0, 0.03, 0.02]) * TAIL_GLOW + head * np.array([1.0, 0.95, 0.85]) * HEAD_GLOW
    return (1 - rev) * e + rev * np.ones(3) * REV_GLOW


def worst_rear(gated):
    w = 0.0
    for a, b in ((REV, TAIL), (REV, HEAD), (TAIL, HEAD)):
        for t in np.linspace(0, 1, 101):
            w = max(w, lamp(a * (1 - t) + b * t, -200.0, gated).max())
    return w


def main():
    old, new = worst_rear(False), worst_rear(True)
    print(f"brightest rear lens pixel, reverse off: old mask {old:.0f} nits, gated {new:.0f} nits")
    assert old > 100.0, "old mask reproduces the flash (head glow on a rear lens)"
    assert new <= TAIL_GLOW + 1e-6, "rear lenses never exceed tail glow while not reversing"
    assert lamp(HEAD, 200.0, True).max() == HEAD_GLOW and lamp(REV, -200.0, True).max() == REV_GLOW
    print("PASS: no head glow on rear lenses")


if __name__ == "__main__":
    main()
