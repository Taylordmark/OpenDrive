"""Track B pass 2 (PLAN-VISUALS.md): tire and rain-bead detail textures for
the Realistic car materials (unreal/build_car_materials_real.py).

  T_TireTread.png     one tread pitch. u = around the tire (the material
                      repeats it TREAD_PITCHES times), v = across the tread.
  T_TireSidewall.png  half the sidewall ring (the material repeats it
                      twice). u = around, v = rim -> shoulder. Raised lettering.
  T_CarRainDrops.png  tiling beads of water, for the paint and glass.

RGB = tangent normal 0.5 + 0.5*n in the texture's own (u, v) frame (the
material builds that frame from the wheel's local position, so the sign
convention is just "n = (-dh/du, -dh/dv, 1)" with u along columns and v
down rows). A = mask: groove depth, letter, or bead coverage.

All procedural, fixed seed. The sidewall text is set in Aileron Regular
(Sora Sagano, dotcolon.net, CC0), the font Pillow embeds as load_default().

Usage:  python content_src/realistic/cars/make_car_detail.py   (writes + self-checks)
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent

# wheel geometry the material also assumes (pipeline/make_car_meshes.py build_wheel)
R, HW = 34.0, 13.0
RIM_R = 0.64 * R
SHOULDER_R, TREAD_HALF = R - 4.0, HW - 4.0      # flat tread spans |y| < TREAD_HALF
TREAD_PITCHES = 56                               # tread blocks around the tire
SIDE_REPEAT = 2                                  # sidewall text runs twice around


def blur(a, sigma_px):
    """Periodic gaussian blur (FFT), so tiles stay seamless."""
    fy, fx = np.fft.fftfreq(a.shape[0])[:, None], np.fft.fftfreq(a.shape[1])[None, :]
    k = np.exp(-2 * (np.pi * sigma_px) ** 2 * (fy ** 2 + fx ** 2))
    return np.real(np.fft.ifft2(np.fft.fft2(a) * k))


def normal_rgba(h, cm_per_px_u, cm_per_px_v, depth_cm, mask):
    """Tangent normal from height h (0..1 = depth_cm), periodic differences."""
    du = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) / (2 * cm_per_px_u) * depth_cm
    dv = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) / (2 * cm_per_px_v) * depth_cm
    n = np.stack([-du, -dv, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return np.concatenate([0.5 + 0.5 * n, np.clip(mask, 0, 1)[..., None]], -1)


def tread():
    W, H = 128, 512                                  # u: one pitch, v: tread width
    pitch_cm = 2 * np.pi * R / TREAD_PITCHES
    u = (np.arange(W) + 0.5) / W
    v = (np.arange(H) + 0.5) / H
    U, V = np.meshgrid(u, v)
    g = np.zeros((H, W))
    for c in (0.27, 0.5, 0.73):                       # three circumferential grooves
        g = np.maximum(g, np.abs(V - c) < 0.035)
    # shoulder slots: lateral, slightly angled, open to the edge
    slot = np.abs(((U + (V - 0.5) * 0.35) % 1.0) - 0.5) < 0.09
    g = np.maximum(g, slot & ((V < 0.2) | (V > 0.8)))
    # sipes across the two middle ribs, offset half a pitch
    sipe = np.abs(((U + 0.5 + (V - 0.5) * 0.6) % 1.0) - 0.5) < 0.025
    g = np.maximum(g, sipe & (np.abs(V - 0.5) > 0.06) & (np.abs(V - 0.5) < 0.2))
    g = blur(g.astype(float), 2.0)
    h = 1.0 - g                                       # 1 = block top
    return normal_rgba(h, pitch_cm / W, 2 * TREAD_HALF / H, 0.12, g)


def sidewall():
    W, H = 2048, 256
    band_cm = SHOULDER_R - RIM_R
    circ_cm = 2 * np.pi * (RIM_R + band_cm / 2) / SIDE_REPEAT
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    big = ImageFont.load_default(size=int(H * 0.30))
    small = ImageFont.load_default(size=int(H * 0.11))
    # rows run rim (v=0, top) -> shoulder; text is drawn upside down so the
    # letter tops point out, away from the rim, as on a real tire
    for text, font, cx, cy in (("OPENDRIVE", big, 0.30, 0.42), ("SPORT RADIAL", big, 0.78, 0.42),
                               ("245/40 ZR18  97Y  TUBELESS", small, 0.30, 0.82),
                               ("M+S  MAX PRESS 350 kPa", small, 0.78, 0.82)):
        d.text((cx * W, cy * H), text, fill=255, font=font, anchor="mm")
    m = np.asarray(img.rotate(180), float) / 255.0
    # a thin raised rim-protector ring near the rim, and a fine ring near the shoulder
    v = (np.arange(H)[:, None] + 0.5) / H
    m = np.maximum(m, (np.abs(v - 0.06) < 0.025) * np.ones((1, W)))
    m = np.maximum(m, (np.abs(v - 0.97) < 0.008) * 0.6 * np.ones((1, W)))
    h = blur(m, 0.8)
    return normal_rgba(h, circ_cm / W, band_cm / H, 0.12, m)


def drops(size=512, n=420, seed=11):
    rng = np.random.default_rng(seed)
    h = np.zeros((size, size))
    yx = np.stack(np.meshgrid(np.arange(size) + 0.5, np.arange(size) + 0.5, indexing="ij"), -1)
    for _ in range(n):
        c = rng.uniform(0, size, 2)
        r = rng.choice([2.5, 3.5, 5.0, 7.0, 10.0], p=[0.35, 0.3, 0.2, 0.1, 0.05])
        d = (yx - c + size / 2) % size - size / 2              # wrapped
        q = 1.0 - ((d ** 2).sum(-1) / r ** 2)
        h = np.maximum(h, np.sqrt(np.clip(q, 0, 1)) * r / 10.0)  # spherical cap, height ~ radius
    mask = np.clip(h * 12.0, 0, 1)
    return normal_rgba(h, 0.04, 0.04, 0.25, mask)             # 512 px = 20 cm


def check(name, img):
    assert img.min() >= 0.0 and img.max() <= 1.0, name
    n = img[..., :3] * 2 - 1
    assert np.allclose(np.linalg.norm(n, axis=-1), 1.0, atol=1e-6), f"{name}: unit normals"
    assert (n[..., 2] > 0.2).all(), f"{name}: no normal past ~80 degrees"
    assert abs(n[..., 0].mean()) < 0.02 and abs(n[..., 1].mean()) < 0.02, f"{name}: no net tilt"
    assert 0.02 < img[..., 3].mean() < 0.6, f"{name}: mask covers part of the tile"


if __name__ == "__main__":
    for fname, fn in (("T_TireTread.png", tread), ("T_TireSidewall.png", sidewall),
                      ("T_CarRainDrops.png", drops)):
        img = fn()
        check(fname, img)
        assert np.array_equal(fn(), img), f"{fname}: deterministic"
        Image.fromarray((img * 255 + 0.5).astype(np.uint8), "RGBA").save(HERE / fname)
        print(f"wrote {fname} {img.shape[1]}x{img.shape[0]}, mask {img[..., 3].mean():.2f} - checks passed")
