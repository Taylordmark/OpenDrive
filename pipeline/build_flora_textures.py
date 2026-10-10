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
import sys
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


def irregular_blob_points(cx, cy, r, n=16, jitter=0.4, elong=(1.0, 1.0), ang=0.0):
    pts = []
    ca, sa = math.cos(ang), math.sin(ang)
    for i in range(n):
        theta = 2 * math.pi * i / n
        rad = r * (1 + rng.uniform(-jitter, jitter))
        x = math.cos(theta) * rad * elong[0]
        y = math.sin(theta) * rad * elong[1]
        pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
    return pts


def make_bough_cell(name, color_dark, color_light, twig_color, n_blobs, elong,
                     n_edge_needles=120):
    """Dense, near-opaque conifer bough sprite (2026-09-03 fix - the earlier
    sparse-strand needle_sprig() read as "moss on a bare pole" rather than a
    branch once instanced at any density; live-drive feedback flagged
    tree_conifer specifically). A handful of large overlapping irregular
    blobs build up a solid needle mass, mottled dark/light for depth, with a
    thin fringe of individual needle strokes only along the silhouette edge
    to keep it from reading as a smooth cutout blob. Used for pine/spruce/
    fir, and (2026-09-25) krummholz too - its old sparse needle_sprig()
    cell read as a bare stick in grass, the same defect as above."""
    cv = new_cell_canvas()
    draw = ImageDraw.Draw(cv)
    s = CELL_PX * SS
    draw.line([(s * 0.5, s * 0.97), (s * 0.5, s * 0.2)], fill=twig_color,
              width=max(2, int(s * 0.014)))
    for _ in range(n_blobs):
        cx = rng.uniform(s * 0.22, s * 0.78)
        cy = rng.uniform(s * 0.1, s * 0.8)
        r = rng.uniform(s * 0.16, s * 0.26)
        el = (elong[0] * rng.uniform(0.85, 1.15), elong[1] * rng.uniform(0.85, 1.15))
        ang = rng.uniform(-0.35, 0.35)
        pts = irregular_blob_points(cx, cy, r, n=16, jitter=0.38, elong=el, ang=ang)
        t = rng.uniform(0.0, 1.0)
        col = tuple(int(color_dark[i] * (1 - t) + color_light[i] * t) for i in range(3))
        draw.polygon(pts, fill=col + (255,))
    # ragged silhouette fringe - short needle strokes scattered near the
    # blob-mass edges so the outline breaks up instead of reading smooth.
    for _ in range(n_edge_needles):
        cx = rng.uniform(s * 0.12, s * 0.88)
        cy = rng.uniform(s * 0.06, s * 0.85)
        ang = rng.uniform(0, 2 * math.pi)
        ln = rng.uniform(s * 0.05, s * 0.11)
        ex, ey = cx + math.cos(ang) * ln, cy + math.sin(ang) * ln
        col = tuple(int(min(255, max(0, c + rng.integers(-18, 12)))) for c in color_dark)
        draw.line([(cx, cy), (ex, ey)], fill=col, width=max(1, int(s * 0.007)))
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
        # pine/spruce/fir: dense near-opaque bough mass (2026-09-03 fix -
        # see make_bough_cell's docstring). Darker overall than the old
        # sparse-strand cells per the "near-opaque, dark green" direction.
        # 2026-09-03 brightness fix: the "near-opaque, dark green" direction
        # from the earlier bough-mass pass (see make_bough_cell's docstring)
        # overshot - combined with M_Foliage's two-sided masked shading
        # (backfaces get zero N.L and render pure black, see
        # unreal/build_flora_materials.py's MSM_TWO_SIDED_FOLIAGE fix), the
        # crowns read near-black/navy in-game (ScreenShot00247.png) instead
        # of sunlit mid forest-green. Brightened ~1.7-1.9x and pulled toward
        # a cleaner green (real lodgepole/spruce stands read medium green in
        # full sun, not near-black) while keeping each species' dark/light
        # mottling range and per-species hue (spruce stays cooler/bluer, fir
        # a touch yellower, matching real bark/needle color differences).
        "pine_needle": make_bough_cell(
            "pine_needle", (48, 100, 54), (86, 148, 84), (58, 40, 24, 255),
            n_blobs=9, elong=(0.8, 1.5)),
        "spruce_needle": make_bough_cell(
            "spruce_needle", (58, 108, 98), (96, 156, 140), (52, 36, 22, 255),
            n_blobs=11, elong=(1.0, 1.15)),
        "fir_needle": make_bough_cell(
            "fir_needle", (46, 94, 66), (80, 132, 94), (48, 34, 22, 255),
            n_blobs=11, elong=(1.1, 1.0)),
        # old sparse cell: still drawn (and thrown away below) only so the
        # shared rng stream - and every cell after this one - is unchanged
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
    # krummholz: dense like the other conifers but darker/olive (exposed,
    # wind-desiccated needles) and squat - wind-compacted mats, not boughs
    cells["krummholz_needle"] = make_bough_cell(
        "krummholz_needle", (40, 74, 50), (74, 108, 72), (54, 38, 24, 255),
        n_blobs=12, elong=(1.0, 0.8), n_edge_needles=160)
    for name, img in cells.items():
        left, top, _, _ = cell_pixel_box(name)
        atlas.alpha_composite(img, (left, top))

    atlas.save(TEX_DIR / "T_FoliageAtlas.png")
    print("wrote T_FoliageAtlas.png")


# ---------------------------------------------------------------------------
# Realistic mode (PLAN-VISUALS.md Track F): `python build_flora_textures.py
# --real` writes the _real textures beside the stylized ones and the manifest
# in content_src/realistic/flora/. T_FoliageAtlas_real keeps the stylized
# atlas LAYOUT (flora_common cells, same 8% margins) at 2x the resolution -
# M_Foliage_real is the parent of the season MIDs every foliage slot gets in
# Realistic, stylized meshes included, so each cell must mean the same
# thing. Cells are drawn as real sprays: needle fascicles on twigs, ovate
# aspen leaves on petioles, tapered grass blades, with back-to-front depth
# shading and colour-bled alpha edges (no dark mip halos). Bark: the same
# CC0 Poly Haven photo for conifers plus a tangent normal map derived from
# its luminance; aspen is procedural and tileable, with a matching normal.
# No downloads: only the local Poly Haven jpg and numpy/PIL.

REAL_DIR = ROOT / "content_src" / "realistic" / "flora"
RCELL = CELL_PX * 2   # 1024 px cells -> 4096x2048 atlas
RSS = 3               # supersample
rr = np.random.default_rng(1007)


def _P(x, y, s):
    """Normalized cell coords (0..1, y down; stems enter at y=1) -> canvas px
    inside the 8% UV margin cell_uv_rect leaves on every side."""
    return ((0.09 + 0.82 * x) * s, (0.09 + 0.82 * y) * s)


def _shade(col, k):
    return tuple(int(min(255, max(0, c * k))) for c in col) + (255,)


def _curve(x0, y0, ang, length, bend, n=8):
    """Polyline from (x0,y0) heading `ang` (radians, 0 = up the cell),
    turning by `bend` radians over its length."""
    pts, x, y = [(x0, y0)], x0, y0
    for i in range(n):
        a = ang + bend * i / n
        x, y = x + math.sin(a) * length / n, y - math.cos(a) * length / n
        pts.append((x, y))
    return pts


def _along(pts, f):
    i = min(int(f * (len(pts) - 1)), len(pts) - 2)
    t = f * (len(pts) - 1) - i
    (ax, ay), (bx, by) = pts[i], pts[i + 1]
    return ax + (bx - ax) * t, ay + (by - ay) * t, math.atan2(bx - ax, -(by - ay))


def _twigs(n_side, side_len, side_ang, third=0):
    """Main shoot + alternating side shoots (+ optional 3rd order) as
    normalized polylines, each with a thickness weight."""
    main = _curve(0.5 + rr.uniform(-0.04, 0.04), 1.0, rr.uniform(-0.12, 0.12), 0.9, rr.uniform(-0.3, 0.3))
    twigs = [(main, 1.0)]
    for i in range(n_side):
        f = 0.04 + 0.8 * (i + rr.uniform(0.2, 0.8)) / n_side
        x, y, a = _along(main, f)
        sgn = 1 if i % 2 else -1
        tw = _curve(x, y, a + sgn * side_ang * rr.uniform(0.8, 1.2), side_len * (1.1 - 0.6 * f) * rr.uniform(0.85, 1.15),
                    -sgn * rr.uniform(0.1, 0.4))
        twigs.append((tw, 0.6))
        for j in range(third):
            g = rr.uniform(0.3, 0.8)
            x2, y2, a2 = _along(tw, g)
            s2 = 1 if j % 2 else -1
            twigs.append((_curve(x2, y2, a2 + s2 * side_ang, side_len * 0.4 * (1.1 - g), 0.0, 4), 0.35))
    return twigs


def real_conifer_cell(dark, light, twig_col, n_side, side_len, side_ang, third, needle_len,
                      needle_ang, per_step, start=0.05, width=0.0032, tip_light=0.25):
    s = RCELL * RSS
    cv = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(cv)
    twigs = _twigs(n_side, side_len, side_ang, third)
    needles = []
    for pts, wgt in twigs:
        n = int(per_step * len(pts) * (0.6 + 0.4 * wgt))
        for k in range(n):
            f = start + (1 - start) * rr.uniform(0, 1) ** 0.7
            x, y, a = _along(pts, f)
            sgn = rr.choice([-1, 1])
            na = a + sgn * math.radians(needle_ang + rr.uniform(-15, 15))
            ln = needle_len * rr.uniform(0.75, 1.15)
            depth = rr.uniform(0, 1)                      # 0 = deep in the spray, 1 = front
            needles.append((depth, x, y, na, ln, f))
        d.line([_P(x, y, s) for x, y in pts], fill=twig_col, width=max(2, int(s * 0.007 * wgt)))
    needles.sort(key=lambda t: t[0])
    for depth, x, y, a, ln, f in needles:
        t = rr.uniform(0, 1)
        col = tuple(dk * (1 - t) + lt * t for dk, lt in zip(dark, light))
        if f > 0.8:                                       # paler new growth at the tips
            col = tuple(c * (1 - tip_light) + l * tip_light * 1.15 for c, l in zip(col, light))
        k = 0.55 + 0.6 * depth
        for off in (-0.1, 0.1):                           # needle pairs (fascicles)
            ex, ey = x + math.sin(a + off) * ln, y - math.cos(a + off) * ln
            d.line([_P(x, y, s), _P(ex, ey, s)], fill=_shade(col, k), width=max(2, int(s * width)))
    return cv


def _leaf_poly(x, y, ang, L, W, serr):
    """Ovate leaf from its base (x,y) along ang: widest ~35% up, pointed tip,
    fine serration. Returns (left half, right half) outlines."""
    halves = []
    for side in (-1, 1):
        pts = []
        for i in range(17):
            v = i / 16
            w = W * math.sin(math.pi * v ** 0.75) * (1.05 - 0.35 * v) * (1 + serr * (i % 2))
            px, py = side * w, v * L
            pts.append((x + px * math.cos(ang) + py * math.sin(ang), y + px * math.sin(ang) - py * math.cos(ang)))
        halves.append(pts)
    return halves


def real_leaf_cell(dark, light, twig_col, n_side, side_len, n_leaves, L, W, serr=0.03,
                   petiole=0.035, spread=1.2):
    s = RCELL * RSS
    cv = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(cv)
    twigs = _twigs(n_side, side_len, 0.7)
    for pts, wgt in twigs:
        d.line([_P(x, y, s) for x, y in pts], fill=twig_col, width=max(2, int(s * 0.006 * wgt)))
    leaves = []
    for _ in range(n_leaves):
        pts, _ = twigs[rr.integers(0, len(twigs))]
        x, y, a = _along(pts, rr.uniform(0.15, 1.0))
        a += rr.uniform(-spread, spread)
        bx, by = x + math.sin(a) * petiole, y - math.cos(a) * petiole
        leaves.append((rr.uniform(0, 1), x, y, bx, by, a + rr.uniform(-0.4, 0.4)))
    leaves.sort(key=lambda t: t[0])
    for depth, x, y, bx, by, a in leaves:
        d.line([_P(x, y, s), _P(bx, by, s)], fill=twig_col, width=max(2, int(s * 0.0025)))
        t = rr.uniform(0, 1)
        col = tuple(dk * (1 - t) + lt * t for dk, lt in zip(dark, light))
        k = 0.6 + 0.55 * depth
        ln, wd = L * rr.uniform(0.8, 1.2), W * rr.uniform(0.8, 1.2)
        left, right = _leaf_poly(bx, by, a, ln, wd, serr)
        lit = rr.uniform(0.85, 1.0)                       # the two halves catch light differently
        d.polygon([_P(*p, s) for p in left], fill=_shade(col, k * lit))
        d.polygon([_P(*p, s) for p in right], fill=_shade(col, k * (1.85 - lit)))
        tx, ty = bx + math.sin(a) * ln * 0.9, by - math.cos(a) * ln * 0.9
        d.line([_P(bx, by, s), _P(tx, ty, s)], fill=_shade(col, k * 1.25), width=max(1, int(s * 0.0018)))
    return cv


def real_grass_cell(n=34):
    s = RCELL * RSS
    cv = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(cv)
    blades = sorted(((rr.uniform(0, 1),) for _ in range(n)))
    for (depth,) in blades:
        x0 = 0.5 + rr.normal(0, 0.12)
        a = rr.normal(0, 0.28)
        L = rr.uniform(0.45, 0.95)
        W = rr.uniform(0.012, 0.022)
        pts = _curve(x0, 1.0, a, L, rr.normal(0, 0.5), n=10)
        dry = rr.random() < 0.18
        root, tip = ((70, 72, 40), (170, 160, 100)) if dry else ((45, 70, 32), (125, 160, 70))
        k = 0.6 + 0.5 * depth
        for i in range(len(pts) - 1):                     # tapered, root-dark quads
            t = i / (len(pts) - 1)
            w0, w1 = W * (1 - t), W * (1 - (i + 1) / (len(pts) - 1)) + 0.002
            (ax, ay), (bx, by) = pts[i], pts[i + 1]
            col = tuple(r * (1 - t) + p * t for r, p in zip(root, tip))
            d.polygon([_P(ax - w0 / 2, ay, s), _P(ax + w0 / 2, ay, s), _P(bx + w1 / 2, by, s),
                       _P(bx - w1 / 2, by, s)], fill=_shade(col, k))
    return cv


def _bleed(img):
    """Fill the RGB under transparent pixels with the nearby opaque colour so
    mips and bilinear filtering don't pull a dark halo into the cutout."""
    a = np.asarray(img, dtype=np.float32)
    rgb, al = a[..., :3], a[..., 3:] / 255.0
    fill = np.zeros_like(rgb)
    wsum = np.zeros_like(al)
    for r in (2, 8, 32):
        pre = Image.fromarray(np.uint8(np.clip(rgb * al, 0, 255)))
        wa = Image.fromarray(np.uint8(al[..., 0] * 255))
        pb = np.asarray(pre.filter(ImageFilter.GaussianBlur(r)), dtype=np.float32)
        wb = np.asarray(wa.filter(ImageFilter.GaussianBlur(r)), dtype=np.float32)[..., None] / 255.0
        take = (wsum < 1e-3) & (wb > 1e-3)
        fill = np.where(take, pb / np.maximum(wb, 1e-3), fill)
        wsum = np.where(take, 1.0, wsum)
    out = np.where(al > 0.99, rgb, fill * (1 - al) + rgb * al)
    return Image.fromarray(np.uint8(np.clip(np.concatenate([out, al * 255], -1), 0, 255)), "RGBA")


def make_atlas_real():
    cells = {
        # lodgepole: long paired needles, bottlebrush tufts toward the tips
        "pine_needle": real_conifer_cell((34, 66, 34), (96, 132, 62), (88, 64, 44, 255), 6, 0.6, 0.8, 0,
                                         0.085, 38, 120, start=0.25),
        # Engelmann spruce: short stiff blue-green needles, pinnate flat spray
        "spruce_needle": real_conifer_cell((40, 72, 70), (104, 140, 132), (92, 66, 46, 255), 10, 0.55, 0.9, 2,
                                           0.035, 55, 90),
        # subalpine fir: flat two-ranked dark needles, pale new growth
        "fir_needle": real_conifer_cell((28, 62, 44), (82, 124, 84), (80, 60, 40, 255), 9, 0.55, 1.0, 2,
                                        0.045, 75, 80, tip_light=0.35),
        "krummholz_needle": real_conifer_cell((38, 58, 36), (92, 108, 66), (74, 56, 38, 255), 11, 0.5, 0.95, 2,
                                              0.032, 60, 110),
        "aspen_leaf": real_leaf_cell((70, 110, 40), (150, 178, 80), (110, 96, 70, 255), 7, 0.5, 230,
                                     0.07, 0.03),
        "willow_leaf": real_leaf_cell((72, 98, 62), (140, 160, 112), (120, 84, 50, 255), 5, 0.5, 150,
                                      0.13, 0.012, serr=0.0, petiole=0.012, spread=0.6),
        "sagebrush_leaf": real_leaf_cell((112, 120, 104), (186, 192, 170), (96, 84, 66, 255), 12, 0.55, 700,
                                         0.04, 0.011, serr=0.12, petiole=0.006, spread=0.5),
        "grass_blade": real_grass_cell(),
    }
    atlas = Image.new("RGBA", (ATLAS_W * 2, ATLAS_H * 2), (0, 0, 0, 0))
    for name, img in cells.items():
        left, top, _, _ = cell_pixel_box(name)
        img = img.resize((RCELL, RCELL), Image.LANCZOS)
        lo, hi = int(RCELL * 0.085), int(RCELL * 0.915)      # nothing past the UV margin
        atlas.alpha_composite(img.crop((lo, lo, hi, hi)), (left * 2 + lo, top * 2 + lo))
    _bleed(atlas).save(TEX_DIR / "T_FoliageAtlas_real.png")
    print("wrote T_FoliageAtlas_real.png")


def _normal_from_height(h, strength):
    """Tangent-space normal map (UE/DirectX: green = -d/d(row)) from a
    tiling height field; np.roll keeps it tiling."""
    dx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5 * strength
    dy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5 * strength
    n = np.stack([-dx, dy, np.ones_like(h)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return Image.fromarray(np.uint8((n * 0.5 + 0.5) * 255))


def _highpass(lum, r):
    blur = np.asarray(Image.fromarray(np.uint8(lum * 255)).filter(ImageFilter.GaussianBlur(r)),
                      dtype=np.float32) / 255
    return lum - blur


def make_conifer_bark_real():
    img = Image.open(SRC_DIR / "bark_conifer_polyhaven.jpg").convert("RGB").resize((1024, 1024), Image.LANCZOS)
    a = np.asarray(img, dtype=np.float32) / 255
    lum = a @ [0.3, 0.59, 0.11]
    # lodgepole: thin grey-brown plates - pull the photo's warm cast toward grey
    grey = lum[..., None] * np.array([1.02, 0.96, 0.9])
    a = np.clip(a * 0.7 + grey * 0.3, 0, 1)
    Image.fromarray(np.uint8(a * 255)).save(TEX_DIR / "T_BarkConifer_real.png")
    _normal_from_height(_highpass(lum, 24), 9.0).save(TEX_DIR / "T_BarkConifer_real_N.png")
    print("wrote T_BarkConifer_real.png + _N")


def _tile_noise(px, cutoff, aniso=(1.0, 1.0)):
    """Periodic (tiling) smooth noise in ~[-1,1] via a low-passed FFT field."""
    f = np.fft.fft2(rr.normal(0, 1, (px, px)))
    ky, kx = np.meshgrid(np.fft.fftfreq(px) * aniso[1], np.fft.fftfreq(px) * aniso[0], indexing="ij")
    f *= np.exp(-(kx ** 2 + ky ** 2) / (2 * cutoff ** 2))
    n = np.real(np.fft.ifft2(f))
    return n / (np.abs(n).max() + 1e-9)


def make_aspen_bark_real(px=1024):
    """Quaking aspen: chalky green-white bark, faint vertical streaking,
    thin horizontal lenticels, and black eye/chevron scars where old limbs
    were. Tiles in both directions; height drives the normal map."""
    base = np.array([0.80, 0.81, 0.72])
    mott = _tile_noise(px, 0.004) * 0.06 + _tile_noise(px, 0.03) * 0.025
    streak = _tile_noise(px, 0.05, aniso=(1.0, 0.08)) * 0.03          # vertical grain
    green = _tile_noise(px, 0.006)
    a = base + (mott + streak)[..., None] + np.clip(green, 0, 1)[..., None] * np.array([-0.04, 0.0, -0.05])
    height = 0.5 + mott * 0.5 + streak * 2
    ss = 2
    mark = Image.new("L", (px * ss, px * ss), 0)          # darkness of features
    md = ImageDraw.Draw(mark)

    def wrapped(fn, x, y):
        for ox in (-px, 0, px):
            for oy in (-px, 0, px):
                fn((x + ox) * ss, (y + oy) * ss)

    for _ in range(260):                                   # lenticels: thin horizontal dashes
        x, y = rr.uniform(0, px), rr.uniform(0, px)
        w, h, v = rr.uniform(6, 22), rr.uniform(0.8, 2.0), int(rr.uniform(90, 170))
        wrapped(lambda X, Y: md.ellipse([X - w * ss, Y - h * ss, X + w * ss, Y + h * ss], fill=v), x, y)
    for _ in range(5):                                     # branch-scar "eyes" with brow chevrons
        x, y = rr.uniform(0, px), rr.uniform(0, px)
        w, h = rr.uniform(20, 40), rr.uniform(9, 16)

        def eye(X, Y, w=w, h=h):
            md.ellipse([X - w * ss, Y - h * ss, X + w * ss, Y + h * ss], fill=235)
            for sgn in (-1, 1):
                md.line([(X, Y - h * ss * 1.6), (X + sgn * w * ss * 2.2, Y + h * ss * 0.6)],
                        fill=170, width=int(h * ss * 0.3))
        wrapped(eye, x, y)
    m = np.asarray(mark.filter(ImageFilter.GaussianBlur(1.2 * ss)).resize((px, px), Image.LANCZOS),
                   dtype=np.float32)[..., None] / 255
    a = a * (1 - m * 0.88) + np.array([0.08, 0.075, 0.07]) * m * 0.88
    height = height - m[..., 0] * 0.35
    Image.fromarray(np.uint8(np.clip(a, 0, 1) * 255)).save(TEX_DIR / "T_BarkAspen_real.png")
    _normal_from_height(height, 6.0).save(TEX_DIR / "T_BarkAspen_real_N.png")
    print("wrote T_BarkAspen_real.png + _N")


def main_real():
    if not (SRC_DIR / "bark_conifer_polyhaven.jpg").exists():
        raise SystemExit("run build_flora_textures.py (stylized) first - it fetches the Poly Haven bark")
    make_conifer_bark_real()
    make_aspen_bark_real()
    make_atlas_real()
    REAL_DIR.mkdir(parents=True, exist_ok=True)
    gen = "CC0 (procedural, generated by this repo)"
    manifest = {
        "T_FoliageAtlas_real.png": {"source": "generated", "license": gen,
                                    "note": "build_flora_textures.py --real: needle/leaf/grass spray atlas"},
        "T_BarkAspen_real.png": {"source": "generated", "license": gen, "note": "procedural aspen bark + _N"},
        "T_BarkConifer_real.png": {"source": "https://polyhaven.com/a/knotted_pine_bark", "license": "CC0",
                                   "author": "Poly Haven",
                                   "note": "colour-graded copy + luminance-derived _N normal map"},
        "tree meshes (*_real.obj)": {"source": "generated", "license": gen,
                                     "note": "make_tree_meshes.py --real"},
    }
    (REAL_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote content_src/realistic/flora/manifest.json")


def main():
    if "--real" in sys.argv:
        main_real()
        return
    fetch_downloads()
    make_bark_production_copy("bark_conifer_polyhaven.jpg", "T_BarkConifer.png")
    make_bark_production_copy("bark_willow_polyhaven.jpg", "T_BarkWillow.png")
    make_aspen_bark()
    make_atlas()


if __name__ == "__main__":
    main()
