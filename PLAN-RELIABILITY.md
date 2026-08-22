# OpenDrive — Reliability Remediation Plan

*2026-08-21. Executes the findings in [`AUDIT-RELIABILITY.md`](AUDIT-RELIABILITY.md).
Ordered so each phase is safe to stop after — nothing here requires the next
phase to be useful.*

---

## Phase 0 — Stop the bleeding (today, minutes each, no design decisions)

Do these in this order; 1 depends on nothing, 2 depends on 1 being safe to
rerun against, 3/4 are independent.

1. **Commit + add a remote.** `git add` the tracked changes and the four
   untracked wall-system files, one commit. Create a private GitHub repo (or
   ask which host), `git remote add origin`, push `audit-and-arc7-rescope`.
   This is the only item that removes a total-loss risk, not just an
   inconsistency risk — do it first regardless of what else happens today.
2. **Rebake barrier + buildings** against the current `road_stations.json`
   (`build_road_barrier.py` → `export_road_barrier_bin.py`,
   `build_building_mesh.py` → `export_building_mesh_bin.py`). Confirms § 2's
   diagnosis directly: if drive feel improves, that's confirmation, not
   assumption. Alternative if you'd rather not maintain the barrier long-term:
   delete it now per `AUDIT.md` item 4 and rely on the out-of-bounds reset —
   your call, either resolves the inconsistency.
3. **Fix `build_forest_elevation.py:88`** — update the unpack to match
   `load_stations()`'s current 7-tuple return. Pure bug fix, no behavior
   decision needed.
4. **Give `M_Road_OneWay` a physmat.** Extend `apply_road_physmat.py` (or
   `apply_oneway_road_material.py`) to set `PM_Asphalt` on the one-way
   material, matching what already happens for `M_Road`.

**Checkpoint:** commit Phase 0's fixes separately from Phase 0 item 1's
initial commit-everything, so the fixes are individually reviewable. Drive-test
after item 2 specifically — that's the one with a real chance of changing how
the car feels.

---

## Phase 1 — Kill the partial-rebake class (this week, hours)

These three are the direct fix for § 3 (why rebuilds mix generations) and are
independent of each other, but do them in this order because 6 is easiest to
validate once 4 exists.

4. **One driver script** — `pipeline/rebuild_area.py`, running every stage for
   the active area in the correct order: fetch → stations → walls → terrain →
   road network → road bin → barrier → buildings → labels →
   biome/settlement → map texture → verify. Pull the actual order from what
   each script's exporter reads/writes (not from the current README, which is
   known incomplete per § 3.3). README's rebuild section gets replaced with
   "run `rebuild_area.py`" plus a one-line note on what it does — not a
   hand-maintained list.
5. **Generation-ID stamping.** Hash `road_stations.json` (+ the constants that
   affect geometry: `ELEV_PLAUSIBLE_MAX_M`, tile size, DEM zoom) into a short
   ID; write it into every sidecar header (terrain/road/wall/barrier/building/
   label). Runtime managers compare the ID across the tile set they load and
   `UE_LOG(Warning)` on first mismatch per prefix (not per tile — avoid log
   spam). This turns § 2 and § 5.1 from silent into visible on the next
   accidental partial rebake.
6. **Exporters delete stale tiles.** Each exporter, before writing, deletes
   existing tiles for the active area/prefix that this run didn't (re)produce
   — or writes a manifest and has the runtime managers check completeness
   against it and warn on a gap. Either approach ends § 3.2; manifest is more
   work but also gives the generation-ID check (item 5) a natural home to log
   through.

**Checkpoint:** run `rebuild_area.py` end-to-end for the current test area once
Phase 1 lands, confirm tile counts/dates are uniform, drive-test again.

---

## Phase 2 — Make the gate mean something (next, 1–2 days)

7. **Make DEM fetch failures fatal** (or counted and gating) in
   `build_terrain_mesh_tiles.py`; invalidate `dem_tiles.npz` when bbox, zoom,
   or the OSM source changes instead of keying on existence alone. Removes
   § 5.3's silent-sea-level-patch failure mode and the associated
   "reruns produce different results" symptom.
8. **Point `verify_terrain_fit.py` at the actual `.bin` files on disk**, not
   an in-memory rebuild. Restore a road-vs-terrain gate (`verify_road_fit.py`
   was lost in July and never replaced — this is a rewrite, not a restore).
   Validate `sample_mesh_height` against one small hand-computed fixture tile
   so its correctness stops resting on "presumably fine since July."

This is the biggest single item in the whole plan and the one most worth
scoping carefully before starting — happy to turn it into its own short plan
once Phase 1 is done and stable.

---

## Phase 3 — Repro and trust (as time allows, hours each)

9. Enable `PythonScriptPlugin` + Geometry Script in `.uproject` (stop relying
   on this machine's editor settings); replace the stale `AREA_NAME` copies in
   `apply_place_labels.py`/`apply_road_centerline.py`/
   `build_openworld_test_level.py` with imports from `test_areas.py`; move
   `ELEV_PLAUSIBLE_MAX_M` into `test_areas.py` so it's per-area before any
   mountain zone build happens (this one has a hard deadline: it must land
   *before* the first non-Oahu bake, or every road above 1300 m gets clipped
   flat).
10. Fix the QA-hook statics (`-odseam`, `-odshot`, `-odpick`) to reset per
    PIE run instead of per editor process; add a short retry/log-on-miss to
    the one-shot map ticker.
11. Document the retaining-wall system in `next steps.md`; record the
    §7.11 Tail-of-the-Dragon drive-test as an explicitly open item.

---

## What's deliberately not in this plan

`AUDIT.md`'s architecture/scale findings (item numbering there is separate) —
this plan is reliability-only, per the audit's own scope. The ~1.2 GB of
retired-area intermediates in `data/` is a cleanup item, not a reliability
fix; worth a `git clean`-style pass whenever convenient but not sequenced
here.

---

## Suggested next step

Start Phase 0 now — all four items are independently shippable in minutes and
item 2 is the one most likely to directly explain what you've been feeling.
Say go and I'll work through them in order.
