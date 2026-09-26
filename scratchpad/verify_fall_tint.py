"""Offline verification #4 (deciduous-arc follow-up): numpy simulation of the
green->orange fall-color shader math (fall_amount = clamp01((FallBlend -
thr_lo)/TURN_BAND), base_after_fall = lerp(atlas_green, fall_color,
fall_amount)) as a grid of simulated canopy patches, for FallBlend in
{0,0.25,0.5,0.75,1.0} x 3 PerInstanceRandom desync values, proving
patch-level turn (not a uniform whole-canopy fade) and full-orange
convergence at FallBlend=1.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from shader_noise_common import fall_or_drop_amount

GRID = 220  # simulated "canopy" patch resolution (world-space cm per axis)
CARD_SPAN_CM = 500.0  # one tree's crown footprint, matches ~230cm crown radius

# Base sampled colors (atlas green vs the fall_color constant baked in
# build_flora_materials.py's deciduous section: LinearColor(0.86,0.42,0.07)).
GREEN = np.array([0.30, 0.55, 0.18])
FALL_COLOR = np.array([0.86, 0.42, 0.07])

FALL_VALUES = [0.0, 0.25, 0.5, 0.75, 1.0]
PIR_VALUES = [0.15, 0.5, 0.85]  # PerInstanceRandom desync: early / mid / late turner
PIR_LABELS = ["PerInstanceRandom=0.15 (early turner)",
              "PerInstanceRandom=0.5 (typical)",
              "PerInstanceRandom=0.85 (late turner)"]

fig, axes = plt.subplots(len(PIR_VALUES), len(FALL_VALUES),
                          figsize=(3.1 * len(FALL_VALUES), 3.3 * len(PIR_VALUES)))

xs = np.linspace(0, CARD_SPAN_CM, GRID)
ys = np.linspace(0, CARD_SPAN_CM, GRID)
wx, wy = np.meshgrid(xs, ys)
# offset each "tree" (PIR row) to a different world-space location, since the
# noise is sampled from ABSOLUTE world position (shared across the level) -
# real trees at different spots naturally get different patch patterns even
# before PerInstanceRandom desync is added.
WORLD_OFFSETS = [(0.0, 0.0), (4000.0, 1500.0), (9000.0, 700.0)]

for row, (pir, label, (ox, oy)) in enumerate(zip(PIR_VALUES, PIR_LABELS, WORLD_OFFSETS)):
    for col, fall_blend in enumerate(FALL_VALUES):
        ax = axes[row, col]
        amt = fall_or_drop_amount(fall_blend, wx + ox, wy + oy, pir)
        img = GREEN[None, None, :] * (1 - amt[..., None]) + FALL_COLOR[None, None, :] * amt[..., None]
        ax.imshow(img, origin="lower", extent=[0, CARD_SPAN_CM, 0, CARD_SPAN_CM])
        frac_orange = np.mean(amt > 0.5)
        ax.set_title(f"FallBlend={fall_blend}\nmean amt={amt.mean():.2f}, >50% orange: {frac_orange*100:.0f}% of area",
                     fontsize=9)
        ax.set_xticks([])
        ax.set_yticks([])
        if col == 0:
            ax.set_ylabel(label, fontsize=9)

fig.suptitle("verify_fall_tint: green->orange patch-turn simulation "
             "(M_Foliage FallBlend wiring, build_flora_materials.py)\n"
             "3 PerInstanceRandom desync values (rows) x FallBlend 0..1 (cols) - "
             "patchy onset, not a uniform whole-canopy fade; FallBlend=1 converges fully orange for all rows",
             fontsize=11)
plt.tight_layout(rect=[0, 0, 1, 0.92])
OUT = r"C:\Users\taylo\AppData\Local\Temp\claude\C--Users-taylo-Coding-OpenDrive\26b7f002-8582-449d-bd00-11b035157f40\scratchpad\verify_fall_tint.png"
plt.savefig(OUT, dpi=130, bbox_inches="tight")
print("wrote", OUT)

# quantitative checks
for row, (pir, label, (ox, oy)) in enumerate(zip(PIR_VALUES, PIR_LABELS, WORLD_OFFSETS)):
    amt0 = fall_or_drop_amount(0.0, wx + ox, wy + oy, pir)
    amt1 = fall_or_drop_amount(1.0, wx + ox, wy + oy, pir)
    print(f"{label}: FallBlend=0 mean_amt={amt0.mean():.4f} (should be ~0), "
          f"FallBlend=1 mean_amt={amt1.mean():.4f} (should be 1.0 exactly - "
          f"{'PASS' if abs(amt1.mean()-1.0) < 1e-6 else 'FAIL'})")
