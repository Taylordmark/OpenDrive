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
SKIRT_EXTRA = 3.0            # m outward for embankment skirt
SKIRT_DROP = 2.5             # m downward at skirt edge
ROAD_LIFT = 0.10             # m above smoothed ground (wishlist #11: keep the
                             # road close to terrain; build_terrain_tiles.py's
                             # CARVE_DROP_M carves the terrain further below)
MAX_BANK_RAD = 0.09          # ~5.2 deg superelevation cap
BANK_GAIN = 9.55             # bank = min(cap, gain * |curvature 1/m|)
CHUNK_STATIONS = 150         # stations per road mesh chunk (~750 m)

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


def gaussian1d(arr, sigma):
    r = max(1, int(3 * sigma))
    x = np.arange(-r, r + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    return np.convolve(np.pad(arr, r, mode="edge"), k, mode="valid")


def median1d(arr, win):
    r = win // 2
    padded = np.pad(arr, r, mode="edge")
    return np.median(np.lib.stride_tricks.sliding_window_view(padded, win), axis=1)


def resample_stations(east, north, spacing=STATION_SPACING):
    """Resample an east/north polyline (m) to even arc-length spacing, then
    Gaussian-smooth laterally (breaks polyline corners, preserves hairpins).
    Returns (s, e, n): station distances (m) + smoothed east/north (m)."""
    d = np.concatenate([[0], np.cumsum(np.hypot(np.diff(east), np.diff(north)))])
    total_len = d[-1]
    s = np.arange(0, total_len, spacing)
    e = np.interp(s, d, east)
    n = np.interp(s, d, north)
    e = gaussian1d(e, 1.2)
    n = gaussian1d(n, 1.2)
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


def grade_smooth_elevation(ground, spacing=STATION_SPACING, max_grade=MAX_GRADE,
                            max_grade_delta=None):
    """Grade-CONFORMING envelope over raw ground - never smooths the terrain
    signal itself (no median/Gaussian filtering of `ground`; a road rides
    the real ground or deviates from it via cut/bridge/tunnel, it never
    fakes the ground into something gentler than it is).

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
        for _ in range(4):
            for i in range(1, len(g)):
                g[i] = np.clip(g[i], g[i - 1] - rate_md[i - 1], g[i - 1] + rate_md[i - 1])
            for i in range(len(g) - 2, -1, -1):
                g[i] = np.clip(g[i], g[i + 1] - rate_md[i], g[i + 1] + rate_md[i])
        h2 = np.empty(n)
        h2[0] = h[0]
        h2[1:] = h[0] + np.cumsum(g * spacing)
        drift = h[-1] - h2[-1]
        h2 += drift * (np.arange(n) / (n - 1))
        h = envelope_clamp(h2)   # hard backstop, see docstring

    return h


def build_road_chunks(s, e, n, z, name_prefix="road", spacing=STATION_SPACING,
                       chunk_stations=CHUNK_STATIONS,
                       road_half_width=ROAD_HALF_WIDTH, skirt_extra=SKIRT_EXTRA,
                       skirt_drop=SKIRT_DROP, max_bank_rad=MAX_BANK_RAD,
                       bank_gain=BANK_GAIN, is_oneway=None):
    """Build banked, skirted road mesh chunks from station arrays.

    `s` is the per-station arc-length distance (m, from resample_stations).
    `e`, `n`, `z` are smoothed per-station east/north/elevation in meters
    (local equirect frame; `z` already includes ROAD_LIFT). `is_oneway`
    (optional, per-station bool array) picks the chunk's "oneway" output
    flag by majority vote across its stations - a chunk boundary essentially
    never lands mid-carriageway (chains only break at real OSM junctions),
    so this is just a tie-breaker, not a meaningfully lossy reduction.
    Returns (chunks, cx, cy, cz) where chunks matches road_mesh.json's
    {"name","vertices","uvs","triangles","oneway"} schema and cx/cy/cz are
    the UE-frame (cm) station centerline arrays for the caller's own use
    (yaw calc, player start, stats).
    """
    # UE frame: X=east, Y=south(-north), Z=up, cm
    cx, cy, cz = e * 100.0, -n * 100.0, z * 100.0
    tx = np.gradient(cx); ty = np.gradient(cy)
    tl = np.hypot(tx, ty); tx /= tl; ty /= tl
    rx, ry = -ty, tx          # right of travel, horizontal

    # superelevation: raise the OUTER edge through curves (inner edge stays at
    # road height, so the carved terrain can never poke through the low side)
    theta = np.unwrap(np.arctan2(ty, tx))
    curv = np.gradient(theta) / spacing          # 1/m, signed
    bank = np.minimum(max_bank_rad, bank_gain * np.abs(gaussian1d(curv, 3.0)))
    bank_tan = np.tan(gaussian1d(bank, 3.0))
    raise_side = -np.sign(gaussian1d(curv, 3.0))         # +1: raise right side

    # cross-section: (lateral offset m, z offset m), left -> right
    profile = [(-(road_half_width + skirt_extra), -skirt_drop),
               (-road_half_width, 0.0),
               (road_half_width, 0.0),
               (road_half_width + skirt_extra, -skirt_drop)]
    n_prof = len(profile)

    def station_verts(i):
        out = []
        for off, dz in profile:
            lift = bank_tan[i] * max(0.0, off * raise_side[i])  # outer-edge raise
            out.append((cx[i] + rx[i] * off * 100.0,
                        cy[i] + ry[i] * off * 100.0,
                        cz[i] + (dz + lift) * 100.0))
        return out

    # chunked mesh buffers; winding (A, A+right, A+forward) faces up in UE
    chunks = []
    n_st = len(s)
    for c0 in range(0, n_st - 1, chunk_stations):
        c1 = min(c0 + chunk_stations, n_st - 1)
        verts, tris, uvs = [], [], []
        for i in range(c0, c1 + 1):
            verts.extend(station_verts(i))
            v = s[i] / 20.0
            uvs.extend([(j / (n_prof - 1), v) for j in range(n_prof)])
        for irow in range(c1 - c0):
            b = irow * n_prof
            for j in range(n_prof - 1):
                p, q = b + j, b + j + n_prof
                tris.append((p, p + 1, q))
                tris.append((p + 1, q + 1, q))
        oneway = bool(np.mean(is_oneway[c0:c1 + 1]) > 0.5) if is_oneway is not None else False
        chunks.append({
            "name": f"{name_prefix}_{c0:04d}",
            "vertices": [round(v, 1) for xyz in verts for v in xyz],
            "uvs": [round(v, 4) for uv in uvs for v in uv],
            "triangles": [i for t in tris for i in t],
            "oneway": oneway,
        })
    return chunks, cx, cy, cz


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
CONFLICT_MIN_GAP_M = 3.0         # floor so two stations meters apart with a
                                  # sub-meter difference (ordinary local
                                  # noise) never gets flagged
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

# Bias a generated wall's bottom/top edges past the real station elevation
# by this much so the wall visually embeds into terrain instead of leaving a
# hairline gap - the same "gap hidden under geometry" pattern already used
# by the road skirt (SKIRT_DROP above) and build_terrain_mesh_tiles.py's
# build_seam_skirt(), applied to a fourth case.
WALL_EMBED_MARGIN_M = 1.5


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
                            wall_min_grade=WALL_MIN_GRADE):
    """Classifies every conflicting pair (detect_elevation_conflict_pairs)
    into one of three outcomes:

    - out of scope (cross-chain, different road_name - e.g. a freeway ramp
      crossing an unrelated street): excluded from the terrain carve exactly
      as before, no wall generated - this is real interchange/ramp geometry,
      deliberately deferred to a later pass, not something this function
      attempts.
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
    station), wall_pairs is a list of (i, j) index tuples needing generated
    wall geometry. A station's exclude flag is only ever set True, never
    reset back to False by a later pair - so a station touched by BOTH an
    out-of-scope pair and an in-scope below-threshold pair still ends up
    correctly excluded (the out-of-scope pair's own reasoning still holds)."""
    n = len(east_m)
    east_m = np.asarray(east_m, dtype=np.float64)
    north_m = np.asarray(north_m, dtype=np.float64)
    elev_m = np.asarray(elev_m, dtype=np.float64)
    pairs_i, pairs_j = detect_elevation_conflict_pairs(
        east_m, north_m, elev_m, seg_idx, station_idx,
        radius_m, max_grade, min_gap_m, same_chain_gap_stations)

    exclude = np.zeros(n, dtype=bool)
    wall_pairs = []
    for i, j in zip(pairs_i.tolist(), pairs_j.tolist()):
        in_scope = (seg_idx[i] == seg_idx[j]) or (
            bool(road_name[i]) and road_name[i] == road_name[j])
        if not in_scope:
            exclude[i] = True
            exclude[j] = True
            continue
        dist_m = math.hypot(east_m[j] - east_m[i], north_m[j] - north_m[i])
        grade = abs(elev_m[j] - elev_m[i]) / dist_m if dist_m > 1e-6 else float("inf")
        if grade >= wall_min_grade:
            exclude[i] = True
            exclude[j] = True
            wall_pairs.append((i, j))
        # else: in-scope, below threshold - leave exclude as-is (un-excluded
        # unless some OTHER pair already required this station excluded)
    return exclude, wall_pairs


def carve_heightmap(hm, station_lon, station_lat, station_elev_m,
                     west, south, east, north, inner=10.0, outer=40.0,
                     drop=0.15):
    """Stamp road elevation into an elevation grid within `inner` m of the
    nearest road station, blending to raw terrain at `outer` m - the same
    inverse-distance-stamp + smoothstep blend as build_stage.py's
    carve_road(), generalized from a fixed local-mpp grid to an arbitrary
    lon/lat-bounded grid (a Terrarium/quantized-mesh tile at any zoom).

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
    w = np.clip((outer - dist) / (outer - inner), 0.0, 1.0)
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
