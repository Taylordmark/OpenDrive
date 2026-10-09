"""Shared road-mesh math: station resampling, grade-smoothed elevation,
superelevation banking, skirt cross-section, and chunked vertex/UV/triangle
mesh assembly.

Extracted from build_stage.py (Arc 7.2 slice 1) so build_road_network.py can
build meshes for an arbitrary OSM road network without duplicating this math.
Behavior is unchanged from the original inline code in build_stage.py.

Coordinate frame (matches the real-world georeferencing convention used
elsewhere in the project): +X = east,
+Y = south, +Z = up, units cm. Callers project lat/lon to a local east/north
meter frame around a shared origin before calling into this module.
"""

import math
from collections import defaultdict

import numpy as np

from test_areas import ELEV_PLAUSIBLE_MAX_M, ELEV_PLAUSIBLE_MIN_M

STATION_SPACING = 5.0        # m between road samples
ROAD_HALF_WIDTH = 4.5        # m, asphalt edge
SKIRT_EXTRA = 3.0            # m beyond the pavement edge the terrain carve clamps
                             # (the road mesh itself no longer reaches this far -
                             # see WRAP_* below)
SKIRT_DROP = 2.5             # m - no longer shapes the road mesh (the 2.5 m
                             # curtain became the WRAP_* underside, 2026-09-28);
                             # kept only because generation_id hashes it
# Road cross-section wrap (2026-09-28, Taylor: the 3 m / 2.5 m skirt curtain
# hung visibly under raised roads - wanted the road to "wrap around the
# bottom in asphalt color" instead). Each station row is a closed loop:
# underside-L, lip-L, pave-L, pave-R, lip-R, underside-R, underside-L again
# (duplicate so the underside strip closes). At grade the lip dives under the
# carved terrain (CARVE_DROP 0.15 m below) within ~0.15 m of the edge; on a
# raised road it reads as a thick asphalt slab.
WRAP_LIP_OUT_M = 0.5         # lip's lateral reach past the pavement edge
WRAP_LIP_DROP_M = 0.6        # lip bottom below the pavement
WRAP_DEPTH_M = 1.0           # slab underside below the pavement (bridge decks are 1.25)
ROAD_PROFILE_COLS = 7        # verts per station row - consumers index by this
ROAD_PAVE_COLS = (2, 3)      # pave-L / pave-R columns within a row
ROAD_LIFT = 0.10             # m above smoothed ground (wishlist #11: keep the
                             # road close to terrain; build_terrain_tiles.py's
                             # CARVE_DROP_M carves the terrain further below)
MAX_BANK_RAD = 0.14          # ~8.0 deg superelevation cap (was 0.09/~5.2 deg -
                              # Taylor, 2026-08-23: wanted more banking through
                              # curves after the Green Valley drive-test)
BANK_GAIN = 9.55             # bank = min(cap, gain * |curvature 1/m|)
CHUNK_STATIONS = 150         # stations per road mesh chunk (~750 m)

# Unpaved roads (2026-09-30; three classes 2026-10-06, PLAN-REALISM Phase 2):
# OSM surface= -> SURFACE_PAVED / SURFACE_GRAVEL (M_Road_Gravel + PM_Gravel) /
# SURFACE_DIRT (M_Road_Dirt + PM_DirtRoad). Untagged ("") is paved, except
# on UNPAVED_DEFAULT_HIGHWAY classes, which default to gravel. Chunks split
# wherever the class flips, so a run shorter than MIN_SURFACE_RUN_STATIONS
# is absorbed into its neighbor rather than spawning a sliver chunk for one
# oddly-tagged OSM way.
SURFACE_PAVED, SURFACE_GRAVEL, SURFACE_DIRT = 0, 1, 2
SURFACE_CLASS_NAMES = ("paved", "gravel", "dirt")
GRAVEL_SURFACE_TAGS = {"gravel", "fine_gravel", "compacted", "unpaved", "pebblestone"}
DIRT_SURFACE_TAGS = {"dirt", "ground", "earth", "mud", "sand", "grass"}
UNPAVED_DEFAULT_HIGHWAY = {"track"}   # untagged track-like ways
MIN_SURFACE_RUN_STATIONS = 20   # 100 m at STATION_SPACING


def surface_class(surface, highway=None):
    """SURFACE_* class for one OSM surface= value (+ highway= for untagged)."""
    if surface in GRAVEL_SURFACE_TAGS:
        return SURFACE_GRAVEL
    if surface in DIRT_SURFACE_TAGS:
        return SURFACE_DIRT
    return SURFACE_GRAVEL if not surface and highway in UNPAVED_DEFAULT_HIGHWAY else SURFACE_PAVED


def unpaved_stations(surfaces, highway_classes=None, min_run=MIN_SURFACE_RUN_STATIONS):
    """Per-station SURFACE_* class (0 = paved, so truthy = unpaved), short
    runs absorbed into the preceding run (or the following one, for a short
    leading run)."""
    flags = [surface_class(s, highway_classes[i] if highway_classes else None)
             for i, s in enumerate(surfaces)]
    out = list(flags)
    n, i = len(flags), 0
    while i < n:
        j = i
        while j < n and flags[j] == flags[i]:
            j += 1
        if j - i < min_run and (i > 0 or j < n):
            fill = out[i - 1] if i > 0 else flags[j]
            out[i:j] = [fill] * (j - i)
        i = j
    return out


def chunk_spans(n_st, chunk_stations, is_unpaved=None):
    """(c0, c1) station spans for build_road_chunks: every chunk_stations,
    plus a cut wherever is_unpaved flips so each chunk has one surface.
    Adjacent spans share their boundary station row, as before."""
    cuts = [0]
    if is_unpaved is not None:
        cuts += [i for i in range(1, n_st - 1) if is_unpaved[i] != is_unpaved[i - 1]]
    cuts.append(n_st - 1)
    return [(c0, min(c0 + chunk_stations, b))
            for a, b in zip(cuts, cuts[1:]) for c0 in range(a, b, chunk_stations)]

# Per-highway-class vertical constraints (2026-07-28, Taylor: a road must
# never smooth the TERRAIN - it rides the ground or it goes around it via
# a cut/bridge/tunnel). Two separate limits per OSM highway= class:
#   max_grade       - how steep the road itself is ever allowed to be.
#   max_grade_delta - how much the grade is allowed to change from one
#                      station EDGE to the next (a vertical-curve ease on
#                      the road's own profile, not terrain smoothing - it's
#                      what keeps a transition, e.g. a bridge approach or
#                      the top of a cut, from reading as a jarring kink).
# Both tighten with expected design speed: a motorway rider doing 200+
# needs a much gentler grade AND a much gentler grade change than someone
# on a residential street at low speed, where an abrupt-but-small grade
# change is a non-event. First-pass numbers only (ship a guess, tune by
# drive-test) - not pulled from an official design-speed table.
# highway= tag -> (max_grade, max_grade_delta per STATION_SPACING edge)
ROAD_CLASS_GRADE = {
    "motorway":       (0.06, 0.005),
    "motorway_link":  (0.08, 0.010),
    "trunk":          (0.07, 0.006),
    "trunk_link":     (0.08, 0.010),
    "primary":        (0.08, 0.010),
    "primary_link":   (0.09, 0.015),
    "secondary":      (0.09, 0.015),
    "secondary_link": (0.10, 0.020),
    "tertiary":       (0.10, 0.020),
    "tertiary_link":  (0.10, 0.020),
    "unclassified":   (0.10, 0.020),
    "residential":    (0.12, 0.025),
    "living_street":  (0.12, 0.030),
    "service":        (0.15, 0.030),
    "track":          (0.18, 0.040),
}
DEFAULT_ROAD_CLASS_GRADE = (0.10, 0.020)   # unrecognized/missing highway= tag
MAX_GRADE = DEFAULT_ROAD_CLASS_GRADE[0]    # kept for callers/comments that
                                            # just want a single reasonable
                                            # ceiling, not per-class detail

# Phase 2a road-visual overhaul (2026-09-03): a small road-CLASS id table so
# the material can pick lane-marking rules (lane count, divider style)
# per chunk without carrying the full OSM highway= string at runtime. Grouped
# coarser than ROAD_CLASS_GRADE above (that one needs every class distinct
# for grade tuning; this one only needs "how many lanes / what markings"
# distinctions, which motorway/motorway_link share, etc.) - collapsed to 7
# buckets. Order is the wire id (0..6); do not reorder without a coordinated
# C++/material update on whichever format version uses it (see ODR3 in
# export_road_mesh_bin_v2.py - the current shipped ODR2 sidecars don't carry
# this at all).
ROAD_CLASS_ID = {
    "motorway": 0, "motorway_link": 0,
    "trunk": 1, "trunk_link": 1,
    "primary": 2, "primary_link": 2,
    "secondary": 3, "secondary_link": 3,
    "tertiary": 4, "tertiary_link": 4, "unclassified": 4,
    "residential": 5, "living_street": 5,
    "service": 6, "track": 6,
}
DEFAULT_ROAD_CLASS_ID = 6   # unrecognized/missing highway= tag - treat like
                             # the narrowest/least-marked class
ROAD_CLASS_ID_NAMES = ["motorway", "trunk", "primary", "secondary",
                        "tertiary", "residential", "service"]

# First-pass lane-count guesses per highway= class (2026-09-03, ship-a-guess
# same as ROAD_CLASS_GRADE - real OSM lanes= tags aren't fetched by
# fetch_osm_network.py today, so this is a typical-case heuristic for the
# material's lane-marking logic, not authoritative per-way data). Value is
# the TWO-WAY total lane count for that class; an OSM way tagged oneway=yes
# is assumed to be one carriageway of a divided road, so its lane count is
# halved (rounded up, floor 1) in road_class_and_lanes() below rather than
# doubling the table.
ROAD_CLASS_LANES_TWOWAY = {
    "motorway": 6, "motorway_link": 2,
    "trunk": 4, "trunk_link": 2,
    "primary": 2, "primary_link": 1,
    "secondary": 2, "secondary_link": 1,
    "tertiary": 2, "tertiary_link": 1,
    "unclassified": 2,
    "residential": 2, "living_street": 1,
    "service": 1, "track": 1,
}
DEFAULT_ROAD_CLASS_LANES_TWOWAY = 2   # unrecognized/missing highway= tag

# Per-road width from lane count (2026-09-06, AUDIT § 7 item 6 "OSM road
# tags -> width"). LANE_WIDTH_M is deliberately the game's established
# 2-lane scale (2 * ROAD_HALF_WIDTH / 2 lanes = 4.5 m/lane - wider than a
# real ~3.5 m lane, matching the whole world's slightly-forgiving scale),
# so an untagged or genuinely-2-lane road keeps EXACTLY today's 9 m
# pavement and nothing already-verified moves. The clamp bounds:
#   floor 3.0 m half-width - a 1-lane service/track still fits a car with
#     margin (6 m full) instead of collapsing to 4.5 m;
#   cap 8.0 m half-width - pavement + SKIRT_EXTRA (3 m) = 11 m footprint
#     stays inside AODForestManager's 11.5 m PavementExclusionCm and the
#     barrier line's ~13 m offset, so no existing exclusion band needs
#     retuning for the widest tagged roads.
LANE_WIDTH_M = 4.5
MIN_HALF_WIDTH_M = 3.0
MAX_HALF_WIDTH_M = 8.0
# Width-change taper: smooth the per-station half-width with this sigma
# (meters of arc length) so a lanes= tag boundary becomes a ~50 m taper,
# the way real pavement gains/loses a lane, not a lateral step mid-road.
WIDTH_SMOOTH_SIGMA_M = 15.0


def half_width_for_stations(highway_classes, is_oneway=None, osm_lanes=None,
                             spacing=STATION_SPACING):
    """Per-station pavement HALF-width (m) from real lanes= tags where
    present (osm_lanes, 0 = untagged) falling back to the
    ROAD_CLASS_LANES_TWOWAY class-table guess (halved for oneway
    carriageways, same heuristic road_class_and_lanes() uses), then
    Gaussian-tapered (WIDTH_SMOOTH_SIGMA_M) so width changes ramp over
    ~3-sigma instead of stepping sideways at a way boundary. Returns a
    float np array parallel to highway_classes."""
    n = len(highway_classes)
    hw = np.empty(n)
    for i in range(n):
        lanes = int(osm_lanes[i]) if osm_lanes is not None and int(osm_lanes[i]) > 0 else 0
        if lanes == 0:
            lanes = ROAD_CLASS_LANES_TWOWAY.get(highway_classes[i],
                                                DEFAULT_ROAD_CLASS_LANES_TWOWAY)
            if is_oneway is not None and is_oneway[i] and lanes >= 2:
                lanes = max(1, -(-lanes // 2))
        hw[i] = lanes * LANE_WIDTH_M / 2.0
    hw = np.clip(hw, MIN_HALF_WIDTH_M, MAX_HALF_WIDTH_M)
    sigma_st = WIDTH_SMOOTH_SIGMA_M / spacing
    if n > 1 and sigma_st > 0:
        hw = gaussian1d(hw, sigma_st)
    return np.clip(hw, MIN_HALF_WIDTH_M, MAX_HALF_WIDTH_M)


def road_class_and_lanes(highway_classes, is_oneway=None, osm_lanes=None):
    """Majority-vote a chunk's stations' OSM highway= class into a single
    (road_class_id, road_class_name, lane_count) triple - same "pick a
    single per-chunk value from many per-station tags" approach
    build_road_chunks() already uses for its oneway output flag (a chunk
    boundary essentially never lands mid-carriageway, so this is a
    tie-breaker, not a meaningfully lossy reduction).

    `is_oneway` (optional, parallel bool array/list) - when true for the
    majority-class stations, the two-way lane count from
    ROAD_CLASS_LANES_TWOWAY is halved (round up, floor 1): an OSM way tagged
    oneway=yes is one carriageway of what's usually a divided road, not a
    road with half as many total lanes.

    `osm_lanes` (optional, parallel int array/list, 0 = untagged - 2026-09-06,
    AUDIT § 7 item 6): REAL per-station lanes= tag values from
    road_stations.json. When any station on the chunk carries a real tag,
    the most common tagged value wins outright over the class-table guess,
    and the oneway halving is skipped - OSM's lanes= on a oneway carriageway
    already counts just that carriageway.

    Returns (road_class_id: int, road_class_name: str, lane_count: int),
    lane_count clamped to [1, 8] (uint8 wire field in ODR3)."""
    if not highway_classes:
        return DEFAULT_ROAD_CLASS_ID, ROAD_CLASS_ID_NAMES[DEFAULT_ROAD_CLASS_ID], DEFAULT_ROAD_CLASS_LANES_TWOWAY
    counts = defaultdict(int)
    for cls in highway_classes:
        counts[cls] += 1
    majority_cls = max(counts, key=counts.get)
    road_class_id = ROAD_CLASS_ID.get(majority_cls, DEFAULT_ROAD_CLASS_ID)
    road_class_name = ROAD_CLASS_ID_NAMES[road_class_id]
    tagged = [int(v) for v in osm_lanes if int(v) > 0] if osm_lanes is not None else []
    if tagged:
        lane_counts = defaultdict(int)
        for v in tagged:
            lane_counts[v] += 1
        lanes = max(lane_counts, key=lane_counts.get)
    else:
        lanes = ROAD_CLASS_LANES_TWOWAY.get(majority_cls, DEFAULT_ROAD_CLASS_LANES_TWOWAY)
        oneway_majority = False
        if is_oneway is not None and len(is_oneway):
            oneway_majority = (sum(1 for v in is_oneway if v) / len(is_oneway)) > 0.5
        if oneway_majority and lanes >= 2:
            lanes = max(1, -(-lanes // 2))   # ceil-div by 2, floor 1
    lanes = max(1, min(8, lanes))
    return road_class_id, road_class_name, lanes

# Raw-DEM despike threshold (2026-07-29, found on the first full-Oahu rebake
# with the no-smoothing grade_smooth_elevation() below: single Terrarium
# pixels decode to garbage - e.g. a raw (0,0,0) RGB triple decodes to
# -32768 m via terrain_common.terrarium_to_elevation()'s formula, with no
# sentinel/NoData guard - and the old median-filter terrain smoothing used
# to absorb these incidentally. Removing that smoothing (by design - it was
# also flattening real climbs) left nothing to catch them, so a single void
# pixel under a bridge span was picked up by fill_structure_gaps() as one
# abutment's ground truth, producing "bridges" graded at 1000s of percent.
# 60 m is far beyond any physically real single-station (5 m) elevation
# jump on Oahu, including its steepest sea cliffs, PROVIDED the terrain
# keeps going in that direction - see despike_ground()'s "bounces back"
# test below for how it tells a real cliff apart from a corrupt pixel.
DEM_SPIKE_THRESHOLD_M = 60.0

# Absolute plausibility floor/ceiling for raw DEM samples (2026-07-29,
# found alongside the despike bug above on the same rebake): not every
# Terrarium decode failure is an isolated single-pixel spike that bounces
# back - seg1941 (Nimitz Highway) had ~90 CONSECUTIVE stations, including
# ones fill_structure_gaps() trusted as solid-ground anchors (not tagged
# bridge/tunnel), all smoothly reading -1098 to -1117 m. That's a corrupt
# or mismatched Terrarium tile over that stretch, not a spike despike_
# ground() could ever catch (nothing to "bounce back" to - the whole
# region agrees with itself). This is a backstop, not a real fix: it
# clips the impossible value to the nearest plausible bound so nothing
# catastrophic (falling through the world) reaches the game, but the
# clipped stations are still WRONG, just boundedly wrong - the
# STRUCTURE_GRADE_WARN sanity check downstream is what actually flags a
# clipped run for a human to go look at the source tile.
#
# Moved to a per-area dict in test_areas.py (2026-08-22, PLAN-RELIABILITY.md
# Phase 3 item 9) - this used to be one constant shared by every area,
# which would have silently clipped real elevation in any future
# mountainous area outside Oahu's range. Imported here (not redefined) so
# every existing caller of clamp_plausible_elevation()'s default args below
# keeps working unchanged.


def road_class_grade_arrays(highway_classes):
    """Per-station (max_grade, max_grade_delta) arrays from each station's
    highway= tag string, via ROAD_CLASS_GRADE/DEFAULT_ROAD_CLASS_GRADE."""
    n = len(highway_classes)
    mg = np.empty(n)
    md = np.empty(n)
    for i, cls in enumerate(highway_classes):
        mg[i], md[i] = ROAD_CLASS_GRADE.get(cls, DEFAULT_ROAD_CLASS_GRADE)
    return mg, md


def gaussian1d(arr, sigma, pin_ends=False):
    """pin_ends: odd-reflect padding - the end values stay put and a straight
    run stays straight (edge padding pulls an end ~0.45 sigma inward)."""
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    pad = (np.pad(arr, r, mode="reflect", reflect_type="odd") if pin_ends and len(arr) > 1
           else np.pad(arr, r, mode="edge"))
    return np.convolve(pad, k, mode="valid")


def median1d(arr, win):
    r = win // 2
    padded = np.pad(arr, r, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, win), axis=1)


def resample_stations(east, north, spacing=STATION_SPACING):
    """Resample an east/north polyline (m) to even arc-length spacing, then
    Gaussian-smooth laterally (breaks polyline corners, preserves hairpins).
    Returns (s, e, n): station distances (m) + smoothed east/north (m).

    The last station sits ON the polyline's end (final step 0.5-1.5 x
    spacing) and smoothing pins both ends (2026-10-08: arange dropped up to
    one spacing and edge-padded smoothing pulled each end ~2.2 m in, so
    every road stopped 2-7 m short of its OSM end and 19 closed Summit
    loops shipped a 3-9 m unpaved seam, e.g. (7733, 5442))."""
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
    total_len = d[-1]
    s = np.arange(0, total_len, spacing)
    if len(s) > 1 and total_len - s[-1] < 0.5 * spacing:
        s = s[:-1]
    s = np.append(s, total_len)
    e = np.interp(s, d, east)
    n = np.interp(s, d, north)
    # ponytail: a closed loop's seam is pinned, not wrapped - both ends meet
    # at the shared node, with a slight heading kink there
    e = gaussian1d(e, 1.2, pin_ends=True)
    n = gaussian1d(n, 1.2, pin_ends=True)
    return s, e, n


def clamp_plausible_elevation(ground, lo=ELEV_PLAUSIBLE_MIN_M, hi=ELEV_PLAUSIBLE_MAX_M):
    """Clip raw DEM samples to [lo, hi] - a blunt backstop against corrupt
    Terrarium tiles that read consistently wrong over many consecutive
    stations (see ELEV_PLAUSIBLE_MIN_M/MAX_M), which despike_ground() can't
    catch because there's no isolated spike to detect. Run this BEFORE
    despike_ground() and fill_structure_gaps() so a bad "solid ground"
    anchor never gets trusted for interpolation in the first place."""
    return np.clip(np.asarray(ground, dtype=float), lo, hi)


def despike_ground(ground, threshold_m=DEM_SPIKE_THRESHOLD_M):
    """Repair isolated single/double-station DEM garbage before it ever
    reaches grade_smooth_elevation() (2026-07-29 - see DEM_SPIKE_THRESHOLD_M
    for why this exists). This is deliberately NOT terrain smoothing: it
    only touches a station that (a) jumps more than `threshold_m` from its
    left neighbor, (b) jumps more than `threshold_m` back out to its right
    neighbor, AND (c) deviates more than `threshold_m` from the straight
    line between those two neighbors - i.e. an isolated notch that bounces
    back, not a real slope that keeps going. A genuine cliff face keeps
    descending station after station (jump_in large, jump_out small) and
    never trips this; only a spike that both drops and immediately recovers
    does. Runs up to 2 passes so two adjacent bad stations (a spike that
    straddles a station boundary) still get caught on the second pass once
    the first bad one's replaced."""
    ground = np.asarray(ground, dtype=float).copy()
    n = len(ground)
    if n < 3:
        return ground
    for _ in range(2):
        lo, hi = ground[:-2], ground[2:]
        mid = ground[1:-1]
        jump_in = np.abs(mid - lo)
        jump_out = np.abs(mid - hi)
        local_dev = np.abs(mid - 0.5 * (lo + hi))
        bad = np.zeros(n, dtype=bool)
        bad[1:-1] = ((jump_in > threshold_m) & (jump_out > threshold_m)
                     & (local_dev > threshold_m))
        if not bad.any():
            break
        idx = np.arange(n)
        good = ~bad
        ground[bad] = np.interp(idx[bad], idx[good], ground[good])
    return ground


# Post-repair despike (2026-08-27, Taylor's Lake Hughes screenshot: a road
# baked as a near-vertical "wall" - a shiny asphalt stripe climbing straight
# up a hillside near the town of Lake Hughes, with tall carve-wall shoulders
# either side). Traced to MIN_BRIDGE_CLEARANCE_M's floor (build_road_
# stations.py) being applied to a short (2-3 station, 10-15 m) OSM bridge tag
# with NO easing ramp into/out of the lift: `h = np.where(is_bridge_only_s,
# np.maximum(h, raw_ground + MIN_BRIDGE_CLEARANCE_M), h)` runs AFTER grade_
# smooth_elevation() and is never re-clamped afterward, so a tiny bridge tag
# (a culvert/creek crossing, not a real elevated span) jumps the road
# straight up to raw_ground+4m for its tagged stations and straight back
# down at the far edge - both edges bypassing every ROAD_CLASS_GRADE ceiling
# entirely. A full-network grade scan found the exact same bounce-back
# signature (up sharply, hold 1-3 stations, down sharply) recurring across
# dozens of unrelated short bridge tags (De Wolfe Road, Vasquez Canyon Road,
# Sierra Highway, Placerita Canyon Road, ...) - a systemic gap in the
# clearance floor, not a one-off DEM artifact, so the fix is a final,
# generic despike pass over the FULLY assembled per-station profile (after
# fill_structure_gaps/MIN_BRIDGE_CLEARANCE_M have both already run), not a
# targeted patch to any one upstream mechanism. Deliberately grade-based
# (not despike_ground's fixed-metre DEM threshold - these jumps are only
# ~4-6 m, far under DEM_SPIKE_THRESHOLD_M) and narrow-run-only, so a real,
# sustained steep mountain climb (which keeps climbing rather than bouncing
# back) is never touched - see the bracket checks in despike_road_profile()
# below.
ROAD_SPIKE_GRADE = 0.20            # per-edge |grade| beyond which an edge is
                                    # "bad" - real roads (even ROAD_CLASS_
                                    # GRADE's steepest, track's 18%) rarely
                                    # exceed this even briefly; generous
                                    # headroom above that ceiling so this
                                    # only fires on genuine outliers, never a
                                    # legitimate steep road
ROAD_SPIKE_MAX_RUN_STATIONS = 6    # widest interior-station run this repairs
                                    # as a spike (~30 m at 5 m spacing) - a
                                    # full-network scan of every >20% edge
                                    # (2026-08-27) found genuine short-
                                    # culvert-scale phantom bridges (Lake
                                    # Hughes Rd: 3 stations, Sierra Highway:
                                    # 4 stations, ...) bracketed within this
                                    # width, and separately found real
                                    # highway-scale bridge decks (e.g. West
                                    # Avenue H: 20 stations, ~100 m) that
                                    # should NOT be flattened - this sits
                                    # comfortably above the former and well
                                    # below the latter, so wider runs are
                                    # left untouched as legitimate structures
ROAD_SPIKE_EASE_SIGMA = 1.5        # gaussian1d sigma (stations) for the
                                    # light re-smoothing pass around each
                                    # repair's two new joins


def despike_road_profile(elev_m, s_m, spike_grade=ROAD_SPIKE_GRADE,
                          max_run_stations=ROAD_SPIKE_MAX_RUN_STATIONS,
                          ease_sigma=ROAD_SPIKE_EASE_SIGMA):
    """Repairs isolated grade-spike outliers in a FINAL, fully-assembled
    per-station elevation profile - i.e. after every other pipeline step
    (DEM sampling, despike_ground, grade_smooth_elevation,
    fill_structure_gaps, MIN_BRIDGE_CLEARANCE_M) has already run. See the
    ROAD_SPIKE_GRADE comment block above for why this exists and why it's
    grade-based rather than despike_ground's fixed-metre DEM threshold.

    A run of 1..`max_run_stations` interior stations, bracketed by a station
    `left` and a station `right = left + run_len + 1`, is repaired if ALL of:
      - the edge INTO the run (left -> left+1) exceeds `spike_grade`
      - the edge OUT of the run (right-1 -> right) exceeds `spike_grade`
        This is the "bounces back" test itself: a spike that shoots up (or
        down) on entry and recovers on exit trips both; a run that's merely
        the start of a longer, still-climbing real profile only trips the
        entry edge, at any run_len, so it's never matched (there's no
        `right` at which the exit edge is ALSO bad but the run is still
        inside max_run_stations, unless the real climb happens to end in
        exactly that many stations, which the next check guards against).
      - both edges immediately OUTSIDE the bracket (left-1 -> left and
        right -> right+1) are within `spike_grade` - i.e. `left` and
        `right` themselves sit on ordinary, plausible road grade, so
        they're trustworthy interpolation anchors rather than themselves
        part of a wider disturbance.
    The shortest matching run_len at each position wins (checked in
    increasing order), so a genuine 1-station notch is never mistaken for a
    wider one that happens to also bracket it.

    Repair: every interior station strictly between `left` and `right` is
    replaced with a straight line between them, then a small window
    straddling each new join is lightly gaussian-smoothed (reusing
    gaussian1d, the same primitive resample_stations()/build_road_chunks()
    already use elsewhere in this module) so the corner reads as a curve
    instead of a kink. Only that local window is touched - everything
    outside it, including untouched genuine bridges/climbs elsewhere in the
    profile, is left exactly as-is.

    Returns (elev_fixed, repairs): a NEW array (input is not mutated) and a
    list of {"station_idx", "before_m", "after_m"} dicts, one per station
    actually changed by the straight-line repair (not the cosmetic ease
    pass), in repair order - for the caller (build_road_stations.py) to log
    per road name, so every repair stays auditable."""
    elev = np.asarray(elev_m, dtype=float).copy()
    s = np.asarray(s_m, dtype=float)
    n = len(elev)
    repairs = []
    if n < max_run_stations + 3:
        return elev, repairs
    for _pass in range(2):   # 2 passes: repairing one run can newly expose
                              # a second spike right next to it (its former
                              # bracket edge sat right at the old run's edge)
        ds = np.diff(s)
        ds = np.where(ds <= 0, 1e-6, ds)
        # grade[i] is the |grade| of the edge connecting station i and i+1
        # (length n-1). "entry edge" of a candidate run bracketed at [left,
        # right] is grade[left]; "exit edge" is grade[right-1]; the edges
        # OUTSIDE the bracket are grade[left-1] and grade[right].
        grade = np.abs(np.diff(elev)) / ds
        any_touched = False
        left = 1   # smallest index with a defined outside-left edge
        while left < n - 2:
            entry_grade = grade[left]
            if entry_grade <= spike_grade:
                left += 1
                continue   # no spike starts here - entry edge isn't bad
            outside_left_grade = grade[left - 1]
            if outside_left_grade > spike_grade:
                # left itself sits on a bad edge - not a trustworthy anchor;
                # the real disturbance may start later, re-evaluate there.
                left += 1
                continue
            matched = False
            for run_len in range(1, max_run_stations + 1):
                right = left + run_len + 1
                if right >= n - 1:
                    break   # no outside-right edge to confirm this width
                exit_grade = grade[right - 1]
                if exit_grade <= spike_grade:
                    continue   # exit edge isn't bad yet - try a wider run
                outside_right_grade = grade[right]
                if outside_right_grade > spike_grade:
                    continue   # right isn't a trustworthy anchor at this
                                # width either - try the next width out
                interior = np.arange(left + 1, right)
                before_vals = elev[interior].copy()
                elev[interior] = np.interp(s[interior], [s[left], s[right]],
                                            [elev[left], elev[right]])
                for ii, bv in zip(interior.tolist(), before_vals.tolist()):
                    repairs.append({"station_idx": ii, "before_m": float(bv),
                                     "after_m": float(elev[ii])})
                w0 = max(0, left - 2)
                w1 = min(n, right + 3)
                elev[w0:w1] = gaussian1d(elev[w0:w1], ease_sigma)
                any_touched = True
                matched = True
                left = right + 1   # skip past the just-repaired run
                break
            if not matched:
                left += 1
        if not any_touched:
            break
    return elev, repairs


def grade_smooth_elevation(ground, spacing=STATION_SPACING, max_grade=MAX_GRADE,
                            max_grade_delta=None, pinned_edge_grades=None):
    """Grade-CONFORMING envelope over raw ground - never smooths the terrain
    signal itself (no median/Gaussian filtering of `ground`; a road rides
    the real ground or deviates from it via cut/bridge/tunnel, it never
    fakes the ground into something gentler than it is).

    `pinned_edge_grades` (2026-08-24, Taylor: "there cant be two sections of
    road on the same xyz axis with different slopes... after departing the
    road width they can SLOWLY start changing gradient, not a super quick
    transition"): optional {edge_index: grade} - after every relaxation
    iteration of pass 2 below, these edges are hard-set to the given grade
    instead of being left free. Since pass 2 already propagates a rate-
    limited (max_grade_delta per station) change outward from any edge that
    moves, re-asserting a pin after each relaxation makes the SAME mechanism
    that eases a bridge approach into a curve also ease a pinned junction
    slope back to the road's natural profile over a few stations, instead of
    a kink - no separate taper logic needed. See
    reconcile_junction_elevations() for where the pinned grade comes from.

    Two passes:

    1. A forward/backward min-envelope clamp (4 iterations to converge):
       the unique maximal profile that never exceeds `ground` and never
       exceeds `max_grade` between adjacent stations. Where ground's own
       slope is already gentle enough the result equals ground exactly
       (the road just rides the terrain); where a climb/descent is too
       steep it cuts below the peak; where a dip is too narrow/steep to
       reach the bottom within max_grade it stays above it - the bridge
       shape falls out of the same clamp, no separate case needed.

    2. An optional grade-RATE limiter on the resulting per-edge grade
       signal (also 4 iterations), so grade doesn't change from one
       station edge to the next by more than `max_grade_delta` - a
       vertical-curve ease on the ROAD's own profile (not terrain
       smoothing) so a transition (bridge approach, top of a cut) reads as
       a curve instead of a kink. Both ends of the envelope profile stay
       pinned exactly: any endpoint drift from re-integrating the eased
       grade is corrected with a single linear ramp across the whole span,
       which shifts every edge's grade by the same constant - this can
       occasionally push an already-near-ceiling edge slightly past
       `max_grade` (confirmed 2026-07-28 by a synthetic-DEM test: a tight
       motorway ceiling plus a sharp recovery ramp overshot by ~0.3pp), so
       a final re-run of pass 1's envelope clamp on the eased result is the
       hard backstop - `max_grade` is a genuine ceiling, `max_grade_delta`
       is a best-effort ease that yields to it on the rare edge it can't.

    `max_grade`/`max_grade_delta` are each either a scalar or a per-station
    array (e.g. from road_class_grade_arrays()); a per-EDGE threshold uses
    the tighter (min) of its two endpoint stations, so a boundary between
    two road classes always honors the stricter one. `max_grade_delta` of
    None skips pass 2 entirely (no rate limiting)."""
    ground = np.asarray(ground, dtype=float)
    n = len(ground)
    mg = np.broadcast_to(np.asarray(max_grade, dtype=float), (n,))
    edge_mg = np.minimum(mg[:-1], mg[1:])   # n-1 per-edge thresholds

    def envelope_clamp(profile):
        profile = profile.copy()
        for _ in range(4):  # lower peaks in both directions until converged
            for i in range(1, n):
                profile[i] = min(profile[i], profile[i - 1] + edge_mg[i - 1] * spacing)
            for i in range(n - 2, -1, -1):
                profile[i] = min(profile[i], profile[i + 1] + edge_mg[i] * spacing)
        return profile

    h = envelope_clamp(ground)

    if max_grade_delta is not None and n >= 3:
        md = np.broadcast_to(np.asarray(max_grade_delta, dtype=float), (n,))
        edge_md = np.minimum(md[:-1], md[1:])            # n-1, matches g below
        rate_md = np.minimum(edge_md[:-1], edge_md[1:])  # n-2 edge-to-edge caps
        g = np.diff(h) / spacing
        pin_idx, pin_val = None, None
        if pinned_edge_grades:
            pin_idx = np.array([k for k in pinned_edge_grades if 0 <= k < len(g)], dtype=np.int64)
            pin_val = np.array([pinned_edge_grades[k] for k in pin_idx], dtype=np.float64)
            if len(pin_idx):
                g[pin_idx] = pin_val
        for _ in range(4):
            for i in range(1, len(g)):
                g[i] = np.clip(g[i], g[i - 1] - rate_md[i - 1], g[i - 1] + rate_md[i - 1])
            for i in range(len(g) - 2, -1, -1):
                g[i] = np.clip(g[i], g[i + 1] - rate_md[i], g[i + 1] + rate_md[i])
            if pin_idx is not None and len(pin_idx):
                g[pin_idx] = pin_val
        h2 = np.empty(n)
        h2[0] = h[0]
        h2[1:] = h[0] + np.cumsum(g * spacing)
        drift = h[-1] - h2[-1]
        h2 += drift * (np.arange(n) / (n - 1))
        h = envelope_clamp(h2)   # hard backstop, see docstring

    return h


# Ground-following profile (2026-09-28, Taylor in Summit County: "roads were
# not continuous with random dropoffs and super wavy"). grade_smooth_
# elevation()'s one-sided min-envelope assumes a real road never climbs
# faster than its ROAD_CLASS_GRADE cap - true in Palmdale, false here: I-70
# over Vail Pass climbs ~7% against a 6% motorway cap, steep residential
# streets beat 12%, and the envelope falls a little further below the ground
# every station of a sustained climb. Measured on the shipped
# road_stations.json: 7.8% of non-structure stations >5 m below the DEM,
# I-70 seg001 15-45 m under it for km, Buffehr Creek 130 m - the terrain
# carve then dug trenches to match, and bridge spans (exempt from the clamp,
# floored at DEM+4 m) stood 14-31 m proud of the sunken approaches: the
# dropoffs. Simulated over the whole network (one-off sim, see status.md
# §7.47 for the shipped numbers) this
# profile takes >5 m-below to 0.3%, steps >1.5 m from 170 to 6, and halves
# the p99 grade-change (waviness).
FOLLOW_GROUND_SIGMA_STATIONS = 4     # 20 m low-pass on the raw DEM profile
FOLLOW_TREND_SIGMA_STATIONS = 12     # 60 m window for the "sustained slope"
FOLLOW_TREND_SLACK = 1.15            # cap relaxes to 115% of the sustained slope
FOLLOW_ABS_MAX_GRADE = 0.22          # never steeper than this (steepest real
                                     # paved streets; beyond it the DEM is
                                     # sampling a sidehill, not the road)
FOLLOW_CURVE_SIGMA_STATIONS = 3      # 15 m vertical-curve ease on the result


def follow_ground_profile(ground, max_grade, spacing=STATION_SPACING):
    """Road elevation profile that TRACKS the ground rather than hanging
    below it (see FOLLOW_* above). Returns (h, relaxed_max_grade): the
    per-station cap is max(class cap, 1.15 x the ground's 60 m-window
    slope), clipped to FOLLOW_ABS_MAX_GRADE, so the grade clamp only trims
    short bumps, never a sustained climb. The clamp is two-sided (mean of
    the cut envelope and the fill envelope) so trimmed bumps split the
    difference instead of always cutting below. Callers re-running
    grade_smooth_elevation() later (junction reconcile) should pass the
    returned relaxed cap, or the one-sided clamp sinks the road again."""
    ground = np.asarray(ground, dtype=float)
    mg = np.broadcast_to(np.asarray(max_grade, dtype=float), ground.shape)
    if len(ground) < 3:
        return ground.copy(), np.array(mg, dtype=float)
    trend = np.abs(np.gradient(gaussian1d(ground, FOLLOW_TREND_SIGMA_STATIONS))) / spacing
    mg = np.minimum(np.maximum(mg, trend * FOLLOW_TREND_SLACK), FOLLOW_ABS_MAX_GRADE)
    g = gaussian1d(ground, FOLLOW_GROUND_SIGMA_STATIONS)
    cut = grade_smooth_elevation(g, spacing, mg)
    fill = -grade_smooth_elevation(-g, spacing, mg)
    return gaussian1d(0.5 * (cut + fill), FOLLOW_CURVE_SIGMA_STATIONS), mg


BANK_SMOOTH_SIGMA_M = 5.0
# smoothing sigma widened 3.0 -> 5.0 m (2026-08-23, Taylor: wanted a smoother
# road feel) - a wider window damps station-to-station curvature noise into a
# gentler bank ramp in/out of curves instead of tracking every small wiggle.


def bank_slopes(e, n, spacing=STATION_SPACING, max_bank_rad=MAX_BANK_RAD,
                bank_gain=BANK_GAIN, bank_scale=None):
    """Per-station superelevation of one ribbon run: pavement height at
    lateral offset `off` m (positive = right of travel) is centerline +
    slope * off - a plane tilted about the centerline, rising on the curve's
    outside. Returns (slope, rx, ry), (rx, ry) the horizontal right-of-travel
    unit normal in the UE frame (X=east, Y=south). build_road_chunks builds
    the ribbon from it and build_terrain_mesh_tiles carves the terrain to the
    same cross-section, so both must see the same run arrays.

    2026-10-02: was an outer-edge-only raise (inner half flat), whose kink the
    flat-at-centerline terrain carve couldn't follow - raised outer edges
    floated up to ~1 m over the shoulder (46% of them, far_gasquet drive)."""
    tx = np.gradient(e); ty = np.gradient(-n)
    tl = np.hypot(tx, ty)
    ok = tl > 1e-6
    if not ok.any():
        z = np.zeros(len(e)); return z, z, z
    # junction-pad snapping can stack stations, so a zero central difference
    # takes its neighbours' direction (was NaN -> NaN carve -> terrain holes)
    k = np.arange(len(e))
    tx, ty = np.interp(k, k[ok], tx[ok] / tl[ok]), np.interp(k, k[ok], ty[ok] / tl[ok])
    tl = np.hypot(tx, ty); tx /= tl; ty /= tl
    theta = np.unwrap(np.arctan2(ty, tx))
    curv = gaussian1d(np.gradient(theta) / spacing, BANK_SMOOTH_SIGMA_M)   # 1/m, signed
    # smoothed SIGNED, so an S-bend's bank runs through flat - smoothing the
    # magnitude and re-applying sign(curv) flipped the whole plane in one
    # station (each edge jumped ~0.5 m at every inflection)
    bank = np.clip(-bank_gain * curv, -max_bank_rad, max_bank_rad)
    slope = np.tan(gaussian1d(bank, BANK_SMOOTH_SIGMA_M))
    if bank_scale is not None:
        bs = np.asarray(bank_scale, dtype=np.float64)
        if bs.ndim == 2:
            # (scale, dz/deast, dz/dnorth): the junction-pad fade also twists
            # the cross-section onto the pad's own slope across the road (a
            # level approach met a tilted pad high on one side, low on the
            # other); right of travel in east/north is (-ty, -tx)
            slope = slope * bs[:, 0] - bs[:, 1] * ty - bs[:, 2] * tx
        else:
            slope = slope * bs
    return slope, -ty, tx


def build_road_chunks(s, e, n, z, name_prefix="road", spacing=STATION_SPACING,
                       chunk_stations=CHUNK_STATIONS,
                       road_half_width=ROAD_HALF_WIDTH, skirt_extra=SKIRT_EXTRA,
                       skirt_drop=SKIRT_DROP, max_bank_rad=MAX_BANK_RAD,
                       bank_gain=BANK_GAIN, is_oneway=None, highway_classes=None,
                       osm_lanes=None, half_width_s=None, junction_suppress=None,
                       is_unpaved=None, bank_scale=None):
    """Build banked, skirted road mesh chunks from station arrays.

    `s` is the per-station arc-length distance (m, from resample_stations).
    `e`, `n`, `z` are smoothed per-station east/north/elevation in meters
    (local equirect frame; `z` already includes ROAD_LIFT). `is_oneway`
    (optional, per-station bool array) picks the chunk's "oneway" output
    flag by majority vote across its stations - a chunk boundary essentially
    never lands mid-carriageway (chains only break at real OSM junctions),
    so this is just a tie-breaker, not a meaningfully lossy reduction.
    `highway_classes` (optional, per-station list of OSM highway= strings,
    e.g. road_stations.json's "highway_class") feeds the same majority-vote
    treatment into road_class_and_lanes() for each chunk's road_class_id/
    road_class/lane_count fields (Phase 2a road-visual overhaul, 2026-09-03).

    UV convention (Phase 2a, 2026-09-03 - was: u=0..1 across the WHOLE
    skirt+pavement profile, v=s/20 arbitrary-scaled station distance):
        u = lateral offset normalized so u=0 is the LEFT PAVEMENT edge and
            u=1 is the RIGHT PAVEMENT edge (off=+-road_half_width) - the
            material can locate lane lines directly from u. The wrap
            (lip/underside) columns clamp u into [0,1] so they render as
            plain asphalt (2026-09-28 - the old skirt columns extrapolated
            outside [0,1] to pick up the material's gravel shoulder).
        v = station arc-length distance IN METERS, continuous along the
            centerline and NOT rescaled by road width - a chunk's `s` values
            already carry the segment's true along-road distance, so tiling
            asphalt never stretches/compresses as the profile's road_half_
            width changes between classes. (The material divides by its own
            world-space tile size; baking a fixed /20 scale in here would
            have permanently coupled the two.)
    A second UV channel ("uv1") is also emitted per vertex: uv1.x = that
    STATION's road HALF-width in meters (so u's normalization is
    recoverable: full pavement width = 2 * uv1.x), uv1.y = junction-suppress
    weight, 0..1 (Arc 7.17, 2026-09-04 - repurposed from the former
    "reserved (0.0)"; still 0.0 when `junction_suppress` is not supplied, so
    old callers/data are unaffected). 1.0 means "inside a real junction's
    flush footprint" - unreal/apply_road_material.py reads this to stop
    drawing lane markings through the junction box; it never gates the base
    asphalt/gravel look, only the painted-line mask. Also drives a tiny
    deterministic per-road-class Z nudge here (JUNCTION_ZBIAS_PER_CLASS_M) so
    two roads flattened to the same target elevation at a crossing don't
    z-fight - see that constant's docstring.

    Variable width (2026-09-06, AUDIT § 7 item 6): `half_width_s` (optional
    per-station float array, meters - see half_width_for_stations()) makes
    the pavement half-width per-station instead of the constant
    road_half_width; `osm_lanes` (optional per-station ints, 0 = untagged)
    additionally feeds real lanes= tags into each chunk's lane_count field.
    The per-vertex uv1.x channel always carried the half-width, so no wire
    format change is needed. Skirt width (skirt_extra) stays constant
    beyond whichever pavement edge applies.

    `is_unpaved` (optional per-station SURFACE_* class, see
    unpaved_stations()) cuts chunks at every surface change and sets each
    chunk's "unpaved" field to that class (0 paved / 1 gravel / 2 dirt).

    `bank_scale` (optional per-station 0..1) multiplies the superelevation -
    experimental_junction_pads fades it to 0 into each junction pad. An
    (n, 3) array adds a target surface gradient, see bank_slopes.

    Returns (chunks, cx, cy, cz) where chunks matches road_mesh.json's
    {"name","vertices","uvs","uv1","triangles","oneway","unpaved","road_class_id",
    "road_class","lane_count"} schema and cx/cy/cz are the UE-frame (cm)
    station centerline arrays for the caller's own use (yaw calc, player
    start, stats).
    """
    # UE frame: X=east, Y=south(-north), Z=up, cm
    cx, cy, cz = e * 100.0, -n * 100.0, z * 100.0
    bank, rx, ry = bank_slopes(e, n, spacing, max_bank_rad, bank_gain, bank_scale)

    # cross-section: (lateral offset m, z offset m) - a closed loop around
    # the slab (see WRAP_* at the top of this module); the strip winding
    # below makes every face point outward (pavement up, lips out, underside
    # down). The pavement offset is per-station when half_width_s is given
    # (variable width, 2026-09-06). skirt_extra/skirt_drop are unused now
    # (kept in the signature for experimental_junction_pads.py's callers).
    def station_profile(i):
        hw = float(half_width_s[i]) if half_width_s is not None else road_half_width
        return hw, [(-hw, -WRAP_DEPTH_M),
                    (-(hw + WRAP_LIP_OUT_M), -WRAP_LIP_DROP_M),
                    (-hw, 0.0),
                    (hw, 0.0),
                    (hw + WRAP_LIP_OUT_M, -WRAP_LIP_DROP_M),
                    (hw, -WRAP_DEPTH_M),
                    (-hw, -WRAP_DEPTH_M)]
    n_prof = ROAD_PROFILE_COLS

    # junction_suppress (Arc 7.17): 0..1 per station from build_road_
    # stations.py's junction-flatten pass (road_mesh_common.
    # flatten_junction_elevations/compute_junction_influence). Feeds the
    # ODR3 uv1.y marking-suppression channel below AND a tiny per-class Z
    # nudge (JUNCTION_ZBIAS_PER_CLASS_M) - see that constant's docstring for
    # why coplanar flattened ribbons need a deterministic z-bias tiebreaker.
    if junction_suppress is not None:
        junction_suppress_arr = np.asarray(junction_suppress, dtype=np.float64)
        if highway_classes is not None:
            station_class_id = np.array(
                [ROAD_CLASS_ID.get(c, DEFAULT_ROAD_CLASS_ID) for c in highway_classes],
                dtype=np.float64)
        else:
            station_class_id = np.zeros(len(s))
        zbias_m = JUNCTION_ZBIAS_PER_CLASS_M * station_class_id * junction_suppress_arr
    else:
        junction_suppress_arr = np.zeros(len(s))
        zbias_m = np.zeros(len(s))

    def station_verts(i, profile):
        out = []
        zb_cm = zbias_m[i] * 100.0
        for off, dz in profile:
            lift = bank[i] * off   # superelevation plane, see bank_slopes
            out.append((cx[i] + rx[i] * off * 100.0,
                        cy[i] + ry[i] * off * 100.0,
                        cz[i] + (dz + lift) * 100.0 - zb_cm))
        return out

    # chunked mesh buffers; winding (A, A+right, A+forward) faces up in UE
    chunks = []
    n_st = len(s)
    for c0, c1 in chunk_spans(n_st, chunk_stations, is_unpaved):
        verts, tris, uvs, uv1 = [], [], [], []
        for i in range(c0, c1 + 1):
            hw, profile = station_profile(i)
            verts.extend(station_verts(i, profile))
            v = s[i]   # meters, continuous - see build_road_chunks docstring
            # u-coordinate per profile column, normalized to THIS station's
            # PAVEMENT edges (pave-L/pave-R at off=+-hw -> u=0/1). The wrap
            # columns clamp into [0,1] so the material draws plain asphalt
            # there, not its u<0 / u>1 gravel shoulder; their uv1.y = 1
            # (junction-suppress) keeps lane paint off the underside, whose
            # u sweeps 1 -> 0 back across the slab.
            uvs.extend([(min(1.0, max(0.0, off / (2.0 * hw) + 0.5)), v) for off, _dz in profile])
            js = float(junction_suppress_arr[i])
            uv1.extend([(hw, js if j in ROAD_PAVE_COLS else 1.0) for j in range(n_prof)])
        for irow in range(c1 - c0):
            b = irow * n_prof
            for j in range(n_prof - 1):
                p, q = b + j, b + j + n_prof
                tris.append((p, p + 1, q))
                tris.append((p + 1, q + 1, q))
        oneway_mask = is_oneway[c0:c1 + 1] if is_oneway is not None else None
        oneway = bool(np.mean(oneway_mask) > 0.5) if oneway_mask is not None else False
        hwy_chunk = highway_classes[c0:c1 + 1] if highway_classes is not None else None
        lanes_chunk = osm_lanes[c0:c1 + 1] if osm_lanes is not None else None
        road_class_id, road_class_name, lane_count = road_class_and_lanes(
            hwy_chunk, oneway_mask, lanes_chunk)
        chunks.append({
            "name": f"{name_prefix}_{c0:04d}",
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "uv1": [round(v, 4) for uv in uv1 for v in uv],
            "triangles": [i for t in tris for i in t],
            "oneway": oneway,
            "unpaved": int(is_unpaved[c0]) if is_unpaved is not None else SURFACE_PAVED,
            "road_class_id": road_class_id,
            "road_class": road_class_name,
            "lane_count": lane_count,
        })
    return chunks, cx, cy, cz


# --------------------------------------------------- junction unification
# Arc 7.17 (2026-09-04, Taylor's Blue River Parkway / Silverthorne
# screenshot): crossing roads baked as independent stacked ribbons at
# different heights, with lane markings running straight through the
# junction box. Root cause confirmed: reconcile_junction_elevations() above
# only reconciles a SINGLE road's own switchback/chain-break disagreement
# (its `in_scope` test requires matching seg_idx OR matching road_name) -
# two DIFFERENTLY NAMED roads crossing at a real OSM junction node never
# enter that pass at all, so nothing anywhere previously made two different
# roads' ribbons agree on elevation where they meet, and nothing suppressed
# lane markings near a crossing. build_road_stations.py detects real
# junction NODES straight from merge_way_chains()'s own OSM graph degree
# (>=3 incident ways at a shared node - the same test that function already
# uses to decide where to BREAK a chain, so a plain "two edges of the same
# road continuing through a degree-2 node" is never misflagged as a
# junction) and builds a `junctions` list this module's functions consume.
JUNCTION_BOX_MARGIN_M = 4.0     # extra radius beyond a road's pavement half-
                                  # width for the flush "box" zone - within
                                  # this radius of a junction center, a
                                  # station's elevation is fully replaced by
                                  # the junction's shared elevation (pavement
                                  # meets flush, no step) and lane markings
                                  # are fully suppressed (open junction
                                  # pavement, no centerline running through
                                  # an intersection).
JUNCTION_APPROACH_M = 20.0      # beyond the box radius, elevation/markings
                                  # blend smoothly back to each road's own
                                  # profile over this additional distance (a
                                  # smoothstep ease, not a hard edge) so
                                  # there's no visible kink/pop where the
                                  # junction's influence ends - within
                                  # Taylor's requested 15-25 m range.
JUNCTION_MAX_DISAGREEMENT_M = 10.0   # = WALL_MIN_GRADE (0.50, defined below -
                                  # can't reference it directly yet at this
                                  # point in the module) * JUNCTION_APPROACH_M
                                  # (2026-09-04, bug #3 in this same pass -
                                  # Taylor's "48 hits, all near junctions"
                                  # survey after the bridge-exclusion fix
                                  # above): build_road_stations.py's "highest
                                  # incident approach wins" doctrine assumed
                                  # any disagreement at a real junction node
                                  # means one side just hasn't grade-smoothed
                                  # down to the shared point yet (true for the
                                  # original Lake Hughes case) - but a KEPT
                                  # segment can also get cut short by MAX_
                                  # SEGMENTS/MIN_SEGMENT_STATIONS mid-descent,
                                  # leaving its own endpoint stranded well
                                  # above the real ground its neighbors
                                  # correctly agree on. Naively taking max()
                                  # over ALL incident elevations then drags
                                  # the CORRECT, mutually-agreeing roads up to
                                  # match that stranded outlier instead of the
                                  # other way around (seg043/seg234 agreed
                                  # within 1m at 2552m; a truncated seg150
                                  # sat at 2627m and won anyway, tearing a 74m
                                  # cliff into two blameless roads over just
                                  # JUNCTION_APPROACH_M). Since every incident
                                  # road meets at the SAME node (plan-view
                                  # distance ~0), any real at-grade
                                  # disagreement has to be small; reusing
                                  # WALL_MIN_GRADE (the "not a single
                                  # heightfield/profile transition any more"
                                  # ceiling classify_wall_stations/
                                  # reconcile_junction_elevations already use
                                  # elsewhere in this file) over the
                                  # JUNCTION_APPROACH_M blend distance gives
                                  # the same "steepest a real approach ramp
                                  # could plausibly be" bound here: a
                                  # participant more than this far from the
                                  # OTHERS' median is excluded from the node
                                  # entirely (neither contributes to the
                                  # target elevation nor gets flattened) -
                                  # same "leave it alone, don't manufacture
                                  # new geometry from it" treatment already
                                  # given to bridge/tunnel endpoints above.
JUNCTION_PULL_MAX_GRADE = 0.10   # flatten_graph_junctions: steepest grade a
                                  # junction pull may ADD to a road - the approach
                                  # lengthens past JUNCTION_APPROACH_M for a big pull


def flatten_graph_junctions(elev, offsets, seg_s, incident, targets, box_r):
    """Pulls each junction's roads to its target height (2026-10-08 rewrite of
    build_road_stations' inline pass). Inside box_r a station takes the target;
    past it a smoothstep blends back over JUNCTION_APPROACH_M, or longer when
    the pull is big enough that 1.5 * pull / length would add more than
    JUNCTION_PULL_MAX_GRADE. Junctions apply one after another as continuous
    blends - the old "strongest pull wins" switched targets between two close
    junctions in one station step (Summit: all 51 station steps over 40%
    grade sat in junction blends, e.g. seg377's 4.3 m jump between boxes).

    incident: node -> [(seg_i, s_node)], targets: node -> elev.
    Returns (final_elev, w): w is the plain JUNCTION_APPROACH_M weight
    (0 = in a box, 1 = untouched), min over junctions - the lane-marking
    suppression and in_junction_box masks keep their old footprint."""
    final = np.array(elev, dtype=np.float64)
    w_min = np.ones(len(final))
    for node, inc in incident.items():
        if len(inc) < 2 or node not in targets:
            continue
        target = targets[node]
        for seg_i, s_node in inc:
            lo, hi = offsets[seg_i], offsets[seg_i + 1]
            d = np.abs(np.asarray(seg_s[seg_i], dtype=np.float64) - s_node)
            pull = abs(target - float(np.interp(s_node, seg_s[seg_i], elev[lo:hi])))
            for L, out in ((JUNCTION_APPROACH_M, None),
                           (max(JUNCTION_APPROACH_M, 1.5 * pull / JUNCTION_PULL_MAX_GRADE), final)):
                t = np.clip((d - box_r) / L, 0.0, 1.0)
                w = t * t * (3.0 - 2.0 * t)
                if out is None:
                    w_min[lo:hi] = np.minimum(w_min[lo:hi], w)
                else:
                    out[lo:hi] = target * (1.0 - w) + out[lo:hi] * w
    return final, w_min


OVERPASS_SEPARATION_M = 6.5   # upper pavement over lower pavement at an OSM
                              # bridge crossing: 4.9 m (16 ft) clearance + the
                              # 1.25 m deck + a little margin. The bare-earth DEM
                              # has no overpasses, so short spans sat 0.2-4 m
                              # above the road under them (Summit, 2026-10-08)
OVERPASS_RAMP_GRADE = 0.06    # steepest grade the lift's approach ramps add
OVERPASS_FLAT_M = 12.0        # full lift at least this far either side of the crossing
OVERPASS_JOIN_M = 40.0        # a same-level crossing this close to a bridge's deck zone joins it
OVERPASS_SMOOTH_STATIONS = 5  # moving average over the lift (25 m at 5 m stations)


def lift_overpasses(segs, passes=3):
    """Raises the upper road at every mid-segment centerline crossing whose
    upper road is an OSM bridge there, until it sits OVERPASS_SEPARATION_M
    over the lower road. Upper = higher OSM layer, else the bridge one. A
    non-bridge upper is skipped: terrain conforms to it, so lifting it would
    bury the road below in fill. The road is raised to a floor that is the
    deck height over the bridge run (and OVERPASS_FLAT_M around the crossing)
    and falls OVERPASS_RAMP_GRADE per metre of road-graph distance beyond -
    through junction nodes too - so the DEM's sag under the span fills in
    instead of riding along; the lift is then lightly smoothed. Repeats so a
    lifted road that is itself crossed over gets its upper road lifted too.

    segs: [{"e","n","s","h","is_struct_s","is_tunnel_s","layer_s"}].
    Returns (h list, over_road list - each crossing's flat deck zone,
    lifted list - stations the lift moved, report rows
    (x, y, up_seg, lo_seg, sep_before_m, sep_after_m), n_skipped)."""
    from shapely.geometry import LineString
    from shapely.strtree import STRtree

    lines = {i: LineString(np.c_[g["e"], g["n"]]) for i, g in enumerate(segs) if len(g["e"]) > 1}
    ids = list(lines)
    tree = STRtree([lines[i] for i in ids])

    def win(i, k):   # +-1 station: a crossing at an abutment reads the bridge's own tags
        return range(max(k - 1, 0), min(k + 2, len(segs[i]["s"])))

    def is_br(i, k):
        return any(segs[i]["is_struct_s"][j] and not segs[i]["is_tunnel_s"][j] for j in win(i, k))

    def layer(i, k):
        return max(segs[i]["layer_s"][j] for j in win(i, k))

    def near(i, p):   # by distance - projected length drifts from s over km
        return int(np.argmin(np.hypot(segs[i]["e"] - p.x, segs[i]["n"] - p.y)))

    crossings, level, n_skipped = [], [], 0   # [x, y, up, s_up, flat_lo, flat_hi, lo, s_lo]
    for a in ids:
        for b in (ids[j] for j in tree.query(lines[a])):
            if b <= a:
                continue
            x = lines[a].intersection(lines[b])
            for p in getattr(x, "geoms", [x]):
                if p.geom_type != "Point":
                    continue
                da, db = lines[a].project(p), lines[b].project(p)
                if min(da, lines[a].length - da, db, lines[b].length - db) < 3.0:
                    continue   # the roads meet at a shared node: a junction, not a stack
                ka, kb = near(a, p), near(b, p)
                da, db = segs[a]["s"][ka], segs[b]["s"][kb]
                la, lb = layer(a, ka), layer(b, kb)
                if la != lb:
                    up = a if la > lb else b
                elif is_br(a, ka) != is_br(b, kb):
                    up = a if is_br(a, ka) else b
                else:
                    level.append((a, da, b, db))   # at grade - unless next to a bridge, below
                    continue
                (ku, su), (lo, sl) = ((ka, da), (b, db)) if up == a else ((kb, db), (a, da))
                if not is_br(up, ku):
                    n_skipped += 1
                    level.append((a, da, b, db))
                    continue
                st = segs[up]["is_struct_s"]
                ku = next(j for j in win(up, ku) if st[j])
                r0 = r1 = ku
                while r0 > 0 and st[r0 - 1]:
                    r0 -= 1
                while r1 < len(st) - 1 and st[r1 + 1]:
                    r1 += 1
                s_up = segs[up]["s"]
                crossings.append([p.x, p.y, up, su, min(s_up[r0], su - OVERPASS_FLAT_M),
                                  max(s_up[r1], su + OVERPASS_FLAT_M), lo, sl])

    # the OSM bridge way often stops short: the road below crosses the upper
    # road's centerline again 10-30 m past it (Summit Post Blvd, I-70 over
    # US 6) and was left at ~5 m with terrain fill. Such a same-level
    # crossing near a bridge crossing joins its flat deck zone - unless the
    # two roads also share a node: that's a ramp merging, not a road below
    # (Summit seg264 at Shrine Pass, held down and then yanked 1.9 m by the
    # junction flatten)
    nodes = [{nd for nd, _ in g.get("node_s", ())} for g in segs]
    extra_under = []
    for c in crossings:
        for a, sa, b, sb in level:
            for r, sr, o in ((a, sa, b), (b, sb, a)):
                if r == c[2] and c[4] - OVERPASS_JOIN_M <= sr <= c[5] + OVERPASS_JOIN_M                         and not nodes[r] & nodes[o]:
                    c[4], c[5] = min(c[4], sr - OVERPASS_FLAT_M), max(c[5], sr + OVERPASS_FLAT_M)
                    extra_under.append((r, o))

    # a ramp that reaches a junction node continues into every road at that
    # node - else the junction flatten pulls the lifted road back down to the
    # unlifted ones in a step (Summit I-70 seg008's on-ramp gore: 2.5 m lift
    # pulled to 0.4 m over 15 m, 28%)
    by_node = defaultdict(list)
    for i, g in enumerate(segs):
        for node, s_node in g.get("node_s", ()):
            by_node[node].append((i, s_node))
    seg_nodes = defaultdict(list)
    for node, inc in by_node.items():
        if len(inc) > 1:
            for i, s_node in inc:
                seg_nodes[i].append((node, s_node))
    under = defaultdict(set)
    for c in crossings:
        under[c[2]].add(c[6])
    for r, o in extra_under:
        under[r].add(o)

    h0 = [np.array(g["h"], dtype=np.float64) for g in segs]
    h = [v.copy() for v in h0]
    box = np.ones(OVERPASS_SMOOTH_STATIONS) / OVERPASS_SMOOTH_STATIONS
    for _ in range(passes):
        lift = [np.zeros(len(v)) for v in h]
        for _x, _y, up, su, f0, f1, lo, sl in crossings:
            need = np.interp(sl, segs[lo]["s"], h[lo]) + OVERPASS_SEPARATION_M
            s = np.asarray(segs[up]["s"], dtype=np.float64)
            seen = {up} | under[up]   # never lift a road this bridge crosses
            stack = [(up, np.maximum(np.maximum(f0 - s, s - f1), 0.0))]
            while stack:
                j, dj = stack.pop()
                floor = need - OVERPASS_RAMP_GRADE * dj
                lift[j] = np.maximum(lift[j], floor - h0[j])
                for node, s_node in seg_nodes[j]:
                    dn = float(np.interp(s_node, segs[j]["s"], dj))
                    if need - OVERPASS_RAMP_GRADE * dn <= float(np.interp(s_node, segs[j]["s"], h0[j])):
                        continue   # the floor is under the road here: the ramp has landed
                    for k, s_k in by_node[node]:
                        if k not in seen:
                            seen.add(k)
                            stack.append((k, dn + np.abs(np.asarray(segs[k]["s"], dtype=np.float64) - s_k)))
        new_h = []
        for v0, lf in zip(h0, lift):
            lf = np.maximum(lf, 0.0)
            if lf.max() > 0.0 and len(lf) > OVERPASS_SMOOTH_STATIONS:
                # rounds off the crease where the floor lands; the end
                # stations keep the raw floor so roads still meet at a node
                pad = OVERPASS_SMOOTH_STATIONS // 2
                sm = np.convolve(np.pad(lf, pad, mode="edge"), box, mode="valid")
                sm[:pad], sm[-pad:] = lf[:pad], lf[-pad:]
                lf = sm
            new_h.append(v0 + lf)
        if all(np.allclose(x, y, atol=0.01) for x, y in zip(h, new_h)):
            break
        h = new_h

    over_road = [np.zeros(len(v), dtype=bool) for v in h]
    rows = []
    for x, y, up, su, f0, f1, lo, sl in crossings:
        s = np.asarray(segs[up]["s"])
        over_road[up] |= (s >= f0) & (s <= f1)
        sep = [np.interp(su, segs[up]["s"], hh[up]) - np.interp(sl, segs[lo]["s"], hh[lo]) for hh in (h0, h)]
        rows.append((x, y, up, lo, sep[0], sep[1]))
    lifted = [np.abs(v - v0) > 0.01 for v, v0 in zip(h, h0)]
    return h, over_road, lifted, rows, n_skipped


JUNCTION_ZBIAS_PER_CLASS_M = 0.02   # tiny deterministic per-road-class Z
                                  # nudge (2 cm/class step below the major
                                  # road), scaled by junction influence
                                  # weight - once every road touching a
                                  # junction is flattened to the SAME target
                                  # elevation their ribbons become coplanar
                                  # right at the crossing and can z-fight;
                                  # nudging the lower-priority (higher
                                  # road_class_id - see ROAD_CLASS_ID) road
                                  # very slightly below the major one, only
                                  # inside the junction's own influence zone,
                                  # resolves it deterministically without
                                  # reading as a visible step.


def road_ue_z_m(elev_m, road_lift_m, origin_height_m):
    """Ground-smoothed absolute elevation (m, DEM frame) -> UE-world Z (m,
    the old georeference actor's CARTOGRAPHIC_ORIGIN frame). One formula,
    shared by build_road_network.py (writing road_mesh.json) and
    verify_terrain_fit.py (checking the built terrain against it) so the two
    can't independently
    drift - the ~800 m elevation-frame bug (2026-07-19) was exactly this
    shift living in one place and being wrong, not two places disagreeing,
    but keeping it in one function removes the *chance* of the latter."""
    return elev_m + road_lift_m - origin_height_m


# --------------------------------------------------- elevation-conflict / wall
# Canonical parameters (2026-07-30) - consolidates what used to be two
# independent, differently-tuned conflict detectors (build_road_stations.py's
# lon/lat-based 40m/flat-5m-delta version and build_terrain_mesh_tiles.py's
# local-meter-frame 150m/grade-based version), which disagreed with each
# other by construction and left the exclusion decision unbounded - a
# flagged station was excluded from the terrain carve entirely with no cap
# on how far the road's own elevation could then diverge from the terrain,
# which is what produced floating road slabs and unintended cliff walls on
# the first full-Oahu rebake after grade_smooth_elevation() stopped
# smoothing terrain (see that function's own docstring). Canonicalized on
# the terrain-tiles version's better-reasoned parameters: 150m (matches
# TERRAIN_CARVE_OUTER_M - anything farther can't influence the same carve
# stamp anyway) and a grade-based threshold (two stations 140m apart can
# differ a lot without conflicting; two 6m apart can't - a flat delta gets
# this backwards).
CONFLICT_RADIUS_M = 150.0
CONFLICT_MAX_GRADE = 0.15        # generous vs MAX_GRADE (0.10) - only flag
                                  # gaps too steep for ANY plausible shared
                                  # road profile, not just an unusual one
CONFLICT_MIN_GAP_M = 1.0         # floor so two stations meters apart with a
                                  # sub-meter difference (ordinary local
                                  # noise) never gets flagged
#
# Lowered 3.0 -> 1.0 (2026-08-22, Taylor: Green Valley drive-test found
# spots where terrain visibly poked up through the road - never supposed
# to happen outside a tunnel). Traced to real, DIFFERENT road segments
# passing within 1-5 m of each other in XY while sitting 2-4 m apart in
# elevation (e.g. station 22826 vs station 8959: d=2.4 m, dz=+2.4 m;
# station 16322 vs station 26781: d=1.1 m, dz=-2.9 m) - carve_grid_m has
# no concept of "which road" a station belongs to, so it blended toward
# the wrong nearby road's elevation at these spots instead of excluding
# them as a conflict. At the old 3.0 m floor, dz=2.4-2.9 m at d=1-2.4 m
# sat just UNDER the dz > 0.15*d + 3.0 threshold - real, close-range,
# cross-road elevation jumps were slipping through by a hair. 1.0 m still
# tolerates ordinary sub-meter noise/same-road grade progression (already
# separately exempted for nearby same-chain stations via
# CONFLICT_SAME_CHAIN_GAP_STATIONS) while catching this class of bug;
# tune further if verify_road_fit.py/verify_terrain_fit.py still show
# outliers after this.
CONFLICT_SAME_CHAIN_GAP_STATIONS = 8   # ~40m at 5m spacing - skip evaluating
                                  # trivially-adjacent same-chain stations
                                  # (ordinary progression, already grade-
                                  # clamped); anything past this gap is a
                                  # real candidate

# Above this grade, a single terrain heightfield genuinely cannot represent
# the transition (two stations close in XY, far in Z, need a near-vertical
# cut face/retaining wall, not a steep-but-continuous slope) - build a wall.
# Below it, un-excluding the station from the normal carve stamp is
# sufficient (carve_grid_m's nearest-station blend can represent an
# arbitrarily steep but CONTINUOUS slope; it only fails at true near-
# vertical/overhanging cases). 0.50 (1:2, ~26.6 deg) is the standard civil-
# engineering rule of thumb for where unreinforced cut/fill needs a
# retaining structure - sits well above CONFLICT_MAX_GRADE (headroom for the
# "un-excluded, tolerable seam" bucket) and well below confirmed real Oahu
# switchback grades (Halekoa Drive ~173%, Satellite Tracking Station Road
# ~180-200%). Ship-a-guess, tune by drive-test, same as ROAD_CLASS_GRADE.
WALL_MIN_GRADE = 0.50

# Root-cause fix (2026-09-07, Taylor: "57,222 station-points classified
# bridge, absurd"). reconcile_junction_elevations()'s in_scope test below
# used to accept ANY cross-segment pair sharing a road_name, anywhere within
# CONFLICT_RADIUS_M (150m) of each other and under WALL_MIN_GRADE - meant to
# catch a same-road CHAIN BREAK (a way split into two segments by
# MAX_SEGMENTS/MIN_SEGMENT_STATIONS, or a genuine switchback fold-back) at
# the seam where the two pieces meet. It does NOT distinguish that narrow
# case from a divided highway: I-70 through Silverthorne/Straight Creek runs
# as two separately-graded carriageways (each benched into its own side of
# the valley) that share the OSM name "I 70" across dozens of separate way
# segments for the entire corridor. Traced via a spot-check (build_road_
# embankments.py's absurd "bridge" county total): station pairs on DIFFERENT
# I 70 segments, 140-150m apart, under 5% grade between them (a perfectly
# ordinary shallow road grade, not a real disagreement) were being pulled
# together by this pass across essentially the WHOLE length of the
# corridor - one carriageway's real, DEM-agreeing elevation kept getting
# hiked up to match the other's, by several meters, at station after
# station, because "same road_name, <150m, <50% grade" is true almost
# everywhere along a road that runs roughly parallel to itself for
# kilometers. That elevation is exactly what build_road_embankments.py
# diffs against DEM terrain, so the corrupted, artificially-raised
# carriageway measured a huge fake "drop" and got classified bridge.
# A genuine chain-break/switchback seam is a single, LOCAL event: it happens
# at (or within a handful of stations of) the END of one segment and the
# START of the next - not scattered across a segment's interior. Restricting
# cross-segment same-name matches to pairs where BOTH stations sit within
# this many stations of their OWN segment's start/end excludes the
# divided-highway false-match class almost entirely (145,742 -> 63
# qualifying I-70 pairs in the summit_county bake) while preserving the
# narrow seam case this pass actually exists for. Same-segment pairs
# (a road folding back on itself within its OWN chain) are unrestricted -
# that's the genuine switchback case and already scoped by seg_idx equality.
JUNCTION_SAME_NAME_EDGE_STATIONS = 5

# Second, larger part of the same root cause, found by the same spot-check
# (Potato Patch Drive, a single continuous switchback climb - ONE seg_idx,
# no cross-segment name matching involved at all): a mountain road climbing
# via switchbacks routinely brings two points on ITS OWN chain, hundreds of
# stations apart in station_idx (different switchback legs), within
# CONFLICT_RADIUS_M (150m) of each other in plan view while genuinely
# sitting many meters apart in real elevation, at an ordinary <50% grade
# (a real switchback has to be driveable). Nothing here restricts SAME-
# segment matches by station-index gap the way the cross-segment fix above
# does for different segments - CONFLICT_SAME_CHAIN_GAP_STATIONS only skips
# TRIVIALLY adjacent same-chain stations (ordinary progression), so a real
# switchback (station 0 vs station 73, say - clearly not adjacent) still
# qualifies and gets forcibly snapped toward whichever nearby-in-plan-view
# station happens to be highest, over and over, corrupting the road's real
# climbing profile (3,750 qualifying same-segment pairs found on Potato
# Patch Drive alone at the 150m radius - this is what produced its reported
# 87m road-vs-DEM discrepancy even AT the pavement centerline, 0m probe
# offset, not a DEM-probe-placement artifact).
#
# The function's own documented true-positive case (Lake Hughes, 2026-08-24:
# "one road's pavement visibly bursting through the other") was measured at
# 1.1-2.4m apart, not anywhere near 150m - CONFLICT_RADIUS_M=150 is tuned for
# classify_wall_stations()/carve-exclusion's own, much wider purpose (it
# matches TERRAIN_CARVE_OUTER_M, the distance a carve stamp can still reach),
# not for "are these two stations actually the same physical spot," which
# reconcile_junction_elevations alone needs answered. Using a much tighter,
# dedicated radius for THIS function's own pair search (not touching
# CONFLICT_RADIUS_M or classify_wall_stations' unrelated carve/wall
# behavior) eliminates both the Potato Patch Drive same-segment case and the
# I-70 cross-segment case (confirmed empirically: 0 qualifying pairs remain
# for either at 20m, combined with the edge restriction above) while still
# comfortably covering the documented real same-spot disagreement distances.
JUNCTION_RECONCILE_RADIUS_M = 20.0

# §7.34 fix (2026-09-08, closing §7.33's Finding 1): reconcile_junction_
# elevations()'s SAME-SEGMENT ("switchback") branch had no restriction
# analogous to JUNCTION_SAME_NAME_EDGE_STATIONS - its own docstring assumed
# "already correctly scoped by seg_idx equality" was enough, but the §7.33
# audit found 242 same-segment pairs still firing at the current 20m radius,
# none of them anywhere near the documented genuine same-spot precedent's
# distance range (Lake Hughes, 1.1-2.4m): 66% sat in the outermost 5m of the
# 20m radius, 88% touched no real graph junction, median imposed snap 4.6m,
# max 9.6m (worst: Robbers Nest Drive seg347, two points of an ordinary
# 12%-grade hairpin 19.8m apart in plan / ~80m along-road, falsely snapped
# together). Re-derived against the same captured pre-reconciliation station
# arrays the audit used (scratchpad/reconcile_pre_arrays.npz via
# pipeline/checks/capture_reconcile_pairs.py, county-wide rebuild - 274,644 stations, reproduced the
# audit's 295/242/53 split exactly) and swept a same-segment-only distance
# cap from 0.5m to 20m (scratchpad/sweep_same_seg_radius.py): ALL 242
# same-segment pairs sit at 4.67m or farther apart in plan view - there is a
# clean, total gap with NOTHING between 0m and 4.67m, comfortably straddling
# the 1.1-2.4m Lake Hughes range with margin on both sides. 3.0m sits in the
# middle of that empty gap: strictly above every documented genuine same-spot
# case, strictly below every one of today's 242 false same-segment pairs (0
# survive), so this single threshold change eliminates the entire false-pair
# population with no per-pair exception list needed. Deliberately NOT reused
# for the cross-segment same-name branch below (JUNCTION_SAME_NAME_EDGE_
# STATIONS, §7.20's own fix) - that branch already has its own, independent,
# already-verified restriction and untouched by this change.
JUNCTION_SAME_SEG_RADIUS_M = 3.0

# Separate, much tighter threshold used ONLY to catch cross-road (different
# seg_idx AND different/missing road_name) pairs (2026-08-24, Taylor's Lake
# Hughes screenshot: a torn-open canyon where "Lake Hughes Road" and an
# unrelated unnamed nearby road sat d=149m apart, dz=17.6m - UNDER
# CONFLICT_MAX_GRADE's allowance at that distance (0.15*149+1=23.4m) so
# detect_elevation_conflict_pairs never flagged it, and carve_heightmap
# happily blended toward BOTH within its 150m outer radius, flipping which
# one "wins" partway between them). CONFLICT_MAX_GRADE's generous allowance
# only makes sense for a genuine same-road switchback where SOME continuous
# grade between the two points is physically plausible - two different,
# unrelated roads have no such relationship, so any real disagreement
# between them within carve range should exclude both from blending into
# each other, not just disagreements steep enough to look wall-eligible.
CROSS_ROAD_CONFLICT_MAX_GRADE = 0.05

# Cross-segment stations this close in XY are "the same physical junction
# spot" for junction_agreement_pass()
# (2026-09-06, immediate-queue item 5). Matches diagnose_junctions.py's
# JUNCTION_RADIUS_M - the audit that measures the residual steps this pass
# exists to remove - and is deliberately tighter than road half-width +
# margin so parallel dual carriageways don't pair up.
JUNCTION_AGREE_RADIUS_M = 5.0

# Grade of the raised taper junction_agreement_pass() lays along the LOWER
# road's own stations on each side of a crossing (2026-09-06, third
# revision). The pass runs on the FINAL smoothed profiles and its output
# ships as-is, so the taper IS the approach shape: a bare one-station raise
# would be a vertical kink, and re-smoothing afterward is exactly what the
# first two revisions tried and what regressed the audit both times (see
# junction_agreement_pass()'s docstring). 0.03 sits well under every
# ROAD_CLASS_GRADE ceiling (tightest: motorway 0.06) - "after departing the
# road width they can SLOWLY start changing gradient" (Taylor, 2026-08-24) -
# and bounds the footprint: a worst-case ~1.75m raise reaches ~58m (~12
# stations) per side, a typical 0.3-0.6m raise ~10-20m.
JUNCTION_AGREE_TAPER_GRADE = 0.03

# Bias a generated wall's bottom/top edges past the real station elevation
# by this much so the wall visually embeds into terrain instead of leaving a
# hairline gap - the same "gap hidden under geometry" pattern already used
# by the road skirt (SKIRT_DROP above) and build_terrain_mesh_tiles.py's
# build_seam_skirt(), applied to a fourth case.
WALL_EMBED_MARGIN_M = 1.5

# Wall-run coherence bounds (2026-08-24, Taylor's screenshot: a stray flat
# diagonal plane slicing across open terrain after out-of-scope pairs became
# wall-eligible). build_retaining_walls.py's cluster_runs() does a plain
# Union-Find over every wall-eligible pair with no notion of "does this
# still look like one real wall" - two pairs sharing a station get merged
# TRANSITIVELY even if the two pairs point in unrelated directions, so a
# dense area (out-of-scope pairs are common - most rural roads lack an OSM
# name tag) can chain dozens of unrelated crossings into one giant run.
# build_wall_run_mesh() then forces that whole run through a SINGLE PCA axis
# + upper/lower 2-means split + ruled-surface stitch, which only makes sense
# for a genuinely path-like cluster (a real hairpin or a single crossing) -
# fed a scattered, non-path-like cluster instead, it happily interpolates
# between unrelated stations at the same projected coordinate and produces
# exactly this kind of nonsense geometry. Filtering here (the shared
# single-source-of-truth function both build_retaining_walls.py and
# build_terrain_mesh_tiles.py call) rather than in build_retaining_walls.py
# itself keeps exclude/wall_pairs consistent: a pair whose run turns out
# incoherent falls back to un-excluded (ordinary carve_grid_m blending
# papers over it, same as any other below-wall_min_grade case) instead of a
# wall that's wrong AND still excluded from carve, which would be worse
# than doing nothing.
WALL_RUN_MAX_ASPECT = 0.6   # secondary/primary PCA std-dev ratio ceiling - a
                             # real hairpin or crossing is elongated (low
                             # ratio); a blob of unrelated merged stations
                             # reads as roughly circular (ratio near 1)


def detect_elevation_conflict_pairs(east_m, north_m, elev_m, seg_idx, station_idx,
                                     radius_m=CONFLICT_RADIUS_M,
                                     max_grade=CONFLICT_MAX_GRADE,
                                     min_gap_m=CONFLICT_MIN_GAP_M,
                                     same_chain_gap_stations=CONFLICT_SAME_CHAIN_GAP_STATIONS):
    """Geometric (not tag-based) detection of station-pairs close in XY but
    too far apart in elevation to be the same piece of ground - a road's own
    switchback doubling back on itself, two different chains crossing, or a
    divided/split OSM way. Grid-bucketed (radius_m cells, 3x3 neighborhood
    per query) so this stays roughly linear across hundreds of thousands of
    stations instead of O(n^2). Returns (pairs_i, pairs_j) int arrays - the
    actual conflicting station INDEX pairs, not just a per-station exclude
    flag, since building wall geometry needs to know which two stations a
    conflict connects, not merely that one exists.

    Unlike a per-station bool, this is the single canonical implementation -
    both build_road_stations.py (folding conflicts into is_bridge-style carve
    exclusion) and build_terrain_mesh_tiles.py / classify_wall_stations below
    (deciding what to carve vs. wall) call this SAME function with the SAME
    parameters, so their answers can't independently drift the way the two
    prior separate implementations did."""
    n = len(east_m)
    east_m = np.asarray(east_m, dtype=np.float64)
    north_m = np.asarray(north_m, dtype=np.float64)
    elev_m = np.asarray(elev_m, dtype=np.float64)
    seg_idx = np.asarray(seg_idx)
    station_idx = np.asarray(station_idx)
    x_cm = east_m * 100.0
    y_cm = -north_m * 100.0
    radius_cm = radius_m * 100.0

    cx = np.floor(x_cm / radius_cm).astype(np.int64)
    cy = np.floor(y_cm / radius_cm).astype(np.int64)
    buckets = defaultdict(list)
    for i in range(n):
        buckets[(int(cx[i]), int(cy[i]))].append(i)

    pairs_i, pairs_j = [], []
    for i in range(n):
        bx, by = int(cx[i]), int(cy[i])
        neighbor_idxs = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neighbor_idxs.extend(buckets.get((bx + dx, by + dy), ()))
        for j in neighbor_idxs:
            if j <= i:
                continue
            if (seg_idx[i] == seg_idx[j]
                    and abs(int(station_idx[i]) - int(station_idx[j])) <= same_chain_gap_stations):
                continue
            d_m = math.hypot(x_cm[j] - x_cm[i], y_cm[j] - y_cm[i]) / 100.0
            if d_m <= 0.01 or d_m > radius_m:
                continue
            dz = abs(elev_m[j] - elev_m[i])
            if dz > max_grade * d_m + min_gap_m:
                pairs_i.append(i)
                pairs_j.append(j)
    return np.array(pairs_i, dtype=np.int64), np.array(pairs_j, dtype=np.int64)


def classify_wall_stations(east_m, north_m, elev_m, seg_idx, station_idx, road_name,
                            radius_m=CONFLICT_RADIUS_M,
                            max_grade=CONFLICT_MAX_GRADE,
                            min_gap_m=CONFLICT_MIN_GAP_M,
                            same_chain_gap_stations=CONFLICT_SAME_CHAIN_GAP_STATIONS,
                            wall_min_grade=WALL_MIN_GRADE,
                            cross_road_max_grade=CROSS_ROAD_CONFLICT_MAX_GRADE,
                            half_width_m=None):
    """Classifies every conflicting pair (detect_elevation_conflict_pairs)
    into one of three outcomes:

    - out of scope (cross-chain, different/missing road_name - e.g. a
      freeway ramp crossing an unrelated street, or - the common real case,
      2026-08-24 - an unnamed rural/unclassified road climbing near a named
      one) ALWAYS returned as a wall_pair, regardless of grade: unlike an
      in-scope pair, there's no shared-road relationship that could justify
      blending toward each other at any grade, so a wall is the only way to
      give BOTH sides real terrain instead of leaving whichever side loses
      the exclusion as bare, un-carved raw DEM. (Before 2026-08-24 this
      case was a dead end - excluded from the carve with no wall built,
      because build_retaining_walls.py only consumed in-scope wall_pairs -
      found via a switchback screenshot where the climbing road had no OSM
      name tag: EVERY station along the stretch near the valley road below
      conflicted with it, so the entire climb sat under untouched hillside
      DEM, not just the couple of stations actually near the crossing.)
    - in scope (same seg_idx, OR same non-empty road_name - the latter
      because a single real switchback road is often split across multiple
      OSM way segments, e.g. "Satellite Tracking Station Road" measured
      72m/40m across a chain break) AND grade >= wall_min_grade: excluded
      from the carve, returned as a wall_pair - a single terrain heightfield
      cannot represent this transition at all (near-vertical/overhanging),
      so generated wall geometry has to fill the gap.
    - in scope AND grade < wall_min_grade: NOT excluded - carve_grid_m's
      ordinary nearest-station stamp can represent a steep-but-continuous
      slope between the two stations directly, no new geometry needed.

    Returns (exclude, wall_pairs): exclude is a bool array parallel to the
    input stations (True = don't carve terrain smoothly toward this
    station - it's covered by a wall instead), wall_pairs is a list of
    (i, j) index tuples needing generated wall geometry (deduplicated - a
    pair caught by both the main and cross-road passes below only appears
    once). A station's exclude flag is only ever set True, never reset back
    to False by a later pair.

    half_width_m (2026-09-28, I-70 station 44269): per-station pavement
    half-width. When given, the in-scope wall grade is measured EDGE to edge
    (centerline distance minus both half-widths, floored at 0.5 m) - two
    carriageways 13.6 m apart centre-to-centre and 6 m apart vertically are
    a 0.44 centerline grade (no wall) but a 6 m cliff over a 3.6 m gap, which
    the 7.8 m terrain grid can't hold. The wall/terrain/verifier stages pass
    it; build_road_stations.py deliberately doesn't (its conflict set shapes
    road_stations.json itself, and changing that forces a full rebuild)."""
    n = len(east_m)
    east_m = np.asarray(east_m, dtype=np.float64)
    north_m = np.asarray(north_m, dtype=np.float64)
    elev_m = np.asarray(elev_m, dtype=np.float64)
    pairs_i, pairs_j = detect_elevation_conflict_pairs(
        east_m, north_m, elev_m, seg_idx, station_idx,
        radius_m, max_grade, min_gap_m, same_chain_gap_stations)

    exclude = np.zeros(n, dtype=bool)
    wall_pairs = []
    wall_pair_set = set()

    def add_wall_pair(i, j):
        key = (i, j) if i < j else (j, i)
        if key not in wall_pair_set:
            wall_pair_set.add(key)
            wall_pairs.append(key)
        exclude[i] = True
        exclude[j] = True

    for i, j in zip(pairs_i.tolist(), pairs_j.tolist()):
        in_scope = (seg_idx[i] == seg_idx[j]) or (
            bool(road_name[i]) and road_name[i] == road_name[j])
        if not in_scope:
            add_wall_pair(i, j)
            continue
        dist_m = math.hypot(east_m[j] - east_m[i], north_m[j] - north_m[i])
        if half_width_m is not None:
            dist_m = max(dist_m - half_width_m[i] - half_width_m[j], 0.5)
        grade = abs(elev_m[j] - elev_m[i]) / dist_m if dist_m > 1e-6 else float("inf")
        if grade >= wall_min_grade:
            add_wall_pair(i, j)
        # else: in-scope, below threshold - leave exclude as-is (un-excluded
        # unless some OTHER pair already required this station excluded)

    # Second, tighter pass: catches cross-road disagreements too small for
    # the main (same-road-switchback-calibrated) threshold above to see, but
    # still real enough that two DIFFERENT roads shouldn't blend into each
    # other's carve - see CROSS_ROAD_CONFLICT_MAX_GRADE's docstring. Same
    # "out of scope always walls" rule as the main pass above.
    cross_i, cross_j = detect_elevation_conflict_pairs(
        east_m, north_m, elev_m, seg_idx, station_idx,
        radius_m, cross_road_max_grade, min_gap_m, same_chain_gap_stations)
    for i, j in zip(cross_i.tolist(), cross_j.tolist()):
        in_scope = (seg_idx[i] == seg_idx[j]) or (
            bool(road_name[i]) and road_name[i] == road_name[j])
        if not in_scope:
            add_wall_pair(i, j)

    # Coherence filter (see WALL_RUN_MAX_ASPECT's comment): drop any wall
    # pair whose transitively-connected run isn't path-like, un-excluding
    # its stations so they fall back to ordinary carve blending instead of
    # feeding build_retaining_walls.py geometry it can't build sanely.
    if wall_pairs:
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, j in wall_pairs:
            union(i, j)

        components = defaultdict(list)
        for i, j in wall_pairs:
            components[find(i)].append(i)
            components[find(i)].append(j)
        bad_roots = set()
        for root, members in components.items():
            stations = sorted(set(members))
            if len(stations) < 3:
                continue   # a lone pair is always path-like (2 points)
            pts = np.stack([east_m[stations], north_m[stations]], axis=1)
            centered = pts - pts.mean(axis=0)
            cov = centered.T @ centered
            evals = np.linalg.eigvalsh(cov)
            primary_std = math.sqrt(max(evals[-1], 0.0))
            secondary_std = math.sqrt(max(evals[0], 0.0))
            if primary_std > 1e-6 and secondary_std / primary_std > WALL_RUN_MAX_ASPECT:
                bad_roots.add(root)

        if bad_roots:
            # An incoherent (blob-shaped) component can't get a clean ruled-surface
            # wall mesh from build_wall_run_mesh()'s single-PCA-axis approach - but
            # its stations are still real, close-in-XY/far-in-Z conflicts (that's
            # what put them in wall_pairs to begin with). Un-excluding them would
            # hand them back to the ordinary nearest-station terrain carve, which
            # can't represent two stations meters apart in XY with a huge Z gap
            # either - it stamps the wrong neighbor's elevation across the gap,
            # producing spikes far worse than the original silent-dead-end bug this
            # whole feature replaced (18m+ deviation, confirmed via verify_terrain_fit.py).
            # So: drop bad-component pairs from wall_pairs (no mesh built for them)
            # but leave `exclude` untouched - they keep falling back to raw,
            # un-carved DEM terrain at that spot, same as before this session's
            # wall-extension work, rather than being carved wrong.
            wall_pairs = [(i, j) for i, j in wall_pairs if find(i) not in bad_roots]

    return exclude, wall_pairs


def reconcile_junction_elevations(east_m, north_m, elev_m, seg_idx, station_idx, road_name,
                                   radius_m=JUNCTION_RECONCILE_RADIUS_M, max_grade=CONFLICT_MAX_GRADE,
                                   min_gap_m=CONFLICT_MIN_GAP_M,
                                   same_chain_gap_stations=CONFLICT_SAME_CHAIN_GAP_STATIONS,
                                   wall_min_grade=WALL_MIN_GRADE, is_bridge=None,
                                   same_name_edge_stations=JUNCTION_SAME_NAME_EDGE_STATIONS,
                                   same_seg_radius_m=JUNCTION_SAME_SEG_RADIUS_M):
    """The reusable road-road junction smoothing pass (Taylor, 2026-08-24: two
    roads meeting/crossing at grade were disagreeing on elevation by meters
    at the same physical spot - Lake Hughes near spawn, one road's pavement
    visibly bursting through the other). Each chain in build_road_stations.py
    is DEM-sampled and grade-smoothed independently (merge_way_chains() /
    grade_smooth_elevation()) - nothing upstream ever makes two DIFFERENT
    chains agree on elevation where they meet, even at a shared OSM node,
    because each chain's own grade-rate-limiting pass can pull that shared
    point away from raw DEM by a different amount depending on the rest of
    ITS OWN profile.

    Reuses classify_wall_stations' own conflict detection (same radius/grade/
    gap thresholds - single source of truth for "these two stations are the
    same physical spot") but only acts on the in-scope, BELOW wall_min_grade
    subset - exactly the set classify_wall_stations leaves alone today (a
    continuous, not near-vertical, disagreement it expects carve_grid_m's
    ordinary blend to paper over in the TERRAIN, which does nothing for the
    ROAD ribbon meshes themselves crossing at different heights). Steep/
    wall-eligible pairs are untouched here - those are real grade-separated
    crossings that need build_retaining_walls.py's wall geometry, not a
    flatten-together fix.

    Snaps the LOWER station of each disagreeing pair up to match the higher
    one, processing pairs HIGHEST-ELEVATION-FIRST (Taylor's own framing:
    "start at the highest point and work our way down making sure they
    connect") so an already-reconciled high point never gets dragged back
    down by a later, lower-priority pair - every write only ever raises a
    station's elevation, never lowers it, so pair order beyond the high-to-
    low sort can't create oscillation or new disagreements.

    Matching position isn't enough on its own (2026-08-24, Taylor: "cant be
    two sections of road on the same xyz axis with different slopes...
    after departing the road width they can SLOWLY start changing
    gradient") - two independently grade-smoothed roads can pass through the
    same point with different tangents, reading as a crease even though
    neither road kinks against ITSELF. `hi`'s profile is left untouched here
    (it's the higher-priority side), so it's the authoritative slope at the
    seam; for each snap this also computes `hi`'s local grade (central
    difference over its own neighbors, i.e. its own rate of climb/descent at
    that point) and reports it as `lo`'s target grade at the seam, SIGN-
    ALIGNED via the two roads' local tangent directions (both `hi`/`lo`
    stations only ever reach this function in-scope - same seg_idx or same
    road_name, i.e. literally the same physical road chain-broken or
    switchbacking on itself - so their headings are coherent enough for
    "same slope" to mean the same thing on both sides). If tangents point
    roughly opposite ways (dot < 0, e.g. a switchback doubling straight
    back), the sign is flipped so both sides still climb/descend together
    rather than mirror-imaging each other.

    `is_bridge` (optional bool array parallel to the input stations) drops
    any pair where EITHER side is a bridge/tunnel station from `keep`
    entirely (2026-09-04, mirrors the exclusion build_road_stations.py's
    graph-junction flatten pass applies for the same reason - see that
    pass's own comment above `junction_incident`). Found via a 51-jump,
    all-bridge-adjacent survey of road_stations.json (up to 34 m over one
    5 m station spacing, e.g. seg208): this pass's `in_scope` test is only
    "same seg_idx or same road_name" - it has no notion of physical
    connectivity, so a bridge deck's own DEM-independent, span-interpolated
    elevation (fill_structure_gaps/is_struct_s) can get matched, purely by
    XY proximity within radius_m, against an unrelated same-named-road
    station elsewhere in the network that the bridge doesn't actually
    connect to, and get snapped toward it - a near-vertical pop right at
    the deck's abutment. Left `None` (the default) preserves the original
    2026-08-24 Lake Hughes same-road behavior unchanged for callers that
    don't have a bridge flag handy.

    `same_seg_radius_m` (2026-09-08 fix, §7.34 - see JUNCTION_SAME_SEG_
    RADIUS_M's docstring): a same-segment ("switchback") match is only in
    scope when the two stations are within this many meters of each other in
    plan view - much tighter than `radius_m`/JUNCTION_RECONCILE_RADIUS_M,
    which only bounds the candidate-pair SEARCH (both branches share that
    search), not what counts as "the same physical spot" once same_seg is
    already known True. Without this, two points on the SAME switchback's
    different legs (close in plan view, genuinely far apart in real
    elevation and along-road distance) read as the documented Lake Hughes
    same-spot disagreement and got forcibly flattened together.

    `same_name_edge_stations` (2026-09-07 fix - see JUNCTION_SAME_NAME_EDGE_
    STATIONS's docstring): a cross-segment, name-only match is only in scope
    when BOTH stations sit within this many stations of their OWN segment's
    start/end. Without this, a divided highway sharing one name across many
    separate OSM way segments (I-70 through Silverthorne, whose two
    carriageways are independently graded and legitimately disagree in
    elevation by meters for kilometers) got treated as "the same physical
    road disagreeing with itself" at nearly every station pair within
    radius_m along its whole length, corrupting one carriageway's elevation
    toward the other's and producing enormous fake road-vs-terrain drops
    downstream. Same-segment pairs (seg_idx[i] == seg_idx[j], i.e. a road
    genuinely switchbacking/folding back on itself within its own chain) are
    unaffected by this restriction.

    Returns (elev_m, touched, pinned_grade): a NEW array (input is not
    mutated) with these snaps applied, a parallel bool mask of which
    stations changed, and a parallel float array (nan where unset) of the
    target grade at each touched station. The caller is expected to re-run
    grade_smooth_elevation() on each touched segment using that segment's
    own per-class max_grade/max_grade_delta arrays AND pass pinned_grade's
    values (converted to that segment's local edge indices) as
    pinned_edge_grades (not done here - this function only sees the
    flattened cross-segment arrays) so each snap turns into a slope-matched,
    rate-limited approach ramp instead of a new vertical kink at the
    junction - the same vertical-curve easing grade_smooth_elevation already
    does for any other local disagreement between input ground and
    profile.

    At-grade CROSS-road agreement (immediate-queue item 5) is deliberately
    NOT handled here: this function's output feeds the caller's per-segment
    re-smoothing, which structurally cannot preserve a two-road agreement it
    only sees one road of at a time (grade_smooth_elevation()'s pass 2 can
    float a whole approach ~1m off its reconciled ground - measured
    2026-09-06). See junction_agreement_pass() below, which
    build_road_stations.py runs on the FINAL smoothed profiles instead."""
    elev_m = np.array(elev_m, dtype=np.float64)
    n = len(elev_m)
    pairs_i, pairs_j = detect_elevation_conflict_pairs(
        east_m, north_m, elev_m, seg_idx, station_idx,
        radius_m, max_grade, min_gap_m, same_chain_gap_stations)
    touched = np.zeros(n, dtype=bool)
    pinned_grade = np.full(n, np.nan, dtype=np.float64)

    is_bridge_arr = None if is_bridge is None else np.asarray(is_bridge, dtype=bool)
    # per-segment station-index bound (last valid station_idx), used below to
    # tell a genuine chain-break/switchback seam apart from a divided-highway
    # false match - see JUNCTION_SAME_NAME_EDGE_STATIONS's docstring.
    seg_idx_arr = np.asarray(seg_idx)
    station_idx_arr = np.asarray(station_idx)
    seg_last_station = {}
    for s_val, st_val in zip(seg_idx_arr.tolist(), station_idx_arr.tolist()):
        prev = seg_last_station.get(s_val, -1)
        if st_val > prev:
            seg_last_station[s_val] = st_val
    keep = []
    for k in range(len(pairs_i)):
        i, j = int(pairs_i[k]), int(pairs_j[k])
        if is_bridge_arr is not None and (is_bridge_arr[i] or is_bridge_arr[j]):
            continue
        same_seg = seg_idx[i] == seg_idx[j]
        same_name = bool(road_name[i]) and road_name[i] == road_name[j]
        if same_seg:
            # §7.34 fix - see JUNCTION_SAME_SEG_RADIUS_M's docstring: a real
            # same-spot disagreement (Lake Hughes, 1.1-2.4m) sits nowhere
            # near a switchback's own two legs merely passing within the
            # wide 20m search radius of each other in plan view - only the
            # former is this pass's actual target.
            dist_m_pre = math.hypot(east_m[j] - east_m[i], north_m[j] - north_m[i])
            in_scope = dist_m_pre <= same_seg_radius_m
        elif same_name:
            # Cross-segment, name-only match: only in scope near EITHER end
            # of BOTH segments (a real chain-break seam is local, not
            # scattered along a segment's interior - see
            # JUNCTION_SAME_NAME_EDGE_STATIONS).
            si, sj = int(station_idx[i]), int(station_idx[j])
            last_i = seg_last_station.get(int(seg_idx[i]), si)
            last_j = seg_last_station.get(int(seg_idx[j]), sj)
            near_edge_i = si <= same_name_edge_stations or si >= last_i - same_name_edge_stations
            near_edge_j = sj <= same_name_edge_stations or sj >= last_j - same_name_edge_stations
            in_scope = near_edge_i and near_edge_j
        else:
            in_scope = False
        if not in_scope:
            continue
        dist_m = math.hypot(east_m[j] - east_m[i], north_m[j] - north_m[i])
        grade = abs(elev_m[j] - elev_m[i]) / dist_m if dist_m > 1e-6 else float("inf")
        if grade < wall_min_grade:
            keep.append(k)

    def local_tangent(idx):
        # unit (east, north) direction at idx, in increasing-station_idx
        # sense, from whichever same-segment neighbor(s) exist.
        seg = seg_idx[idx]
        a = idx - 1 if idx - 1 >= 0 and seg_idx[idx - 1] == seg else idx
        b = idx + 1 if idx + 1 < n and seg_idx[idx + 1] == seg else idx
        if a == b:
            return 0.0, 0.0
        dx, dy = east_m[b] - east_m[a], north_m[b] - north_m[a]
        d = math.hypot(dx, dy)
        return (dx / d, dy / d) if d > 1e-9 else (0.0, 0.0)

    elev_orig = elev_m.copy()

    def local_grade(idx):
        # signed dz/ds at idx (increasing-station_idx sense), central
        # difference over whichever same-segment neighbor(s) exist. Reads
        # elev_ORIG, not the elev_m this loop is mutating in place (2026-08-24
        # fix, Taylor's Lake Hughes screenshots after the first version of
        # this: `hi` can itself have been snapped upward earlier in this same
        # descending-elevation pass while its own immediate neighbors are
        # still at their raw, un-resmoothed elevation - a central difference
        # against the live array then reads a fictitious spike (measured up
        # to ~500% grade) at hi instead of the real local terrain grade,
        # which got pinned into lo's profile and tore a canyon into the
        # carve. The pre-loop snapshot is always real input-derived slope.
        seg = seg_idx[idx]
        a = idx - 1 if idx - 1 >= 0 and seg_idx[idx - 1] == seg else idx
        b = idx + 1 if idx + 1 < n and seg_idx[idx + 1] == seg else idx
        if a == b:
            return 0.0
        d = math.hypot(east_m[b] - east_m[a], north_m[b] - north_m[a])
        return (elev_orig[b] - elev_orig[a]) / d if d > 1e-9 else 0.0

    if keep:
        keep_i = pairs_i[np.array(keep)]
        keep_j = pairs_j[np.array(keep)]
        order = np.argsort(-np.maximum(elev_m[keep_i], elev_m[keep_j]))
        for k in order:
            i, j = int(keep_i[k]), int(keep_j[k])
            hi, lo = (i, j) if elev_m[i] >= elev_m[j] else (j, i)
            if elev_m[lo] < elev_m[hi]:
                elev_m[lo] = elev_m[hi]
                touched[lo] = True
                hx, hy = local_tangent(hi)
                lx, ly = local_tangent(lo)
                aligned = (hx * lx + hy * ly) >= 0.0
                target = local_grade(hi)
                # hard backstop: a pinned slope should never exceed
                # wall_min_grade (the same "single heightfield can still
                # represent this" ceiling classify_wall_stations uses) -
                # anything past that is not a real road grade and would tear
                # the terrain carve instead of easing into it.
                target = float(np.clip(target, -wall_min_grade, wall_min_grade))
                pinned_grade[lo] = target if aligned else -target

    return elev_m, touched, pinned_grade


def junction_agreement_pass(east_m, north_m, elev_m, seg_idx,
                             junction_radius_m=JUNCTION_AGREE_RADIUS_M,
                             max_grade=CONFLICT_MAX_GRADE,
                             min_gap_m=CONFLICT_MIN_GAP_M,
                             junction_skip=None):
    """Cross-road at-grade junction agreement (2026-09-06, immediate-queue
    item 5). reconcile_junction_elevations() only acts on pairs
    detect_elevation_conflict_pairs() flags, and that threshold
    (max_grade * d + min_gap_m) deliberately ignores roughly sub-meter
    disagreements - which is exactly the size of step two independently
    grade-smoothed roads leave where they CROSS at grade (junction audit
    2026-09-06 baseline: 37 sites > 0.3m, worst 1.09m - one pavement
    sitting proud of the other right at the crossing, left for the
    junction-pad bake to paper over).

    Same snap rule as reconcile_junction_elevations() (raise the LOWER
    station to the higher one, highest-elevation pair first, raise-only so
    ordering can't oscillate), applied to every cross-SEGMENT pair within
    junction_radius_m whose gap sits BELOW the conflict threshold - bigger
    gaps are wall/exclusion territory and are left alone, and
    `junction_skip` (the caller's bridge/tunnel/conflict exclusion mask)
    guards the tagged cases so a road passing under an overpass is never
    dragged up to the deck.

    CRITICALLY, this pass runs on the FINAL smoothed profiles and its
    output ships as-is - NOT on raw reconciled ground with a re-smooth
    after, which was tried twice on the 2026-09-06 rebakes and regressed
    the audit both times: grade_smooth_elevation()'s envelope clamp cut a
    bare one-station ground raise back to ~max_grade*spacing (72 sites
    > 0.3m vs 37 baseline), and its pass-2 rate limiter + endpoint-drift
    ramp floated whole approaches ~1m ABOVE their reconciled ground even
    with no pins at all (the final envelope_clamp backstop bounds GRADE,
    not "<= ground") - per-segment re-smoothing structurally cannot
    preserve a two-road agreement it only sees one road of at a time.
    Instead each snap shapes its own approach: taper_walk() below extends
    a JUNCTION_AGREE_TAPER_GRADE descent from the crossing along the lower
    road's own stations (raise-only, so it stops the moment the road's
    existing profile is already higher), grade-legal under every
    ROAD_CLASS_GRADE ceiling and "SLOWLY start[ing] changing gradient"
    (Taylor, 2026-08-24) away from the crossing.

    Returns (elev_m, touched): a NEW array (input is not mutated) and a
    bool mask marking BOTH stations of every at-grade crossing pair - also
    the ones already in agreement, since `touched` means "an at-grade
    junction lives here" (build_road_guardrails.py breaks rail runs on it),
    not "this station's elevation moved". Taper-raised approach stations
    are not flagged. junction_radius_m <= 0 disables the pass."""
    elev_m = np.array(elev_m, dtype=np.float64)
    n = len(elev_m)
    touched = np.zeros(n, dtype=bool)
    if junction_radius_m <= 0.0 or n == 0:
        return elev_m, touched
    skip = (np.zeros(n, dtype=bool) if junction_skip is None
            else np.asarray(junction_skip, dtype=bool))
    cell = junction_radius_m
    jbuckets = defaultdict(list)
    for i in range(n):
        if not skip[i]:
            jbuckets[(int(east_m[i] // cell), int(north_m[i] // cell))].append(i)
    r2 = junction_radius_m * junction_radius_m
    jpairs = []
    for (bx, by), idxs in jbuckets.items():
        neigh = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                neigh.extend(jbuckets.get((bx + dx, by + dy), ()))
        for i in idxs:
            for j in neigh:
                if j <= i or seg_idx[i] == seg_idx[j]:
                    continue
                de = east_m[i] - east_m[j]
                dn = north_m[i] - north_m[j]
                d2 = de * de + dn * dn
                if d2 > r2:
                    continue
                d = math.sqrt(d2)
                if abs(elev_m[i] - elev_m[j]) > max_grade * d + min_gap_m:
                    continue   # conflict-sized gap - not an at-grade junction
                jpairs.append((i, j))
    if not jpairs:
        return elev_m, touched

    base_elev = elev_m.copy()

    def taper_walk(start, step, target):
        # Raise the profile along `start`'s own segment in direction
        # `step`, easing the crossing's RAISE back to zero at
        # JUNCTION_AGREE_TAPER_GRADE on top of each station's pre-pass
        # elevation. Raise-only; stops at the segment boundary, at a skipped
        # (bridge/tunnel/conflict) station, or once the taper dips below
        # the road's own existing profile. (2026-09-28: was an absolute
        # `target - grade*dist` line, which never meets a road climbing
        # faster than 3% toward the crossing - the raise GREW every station
        # until a skip station cut it off: a 103 m cliff on Willow Creek
        # seg013 once follow_ground_profile() kept its real ~10% grade.)
        raised = 0
        seg = seg_idx[start]
        k, prev = start + step, start
        dist = 0.0
        raise0 = target - base_elev[start]
        while 0 <= k < n and seg_idx[k] == seg:
            if skip[k]:
                break
            dist += math.hypot(east_m[k] - east_m[prev],
                               north_m[k] - north_m[prev])
            want = base_elev[k] + raise0 - JUNCTION_AGREE_TAPER_GRADE * dist
            if want <= elev_m[k]:
                break
            elev_m[k] = want
            raised += 1
            prev, k = k, k + step
        return raised

    jp = np.array(jpairs, dtype=np.int64)
    jorder = np.argsort(-np.maximum(elev_m[jp[:, 0]], elev_m[jp[:, 1]]))
    n_junction_snaps = 0
    n_taper_raised = 0
    for k in jorder:
        i, j = int(jp[k, 0]), int(jp[k, 1])
        hi, lo = (i, j) if elev_m[i] >= elev_m[j] else (j, i)
        touched[hi] = touched[lo] = True
        if elev_m[lo] < elev_m[hi]:
            target = float(elev_m[hi])
            elev_m[lo] = target
            n_junction_snaps += 1
            n_taper_raised += taper_walk(lo, -1, target)
            n_taper_raised += taper_walk(lo, +1, target)
    if n_junction_snaps:
        print(f"junction-agreement pass: {n_junction_snaps} station(s) "
              f"raised to their crossing's higher pavement, "
              f"{n_taper_raised} approach station(s) taper-raised at "
              f"{JUNCTION_AGREE_TAPER_GRADE * 100:.0f}% "
              f"({len(jpairs)} at-grade cross-segment pair(s) within "
              f"{junction_radius_m:.0f}m)")
    return elev_m, touched


def carve_heightmap(hm, station_lon, station_lat, station_elev_m,
                     west, south, east, north, inner=10.0, outer=40.0,
                     drop=0.15, cut_inner=None, cut_outer=None):
    """Stamp road elevation into an elevation grid within `inner` m of the
    nearest road station, blending to raw terrain at `outer` m - the same
    inverse-distance-stamp + smoothstep blend as build_stage.py's
    carve_road(), generalized from a fixed local-mpp grid to an arbitrary
    lon/lat-bounded grid (a Terrarium/quantized-mesh tile at any zoom).

    `cut_inner`/`cut_outer` (both default to `inner`/`outer` if unset) give a
    separate, normally tighter, taper for pixels whose raw terrain sits ABOVE
    the nearest station (a hillside/cut pixel) - see
    build_terrain_mesh_tiles.py's TERRAIN_CARVE_CUT_INNER_M for the reasoning
    (a wide taper on a cut face reads as terrain sitting on the road instead
    of a clean cut). Fill pixels (raw terrain below the station) always use
    `inner`/`outer`.

    `hm` is (rows, cols), row 0 = the `north` edge, col 0 = the `west` edge.
    Callers building a pymartini/quantized-mesh tile MUST pass the array
    AFTER the `.T` pymartini expects for `create_tile`/`rescale_positions`,
    not before - pymartini indexes whatever array it's actually given with
    standard row=y/col=x semantics, and that array is the transposed one.
    Carving before the transpose stamps every station at its diagonal-mirror
    position instead of its real one (found 2026-07-20 via two stations that
    measured 60 m and 444 m off; both dropped under 1 m once the carve moved
    to after the transpose - confirmed with a controlled non-planar 2-D
    gradient test, not assumed). `station_lon/lat/elev_m` are 1-D arrays for
    every road station anywhere near this tile (the caller should prefilter
    by bbox for speed; a station outside `outer` of every pixel is a no-op
    either way, this is a correctness fallback not a requirement).

    `drop` (m) biases the carved surface slightly below the actual road
    surface so it can never poke through (same margin build_stage.py uses).
    No-ops (returns hm unchanged) if no stations are passed.
    """
    if len(station_lon) == 0:
        return hm
    rows, cols = hm.shape
    lat_mid = (south + north) / 2.0
    m_per_deg_lat = 111132.0
    m_per_deg_lon = m_per_deg_lat * math.cos(math.radians(lat_mid))
    mpp_x = (east - west) / (cols - 1) * m_per_deg_lon
    mpp_y = (north - south) / (rows - 1) * m_per_deg_lat
    if mpp_x <= 0 or mpp_y <= 0:
        return hm
    dist = np.full(hm.shape, np.inf, dtype=np.float32)
    roadh = np.zeros_like(hm)
    r = int(math.ceil(outer / max(min(mpp_x, mpp_y), 1e-6))) + 1
    px_all = (station_lon - west) / (east - west) * (cols - 1)
    py_all = (north - station_lat) / (north - south) * (rows - 1)
    for i in range(len(station_lon)):
        px, py = px_all[i], py_all[i]
        x0, x1 = max(0, int(px) - r), min(cols, int(px) + r + 1)
        y0, y1 = max(0, int(py) - r), min(rows, int(py) + r + 1)
        if x0 >= x1 or y0 >= y1:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1]
        d = np.hypot((xx - px) * mpp_x, (yy - py) * mpp_y)
        sub = dist[y0:y1, x0:x1]
        m = d < sub
        sub[m] = d[m]
        roadh[y0:y1, x0:x1][m] = station_elev_m[i]
    ci = inner if cut_inner is None else cut_inner
    co = outer if cut_outer is None else cut_outer
    is_cut = hm > roadh
    px_inner = np.where(is_cut, ci, inner)
    px_outer = np.where(is_cut, co, outer)
    w = np.clip((px_outer - dist) / (px_outer - px_inner), 0.0, 1.0)
    w = w * w * (3 - 2 * w)
    return w * (roadh - drop) + (1 - w) * hm


def sample_mesh_height(positions, triangles, x_cm, y_cm, eps=1e-4):
    """Barycentric-sample a triangle mesh's height (Z) at an XY point.

    RECONSTRUCTED (2026-07-28): this used to live in pipeline/verify_road_fit.py,
    which a prior cleanup pass deleted as believed-dead - it was actually still
    imported by build_forest_elevation.py and verify_terrain_fit.py (both real,
    live pipeline stages), so that deletion broke them. The original
    implementation could not be recovered (checked git history/stash/fsck - never
    committed; Windows Recycle Bin - git-bash rm bypasses it; VS Code local
    history - this file was never opened there; File History/VSS - not
    configured on this machine). This is a from-scratch reimplementation of the
    standard algorithm the call sites clearly require (find the triangle whose
    XY footprint contains the query point, interpolate Z from its vertices'
    barycentric weights) - tested against real built data
    (data/road_tiles/oahu_test/) to confirm it returns sane heights and a
    near-zero miss rate, but it is NOT guaranteed byte-identical to whatever the
    original did internally. Re-verify verify_terrain_fit.py's output against a
    known-good baseline before trusting a borderline PASS/FAIL from it.

    AUDIT-RELIABILITY.md SS4.3 (2026-08-21): wherever more than one triangle's
    XY footprint contains the query point (skirts, walls, bridges - anywhere
    geometry legitimately overlaps in plan view), this now returns the
    TOPMOST (highest interpolated Z) containing triangle, not simply the
    first one np.where() happens to return - "first" was order-arbitrary
    (depended on triangle emission order, not geometry) and wrong exactly at
    the overlap sites a fit gate cares most about, e.g. a skirt's downward
    slope sitting well below the drivable surface it skirts. Checked both
    current call sites (build_forest_elevation.py - wants the ground/canopy
    surface a tree actually sits on, not a skirt underside;
    verify_terrain_fit.py - wants the drivable/carved surface, same reasoning)
    before making this change: both want "the surface you'd actually stand
    on," i.e. topmost, so this is a safe shared-behavior change, not a
    per-caller special case.

    positions: (N,3) array of vertex (x_cm, y_cm, z_cm).
    triangles: (M,3) int array of vertex indices per triangle.
    Returns the interpolated z_cm at the TOPMOST containing triangle, or None
    if the point falls outside every triangle (e.g. genuinely off the tile,
    or right on a shared-edge seam that floating-point rounding pushed just
    outside both neighbors).
    """
    tri_pts = positions[triangles]   # (M, 3, 3): [tri, vert(0/1/2), xyz]
    ax, ay = tri_pts[:, 0, 0], tri_pts[:, 0, 1]
    bx, by = tri_pts[:, 1, 0], tri_pts[:, 1, 1]
    cx, cy = tri_pts[:, 2, 0], tri_pts[:, 2, 1]

    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    nonzero = denom != 0
    safe_denom = np.where(nonzero, denom, 1.0)   # avoid div-by-zero on degenerate tris

    w_a = ((by - cy) * (x_cm - cx) + (cx - bx) * (y_cm - cy)) / safe_denom
    w_b = ((cy - ay) * (x_cm - cx) + (ax - cx) * (y_cm - cy)) / safe_denom
    w_c = 1.0 - w_a - w_b

    inside = nonzero & (w_a >= -eps) & (w_b >= -eps) & (w_c >= -eps)
    idxs = np.where(inside)[0]
    if len(idxs) == 0:
        return None

    za_all = tri_pts[idxs, 0, 2]
    zb_all = tri_pts[idxs, 1, 2]
    zc_all = tri_pts[idxs, 2, 2]
    z_all = w_a[idxs] * za_all + w_b[idxs] * zb_all + w_c[idxs] * zc_all
    return float(np.max(z_all))


def sample_mesh_heights(positions, triangles, xs_cm, ys_cm, eps=1e-4, chunk=50_000):
    """Batch sample_mesh_height (2026-10-01): the same barycentric test and
    topmost rule at many points, NaN where a point is off-mesh. Triangles are
    binned by XY bounding box into a grid, so each point only tests the few
    triangles in its cell instead of all of them - per-point calls over a
    whole tile took the forest verifier over an hour."""
    xs = np.asarray(xs_cm, dtype=np.float64)
    ys = np.asarray(ys_cm, dtype=np.float64)
    out = np.full(len(xs), -np.inf)
    if len(xs) == 0 or len(triangles) == 0:
        return np.full(len(xs), np.nan)
    tri_pts = positions[triangles]   # (M, 3, 3)
    tx, ty = tri_pts[:, :, 0], tri_pts[:, :, 1]
    # pad each bbox: eps lets a point sit a hair outside its triangle
    pad = 1.0 + 1e-3 * np.maximum(tx.max(1) - tx.min(1), ty.max(1) - ty.min(1))
    bx0, bx1 = tx.min(1) - pad, tx.max(1) + pad
    by0, by1 = ty.min(1) - pad, ty.max(1) + pad
    g = int(np.clip(np.sqrt(len(triangles)) / 2, 1, 256))
    gx0, gy0 = bx0.min(), by0.min()
    cell = max(bx1.max() - gx0, by1.max() - gy0) / g

    def to_cell(v, v0):
        return np.clip(((v - v0) / cell).astype(np.int64), 0, g - 1)

    ci0, ci1, cj0, cj1 = to_cell(bx0, gx0), to_cell(bx1, gx0), to_cell(by0, gy0), to_cell(by1, gy0)
    nx = ci1 - ci0 + 1
    cnt = nx * (cj1 - cj0 + 1)
    k = np.arange(cnt.sum()) - np.repeat(np.cumsum(cnt) - cnt, cnt)
    nx_r = np.repeat(nx, cnt)
    cells = (np.repeat(cj0, cnt) + k // nx_r) * g + np.repeat(ci0, cnt) + k % nx_r
    order = np.argsort(cells, kind="stable")
    tri_of = np.repeat(np.arange(len(triangles)), cnt)[order]
    starts = np.searchsorted(cells[order], np.arange(g * g))
    ends = np.searchsorted(cells[order], np.arange(g * g), side="right")

    ax, ay, az = tri_pts[:, 0, 0], tri_pts[:, 0, 1], tri_pts[:, 0, 2]
    bx, by, bz = tri_pts[:, 1, 0], tri_pts[:, 1, 1], tri_pts[:, 1, 2]
    cx, cy, cz = tri_pts[:, 2, 0], tri_pts[:, 2, 1], tri_pts[:, 2, 2]
    denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)

    for lo in range(0, len(xs), chunk):
        px, py = xs[lo:lo + chunk], ys[lo:lo + chunk]
        inside_grid = ((px >= gx0) & (px <= gx0 + g * cell)
                       & (py >= gy0) & (py <= gy0 + g * cell))
        pc = to_cell(py, gy0) * g + to_cell(px, gx0)
        c = np.where(inside_grid, ends[pc] - starts[pc], 0)
        pt = np.repeat(np.arange(len(px)), c)
        kk = np.arange(c.sum()) - np.repeat(np.cumsum(c) - c, c)
        tri = tri_of[np.repeat(starts[pc], c) + kk]
        x, y = px[pt], py[pt]
        d = denom[tri]
        nonzero = d != 0
        d = np.where(nonzero, d, 1.0)
        w_a = ((by[tri] - cy[tri]) * (x - cx[tri]) + (cx[tri] - bx[tri]) * (y - cy[tri])) / d
        w_b = ((cy[tri] - ay[tri]) * (x - cx[tri]) + (ax[tri] - cx[tri]) * (y - cy[tri])) / d
        w_c = 1.0 - w_a - w_b
        ok = nonzero & (w_a >= -eps) & (w_b >= -eps) & (w_c >= -eps)
        tri, pt = tri[ok], pt[ok]
        z = w_a[ok] * az[tri] + w_b[ok] * bz[tri] + w_c[ok] * cz[tri]
        np.maximum.at(out, lo + pt, z)
    out[np.isinf(out)] = np.nan
    return out
