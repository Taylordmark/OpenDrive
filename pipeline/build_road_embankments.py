"""Embankment skirts: where the road rides ABOVE the surrounding terrain but
not so high a bridge deck is the right answer, fill the gap with a sloped
earthen skirt from the pavement edge down to terrain. Third leg of the same
"pipeline ships points, C++ extrudes at runtime" split as
build_road_guardrails.py/AODGuardrailManager (visible guardrails, Arc 7.16) -
this module reuses that script's own terrain-drop detection shape almost
verbatim (same DEM probe, same lake/junction exclusions) rather than
reinventing it; see that file's docstring for the exclusion reasoning this
one reuses.

Three-way classification by per-station drop (Taylor-approved design, see
"next steps.md" §7.19):
    drop <= EMBANK_LOW_THRESHOLD_M (2.5 m)   -> none        (terrain meets
                                                              road acceptably)
    EMBANK_LOW < drop <= EMBANK_HIGH (7 m)   -> embankment   (this module
                                                              emits geometry)
    drop > EMBANK_HIGH_THRESHOLD_M, OR a      -> bridge       (a real span
    qualifying drop over open water                          only - see the
                                                              §7.21 sub-
                                                              classification
                                                              pass below)

Threshold stability: a per-side Schmitt-trigger (hysteresis) state machine
walks each side's station sequence so a station sitting exactly on 2.5 m or
7 m doesn't flicker in/out of a class station-to-station - the state only
crosses UP at threshold+EMBANK_HYSTERESIS_M and back DOWN at
threshold-EMBANK_HYSTERESIS_M. On top of that, exactly like guardrails'
GUARDRAIL_MIN_RUN_M, any surviving contiguous run shorter than
EMBANK_MIN_RUN_M is demoted back to "none" - a real embankment/bridge is a
sustained condition, not a momentary DEM blip.

§7.21 bridge sub-classification (2026-09-07, this pass): §7.20 root-caused
the classifier's raw drop signal but left a second, honestly-disclosed
problem alone - 67.83 km of "bridge" survived that fix, and most of it is
NOT a real span. The longest runs (I-70's East Portal approach, Boreas Pass
Road, CO-9/Hoosier Pass) are benched mountain sidehill cuts: one shoulder
drops toward a valley, the DEM can't represent the road's own independently-
graded far shoulder (same class of issue as is_structure_way()'s documented
I-5/Skagit precedent - a single terrain heightmap can't represent two
carriageways/edges benched at different heights on the same mountainside),
and the "drop" is really just the downhill DEM falling away from an ordinary
cut-and-fill roadbed, not a gap under a deck. A real bridge crosses
something - a ravine, a reservoir, another road - so BOTH edges of the
pavement read as elevated above the ground beneath, and the span itself is
short (a heightmap CAN represent a valley floor under a real span; a bridge
spanning it is not "sustained" for kilometers). After every "bridge"-class
run is found (still via the pipeline above), each contiguous run is re-tested
against three signals, any one of which is sufficient to keep it "bridge":
  1. water crossing - ANY of the run's stations sample inside a mapped lake
     polygon (>= BRIDGE_WATER_MIN_FRAC of the run). Per the task brief, a
     water crossing always gets a deck regardless of measured drop -
     reservoir/lake edges are exactly the case a DEM heightmap represents
     faithfully (open water is flat), so this signal is trustworthy.
  2. OSM structure tag - a widened proxy (seg["is_bridge"], which is
     expand_carve_exclusion(is_struct_s)|conflict_s per build_road_stations.py,
     minus is_tunnel) shows meaningful coverage (>= BRIDGE_OSM_TAG_MIN_FRAC)
     over the run. This is NOT the narrow raw OSM bridge=yes tag - that value
     isn't shipped to road_stations.json's output (only the buffer-expanded
     carve-exclusion mask is, see build_road_stations.py L975) - so this
     signal is deliberately treated as corroborating, not primary, and the
     honest limitation is documented in verify_bridge_stats.txt.
  3. short + both-sided - the run is <= BRIDGE_GENUINE_MAX_LEN_M (300 m) AND
     the OPPOSITE side of the road also carries a qualifying (embankment-or-
     above) drop over most of the same stretch (>= BRIDGE_BOTH_SIDED_MIN_FRAC).
     A sidehill cut is asymmetric by construction - one shoulder is benched
     into the hillside (no drop, sometimes even a cut slope going UP), the
     other drops toward the valley. A real span crosses a gap that's below
     the road on both edges, and a real span is short (see module's own
     docstring reasoning above for why the DEM-vs-two-carriageways artifact
     specifically produces multi-km "drops," never multi-km SPANS).
Any run failing all three is DEMOTED to "embankment" (folded into this same
pass's skirt bake, not left naked) with its drop value capped at
DEMOTED_BRIDGE_DROP_CAP_M (12 m) purely for the skirt's own geometry - an
idealized 1.5:1 straight-line skirt at a real 30 m drop would show a 45 m
horizontal run, visibly wrong for a hillside cut (verify_bridge_geometry.png
shows this cap decision against the uncapped alternative). The cap changes
ONLY the shipped skirt height, never the underlying classification math or
any other station's drop value.

The result: what survives as "bridge" after this pass is a short, credible
list of real spans (see verify_bridge_stats.txt) - not every multi-km
mountain-pass cut the raw drop threshold alone would flag. Every run's
attributes and the accept/demote reason are written to
bridge_subclass_report.json (verification input for pipeline/build_road_
bridges.py and the offline verification plots) regardless of outcome.

§7.22 refinement (2026-09-07, this pass): 7.21's own "short + both-sided"
signal turned out to be necessary but NOT sufficient - it's also satisfied
by a switchback bench or a fill crest (both edges of the roadbed dip briefly
at a bench turn without any real gap underneath), which is exactly why
7.21's shipped "genuine" list included 20 spans on Boreas Pass Road (an
unpaved 4x4 pass) and clusters on CO-9/Hoosier Pass and several dirt county
roads (County Road 4, Homestake Creek Road, Robbers Nest Drive) - not
credible: real Summit County has on the order of 2-4 km of actual bridge
deck (I-70 interchange/overpass structures, a handful of US-6/Blue River/
Tenmile Creek crossings), not the 92 runs/9.93 km 7.21 shipped. Two new AND
conditions were added on top of the existing short+both-sided test (the
water-crossing OR stays unconditional - a real water crossing already
implies a real gap under the deck):
  1. **paved-road test** (`build_road_surface_index()`/`is_unpaved`) - a
     dirt/gravel/track/service road never gets a concrete deck regardless of
     drop shape. `road_stations.json` ships `highway_class` per station but
     not the OSM `surface` tag (build_road_network.py's `highway_class()`
     has no `surface()` counterpart), so this joins back to the cached
     `data/raw/<AREA>_test/osm.json` by road name, length-weighting every
     OSM way sharing that name/ref. Deliberately does NOT blanket-demote
     highway=unclassified despite the task brief's own suggested tag list -
     inspecting the actual county data first (per the brief's own
     instruction) found unclassified is majority-PAVED here (176/323 ways
     asphalt-surfaced), so a per-name length-weighted `surface` tag vote is
     used instead of a per-class blanket rule. A residual ambiguous case
     (plain "County Road 4"/"Willow Creek": highway=residential, no surface
     tag, `tiger:reviewed` absent or "no" - not affirmatively reviewed as
     "yes"/"aerial") falls back to a documented, honestly-caveated
     heuristic - see that function's own docstring.
  2. **terrain-rebound (abutment plausibility) test** (`rebound_shape_ok()`)
     - a real span's drop profile is a bowl: the terrain climbs back to near
     road level (drop <= REBOUND_DROP_M, 3 m) within REBOUND_WINDOW_M (70 m)
     beyond BOTH ends of the run. A bench/fill-crest instead holds a
     plateau - the drop stays elevated well past the run's own hysteresis-
     drawn boundary, because it's really one long sustained cut, not a short
     real gap. Checked against the FULL side's station array beyond the
     run's own slice (a run's own edge stations are, by construction of the
     hysteresis classifier, already at-or-above threshold, so the terrain
     has to be checked outside the run, not inside it).
     Window tuned empirically against the actual county data (OD_DEBUG_
     REBOUND=1 dump of every water-excluded/paved/short/both-sided
     candidate's full drop profile out to 250 m past each run end - see
     `_DEBUG_REBOUND_DUMP` below and scratchpad/plot_rebound_tuning.py):
     the task brief's own suggested ~30-50 m guess is too tight - it clips
     the real, named-genuine Silverthorne I-70/CO-9 interchange (its drop
     doesn't fall to 3 m until ~65 m out, then keeps falling to ~0 by ~60-65
     m - a real, if slightly wide, abutment approach grade, not a plateau).
     70 m is the smallest window that admits Silverthorne on both sides
     while total genuine km among those 65 candidates is still climbing
     slowly (0.59 km at 40 m -> 1.78 km at 70 m -> 2.24 km at 100 m -> 3.18
     km at 250 m) rather than having already leveled off - i.e. 70 m is
     picked for the specific, named reason it's needed (Silverthorne), not
     because a bigger window recovers more total mileage; going further
     mostly keeps re-admitting more of the CO-9/Hoosier switchback-bench
     population the discriminator exists to reject; 40 of the 42 CO-9-named
     candidates still correctly fail at 70 m. See verify_bridge_rebound.png
     and "next steps.md" §7.22 for the tuning plot and before/after numbers.
Both new conditions are AND'd onto the existing short+both-sided test (which
stays necessary but not sufficient, per the task brief); they do not apply
to the unconditional water-crossing OR.

§7.23 water-crossing as a first-class bridge trigger (2026-09-07, this pass):
7.21/7.22 both left the "water crossing" caveat honestly unresolved - the
in-lake branch inside `classify_with_hysteresis()` only ever promoted a
station to BRIDGE_CLS if it ALSO cleared the ordinary EMBANK_LOW_THRESHOLD_M
(2.5 m, hysteresis-adjusted) drop against the raw DEM sample, and that DEM
sample is exactly what's untrustworthy right at a shoreline (the offline
inventory below found the classifier's own `sample_dem()` reads within
centimeters of the lake's own baked surface Z at the one place a road
noticeably touches mapped water in this county - i.e. the "drop" signal is
near zero there, not because there's no gap, but because the raw DEM simply
doesn't resolve one). Per the task brief, a water crossing should get a
bridge deck "regardless of measured drop" - this pass makes that literal:
`main()` now runs a SECOND, independent pass over both sides of every
segment (after `compute_side()`'s ordinary hysteresis classification) that
can promote a station straight to BRIDGE_CLS purely from lake-polygon
geometry, with no DEM drop requirement at all.

**Offline inventory first** (`scratchpad/inventory_water_crossings*.py`,
county-wide, against the same baked lake polygons `load_lake_beds()` already
serves to this module): at the pipeline's own pavement-edge offset
(ROAD_HALF_WIDTH = 4.5 m, same convention `compute_side()`'s existing
`in_lake` test already uses), **zero** stations anywhere in the 1,579-segment
network have BOTH pavement edges inside a mapped lake polygon at once - i.e.
no location in this county's current OSM/DEM data reads as the road running
directly INTO the middle of open water on both sides. The single place
pavement geometry comes close is a ~250 m stretch of Swan Mountain Road
(lat 39.6007-39.6013, lon -106.0144 to -106.0176) where ONE edge dips inside
the Dillon Reservoir polygon; comparing that stretch's road elevation
against the SAME baked lake surface Z the water bake itself uses (not the
raw DEM) gives a clearance of -1.2 m to +0.6 m - i.e. the road sits
essentially AT water level, not elevated over a gap. That is the geometric
signature of a lakeside road on shoreline fill/rip-rap, not a deck spanning
open water. Dillon Dam Road - the dam's own crest - never gets closer than
~12 m to any mapped lake polygon at its ROAD centerline anywhere along its
length, let alone at the pavement edge: the dam carries the road on wide
engineered fill set back from the current shoreline, and OSM does not tag
`man_made=dam` anywhere in this county's cached data (checked directly), so
there is no tag to key a special-case off of - the geometry itself (no
edge ever enters mapped water) already excludes it before any clearance
guard is even needed. US 6, CO 9, CO 91, and I-70/"Gerald R. Ford Memorial
Highway" all pass within 10-200 m of various lake/pond polygons but their
pavement edges never enter one anywhere in the county. Separately: the named
creek/river crossings the task brief itself anticipated (Blue River, Tenmile
Creek, Straight Creek) are OSM `waterway=river|stream` LINEAR features, which
`build_water_tiles.py` explicitly does not bake into lake polygons at all
(only closed `natural=water`/`landuse=reservoir` rings, per that module's own
documented scope) - a lake-polygon water-crossing test structurally cannot
see those regardless of how the geometry test below is tuned; this is a
pre-existing scope limit of the water bake, out of this pass's declared
scope (`build_road_embankments.py`/`build_road_bridges.py` only).

**The trigger** (`main()`, right after both sides' `compute_side()` calls,
before `subclassify_bridge_runs()`): for each side, a station is forced to
BRIDGE_CLS (from whatever `classify_with_hysteresis()` left it, NONE_CLS or
EMBANK_CLS - EMBANK_CLS is impossible at an in-lake station by construction,
see that function) if its edge is inside a lake polygon AND either -
  1. the OPPOSITE edge is inside a lake polygon too at the same station
     (genuine both-sided straddle - trusted unconditionally, no clearance
     check needed, since both pavement edges cannot be inside the mapped
     ring while the road sits on dry ground), or
  2. this edge alone is inside a lake polygon and the road's own elevation
     clears the matching lake's BAKED surface Z (not the raw DEM) by at
     least `WATER_CROSSING_MIN_CLEARANCE_M` (1.5 m) - **this is the dam/
     one-sided-shoreline guard**, calibrated directly against the Swan
     Mountain Road evidence above (-1.2 m to +0.6 m actual clearance, well
     under the 1.5 m bar) so that a lakeside road sitting at grade next to
     the reservoir does NOT get promoted to a floating deck. Dillon Dam Road
     never reaches this branch at all (its edges never test `in_lake` in
     the first place, per the inventory above), so no clearance check ever
     runs for it either - the geometry excludes it upstream of the guard.
On this county's current baked data, condition 1 never fires (confirmed
zero both-sided stations county-wide) and condition 2 never clears the
guard (Swan Mountain Road's clearance stays negative/near-zero the whole
stretch) - so this pass adds **zero** new genuine bridge stations to the
county's current bake. That is the honest, calibrated result, not a bug:
the code path is real and will correctly promote a genuine future/other-
county water crossing (both edges in water, or one edge clearly elevated
over it), it simply finds none to promote here. Total genuine bridge
mileage is expected to stay at 7.22's 1.78 km (see verify_water_bridge_
stats.txt for the as-run number). `subclassify_bridge_runs()`'s existing
water-crossing OR (previously keyed off the raw, unguarded `in_lake` mask)
now reads this same guarded per-station result instead, so a run promoted
by the new trigger stays genuine unconditionally, bypassing the paved/
rebound/short-both-sided gates - exactly the "existing genuine-subclass
rules bypassed via the unconditional water rule" the task brief asked for -
while a run that merely brushes a lake polygon without clearing the guard
falls through to the ordinary discriminator, same as before.

Forced BRIDGE_CLS runs are still subject to `demote_short_runs()` (reapplied
after the override) - a momentary single-station polygon touch doesn't spawn
a degenerate one-station bridge, matching every other run-building path in
this module.

**Pier/deck height over water surface, not DEM lakebed:** any final
BRIDGE_CLS station whose edge is `in_lake` (regardless of which rule put it
there) has its shipped `drop_cm` recomputed against the baked lake surface Z
plus `WATER_PIER_SUBMERGENCE_M` (0.5 m) instead of the raw DEM sample -
`build_road_bridges.py`'s pier-height math (`drop_cm - DECK_DEPTH_M`) is
unchanged, so this makes a water-crossing pier's foot terminate a half-meter
BELOW the lake's own flat, trustworthy baked surface (visually submerged,
not floating at the waterline or planted in a noisy raw-DEM "lakebed"
reading that build_terrain_mesh_tiles.py's own water-depression pass has
already established is not the real terrain height under the lake anyway).

Exclusions, reused from build_road_guardrails.py's own precedent:
  - junction proximity: FIRST DRAFT of this module used only the newer
    per-station `in_junction_box` field (Arc 7.18) directly, reasoning that
    it was a strict superset-free shortcut around re-deriving
    reconcile_junction_elevations()'s own `touched` mask a second time the
    way build_road_guardrails.py does. Live verification caught this as
    WRONG: `in_junction_box` only covers the literal flush-pavement box at
    each real graph junction node, which is far narrower than guardrails'
    `touched` mask - and in dense town street grids (Frisco/Breckenridge are
    built on real hillside grades, not flat), the gaps BETWEEN that narrow
    box and the next one are exactly where noisy DEM-vs-pavement-edge
    sampling produces spurious multi-meter "drops" that have nothing to do
    with a real embankment-worthy slope (a probe point 7.5m off a town
    street can land on a neighboring lot's own cut/fill grade). Switched to
    reusing `reconcile_junction_elevations()`'s `touched` mask exactly the
    way build_road_guardrails.py does (same call, same is_bridge exclusion)
    - empirically the mechanism that actually keeps guardrails' own town
    negative-control (`rails_town_negative.png`, North 6th Avenue Frisco)
    clean, not `in_junction_box` (which postdates that verification and was
    never actually exercised against the town-grid case). `in_junction_box`
    is ALSO applied (the two masks are OR'd) since it's free and strictly
    correct for the literal box itself. A station in either mask is always
    "none" - no skirt should spill across an intersection a driver needs to
    turn into.
  - open water: same point-in-polygon-only lake test as
    build_road_guardrails.py (deliberately narrower than
    build_road_barrier.py's shore-margin version - see that module's
    docstring for why the margin version silently ate Dillon Dam Road).
    Unlike guardrails, a qualifying-drop station over water is NOT skipped
    here - it's classified "bridge" (a water crossing should get a deck,
    never a dirt fill dumped into the reservoir), matching the brief's
    "drop > 7m (or water crossing): bridge" rule.

Dillon Dam Road (the guardrail feature's own primary test case - a real
causeway with a sustained drop to the reservoir) is expected to land as
"bridge" here precisely because it's a water crossing, not because of raw
drop magnitude alone - see verify_embank_runs.txt for the actual measured
numbers instead of assuming.

Usage:  python build_road_embankments.py   (needs road_stations.json first)

Reads   data/road_tiles/<AREA_NAME>_test/road_stations.json
Outputs data/road_tiles/<AREA_NAME>_test/road_embankments.json
        {"runs": [{"tile","side","cls","station_xyzd_cm"}, ...]}
        (cls: "embankment" or "bridge"; station_xyzd_cm is a flat
        [x,y,z,drop, x,y,z,drop, ...] list, cm, drop_cm = road-above-terrain
        drop at that station in cm - a demoted run's drop_cm is capped, see
        module docstring)
        data/road_tiles/<AREA_NAME>_test/bridge_subclass_report.json
        {"runs": [{seg,side,road_name,length_m,max_drop_m,mean_drop_m,
                   water_frac,osm_frac,both_sided_frac,rise_ratio,
                   genuine,reasons,mid_lat,mid_lon}, ...]}
        (every bridge-threshold-crossing run found, genuine or demoted, for
        pipeline/build_road_bridges.py and the offline verification plots)
"""

import json
import math
import os
from collections import Counter
from pathlib import Path

import numpy as np
import requests

from road_mesh_common import ROAD_HALF_WIDTH, road_ue_z_m, reconcile_junction_elevations
from test_areas import AREA_NAME, ORIGIN_HEIGHT_M, TERRAIN_TILE_SIZE_CM
from build_terrain_mesh_tiles import (NEAR_DEM_ZOOM, M_PER_DEG_LAT, sample_dem,
                                       load_lake_beds, _point_in_polygon,
                                       WATER_SHORE_BLEND_M)

LAKE_SHORE_EXCLUSION_MARGIN_M = WATER_SHORE_BLEND_M + 3.0   # bbox pre-filter only, see docstring

EMBANK_LOW_THRESHOLD_M = 2.5     # <= this: no treatment
EMBANK_HIGH_THRESHOLD_M = 7.0    # > this (or water crossing): bridge, not embankment
EMBANK_HYSTERESIS_M = 0.3        # Schmitt-trigger band half-width at each threshold
EMBANK_MIN_RUN_M = 10.0          # sustained-condition requirement, matches guardrails
EMBANK_PROBE_OFFSET_M = ROAD_HALF_WIDTH + 3.0   # same "a few meters beyond the shoulder" probe
EMBANK_EDGE_OFFSET_M = ROAD_HALF_WIDTH          # skirt top seats AT the pavement edge (no gap
                                                  # vs. the road ribbon's own edge vertex)
WALL_CHUNK_STATIONS = 80         # matches build_road_guardrails.py's tile-bucketing granularity

# --- §7.21 bridge sub-classification thresholds (see module docstring) ---
BRIDGE_GENUINE_MAX_LEN_M = 300.0     # real spans are short
BRIDGE_BOTH_SIDED_MIN_FRAC = 0.5     # majority-of-run opposite-side qualifying-drop overlap
BRIDGE_WATER_MIN_FRAC = 0.03         # any meaningful lake-polygon overlap -> always genuine
BRIDGE_OSM_TAG_MIN_FRAC = 0.3        # meaningful widened-OSM-structure-tag coverage (proxy, see docstring)
DEMOTED_BRIDGE_DROP_CAP_M = 12.0     # cap a demoted run's shipped skirt drop (geometry only)

NONE_CLS, EMBANK_CLS, BRIDGE_CLS = 0, 1, 2
CLS_NAMES = {EMBANK_CLS: "embankment", BRIDGE_CLS: "bridge"}

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"
OUT_PATH = STATIONS_PATH.parent / "road_embankments.json"
REPORT_PATH = STATIONS_PATH.parent / "bridge_subclass_report.json"
OSM_CACHE_PATH = ROOT / "data" / "raw" / f"{AREA_NAME}_test" / "osm.json"

# --- §7.23 water-crossing-as-first-class-trigger thresholds (see module
# docstring addendum) ---
WATER_CROSSING_MIN_CLEARANCE_M = 1.5   # one-edge-in-lake stations need this much
                                         # clearance above the BAKED lake surface Z
                                         # (not the raw DEM) to count as a real
                                         # elevated crossing rather than an at-grade
                                         # shoreline road/dam-top/rip-rap fill - see
                                         # docstring for the Swan Mountain Road/
                                         # Dillon Dam Road evidence this was
                                         # calibrated against
WATER_PIER_SUBMERGENCE_M = 0.5          # a water-crossing pier's foot terminates
                                         # this far BELOW the baked lake surface Z
                                         # (visually anchored under the waterline)
                                         # instead of at the noisy raw-DEM "lakebed"
                                         # reading

# --- §7.22 refinement thresholds (see module docstring addendum) ---
MINOR_HIGHWAY_CLASSES = {"track", "path", "bridleway", "cycleway", "footway", "steps", "service"}
UNPAVED_SURFACE_TAGS = {"dirt", "gravel", "unpaved", "ground", "compacted", "grass", "sand", "earth", "mud"}
PAVED_SURFACE_TAGS = {"asphalt", "paved", "concrete", "paving_stones", "sett", "cobblestone"}
UNPAVED_LENGTH_FRAC = 0.4        # named-road demoted if this much of its OSM length is unpaved-tagged
MINOR_HIGHWAY_LENGTH_FRAC = 0.5  # named-road demoted if this much of its OSM length is track/path/service/etc.
TIGER_UNREVIEWED_LENGTH_FRAC = 0.5  # fallback: unreviewed TIGER import, no surface tag anywhere, resi/unclassified
REBOUND_WINDOW_M = 70.0          # look this far past each run end for terrain to rebound
REBOUND_DROP_M = 3.0             # "back to near road level" - must fall at/below this within the window


def contiguous_runs(mask):
    """Yields (start, end) exclusive index pairs of contiguous True runs."""
    diff = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]
    return list(zip(starts, ends))


def classify_with_hysteresis(drop_m, in_lake, in_junction):
    """Per-station Schmitt-trigger classification along one side's station
    sequence - see module docstring. Returns an int array of NONE_CLS/
    EMBANK_CLS/BRIDGE_CLS, already zeroed at junction-box stations."""
    n = len(drop_m)
    out = np.zeros(n, dtype=np.int8)
    state = NONE_CLS
    lo, hi, hy = EMBANK_LOW_THRESHOLD_M, EMBANK_HIGH_THRESHOLD_M, EMBANK_HYSTERESIS_M
    for i in range(n):
        d = drop_m[i]
        if in_lake[i]:
            # Open water: never embankment (never dump fill into a lake) -
            # bridge if the drop crosses the LOW threshold at all (a water
            # crossing gets a deck the moment the road is meaningfully above
            # the water, not only once it's past the high threshold too),
            # else none.
            state = BRIDGE_CLS if d >= lo - hy else NONE_CLS
        elif state == NONE_CLS:
            if d >= hi + hy:
                state = BRIDGE_CLS
            elif d >= lo + hy:
                state = EMBANK_CLS
        elif state == EMBANK_CLS:
            if d >= hi + hy:
                state = BRIDGE_CLS
            elif d < lo - hy:
                state = NONE_CLS
        elif state == BRIDGE_CLS:
            if d < lo - hy:
                state = NONE_CLS
            elif d < hi - hy:
                state = EMBANK_CLS
        out[i] = state

    out[in_junction] = NONE_CLS
    return out


def demote_short_runs(cls_arr, s_m):
    """Any contiguous non-NONE run shorter than EMBANK_MIN_RUN_M (arc length)
    is demoted back to NONE_CLS - a momentary DEM blip shouldn't spawn a 3m
    stub of embankment or a one-station bridge flag. Mirrors build_road_
    guardrails.py's GUARDRAIL_MIN_RUN_M filter, generalized to a 3-class
    array (kept per-run rather than per-mask since a run may change class
    mid-run at the hi/lo boundary - it's still one physically-contiguous
    "above threshold" stretch)."""
    out = cls_arr.copy()
    nonzero = cls_arr != NONE_CLS
    for start, end in contiguous_runs(nonzero):
        run_len_m = s_m[end - 1] - s_m[start] if end > start else 0.0
        if run_len_m < EMBANK_MIN_RUN_M:
            out[start:end] = NONE_CLS
    return out


_ROAD_SURFACE_INDEX = None   # lazy-built, module-level cache (see build_road_surface_index())

# §7.22 rebound-threshold tuning instrumentation: OFF by default (None), only
# populated when OD_DEBUG_REBOUND=1 is set in the environment - dumps wide-
# window drop profiles for every water/unpaved-filtered short+both-sided
# candidate to scratchpad-owned bridge_rebound_debug.json so a one-off tuning
# script can plot bowl-vs-plateau shapes without re-running the bake. Not a
# normal-path feature; the threshold values it informed (REBOUND_WINDOW_M/
# REBOUND_DROP_M above) are what actually ships.
_DEBUG_REBOUND_DUMP = [] if os.environ.get("OD_DEBUG_REBOUND") == "1" else None


def _way_length_m(way, node_latlon):
    nds = way.get("nodes", [])
    total = 0.0
    for a, b in zip(nds, nds[1:]):
        pa, pb = node_latlon.get(a), node_latlon.get(b)
        if pa is None or pb is None:
            continue
        lat1, lon1 = pa
        lat2, lon2 = pb
        dlat = (lat2 - lat1) * M_PER_DEG_LAT
        dlon = (lon2 - lon1) * M_PER_DEG_LAT * math.cos(math.radians(lat1))
        total += math.hypot(dlat, dlon)
    return total


def build_road_surface_index():
    """§7.22: road_stations.json ships per-station `highway_class` (OSM
    highway= value) but NOT `surface` - build_road_network.py never carries
    that tag through (see that module's `highway_class()`/`way_name()`
    helpers - there's no `surface()` counterpart). Rather than add a new
    per-station field upstream (out of scope - this pass touches
    build_road_embankments.py only), join back to the cached OSM source the
    pipeline already reads (`data/raw/<AREA>_test/osm.json`) by road NAME,
    length-weighting each way's surface/highway tag over every way sharing
    that name/ref - the same granularity a sub-classified run is already
    decided at (one road_name_top per run, via Counter in
    subclassify_bridge_runs()).

    Inspected the actual tag data before picking a rule (see "next steps.md"
    §7.22): highway=unclassified is NOT a reliable unpaved signal in this
    county's OSM data (176/323 unclassified ways are asphalt-surfaced) so it
    is deliberately NOT used as a blanket demotion rule despite being one of
    the task brief's own suggested tags - the `surface` tag and the
    track/path/service highway classes are far more reliable here, and are
    the ones actually used below. A residual ambiguous case (a plain
    "County Road 4"/"Willow Creek"-style TIGER import: highway=residential,
    NO surface tag at all, `tiger:reviewed` absent or "no" - i.e. not
    affirmatively reviewed as "yes"/"aerial") is treated as unpaved by a
    documented, honestly-caveated fallback - real Summit/Park/Eagle County
    unreviewed-or-unmarked TIGER residential ways with no surveyed surface
    tag are overwhelmingly unpaved rural roads in this data (spot-checked:
    County Road 4 - `tiger:reviewed` tag absent entirely on both its ways -
    and Willow Creek - `tiger:reviewed=no` - both match), but this is a
    heuristic, not a certainty - see verify_bridge_stats2.txt's caveats.
    NOTE: an earlier version of this rule only matched `tiger:reviewed=="no"`
    literally, which silently missed the (more common, 231 vs 734 ways)
    case where the tag is absent entirely rather than explicitly "no" -
    County Road 4 itself has no `tiger:reviewed` tag on either of its ways
    and was NOT demoted until this was corrected.

    Returns: dict road_name -> (is_unpaved: bool, reason: str, stats dict).
    Names not present in the OSM cache (e.g. "(unnamed)") are absent - callers
    treat missing names as "unknown -> not unpaved" (fail open, matching this
    module's existing default-to-eligible posture elsewhere).
    """
    global _ROAD_SURFACE_INDEX
    if _ROAD_SURFACE_INDEX is not None:
        return _ROAD_SURFACE_INDEX

    index = {}
    if not OSM_CACHE_PATH.exists():
        print(f"  WARNING: {OSM_CACHE_PATH} not found - unpaved-road demotion rule "
              f"will not fire (fails open, treating all roads as pavable)")
        _ROAD_SURFACE_INDEX = index
        return index

    raw = json.loads(OSM_CACHE_PATH.read_text(encoding="utf-8"))
    elements = raw.get("elements", [])
    node_latlon = {e["id"]: (e["lat"], e["lon"]) for e in elements if e["type"] == "node"}
    ways = [e for e in elements if e["type"] == "way"]

    from collections import defaultdict
    by_name = defaultdict(lambda: {"paved_m": 0.0, "unpaved_m": 0.0, "minor_m": 0.0,
                                    "tiger_no_surface_m": 0.0, "other_m": 0.0, "total_m": 0.0})
    for w in ways:
        tags = w.get("tags", {})
        name = tags.get("name") or tags.get("ref") or ""
        if not name:
            continue
        highway = tags.get("highway", "")
        surface = tags.get("surface", "")
        length_m = _way_length_m(w, node_latlon)
        if length_m <= 0:
            continue
        rec = by_name[name]
        rec["total_m"] += length_m
        has_tiger = any(k.startswith("tiger:") for k in tags)
        if highway in MINOR_HIGHWAY_CLASSES:
            rec["minor_m"] += length_m
        elif surface in UNPAVED_SURFACE_TAGS:
            rec["unpaved_m"] += length_m
        elif surface in PAVED_SURFACE_TAGS:
            rec["paved_m"] += length_m
        elif not surface and has_tiger and tags.get("tiger:reviewed") not in ("yes", "aerial") and \
                highway in ("residential", "unclassified"):
            rec["tiger_no_surface_m"] += length_m
        else:
            rec["other_m"] += length_m

    for name, rec in by_name.items():
        total = rec["total_m"]
        if total <= 0:
            continue
        if rec["paved_m"] / total >= 0.5:
            # Explicit paved-surface tag dominates - never demote even if a
            # minority stretch is unpaved/unknown (e.g. Boreas Pass Road's
            # own lower, in-town, actually-paved segment doesn't save the
            # 79% gravel/compacted majority above it, but a road that's
            # mostly paved shouldn't demote over a minor unpaved fraction).
            unpaved, reason = False, "OSM surface tag mostly paved"
        elif rec["minor_m"] / total >= MINOR_HIGHWAY_LENGTH_FRAC:
            unpaved = True
            reason = (f"highway=track/path/service/etc. over {rec['minor_m']/total*100:.0f}% "
                      f"of this road's OSM length - not a road decks are built for")
        elif rec["unpaved_m"] / total > UNPAVED_LENGTH_FRAC:
            unpaved = True
            reason = (f"OSM surface tag unpaved (dirt/gravel/etc.) over "
                      f"{rec['unpaved_m']/total*100:.0f}% of this road's OSM length")
        elif rec["tiger_no_surface_m"] / total >= TIGER_UNREVIEWED_LENGTH_FRAC:
            unpaved = True
            reason = ("unreviewed TIGER import, no surface tag anywhere on this road "
                      "(fallback heuristic - see build_road_surface_index() docstring)")
        else:
            unpaved, reason = False, "no unpaved signal found (default eligible)"
        index[name] = (unpaved, reason, dict(rec))

    _ROAD_SURFACE_INDEX = index
    return index


def rebound_shape_ok(s_full, drop_full, run_start, run_end,
                      window_m=REBOUND_WINDOW_M, thresh_m=REBOUND_DROP_M):
    """§7.22 abutment-plausibility test: a real span's terrain profile is a
    bowl - the drop shrinks back down to near road level within `window_m`
    beyond BOTH ends of the run (a real abutment sits on solid ground, not
    mid-slope). A sidehill-cut bench/fill-crest instead holds a plateau - the
    drop stays elevated well past the run's own (hysteresis-drawn) boundary.

    Looks at the FULL side's station arrays beyond the run's own [run_start,
    run_end) slice (a run's own edge stations are, by construction, already
    at-or-above the classification threshold, so the rebound has to be
    checked in the stations the hysteresis machine excluded, not inside the
    run itself).

    A run that touches the very start/end of a segment's station array (no
    further stations to check - the road chain itself ends or hands off to a
    different OSM way there) is treated as passing on that side: there's
    nothing to disprove a real abutment with, and demoting solely because a
    segment boundary was reached would penalize spans at the edge of the
    pipeline's per-chain station data rather than a real geometric signal.

    Returns (passes: bool, before_min_m: float|None, after_min_m: float|None)
    - the raw minimums are returned for the tuning histogram even when the
    run demotes on this rule, so verify_bridge_rebound.png can show the full
    distribution, not just the pass/fail cut.
    """
    n = len(s_full)

    def side_min(is_before):
        if is_before:
            if run_start <= 0:
                return None
            s_edge = s_full[run_start]
            lo = run_start - 1
            while lo >= 0 and s_edge - s_full[lo] <= window_m:
                lo -= 1
            window_slice = drop_full[lo + 1:run_start]
        else:
            if run_end >= n:
                return None
            s_edge = s_full[run_end - 1]
            hi = run_end
            while hi < n and s_full[hi] - s_edge <= window_m:
                hi += 1
            window_slice = drop_full[run_end:hi]
        if len(window_slice) == 0:
            return None
        return float(np.min(window_slice))

    before_min = side_min(True)
    after_min = side_min(False)
    before_ok = before_min is None or before_min <= thresh_m
    after_ok = after_min is None or after_min <= thresh_m
    return (before_ok and after_ok), before_min, after_min


def build_station_runs(ox_cm, oy_cm, z_cm, drop_cm, tile_size_cm):
    """Splits one side's classified station polyline into tile-bucketable
    chunks carrying (x, y, z, drop) per station - mirrors build_road_
    guardrails.py's build_station_runs(), extended with the drop value the
    C++ manager needs to size the 1.5:1 slope."""
    n = len(ox_cm)
    runs = []
    for c0 in range(0, max(1, n - 1), WALL_CHUNK_STATIONS):
        c1 = min(c0 + WALL_CHUNK_STATIONS, n - 1)
        if c1 <= c0 and n > 1:
            continue
        pts = [(ox_cm[i], oy_cm[i], z_cm[i], drop_cm[i]) for i in range(c0, c1 + 1)]
        if len(pts) < 2:
            continue
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        tile_key = f"{int(cx // tile_size_cm)},{int(cy // tile_size_cm)}"
        runs.append({
            "tile": tile_key,
            "station_xyzd_cm": [round(v, 1) for p in pts for v in p],
        })
    return runs


def compute_side(seg, side, lat0, lon0, m_per_deg_lon, lakes, junction_touched_seg,
                  is_bridge_osm_seg, session, dem_cache):
    """One side's full detection pipeline (DEM probe -> drop -> hysteresis
    classification -> min-run demotion). Split out from main() so the bridge
    sub-classification pass below can see BOTH sides of a segment before
    committing to run geometry."""
    east = np.array(seg["east_m"])
    north = np.array(seg["north_m"])
    elev = np.array(seg["elev_m"])
    s = np.array(seg["s_m"])
    n = len(east)
    road_name = seg.get("road_name", [""] * n)
    in_junction_field = np.array(seg.get("in_junction_box", [False] * n), dtype=bool)
    in_junction_box = in_junction_field | junction_touched_seg

    tx = np.gradient(east)
    ty = np.gradient(north)
    tl = np.hypot(tx, ty)
    tl[tl < 1e-6] = 1.0
    tx /= tl
    ty /= tl
    rx, ry = -ty, tx

    road_z_ue_m = road_ue_z_m(elev, 0.0, ORIGIN_HEIGHT_M)
    z_cm_all = road_z_ue_m * 100.0

    probe_e = east + rx * EMBANK_PROBE_OFFSET_M * side
    probe_n = north + ry * EMBANK_PROBE_OFFSET_M * side
    edge_e = east + rx * EMBANK_EDGE_OFFSET_M * side
    edge_n = north + ry * EMBANK_EDGE_OFFSET_M * side

    probe_lat = lat0 + probe_n / M_PER_DEG_LAT
    probe_lon = lon0 + probe_e / m_per_deg_lon
    terrain_elev = sample_dem(probe_lat, probe_lon, NEAR_DEM_ZOOM, session, dem_cache)

    drop_m = elev - terrain_elev

    edge_cx = edge_e * 100.0
    edge_cy = -edge_n * 100.0

    in_lake = np.zeros(n, dtype=bool)
    lake_z_cm = np.full(n, np.nan)   # §7.23: baked lake surface Z (cm) at whichever
                                       # lake polygon this edge point falls inside, NaN
                                       # elsewhere - used for the water-crossing
                                       # clearance guard and the pier/deck water-surface
                                       # termination (see module docstring)
    bbox_margin_cm = LAKE_SHORE_EXCLUSION_MARGIN_M * 100.0
    for lake in lakes:
        bx0, bx1, by0, by1 = lake["bbox"]
        bb = ((edge_cx >= bx0 - bbox_margin_cm) & (edge_cx <= bx1 + bbox_margin_cm) &
              (edge_cy >= by0 - bbox_margin_cm) & (edge_cy <= by1 + bbox_margin_cm))
        if not bb.any():
            continue
        idx = np.where(bb)[0]
        inside = _point_in_polygon(edge_cx[idx], edge_cy[idx], lake["ring_x"], lake["ring_y"])
        in_lake[idx] |= inside
        hit_idx = idx[inside]
        lake_z_cm[hit_idx] = lake["z_cm"]  # last-match wins on rare overlap - harmless

    cls_arr = classify_with_hysteresis(drop_m, in_lake, in_junction_box)
    cls_arr = demote_short_runs(cls_arr, s)
    drop_cm = np.clip(drop_m, 0.0, None) * 100.0

    return {
        "east": east, "north": north, "s": s, "road_name": road_name,
        "in_junction_box": in_junction_box, "in_lake": in_lake, "lake_z_cm": lake_z_cm,
        "edge_cx": edge_cx, "edge_cy": edge_cy, "z_cm": z_cm_all,
        "drop_m": drop_m, "drop_cm": drop_cm, "cls_arr": cls_arr,
        "is_bridge_osm": is_bridge_osm_seg,
        "water_crossing": np.zeros(n, dtype=bool),  # filled in by main()'s §7.23 pass
    }


def subclassify_bridge_runs(seg_i, side, d, other, lat0, lon0, m_per_deg_lon, report):
    """§7.21: re-tests every BRIDGE_CLS run against the water/OSM-tag/short-
    and-both-sided discriminator (module docstring). Demotes a failing run's
    cls_arr in place to EMBANK_CLS with a capped drop_cm, and appends one
    record per run (genuine or demoted) to `report` for bridge_subclass_
    report.json."""
    cls_arr = d["cls_arr"]
    drop_m = d["drop_m"]
    drop_cm = d["drop_cm"]
    s = d["s"]
    surface_index = build_road_surface_index()
    for run_start, run_end in contiguous_runs(cls_arr == BRIDGE_CLS):
        run_len_m = float(s[run_end - 1] - s[run_start]) if run_end > run_start else 0.0
        # §7.23: water_frac now reads the GUARDED water_crossing mask (both-edges-in-
        # lake, or one-edge-in-lake with real clearance above the baked water surface -
        # see module docstring), not the raw in_lake geometry test - a run that merely
        # brushes a lake polygon at grade (Swan Mountain Road's shoreline stretch) must
        # NOT unconditionally pass here, only a run with an actual water-crossing
        # signal should.
        water_frac = float(d["water_crossing"][run_start:run_end].mean())
        osm_frac = float(d["is_bridge_osm"][run_start:run_end].mean())
        other_slice = other["cls_arr"][run_start:run_end]
        both_sided_frac = float((other_slice != NONE_CLS).mean()) if len(other_slice) else 0.0
        max_drop = float(drop_m[run_start:run_end].max())
        edge_drop = float(0.5 * (drop_m[run_start] + drop_m[run_end - 1]))
        rise_ratio = (max_drop - edge_drop) / max_drop if max_drop > 1e-6 else 0.0

        names = [nm for nm in d["road_name"][run_start:run_end] if nm]
        road_name_top = Counter(names).most_common(1)[0][0] if names else "(unnamed)"

        # §7.22 rule 1: unpaved/minor roads never get a concrete deck,
        # regardless of drop shape (checked before rule 2's short+both-sided
        # test, since it demotes independently of it).
        surf_entry = surface_index.get(road_name_top)
        is_unpaved = bool(surf_entry[0]) if surf_entry else False
        unpaved_reason = surf_entry[1] if surf_entry else "road not found in OSM surface index (fail open)"

        # §7.22 rule 2: terrain-rebound (abutment plausibility) - a real span's
        # drop profile is a bowl (rebounds near both ends), a sidehill
        # bench/fill-crest is a plateau (still dropping steadily past the
        # run's own boundary).
        rebound_ok, rebound_before_min, rebound_after_min = rebound_shape_ok(s, drop_m, run_start, run_end)

        if _DEBUG_REBOUND_DUMP is not None and water_frac < BRIDGE_WATER_MIN_FRAC and \
                run_len_m <= BRIDGE_GENUINE_MAX_LEN_M and both_sided_frac >= BRIDGE_BOTH_SIDED_MIN_FRAC and \
                not is_unpaved:
            # threshold-tuning dump (gated on OD_DEBUG_REBOUND env var, see
            # scratchpad/plot_rebound_tuning.py) - wide window (250 m) so the
            # tuning plot can see well past REBOUND_WINDOW_M in both directions.
            dbg_window = 250.0
            s_edge0 = s[run_start]
            lo = run_start
            while lo > 0 and s_edge0 - s[lo - 1] <= dbg_window:
                lo -= 1
            s_edge1 = s[run_end - 1]
            hi = run_end
            while hi < len(s) and s[hi] - s_edge1 <= dbg_window:
                hi += 1
            _DEBUG_REBOUND_DUMP.append({
                "road_name": road_name_top, "run_len_m": run_len_m,
                "seg": int(seg_i), "side": int(side),
                "s_rel": [float(v - s[run_start]) for v in s[lo:hi]],
                "drop_m": [float(v) for v in drop_m[lo:hi]],
                "run_start_s_rel": 0.0, "run_end_s_rel": float(s[run_end - 1] - s[run_start]),
                "rebound_before_min_m": rebound_before_min, "rebound_after_min_m": rebound_after_min,
            })

        reasons = []
        genuine = False
        if water_frac >= BRIDGE_WATER_MIN_FRAC:
            # Unconditional OR - a water crossing always gets a deck, per the
            # task brief, regardless of the paved/rebound rules below (a
            # genuine reservoir/creek crossing IS the abutment-plausibility
            # signal already - there's a real gap under the deck by
            # definition of "over open water").
            genuine = True
            reasons.append(f"water crossing ({water_frac * 100:.0f}% of run over mapped open water)")
        # NOTE: osm_frac is measured and reported (see module docstring) but
        # deliberately NOT used as a discriminator - live-checked mid-pass and
        # found contaminated: seg["is_bridge"] = expand_carve_exclusion(
        # is_struct_s)|conflict_s, and conflict_s comes from classify_wall_
        # stations()'s OWN 150m same-road-name conflict mask (CONFLICT_RADIUS_M),
        # a DIFFERENT call from reconcile_junction_elevations() that SS7.20
        # explicitly did NOT touch ("correctly sized for classify_wall_stations()'s
        # different... purpose and left untouched there"). I-70/"Gerald R. Ford
        # Memorial Highway" runs same-name parallel carriageways for km at a
        # time - EXACTLY the geometry that made reconcile_junction_elevations()
        # false-match before SS7.20's fix - so conflict_s (and therefore this
        # OSM proxy) still misfires pervasively on I-70 through this unfixed
        # sibling mask. Using it as a discriminator would have kept the East
        # Portal run "genuine" at 58-100% "OSM" coverage for exactly the wrong
        # reason. Left in the report for the record, not the decision.
        elif (run_len_m <= BRIDGE_GENUINE_MAX_LEN_M and both_sided_frac >= BRIDGE_BOTH_SIDED_MIN_FRAC
                and not is_unpaved and rebound_ok):
            genuine = True
            reasons.append(f"short span ({run_len_m:.0f} m) with drop on both sides "
                            f"({both_sided_frac * 100:.0f}% opposite-side overlap), paved road, "
                            f"terrain rebounds near both ends (bowl shape, not a plateau)")

        if not genuine:
            demote_reasons = []
            if water_frac < BRIDGE_WATER_MIN_FRAC:
                if run_len_m > BRIDGE_GENUINE_MAX_LEN_M or both_sided_frac < BRIDGE_BOTH_SIDED_MIN_FRAC:
                    demote_reasons.append(
                        f"sidehill cut: one-sided ({both_sided_frac * 100:.0f}% opposite-side "
                        f"overlap), {run_len_m:.0f} m run (exceeds the {BRIDGE_GENUINE_MAX_LEN_M:.0f} m "
                        f"genuine-span length or fails the both-sided test)")
                if is_unpaved:
                    demote_reasons.append(f"unpaved/minor road: {unpaved_reason}")
                if not rebound_ok:
                    below = []
                    if rebound_before_min is not None and rebound_before_min > REBOUND_DROP_M:
                        below.append(f"before-end min drop {rebound_before_min:.1f} m")
                    if rebound_after_min is not None and rebound_after_min > REBOUND_DROP_M:
                        below.append(f"after-end min drop {rebound_after_min:.1f} m")
                    demote_reasons.append(
                        f"no terrain rebound within {REBOUND_WINDOW_M:.0f} m of run end(s) "
                        f"(plateau, not a bowl: {'; '.join(below)}, threshold {REBOUND_DROP_M:.1f} m)")
                if not demote_reasons:
                    # both-sided+short+paved+rebound all passed individually
                    # but genuine stayed False - shouldn't happen given the
                    # elif above, kept as a defensive fallback message.
                    demote_reasons.append("failed combined genuine test")
            reasons.extend(demote_reasons)
            cls_arr[run_start:run_end] = EMBANK_CLS
            cap_cm = DEMOTED_BRIDGE_DROP_CAP_M * 100.0
            drop_cm[run_start:run_end] = np.minimum(drop_cm[run_start:run_end], cap_cm)

        mid = (run_start + run_end) // 2
        mid_lat = lat0 + d["north"][mid] / M_PER_DEG_LAT
        mid_lon = lon0 + d["east"][mid] / m_per_deg_lon

        report.append({
            "seg": int(seg_i), "side": int(side), "road_name": road_name_top,
            "length_m": round(run_len_m, 1), "max_drop_m": round(max_drop, 2),
            "mean_drop_m": round(float(drop_m[run_start:run_end].mean()), 2),
            "water_frac": round(water_frac, 3), "osm_frac": round(osm_frac, 3),
            "both_sided_frac": round(both_sided_frac, 3), "rise_ratio": round(rise_ratio, 3),
            "is_unpaved": is_unpaved, "unpaved_reason": unpaved_reason,
            "rebound_ok": bool(rebound_ok),
            "rebound_before_min_m": None if rebound_before_min is None else round(rebound_before_min, 2),
            "rebound_after_min_m": None if rebound_after_min is None else round(rebound_after_min, 2),
            "genuine": genuine, "reasons": reasons,
            "mid_lat": round(float(mid_lat), 6), "mid_lon": round(float(mid_lon), 6),
        })


def main():
    data = json.loads(STATIONS_PATH.read_text())
    lat0, lon0 = data["origin_latlon"]
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(math.radians(lat0))

    lakes = load_lake_beds()
    if lakes:
        print(f"{len(lakes)} lake bed(s) loaded - qualifying-drop stations over "
              f"open water are classified 'bridge', never 'embankment'")

    # cross-chain junction proximity (see module docstring's "Exclusions" note
    # for why this reuses build_road_guardrails.py's own `touched`-mask
    # mechanism instead of the narrower in_junction_box field alone).
    all_east = np.concatenate([np.array(seg["east_m"]) for seg in data["segments"]])
    all_north = np.concatenate([np.array(seg["north_m"]) for seg in data["segments"]])
    all_elev = np.concatenate([np.array(seg["elev_m"]) for seg in data["segments"]])
    all_seg_idx = np.concatenate([
        np.full(len(seg["east_m"]), i, dtype=np.int32) for i, seg in enumerate(data["segments"])])
    all_station_idx = np.concatenate([np.arange(len(seg["east_m"])) for seg in data["segments"]])
    all_road_name = [name for seg in data["segments"] for name in seg["road_name"]]
    all_is_bridge = np.concatenate([np.array(seg["is_bridge"], dtype=bool) for seg in data["segments"]])
    _reconciled, junction_touched, _pinned = reconcile_junction_elevations(
        all_east, all_north, all_elev, all_seg_idx, all_station_idx, all_road_name,
        is_bridge=all_is_bridge)
    offsets = np.concatenate([[0], np.cumsum([len(seg["east_m"]) for seg in data["segments"]])])
    print(f"{int(junction_touched.sum())} station(s) flagged near a road-road junction "
          f"(embankment/bridge classification forces 'none' there)")

    session = requests.Session()
    dem_cache = {}

    runs_out = []
    km_by_cls = {"embankment": 0.0, "bridge": 0.0}
    stations_by_cls = {"embankment": 0, "bridge": 0}
    n_skipped_junction = 0
    n_runs_by_cls = {"embankment": 0, "bridge": 0}
    dam_report = []
    bridge_report = []
    n_bridge_runs_pre = 0
    n_bridge_runs_demoted = 0
    n_water_crossing_stations = 0
    n_water_crossing_both_edges = 0

    for seg_i, seg in enumerate(data["segments"]):
        n = len(seg["east_m"])
        if n < 3:
            continue
        lo, hi = offsets[seg_i], offsets[seg_i + 1]
        junction_touched_seg = junction_touched[lo:hi]
        is_bridge_osm_seg = (np.array(seg["is_bridge"], dtype=bool) &
                              ~np.array(seg["is_tunnel"], dtype=bool))

        sides = {}
        for side in (-1.0, 1.0):
            sides[side] = compute_side(seg, side, lat0, lon0, m_per_deg_lon, lakes,
                                        junction_touched_seg, is_bridge_osm_seg,
                                        session, dem_cache)
            n_skipped_junction += int((sides[side]["in_junction_box"] &
                                        (sides[side]["drop_m"] >= EMBANK_LOW_THRESHOLD_M)).sum())

        # §7.23: water-crossing-as-first-class-trigger override, BEFORE §7.21's
        # sub-classification (so subclassify_bridge_runs()'s water_frac test -
        # which now reads d["water_crossing"], not raw in_lake, see that
        # function's docstring - sees the fully-resolved mask). See module
        # docstring for the offline inventory this threshold was calibrated
        # against (Swan Mountain Road's at-grade shoreline stretch vs. Dillon
        # Dam Road never touching a lake polygon at all).
        for side in (-1.0, 1.0):
            d = sides[side]
            other = sides[-side]
            both_edges_in_lake = d["in_lake"] & other["in_lake"]
            clearance_m = d["z_cm"] / 100.0 - d["lake_z_cm"] / 100.0  # NaN-safe: comparisons with NaN are False
            guarded_one_sided = (d["in_lake"] & ~both_edges_in_lake &
                                  (clearance_m >= WATER_CROSSING_MIN_CLEARANCE_M))
            water_crossing = (both_edges_in_lake | guarded_one_sided) & ~d["in_junction_box"]
            d["water_crossing"] = water_crossing
            n_water_crossing_stations += int(water_crossing.sum())
            n_water_crossing_both_edges += int((both_edges_in_lake & ~d["in_junction_box"]).sum())
            if water_crossing.any():
                d["cls_arr"] = np.where(water_crossing, BRIDGE_CLS, d["cls_arr"]).astype(np.int8)
                d["cls_arr"] = demote_short_runs(d["cls_arr"], d["s"])

            # Pier/deck termination at the WATER SURFACE (baked lake Z), not the
            # noisy raw-DEM sample - any in_lake station (whichever rule promoted
            # it to bridge) ships a drop_cm measured against the lake's own flat
            # surface plus WATER_PIER_SUBMERGENCE_M, so build_road_bridges.py's
            # existing "drop_cm - DECK_DEPTH_M" pier-height math terminates the
            # pier foot a half-meter below the real water surface instead of at
            # whatever the raw DEM happened to read under the lake.
            has_lake_z = d["in_lake"] & ~np.isnan(d["lake_z_cm"])
            if has_lake_z.any():
                water_drop_cm = (d["z_cm"] - (d["lake_z_cm"] - WATER_PIER_SUBMERGENCE_M * 100.0))
                d["drop_cm"] = np.where(has_lake_z, np.clip(water_drop_cm, 0.0, None), d["drop_cm"])

        # §7.21: sub-classify every bridge-threshold run on each side against
        # the OTHER side's data before committing to final run geometry.
        for side in (-1.0, 1.0):
            d = sides[side]
            other = sides[-side]
            n_bridge_runs_pre += len(contiguous_runs(d["cls_arr"] == BRIDGE_CLS))
            before_report_len = len(bridge_report)
            subclassify_bridge_runs(seg_i, side, d, other, lat0, lon0, m_per_deg_lon, bridge_report)
            n_bridge_runs_demoted += sum(
                1 for rec in bridge_report[before_report_len:] if not rec["genuine"])

        for side in (-1.0, 1.0):
            d = sides[side]
            road_name = d["road_name"]
            edge_cx, edge_cy, z_cm_all, drop_cm = d["edge_cx"], d["edge_cy"], d["z_cm"], d["drop_cm"]
            drop_m = d["drop_m"]
            cls_arr = d["cls_arr"]

            for target_cls, cls_name in CLS_NAMES.items():
                for run_start, run_end in contiguous_runs(cls_arr == target_cls):
                    run_len_m = d["s"][run_end - 1] - d["s"][run_start]
                    km_by_cls[cls_name] += run_len_m / 1000.0
                    stations_by_cls[cls_name] += (run_end - run_start)
                    n_runs_by_cls[cls_name] += 1

                    station_runs = build_station_runs(
                        edge_cx[run_start:run_end], edge_cy[run_start:run_end],
                        z_cm_all[run_start:run_end], drop_cm[run_start:run_end],
                        TERRAIN_TILE_SIZE_CM)
                    for sr in station_runs:
                        sr["side"] = int(side)
                        sr["cls"] = cls_name
                        runs_out.append(sr)

                    names = set(road_name[run_start:run_end])
                    if any("Dillon Dam" in (nm or "") for nm in names):
                        dam_report.append((cls_name, run_len_m, float(drop_m[run_start:run_end].max())))

    OUT_PATH.write_text(json.dumps({"runs": runs_out}))
    REPORT_PATH.write_text(json.dumps({
        "runs": bridge_report,
        "thresholds": {
            "genuine_max_len_m": BRIDGE_GENUINE_MAX_LEN_M,
            "both_sided_min_frac": BRIDGE_BOTH_SIDED_MIN_FRAC,
            "water_min_frac": BRIDGE_WATER_MIN_FRAC,
            "osm_tag_min_frac": BRIDGE_OSM_TAG_MIN_FRAC,
            "demoted_drop_cap_m": DEMOTED_BRIDGE_DROP_CAP_M,
            "unpaved_length_frac": UNPAVED_LENGTH_FRAC,
            "minor_highway_length_frac": MINOR_HIGHWAY_LENGTH_FRAC,
            "tiger_unreviewed_length_frac": TIGER_UNREVIEWED_LENGTH_FRAC,
            "rebound_window_m": REBOUND_WINDOW_M,
            "rebound_drop_m": REBOUND_DROP_M,
            "water_crossing_min_clearance_m": WATER_CROSSING_MIN_CLEARANCE_M,
            "water_pier_submergence_m": WATER_PIER_SUBMERGENCE_M,
        },
    }, indent=1))

    total_embank_stations = stations_by_cls["embankment"]
    total_bridge_stations = stations_by_cls["bridge"]
    print(f"{len(runs_out)} run(s) ({total_embank_stations + total_bridge_stations} station(s)) -> {OUT_PATH}")
    print(f"  embankment: {n_runs_by_cls['embankment']} run(s), {total_embank_stations} station(s), "
          f"~{km_by_cls['embankment']:.2f} km")
    print(f"  bridge:     {n_runs_by_cls['bridge']} run(s), {total_bridge_stations} station(s), "
          f"~{km_by_cls['bridge']:.2f} km")
    print(f"  skipped (junction-box, qualifying drop): {n_skipped_junction} station(s)")
    n_genuine = sum(1 for r in bridge_report if r["genuine"])
    print(f"  §7.21 sub-classification: {n_bridge_runs_pre} bridge-threshold run(s) found pre-tile-chunking, "
          f"{n_genuine} genuine ({n_bridge_runs_pre - n_genuine} demoted to embankment) -> {REPORT_PATH}")
    print(f"  §7.23 water-crossing trigger: {n_water_crossing_stations} station(s) forced to 'bridge' by "
          f"lake-polygon geometry regardless of DEM drop ({n_water_crossing_both_edges} via the unconditional "
          f"both-edges-in-water case, {n_water_crossing_stations - n_water_crossing_both_edges} via the "
          f"guarded one-edge-in-water+{WATER_CROSSING_MIN_CLEARANCE_M:.1f}m-clearance case)")
    if dam_report:
        print("  Dillon Dam Road runs found:")
        for cls_name, run_len_m, max_drop in dam_report:
            print(f"    {cls_name}: {run_len_m:.1f} m run, max drop {max_drop:.2f} m")
    else:
        print("  Dillon Dam Road: no qualifying run found on this side/segment pass")

    if _DEBUG_REBOUND_DUMP is not None:
        dbg_path = REPORT_PATH.parent / "bridge_rebound_debug.json"
        dbg_path.write_text(json.dumps(_DEBUG_REBOUND_DUMP))
        print(f"  OD_DEBUG_REBOUND: {len(_DEBUG_REBOUND_DUMP)} candidate profile(s) -> {dbg_path}")


if __name__ == "__main__":
    main()
