"""Shared generation-ID stamping for every OpenDrive binary sidecar format
(added 2026-08-21, AUDIT-RELIABILITY.md SS3.1/SS5.1, work-queue item 5).

Why this exists: nothing in the pipeline previously recorded which run of
road_stations.json a given .bin tile was baked against, so a partial rerun
(e.g. re-baking terrain/road/walls after a station-elevation edit but NOT
re-baking barrier/buildings) silently produced a mixed-generation world with
no error anywhere (AUDIT-RELIABILITY.md SS2). Every exporter in the pipeline
now calls compute_generation_id() once per run and writes the same short id
into every tile file's header (right after the magic); the C++ tile
managers compare it across tiles within their own prefix and log (not
block) on a mismatch.

The id is a truncated sha256 over:
  - road_stations.json's origin + every segment's east_m/north_m/elev_m
    arrays (rounded to mm to strip float-repr/round-trip noise across json
    dumps, not because mm precision matters physically) - this is the
    single source of truth every downstream stage reads, so a station
    rewrite (the exact 2026-07-30 Z-axis rewrite that produced SS2's mixed
    bake) changes the id.
  - the handful of constants that actually change the SHAPE of derived
    geometry, not just performance/LOD knobs: road cross-section/skirt
    dimensions and banking (road_mesh_common.py), the DEM plausibility
    clamp (now per-area in test_areas.py - AUDIT-RELIABILITY.md SS3.1
    flagged the old shared constant as Oahu-specific, fixed 2026-08-22 per
    PLAN-RELIABILITY.md Phase 3 item 9), and the world-space tile grid +
    origin height (test_areas.py) that every tile-bucketing exporter
    depends on.
    Deliberately excluded: pure LOD/perf constants (NEAR_GRID_N, FAR_GRID_N,
    MAX_ERROR_NEAR_M, DEM zoom levels, martini simplification tolerance,
    carve inner/outer radii) - these change tessellation density/cost, not
    the road/terrain PROFILE two independently-baked formats need to agree
    on, and stamping them would force a barrier/building re-bake on a
    terrain-only perf tuning pass for no reliability benefit.

Deliberately NOT importing build_terrain_mesh_tiles.py here (it pulls in
requests/pymartini) - this module is imported by every exporter, including
lightweight ones like export_place_labels_bin.py that have no business
requiring the terrain bake's dependencies just to stamp a header.
"""

import hashlib
import json
from pathlib import Path

from road_mesh_common import (
    BANK_GAIN,
    DEM_SPIKE_THRESHOLD_M,
    JUNCTION_AGREE_RADIUS_M,
    JUNCTION_AGREE_TAPER_GRADE,
    LANE_WIDTH_M,
    MAX_BANK_RAD,
    MAX_HALF_WIDTH_M,
    MIN_HALF_WIDTH_M,
    ROAD_HALF_WIDTH,
    ROAD_LIFT,
    SKIRT_DROP,
    SKIRT_EXTRA,
    STATION_SPACING,
    WIDTH_SMOOTH_SIGMA_M,
)
from test_areas import (
    AREA_NAME,
    ELEV_PLAUSIBLE_MAX_M,
    ELEV_PLAUSIBLE_MIN_M,
    ORIGIN_HEIGHT_M,
    ROAD_TILE_SIZE_CM,
    TERRAIN_TILE_SIZE_CM,
)

ROOT = Path(__file__).resolve().parent.parent
STATIONS_PATH = ROOT / "data" / "road_tiles" / f"{AREA_NAME}_test" / "road_stations.json"

# Fixed-width ASCII-hex field every sidecar header carries right after its
# magic - 16 hex chars (8 bytes of real hash entropy) is plenty to catch an
# accidental stale/mismatched pairing without bloating every tile file the
# way a full 64-char sha256 digest would.
GENERATION_ID_BYTES = 16


def compute_generation_id() -> str:
    """16-hex-char id derived from road_stations.json's content plus the
    geometry-shape-affecting constants listed in this module's docstring.
    Same output for repeated calls within one run/process - callers should
    call this once per pipeline run and pass the result to every tile
    writer, not recompute it per tile (wasted work, and would break the
    "one id per run" invariant if road_stations.json were somehow rewritten
    mid-run)."""
    h = hashlib.sha256()

    if STATIONS_PATH.is_file():
        data = json.loads(STATIONS_PATH.read_text())
        h.update(repr(data.get("frame_lcc")).encode("utf-8"))
        for seg in data.get("segments", []):
            # Round to mm - strips float repr/json round-trip noise, not a
            # precision requirement; a real station edit changes values by
            # far more than 0.001 m.
            east = [round(v, 3) for v in seg.get("east_m", [])]
            north = [round(v, 3) for v in seg.get("north_m", [])]
            elev = [round(v, 3) for v in seg.get("elev_m", [])]
            h.update(repr(east).encode("utf-8"))
            h.update(repr(north).encode("utf-8"))
            h.update(repr(elev).encode("utf-8"))
    else:
        # No stations file yet (e.g. this exporter somehow ran before
        # build_road_stations.py) - still produce a stable, distinct id
        # rather than crashing; the missing-upstream-file failure itself is
        # already handled/reported elsewhere in each exporter's own read.
        h.update(b"NO_STATIONS_FILE")

    geometry_constants = (
        AREA_NAME,
        ORIGIN_HEIGHT_M,
        ROAD_TILE_SIZE_CM,
        TERRAIN_TILE_SIZE_CM,
        STATION_SPACING,
        ROAD_HALF_WIDTH,
        # Per-station lanes-tag width (2026-09-06): these change the road
        # footprint and the terrain carve radius, so a pre-width tile must
        # mismatch a post-width one loudly.
        LANE_WIDTH_M,
        MIN_HALF_WIDTH_M,
        MAX_HALF_WIDTH_M,
        WIDTH_SMOOTH_SIGMA_M,
        # Junction-agreement pass (2026-09-06, item 5): changes station
        # profiles at every at-grade crossing, so pre/post bakes must
        # mismatch loudly.
        JUNCTION_AGREE_RADIUS_M,
        JUNCTION_AGREE_TAPER_GRADE,
        SKIRT_EXTRA,
        SKIRT_DROP,
        ROAD_LIFT,
        MAX_BANK_RAD,
        BANK_GAIN,
        "bank-plane",   # 2026-10-02: bank tilts about the centerline, terrain follows it
        ELEV_PLAUSIBLE_MIN_M,
        ELEV_PLAUSIBLE_MAX_M,
        DEM_SPIKE_THRESHOLD_M,
    )
    h.update(repr(geometry_constants).encode("utf-8"))

    return h.hexdigest()[:GENERATION_ID_BYTES]


def pack_generation_id(gen_id: str) -> bytes:
    """ASCII-encodes gen_id to exactly GENERATION_ID_BYTES bytes, ready to
    write into a sidecar header right after the magic. Every writer in this
    pipeline should use this (not roll its own encoding) so the on-disk
    field width never drifts between formats."""
    b = gen_id.encode("ascii")
    assert len(b) == GENERATION_ID_BYTES, (
        f"generation id must be exactly {GENERATION_ID_BYTES} hex chars, "
        f"got {gen_id!r} ({len(b)} bytes)"
    )
    return b


def delete_stale_tiles(out_dir: Path, glob_pattern: str, written_paths) -> int:
    """Deletes any pre-existing file in out_dir matching glob_pattern that
    ISN'T one of written_paths (the files this run just wrote/is about to
    have written). Returns the count deleted. Pure pathlib, no shell calls -
    callers must pass a glob_pattern scoped tightly enough to their own
    format+area (e.g. "barrier_oahu_test_tile_*.bin") so this can never
    reach across formats or areas (see each exporter's own comment for its
    exact pattern)."""
    written = {Path(p).resolve() for p in written_paths}
    deleted = 0
    for existing in out_dir.glob(glob_pattern):
        if existing.resolve() not in written and _in_job(existing.name):
            existing.unlink()
            deleted += 1
    return deleted


def _in_job(name):
    """(2026-10-21, area picker) A pass-2 job owns only the tiles in its rect:
    tiles another job baked are never stale. True with no job rect."""
    import re
    from bake_job import job_rect, tile_in_job
    if job_rect() is None:
        return True
    m = re.search(r"_tile_(-?\d+)_(-?\d+)\.bin$", name)
    if not m:
        return False   # a whole-area file: not this job's to delete
    from test_areas import ROAD_TILE_SIZE_CM, TERRAIN_TILE_SIZE_CM
    size = ROAD_TILE_SIZE_CM if name.startswith(("road_", "roadv3_")) else TERRAIN_TILE_SIZE_CM
    return tile_in_job(int(m[1]), int(m[2]), size)
