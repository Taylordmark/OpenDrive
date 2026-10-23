"""Offline check of the flowing-river data in the baked water tiles
(build_water_tiles.py's ODF1 trailer, 2026-10-23) - no UE launch.

  - every tile carries ODF1, it parses to exactly the end of the file, and
    its kinds match the water_<area>_test_ribbon_flags.json sidecar
  - river ribbons (kind 1): U in [0, channel width], V = distance downstream
    (water never runs uphill as V grows), speed/whitewater in range, and
    some stretches run white (rapids / cascades)
  - waterfall sheets (kind 3): one per OSM fall in waterway_build_summary,
    V runs down the sheet, at least FALL_MIN_H_M tall, full whitewater
  - mist (kind 4): one per fall (OSM + DEM knickpoint), intensity in (0, 1]

Run:  OD_AREA=deals_gap python pipeline/checks/verify_river_flow.py
"""

import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_terrain_mesh_tiles import GENERATION_ID_BYTES, OUT_DIR, WATER_TILE_GLOB
from build_water_tiles import (FALL_MIN_H_M, FLOW_SPEED_MAX_MS, FLOW_SPEED_MIN_MS, KIND_MIST,
                               KIND_RIBBON, KIND_WATERFALL, RAW_DIR, UV_TILE_M, WATERWAY_MAX_WIDTH_M)
from test_areas import AREA_NAME


def parse(raw):
    """-> [(kind, verts (N,3) cm, uvs (N,2), flow (N,2))]; raises on a bad tile."""
    assert raw[:4] == b"ODL1", "bad magic"
    cur = 4 + GENERATION_ID_BYTES
    (n,) = struct.unpack_from("<I", raw, cur); cur += 4
    feats = []
    for _ in range(n):
        vc, tc = struct.unpack_from("<II", raw, cur); cur += 8
        verts = np.frombuffer(raw, "<f4", vc * 3, cur).reshape(-1, 3); cur += vc * 12
        uvs = np.frombuffer(raw, "<f4", vc * 2, cur).reshape(-1, 2); cur += vc * 8 + tc * 12
        feats.append([verts, uvs])
    assert raw[cur:cur + 4] == b"ODF1", "no ODF1 flow trailer (pre-2026-10-23 bake?)"
    cur += 4
    out = []
    for verts, uvs in feats:
        kind = raw[cur]; cur += 1
        flow = np.frombuffer(raw, "<f4", len(verts) * 2, cur).reshape(-1, 2); cur += len(verts) * 8
        out.append((kind, verts, uvs, flow))
    assert cur == len(raw), f"{len(raw) - cur} trailing bytes after ODF1"
    return out


def downhill_with_v(verts, uvs, tol_cm=2.0):
    """Water surface z never rises as V (downstream distance) grows."""
    order = np.lexsort((uvs[:, 0], uvs[:, 1]))
    v, z = uvs[order, 1], verts[order, 2]
    # compare each distinct V level's max z to the previous level's min z
    levels = np.unique(v)
    zmax = np.array([z[v == lv].max() for lv in levels])
    zmin = np.array([z[v == lv].min() for lv in levels])
    return bool((zmax[1:] <= zmin[:-1] + tol_cm).all()) if len(levels) > 1 else True


def main():
    paths = sorted(OUT_DIR.glob(WATER_TILE_GLOB))
    assert paths, f"no water tiles matching {WATER_TILE_GLOB} in {OUT_DIR}"
    flags = json.loads((OUT_DIR / f"water_{AREA_NAME}_test_ribbon_flags.json").read_text())
    summary = json.loads((RAW_DIR / "waterway_build_summary.json").read_text())
    falls = summary.get("fall_summaries", [])
    errors = []
    n_kind = {}
    n_white_ribbons = n_uphill = 0
    speeds, whites = [], []
    for p in paths:
        try:
            feats = parse(p.read_bytes())
        except (AssertionError, struct.error, IndexError) as e:
            errors.append(f"{p.name}: {e}")
            continue
        key = p.name.rsplit("_tile_", 1)[1][:-4].replace("_", ",", 1)
        if [f[0] for f in feats] != flags.get(key):
            errors.append(f"{p.name}: trailer kinds != ribbon_flags.json[{key}]")
        for i, (kind, verts, uvs, flow) in enumerate(feats):
            where = f"{p.name} feature {i} kind {kind}"
            n_kind[kind] = n_kind.get(kind, 0) + 1
            if not np.isfinite(flow).all():
                errors.append(f"{where}: non-finite flow"); continue
            if kind == KIND_RIBBON:
                speeds.append(flow[:, 0]); whites.append(flow[:, 1])
                if uvs[:, 0].min() < -1e-4 or uvs[:, 0].max() > WATERWAY_MAX_WIDTH_M / UV_TILE_M + 1e-4:
                    errors.append(f"{where}: U {uvs[:, 0].min():.3f}..{uvs[:, 0].max():.3f} outside the channel")
                if not (FLOW_SPEED_MIN_MS - 1e-4 <= flow[:, 0].min() and flow[:, 0].max() <= FLOW_SPEED_MAX_MS + 1e-4):
                    errors.append(f"{where}: speed {flow[:, 0].min():.2f}..{flow[:, 0].max():.2f} m/s out of range")
                if flow[:, 1].min() < 0 or flow[:, 1].max() > 1:
                    errors.append(f"{where}: whitewater outside 0..1")
                if not downhill_with_v(verts, uvs):
                    n_uphill += 1
                n_white_ribbons += flow[:, 1].mean() >= 0.6
            elif kind == KIND_WATERFALL:
                if not downhill_with_v(verts, uvs):
                    errors.append(f"{where}: sheet z rises along V")
                if verts[:, 2].max() - verts[:, 2].min() < FALL_MIN_H_M * 100 - 1:
                    errors.append(f"{where}: only {np.ptp(verts[:, 2]) / 100:.1f} m tall")
                if (flow[:, 1] < 1).any():
                    errors.append(f"{where}: not full whitewater")
            elif kind == KIND_MIST:
                if not (0 < flow[:, 0].min() and flow[:, 0].max() <= 1):
                    errors.append(f"{where}: mist intensity {flow[:, 0].min():.2f}..{flow[:, 0].max():.2f}")
    if n_uphill:
        errors.append(f"{n_uphill} ribbon quad(s) where the water rises downstream")
    n_osm = sum(f["source"] == "osm" for f in falls)
    if n_kind.get(KIND_WATERFALL, 0) != n_osm:
        errors.append(f"{n_kind.get(KIND_WATERFALL, 0)} waterfall sheets, summary lists {n_osm} OSM falls")
    if n_kind.get(KIND_MIST, 0) != len(falls):
        errors.append(f"{n_kind.get(KIND_MIST, 0)} mist features, summary lists {len(falls)} falls")
    if n_kind.get(KIND_RIBBON, 0) and not n_white_ribbons:
        errors.append("no ribbon runs white - rapids/cascade shading would never show")
    sp = np.concatenate(speeds) if speeds else np.zeros(1)
    wh = np.concatenate(whites) if whites else np.zeros(1)
    print(f"{len(paths)} tiles; features by kind {dict(sorted(n_kind.items()))}; "
          f"river speed p10/p50/p90 {np.percentile(sp, 10):.2f}/{np.percentile(sp, 50):.2f}/"
          f"{np.percentile(sp, 90):.2f} m/s; whitewater>=0.6 on {(wh >= 0.6).mean() * 100:.1f}% of "
          f"ribbon verts ({n_white_ribbons} rapids-emitter-grade quads); falls {n_osm} OSM + "
          f"{len(falls) - n_osm} DEM knickpoint")
    for e in errors[:40]:
        print("  FAIL " + e)
    print("PASS" if not errors else f"{len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
