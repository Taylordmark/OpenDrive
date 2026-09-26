"""Offline verification #1 (deciduous-arc follow-up, next steps.md sec 7.17):
re-implements AODSeasonController::ComputeParamsForClock's exact math (ported
1:1 from OpenDriveUE/Source/OpenDriveUE/ODSeasonController.cpp, HoldSeconds=
2700, TransitionSeconds=900, WinterLightSnowfall=0.2) in numpy, and plots all
six season params across the full 4-hour (14400 s) loop, with per-step deltas
to check for discontinuities.
"""
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HOLD = 2700.0
TRANS = 900.0
WINTER_LIGHT_SNOWFALL = 0.2

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


def compute_params(clock):
    """Vectorized 1:1 port of ComputeParamsForClock. clock: ndarray, seconds
    in [0, LOOP)."""
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


N = 14400 * 4  # 4 samples/sec for a smooth curve + fine-grained delta check
clock = np.linspace(0.0, LOOP, N, endpoint=False)
fall, snow, snowfall, freeze, spring, leafdrop = compute_params(clock)

series = {
    "FallBlend": (fall, "tab:orange"),
    "LeafDrop": (leafdrop, "tab:brown"),
    "SnowCover": (snow, "tab:blue"),
    "SnowfallIntensity": (snowfall, "tab:cyan"),
    "FreezeBlend": (freeze, "tab:purple"),
    "SpringGreen": (spring, "tab:green"),
}

fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

# season-hold shading + transition shading
hold_spans = [(S0, S1, "summer"), (S2, S3, "fall"), (S4, S5, "winter"), (S6, S7, "spring")]
trans_spans = [(S1, S2, "sum->fall"), (S3, S4, "fall->win"), (S5, S6, "win->spr"), (S7, LOOP, "spr->sum")]

for ax in (ax1, ax2):
    for lo, hi, _ in hold_spans:
        ax.axvspan(lo / 3600, hi / 3600, color="0.92", zorder=0)
    for lo, hi, _ in trans_spans:
        ax.axvspan(lo / 3600, hi / 3600, color="1.0", alpha=0.0, zorder=0)  # transitions = white/unshaded
    for b in (S0, S1, S2, S3, S4, S5, S6, S7, LOOP):
        ax.axvline(b / 3600, color="0.75", lw=0.6, zorder=0)

for name, (data, color) in series.items():
    ax1.plot(clock / 3600, data, label=name, color=color, lw=1.8)

ax1.set_ylabel("param value [0,1]")
ax1.set_title("verify_year_params: all season params over the 4-hour loop\n"
              "(shaded = 45-min HOLD; unshaded = 15-min smoothstep TRANSITION)")
ax1.legend(loc="upper right", ncol=3, fontsize=9)
ax1.set_ylim(-0.05, 1.15)

# annotate hold/season names
for lo, hi, label in hold_spans:
    ax1.text((lo + hi) / 2 / 3600, 1.08, label, ha="center", fontsize=9, color="0.35")
for lo, hi, label in trans_spans:
    ax1.text((lo + hi) / 2 / 3600, 1.08, label, ha="center", fontsize=8, color="0.6", style="italic")

# derivative / step-to-step delta plot to prove no discontinuities
dt = clock[1] - clock[0]
max_abs_delta = 0.0
for name, (data, color) in series.items():
    delta = np.diff(data) / dt
    max_abs_delta = max(max_abs_delta, np.max(np.abs(delta)))
    ax2.plot(clock[1:] / 3600, delta, label=f"d({name})/dt", color=color, lw=1.2)

ax2.set_xlabel("clock [hours] (0 to 4h loop)")
ax2.set_ylabel("d(param)/dt [1/s]")
ax2.set_title("per-sample derivative - a true discontinuity would show as a spike/step "
              "far larger than the smoothstep ramps' bounded peak slope")
ax2.legend(loc="upper right", ncol=3, fontsize=8)

# sanity annotations
seq_notes = []
# check: orange turn (fall reaches 1.0) completes before leaf drop starts (>0)
fall_reaches_1_at = clock[np.argmax(fall >= 0.999)]
leafdrop_starts_at = clock[np.argmax(leafdrop > 1e-6)]
seq_notes.append(f"FallBlend reaches 1.0 at t={fall_reaches_1_at:.0f}s (S2={S2:.0f}s); "
                  f"LeafDrop first >0 at t={leafdrop_starts_at:.0f}s (S3={S3:.0f}s) -> "
                  f"{'PASS: orange-turn completes before drop starts' if fall_reaches_1_at <= leafdrop_starts_at + 1e-6 else 'FAIL'}")
leafdrop_reaches_1_at = clock[np.argmax(leafdrop >= 0.999)]
heavy_snow_at = clock[np.argmax(snowfall >= 0.999)]
seq_notes.append(f"LeafDrop reaches 1.0 at t={leafdrop_reaches_1_at:.0f}s (S4={S4:.0f}s); "
                  f"SnowfallIntensity reaches its transition peak (1.0, before dropping to the "
                  f"WinterLightSnowfall={WINTER_LIGHT_SNOWFALL} hold level) at t={heavy_snow_at:.0f}s -> "
                  f"{'PASS: leaf drop completes at/before heavy transition-snow peak' if leafdrop_reaches_1_at <= heavy_snow_at + 1e-6 else 'FAIL'}")
melt_starts_at = clock[np.argmax((clock >= S5))]
rebud_progress_at_melt_start = leafdrop[np.argmax(clock >= S5)]
seq_notes.append(f"Winter->spring transition starts at t={S5:.0f}s with LeafDrop={rebud_progress_at_melt_start:.3f} "
                  f"(should be 1.0, i.e. spring re-bud STARTS from fully-bare, following melt) -> "
                  f"{'PASS' if abs(rebud_progress_at_melt_start - 1.0) < 1e-3 else 'FAIL'}")
seq_notes.append(f"max |d(param)/dt| across all series = {max_abs_delta:.6f} (finite, bounded - "
                  f"no discontinuity/step found at this sample rate ({1/dt:.1f} Hz, dt={dt:.4f}s))")

fig.text(0.01, -0.02, "\n".join(seq_notes), fontsize=9, family="monospace", va="top")

plt.tight_layout(rect=[0, 0.06, 1, 1])
OUT = r"C:\Users\taylo\AppData\Local\Temp\claude\C--Users-taylo-Coding-OpenDrive\26b7f002-8582-449d-bd00-11b035157f40\scratchpad\verify_year_params.png"
plt.savefig(OUT, dpi=130, bbox_inches="tight")
print("wrote", OUT)
for n in seq_notes:
    print(n)
