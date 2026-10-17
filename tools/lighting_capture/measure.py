"""Side-by-side + image statistics for lighting research (game shot vs reference photo).

  python measure.py <pairs.json> <outdir>
pairs.json: [{"id", "game": path, "ref": path, "label", "game_crop": [l,t,r,b] (optional, fractions),
              "ref_crop": [...]}]
Writes <outdir>/<id>.jpg (side by side with luminance histograms) and <outdir>/metrics.json.

Metrics are on sRGB-decoded linear luminance (Rec.709) of the (cropped) frame:
  mean_L / p05 / p50 / p95 in sRGB-encoded 0..255 (what the eye reads on screen),
  dr_stops = log2(p99/p01) of linear luminance (scene contrast actually shown),
  sat = mean HSV saturation, shadow_bv = mean (B-R)/(B+R) of the darkest 15 % (shadow tint, + = blue),
  sky_top / sky_horizon = mean sRGB of top 6 % / 30-36 % band rows (only meaningful with sky in frame),
  clip_hi / clip_lo = fraction of pixels >= 250 / <= 5 in any channel.
"""
import json, sys, os
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def lin(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def enc(l):
    return 255 * np.where(l <= 0.0031308, l * 12.92, 1.055 * np.power(np.maximum(l, 0), 1 / 2.4) - 0.055)


def crop(im, box):
    if not box:
        return im
    w, h = im.size
    return im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))


def metrics(im):
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    L = lin(a) @ np.array([0.2126, 0.7152, 0.0722])
    mx, mn = a.max(2), a.min(2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0)
    p = lambda q: float(np.percentile(L, q))
    dark = L <= np.percentile(L, 15)
    r, b = a[..., 0][dark], a[..., 2][dark]
    h = a.shape[0]
    return {
        "mean_L": float(enc(L.mean())), "p05": float(enc(p(5))), "p50": float(enc(p(50))), "p95": float(enc(p(95))),
        "dr_stops": float(np.log2(max(p(99), 1e-5) / max(p(1), 1e-5))),
        "sat": float(sat.mean()), "shadow_bv": float(((b - r) / np.maximum(b + r, 1)).mean()),
        "sky_top": [round(float(x)) for x in a[: max(1, int(h * 0.06))].reshape(-1, 3).mean(0)],
        "sky_horizon": [round(float(x)) for x in a[int(h * 0.30): int(h * 0.36)].reshape(-1, 3).mean(0)],
        "clip_hi": float((a.max(2) >= 250).mean()), "clip_lo": float((a.min(2) <= 5).mean()),
    }


def hist_img(im, w, h):
    a = np.asarray(im.convert("RGB"), dtype=np.float64)
    L = enc(lin(a) @ np.array([0.2126, 0.7152, 0.0722]))
    out = Image.new("RGB", (w, h), (24, 24, 28))
    d = ImageDraw.Draw(out)
    for ch, col in ((L, (230, 230, 230)), (a[..., 0], (220, 70, 70)), (a[..., 2], (80, 120, 230))):
        hist, _ = np.histogram(ch, bins=64, range=(0, 256))
        hist = hist / max(hist.max(), 1)
        pts = [(i * w / 63, h - 2 - v * (h - 6)) for i, v in enumerate(hist)]
        d.line(pts, fill=col, width=2)
    return out


def main():
    pairs = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    allm = {}
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    W, H, HH = 760, 428, 90
    for p in pairs:
        g = crop(Image.open(p["game"]).convert("RGB"), p.get("game_crop"))
        r = crop(Image.open(p["ref"]).convert("RGB"), p.get("ref_crop"))
        mg, mr = metrics(g), metrics(r)
        allm[p["id"]] = {"label": p.get("label", ""), "game": mg, "ref": mr}
        sheet = Image.new("RGB", (2 * W + 10, H + HH + 30), (16, 16, 18))
        for k, (im, m, tag) in enumerate(((g, mg, "OpenDrive"), (r, mr, "Reference"))):
            t = im.copy()
            t.thumbnail((W, H))
            x = k * (W + 10)
            sheet.paste(t, (x + (W - t.width) // 2, (H - t.height) // 2))
            sheet.paste(hist_img(im, W, HH), (x, H + 30))
            ImageDraw.Draw(sheet).text(
                (x + 6, H + 4),
                f"{tag}  mean {m['mean_L']:.0f}  p5 {m['p05']:.0f}  p95 {m['p95']:.0f}  DR {m['dr_stops']:.1f} st  sat {m['sat']:.2f}",
                fill=(235, 235, 235), font=font)
        sheet.save(os.path.join(out, p["id"] + ".jpg"), quality=88)
        print(p["id"], "game", {k: mg[k] for k in ("mean_L", "dr_stops", "sat", "shadow_bv")},
              "ref", {k: mr[k] for k in ("mean_L", "dr_stops", "sat", "shadow_bv")})
    json.dump(allm, open(os.path.join(out, "metrics.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
