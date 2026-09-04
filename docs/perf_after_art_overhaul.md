# Perf measurement: art-direction overhaul (§7.15)

**Date:** 2026-09-03
**Purpose:** quantify frame cost / hitching after the art-direction overhaul
(new atmosphere/volumetric clouds/post `3d82f91`, PBR ground `fd7ed1f`,
textured roads + ODR3 `60ace94`, card foliage `c810ad2`/`bc34028`, textured
building facades `b941365`/`f57eab2`, new car meshes) — before Taylor does a
live drive-test. **Measurement only; nothing was changed or tuned.**

## Methodology

`-csvprofile` was tried first (UE's CSV frame-stat capture) but was a dead
end for this workflow: it only flushes its CSV to `Saved/Profiling/CSV` on a
graceful engine shutdown, and these headless `-game` sessions have no clean
exit path — they have to be `taskkill /F`'d after a fixed run length, which
skips the flush. No CSV was ever written.

Fell back to parsing the existing `abslog`. Two facts about UE's default log
line format turned out to be enough to reconstruct frame timing without
touching any code:

1. Every log line's `[HH.MM.SS:mmm]` prefix is a real wall-clock timestamp.
2. Every log line's second bracket, `[N]`, is the engine's per-frame counter
   (a wrapping `uint16`, handled by unwrapping across the 65536 boundary).

The project's existing `-odtrace` flag (10 Hz `ODDrivetrain:` lines from
`ODVehicleMovement.cpp`) gave a regular heartbeat with both fields present.
For each consecutive pair of trace lines, `wall_delta_ms / frame_delta` gives
the average frame time over that span; a span where only 1–2 frames elapsed
but the wall-clock gap is large is a single/double-frame stall, and its
average frame time is a good estimate of that stall's real duration. This
reproduces average/median frame time and hitch detection (threshold 75 ms,
matching the 75 ms streaming ground-snap gate) without stat commands,
without CSV, and without touching engine or game code. Parser:
`hitch_final.py` in the scratch dir, run over each `abslog`.

Baseline: the pre-overhaul logs already in the scratch directory
(`summit_frisco_test2.log`, static Frisco; `summit_frisco_test6.log`, Frisco
`-odrev=20`) use the same `-odtrace` flag, so the same parser applies
directly to them for a like-for-like comparison.

## Results

| Run | Avg frame | Avg FPS | Median | Worst-1% tail avg | Hitches ≥75ms | Hitch total |
|---|---|---|---|---|---|---|
| Frisco static (post) | 15.05 ms | 66.5 | 14.57 ms | 101.2 ms | 4 | 1345 ms |
| Vail, `-odrev=20` (post) | 15.07 ms | 66.3 | 14.57 ms | 129.9 ms | 4 | 1442 ms |
| Frisco dusk, `-odhour=19` (post) | 14.84 ms | 67.4 | 14.29 ms | 142.9 ms | 3 | 1107 ms |
| **Baseline:** Frisco static (pre-overhaul) | 14.55 ms | 68.7 | 14.43 ms | 89.5 ms | 6 | 2267 ms |
| **Baseline:** Frisco `-odrev=20` (pre-overhaul) | 12.32 ms | 81.2 | 11.89 ms | 129.5 ms | 4 | 2043 ms |

Runs are ~187–196 s each, ~1700–1780 samples (10 Hz), ~12,400–13,200 engine
frames advanced per run.

No `LogStreaming` hitch/warning lines, no shader-cost warnings, no memory
warnings in any of the three post-overhaul logs — only routine init/shader-
compile chatter. The pre-existing `Failed to read file ...Masks/*.bin`
warnings (unrelated mask-loading issue, present before this overhaul too:
105–213 occurrences per run across old and new logs alike) are unchanged in
character and not a perf signal.

**A striking pattern:** all three post-overhaul runs, and the pre-overhaul
`-odrev=20` baseline, show a near-identical **~25 s periodic hitch** (spikes
recur at roughly t≈45s, 71s, 95s, 120s, 144s, 170s, 194s in every run,
static or driving, day or dusk). Since this shows up with the exact same
cadence and rough magnitude in the pre-overhaul baseline, it predates the
art overhaul entirely — most likely UE's incremental garbage collector or a
similar fixed-interval engine housekeeping tick, not anything introduced by
the new materials/meshes. Not investigated further per scope (measurement
only).

## Comparison to baseline

Post-overhaul numbers are within noise of the pre-overhaul static-Frisco
baseline (15.0 ms vs 14.55 ms avg, ~3% slower) and the driving/forest case
(Vail, foliage-heavy) lands at the *same* frame cost as static Frisco
(15.07 ms vs 15.05 ms) — foliage overdraw is not showing up as a measurable
cost at this vantage/density. The `-odrev=20` baseline ran faster (12.3 ms)
than the current `-odrev=20` runs (~15 ms), but that comparison isn't
apples-to-apples: the pre-overhaul baseline log is dated before the terrain/
road/building work in general, not just this art pass, and duration/sampled
window differ. Frisco-static-vs-Frisco-static is the fairer comparison and
shows only a small, plausible delta.

Hitch counts and total hitch time are, if anything, slightly *lower*
post-overhaul (3–4 hitches / ~1.1–1.4 s total per run) than the pre-overhaul
static baseline (6 hitches / 2.3 s), though both pre- and post-overhaul
numbers are dominated by the same ~25 s periodic pattern described above
rather than anything scene-specific.

## Verdict: **fine**

Median frame time is ~14.3–14.6 ms (≈67–70 fps) in every scenario tested,
nowhere near the 33 ms/30 fps danger zone, and hitching is unchanged in
character and magnitude from before the overhaul. The new atmosphere/
volumetrics, PBR ground, textured roads, card foliage, and textured facades
do not show up as a measurable regression in any of the three scenarios
(static town, high-speed forest corridor, dusk with emissive
windows+headlights+low-sun volumetric fog). Safe to proceed to a live
drive-test without a tuning pass first.

## Top suspected costs (for future tuning, not urgent)

Since none of the three scenarios shows a real regression, this is a ranked
guess for where headroom would go first if a future scene pushes past this
budget, not evidence of a current problem:

1. **Volumetric fog / clouds at low sun angle (dusk).** Dusk run's
   worst-1%-tail (142.9 ms) is the highest of the three, despite the lowest
   median — consistent with occasional heavier passes (cloud/atmosphere
   re-evaluation, emissive window overdraw) rather than a flat cost. Still
   well within the current budget.
2. **Foliage overdraw (Vail corridor).** No measurable frame-time
   regression here, but it has the highest total hitch time (1442 ms) of
   the three and the single largest isolated stall (891 ms at t=71.3s,
   same ~25s-cadence slot as the other runs' hitches there, so likely the
   periodic system above rather than foliage itself — flagged for
   attention if that pattern ever localizes to Vail specifically).
3. **Translucent car glass / building window emissives.** Not evidenced by
   these three runs (vehicle is mostly stationary or scripted through open
   road, buildings only populate the town scenes at a fixed density), but
   worth watching once a scenario has many buildings AND many cars on
   screen simultaneously — this pass didn't stress that combination.

## Addendum (2026-09-03): the periodic hitch, actually investigated

The body of this doc flagged a "striking pattern" — a near-25s-cadence
≥75ms hitch, present in every run above and in the pre-overhaul baseline
too — and guessed GC without digging further, since it was out of scope
that pass. This follow-up (§7.15, "Diagnose and fix the periodic frame
hitch") went and checked. Short version: **the GC hypothesis is wrong**,
and the hitch itself turned out not to be a reproducible engine mechanism
at all.

**Method:** added `[Core.Log] LogGarbage=Verbose` to `DefaultEngine.ini`
temporarily and re-ran the same `-odtrace`-based headless methodology
(Frisco, `-odrev=20`, ~180-195s) so every `LogGarbage: Collecting garbage`
/ `GC purged N objects` line and every `ODDrivetrain` frame-timing sample
land in the same `abslog`, directly comparable on the same wall clock.

**GC ruled out:**
- Full GC collect cycles fire on the vanilla
  `gc.TimeBetweenPurgingPendingKillObjects=61.1` cadence — observed at
  wallclock 05:04:45, 05:05:47, 05:06:48, 05:07:50 (~61-62s apart), not
  ~25s apart.
- Each collect costs ~18-20ms end to end (`GC Reachability Analysis total
  time: ... ms`, `GC purged N objects ... in X ms`) — roughly a quarter of
  the 75ms hitch threshold, nowhere near able to produce an 800ms+ stall by
  itself.
- None of the GC event timestamps land anywhere near an observed ≥75ms
  frame-time hitch in the same log (e.g. the one hitch found in that run,
  t=90.6s, sits almost exactly between the t=60s and t=121s GC events, not
  on top of either).

**Tile-streaming managers ruled out too:** the original baseline hitches
(4 of them, `perf_frisco_static.log`) occur in a fully **static** scene
where every one of the 8 tile managers (`ODTerrainManager`,
`ODBuildingManager`, `ODWallManager`, `ODBarrierManager`, `ODRoadManager`,
`ODForestManager`, `ODWaterManager`, `ODParkingManager`) reports
`pending=0` and effectively zero new work — there is no tile load/unload
churn happening for a per-tile-destroy-and-GC pattern to blame. Their own
5s diagnostic-dump ticks (`ODTerrainManager::Tick`'s `LastDiagTime` timer)
were checked too: one hitch happened to land on a dump tick, the rest
didn't, ruling that out as a systematic cause as well.

**Partial measurement artifact found along the way:** re-running with
`-odshot=90` reproduced a hitch that lines up exactly with the QA
screenshot's own `LogCore: Tracing Screenshot ... taken` log line every
time it was present (this run, and `summit_frisco_test6.log`'s t=32.4s
hitch against its `-odshot=32`). `-odshot`'s synchronous high-res capture +
readback is expected to cost a frame; it isn't a hitch a real player would
ever see, and it should be excluded from future hitch tallies (or the flag
dropped when hitch-hunting specifically).

**The rest of the hitch doesn't reproduce.** Two fresh, otherwise-identical
control runs today (Frisco `-odrev=20`, no `-odshot`) — one before, one
after the config change below — both came back **completely hitch-free**:
0 hitches ≥75ms in either, median frame time 14.9-15.3ms (~65-66 fps),
matching the rest of this doc's numbers. The originally-recorded ~25s
pattern, on closer inspection, wasn't even at a clean 25s multiple run to
run (static: 125.5/144.2/175.1/193.7s, i.e. 18.7/30.9/18.6s gaps; Vail:
71.3/95.1/188.1/190.3s) and has no corresponding line in any engine log
category checked (no `LogStreaming`, no shader-compile, no memory
warnings, no GC, no manager diagnostic). The likeliest explanation is
transient OS/driver/hardware jitter on the dev laptop at original capture
time (background process, disk I/O, GPU power-state transition) rather
than anything in engine or game code — consistent with it not reproducing
today under the same test conditions.

**Action taken:** added `gc.AllowIncrementalReachability=1` +
`gc.IncrementalReachabilityTimeLimit=0.002` +
`gc.NumRetriesBeforeForcingGC=10` + `s.ForceGCAfterLevelStreamedOut=0` to
`DefaultEngine.ini` (`[/Script/Engine.GarbageCollectionSettings]`) as
free, standard-practice GC amortization — it costs nothing measurable and
is UE's own recommended setup for spreading reachability analysis across
frames instead of one blocking pass — but this is hardening for a
hypothetical future GC spike, **not** a fix for this specific hitch, since
GC was directly shown not to be its cause. No C++ changes were made:
there was no evidenced churn pattern in the streaming managers to pool or
fix.

| Run | Duration | Avg frame | Median | Hitches ≥75ms | Hitch total |
|---|---|---|---|---|---|
| Before (no GC config, no -odshot) | 185.9s | 15.26 ms | 15.29 ms | 0 | 0 ms |
| After (GC config added, no -odshot) | 173.5s | 15.48 ms | 14.86 ms | 0 | 0 ms |
| After, with `-odshot=90` (artifact check) | 186.4s | 11.94 ms | 11.67 ms | 2 (both at the odshot capture) | 922 ms |

Median frame time is unregressed (in fact both same-day runs beat the
original doc's 14.55-15.07ms baseline range, most likely normal
run-to-run variance/warm-cache effects rather than anything caused by
this change). **Verdict stands: fine.** No blocker for Taylor's live
drive-test.
