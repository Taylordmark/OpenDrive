"""Offline verification #3 (deciduous-arc follow-up): numpy simulation of the
leaf-dissolve shader math (drop_amount = clamp01((LeafDrop - thr_lo)/
TURN_BAND), opacity_after_drop = atlas_alpha * (1 - drop_amount), then
alpha-cutout clipped at opacity_mask_clip_value=0.35 - exactly
build_flora_materials.py's M_Foliage wiring) rendered as a grid of canopy-
card opacity images at LeafDrop = 0, 0.25, 0.5, 0.75, 1.0 for 3
PerInstanceRandom values, to prove patchy (not uniform-fade) erosion and
per-tree desync.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shader_noise_common import fall_or_drop_amount

GRID = 220
CARD_SPAN_CM = 500.0
OPACITY_CLIP = 0.35  # m_fol.opacity_mask_clip_value, set in build_flora_materials.py

LEAFDROP_VALUES = [0.0, 0.25, 0.5, 0.75, 1.0]
PIR_VALUES = [0.15, 0.5, 0.85]
PIR_LABELS = ["PerInstanceRandom=0.15 (early dropper)",
              "PerInstanceRandom=0.5 (typical)",
              "PerInstanceRandom=0.85 (late dropper)"]
WORLD_OFFSETS = [(0.0, 0.0), (4000.0, 1500.0), (9000.0, 700.0)]

# Simulated "atlas alpha" for a leaf card - not perfectly uniform (real cards
# have leaf-silhouette cutouts), but a soft radial falloff is enough to show
# the dissolve mechanic operates ON TOP of the base alpha-cutout shape,
# rather than replacing it.
xs = np.linspace(-1, 1, GRID)
ys = np.linspace(-1, 1, GRID)
gx, gy = np.meshgrid(xs, ys)
base_alpha = np.clip(1.0 - (gx**2 + gy**2) * 0.6, 0.0, 1.0)

wx_span = np.linspace(0, CARD_SPAN_CM, GRID)
wy_span = np.linspace(0, CARD_SPAN_CM, GRID)
wx, wy = np.meshgrid(wx_span, wy_span)

fig, axes = plt.subplots(len(PIR_VALUES), len(LEAFDROP_VALUES),
                          figsize=(3.1 * len(LEAFDROP_VALUES), 3.3 * len(PIR_VALUES)))

for row, (pir, label, (ox, oy)) in enumerate(zip(PIR_VALUES, PIR_LABELS, WORLD_OFFSETS)):
    for col, leafdrop in enumerate(LEAFDROP_VALUES):
        ax = axes[row, col]
        drop_amt = fall_or_drop_amount(leafdrop, wx + ox, wy + oy, pir)
        opacity = base_alpha * (1.0 - drop_amt)
        visible = opacity >= OPACITY_CLIP  # alpha-cutout: below clip = fully discarded (bare branch shows through)
        img = np.zeros((GRID, GRID, 3))
        img[..., 1] = np.where(visible, 0.15 + 0.55 * opacity, 0.0)  # green leaf where visible
        # bare/dropped area rendered as background twig-brown so the "reveals
        # branch geometry underneath" behavior is visible
        img[~visible] = [0.55, 0.40, 0.28]
        ax.imshow(img, origin="lower", extent=[0, CARD_SPAN_CM, 0, CARD_SPAN_CM])
        pct_dropped = 100.0 * np.mean(~visible)
        ax.set_title(f"LeafDrop={leafdrop}\n{pct_dropped:.0f}% of card opacity-clipped (bare)", fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        if col == 0:
            ax.set_ylabel(label, fontsize=9)

fig.suptitle("verify_canopy_dissolve: leaf-dissolve opacity-mask simulation "
             "(M_Foliage LeafDrop wiring, build_flora_materials.py)\n"
             "green = remaining leaf card opacity; brown = alpha-cutout-clipped (bare twig/branch shows through) - "
             "erosion is patchy per-pixel, not a uniform card-wide fade",
             fontsize=11)
plt.tight_layout(rect=[0, 0, 1, 0.92])
OUT = r"C:\Users\taylo\AppData\Local\Temp\claude\C--Users-taylo-Coding-OpenDrive\26b7f002-8582-449d-bd00-11b035157f40\scratchpad\verify_canopy_dissolve.png"
plt.savefig(OUT, dpi=130, bbox_inches="tight")
print("wrote", OUT)

for row, (pir, label, (ox, oy)) in enumerate(zip(PIR_VALUES, PIR_LABELS, WORLD_OFFSETS)):
    d0 = fall_or_drop_amount(0.0, wx + ox, wy + oy, pir)
    d1 = fall_or_drop_amount(1.0, wx + ox, wy + oy, pir)
    print(f"{label}: LeafDrop=0 mean drop_amt={d0.mean():.4f} (should be 0 exactly - "
          f"{'PASS' if d0.mean() == 0.0 else 'FAIL'}), "
          f"LeafDrop=1 mean drop_amt={d1.mean():.4f} (should be ~1.0)")
