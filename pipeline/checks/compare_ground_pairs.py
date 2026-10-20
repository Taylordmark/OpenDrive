"""Side-by-side game vs reference + colour stats (terrain realism research).
python pipeline/checks/compare_ground_pairs.py <caps_dir> <refs_dir> <out_dir> spot=ref_file ...
Stats on the lower 45% of each frame minus its middle third (ground band; sky,
car and HUD excluded) and full frame:
mean sRGB-linear luminance, median chroma (HSV saturation), mean hue of
saturated pixels, local-contrast (std of luma in 16 px blocks, = texture detail).
"""
import json, sys, colorsys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw

def lin(a): a = a / 255.0; return np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
L = np.array([0.2126, 0.7152, 0.0722])

def stats(im):
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    out = {}
    g = a[int(a.shape[0] * 0.55):]
    w3 = g.shape[1] // 3   # drop the middle third: the chase-cam car + HUD sit there
    for name, band in (("ground", np.concatenate([g[:, :w3], g[:, -w3:]], axis=1)), ("full", a)):
        y = lin(band) @ L
        mx, mn = band.max(-1), band.min(-1)
        sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
        h, w = y.shape[0] // 16 * 16, y.shape[1] // 16 * 16
        blocks = y[:h, :w].reshape(h // 16, 16, w // 16, 16).std(axis=(1, 3))
        hues = [colorsys.rgb_to_hsv(*(p / 255))[0] for p in band.reshape(-1, 3)[::97] if p.max() - p.min() > 25]
        hv = np.array(hues) * 2 * np.pi if hues else np.zeros(1)
        hue = (np.degrees(np.arctan2(np.sin(hv).mean(), np.cos(hv).mean())) % 360) if hues else -1
        out[name] = dict(luma=float(y.mean()), luma_p90=float(np.percentile(y, 90)),
                         sat=float(np.median(sat)), hue=float(hue),
                         detail=float(np.median(blocks) / max(y.mean(), 1e-4)))
    return out

def hist_strip(im, w, h=70):
    a = np.asarray(im.convert("RGB"))
    a = a[int(a.shape[0] * 0.55):]
    hsv = np.asarray(Image.fromarray(a).convert("HSV"), dtype=np.float64).reshape(-1, 3)
    hsv = hsv[hsv[:, 1] > 30]
    canvas = Image.new("RGB", (w, h), (24, 24, 28))
    if len(hsv):
        hist, _ = np.histogram(hsv[:, 0], bins=72, range=(0, 256))
        hist = hist / hist.max()
        d = ImageDraw.Draw(canvas)
        bw = w / 72
        for i, v in enumerate(hist):
            r, g, b = colorsys.hsv_to_rgb(i / 72, 0.7, 0.9)
            d.rectangle([i * bw, h - v * (h - 4), (i + 1) * bw - 1, h], fill=(int(r * 255), int(g * 255), int(b * 255)))
    return canvas

if __name__ == "__main__":
    caps, refs, out = map(Path, sys.argv[1:4]); out.mkdir(parents=True, exist_ok=True)
    allstats = {}
    for pair in sys.argv[4:]:
        spot, ref = pair.split("=")
        g = Image.open(caps / f"{spot}.png").convert("RGB"); r = Image.open(refs / ref).convert("RGB")
        H = 540
        g = g.resize((int(g.width * H / g.height), H)); r = r.resize((int(r.width * H / r.height), H))
        sg, sr = stats(g), stats(r)
        allstats[spot] = {"game": sg, "ref": sr, "ref_file": ref}
        W = g.width + r.width + 12
        sbs = Image.new("RGB", (W, H + 70 + 6), (24, 24, 28))
        sbs.paste(g, (0, 0)); sbs.paste(r, (g.width + 12, 0))
        sbs.paste(hist_strip(g, g.width), (0, H + 6)); sbs.paste(hist_strip(r, r.width), (g.width + 12, H + 6))
        sbs.save(out / f"sbs_{spot}.jpg", quality=86)
        print(spot, json.dumps({k: {kk: {a: round(b, 3) for a, b in vv.items()} for kk, vv in v.items()} for k, v in (("game", sg), ("ref", sr))}))
    (out / "stats.json").write_text(json.dumps(allstats, indent=1))
