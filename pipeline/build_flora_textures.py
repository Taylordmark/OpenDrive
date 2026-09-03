"""Phase 3b flora textures (pipeline half of the art-direction shift toward
The Crew 2 grounded semi-photoreal look - see next steps.md). Produces:

  content_src/textures/flora/  - raw sourced/generated textures + manifest.json
      bark_conifer_polyhaven.jpg   (downloaded, CC0, Poly Haven)
      bark_willow_polyhaven.jpg    (downloaded, CC0, Poly Haven)
      manifest.json                (source URL + license per file)

  data/stages/tail_of_the_dragon/textures/  - production textures consumed
  by make_tree_meshes.py's UVs and imported by import_biome_flora.py:
      T_BarkConifer.png   (from bark_conifer_polyhaven.jpg - pine/spruce/fir/
                            krummholz all share this: real Rockies conifers
                            all read as "dark fissured bark" at gameplay range)
      T_BarkAspen.png     (procedural - pale trunk, black diamond/horizontal
                            scars; no good CC0 pale/birch-style bark found)
      T_BarkWillow.png    (from bark_willow_polyhaven.jpg)
      T_FoliageAtlas.png  (procedural RGBA alpha-cutout branch/leaf card
                            atlas - see flora_common.py for the cell grid)

ambientCG's "Bark" category has no pale/birch-toned set and no true
branch-card atlases exist there at all (checked: category=Foliage returns
zero results, q=leaf/branch/needle/conifer are ground-litter decals, not
alpha branch sprays) - per the task brief, foliage cards and aspen bark are
built procedurally instead of forced into a bad-fit download.

Run: python pipeline/build_flora_textures.py
"""

import json
import math
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from flora_common import ATLAS_W, ATLAS_H, CELL_PX, cell_pixel_box

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "content_src" / "textures" / "flora"
STAGE_DIR = ROOT / "data" / "stages" / "tail_of_the_dragon"
TEX_DIR = STAGE_DIR / "textures"
SRC_DIR.mkdir(parents=True, exist_ok=True)
TEX_DIR.mkdir(parents=True, exist_ok=True)

rng = np.random.default_rng(7)

DOWNLOADS = {
    "bark_conifer_polyhaven.jpg": {
        "url": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/"
               "knotted_pine_bark/knotted_pine_bark_diff_1k.jpg",
        "source": "https://polyhaven.com/a/knotted_pine_bark",
        "license": "CC0",
        "author": "Poly Haven",
    },
    "bark_willow_polyhaven.jpg": {
        "url": "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k/"
               "bark_willow/bark_willow_diff_1k.jpg",
        "source": "https://polyhaven.com/a/bark_willow",
        "license": "CC0",
        "author": "Poly Haven",
    },
}


def fetch_downloads():
    manifest = {}
    for fname, info in DOWNLOADS.items():
        dest = SRC_DIR / fname
        if not dest.exists():
            print(f"downloading {fname} ...")
            urllib.request.urlretrieve(info["url"], dest)
        manifest[fname] = {k: v for k, v in info.items() if k != "url"}
        manifest[fname]["downloaded"] = True
    manifest["T_BarkAspen.png"] = {
        "source": "generated", "license": "CC0 (procedural, no external asset)",
        "note": "pale trunk + black diamond scars, hand-authored gradient/noise",
    }
    manifest["T_FoliageAtlas.png"] = {
        "source": "generated", "license": "CC0 (procedural)",
        "note": "alpha-cutout branch/needle/leaf card atlas, 8 species cells",
    }
    (SRC_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote manifest.json")
    return manifest


def make_bark_production_copy(src_name, dst_name, size=1024):
    img = Image.open(SRC_DIR / src_name).convert("RGB").resize((size, size), Image.LANCZOS)
    img.save(TEX_DIR / dst_name)
    print(f"wrote {dst_name}")


def make_aspen_bark(size=1024):
    """Pale grey-white trunk with short black lenticel dashes + occasional
    dark diamond 'eye' scars - the signature quaking-aspen look, since no
    CC0 photo match was found. Built at 4x and downsampled for soft edges."""
    ss = 2
    px = size * ss
    base = np.array([0.80, 0.78, 0.72])
    # low-frequency blotchy noise (a few octaves of blurred random fields)
    # reads as natural mottling instead of the flat-noise "static" look.
    mottle = np.zeros((px, px, 1))
    for octave, amp in ((8, 0.05), (24, 0.03), (64, 0.015)):
        field = rng.normal(0, 1, (octave, octave, 1))
        field = np.array(Image.fromarray(
            ((field[:, :, 0] - field.min()) / (np.ptp(field) + 1e-6) * 255).astype(np.uint8)
        ).resize((px, px), Image.BICUBIC)) / 255.0
        mottle += (field[:, :, None] - 0.5) * 2 * amp
    img = np.clip(base[None, None, :] + mottle, 0, 1)
    pil = Image.fromarray((img * 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(pil)

    # short, faint lenticel dashes - real aspen bark shows these as SPARSE
    # small horizontal-ish marks, muted grey (not black), mostly smooth
    # bark in between. Previous version was 10x too dense and read as a
    # diseased/spotty texture rather than bark.
    n_lenticels = int(px * px / 32000)
    for _ in range(n_lenticels):
        cx = rng.uniform(0, px)
        cy = rng.uniform(0, px)
        w = rng.uniform(px * 0.005, px * 0.014)
        h = w * rng.uniform(0.16, 0.3)
        ang = rng.uniform(-10, 10)
        shade = int(rng.uniform(70, 115))
        col = (shade, shade - 2, shade - 6)
        dash = Image.new("RGBA", (int(w * 2) + 4, int(h * 2) + 4), (0, 0, 0, 0))
        ImageDraw.Draw(dash).ellipse(
            [2, 2, 2 + w * 2, 2 + h * 2], fill=col + (220,))
        dash = dash.rotate(ang, expand=True)
        pil.paste(dash, (int(cx - dash.width / 2), int(cy - dash.height / 2)), dash)

    # a handful of darker diamond/kidney "eye" scars at old-branch nodes -
    # the few genuinely dark marks aspen bark actually has.
    n_scars = int(px * px / 260000)
    for _ in range(n_scars):
        cx = rng.uniform(0, px)
        cy = rng.uniform(0, px)
        w = rng.uniform(px * 0.018, px * 0.038)
        h = w * rng.uniform(0.45, 0.7)
        shade = int(rng.uniform(35, 65))
        draw.ellipse([cx - w, cy - h, cx + w, cy + h], fill=(shade, shade, shade - 4))

    pil = pil.filter(ImageFilter.GaussianBlur(0.8 * ss)).resize((size, size), Image.LANCZOS)
    pil.save(TEX_DIR / "T_BarkAspen.png")
    print("wrote T_BarkAspen.png")


# ---------------------------------------------------------------------------
# Foliage atlas: alpha-cutout branch/leaf card sprites, supersampled 4x then
# downsampled for cheap antialiasing.

SS = 4  # supersample factor


def new_cell_canvas():
    return Image.new("RGBA", (CELL_PX * SS, CELL_PX * SS), (0, 0, 0, 0))


def needle_sprig(draw, cx, cy, s, color, n_needles, needle_len, spread_deg,
                  twig_color, n_twigs=5, jitter=0.15):
    """A twig with needles radiating off it - shared by pine/spruce/fir/
    krummholz, differentiated by density/length/spread/color."""
    # central twig: a gentle curve made of short segments
    pts = [(cx, cy + s * 0.42)]
    for i in range(1, n_twigs + 1):
        t = i / n_twigs
        pts.append((cx + s * 0.05 * math.sin(t * 2.2), cy + s * 0.42 - t * s * 0.8))
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=twig_color, width=max(1, int(s * 0.012)))
    for i in range(len(pts)):
        tx, ty = pts[i]
        n_here = max(1, int(n_needles * (0.5 + 0.5 * i / len(pts))))
        for _ in range(n_here):
            ang = math.radians(rng.uniform(-spread_deg, spread_deg) - 90)
            ln = needle_len * s * rng.uniform(0.7, 1.15)
            jx = rng.uniform(-jitter, jitter) * s * 0.05
            ex = tx + math.cos(ang) * ln + jx
            ey = ty + math.sin(ang) * ln
            c = tuple(int(min(255, max(0, v + rng.integers(-10, 10)))) for v in color)
            draw.line([(tx, ty), (ex, ey)], fill=c, width=max(1, int(s * 0.01)))


def make_conifer_cell(name, color, twig_color, n_needles, needle_len, spread_deg, n_sprigs):
    cv = new_cell_canvas()
    draw = ImageDraw.Draw(cv)
    s = CELL_PX * SS
    for _ in range(n_sprigs):
        cx = rng.uniform(s * 0.2, s * 0.8)
        cy = rng.uniform(s * 0.55, s * 0.95)
        scale = rng.uniform(0.65, 1.05)
        needle_sprig(draw, cx, cy, s * scale * 0.5, color, n_needles, needle_len,
                     spread_deg, twig_color)
    cv = cv.resize((CELL_PX, CELL_PX), Image.LANCZOS)
    return cv


def leaf_shape(draw, cx, cy, w, h, ang, color):
    """A simple pointed-oval leaf polygon, rotated."""
    pts = []
    n = 10
    for i in range(n):
        t = i / n * 2 * math.pi
        r = (abs(math.cos(t)) ** 0.7) * w * 0.5
        y = math.sin(t) * h * 0.5
        pts.append((r if math.cos(t) >= 0 else -r, y))
    ca, sa = math.cos(ang), math.sin(ang)
    poly = [(cx + px * ca - py * sa, cy + px * sa + py * ca) for px, py in pts]
    draw.polygon(poly, fill=color)


def make_leaf_cell(name, base_color, n_leaves, leaf_w, leaf_h, stem_color=None,
                    cluster_std=None):
    cv = new_cell_canvas()
    draw = ImageDraw.Draw(cv)
    s = CELL_PX * SS
    if stem_color:
        draw.line([(s * 0.5, s * 0.95), (s * 0.5, s * 0.15)], fill=stem_color,
                   width=max(2, int(s * 0.008)))
    for _ in range(n_leaves):
        if cluster_std:
            cx = np.clip(rng.normal(s * 0.5, s * cluster_std), s * 0.05, s * 0.95)
            cy = np.clip(rng.normal(s * 0.55, s * cluster_std), s * 0.1, s * 0.95)
        else:
            cx = rng.uniform(s * 0.15, s * 0.85)
            cy = rng.uniform(s * 0.1, s * 0.85)
        w = leaf_w * s * rng.uniform(0.7, 1.2)
        h = leaf_h * s * rng.uniform(0.7, 1.2)
        ang = rng.uniform(0, 2 * math.pi)
        c = tuple(int(min(255, max(0, v + rng.integers(-14, 14)))) for v in base_color)
        leaf_shape(draw, cx, cy, w, h, ang, c)
    cv = cv.resize((CELL_PX, CELL_PX), Image.LANCZOS)
    return cv


def make_atlas():
    atlas = Image.new("RGBA", (ATLAS_W, ATLAS_H), (0, 0, 0, 0))

    cells = {
        "pine_needle": make_conifer_cell(
            "pine_needle", (44, 92, 48, 255), (58, 40, 24, 255),
            n_needles=10, needle_len=0.34, spread_deg=22, n_sprigs=7),
        "spruce_needle": make_conifer_cell(
            "spruce_needle", (58, 104, 96, 255), (52, 36, 22, 255),
            n_needles=16, needle_len=0.22, spread_deg=45, n_sprigs=9),
        "fir_needle": make_conifer_cell(
            "fir_needle", (36, 78, 58, 255), (48, 34, 22, 255),
            n_needles=18, needle_len=0.18, spread_deg=60, n_sprigs=10),
        "krummholz_needle": make_conifer_cell(
            "krummholz_needle", (60, 88, 60, 255), (66, 48, 30, 255),
            n_needles=8, needle_len=0.20, spread_deg=50, n_sprigs=5),
        "aspen_leaf": make_leaf_cell(
            "aspen_leaf", (150, 172, 70, 255), n_leaves=26,
            leaf_w=0.16, leaf_h=0.16, stem_color=(90, 74, 40, 255)),
        "willow_leaf": make_leaf_cell(
            "willow_leaf", (108, 138, 92, 255), n_leaves=30,
            leaf_w=0.06, leaf_h=0.30, stem_color=(76, 58, 34, 255)),
        "sagebrush_leaf": make_leaf_cell(
            "sagebrush_leaf", (150, 155, 132, 255), n_leaves=90,
            leaf_w=0.055, leaf_h=0.14, cluster_std=0.22),
        "grass_blade": make_leaf_cell(
            "grass_blade", (120, 148, 74, 255), n_leaves=22,
            leaf_w=0.03, leaf_h=0.34),
    }
    for name, img in cells.items():
        left, top, _, _ = cell_pixel_box(name)
        atlas.alpha_composite(img, (left, top))

    atlas.save(TEX_DIR / "T_FoliageAtlas.png")
    print("wrote T_FoliageAtlas.png")


def main():
    fetch_downloads()
    make_bark_production_copy("bark_conifer_polyhaven.jpg", "T_BarkConifer.png")
    make_bark_production_copy("bark_willow_polyhaven.jpg", "T_BarkWillow.png")
    make_aspen_bark()
    make_atlas()


if __name__ == "__main__":
    main()
