"""Offline verification #2 (snowfall-snap fix, next steps.md sec 7.17):
re-implements AODSeasonController::ComputeParamsForClock's exact math AFTER
the 2026-09-07 SnowfallIntensity fix (ported 1:1 from
OpenDriveUE/Source/OpenDriveUE/ODSeasonController.cpp, HoldSeconds=2700,
TransitionSeconds=900, WinterLightSnowfall=0.2, WinterSnowfallDecaySeconds=
300) in numpy, mirroring verify_year_params.py (the original all-params
verify script; see repo scratchpad/verify_year_params.py).

Bug fixed: the fall->winter transition ramps SnowfallIntensity 0->1 (a
blizzard peak right at the transition's end, S4), but the OLD winter-hold
branch hard-set it to WinterLightSnowfall (0.2) the instant the hold began -
an instant step from 1.0 to 0.2 at exactly S4. Fix: the winter-hold branch
now eases DOWN from the transition's peak (1.0) to the hold level (0.2) via
smoothstep over the first WinterSnowfallDecaySeconds (300s / 5 in-game min)
of the hold, then stays flat - same curve shape as every other ramp.

This script computes BOTH the old (buggy) and new (fixed) SnowfallIntensity
curves for a before/after overlay, plots all six params (fixed version) plus
the per-sample derivative subplot, and reprints the original sequencing PASS
checks to confirm they still hold.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HOLD = 2700.0
TRANS = 900.0
WINTER_LIGHT_SNOWFALL = 0.2
WINTER_SNOWFALL_DECAY_SECONDS = 300.0

S0 = 0.0
S1 = HOLD
S2 = S1 + TRANS
S3 = S2 + HOLD
S4 = S3 + TRANS
S5 = S4 + HOLD
S6 = S5 + TRANS
S7 = S6 + HOLD
LOOP = S7 + TRANS
assert abs(LOOP - 4 * (HOLD + TRANS)) < 1e-6


def smoothstep01(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def compute_params(clock, fixed=True):
    """Vectorized 1:1 port of ComputeParamsForClock. clock: ndarray, seconds
    in [0, LOOP). fixed=False reproduces the OLD (buggy) hard-step winter
    hold value, for the before/after overlay only."""
    n = len(clock)
    fall = np.zeros(n)
    snow = np.zeros(n)
    snowfall = np.zeros(n)
    freeze = np.zeros(n)
    spring = np.zeros(n)
    leafdrop = np.zeros(n)

    # summer hold [S0,S1): everything 0 (already initialized)

    m = (clock >= S1) & (clock < S2)
    t = smoothstep01((clock[m] - S1) / TRANS)
    fall[m] = t

    m = (clock >= S2) & (clock < S3)
    fall[m] = 1.0

    m = (clock >= S3) & (clock < S4)
    t = smoothstep01((clock[m] - S3) / TRANS)
    fall[m] = 1.0
    leafdrop[m] = t
    snow[m] = t
    freeze[m] = t
    snowfall[m] = t

    m = (clock >= S4) & (clock < S5)
    fall[m] = 1.0
    leafdrop[m] = 1.0
    snow[m] = 1.0
    freeze[m] = 1.0
    if fixed:
        decay_t = smoothstep01((clock[m] - S4) / WINTER_SNOWFALL_DECAY_SECONDS)
        snowfall[m] = 1.0 + decay_t * (WINTER_LIGHT_SNOWFALL - 1.0)
    else:
        snowfall[m] = WINTER_LIGHT_SNOWFALL

    m = (clock >= S5) & (clock < S6)
    t = smoothstep01((clock[m] - S5) / TRANS)
    snow[m] = 1.0 - t
    freeze[m] = 1.0 - t
    snowfall[m] = WINTER_LIGHT_SNOWFALL * (1.0 - t)
    spring[m] = t
    fall[m] = 1.0 - t
    leafdrop[m] = 1.0 - t

    m = (clock >= S6) & (clock < S7)
    t = smoothstep01((clock[m] - S6) / HOLD)
    spring[m] = 1.0 - t

    # else (S7 <= clock < LOOP): spring->summer transition, everything at the
    # OutX defaults (0) set at top of the real function - matches C++ exactly
    # (SpringGreen already decayed to 0 by S7, FallBlend/LeafDrop already 0).

    return fall, snow, snowfall, freeze, spring, leafdrop


N = 14400 * 4  # 4 samples/sec, matching the original verify + the reported bug's 4 Hz sample rate
clock = np.linspace(0.0, LOOP, N, endpoint=False)
fall, snow, snowfall, freeze, spring, leafdrop = compute_params(clock, fixed=True)
_, _, snowfall_old, _, _, _ = compute_params(clock, fixed=False)

series = {
    "FallBlend": (fall, "tab:orange"),
    "LeafDrop": (leafdrop, "tab:brown"),
    "SnowCover": (snow, "tab:blue"),
    "SnowfallIntensity": (snowfall, "tab:cyan"),
    "FreezeBlend": (freeze, "tab:purple"),
    "SpringGreen": (spring, "tab:green"),
}

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

hold_spans = [(S0, S1, "summer"), (S2, S3, "fall"), (S4, S5, "winter"), (S6, S7, "spring")]
trans_spans = [(S1, S2, "sum->fall"), (S3, S4, "fall->win"), (S5, S6, "win->spr"), (S7, LOOP, "spr->sum")]

for ax in (ax1, ax2):
    for lo, hi, _ in hold_spans:
        ax.axvspan(lo / 3600, hi / 3600, color="0.92", zorder=0)
    for b in (S0, S1, S2, S3, S4, S5, S6, S7, LOOP):
        ax.axvline(b / 3600, color="0.75", lw=0.6, zorder=0)

for name, (data, color) in series.items():
    ax1.plot(clock / 3600, data, label=name, color=color, lw=1.8)

# before/after overlay for SnowfallIntensity only, zoomed via a dashed "old" line
ax1.plot(clock / 3600, snowfall_old, label="SnowfallIntensity (OLD, buggy)", color="red",
          lw=1.2, ls="--", alpha=0.8, zorder=5)

ax1.set_ylabel("param value [0,1]")
ax1.set_title("verify_snowfall_fix: all season params over the 4-hour loop (AFTER fix)\n"
              "red dashed = OLD SnowfallIntensity (instant 1.0->0.2 step at S4, for comparison)\n"
              "(shaded = 45-min HOLD; unshaded = 15-min smoothstep TRANSITION)")
ax1.legend(loc="upper right", ncol=3, fontsize=9)
ax1.set_ylim(-0.05, 1.15)

for lo, hi, label in hold_spans:
    ax1.text((lo + hi) / 2 / 3600, 1.08, label, ha="center", fontsize=9, color="0.35")
for lo, hi, label in trans_spans:
    ax1.text((lo + hi) / 2 / 3600, 1.08, label, ha="center", fontsize=8, color="0.6", style="italic")

# zoomed inset around the fall->winter->winter-hold boundary (S3..S4+decay) to
# make the before/after difference legible at the full-year scale
axins = ax1.inset_axes([0.60, 0.42, 0.38, 0.42])
zoom_lo, zoom_hi = (S3 - 200) / 3600, (S4 + WINTER_SNOWFALL_DECAY_SECONDS + 400) / 3600
zmask = (clock / 3600 >= zoom_lo) & (clock / 3600 <= zoom_hi)
axins.plot(clock[zmask] / 3600, snowfall[zmask], color="tab:cyan", lw=2.0, label="fixed")
axins.plot(clock[zmask] / 3600, snowfall_old[zmask], color="red", lw=1.4, ls="--", label="old (buggy)")
axins.axvline(S4 / 3600, color="0.4", lw=0.8)
axins.set_title("fall->winter boundary (S4) zoom", fontsize=8)
axins.legend(fontsize=6, loc="lower left")
axins.tick_params(labelsize=6)

# derivative / step-to-step delta plot to prove no discontinuities
dt = clock[1] - clock[0]
max_abs_delta = 0.0
max_deltas_per_series = {}
for name, (data, color) in series.items():
    delta = np.diff(data) / dt
    peak = np.max(np.abs(delta))
    max_deltas_per_series[name] = peak
    max_abs_delta = max(max_abs_delta, peak)
    ax2.plot(clock[1:] / 3600, delta, label=f"d({name})/dt", color=color, lw=1.2)

# old (buggy) SnowfallIntensity derivative overlay, to visualize the removed spike
delta_old = np.diff(snowfall_old) / dt
ax2.plot(clock[1:] / 3600, delta_old, label="d(SnowfallIntensity_OLD)/dt", color="red", lw=1.0, ls="--", alpha=0.7)
max_delta_old = np.max(np.abs(delta_old))

ax2.set_xlabel("clock [hours] (0 to 4h loop)")
ax2.set_ylabel("d(param)/dt [1/s]")
ax2.set_title("per-sample derivative - a true discontinuity would show as a spike/step "
              "far larger than the smoothstep ramps' bounded peak slope")
ax2.legend(loc="upper right", ncol=3, fontsize=8)

# sanity annotations (same sequencing checks as verify_year_params.py)
seq_notes = []
fall_reaches_1_at = clock[np.argmax(fall >= 0.999)]
leafdrop_starts_at = clock[np.argmax(leafdrop > 1e-6)]
seq_notes.append(f"FallBlend reaches 1.0 at t={fall_reaches_1_at:.0f}s (S2={S2:.0f}s); "
                  f"LeafDrop first >0 at t={leafdrop_starts_at:.0f}s (S3={S3:.0f}s) -> "
                  f"{'PASS: orange-turn completes before drop starts' if fall_reaches_1_at <= leafdrop_starts_at + 1e-6 else 'FAIL'}")
leafdrop_reaches_1_at = clock[np.argmax(leafdrop >= 0.999)]
heavy_snow_at = clock[np.argmax(snowfall >= 0.999)]
seq_notes.append(f"LeafDrop reaches 1.0 at t={leafdrop_reaches_1_at:.0f}s (S4={S4:.0f}s); "
                  f"SnowfallIntensity reaches its transition peak (1.0, now EASING DOWN to the "
                  f"WinterLightSnowfall={WINTER_LIGHT_SNOWFALL} hold level over "
                  f"{WINTER_SNOWFALL_DECAY_SECONDS:.0f}s instead of snapping) at t={heavy_snow_at:.0f}s -> "
                  f"{'PASS: leaf drop completes at/before heavy transition-snow peak' if leafdrop_reaches_1_at <= heavy_snow_at + 1e-6 else 'FAIL'}")
melt_starts_at = clock[np.argmax((clock >= S5))]
rebud_progress_at_melt_start = leafdrop[np.argmax(clock >= S5)]
seq_notes.append(f"Winter->spring transition starts at t={S5:.0f}s with LeafDrop={rebud_progress_at_melt_start:.3f} "
                  f"(should be 1.0, i.e. spring re-bud STARTS from fully-bare, following melt) -> "
                  f"{'PASS' if abs(rebud_progress_at_melt_start - 1.0) < 1e-3 else 'FAIL'}")
snowfall_at_s4_plus_decay = snowfall[np.argmax(clock >= S4 + WINTER_SNOWFALL_DECAY_SECONDS)]
seq_notes.append(f"SnowfallIntensity decays to WinterLightSnowfall by t=S4+{WINTER_SNOWFALL_DECAY_SECONDS:.0f}s: "
                  f"value={snowfall_at_s4_plus_decay:.4f} -> "
                  f"{'PASS' if abs(snowfall_at_s4_plus_decay - WINTER_LIGHT_SNOWFALL) < 1e-3 else 'FAIL'}")
seq_notes.append(f"max |d(SnowfallIntensity)/dt| AFTER fix = {max_deltas_per_series['SnowfallIntensity']:.6f} "
                  f"vs OLD (buggy) = {max_delta_old:.6f} -> "
                  f"{'PASS: no isolated spike, same order as other ramps' if max_deltas_per_series['SnowfallIntensity'] < 10 * max(v for k, v in max_deltas_per_series.items() if k != 'SnowfallIntensity') else 'FAIL'}")
seq_notes.append("max |d(param)/dt| per series: " + ", ".join(
    f"{k}={v:.6f}" for k, v in max_deltas_per_series.items()))
seq_notes.append(f"max |d(param)/dt| across all series (fixed) = {max_abs_delta:.6f} (finite, bounded - "
                  f"no discontinuity/step found at this sample rate ({1/dt:.1f} Hz, dt={dt:.4f}s))")

fig.text(0.01, -0.02, "\n".join(seq_notes), fontsize=9, family="monospace", va="top")

plt.tight_layout(rect=[0, 0.08, 1, 1])
OUT = r"C:\Users\taylo\AppData\Local\Temp\claude\C--Users-taylo-Coding-OpenDrive\26b7f002-8582-449d-bd00-11b035157f40\scratchpad\verify_snowfall_fix.png"
plt.savefig(OUT, dpi=130, bbox_inches="tight")
print("wrote", OUT)
for n in seq_notes:
    print(n)
